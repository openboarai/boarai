"""Run terminal phase: the completed / cancelled / failed paths, plus
interruption persistence and error markers."""
from __future__ import annotations

import logging
import asyncio
import time

from typing import Any, Optional
from boarai.core.event import EventType
from boarai.core.session.run_models import Run, RunStatus
from boarai.core.session.run_tools import extract_summary_section
from boarai.core.session.orphan_tools import patch_orphan_tool_results
from .loop_config import RunLoopState
from .messages import TOOL_CANCELLED

logger = logging.getLogger(__name__)



class TurnFinalizeMixin:
    """Terminal paths phase of RunExecutor (mixed in via multiple inheritance)."""

    async def _flush_stream_buffer(self, run: Run) -> None:
        """Emit coalesced stream text before terminal events (ordering)."""
        flush = getattr(self._broadcaster, "flush_chunks", None)
        if flush is None:
            return
        try:
            await flush(run.run_id)
        except Exception:  # noqa: BLE001 - terminal path must not fail
            logger.debug("chunk buffer flush skipped for run %s", run.run_id)

    async def _save_on_interrupt(
        self,
        run: Run,
        session: Any,
        turn_text: str,
    ) -> None:
        """Persist the CURRENT turn's partial text on interrupt."""
        if not turn_text or not session or not session.messages:
            return
        last_idx = len(session.messages) - 1
        last_msg = session.messages[last_idx]
        if last_msg.role != "assistant":
            return
        # Only overwrite a bubble that is empty or still partial; a turn that
        # already completed correctly (content persisted) must stay untouched.
        if last_msg.content and len(last_msg.content) >= len(turn_text):
            return
        self._write_assistant_turn(session, last_idx, turn_text)
        try:
            await self._cfg.save_session(run.session_id)
        except Exception as e:
            logger.warning(
                "Failed to save session %s on interrupt: %s",
                run.session_id,
                e,
            )

    @staticmethod
    def _write_assistant_turn(
        session: Any,
        idx: int,
        text: str,
        tool_calls: Any = None,
        thinking: str = "",
        thinking_seconds: Optional[float] = None,
    ) -> None:
        """Write a turn's final text/tool_calls/thinking onto its assistant bubble."""
        if not session or idx < 0 or idx >= len(session.messages):
            return
        msg = session.messages[idx]
        if msg.role != "assistant":
            return
        if text:
            msg.content = text
        meta_changed = False
        meta = dict(msg.metadata or {})
        if tool_calls:
            meta["tool_calls"] = True
            meta_changed = True
            msg.tool_calls = tool_calls
        if thinking_seconds:
            # Persisted thinking duration so clients can show the elapsed
            # time after refresh / reload (live measurement is volatile).
            meta["thinking_seconds"] = float(thinking_seconds)
            meta_changed = True
        if meta_changed:
            msg.metadata = meta
        if thinking:
            msg.thinking = thinking

    @staticmethod
    def _prune_empty_tail_assistant(session: Any) -> None:
        """Drop a trailing EMPTY assistant shell left by a cancelled run."""
        if not session or not session.messages:
            return
        last = session.messages[-1]
        if last.role != "assistant":
            return
        if last.content or last.tool_calls or last.thinking:
            return
        session.messages.pop()
        session.message_count = len(session.messages)

    @staticmethod
    def _stamp_assistant_run_error(
        session: Any,
        error_text: str,
        run_id: Optional[str] = None,
    ) -> None:
        """Write run failure onto the last assistant message for durable UI."""
        if not session or not getattr(session, "messages", None):
            return
        text = (error_text or "Run failed").strip()
        # Prefer last empty/partial assistant without successful tool-only meta
        target = None
        for msg in reversed(session.messages):
            if msg.role != "assistant":
                continue
            target = msg
            break
        if target is None:
            session.add_message("assistant", "")
            target = session.messages[-1]
        meta = dict(target.metadata or {})
        meta["is_error"] = True
        meta["error"] = text
        if run_id:
            meta["run_id"] = run_id
        target.metadata = meta
        # Keep any partial stream; append error marker only when empty
        if not (target.content or "").strip():
            target.content = f"[Error] {text}"

    async def _finalize_completed(self, st: RunLoopState) -> None:
        """Completed path: empty-reply fallback, pairing self-check, usage, summary memory, event broadcast and background extraction."""
        ctx = st.ctx; diff_tracker = st.diff_tracker; metrics = st.metrics
        model = st.model; run = st.run; session = st.session
        task_state_mgr = st.task_state_mgr; turn_state = st.turn_state; user_input = st.user_input
        await self._flush_stream_buffer(run)
        # Empty-reply fallback: a run whose final assistant message has no
        # text and no tool_calls (observed when the model goes silent
        # after a tool-approval timeout) leaves the user with NO answer
        # bubble at all. Persist a short explanation instead.
        if session and session.messages:
            last_asst = next(
                (m for m in reversed(session.messages) if m.role == "assistant"),
                None,
            )
            if (
                last_asst is not None
                and not (last_asst.content or "").strip()
                and not getattr(last_asst, "tool_calls", None)
                and any(m.role == "tool" for m in session.messages)
            ):
                last_tool = next(
                    (m for m in reversed(session.messages) if m.role == "tool"),
                    None,
                )
                ttxt = str(getattr(last_tool, "content", "") or "")
                if "approval timed out" in ttxt or "timed out" in ttxt:
                    fallback = (
                        "工具审批等待超时，该操作未执行。"
                        "你可以在界面中批准该操作后让我重试，或告诉我换一种做法。"
                    )
                elif "not approved" in ttxt.lower() or "denied" in ttxt.lower():
                    fallback = (
                        "工具调用未获批准，本次没有执行该操作。"
                        "可以调整请求后重试。"
                    )
                else:
                    fallback = "本次运行在工具调用后没有生成文本回复，请重试或换个问法。"
                fb_msg = session.add_message(
                    role="assistant",
                    content=fallback,
                    metadata={"fallback": "empty_reply"},
                )
                logger.info(
                    "Run %s: empty final assistant reply, injected fallback "
                    "(last tool: %.80s)",
                    run.run_id, ttxt,
                )
                try:
                    await self._broadcaster.broadcast_chunk(
                        run, fallback,
                        message_id=fb_msg.message_id, seq=fb_msg.seq,
                    )
                except Exception:  # noqa: BLE001
                    pass
                # The fallback is a whole message on its own and nothing after
                # this point emits stream text, so it needs an explicit drain —
                # otherwise it sits in the coalescing buffer, the terminal done
                # frame overtakes it, and run removal discards it. The bubble
                # is persisted either way, so the symptom is an empty answer
                # live that only appears after a reload.
                await self._flush_stream_buffer(run)
                await self._cfg.save_session(run.session_id)

        if not st.pending_tool_calls and session and session.messages:
            await self._cfg.save_session(run.session_id)

        if session and session.messages:
            asst_tc_count = 0
            tool_count = 0
            for m in session.messages:
                if m.role == "assistant" and getattr(m, "tool_calls", None):
                    asst_tc_count += len(m.tool_calls)
                if m.role == "tool":
                    tool_count += 1
            if asst_tc_count != tool_count:
                logger.error(
                    "Run %s: POST-RUN IRON LOOP BROKEN! "
                    "assistant tool_calls=%s, tool messages=%s",
                    run.run_id,
                    asst_tc_count,
                    tool_count,
                )

        await self._cfg.record_usage(
            run, session, model, st.full_content, st.turn0_token_estimates,
            # A budget stop is not a clean completion: analytics must be able
            # to tell "the model finished" from "we cut it off".
            status=(
                "budget"
                if getattr(st.metrics, "finish_reason", "") == "budget"
                else "completed"
            ),
        )

        if session and session.compressed_summary:
            try:
                from boarai.core.memory import get_memory_manager
                from boarai.core.memory.types import MemoryEntry

                mm = get_memory_manager()
                if mm:
                    summary = session.compressed_summary
                    pod_uri = f"pod://session/{run.session_id}"
                    dec_line = extract_summary_section(
                        summary, "══ KEY DECISIONS ══"
                    )
                    if dec_line and dec_line != "none confirmed":
                        entry = MemoryEntry(
                            pod_uri=pod_uri,
                            topic=f"Decisions from session {run.session_id[:8]}",
                            content=dec_line,
                            entry_type="decision",
                            tags=[
                                "compressed",
                                "decision",
                                session.agent_name or "default",
                            ],
                            importance=0.9,
                            metadata={
                                "channel": getattr(session, "source", "system"),
                            },
                            source_session=run.session_id,
                            source_agent=session.agent_name or "default",
                        )
                        await mm.remember(pod_uri, entry, publish=False)
                    pref_line = extract_summary_section(
                        summary, "══ USER PREFERENCES ══"
                    )
                    if pref_line and pref_line != "none noted":
                        entry = MemoryEntry(
                            pod_uri=pod_uri,
                            topic="User preferences",
                            content=pref_line,
                            entry_type="preference",
                            tags=[
                                "compressed",
                                "preference",
                                session.agent_name or "default",
                            ],
                            importance=0.8,
                            metadata={
                                "channel": getattr(session, "source", "system"),
                            },
                            source_session=run.session_id,
                            source_agent=session.agent_name or "default",
                        )
                        await mm.remember(pod_uri, entry, publish=False)
                    bg_line = extract_summary_section(
                        summary, "══ BACKGROUND ══"
                    )
                    if bg_line and bg_line != "conversation background":
                        entry = MemoryEntry(
                            pod_uri=pod_uri,
                            topic=f"Background from session {run.session_id[:8]}",
                            content=bg_line,
                            entry_type="fact",
                            tags=[
                                "compressed",
                                "background",
                                session.agent_name or "default",
                            ],
                            importance=0.5,
                            metadata={
                                "channel": getattr(session, "source", "system"),
                            },
                            source_session=run.session_id,
                            source_agent=session.agent_name or "default",
                        )
                        await mm.remember(pod_uri, entry, publish=False)
            except Exception as e:
                logger.debug("Memory extraction from summary skipped: %s", e)

        if not metrics.finish_reason:
            metrics.finish_reason = "completed"
        metrics.tool_calls = (
            turn_state.tool_call_total if ctx else metrics.tool_calls
        )
        metrics.stagnation_hits = (
            turn_state.stagnation_hits if ctx else metrics.stagnation_hits
        )

        run.status = RunStatus.COMPLETED
        run.completed_at = time.time()
        run.updated_at = time.time()
        self._registry.clear_run_rate_limits(run.run_id)
        self._registry.clear_active_if(run.session_id, run.run_id)

        if session and session.status != "active":
            session.status = "active"
            try:
                saved = await self._cfg.save_session(run.session_id)
                if saved is False:
                    logger.warning(
                        "Failed to persist active status for session %s",
                        run.session_id,
                    )
            except Exception as save_err:
                logger.warning(
                    "Failed to clear error status for session %s: %s",
                    run.session_id,
                    save_err,
                )

        task_state_changed = False
        if task_state_mgr and session and user_input and st.full_content:
            try:
                updated_state = task_state_mgr.update_from_llm(
                    run.session_id,
                    task_state or task_state_mgr.load(run.session_id),
                    user_input,
                    st.full_content,
                )
                delta = getattr(updated_state, "last_delta", "") or ""
                if delta and delta != getattr(task_state, "last_delta", ""):
                    session.add_message(
                        "user",
                        delta,
                        metadata={"type": "task_state_delta"},
                    )
                    task_state = updated_state
                    task_state_changed = True
            except Exception as e:
                logger.debug("Task state update skipped: %s", e)

        if task_state_changed:
            try:
                saved = await self._cfg.save_session(run.session_id)
                if saved is False:
                    logger.error(
                        "Failed to persist task-state delta for session %s",
                        run.session_id,
                    )
            except Exception as save_err:  # noqa: BLE001
                logger.error(
                    "Failed to persist task-state delta for session %s: %s",
                    run.session_id,
                    save_err,
                )

        metrics_dict = metrics.to_dict() if metrics else {}
        done_event = {
            "run_id": run.run_id,
            "type": "run.completed",
            "content": st.full_content,
            "session_id": run.session_id,
            "status": "completed",
            "session_title": session.title if session else "",
            "summary": (
                (st.full_content[:200] + "...")
                if len(st.full_content or "") > 200
                else (st.full_content or "")
            ),
            "message_count": session.message_count if session else 0,
            "duration_seconds": round(time.time() - run.created_at, 1),
            "model": run.model or "unknown",
            "agent_name": session.agent_name if session else "default",
            **metrics_dict,
        }
        if run.task_id:
            done_event["task_id"] = run.task_id

        # --- Run-level file-change diff (traceability) ---
        # Emit run.diff BEFORE the done event so the UI receives changes
        # first. The lightweight files_changed summary also rides on the
        # done event, and the full record is persisted to the session
        # artifact change log so it survives a reload.
        if diff_tracker.has_changes():
            try:
                diff_payload = diff_tracker.to_payload()
                done_event["files_changed"] = diff_payload["files_changed"]
                await self._broadcaster.broadcast_diff(run, diff_payload)
                try:
                    from boarai.core.session.artifact import get_artifact_store

                    # Persist the FULL record (with revert sources:
                    # snapshot/trash_id/existed_before) — server-side only.
                    record = diff_tracker.to_payload(include_internal=True)
                    # Off-loop: changes.json read+write must not block the
                    # event loop (async-first; mirrors TrashManager I/O).
                    await asyncio.to_thread(
                        get_artifact_store().record_run_changes,
                        run.session_id,
                        run.run_id,
                        record["files_changed"],
                        record["unified_diff"],
                        record["stats"],
                    )
                except Exception as persist_err:  # noqa: BLE001
                    logger.debug("Run diff persist skipped: %s", persist_err)
            except Exception as diff_err:  # noqa: BLE001
                logger.warning("Run diff emission failed: %s", diff_err)

        # --- V2 hook: run.on_complete (observe) ---
        await self._cfg.trigger_hook(
            "run.on_complete",
            {
                "run_id": run.run_id,
                "session_id": run.session_id,
                "task_id": run.task_id,
                "status": "completed",
                "content": st.full_content,
            },
            timeout=2.0,
        )

        await self._cfg.publish_event(EventType.RUN_COMPLETED, done_event, "run_manager")
        done_event["type"] = "done"
        await self._cfg.publish_event(EventType.RUN_DONE, done_event, "run_manager")
        done_event["type"] = "done"
        await self._broadcaster.broadcast_to_ws(run.run_id, done_event)

        if run.completion_future and not run.completion_future.done():
            run.completion_future.set_result(run)

        if session:
            self._compression.arm_after_run(run.session_id)

        self._schedule_skill_evolution(session, run, ctx, metrics)

    async def _finalize_cancelled(self, st: RunLoopState) -> None:
        """Cancelled path: persist the interruption text, clear the empty placeholder, release approvals, emit the cancelled event."""
        ctx = st.ctx; model = st.model; run = st.run
        session = st.session
        await self._flush_stream_buffer(run)
        await self._save_on_interrupt(run, session, st.current_turn_text)
        # Drop an empty assistant shell left when the model streamed nothing
        # before the cancel — otherwise a reload renders it as a phantom
        # "error occurred" bubble.
        self._prune_empty_tail_assistant(session)
        # Persist token/cache usage even on cancel so WebUI stats survive
        # the interrupt (was previously only recorded on clean completion).
        await self._cfg.record_usage(
            run, session, model, st.full_content, st.turn0_token_estimates,
            status="cancelled",
        )
        # Release any pending approval so a waiter never dangles after cancel.
        try:
            self._confirm.cleanup_run(run.run_id)
        except Exception:  # noqa: BLE001
            pass

        if run._in_tool_execution or (st.pending_tool_calls and session):
            run._in_tool_execution = False
            if st.pending_tool_calls and session:
                for tc in st.pending_tool_calls:
                    tc_id = tc.get("id", "")
                    has_match = any(
                        m.role == "tool"
                        and (
                            m.tool_call_id == tc_id
                            or m.metadata.get("tool_call_id") == tc_id
                        )
                        for m in session.messages
                    )
                    if not has_match:
                        session.add_message(
                            role="tool",
                            content=TOOL_CANCELLED,
                            metadata={"tool_call_id": tc_id},
                        )
                        session.messages[-1].tool_call_id = tc_id
                await self._cfg.save_session(run.session_id)

        run.status = RunStatus.CANCELLED
        run.completed_at = time.time()
        run.updated_at = time.time()
        self._registry.clear_run_rate_limits(run.run_id)
        self._registry.clear_active_if(run.session_id, run.run_id)

        cancel_event = {
            "run_id": run.run_id,
            "type": "cancelled",
            "content": st.full_content or "",
            "session_id": run.session_id,
            "status": "cancelled",
            "reason": run.cancel_reason or "user_cancelled",
        }
        if ctx and ctx.metrics:
            cancel_event.update(ctx.metrics.to_dict())
        await self._cfg.publish_event(EventType.RUN_CANCELLED, cancel_event, "run_manager")
        await self._broadcaster.broadcast_to_ws(run.run_id, cancel_event)
        if run.completion_future and not run.completion_future.done():
            run.completion_future.set_result(run)
        if session:
            self._compression.arm_after_run(run.session_id)

    async def _finalize_failed(self, st: RunLoopState, exc: Exception) -> None:
        """Failed path: persist the error into the assistant bubble, mark the session error, emit the error event, extract lessons."""
        e = exc
        ctx = st.ctx; model = st.model; run = st.run
        session = st.session
        await self._flush_stream_buffer(run)
        await self._save_on_interrupt(run, session, st.current_turn_text)
        # Persist token/cache usage even on failure so WebUI stats survive
        # (was previously only recorded on clean completion).
        await self._cfg.record_usage(
            run, session, model, st.full_content, st.turn0_token_estimates,
            status="failed",
        )
        run.status = RunStatus.FAILED
        run.error = str(e)
        run.completed_at = time.time()
        run.updated_at = time.time()
        self._registry.clear_run_rate_limits(run.run_id)
        if session:
            session.status = "error"
            try:
                # Close open tool_calls so UI does not stick on "running"
                patch_orphan_tool_results(session, content=TOOL_CANCELLED)
                # Persist error on the last assistant bubble so UI can show
                # message + retry after reload (WS-only _error is lost).
                self._stamp_assistant_run_error(session, str(e), run.run_id)
                await self._cfg.save_session(run.session_id)
            except Exception as save_err:
                logger.warning(
                    "Failed to persist error status for session %s: %s",
                    run.session_id,
                    save_err,
                )
        logger.error("Run %s failed: %s", run.run_id, e)

        error_event = {
            "run_id": run.run_id,
            "type": "error",
            "error": str(e),
            "session_id": run.session_id,
        }
        if run.task_id:
            error_event["task_id"] = run.task_id
        if ctx and ctx.metrics:
            error_event.update(ctx.metrics.to_dict())

        # --- V2 hook: run.on_error (observe) ---
        await self._cfg.trigger_hook(
            "run.on_error",
            {
                "run_id": run.run_id,
                "session_id": run.session_id,
                "task_id": run.task_id,
                "error": str(e),
            },
            timeout=2.0,
        )

        await self._cfg.publish_event(EventType.RUN_ERROR, error_event, "run_manager")
        await self._broadcaster.broadcast_to_ws(run.run_id, error_event)
        self._registry.clear_active_if(run.session_id, run.run_id)
        if run.completion_future and not run.completion_future.done():
            run.completion_future.set_result(run)
        if session:
            self._compression.arm_after_run(run.session_id)
        await self._cfg.extract_lesson(session, run, ctx)
