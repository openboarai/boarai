"""
Memory tools — active long-term memory operations for the LLM.

Since v1.5 one-way isolation, extensions may import core.* directly.
These tools talk to the MemoryManager (core.memory) so the agent can
actively recall, store, list and forget memory entries instead of only
relying on passive auto-injection (memory-horizon).

Design notes (see docs/memory design):
  * memory_recall  — keyword/token-overlap search across pods (real-time).
  * memory_remember — store a single entry (dedupe-aware, importance-based).
  * memory_list    — enumerate entries of a pod (self-audit).
  * memory_forget  — soft-delete an entry into the trash (recoverable).
"""

import logging
from typing import Any, Dict, List, Optional

from boarai.core.extension.tool import (
    CompressStrategy,
    SecurityProfile,
    SuggestedCategory,
    ToolParameter,
    tool,
)
from boarai.core.memory import get_memory_manager
from boarai.core.memory.types import EntryType, MemoryEntry, MemoryQuery

logger = logging.getLogger(__name__)

# Valid pod URI shortcuts: "" => global default
_VALID_ENTRY_TYPES = {e.value for e in EntryType}


def _resolve_pod(pod: str, ctx, agent_name: Optional[str]) -> str:
    """Map a user-friendly pod argument to a pod:// URI."""
    p = (pod or "").strip().lower()
    if not p or p == "global":
        return "pod://global"
    if p == "agent":
        name = agent_name or "default"
        return f"pod://agent/{name}"
    if p == "session":
        sid = getattr(ctx, "session_id", None)
        if sid:
            return f"pod://session/{sid}"
        return "pod://global"
    if p.startswith("pod://"):
        return p
    # Allow bare names for shared/session/agent pods
    if p.startswith("shared/"):
        return f"pod://shared/{p[len('shared/'):]}"
    if p.startswith("session/"):
        return f"pod://session/{p[len('session/'):]}"
    if p.startswith("agent/"):
        return f"pod://agent/{p[len('agent/'):]}"
    logger.warning("Unrecognized pod '%s' — falling back to pod://global", pod)
    return "pod://global"


def _agent_name_from_kwargs(kwargs: Dict[str, Any]) -> Optional[str]:
    """agent_name is passed via exec kwargs by run_tools.execute_single_tool."""
    return kwargs.get("agent_name") or None


def _format_entry(e: MemoryEntry, brief: bool = False,
                  max_content: Optional[int] = None) -> Dict[str, Any]:
    """Render a memory entry for LLM consumption (stable field order)."""
    if brief:
        content = e.content
        if max_content and len(content) > max_content:
            content = content[:max_content] + "…"
        return {
            "entry_id": e.entry_id,
            "topic": e.topic,
            "content": content,
            "importance": round(e.importance, 3),
            "entry_type": e.entry_type,
        }
    return {
        "entry_id": e.entry_id,
        "pod_uri": e.pod_uri,
        "topic": e.topic,
        "content": e.content,
        "entry_type": e.entry_type,
        "importance": round(e.importance, 3),
        "tags": e.tags,
        "created_at": e.created_at,
        "updated_at": e.updated_at or e.created_at,
    }


@tool(name="memory_recall",
      description="Search long-term memory for entries matching a query. "
                  "Useful when you need facts, decisions, preferences, or "
                  "background that may have been stored across sessions but "
                  "were not auto-injected into context. Returns top matches "
                  "sorted by relevance/importance.",
      security_profile=SecurityProfile.NONE,
      compress_strategy=CompressStrategy.LIST,
      suggested_category=SuggestedCategory.READONLY,
      parameters=[
          ToolParameter(name="query", type="string", required=True,
                        description="Natural-language or keyword query (CJK/English tokens)"),
          ToolParameter(name="pod", type="string", required=False, default="global",
                        description="Pod scope: '' or 'global', 'agent', 'session', "
                                    "or a pod:// URI"),
          ToolParameter(name="limit", type="integer", required=False, default=10,
                        description="Max results to return (max 50)"),
          ToolParameter(name="min_importance", type="number", required=False, default=0.0,
                        description="Minimum importance threshold (0.0-1.0)"),
      ])
