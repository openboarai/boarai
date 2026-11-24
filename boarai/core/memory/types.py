"""
Data types for BoarAi Synaptic Memory Mesh.

Defines MemoryEntry (atomic memory unit), MemorySynapse (mount pointer),
MemoryQuery (search parameters), and related enums.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

class EntryType(str, Enum):
    """Type of memory entry — determines lifecycle treatment."""
    FACT = "fact"
    DECISION = "decision"
    PREFERENCE = "preference"
    PATTERN = "pattern"
    SUMMARY = "summary"
    INSIGHT = "insight"
    PROCEDURE = "procedure"  # Auto-generated skill (reusable procedure)
    LESSON = "lesson"        # Failure-derived lesson (what to avoid / do instead)

class LifecycleState(str, Enum):
    """Lifecycle state for a memory entry."""
    ACTIVE = "active"
    ARCHIVED = "archived"
    PENDING_DELETION = "pending_deletion"
    DISTILLED = "distilled"

@dataclass
class MemoryEntry:
    """An atomic memory unit — a fact, decision, preference, pattern, or insight."""
    entry_id: str = ""
    pod_uri: str = ""
    topic: str = ""
    content: str = ""
    entry_type: str = EntryType.FACT.value

    # Scoring
    importance: float = 0.5
    access_count: int = 0

    # Tags & lifecycle
    tags: List[str] = field(default_factory=list)
    lifecycle_state: str = LifecycleState.ACTIVE.value

    # Timestamps
    created_at: str = ""
    updated_at: str = ""
    last_accessed_at: Optional[str] = None

    # Source tracking
    source_session: str = ""
    source_agent: str = ""

    # Affective (reserved, not used in retrieval yet)
    emotional_valence: Optional[float] = None   # -1.0 ~ 1.0
    emotional_arousal: Optional[float] = None   #  0.0 ~ 1.0

    # Evolution (consolidation)
    insight_level: int = 0          # 0=raw, 1=consolidated, 2=insight
    consolidated_from: List[str] = field(default_factory=list)

    # Conflict tracking
    conflict_group: Optional[str] = None

    # Extra metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.entry_id:
            self.entry_id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "pod_uri": self.pod_uri,
            "topic": self.topic,
            "content": self.content,
            "entry_type": self.entry_type,
            "importance": self.importance,
            "access_count": self.access_count,
            "tags": self.tags,
            "lifecycle_state": self.lifecycle_state,
            "created_at": self.created_at,
            "updated_at": self.updated_at or self.created_at,
            "last_accessed_at": self.last_accessed_at,
            "source_session": self.source_session,
            "source_agent": self.source_agent,
            "emotional_valence": self.emotional_valence,
            "emotional_arousal": self.emotional_arousal,
            "insight_level": self.insight_level,
            "consolidated_from": self.consolidated_from,
            "conflict_group": self.conflict_group,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        return cls(
            entry_id=data.get("entry_id", ""),
            pod_uri=data.get("pod_uri", ""),
            topic=data.get("topic", ""),
            content=data.get("content", ""),
            entry_type=data.get("entry_type", "fact"),
            importance=data.get("importance", 0.5),
            access_count=data.get("access_count", 0),
            tags=data.get("tags", []),
            lifecycle_state=data.get("lifecycle_state", "active"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            last_accessed_at=data.get("last_accessed_at"),
            source_session=data.get("source_session", ""),
            source_agent=data.get("source_agent", ""),
            emotional_valence=data.get("emotional_valence"),
            emotional_arousal=data.get("emotional_arousal"),
            insight_level=data.get("insight_level", 0),
            consolidated_from=data.get("consolidated_from", []),
            conflict_group=data.get("conflict_group"),
            metadata=data.get("metadata", {}),
        )

@dataclass
class MemorySynapse:
    """A dynamic mount pointer from a context to a MemoryPod."""
    synapse_id: str = ""
    source_pod_uri: str = ""
    target_context: str = ""        # "session:{id}" or "agent:{name}"
    label: str = ""
    weight: float = 0.5
    decay_rate: float = 0.02        # Per-turn decay
    filters: Dict[str, Any] = field(default_factory=dict)
    ttl: Optional[int] = None       # Seconds, None=forever
    created_at: str = ""
    last_accessed: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.synapse_id:
            self.synapse_id = "syn_" + str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def current_weight(self, turns_elapsed: int = 0) -> float:
        """Calculate current weight after decay."""
        w = self.weight * ((1 - self.decay_rate) ** turns_elapsed)
        return max(0.0, w)

    def is_alive(self, turns_elapsed: int = 0) -> bool:
        """Check if synapse is still alive."""
        if self.ttl and self.last_accessed:
            elapsed = (datetime.now() - datetime.fromisoformat(self.last_accessed)).total_seconds()
            if elapsed > self.ttl:
                return False
        return self.current_weight(turns_elapsed) > 0.05

    def to_dict(self) -> Dict[str, Any]:
        return {
            "synapse_id": self.synapse_id,
            "source_pod_uri": self.source_pod_uri,
            "target_context": self.target_context,
            "label": self.label,
            "weight": self.weight,
            "decay_rate": self.decay_rate,
            "filters": self.filters,
            "ttl": self.ttl,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemorySynapse":
        return cls(
            synapse_id=data.get("synapse_id", ""),
            source_pod_uri=data.get("source_pod_uri", ""),
            target_context=data.get("target_context", ""),
            label=data.get("label", ""),
            weight=data.get("weight", 0.5),
            decay_rate=data.get("decay_rate", 0.02),
            filters=data.get("filters", {}),
            ttl=data.get("ttl"),
            created_at=data.get("created_at", ""),
            last_accessed=data.get("last_accessed"),
        )

@dataclass
class MemoryQuery:
    """Search parameters for memory retrieval."""
    text: Optional[str] = None
    tags: Optional[List[str]] = None
    entry_type: Optional[str] = None
    agent_name: Optional[str] = None
    session_id: Optional[str] = None
    pod_uris: Optional[List[str]] = None
    min_importance: float = 0.0
    limit: int = 10
    offset: int = 0
    sort_by: str = "importance"
    sort_desc: bool = True

@dataclass
class MemoryStats:
    """Memory system statistics."""
    total_entries: int = 0
    total_pods: int = 0
    total_synapses: int = 0
    entries_by_type: Dict[str, int] = field(default_factory=dict)
    oldest_entry: str = ""
    newest_entry: str = ""
    total_tags: int = 0
    storage_size_bytes: int = 0
    pending_reincarnations: int = 0
    retired_agents: int = 0
