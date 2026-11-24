"""
Task kind definitions for BoarAi task system.

TaskKind is the primary extension point for the task system.
Extensions can register new kinds and their corresponding executors
via TaskManager.register_executor().
"""

from enum import Enum

class TaskKind(str, Enum):
    """Task execution kind."""

    SCRIPT = "script"
    AI = "ai"

    def __str__(self) -> str:
        return self.value
