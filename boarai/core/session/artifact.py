"""
Artifact Store — per-session file metadata and symbol index.

Part of the session module because artifacts are session-scoped:
storage at .boarai/sessions/{id}/artifacts/, lifecycle tied to
session create/delete.

ArtifactStore: Tracks file versions, hashes, modification history.
SymbolIndex:   Maps code symbols (functions, classes) to file locations
               for efficient lookups without reading entire files.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_STORE_DIR = "artifacts"
_STORE_FILE = "metadata.json"
_SYMBOL_FILE = "symbols.json"
_CHANGES_FILE = "changes.json"

# Change-log caps: keep the most recent N runs and bound each stored diff so a
# long-lived session cannot grow the artifact file without limit.
_MAX_CHANGE_RECORDS = 200
_MAX_STORED_DIFF_CHARS = 200_000


@dataclass
class ArtifactMetadata:
    """Metadata for a tracked file artifact."""
    path: str = ""
    version: int = 0
    current_hash: str = ""
    size_bytes: int = 0
    lines: int = 0
    frames: List[int] = field(default_factory=list)
    last_modified: str = ""
    status: str = "tracked"  # tracked, modified, deleted

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "version": self.version,
            "current_hash": self.current_hash,
            "size_bytes": self.size_bytes,
            "lines": self.lines,
            "frames": self.frames,
            "last_modified": self.last_modified,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArtifactMetadata":
        return cls(
            path=data.get("path", ""),
            version=data.get("version", 0),
            current_hash=data.get("current_hash", ""),
            size_bytes=data.get("size_bytes", 0),
            lines=data.get("lines", 0),
            frames=data.get("frames", []),
            last_modified=data.get("last_modified", ""),
            status=data.get("status", "tracked"),
        )


@dataclass
class SymbolEntry:
    """A code symbol (function, class, variable) mapped to a file location."""
    name: str = ""
    kind: str = "function"  # function, class, method, variable
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SymbolEntry":
        return cls(
            name=data.get("name", ""),
            kind=data.get("kind", "function"),
            file_path=data.get("file_path", ""),
            start_line=data.get("start_line", 0),
            end_line=data.get("end_line", 0),
        )


class ArtifactStore:
    """Per-session artifact metadata store."""

    def __init__(self, sessions_dir: Path):
        self._sessions_dir = Path(sessions_dir)
        self._metadata: Dict[str, ArtifactMetadata] = {}

    def _get_artifacts_dir(self, session_id: str) -> Path:
        safe_id = session_id
        for char in ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]:
            safe_id = safe_id.replace(char, "_")
        return self._sessions_dir / safe_id / _STORE_DIR

    def _get_metadata_path(self, session_id: str) -> Path:
        return self._get_artifacts_dir(session_id) / _STORE_FILE

    def load(self, session_id: str) -> None:
        """Load artifact metadata for a session."""
        path = self._get_metadata_path(session_id)
        if not path.exists():
            self._metadata = {}
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._metadata = {
                k: ArtifactMetadata.from_dict(v)
                for k, v in data.items()
            }
        except Exception as e:
            logger.warning(f"Failed to load artifacts for {session_id}: {e}")
            self._metadata = {}

    def save(self, session_id: str) -> None:
        """Save artifact metadata for a session."""
        path = self._get_metadata_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v.to_dict() for k, v in self._metadata.items()}
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def register(
        self,
        session_id: str,
        path: str,
        current_hash: str = "",
        lines: int = 0,
        size_bytes: int = 0,
    ) -> ArtifactMetadata:
        """Register or update an artifact."""
        if path not in self._metadata:
            meta = ArtifactMetadata(
                path=path,
                version=1,
                current_hash=current_hash,
                lines=lines,
                size_bytes=size_bytes,
                last_modified=datetime.now().isoformat(),
            )
            self._metadata[path] = meta
        else:
            meta = self._metadata[path]
            if meta.current_hash != current_hash:
                meta.version += 1
                meta.current_hash = current_hash
                meta.lines = lines
                meta.size_bytes = size_bytes
                meta.last_modified = datetime.now().isoformat()
                meta.status = "modified"

        self.save(session_id)
        return meta

    def add_frame(self, session_id: str, file_path: str, frame_id: int) -> None:
        """Record that a frame modified this artifact."""
        if file_path in self._metadata:
            meta = self._metadata[file_path]
            if frame_id not in meta.frames:
                meta.frames.append(frame_id)
            self.save(session_id)

    def get(self, file_path: str) -> Optional[ArtifactMetadata]:
        """Get metadata for a file."""
        return self._metadata.get(file_path)

    def list_modified(self) -> List[ArtifactMetadata]:
        """List artifacts with status=modified."""
        return [m for m in self._metadata.values() if m.status == "modified"]

    # ==================== Run Change Log ====================
    # Per-session, run-indexed record of file changes (added/modified/deleted)
    # with the combined unified diff. Stored at artifacts/changes.json so the
    # WebUI can show "what this conversation changed" after a reload. These
    # methods do direct file I/O keyed by session_id (stateless per call), so
    # they do not depend on ``load()`` having been called for that session.

    def _get_changes_path(self, session_id: str) -> Path:
        return self._get_artifacts_dir(session_id) / _CHANGES_FILE

    def record_run_changes(
        self,
        session_id: str,
        run_id: str,
        files_changed: List[Dict[str, Any]],
        unified_diff: str = "",
        stats: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append one run's file-change record to the session change log."""
        if not session_id or not files_changed:
            return
        try:
            path = self._get_changes_path(session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            records: List[Dict[str, Any]] = []
            if path.exists():
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(loaded, list):
                        records = loaded
                except Exception:
                    records = []
            diff_text = unified_diff or ""
            if len(diff_text) > _MAX_STORED_DIFF_CHARS:
                diff_text = diff_text[:_MAX_STORED_DIFF_CHARS] + "\n…(truncated)"
            records.append({
                "run_id": run_id,
                "ts": datetime.now().isoformat(),
                "files_changed": files_changed,
                "stats": stats or {},
                "unified_diff": diff_text,
            })
            evicted: List[Dict[str, Any]] = []
            if len(records) > _MAX_CHANGE_RECORDS:
                evicted = records[:-_MAX_CHANGE_RECORDS]
                records = records[-_MAX_CHANGE_RECORDS:]
            path.write_text(
                json.dumps(records, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # Protect this run's revert snapshots from TTL cleanup; release the
            # snapshots of any evicted records so storage stays bounded.
            self._sync_snapshot_protection(files_changed, evicted)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to record run changes for %s: %s", session_id, e)

    def _sync_snapshot_protection(
        self,
        new_files_changed: List[Dict[str, Any]],
        evicted_records: List[Dict[str, Any]],
    ) -> None:
        """Protect new runs' snapshots; release evicted runs' snapshots."""
        try:
            from boarai.core.trash import get_trash_manager

            tm = get_trash_manager()
            if not tm:
                return
            new_snaps = [
                f.get("snapshot") for f in (new_files_changed or []) if f.get("snapshot")
            ]
            if new_snaps:
                tm.protect_snapshots(new_snaps)
            evicted_snaps: List[str] = []
            for rec in evicted_records or []:
                for f in rec.get("files_changed", []) or []:
                    if f.get("snapshot"):
                        evicted_snaps.append(f["snapshot"])
            if evicted_snaps:
                tm.release_snapshots(evicted_snaps)
        except Exception as e:  # noqa: BLE001
            logger.debug("Snapshot protection sync skipped: %s", e)

    def _read_raw_records(self, path: Path) -> List[Dict[str, Any]]:
        """Load the raw changes.json record list ([] when missing/corrupt)."""
        if not path.exists():
            return []
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, list) else []
        except Exception:
            return []

    def clear_run_changes(self, session_id: str) -> int:
        """Drop the session's whole change log (the user *kept* the changes)."""
        if not session_id:
            return 0
        path = self._get_changes_path(session_id)
        records = self._read_raw_records(path)
        try:
            path.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to clear change log for %s: %s", session_id, e)
            return 0
        # Release protection for every snapshot those records referenced.
        self._sync_snapshot_protection([], records)
        return len(records)

    def prune_reverted_entries(self, session_id: str, reverted_paths: List[str]) -> int:
        """Remove the given paths' entries from the change log."""
        if not session_id or not reverted_paths:
            return 0
        done = set(reverted_paths)
        path = self._get_changes_path(session_id)
        records = self._read_raw_records(path)
        if not records:
            return 0
        kept_records: List[Dict[str, Any]] = []
        removed: List[Dict[str, Any]] = []
        for rec in records:
            kept = []
            for f in rec.get("files_changed", []) or []:
                if f.get("path") in done:
                    removed.append(f)
                else:
                    kept.append(f)
            if kept:
                rec = dict(rec)
                rec["files_changed"] = kept
                kept_records.append(rec)
        if not removed:
            return 0
        try:
            if kept_records:
                path.write_text(
                    json.dumps(kept_records, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            else:
                path.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to prune change log for %s: %s", session_id, e)
            return 0
        # Consumed entries' snapshots are no longer referenced by the log.
        self._sync_snapshot_protection([], [{"files_changed": removed}])
        return len(removed)

    def list_run_changes(
        self, session_id: str, limit: int = 50, include_internal: bool = False
    ) -> List[Dict[str, Any]]:
        """Return the session's run-change records, newest first."""
        if not session_id:
            return []
        path = self._get_changes_path(session_id)
        if not path.exists():
            return []
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, list):
                return []
        except Exception:
            return []
        records = list(reversed(loaded))[: max(1, int(limit))]
        if include_internal:
            return records
        return [self._project_record(r) for r in records]

    @staticmethod
    def _project_record(rec: Dict[str, Any]) -> Dict[str, Any]:
        """Strip server-side revert sources from a record (for the API / UI)."""
        out = dict(rec)
        projected: List[Dict[str, Any]] = []
        for f in rec.get("files_changed", []) or []:
            if not isinstance(f, dict):
                continue
            projected.append({
                "path": f.get("path", ""),
                "change_type": f.get("change_type", "modified"),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "revertible": bool(f.get("revertible", False)),
            })
        out["files_changed"] = projected
        return out


class SymbolIndex:
    """Maps symbol names to file locations."""

    def __init__(self, sessions_dir: Path):
        self._sessions_dir = Path(sessions_dir)
        self._symbols: Dict[str, List[SymbolEntry]] = {}

    def _get_symbols_path(self, session_id: str) -> Path:
        safe_id = session_id
        for char in ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]:
            safe_id = safe_id.replace(char, "_")
        return self._sessions_dir / safe_id / _STORE_DIR / _SYMBOL_FILE

    def load(self, session_id: str) -> None:
        """Load symbols for a session."""
        path = self._get_symbols_path(session_id)
        if not path.exists():
            self._symbols = {}
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._symbols = {
                k: [SymbolEntry.from_dict(s) for s in v]
                for k, v in data.items()
            }
        except Exception as e:
            logger.warning(f"Failed to load symbols: {e}")
            self._symbols = {}

    def save(self, session_id: str) -> None:
        """Save symbols for a session."""
        path = self._get_symbols_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: [s.to_dict() for s in v] for k, v in self._symbols.items()}
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def index_file(
        self,
        session_id: str,
        file_path: str,
        symbols: List[Dict[str, Any]],
    ) -> None:
        """Index symbols from a file."""
        entries = [
            SymbolEntry(
                name=s["name"],
                kind=s.get("kind", "function"),
                file_path=file_path,
                start_line=s.get("start_line", 0),
                end_line=s.get("end_line", 0),
            )
            for s in symbols
        ]
        self._symbols[file_path] = entries
        self.save(session_id)

    def find(self, name: str) -> List[SymbolEntry]:
        """Find symbols by name (exact match)."""
        results = []
        for file_path, entries in self._symbols.items():
            for e in entries:
                if e.name == name:
                    results.append(e)
        return results

    def search(self, query: str) -> List[SymbolEntry]:
        """Fuzzy search symbols (case-insensitive substring)."""
        results = []
        q = query.lower()
        for file_path, entries in self._symbols.items():
            for e in entries:
                if q in e.name.lower():
                    results.append(e)
        return results


# ==================== Singleton ====================

_artifact_store: Optional[ArtifactStore] = None


def get_artifact_store() -> ArtifactStore:
    """Get the global ArtifactStore (rooted at ``data_dir/sessions``)."""
    global _artifact_store
    if _artifact_store is None:
        from boarai.core.runtime import get_data_dir

        _artifact_store = ArtifactStore(Path(get_data_dir()) / "sessions")
    return _artifact_store
