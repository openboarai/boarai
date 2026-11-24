"""
Script task executor for BoarAi task system.

Executes Python scripts in subprocesses.
Parses output directives: PROGRESS / STEP / STEP_DONE / OUTPUT_FILE / ERROR.
Timeout covers the full lifecycle (stream reading + process wait).
Absolute paths are sandboxed (rejected by default).
"""

import asyncio
import logging
import os
import re
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..models import Task, TaskResult, TaskStep
from ..kind import TaskKind
from ..status import TaskStatus
from .base import BaseTaskExecutor, ExecContext

logger = logging.getLogger(__name__)

RE_PROGRESS = re.compile(r"^PROGRESS:\s*(\d+(?:\.\d+)?)\s*$")
RE_STEP = re.compile(r"^STEP:\s*(.+)\s*$")
RE_STEP_DONE = re.compile(r"^STEP_DONE:\s*(.+)\s*$")
RE_OUTPUT_FILE = re.compile(r"^OUTPUT_FILE:\s*(.+)\s*$")
RE_ERROR = re.compile(r"^ERROR:\s*(.+)\s*$")

# --- Bounded buffering ---
MAX_STDOUT_LINES = 2000        # cap retained stdout lines (tail)
MAX_STDOUT_CHARS = 200_000     # cap total retained stdout chars (OOM guard)
MAX_LINE_CHARS = 16_384        # cap a single line length
MAX_STDERR_TAIL_CHARS = 512    # tail of stderr surfaced to the task error
MAX_OUTPUT_FILES = 50          # cap declared OUTPUT_FILE entries


