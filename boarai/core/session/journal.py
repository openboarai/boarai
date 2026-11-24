"""Session journal: append-only JSONL persistence.

Sessions used to be one JSON object (``{id}/{id}.json``) rewritten in full on
every save. That had two consequences: a single streamed run rewrote the whole
history dozens of times (cost growing with the square of session length), and
the file carried no positional record of what the model saw at turn N, so
branching, rewind and exact replay could not be expressed at all.

This module stores the transcript as append-only JSONL, the layout pi/omp uses:
one entry per line, linked through ``(id, parentId)`` into a tree.

Design notes
------------

**Tree, not list.** Each append points ``parentId`` at the current active leaf.
Truncation (re-running after editing a user message) deletes nothing: the new
message hangs off the retained boundary as a new branch, and the abandoned tail
just drops out of the active path. Fork and ``navigate_tree`` therefore cost
nothing extra.

**Read = fold.** ``fold_entries`` streams every entry and folds session scalars,
the message tree and the active path out of it. A corrupt line is skipped with a
warning and never fails the file: one bad line must not make a session
unopenable.

**Changes are new entries, never edits.** Scalar updates are ``session_info``,
field corrections of persisted messages are ``message_patch``, and context
compression is ``compaction`` (carrying ``firstKeptEntryId``, meaning messages
before it leave the model context). The original history always survives, so
replay and audit see what actually happened.

Derived indexes (metadata / symbols / changes under ``artifacts/``) live
elsewhere: they are not transcript and keep their own format.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SESSION_FORMAT_VERSION = 4
"""Current JSONL format version; v3 was the single-file JSON layout that ``migrate.py`` upgrades."""

LEGACY_FORMAT_VERSION = 3

# --- Entry types ---
ENTRY_SESSION = "session"
"""File header; exactly one per session, carrying the initial scalars."""

ENTRY_MESSAGE = "message"
"""One conversation message (user / assistant / system / tool)."""

ENTRY_SESSION_INFO = "session_info"
"""Incremental scalar update (title / pinned / model / status ...)."""

ENTRY_USAGE = "usage"
"""Token and cache usage of one run, folded into ``cache_requests``."""

ENTRY_COMPACTION = "compaction"
"""Context compaction event.

