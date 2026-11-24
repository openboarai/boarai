> **English** | [简体中文](memory_zh.md)

# Memory System (core/memory)

The synaptic memory mesh: pod storage, synapse retrieval, weaving, consolidation, reincarnation, idle-time distillation and lesson extraction.

## Layering

`MemoryProvider` (replaceable retrieval contract) → `SynapticMeshProvider` (the default pod + synapse implementation) → `MemoryManager` (orchestration facade; it no longer implements retrieval itself). The domain keeps its biological vocabulary: pod, synapse, weave.

## Modules

| File | Responsibility |
|------|----------------|
| `manager.py` | Subsystem orchestration facade (entries such as `soft_delete_entry`) |
| `provider.py` | Replaceable retrieval backend contract |
| `synaptic_mesh.py` | Pod + synapse retrieval backend (where the original memory implementation landed) |
| `pod_store.py` | Pod persistence, path resolution and concurrency control |
| `tag_index.py` | Pure JSON inverted index for semantic retrieval |
| `types.py` | Data types |
| `weaver.py` | `MemoryWeaver` fusion engine |
| `consolidation.py` | `ConsolidationEngine` memory self-evolution |
| `reincarnation.py` | `ReincarnationEngine` memory lifecycle management |
| `distill_worker.py` | Background idle-time distillation from trashed sessions/agents, with retry caps and distillation marks |
| `lesson.py` | Failure-derived lesson extraction: turns run/tool/LLM failures into reusable lesson entries |
| `events.py` | Memory-domain event constants |

## Key contracts

- **Deletion defaults to soft delete**: memory entries go through the recycle bin (30-day TTL); hard deletes are forbidden; distillation consumes trash entries (trash_id threads through), and after the retry cap a `distill_failed` mark is set instead of looping forever.
- The memory event family `memory.stored/deleted/accessed`, `memory.synapse.created/removed/orphaned/redirected`, `memory.consolidation.done`, `memory.insight.generated`, `memory.pod.recycled/marked`, `memory.reap.done`, `memory.skill.forge_request` is fully registered in the event catalogue.
- Background work (distillation/consolidation) runs in idle windows and never inside a conversation turn's hot path.
- Global lesson deduplication belongs to the curator subsystem (see its doc); this module only produces entries.
