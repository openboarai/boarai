"""
MemoryWeaver — the fusion engine for the Synaptic Memory Mesh.

Responsibilities:
1. During prompt assembly, scan active synapses for current context
2. Multi-route parallel retrieval from mounted pods
3. Cross-pod deduplication with conflict resolution
4. Token budget enforcement
5. Proactive memory push (high-relevance unsolicited recall)
6. Staging area preview with verification watermark
"""

import asyncio
import json
import logging
from datetime import datetime
from collections import Counter
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field

from ..event import Event, get_event_bus, publish_event
from ..prompt.token_counter import count_tokens
from ..runtime.lru import BoundedMap
from .types import MemoryEntry, MemoryQuery, MemorySynapse, EntryType
from .events import MEMORY_ACCESSED, PROMPT_ZONE3_ASSEMBLING, PATTERN_RESONANCE

logger = logging.getLogger(__name__)


@dataclass
class WeaveResult:
    """Result of a weave operation."""
    knowledge_text: Optional[str] = None
    token_estimate: int = 0
    entries_used: int = 0
    sources: List[str] = field(default_factory=list)
    conflicts_resolved: int = 0
    proactive_note: Optional[str] = None


class MemoryWeaver:
    """Memory fusion engine — compiles multi-source memories into a Knowledge Horizon."""

    def __init__(self, manager=None):
        self._manager = manager
        # Bounded by context key: both grow one entry per session context and
        # nothing evicted them, so a long-running server leaked memory for every
        # context it had ever woven.
        self._proactive_cache: BoundedMap[str, int] = BoundedMap()  # context → turns since last push
        self._last_conflicts = 0  # Counter for conflicts resolved in last weave
        self._cache: BoundedMap[str, Dict[str, Any]] = BoundedMap()  # context → weave cache entry
        self._frozen: set = set()  # contexts frozen at session start (v1.5)

    def set_manager(self, manager) -> None:
        """Set the MemoryManager instance (lazy injection)."""
        self._manager = manager

    def freeze_context(self, context: str) -> None:
        """Freeze the current weave result for a context (v1.5 session semantics)."""
        self._frozen.add(context)

    def unfreeze_context(self, context: str) -> None:
        """Remove a context from the frozen set (used by tests / reload)."""
        self._frozen.discard(context)

    def is_frozen(self, context: str) -> bool:
        """Whether a context is frozen (session prefix locked)."""
        return context in self._frozen

    def release_context(self, context: str) -> None:
        """Drop every per-context entry when a session ends."""
        self._frozen.discard(context)
        self._cache.pop(context, None)
        self._proactive_cache.pop(context, None)

    def _matches_filter(self, entry: MemoryEntry, synapse: MemorySynapse) -> bool:
        """Check if an entry matches a synapse's filter rules."""
        filters = synapse.filters
        if not filters:
            return True

        # Tag filter
        if "tags" in filters:
            required_tags = filters["tags"]
            if not any(t in entry.tags for t in required_tags):
                return False

        # Entry type filter
        if "entry_types" in filters:
            if entry.entry_type not in filters["entry_types"]:
                return False

        # Importance filter
        if "min_importance" in filters:
            if entry.importance < filters["min_importance"]:
                return False

        return True

    def _resolve_conflict(self, entries: List[MemoryEntry]) -> List[MemoryEntry]:
        """Resolve conflicts: same topic, different content."""
        if len(entries) < 2:
            return entries

        # Group by topic (case-insensitive)
        groups: Dict[str, List[MemoryEntry]] = {}
        for e in entries:
            key = e.topic.lower().strip()
            if key:
                groups.setdefault(key, []).append(e)

        resolved = []
        self._last_conflicts = 0

        for topic, group in groups.items():
            if len(group) == 1:
                resolved.append(group[0])
                continue

            # Detect conflict: different content
            contents = set(e.content.strip().lower() for e in group)
            if len(contents) == 1:
                resolved.append(group[0])
                continue

            # Conflict! Resolve by score × recency
            self._last_conflicts += 1
            scored = []
            for e in group:
                # Parse time for tiebreaker
                try:
                    t = datetime.fromisoformat(e.updated_at or e.created_at).timestamp()
                except Exception:
                    t = 0
                score = e.importance * 10 + (t / 1e10)  # Importance-dominant, time as tiebreaker
                scored.append((score, e))

            scored.sort(key=lambda x: x[0], reverse=True)
            winner = scored[0][1]

            # Mark conflict in winner's metadata
            winner.metadata["conflict_resolved"] = True
            winner.metadata["conflict_sources"] = [e.entry_id for _, e in scored[1:]]
            resolved.append(winner)

        return resolved

    def _calculate_adjusted_score(
        self, entry: MemoryEntry, synapse: MemorySynapse, turns_elapsed: int = 0
    ) -> float:
        """Calculate adjusted importance score with decay."""
        current_weight = synapse.current_weight(turns_elapsed)
        # Time decay: halflife = 72 hours
        try:
            updated = datetime.fromisoformat(entry.updated_at or entry.created_at)
            hours_elapsed = (datetime.now() - updated).total_seconds() / 3600
            time_decay = 2 ** (-hours_elapsed / 72)  # e^(-hours/72) ≈ 2^(-hours/72 * 1.44)
        except Exception:
            time_decay = 1.0

        return entry.importance * current_weight * time_decay

    async def weave(
        self,
        context: str,
        user_input: str = "",
        token_budget: int = 2000,
        agent_filters: Optional[Dict] = None,
        turns_elapsed: int = 0,
    ) -> WeaveResult:
        """Main weave pipeline: compile multi-source memories into Knowledge Horizon."""
        if not self._manager:
            return WeaveResult(knowledge_text=None)

        manager = self._manager

        # Step 0: Session-level cache — skip re-weaving when nothing changed.
        #
        # v1.5 session-freeze semantics: the first weave of a session fixes the
        # memory-horizon prefix for the WHOLE session (immutable). Even if new
        # memories are written mid-session (pod dirty) or turns elapse, the
        # frozen context ALWAYS returns the first result — this keeps the
        # system prompt prefix byte-stable so provider prefix caches keep
        # hitting. New memories are still reachable via memory_recall tools.
        #
        # Non-frozen contexts (e.g. ad-hoc weave calls) keep the old
        # turn-based cache to avoid pathological re-weaving.
        if context in self._frozen:
            cache = self._cache.get(context)
            if cache is not None:
                return WeaveResult(
                    knowledge_text=cache.get("text"),
                    token_estimate=cache.get("tokens", 0),
                    entries_used=cache.get("entries", 0),
                    sources=cache.get("sources", []),
                    conflicts_resolved=cache.get("conflicts", 0),
                )
            # Frozen context with no cached result yet (first weave found
            # nothing): lock the EMPTY state too, so later memory writes
            # never leak into this session's prompt prefix.
            self._cache[context] = {
                "turns": turns_elapsed,
                "text": None,
                "tokens": 0,
                "entries": 0,
                "sources": [],
                "conflicts": 0,
            }
            return WeaveResult(knowledge_text=None)

        cache = self._cache.get(context)
        if (
            cache is not None
            and cache.get("turns") == turns_elapsed
            and not manager.pod_store.cache_dirty
        ):
            return WeaveResult(
                knowledge_text=cache.get("text"),
                token_estimate=cache.get("tokens", 0),
                entries_used=cache.get("entries", 0),
                sources=cache.get("sources", []),
                conflicts_resolved=cache.get("conflicts", 0),
            )

        # Step 1: Get active synapses
        synapses = await manager.get_active_synapses(context, turns_elapsed)
        if not synapses:
            return WeaveResult(knowledge_text=None)

        # Step 2: Retrieve + filter entries from all source pods
        all_candidates: List[tuple] = []  # (adjusted_score, entry, synapse)
        for synapse in synapses:
            pod_uri = synapse.source_pod_uri
            entries = await manager.pod_store.read_entries(pod_uri)

            for entry in entries:
                if entry.lifecycle_state != "active":
                    continue
                if not self._matches_filter(entry, synapse):
                    continue
                score = self._calculate_adjusted_score(entry, synapse, turns_elapsed)
                if score <= 0:
                    continue
                all_candidates.append((score, entry, synapse))

        if not all_candidates:
            return WeaveResult(knowledge_text=None)

        # Step X: Pattern resonance detection (zero-latency, hash-collision only)
        self._detect_pattern_resonance(context, [c[1] for c in all_candidates])

        # Step 2b: Supplement with tag index lookup (semantic expansion)
        if user_input:
            index_entries = await self._index_lookup(user_input, all_candidates)
            if index_entries:
                all_candidates.extend(index_entries)

        # Step 3: Sort by adjusted score
        all_candidates.sort(key=lambda x: x[0], reverse=True)

        # Step 4: Resolve conflicts (deduplicate by topic)
        entries_to_weave = [c[1] for c in all_candidates]
        resolved = self._resolve_conflict(entries_to_weave)
        conflict_count = getattr(self, '_last_conflicts', 0)

        # Step 4b: Persist conflict-resolution metadata back to source pods
        # so decisions accumulate instead of being recomputed every turn.
        if conflict_count:
            await self._persist_conflicts(manager, resolved)

        # Step 5: Token budget enforcement
        woven_entries = []
        total_tokens = 0
        for entry in resolved:
            # Estimate entry tokens: topic + content + overhead (~50 tokens)
            entry_tokens = count_tokens(entry.topic + entry.content) + 50
            if total_tokens + entry_tokens > token_budget and woven_entries:
                break
            woven_entries.append(entry)
            total_tokens += entry_tokens

        if not woven_entries:
            return WeaveResult(knowledge_text=None)

        # Step 6: Render as Markdown Knowledge Horizon
        # Group by source pod
        by_pod: Dict[str, List[MemoryEntry]] = {}
        for entry in woven_entries:
            by_pod.setdefault(entry.pod_uri, []).append(entry)

        lines = ["## 挂载记忆 (Knowledge Horizon)"]
        sources = []

        for pod_uri, pod_entries in by_pod.items():
            _, pid = manager.pod_store.parse_pod_uri(pod_uri)
            # Find synapse for weight info
            weight_info = ""
            for s in synapses:
                if s.source_pod_uri == pod_uri:
                    w = s.current_weight(turns_elapsed)
                    weight_info = f" (权重 {w:.2f})"
                    break

            lines.append(f"\n### {pid}{weight_info}")
            sources.append(pid)

            for entry in pod_entries:
                # Format entry
                conflict_mark = ""
                if entry.metadata.get("conflict_resolved"):
                    conflict_mark = " ⚠️"

                tags_str = f" [{', '.join(entry.tags[:3])}]" if entry.tags else ""
                lines.append(f"- **{entry.topic}**{tags_str}{conflict_mark}")
                lines.append(f"  {entry.content[:300]}")

        # Add conflict note
        if conflict_count > 0:
            lines.append(f"\n---\n⚠️ 检测到 {conflict_count} 组冲突记忆，已按重要性×时效性裁决。")

        # Add staging preview
        staging_text = await self._load_staging_preview()
        if staging_text:
            lines.append(f"\n---\n📌 {staging_text}")

        knowledge_text = "\n".join(lines)
        token_estimate = count_tokens(knowledge_text)

        # Cache the weave result; only clear dirty flags for pods this weave
        # actually mounted, so other contexts never hit a stale cache entry.
        self._cache[context] = {
            "turns": turns_elapsed,
            "text": knowledge_text,
            "tokens": token_estimate,
            "entries": len(woven_entries),
            "sources": sources,
            "conflicts": conflict_count,
        }
        for s in synapses:
            manager.pod_store.cache_dirty.discard(s.source_pod_uri)

        return WeaveResult(
            knowledge_text=knowledge_text,
            token_estimate=token_estimate,
            entries_used=len(woven_entries),
            sources=sources,
            conflicts_resolved=conflict_count,
        )

    async def _persist_conflicts(
        self, manager, entries: List[MemoryEntry],
    ) -> None:
        """Persist conflict-resolution metadata back to source pods."""
        by_pod: Dict[str, List[MemoryEntry]] = {}
        for e in entries:
            if e.metadata.get("conflict_resolved"):
                by_pod.setdefault(e.pod_uri, []).append(e)
        for pod_uri, winners in by_pod.items():
            try:
                all_entries = await manager.pod_store.read_entries(pod_uri)
                winner_ids = {w.entry_id for w in winners}
                changed = False
                for e in all_entries:
                    if e.entry_id in winner_ids:
                        e.metadata["conflict_resolved"] = True
                        changed = True
                if changed:
                    await manager.pod_store.write_entries(pod_uri, all_entries)
            except Exception as e:
                logger.debug(f"Conflict persist failed for {pod_uri}: {e}")

    def _detect_pattern_resonance(
        self, context: str, entries: List[MemoryEntry],
    ) -> None:
        """Detect cross-pod pattern resonance — zero-latency hot path."""
        # Group by topic
        topic_groups: Dict[str, List[MemoryEntry]] = {}
        for entry in entries:
            if not entry.topic:
                continue
            topic_groups.setdefault(entry.topic, []).append(entry)

        event_bus = get_event_bus()
        found = False
        for topic, group in topic_groups.items():
            source_pods = {e.pod_uri for e in group}
            importance_sum = sum(e.importance for e in group)

            if len(source_pods) >= 3 and importance_sum > 2.0:
                # Fire-and-forget: publish event, zero impact on TTFT
                if event_bus:
                    asyncio.ensure_future(publish_event(
                        PATTERN_RESONANCE,
                        {
                            "topic": topic,
                            "sample_entries": [e.to_dict() for e in group[:5]],
                            "source_pods": list(source_pods),
                            "agent": context,
                        },
                        "memory_weaver",
                    ))
                found = True
                break  # One skill per weave (the strongest signal)

        if found:
            logger.debug(f"[Resonance] 检测到模式共振，正在抛锻造信号")

    async def _load_staging_preview(self) -> Optional[str]:
        """Load staging insights for preview (with verification watermark)."""
        if not self._manager:
            return None
        staging_path = self._manager.pod_store.boarai_dir / "memory" / "staging" / "global_insights.json"
        if not staging_path.exists():
            return None

        try:
            import aiofiles
            async with aiofiles.open(staging_path, "r", encoding="utf-8") as f:
                content = await f.read()
            if not content.strip():
                return None

            data = json.loads(content)
            insights = data.get("insights", [])
            pending = [i for i in insights if i.get("status") == "pending"]
            if not pending:
                return None

            lines = ["**待验证洞察 (来自已删除会话的蒸馏)**\n"]
            for insight in pending[:3]:  # Max 3 preview
                tags = insight.get("tags", [])
                target = insight.get("target", "unknown")
                lines.append(f"- {insight.get('epitaph', '')[:100]}... → [{target}]")

            if len(pending) > 3:
                lines.append(f"  *还有 {len(pending) - 3} 条待确认*")

            lines.append("\n*以上信息来自已删除会话的蒸馏，未经确认，仅供参考。*")
            return "\n".join(lines)

        except Exception as e:
            logger.debug(f"Failed to load staging preview: {e}")
            return None

    async def check_proactive(
        self,
        context: str,
        user_input: str,
        threshold: float = 0.9,
        cooldown_turns: int = 5,
        turns_elapsed: int = 0,
    ) -> Optional[str]:
        """Check if a proactive memory push is warranted."""
        if not self._manager:
            return None

        # Check cooldown
        last_turn = self._proactive_cache.get(context, 0)
        if turns_elapsed - last_turn < cooldown_turns:
            return None

        manager = self._manager
        synapses = await manager.get_active_synapses(context, turns_elapsed)

        best_match = None
        best_score = 0.0

        for synapse in synapses:
            entries = await manager.pod_store.read_entries(synapse.source_pod_uri)
            for entry in entries:
                if entry.lifecycle_state != "active":
                    continue
                score = self._calculate_adjusted_score(entry, synapse, turns_elapsed)
                if score > best_score and score > threshold:
                    # Check relevance to user_input (simple keyword overlap)
                    if user_input:
                        keywords = set(user_input.lower().split()[:10])
                        entry_keywords = set(
                            (entry.topic + " " + entry.content).lower().split()[:20]
                        )
                        overlap = len(keywords & entry_keywords)
                        if overlap == 0:
                            continue  # No keyword overlap, skip
                    best_score = score
                    best_match = entry

        if not best_match:
            return None

        # Update cooldown
        self._proactive_cache[context] = turns_elapsed

        _, pid = manager.pod_store.parse_pod_uri(best_match.pod_uri)
        return (
            f"💡 **主动记忆提醒**：当前讨论与历史记忆高度相关 "
            f"(来自 {pid}，匹配度 {best_score:.2f}):\n"
            f"- **{best_match.topic}**: {best_match.content[:200]}"
        )

    async def _index_lookup(
        self,
        user_input: str,
        existing_candidates: List[tuple],
    ) -> List[tuple]:
        """Supplement existing candidates via tag inverted index."""
        if not self._manager or not self._manager.pod_store.tag_index:
            return []

        index = self._manager.pod_store.tag_index
        manager = self._manager

        # Extract simple keywords: split + filter short words
        keywords = [k for k in user_input.lower().split() if len(k) >= 2]
        if not keywords:
            return []

        # O(1) dictionary lookup
        hit_map = index.search(keywords, top_k=10)

        # Collect unique entry IDs
        seen_ids = {c[1].entry_id for c in existing_candidates}
        new_ids: Set[str] = set()
        for ids in hit_map.values():
            new_ids.update(ids)
        new_ids -= seen_ids

        if not new_ids:
            return []

        # Load entries and score
        results = []
        for entry_id in new_ids:
            entry = await manager.pod_store.find_entry_by_id(entry_id)
            if not entry:
                continue
            if entry.lifecycle_state != "active":
                continue
            # Default score for index hits (lower than synapse hits)
            score = entry.importance * 0.3
            if score > 0:
                results.append((score, entry, None))

        if results:
            logger.debug(
                f"Index lookup: {len(keywords)} keywords → "
                f"{len(new_ids)} new entries from {len(hit_map)} tags"
            )
        return results

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract meaningful keywords for index lookup."""
        import re
        # Split on non-word chars, keep only alphanumeric + CJK + hyphens
        words = re.findall(r'[\w\u4e00-\u9fff-]+', text.lower())
        # Filter out single chars and very long words
        return [w[:20] for w in words if 2 <= len(w) <= 30]


# ==================== Global Singleton ====================

_memory_weaver: Optional[MemoryWeaver] = None


def get_memory_weaver() -> MemoryWeaver:
    """Get the global MemoryWeaver singleton."""
    global _memory_weaver
    if _memory_weaver is None:
        _memory_weaver = MemoryWeaver()
    return _memory_weaver