``firstKeptEntryId`` marks the point before which messages leave the model
context (the contiguous-prefix case); ``omitEntryIds`` lists individually
dropped entries (``trim_history`` keeps the system prefix plus the last N turns
and drops a **non-contiguous** middle, which no single boundary can express).
Both only record that these messages are no longer active; the original lines
stay in the file.
"""

ENTRY_MESSAGE_PATCH = "message_patch"
"""Field corrections on an already-persisted message (usage metadata, run error markers)."""

ENTRY_TOOL_CHANGE = "tool_change"
"""Tool set change (toolsAdded / toolsRemoved) so replay can rebuild the visible tools."""

ENTRY_NAVIGATE = "navigate"
"""Repoint the active leaf at another branch (``navigate_tree``)."""

ENTRY_TYPES = frozenset({
    ENTRY_SESSION,
    ENTRY_MESSAGE,
    ENTRY_SESSION_INFO,
    ENTRY_USAGE,
    ENTRY_COMPACTION,
    ENTRY_MESSAGE_PATCH,
    ENTRY_TOOL_CHANGE,
    ENTRY_NAVIGATE,
})


def new_entry_id() -> str:
    """Generate an entry id: eight hex characters."""
    return secrets.token_hex(4)


def make_entry(
    entry_type: str,
    parent_id: Optional[str] = None,
    **payload: Any,
) -> Dict[str, Any]:
    """Build an entry dict."""
    entry: Dict[str, Any] = {
        "type": entry_type,
        "id": payload.pop("entry_id", None) or new_entry_id(),
        "parentId": parent_id,
    }
    entry.update(payload)
    return entry


def encode_entry(entry: Dict[str, Any]) -> str:
    """Serialise an entry into one line, without the trailing newline."""
    return json.dumps(entry, ensure_ascii=False, separators=(",", ":"))


def decode_entry(line: str) -> Optional[Dict[str, Any]]:
    """Parse one entry line; ``None`` when it cannot be parsed."""
    text = line.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("type") not in ENTRY_TYPES:
        return None
    return data


@dataclass
class FoldResult:
    """Result of ``fold_entries``: the folded session state."""

    scalars: Dict[str, Any] = field(default_factory=dict)
    """Session scalars (title / agent_name / model / status / ...)."""

    messages: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)
    """``(entry_id, message_dict)`` pairs on the active path, in conversation order."""

    branches: Dict[str, List[str]] = field(default_factory=dict)
    """``parentId -> [child entry_id, ...]``, used to enumerate branches."""

    nodes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    """``entry_id -> message entry``, including branches off the active path."""

    parents: Dict[str, Optional[str]] = field(default_factory=dict)
    """``entry_id -> parentId``。"""

    leaf_id: Optional[str] = None
    """Currently active leaf."""

    root_id: Optional[str] = None
    """Id of the header entry; new branches after a cleared session start here."""

    cache_requests: List[Dict[str, Any]] = field(default_factory=list)
    compactions: List[Dict[str, Any]] = field(default_factory=list)
    tool_changes: List[Dict[str, Any]] = field(default_factory=list)
    skipped_lines: int = 0
    """Number of corrupt lines skipped (diagnostics only)."""

    @property
    def message_entry_ids(self) -> List[str]:
        return [eid for eid, _ in self.messages]


def fold_entries(entries: List[Dict[str, Any]]) -> FoldResult:
    """Fold entry records into session state."""
    out = FoldResult()
    msg_by_id: Dict[str, Dict[str, Any]] = {}
    seen_ids: set = set()
    prev_id: Optional[str] = None

    for entry in entries:
        etype = entry.get("type")
        eid = entry.get("id")
        parent = entry.get("parentId")
        if parent is not None and parent not in seen_ids:
            # Dangling parent: the entry this one chained from never reached
            # disk (a save whose append batch was lost). Stopping the walk
            # here — the old behaviour — dropped the ENTIRE active path
            # before the gap, so an interrupted session reopened as an empty
            # conversation while its title still loaded. In an append-only
            # single-writer log the true parent always precedes the child in
            # file order, so relink positionally: the gap closes and every
            # message before it stays reachable.
            logger.warning(
                "Session journal: entry %s parents missing entry %s; "
                "relinking to previous entry %s",
                eid, parent, prev_id,
            )
            parent = prev_id
        out.parents[eid] = parent
        seen_ids.add(eid)
        prev_id = eid

        if etype == ENTRY_SESSION:
            # Header: the payload minus the entry envelope is the initial scalar set.
            out.scalars = {
                k: v for k, v in entry.items()
                if k not in ("type", "id", "parentId")
            }
            out.root_id = eid

        elif etype == ENTRY_SESSION_INFO:
            fields = entry.get("fields")
            if isinstance(fields, dict):
                out.scalars.update(fields)

        elif etype == ENTRY_USAGE:
            usage = entry.get("usage")
            if isinstance(usage, dict):
                out.cache_requests.append(usage)

        elif etype == ENTRY_COMPACTION:
            out.compactions.append({
                "id": eid,
                "summary": entry.get("summary", ""),
                "firstKeptEntryId": entry.get("firstKeptEntryId"),
                "tokensBefore": entry.get("tokensBefore", 0),
                "omitEntryIds": list(entry.get("omitEntryIds") or []),
            })

        elif etype == ENTRY_TOOL_CHANGE:
            out.tool_changes.append({
                "id": eid,
                "toolsAdded": list(entry.get("toolsAdded") or []),
                "toolsRemoved": list(entry.get("toolsRemoved") or []),
                "reason": entry.get("reason", ""),
            })

        elif etype == ENTRY_MESSAGE:
            msg = entry.get("message")
            payload = dict(msg) if isinstance(msg, dict) else {}
            msg_by_id[eid] = payload
            out.nodes[eid] = entry
            out.branches.setdefault(parent, []).append(eid)

        elif etype == ENTRY_MESSAGE_PATCH:
            target = entry.get("targetId")
            patch = entry.get("fields")
            existing = msg_by_id.get(target)
            if existing is not None and isinstance(patch, dict):
                existing.update(patch)

        elif etype == ENTRY_NAVIGATE:
            # Switch the active leaf. The target must be an existing message entry;
            # otherwise the navigate is ignored and the default branch applies (the
            # leaf becomes the navigate itself). A stale leaf must not erase the whole
            # active path, but a failed switch must not be reported as success either.
            target = entry.get("leafId")
            if target in msg_by_id:
                out.leaf_id = target
                continue
            logger.warning(
                "navigate to unknown entry %r ignored (journal has %s messages)",
                target, len(msg_by_id),
            )

        # Except for a successful navigate, every entry becomes the new active leaf.
        out.leaf_id = eid

    # Walk back from the final leaf_id to the root through the parents chain,
    # collecting message entries only. navigate is a control entry and never sits on
    # the path, but it repointed the leaf at the target message, so abandoned
    # branches drop out of the active path without extra bookkeeping.
    path: List[str] = []
    seen: set = set()
    cursor = out.leaf_id
    while cursor is not None and cursor not in seen:
        seen.add(cursor)
        if cursor in msg_by_id:
            path.append(cursor)
        cursor = out.parents.get(cursor)
    path.reverse()
    path = _apply_compaction_cuts(path, out.compactions)
    out.messages = [(eid, msg_by_id[eid]) for eid in path]
    return out


def _apply_compaction_cuts(
    path: List[str], compactions: List[Dict[str, Any]],
) -> List[str]:
    """Apply compaction markers to the active path in file order."""
    for comp in compactions:
        omit = set(comp.get("omitEntryIds") or [])
        if omit:
            path = [eid for eid in path if eid not in omit]
        first_kept = comp.get("firstKeptEntryId")
        if first_kept and first_kept in path:
            path = path[path.index(first_kept):]
    return path


class SessionJournal:
    """Append-only JSONL file of one session."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._leaf_id: Optional[str] = None
        self._loaded = False
        # Serializes append/rewrite against each other. aiofiles performs the
        # write on a thread-pool executor, so two coroutines saving the same
        # session concurrently would otherwise interleave bytes and produce
        # lines that are not valid JSON. Append-only means there is no lost
        # update to guard against -- only the interleave.
        self._write_lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def leaf_id(self) -> Optional[str]:
        """Id of the current active leaf, used as ``parentId`` when appending."""
        return self._leaf_id

    def exists(self) -> bool:
        return self._path.is_file()

    def set_leaf(self, entry_id: Optional[str]) -> None:
        """Set the active leaf explicitly (used after ``navigate_tree``)."""
        self._leaf_id = entry_id

    async def read_entries(self) -> List[Dict[str, Any]]:
        """Read every entry in order, skipping corrupt lines."""
        if not self._path.is_file():
            return []
        import aiofiles

        entries: List[Dict[str, Any]] = []
        skipped = 0
        try:
            async with aiofiles.open(self._path, "r", encoding="utf-8") as f:
                async for line in f:
                    if not line.strip():
                        continue
                    parsed = decode_entry(line)
                    if parsed is None:
                        skipped += 1
                        continue
                    entries.append(parsed)
        except OSError as e:
            logger.error("Failed to read session journal %s: %s", self._path, e)
            return []
        if skipped:
            logger.warning(
                "Session journal %s: skipped %s corrupt line(s); "
                "remaining %s entries are usable",
                self._path.name, skipped, len(entries),
            )
        return entries

    async def read_fold(self) -> FoldResult:
        """Read and fold, returning the active-path view."""
        entries = await self.read_entries()
        folded = fold_entries(entries)
        folded.skipped_lines = 0
        self._leaf_id = folded.leaf_id
        self._loaded = True
        return folded

    async def append(self, entry: Dict[str, Any]) -> None:
        """Append one entry and flush."""
        await self.append_many([entry])

    async def append_many(self, entries: List[Dict[str, Any]]) -> None:
        """Append a batch with one open and one flush."""
        if not entries:
            return
        async with self._write_lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            lines: List[str] = []
            cursor = self._leaf_id
            for entry in entries:
                # A None parentId means "attach to the current leaf"; an explicit
                # parent (a forked branch) is left untouched.
                if entry.get("parentId") is None:
                    entry["parentId"] = cursor
                lines.append(encode_entry(entry))
                cursor = entry.get("id") or cursor
            import aiofiles

            async with aiofiles.open(self._path, "a", encoding="utf-8") as f:
                await f.write("".join(line + "\n" for line in lines))
                await f.flush()
                try:
                    os.fsync(f.fileno())
                except (OSError, AttributeError, ValueError):
                    # Not every platform or file object supports fsync; flush already
                    # made the bytes visible to the process.
                    pass
            self._leaf_id = cursor
            self._loaded = True

    async def rewrite(self, entries: List[Dict[str, Any]]) -> None:
        """Atomically rewrite the whole file (tmp + fsync + os.replace)."""
        async with self._write_lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = "".join(encode_entry(e) + "\n" for e in entries)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            import aiofiles

            async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
                await f.write(payload)
                await f.flush()
                try:
                    os.fsync(f.fileno())
                except (OSError, AttributeError, ValueError):
                    pass
            os.replace(str(tmp), str(self._path))
            self._leaf_id = entries[-1].get("id") if entries else None
            self._loaded = True

    async def size_bytes(self) -> int:
        try:
            return self._path.stat().st_size if self._path.is_file() else 0
        except OSError:
            return 0


__all__ = [
    "ENTRY_COMPACTION",
    "ENTRY_MESSAGE",
    "ENTRY_MESSAGE_PATCH",
    "ENTRY_NAVIGATE",
    "ENTRY_SESSION",
    "ENTRY_SESSION_INFO",
    "ENTRY_TOOL_CHANGE",
    "ENTRY_TYPES",
    "ENTRY_USAGE",
    "FoldResult",
    "LEGACY_FORMAT_VERSION",
    "SESSION_FORMAT_VERSION",
    "SessionJournal",
    "decode_entry",
    "encode_entry",
    "fold_entries",
    "make_entry",
    "new_entry_id",
]
