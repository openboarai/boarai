"""
BoarAi Task System.

Provides task management with two built-in task types:
- script: Execute Python scripts in subprocess
- ai: Send prompts to AI/Agent for execution
"""

from .kind import TaskKind
from .status import TaskStatus
from .models import Task, TaskStep, TaskResult, TaskSchedule, TaskRunRecord
from .manager import TaskManager, get_task_manager
from .storage import TaskStorage
from .executor import BaseTaskExecutor, ScriptExecutor, AIExecutor
from .scheduler import TaskScheduler, get_task_scheduler
from . import timeutil

__all__ = [
    "TaskKind",
    "TaskStatus",
    "Task",
    "TaskStep",
    "TaskResult",
    "TaskSchedule",
    "TaskRunRecord",
    "TaskManager",
    "get_task_manager",
    "TaskStorage",
    "BaseTaskExecutor",
    "ScriptExecutor",
    "AIExecutor",
    "TaskScheduler",
    "get_task_scheduler",
    "timeutil",
]