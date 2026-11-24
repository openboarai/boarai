"""Per-run file-change aggregation.

Collects the file-change records surfaced by file tools (via
``core.session.run_tools.execute_single_tool``) into a net per-run view: which
files were added/modified/deleted, line counts, and a combined unified diff.

The result is emitted as the ``run.diff`` event when a run finishes and
persisted to the session's artifact change log, so "what did this conversation
change on disk" is traceable live and after reload.

Design mirrors the reference agents (codex ``TurnDiffTracker``, hermes
``LocalEditSnapshot``): capture per-change diffs at the tool, aggregate by path
at the run, render/push once. Diffs are produced by the file tools themselves
(``difflib.unified_diff``) because only they hold the exact before/after text.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Caps so a pathological run cannot produce an unbounded payload.
MAX_RUN_DIFF_CHARS = 200_000
MAX_TRACKED_FILES = 500


class RunDiffTracker:
    """Aggregates the file changes made during a single run."""

    def __init__(self) -> None:
        # path -> {first_type, last_type, diffs: [str], additions, deletions}
        self._by_path: Dict[str, Dict[str, Any]] = {}
        self._order: List[str] = []

    def add(self, file_change: Optional[Dict[str, Any]]) -> None:
        """Accumulate one file-change record (no-op for empty/invalid input)."""
        if not isinstance(file_change, dict):
            return
        path = str(file_change.get("path") or "").strip()
        if not path:
            return
        change_type = str(file_change.get("change_type") or "modified")
        diff = file_change.get("diff")
        try:
            adds = int(file_change.get("additions") or 0)
            dels = int(file_change.get("deletions") or 0)
        except (TypeError, ValueError):
            adds = dels = 0

        if path not in self._by_path and len(self._by_path) >= MAX_TRACKED_FILES:
            # Past the cap, stop tracking brand-new paths (existing ones still
            # accumulate counts) so the payload stays bounded.
            return

        entry = self._by_path.get(path)
        if entry is None:
            entry = {
                "first_type": change_type,
                "last_type": change_type,
                "diffs": [],
                "additions": 0,
                "deletions": 0,
                # Revert sources (server-side only; stripped from WS payload):
                "existed_before": None,  # state at FIRST touch this run
                "snapshot": None,        # earliest before-content snapshot
                "trash_id": None,        # latest delete's trash id
            }
            self._by_path[path] = entry
            self._order.append(path)
        entry["last_type"] = change_type
        if isinstance(diff, str) and diff:
            entry["diffs"].append(diff)
        entry["additions"] += adds
        entry["deletions"] += dels
        # First-touch existence wins (= "before this run" state).
        if entry["existed_before"] is None and "existed_before" in file_change:
            entry["existed_before"] = bool(file_change.get("existed_before"))
        # Earliest snapshot wins (the true before-run content).
        snap = file_change.get("snapshot")
        if isinstance(snap, str) and snap and not entry["snapshot"]:
            entry["snapshot"] = snap
        # Keep the latest delete's trash id (delete recovery source).
        tid = file_change.get("trash_id")
        if isinstance(tid, str) and tid:
            entry["trash_id"] = tid

    def has_changes(self) -> bool:
        return bool(self._by_path)

    @staticmethod
    def _resolve_type(first: str, last: str) -> str:
        """Collapse a path's sequence of change types into one net label."""
        if last == "deleted":
            return "deleted"
        if first == "added":
            return "added"
        return "modified"

    @staticmethod
    def _is_revertible(e: Dict[str, Any]) -> bool:
        """Whether this path can be restored to its before-run state."""
        existed = e.get("existed_before")
        if existed is False:
            return True  # added this run → revert = move to trash
        return bool(e.get("snapshot") or e.get("trash_id"))

    def files_changed(self, include_internal: bool = False) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for path in self._order:
            e = self._by_path[path]
            item: Dict[str, Any] = {
                "path": path,
                "change_type": self._resolve_type(e["first_type"], e["last_type"]),
                "additions": e["additions"],
                "deletions": e["deletions"],
                "revertible": self._is_revertible(e),
            }
            if include_internal:
                # Server-side only (persisted to changes.json for revert); never
                # sent to the browser. existed_before may be None if unknown.
                item["existed_before"] = bool(e.get("existed_before"))
                item["snapshot"] = e.get("snapshot")
                item["trash_id"] = e.get("trash_id")
            out.append(item)
        return out

    def unified_diff(self) -> str:
        """Combined unified diff across all touched files, in first-touch order."""
        chunks: List[str] = []
        total = 0
        truncated = False
        for path in self._order:
            for d in self._by_path[path]["diffs"]:
                if total + len(d) > MAX_RUN_DIFF_CHARS:
                    truncated = True
                    break
                chunks.append(d if d.endswith("\n") else d + "\n")
                total += len(d) + 1
            if truncated:
                break
        text = "".join(chunks)
        if truncated:
            text += "\n…(run diff truncated)"
        return text

    def stats(self) -> Dict[str, int]:
        return {
            "files": len(self._by_path),
            "additions": sum(e["additions"] for e in self._by_path.values()),
            "deletions": sum(e["deletions"] for e in self._by_path.values()),
        }

    def to_payload(self, include_internal: bool = False) -> Dict[str, Any]:
        return {
            "files_changed": self.files_changed(include_internal=include_internal),
            "unified_diff": self.unified_diff(),
            "stats": self.stats(),
        }