async def memory_recall(ctx, **kwargs) -> Dict:
    """Recall memory entries matching the query across the given pod."""
    query = str(kwargs.get("query") or "").strip()
    pod = str(kwargs.get("pod") or "global")
    limit = int(kwargs.get("limit") or 10)
    min_importance = float(kwargs.get("min_importance") or 0.0)
    if not query:
        return {"error": "query is required"}
    mm = get_memory_manager()
    if mm is None:
        return {"error": "Memory system unavailable"}

    agent_name = _agent_name_from_kwargs(kwargs)
    pod_uri = _resolve_pod(pod, ctx, agent_name)
    mq = MemoryQuery(
        text=query,
        pod_uris=[pod_uri],
        min_importance=max(0.0, min(1.0, min_importance)),
        limit=max(1, min(limit, 50)),
        sort_by="relevance",
        sort_desc=True,
    )
    try:
        hits = await mm.recall(mq, pod_uris=[pod_uri])
    except Exception as e:  # noqa: BLE001
        logger.error("memory_recall failed: %s", e)
        return {"error": f"Failed to recall memory: {e}"}

    return {
        "query": query,
        "pod": pod_uri,
        "count": len(hits),
        "entries": [_format_entry(h, brief=True) for h in hits],
    }


@tool(name="memory_remember",
      description="Store a memory for future sessions — write immediately (no need "
                  "to acknowledge first) when the user says 'remember this', or "
                  "you learn a durable lesson, user preference, or design decision "
                  "+ reason. Skip task progress, work logs, and anything stale "
                  "within days. Write declarative facts ('User prefers concise "
                  "replies'), never self-instructions. Duplicates are merged.",
      security_profile=SecurityProfile.NONE,
      compress_strategy=CompressStrategy.DEFAULT,
      suggested_category=SuggestedCategory.SAFE,
      parameters=[
          ToolParameter(name="content", type="string", required=True,
                        description="The memory content to store (a fact, decision, preference, ...)"),
          ToolParameter(name="topic", type="string", required=False, default="",
                        description="Optional short topic/label for grouping"),
          ToolParameter(name="entry_type", type="string", required=False, default="fact",
                        description="Type: fact, decision, preference, pattern, insight, procedure",
                        enum=sorted(_VALID_ENTRY_TYPES)),
          ToolParameter(name="importance", type="number", required=False, default=0.6,
                        description="Importance 0.0-1.0 (higher = more likely auto-injected)"),
          ToolParameter(name="tags", type="array", required=False,
                        description="Optional list of tag strings"),
          ToolParameter(name="pod", type="string", required=False, default="global",
                        description="Pod scope: 'global', 'agent', 'session', or a pod:// URI"),
      ])
async def memory_remember(ctx, **kwargs) -> Dict:
    """Store a single memory entry (dedupe-aware)."""
    content = str(kwargs.get("content") or "").strip()
    topic = str(kwargs.get("topic") or "").strip()
    entry_type = str(kwargs.get("entry_type") or "fact")
    importance = kwargs.get("importance", 0.6)
    tags = kwargs.get("tags") or []
    pod = str(kwargs.get("pod") or "global")
    if not content:
        return {"error": "content is required"}
    if entry_type not in _VALID_ENTRY_TYPES:
        return {"error": f"Invalid entry_type: {entry_type} (expected one of "
                         f"{sorted(_VALID_ENTRY_TYPES)})"}
    mm = get_memory_manager()
    if mm is None:
        return {"error": "Memory system unavailable"}

    agent_name = _agent_name_from_kwargs(kwargs)
    pod_uri = _resolve_pod(pod, ctx, agent_name)
    entry = MemoryEntry(
        topic=topic,
        content=content,
        entry_type=entry_type,
        importance=max(0.0, min(1.0, float(importance if importance is not None else 0.6))),
        tags=list(tags or []),
    )
    try:
        entry_id = await mm.remember(pod_uri, entry)
    except Exception as e:  # noqa: BLE001
        logger.error("memory_remember failed: %s", e)
        return {"error": f"Failed to store memory: {e}"}

    return {
        "status": "stored",
        "entry_id": entry_id,
        "pod": pod_uri,
        "topic": entry.topic,
        "entry_type": entry.entry_type,
        "importance": entry.importance,
        "duplicate_merged": entry_id != entry.entry_id,
    }


