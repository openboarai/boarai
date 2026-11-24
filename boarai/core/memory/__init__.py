"""Synaptic memory mesh with consolidation, reincarnation and skill forging.

Layering: ``MemoryProvider`` (replaceable retrieval contract) ->
``SynapticMeshProvider`` (pod + synapse + token-overlap implementation) ->
``MemoryManager`` (orchestrator: backend selection, call forwarding, lifecycle).
``PodStore`` is the storage facet that the engines depend on instead of a
concrete backend.
"""

from .consolidation import ConsolidationEngine
from .manager import (
    MemoryConfig,
    MemoryManager,
    get_memory_manager,
    reset_memory_manager,
)
from .pod_store import FilePodStore, PodStore
from .provider import (
    MemoryProvider,
    available_providers,
    create_provider,
    register_provider,
)
from .synaptic_mesh import SynapticMeshProvider
from .weaver import (
    MemoryWeaver,
    get_memory_weaver,
)
from .types import (
    MemoryQuery,
)
from .reincarnation import (
    ReincarnationEngine,
)

__all__ = [
    "ConsolidationEngine",
    "FilePodStore",
    "MemoryConfig",
    "MemoryManager",
    "MemoryProvider",
    "MemoryQuery",
    "MemoryWeaver",
    "PodStore",
    "ReincarnationEngine",
    "SynapticMeshProvider",
    "available_providers",
    "create_provider",
    "get_memory_manager",
    "get_memory_weaver",
    "register_provider",
    "reset_memory_manager",
]
