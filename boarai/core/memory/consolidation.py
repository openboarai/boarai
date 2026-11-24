"""
ConsolidationEngine — memory self-evolution engine.

Runs asynchronously after:
1. Batch memory writes (entries > 100 triggers consolidation)
2. Conflict detection during weaving
3. Manual trigger (CLI/API)

Responsibilities:
- Detect conflicting entries (same topic, contradictory content)
- Resolve conflicts by selecting the best version
- Generate higher-level Insights from related entries
- Prune stale, low-importance entries
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from ..event import get_event_bus, publish_event
from .types import MemoryEntry, MemoryQuery, EntryType, LifecycleState
from .events import MEMORY_CONSOLIDATION_DONE, MEMORY_INSIGHT_GENERATED

logger = logging.getLogger(__name__)


def _enrich_consolidation_tags(
    base_tags: List[str], entries: List[MemoryEntry],
) -> List[str]:
    """Enrich insight tags with the tags of the contributing entries."""
    result = set(base_tags)
    for e in entries:
        for t in e.tags:
            if t:
                result.add(t)
    # Generate combined tags from pairing
    base = list(result)
    if len(base) >= 2:
        for i in range(min(len(base), 3)):
            for j in range(i + 1, min(len(base), 4)):
                combined = f"{base[i]}{base[j]}"
                if 2 <= len(combined) <= 30:
                    result.add(combined)
    return list(result)[:15]


@dataclass
class ConsolidationReport:
    """Report from a consolidation run."""
    pod_uri: str = ""
    conflicts_found: int = 0
    conflicts_resolved: int = 0
    insights_generated: int = 0
    entries_pruned: int = 0
    total_entries_before: int = 0
    total_entries_after: int = 0
    details: List[str] = field(default_factory=list)


class ConsolidationEngine:
    """Memory self-evolution engine."""

    def __init__(self, manager=None):
        self._manager = manager

    def set_manager(self, manager) -> None:
        self._manager = manager

    async def consolidate_pod(self, pod_uri: str) -> ConsolidationReport:
        """Run full consolidation on a single pod."""
        if not self._manager:
            return ConsolidationReport(pod_uri=pod_uri)

        manager = self._manager
        report = ConsolidationReport(pod_uri=pod_uri)

        # Lock the pod for the whole read/transform/write cycle. remember()
        # acquires the same per-pod lock internally, so insight writes are done
        # AFTER the lock is released to avoid deadlock.
        async with manager.pod_store.pod_lock(pod_uri):
            entries = await manager.pod_store.read_entries(pod_uri)
            report.total_entries_before = len(entries)
            if not entries:
                return report

            # Step 1: Detect conflicts
            conflicts = self._detect_conflicts(entries)
            report.conflicts_found = len(conflicts)

            # Step 2: Resolve conflicts
            for conflict_key, conflict_entries in conflicts.items():
                resolved = self._resolve_conflict_group(conflict_key, conflict_entries)
                if resolved:
                    report.conflicts_resolved += 1
                    report.details.append(
                        f"Resolved conflict '{conflict_key}': "
                        f"{len(conflict_entries)} entries → 1 resolved"
                    )

            # Step 3: Prune stale entries
            pruned = self._prune_entries(entries)
            report.entries_pruned = len(pruned)

            # Keep only non-pruned entries
            pruned_ids = set(e.entry_id for e in pruned)
            entries = [e for e in entries if e.entry_id not in pruned_ids]
            report.total_entries_after = len(entries)

            # Write back pruned set
            await manager.pod_store.write_entries(pod_uri, entries)

        # Step 4: Generate + persist insights (remember() takes the lock itself)
        insights = await self._generate_insights(entries, pod_uri)
        for insight in insights:
            await manager.remember(pod_uri, insight, publish=True)
            report.insights_generated += 1
            report.details.append(
                f"Generated insight: {insight.topic[:60]}..."
            )

        # Publish event
        bus = get_event_bus()
        if bus:
            await publish_event(
                MEMORY_CONSOLIDATION_DONE,
                {
                    "pod_uri": pod_uri,
                    "conflicts_found": report.conflicts_found,
                    "conflicts_resolved": report.conflicts_resolved,
                    "insights_generated": report.insights_generated,
                    "entries_pruned": report.entries_pruned,
                },
                "consolidation_engine",
            )

        logger.info(f"Consolidation complete for {pod_uri}: {report}")
        return report

    def _detect_conflicts(self, entries: List[MemoryEntry]) -> Dict[str, List[MemoryEntry]]:
        """Detect entries with same topic but different content."""
        # Group by topic (case-insensitive, truncated)
        groups: Dict[str, List[MemoryEntry]] = {}
        for e in entries:
            if e.lifecycle_state != LifecycleState.ACTIVE.value:
                continue
            key = e.topic.lower().strip()[:100]
            if not key:
                continue
            groups.setdefault(key, []).append(e)

        # Filter groups with actual content conflicts
        conflicts: Dict[str, List[MemoryEntry]] = {}
        for key, group in groups.items():
            if len(group) < 2:
                continue
            # Check for different content
            contents = set(g.content.strip().lower()[:200] for g in group)
            if len(contents) > 1:
                conflicts[key] = group

        return conflicts

    def _resolve_conflict_group(
        self, topic_key: str, entries: List[MemoryEntry]
    ) -> bool:
        """Resolve a conflict group by selecting the best entry."""
        if not entries:
            return False

        # Score each entry: importance-dominant, recency as tiebreaker
        scored = []
        for e in entries:
            try:
                t = datetime.fromisoformat(e.updated_at or e.created_at).timestamp()
            except Exception:
                t = 0
            score = e.importance * 100 + (t / 1e9)
            scored.append((score, e))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Mark all but the winner as distilled
        winner = scored[0][1]
        for _, e in scored[1:]:
            e.lifecycle_state = LifecycleState.DISTILLED.value

        # Mark winner with conflict info
        winner.metadata["conflict_resolved"] = True
        winner.metadata["conflict_sources"] = [e.entry_id for _, e in scored[1:]]
        winner.importance = min(1.0, winner.importance + 0.05)  # Slight boost
        winner.updated_at = datetime.now().isoformat()

        return True

    async def _generate_insights(
        self, entries: List[MemoryEntry], pod_uri: str
    ) -> List[MemoryEntry]:
        """Generate higher-level insights from related entries."""
        active = [e for e in entries if e.lifecycle_state == LifecycleState.ACTIVE.value]
        if len(active) < 3:
            return []

        insights = []

        # Group by shared tags
        tag_groups: Dict[str, List[MemoryEntry]] = {}
        for e in active:
            for tag in e.tags:
                tag_groups.setdefault(tag, []).append(e)

        for tag, tag_entries in tag_groups.items():
            if len(tag_entries) < 3:
                continue

            # Check if entries have different topics
            topics = set(e.topic.lower().strip() for e in tag_entries)
            if len(topics) < 2:
                continue

            # Create insight
            decision_entries = [e for e in tag_entries if e.entry_type == EntryType.DECISION.value]
            fact_entries = [e for e in tag_entries if e.entry_type == EntryType.FACT.value]

            decisions = "\n".join(f"- {e.topic}: {e.content[:200]}" for e in decision_entries[:3])
            facts = "\n".join(f"- {e.topic}: {e.content[:200]}" for e in fact_entries[:3])

            content_parts = []
            if decisions:
                content_parts.append(f"相关决策:\n{decisions}")
            if facts:
                content_parts.append(f"相关事实:\n{facts}")

            if not content_parts:
                continue

            insight = MemoryEntry(
                pod_uri=pod_uri,
                topic=f"[Insight] 关于 {tag} 的模式总结",
                content="\n\n".join(content_parts),
                entry_type=EntryType.INSIGHT.value,
                tags=_enrich_consolidation_tags([tag, "insight"], tag_entries),
                importance=0.85,
                insight_level=2,
                consolidated_from=[e.entry_id for e in tag_entries],
                metadata={"generated_by": "consolidation_engine"},
            )
            insights.append(insight)

        return insights

    def _prune_entries(self, entries: List[MemoryEntry]) -> List[MemoryEntry]:
        """Identify entries that should be pruned."""
        pruned = []
        now = datetime.now()

        for e in entries:
            # Already marked for deletion
            if e.lifecycle_state in (LifecycleState.DISTILLED.value, LifecycleState.PENDING_DELETION.value):
                pruned.append(e)
                continue

            # Low importance + old
            if e.importance < 0.1 and e.last_accessed_at:
                try:
                    last = datetime.fromisoformat(e.last_accessed_at)
                    if (now - last).days > 30:
                        pruned.append(e)
                        continue
                except Exception:
                    pass

        # If no entries were pruned by rules, check total count
        if not pruned and self._manager:
            max_entries = getattr(self._manager._config, 'max_entries_per_pod', 10000)
            if len(entries) > max_entries:
                # Sort by importance (ascending) and prune oldest lowest
                sorted_entries = sorted(entries, key=lambda e: e.importance)
                excess = len(entries) - max_entries
                pruned = sorted_entries[:excess]

        return pruned

    async def needs_consolidation(self, pod_uri: str) -> bool:
        """Check if a pod needs consolidation."""
        if not self._manager:
            return False
        entries = await self._manager.pod_store.read_entries(pod_uri)

        # Condition 1: too many entries
        max_entries = getattr(self._manager._config, 'max_entries_per_pod', 10000)
        if len(entries) >= max_entries * 0.8:  # 80% threshold
            return True

        # Condition 2: has unresolved conflicts
        conflicts = self._detect_conflicts(entries)
        if conflicts:
            return True

        return False
