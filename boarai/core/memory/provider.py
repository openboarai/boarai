"""Replaceable contract for a memory retrieval backend.

The memory domain keeps its biological vocabulary (pod, synapse, weave,
reincarnation), but retrieval used to live in one concrete class,
``MemoryManager``: the four engines (weaver, consolidation, reincarnation,
distill worker) and the API layer all depended on its method set, so swapping
the retrieval implementation meant rewriting every call site.

``MemoryProvider`` turns that layer into an explicit contract:

* Six methods **must** be implemented: store, recall, forget, list, plus the
  two session lifecycle hooks every backend has to answer.
* Lifecycle hooks ship with **default bodies**, so a stateless backend (a
  future sqlite-FTS or embedding provider) needs no empty overrides.

``MemoryManager`` is now an orchestrator only: it resolves the configured
provider, forwards calls and drives the lifecycle. ``SynapticMeshProvider`` is
the first implementation and carries the entire original pod + synapse +
token-overlap behaviour without losing a feature.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from .pod_store import PodStore
from .types import MemoryEntry, MemoryQuery

logger = logging.getLogger(__name__)


class MemoryProvider(ABC):
    """Contract every memory retrieval backend implements."""

    name: str = "abstract"

    @property
    @abstractmethod
    def pod_store(self) -> PodStore:
        """Entry storage facet: path resolution, atomic IO, per-pod locks, parse cache."""

    # --- Entry-level contract ---

    @abstractmethod
    async def remember(
        self,
        pod_uri: str,
        entry: MemoryEntry,
        publish: bool = True,
        dedupe: bool = True,
    ) -> str:
        """Store one memory entry."""

    @abstractmethod
    async def recall(
        self,
        query: MemoryQuery,
        pod_uris: Optional[List[str]] = None,
    ) -> List[MemoryEntry]:
        """Retrieve entries already ordered and paginated per ``query``."""

    @abstractmethod
    async def forget(self, entry_id: str, pod_uri: Optional[str] = None) -> bool:
        """Delete one entry by id; ``pod_uri=None`` searches the standard pods."""

    @abstractmethod
    async def list_entries(self, pod_uri: str) -> List[MemoryEntry]:
        """List every entry stored in one pod."""

    # --- Lifecycle hooks (default no-ops) ---

    async def on_session_start(
        self,
        session_id: str,
        agent_name: str = "default",
        context: Optional[str] = None,
    ) -> None:
        """Mount the memories visible to a starting session. No-op by default."""

    async def on_session_end(
        self,
        session_id: str,
        agent_name: str = "default",
    ) -> None:
        """Unmount or flush a finished session's memories. No-op by default."""

    async def start(self) -> None:
        """Start background tasks and indexes. No-op by default."""

    async def shutdown(self) -> None:
        """Release background tasks and resources. No-op by default."""

    # --- Lazy service injection (filled in by the orchestrator) ---

    def set_llm_service(self, service: Any) -> None:
        """Accept the LLM service used by distillation and forging. Ignored by default."""

    def set_session_manager(self, mgr: Any) -> None:
        """Accept the session manager used by auto-extraction. Ignored by default."""


# --- Registry ---

_PROVIDER_FACTORIES: Dict[str, Callable[..., MemoryProvider]] = {}


def register_provider(name: str, factory: Callable[..., MemoryProvider]) -> None:
    """Register a provider factory under ``name``."""
    _PROVIDER_FACTORIES[name] = factory


def available_providers() -> List[str]:
    """Registered provider names in stable sorted order, for config checks and diagnostics."""
    _register_builtins()
    return sorted(_PROVIDER_FACTORIES)


def create_provider(name: str, **kwargs: Any) -> MemoryProvider:
    """Build a provider by name."""
    _register_builtins()
    try:
        factory = _PROVIDER_FACTORIES[name]
    except KeyError:
        raise KeyError(
            f"unknown memory provider {name!r}; available: {available_providers()}"
        ) from None
    return factory(**kwargs)


def _register_builtins() -> None:
    """Register the built-in providers exactly once."""
    if "synaptic_mesh" in _PROVIDER_FACTORIES:
        return
    from .synaptic_mesh import SynapticMeshProvider

    register_provider("synaptic_mesh", SynapticMeshProvider)


__all__ = [
    "MemoryProvider",
    "available_providers",
    "create_provider",
    "register_provider",
]
