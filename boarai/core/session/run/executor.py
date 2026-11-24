"""Run executor: conversation tool-loop (from RunManager._execute_run).

The six responsibilities each live in a sibling turn_*.py module and are
mixed into RunExecutor; this file keeps only the wiring (__init__ / _cfg /
lazy service properties) and execute()'s control flow. Cross-phase state
rides on loop_config's RunLoopState / TurnRequest / TurnFrame.
"""
from __future__ import annotations

import logging
import asyncio
import time

from typing import Any, Optional
from boarai.core.event import EventType
from boarai.core.session.run_models import Run
from boarai.core.session.thinking import SilenceDetector
from .context_prep import ContextPrep
from .loop_config import (
    LoopStep,
    RunLoopConfig,
    RunLoopState,
    build_default_config,
)
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from boarai.core.session.run.broadcaster import RunBroadcaster
    from boarai.core.session.run.compression_gate import CompressionGate
    from boarai.core.session.run.registry import RunRegistry
    from boarai.core.session.run.tool_confirm import ToolConfirmGate

from .turn_background import TurnBackgroundMixin
from .turn_finalize import TurnFinalizeMixin
from .turn_preflight import TurnPreflightMixin
from .turn_stream import TurnStreamMixin
from .turn_tool_round import TurnToolRoundMixin
from .turn_usage import TurnUsageMixin

logger = logging.getLogger(__name__)


