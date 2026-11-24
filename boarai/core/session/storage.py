"""
Session storage for BoarAi system.

This module handles persistent storage of sessions.
"""

import asyncio
import json
import logging
import os
import copy
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles

from boarai.core.runtime import write_text_atomic_async

from .journal import (
    ENTRY_SESSION_INFO,
    SessionJournal,
    decode_entry,
)

logger = logging.getLogger(__name__)

SESSION_FILE_SUFFIX = ".jsonl"
"""Suffix of the current transcript format (append-only JSONL)."""

LEGACY_FILE_SUFFIX = ".json"
"""Suffix of the v3 single-file JSON: read-only, kept for migration and compatibility."""


class SessionStorage:
    """Session storage handler."""

    def __init__(self, storage_path: Path):
        """Initialize session storage."""
        self._storage_path = Path(storage_path).resolve()
        self._journals: Dict[str, SessionJournal] = {}
        self._save_locks: Dict[str, asyncio.Lock] = {}
        self._ensure_storage_directory()

    def _save_lock(self, session_id: str) -> asyncio.Lock:
        """Per-session lock serializing ``save_session`` critical sections."""
        lock = self._save_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._save_locks[session_id] = lock
        return lock

    def _ensure_storage_directory(self) -> None:
        """Ensure storage directory exists."""
        self._storage_path.mkdir(parents=True, exist_ok=True)

    def _get_session_path(self, session_id: str) -> Path:
        """Get path to the session journal inside its folder."""
        safe = self._sanitize_id(session_id)
        return self._storage_path / safe / f"{safe}{SESSION_FILE_SUFFIX}"

    def _get_legacy_session_path(self, session_id: str) -> Path:
        """Path of the v3 single-file JSON (migration source, read-only)."""
        safe = self._sanitize_id(session_id)
        return self._storage_path / safe / f"{safe}{LEGACY_FILE_SUFFIX}"

    def journal_for(self, session_id: str) -> SessionJournal:
        """Journal for this session, cached by id so the active leaf survives calls."""
        key = self._sanitize_id(session_id)
        journal = self._journals.get(key)
        if journal is None:
            journal = SessionJournal(self._get_session_path(session_id))
            self._journals[key] = journal
        return journal

    def _drop_journal(self, session_id: str) -> None:
        """Forget the cached journal (after the session is deleted or moved)."""
        key = self._sanitize_id(session_id)
        self._journals.pop(key, None)
        self._save_locks.pop(key, None)

    def _get_session_dir(self, session_id: str) -> Path:
        """Get path to session folder."""
        return self._storage_path / self._sanitize_id(session_id)

    def get_session_dir(self, session_id: str) -> Path:
        """Public helper: session folder path for a given session ID."""
        return self._get_session_dir(session_id)

    @staticmethod
    def _sanitize_id(session_id: str) -> str:
        """Replace path-invalid characters in a session ID."""
        safe_id = session_id
        for char in ["/", "\\", ":", "*", "?", "\"", "<", ">", "|"]:
            safe_id = safe_id.replace(char, "_")
        return safe_id
    
    async def save_session(self, session: Any) -> bool:
        """Save session to its append-only journal."""
        tracking = self._snapshot_journal_tracking(session)
        async with self._save_lock(session.session_id):
            try:
                journal = self.journal_for(session.session_id)
                needs_full = (
                    getattr(session, "_journal_needs_rewrite", False)
                    or not journal.exists()
                )

                if needs_full:
                    entries = session.to_entries()
                    await journal.rewrite(entries)
                    session._journal_needs_rewrite = False
                else:
                    # The session is the only source of truth for the active leaf:
                    # truncate_messages (edit-and-rerun) and clear_messages move it
                    # to the branch point, and the append must continue from there
                    # or new messages would hang off the abandoned tail.
                    journal.set_leaf(session.active_leaf)
                    pending = session.pending_journal_entries()
                    if pending:
                        await journal.append_many(pending)

                logger.debug(
                    "Saved session %s (%s)",
                    session.session_id, "rewrite" if needs_full else "append",
                )
                return True

            except Exception as e:
                self._restore_journal_tracking(session, tracking)
                # The append may have reached disk before an I/O error surfaced.
                # A full atomic rewrite on retry deduplicates either outcome.
                session._journal_needs_rewrite = True
                logger.error(f"Failed to save session {session.session_id}: {e}")
                return False

    @staticmethod
    def _snapshot_journal_tracking(session: Any) -> Dict[str, Any]:
        names = (
            "_journal_needs_rewrite",
            "_journal_scalars_written",
            "_journal_usage_written",
            "_journal_tools_written",
            "_journal_compactions_written",
            "journal_root",
            "active_leaf",
        )
        state = {
            name: copy.deepcopy(getattr(session, name))
            for name in names
            if hasattr(session, name)
        }
        state["messages"] = [
            (
                message,
                getattr(message, "entry_id", None),
                getattr(message, "parent_entry_id", None),
                getattr(message, "_journal_fp", None),
            )
            for message in getattr(session, "messages", ())
        ]
        return state

    @staticmethod
    def _restore_journal_tracking(session: Any, state: Dict[str, Any]) -> None:
        for name, value in state.items():
            if name != "messages":
                setattr(session, name, value)
        for message, entry_id, parent_entry_id, fingerprint in state.get("messages", ()):
            message.entry_id = entry_id
            message.parent_entry_id = parent_entry_id
            message._journal_fp = fingerprint

    async def load_session(self, session_id: str) -> Optional[Any]:
        """Load session from its journal, falling back to a legacy v3 file."""
        # Import here to avoid circular imports
        from .manager import Session

        journal = self.journal_for(session_id)
        if journal.exists():
            entries = await journal.read_entries()
            if entries:
                try:
                    session = Session.from_entries(entries)
                except Exception as e:
                    logger.error(
                        "Failed to fold journal for session %s: %s", session_id, e,
                    )
                    return None
                journal.set_leaf(session.active_leaf)
                logger.debug(
                    "Loaded session %s from journal (%s messages)",
                    session_id, len(session.messages),
                )
                return session
            logger.warning(
                "Session journal for %s exists but folded to nothing; "
                "falling back to legacy file if present", session_id,
            )

        return await self._load_legacy_session(session_id)

    async def _load_legacy_session(self, session_id: str) -> Optional[Any]:
        """Read the v3 single-file JSON of a session migration has not reached yet."""
        from .manager import Session

        legacy_path = self._get_legacy_session_path(session_id)
        if not legacy_path.exists():
            return None
        try:
            async with aiofiles.open(legacy_path, 'r', encoding='utf-8') as f:
                content = await f.read()
            session = Session.from_dict(json.loads(content))
            logger.debug("Loaded legacy v3 session %s", session_id)
            return session
        except Exception as e:
            logger.error(f"Failed to load legacy session {session_id}: {e}")
            return None

    @staticmethod
    def _sync_rmtree(path: Path) -> bool:
        """Safely remove a directory tree, handling permission issues (Windows)."""
        import stat
        import shutil

        def _onerror(func, fpath, excinfo):
            """Clear read-only bit and retry once."""
            try:
                os.chmod(fpath, stat.S_IWRITE)
                func(fpath)
            except Exception:
                # Give up on this file — skip it
                pass

        try:
            shutil.rmtree(str(path), onerror=_onerror)
            return True
        except Exception:
            return False

    async def delete_session(self, session_id: str, distill: bool = False, title: Optional[str] = None) -> bool:
        """Delete session folder from storage."""
        try:
            session_dir = self._get_session_dir(session_id)

            if not session_dir.exists():
                self._drop_journal(session_id)
                return True

            # The directory is about to be moved or deleted, so the cached
            # journal points at a path that is about to die.
            self._drop_journal(session_id)

            # Try soft-delete via TrashManager
            try:
                from boarai.core.trash import get_trash_manager
                tm = get_trash_manager()
                if tm:
                    from datetime import datetime
                    session_title = title or ""
                    if not session_title:
                        session_title = await asyncio.to_thread(
                            self._read_session_title, session_dir, session_id
                        )
                    result = await tm.trash_session(
                        session_id=session_id,
                        session_dir=session_dir,
                        distill=distill,
                        metadata={
                            "deleted_at": datetime.now().isoformat(),
                            "deleted_by": "api",
                            "session_id": session_id,
                            "title": session_title,
                        },
                    )
                    if result is not None:
                        logger.info(f"Session {session_id} moved to trash")
                        return True
                    # trash_session returned None = move failed, fall through to rmtree
                    logger.warning(
                        f"Trash move returned None for {session_id}, "
                        f"falling back to rmtree"
                    )
            except Exception as e:
                logger.warning(f"Trash move failed, falling back to rmtree: {e}")

            # Fallback: hard delete
            ok = await asyncio.to_thread(self._sync_rmtree, session_dir)
            if not ok:
                logger.error(f"Failed to delete session directory: {session_dir}")
                return False
            logger.debug(f"Deleted session {session_id} from {session_dir}")

            return True

        except Exception as e:
            logger.error(f"Failed to delete session {session_id}: {e}")
            return False

    @staticmethod
    def _transcript_file(session_dir: Path) -> Optional[Path]:
        """Transcript file inside a session directory: journal first, v3 JSON second."""
        journal = session_dir / f"{session_dir.name}{SESSION_FILE_SUFFIX}"
        if journal.is_file():
            return journal
        legacy = session_dir / f"{session_dir.name}{LEGACY_FILE_SUFFIX}"
        if legacy.is_file():
            return legacy
        return None

    @staticmethod
    def _read_journal_title(path: Path) -> str:
        """Current title from the journal (header value plus later session_info overrides)."""
        title = ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    entry = decode_entry(line)
                    if entry is None:
                        continue
                    etype = entry.get("type")
                    if etype == ENTRY_SESSION_INFO:
                        fields = entry.get("fields")
                        if isinstance(fields, dict) and "title" in fields:
                            title = str(fields.get("title") or "")
                    elif "title" in entry:
                        # Header entry (type == "session")
                        title = str(entry.get("title") or "")
        except OSError:
            return ""
        return title

    @classmethod
    def _read_session_title(cls, session_dir: Path, session_id: str) -> str:
        """Read the stored session title from disk (best-effort)."""
        safe = cls._sanitize_id(session_id)
        journal_file = session_dir / f"{safe}{SESSION_FILE_SUFFIX}"
        if journal_file.is_file():
            return cls._read_journal_title(journal_file)
        try:
            session_file = session_dir / f"{safe}{LEGACY_FILE_SUFFIX}"
            if session_file.is_file():
                data = json.loads(session_file.read_text(encoding="utf-8"))
                return data.get("title", "")
        except (json.JSONDecodeError, OSError):
            pass
        return ""

    async def list_sessions(self) -> List[str]:
        """List all sessions in storage."""
        try:
            session_ids = []

            for session_dir in self._storage_path.iterdir():
                if session_dir.is_dir() and self._transcript_file(session_dir):
                    session_ids.append(session_dir.name)

            return session_ids

        except Exception as e:
            logger.error(f"Failed to list sessions: {e}")
            return []

    async def session_exists(self, session_id: str) -> bool:
        """Check if session exists in storage (journal or legacy v3 file)."""
        return (
            self._get_session_path(session_id).exists()
            or self._get_legacy_session_path(session_id).exists()
        )

    async def get_storage_stats(self) -> Dict[str, Any]:
        """Get storage statistics."""
        try:
            session_dirs = [
                d for d in self._storage_path.iterdir()
                if d.is_dir() and self._transcript_file(d)
            ]

            total_size = 0
            for d in session_dirs:
                for f in d.rglob("*"):
                    if f.is_file():
                        total_size += f.stat().st_size

            return {
                "storage_path": str(self._storage_path),
                "total_sessions": len(session_dirs),
                "total_size_bytes": total_size,
                "total_size_mb": total_size / (1024 * 1024),
            }

        except Exception as e:
            logger.error(f"Failed to get storage stats: {e}")
            return {
                "storage_path": str(self._storage_path),
                "error": str(e),
            }

    async def cleanup_old_sessions(self, max_age_days: int = 30) -> int:
        """Cleanup sessions older than ``max_age_days`` via soft-delete."""
        import time

        try:
            cleaned_count = 0
            current_time = time.time()
            max_age_seconds = max_age_days * 24 * 60 * 60

            for session_dir in self._storage_path.iterdir():
                if not session_dir.is_dir():
                    continue
                session_file = self._transcript_file(session_dir)
                if session_file is None:
                    continue
                try:
                    file_age = current_time - session_file.stat().st_mtime
                    if file_age <= max_age_seconds:
                        continue
                except Exception as e:
                    logger.error(f"Failed to stat session {session_dir.name}: {e}")
                    continue

                # Soft-delete (move to trash) — never hard rmtree here.
                if await self.delete_session(session_dir.name, distill=False):
                    cleaned_count += 1
                    logger.debug(f"Cleaned up old session: {session_dir.name}")

            if cleaned_count > 0:
                logger.info(f"Cleaned up {cleaned_count} old sessions")

            return cleaned_count

        except Exception as e:
            logger.error(f"Failed to cleanup old sessions: {e}")
            return 0


class SessionStorageError(Exception):
    """Session storage related errors."""
    pass
