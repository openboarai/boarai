"""
Task data models for BoarAi task system.

Defines Task, TaskStep, TaskResult, TaskSchedule, TaskRunRecord.
All models support to_dict()/from_dict() for JSON serialization.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .kind import TaskKind
from .status import TaskStatus

@dataclass
class TaskStep:
    """A single step within a task."""

    step_id: int
    title: str
    description: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskStep":
        return cls(
            step_id=int(data["step_id"]),
            title=data["title"],
            description=data.get("description"),
            status=TaskStatus(data.get("status", TaskStatus.PENDING.value)),
            result=data.get("result"),
            error=data.get("error"),
            started_at=(
                datetime.fromisoformat(data["started_at"])
                if data.get("started_at") else None
            ),
            completed_at=(
                datetime.fromisoformat(data["completed_at"])
                if data.get("completed_at") else None
            ),
        )

@dataclass
class TaskSchedule:
    """Schedule configuration for once / cron / interval tasks."""

    type: str  # "once", "cron", "interval"
    expression: Optional[str] = None  # cron expr, interval like "30m", or once ISO
    timezone: Optional[str] = None  # IANA tz, default local/UTC handling in scheduler
    next_run_at: Optional[datetime] = None
    # When True (default for interval), first fire as soon as due (next_run_at <= now)
    run_immediately: bool = False
    # After failure: still advance next_run_at for recurring tasks
    reschedule_on_failure: bool = True
    # Stop recurring after N consecutive failures (0 = never stop)
    max_consecutive_failures: int = 0
    consecutive_failures: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "expression": self.expression,
            "timezone": self.timezone,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "run_immediately": self.run_immediately,
            "reschedule_on_failure": self.reschedule_on_failure,
            "max_consecutive_failures": self.max_consecutive_failures,
            "consecutive_failures": self.consecutive_failures,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskSchedule":
        return cls(
            type=data["type"],
            expression=data.get("expression"),
            timezone=data.get("timezone"),
            next_run_at=(
                datetime.fromisoformat(data["next_run_at"])
                if data.get("next_run_at") else None
            ),
            run_immediately=bool(data.get("run_immediately", False)),
            reschedule_on_failure=bool(data.get("reschedule_on_failure", True)),
            max_consecutive_failures=int(data.get("max_consecutive_failures", 0) or 0),
            consecutive_failures=int(data.get("consecutive_failures", 0) or 0),
        )

@dataclass
class TaskResult:
    """Execution result of a task."""

    success: bool
    output: Optional[str] = None
    exit_code: Optional[int] = None
    output_files: Optional[List[str]] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "exit_code": self.exit_code,
            "output_files": self.output_files,
            "error": self.error,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskResult":
        if not isinstance(data, dict):
            return cls(success=False, error=str(data), output=str(data))
        return cls(
            success=bool(data.get("success", False)),
            output=data.get("output"),
            exit_code=data.get("exit_code"),
            output_files=data.get("output_files"),
            error=data.get("error"),
            metadata=data.get("metadata") or {},
        )

    @classmethod
    def from_any(cls, value: Any) -> "TaskResult":
        """Normalize string / dict / TaskResult into TaskResult."""
        if value is None:
            return cls(success=True)
        if isinstance(value, TaskResult):
            return value
        if isinstance(value, dict):
            return cls.from_dict(value)
        text = str(value)
        return cls(success=True, output=text)

@dataclass
class TaskRunRecord:
    """One historical execution of a (possibly recurring) task."""

    run_id: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    status: str = "completed"  # completed | failed | cancelled | skipped
    success: bool = False
    error: Optional[str] = None
    output_preview: Optional[str] = None
    exit_code: Optional[int] = None
    trigger: str = "manual"  # manual | schedule | retry | resume | subtask

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "status": self.status,
            "success": self.success,
            "error": self.error,
            "output_preview": self.output_preview,
            "exit_code": self.exit_code,
            "trigger": self.trigger,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskRunRecord":
        return cls(
            run_id=data.get("run_id") or str(uuid.uuid4()),
            started_at=(
                datetime.fromisoformat(data["started_at"])
                if data.get("started_at") else None
            ),
            completed_at=(
                datetime.fromisoformat(data["completed_at"])
                if data.get("completed_at") else None
            ),
            status=data.get("status", "completed"),
            success=bool(data.get("success", False)),
            error=data.get("error"),
            output_preview=data.get("output_preview"),
            exit_code=data.get("exit_code"),
            trigger=data.get("trigger", "manual"),
        )

@dataclass
class Task:
    """Core task data model."""

    # --- Core identity ---
    task_id: str
    title: str
    description: str
    kind: TaskKind

    # --- Script task parameters ---
    script_path: Optional[str] = None
    script_args: Optional[List[str]] = None
    script_env: Optional[Dict[str, str]] = None

    # --- AI task parameters ---
    prompt: Optional[str] = None
    model: Optional[str] = None

    # --- Lifecycle ---
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0
    max_retries: int = 0
    retry_count: int = 0
    # Per-run timeout seconds (script); 0 = executor default
    timeout_seconds: int = 0

    # --- Result ---
    result: Optional[TaskResult] = None
    error: Optional[str] = None

    # --- Relationship ---
    session_id: str = ""
    session_mode: str = "once"          # "once" | "always" | "manual"
    parent_task_id: Optional[str] = None
    root_task_id: Optional[str] = None
    agent_id: Optional[str] = None
    # Dependency task IDs that must be COMPLETED before this can run
    depends_on: List[str] = field(default_factory=list)

    # --- Sub-tasks ---
    # sub_task_ids is the authoritative child reference for persistence.
    # sub_tasks is a runtime-only tree cache (not nested into JSON on save).
    sub_task_ids: List[str] = field(default_factory=list)
    sub_tasks: List["Task"] = field(default_factory=list)

    # --- Steps ---
    steps: List[TaskStep] = field(default_factory=list)

    # --- Schedule ---
    schedule: Optional[TaskSchedule] = None

    # --- Run history (newest last; capped by manager) ---
    run_history: List[TaskRunRecord] = field(default_factory=list)

    # --- Persistent counters ---
    success_count: int = 0
    failure_count: int = 0

    # --- Timestamps ---
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # --- Extensible ---
    metadata: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def generate_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def compute_root_id(
        parent_task_id: Optional[str],
        grandparent_root_id: Optional[str],
    ) -> str:
        if grandparent_root_id:
            return grandparent_root_id
        if parent_task_id:
            return parent_task_id
        return ""

    def is_root(self) -> bool:
        return self.parent_task_id is None

    def is_leaf(self) -> bool:
        if self.sub_task_ids:
            return len(self.sub_task_ids) == 0
        return len(self.sub_tasks) == 0

    def child_ids(self) -> List[str]:
        """Authoritative child task ids (prefer ids over nested cache)."""
        if self.sub_task_ids:
            return list(self.sub_task_ids)
        return [st.task_id for st in self.sub_tasks]

    def to_dict(self) -> Dict[str, Any]:
        # Persist ids only — never nest full child Task trees (stale snapshot risk).
        ids = self.child_ids()
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "kind": self.kind.value,
            "script_path": self.script_path,
            "script_args": self.script_args,
            "script_env": self.script_env,
            "prompt": self.prompt,
            "model": self.model,
            "status": self.status.value,
            "progress": self.progress,
            "max_retries": self.max_retries,
            "retry_count": self.retry_count,
            "timeout_seconds": self.timeout_seconds,
            "result": self.result.to_dict() if self.result else None,
            "error": self.error,
            "session_id": self.session_id,
            "session_mode": self.session_mode,
            "parent_task_id": self.parent_task_id,
            "root_task_id": self.root_task_id,
            "agent_id": self.agent_id,
            "depends_on": list(self.depends_on or []),
            "sub_task_ids": ids,
            # Empty list for API backward-compat shape; tree loaded via include_tree
            "sub_tasks": [],
            "steps": [s.to_dict() for s in self.steps],
            "schedule": self.schedule.to_dict() if self.schedule else None,
            "run_history": [r.to_dict() for r in self.run_history],
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "metadata": self.metadata,
        }

    def to_summary_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "kind": self.kind.value,
            "status": self.status.value,
            "progress": self.progress,
            "session_id": self.session_id,
            "session_mode": self.session_mode,
            "parent_task_id": self.parent_task_id,
            "root_task_id": self.root_task_id,
            "agent_id": self.agent_id,
            "sub_task_count": len(self.child_ids()),
            "step_count": len(self.steps),
            "run_count": len(self.run_history),
            "error": self.error,
            "max_retries": self.max_retries,
            "retry_count": self.retry_count,
            "timeout_seconds": self.timeout_seconds,
            "script_path": self.script_path,
            "model": self.model,
            "schedule": self.schedule.to_dict() if self.schedule else None,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        steps_raw = data.get("steps") or []
        history_raw = data.get("run_history") or []
        depends = data.get("depends_on") or []
        if isinstance(depends, str):
            depends = [depends]

        # Migrate legacy nested sub_tasks → sub_task_ids
        nested = data.get("sub_tasks") or []
        ids = list(data.get("sub_task_ids") or [])
        if not ids and nested:
            for st in nested:
                if isinstance(st, dict) and st.get("task_id"):
                    ids.append(st["task_id"])

        return cls(
            task_id=data["task_id"],
            title=data["title"],
            description=data.get("description", ""),
            kind=TaskKind(data.get("kind", TaskKind.AI.value)),
            script_path=data.get("script_path"),
            script_args=data.get("script_args"),
            script_env=data.get("script_env"),
            prompt=data.get("prompt"),
            model=data.get("model"),
            status=TaskStatus(data.get("status", TaskStatus.PENDING.value)),
            progress=float(data.get("progress", 0.0) or 0.0),
            max_retries=int(data.get("max_retries", 0) or 0),
            retry_count=int(data.get("retry_count", 0) or 0),
            timeout_seconds=int(data.get("timeout_seconds", 0) or 0),
            result=(
                TaskResult.from_dict(data["result"])
                if data.get("result") else None
            ),
            error=data.get("error"),
            session_id=data.get("session_id", "") or "",
            session_mode=data.get("session_mode", "once") or "once",
            parent_task_id=data.get("parent_task_id"),
            root_task_id=data.get("root_task_id"),
            agent_id=data.get("agent_id"),
            depends_on=list(depends),
            sub_task_ids=ids,
            # Do not hydrate nested snapshots as live objects (stale risk)
            sub_tasks=[],
            steps=[TaskStep.from_dict(s) for s in steps_raw],
            schedule=(
                TaskSchedule.from_dict(data["schedule"])
                if data.get("schedule") else None
            ),
            run_history=[TaskRunRecord.from_dict(r) for r in history_raw],
            success_count=int(data.get("success_count", 0) or 0),
            failure_count=int(data.get("failure_count", 0) or 0),
            created_at=datetime.fromisoformat(data["created_at"])
            if data.get("created_at") else datetime.now(),
            updated_at=datetime.fromisoformat(data["updated_at"])
            if data.get("updated_at") else datetime.now(),
            started_at=(
                datetime.fromisoformat(data["started_at"])
                if data.get("started_at") else None
            ),
            completed_at=(
                datetime.fromisoformat(data["completed_at"])
                if data.get("completed_at") else None
            ),
            metadata=data.get("metadata") or {},
        )