class RunExecutor(
    TurnPreflightMixin,
    TurnStreamMixin,
    TurnToolRoundMixin,
    TurnFinalizeMixin,
    TurnUsageMixin,
    TurnBackgroundMixin,
):
    """Background conversation execution for a single Run."""

    def __init__(
        self,
        registry: "RunRegistry",
        broadcaster: "RunBroadcaster",
        confirm: "ToolConfirmGate",
        compression: "CompressionGate",
        config: Optional[RunLoopConfig] = None,
    ) -> None:
        self._registry = registry
        self._broadcaster_impl = broadcaster
        self._confirm = confirm
        self._compression = compression
        self._sm: Optional[Any] = None
        self._llm_svc: Optional[Any] = None
        self._prep = ContextPrep()
        # config=None -> assemble the default config from real implementations
        # on first use. Lazy construction matters: the default config's
        # save_session / llm forward to the two lazy attributes below, and the
        # global singletons may not exist yet when RunExecutor is constructed
        # (same resolution timing as before the refactor).
        self._cfg_override = config
        self._cfg_default: Optional[RunLoopConfig] = None

    @property
    def _cfg(self) -> RunLoopConfig:
        """Outbound boundaries of this run. An injected config wins, else the real implementations."""
        if self._cfg_override is not None:
            return self._cfg_override
        if self._cfg_default is None:
            self._cfg_default = build_default_config(self, self._broadcaster_impl)
        return self._cfg_default

    @property
    def _broadcaster(self) -> Any:
        """Broadcast sink: the RunBroadcaster passed to the constructor unless config overrides it."""
        cfg = self._cfg_override
        return cfg.broadcast if cfg is not None else self._broadcaster_impl

    @property
    def _session_manager(self) -> Any:
        if self._sm is None:
            from boarai.core.session.manager import get_session_manager

            self._sm = get_session_manager()
        return self._sm

    @property
    def _llm(self) -> Any:
        if self._llm_svc is None:
            from boarai.core.llm import get_llm_service

            self._llm_svc = get_llm_service()
        return self._llm_svc

    @staticmethod
    async def _trigger_core_hook(
        name: str,
        payload: Any = None,
        timeout: float = 2.0,
    ) -> Any:
        """Trigger a core.* hook point via the HookRegistry (best-effort)."""
        try:
            from boarai.core.extension import get_hook_registry
            return await get_hook_registry().trigger(f"core.{name}", payload, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"core.{name} hook skipped: {e}")
            return None

    async def execute(self, run: Run) -> None:
        """Execute a run in background with tool calling loop."""
        from boarai.core.logging import log_context, short_id

        with log_context(run=short_id(run.run_id), sess=short_id(run.session_id)):
            await self._execute_loop(run)

    async def _execute_loop(self, run: Run) -> None:
        """The tool loop itself, inside the run's log context."""
        started = time.monotonic()
        status = "failed"
        st = RunLoopState(run=run)
        logger.info(
            "run.start run_id=%s session_id=%s model=%s",
            run.run_id, run.session_id, run.model or "default",
        )

        try:
            st.adopt(await self._cfg.prepare_context(run))
            # tools_enabled controls whether tools are **executed**; wire_tools
            # controls whether their schema goes **on the wire**. FORCE_FINAL
            # disables execution but keeps the schema, so a forced wrap-up does
            # not fragment the provider's prefix cache (DeepSeek and others count
            # tools as part of the prefix).
            st.tools_enabled = (
                bool(st.ctx.route.tools_enabled) if st.ctx.route else True
            )
            st.wire_tools = st.tools_enabled
            # The transient termination hint is attached to the current request
            # only and never written into session history (that protects both the
            # prefix cache and the message list the user sees).
            st.pending_inject = ""
            st.readonly_auto = bool(st.ctx.run_cfg.get("readonly_auto_approve", False))

            await self._announce_run_start(st)

            # force_final may need one extra text-only wrap-up turn after the
            # last budgeted turn.
            st.turn_budget = st.max_turns
            st.turn = 0
            # One silence detector per RUN: building a new one each turn would
            # reset the counter and make max_retries meaningless (every turn would
            # start with a fresh retry budget).
            st.silence_detector = SilenceDetector(max_retries=2)

            # The session file directory used for attachment downsampling is
            # resolved once (images -> data-URL blocks, small text -> inline,
            # everything else -> path description).
            from boarai.core.session import attachments as _attachments

            if any(
                (getattr(m, "metadata", None) or {}).get("attachments")
                for m in st.session.messages
            ):
                try:
                    st.attach_files_dir = _attachments.get_files_dir(run.session_id)
                except Exception as attach_err:  # noqa: BLE001 - degrade gracefully
                    logger.debug("attachment dir resolve failed: %s", attach_err)

            while st.turn < st.turn_budget:
                # Budget is checked before the next request is assembled, so a
                # soft limit set last turn applies here (schemas stay on the
                # wire, tool execution is already disabled).
                if await self._apply_budget(st) is LoopStep.BREAK:
                    break
                st.turn_state.turn = st.turn
                st.metrics.turns = st.turn + 1

                await self._cfg.publish_event(
                    EventType.AGENT_ITERATION_START,
                    {
                        "session_id": run.session_id,
                        "run_id": run.run_id,
                        "turn": st.turn,
                        "agent_name": st.agent_name,
                        "channel": st.metrics.channel,
                        "route_reason": st.metrics.route_reason,
                    },
                    "run_manager",
                )

                session_raw = await self._build_wire_history(st)
                req = await self._prepare_turn_request(st, session_raw)
                tf = await self._open_turn(st, req)
                await self._stream_turn(st, req, tf)

                # Silence (thinking only, no text) -> the phase already
                # incremented turn, so go to the next turn.
                if await self._close_turn(st, req, tf) is LoopStep.NEXT:
                    continue

                if st.pending_tool_calls:
                    if await self._run_tool_round(st, req, tf) is LoopStep.BREAK:
                        break
                    continue

                # No tool calls: record the stop reason from finish_reason; all
                # three branches end the loop.
                await self._decide_text_stop(st, tf)
                break

            await self._finalize_completed(st)
            status = "completed"

        except asyncio.CancelledError:
            status = "cancelled"
            await self._finalize_cancelled(st)
            raise

        except Exception as e:
            status = "failed"
            await self._finalize_failed(st, e)

        finally:
            self._log_run_end(st, status, started)

    @staticmethod
    def _log_run_end(st: RunLoopState, status: str, started: float) -> None:
        """One structured line closing the run."""
        metrics = getattr(st, "metrics", None)
        logger.info(
            "run.end status=%s duration=%.2fs turns=%s tool_calls=%s "
            "tool_failures=%s finish=%s channel=%s model=%s chars=%s",
            status,
            time.monotonic() - started,
            getattr(metrics, "turns", None),
            getattr(metrics, "tool_calls", None),
            getattr(metrics, "tool_failures", None),
            getattr(metrics, "finish_reason", "") or "-",
            getattr(metrics, "channel", "") or "-",
            getattr(st, "model", None) or "-",
            len(getattr(st, "full_content", "") or ""),
        )
