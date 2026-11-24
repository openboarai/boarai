"""
Session manager for BoarAi system.

This module handles session creation, management, and lifecycle.
"""

import asyncio
import copy
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..event import EventBus, EventType, get_event_bus, publish_event
from .journal import (
    ENTRY_COMPACTION,
    ENTRY_MESSAGE,
    ENTRY_MESSAGE_PATCH,
    ENTRY_SESSION,
    ENTRY_SESSION_INFO,
    ENTRY_TOOL_CHANGE,
    ENTRY_USAGE,
    SESSION_FORMAT_VERSION,
    FoldResult,
    fold_entries,
    make_entry,
    new_entry_id,
)
from .storage import SessionStorage

logger = logging.getLogger(__name__)


class _Missing:
    """Sentinel: distinguishes "absent" from "present and equal to None"."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "<missing>"


_MISSING = _Missing()


def _strip_entry_ids(records: Any) -> List[Dict[str, Any]]:
    """Drop the journal-internal ``id`` from tool_change / compaction records."""
    if not isinstance(records, list):
        return []
    return [
        {k: v for k, v in r.items() if k != "id"}
        for r in records if isinstance(r, dict)
    ]


@dataclass
class SessionMessage:
    """Message in a session."""
    role: str  # "user", "assistant", "system", "tool"
    content: str
    message_id: str = ""  # UUID, auto-generated if empty
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    tool_calls: Optional[List[Dict[str, Any]]] = None
    """Tool calls list (OpenAI standard format). Only for role="assistant"."""
    tool_call_id: Optional[str] = None
    """Tool call ID this message is responding to. Only for role="tool"."""
    thinking: Optional[str] = None
    """Model's internal reasoning/thinking content (not visible text).
    
    Extracted from reasoner model's reasoning_content field or
    <think> tags in the response. Persisted to allow re-display
    when re-loading a session.
    """
    seq: int = 0
    """Monotonic per-session sequence number (assigned by Session.add_message).

    Stable ordering key that survives save/load. Incremental clients can use it
    as a watermark; the WebUI merges disk snapshots with in-flight optimistic
    bubbles by message_id (seq is the durable order/version fallback).
    """
    entry_id: str = ""
    """This message's id in the append-only JSONL journal.

    Empty until the message is persisted. It is the tree-node identity: the
    next entry's parentId points at it, and fork / navigate_tree address
    messages by it. Distinct from message_id, which is the wire/UI identity
    clients already merge on.
    """
    parent_entry_id: Optional[str] = None
    """Journal entry id of the preceding entry (message or control entry).

    None only for the very first message, whose parent is the file header.
    """
    _journal_fp: str = field(default="", repr=False, compare=False)
    """Fingerprint of the last persisted serialization (never written to disk).

    Append-only storage cannot rewrite a line, so a message mutated after it
    was persisted (usage metadata, a run-error stamp) is re-published as a
    message_patch entry. The fingerprint is how save_session notices;
    without it every save would either re-append duplicates or silently drop
    post-hoc mutations.
    """

    def __post_init__(self) -> None:
        """Auto-generate message_id if empty."""
        if not self.message_id:
            self.message_id = str(uuid.uuid4())
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "role": self.role,
            "content": self.content,
            "message_id": self.message_id,
            "seq": self.seq,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }
        if self.tool_calls:
            result["tool_calls"] = self.tool_calls
        # Always save tool_call_id for tool role messages
        # (API requires this field; don't skip even if empty)
        if self.tool_call_id or self.role == "tool":
            result["tool_call_id"] = self.tool_call_id or ""
        if self.thinking:
            result["thinking"] = self.thinking
        # Journal tree position. Clients need these to address a message in
        # fork / navigate_tree; they are absent for an unpublished message.
        if self.entry_id:
            result["entry_id"] = self.entry_id
        if self.parent_entry_id:
            result["parent_entry_id"] = self.parent_entry_id
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionMessage":
        """Create from dictionary."""
        return cls(
            role=data["role"],
            content=data["content"],
            message_id=data.get("message_id", ""),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            metadata=data.get("metadata", {}),
            tool_calls=data.get("tool_calls"),
            tool_call_id=data.get("tool_call_id"),
            thinking=data.get("thinking"),
            seq=data.get("seq", 0),
            entry_id=data.get("entry_id", ""),
            parent_entry_id=data.get("parent_entry_id"),
        )


@dataclass
class Session:
    """Session data structure."""
    
    # Known default/placeholder titles that trigger auto-naming
    # Include short UI labels (the create button may read "New" in any locale)
    _DEFAULT_TITLES = frozenset({
        "New Session", "new session", "New session",
        "New", "new",
        "\u65b0\u5efa\u4f1a\u8bdd", "\u65b0\u5efa", "\u65b0\u4f1a\u8bdd",
        "Untitled", "untitled", "\u672a\u547d\u540d",
    })
    
    session_id: str
    title: str = "New Session"
    agent_name: str = "default"
    """Agent type used for this session (default/coder/writer/planner)."""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    messages: List[SessionMessage] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    pinned: bool = False
    archived: bool = False
    source: str = "system"
    """Source identifier of this session.

    Set by the channel that created the session (e.g., 'webui', 'cli', 'api').
    Channels should register their source via SourceRegistry during startup.
    Defaults to 'system' when no specific channel is associated.
    """
    message_count: int = 0
    compressed_summary: Optional[str] = None
    """Compressed conversation summary (set by InsertThenCompress).
    
    When set, Zone 3a will use this summary instead of raw history.
    This keeps Zone 3 small and bounded, maintaining cache hit rate.
    """
    cache_requests: List[Dict[str, Any]] = field(default_factory=list)
    """History of LLM request cache metrics.
    
    Each entry: {"ts": str, "model": str, "input_tokens": int,
                  "cache_read_tokens": int, "cache_creation_tokens": int}
    Automatically persisted via to_dict/from_dict.
    """
    compression_count: int = 0
    """Number of times InsertThenCompress has been applied to this session."""
    security_overrides: Dict[str, Any] = field(default_factory=dict)
    """Per-session security overrides.

    Only the keys ``ToolGuard.evaluate_tool`` actually reads are honoured:

        {"approval_mode": "deny" | "on" | "off",
         "forbidden_tools": ["<tool_name>"]}

    ``approval_mode`` replaces the global mode AND the agent
    allow/ask/can_auto_approve shortcuts for this session; ``forbidden_tools``
    is a hard block on top of the global list. Every other key is ignored —
    this is deliberately not a full ``SecurityConfig`` mirror, because a
    session must never be able to loosen a global floor (paths, shell
    patterns, network) by writing a dict.
    """
    thinking_mode: str = "none"
    """Session thinking mode UI label: none|off|light|deep|ultra.

    ``none`` is the persisted spelling of "auto": no thinking field is sent and
    the model default applies. ``off`` explicitly disables thinking (clamped to
    the model's lowest level when the model cannot disable it). light/deep/ultra
    map to low/medium/xhigh and are clamped per model on the wire.
    """
    model: str = ""
    """Model used for this session (e.g. "mimo/mimo-v2.5").

    Persisted so the UI can restore the same model when re-opening a session.
    Updated automatically when a new run is created with a different model.
    """
    workspace_path: str = ""
    """Absolute working directory for tools/prompt for this session.

    Empty string means fall back to project_root (BoarAi install root).
    Cross-platform absolute path (Windows drive/UNC or POSIX).
    """
    status: str = "active"
    """Session health status for the UI.

    - "active": normal (default, also reset on successful run / new run)
    - "error":  the last run failed (e.g. malformed messages, API errors);
                the WebUI highlights such sessions with a light red background.
    """
    parent_session: str = ""
    """Absolute path of the journal this session was forked from.

    Display/lineage metadata only: it has no effect on conversation content.
    The sidebar uses it to nest a fork under its origin.
    """
    tool_changes: List[Dict[str, Any]] = field(default_factory=list)
    """Tool-set change log, each ``{toolsAdded, toolsRemoved, reason}``.

    Persisted as ``tool_change`` entries so a replay can reconstruct exactly
    which tools the model could see at a given turn.
    """
    journal_compactions: List[Dict[str, Any]] = field(default_factory=list)
    """Compaction log, each ``{summary, firstKeptEntryId, omitEntryIds}``.

    ``firstKeptEntryId`` marks a contiguous prefix that left the model
    context; ``omitEntryIds`` lists entries dropped non-contiguously (that is
    the shape ``trim_history`` produces: system prefix + recent N turns).
    The original message lines are never removed from the file.
    """
    active_leaf: Optional[str] = field(default=None, repr=False)
    """Entry id of the active branch leaf (the parentId for the next append)."""
    branch_parents: Dict[str, List[str]] = field(default_factory=dict, repr=False)
    """``parentId -> [child entry id]``; more than one child means a branch."""
    journal_root: Optional[str] = field(default=None, repr=False)
    """Entry id of the journal header (a fresh branch starts here)."""
    _journal_needs_rewrite: bool = field(default=False, repr=False)
    """Set when a mutation cannot be expressed by appending.

    See :meth:`mark_journal_dirty`.
    """
    _journal_scalars_written: Dict[str, Any] = field(default_factory=dict, repr=False)
    _journal_usage_written: int = field(default=0, repr=False)
    _journal_tools_written: int = field(default=0, repr=False)
    _journal_compactions_written: int = field(default=0, repr=False)
    next_seq: int = field(default=1, init=False, repr=False)
    """Next monotonic sequence number to hand out (not serialized; rebuilt on
    load from the highest message seq)."""
    
    def _alloc_seq(self) -> int:
        """Allocate the next per-session sequence number (monotonic)."""
        seq = self.next_seq
        self.next_seq += 1
        return seq
    
    def add_message(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> SessionMessage:
        """Add a message to the session."""
        message = SessionMessage(
            role=role,
            content=content,
            metadata=metadata or {},
            seq=self._alloc_seq(),
            parent_entry_id=self.active_leaf,
        )
        self.messages.append(message)
        self.message_count = len(self.messages)
        self.updated_at = datetime.now()
        return message

    def insert_capability_message(
        self, role: str, content: str, index: int
    ) -> SessionMessage:
        """Insert a runtime capability message at a fixed position (seq 0)."""
        message = SessionMessage(role=role, content=content)
        self.messages.insert(index, message)
        return message

    def session_capability_index(self) -> Optional[int]:
        """Index of the session-opening context inject, or None."""
        for i, m in enumerate(self.messages):
            if self.is_system_inject(m) and str(m.content).startswith(
                f"{self._SYSTEM_INJECT_PREFIX} Session:"
            ):
                return i
        return None

    def ensure_session_capability_inject(self) -> bool:
        """Write the session-opening context inject as message #0."""
        if self.messages:
            return False
        from boarai.core.prompt.assembler import PromptAssembler
        from boarai.core.session.workspace import resolve_session_workspace

        try:
            workspace = resolve_session_workspace(self)
        except Exception:  # noqa: BLE001 - inject must never block a turn
            workspace = None
        ctx_msg = PromptAssembler._build_session_capability(
            session_id=self.session_id,
            workspace=workspace,
        )
        self.insert_capability_message(ctx_msg["role"], ctx_msg["content"], 0)
        return True

    def _normalize_capability_inject_position(self) -> None:
        """Restore the design order: the session-opening inject is message #0."""
        ctx_idx = self.session_capability_index()
        if ctx_idx is not None and ctx_idx > 0:
            inject = self.messages.pop(ctx_idx)
            self.messages.insert(0, inject)
    
    def get_messages(self, limit: Optional[int] = None) -> List[SessionMessage]:
        """Get messages from the session."""
        if limit:
            return self.messages[-limit:]
        return self.messages.copy()
    
    def get_raw_turns(self, n: int = 10) -> List[SessionMessage]:
        """Get the last N user+assistant turns (not counting system or archived msgs)."""
        recent = []
        turn_count = 0
        for msg in reversed(self.messages):
            if msg.role in ("user", "assistant"):
                # Skip archived_background — they are compression artifacts, not real turns
                if msg.metadata and msg.metadata.get("type") == "archived_background":
                    continue
                turn_count += 1
            recent.insert(0, msg)
            if turn_count >= n * 2:
                break
        return recent

    _SYSTEM_INJECT_PREFIX = "[BoarAi_System]"
    """Prefix marking persistent runtime-injected user messages.

    These are NOT human turns — they carry session context, compressed
    summaries, and incremental task-state deltas. They must survive
    trim_history so the compressed prefix stays stable.
    """

    def is_system_inject(self, msg: "SessionMessage") -> bool:
        """Whether a user message is a runtime system inject (not a real turn)."""
        return (
            msg.role == "user"
            and str(msg.content).startswith(self._SYSTEM_INJECT_PREFIX)
        )

    def trim_history(
        self,
        keep_turns: int,
    ) -> None:
        """Trim old conversation history."""
        previous = list(self.messages)
        # Permanent prefix: system messages + [BoarAi_System] user injects.
        prefix = [
            m for m in self.messages
            if m.role == "system" or self.is_system_inject(m)
        ]
        prefix_ids = {id(m) for m in prefix}

        # Recent raw turns (skips archived_background + already-kept prefix).
        recent = [
            m for m in self.get_raw_turns(keep_turns)
            if id(m) not in prefix_ids
        ]

        # Entries dropped here stay on disk; the compaction record is what
        # takes them off the active path when the journal is folded again.
        # Computed from the SURVIVING list: `self.messages` is still the full
        # pre-trim list at this point, so reading it here would make kept_ids
        # cover everything and the omit set come out empty.
        surviving = prefix + recent
        kept_ids = {m.entry_id for m in surviving if m.entry_id}
        omitted = [
            m.entry_id for m in previous
            if m.entry_id and m.entry_id not in kept_ids
        ]
        first_kept = next(
            (m for m in surviving if m.entry_id and m.entry_id not in omitted),
            None,
        )
        self.record_compaction(
            summary=self.compressed_summary or "",
            first_kept_message=first_kept,
            omit_entry_ids=omitted,
        )

        self.messages = prefix + recent
        self.message_count = len(self.messages)
        self.compression_count += 1
        self.updated_at = datetime.now()

    def find_last_goal_inject(self) -> Optional["SessionMessage"]:
        """Return the last task-goal inject message, or None."""
        for m in reversed(self.messages):
            if self.is_system_inject(m) and str(m.content).startswith(
                f"{self._SYSTEM_INJECT_PREFIX} Goal:"
            ):
                return m
        return None

    def insert_or_replace_summary(self, summary_text: str) -> None:
        """Insert the compressed summary as a user message right after the session-context inject; replace any existing summary inject."""
        # Drop any previous summary inject.
        self.messages = [
            m for m in self.messages
            if not (
                self.is_system_inject(m)
                and str(m.content).startswith(
                    f"{self._SYSTEM_INJECT_PREFIX} Compressed History Summary"
                )
            )
        ]

        # Find the session-context inject (first [BoarAi_System] Session: msg).
        ctx_idx = self.session_capability_index()

        summary_msg = SessionMessage(
            role="user",
            content=summary_text,
            metadata={"type": "compressed_summary"},
            seq=self._alloc_seq(),
        )
        insert_at = (ctx_idx + 1) if ctx_idx is not None else 0
        self.messages.insert(insert_at, summary_msg)
        self.message_count = len(self.messages)
        self.updated_at = datetime.now()
        self.mark_journal_dirty()

    def replace_history(
        self,
        summary: str,
        recent_messages: List[SessionMessage],
    ) -> None:
        """Deprecated: use trim_history() instead."""
        self.compressed_summary = summary
        self.trim_history(10)
    
    def clear_messages(self) -> None:
        """Clear all messages and compression state."""
        self.messages.clear()
        self.message_count = 0
        self.compressed_summary = None
        self.updated_at = datetime.now()
        # Append-only: nothing on disk is deleted. Moving the active leaf back
        # to the header makes the folded active path empty, and later messages
        # start a fresh branch from there.
        self.active_leaf = self.journal_root

    def truncate_messages(self, after_message_id: str) -> Optional[SessionMessage]:
        """Keep messages up to and including the given message; drop the tail."""
        for i, msg in enumerate(self.messages):
            if msg.message_id == after_message_id:
                del self.messages[i + 1:]
                self.message_count = len(self.messages)
                self.compressed_summary = None
                self.updated_at = datetime.now()
                # Append-only: the tail is not deleted. Moving the active leaf
                # to the kept boundary makes the next append start a new
                # branch there; the abandoned tail simply stops being on the
                # active path, and navigate_tree can still switch back to it.
                if msg.entry_id:
                    self.active_leaf = msg.entry_id
                return msg
        return None

    def add_cache_request(
        self, usage: Dict[str, int], model: str,
        token_estimates: Optional[Dict[str, int]] = None,
        run_id: Optional[str] = None,
    ) -> None:
        """Record a cache metric entry after a run's LLM calls."""
        # Run-level totals fall back to last-turn values for single-turn runs
        # and legacy callers that only pass per-turn keys.
        run_input = usage.get("run_total_input_tokens") or usage.get("prompt_tokens", 0)
        run_completion = usage.get("run_total_completion_tokens") or usage.get(
            "completion_tokens", 0
        )
        run_cache_read = usage.get("run_total_cache_read_tokens")
        if run_cache_read is None:
            run_cache_read = usage.get("cache_read_input_tokens", 0)
        run_cache_creation = usage.get("run_total_cache_creation_tokens")
        if run_cache_creation is None:
            run_cache_creation = usage.get("cache_creation_input_tokens", 0)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": model or "unknown",
            "input_tokens": run_input,
            "completion_tokens": run_completion,
            "cache_read_tokens": run_cache_read,
            "cache_creation_tokens": run_cache_creation,
            "cache_miss_tokens": usage.get("cache_miss_tokens", 0),
            # Last-turn prompt size = current context footprint.
            "context_tokens": usage.get("prompt_tokens", 0),
            "token_estimates": token_estimates or {},
        }
        # Optional run-level metadata (turn count; totals already folded above)
        if usage.get("run_turns"):
            entry["run_turns"] = usage["run_turns"]
        if run_id:
            entry["run_id"] = run_id
        self.cache_requests.append(entry)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with cache metrics."""
        result = {
            "session_id": self.session_id,
            "title": self.title,
            "agent_name": self.agent_name,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "messages": [msg.to_dict() for msg in self.messages],
            "metadata": self.metadata,
            "pinned": self.pinned,
            "archived": self.archived,
            "source": self.source,
            "message_count": self.message_count,
        }
        if self.compressed_summary:
            result["compressed_summary"] = self.compressed_summary
        if self.cache_requests:
            result["cache_requests"] = self.cache_requests
            # Expose context_usage: last turn's prompt size (context footprint).
            # Legacy entries lack context_tokens → fall back to input_tokens
            # (which was the last-turn prompt before the field split).
            last_req = self.cache_requests[-1]
            result["context_usage"] = (
                last_req.get("context_tokens") or last_req.get("input_tokens", 0)
            )
            if last_req.get("token_estimates"):
                result["token_estimates"] = last_req["token_estimates"]
        if self.compression_count:
            result["compression_count"] = self.compression_count
        if self.security_overrides:
            result["security_overrides"] = self.security_overrides
        # Journal-derived state, carried here too so the legacy single-JSON
        # path stays lossless and a rollback to v3 does not drop it.
        if self.tool_changes:
            result["tool_changes"] = self.tool_changes
        if self.journal_compactions:
            result["journal_compactions"] = self.journal_compactions
        if self.active_leaf:
            result["active_leaf"] = self.active_leaf
        if self.parent_session:
            result["parent_session"] = self.parent_session
        # Always expose thinking_mode so UI can restore pills (including "none")
        result["thinking_mode"] = self.thinking_mode or "none"
        if self.model:
            result["model"] = self.model
        if self.workspace_path:
            result["workspace_path"] = self.workspace_path
        if self.status and self.status != "active":
            result["status"] = self.status
        return result
    
    def to_summary_dict(self) -> Dict[str, Any]:
        """Convert to summary dictionary (without messages)."""
        result = {
            "session_id": self.session_id,
            "title": self.title,
            "agent_name": self.agent_name,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": self.metadata,
            "pinned": self.pinned,
            "archived": self.archived,
            "source": self.source,
            "message_count": self.message_count,
            "preview": self.messages[-1].content[:100] if self.messages else None,
            # Always include so session list / UI restore works for "none"
            "thinking_mode": self.thinking_mode or "none",
        }
        if self.model:
            result["model"] = self.model
        if self.workspace_path:
            result["workspace_path"] = self.workspace_path
        if self.status and self.status != "active":
            result["status"] = self.status
        # Per-session security overrides (WebUI toolbar approval-mode pill
        # restores from here — WS session.get merges this summary).
        if self.security_overrides:
            result["security_overrides"] = self.security_overrides
        # Usage aggregates for the WebUI toolbar (WS session.get returns this
        # summary, so token/context stats must live here — the full
        # cache_requests array stays in to_dict only, to keep list payloads
        # small).
        if self.cache_requests:
            last = self.cache_requests[-1]
            result["context_usage"] = (
                last.get("context_tokens") or last.get("input_tokens", 0)
            )
            if last.get("token_estimates"):
                result["token_estimates"] = last["token_estimates"]
            result["usage_totals"] = {
                "input": sum(r.get("input_tokens", 0) for r in self.cache_requests),
                "output": sum(
                    r.get("completion_tokens", 0) for r in self.cache_requests
                ),
                "cache_read": sum(
                    r.get("cache_read_tokens", 0) for r in self.cache_requests
                ),
                "cache_creation": sum(
                    r.get("cache_creation_tokens", 0)
                    for r in self.cache_requests
                ),
                "model": last.get("model", "") or "",
                # LLM API call count: each cache_request records one run and its
                # run_turns holds the calls made inside that run; records without
                # the field predate it and fall back to one call per run.
                "api_calls": sum(
                    int(r.get("run_turns") or 1) for r in self.cache_requests
                ),
            }
        if self.compression_count:
            result["compression_count"] = self.compression_count
        return result
    
    @property
    def watermark_seq(self) -> int:
        """Max per-session seq across all messages (watermark for incremental sync)."""
        return max((m.seq for m in self.messages), default=0)

    @property
    def is_default_title(self) -> bool:
        """Check if session still has a default/placeholder title."""
        return self.title.strip() in self._DEFAULT_TITLES or not self.title.strip()

    def user_message_count(self) -> int:
        """Count real user turns (system injects and tool/assistant excluded)."""
        return sum(
            1 for m in self.messages
            if m.role == "user" and not self.is_system_inject(m)
        )

    def maybe_auto_title_from_user(self, content: str) -> bool:
        """If still default title and this is the first user message, set title."""
        if not self.is_default_title:
            return False
        if self.user_message_count() != 1:
            return False
        text = self._user_text_for_title(content)
        if not text:
            return False
        new_title = self.generate_title(text)
        if not new_title or new_title.strip() in self._DEFAULT_TITLES:
            return False
        self.title = new_title
        return True

    @staticmethod
    def _user_text_for_title(content: str) -> str:
        """Strip injected system lines from content used for auto-title."""
        if not content:
            return ""
        lines = []
        for line in str(content).splitlines():
            s = line.strip()
            if not s:
                if lines:
                    lines.append("")
                continue
            if s.startswith("[BoarAi_System]"):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    @staticmethod
    def generate_title(content: str, max_len: int = 40) -> str:
        """Generate a human-readable title from the first user message."""
        # 1. Take the first non-empty line (after system-inject strip by caller)
        first_line = (content.strip() or content).splitlines()[0].strip()
        
        # 2. Strip common URL patterns (http://, https://, ftp://)
        text = re.sub(r'https?://\S+|ftp://\S+', '', first_line).strip()
        
        # 3. If nothing left after stripping URLs, use the original first line
        if not text:
            text = first_line
        
        # 4. Try to take the first sentence (before . ! ? ： 。！？)
        sentence_end = re.search(r'[.!?：。！？\n]', text)
        if sentence_end:
            text = text[:sentence_end.start() + 1].strip()
        
        # 5. Truncate and clean
        if len(text) > max_len:
            text = text[:max_len - 1].rstrip() + "…"
        
        return text
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        """Create from dictionary (backward-compatible)."""
        session = cls(
            session_id=data["session_id"],
            title=data.get("title", "New Session"),
            agent_name=data.get("agent_name", "default"),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            metadata=data.get("metadata", {}),
            pinned=data.get("pinned", False),
            archived=data.get("archived", False),
            source=data.get("source", "system"),
            message_count=data.get("message_count", 0),
            parent_session=str(data.get("parent_session", "") or ""),
        )

        # Load cache metrics (backward-compatible: missing = empty)
        session.compressed_summary = data.get("compressed_summary")
        session.cache_requests = data.get("cache_requests", [])
        session.compression_count = data.get("compression_count", 0)
        session.thinking_mode = data.get("thinking_mode", "none")
        session.model = data.get("model", "")
        session.workspace_path = data.get("workspace_path", "") or ""
        session.status = data.get("status", "active")
        # Per-session security overrides were persisted but never restored —
        # they silently reset to defaults on every restart/reload.
        # Type guard: a tampered/corrupt session JSON may store a non-dict
        # here; dict(<str>/<int>) would raise and break session loading.
        _so = data.get("security_overrides")
        session.security_overrides = dict(_so) if isinstance(_so, dict) else {}
        session.tool_changes = _strip_entry_ids(data.get("tool_changes"))
        session.journal_compactions = _strip_entry_ids(
            data.get("journal_compactions")
        )
        session.active_leaf = data.get("active_leaf")
        session.parent_session = str(data.get("parent_session", "") or "")

        for msg_data in data.get("messages", []):
            session.messages.append(SessionMessage.from_dict(msg_data))

        # Legacy journals recorded the session-opening inject AFTER the first
        # user message (it used to be persisted at turn-0 preflight, and an
        # append-only journal can only tail-append). Restore the design order
        # in memory: the inject is message #0.
        session._normalize_capability_inject_position()

        # Rebuild the sequence counter and backfill legacy messages that predate
        # `seq` (seq == 0). seq is the pagination cursor (before_seq/since_seq);
        # a 0 breaks backward paging on old sessions, so re-number the whole list
        # in array order whenever any message lacks a seq. message_id remains the
        # stable identity — seq is order-only and idempotently rebuilt on load.
        if any(m.seq == 0 for m in session.messages):
            for i, m in enumerate(session.messages):
                m.seq = i + 1
        max_seq = max((m.seq for m in session.messages), default=0)
        session.next_seq = max(max_seq, len(session.messages)) + 1

        return session


    # ==================== Append-only journal mapping ====================

    _JOURNAL_SCALARS = (
        "title", "agent_name", "pinned", "archived", "source",
        "parent_session", "thinking_mode", "model", "workspace_path", "status",
        "compressed_summary", "compression_count",
    )
    """Session scalars that travel as incremental ``session_info`` entries.

    ``session_id`` and ``created_at`` are immutable and appear only in the file
    header. ``metadata`` and ``security_overrides`` are dicts and also go
    through incremental updates. ``cache_requests`` is not here: it is
    append-only, one ``usage`` entry per record.
    """

    _JOURNAL_VOLATILE = frozenset({"message_count"})
    """Scalars excluded from the change diff.

    ``message_count`` is recomputed from the active path on fold, so writing it
    would emit a ``session_info`` line on every single message append while
    carrying no information the reader does not already have.
    """

    def journal_header(self) -> Dict[str, Any]:
        """File-header payload: immutable identity plus initial scalars."""
        header: Dict[str, Any] = {
            "version": SESSION_FORMAT_VERSION,
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
        }
        header.update(self.journal_scalars())
        return header

    def journal_scalars(self) -> Dict[str, Any]:
        """Current scalar snapshot (diffable against the last persisted one)."""
        out: Dict[str, Any] = {}
        for name in self._JOURNAL_SCALARS:
            out[name] = getattr(self, name)
        out["updated_at"] = self.updated_at.isoformat()
        out["message_count"] = len(self.messages)
        out["metadata"] = copy.deepcopy(self.metadata)
        out["security_overrides"] = copy.deepcopy(self.security_overrides)
        return out

    def _journal_diff(self) -> Dict[str, Any]:
        """Scalars that changed since the last persisted snapshot."""
        current = self.journal_scalars()
        changed = {
            k: v for k, v in current.items()
            if k not in self._JOURNAL_VOLATILE
            and self._journal_scalars_written.get(k, _MISSING) != v
        }
        self._journal_scalars_written = current
        return changed

    @staticmethod
    def _message_fingerprint(msg: "SessionMessage") -> str:
        """Stable fingerprint of a message's content, excluding tree position."""
        import hashlib
        import json as _json

        payload = msg.to_dict()
        payload.pop("entry_id", None)
        payload.pop("parent_entry_id", None)
        blob = _json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]

    def _mark_journal_written(self) -> None:
        """Record that everything currently in memory is on disk."""
        self._journal_scalars_written = self.journal_scalars()
        self._journal_usage_written = len(self.cache_requests)
        self._journal_tools_written = len(self.tool_changes)
        self._journal_compactions_written = len(self.journal_compactions)

    def mark_journal_dirty(self) -> None:
        """Flag this session as needing a full journal rewrite."""
        self._journal_needs_rewrite = True

    def to_entries(self) -> List[Dict[str, Any]]:
        """Expand the whole session into a linear entry sequence."""
        entries: List[Dict[str, Any]] = [
            make_entry(ENTRY_SESSION, None, **self.journal_header()),
        ]
        self.journal_root = entries[0]["id"]
        parent = self.journal_root

        for msg in self.messages:
            eid = msg.entry_id or new_entry_id()
            entries.append(make_entry(
                ENTRY_MESSAGE, parent, entry_id=eid,
                seq=msg.seq, message=msg.to_dict(),
            ))
            msg.entry_id = eid
            msg.parent_entry_id = parent
            msg._journal_fp = self._message_fingerprint(msg)
            parent = eid

        for req in self.cache_requests:
            entries.append(make_entry(ENTRY_USAGE, parent, usage=req))
            parent = entries[-1]["id"]

        for comp in self.journal_compactions:
            entries.append(make_entry(
                ENTRY_COMPACTION, parent,
                entry_id=comp.get("id"),
                summary=comp.get("summary", ""),
                firstKeptEntryId=comp.get("firstKeptEntryId"),
                tokensBefore=comp.get("tokensBefore", 0),
                omitEntryIds=comp.get("omitEntryIds", []),
            ))
            parent = entries[-1]["id"]

        for tc in self.tool_changes:
            entries.append(make_entry(
                ENTRY_TOOL_CHANGE, parent,
                entry_id=tc.get("id"),
                toolsAdded=tc.get("toolsAdded", []),
                toolsRemoved=tc.get("toolsRemoved", []),
                reason=tc.get("reason", ""),
            ))
            parent = entries[-1]["id"]

        self._mark_journal_written()
        # journal.rewrite leaves the leaf at the last entry written. A later
        # truncate_messages / clear_messages moves it elsewhere, so overwrite
        # explicitly: the leaf read back on fold must equal the leaf written,
        # or the next append would attach to the wrong branch.
        self.active_leaf = parent
        return entries

    def pending_journal_entries(self) -> List[Dict[str, Any]]:
        """Produce only the entries not yet on disk (the incremental save)."""
        entries: List[Dict[str, Any]] = []
        parent = self.active_leaf

        # 1. Corrections to messages already on disk.
        for msg in self.messages:
            if not msg.entry_id:
                continue
            fp = self._message_fingerprint(msg)
            if fp == msg._journal_fp:
                continue
            entries.append(make_entry(
                ENTRY_MESSAGE_PATCH, parent,
                targetId=msg.entry_id, fields=msg.to_dict(),
            ))
            msg._journal_fp = fp
            parent = entries[-1]["id"]

        # 2. New messages.
        for msg in self.messages:
            if msg.entry_id:
                continue
            eid = new_entry_id()
            entries.append(make_entry(
                ENTRY_MESSAGE, parent, entry_id=eid,
                seq=msg.seq, message=msg.to_dict(),
            ))
            msg.entry_id = eid
            msg.parent_entry_id = parent
            msg._journal_fp = self._message_fingerprint(msg)
            parent = eid

        # 3. New usage records.
        for req in self.cache_requests[self._journal_usage_written:]:
            entries.append(make_entry(ENTRY_USAGE, parent, usage=req))
            parent = entries[-1]["id"]
        self._journal_usage_written = len(self.cache_requests)

        # 4. New compaction records.
        for comp in self.journal_compactions[self._journal_compactions_written:]:
            entries.append(make_entry(
                ENTRY_COMPACTION, parent,
                entry_id=comp.get("id"),
                summary=comp.get("summary", ""),
                firstKeptEntryId=comp.get("firstKeptEntryId"),
                tokensBefore=comp.get("tokensBefore", 0),
                omitEntryIds=comp.get("omitEntryIds", []),
            ))
            parent = entries[-1]["id"]
        self._journal_compactions_written = len(self.journal_compactions)

        # 5. New tool-set changes.
        for tc in self.tool_changes[self._journal_tools_written:]:
            entries.append(make_entry(
                ENTRY_TOOL_CHANGE, parent,
                entry_id=tc.get("id"),
                toolsAdded=tc.get("toolsAdded", []),
                toolsRemoved=tc.get("toolsRemoved", []),
                reason=tc.get("reason", ""),
            ))
            parent = entries[-1]["id"]
        self._journal_tools_written = len(self.tool_changes)

        # 6. Scalar delta -- only when something actually changed, so an
        #    unchanged save appends nothing at all.
        changed = self._journal_diff()
        if changed:
            entries.append(make_entry(ENTRY_SESSION_INFO, parent, fields=changed))
            parent = entries[-1]["id"]

        if entries:
            self.active_leaf = parent
        return entries

    def record_compaction(
        self,
        summary: str,
        first_kept_message: Optional["SessionMessage"] = None,
        tokens_before: int = 0,
        omit_entry_ids: Optional[List[str]] = None,
    ) -> None:
        """Log a context compaction; it lands as a ``compaction`` entry on save."""
        self.journal_compactions.append({
            "summary": summary,
            "firstKeptEntryId": (
                first_kept_message.entry_id if first_kept_message else None
            ),
            "tokensBefore": tokens_before,
            "omitEntryIds": list(omit_entry_ids or []),
        })

    def record_tool_change(
        self,
        tools_added: Optional[List[str]] = None,
        tools_removed: Optional[List[str]] = None,
        reason: str = "",
    ) -> bool:
        """Log a tool-set change; it lands as a ``tool_change`` entry on save."""
        added = list(tools_added or [])
        removed = list(tools_removed or [])
        if not added and not removed:
            return False
        self.tool_changes.append({
            "toolsAdded": added,
            "toolsRemoved": removed,
            "reason": reason,
        })
        return True

    @classmethod
    def from_entries(cls, entries: List[Dict[str, Any]]) -> "Session":
        """Rebuild a session from journal entries (the active-path view)."""
        folded = fold_entries(entries)
        scalars = folded.scalars

        created_raw = scalars.get("created_at")
        try:
            created_at = (
                datetime.fromisoformat(created_raw) if created_raw
                else datetime.now()
            )
        except (ValueError, TypeError):
            created_at = datetime.now()
        updated_raw = scalars.get("updated_at")
        try:
            updated_at = (
                datetime.fromisoformat(updated_raw) if updated_raw else created_at
            )
        except (ValueError, TypeError):
            updated_at = created_at

        session = cls(
            session_id=str(scalars.get("session_id", "") or ""),
            title=scalars.get("title", "New Session"),
            agent_name=scalars.get("agent_name", "default"),
            created_at=created_at,
            updated_at=updated_at,
            pinned=bool(scalars.get("pinned", False)),
            archived=bool(scalars.get("archived", False)),
            source=scalars.get("source", "system") or "system",
            parent_session=str(scalars.get("parent_session", "") or ""),
        )
        meta = scalars.get("metadata")
        session.metadata = (
            copy.deepcopy(meta) if isinstance(meta, dict) else {}
        )
        overrides = scalars.get("security_overrides")
        session.security_overrides = (
            copy.deepcopy(overrides) if isinstance(overrides, dict) else {}
        )
        session.compressed_summary = scalars.get("compressed_summary")
        session.compression_count = int(scalars.get("compression_count", 0) or 0)
        session.thinking_mode = scalars.get("thinking_mode", "none") or "none"
        session.model = scalars.get("model", "") or ""
        session.workspace_path = scalars.get("workspace_path", "") or ""
        session.status = scalars.get("status", "active") or "active"
        session.cache_requests = list(folded.cache_requests)
        session.tool_changes = _strip_entry_ids(folded.tool_changes)
        session.journal_compactions = _strip_entry_ids(folded.compactions)
        session.active_leaf = folded.leaf_id
        session.journal_root = folded.root_id

        for entry_id, msg_dict in folded.messages:
            msg = SessionMessage.from_dict(msg_dict)
            msg.entry_id = entry_id
            node = folded.nodes.get(entry_id) or {}
            msg.parent_entry_id = node.get("parentId")
            msg._journal_fp = cls._message_fingerprint(msg)
            session.messages.append(msg)

        # Branches off the active path stay addressable for navigate_tree.
        session.branch_parents = {
            k: list(v) for k, v in folded.branches.items() if k is not None
        }

        session.message_count = len(session.messages)
        session._normalize_capability_inject_position()
        # Same seq rule as from_dict: if any message lacks a seq, renumber the
        # active path in order, because seq is the pagination cursor and a 0
        # breaks backward paging on older sessions.
        if any(m.seq == 0 for m in session.messages):
            for i, m in enumerate(session.messages):
                m.seq = i + 1
        max_seq = max((m.seq for m in session.messages), default=0)
        session.next_seq = max(max_seq, len(session.messages)) + 1

        session._mark_journal_written()
        return session


