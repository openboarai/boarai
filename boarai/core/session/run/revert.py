"""Revert file changes recorded by the run-level change log.

Snapshot-based (no git): each touched file is restored to its before-run state
using the pre-write snapshot captured by the file tools, or the trash copy for
deletions. Files that were *added* during the run are moved to the trash
(recoverable), not hard-deleted.

Granularities (all confirmed product requirements):
  - :func:`revert_file`    — one file, back to before a given run.
  - :func:`revert_run`     — every file a run touched, back to before that run.
  - :func:`revert_session` — every file the session touched, back to before the
    session (earliest snapshot per path). Successfully reverted entries are
    pruned from the change log (failed ones stay retryable).
  - :func:`accept_session` — *keep* everything: drop the change log and
    release snapshots; files on disk stay as they are.

Safety:
  - Targets are resolved against the session workspace and rejected if they
    escape it (path traversal).
  - Added files go to the trash; restores are best-effort per file, so one
    failure never aborts the rest. The result reports reverted/failed lists.
  - Real-time by design (product decision 2026-11-12): revert/accept are
    allowed while a run is active. The running agent may write the same files
    again afterwards; its change record is appended at run completion, so
    later changes appear as fresh log entries.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ==================== Helpers ====================

async def _get_workspace(session_id: str) -> Optional[Path]:
    """Resolve the session's workspace directory (absolute)."""
    try:
        from boarai.core.session.manager import get_session_manager

        sm = get_session_manager()
        sess = await sm.get_session(session_id)
        ws = getattr(sess, "workspace_path", "") if sess else ""
        if ws:
            return Path(ws).resolve()
    except Exception as e:  # noqa: BLE001
        logger.debug("revert: workspace lookup failed for %s: %s", session_id, e)
    # Fallback: standard per-session workspace location.
    try:
        from boarai.core.runtime import get_data_dir

        p = Path(get_data_dir()) / "sessions" / session_id / "workspace"
        return p.resolve() if p.exists() else None
    except Exception:  # noqa: BLE001
        return None


def _safe_resolve(workspace: Path, rel: str) -> Optional[Path]:
    """Resolve a workspace-relative path, rejecting traversal outside it."""
    if not rel:
        return None
    try:
        candidate = (workspace / rel).resolve()
        return candidate if candidate.is_relative_to(workspace.resolve()) else None
    except Exception:  # noqa: BLE001
        return None


async def _apply_entry(workspace: Path, entry: Dict[str, Any]) -> Dict[str, Any]:
    """Revert a single file entry to its before-run state."""
    rel = str(entry.get("path") or "")
    target = _safe_resolve(workspace, rel)
    if target is None:
        return {"path": rel, "ok": False, "reason": "path outside workspace"}

    existed_before = bool(entry.get("existed_before"))
    snapshot = entry.get("snapshot")
    trash_id = entry.get("trash_id")

    try:
        from boarai.core.trash import get_trash_manager

        tm = get_trash_manager()

        # Added during the run/session → it should not exist afterwards.
        if not existed_before:
            if not target.exists():
                return {"path": rel, "ok": True, "action": "already-absent"}
            if tm:
                tid = await tm.trash_file(target)
                if tid:
                    return {"path": rel, "ok": True, "action": "trashed", "trash_id": tid}
            # Fallback: hard delete (trash unavailable).
            if target.is_dir():
                await asyncio.to_thread(shutil.rmtree, str(target), True)
            else:
                target.unlink(missing_ok=True)
            return {"path": rel, "ok": True, "action": "deleted"}

        # Existed before → restore the before-run content.
        if snapshot and Path(snapshot).exists() and tm:
            if await tm.restore_snapshot(Path(snapshot), target):
                return {"path": rel, "ok": True, "action": "restored"}
            return {"path": rel, "ok": False, "reason": "snapshot restore failed"}

        # Deletion with no snapshot → recover the trashed original.
        if trash_id and tm:
            if await tm.restore(trash_id):
                return {"path": rel, "ok": True, "action": "restored-from-trash"}
            return {"path": rel, "ok": False, "reason": "trash restore failed"}

        return {"path": rel, "ok": False, "reason": "no revert source (snapshot expired?)"}
    except Exception as e:  # noqa: BLE001
        logger.warning("revert entry failed for %s: %s", rel, e)
        return {"path": rel, "ok": False, "reason": str(e)}


