"""Orchestrator of the memory subsystem.

This class no longer implements memory: retrieval is defined by the
``MemoryProvider`` contract, currently backed by ``SynapticMeshProvider``
(pods, synapses, token-overlap scoring). Its responsibilities are:

1. **Backend selection** - build a provider from the registry using
   ``MemoryConfig.provider``; an unknown name fails loudly instead of falling
   back, because silently switching backends hides existing data.
2. **Call forwarding** - the four engines (weaver, consolidation, reincarnation,
   distill worker), the API layer and the session lifecycle all depend on this
   method set, and forwarding keeps them backend-agnostic.
3. **Lifecycle** - ``start``/``stop`` drive the provider and inject the lazily
   resolved LLM service and session manager.

Storage mechanics (path resolution, atomic writes, per-pod locks, parse cache)
live in the ``PodStore`` facet exposed as ``pod_store``; engines depend on that
facet, never on a concrete store class.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..event import EventBus
from .pod_store import PodStore
from .provider import MemoryProvider, available_providers, create_provider
from .types import MemoryEntry, MemoryQuery, MemoryStats, MemorySynapse

logger = logging.getLogger(__name__)


@dataclass
class MemoryConfig:
    """Memory system configuration."""
    enabled: bool = True
    provider: str = "synaptic_mesh"
    """Retrieval backend name; must exist in ``available_providers()``.

    Leaves room for a sqlite-FTS or embedding backend later without touching a
    single call site.
    """
    default_weight: float = 0.5
    default_decay_rate: float = 0.02
    max_entries_per_pod: int = 10000
    staging_expiry_days: int = 7
    synapse_dead_threshold: float = 0.05
    max_entries_per_synapse: int = 10
    weave_token_budget: int = 2000
    cooldown_turns: int = 5
    proactive_threshold: float = 0.9


class MemoryManager:
    """Singleton orchestrator of the memory subsystem."""

    def __init__(
        self,
        project_root: Optional[Path] = None,
        event_bus: Optional[EventBus] = None,
        config: Optional[MemoryConfig] = None,
        data_dir: Optional[Path] = None,
        provider: Optional[MemoryProvider] = None,
    ):
        if data_dir is None:
            from boarai.core.runtime import resolve_data_dir
            data_dir = resolve_data_dir(project_root)
        self._project_root = project_root
        self._event_bus = event_bus
        self._config = config or MemoryConfig()
        # An explicitly injected provider wins (tests and plugins); otherwise
        # the configured name is resolved through the provider registry.
        self._provider: MemoryProvider = provider or create_provider(
            self._config.provider,
            data_dir=data_dir,
            event_bus=event_bus,
            config=self._config,
            project_root=project_root,
        )
        self._llm_service = None
        self._session_manager = None

    # --- Provider access ---

    @property
    def provider(self) -> MemoryProvider:
        """The active retrieval backend."""
        return self._provider

    @property
    def provider_name(self) -> str:
        """Name of the active backend, for diagnostics and config echo."""
        return getattr(self._provider, "name", self._config.provider)

    @property
    def pod_store(self) -> PodStore:
        """Storage facet for memory entries."""
        return self._provider.pod_store

    # Engines read thresholds through ``self._manager._config`` (consolidation);
    # that attribute belongs to the orchestrator, not to a backend.

    # --- Entry CRUD (forwarded) ---

    async def list_entries(self, pod_uri: str) -> List[MemoryEntry]:
        """List every entry in one pod."""
        return await self._provider.list_entries(pod_uri)

    async def remember(
        self,
        pod_uri: str,
        entry: MemoryEntry,
        publish: bool = True,
        dedupe: bool = True,
    ) -> str:
        """Persist one memory entry."""
        return await self._provider.remember(
            pod_uri, entry, publish=publish, dedupe=dedupe,
        )

    async def recall(
        self,
        query: MemoryQuery,
        pod_uris: Optional[List[str]] = None,
    ) -> List[MemoryEntry]:
        """Query entries, optionally restricted to a set of pods."""
        return await self._provider.recall(query, pod_uris)

    async def forget(self, entry_id: str, pod_uri: Optional[str] = None) -> bool:
        """Delete one entry by id."""
        return await self._provider.forget(entry_id, pod_uri)

    async def soft_delete_entry(
        self, entry_id: str, pod_uri: Optional[str] = None,
    ) -> bool:
        """Move an entry to the recycle bin, then drop it from the pod."""
        return await self._provider.soft_delete_entry(entry_id, pod_uri)

    async def restore_entry(self, entry_dict: Dict[str, Any]) -> bool:
        """Restore a trashed entry into its original pod."""
        return await self._provider.restore_entry(entry_dict)

    async def update_entry_importance(
        self, entry_id: str, new_importance: float,
        pod_uri: Optional[str] = None,
    ) -> bool:
        """Change an entry's importance score."""
        return await self._provider.update_entry_importance(
            entry_id, new_importance, pod_uri,
        )

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
        """Edit an entry in place; ``None`` leaves a field unchanged."""
        return await self._provider.update_entry(
            entry_id,
            pod_uri=pod_uri,
            topic=topic,
            content=content,
            entry_type=entry_type,
            tags=tags,
            importance=importance,
        )

    # --- Synapses (forwarded) ---

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
        """Create a synapse: a mount pointer from a context to a pod."""
        return await self._provider.create_synapse(
            source_pod_uri, target_context, label=label, weight=weight,
            decay_rate=decay_rate, filters=filters, ttl=ttl,
        )

    async def remove_synapse(self, synapse_id: str) -> bool:
        """Delete one synapse by id."""
        return await self._provider.remove_synapse(synapse_id)

    async def list_synapses(
        self, context: Optional[str] = None,
    ) -> List[MemorySynapse]:
        """List synapses, optionally filtered by target context."""
        return await self._provider.list_synapses(context)

    async def get_active_synapses(
        self, context: str, turns_elapsed: int = 0,
    ) -> List[MemorySynapse]:
        """List synapses still alive for a context; the weaver's entry point."""
        return await self._provider.get_active_synapses(context, turns_elapsed)

    async def mount_pod(
        self,
        pod_uri: str,
        context: str = "",
        weight: float = 0.5,
        filters: Optional[Dict] = None,
        ttl: Optional[int] = None,
    ) -> MemorySynapse:
        """Mount a pod on a context; convenience wrapper over create_synapse."""
        return await self._provider.mount_pod(
            pod_uri, context, weight=weight, filters=filters, ttl=ttl,
        )

    async def unmount_pod(self, pod_uri: str, context: str = "") -> bool:
        """Remove the synapse that points at a pod."""
        return await self._provider.unmount_pod(pod_uri, context)

    async def ensure_default_mounts(
        self, context: str, agent_name: str = "default",
    ) -> bool:
        """Make sure a context mounts the global and agent pods (idempotent)."""
        return await self._provider.ensure_default_mounts(context, agent_name)

    async def cleanup_orphaned_synapses(self) -> int:
        """Drop synapses whose source pod no longer exists."""
        return await self._provider.cleanup_orphaned_synapses()

    # --- Lifecycle and session hooks ---

    async def on_session_start(
        self,
        session_id: str,
        agent_name: str = "default",
        context: Optional[str] = None,
    ) -> None:
        """Session hook: mount the memories this session is allowed to see."""
        await self._provider.on_session_start(session_id, agent_name, context)

    async def on_session_end(
        self,
        session_id: str,
        agent_name: str = "default",
    ) -> None:
        """Session hook: release the memory context held for that session."""
        await self._provider.on_session_end(session_id, agent_name)

    async def preserve_session_memory(
        self,
        session_id: str,
        agent_name: str = "default",
        min_importance: float = 0.6,
    ) -> int:
        """Promote valuable memories to global before a session is deleted."""
        return await self._provider.preserve_session_memory(
            session_id, agent_name, min_importance,
        )

    async def on_agent_deleted(
        self, agent_name: str, preserve_memory: bool = True,
    ) -> Dict[str, Any]:
        """Retire an agent: keep its synapses safe and demote memories to cold storage."""
        return await self._provider.on_agent_deleted(agent_name, preserve_memory)

    async def summon_retired(
        self, agent_name: str, query: MemoryQuery,
    ) -> List[Dict[str, Any]]:
        """Query a retired agent's memories; results carry a staleness watermark."""
        return await self._provider.summon_retired(agent_name, query)

    # --- Index and export ---

    async def rebuild_tag_index(self) -> int:
        """Rebuild the inverted tag index from the pods on disk."""
        return await self._provider.rebuild_tag_index()

    async def export_pods(self) -> Dict[str, Any]:
        """Export every pod, synapse and staging record for the backup API."""
        return await self._provider.export_pods()

    async def remember_batch(
        self, pod_uri: str, entries: List[MemoryEntry],
    ) -> List[str]:
        """Persist several entries at once."""
        return await self._provider.remember_batch(pod_uri, entries)

    async def get_stats(self) -> MemoryStats:
        """Return aggregate memory statistics."""
        return await self._provider.get_stats()

    # --- Lifecycle ---

    async def start(self) -> None:
        """Start the subsystem by delegating to the provider."""
        if not self._config.enabled:
            logger.info("MemoryManager disabled by config; provider not started")
            return
        self._provider.set_llm_service(self._llm_service)
        self._provider.set_session_manager(self._session_manager)
        await self._provider.start()
        logger.info("MemoryManager started (provider=%s)", self.provider_name)

    async def stop(self) -> None:
        """Stop the subsystem and cancel the provider's background tasks."""
        await self._provider.shutdown()
        logger.info("MemoryManager stopped")

    # --- Lazy service injection ---

    def set_llm_service(self, service: Any) -> None:
        """Inject LLM service for distillation (lazy, may be None)."""
        self._llm_service = service
        self._provider.set_llm_service(service)

    def set_session_manager(self, mgr: Any) -> None:
        """Inject session manager for auto-extraction."""
        self._session_manager = mgr
        self._provider.set_session_manager(mgr)


# --- Global singleton ---

_memory_manager: Optional[MemoryManager] = None


def get_memory_manager(
    project_root: Optional[Path] = None,
    event_bus: Optional[EventBus] = None,
    config: Optional[MemoryConfig] = None,
    data_dir: Optional[Path] = None,
) -> MemoryManager:
    """Return the process-wide manager, creating it on first use."""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager(
            project_root=project_root,
            event_bus=event_bus,
            config=config,
            data_dir=data_dir,
        )
    return _memory_manager


def reset_memory_manager() -> None:
    """Drop the singleton (tests and data-dir re-anchoring)."""
    global _memory_manager
    _memory_manager = None


__all__ = [
    "MemoryConfig",
    "MemoryManager",
    "available_providers",
    "get_memory_manager",
    "reset_memory_manager",
]
