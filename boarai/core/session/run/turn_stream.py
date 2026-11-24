"""Turn streaming phase: consuming the LLM stream (vision downgrade, stream-break
continuation) and closing the turn."""
from __future__ import annotations

import logging
import time

from typing import Any, Optional
from boarai.core.session.thinking import build_continuation_messages
from .context_prep import RunContext
from .loop_config import (
    LoopStep,
    RunLoopState,
    TurnFrame,
    TurnRequest,
)
from .messages import SILENCE_NUDGE

logger = logging.getLogger(__name__)

# Streaming persistence throttle. save_session rewrites the whole session file,
# so doing it every few chunks is O(n^2) I/O over a long conversation. A crash
# between checkpoints costs at most this much transcript, and the turn-end and
# interrupt paths both force a save, so a completed turn is never partial.
STREAM_SAVE_INTERVAL_SECONDS = 2.0
STREAM_SAVE_CHUNK_THRESHOLD = 200

def stream_save_due(
    last_save_monotonic: float,
    now_monotonic: float,
    chunks_since_save: int,
) -> bool:
    """Whether a mid-stream session checkpoint should be written now."""
    return (
        now_monotonic - last_save_monotonic >= STREAM_SAVE_INTERVAL_SECONDS
        or chunks_since_save >= STREAM_SAVE_CHUNK_THRESHOLD
    )


def stream_counts_as_success(
    text_buffer: str,
    thinking: str,
    pending_tool_calls: Any,
) -> bool:
    """Whether a turn's stream outcome books a fallback-machine success."""
    return bool(text_buffer) or bool(thinking) or bool(pending_tool_calls)


