"""Task status definitions for BoarAi task system.

The task status machine:
    PENDING → QUEUED → RUNNING ⇄ PAUSED → COMPLETED
                        │                  │
                        ├→ FAILED → (retry → QUEUED)
                        └→ CANCELLED
                        (via dependency failure) → SKIPPED
"""

from enum import Enum

class TaskStatus(str, Enum):
    """Task lifecycle status."""

    PENDING = "pending"       # Created, not yet queued
    QUEUED = "queued"         # In queue, waiting for execution
    RUNNING = "running"       # Currently executing
    PAUSED = "paused"        # Paused (e.g., waiting for sub-tasks)
    COMPLETED = "completed"   # Successfully finished
    FAILED = "failed"        # Execution failed (may retry)
    CANCELLED = "cancelled"   # Cancelled by user or system
    SKIPPED = "skipped"      # Skipped (parent failed, dependency not met)

    @property
    def is_terminal(self) -> bool:
        """Check if this is a terminal (final) status."""
        return self in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.SKIPPED,
        )

    @property
    def is_active(self) -> bool:
        """Check if this status represents active execution."""
        return self in (TaskStatus.RUNNING, TaskStatus.QUEUED, TaskStatus.PAUSED)
