"""
TrashManager — soft-delete recycle bin for sessions, agents, skills, and files.

Also provides lightweight file snapshots for write/edit undo.

Storage layout:
    .boarai/trash/
    ├── trash.json               ← index of all trash entries
    ├── sessions/                 ← soft-deleted sessions
    │   └── {ts}_{session_id}/
    ├── agents/                   ← soft-deleted agents
    │   └── {ts}_{agent_name}/
    ├── skills/                   ← soft-deleted skills (whole dir)
    │   └── {ts}_{skill_name}/
    └── files/                    ← soft-deleted files
        └── {ts}_{hash8}__{filename}

    .boarai/snapshots/files/      ← pre-write file snapshots
        └── {ts}_{hash8}__{filename}
"""

import asyncio
import hashlib
import json
import logging
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Default TTLs
TTL_SESSION = 7 * 86400   # 7 days
TTL_AGENT = 30 * 86400    # 30 days
TTL_FILE = 48 * 3600      # 48 hours
TTL_SNAPSHOT = 48 * 3600  # 48 hours
TTL_MEMORY = 30 * 86400   # 30 days
TTL_SKILL = 30 * 86400    # 30 days
TTL_EXTENSION = 30 * 86400  # 30 days

CLEANUP_INTERVAL = 3600   # 1 hour


@dataclass
class TrashEntry:
    """A single entry in the trash index."""
    trash_id: str
    type: str  # "session", "agent", "file"
    original_path: str
    trash_path: str
    deleted_at: str       # ISO datetime
    deleted_by: str = "system"
    expires_at: str = ""  # ISO datetime
    reason: str = ""
    distill_mode: str = ""  # "" | "pending" | "distilled" | "failed"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trash_id": self.trash_id,
            "type": self.type,
            "original_path": self.original_path,
            "trash_path": self.trash_path,
            "deleted_at": self.deleted_at,
            "deleted_by": self.deleted_by,
            "expires_at": self.expires_at,
            "reason": self.reason,
            "distill_mode": self.distill_mode,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrashEntry":
        return cls(**{k: data.get(k, "") for k in cls.__dataclass_fields__})