class TurnStreamMixin:
    """Streaming and turn-closing phase of RunExecutor (mixed in via multiple inheritance)."""

    def _thinking_kwargs(self, ctx: RunContext, model: Optional[str]) -> dict:
        """Session thinking_mode → LLM kwargs."""
        from boarai.core.session.thinking.config import ThinkingMode, resolve_thinking_config

        route = ctx.route
        tcfg = route.thinking_config if route else None
        if tcfg is None:
            tm = getattr(ctx.session, "thinking_mode", None) or "none"
            try:
                tcfg = resolve_thinking_config(ThinkingMode(str(tm).lower()), model or "")
            except Exception as e:
                logger.debug("Thinking config skipped: %s", e)
                return {}
        try:
            return tcfg.apply_to_turn(model or "")
        except Exception as e:
            logger.debug("Thinking apply skipped: %s", e)
            return {}

    async def _stream_turn(self, st: RunLoopState, req: TurnRequest, tf: TurnFrame) -> None:
        """Consume the LLM stream, with vision downgrade and two recovery attempts for a broken stream."""
        run = st.run; session = st.session
        for recovery_attempt in range(2):
            if not tf.vision_stripped:
                messages_for_call = req.assembled.messages
                if recovery_attempt > 0 and tf.text_buffer:
                    messages_for_call = build_continuation_messages(
                        req.assembled.messages, tf.text_buffer
                    )
                    logger.info(
                        "Run %s: continuation recovery, partial=%s chars",
                        run.run_id,
                        len(tf.text_buffer),
                    )
            # else: keep the already-stripped messages from the
            # vision-downgrade retry (recovery_attempt == 1).

            async def _on_llm_retry(
                attempt: int,
                max_attempts: int,
                error: str,
                delay_seconds: float = 0.0,
                **_kwargs: Any,
            ) -> None:
                await self._broadcaster.broadcast_retry(
                    run,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    error=str(error or ""),
                    delay_seconds=float(delay_seconds or 0.0),
                )

            try:
                async for chunk in self._cfg.llm.chat_stream(
                    messages=messages_for_call,
                    model=req.resolved_model,
                    tools=req.llm_tools,
                    cache_breakpoints=req.assembled.breakpoints,
                    on_retry=_on_llm_retry,
                    **tf.thinking_kwargs,
                ):
                    chunk_reasoning = getattr(chunk, "reasoning", None) or ""
                    if chunk_reasoning:
                        if tf.thinking_started_at is None:
                            tf.thinking_started_at = time.monotonic()
                        tf.reasoning_mgr.feed_reasoning_delta(chunk_reasoning)
                        tf.thinking_buffer += chunk_reasoning
                        await self._broadcaster.broadcast_thinking(
                            run, chunk_reasoning, message_id=tf.cur_msg_id, seq=tf.cur_msg_seq
                        )

                    if chunk.content:
                        result = tf.think_processor.feed(chunk.content)
                        clean_text = result.content
                        extracted_think = result.thinking

                        if extracted_think:
                            if tf.thinking_started_at is None:
                                tf.thinking_started_at = time.monotonic()
                            tf.reasoning_mgr.feed_reasoning_delta(extracted_think)
                            tf.thinking_buffer += extracted_think
                            await self._broadcaster.broadcast_thinking(
                                run, extracted_think, message_id=tf.cur_msg_id, seq=tf.cur_msg_seq
                            )

                        if clean_text:
                            # First visible text ends the reasoning window
                            if (
                                tf.thinking_started_at is not None
                                and tf.thinking_ended_at is None
                            ):
                                tf.thinking_ended_at = time.monotonic()
                            # --- V2 hook: llm.transform_output (opt-in) ---
                            try:
                                from boarai.core.extension import get_hook_registry
                                _hooks = get_hook_registry()
                                if _hooks.is_declared("core.llm.transform_output"):
                                    _tr = await _hooks.trigger(
                                        "core.llm.transform_output",
                                        clean_text,
                                        timeout=1.0,
                                    )
                                    if _tr is not None and _tr.data is not None:
                                        clean_text = str(_tr.data)
                            except Exception:  # noqa: BLE001
                                pass
                            tf.text_buffer += clean_text
                            st.full_content += clean_text
                            st.current_turn_text = tf.text_buffer
                            st.chunk_count += 1
                            run.full_response = st.full_content
                            run.current_turn_text = tf.text_buffer

                            # Throttled checkpoint: a full-file rewrite
                            # every few chunks is O(n^2) over a long
                            # session. The turn-end and interrupt paths
                            # force the final save.
                            tf.chunks_since_save += 1
                            now = time.monotonic()
                            if stream_save_due(
                                tf.last_stream_save, now, tf.chunks_since_save
                            ):
                                session.messages[
                                    tf.assistant_msg_idx
                                ].content = tf.text_buffer
                                await self._cfg.save_session(
                                    run.session_id
                                )
                                tf.chunks_since_save = 0
                                tf.last_stream_save = now

                            await self._broadcaster.broadcast_chunk(
                                run, clean_text, message_id=tf.cur_msg_id, seq=tf.cur_msg_seq
                            )

                    if chunk.tool_calls and st.tools_enabled:
                        st.pending_tool_calls = chunk.tool_calls

                    if chunk.finish_reason:
                        tf.finish_reason = chunk.finish_reason

                    if chunk.usage:
                        run._actual_usage = chunk.usage
                        # Per-turn aggregation: the old code kept only
                        # the LAST turn's usage, so multi-turn runs had
                        # no visibility into intermediate consumption.
                        # Idempotent per turn: providers are expected to
                        # yield usage exactly once (final chunk), but a
                        # future provider change must not double-count.
                        if not tf.usage_counted:
                            tf.usage_counted = True
                            chunk_cache_read = int(
                                chunk.usage.get("cached_tokens", 0)
                                or chunk.usage.get("cache_hit_tokens", 0)
                                or chunk.usage.get(
                                    "cache_read_input_tokens", 0
                                )
                                or 0
                            )
                            agg = getattr(run, "_usage_agg", None) or {
                                "turns": 0, "input": 0, "completion": 0,
                                "cache_read": 0, "cache_creation": 0,
                                "cost_usd": 0.0,
                            }
                            run._usage_agg = {
                                "turns": agg["turns"] + 1,
                                "input": agg["input"] + int(
                                    chunk.usage.get("prompt_tokens", 0) or 0
                                ),
                                "completion": agg["completion"] + int(
                                    chunk.usage.get("completion_tokens", 0) or 0
                                ),
                                "cache_read": (
                                    agg.get("cache_read", 0)
                                    + chunk_cache_read
                                ),
                                "cache_creation": (
                                    agg.get("cache_creation", 0)
                                    + int(
                                        chunk.usage.get(
                                            "cache_creation_input_tokens",
                                            0,
                                        )
                                        or 0
                                    )
                                ),
                                # Reasoning tokens (thinking) so the
                                # WebUI can split output into
                                # thinking vs reply content.
                                "reasoning": (
                                    agg.get("reasoning", 0)
                                    + int(
                                        (
                                            chunk.usage.get(
                                                "completion_tokens_details"
                                            )
                                            or {}
                                        ).get("reasoning_tokens", 0)
                                        or chunk.usage.get(
                                            "reasoning_tokens", 0
                                        )
                                        or 0
                                    )
                                ),
                                # Only providers that report a cost populate this;
                                # the budget's cost limit stays inert otherwise.
                                "cost_usd": (
                                    float(agg.get("cost_usd", 0.0) or 0.0)
                                    + float(chunk.usage.get("cost_usd", 0.0) or 0.0)
                                ),
                            }
                            # Live sidebar/toolbar counters: push the
                            # cumulative run usage as this turn completes
                            # (WS "usage" frame + run.usage event).
                            await self._broadcaster.broadcast_usage(
                                run, run._usage_agg
                            )

                tf.stream_completed = True
                # Reasoning-only turn (tool calls / silence): close the
                # window at stream end so the duration stays accurate.
                if (
                    tf.thinking_started_at is not None
                    and tf.thinking_ended_at is None
                ):
                    tf.thinking_ended_at = time.monotonic()
                # --- V2 hook: llm.post_response (observe) ---
                try:
                    await self._cfg.trigger_hook(
                        "llm.post_response",
                        {
                            "run_id": run.run_id,
                            "session_id": run.session_id,
                            "model": req.resolved_model,
                            "finish_reason": tf.finish_reason,
                            "content_len": len(st.full_content),
                            "turns": getattr(run, "_turn_count", 0),
                        },
                        timeout=2.0,
                    )
                except Exception:  # noqa: BLE001
                    pass
                break

            except Exception as stream_err:
                if (
                    recovery_attempt == 0
                    and not tf.vision_stripped
                    and getattr(stream_err, "vision_rejected", False)
                ):
                    # Text-only model rejected the multimodal payload.
                    # Graceful degradation: strip image_url blocks and
                    # retry ONCE with the same model (never writes back
                    # to session history — prompt-cache stability).
                    from boarai.core.session.run.messages import (
                        has_image_blocks,
                        strip_image_blocks,
                    )

                    if has_image_blocks(req.assembled.messages):
                        stripped = strip_image_blocks(
                            list(req.assembled.messages)
                        ).normalized
                        tf.vision_stripped = True
                        messages_for_call = stripped
                        # The capability declaration lied: flag the run so
                        # later turns re-plan attachments as omission notes
                        # and tool-side reads stop queueing pixels — without
                        # this every remaining turn repeats the 400+strip
                        # round-trip.
                        try:
                            run._vision_pixels_refused = True
                            getattr(run, "_pending_wire_images", []).clear()
                        except Exception:  # noqa: BLE001
                            pass
                        logger.warning(
                            "Run %s: model %s rejected image blocks; "
                            "retrying without image_url content",
                            run.run_id,
                            req.resolved_model,
                        )
                        await self._broadcaster.broadcast_vision_downgrade(
                            run,
                            model=req.resolved_model or "",
                            reason=(
                                "Model rejected image content; image "
                                "blocks omitted for this request."
                            ),
                        )
                        continue
                if recovery_attempt == 0 and tf.text_buffer:
                    logger.warning(
                        "Run %s: stream interrupted at %s chars, recovery. %s",
                        run.run_id,
                        len(tf.text_buffer),
                        stream_err,
                    )
                    continue
                raise

    async def _close_turn(self, st: RunLoopState, req: TurnRequest, tf: TurnFrame) -> LoopStep:
        """Close this turn's stream: flush the tail, flush thinking, update the fallback ledger, retry on silence, persist."""
        run = st.run; session = st.session
        # Drain the coalesced stream buffer before this turn's save_session and
        # before the next API call. The coalescing bounds only fire on chunk
        # arrival, so without this the last sub-bound slice of the answer waits
        # for the next event — the visible typing stalls exactly where the user
        # is reading, and a full-file save_session sits in between.
        await self._flush_stream_buffer(run)

        flush = tf.think_processor.flush()
        if flush:
            tf.reasoning_mgr.feed_reasoning_delta(flush)
            tf.thinking_buffer += flush
            await self._broadcaster.broadcast_thinking(
                run, flush, message_id=tf.cur_msg_id, seq=tf.cur_msg_seq
            )

        final_thinking = tf.reasoning_mgr.combine_with_think(tf.thinking_buffer)

        if stream_counts_as_success(
            tf.text_buffer, final_thinking, st.pending_tool_calls
        ):
            req.fallback_fm.record_success(
                run.session_id, req.provider, req.resolved_model
            )
        else:
            # Nothing usable came back at all. This is the only stream outcome
            # that may demote the model.
            logger.warning(
                "Run %s: empty model response (no text, no reasoning, no tool "
                "calls); recording fallback failure",
                run.run_id,
            )
            req.fallback_fm.record_failure(
                run.session_id, req.provider, req.resolved_model
            )

        if st.silence_detector.check(
            tf.text_buffer, final_thinking, st.pending_tool_calls
        ):
            if (
                not st.silence_detector.is_exhausted
                and tf.finish_reason in ("stop", "end_turn", None)
            ):
                logger.info(
                    "Run %s: silence detected (reasoning-only), retrying",
                    run.run_id,
                )
                if tf.assistant_msg_idx < len(session.messages):
                    session.messages.pop(tf.assistant_msg_idx)
                await self._cfg.save_session(run.session_id)
                # Transient nudge on THIS request only — never written
                # into session history (the old path appended it to the
                # persisted user message, polluting visible history and
                # the prompt prefix).
                st.pending_inject = SILENCE_NUDGE
                st.turn += 1
                return LoopStep.NEXT

        thinking_seconds: Optional[float] = None
        if tf.thinking_started_at is not None and tf.thinking_ended_at is not None:
            thinking_seconds = round(
                max(0.1, tf.thinking_ended_at - tf.thinking_started_at), 1
            )
        if tf.text_buffer or st.pending_tool_calls or final_thinking:
            self._write_assistant_turn(
                session,
                tf.assistant_msg_idx,
                tf.text_buffer,
                st.pending_tool_calls,
                final_thinking,
                thinking_seconds=thinking_seconds,
            )
            await self._cfg.save_session(run.session_id)
        return LoopStep.FALLTHROUGH
