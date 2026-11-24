"""Run usage accounting: token/cache usage into the session, the global ledger
and message metadata."""
from __future__ import annotations

import logging
import time

from typing import (
    Any,
    Dict,
    Optional,
)
from boarai.core.session.run_models import Run
from boarai.core.session.usage_ledger import get_usage_ledger

logger = logging.getLogger(__name__)



class TurnUsageMixin:
    """Usage accounting phase of RunExecutor (mixed in via multiple inheritance)."""

    async def _record_run_cache_usage(
        self,
        run: Run,
        session: Any,
        model: Optional[str],
        full_content: str,
        token_estimates: Any = None,
        status: str = "completed",
    ) -> None:
        """Persist this run's LLM token/cache usage to the session + ledger."""
        if not session:
            return
        if getattr(run, "_usage_recorded", False):
            logger.warning(
                "Run %s: usage already recorded for this run; skipping duplicate (%s)",
                run.run_id, status,
            )
            return
        try:
            agg = getattr(run, "_usage_agg", None) or {}
            actual = getattr(run, "_actual_usage", None)
            if not actual and not agg.get("turns"):
                logger.debug(
                    "Run %s: no LLM usage captured (%s), skipping usage record",
                    run.run_id, status,
                )
                return
            actual = actual or {}
            # Completion fallback: provider reported input but the stream was
            # cut before completion tokens arrived (cancel mid-stream).
            completion = actual.get("completion_tokens", 0) or max(
                1, len(full_content or "") // 4
            )
            last_cache_read = (
                actual.get("cached_tokens", 0)
                or actual.get("cache_hit_tokens", 0)
                or actual.get("cache_read_input_tokens", 0)
            )
            # Run-level consumption totals (all turns), shared by the cache
            # entry, the ledger and the per-message stamp below.
            run_input = int(agg.get("input", 0) or actual.get("prompt_tokens", 0) or 0)
            run_output = int(agg.get("completion", 0) or completion or 0)
            session.add_cache_request(
                {
                    # Last-turn values (context footprint):
                    "prompt_tokens": actual.get("prompt_tokens", 0),
                    "completion_tokens": completion,
                    "cache_read_input_tokens": last_cache_read,
                    "cache_creation_input_tokens": actual.get(
                        "cache_creation_input_tokens", 0
                    ),
                    "cache_miss_tokens": actual.get("cache_miss_tokens", 0),
                    # Run-level totals (all turns — the consumption figures):
                    "run_turns": agg.get("turns", 0),
                    "run_total_input_tokens": run_input,
                    "run_total_completion_tokens": run_output,
                    "run_total_cache_read_tokens": agg.get("cache_read", 0),
                    "run_total_cache_creation_tokens": agg.get(
                        "cache_creation", 0
                    ),
                },
                model or "unknown",
                token_estimates=token_estimates,
                run_id=run.run_id,
            )
            # Global append-only ledger: token history must survive session
            # deletion (monitoring stats aggregate from the ledger).
            if session.cache_requests:
                last = session.cache_requests[-1]
                await get_usage_ledger().record(
                    {
                        "ts": last.get("ts", ""),
                        "run_id": run.run_id,
                        "session_id": run.session_id,
                        "session_title": getattr(session, "title", "") or "",
                        "agent_name": getattr(session, "agent_name", "") or "default",
                        "model": last.get("model", model or "unknown"),
                        "input_tokens": last.get("input_tokens", 0),
                        "completion_tokens": last.get("completion_tokens", 0),
                        "cache_read_tokens": last.get("cache_read_tokens", 0),
                        "cache_creation_tokens": last.get(
                            "cache_creation_tokens", 0
                        ),
                        "context_tokens": last.get("context_tokens", 0),
                        "status": status,
                    }
                )
                run._usage_recorded = True
            # Per-round UI stats: stamp this run's consumption + wall-clock
            # span onto its last assistant message so the WebUI can render
            # "tokens · duration" chips per round without extra API calls.
            self._stamp_run_usage_meta(
                session,
                run,
                {
                    "input": run_input,
                    "output": run_output,
                    "cache_read": int(agg.get("cache_read", 0) or 0),
                    "cache_creation": int(agg.get("cache_creation", 0) or 0),
                    "reasoning": int(agg.get("reasoning", 0) or 0),
                },
            )
            await self._cfg.save_session(run.session_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Failed to record cache usage for run %s: %s", run.run_id, e
            )

    @staticmethod
    def _stamp_run_usage_meta(
        session: Any, run: Run, usage: Dict[str, int]
    ) -> None:
        """Persist run_id / token breakdown / start-finish epoch on the run's last assistant message metadata."""
        if not session or not getattr(session, "messages", None):
            return
        for msg in reversed(session.messages):
            if msg.role != "assistant":
                continue
            meta = dict(msg.metadata or {})
            meta["run_id"] = run.run_id
            meta["run_usage"] = dict(usage)
            meta["run_started_at"] = float(getattr(run, "created_at", 0) or 0)
            meta["run_finished_at"] = time.time()
            msg.metadata = meta
            break

    async def _apply_budget(self, st: Any) -> Any:
        """Enforce the run budget from the usage aggregated so far."""
        from .loop_config import LoopStep
        from .termination import TerminateKind, budget_ratio, decide_after_usage

        if getattr(st, "term_cfg", None) is None or getattr(st, "turn_state", None) is None:
            return LoopStep.FALLTHROUGH
        agg = getattr(st.run, "_usage_agg", None) or {}
        ts = st.turn_state
        ts.input_tokens_total = int(agg.get("input", 0) or 0)
        ts.output_tokens_total = int(agg.get("completion", 0) or 0)
        ts.cost_usd_total = float(agg.get("cost_usd", 0.0) or 0.0)

        cfg = st.term_cfg
        ratio = budget_ratio(ts, cfg)
        if ratio > 0:
            # Snapshot for the API: "used / limit" plus why it stopped.
            st.run._budget = {
                "input_tokens": ts.input_tokens_total,
                "output_tokens": ts.output_tokens_total,
                "cost_usd": round(ts.cost_usd_total, 6),
                "limit_input_tokens": cfg.max_input_tokens,
                "limit_total_tokens": cfg.max_total_tokens,
                "limit_cost_usd": cfg.max_cost_usd,
                "soft_ratio": cfg.budget_soft_ratio,
                "ratio": round(ratio, 4),
                "exhausted": ratio >= 1.0,
            }

        if (
            cfg.max_cost_usd > 0
            and ts.cost_usd_total <= 0
            and ts.input_tokens_total > 0
            and not getattr(st, "_cost_budget_warned", False)
        ):
            st._cost_budget_warned = True
            logger.warning(
                "Run %s: max_cost_usd is configured but the provider reports no "
                "cost; the cost budget is inert (token budgets still apply)",
                st.run.run_id,
            )

        action = decide_after_usage(ts, cfg)
        if action.kind is TerminateKind.ABORT:
            st.metrics.finish_reason = action.reason or "budget"
            logger.warning(
                "Run %s: budget exhausted (input=%s output=%s); aborting",
                st.run.run_id, ts.input_tokens_total, ts.output_tokens_total,
            )
            return LoopStep.BREAK
        if action.kind is TerminateKind.FORCE_FINAL_TEXT:
            st.pending_inject = action.inject_message or ""
            # Same trade as force_final: keep the schemas on the wire so the
            # prefix cache stays intact, but never execute another tool.
            st.tools_enabled = False
            st.wire_tools = True
            if st.turn_budget <= st.turn:
                st.turn_budget = st.turn + 1
            logger.warning(
                "Run %s: budget soft limit reached (input=%s); forcing a final answer",
                st.run.run_id, ts.input_tokens_total,
            )
        return LoopStep.FALLTHROUGH
