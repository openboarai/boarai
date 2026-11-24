"""
AI task executor for BoarAi task system.

Executes AI tasks by sending prompts to an LLM/Agent via RunManager.
Auto-creates a session if none is assigned.
"""

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from ..models import Task, TaskResult
from ..kind import TaskKind
from .base import BaseTaskExecutor, ExecContext

logger = logging.getLogger(__name__)


class AIExecutor(BaseTaskExecutor):
    """Executes AI tasks via RunManager."""

    kind = TaskKind.AI

    def __init__(self) -> None:
        self._run_ids: Dict[str, str] = {}
        self._cancelled: set = set()

    async def validate(self, task: Task) -> Optional[str]:
        if not task.prompt and not task.description:
            return "prompt or description is required for AI tasks"
        return None

    async def execute(
        self,
        task: Task,
        context: ExecContext,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> TaskResult:
        if task.task_id in self._cancelled:
            self._cancelled.discard(task.task_id)
            return TaskResult(success=False, error="Task cancelled before execution")

        # A retry starts with the PREVIOUS attempt's result still on the task.
        # Clear the completion mark first, otherwise a crash during this
        # attempt would be recovered as "completed" with the old answer.
        try:
            from boarai.core.runtime.artifact_mark import clear_mark

            clear_mark("task", task.task_id)
        except Exception:  # noqa: BLE001 — bookkeeping only
            pass

        session_manager = context.session_manager
        run_manager = context.run_manager
        emit = self.emit_progress

        if not session_manager:
            return TaskResult(success=False, error="Session manager not available")
        if not run_manager:
            return TaskResult(success=False, error="Run manager not available")

        prompt = task.prompt or task.description
        model = task.model

        # Resolve session based on session_mode
        session_mode = getattr(task, "session_mode", "once") or "once"
        session_id = task.session_id

        if session_mode == "manual":
            if not session_id:
                return TaskResult(success=False, error="Task session_mode=manual but no session selected")
        elif session_mode == "always":
            # Always create a new session, never save back to task
            try:
                s = await session_manager.create_session()
                session_id = getattr(s, "session_id", None) or getattr(s, "id", None) or str(s)
                logger.info(f"AI task {task.task_id}: created new session {session_id} (mode=always)")
            except Exception as e:
                return TaskResult(success=False, error=f"Failed to create session: {e}")
        else:   # "once"
            if not session_id:
                try:
                    s = await session_manager.create_session()
                    session_id = getattr(s, "session_id", None) or getattr(s, "id", None) or str(s)
                    task.session_id = session_id
                    if context.task_manager:
                        await context.task_manager._save(task)
                    logger.info(f"AI task {task.task_id}: auto-created & bound session {session_id}")
                except Exception as e:
                    return TaskResult(success=False, error=f"Failed to auto-create session: {e}")

        # Length, not content: the prompt is user text, and a log file that
        # outlives the task is the wrong place for it (the session journal
        # already stores the message where it belongs).
        logger.info(
            "Executing AI task %s (session=%s prompt_chars=%d)",
            task.task_id, session_id, len(prompt or ""),
        )

        try:
            session = await session_manager.get_session(session_id)
            if not session:
                session = await session_manager.create_session(session_id=session_id)

            # Add prompt as user message
            if hasattr(session, "add_message"):
                if hasattr(session, "ensure_session_capability_inject"):
                    session.ensure_session_capability_inject()
                session.add_message("user", prompt)
                await session_manager.save_session(session_id)

            loop = asyncio.get_running_loop()
            completion_future = loop.create_future()

            chunk_count = [0]

            async def on_chunk(event: Dict[str, Any]) -> None:
                if event.get("type") == "chunk":
                    chunk_count[0] += 1
                    prog = min(chunk_count[0] * 0.01, 0.9)
                    await emit(progress_callback, prog)

            run_id = await run_manager.create_run(
                session_id=session_id,
                model=model,
                task_id=task.task_id,
                completion_future=completion_future,
            )
            self._run_ids[task.task_id] = run_id

            if hasattr(run_manager, "register_ws_callback"):
                run_manager.register_ws_callback(run_id, on_chunk)

            completed_run = await completion_future

            if hasattr(run_manager, "unregister_ws_callback"):
                run_manager.unregister_ws_callback(run_id, on_chunk)

            run_status = getattr(completed_run, "status", None)
            run_status_str = run_status.value if hasattr(run_status, "value") else str(run_status or "completed")
            full_response = getattr(completed_run, "full_response", "")
            run_error = getattr(completed_run, "error", None)

            await emit(progress_callback, 1.0)

            if run_status_str == "completed":
                logger.info(f"AI task {task.task_id} completed: {len(full_response)} chars")
                try:
                    from boarai.core.runtime.artifact_mark import (
                        config_fingerprint,
                        fingerprint_text,
                        write_mark,
                    )

                    write_mark(
                        "task",
                        task.task_id,
                        run_id=run_id,
                        config_fp=config_fingerprint(model=model or ""),
                        output_fp=fingerprint_text(full_response),
                        extra={"session_id": session_id},
                    )
                except Exception:  # noqa: BLE001 — bookkeeping only
                    pass
                return TaskResult(success=True, output=full_response, exit_code=0)
            elif run_status_str == "cancelled":
                return TaskResult(success=False, error="Task cancelled", output=full_response)
            else:
                return TaskResult(
                    success=False,
                    error=run_error or f"Run ended with status: {run_status_str}",
                    output=full_response,
                    exit_code=-1,
                )

        except asyncio.CancelledError:
            logger.info(f"AI task {task.task_id} cancelled")
            run_id = self._run_ids.get(task.task_id)
            if run_id and run_manager:
                try:
                    await run_manager.cancel_run(run_id)
                except Exception:
                    pass
            return TaskResult(success=False, error="Task cancelled")
        except Exception as e:
            logger.error(f"AI task {task.task_id} error: {e}")
            return TaskResult(success=False, error=str(e), exit_code=-1)
        finally:
            self._run_ids.pop(task.task_id, None)

    async def cancel(self, task_id: str) -> bool:
        self._cancelled.add(task_id)
        try:
            from boarai.core.session import get_run_manager
            run_manager = get_run_manager()
            run_id = self._run_ids.get(task_id)
            if run_id:
                logger.info(f"Cancelling Run {run_id} for AI task {task_id}")
                return await run_manager.cancel_run(run_id)
        except Exception as e:
            logger.error(f"Failed to cancel AI task {task_id}: {e}")
        return False