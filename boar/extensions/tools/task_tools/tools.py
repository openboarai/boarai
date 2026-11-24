"""
Task management tools — individual tools for each operation.

LLM-facing surface is the single `task_manager` aggregate (action enum);
the per-op tools below stay registered for API/WebUI/internal callers
(llm_visible=False → zero prompt cost).
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict

from boarai.core.extension.tool import (
    CompressStrategy,
    SecurityProfile,
    SuggestedCategory,
    ToolParameter,
    tool,
)

from boarai.core.task.timeutil import parse_iso_datetime

logger = logging.getLogger(__name__)


def _get_tm(ctx):
    try:
        return ctx.task_manager
    except Exception:
        return None


# --- Create ---

@tool(name="task_create",
      description="Create a new task. Returns the task ID and status.",
      llm_visible=False,
      parameters=[
          ToolParameter(name="title", type="string", required=True, description="Task title"),
          ToolParameter(name="description", type="string", required=False, description="Task description"),
          ToolParameter(name="kind", type="string", required=False, default="ai",
                        description="Task type: ai or script", enum=["ai", "script"]),
          ToolParameter(name="steps", type="array", required=False, description="List of step objects [{title, description}]"),
          ToolParameter(name="script_path", type="string", required=False, description="Script path (required for script tasks)"),
          ToolParameter(name="prompt", type="string", required=False, description="AI prompt (for AI tasks)"),
          ToolParameter(name="session_id", type="string", required=False, description="Associated session ID"),
          ToolParameter(name="schedule", type="object", required=False,
                        description="Schedule: {type: once/interval/cron, expression/next_run_at: ..., timezone: ...}"),
          ToolParameter(name="max_retries", type="integer", required=False, default=0, description="Max retry count on failure"),
          ToolParameter(name="timeout_seconds", type="integer", required=False, default=0, description="Timeout in seconds (0 = default)"),
          ToolParameter(name="depends_on", type="array", required=False, description="List of task IDs this depends on"),
          ToolParameter(name="execute_now", type="boolean", required=False, default=False, description="Execute immediately after creation"),
      ])
async def task_create(ctx, **kwargs) -> Dict:
    tm = _get_tm(ctx)
    if not tm:
        return {"error": "Task system is not available in this environment."}

    schedule = None
    raw_schedule = kwargs.get("schedule")
    if raw_schedule and isinstance(raw_schedule, dict):
        from boarai.core.task.models import TaskSchedule
        s = raw_schedule
        # Accept both {type, expression} and {type, value}
        expression = s.get("expression") or s.get("value")
        next_run = parse_iso_datetime(s.get("next_run_at")) if not expression else None
        # For "once" type, auto-set next_run_at from the expression (ISO string)
        if s.get("type") == "once" and expression and not next_run:
            next_run = parse_iso_datetime(expression)
            expression = None
        schedule = TaskSchedule(
            type=s.get("type", "once"),
            expression=expression,
            timezone=s.get("timezone"),
            next_run_at=next_run,
        )

    t = await tm.create_task(
        title=kwargs.get("title", ""),
        description=kwargs.get("description", ""),
        kind=kwargs.get("kind", "ai"),
        steps=kwargs.get("steps"),
        script_path=kwargs.get("script_path"),
        prompt=kwargs.get("prompt"),
        session_id=kwargs.get("session_id", ""),
        schedule=schedule,
        max_retries=int(kwargs.get("max_retries", 0) or 0),
        timeout_seconds=int(kwargs.get("timeout_seconds", 0) or 0),
        depends_on=kwargs.get("depends_on"),
    )

    if kwargs.get("execute_now"):
        import asyncio
        asyncio.create_task(tm.execute_task(t.task_id))

    return {
        "task_id": t.task_id,
        "title": t.title,
        "status": t.status.value,
        "schedule": t.schedule.to_dict() if t.schedule else None,
    }


# --- Update ---

@tool(name="task_update",
      description="Update a task's status, progress, or result.",
      llm_visible=False,
      parameters=[
          ToolParameter(name="task_id", type="string", required=True, description="Task ID"),
          ToolParameter(name="status", type="string", required=False, description="New status",
                        enum=["pending", "running", "completed", "failed", "cancelled", "paused"]),
          ToolParameter(name="progress", type="number", required=False, description="Progress 0.0-1.0 (or 0-100 integer)"),
          ToolParameter(name="result", type="string", required=False, description="Result text"),
      ])
async def task_update(ctx, **kwargs) -> Dict:
    tm = _get_tm(ctx)
    if not tm:
        return {"error": "Task system is not available in this environment."}

    progress = kwargs.get("progress")
    if progress is not None:
        if isinstance(progress, int) and progress > 1:
            progress = progress / 100.0
        progress = max(0.0, min(1.0, float(progress)))
        kwargs["progress"] = progress

    t = await tm.update_task(
        task_id=kwargs.get("task_id", ""),
        status=kwargs.get("status"),
        progress=kwargs.get("progress"),
        result=kwargs.get("result"),
    )
    return {"task_id": t.task_id, "status": t.status.value, "progress": t.progress}


# --- List ---

@tool(name="task_list",
      description="List tasks with optional filters.",
      llm_visible=False,
      parameters=[
          ToolParameter(name="session_id", type="string", required=False, description="Filter by session ID"),
          ToolParameter(name="status", type="string", required=False,
                        enum=["pending", "queued", "running", "completed", "failed", "cancelled", "paused"],
                        description="Filter by status"),
          ToolParameter(name="kind", type="string", required=False, description="ai or script", enum=["ai", "script"]),
          ToolParameter(name="limit", type="integer", required=False, default=50, description="Max results"),
      ])
async def task_list(ctx, **kwargs) -> Dict:
    tm = _get_tm(ctx)
    if not tm:
        return {"error": "Task system is not available in this environment."}
    tasks = await tm.list_tasks(
        session_id=kwargs.get("session_id"), status=kwargs.get("status"),
        kind=kwargs.get("kind"), limit=kwargs.get("limit", 50),
    )
    return {
        "tasks": [
            {"task_id": t.task_id, "title": t.title, "status": t.status.value,
             "kind": t.kind.value, "progress": t.progress}
            for t in tasks
        ],
        "total": len(tasks),
    }


# --- Get ---

@tool(name="task_get",
      description="Get detailed information about a specific task.",
      llm_visible=False,
      parameters=[ToolParameter(name="task_id", type="string", required=True, description="Task ID")])
async def task_get(ctx, **kwargs) -> Dict:
    tm = _get_tm(ctx)
    if not tm:
        return {"error": "Task system is not available in this environment."}
    t = await tm.get_task(kwargs.get("task_id", ""))
    if not t:
        return {"error": f"Task {kwargs.get('task_id')} not found"}
    return t.to_dict()


# --- Delete ---

@tool(name="task_delete",
      description="Delete a task and its sub-tasks recursively.",
      llm_visible=False,
      parameters=[ToolParameter(name="task_id", type="string", required=True, description="Task ID")])
async def task_delete(ctx, **kwargs) -> Dict:
    tm = _get_tm(ctx)
    if not tm:
        return {"error": "Task system is not available in this environment."}
    tid = kwargs.get("task_id", "")
    ok = await tm.delete_task(tid)
    return {"task_id": tid, "deleted": ok}


# --- Execute ---

@tool(name="task_execute",
      description="Start executing a pending task immediately.",
      llm_visible=False,
      parameters=[ToolParameter(name="task_id", type="string", required=True, description="Task ID")])
async def task_execute(ctx, **kwargs) -> Dict:
    tm = _get_tm(ctx)
    if not tm:
        return {"error": "Task system is not available in this environment."}
    t = await tm.execute_task(kwargs.get("task_id", ""))
    return {"task_id": t.task_id, "status": t.status.value}


# --- Cancel ---

@tool(name="task_cancel",
      description="Cancel a running task.",
      llm_visible=False,
      parameters=[ToolParameter(name="task_id", type="string", required=True, description="Task ID")])
async def task_cancel(ctx, **kwargs) -> Dict:
    tm = _get_tm(ctx)
    if not tm:
        return {"error": "Task system is not available in this environment."}
    tid = kwargs.get("task_id", "")
    ok = await tm.cancel_task(tid)
    return {"task_id": tid, "cancelled": ok}


# --- Complete Step ---

@tool(name="task_complete_step",
      description="Mark a step within a task as completed. step_id matches task creation indexing (1-based).",
      llm_visible=False,
      parameters=[
          ToolParameter(name="task_id", type="string", required=True, description="Task ID"),
          ToolParameter(name="step_id", type="integer", required=True, description="Step ID (1-based, as given at creation)"),
          ToolParameter(name="result", type="string", required=False, description="Step result description"),
      ])
async def task_complete_step(ctx, **kwargs) -> Dict:
    tm = _get_tm(ctx)
    if not tm:
        return {"error": "Task system is not available in this environment."}
    await tm.complete_step(
        kwargs.get("task_id", ""),
        kwargs.get("step_id", 0),
        kwargs.get("result", ""),
    )
    return {"task_id": kwargs.get("task_id"), "step_id": kwargs.get("step_id"), "status": "completed"}


# --- Create Sub-Task ---

@tool(name="task_create_sub",
      description="Create a sub-task under a parent. Parent is paused until sub-tasks complete.",
      llm_visible=False,
      parameters=[
          ToolParameter(name="parent_task_id", type="string", required=True, description="Parent task ID"),
          ToolParameter(name="title", type="string", required=True, description="Sub-task title"),
          ToolParameter(name="description", type="string", required=False, description="Sub-task description"),
          ToolParameter(name="kind", type="string", required=False, default="ai",
                        description="Sub-task type: ai or script", enum=["ai", "script"]),
          ToolParameter(name="script_path", type="string", required=False, description="Script path (script tasks)"),
          ToolParameter(name="prompt", type="string", required=False, description="AI prompt (AI tasks)"),
      ])
async def task_create_sub(ctx, **kwargs) -> Dict:
    tm = _get_tm(ctx)
    if not tm:
        return {"error": "Task system is not available in this environment."}
    t = await tm.create_sub_task(
        parent_task_id=kwargs.get("parent_task_id", ""),
        title=kwargs.get("title", ""),
        description=kwargs.get("description", ""),
        kind=kwargs.get("kind", "ai"),
        script_path=kwargs.get("script_path"),
        prompt=kwargs.get("prompt"),
    )
    return {"sub_task_id": t.task_id, "title": t.title, "status": t.status.value}


# --- Aggregate: the ONLY LLM-facing task tool ---

@tool(name="task_manager",
      description=(
          "Background task system: persistent AI jobs that survive this chat "
          "(for deferred or asynchronous work — not for tracking the current "
          "conversation). Actions: create (title, optional description/prompt/"
          "steps; execute_now to run immediately) | update | list | execute "
          "(start a pending task) | cancel (stop a running task and its "
          "sub-tasks) | create_sub (child task; parent pauses until children "
          "finish)."
      ),
      security_profile=SecurityProfile.NONE,
      compress_strategy=CompressStrategy.DEFAULT,
      suggested_category=SuggestedCategory.SAFE,
      parameters=[
          ToolParameter(name="action", type="string", required=True,
                        enum=["create", "update", "list", "execute", "cancel", "create_sub"],
                        description="Operation to perform"),
          ToolParameter(name="task_id", type="string", required=False,
                        description="Task ID (update/execute/cancel)"),
          ToolParameter(name="parent_task_id", type="string", required=False,
                        description="Parent task ID (create_sub)"),
          ToolParameter(name="title", type="string", required=False,
                        description="Task title (create/create_sub)"),
          ToolParameter(name="description", type="string", required=False,
                        description="Task description (create/create_sub)"),
          ToolParameter(name="prompt", type="string", required=False,
                        description="Full AI prompt (create/create_sub)"),
          ToolParameter(name="steps", type="array", required=False,
                        description="Step objects [{title, description}] (create)"),
          ToolParameter(name="status", type="string", required=False,
                        enum=["pending", "running", "completed", "failed", "cancelled", "paused"],
                        description="New status (update) or status filter (list)"),
          ToolParameter(name="progress", type="number", required=False,
                        description="Progress 0.0-1.0 (update)"),
          ToolParameter(name="result", type="string", required=False,
                        description="Result text (update)"),
          ToolParameter(name="execute_now", type="boolean", required=False, default=False,
                        description="Run immediately after create"),
      ])
async def task_manager(ctx, **kwargs) -> Dict:
    """Aggregate dispatcher — routes to the per-op handlers above."""
    action = str(kwargs.pop("action", "") or "").strip()
    if not action:
        return {"error": "action is required"}

    if action == "create":
        return await task_create(ctx, **kwargs)
    if action == "update":
        return await task_update(ctx, **kwargs)
    if action == "list":
        return await task_list(ctx, **kwargs)
    if action == "execute":
        return await task_execute(ctx, **kwargs)
    if action == "cancel":
        return await task_cancel(ctx, **kwargs)
    if action == "create_sub":
        return await task_create_sub(ctx, **kwargs)
    return {"error": f"Unknown action: {action}"}
