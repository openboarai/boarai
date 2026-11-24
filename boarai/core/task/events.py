"""
Task event helpers for BoarAi task system.

Provides helper functions to publish task-related events
with consistent payload format.

Usage:
    from boarai.core.task.events import publish_task_created
    await publish_task_created(task)
"""

from typing import Any, Dict, Optional

from boarai.core.event import EventType, get_event_bus, publish_event

# Task event type strings (used by external subscribers like WebUI)
TASK_CREATED = "task.created"
TASK_UPDATED = "task.updated"
TASK_STATUS_CHANGED = "task.status_changed"
TASK_PROGRESS = "task.progress"
TASK_STEP_COMPLETED = "task.step_completed"
TASK_COMPLETED = "task.completed"
TASK_FAILED = "task.failed"
TASK_CANCELLED = "task.cancelled"
TASK_SUB_CREATED = "task.sub_created"

# Future multi-agent events
TASK_ASSIGNED = "task.assigned"
TASK_CLAIMED = "task.claimed"
TASK_HEARTBEAT = "task.heartbeat"


async def _publish_task_event(
    event_type: str,
    task_id: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Publish a task event with standardized payload."""
    payload: Dict[str, Any] = {"task_id": task_id}
    if extra:
        payload.update(extra)
    await publish_event(event_type, payload, "task_manager")


async def _kind_str(task) -> str:
    """Get task kind as string (handles both enum and string types)."""
    k = task.kind
    return k.value if hasattr(k, 'value') else str(k)


async def publish_task_created(task) -> None:
    """Publish task.created event."""
    await _publish_task_event(TASK_CREATED, task.task_id, {
        "title": task.title,
        "kind": await _kind_str(task),
        "session_id": task.session_id,
        "parent_task_id": task.parent_task_id,
    })


async def publish_task_updated(task) -> None:
    """Publish task.updated event."""
    await _publish_task_event(TASK_UPDATED, task.task_id, {
        "status": task.status.value,
        "progress": task.progress,
    })


async def publish_task_status_changed(task, old_status: str) -> None:
    """Publish task.status_changed event."""
    await _publish_task_event(TASK_STATUS_CHANGED, task.task_id, {
        "old_status": old_status,
        "new_status": task.status.value,
    })


async def publish_task_progress(task) -> None:
    """Publish task.progress event."""
    await _publish_task_event(TASK_PROGRESS, task.task_id, {
        "progress": task.progress,
    })


async def publish_task_step_completed(
    task_id: str,
    step_id: int,
    step_title: str,
) -> None:
    """Publish task.step_completed event."""
    await _publish_task_event(TASK_STEP_COMPLETED, task_id, {
        "step_id": step_id,
        "step_title": step_title,
    })


async def publish_task_completed(task) -> None:
    """Publish task.completed event."""
    await _publish_task_event(TASK_COMPLETED, task.task_id, {
        "result": task.result.to_dict() if task.result else None,
    })


async def publish_task_failed(task) -> None:
    """Publish task.failed event."""
    await _publish_task_event(TASK_FAILED, task.task_id, {
        "error": task.error,
        "retry_count": task.retry_count,
        "max_retries": task.max_retries,
    })


async def publish_task_cancelled(task) -> None:
    """Publish task.cancelled event."""
    await _publish_task_event(TASK_CANCELLED, task.task_id, {})


async def publish_task_sub_created(
    parent_task_id: str,
    sub_task_id: str,
    sub_title: str,
    sub_kind: str,
) -> None:
    """Publish task.sub_created event."""
    await _publish_task_event(TASK_SUB_CREATED, parent_task_id, {
        "sub_task_id": sub_task_id,
        "sub_title": sub_title,
        "sub_kind": sub_kind,
    })
