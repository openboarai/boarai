"""Session tree operations: fork, branch enumeration and ``navigate_tree``.

Once an append-only log links messages through ``(id, parentId)`` into a tree,
three operations that used to be impossible become plain file work:

**fork** copies out an **independent new session file** starting at a message.
The new header carries ``parentSession`` pointing back at the source journal, so
the sidebar can nest derived sessions under their origin. ``parentSession`` is
display-only lineage metadata that does not affect conversation content, which
is why the whole file can safely be rewritten.

**Branch enumeration** finds branch points: a ``parentId`` with more than one
message under it. Editing a user message and re-running creates a second branch,
and the abandoned one stays intact on disk.

**navigate_tree** switches the active leaf **inside one file**. It appends a
``navigate`` control entry, copies no message and deletes nothing; switching back
appends another one. It is not a substitute for fork: fork yields a new file with
a new session identity, navigate only changes the viewpoint on the same history.

These helpers depend on ``SessionStorage`` only and never touch the global
singleton, so the API layer, the CLI and tests call them the same way.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .journal import (
    ENTRY_MESSAGE,
    ENTRY_NAVIGATE,
    FoldResult,
    SessionJournal,
    fold_entries,
    make_entry,
)

logger = logging.getLogger(__name__)

_PREVIEW_CHARS = 120
"""Preview truncation length: enough to recognise a sentence, small enough that a
listing response never carries a whole message."""


class SessionTreeError(Exception):
    """Tree operation failed (unknown entry, missing journal, fork conflict)."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class BranchChild:
    """One candidate branch under a branch point."""

    entry_id: str
    role: str
    preview: str
    seq: int
    active: bool
    """Whether this child sits on the current active path."""


@dataclass(frozen=True, slots=True)
class BranchPoint:
    """A position where one ``parentId`` holds more than one message."""

    parent_entry_id: Optional[str]
    parent_preview: str
    children: List[BranchChild] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SessionTree:
    """Tree-shaped overview of one session journal."""

    session_id: str
    active_leaf: Optional[str]
    active_path: List[str]
    """Entry ids on the active path, in conversation order."""
    branch_points: List[BranchPoint] = field(default_factory=list)
    message_count: int = 0
    """Messages on the active path (the session length the UI shows)."""
    total_message_entries: int = 0
    """Every message line in the file, including abandoned branches."""


def _preview(payload: Any) -> str:
    """Extract recognisable preview text from a message payload."""
    if not isinstance(payload, dict):
        return ""
    content = payload.get("content")
    if isinstance(content, str) and content.strip():
        text = content.strip()
    elif payload.get("tool_calls"):
        calls = payload["tool_calls"]
        try:
            name = calls[0]["function"]["name"]
        except (KeyError, IndexError, TypeError):
            name = "tool_call"
        return f"[tool_calls: {name}]"
    else:
        return ""
    text = " ".join(text.split())
    if len(text) > _PREVIEW_CHARS:
        return text[: _PREVIEW_CHARS - 1] + "…"
    return text


def build_tree(session_id: str, folded: FoldResult) -> SessionTree:
    """Turn a fold result into the tree overview."""
    active = set(folded.message_entry_ids)
    branch_points: List[BranchPoint] = []

    for parent_id, children in folded.branches.items():
        if len(children) < 2:
            continue
        parent_node = folded.nodes.get(parent_id or "")
        parent_preview = (
            _preview(parent_node.get("message")) if parent_node else ""
        )
        kids: List[BranchChild] = []
        for child_id in children:
            node = folded.nodes.get(child_id) or {}
            payload = node.get("message") or {}
            kids.append(BranchChild(
                entry_id=child_id,
                role=str(payload.get("role", "")),
                preview=_preview(payload),
                seq=int(node.get("seq", payload.get("seq", 0)) or 0),
                active=child_id in active,
            ))
        # Stable ordering: active child first, the rest by seq, so the UI's
        # default selection is predictable.
        kids.sort(key=lambda c: (not c.active, c.seq, c.entry_id))
        branch_points.append(BranchPoint(
            parent_entry_id=parent_id,
            parent_preview=parent_preview,
            children=kids,
        ))

    # Branch points ordered by their position in the active path / file, so
    # repeated calls return children in the same order.
    order = {eid: i for i, eid in enumerate(folded.nodes)}
    branch_points.sort(
        key=lambda bp: order.get(bp.parent_entry_id or "", len(order))
    )

    return SessionTree(
        session_id=session_id,
        active_leaf=folded.leaf_id,
        active_path=list(folded.message_entry_ids),
        branch_points=branch_points,
        message_count=len(folded.messages),
        total_message_entries=len(folded.nodes),
    )


