"""
Task storage for BoarAi task system.

Handles persistent storage of tasks as JSON files, one file per task.
Follows the same pattern as SessionStorage.

Storage layout:
    .boarai/tasks/<task_id>.json           → Task data
    .boarai/tasks/output/<task_id>/        → Task output files
    .boarai/tasks/scripts/                 → User/Agent uploaded scripts
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Task

logger = logging.getLogger(__name__)


class TaskStorage:
    """Task storage handler."""

    def __init__(self, storage_path: Path):
        """Initialize task storage."""
        self._storage_path = Path(storage_path).resolve()
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Create all necessary directories."""
        self._storage_path.mkdir(parents=True, exist_ok=True)
        (self._storage_path / "output").mkdir(exist_ok=True)
        (self._storage_path / "scripts").mkdir(exist_ok=True)

    def _get_task_path(self, task_id: str) -> Path:
        """Get path to task JSON file."""
        safe_id = task_id.replace("/", "_").replace("\\", "_")
        return self._storage_path / f"{safe_id}.json"

    def _get_output_dir(self, task_id: str) -> Path:
        """Get output directory for a task."""
        safe_id = task_id.replace("/", "_").replace("\\", "_")
        path = self._storage_path / "output" / safe_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def save_task(self, task: Task) -> bool:
        """Save a task to storage (atomic write)."""
        try:
            task_path = self._get_task_path(task.task_id)
            task_data = task.to_dict()
            content = json.dumps(task_data, indent=2, ensure_ascii=False)

            import aiofiles
            tmp_path = task_path.with_suffix(task_path.suffix + ".tmp")
            async with aiofiles.open(tmp_path, "w", encoding="utf-8") as f:
                await f.write(content)
                await f.flush()
                try:
                    os.fsync(f.fileno())
                except (OSError, AttributeError):
                    pass
            os.replace(tmp_path, task_path)

            logger.debug(f"Saved task {task.task_id} to {task_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to save task {task.task_id}: {e}")
            try:
                tmp = self._get_task_path(task.task_id).with_suffix(".json.tmp")
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
            except Exception:
                pass
            return False

    async def load_task(self, task_id: str) -> Optional[Task]:
        """Load a task from storage."""
        try:
            task_path = self._get_task_path(task_id)
            if not task_path.exists():
                return None

            import aiofiles
            async with aiofiles.open(task_path, "r", encoding="utf-8") as f:
                content = await f.read()
                task_data = json.loads(content)

            task = Task.from_dict(task_data)
            logger.debug(f"Loaded task {task_id} from {task_path}")
            return task

        except Exception as e:
            logger.error(f"Failed to load task {task_id}: {e}")
            return None

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task from storage."""
        try:
            task_path = self._get_task_path(task_id)
            if task_path.exists():
                task_path.unlink()
                logger.debug(f"Deleted task {task_id} from {task_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete task {task_id}: {e}")
            return False

    async def list_tasks(self) -> List[str]:
        """List all task IDs in storage."""
        try:
            return [
                f.stem for f in self._storage_path.glob("*.json")
            ]
        except Exception as e:
            logger.error(f"Failed to list tasks: {e}")
            return []

    async def load_many(self, task_ids: List[str]) -> List[Task]:
        """Batch load tasks (skips missing/corrupt files)."""
        if not task_ids:
            return []
        try:
            existing = {
                f.stem: f for f in self._storage_path.glob("*.json")
            }
        except Exception as e:
            logger.error(f"Failed to scan tasks for batch load: {e}")
            return []

        import aiofiles

        async def _read_one(task_id: str) -> Optional[Task]:
            path = existing.get(task_id)
            if path is None:
                return None
            try:
                async with aiofiles.open(path, "r", encoding="utf-8") as f:
                    content = await f.read()
                return Task.from_dict(json.loads(content))
            except Exception as e:
                logger.error(f"Failed to load task {task_id}: {e}")
                return None

        tasks = await asyncio.gather(*(_read_one(tid) for tid in task_ids))
        return [t for t in tasks if t is not None]

    async def task_exists(self, task_id: str) -> bool:
        """Check if a task exists in storage."""
        return self._get_task_path(task_id).exists()

    async def get_storage_stats(self) -> Dict[str, Any]:
        """Get storage statistics."""
        try:
            task_files = list(self._storage_path.glob("*.json"))
            total_size = sum(f.stat().st_size for f in task_files)
            return {
                "storage_path": str(self._storage_path),
                "total_tasks": len(task_files),
                "total_size_bytes": total_size,
            }
        except Exception as e:
            return {"error": str(e)}