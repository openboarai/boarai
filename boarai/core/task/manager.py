"""
Task manager for BoarAi task system.

TaskManager is the central orchestrator for all task operations.
It manages:
- Task CRUD (create, read, update, delete)
- Executor registration (extensible task types)
- Task execution lifecycle (locked, waitable, retry-capable)
- Sub-task tree with auto-pause/resume of parent
- Run history recording
- Startup recovery of orphaned tasks
- Publishing task events via EventBus
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .kind import TaskKind
from .status import TaskStatus
from .models import Task, TaskStep, TaskResult, TaskSchedule, TaskRunRecord
from .storage import TaskStorage
from .executor import (
    BaseTaskExecutor,
    ExecContext,
    ScriptExecutor,
    AIExecutor,
)
from . import events as task_events
from boarai.core.event import EventBus, get_event_bus, publish_event
from boarai.core.runtime import spawn

logger = logging.getLogger(__name__)

MAX_RUN_HISTORY = 50  # keep latest N runs

# --- State guard helpers ---

# Max exponential-backoff delay for task retry (seconds)
_MAX_RETRY_BACKOFF = 60.0

# Allowed starting states for execution (post-guard)
_ALLOWED_START = frozenset({TaskStatus.PENDING, TaskStatus.PAUSED, TaskStatus.QUEUED, TaskStatus.COMPLETED})

# States that must NEVER enter execute (already active or explicitly aborted)
_BLOCKED_START = frozenset({
    TaskStatus.RUNNING,
    TaskStatus.CANCELLED, TaskStatus.SKIPPED,
})


def _can_start(task: Task) -> bool:
    """Return True when the task is eligible to begin execution."""
    if task.status in _BLOCKED_START:
        return False
    if task.status == TaskStatus.FAILED:
        return task.retry_count < task.max_retries
    return task.status in _ALLOWED_START


class TaskManager:
    """Central task orchestrator."""

    def __init__(
        self,
        storage_path: Optional[Path] = None,
        event_bus: Optional[EventBus] = None,
        llm_service: Any = None,
        session_manager: Any = None,
        run_manager: Any = None,
    ):
        self._event_bus = event_bus or get_event_bus()
        self._storage = TaskStorage(storage_path) if storage_path else None
        self._llm = llm_service
        self._session_manager = session_manager
        self._run_manager = run_manager

        # In-memory task cache
        self._tasks: Dict[str, Task] = {}
        self._lock = asyncio.Lock()

        # Running execution handles (task_id -> asyncio.Task)
        self._running_handles: Dict[str, asyncio.Task] = {}

        # Per-task locks (span full execution, NOT acquired by cancel/pause)
        self._execution_locks: Dict[str, asyncio.Lock] = {}

        # Cancel / pause intent (memory-only — cleared on execution disposition)
        self._cancel_intent: set[str] = set()
        self._pause_intent: set[str] = set()

        # Reverse dependency index: dep_id -> set of waiter task_ids.
        # Built lazily on demand and maintained by create/update/delete.
        self._dep_waiters: Dict[str, set] = {}

        # Registered executors
        self._executors: Dict[TaskKind, BaseTaskExecutor] = {}
        self._register_core_executors()

    # --- Late binding (P1-7 workaround) ---

    def bind_if_missing(
        self,
        *,
        storage_path: Optional[Path] = None,
        session_manager: Any = None,
        run_manager: Any = None,
        llm_service: Any = None,
    ) -> None:
        """Fill optional dependencies if they are currently None. Idempotent."""
        if self._storage is None and storage_path is not None:
            self._storage = TaskStorage(storage_path)
        if self._session_manager is None and session_manager is not None:
            self._session_manager = session_manager
        if self._run_manager is None and run_manager is not None:
            self._run_manager = run_manager
        if self._llm is None and llm_service is not None:
            self._llm = llm_service

    # --- Executor registry ---

    def _register_core_executors(self) -> None:
        self._executors[TaskKind.SCRIPT] = ScriptExecutor()
        self._executors[TaskKind.AI] = AIExecutor()
        logger.info("Registered core executors: script, ai")

    def register_executor(self, kind: TaskKind, executor: BaseTaskExecutor) -> None:
        if kind in self._executors:
            raise ValueError(f"Executor for kind '{kind}' is already registered")
        self._executors[kind] = executor
        logger.info(f"Registered executor for kind: {kind}")

    def get_executor(self, kind: TaskKind) -> Optional[BaseTaskExecutor]:
        return self._executors.get(kind)

    def list_executor_kinds(self) -> List[str]:
        return [k.value for k in self._executors]

    # --- Helper: execution lock ---

    def _get_exec_lock(self, task_id: str) -> asyncio.Lock:
        if task_id not in self._execution_locks:
            self._execution_locks[task_id] = asyncio.Lock()
        return self._execution_locks[task_id]

    # --- Task CRUD ---

    async def create_task(
        self,
        title: str,
        description: str = "",
        kind: TaskKind = TaskKind.AI,
        script_path: Optional[str] = None,
        script_args: Optional[List[str]] = None,
        script_env: Optional[Dict[str, str]] = None,
        prompt: Optional[str] = None,
        model: Optional[str] = None,
        steps: Optional[List[TaskStep]] = None,
        session_id: str = "",
        session_mode: str = "once",
        parent_task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        schedule: Optional[TaskSchedule] = None,
        max_retries: int = 0,
        timeout_seconds: int = 0,
        depends_on: Optional[List[str]] = None,
    ) -> Task:
        if isinstance(kind, str):
            kind = TaskKind(kind)
        async with self._lock:
            task_id = Task.generate_id()

            # Determine root_task_id
            root_task_id = None
            if parent_task_id:
                parent = await self._resolve_task(parent_task_id)
                if parent:
                    root_task_id = Task.compute_root_id(parent_task_id, parent.root_task_id)

            task = Task(
                task_id=task_id,
                title=title,
                description=description,
                kind=kind,
                script_path=script_path,
                script_args=script_args,
                script_env=script_env,
                prompt=prompt,
                model=model,
                session_id=session_id or "",
                session_mode=session_mode,
                parent_task_id=parent_task_id,
                root_task_id=root_task_id,
                steps=steps or [],
                schedule=schedule,
                max_retries=max_retries,
                timeout_seconds=timeout_seconds,
                depends_on=list(depends_on or []),
                metadata=metadata or {},
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )

            if not parent_task_id:
                task.root_task_id = task_id

            self._tasks[task_id] = task
            await self._save(task)
            self._index_deps(task)

            # Auto-create session on creation only for "once" mode
            if task.session_mode == "once" and not task.session_id and self._session_manager:
                try:
                    s = await self._session_manager.create_session()
                    if s:
                        sid = getattr(s, "session_id", None) or getattr(s, "id", None) or str(getattr(s, "id", ""))
                        task.session_id = sid
                        await self._save(task)
                except Exception:
                    pass

            # Link to parent via ids only (no nested snapshot)
            if parent_task_id:
                parent = self._tasks.get(parent_task_id)
                if parent is None:
                    parent = await self._resolve_task(parent_task_id)
                if parent:
                    parent.sub_task_ids.append(task_id)
                    if not any(st.task_id == task_id for st in parent.sub_tasks):
                        parent.sub_tasks.append(task)
                    parent.updated_at = datetime.now()
                    await self._save(parent)
                    await task_events.publish_task_sub_created(
                        parent_task_id, task_id, title, kind.value
                    )

            await task_events.publish_task_created(task)
            await self._sync_taskstate_on_create(task)

            logger.info(
                f"Created task {task_id}: '{title}' ({kind.value}, parent={parent_task_id})"
            )
            return task

    async def get_task(self, task_id: str, include_tree: bool = False) -> Optional[Task]:
        task = self._tasks.get(task_id)
        if task:
            if include_tree:
                await self._ensure_sub_tasks_loaded(task)
            return task
        task = await self._resolve_task(task_id)
        if task:
            self._tasks[task_id] = task
            self._index_deps(task)
            if include_tree:
                await self._ensure_sub_tasks_loaded(task)
        return task

    async def update_task(self, task_id: str, **fields: Any) -> Optional[Task]:
        task = await self.get_task(task_id)
        if not task:
            return None

        old_status = task.status.value if "status" in fields else None

        async with self._lock:
            # None-able fields (allow clearing)
            none_ok = frozenset({
                "schedule", "error", "result", "prompt",
                "script_path", "session_id", "session_mode", "depends_on",
                "started_at", "completed_at",
                "parent_task_id", "root_task_id",
                "retry_count", "progress",
            })
            if "depends_on" in fields:
                self._unindex_deps(task)
            for key, value in fields.items():
                if value is None and key not in none_ok:
                    continue
                if key == "status" and isinstance(value, str):
                    value = TaskStatus(value)
                if key == "kind" and isinstance(value, str):
                    value = TaskKind(value)
                if key == "result":
                    value = TaskResult.from_any(value)
                if hasattr(task, key):
                    setattr(task, key, value)
            if "depends_on" in fields:
                self._index_deps(task)

            task.updated_at = datetime.now()
            if "status" in fields:
                new_status = task.status
                if new_status == TaskStatus.RUNNING:
                    # Always refresh started_at on enter RUNNING
                    task.started_at = datetime.now()
                if new_status.is_terminal:
                    task.completed_at = datetime.now()
            await self._save(task)

        await task_events.publish_task_updated(task)
        if old_status:
            await task_events.publish_task_status_changed(task, old_status)

        if task.status == TaskStatus.COMPLETED:
            await task_events.publish_task_completed(task)
        elif task.status == TaskStatus.FAILED:
            await task_events.publish_task_failed(task)
        elif task.status == TaskStatus.CANCELLED:
            await task_events.publish_task_cancelled(task)

        if task.status.is_terminal and task.session_id:
            await self._sync_taskstate_on_complete(task)

        return task

    async def delete_task(self, task_id: str) -> bool:
        task = await self.get_task(task_id, include_tree=True)
        if not task:
            return False

        # Cancel running handles first (children too)
        for sub in task.sub_tasks:
            await self._cancel_handle(sub.task_id)
        await self._cancel_handle(task_id)

        # Recursively delete children
        for sub in list(task.sub_tasks):
            await self.delete_task(sub.task_id)

        async with self._lock:
            self._tasks.pop(task_id, None)
            self._unindex_deps(task)
            self._execution_locks.pop(task_id, None)
            self._cancel_intent.discard(task_id)
            self._pause_intent.discard(task_id)

        result = await self._storage.delete_task(task_id) if self._storage else True
        if result:
            logger.info(f"Deleted task {task_id}")
        return result

    async def list_tasks(
        self,
        session_id: Optional[str] = None,
        status: Optional[TaskStatus] = None,
        kind: Optional[TaskKind] = None,
        parent_task_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Task]:
        # Merge memory + storage IDs; batch-load the disk-only ones
        mem_ids = set(self._tasks.keys())
        sto_ids = set(await self._storage.list_tasks()) if self._storage else set()
        disk_only = sorted(sto_ids - mem_ids)
        if disk_only and self._storage:
            loaded = await self._storage.load_many(disk_only)
            for t in loaded:
                self._tasks[t.task_id] = t
                self._index_deps(t)
        all_ids = mem_ids | sto_ids

        results = []
        for tid in all_ids:
            t = self._tasks.get(tid) or await self._resolve_task(tid)
            if not t:
                continue
            if session_id and t.session_id != session_id:
                continue
            if status and t.status != status:
                continue
            if kind and t.kind != kind:
                continue
            if parent_task_id is not None:
                if t.parent_task_id != parent_task_id:
                    continue
            else:
                # Default: only root tasks
                if t.parent_task_id is not None:
                    continue
            results.append(t)

        results.sort(key=lambda t: t.created_at, reverse=True)
        return results[offset:offset + limit]

    # --- Sub-task management ---

    async def create_sub_task(
        self,
        parent_task_id: str,
        title: str,
        description: str = "",
        kind: TaskKind = TaskKind.AI,
        script_path: Optional[str] = None,
        script_args: Optional[List[str]] = None,
        prompt: Optional[str] = None,
        steps: Optional[List[TaskStep]] = None,
    ) -> Task:
        parent = await self.get_task(parent_task_id)
        if not parent:
            raise ValueError(f"Parent task not found: {parent_task_id}")

        sub_task = await self.create_task(
            title=title,
            description=description,
            kind=kind,
            script_path=script_path,
            script_args=script_args,
            prompt=prompt,
            steps=steps,
            session_id=parent.session_id,
            parent_task_id=parent_task_id,
        )

        # Auto-pause parent while sub-tasks are pending
        if parent.status.is_active:
            await self.update_task(parent_task_id, status=TaskStatus.PAUSED)

        return sub_task

    async def get_sub_tasks(
        self, parent_task_id: str, recursive: bool = False
    ) -> List[Task]:
        parent = await self.get_task(parent_task_id, include_tree=True)
        if not parent:
            return []
        if not recursive:
            return list(parent.sub_tasks)

        result = []

        async def _collect(t: Task):
            for sub in t.sub_tasks:
                result.append(sub)
                await _collect(sub)

        await _collect(parent)
        return result

    async def get_task_tree(self, task_id: str) -> Optional[Task]:
        return await self.get_task(task_id, include_tree=True)

    # --- Intent helpers ---

    def _record_intent(self, task_id: str, action: str, include_subs: bool, recursive_fn):
        if action == "cancel":
            self._cancel_intent.add(task_id)
        else:
            self._pause_intent.add(task_id)

    def _clear_intent(self, task_id: str):
        self._cancel_intent.discard(task_id)
        self._pause_intent.discard(task_id)

    def _consume_intent(self, task_id: str) -> Optional[str]:
        """Pop intent so handler can act exactly once. Returns 'cancel' or 'pause' or None."""
        if task_id in self._cancel_intent:
            self._cancel_intent.discard(task_id)
            return "cancel"
        if task_id in self._pause_intent:
            self._pause_intent.discard(task_id)
            return "pause"
        return None

    # --- Execution ---

    async def execute_task(self, task_id: str, *, wait: bool = False) -> Task:
        """Execute a task. The per-task lock spans the full run (prevents double-launch)."""
        lock = self._get_exec_lock(task_id)
        if wait:
            async with lock:
                return await self._execute_under_lock(task_id)
        else:
            async def _fire():
                async with lock:
                    await self._execute_under_lock(task_id)

            handle = asyncio.create_task(_fire())
            self._running_handles[task_id] = handle
            handle.add_done_callback(lambda _: self._running_handles.pop(task_id, None))
            t = await self.get_task(task_id)
            return t or Task(task_id="?", title="?", description="", kind=TaskKind.AI)

    async def _execute_under_lock(self, task_id: str) -> Task:
        """Full execution lifecycle; must be called inside the per-task lock."""

        task = await self.get_task(task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")

        # --- Gate: only startable states ---
        if not _can_start(task):
            logger.warning(
                f"Task {task_id} cannot start (status={task.status}, "
                f"retries={task.retry_count}/{task.max_retries}), skip"
            )
            return task

        # Check for cancellation intent before we proceed
        intent = self._consume_intent(task_id)
        if intent == "cancel":
            if not task.status.is_terminal:
                await self.update_task(task_id, status=TaskStatus.CANCELLED)
                await self._record_run(task_id, success=False, error="Cancelled", trigger="cancel")
            self._clear_intent(task_id)
            task = (await self.get_task(task_id)) or task
            return await self._handle_post_execute(task)

        # Dependency check
        if task.depends_on:
            pending = []
            for dep_id in task.depends_on:
                dep = await self.get_task(dep_id)
                if dep and dep.status != TaskStatus.COMPLETED:
                    pending.append(dep_id)
            if pending:
                logger.info(f"Task {task_id} blocked by deps: {pending}")
                return task

        # Root with pending sub-tasks: pause
        if task.is_root() and task.sub_tasks:
            pending_subs = [st for st in task.sub_tasks if not st.status.is_terminal]
            if pending_subs:
                await self.update_task(task_id, status=TaskStatus.PAUSED)
                logger.info(f"Task {task_id} paused: {len(pending_subs)} sub-tasks pending")
                return task

        executor = self._executors.get(task.kind)
        if not executor:
            raise ValueError(f"No executor for task kind: {task.kind}")

        # Mark RUNNING
        await self.update_task(task_id, status=TaskStatus.RUNNING)
        task = (await self.get_task(task_id)) or task

        # Build context
        output_dir = None
        if self._storage:
            try:
                output_dir = str(self._storage._get_output_dir(task_id))
            except Exception:
                pass
        exec_context = ExecContext(
            event_bus=self._event_bus,
            llm_service=self._llm,
            session_manager=self._session_manager,
            run_manager=self._run_manager,
            task_manager=self,
            storage_path=getattr(self._storage, '_storage_path', None),
            output_dir=output_dir,
        )

        # Progress callback — async-friendly emit helper
        async def on_progress(value: float):
            await self.update_task(task_id, progress=min(1.0, max(0.0, value)))

        # --- Validate ---
        validation_error = await executor.validate(task)
        if validation_error:
            await self.update_task(task_id, status=TaskStatus.FAILED, error=validation_error)
            await self._record_run(task_id, success=False, error=validation_error, trigger="manual")
            self._clear_intent(task_id)
            task = (await self.get_task(task_id)) or task
            return await self._handle_post_execute(task)

        # --- Run ---
        try:
            result = await executor.execute(task, exec_context, progress_callback=on_progress)
            task = (await self.get_task(task_id)) or task
        except asyncio.CancelledError:
            intent = self._consume_intent(task_id)
            if intent == "pause":
                await self.update_task(task_id, status=TaskStatus.PAUSED, error="Paused")
                await self._record_run(task_id, success=False, error="Paused", trigger="pause")
            elif intent == "cancel":
                await self.update_task(task_id, status=TaskStatus.CANCELLED)
                await self._record_run(task_id, success=False, error="Cancelled", trigger="cancel")
            else:
                # Cancelled by system / handle cancel without explicit intent
                await self.update_task(task_id, status=TaskStatus.CANCELLED)
                await self._record_run(task_id, success=False, error="Cancelled", trigger="manual")
            task = (await self.get_task(task_id)) or task
            return await self._handle_post_execute(task)
        except Exception as exc:
            logger.error(f"Task {task_id} executor error: {exc}", exc_info=True)
            result = TaskResult(success=False, error=str(exc), exit_code=-1)

        # --- Outcome ---
        if result.success:
            await self.update_task(task_id, status=TaskStatus.COMPLETED, progress=1.0, result=result, error=None)
            await self._record_run(task_id, success=True, output=result.output, trigger="manual")
        else:
            task = (await self.get_task(task_id)) or task
            if task.retry_count < task.max_retries:
                await self.update_task(
                    task_id,
                    status=TaskStatus.QUEUED,
                    retry_count=task.retry_count + 1,
                    error=result.error,
                )
                await self._record_run(task_id, success=False, error=result.error, trigger="retry")
                logger.info(f"Task {task_id} queued for retry {task.retry_count + 1}/{task.max_retries}")
                spawn(self._process_retry_queue(task_id))
            else:
                await self.update_task(task_id, status=TaskStatus.FAILED, result=result, error=result.error)
                await self._record_run(task_id, success=False, error=result.error, trigger="manual")

        self._clear_intent(task_id)
        task = (await self.get_task(task_id)) or task
        return await self._handle_post_execute(task)

    async def _process_retry_queue(self, task_id: str) -> None:
        """Re-execute a queued task after an exponential backoff delay."""
        t = await self.get_task(task_id)
        if not t or t.status != TaskStatus.QUEUED:
            return
        # Exponential backoff: 2**retry_count seconds (aligned with LLM retry policy)
        delay = min(2 ** max(t.retry_count, 0), _MAX_RETRY_BACKOFF)
        await asyncio.sleep(delay)
        t = await self.get_task(task_id)
        if t and t.status == TaskStatus.QUEUED:
            await self.execute_task(task_id)

    # --- Reverse dependency index (III-2) ---

    def _index_dep(self, dep_id: str, waiter_id: str) -> None:
        """Register waiter_id as waiting on dep_id."""
        self._dep_waiters.setdefault(dep_id, set()).add(waiter_id)

    def _index_deps(self, task: Task) -> None:
        """Register a task's depends_on edges into the reverse index."""
        if not task.depends_on:
            return
        for dep_id in task.depends_on:
            self._index_dep(dep_id, task.task_id)

    def _unindex_deps(self, task: Task) -> None:
        """Remove a task's depends_on edges from the reverse index."""
        if not task.depends_on:
            return
        for dep_id in task.depends_on:
            waiters = self._dep_waiters.get(dep_id)
            if waiters:
                waiters.discard(task.task_id)
                if not waiters:
                    self._dep_waiters.pop(dep_id, None)

    def _get_waiters(self, task_id: str) -> List[str]:
        """Return task IDs waiting on the given task (via reverse index)."""
        return sorted(self._dep_waiters.get(task_id, set()))

    async def _handle_post_execute(self, task: Task) -> Task:
        """After task execution: resume parent if all sub-tasks complete; wake dependents."""
        # Parent resume
        if task.parent_task_id:
            parent = await self.get_task(task.parent_task_id, include_tree=True)
            if parent and parent.status == TaskStatus.PAUSED:
                # Re-assess children statuses via fresh cache
                pending = [st for st in parent.sub_tasks if not st.status.is_terminal]
                if not pending:
                    logger.info(f"All sub-tasks complete for {parent.task_id}, resuming parent")
                    await self.update_task(parent.task_id, status=TaskStatus.PENDING)
                    # Execute parent regardless of schedule (auto-continue workflow)
                    spawn(self.execute_task(parent.task_id))

        # Wake dependents via the reverse index (no full-table PENDING scan)
        if task.status == TaskStatus.COMPLETED:
            for waiter_id in self._get_waiters(task.task_id):
                waiter = await self.get_task(waiter_id)
                if not waiter or waiter.status != TaskStatus.PENDING:
                    continue
                if await self._deps_all_completed(waiter):
                    logger.info(f"Waking dependent task {waiter.task_id} after {task.task_id}")
                    spawn(self.execute_task(waiter.task_id))

        return task

    async def _deps_all_completed(self, task: Task) -> bool:
        for dep_id in task.depends_on:
            dep = await self.get_task(dep_id)
            if not dep or dep.status != TaskStatus.COMPLETED:
                return False
        return True

    async def _record_run(
        self,
        task_id: str,
        success: bool,
        error: Optional[str] = None,
        output: Optional[str] = None,
        trigger: str = "manual",
    ) -> None:
        task = await self.get_task(task_id)
        if not task:
            return
        record = TaskRunRecord(
            run_id=str(uuid.uuid4()),
            started_at=task.started_at,
            completed_at=datetime.now(),
            status="completed" if success else ("cancelled" if error and "cancelled" in error.lower() else "failed"),
            success=success,
            error=error,
            output_preview=(output or "")[:500] if output else None,
            exit_code=task.result.exit_code if task.result else None,
            trigger=trigger,
        )
        task.run_history.append(record)
        if len(task.run_history) > MAX_RUN_HISTORY:
            task.run_history = task.run_history[-MAX_RUN_HISTORY:]
        # Increment persistent counters (skip cancel/pause interrupts)
        if trigger not in ("cancel", "pause"):
            if success:
                task.success_count += 1
            else:
                task.failure_count += 1
        await self._save(task)

    # --- Cancel / Pause / Resume (no exec-lock deadlock) ---

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel task: set intent, cancel executor + handle immediately (no exec-lock)."""
        self._record_intent(task_id, "cancel", True, self._cancel_sub_intent)

        task = await self.get_task(task_id, include_tree=True)
        if task:
            # Recurse sub-tasks
            for sub in task.sub_tasks:
                await self.cancel_task(sub.task_id)
            # Cancel executor
            if task.kind in self._executors:
                try:
                    await self._executors[task.kind].cancel(task_id)
                except Exception:
                    pass
        # Cancel running handle
        await self._cancel_handle(task_id)

        # If task is already terminal, do NOT overwrite state
        if task and not task.status.is_terminal:
            await self.update_task(task_id, status=TaskStatus.CANCELLED)
            await self._record_run(task_id, success=False, error="Cancelled", trigger="cancel")
        logger.info(f"Cancelled task {task_id}")
        return True

    def _cancel_sub_intent(self, task_id: str):
        self._cancel_intent.add(task_id)

    async def _cancel_task_locked(self, task_id: str) -> bool:
        """Legacy locked cancel — retains exec lock for callers that still hold it."""
        return await self.cancel_task(task_id)

    async def _cancel_handle(self, task_id: str) -> None:
        handle = self._running_handles.pop(task_id, None)
        if handle and not handle.done():
            handle.cancel()
            try:
                await handle
            except asyncio.CancelledError:
                pass

    async def pause_task(self, task_id: str) -> bool:
        """Pause: set intent, cancel handle + executor (no exec-lock)."""
        task = await self.get_task(task_id)
        if not task or task.status not in (TaskStatus.RUNNING, TaskStatus.QUEUED):
            return False

        self._record_intent(task_id, "pause", False, lambda tid: None)

        # Cancel executor
        if task.kind in self._executors:
            try:
                await self._executors[task.kind].cancel(task_id)
            except Exception:
                pass
        await self._cancel_handle(task_id)
        return await self.update_task(task_id, status=TaskStatus.PAUSED) is not None

    async def resume_task(self, task_id: str) -> bool:
        """Resume: set back to PENDING and re-queue for execution."""
        task = await self.get_task(task_id)
        if not task or task.status != TaskStatus.PAUSED:
            return False
        await self.update_task(task_id, status=TaskStatus.PENDING)
        # Launch execution
        spawn(self._resume_after_lock(task_id))
        return True

    async def _resume_after_lock(self, task_id: str):
        """Execute resumed task under its lock."""
        lock = self._get_exec_lock(task_id)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=1.0)
        except asyncio.TimeoutError:
            return
        try:
            task = await self.get_task(task_id)
            if task and task.status == TaskStatus.PENDING:
                await self._execute_under_lock(task_id)
        finally:
            lock.release()

    # --- Step management ---

    async def update_progress(self, task_id: str, progress: float) -> bool:
        task = await self.update_task(task_id, progress=min(max(progress, 0.0), 1.0))
        if task:
            await task_events.publish_task_progress(task)
            return True
        return False

    async def complete_step(
        self, task_id: str, step_id: int, result: Optional[str] = None
    ) -> bool:
        task = await self.get_task(task_id)
        if not task:
            return False
        for step in task.steps:
            if step.step_id == step_id:
                step.status = TaskStatus.COMPLETED
                step.result = result
                step.completed_at = datetime.now()
                task.updated_at = datetime.now()
                await self._save(task)
                if task.steps:
                    completed = sum(1 for s in task.steps if s.status == TaskStatus.COMPLETED)
                    await self.update_progress(task_id, completed / len(task.steps))
                step_title = step.title or f"Step {step_id}"
                await task_events.publish_task_step_completed(task_id, step_id, step_title)
                return True
        logger.warning(f"Step {step_id} not found in task {task_id}")
        return False

    # --- Lifecycle ---

    async def start(self) -> None:
        """Start manager: load existing tasks and recover orphaned states."""
        logger.info("Task manager starting...")
        if self._storage:
            for tid in await self._storage.list_tasks():
                try:
                    t = await self._storage.load_task(tid)
                    if t:
                        self._tasks[tid] = t
                except Exception:
                    pass
            logger.info(f"Loaded {len(self._tasks)} tasks from storage")

        # Recover tasks stuck in RUNNING/QUEUED/PAUSED
        await self._recover_orphaned_tasks()

        logger.info("Task manager started")

    @staticmethod
    def _task_output_text(task: Any) -> str:
        """The answer text a completion mark was written for."""
        result = getattr(task, "result", None)
        if result is None:
            return ""
        if isinstance(result, str):
            return result
        output = getattr(result, "output", None)
        if isinstance(output, str):
            return output
        if isinstance(result, dict):
            for key in ("output", "result", "text"):
                value = result.get(key)
                if isinstance(value, str) and value:
                    return value
        return str(result)

    @classmethod
    def _completion_state(cls, task: Any) -> str:
        """How trustworthy this task's recorded result is after a restart."""
        try:
            from boarai.core.runtime.artifact_mark import classify, fingerprint_text

            return classify(
                "task",
                task.task_id,
                output_fp=fingerprint_text(cls._task_output_text(task)),
            )
        except Exception:  # noqa: BLE001 — recovery must never crash startup
            return "missing"

    async def _recover_orphaned_tasks(self) -> None:
        """Recover running/queued tasks: complete if proven, else paused."""
        orphan_count = 0
        for tid, task in list(self._tasks.items()):
            if task.status not in (TaskStatus.RUNNING, TaskStatus.QUEUED):
                continue
            state = self._completion_state(task)
            if state == "complete" and getattr(task, "result", None):
                task.status = TaskStatus.COMPLETED
                task.updated_at = datetime.now()
                task.error = None
                await self._save(task)
                logger.info(
                    f"Recovered task {tid}: run had completed (mark matched) -> COMPLETED"
                )
                continue
            task.status = TaskStatus.PAUSED
            task.updated_at = datetime.now()
            task.error = f"System restart: task was interrupted ({state})"
            await self._save(task)
            orphan_count += 1
            logger.warning(
                f"Recovered orphaned task {tid}: {task.title} -> PAUSED ({state})"
            )
        if orphan_count:
            logger.info(f"Recovered {orphan_count} orphaned tasks")

    async def stop(self) -> None:
        """Stop manager: cancel all running tasks and save state."""
        logger.info("Stopping task manager...")
        for task_id in list(self._running_handles.keys()):
            await self._cancel_handle(task_id)
        async with self._lock:
            for task_id, task in list(self._tasks.items()):
                if self._storage:
                    await self._storage.save_task(task)
        logger.info("Task manager stopped")

    # --- Internal helpers ---

    async def _resolve_task(self, task_id: str) -> Optional[Task]:
        """Load from storage if not in memory."""
        if self._storage:
            return await self._storage.load_task(task_id)
        return None

    async def _save(self, task: Task) -> None:
        if self._storage:
            await self._storage.save_task(task)

    async def _ensure_sub_tasks_loaded(self, task: Task) -> None:
        """Load children by authoritative sub_task_ids; bypass stale nested snapshots."""
        ids = list(task.sub_task_ids) if task.sub_task_ids else [
            st.task_id for st in task.sub_tasks
        ]
        task.sub_tasks = []
        for tid in ids:
            child = await self.get_task(tid, include_tree=True)
            if child:
                task.sub_tasks.append(child)

    async def _sync_taskstate_on_create(self, task: Task) -> None:
        if not task.session_id:
            return
        try:
            from boarai.core.prompt.task_state import get_task_state_manager
            tsm = get_task_state_manager()
            ts = await tsm.load(task.session_id)
            if task.title and task.title not in ts.working_files:
                ts.working_files.append(f"[Task] {task.title}")
            if not ts.goal:
                ts.goal = task.title
            if not ts.current_step:
                ts.current_step = f"Task created: {task.title}"
            await tsm.save(ts)
        except Exception:
            pass

    async def _sync_taskstate_on_complete(self, task: Task) -> None:
        if not task.session_id:
            return
        try:
            from boarai.core.prompt.task_state import get_task_state_manager
            tsm = get_task_state_manager()
            ts = await tsm.load(task.session_id)
            entry = f"{task.title}: {task.status.value}"
            if entry not in ts.completed:
                ts.completed.append(entry)
            if task.status == TaskStatus.FAILED and task.error:
                ts.open_questions.append(f"Task failed: {task.error}")
            await tsm.save(ts)
        except Exception:
            pass

    async def cleanup_old_tasks(self, retention_days: int = 7) -> int:
        cutoff = datetime.now() - timedelta(days=retention_days)
        deleted = 0
        ids = set(self._tasks.keys())
        if self._storage:
            ids |= set(await self._storage.list_tasks())
        for tid in list(ids):
            task = await self.get_task(tid)
            if not task or not task.status.is_terminal:
                continue
            check_time = task.completed_at or task.updated_at
            if check_time and check_time > cutoff:
                continue
            await self.delete_task(tid)
            deleted += 1
        if deleted:
            logger.info(f"Cleaned up {deleted} old tasks (>{retention_days}d)")
        return deleted

    def get_stats(self) -> Dict[str, Any]:
        sc: Dict[str, int] = {}
        for s in TaskStatus:
            n = sum(1 for t in self._tasks.values() if t.status == s)
            if n:
                sc[s.value] = n
        kc: Dict[str, int] = {}
        for k in TaskKind:
            n = sum(1 for t in self._tasks.values() if t.kind == k)
            if n:
                kc[k.value] = n
        return {
            "total_tasks": len(self._tasks),
            "running_tasks": len(self._running_handles),
            "by_status": sc,
            "by_kind": kc,
            "executor_kinds": self.list_executor_kinds(),
        }


# --- Global singleton ---

_task_manager: Optional[TaskManager] = None


def get_task_manager(
    project_root: Optional[Path] = None,
    llm_service: Any = None,
    session_manager: Any = None,
    run_manager: Any = None,
) -> TaskManager:
    global _task_manager
    from boarai.core.runtime import resolve_data_dir
    data_dir = resolve_data_dir(project_root)
    if _task_manager is None:
        storage_path = data_dir / "tasks"
        _task_manager = TaskManager(
            storage_path=storage_path,
            llm_service=llm_service,
            session_manager=session_manager,
            run_manager=run_manager,
        )
    else:
        # Late-binding for deps set after initial construction
        _task_manager.bind_if_missing(
            storage_path=data_dir / "tasks",
            session_manager=session_manager,
            run_manager=run_manager,
            llm_service=llm_service,
        )
    return _task_manager