@tool(name="memory_list",
      description="List memory entries in a pod (newest or by importance). "
                  "Useful for auditing what memory already exists before "
                  "writing, or to find an entry_id for memory_forget.",
      security_profile=SecurityProfile.NONE,
      compress_strategy=CompressStrategy.LIST,
      suggested_category=SuggestedCategory.READONLY,
      llm_visible=False,
      parameters=[
          ToolParameter(name="pod", type="string", required=False, default="global",
                        description="Pod scope: 'global', 'agent', 'session', or a pod:// URI"),
          ToolParameter(name="limit", type="integer", required=False, default=20,
                        description="Max entries per page (max 100)"),
          ToolParameter(name="offset", type="integer", required=False, default=0,
                        description="Skip the first N entries (pagination; use with total)"),
          ToolParameter(name="min_importance", type="number", required=False, default=0.0,
                        description="Only list entries with importance >= this value"),
          ToolParameter(name="sort_by", type="string", required=False, default="updated_at",
                        description="Sort key: updated_at, created_at, importance"),
          ToolParameter(name="brief", type="boolean", required=False, default=True,
                        description="True (default): compact fields only — keeps results "
                                    "small enough to enumerate many entries. False: full fields."),
      ])
async def memory_list(ctx, **kwargs) -> Dict:
    """List entries in a pod."""
    pod = str(kwargs.get("pod") or "global")
    limit = int(kwargs.get("limit") or 20)
    offset = max(0, int(kwargs.get("offset") or 0))
    min_importance = float(kwargs.get("min_importance") or 0.0)
    sort_by = str(kwargs.get("sort_by") or "updated_at")
    brief = kwargs.get("brief", True)
    brief = bool(brief) if not isinstance(brief, str) else brief.lower() != "false"
    mm = get_memory_manager()
    if mm is None:
        return {"error": "Memory system unavailable"}

    agent_name = _agent_name_from_kwargs(kwargs)
    pod_uri = _resolve_pod(pod, ctx, agent_name)
    key = sort_by if sort_by in ("updated_at", "created_at", "importance") else "updated_at"

    entries = await mm._read_entries(pod_uri)  # noqa: SLF001 — extension may reuse core internals (v1.5)
    if min_importance:
        entries = [e for e in entries if e.importance >= float(min_importance)]
    if key == "importance":
        entries.sort(key=lambda e: (e.importance, e.updated_at or ""), reverse=True)
    else:
        entries.sort(key=lambda e: (getattr(e, key, "") or ""), reverse=True)

    total = len(entries)
    page = entries[offset : offset + max(1, min(int(limit or 20), 100))]

    return {
        "pod": pod_uri,
        "count": len(page),
        "total": total,
        "offset": offset,
        "has_more": offset + len(page) < total,
        "entries": [_format_entry(e, brief=brief, max_content=200 if brief else None)
                    for e in page],
    }


@tool(name="memory_forget",
      description="Soft-delete a memory entry (moves it to the recycle bin for "
                  "30 days; recoverable). Use the entry_id returned by memory_recall.",
      security_profile=SecurityProfile.NONE,
      compress_strategy=CompressStrategy.DEFAULT,
      suggested_category=SuggestedCategory.DANGEROUS,
      parameters=[
          ToolParameter(name="entry_id", type="string", required=True,
                        description="Memory entry id to forget"),
          ToolParameter(name="pod", type="string", required=False, default="global",
                        description="Pod scope: 'global', 'agent', 'session', or a pod:// URI "
                                    "(searches global+shared when empty)"),
      ])
async def memory_forget(ctx, **kwargs) -> Dict:
    """Soft-delete a memory entry into the trash."""
    entry_id = str(kwargs.get("entry_id") or "").strip()
    pod = str(kwargs.get("pod") or "global")
    if not entry_id:
        return {"error": "entry_id is required"}
    mm = get_memory_manager()
    if mm is None:
        return {"error": "Memory system unavailable"}

    agent_name = _agent_name_from_kwargs(kwargs)
    pod_uri = _resolve_pod(pod, ctx, agent_name) if pod else None
    try:
        ok = await mm.soft_delete_entry(entry_id, pod_uri=pod_uri)
    except Exception as e:  # noqa: BLE001
        logger.error("memory_forget failed: %s", e)
        return {"error": f"Failed to forget memory: {e}"}

    if not ok:
        return {
            "error": (
                f"Entry not found: {entry_id} (or trash unavailable). "
                "Searched pod://global + shared pods — if memory_list/memory_recall "
                "showed this id under an agent or session pod, retry with "
                '{"pod": "agent"} or the explicit pod:// URI.'
            )
        }
    return {
        "status": "forgotten",
        "entry_id": entry_id,
        "note": "Soft-deleted into recycle bin; restore via trash_restore or WebUI data page.",
    }
