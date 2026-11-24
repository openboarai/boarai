"""Run registry: lifecycle state, cancel, TTL cleanup."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from boarai.core.extension import get_tool_registry
from boarai.core.session.run_models import Run, RunStatus

logger = logging.getLogger(__name__)


class RunRegistry:
    """In-memory run map, per-session active pointer, cancel + TTL."""

    def __init__(self, ttl: int = 3600) -> None:
        self._runs: Dict[str, Run] = {}
        self._ttl = ttl
        self._session_active_runs: Dict[str, str] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._on_run_removed: Optional[Callable[[str], None]] = None

    def set_on_run_removed(self, cb: Callable[[str], None]) -> None:
        self._on_run_removed = cb

    def get(self, run_id: str) -> Optional[Run]:
        return self._runs.get(run_id)

    def all_runs(self) -> List[Run]:
        return list(self._runs.values())

    def remove_run(self, run_id: str) -> bool:
        """Remove a run from the registry and clear its active pointer."""
        if run_id not in self._runs:
            return False
        self._runs.pop(run_id)
        for sid, rid in list(self._session_active_runs.items()):
            if rid == run_id:
                self._session_active_runs.pop(sid, None)
        if self._on_run_removed:
            try:
                self._on_run_removed(run_id)
            except Exception:
                pass
        return True

    def create(
        self,
        session_id: str,
        *,
        model: Optional[str] = None,
        task_id: Optional[str] = None,
        completion_future: Optional[asyncio.Future] = None,
        agent_name: str = "default",
    ) -> Run:
        run_id = str(uuid.uuid4())
        run = Run(run_id, session_id, task_id=task_id)
        run.model = model
        run.completion_future = completion_future
        run._agent_name = agent_name
        self._runs[run_id] = run
        self._session_active_runs[session_id] = run_id
        return run

    def clear_active_if(self, session_id: str, run_id: str) -> None:
        if self._session_active_runs.get(session_id) == run_id:
            del self._session_active_runs[session_id]

    def get_active_run_id(self, session_id: str) -> Optional[str]:
        return self._session_active_runs.get(session_id)

    def get_run_dict(self, run_id: str) -> Optional[Dict[str, Any]]:
        run = self._runs.get(run_id)
        return run.to_dict() if run else None

    def get_session_runs(self, session_id: str) -> List[Dict[str, Any]]:
        return [
            run.to_dict()
            for run in self._runs.values()
            if run.session_id == session_id
        ]

    def get_active_run_for_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        # Include QUEUED: refresh mid-start must still restore stop button / stream
        live = (RunStatus.QUEUED, RunStatus.RUNNING)
        for run in self._runs.values():
            if run.session_id == session_id and run.status in live:
                return {
                    **run.to_dict(),
                    "current_turn_text": run.current_turn_text,
                }
        return None

    async def interrupt_active(
        self, session_id: str, reason: str = "new_run_created"
    ) -> None:
        old_run_id = self._session_active_runs.get(session_id)
        if old_run_id and old_run_id in self._runs:
            old_run = self._runs[old_run_id]
            if old_run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
                logger.info(
                    "Session %s: interrupting run %s (%s)",
                    session_id,
                    old_run_id,
                    reason,
                )
                await self.cancel_run_internal(old_run, reason=reason)

    async def cancel_run_internal(
        self,
        run: Run,
        reason: str = "user_cancelled",
    ) -> None:
        run.cancel_reason = reason
        if run.task and not run.task.done():
            run.task.cancel()
            try:
                await run.task
            except asyncio.CancelledError:
                pass
        run.status = RunStatus.CANCELLED
        run.updated_at = time.time()
        self.clear_active_if(run.session_id, run.run_id)

    async def cancel_run(self, run_id: str) -> bool:
        run = self._runs.get(run_id)
        if not run:
            return False

        if run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
            if getattr(run, "_in_tool_execution", False):
                run._cancelling = True
                logger.info(
                    "Run %s: tools executing, granting 3s grace "
                    "window for result flush before cancel",
                    run_id,
                )
                try:
                    await asyncio.wait_for(self._wait_tool_flush(run), timeout=3.0)
                except asyncio.TimeoutError:
                    logger.warning(
                        "Run %s: tool flush grace period expired, "
                        "forcing cancellation",
                        run_id,
                    )

            run.cancel_reason = "user_cancelled"
            run.status = RunStatus.CANCELLED
            run.completed_at = time.time()
            run.updated_at = time.time()

            if run.task and not run.task.done():
                run.task.cancel()
                try:
                    await run.task
                except asyncio.CancelledError:
                    pass

            await self._patch_orphan_tool_calls(run)
            logger.info("Cancelled run %s", run_id)
            return True

        return False

    async def _wait_tool_flush(self, run: Run) -> None:
        while getattr(run, "_in_tool_execution", False):
            await asyncio.sleep(0.1)

    async def _patch_orphan_tool_calls(self, run: Run) -> None:
        try:
            from boarai.core.session.manager import get_session_manager
            from boarai.core.session.orphan_tools import patch_orphan_tool_results
            from boarai.core.session.run.messages import TOOL_CANCELLED

            sm = get_session_manager()
            session = await sm.get_session(run.session_id)
            if not session or not session.messages:
                return

            # get_session may already heal on cold load; re-check for in-memory
            # orphans created mid-run after that load.
            orphans = patch_orphan_tool_results(session, content=TOOL_CANCELLED)
            if orphans:
                logger.info(
                    "Run %s: injecting %s orphan tool placeholders (%s)",
                    run.run_id,
                    len(orphans),
                    orphans,
                )
                await sm.save_session(run.session_id)

        except Exception as e:
            logger.warning(
                "Failed to patch orphan tool calls for run %s: %s",
                run.run_id,
                e,
            )

    @staticmethod
    def clear_run_rate_limits(run_id: str) -> None:
        try:
            get_tool_registry().reset_run_rate_limits(run_id)
        except Exception as e:
            logger.debug("reset_run_rate_limits(%s) skipped: %s", run_id, e)

    def mark_completed(self, run: Run) -> None:
        run.status = RunStatus.COMPLETED
        run.completed_at = time.time()
        run.updated_at = time.time()
        self.clear_run_rate_limits(run.run_id)
        self.clear_active_if(run.session_id, run.run_id)

    def mark_cancelled(self, run: Run) -> None:
        run.status = RunStatus.CANCELLED
        run.completed_at = time.time()
        run.updated_at = time.time()
        self.clear_run_rate_limits(run.run_id)
        self.clear_active_if(run.session_id, run.run_id)

    def mark_failed(self, run: Run, error: str) -> None:
        run.status = RunStatus.FAILED
        run.error = error
        run.completed_at = time.time()
        run.updated_at = time.time()
        self.clear_run_rate_limits(run.run_id)
        self.clear_active_if(run.session_id, run.run_id)

    async def start_cleanup(self) -> None:
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop_cleanup(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

    async def cancel_all_active(self) -> None:
        for run in list(self._runs.values()):
            if run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
                await self.cancel_run(run.run_id)

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(300)
                now = time.time()
                expired = [
                    run_id
                    for run_id, run in self._runs.items()
                    if run.status
                    in (
                        RunStatus.COMPLETED,
                        RunStatus.FAILED,
                        RunStatus.CANCELLED,
                    )
                    and (now - (run.completed_at or run.updated_at)) > self._ttl
                ]
                for run_id in expired:
                    for sid, rid in list(self._session_active_runs.items()):
                        if rid == run_id:
                            del self._session_active_runs[sid]
                    del self._runs[run_id]
                    if self._on_run_removed:
                        self._on_run_removed(run_id)

                if expired:
                    logger.debug("Cleaned up %s expired runs", len(expired))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Cleanup error: %s", e)