def _summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    reverted = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    return {
        "ok": True,
        "reverted": reverted,
        "failed": failed,
        "stats": {"reverted": len(reverted), "failed": len(failed)},
    }


async def _load_records(session_id: str) -> List[Dict[str, Any]]:
    """Newest-first run-change records (with internal revert sources)."""
    from boarai.core.session.artifact import get_artifact_store

    store = get_artifact_store()
    return await asyncio.to_thread(store.list_run_changes, session_id, 200, True)


# ==================== Public API ====================

async def revert_run(
    session_id: str, run_id: str, path: Optional[str] = None
) -> Dict[str, Any]:
    """Revert one run's changes (or a single file within it)."""
    records = await _load_records(session_id)
    rec = next((r for r in records if r.get("run_id") == run_id), None)
    if not rec:
        return {"ok": False, "error": f"run record not found: {run_id}"}
    workspace = await _get_workspace(session_id)
    if not workspace:
        return {"ok": False, "error": "session workspace not found"}

    entries = rec.get("files_changed", []) or []
    if path:
        entries = [e for e in entries if e.get("path") == path]
        if not entries:
            return {"ok": False, "error": f"path not in run changes: {path}"}
    if not entries:
        return {"ok": True, "reverted": [], "failed": [], "stats": {"reverted": 0, "failed": 0}}

    results = [await _apply_entry(workspace, e) for e in entries]
    return _summarize(results)


async def revert_session(session_id: str, paths: Optional[List[str]] = None) -> Dict[str, Any]:
    """Revert files the session touched, to their before-session state."""
    records = await _load_records(session_id)
    if not records:
        return {"ok": True, "reverted": [], "failed": [], "stats": {"reverted": 0, "failed": 0}}
    workspace = await _get_workspace(session_id)
    if not workspace:
        return {"ok": False, "error": "session workspace not found"}

    wanted = set(paths) if paths is not None else None
    # records are newest-first → iterate reversed (oldest first); first touch wins.
    earliest: Dict[str, Dict[str, Any]] = {}
    for rec in reversed(records):
        for e in rec.get("files_changed", []) or []:
            p = e.get("path")
            if p and (wanted is None or p in wanted) and p not in earliest:
                earliest[p] = e

    results = [await _apply_entry(workspace, e) for e in earliest.values()]
    summary = _summarize(results)
    await _prune_reverted(session_id, summary)
    return summary


async def _prune_reverted(session_id: str, summary: Dict[str, Any]) -> None:
    """Drop change-log entries that were reverted successfully (best-effort)."""
    ok_paths = [r.get("path") for r in summary.get("reverted", []) if r.get("path")]
    if not ok_paths:
        return
    try:
        from boarai.core.session.artifact import get_artifact_store

        store = get_artifact_store()
        await asyncio.to_thread(store.prune_reverted_entries, session_id, ok_paths)
    except Exception as e:  # noqa: BLE001
        logger.warning("post-revert prune failed for %s: %s", session_id, e)


async def accept_session(session_id: str, paths: Optional[List[str]] = None) -> Dict[str, Any]:
    """*Keep* changes: drop change-log entries and release their snapshots."""
    try:
        from boarai.core.session.artifact import get_artifact_store

        store = get_artifact_store()
        if paths:
            cleared = await asyncio.to_thread(
                store.prune_reverted_entries, session_id, list(paths)
            )
            return {"ok": True, "cleared": cleared}
        cleared = await asyncio.to_thread(store.clear_run_changes, session_id)
        return {"ok": True, "cleared": cleared}
    except Exception as e:  # noqa: BLE001
        logger.warning("accept changes failed for %s: %s", session_id, e)
        return {"ok": False, "error": str(e)}
