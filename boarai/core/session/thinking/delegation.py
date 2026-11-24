"""Background sub-agent delegation with a per-parent concurrency cap.

``SubAgentSession.run()`` is blocking: a parent that delegates a task cannot do
anything else until it returns, so a parent that wants three investigations run
in parallel has to make three sequential tool calls. Worse, a long sub-agent
holds the parent's turn open for its whole duration.

This module adds the two things that were missing:

**Background submission.** ``submit`` returns a delegation id immediately and
runs the sub-agent as a spawned task. The parent keeps its turn, and collects
the outcome later through ``await_result`` (the ``get_subagent_result`` tool).

**A per-parent concurrency cap.** ``max_concurrent_children`` (default 3) bounds
how many of one session's children run at once. It is per parent, not global:
two sessions delegating concurrently should not starve each other, but one
session should not be able to fan out without limit either. Submissions beyond
the cap are QUEUED rather than rejected, so the parent gets an id back either
way and never has to retry.

Lifetime notes:

* Tasks are created through :func:`core.runtime.spawn`, so a delegation cannot
  be garbage-collected mid-flight.
* A parent run that is stopped must not leave children running.
  ``interrupt_for_session`` is that stop fan-out.
* Finished delegations are retained (bounded) so a parent that polls late still
  finds its result. Only records the parent has already consumed are pruned.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

from boarai.core.runtime import spawn

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENT_CHILDREN = 3
"""Per-parent cap on simultaneously RUNNING delegations."""

DEFAULT_RETAIN_FINISHED = 64
"""How many delegation records to keep per parent for late polling."""

_POLL_INTERVAL = 0.02
"""Backstop poll for ``await_result``; only reached if a wake is missed."""


class DelegationStatus(str, Enum):
    """Lifecycle of one background delegation."""

    QUEUED = "queued"
    """Accepted, waiting for a concurrency slot."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({
    DelegationStatus.COMPLETED,
    DelegationStatus.FAILED,
    DelegationStatus.CANCELLED,
})

DelegationRunner = Callable[["Delegation"], Awaitable[Dict[str, Any]]]


@dataclass
class Delegation:
    """One background sub-agent run."""

    delegation_id: str
    session_id: str
    preset: str
    instruction: str
    max_turns: int = 10
    parent_agent_name: str = "default"
    status: DelegationStatus = DelegationStatus.QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    consumed: bool = False
    """Set when the parent collected the result, so pruning can reclaim it."""
    subagent_id: str = ""
    task: Optional["asyncio.Task[Any]"] = field(default=None, repr=False)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def duration(self) -> Optional[float]:
        """Elapsed run seconds, or None if it never started."""
        if self.started_at is None:
            return None
        end = self.finished_at if self.finished_at is not None else time.time()
        return round(end - self.started_at, 3)

    def to_dict(self, *, include_result: bool = False) -> Dict[str, Any]:
        """Serializable view for tool results and API payloads."""
        out: Dict[str, Any] = {
            "delegation_id": self.delegation_id,
            "session_id": self.session_id,
            "preset": self.preset,
            "status": self.status.value,
            "subagent_id": self.subagent_id,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration(),
            "consumed": self.consumed,
        }
        if self.error is not None:
            out["error"] = self.error
        if include_result:
            out["instruction"] = self.instruction
            out["result"] = self.result
        return out


