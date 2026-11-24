"""
Abstract base executor for BoarAi task system.

Defines the interface that all task executors must implement.
Extensions implement BaseTaskExecutor and register via TaskManager.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
import asyncio

from ..kind import TaskKind
from ..models import Task, TaskResult

@dataclass
class ExecContext:
    """Execution context passed to every executor."""

    event_bus: Any = None          # EventBus instance
    llm_service: Any = None        # LLM service
    session_manager: Any = None    # Session manager
    run_manager: Any = None        # Run manager
    task_manager: Any = None       # Task manager (for creating sub-tasks)
    storage_path: Any = None       # Task storage path
    output_dir: Optional[str] = None  # Per-task output directory

class BaseTaskExecutor(ABC):
    """Abstract base class for all task executors."""

    kind: TaskKind  # Subclasses must declare their kind

    @abstractmethod
    async def validate(self, task: Task) -> Optional[str]:
        """Validate task before execution."""
        ...

    @abstractmethod
    async def execute(
        self,
        task: Task,
        context: ExecContext,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> TaskResult:
        """Execute the task."""
        ...

    @staticmethod
    async def emit_progress(
        callback: Optional[Callable[[float], None]], value: float
    ) -> None:
        """Safely emit progress value to callback (sync or async)."""
        if callback is None:
            return
        result = callback(value)
        if asyncio.iscoroutine(result):
            await result

    @abstractmethod
    async def cancel(self, task_id: str) -> bool:
        """Cancel a running task."""
        ...