async def read_tree(storage: Any, session_id: str) -> SessionTree:
    """Read the branch structure of a session; raises ``SessionTreeError`` when the journal is missing."""
    journal = storage.journal_for(session_id)
    if not journal.exists():
        raise SessionTreeError(
            "no_journal", f"session {session_id} has no journal on disk",
        )
    folded = fold_entries(await journal.read_entries())
    return build_tree(session_id, folded)


def _path_to(folded: FoldResult, leaf_id: str) -> List[str]:
    """Walk from a leaf back to the root and return the active path ids, without changing folded state."""
    path: List[str] = []
    seen: set = set()
    cursor: Optional[str] = leaf_id
    while cursor is not None and cursor not in seen:
        seen.add(cursor)
        if cursor in folded.nodes:
            path.append(cursor)
        cursor = folded.parents.get(cursor)
    path.reverse()
    return path


async def session_context(
    storage: Any, session_id: str, leaf_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the context visible from a leaf **without** persisting a switch."""
    journal = storage.journal_for(session_id)
    if not journal.exists():
        raise SessionTreeError(
            "no_journal", f"session {session_id} has no journal on disk",
        )
    folded = fold_entries(await journal.read_entries())
    target = leaf_id or folded.leaf_id
    if target is not None and target not in folded.nodes and leaf_id:
        raise SessionTreeError(
            "unknown_entry", f"entry {leaf_id} is not a message of this session",
        )

    if leaf_id:
        ids = _path_to(folded, leaf_id)
        messages = [
            dict(folded.nodes[eid].get("message") or {}, entry_id=eid)
            for eid in ids
        ]
    else:
        messages = [
            dict(payload, entry_id=eid) for eid, payload in folded.messages
        ]

    return {
        "session_id": session_id,
        "leafId": leaf_id or folded.leaf_id,
        "is_persisted_leaf": (leaf_id or folded.leaf_id) == folded.leaf_id,
        "entryIds": [m["entry_id"] for m in messages],
        "messages": messages,
        "messageCount": len(messages),
        "totalMessageEntries": len(folded.nodes),
    }


async def navigate_tree(
    storage: Any, session_id: str, leaf_id: str,
) -> Dict[str, Any]:
    """Move the active leaf to ``leaf_id`` inside the same file."""
    journal = storage.journal_for(session_id)
    if not journal.exists():
        raise SessionTreeError(
            "no_journal", f"session {session_id} has no journal on disk",
        )
    entries = await journal.read_entries()
    folded = fold_entries(entries)
    if leaf_id not in folded.nodes:
        raise SessionTreeError(
            "unknown_entry",
            f"entry {leaf_id} is not a message of session {session_id}",
        )
    if leaf_id == folded.leaf_id:
        return await session_context(storage, session_id, None)

    await journal.append_many([
        make_entry(ENTRY_NAVIGATE, folded.leaf_id, leafId=leaf_id),
    ])
    logger.info("Session %s navigated to entry %s", session_id, leaf_id)
    return await session_context(storage, session_id, None)


async def fork_session(
    storage: Any,
    session_id: str,
    at_entry_id: Optional[str] = None,
    new_session_id: Optional[str] = None,
) -> Any:
    """Fork an independent new session from one message."""
    from .manager import Session, SessionMessage

    source = await storage.load_session(session_id)
    if source is None:
        raise SessionTreeError(
            "session_not_found", f"session {session_id} not found",
        )

    messages = list(source.messages)
    if at_entry_id is not None:
        cut = None
        for i, msg in enumerate(messages):
            if msg.entry_id == at_entry_id:
                cut = i
                break
        if cut is None:
            # Not on the active path: this may be an entry of another branch, so
            # locate it by walking the tree.
            journal = storage.journal_for(session_id)
            folded = fold_entries(await journal.read_entries())
            if at_entry_id not in folded.nodes:
                raise SessionTreeError(
                    "unknown_entry",
                    f"entry {at_entry_id} is not part of session {session_id}",
                )
            path = _path_to(folded, at_entry_id)
            by_entry = {m.entry_id: m for m in messages}
            messages = [by_entry[e] for e in path if e in by_entry]
            if not messages:
                raise SessionTreeError(
                    "entry_not_on_path",
                    f"entry {at_entry_id} is on a branch that is not loaded",
                )
        else:
            messages = messages[: cut + 1]

    target_id = new_session_id or str(uuid.uuid4())
    if await storage.session_exists(target_id):
        raise SessionTreeError(
            "target_exists", f"session {target_id} already exists",
        )

    source_journal = storage._get_session_path(session_id)
    forked = Session(
        session_id=target_id,
        title=source.title,
        agent_name=source.agent_name,
        created_at=source.created_at,
        pinned=False,
        archived=False,
        source=source.source,
        parent_session=str(source_journal),
        thinking_mode=source.thinking_mode,
        model=source.model,
        workspace_path=source.workspace_path,
        status="active",
    )
    forked.metadata = dict(source.metadata)
    forked.metadata["forked_from"] = session_id
    if at_entry_id:
        forked.metadata["forked_at_entry"] = at_entry_id
    # Security overrides are a session-level approval decision and are deliberately
    # not inherited: a new session must not silently start with the approval mode
    # the source session had relaxed. But a fork IS a new session, so it still
    # gets the configured new-session default (deny/allow) — otherwise a fork
    # would start out *looser* than that default (global fallback) whenever the
    # user chose a strict default. "ask" (the default) stamps nothing.
    from .service import stamp_default_approval_mode

    stamp_default_approval_mode(forked)

    for msg in messages:
        copy = SessionMessage(
            role=msg.role,
            content=msg.content,
            # Fresh message ids: a forked session is an independent transcript, and
            # sharing ids with the source would make the two files' incremental UI
            # sync interfere with each other.
            metadata=dict(msg.metadata),
            tool_calls=(
                [dict(tc) for tc in msg.tool_calls] if msg.tool_calls else None
            ),
            tool_call_id=msg.tool_call_id,
            thinking=msg.thinking,
        )
        forked.messages.append(copy)
    forked.message_count = len(forked.messages)
    # Renumber seq: the new session is a fresh linear history.
    for i, msg in enumerate(forked.messages):
        msg.seq = i + 1
    forked.next_seq = len(forked.messages) + 1
    # Usage and compaction records belong to the source session's run history;
    # they are not carried into the fork.
    forked.cache_requests = []
    forked.journal_compactions = []
    forked.tool_changes = []
    forked.active_leaf = None
    forked.journal_root = None
    forked.branch_parents = {}
    forked._mark_journal_written()

    if not await storage.save_session(forked):
        raise SessionTreeError(
            "save_failed", f"failed to write the forked journal for {target_id}",
        )
    logger.info(
        "Forked session %s → %s (%s messages, at=%s)",
        session_id, target_id, len(forked.messages), at_entry_id or "leaf",
    )
    return forked


def fork_journal_path(storage: Any, session_id: str) -> Path:
    """Absolute journal path of a session, written into a fork header as ``parentSession``."""
    return storage._get_session_path(session_id)


__all__ = [
    "BranchChild",
    "BranchPoint",
    "SessionTree",
    "SessionTreeError",
    "build_tree",
    "fork_journal_path",
    "fork_session",
    "navigate_tree",
    "read_tree",
    "session_context",
]