class DelegationPool:
    """Background delegations, capped per parent session."""

    def __init__(
        self,
        max_concurrent_children: int = DEFAULT_MAX_CONCURRENT_CHILDREN,
        retain_finished: int = DEFAULT_RETAIN_FINISHED,
        runner: Optional[DelegationRunner] = None,
    ) -> None:
        """Initialize with a per-parent concurrency cap, record retention size, and optional runner."""
        self._max = max(1, int(max_concurrent_children))
        self._retain = max(1, int(retain_finished))
        self._runner = runner
        self._delegations: Dict[str, Delegation] = {}
        # Insertion-ordered ids per parent, so pruning drops the oldest first.
        self._by_session: Dict[str, List[str]] = {}
        self._semaphores: Dict[str, asyncio.Semaphore] = {}
        self._waiters: Dict[str, List["asyncio.Future[None]"]] = {}

    # --- Introspection ---

    @property
    def max_concurrent_children(self) -> int:
        return self._max

    def get(self, delegation_id: str) -> Optional[Delegation]:
        return self._delegations.get(delegation_id)

    def list_for_session(self, session_id: str) -> List[Delegation]:
        """All retained delegations of one parent, in submission order."""
        return [
            self._delegations[did]
            for did in self._by_session.get(session_id, [])
            if did in self._delegations
        ]

    def running_for_session(self, session_id: str) -> List[Delegation]:
        return [
            d for d in self.list_for_session(session_id)
            if d.status is DelegationStatus.RUNNING
        ]

    def pending_for_session(self, session_id: str) -> int:
        """Queued + running count: children this parent still owes."""
        return sum(1 for d in self.list_for_session(session_id) if not d.is_terminal)

    def finished_unconsumed(self, session_id: str) -> List[Delegation]:
        """Terminal delegations the parent has not collected yet."""
        return [
            d for d in self.list_for_session(session_id)
            if d.is_terminal and not d.consumed
        ]

    # --- Submission ---

    def submit(
        self,
        session_id: str,
        preset: str,
        instruction: str,
        *,
        max_turns: int = 10,
        parent_agent_name: str = "default",
    ) -> Delegation:
        """Accept a delegation and start it in the background."""
        delegation = Delegation(
            delegation_id=f"dlg_{uuid.uuid4().hex[:10]}",
            session_id=session_id,
            preset=preset,
            instruction=instruction,
            max_turns=max(1, min(int(max_turns or 10), 100)),
            parent_agent_name=parent_agent_name or "default",
        )
        self._delegations[delegation.delegation_id] = delegation
        self._by_session.setdefault(session_id, []).append(delegation.delegation_id)
        self._prune(session_id)

        # spawn (not create_task) so the task cannot be collected mid-flight.
        delegation.task = spawn(
            self._execute(delegation),
            name=f"delegation-{delegation.delegation_id}",
        )
        logger.info(
            "Delegation %s submitted (session=%s preset=%s cap=%d pending=%d)",
            delegation.delegation_id, session_id, preset, self._max,
            self.pending_for_session(session_id),
        )
        return delegation

    def _semaphore(self, session_id: str) -> asyncio.Semaphore:
        sem = self._semaphores.get(session_id)
        if sem is None:
            # Created lazily so the semaphore binds to the loop that actually
            # runs the delegations rather than to import time.
            sem = asyncio.Semaphore(self._max)
            self._semaphores[session_id] = sem
        return sem

    async def _execute(self, delegation: Delegation) -> None:
        """Wait for a slot, run the sub-agent, record the outcome."""
        sem = self._semaphore(delegation.session_id)
        try:
            async with sem:
                if delegation.status is DelegationStatus.CANCELLED:
                    # Interrupted while it was still queued.
                    return
                delegation.status = DelegationStatus.RUNNING
                delegation.started_at = time.time()
                await self._publish_start(delegation)
                try:
                    result = await self._run(delegation)
                    delegation.result = result
                    delegation.status = DelegationStatus.COMPLETED
                    delegation.subagent_id = str(result.get("subagent_id", "") or "")
                except asyncio.CancelledError:
                    self._mark_cancelled(delegation, "cancelled")
                    raise
                except Exception as e:  # noqa: BLE001
                    delegation.status = DelegationStatus.FAILED
                    delegation.error = f"{type(e).__name__}: {e}"
                    delegation.finished_at = time.time()
                    logger.warning(
                        "Delegation %s failed: %s", delegation.delegation_id, e,
                    )
                else:
                    delegation.finished_at = time.time()
        except asyncio.CancelledError:
            if not delegation.is_terminal:
                self._mark_cancelled(delegation, "cancelled")
            raise
        finally:
            await self._publish_end(delegation)
            self._wake_waiters(delegation)

    @staticmethod
    def _mark_cancelled(delegation: Delegation, reason: str) -> None:
        delegation.status = DelegationStatus.CANCELLED
        delegation.error = reason
        delegation.finished_at = time.time()

    async def _run(self, delegation: Delegation) -> Dict[str, Any]:
        if self._runner is not None:
            return await self._runner(delegation)
        return await _default_runner(delegation)

    # --- Collection ---

    async def await_result(
        self,
        delegation_id: str,
        timeout: Optional[float] = None,
        *,
        mark_consumed: bool = True,
    ) -> Delegation:
        """Wait for a delegation to finish and return its record."""
        delegation = self._delegations.get(delegation_id)
        if delegation is None:
            raise KeyError(f"unknown delegation id: {delegation_id}")

        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + max(0.0, timeout)
        # Re-check the terminal state on every wake: `_execute` awaits its end
        # event before waking waiters, so a wake can be missed. The bounded
        # poll is the backstop that turns a missed wake into a short delay
        # instead of a hang until the caller's own timeout.
        while not delegation.is_terminal:
            waiter: "asyncio.Future[None]" = loop.create_future()
            waiters = self._waiters.setdefault(delegation_id, [])
            waiters.append(waiter)
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                if waiter in waiters:
                    waiters.remove(waiter)
                raise asyncio.TimeoutError(
                    f"delegation {delegation_id} still {delegation.status.value}"
                )
            slice_seconds = (
                _POLL_INTERVAL if remaining is None
                else min(_POLL_INTERVAL, remaining)
            )
            try:
                await asyncio.wait_for(waiter, timeout=slice_seconds)
            except asyncio.TimeoutError:
                pass
            finally:
                if waiter in waiters:
                    waiters.remove(waiter)
            if deadline is not None and loop.time() >= deadline:
                if not delegation.is_terminal:
                    raise asyncio.TimeoutError(
                        f"delegation {delegation_id} still "
                        f"{delegation.status.value} after {timeout}s"
                    )

        if mark_consumed:
            delegation.consumed = True
        return delegation

    def _wake_waiters(self, delegation: Delegation) -> None:
        for waiter in self._waiters.pop(delegation.delegation_id, []):
            if not waiter.done():
                waiter.set_result(None)

    # --- Stop fan-out ---

    async def interrupt_for_session(self, session_id: str) -> int:
        """Cancel every non-terminal delegation of one parent."""
        cancelled = 0
        for delegation in self.list_for_session(session_id):
            if delegation.is_terminal:
                continue
            task = delegation.task
            if task is not None and not task.done():
                task.cancel()
            self._mark_cancelled(delegation, "interrupted")
            cancelled += 1
            self._wake_waiters(delegation)
        if cancelled:
            logger.info(
                "Interrupted %s background delegation(s) for session %s",
                cancelled, session_id,
            )
        return cancelled

    # --- Retention ---

    def _prune(self, session_id: str) -> None:
        """Drop the oldest consumed delegations once the cap is exceeded."""
        ids = self._by_session.get(session_id)
        if not ids or len(ids) <= self._retain:
            return
        consumed = [
            did for did in ids
            if (d := self._delegations.get(did)) is not None
            and d.is_terminal and d.consumed
        ]
        overflow = len(ids) - self._retain
        for did in consumed[:overflow]:
            self._delegations.pop(did, None)
        self._by_session[session_id] = [
            did for did in ids if did in self._delegations
        ]

    def clear(self) -> None:
        """Cancel everything and drop all state (tests and shutdown)."""
        for delegation in list(self._delegations.values()):
            task = delegation.task
            if task is not None and not task.done():
                task.cancel()
        self._delegations.clear()
        self._by_session.clear()
        self._semaphores.clear()
        for waiters in self._waiters.values():
            for waiter in waiters:
                if not waiter.done():
                    waiter.cancel()
        self._waiters.clear()

    # --- Events ---

    @staticmethod
    async def _publish_start(delegation: Delegation) -> None:
        try:
            from boarai.core.event import EventType, publish_event

            await publish_event(
                EventType.AGENT_SUBAGENT_START,
                {
                    "session_id": delegation.session_id,
                    "delegation_id": delegation.delegation_id,
                    "preset": delegation.preset,
                    "instruction": delegation.instruction[:200],
                    "background": True,
                },
                "delegation",
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("delegation start event skipped: %s", e)

    @staticmethod
    async def _publish_end(delegation: Delegation) -> None:
        try:
            from boarai.core.event import EventType, publish_event

            event = getattr(EventType, "AGENT_SUBAGENT_END", None)
            if event is None:
                return
            await publish_event(
                event,
                {
                    "session_id": delegation.session_id,
                    "delegation_id": delegation.delegation_id,
                    "subagent_id": delegation.subagent_id,
                    "status": delegation.status.value,
                    "error": delegation.error,
                    "duration_seconds": delegation.duration(),
                    "background": True,
                },
                "delegation",
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("delegation end event skipped: %s", e)


async def _default_runner(delegation: Delegation) -> Dict[str, Any]:
    """Production runner: drive a real ``SubAgentSession``."""
    from .subagent import SubAgentSession

    session = SubAgentSession(
        session_id=delegation.session_id,
        preset_name=delegation.preset,
        instruction=delegation.instruction,
        max_turns=delegation.max_turns,
        parent_agent_name=delegation.parent_agent_name,
    )
    delegation.subagent_id = session.subagent_id
    return await session.run()


# --- Singleton ---

_pool: Optional[DelegationPool] = None


def get_delegation_pool(
    max_concurrent_children: Optional[int] = None,
) -> DelegationPool:
    """Get (or create) the process-wide delegation pool."""
    global _pool
    if _pool is None:
        if max_concurrent_children is None:
            max_concurrent_children = _configured_limit()
        _pool = DelegationPool(
            max_concurrent_children=(
                max_concurrent_children
                if max_concurrent_children is not None
                else DEFAULT_MAX_CONCURRENT_CHILDREN
            ),
        )
    return _pool


def reset_delegation_pool() -> None:
    """Drop the singleton (tests and config reloads)."""
    global _pool
    if _pool is not None:
        _pool.clear()
    _pool = None


def _configured_limit() -> Optional[int]:
    """Read ``run.max_concurrent_children`` from config, or None if unset."""
    try:
        from boarai.core.session.run.route import load_run_config

        value = load_run_config().get("max_concurrent_children")
        return int(value) if value is not None else None
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "DEFAULT_MAX_CONCURRENT_CHILDREN",
    "DEFAULT_RETAIN_FINISHED",
    "Delegation",
    "DelegationPool",
    "DelegationRunner",
    "DelegationStatus",
    "TERMINAL_STATUSES",
    "get_delegation_pool",
    "reset_delegation_pool",
]
