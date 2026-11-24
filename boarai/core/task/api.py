"""
Task API handler for BoarAi system.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from aiohttp import web

from .manager import get_task_manager
from .kind import TaskKind
from .status import TaskStatus
from .models import TaskStep, TaskSchedule
from boarai.core.runtime import parse_json_object, spawn

logger = logging.getLogger(__name__)


class TaskAPIHandler:
    """Handles task API requests."""

    def __init__(self):
        self._task_manager = get_task_manager()

    def _ok(self, data: Any) -> web.Response:
        return web.json_response({"success": True, "data": data})

    def _err(self, status_code: int, code: str, message: str) -> web.Response:
        return web.json_response(
            {"success": False, "error": {"code": code, "message": message}},
            status=status_code,
        )

    # --- List ---

    async def handle_list_tasks(self, request: web.Request) -> web.Response:
        try:
            sid = request.query.get("session_id")
            st = request.query.get("status")
            kd = request.query.get("kind")
            pid = request.query.get("parent_task_id")
            limit = int(request.query.get("limit", 100))
            offset = int(request.query.get("offset", 0))

            tasks = await self._task_manager.list_tasks(
                session_id=sid,
                status=TaskStatus(st) if st else None,
                kind=TaskKind(kd) if kd else None,
                parent_task_id=pid,
                limit=limit,
                offset=offset,
            )
            return self._ok({
                "count": len(tasks),
                "tasks": [t.to_summary_dict() for t in tasks],
                "limit": limit,
                "offset": offset,
            })
        except Exception as e:
            logger.error(f"Error listing tasks: {e}")
            return self._err(500, "INTERNAL_ERROR", str(e))

    # --- Create ---

    async def handle_create_task(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()

            # Steps
            steps = None
            if "steps" in data and data["steps"]:
                steps = [
                    TaskStep(
                        step_id=i + 1,
                        title=s.get("title", f"Step {i+1}"),
                        description=s.get("description"),
                    )
                    for i, s in enumerate(data["steps"])
                ]

            # Schedule
            schedule = None
            if "schedule" in data and data["schedule"]:
                s = data["schedule"]
                from .timeutil import parse_iso_datetime
                next_run = parse_iso_datetime(s.get("next_run_at"), timezone=s.get("timezone"))
                schedule = TaskSchedule(
                    type=s.get("type", "once"),
                    expression=s.get("expression"),
                    timezone=s.get("timezone"),
                    next_run_at=next_run,
                    run_immediately=bool(s.get("run_immediately", False)),
                    reschedule_on_failure=bool(s.get("reschedule_on_failure", True)),
                    max_consecutive_failures=int(s.get("max_consecutive_failures", 0) or 0),
                )

            task = await self._task_manager.create_task(
                title=data["title"],
                description=data.get("description", ""),
                kind=TaskKind(data.get("kind", "ai")),
                script_path=data.get("script_path"),
                script_args=data.get("script_args"),
                script_env=data.get("script_env"),
                prompt=data.get("prompt"),
                model=data.get("model"),
                steps=steps,
            session_id=data.get("session_id", ""),
            session_mode=data.get("session_mode", "once"),
            parent_task_id=data.get("parent_task_id"),
                metadata=data.get("metadata"),
                schedule=schedule,
                max_retries=int(data.get("max_retries", 0) or 0),
                timeout_seconds=int(data.get("timeout_seconds", 0) or 0),
                depends_on=data.get("depends_on"),
            )

            # Optionally execute immediately
            if data.get("execute_now"):
                asyncio = __import__("asyncio")
                spawn(self._task_manager.execute_task(task.task_id))

            return self._ok(task.to_dict())
        except Exception as e:
            logger.error(f"Error creating task: {e}")
            return self._err(500, "INTERNAL_ERROR", str(e))

    # --- Get ---

    async def handle_get_task(self, request: web.Request) -> web.Response:
        try:
            task_id = request.match_info["task_id"]
            inc_tree = request.query.get("tree", "false").lower() == "true"
            task = await self._task_manager.get_task(task_id, include_tree=inc_tree)
            if not task:
                return self._err(404, "NOT_FOUND", f"Task {task_id} not found")
            return self._ok(task.to_dict())
        except Exception as e:
            logger.error(f"Error getting task: {e}")
            return self._err(500, "INTERNAL_ERROR", str(e))

    # --- Update ---

    async def handle_update_task(self, request: web.Request) -> web.Response:
        try:
            task_id = request.match_info["task_id"]
            data, parse_error = parse_json_object(await request.read())
            if data is None:
                return self._err(400, "INVALID_BODY", parse_error)

            update_fields = {}
            simple_fields = [
                "title", "description", "script_path", "script_args",
                "script_env", "prompt", "model", "session_id", "session_mode",
                "progress", "error", "max_retries", "timeout_seconds",
                "depends_on",
            ]
            for f in simple_fields:
                if f in data:
                    val = data[f]
                    if f == "progress":
                        val = float(val or 0)
                    if f == "max_retries" or f == "timeout_seconds":
                        val = int(val or 0)
                    update_fields[f] = val

            if "status" in data:
                update_fields["status"] = TaskStatus(data["status"])
            if "kind" in data:
                update_fields["kind"] = TaskKind(data["kind"])
            if "result" in data:
                from .models import TaskResult
                r = data["result"]
                update_fields["result"] = TaskResult.from_dict(r) if isinstance(r, dict) else TaskResult.from_any(r)
            if "schedule" in data:
                s = data["schedule"]
                if s is None:
                    update_fields["schedule"] = None
                elif s:
                    from .timeutil import parse_iso_datetime
                    next_run = parse_iso_datetime(s.get("next_run_at"), timezone=s.get("timezone"))
                    update_fields["schedule"] = TaskSchedule(
                        type=s.get("type", "once"),
                        expression=s.get("expression"),
                        timezone=s.get("timezone"),
                        next_run_at=next_run,
                        run_immediately=bool(s.get("run_immediately", False)),
                        reschedule_on_failure=bool(s.get("reschedule_on_failure", True)),
                        max_consecutive_failures=int(s.get("max_consecutive_failures", 0) or 0),
                    )

            task = await self._task_manager.update_task(task_id, **update_fields)
            if not task:
                return self._err(404, "NOT_FOUND", f"Task {task_id} not found")
            return self._ok(task.to_dict())
        except Exception as e:
            logger.error(f"Error updating task: {e}")
            return self._err(500, "INTERNAL_ERROR", str(e))

    # --- Delete ---

    async def handle_delete_task(self, request: web.Request) -> web.Response:
        try:
            task_id = request.match_info["task_id"]
            ok = await self._task_manager.delete_task(task_id)
            if not ok:
                return self._err(404, "NOT_FOUND", f"Task {task_id} not found")
            return self._ok({"deleted": True})
        except Exception as e:
            logger.error(f"Error deleting task: {e}")
            return self._err(500, "INTERNAL_ERROR", str(e))

    # --- Execute / Cancel / Pause / Resume ---

    async def handle_execute_task(self, request: web.Request) -> web.Response:
        try:
            task_id = request.match_info["task_id"]
            task = await self._task_manager.execute_task(task_id)
            return self._ok(task.to_dict())
        except ValueError as e:
            return self._err(400, "INVALID_REQUEST", str(e))
        except Exception as e:
            logger.error(f"Error executing task: {e}")
            return self._err(500, "INTERNAL_ERROR", str(e))

    async def handle_cancel_task(self, request: web.Request) -> web.Response:
        try:
            task_id = request.match_info["task_id"]
            ok = await self._task_manager.cancel_task(task_id)
            if not ok:
                return self._err(404, "NOT_FOUND", f"Task {task_id} not found")
            return self._ok({"cancelled": True})
        except Exception as e:
            logger.error(f"Error cancelling task: {e}")
            return self._err(500, "INTERNAL_ERROR", str(e))

    async def handle_pause_task(self, request: web.Request) -> web.Response:
        try:
            task_id = request.match_info["task_id"]
            ok = await self._task_manager.pause_task(task_id)
            if not ok:
                return self._err(404, "NOT_FOUND", f"Task {task_id} not found")
            return self._ok({"paused": True})
        except Exception as e:
            logger.error(f"Error pausing task: {e}")
            return self._err(500, "INTERNAL_ERROR", str(e))

    async def handle_resume_task(self, request: web.Request) -> web.Response:
        try:
            task_id = request.match_info["task_id"]
            ok = await self._task_manager.resume_task(task_id)
            if not ok:
                return self._err(404, "NOT_FOUND", f"Task {task_id} not found")
            return self._ok({"resumed": True})
        except Exception as e:
            logger.error(f"Error resuming task: {e}")
            return self._err(500, "INTERNAL_ERROR", str(e))

    # --- Steps ---

    async def handle_list_steps(self, request: web.Request) -> web.Response:
        try:
            task_id = request.match_info["task_id"]
            task = await self._task_manager.get_task(task_id)
            if not task:
                return self._err(404, "NOT_FOUND", f"Task {task_id} not found")
            return self._ok({"steps": [s.to_dict() for s in task.steps]})
        except Exception as e:
            logger.error(f"Error listing steps: {e}")
            return self._err(500, "INTERNAL_ERROR", str(e))

    async def handle_complete_step(self, request: web.Request) -> web.Response:
        try:
            task_id = request.match_info["task_id"]
            raw_step_id = request.match_info["step_id"]
            if not (raw_step_id.isascii() and raw_step_id.isdigit()):
                return self._err(
                    400, "INVALID_STEP_ID",
                    f"Step id must be a non-negative integer, got '{raw_step_id}'",
                )
            step_id = int(raw_step_id)
            data = await request.json() if request.can_read_body else {}
            result = data.get("result") if isinstance(data, dict) else None
            ok = await self._task_manager.complete_step(task_id, step_id, result)
            if not ok:
                return self._err(404, "NOT_FOUND", f"Task {task_id} or step {step_id} not found")
            return self._ok({"completed": True})
        except Exception as e:
            logger.error(f"Error completing step: {e}")
            return self._err(500, "INTERNAL_ERROR", str(e))

    # --- Sub-tasks ---

    async def handle_list_subtasks(self, request: web.Request) -> web.Response:
        try:
            task_id = request.match_info["task_id"]
            recursive = request.query.get("recursive", "false").lower() == "true"
            subs = await self._task_manager.get_sub_tasks(task_id, recursive=recursive)
            return self._ok({
                "count": len(subs),
                "sub_tasks": [t.to_summary_dict() for t in subs],
            })
        except Exception as e:
            logger.error(f"Error listing sub-tasks: {e}")
            return self._err(500, "INTERNAL_ERROR", str(e))

    async def handle_get_task_tree(self, request: web.Request) -> web.Response:
        try:
            task_id = request.match_info["task_id"]
            task = await self._task_manager.get_task_tree(task_id)
            if not task:
                return self._err(404, "NOT_FOUND", f"Task {task_id} not found")
            return self._ok(task.to_dict())
        except Exception as e:
            logger.error(f"Error getting task tree: {e}")
            return self._err(500, "INTERNAL_ERROR", str(e))

    # --- Stats + Run history ---

    async def handle_task_stats(self, request: web.Request) -> web.Response:
        try:
            return self._ok(self._task_manager.get_stats())
        except Exception as e:
            logger.error(f"Error getting task stats: {e}")
            return self._err(500, "INTERNAL_ERROR", str(e))

    async def handle_task_history(self, request: web.Request) -> web.Response:
        try:
            task_id = request.match_info["task_id"]
            task = await self._task_manager.get_task(task_id)
            if not task:
                return self._err(404, "NOT_FOUND", f"Task {task_id} not found")
            return self._ok({
                "task_id": task_id,
                "run_history": [r.to_dict() for r in task.run_history],
            })
        except Exception as e:
            logger.error(f"Error getting task history: {e}")
            return self._err(500, "INTERNAL_ERROR", str(e))


# --- Singleton ---

_task_api_handler: Optional[TaskAPIHandler] = None


def get_task_api_handler() -> TaskAPIHandler:
    global _task_api_handler
    if _task_api_handler is None:
        _task_api_handler = TaskAPIHandler()
    return _task_api_handler