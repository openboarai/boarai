"""
Run Manager for BoarAi (facade).

Runs represent background conversation executions independent of client
connections. Implementation is split under core.session.run:

    RunRegistry      — lifecycle / cancel / TTL
    RunBroadcaster   — EventBus + WS fan-out
    ToolConfirmGate  — tool approval
    CompressionGate  — idle compress + create race
    RunExecutor      — conversation tool-loop

Public API is stable for api/session, api/run, task executor.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from boarai.core.session.run.broadcaster import RunBroadcaster
from boarai.core.session.run.compression_gate import CompressionGate
from boarai.core.session.run.executor import RunExecutor
from boarai.core.session.run.registry import RunRegistry
from boarai.core.session.run.tool_confirm import ToolConfirmGate
from boarai.core.session.run_models import Run, RunStatus

logger = logging.getLogger(__name__)


class RunManager:
    """Facade over run subsystem components."""

    def __init__(self, ttl: int = 3600) -> None:
        self._registry = RunRegistry(ttl=ttl)
        self._broadcaster = RunBroadcaster(self._registry)
        self._confirm = ToolConfirmGate(self._broadcaster)
        # Inject the registry lookup so the gate never imports run_manager
        # back (cycle-free dependency direction: manager → gate).
        self._compression = CompressionGate(
            active_run_predicate=lambda sid: (
                self._registry.get_active_run_for_session(sid) is not None
            )
        )
        self._executor = RunExecutor(
            registry=self._registry,
            broadcaster=self._broadcaster,
            confirm=self._confirm,
            compression=self._compression,
        )

    async def start(self) -> None:
        await self._registry.start_cleanup()
        logger.info("Run manager started")

    async def stop(self) -> None:
        await self._registry.stop_cleanup()
        await self._registry.cancel_all_active()
        logger.info("Run manager stopped")

    async def create_run(
        self,
        session_id: str,
        model: Optional[str] = None,
        task_id: Optional[str] = None,
        completion_future: Optional[asyncio.Future] = None,
        agent_name: str = "default",
        start_immediately: bool = True,
        model_source: str = "default",
        kind: str = "chat",
    ) -> str:
        """Create a run and optionally start it immediately."""
        self._compression.cancel_timer(session_id)
        await self._compression.wait_ready(session_id)
        await self._registry.interrupt_active(session_id, reason="new_run_created")
        # The interrupted run's unwind calls arm_after_run, which re-arms the
        # idle timer AFTER the cancel above — cancel it again so a long new
        # run can never race a mid-run compression (belt-and-braces; the
        # CompressionGate also guards on active runs itself).
        self._compression.cancel_timer(session_id)

        run = self._registry.create(
            session_id,
            model=model,
            task_id=task_id,
            completion_future=completion_future,
            agent_name=agent_name,
        )
        run._model_source = model_source or "default"
        run.kind = kind or "chat"

        if start_immediately:
            self.start_run(run.run_id)

        task_part = f", task={task_id}" if task_id else ""
        logger.info(
            "Created run %s for session %s (agent=%s, started=%s%s)",
            run.run_id,
            session_id,
            agent_name,
            start_immediately,
            task_part,
        )
        return run.run_id

    def start_run(self, run_id: str) -> bool:
        """Start a queued run after its client subscription is ready."""
        run = self._registry.get(run_id)
        if not run or run.status != RunStatus.QUEUED:
            return False

        run.task = asyncio.create_task(self._executor.execute(run))
        run.status = RunStatus.RUNNING
        run.updated_at = time.time()
        return True

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self._registry.get_run_dict(run_id)

    def get_run_object(self, run_id: str):
        """The in-memory Run object (None when unknown/expired)."""
        return self._registry.get(run_id)

    def get_session_runs(self, session_id: str) -> List[Dict[str, Any]]:
        return self._registry.get_session_runs(session_id)

    def all_runs(self) -> List["Run"]:
        """All in-flight runs across sessions (delegates to registry)."""
        return self._registry.all_runs()

    def remove_run(self, run_id: str) -> bool:
        """Remove a run from the registry and clear its active pointer."""
        return self._registry.remove_run(run_id)

    def get_active_run_for_session(
        self, session_id: str
    ) -> Optional[Dict[str, Any]]:
        return self._registry.get_active_run_for_session(session_id)

    async def cancel_run(self, run_id: str) -> bool:
        run = self._registry.get(run_id)
        result = await self._registry.cancel_run(run_id)
        self._confirm.cleanup_run(run_id)
        # A turn's interrupt reaches only the parent's own work; background
        # sub-agents are detached, so cancelling the run must fan out to them
        # too or they keep running (and billing) after the stop.
        if run is not None:
            await self.cancel_session_delegations(run.session_id)
        return result

    async def cancel_session_delegations(self, session_id: str) -> int:
        """Cancel background sub-agents of one session; returns the count."""
        try:
            from boarai.core.session.thinking.delegation import get_delegation_pool

            return await get_delegation_pool().interrupt_for_session(session_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("Delegation interrupt skipped for %s: %s", session_id, e)
            return 0

    def register_ws_callback(self, run_id: str, callback: Callable) -> bool:
        return self._broadcaster.register_ws_callback(run_id, callback)

    def unregister_ws_callback(self, run_id: str, callback: Callable) -> None:
        self._broadcaster.unregister_ws_callback(run_id, callback)

    def set_tool_auto_execute(self, auto: bool) -> None:
        self._confirm.set_tool_auto_execute(auto)

    def confirm_tool(
        self, run_id: str, confirmation_id: str, approved: bool,
        grant_scope: str = "once",
    ) -> bool:
        return self._confirm.confirm_tool(
            run_id, confirmation_id, approved, grant_scope=grant_scope
        )

    def clear_session_grants(self, session_id: str) -> None:
        self._confirm.clear_session_grants(session_id)

    def clear_session_grants_for(self, session_ids: list) -> None:
        """Bulk-clear session-scope approval grants (e.g. stale cleanup)."""
        for sid in session_ids or []:
            self._confirm.clear_session_grants(sid)


_run_manager: Optional[RunManager] = None


def get_run_manager() -> RunManager:
    """Get the global run manager instance."""
    global _run_manager
    if _run_manager is None:
        _run_manager = RunManager()
    return _run_manager