class ScriptExecutor(BaseTaskExecutor):
    """Executes script tasks in subprocesses."""

    kind = TaskKind.SCRIPT
    DEFAULT_TIMEOUT = 3600  # 1 hour

    def __init__(self) -> None:
        self._running_processes: Dict[str, asyncio.subprocess.Process] = {}

    async def validate(self, task: Task) -> Optional[str]:
        if not task.script_path:
            return "script_path is required for script tasks"
        return None

    async def execute(
        self,
        task: Task,
        context: ExecContext,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> TaskResult:
        script_path = self._resolve_script_path(task, context)
        if not script_path:
            return TaskResult(success=False, error=f"Script not found: {task.script_path}", exit_code=-1)

        output_dir = context.output_dir
        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)

        cmd = [sys.executable, str(script_path)]
        if task.script_args:
            cmd.extend(task.script_args)

        env = os.environ.copy()
        if task.script_env:
            env.update(task.script_env)
        env["BOAR_TASK_ID"] = task.task_id
        env["BOAR_TASK_TITLE"] = task.title
        if output_dir:
            env["BOAR_OUTPUT_DIR"] = output_dir
        if context.storage_path:
            env["BOAR_TASKS_DIR"] = str(context.storage_path)

        timeout = task.timeout_seconds or self.DEFAULT_TIMEOUT

        logger.info(f"Executing script task {task.task_id}: {' '.join(cmd)}")

        stdout_lines: List[str] = []
        stderr_lines: List[str] = []
        output_files: List[str] = []
        exit_code = -1
        collected_error: Optional[str] = None
        step_stack: List[TaskStep] = []
        task_manager = context.task_manager
        emit = self.emit_progress

        try:
            # Windows: the server runs detached (no console) — without
            # CREATE_NO_WINDOW every script task would flash a new console
            # window on the user's screen.
            popen_kwargs: Dict[str, Any] = {}
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=output_dir or str(Path(script_path).parent),
                **popen_kwargs,
            )
            self._running_processes[task.task_id] = process

            # Timeout covers stream reads + process wait
            exit_code, collected_error = await asyncio.wait_for(
                self._consume_process(process, task, stdout_lines, output_files,
                                      step_stack, task_manager, progress_callback, emit,
                                      output_dir=output_dir),
                timeout=timeout,
            )

            logger.info(f"Script task {task.task_id} completed with exit code {exit_code}")

        except asyncio.TimeoutError:
            await self._kill_process(task.task_id)
            return TaskResult(success=False, error=f"Script timed out after {timeout}s", exit_code=-1)
        except asyncio.CancelledError:
            await self._kill_process(task.task_id)
            return TaskResult(success=False, error="Task cancelled", exit_code=-1)
        except Exception as e:
            collected_error = collected_error or str(e)
            logger.error(f"Script task {task.task_id} failed: {e}")
        finally:
            self._running_processes.pop(task.task_id, None)

        success = exit_code == 0 and not collected_error
        output = "\n".join(stdout_lines)

        return TaskResult(
            success=success,
            output=output,
            exit_code=exit_code,
            output_files=output_files if output_files else None,
            error=collected_error or (f"Script exited with code {exit_code}" if not success else None),
        )

    def _append_stdout(self, stdout_lines: List[str], line: str) -> None:
        """Append a stdout line under bounded caps (tail-only retention)."""
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + " ...[line truncated]"
        stdout_lines.append(line)
        # Drop oldest lines once over the line cap
        if len(stdout_lines) > MAX_STDOUT_LINES:
            del stdout_lines[: len(stdout_lines) - MAX_STDOUT_LINES]
        # Drop oldest lines while over the char cap
        total = sum(len(l) for l in stdout_lines)
        while total > MAX_STDOUT_CHARS and len(stdout_lines) > 1:
            total -= len(stdout_lines[0])
            del stdout_lines[0]

    def _check_output_file(self, raw: str, output_dir: Optional[str]) -> Optional[str]:
        """Sandbox a declared OUTPUT_FILE path; returns canonical path or None."""
        raw = raw.strip()
        if not raw:
            return None
        try:
            declared = Path(raw)
        except (TypeError, ValueError):
            return None
        if declared.is_absolute():
            logger.warning("OUTPUT_FILE rejected (sandbox): absolute path %s", raw)
            return None
        if ".." in declared.parts:
            logger.warning("OUTPUT_FILE rejected (sandbox): parent traversal %s", raw)
            return None
        if output_dir:
            base = Path(output_dir).resolve()
            candidate = (base / declared).resolve()
            try:
                candidate.relative_to(base)
            except ValueError:
                logger.warning("OUTPUT_FILE escapes output dir: %s", raw)
                return None
            return str(candidate)
        return str(Path.cwd() / declared)

    async def _consume_process(
        self,
        process: asyncio.subprocess.Process,
        task: Task,
        stdout_lines: List[str],
        output_files: List[str],
        step_stack: List[TaskStep],
        task_manager: Any,
        progress_callback: Optional[Callable[[float], None]],
        emit,
        output_dir: Optional[str] = None,
    ) -> tuple:
        """Read stdout/stderr in parallel, then wait. Returns (exit_code, error)."""
        stderr_data_future = asyncio.ensure_future(process.stderr.read())
        collected_error: Optional[str] = None

        async for line_bytes in process.stdout:
            line = line_bytes.decode("utf-8", errors="replace").rstrip()
            self._append_stdout(stdout_lines, line)
            logger.debug(f"[{task.task_id}] {line}")

            m = RE_PROGRESS.match(line)
            if m:
                val = float(m.group(1)) / 100.0
                await emit(progress_callback, min(val, 1.0))
                continue

            m = RE_STEP.match(line)
            if m:
                step_name = m.group(1).strip()
                matched = None
                for s in task.steps:
                    if s.title and s.title.strip() == step_name and s.status == TaskStatus.PENDING:
                        matched = s
                        break
                if matched:
                    matched.status = TaskStatus.RUNNING
                    matched.started_at = datetime.now()
                    step_stack.append(matched)
                    if task_manager:
                        await task_manager._save(task)
                logger.debug(f"Step started: {step_name}")
                continue

            m = RE_STEP_DONE.match(line)
            if m:
                step_name = m.group(1).strip()
                matched = None
                for s in reversed(step_stack) if step_stack else task.steps:
                    if s.title and s.title.strip() == step_name:
                        matched = s
                        break
                if matched:
                    matched.status = TaskStatus.COMPLETED
                    matched.completed_at = datetime.now()
                    if step_stack and step_stack[-1] is matched:
                        step_stack.pop()
                    if task_manager and task.steps:
                        completed = sum(1 for s in task.steps if s.status == TaskStatus.COMPLETED)
                        await task_manager.update_progress(task.task_id, completed / len(task.steps))
                logger.debug(f"Step completed: {step_name}")
                continue

            m = RE_ERROR.match(line)
            if m:
                error_msg = m.group(1).strip()
                collected_error = error_msg
                logger.warning(f"Script error directive: {error_msg}")
                continue

            m = RE_OUTPUT_FILE.match(line)
            if m:
                if len(output_files) < MAX_OUTPUT_FILES:
                    accepted = self._check_output_file(m.group(1), output_dir)
                    if accepted:
                        output_files.append(accepted)
                else:
                    logger.warning("OUTPUT_FILE cap reached (%d), ignoring: %s",
                                   MAX_OUTPUT_FILES, m.group(1).strip())
                continue

        # Read stderr after stdout is exhausted
        stderr_data = await stderr_data_future
        stderr_tail = ""
        if stderr_data:
            stderr_text = stderr_data.decode("utf-8", errors="replace")
            stderr_tail = stderr_text[-MAX_STDERR_TAIL_CHARS:]
            for l in stderr_text.splitlines():
                logger.warning(f"[{task.task_id}] STDERR: {l}")

        exit_code = await process.wait()
        # Surface a stderr tail fragment on failure so errors are not silent
        if stderr_tail and (exit_code != 0 or collected_error):
            if collected_error:
                collected_error = f"{collected_error}\n[stderr] {stderr_tail.strip()}"
            else:
                collected_error = f"[stderr] {stderr_tail.strip()}"
        return exit_code, collected_error

    async def cancel(self, task_id: str) -> bool:
        return await self._kill_process(task_id)

    async def _kill_process(self, task_id: str, grace_s: float = 3.0) -> bool:
        process = self._running_processes.get(task_id)
        if not process:
            return False
        try:
            if os.name == "nt":
                # Windows: terminate the whole process tree (grandchildren included)
                # via taskkill /T /F before falling back to terminate().
                if process.returncode is None:
                    try:
                        result = subprocess.run(
                            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                            capture_output=True, text=True, timeout=grace_s,
                        )
                        if result.returncode == 0:
                            logger.info("Killed process tree for task %s (taskkill)", task_id)
                            try:
                                await asyncio.wait_for(process.wait(), timeout=grace_s)
                            except asyncio.TimeoutError:
                                pass
                            return True
                        logger.warning("taskkill failed (%d): %s", result.returncode, result.stdout.strip())
                    except (subprocess.SubprocessError, OSError) as e:
                        logger.warning("taskkill unavailable (%s), falling back to terminate", e)
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=grace_s)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            else:
                process.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), timeout=grace_s)
                except asyncio.TimeoutError:
                    process.send_signal(signal.SIGKILL)
                    await process.wait()
            logger.info(f"Killed process for task {task_id}")
            return True
        except ProcessLookupError:
            return True
        except Exception as e:
            logger.error(f"Failed to kill process for {task_id}: {e}")
            return False

    def _resolve_script_path(self, task: Task, context: ExecContext) -> Optional[str]:
        raw = task.script_path
        if not raw or not isinstance(raw, str) or not raw.strip():
            return None

        try:
            script = Path(raw.strip())
        except (TypeError, ValueError):
            return None

        def _accept(candidate: Path) -> Optional[str]:
            try:
                resolved = candidate.resolve()
            except OSError:
                return None
            if not resolved.is_file():
                return None
            return str(resolved)

        # Absolute paths: default-REJECT (sandbox override if needed)
        if script.is_absolute():
            logger.warning("Script path rejected (sandbox): absolute path %s", raw)
            return None

        if ".." in script.parts:
            logger.warning("Script path rejects parent traversal: %s", raw)
            return None

        if context.storage_path:
            base = Path(context.storage_path) / "scripts"
            candidate = (base / script).resolve()
            try:
                candidate.relative_to(base.resolve())
            except ValueError:
                logger.warning("Script path escapes scripts dir: %s", raw)
                return None
            accepted = _accept(candidate)
            if accepted:
                return accepted

        base = Path.cwd() / "scripts"
        candidate = (base / script).resolve()
        try:
            candidate.relative_to(base.resolve())
        except ValueError:
            return None
        return _accept(candidate)