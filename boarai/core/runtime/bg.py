"""Fire-and-forget task spawning that survives garbage collection.

:func:`asyncio.create_task` returns a task the event loop holds only weakly. A
caller that discards the return value keeps no strong reference either, so the
task can be collected mid-flight and its work silently stops. Every bare
``asyncio.create_task(...)`` whose result is dropped is exposed to this.

:func:`spawn` keeps the task in a module-level set for the duration of its run
and removes it on completion, which is the pattern the CPython documentation
recommends and the one ``CompressionGate`` already used locally for its
compression tasks.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine, Optional, Set

logger = logging.getLogger(__name__)

# Strong references to in-flight background tasks. Entries are removed by the
# done callback, so this set holds only tasks that are still running.
_tasks: Set["asyncio.Task[Any]"] = set()


def _discard(task: "asyncio.Task[Any]") -> None:
    _tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        # Nothing awaits a spawned task, so an exception raised inside it would
        # otherwise surface only as an "exception was never retrieved" warning
        # at garbage-collection time, with no context about which task failed.
        logger.warning("Background task failed: %r", exc, exc_info=exc)


def spawn(coro: Coroutine[Any, Any, Any], *, name: Optional[str] = None) -> "asyncio.Task[Any]":
    """Schedule ``coro`` on the running loop and keep it alive until it finishes."""
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_discard)
    return task


def pending_count() -> int:
    """Number of spawned tasks that have not finished. Test and status helper."""
    return len(_tasks)


async def drain(timeout: Optional[float] = None) -> None:
    """Await every spawned task, for orderly shutdown."""
    pending = list(_tasks)
    if not pending:
        return
    if timeout is None:
        await asyncio.gather(*pending, return_exceptions=True)
        return
    await asyncio.wait(pending, timeout=timeout)