class SessionManager:
    """Session manager for BoarAi system."""
    
    def __init__(
        self,
        storage_path: Optional[Path] = None,
        event_bus: Optional[EventBus] = None
    ):
        """Initialize session manager."""
        self._event_bus = event_bus or get_event_bus()
        self._storage = SessionStorage(storage_path)
        self._sessions: Dict[str, Session] = {}
        self._lock = asyncio.Lock()
    
    async def create_session(
        self,
        session_id: Optional[str] = None,
        agent_name: str = "default",
    ) -> Session:
        """Create a new session."""
        if session_id is None:
            session_id = str(uuid.uuid4())

        session = Session(session_id=session_id, agent_name=agent_name)

        # NOTE (v1.15/v1.17): the base constructor still does NOT snapshot the
        # *global* approval mode into security_overrides (a stamped "on" would
        # suppress the global auto_approve shortcut and regress auto_approve=true
        # deployments into prompting). The configured "new session default"
        # (``session.default_approval_mode``) is stamped later by
        # ``apply_session_defaults`` on the interactive creation paths
        # (HTTP/WS/message channel) and by the fork path — but only for
        # deny/allow ("ask" stamps nothing and follows the global fallback).
        # A deny/allow stamp is explicit user intent and wins over
        # auto_approve by design. Headless paths (task executor) keep
        # the global fallback so unattended runs still auto-approve.

        # Create session memory pod (empty memory.json for MemoryWeaver)
        try:
            if self._storage and self._storage._storage_path:
                memory_file = self._storage._storage_path / session_id / "memory.json"
                memory_file.parent.mkdir(parents=True, exist_ok=True)
                if not memory_file.exists():
                    import aiofiles
                    async with aiofiles.open(memory_file, "w", encoding="utf-8") as f:
                        await f.write("[]")
        except Exception as e:
            logger.debug(f"Session memory pod creation skipped: {e}")

        async with self._lock:
            self._sessions[session_id] = session

        logger.info(f"Created session: {session_id} (agent={agent_name})")

        # Publish event OUTSIDE the lock (handlers may re-enter SessionManager)
        await publish_event(
            EventType.SESSION_CREATED,
            {"session_id": session_id, "agent_name": agent_name},
            "session_manager"
        )

        # --- V2 hook: session.start (observe) ---
        try:
            from boarai.core.extension import get_hook_registry

            await get_hook_registry().trigger(
                "core.session.start",
                {"session_id": session_id, "agent_name": agent_name},
                timeout=2.0,
            )
        except Exception:  # noqa: BLE001 — hooks must never break creation
            pass

        return session
    
    def peek_session(self, session_id: str) -> Optional[Session]:
        """In-memory session only — no storage load, no orphan heal pass."""
        if not session_id:
            return None
        return self._sessions.get(session_id)

    async def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID."""
        # Check in-memory first
        if session_id in self._sessions:
            session = self._sessions[session_id]
            # Also heal warm cache (orphans from mid-run / failed save)
            await self._heal_orphan_tool_results(session)
            return session
        
        # Try to load from storage
        session = await self._storage.load_session(session_id)
        
        if session:
            self._sessions[session_id] = session
            # Heal orphan tool_calls left after crash/restart mid-tool
            await self._heal_orphan_tool_results(session)
            return session
        
        return None

    async def _heal_orphan_tool_results(self, session: Session) -> int:
        """Patch assistant tool_calls missing tool-role results; persist if needed."""
        try:
            from .orphan_tools import patch_orphan_tool_results

            try:
                from boarai.core.session.run_manager import get_run_manager

                active = get_run_manager().get_active_run_for_session(session.session_id)
                if active:
                    return 0
            except Exception:
                pass

            patched = patch_orphan_tool_results(session)
            if patched:
                await self.save_session(session.session_id)
            return len(patched)
        except Exception as e:
            logger.warning(
                "Failed to heal orphan tools for session %s: %s",
                getattr(session, "session_id", "?"),
                e,
            )
            return 0
    
    async def update_session(self, session: Session) -> bool:
        """Update a session."""
        async with self._lock:
            if session.session_id not in self._sessions:
                return False

            self._sessions[session.session_id] = session
            session.updated_at = datetime.now()

        # Publish event OUTSIDE the lock (handlers may re-enter SessionManager)
        await publish_event(
            EventType.SESSION_UPDATED,
            {"session_id": session.session_id},
            "session_manager"
        )

        return True
    
    async def delete_session(self, session_id: str, distill: bool = False) -> bool:
        """Delete a session."""
        # 1. Remove from memory under lock (atomic pop; minimizes the window
        #    in which a concurrent get_session could reload the deleted session).
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            # Not in memory — check if it exists on disk
            if not await self._storage.session_exists(session_id):
                return False

        # 2. Kill any active runs for this session
        try:
            from boarai.core.session.run_manager import RunStatus, get_run_manager
            rm = get_run_manager()
            for run in list(rm.all_runs()):
                if run.session_id == session_id:
                    if run.status == RunStatus.RUNNING and run.task:
                        run.task.cancel()
                        logger.info(
                            f"Cancelled run {run.run_id} for deleted session {session_id}"
                        )
                    # Clean up run from memory
                    rm.remove_run(run.run_id)
            # Drop session-scope approval grants (memory hygiene)
            rm.clear_session_grants(session_id)
        except Exception as e:
            logger.warning(f"Failed to cancel runs for {session_id}: {e}")

        # 3. Preserve valuable memories to global before the folder is moved
        try:
            from boarai.core.memory import get_memory_manager
            mm = get_memory_manager()
            if mm:
                await mm.preserve_session_memory(
                    session_id,
                    agent_name=session.agent_name if session else "default",
                )
        except Exception as e:
            logger.debug(f"Memory preserve skipped for {session_id}: {e}")

        # 4. Cancel idle compression timer
        try:
            from boarai.core.prompt import get_idle_timer
            timer = get_idle_timer()
            timer.cancel(session_id)
        except Exception:
            pass

        # 5. Move to trash (same-disk rename, fast, no lock held)
        try:
            storage_ok = await self._storage.delete_session(
                session_id, distill=distill, title=session.title if session else None
            )
            if not storage_ok:
                logger.error(
                    f"Failed to move session {session_id} to trash — "
                    f"files may remain on disk"
                )
        except Exception as e:
            logger.error(f"Session deletion error for {session_id}: {e}")

        # 6. Return immediately — no waiting for background tasks
        logger.info(f"Deleted session: {session_id} (distill={distill})")

        # --- V2 hook: session.end (observe) ---
        try:
            from boarai.core.extension import get_hook_registry

            await get_hook_registry().trigger(
                "core.session.end",
                {"session_id": session_id,
                 "agent_name": session.agent_name if session else "default"},
                timeout=2.0,
            )
        except Exception:  # noqa: BLE001 — hooks must never break deletion
            pass

        return True

    async def legacy_usage_entries(self) -> List[Dict[str, Any]]:
        """Disk-level snapshot of pre-ledger usage for ledger migration."""
        out: List[Dict[str, Any]] = []
        try:
            ids = await self._storage.list_sessions()
        except Exception as e:  # noqa: BLE001
            logger.warning("Legacy usage scan failed: %s", e)
            return out
        for sid in ids:
            try:
                session = self._sessions.get(sid)
                if not session:
                    session = await self._storage.load_session(sid)
                if not session:
                    continue
                for idx, req in enumerate(
                    getattr(session, "cache_requests", None) or []
                ):
                    if req.get("run_id"):
                        continue
                    out.append({
                        "index": idx,
                        "session_id": sid,
                        "session_title": getattr(session, "title", "") or "",
                        "agent_name": (
                            getattr(session, "metadata", None) or {}
                        ).get("agent_name", "default"),
                        "ts": req.get("ts", ""),
                        "model": req.get("model", "unknown"),
                        "input_tokens": req.get("input_tokens", 0),
                        "completion_tokens": req.get("completion_tokens", 0),
                        "cache_read_tokens": req.get("cache_read_tokens", 0),
                        "cache_creation_tokens": req.get(
                            "cache_creation_tokens", 0
                        ),
                    })
            except Exception as e:  # noqa: BLE001
                logger.warning("Legacy usage read failed for %s: %s", sid, e)
        return out

    async def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        sort: str = "updated_at",
        order: str = "desc",
        status: Optional[str] = None,
        search: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List sessions with pagination and filtering."""
        # Get session IDs from disk (ground truth)
        storage_sessions = await self._storage.list_sessions()
        storage_ids = set(storage_sessions)

        # Purge stale in-memory sessions whose backing folder no longer exists
        # (e.g. manually deleted from filesystem or cleanup activity)
        stale_ids = []
        for sid in list(self._sessions.keys()):
            if sid in storage_ids:
                continue
            # Check backing folder directly — skip sessions that lost their files
            try:
                session_dir = self._storage._get_session_dir(sid)
                if not session_dir.exists():
                    stale_ids.append(sid)
            except Exception:
                stale_ids.append(sid)

        for sid in stale_ids:
            logger.info(f"Purging stale session {sid} (backing folder missing)")
            del self._sessions[sid]

        # Union: disk first, remaining in-memory sessions second
        all_session_ids = storage_ids | set(self._sessions.keys())
        
        # Load all sessions. Prefer in-memory (no heal side-effect); load from
        # disk WITHOUT caching so listing does not balloon the memory cache.
        sessions = []
        for session_id in all_session_ids:
            session = self._sessions.get(session_id)
            if session is None:
                session = await self._storage.load_session(session_id)
            if session:
                # Apply filters
                if status == "archived" and not session.archived:
                    continue
                if status == "active" and session.archived:
                    continue
                if status == "pinned" and not session.pinned:
                    continue
                if source and session.source != source:
                    continue
                # Background skill-authoring sessions are internal plumbing:
                # hidden from the default list (HTTP + WS share this path);
                # callers that explicitly ask for source=skill_authoring
                # still get them (audit / WebUI lineage links).
                if (
                    session.source == "skill_authoring"
                    and source != "skill_authoring"
                ):
                    continue
                if search and search.lower() not in session.title.lower():
                    continue
                sessions.append(session)
        
        # Sort sessions
        reverse = order == "desc"
        if sort == "created_at":
            sessions.sort(key=lambda s: s.created_at, reverse=reverse)
        elif sort == "title":
            sessions.sort(key=lambda s: s.title.lower(), reverse=reverse)
        else:  # updated_at (default)
            sessions.sort(key=lambda s: s.updated_at, reverse=reverse)
        
        # Pinned sessions first
        sessions.sort(key=lambda s: not s.pinned)
        
        # Apply pagination
        total = len(sessions)
        paginated_sessions = sessions[offset:offset + limit]
        
        # Convert to summary dicts
        session_dicts = [s.to_summary_dict() for s in paginated_sessions]
        
        return {
            "sessions": session_dicts,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": (offset + limit) < total,
            # Channel id → display name from the SourceRegistry, so clients can
            # label each session's originating adapter without hardcoding any
            # channel. Sources that were uninstalled fall back to the raw id
            # client-side.
            "sources": self._source_display_map(),
        }

    @staticmethod
    def _source_display_map() -> Dict[str, str]:
        """Best-effort channel display-name map (empty on registry failure)."""
        try:
            from boarai.core.extension import get_source_registry
            return get_source_registry().list_sources()
        except Exception:  # noqa: BLE001 — display metadata only
            return {}
    
    async def save_session(self, session_id: str) -> bool:
        """Save a session to storage."""
        session = self._sessions.get(session_id)
        
        if not session:
            return False
        
        try:
            saved = await self._storage.save_session(session)
            if not saved:
                logger.error("Failed to save session: %s", session_id)
                return False
            logger.debug(f"Saved session: {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to save session {session_id}: {e}")
            return False
    
    async def save_all_sessions(self) -> Dict[str, bool]:
        """Save all sessions to storage."""
        results = {}
        
        for session_id in self._sessions:
            results[session_id] = await self.save_session(session_id)
        
        return results
    
    async def load_session(self, session_id: str) -> Optional[Session]:
        """Load a session from storage."""
        session = await self._storage.load_session(session_id)
        
        if session:
            self._sessions[session_id] = session
            await self._heal_orphan_tool_results(session)
            logger.debug(f"Loaded session: {session_id}")
        
        return session
    
    async def cleanup_old_sessions(self, max_age_days: int = 30) -> int:
        """Cleanup sessions older than ``max_age_days`` (soft-delete to trash)."""
        import time

        cutoff = time.time() - (max_age_days * 24 * 60 * 60)

        # 1. Drop stale in-memory sessions first (under lock)
        async with self._lock:
            stale_ids = [
                sid for sid, s in self._sessions.items()
                if s.updated_at.timestamp() < cutoff
            ]
            for sid in stale_ids:
                del self._sessions[sid]

        # 1b. Drop stale session-scope approval grants (memory hygiene)
        if stale_ids:
            try:
                from boarai.core.session.run_manager import get_run_manager
                get_run_manager().clear_session_grants_for(stale_ids)
            except Exception as e:
                logger.debug(f"Failed to clear grants for stale sessions: {e}")

        # 2. Soft-delete old sessions on disk (moves to trash, not rmtree)
        return await self._storage.cleanup_old_sessions(max_age_days)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get session manager statistics."""
        total_messages = sum(s.message_count for s in self._sessions.values())
        pinned_count = sum(1 for s in self._sessions.values() if s.pinned)
        archived_count = sum(1 for s in self._sessions.values() if s.archived)
        
        return {
            "total_sessions": len(self._sessions),
            "active_sessions": len(self._sessions) - archived_count,
            "pinned_sessions": pinned_count,
            "archived_sessions": archived_count,
            "total_messages": total_messages,
            "session_ids": list(self._sessions.keys()),
        }
    
    async def update_session_fields(
        self,
        session_id: str,
        title: Optional[str] = None,
        pinned: Optional[bool] = None,
        archived: Optional[bool] = None,
        metadata: Optional[Dict[str, Any]] = None,
        agent_name: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        model: Optional[str] = None,
        workspace_path: Optional[str] = None,
        approval_mode: Optional[str] = None,
    ) -> Optional[Session]:
        """Update specific session fields."""
        session = await self.get_session(session_id)
        if not session:
            return None

        if title is not None:
            session.title = title
        if pinned is not None:
            session.pinned = pinned
        if archived is not None:
            session.archived = archived
        if metadata is not None:
            session.metadata = metadata
        if agent_name is not None:
            session.agent_name = agent_name
        if thinking_mode is not None:
            from .thinking.config import normalize_thinking_mode

            session.thinking_mode = normalize_thinking_mode(thinking_mode)
        if model is not None:
            session.model = model
        if workspace_path is not None:
            session.workspace_path = workspace_path
        if approval_mode is not None and isinstance(approval_mode, str):
            am = approval_mode.strip().lower()
            if am in ("ask", "auto", "allow"):  # UI aliases
                am = "on" if am == "ask" else "off"
            prev_mode = session.security_overrides.get("approval_mode")
            if am == "":
                session.security_overrides.pop("approval_mode", None)
            elif am in ("deny", "on", "off"):
                session.security_overrides["approval_mode"] = am
            # Unknown values are ignored (keep the current override).
            # Non-string types are rejected outright (no str() coercion).
            new_mode = session.security_overrides.get("approval_mode")
            if new_mode != prev_mode:
                # H4: session security-override switches are security-relevant
                # events — best-effort audit trail (never breaks the update).
                try:
                    from boarai.core.security import get_security_manager

                    sec = get_security_manager()
                    if sec and getattr(sec, "_initialized", False):
                        sec.audit_record("approval", {
                            "decision": "session_override",
                            "session_id": session_id,
                            "field": "approval_mode",
                            "old": prev_mode,
                            "new": new_mode,
                        })
                except Exception:  # noqa: BLE001
                    logger.debug("session_override audit skipped")

        session.updated_at = datetime.now()

        # Publish event
        changes = {
            "title": title,
            "pinned": pinned,
            "archived": archived,
            "metadata": metadata,
            "agent_name": agent_name,
            "workspace_path": workspace_path,
            "approval_mode": approval_mode,
        }
        await publish_event(
            EventType.SESSION_UPDATED,
            {"session_id": session_id, "changes": changes},
            "session_manager"
        )

        return session


# Global session manager instance
_session_manager: Optional[SessionManager] = None


def get_session_manager(
    project_root: Optional[Path] = None,
    data_dir: Optional[Path] = None,
) -> SessionManager:
    """Get the global session manager instance."""
    global _session_manager
    if _session_manager is None:
        if data_dir is None:
            from boarai.core.runtime import resolve_data_dir
            data_dir = resolve_data_dir(project_root)
        storage_path = data_dir / "sessions"
        _session_manager = SessionManager(storage_path=storage_path)
    return _session_manager
