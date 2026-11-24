"""Run event broadcaster: EventBus + WS callbacks + session WS clients."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from boarai.core.event import EventType, publish_event

if TYPE_CHECKING:
    from boarai.core.session.run.registry import RunRegistry
    from boarai.core.session.run_models import Run

logger = logging.getLogger(__name__)

# Stream-text coalescing bounds (C4). Per-delta WS frames cost a JSON
# serialize + socket write each; batching at display rate cuts frame count
# ~10x without a perceptible typing delay. Either bound flushes: the char
# cap keeps fast bursts bounded, the time cap keeps slow drips live. Both
# are evaluated on chunk arrival, so a window opened by a slice that is then
# never followed — a stalled provider, a connection closed without a done
# chunk — is drained by a per-run timer instead (see _arm_chunk_timer).
CHUNK_COALESCE_SECONDS = 0.04
CHUNK_COALESCE_CHARS = 512


class RunBroadcaster:
    """Fan-out run events to EventBus, per-run callbacks, and session WS."""

    def __init__(self, registry: "RunRegistry") -> None:
        self._registry = registry
        self._ws_callbacks: Dict[str, List[Callable]] = {}
        self._chunk_buf: Dict[str, Dict[str, Any]] = {}
        self._chunk_timer: Dict[str, "asyncio.Task[None]"] = {}
        registry.set_on_run_removed(self.drop_run)

    def drop_run(self, run_id: str) -> None:
        self._ws_callbacks.pop(run_id, None)
        self._chunk_buf.pop(run_id, None)
        self._cancel_chunk_timer(run_id)

    def _cancel_chunk_timer(self, run_id: str) -> None:
        """Unregister and cancel a run's pending coalescing timer."""
        timer = self._chunk_timer.pop(run_id, None)
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()

    def _arm_chunk_timer(self, run_id: str) -> None:
        """Guarantee an open coalescing window drains with no further chunks."""
        if run_id in self._chunk_timer:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop: the arrival bounds and the terminal flush in
            # every finalize path still drain the buffer.
            return
        self._chunk_timer[run_id] = loop.create_task(self._drain_after_window(run_id))

    async def _drain_after_window(self, run_id: str) -> None:
        try:
            await asyncio.sleep(CHUNK_COALESCE_SECONDS)
            await self.flush_chunks(run_id)
        finally:
            # Identity-guarded: a newer window for the same run may already
            # own the slot, and popping it would leave that timer untracked.
            if self._chunk_timer.get(run_id) is asyncio.current_task():
                self._chunk_timer.pop(run_id, None)

    def register_ws_callback(self, run_id: str, callback: Callable) -> bool:
        if self._registry.get(run_id) is None:
            return False
        if run_id not in self._ws_callbacks:
            self._ws_callbacks[run_id] = []
        self._ws_callbacks[run_id].append(callback)
        return True

    def unregister_ws_callback(self, run_id: str, callback: Callable) -> None:
        if run_id in self._ws_callbacks and callback in self._ws_callbacks[run_id]:
            self._ws_callbacks[run_id].remove(callback)
            if not self._ws_callbacks[run_id]:
                del self._ws_callbacks[run_id]

    def has_audience(self, run_id: str, session_id: str) -> bool:
        """Whether anyone could answer an approval prompt for this run."""
        if self._ws_callbacks.get(run_id):
            return True
        try:
            from boarai.core.api import get_session_api_handler

            handler = get_session_api_handler()
            return bool(handler._ws_clients.get(session_id))
        except Exception:  # noqa: BLE001
            return True

    async def broadcast_chunk(
        self, run: "Run", content: str, message_id: str = "", seq: int = 0
    ) -> None:
        """Buffer stream text; emit one coalesced frame per bound or flush."""
        buf = self._chunk_buf.get(run.run_id)
        # A buffer still bound to a previous turn's message_id must be flushed
        # before a new message id arrives. Keeping the stale binding would
        # route the new turn's text under the old bubble: the frontend appends
        # it there (live concatenation) while the persisted turn lands as its
        # own message — the same narration shows twice mid-run.
        if buf is not None and message_id and buf["message_id"] != message_id:
            await self.flush_chunks(run.run_id)
            buf = None
        if buf is None:
            buf = {
                "parts": [],
                "chars": 0,
                "started": time.monotonic(),
                "message_id": message_id or "",
                "seq": seq,
                "session_id": run.session_id,
            }
            self._chunk_buf[run.run_id] = buf
            self._arm_chunk_timer(run.run_id)
        buf["parts"].append(content)
        buf["chars"] += len(content)
        if (
            buf["chars"] >= CHUNK_COALESCE_CHARS
            or time.monotonic() - buf["started"] >= CHUNK_COALESCE_SECONDS
        ):
            await self.flush_chunks(run.run_id)

    async def flush_chunks(self, run_id: str) -> None:
        """Emit the buffered stream text for one run as a single frame."""
        self._cancel_chunk_timer(run_id)
        buf = self._chunk_buf.pop(run_id, None)
        if not buf or not buf["parts"]:
            return
        content = "".join(buf["parts"])
        event_data = {
            "run_id": run_id,
            "type": "chunk",
            "content": content,
            "message_id": buf["message_id"],
            "session_id": buf["session_id"],
            "seq": buf["seq"],
        }
        await publish_event(EventType.RUN_CHUNK, event_data, "run_manager")
        await self.broadcast_to_ws(
            run_id,
            {
                "type": "chunk",
                "content": content,
                "message_id": buf["message_id"],
                "seq": buf["seq"],
            },
        )

    async def broadcast_thinking(
        self, run: "Run", thinking: str, message_id: str = "", seq: int = 0
    ) -> None:
        if not thinking:
            return
        await self.flush_chunks(run.run_id)
        run.full_thinking += thinking
        event_data = {
            "run_id": run.run_id,
            "type": "thinking",
            "content": thinking,
            "message_id": message_id or "",
            "session_id": run.session_id,
            "seq": seq,
        }
        await publish_event(EventType.THINKING_DELTA, event_data, "run_manager")
        await self.broadcast_to_ws(
            run.run_id,
            {"type": "thinking", "content": thinking, "message_id": message_id or "", "seq": seq},
        )

    async def broadcast_usage(self, run: "Run", agg: Dict[str, Any]) -> None:
        """Push a live cumulative usage snapshot for the run."""
        if not agg:
            return
        await self.flush_chunks(run.run_id)
        event_data = {
            "run_id": run.run_id,
            "type": "usage",
            "session_id": run.session_id,
            "turns": int(agg.get("turns", 0) or 0),
            "input": int(agg.get("input", 0) or 0),
            "completion": int(agg.get("completion", 0) or 0),
            "cache_read": int(agg.get("cache_read", 0) or 0),
            "cache_creation": int(agg.get("cache_creation", 0) or 0),
        }
        await publish_event(EventType.RUN_USAGE, event_data, "run_manager")
        await self.broadcast_to_ws(run.run_id, event_data)

    async def broadcast_retry(
        self,
        run: "Run",
        attempt: int,
        max_attempts: int,
        error: str,
        delay_seconds: float = 0.0,
    ) -> None:
        """Notify clients that the provider is retrying a transient failure."""
        await self.flush_chunks(run.run_id)
        event_data = {
            "run_id": run.run_id,
            "type": "retry",
            "session_id": run.session_id,
            "attempt": int(attempt),
            "max_attempts": int(max_attempts),
            "error": (error or "")[:500],
            "delay_seconds": float(delay_seconds or 0.0),
        }
        logger.info(
            "Run %s retry %s/%s after error: %s (delay=%.1fs)",
            run.run_id,
            int(attempt),
            int(max_attempts),
            (error or "")[:200],
            float(delay_seconds or 0.0),
        )
        await publish_event(EventType.RUN_RETRY, event_data, "run_manager")
        await self.broadcast_to_ws(run.run_id, event_data)

    async def broadcast_vision_downgrade(
        self,
        run: "Run",
        *,
        model: str = "",
        reason: str = "",
    ) -> None:
        """Notify clients that image blocks were stripped for a text-only model."""
        await self.flush_chunks(run.run_id)
        event_data = {
            "run_id": run.run_id,
            "type": "vision_downgrade",
            "session_id": run.session_id,
            "model": model or "",
            "reason": (reason or "")[:500],
        }
        logger.warning(
            "Run %s: vision downgrade — %s (model=%s)",
            run.run_id,
            (reason or "")[:200],
            model or "",
        )
        await publish_event(EventType.RUN_VISION_DOWNGRADE, event_data, "run_manager")
        await self.broadcast_to_ws(run.run_id, event_data)

    async def broadcast_diff(self, run: "Run", payload: Dict[str, Any]) -> None:
        """Fan-out the aggregated per-run file changes + unified diff."""
        files_changed = payload.get("files_changed", [])
        stats = payload.get("stats", {})
        await self.flush_chunks(run.run_id)
        base = {
            "run_id": run.run_id,
            "type": "diff",
            "session_id": run.session_id,
            "files_changed": files_changed,
            "stats": stats,
        }
        await publish_event(EventType.RUN_DIFF, dict(base), "run_manager")
        await self.broadcast_to_ws(
            run.run_id, {**base, "unified_diff": payload.get("unified_diff", "")}
        )

    async def broadcast_tool_event(
        self,
        run: "Run",
        event_type: str,
        tool_calls: List[Dict[str, Any]],
        tool_results: Optional[List[Dict[str, Any]]] = None,
        message_id: str = "",
        seq: int = 0,
    ) -> None:
        await self.flush_chunks(run.run_id)
        tool_info = []
        for tc in tool_calls:
            func = tc.get("function", {}) or {}
            args = func.get("arguments", "")
            tool_info.append(
                {
                    "id": tc.get("id", ""),
                    "name": func.get("name", "") or tc.get("name", ""),
                    "arguments": args,
                }
            )
        # Keep a stable "tool.*" type for EventBus and WS clients.
        # store.js listens for run.tool.pending / run.tool.start / run.tool.finished.
        normalized = event_type if event_type.startswith("tool.") else f"tool.{event_type}"
        event_data = {
            "run_id": run.run_id,
            "type": normalized,
            "tools": tool_info,
            "message_id": message_id or "",
            "session_id": run.session_id,
            "seq": seq,
        }
        if tool_results is not None:
            # Cap payload size so proxies / browsers do not drop the frame.
            # Full results are already persisted on the session; UI can loadMessages.
            event_data["tool_results"] = self._compact_tool_results(tool_results)
        await publish_event(normalized, event_data, "run_manager")
        await self.broadcast_to_ws(run.run_id, event_data)

    async def broadcast_tool_output(
        self,
        run_id: str,
        session_id: str,
        tool_call_id: str,
        name: str,
        stream: str,
        delta: str,
    ) -> None:
        """Incremental output from a RUNNING tool (e.g. shell stdout lines)."""
        await self.flush_chunks(run_id)
        payload = {
            "run_id": run_id,
            "type": "tool.output",
            "session_id": session_id,
            "tool_call_id": tool_call_id,
            "name": name,
            "stream": stream,
            "delta": delta,
        }
        await publish_event(EventType.TOOL_OUTPUT, dict(payload), "tool_registry")
        await self.broadcast_to_ws(run_id, payload)

    @staticmethod
    def _compact_tool_results(
        tool_results: List[Dict[str, Any]],
        *,
        max_content: int = 4000,
    ) -> List[Dict[str, Any]]:
        """Shrink tool_results for WS fan-out; keep ids so UI can pair status."""
        out: List[Dict[str, Any]] = []
        for tr in tool_results or []:
            item = dict(tr)
            content = item.get("content")
            if isinstance(content, str) and len(content) > max_content:
                item["content"] = content[:max_content] + "\n…(truncated for live UI; full result in session history)"
                item["content_truncated"] = True
            # Drop bulky nested result blob for wire; session JSON has full copy
            if isinstance(item.get("result"), dict):
                res = item["result"]
                slim = {
                    k: res.get(k)
                    for k in ("success", "exit_code", "error", "command")
                    if k in res
                }
                stdout = res.get("stdout")
                if isinstance(stdout, str) and stdout:
                    slim["stdout"] = stdout[:1500] + ("…" if len(stdout) > 1500 else "")
                item["result"] = slim
            out.append(item)
        return out

    async def broadcast_to_ws(self, run_id: str, event: Dict[str, Any]) -> None:
        run = self._registry.get(run_id)
        if not run:
            return

        event_type = str(event.get("type", "event"))
        if event_type.startswith("run."):
            event_type = event_type[4:]
        payload = {
            **event,
            "type": event_type,
            "run_id": event.get("run_id", run.run_id),
            "session_id": event.get("session_id", run.session_id),
        }
        message = {"type": f"run.{event_type}", "payload": payload}

        if run_id in self._ws_callbacks:
            dead = []
            for callback in list(self._ws_callbacks[run_id]):
                try:
                    result = callback(payload)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    dead.append(callback)
            for callback in dead:
                self.unregister_ws_callback(run_id, callback)

        try:
            from boarai.core.api import get_session_api_handler

            handler = get_session_api_handler()
            clients = handler._ws_clients.get(run.session_id, set())
            if not clients:
                logger.debug(
                    "No WS clients subscribed for session %s (run %s event %s)",
                    run.session_id,
                    run_id,
                    event_type,
                )
            disconnected = set()
            for ws in list(clients):
                try:
                    await ws.send_json(message)
                except Exception as send_err:
                    logger.warning(
                        "WS send failed for run %s event %s: %s",
                        run_id,
                        event_type,
                        send_err,
                    )
                    disconnected.add(ws)
            for ws in disconnected:
                handler._discard_ws_client(ws)
        except Exception as e:
            logger.warning("Failed to broadcast run %s event %s: %s", run_id, event_type, e)