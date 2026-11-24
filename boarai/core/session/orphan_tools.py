"""Repair assistant tool_calls that have no matching tool-role result.

Orphans appear after crash/restart mid-tool, failed session save, or cancel
without a result message. UI treats missing results as \"running\" unless patched.

For prompt assembly, prefer ``core.session.run.messages.normalize_messages()``
which is a pure in-memory function that handles both missing outputs and orphan
removal. The patch_orphan_tool_results() function below persists changes to the
session store — use it only when disk persistence is needed (e.g., crash recovery
during tool execution).

.. deprecated:: 1.3.0
    For prompt-time normalization, use ``normalize_messages()`` instead.
    This module remains for cases where session disk repair is needed.
"""
from __future__ import annotations

import logging
import warnings
from typing import Any, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Keep text aligned with core.session.run.messages.TOOL_CANCELLED
DEFAULT_ORPHAN_CONTENT = (
    "[Tool execution cancelled — conversation was interrupted. "
    "Please retry if needed.]"
)


def collect_tool_result_ids(messages: Sequence[Any]) -> set[str]:
    """Ids present on role=tool messages (field + metadata)."""
    seen: set[str] = set()
    for msg in messages or []:
        role = getattr(msg, "role", None) if not isinstance(msg, dict) else msg.get("role")
        if role != "tool":
            continue
        if isinstance(msg, dict):
            tid = msg.get("tool_call_id") or ""
            meta = msg.get("metadata") or {}
        else:
            tid = getattr(msg, "tool_call_id", None) or ""
            meta = getattr(msg, "metadata", None) or {}
        if tid:
            seen.add(tid)
        mid = meta.get("tool_call_id") if isinstance(meta, dict) else None
        if mid:
            seen.add(mid)
    return seen


def list_orphan_tool_call_ids(messages: Sequence[Any]) -> List[str]:
    """Assistant tool_call ids with no tool result message."""
    seen = collect_tool_result_ids(messages)
    orphans: List[str] = []
    for msg in messages or []:
        role = getattr(msg, "role", None) if not isinstance(msg, dict) else msg.get("role")
        if role != "assistant":
            continue
        tool_calls = (
            msg.get("tool_calls")
            if isinstance(msg, dict)
            else getattr(msg, "tool_calls", None)
        ) or []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            tc_id = tc.get("id") or ""
            if tc_id and tc_id not in seen and tc_id not in orphans:
                orphans.append(tc_id)
    return orphans


def patch_orphan_tool_results(
    session: Any,
    content: Optional[str] = None,
) -> List[str]:
    """Append tool-role placeholders for orphan tool_calls. Returns patched ids."""
    if session is None:
        return []
    messages = getattr(session, "messages", None)
    if not messages:
        return []

    orphans = list_orphan_tool_call_ids(messages)
    if not orphans:
        return []

    text = content if content is not None else DEFAULT_ORPHAN_CONTENT
    add = getattr(session, "add_message", None)

    for tc_id in orphans:
        if callable(add):
            msg = add(
                role="tool",
                content=text,
                metadata={"tool_call_id": tc_id, "orphan_patched": True},
            )
            # ensure field set even if add_message ignores it
            if msg is not None and hasattr(msg, "tool_call_id"):
                msg.tool_call_id = tc_id
            elif messages:
                last = messages[-1]
                if getattr(last, "role", None) == "tool":
                    last.tool_call_id = tc_id
        else:
            # plain dict session (tests / raw)
            messages.append(
                {
                    "role": "tool",
                    "content": text,
                    "tool_call_id": tc_id,
                    "metadata": {"tool_call_id": tc_id, "orphan_patched": True},
                }
            )

    logger.info(
        "Patched %s orphan tool result(s) on session %s: %s",
        len(orphans),
        getattr(session, "session_id", "?"),
        orphans,
    )
    return orphans