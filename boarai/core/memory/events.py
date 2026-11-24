"""
Event constants for BoarAi memory system.

These are registered in core/event/types.py as EventType enum members.
"""

# Memory lifecycle events
MEMORY_STORED = "memory.stored"
MEMORY_DELETED = "memory.deleted"
MEMORY_ACCESSED = "memory.accessed"

# Synapse events
MEMORY_SYNAPSE_CREATED = "memory.synapse.created"
MEMORY_SYNAPSE_REMOVED = "memory.synapse.removed"
MEMORY_SYNAPSE_ORPHANED = "memory.synapse.orphaned"
MEMORY_SYNAPSE_REDIRECTED = "memory.synapse.redirected"

# Consolidation events
MEMORY_CONSOLIDATION_DONE = "memory.consolidation.done"
MEMORY_INSIGHT_GENERATED = "memory.insight.generated"

# Reincarnation events
MEMORY_POD_RECYCLED = "memory.pod.recycled"
MEMORY_POD_MARKED = "memory.pod.marked"  # Phase 1 of Mark+Reap
MEMORY_REAP_DONE = "memory.reap.done"

# Prompt assembly hook
PROMPT_ZONE3_ASSEMBLING = "prompt.zone3.assembling"

# Skill forge event (autonomous skill creation via pattern resonance)
PATTERN_RESONANCE = "memory.skill.forge_request"

MEMORY_EVENTS = [
    MEMORY_STORED, MEMORY_DELETED, MEMORY_ACCESSED,
    MEMORY_SYNAPSE_CREATED, MEMORY_SYNAPSE_REMOVED,
    MEMORY_SYNAPSE_ORPHANED, MEMORY_SYNAPSE_REDIRECTED,
    MEMORY_CONSOLIDATION_DONE, MEMORY_INSIGHT_GENERATED,
    MEMORY_POD_RECYCLED, MEMORY_POD_MARKED, MEMORY_REAP_DONE,
    PROMPT_ZONE3_ASSEMBLING, PATTERN_RESONANCE,
]