class TrashManager:
    """Soft-delete recycle bin with auto-cleanup and restore."""

    def __init__(self, project_root: Optional[Path] = None, data_dir: Optional[Path] = None):
        if data_dir is None:
            from boarai.core.runtime import resolve_data_dir
            data_dir = resolve_data_dir(project_root)
        self._root = Path(data_dir)
        self._trash_dir = self._root / "trash"
        self._index_path = self._trash_dir / "trash.json"
        self._snapshot_dir = self._root / "snapshots" / "files"
        self._entries: Dict[str, TrashEntry] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        # Snapshot file names protected from TTL cleanup because a session
        # change log (artifacts/changes.json) still references them for revert.
        self._protected_path = self._root / "snapshots" / "protected.json"
        self._protected_names: Set[str] = set()
        self._protected_loaded = False
        # protect/release run from worker threads (asyncio.to_thread); guard the
        # read-modify-write of protected.json with a threading lock.
        self._protected_lock = threading.Lock()

    # ==================== Lifecycle ====================

    async def start(self) -> None:
        """Load index and start background cleanup."""
        await self._load_index()
        self._trash_dir.mkdir(parents=True, exist_ok=True)
        (self._trash_dir / "sessions").mkdir(exist_ok=True)
        (self._trash_dir / "agents").mkdir(exist_ok=True)
        (self._trash_dir / "files").mkdir(exist_ok=True)
        (self._trash_dir / "memory").mkdir(exist_ok=True)
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        # Run initial cleanup
        await self._auto_cleanup()
        # Start periodic cleanup
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("TrashManager started")

    async def stop(self) -> None:
        """Stop background cleanup."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

    async def _load_index(self) -> None:
        """Load trash.json into memory."""
        if not self._index_path.exists():
            return
        try:
            content = await asyncio.to_thread(
                self._index_path.read_text, encoding="utf-8",
            )
            if content.strip():
                data = json.loads(content)
                self._entries = {
                    k: TrashEntry.from_dict(v) for k, v in data.items()
                }
        except (json.JSONDecodeError, FileNotFoundError):
            self._entries = {}

    async def _save_index(self) -> None:
        """Persist trash.json to disk."""
        try:
            data = {k: v.to_dict() for k, v in self._entries.items()}
            await asyncio.to_thread(
                lambda: self._index_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                ),
            )
        except Exception as e:
            logger.error(f"Failed to save trash index: {e}")

    # ==================== Trash Operations ====================

    async def trash_memory(self, entry: Dict[str, Any]) -> Optional[str]:
        """Store a memory entry in the trash as a JSON payload copy."""
        try:
            entry_id = entry.get("entry_id", "")
            pod_uri = entry.get("pod_uri", "")
            ts = int(time.time())
            trash_id = f"{ts}_{entry_id}"
            dest_dir = self._trash_dir / "memory"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{trash_id}.json"
            await asyncio.to_thread(
                dest.write_text,
                json.dumps(entry, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            now = datetime.now()
            entry_obj = TrashEntry(
                trash_id=trash_id,
                type="memory",
                original_path=f"pod:{pod_uri}",
                trash_path=str(dest),
                deleted_at=now.isoformat(),
                expires_at=(now + timedelta(seconds=TTL_MEMORY)).isoformat(),
                reason=f"TTL {TTL_MEMORY}s",
                metadata={
                    "entry_id": entry_id,
                    "pod_uri": pod_uri,
                    "topic": entry.get("topic", ""),
                    "content_preview": (entry.get("content") or "")[:80],
                    "entry_type": entry.get("entry_type", ""),
                },
            )
            async with self._lock:
                self._entries[trash_id] = entry_obj
                await self._save_index()
            logger.info(f"Memory trashed: {entry_id} → {trash_id}")
            return trash_id
        except Exception as e:
            logger.error(f"Failed to trash memory {entry.get('entry_id', '')}: {e}")
            return None

    async def read_trash_payload(self, trash_id: str) -> Optional[Dict[str, Any]]:
        """Read the stored JSON payload of a trashed memory entry."""
        async with self._lock:
            entry = self._entries.get(trash_id)
        if not entry or entry.type != "memory":
            return None
        try:
            path = Path(entry.trash_path)
            if not path.is_file():
                return None
            content = await asyncio.to_thread(path.read_text, encoding="utf-8")
            return json.loads(content)
        except Exception as e:
            logger.error(f"Failed to read trash payload {trash_id}: {e}")
            return None

    async def trash_session(
        self,
        session_id: str,
        session_dir: Path,
        distill: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Move a session directory to trash."""
        return await self._trash_item(
            item_type="session",
            item_id=session_id,
            source_path=session_dir,
            ttl=TTL_SESSION,
            distill=distill,
            metadata=metadata,
        )

    async def trash_agent(
        self,
        agent_name: str,
        agent_dir: Path,
        distill: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Move an agent directory to trash."""
        return await self._trash_item(
            item_type="agent",
            item_id=agent_name,
            source_path=agent_dir,
            ttl=TTL_AGENT,
            distill=distill,
            metadata=metadata,
        )

    async def trash_file(
        self,
        file_path: Path,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Move a file to trash."""
        if not file_path.is_file():
            return None
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        hash8 = hashlib.sha256(str(file_path).encode()).hexdigest()[:8]
        dest_name = f"{ts}_{hash8}__{file_path.name}"
        dest = self._trash_dir / "files" / dest_name

        return await self._trash_item(
            item_type="file",
            item_id=f"file_{ts}_{hash8}",
            source_path=file_path,
            dest_path=dest,
            ttl=TTL_FILE,
            metadata=metadata,
        )

    async def trash_skill(
        self,
        skill_name: str,
        skill_dir: Path,
    ) -> Optional[str]:
        """Move a skill directory to trash (recycle bin, recoverable)."""
        if not skill_dir.is_dir():
            return None
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        dest = self._trash_dir / "skills" / f"{ts}_{skill_name}"

        return await self._trash_item(
            item_type="skill",
            item_id=f"skill_{ts}_{skill_name}",
            source_path=skill_dir,
            dest_path=dest,
            ttl=TTL_SKILL,
            metadata={"skill_name": skill_name},
        )

    async def trash_extension(
        self,
        ext_name: str,
        ext_dir: Path,
    ) -> Optional[str]:
        """Move a USER-LAYER extension directory to trash (recoverable)."""
        if not ext_dir.is_dir():
            return None
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        dest = self._trash_dir / "extensions" / f"{ts}_{ext_name}"

        return await self._trash_item(
            item_type="extension",
            item_id=f"extension_{ts}_{ext_name}",
            source_path=ext_dir,
            dest_path=dest,
            ttl=TTL_EXTENSION,
            metadata={"extension_name": ext_name},
        )

    async def _trash_item(
        self,
        item_type: str,
        item_id: str,
        source_path: Path,
        ttl: int,
        distill: bool = False,
        dest_path: Optional[Path] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Generic trash operation: move + index."""
        if not source_path.exists():
            return None

        ts = datetime.now()
        if dest_path is None:
            dest_name = f"{ts.strftime('%Y-%m-%d_%H%M%S')}_{item_id}"
            dest_path = self._trash_dir / f"{item_type}s" / dest_name

        # Move to trash (rename, not copy — O(1) on same filesystem)
        try:
            await asyncio.to_thread(
                lambda: _safe_move(str(source_path), str(dest_path)),
            )
        except Exception as e:
            logger.error(f"Failed to trash {item_type} {item_id}: {e}")
            return None

        trash_id = f"trash_{ts.strftime('%Y%m%d%H%M%S%f')}"

        entry = TrashEntry(
            trash_id=trash_id,
            type=item_type,
            original_path=str(source_path),
            trash_path=str(dest_path),
            deleted_at=ts.isoformat(),
            expires_at=(ts + timedelta(seconds=ttl)).isoformat(),
            reason=metadata.get("reason", "") if metadata else "",
            distill_mode="pending" if distill else "",
            metadata=metadata or {},
        )

        async with self._lock:
            self._entries[trash_id] = entry
            await self._save_index()

        logger.info(f"Trashed {item_type}: {item_id} → {dest_path}")
        return trash_id

    # ==================== Snapshot Operations ====================

    async def snapshot_file(self, file_path: Path) -> Optional[Path]:
        """Create a pre-write snapshot of a file."""
        if not file_path.is_file():
            return None

        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        hash8 = hashlib.sha256(str(file_path).encode()).hexdigest()[:8]
        snap_name = f"{ts}_{hash8}__{file_path.name}"
        snap_path = self._snapshot_dir / snap_name

        try:
            await asyncio.to_thread(
                lambda: shutil.copy2(str(file_path), str(snap_path)),
            )
            return snap_path
        except Exception as e:
            logger.debug(f"File snapshot skipped for {file_path}: {e}")
            return None

    async def restore_snapshot(self, snap_path: Path, target_path: Path) -> bool:
        """Restore a file from its snapshot."""
        if not snap_path.exists():
            return False
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(
                lambda: shutil.copy2(str(snap_path), str(target_path)),
            )
            return True
        except Exception as e:
            logger.error(f"Snapshot restore failed: {e}")
            return False

    async def list_snapshots(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List recent file snapshots."""
        if not self._snapshot_dir.exists():
            return []
        results = []
        for f in sorted(self._snapshot_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.is_file():
                results.append({
                    "filename": f.name,
                    "snapshot_path": str(f),
                    "created_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                    "size": f.stat().st_size,
                })
            if len(results) >= limit:
                break
        return results

    async def cleanup_snapshots(self) -> int:
        """Remove expired snapshots (older than TTL_SNAPSHOT)."""
        if not self._snapshot_dir.exists():
            return 0
        protected = self._load_protected()
        cutoff = time.time() - TTL_SNAPSHOT
        count = 0
        for f in self._snapshot_dir.iterdir():
            if not f.is_file() or f.name in protected:
                continue
            if f.stat().st_mtime < cutoff:
                try:
                    f.unlink()
                    count += 1
                except Exception:
                    pass
        if count:
            logger.debug(f"Cleaned up {count} expired snapshots")
        return count

    # ==================== Snapshot Protection (for revert) ====================

    def _load_protected(self) -> Set[str]:
        """Load the protected-snapshot name set (lazily, cached)."""
        if self._protected_loaded:
            return self._protected_names
        try:
            if self._protected_path.exists():
                data = json.loads(self._protected_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._protected_names = {str(x) for x in data}
        except Exception:
            self._protected_names = set()
        self._protected_loaded = True
        return self._protected_names

    def _save_protected(self) -> None:
        try:
            self._protected_path.parent.mkdir(parents=True, exist_ok=True)
            self._protected_path.write_text(
                json.dumps(sorted(self._protected_names), ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to save protected snapshots: {e}")

    @staticmethod
    def _snap_name(path: Any) -> str:
        """Basename of a snapshot path (robust to abs/rel variations)."""
        try:
            return Path(path).name
        except Exception:  # noqa: BLE001
            return str(path)

    def protect_snapshots(self, paths: List[str]) -> None:
        """Protect snapshots from TTL cleanup (referenced by a change log)."""
        names = {self._snap_name(p) for p in (paths or []) if p}
        if not names:
            return
        with self._protected_lock:
            protected = self._load_protected()
            if names - protected:
                self._protected_names = protected | names
                self._save_protected()

    def release_snapshots(self, paths: List[str]) -> None:
        """Unprotect snapshots (e.g. when their change-log records are evicted)."""
        names = {self._snap_name(p) for p in (paths or []) if p}
        if not names:
            return
        with self._protected_lock:
            protected = self._load_protected()
            if names & protected:
                self._protected_names = protected - names
                self._save_protected()

    # ==================== Restore & Purge ====================

    async def restore(self, trash_id: str) -> bool:
        """Restore a trashed item to its original location."""
        async with self._lock:
            entry = self._entries.get(trash_id)
            if not entry:
                return False

            trash_path = Path(entry.trash_path)
            original_path = Path(entry.original_path)

            if not trash_path.exists():
                logger.warning(f"Trash path no longer exists: {trash_path}")
                del self._entries[trash_id]
                await self._save_index()
                return False

            if original_path.exists():
                logger.warning(f"Original path already exists, cannot restore: {original_path}")
                return False

            # Move back
            try:
                original_path.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(
                    lambda: _safe_move(str(trash_path), str(original_path)),
                )
            except Exception as e:
                logger.error(f"Failed to restore {trash_id}: {e}")
                return False

            del self._entries[trash_id]
            await self._save_index()
            logger.info(f"Restored {entry.type}: {entry.original_path}")
            return True

    async def purge(self, trash_id: str) -> bool:
        """Permanently delete a trashed item."""
        async with self._lock:
            entry = self._entries.get(trash_id)
            if not entry:
                return False

            trash_path = Path(entry.trash_path)
            if trash_path.exists():
                try:
                    if trash_path.is_dir():
                        await asyncio.to_thread(
                            lambda: shutil.rmtree(str(trash_path), ignore_errors=True),
                        )
                    else:
                        trash_path.unlink(missing_ok=True)
                except Exception as e:
                    logger.error(f"Failed to purge {trash_id}: {e}")

            del self._entries[trash_id]
            await self._save_index()
            return True

    async def purge_all(self) -> int:
        """Permanently delete all trashed items."""
        async with self._lock:
            count = len(self._entries)
            for entry in list(self._entries.values()):
                trash_path = Path(entry.trash_path)
                if trash_path.exists():
                    try:
                        if trash_path.is_dir():
                            await asyncio.to_thread(
                                lambda p=trash_path: shutil.rmtree(str(p), ignore_errors=True),
                            )
                        else:
                            trash_path.unlink(missing_ok=True)
                    except Exception:
                        pass
            self._entries.clear()
            await self._save_index()
            logger.info(f"Purged all {count} trash entries")
            return count

    # ==================== Distillation Metadata ====================

    def get_pending_distillation(self) -> List[TrashEntry]:
        """Get all trash entries marked for pending distillation."""
        return [e for e in self._entries.values() if e.distill_mode == "pending"]

    def is_distilled(self, trash_id: str) -> bool:
        """Check if an entry has already been distilled."""
        entry = self._entries.get(trash_id)
        return entry is not None and entry.distill_mode == "distilled"

    async def mark_distilled(self, trash_id: str) -> bool:
        """Mark a trash entry as distilled."""
        async with self._lock:
            entry = self._entries.get(trash_id)
            if not entry:
                return False
            entry.distill_mode = "distilled"
            await self._save_index()
            return True

    async def mark_distill_failed(self, trash_id: str) -> bool:
        """Mark a trash entry as distillation failed."""
        async with self._lock:
            entry = self._entries.get(trash_id)
            if not entry:
                return False
            entry.distill_mode = "failed"
            await self._save_index()
            return True

    async def reset_distill_mode(self, trash_id: str) -> bool:
        """Reset distill_mode (e.g., on restore)."""
        async with self._lock:
            entry = self._entries.get(trash_id)
            if not entry:
                return False
            if entry.distill_mode == "distilled":
                return False  # Cannot reset already distilled
            entry.distill_mode = ""
            await self._save_index()
            return True

    # ==================== Auto Cleanup ====================

    async def _auto_cleanup(self) -> int:
        """Remove expired trash entries."""
        now = datetime.now()
        expired = []

        async with self._lock:
            for tid, entry in list(self._entries.items()):
                try:
                    expires = datetime.fromisoformat(entry.expires_at)
                    if expires < now:
                        expired.append(tid)
                except (ValueError, TypeError):
                    expired.append(tid)

        count = 0
        for tid in expired:
            if await self.purge(tid):
                count += 1

        # Also cleanup snapshots
        snap_count = await self.cleanup_snapshots()

        if count or snap_count:
            logger.info(f"Auto-cleanup: {count} trash + {snap_count} snapshots expired")
        return count

    async def _cleanup_loop(self) -> None:
        """Periodic background cleanup."""
        while True:
            try:
                await asyncio.sleep(CLEANUP_INTERVAL)
                await self._auto_cleanup()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Trash cleanup loop error: {e}")

    # ==================== Query ====================

    def list_trash(
        self,
        item_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List trash entries, newest first."""
        entries = list(self._entries.values())
        if item_type:
            entries = [e for e in entries if e.type == item_type]
        entries.sort(key=lambda e: e.deleted_at, reverse=True)
        return [e.to_dict() for e in entries[:limit]]

    def backfill_session_titles(self, entries: List[Dict[str, Any]]) -> None:
        """Fill missing session titles from the trashed session files (in-place)."""
        for e in entries:
            if e.get("type") != "session":
                continue
            meta = e.setdefault("metadata", {})
            if meta.get("title"):
                continue
            title = self._load_session_title(
                meta.get("session_id", ""), Path(e.get("trash_path", ""))
            )
            if title:
                meta["title"] = title

    def _load_session_title(self, session_id: str, trash_path: Path) -> str:
        """Read the session title from a trashed session directory."""
        try:
            if not trash_path.is_dir():
                return ""
            candidates: List[Path] = []
            if session_id:
                candidates.append(trash_path / f"{session_id}.json")
            candidates.extend(trash_path.glob("*.json"))
            for f in candidates:
                if not f.is_file():
                    continue
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    title = data.get("title", "")
                    if title:
                        return title
                except (json.JSONDecodeError, OSError):
                    continue
        except OSError:
            pass
        return ""

    def get_trash(self, trash_id: str) -> Optional[Dict[str, Any]]:
        """Get a single trash entry."""
        entry = self._entries.get(trash_id)
        return entry.to_dict() if entry else None

    def get_stats(self) -> Dict[str, Any]:
        """Get trash statistics."""
        type_counts = {}
        for e in self._entries.values():
            type_counts[e.type] = type_counts.get(e.type, 0) + 1
        return {
            "total": len(self._entries),
            "by_type": type_counts,
        }


# ==================== Helpers ====================

def _safe_move(src: str, dst: str) -> None:
    """Move file/directory, creating parent dirs."""
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    shutil.move(src, dst)


# ==================== Singleton ====================

_trash_manager: Optional[TrashManager] = None


def get_trash_manager(project_root: Optional[Path] = None, data_dir: Optional[Path] = None) -> TrashManager:
    """Get global TrashManager singleton."""
    global _trash_manager
    if _trash_manager is None:
        _trash_manager = TrashManager(project_root, data_dir)
    return _trash_manager
