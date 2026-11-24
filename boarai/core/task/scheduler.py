"""
Task scheduler for BoarAi.

Checks scheduled tasks periodically and executes them when their
next_run_at time arrives.  Supports:
- "once":     Run at a specific datetime
- "interval": Run every N minutes/hours/days (e.g. "30m", "2h", "7d")
- "cron":     Standard 5-field cron expression (e.g. "0 9 * * 1-5")

New in v2:
- Timezone-aware wall-clock comparisons (via timeutil)
- await execution completion before reschedule (manager.execute_task(wait=True))
- Reschedule on failure (configurable per schedule)
- QUEUED status handled: scheduler reaps queued tasks
- Periodic orphan recovery
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Optional, Set

from .manager import get_task_manager
from .status import TaskStatus
from .timeutil import now_for_schedule
from boarai.core.runtime import spawn

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 10  # seconds

RE_INTERVAL = re.compile(r"^(\d+)\s*(m|min|h|hr|d|day)s?\s*$", re.IGNORECASE)


class TaskScheduler:
    """Background scheduler for cron, interval, and once tasks."""

    def __init__(self) -> None:
        self._task_obj: Optional[asyncio.Task] = None
        self._running = False
        self._executing_tasks: Set[str] = set()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task_obj = asyncio.create_task(self._run_loop())
        logger.info(f"Task scheduler started (check every {CHECK_INTERVAL}s)")

    async def stop(self) -> None:
        self._running = False
        if self._task_obj and not self._task_obj.done():
            self._task_obj.cancel()
            try:
                await self._task_obj
            except asyncio.CancelledError:
                pass
        logger.info("Task scheduler stopped")

    async def _run_loop(self) -> None:
        cleanup_counter = 0
        while self._running:
            try:
                await self._check_scheduled_tasks()
                await self._reap_queued_tasks()
                cleanup_counter += 1
                if cleanup_counter >= 60:  # every ~10 min
                    cleanup_counter = 0
                    try:
                        await get_task_manager().cleanup_old_tasks(retention_days=7)
                    except Exception as e:
                        logger.warning(f"Scheduler cleanup error: {e}")
            except Exception as e:
                logger.error(f"Scheduler check error: {e}")
            await asyncio.sleep(CHECK_INTERVAL)

    async def _reap_queued_tasks(self) -> None:
        """Pick up and execute QUEUED tasks (retries, etc.)."""
        task_manager = get_task_manager()
        try:
            queued = await task_manager.list_tasks(status=TaskStatus.QUEUED)
        except Exception:
            return
        for t in queued:
            if t.task_id in self._executing_tasks or t.task_id in task_manager._running_handles:
                continue
            self._executing_tasks.add(t.task_id)
            spawn(self._execute_queued(task_manager, t))

    async def _execute_queued(self, tm, task) -> None:
        """Execute a queued task with cleanup."""
        try:
            await tm.execute_task(task.task_id)
        except Exception as e:
            logger.warning(f"Queued task {task.task_id} execute failed: {e}")
        finally:
            self._executing_tasks.discard(task.task_id)

    async def _check_scheduled_tasks(self) -> None:
        task_manager = get_task_manager()

        # Collect PENDING and QUEUED tasks
        all_pending = await task_manager.list_tasks(status=TaskStatus.PENDING)
        try:
            queued = await task_manager.list_tasks(status=TaskStatus.QUEUED)
        except Exception:
            queued = []

        tasks = all_pending + queued

        # Filter out already executing
        tasks = [
            t for t in tasks
            if t.task_id not in task_manager._running_handles
            and t.task_id not in self._executing_tasks
        ]

        due_count = 0
        for task in tasks:
            if not task.schedule or not task.schedule.type:
                continue

            sched = task.schedule
            tz = sched.timezone
            now = now_for_schedule(tz)

            should_execute = False

            # === once ===
            if sched.type == "once" and sched.next_run_at:
                if now >= sched.next_run_at:
                    should_execute = True
                    logger.info(f"Scheduler fires once task {task.task_id}: '{task.title}'")

            # === interval ===
            elif sched.type == "interval" and sched.expression:
                delta = self._parse_interval(sched.expression)
                if delta is None:
                    logger.warning(f"Invalid interval expression for {task.task_id}: {sched.expression}")
                    continue

                # Base time
                base = sched.next_run_at or task.created_at
                # Normalize naive
                if base and base.tzinfo is not None:
                    base = base.replace(tzinfo=None)

                if now >= base + delta:
                    # Do NOT write next_run_at here — defer to _execute_and_reschedule
                    # to avoid losing cycles on crash / skip.
                    should_execute = True
                    logger.info(
                        f"Scheduler fires interval task {task.task_id}: '{task.title}' "
                        f"(every {sched.expression})"
                    )

            # === cron ===
            elif sched.type == "cron" and sched.expression:
                try:
                    from croniter import croniter
                except ImportError:
                    logger.error_once = getattr(logger, 'error_once', logger.error)  # noqa
                    logger.error("croniter not installed, cannot schedule cron tasks")
                    continue

                if not sched.next_run_at:
                    try:
                        cron = croniter(sched.expression, now)
                        sched.next_run_at = cron.get_next(datetime)
                        await task_manager._save(task)
                        logger.info(f"Cron task {task.task_id} init, first at {sched.next_run_at}")
                    except Exception as e:
                        logger.error(f"Invalid cron for {task.task_id}: {sched.expression} - {e}")
                        continue

                if now >= sched.next_run_at:
                    should_execute = True
                    logger.info(f"Scheduler fires cron task {task.task_id}: '{task.title}' ({sched.expression})")

            if should_execute:
                self._executing_tasks.add(task.task_id)
                spawn(self._execute_and_reschedule(task_manager, task))
                due_count += 1

        if due_count:
            logger.info(f"Scheduler fired {due_count} due task(s)")

    async def _execute_and_reschedule(self, task_manager, task) -> None:
        """Execute + advance schedule + re-enable."""
        try:
            # Wait for full execution lifecycle (including result)
            await task_manager.execute_task(task.task_id, wait=True)

            # Reload fresh
            task = await task_manager.get_task(task.task_id)
            if not task or not task.schedule:
                return

            sched = task.schedule

            # --- Reschedule logic ---
            now = now_for_schedule(sched.timezone)

            # Reschedule next_run_at
            if sched.type == "once":
                return  # one-shot, never rescheduled

            # interval: compute next_run_at AFTER execution (not before)
            if sched.type == "interval" and sched.expression:
                delta = self._parse_interval(sched.expression)
                if delta:
                    base = sched.next_run_at or task.created_at or now
                    if base.tzinfo is not None:
                        base = base.replace(tzinfo=None)
                    # Catch-up: skip missed cycles, set next from now + delta
                    MAX_CATCH = 1000
                    new_next = base + delta
                    skip = 0
                    while now >= new_next and skip < MAX_CATCH:
                        new_next += delta
                        skip += 1
                    if skip >= MAX_CATCH:
                        logger.warning(f"Task {task.task_id} missed {skip}+ cycles, reset from now")
                        new_next = now + delta
                    sched.next_run_at = new_next

            if sched.type == "cron" and sched.expression:
                try:
                    from croniter import croniter
                    cron = croniter(sched.expression, now)
                    sched.next_run_at = cron.get_next(datetime)
                except (ImportError, Exception):
                    return
            # interval: next_run_at computed above (after execution, catch-up safe)

            # Determine whether to reschedule based on outcome
            if task.status == TaskStatus.COMPLETED:
                sched.consecutive_failures = 0
            elif task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
                sched.consecutive_failures += 1
                if sched.max_consecutive_failures > 0 and sched.consecutive_failures >= sched.max_consecutive_failures:
                    logger.warning(f"Task {task.task_id} reached max consecutive failures ({sched.max_consecutive_failures}), stopping")
                    await task_manager._save(task)
                    return
                if not sched.reschedule_on_failure:
                    logger.info(f"Task {task.task_id} reschedule_on_failure=False, stopping")
                    await task_manager._save(task)
                    return

            # Reset to PENDING for next cycle (clear execution state)
            await task_manager.update_task(
                task.task_id,
                status=TaskStatus.PENDING,
                progress=0.0,
                started_at=None,
                completed_at=None,
                result=None,
                error=None,
                retry_count=0,
            )
            await task_manager._save(task)
            logger.info(f"Task {task.task_id} rescheduled to {sched.next_run_at}")

        except Exception as e:
            logger.error(f"Scheduled execution failed for {task.task_id}: {e}", exc_info=True)
        finally:
            self._executing_tasks.discard(task.task_id)

    @staticmethod
    def _parse_interval(expression: str) -> Optional[timedelta]:
        m = RE_INTERVAL.match(expression.strip())
        if not m:
            return None
        value = int(m.group(1))
        unit = m.group(2).lower()
        if unit in ("m", "min"):
            return timedelta(minutes=value)
        elif unit in ("h", "hr"):
            return timedelta(hours=value)
        elif unit in ("d", "day"):
            return timedelta(days=value)
        return None


# Global singleton
_scheduler: Optional[TaskScheduler] = None


def get_task_scheduler() -> TaskScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = TaskScheduler()
    return _scheduler