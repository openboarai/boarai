"""``SynapticMeshProvider``: pod + synapse retrieval backend.

This is where the original memory implementation landed: pods as memory
containers, synapses as mount pointers, token-overlap scoring, the Mark & Reap
lifecycle, and skill forging/distillation. All of it used to share a single
class with orchestration; it is now the first ``MemoryProvider``
implementation, so the retrieval backend can be swapped while the four engines
(weaver, consolidation, reincarnation, distill worker) stay untouched.

Storage mechanics (path resolution, atomic IO, per-pod locks, parse cache) are
deliberately absent here: they belong to the ``PodStore`` facet, reached through
``self._store``, which keeps the storage backend independently replaceable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..event import EventBus, get_event_bus, publish_event
from .events import (
    MEMORY_DELETED,
    MEMORY_POD_RECYCLED,
    MEMORY_STORED,
    MEMORY_SYNAPSE_CREATED,
    MEMORY_SYNAPSE_ORPHANED,
    MEMORY_SYNAPSE_REDIRECTED,
    MEMORY_SYNAPSE_REMOVED,
    PATTERN_RESONANCE,
)
from .pod_store import FilePodStore, PodStore
from .provider import MemoryProvider
from .types import (
    EntryType,
    LifecycleState,
    MemoryEntry,
    MemoryQuery,
    MemoryStats,
    MemorySynapse,
)

logger = logging.getLogger(__name__)


class SynapticMeshProvider(MemoryProvider):
    """Retrieval over pods and synapses using token-overlap scoring."""

    name = "synaptic_mesh"

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        event_bus: Optional[EventBus] = None,
        config: Any = None,
        project_root: Optional[Path] = None,
    ) -> None:
        if data_dir is None:
            from boarai.core.runtime import resolve_data_dir

            data_dir = resolve_data_dir(project_root)
        if config is None:
            from .manager import MemoryConfig

            config = MemoryConfig()

        self._project_root = project_root
        self._event_bus = event_bus
        self._config = config
        self._store: PodStore = FilePodStore(data_dir)

        # Read-modify-write lock for the synapse index file. ``_write_synapses``
        # replaces the file via os.replace, and on Windows a concurrent reader
        # of the replaced file raises PermissionError; serialising access
        # removes that race entirely.
        self._synapse_lock = asyncio.Lock()

        # Contexts whose default mounts are already guaranteed, so the synapse
        # file is not re-read every turn. Cleared on unmount.
        self._mounts_checked: Set[str] = set()

        # Consumer for skill forging requests.
        self._forge_queue: asyncio.Queue = asyncio.Queue()
        self._forge_task: Optional[asyncio.Task] = None

        self._event_handlers: List[Tuple[str, Any]] = []
        self._synapse_cache: Dict[str, List[MemorySynapse]] = {}
        self._synapse_cache_dirty: bool = True

        self._llm_service = None
        self._session_manager = None
        self._distill_worker = None

    # --- Storage facet ---

    @property
    def pod_store(self) -> PodStore:
        return self._store

    @property
    def config(self) -> Any:
        """Memory configuration; engines such as consolidation read thresholds from it."""
        return self._config

    @property
    def boarai_dir(self) -> Path:
        """Memory data root."""
        return self._store.boarai_dir

    async def list_entries(self, pod_uri: str) -> List[MemoryEntry]:
        """List every entry of one pod (public read entry point)."""
        return await self._store.read_entries(pod_uri)

    # --- Entry CRUD ---

    async def remember(
        self,
        pod_uri: str,
        entry: MemoryEntry,
        publish: bool = True,
        dedupe: bool = True,
    ) -> str:
        """Store a memory entry in a pod (concurrency-safe, dedup-aware)."""
        entry.pod_uri = pod_uri
        if not entry.created_at:
            entry.created_at = datetime.now().isoformat()
        entry.updated_at = datetime.now().isoformat()

        async with self._store.pod_lock(pod_uri):
            entries = await self._store.read_entries(pod_uri)
            # Replace if entry_id exists, otherwise append
            replaced = False
            for i, e in enumerate(entries):
                if e.entry_id == entry.entry_id:
                    entries[i] = entry
                    replaced = True
                    break
            if not replaced:
                if dedupe:
                    dup = self._find_similar_entry(entries, entry)
                    if dup is not None:
                        dup.importance = max(dup.importance, entry.importance)
                        dup.updated_at = entry.updated_at
                        if entry.tags:
                            dup.tags = list(set(dup.tags + entry.tags))
                        await self._store.write_entries(pod_uri, entries)
                        return dup.entry_id
                entries.append(entry)

            await self._store.write_entries(pod_uri, entries)

        # Sync tags to inverted index
        if self._store.tag_index and entry.tags:
            await self._store.tag_index.add(entry.entry_id, entry.tags)

        if publish and self._event_bus:
            await publish_event(
                MEMORY_STORED,
                {"entry_id": entry.entry_id, "pod_uri": pod_uri,
                 "topic": entry.topic, "entry_type": entry.entry_type},
                "memory_manager",
            )

        # Auto-trigger consolidation when pod exceeds threshold
        if len(entries) > 100:
            asyncio.ensure_future(self._auto_consolidate(pod_uri))

        return entry.entry_id

    @staticmethod
    def _find_similar_entry(
        entries: List[MemoryEntry], entry: MemoryEntry,
    ) -> Optional[MemoryEntry]:
        """Find an existing active entry with identical content (exact dedupe)."""
        content_key = (entry.content or "").strip().lower()[:120]
        if not content_key:
            return None
        for e in entries:
            if e.lifecycle_state != LifecycleState.ACTIVE.value:
                continue
            if (e.content or "").strip().lower()[:120] == content_key:
                return e
        return None

    async def _auto_consolidate(self, pod_uri: str) -> None:
        """Auto-trigger consolidation for pods with too many entries."""
        try:
            from .consolidation import ConsolidationEngine
            engine = ConsolidationEngine(self)
            report = await engine.consolidate_pod(pod_uri)
            logger.info(
                f"Auto-consolidation: {pod_uri} "
                f"pruned={report.entries_pruned} resolved={report.conflicts_resolved}"
            )
        except Exception as e:
            logger.debug(f"Auto-consolidation skipped for {pod_uri}: {e}")

    async def _auto_confirm_staging(self) -> None:
        """Auto-confirm high-confidence staging entries and clean expired ones."""
        staging_path = self.boarai_dir / "memory" / "staging" / "global_insights.json"
        if not staging_path.exists():
            return

        try:
            f = await asyncio.to_thread(
                open, staging_path, "r", encoding="utf-8",
            )
            try:
                data = json.loads(await asyncio.to_thread(f.read))
            finally:
                f.close()
        except (json.JSONDecodeError, FileNotFoundError, OSError):
            return

        insights = data.get("insights", [])
        if not insights:
            return

        now = datetime.now()
        auto_confirmed = []
        expired = []
        remaining = []

        for insight in insights:
            expires_str = insight.get("expires_at", "")
            try:
                expires_at = datetime.fromisoformat(expires_str) if expires_str else None
            except (ValueError, TypeError):
                expires_at = None

            # Auto-expire entries older than 7 days
            if expires_at and expires_at < now:
                expired.append(insight)
                continue

            confidence = insight.get("confidence", 0)
            # Auto-confirm high-confidence entries (> 0.95)
            if confidence > 0.95:
                auto_confirmed.append(insight)
            else:
                remaining.append(insight)

        # Merge auto-confirmed entries to global pod
        for insight in auto_confirmed:
            try:
                await self.remember(
                    "pod://global",
                    MemoryEntry(
                        topic=f"Insight: {insight.get('source_pod', 'unknown')}",
                        content=insight.get("epitaph", ""),
                        entry_type=EntryType.INSIGHT.value,
                        tags=insight.get("tags", ["auto_confirmed"]),
                        importance=insight.get("confidence", 0.9),
                    ),
                    publish=False,
                )
            except Exception as e:
                logger.debug(f"Auto-confirm staging failed: {e}")

        if auto_confirmed:
            logger.info(f"Auto-confirmed {len(auto_confirmed)} staging entries → global")

        # Write remaining entries back
        data["insights"] = remaining
        try:
            from ..runtime import write_text_atomic_async

            staging_path.parent.mkdir(parents=True, exist_ok=True)
            await write_text_atomic_async(
                staging_path, json.dumps(data, ensure_ascii=False, indent=2),
            )
        except Exception as e:
            logger.error(f"Failed to update staging file: {e}")

    async def remember_batch(
        self, pod_uri: str, entries: List[MemoryEntry]
    ) -> List[str]:
        """Store multiple entries atomically."""
        existing = await self._store.read_entries(pod_uri)
        now = datetime.now().isoformat()
        ids = []
        for entry in entries:
            entry.pod_uri = pod_uri
            entry.created_at = entry.created_at or now
            entry.updated_at = now
            for i, e in enumerate(existing):
                if e.entry_id == entry.entry_id:
                    existing[i] = entry
                    break
            else:
                existing.append(entry)
            ids.append(entry.entry_id)

        await self._store.write_entries(pod_uri, existing)
        return ids

    async def recall(
        self,
        query: MemoryQuery,
        pod_uris: Optional[List[str]] = None,
    ) -> List[MemoryEntry]:
        """Search for memory entries matching the query across specified pods."""
        if pod_uris is None:
            pod_uris = ["pod://global"]

        q_tokens = self._tokenize(query.text) if query.text else set()
        results = []
        # Overlap scores live beside the results, not inside them. Writing them
        # onto entry.metadata used a persisted-looking dict as scratch space and
        # needed a strip pass before returning; entries that missed that pass
        # (an exception, a new sort branch) would carry a private key outward.
        overlap: Dict[str, float] = {}
        for pod_uri in pod_uris:
            entries = await self._store.read_entries(pod_uri)
            for entry in entries:
                if entry.lifecycle_state != LifecycleState.ACTIVE.value:
                    continue
                if entry.importance < query.min_importance:
                    continue
                if query.entry_type and entry.entry_type != query.entry_type:
                    continue
                if query.agent_name and entry.source_agent != query.agent_name:
                    continue
                if query.session_id and entry.source_session != query.session_id:
                    continue
                if query.tags:
                    if not any(t in entry.tags for t in query.tags):
                        continue
                if query.text:
                    hay = f"{entry.topic} {entry.content}".lower()
                    if query.text.lower() not in hay:
                        # Overlap filter: compare query tokens against the
                        # entry's OWN tokens only (never re-tokenize the query
                        # inside the hay, which made the intersection trivially
                        # non-empty and the filter a no-op).
                        h_tokens = self._tokenize(hay)
                        if not q_tokens & h_tokens:
                            continue
                    # Keyword overlap boosts entry score for the sort below
                    h_tokens = self._tokenize(entry.topic + " " + entry.content)
                    overlap[entry.entry_id] = (
                        len(q_tokens & h_tokens) / max(len(q_tokens), 1)
                    )
                results.append(entry)

        # Sort: recall overlap is a soft tiebreaker on top of the requested key
        reverse = query.sort_desc

        def _by_importance(e: MemoryEntry) -> tuple:
            return (e.importance, overlap.get(e.entry_id, 0.0))

        if query.sort_by == "importance":
            results.sort(key=_by_importance, reverse=True)
        elif query.sort_by == "created_at":
            results.sort(key=lambda e: e.created_at, reverse=reverse)
        elif query.sort_by == "access_count":
            results.sort(key=lambda e: e.access_count, reverse=reverse)
        else:
            results.sort(key=_by_importance, reverse=True)

        # Apply limit
        return results[query.offset:query.offset + query.limit]

    @staticmethod
    def _tokenize(text: str) -> Set[str]:
        """Tokenize text into CJK chars + latin words for overlap matching."""
        import re
        if not text:
            return set()
        text = text.lower()
        cjk = set(re.findall(r"[\u4e00-\u9fff]", text))
        words = set(re.findall(r"[a-z0-9]{2,}", text))
        return cjk | words

    async def soft_delete_entry(
        self, entry_id: str, pod_uri: Optional[str] = None,
    ) -> bool:
        """Move a memory entry to the trash (soft delete)."""
        if pod_uri:
            pods = [pod_uri]
        else:
            pods = ["pod://global", "pod://shared"]
        for pod in pods:
            async with self._store.pod_lock(pod):
                entries = await self._store.read_entries(pod)
                for i, e in enumerate(entries):
                    if e.entry_id != entry_id:
                        continue
                    from boarai.core.trash import get_trash_manager
                    tm = get_trash_manager()
                    if not tm:
                        return False
                    trash_id = await tm.trash_memory(e.to_dict())
                    if not trash_id:
                        return False
                    entries.pop(i)
                    await self._store.write_entries(pod, entries)
                    if self._store.tag_index:
                        await self._store.tag_index.remove(entry_id)
                    return True
        return False

    async def restore_entry(self, entry_dict: Dict[str, Any]) -> bool:
        """Restore a trashed memory entry back to its original pod."""
        try:
            entry = MemoryEntry.from_dict(entry_dict)
        except Exception as e:
            logger.error(f"Failed to parse trashed memory payload: {e}")
            return False
        entry.lifecycle_state = LifecycleState.ACTIVE.value
        await self.remember(entry.pod_uri, entry, publish=False, dedupe=True)
        return True

    async def forget(self, entry_id: str, pod_uri: Optional[str] = None) -> bool:
        """Delete a memory entry by ID."""
        if pod_uri:
            pods = [pod_uri]
        else:
            # Search all standard pods
            pods = ["pod://global", "pod://shared"]

        for pod in pods:
            async with self._store.pod_lock(pod):
                entries = await self._store.read_entries(pod)
                for i, e in enumerate(entries):
                    if e.entry_id == entry_id:
                        entries.pop(i)
                        await self._store.write_entries(pod, entries)
                        break
                else:
                    continue
            # Clean up tag index
            if self._store.tag_index:
                await self._store.tag_index.remove(entry_id)
            if self._event_bus:
                await publish_event(
                    MEMORY_DELETED,
                    {"entry_id": entry_id, "pod_uri": pod},
                    "memory_manager",
                )
            return True
        return False

    async def update_entry_importance(
        self, entry_id: str, new_importance: float,
        pod_uri: Optional[str] = None,
    ) -> bool:
        """Update the importance score of a memory entry."""
        if pod_uri:
            pods = [pod_uri]
        else:
            pods = ["pod://global", "pod://shared"]

        for pod in pods:
            async with self._store.pod_lock(pod):
                entries = await self._store.read_entries(pod)
                for e in entries:
                    if e.entry_id == entry_id:
                        e.importance = max(0.0, min(1.0, new_importance))
                        e.updated_at = datetime.now().isoformat()
                        await self._store.write_entries(pod, entries)
                        return True
        return False

    async def update_entry(
        self,
        entry_id: str,
        pod_uri: Optional[str] = None,
        topic: Optional[str] = None,
        content: Optional[str] = None,
        entry_type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        importance: Optional[float] = None,
    ) -> bool:
        """Update editable fields of a memory entry (in place, concurrency-safe)."""
        if pod_uri:
            pods = [pod_uri]
        else:
            pods = ["pod://global", "pod://shared"]
        for pod in pods:
            async with self._store.pod_lock(pod):
                entries = await self._store.read_entries(pod)
                for e in entries:
                    if e.entry_id != entry_id:
                        continue
                    if topic is not None:
                        e.topic = topic
                    if content is not None:
                        e.content = content
                    if entry_type is not None:
                        e.entry_type = entry_type
                    if importance is not None:
                        e.importance = max(0.0, min(1.0, importance))
                    if tags is not None:
                        if self._store.tag_index:
                            await self._store.tag_index.remove(entry_id)
                        e.tags = list(tags)
                        if self._store.tag_index and tags:
                            await self._store.tag_index.add(entry_id, tags)
                    e.updated_at = datetime.now().isoformat()
                    await self._store.write_entries(pod, entries)
                    return True
        return False

    # ==================== Synapse CRUD ====================

    def _synapses_path(self) -> Path:
        return self.boarai_dir / "memory" / "synapses" / "index.json"

    async def _read_synapses(self) -> List[MemorySynapse]:
        """Read all synapses from disk (lock-guarded against atomic writes)."""
        async with self._synapse_lock:
            return await self._read_synapses_unlocked()

    async def _read_synapses_unlocked(self) -> List[MemorySynapse]:
        """Read all synapses without taking ``_synapse_lock``."""
        path = self._synapses_path()
        if not path.exists():
            return []
        try:
            import aiofiles
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                content = await f.read()
            if not content.strip():
                return []
            data = json.loads(content)
            return [MemorySynapse.from_dict(s) for s in data]
        except Exception as e:
            logger.error(f"Failed to read synapses: {e}")
            return []

    async def _write_synapses_unlocked(self, synapses: List[MemorySynapse]) -> None:
        """Write synapses atomically WITHOUT taking ``_synapse_lock``."""
        from ..runtime import write_text_atomic_async

        path = self._synapses_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = [s.to_dict() for s in synapses]
        await write_text_atomic_async(
            path, json.dumps(data, ensure_ascii=False, indent=2),
        )
        self._synapse_cache_dirty = True

    async def _write_synapses(self, synapses: List[MemorySynapse]) -> None:
        """Write synapses atomically (copy-erase-replace), lock-guarded."""
        async with self._synapse_lock:
            await self._write_synapses_unlocked(synapses)

    async def create_synapse(
        self,
        source_pod_uri: str,
        target_context: str,
        label: str = "",
        weight: float = 0.5,
        decay_rate: float = 0.02,
        filters: Optional[Dict] = None,
        ttl: Optional[int] = None,
    ) -> MemorySynapse:
        """Create a synapse — a mount pointer from context to a pod."""
        synapse = MemorySynapse(
            source_pod_uri=source_pod_uri,
            target_context=target_context,
            label=label or source_pod_uri,
            weight=weight,
            decay_rate=decay_rate,
            filters=filters or {},
            ttl=ttl,
        )
        # Read-modify-write inside one lock: prevents lost updates when many
        # sessions auto-mount concurrently (the previous read-then-write split
        # let two creators both append to a stale list, dropping one).
        async with self._synapse_lock:
            synapses = await self._read_synapses_unlocked()
            synapses.append(synapse)
            await self._write_synapses_unlocked(synapses)

        if self._event_bus:
            await publish_event(
                MEMORY_SYNAPSE_CREATED,
                synapse.to_dict(),
                "memory_manager",
            )
        return synapse

    async def remove_synapse(self, synapse_id: str) -> bool:
        """Remove a synapse by ID."""
        async with self._synapse_lock:
            synapses = await self._read_synapses_unlocked()
            removed = None
            for i, s in enumerate(synapses):
                if s.synapse_id == synapse_id:
                    removed = synapses.pop(i)
                    await self._write_synapses_unlocked(synapses)
                    break
        if removed is None:
            return False
        if self._event_bus:
            await publish_event(
                MEMORY_SYNAPSE_REMOVED,
                {"synapse_id": synapse_id, "source_pod_uri": removed.source_pod_uri},
                "memory_manager",
            )
        return True

    async def list_synapses(self, context: Optional[str] = None) -> List[MemorySynapse]:
        """List all synapses, optionally filtered by target context."""
        synapses = await self._read_synapses()
        if context:
            return [s for s in synapses if s.target_context == context]
        return synapses

    async def get_active_synapses(
        self, context: str, turns_elapsed: int = 0
    ) -> List[MemorySynapse]:
        """Get all alive synapses for a context."""
        synapses = await self._read_synapses()
        active = []
        for s in synapses:
            if s.target_context != context:
                continue
            if not s.is_alive(turns_elapsed):
                continue
            active.append(s)
        return active

    async def mount_pod(
        self,
        pod_uri: str,
        context: str = "",
        weight: float = 0.5,
        filters: Optional[Dict] = None,
        ttl: Optional[int] = None,
    ) -> MemorySynapse:
        """Convenience: create a synapse to mount a pod."""
        _, pid = self._store.parse_pod_uri(pod_uri)
        label = f"{pid}"
        return await self.create_synapse(
            source_pod_uri=pod_uri,
            target_context=context,
            label=label,
            weight=weight,
            filters=filters,
            ttl=ttl,
        )

    async def on_session_start(
        self,
        session_id: str,
        agent_name: str = "default",
        context: Optional[str] = None,
    ) -> None:
        """Session hook: guarantee the context mounts the global and agent pods."""
        await self.ensure_default_mounts(
            context or f"session:{session_id}", agent_name,
        )

    async def on_session_end(
        self,
        session_id: str,
        agent_name: str = "default",
    ) -> None:
        """Session hook: release the frozen weave context of a finished session."""
        try:
            from .weaver import get_memory_weaver

            get_memory_weaver().release_context(f"session:{session_id}")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"weaver context release skipped for {session_id}: {e}")

    async def ensure_default_mounts(
        self, context: str, agent_name: str = "default",
    ) -> bool:
        """Ensure a context has mounts to global + its agent pod (idempotent)."""
        if not context:
            return False
        if context in self._mounts_checked:
            return False
        synapses = await self._read_synapses()
        mounted = {
            s.source_pod_uri
            for s in synapses
            if s.target_context == context and s.is_alive(0)
        }
        created = False
        for pod_uri, label in [
            ("pod://global", "auto:global"),
            (f"pod://agent/{agent_name}", f"auto:agent:{agent_name}"),
        ]:
            if pod_uri in mounted:
                continue
            try:
                await self.create_synapse(
                    source_pod_uri=pod_uri,
                    target_context=context,
                    label=label,
                    weight=0.5,
                    decay_rate=0.0,  # Never decays — auto mounts stay alive
                )
                created = True
            except Exception as e:
                logger.debug(f"Auto-mount failed for {pod_uri} → {context}: {e}")
        # Only remember the context was ensured if the mounts actually exist
        # or were created; otherwise a failed attempt would permanently skip
        # future auto-mounting for this session.
        if mounted or created:
            self._mounts_checked.add(context)
        return created

    async def preserve_session_memory(
        self,
        session_id: str,
        agent_name: str = "default",
        min_importance: float = 0.6,
    ) -> int:
        """Migrate a session's valuable memories to global before it is deleted."""
        pod_uri = f"pod://session/{session_id}"
        try:
            entries = await self._store.read_entries(pod_uri)
        except Exception as e:
            logger.debug(f"preserve_session_memory read failed for {session_id}: {e}")
            return 0
        if not entries:
            return 0
        migrated = 0
        for e in entries:
            if e.lifecycle_state != LifecycleState.ACTIVE.value:
                continue
            if e.importance < min_importance:
                continue
            meta = dict(e.metadata)
            meta.setdefault("source_session", session_id)
            meta.setdefault("source_agent", e.source_agent or agent_name)
            meta.setdefault("preserved_at", datetime.now().isoformat())
            preserved = MemoryEntry(
                pod_uri="pod://global",
                topic=e.topic,
                content=e.content,
                entry_type=e.entry_type,
                tags=list(set(e.tags + ["preserved_from_session"])),
                importance=e.importance,
                metadata=meta,
                source_session=session_id,
                source_agent=e.source_agent or agent_name,
            )
            try:
                await self.remember(
                    "pod://global", preserved, publish=False, dedupe=True,
                )
                migrated += 1
            except Exception as e2:
                logger.debug(f"preserve entry failed for {e.entry_id}: {e2}")
        if migrated:
            logger.info(
                "Preserved %s memory entries from session %s → global",
                migrated, session_id,
            )
        return migrated

    async def unmount_pod(self, pod_uri: str, context: str = "") -> bool:
        """Remove synapses matching a pod URI and optionally a context."""
        synapses = await self._read_synapses()
        removed = False
        new_synapses = []
        for s in synapses:
            condition = s.source_pod_uri == pod_uri
            if context:
                condition = condition and s.target_context == context
            if condition:
                removed = True
            else:
                new_synapses.append(s)
        if removed:
            await self._write_synapses(new_synapses)
            # Auto-mount caches may now be stale — re-check on next ensure
            self._mounts_checked.clear()
        return removed

    # ==================== Orphan Synapse Cleanup ====================

    async def cleanup_orphaned_synapses(self) -> int:
        """Atomically remove all synapses whose source pod no longer exists."""
        async with self._synapse_lock:
            path = self._synapses_path()
            if not path.exists():
                return 0

            import aiofiles
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                content = await f.read()
            if not content.strip():
                return 0
            data = json.loads(content)

            original_count = len(data)
            alive = []
            for s in data:
                source_pod = s.get("source_pod_uri", "")
                pod_exists = await self._store.pod_file_exists(source_pod)
                if pod_exists:
                    alive.append(s)
                elif self._event_bus:
                    await publish_event(
                        MEMORY_SYNAPSE_ORPHANED,
                        {
                            "synapse_id": s.get("synapse_id", "?"),
                            "source_pod_uri": source_pod,
                            "target_context": s.get("target_context", "?"),
                        },
                        "memory_manager",
                    )

            if len(alive) != original_count:
                from ..runtime import write_text_atomic_async

                await write_text_atomic_async(
                    path, json.dumps(alive, ensure_ascii=False, indent=2),
                )
                self._synapse_cache_dirty = True

            return original_count - len(alive)

    # ==================== Agent Deletion ====================

    async def on_agent_deleted(
        self, agent_name: str, preserve_memory: bool = True
    ) -> Dict[str, Any]:
        """Handle agent deletion with memory preservation."""
        agent_pod = f"pod://agent/{agent_name}"
        report = {
            "agent_name": agent_name,
            "synapses_redirected": 0,
            "synapses_disconnected": 0,
        }

        # 1. Protect synapses
        synapses = await self._read_synapses()
        redirected = 0
        disconnected = 0
        new_synapses = []
        for s in synapses:
            if s.source_pod_uri == agent_pod:
                if s.current_weight() > 0.5:
                    # High-weight synapse: redirect to global
                    s.source_pod_uri = "pod://global"
                    new_synapses.append(s)
                    redirected += 1
                    if self._event_bus:
                        await publish_event(
                            MEMORY_SYNAPSE_REDIRECTED,
                            {"synapse_id": s.synapse_id, "from": agent_pod,
                             "to": "pod://global"},
                            "memory_manager",
                        )
                else:
                    disconnected += 1
            else:
                new_synapses.append(s)

        await self._write_synapses(new_synapses)
        report["synapses_redirected"] = redirected
        report["synapses_disconnected"] = disconnected

        # 2. Downgrade memory
        if preserve_memory:
            agent_memory_path = self._store.resolve_pod_path(agent_pod)
            if agent_memory_path and agent_memory_path.exists():
                retired_dir = self.boarai_dir / "memory" / "retired_agents"
                retired_dir.mkdir(parents=True, exist_ok=True)
                dest = retired_dir / (
                    f"{agent_name}_{datetime.now().strftime('%Y%m%d')}.json"
                )
                await asyncio.to_thread(shutil.copy2, str(agent_memory_path), str(dest))
                # Remove original
                await asyncio.to_thread(agent_memory_path.unlink)
                report["memory_preserved_to"] = str(dest)

        if self._event_bus:
            await publish_event(MEMORY_POD_RECYCLED, report, "memory_manager")

        return report

    # ==================== Summon Retired ====================

    async def summon_retired(
        self, agent_name: str, query: MemoryQuery
    ) -> List[Dict[str, Any]]:
        """Query a retired agent's memory. Results carry a watermark."""
        retired_dir = self.boarai_dir / "memory" / "retired_agents"
        if not retired_dir.exists():
            return []

        # Find the most recent retired file for this agent
        files = sorted(retired_dir.glob(f"{agent_name}_*.json"), reverse=True)
        if not files:
            return []

        path = files[0]
        try:
            import aiofiles
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                content = await f.read()
            if not content.strip():
                return []
            data = json.loads(content)
            entries = (
                [MemoryEntry.from_dict(e) for e in data]
                if isinstance(data, list) else []
            )
        except Exception as e:
            logger.error(f"Failed to load retired agent {agent_name}: {e}")
            return []

        # Apply query filters (simple keyword + importance)
        results = []
        for entry in entries:
            if entry.importance < query.min_importance:
                continue
            if query.text:
                text = query.text.lower()
                if text not in entry.topic.lower() and text not in entry.content.lower():
                    continue
            if query.tags and not any(t in entry.tags for t in query.tags):
                continue
            results.append(entry)

        # Sort by importance
        results.sort(key=lambda e: e.importance, reverse=True)
        results = results[:query.limit]

        # Watermark
        output = []
        for r in results:
            d = r.to_dict()
            d["metadata"] = d.get("metadata", {})
            d["metadata"]["summoned_from"] = f"retired_agent:{agent_name}"
            d["metadata"]["summon_warning"] = (
                f"以下知识来自已退役智能体 '{agent_name}'，可能存在过时风险"
            )
            output.append(d)

        return output

    # ==================== Index Rebuild & Export ====================

    async def rebuild_tag_index(self) -> int:
        """Rebuild the inverted tag index from on-disk pods."""
        if not self._store.tag_index:
            return 0
        await self._store.tag_index.clear()
        pods = ["pod://global", "pod://shared"]
        for base, prefix in [
            (self.boarai_dir / "sessions", "pod://session"),
            (self.boarai_dir / "agents", "pod://agent"),
        ]:
            if base.is_dir():
                for name in sorted(os.listdir(str(base))):
                    if not (base / name).is_dir():
                        continue
                    pods.append(f"{prefix}/{name}")
        indexed = 0
        for pod in pods:
            try:
                for e in await self._store.read_entries(pod):
                    if e.tags:
                        await self._store.tag_index.add(e.entry_id, e.tags)
                        indexed += 1
            except Exception as e:
                logger.debug(f"Index rebuild skip pod {pod}: {e}")
        logger.info(
            "TagIndex rebuilt from %s pods (%s entries indexed)",
            len(pods), indexed,
        )
        return indexed

    async def export_pods(self) -> Dict[str, Any]:
        """Export all memory pods (global/shared/session/agent/skill) as JSON."""
        result: Dict[str, Any] = {}
        for pod_uri in ["pod://global"]:
            entries = await self._store.read_entries(pod_uri)
            if entries:
                result[pod_uri] = [e.to_dict() for e in entries]
        shared_dir = self.boarai_dir / "memory" / "shared"
        if shared_dir.is_dir():
            for f in sorted(shared_dir.glob("*.json")):
                pod_uri = f"pod://shared/{f.stem}"
                entries = await self._store.read_entries(pod_uri)
                if entries:
                    result[pod_uri] = [e.to_dict() for e in entries]
        for base, prefix in [
            (self.boarai_dir / "sessions", "pod://session"),
            (self.boarai_dir / "agents", "pod://agent"),
        ]:
            if base.is_dir():
                for name in sorted(os.listdir(str(base))):
                    if not (base / name).is_dir():
                        continue
                    pod_uri = f"{prefix}/{name}"
                    entries = await self._store.read_entries(pod_uri)
                    if entries:
                        result[pod_uri] = [e.to_dict() for e in entries]
        # Include synapses (mount graph) so a full backup can rebuild mounts
        synapses = await self._read_synapses()
        if synapses:
            result["_synapses"] = [s.to_dict() for s in synapses]
        # Include staging entries (pending distillations)
        staging_dir = self.boarai_dir / "memory" / "staging"
        if staging_dir.is_dir():
            for f in sorted(staging_dir.glob("*.json")):
                try:
                    content = await asyncio.to_thread(f.read_text, encoding="utf-8")
                    result[f"staging/{f.stem}"] = json.loads(content)
                except Exception as e:
                    logger.debug(f"Export skip staging file {f}: {e}")
        return result

    # ==================== Lifecycle ====================

    async def start(self) -> None:
        """Start the provider: subscribe to events, load index, start workers."""
        if not self._event_bus:
            self._event_bus = get_event_bus()

        # Subscribe to pattern resonance events for skill forging
        async def on_resonance(event):
            await self._forge_queue.put(event.payload)

        await self._event_bus.subscribe(PATTERN_RESONANCE, on_resonance)
        self._event_handlers.append((PATTERN_RESONANCE, on_resonance))

        # Start skill forge consumer
        self._forge_task = asyncio.create_task(self._forge_consumer())
        logger.info("SynapticMeshProvider started (forge consumer active)")

        # Load tag index
        from .tag_index import TagIndex
        index_path = self.boarai_dir / "memory" / "index.json"
        self._store.tag_index = TagIndex(index_path)
        await self._store.tag_index.load()
        logger.info("TagIndex loaded")

        # Auto-confirm staging + clean expired on startup
        await self._auto_confirm_staging()

        # Rebuild the tag index from on-disk pods (repairs stale/dangling index)
        await self.rebuild_tag_index()

        # Start distillation worker (idle-time LLM distillation of trashed
        # sessions/agents)
        from .distill_worker import DistillWorker
        self._distill_worker = DistillWorker(self)
        self._distill_worker.set_llm(self._llm_service)
        await self._distill_worker.start()
        logger.info("DistillWorker started")

    async def shutdown(self) -> None:
        """Stop background tasks (forge consumer + distill worker)."""
        if self._distill_worker:
            await self._distill_worker.stop()
            self._distill_worker = None
        if self._forge_task:
            self._forge_task.cancel()
            self._forge_task = None
        logger.info("SynapticMeshProvider stopped")

    # ==================== Skill Forge Consumer ====================

    async def _forge_consumer(self) -> None:
        """Background consumer: processes pattern resonance forging requests."""
        from .reincarnation import ReincarnationEngine
        engine = ReincarnationEngine(self)

        while True:
            try:
                payload = await asyncio.wait_for(
                    self._forge_queue.get(), timeout=266.0,
                )
                # Cold path: forge the skill
                result = await engine.forge_skill(payload)
                if result:
                    logger.info(f"[ForgeConsumer] Skill forging completed: {result}")
            except asyncio.TimeoutError:
                # Idle timeout — forge consumer keeps listening
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[ForgeConsumer] Forge consumer failed: {e}", exc_info=True)

    # ==================== Lazy service injection ====================

    def set_llm_service(self, service: Any) -> None:
        """Inject LLM service for distillation (lazy, may be None)."""
        self._llm_service = service

    def set_session_manager(self, mgr: Any) -> None:
        """Inject session manager for auto-extraction."""
        self._session_manager = mgr

    # ==================== Stats ====================

    async def get_stats(self) -> MemoryStats:
        """Get memory system statistics."""
        stats = MemoryStats()

        # Count global entries
        global_entries = await self._store.read_entries("pod://global")
        stats.total_entries = len(global_entries)

        # Count synapses
        synapses = await self._read_synapses()
        stats.total_synapses = len(synapses)

        # Count by type
        type_counts: Dict[str, int] = {}
        for e in global_entries:
            t = e.entry_type
            type_counts[t] = type_counts.get(t, 0) + 1
        stats.entries_by_type = type_counts

        # Count staging
        staging_path = self.boarai_dir / "memory" / "staging" / "global_insights.json"
        if staging_path.exists():
            try:
                import aiofiles
                async with aiofiles.open(staging_path, "r", encoding="utf-8") as f:
                    staging_data = json.loads(await f.read())
                stats.pending_reincarnations = len(staging_data.get("insights", []))
            except Exception:
                pass

        # Count retired agents
        retired_dir = self.boarai_dir / "memory" / "retired_agents"
        if retired_dir.exists():
            stats.retired_agents = len(list(retired_dir.glob("*.json")))

        return stats


__all__ = ["SynapticMeshProvider"]
