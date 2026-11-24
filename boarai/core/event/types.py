"""Canonical event catalogue for the BoarAi event bus.

Every event travelling over the bus must be declared here as a string constant
so that publishers and subscribers never rely on magic strings. WebSocket
mirrors reuse the same value when the wire name is identical; otherwise the
translation lives in the transport layer.
"""

from enum import Enum
from typing import Any, Dict


class EventType(str, Enum):
    """Event types published on the bus, grouped by owning subsystem."""

    # System lifecycle
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"
    SYSTEM_ERROR = "system.error"

    # Legacy plugin aliases, kept for extensions written before the rename
    PLUGIN_LOADED = "plugin.loaded"
    PLUGIN_UNLOADED = "plugin.unloaded"
    PLUGIN_ERROR = "plugin.error"
    PLUGIN_ENABLED = "plugin.enabled"
    PLUGIN_DISABLED = "plugin.disabled"

    # LLM request lifecycle and provider registry
    LLM_REQUEST = "llm.request"
    LLM_RESPONSE = "llm.response"
    LLM_ERROR = "llm.error"
    LLM_PROVIDER_REGISTERED = "llm.provider.registered"
    LLM_PROVIDER_UNREGISTERED = "llm.provider.unregistered"

    # Session lifecycle and message fan-out
    SESSION_CREATED = "session.created"
    SESSION_UPDATED = "session.updated"
    SESSION_DESTROYED = "session.destroyed"
    # Wire alias consumed by WebUI/API broadcasts; same lifecycle as destroyed.
    SESSION_DELETED = "session.deleted"
    SESSION_MESSAGE = "session.message"
    SESSION_MESSAGE_NEW = "session.message.new"
    SESSION_MESSAGES_CLEARED = "session.messages_cleared"
    # Emitted when an edit truncates the message tail; session-subscribed scope.
    SESSION_MESSAGES_TRUNCATED = "session.messages_truncated"
    SESSION_ARCHIVED = "session.archived"
    # The session's file-change log was dropped (user kept the changes) or
    # consumed (revert-all). Session-scoped broadcast so every subscribed
    # client refreshes its changes panel. Payload: session_id, reason
    # ("accepted" | "reverted"), and revert stats when applicable.
    SESSION_CHANGES_CLEARED = "session.changes.cleared"

    # WebUI file manager: a text file was saved through /api/fs/write.
    # Mirrored to every WS client (same path as session.deleted) so other
    # tabs refresh their tree and stale badges. Payload: path, mtime,
    # session_id.
    FS_CHANGED = "fs.changed"

    # Run lifecycle
    RUN_CHUNK = "run.chunk"
    RUN_DONE = "run.done"
    RUN_COMPLETED = "run.completed"
    RUN_ERROR = "run.error"
    RUN_CREATED = "run.created"
    RUN_ASSEMBLY = "run.assembly"
    RUN_CANCELLED = "run.cancelled"
    RUN_RETRY = "run.retry"
    # Aggregated file changes (added/modified/deleted) plus unified diff,
    # emitted once per finished run and mirrored to WS as "run.diff".
    RUN_DIFF = "run.diff"
    # Cumulative token snapshot (turns/input/completion/cache) emitted after
    # each LLM turn so clients can update counters live instead of waiting for
    # the terminal run record. Mirrored to WS as "usage"; never persisted.
    RUN_USAGE = "run.usage"
    # The provider rejected image_url blocks (text-only model). The executor
    # strips the images, retries, and carries reason plus a UI hint. Never
    # persisted.
    RUN_VISION_DOWNGRADE = "run.vision_downgrade"

    # Tool invocation pipeline
    TOOL_REGISTERED = "tool.registered"
    TOOL_UNREGISTERED = "tool.unregistered"
    TOOL_EXECUTED = "tool.executed"
    TOOL_EXECUTING = "tool.executing"
    TOOL_ERROR = "tool.error"
    TOOL_PENDING = "tool.pending"
    TOOL_START = "tool.start"
    TOOL_FINISHED = "tool.finished"
    TOOL_OUTPUT = "tool.output"

    # Configuration
    CONFIG_CHANGED = "config.changed"
    CONFIG_RELOADED = "config.reloaded"

    # WebUI access gate (core.security.access / core.server.gate). Mirrored to
    # every WS client by the serve.py forwarding list: a rotated key or a
    # revoked device must drop its socket instead of serving a dead session.
    # Payload: {reason: "logout" | "session_revoked" | "key_rotated",
    #           session_ids: [...]}.
    AUTH_REVOKED = "auth.revoked"

    # Online update (core.update): mirrored to every WS client by the
    # serve.py forwarding list. available = a newer version was found;
    # progress = worker phase change; done/failed = terminal worker state.
    UPDATE_AVAILABLE = "update.available"
    UPDATE_PROGRESS = "update.progress"
    UPDATE_DONE = "update.done"
    UPDATE_FAILED = "update.failed"

    # Reasoning / extended thinking stream
    THINKING_START = "thinking.start"
    THINKING_DELTA = "thinking.delta"
    THINKING_END = "thinking.end"
    THINKING_SILENCE = "thinking.silence"

    # User channel traffic
    USER_INPUT = "user.input"
    USER_OUTPUT = "user.output"

    # Task lifecycle
    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    TASK_STATUS_CHANGED = "task.status_changed"
    TASK_PROGRESS = "task.progress"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_CANCELLED = "task.cancelled"
    TASK_STEP_COMPLETED = "task.step_completed"
    TASK_SUB_CREATED = "task.sub_created"

    # Extension lifecycle
    EXTENSION_LOADED = "extension.loaded"
    EXTENSION_UNLOADED = "extension.unloaded"
    EXTENSION_ERROR = "extension.error"
    # A discovered extension failed to load (gate/schema/syntax/...). Mirrored
    # to WS clients and surfaced in GET /api/extensions `failed` (dual channel).
    EXTENSION_LOAD_FAILED = "extension.load_failed"
    EXTENSION_ENABLED = "extension.enabled"
    EXTENSION_DISABLED = "extension.disabled"
    EXTENSION_INSTALLED = "extension.installed"
    EXTENSION_UNINSTALLED = "extension.uninstalled"
    EXTENSION_RELOADED = "extension.reloaded"
    EXTENSION_CONFIG_CHANGED = "extension.config_changed"

    # Agent loop and subagent dispatch
    AGENT_ITERATION_START = "agent.iteration_start"
    AGENT_SUBAGENT_START = "agent.subagent_start"
    AGENT_SUBAGENT_END = "agent.subagent_end"

    # Synaptic memory mesh
    MEMORY_STORED = "memory.stored"
    MEMORY_DELETED = "memory.deleted"
    MEMORY_ACCESSED = "memory.accessed"
    MEMORY_SYNAPSE_CREATED = "memory.synapse.created"
    MEMORY_SYNAPSE_REMOVED = "memory.synapse.removed"
    MEMORY_SYNAPSE_ORPHANED = "memory.synapse.orphaned"
    MEMORY_SYNAPSE_REDIRECTED = "memory.synapse.redirected"
    MEMORY_CONSOLIDATION_DONE = "memory.consolidation.done"
    MEMORY_INSIGHT_GENERATED = "memory.insight.generated"
    MEMORY_POD_RECYCLED = "memory.pod.recycled"
    MEMORY_POD_MARKED = "memory.pod.marked"
    MEMORY_REAP_DONE = "memory.reap.done"
    MEMORY_SKILL_FORGE_REQUEST = "memory.skill.forge_request"

    # Prompt assembly / compression
    PROMPT_ZONE3_ASSEMBLING = "prompt.zone3.assembling"
    PROMPT_COMPRESSED = "prompt.compressed"
    # The stable prefix changed although agent/tools/config did not: every
    # downstream turn loses its cache hit. Observability only, never blocks.
    PROMPT_PREFIX_DRIFT = "prompt.prefix_drift"

    # Skill self-improvement: create/update/archive lets the WebUI surface a
    # non-blocking notice, reload marks a hot refresh after external edits.
    SKILL_CREATED = "skill.created"
    SKILL_UPDATED = "skill.updated"
    SKILL_ARCHIVED = "skill.archived"
    SKILL_RELOADED = "skill.reloaded"


class Event:
    """Immutable-in-practice envelope carried by the event bus."""

    def __init__(
        self,
        event_type: str,
        payload: Dict[str, Any],
        source: str = "",
        timestamp: float = 0.0
    ):
        """Initialise an event envelope."""
        self.event_type = event_type
        self.payload = payload
        self.source = source

        if timestamp == 0.0:
            import time
            self.timestamp = time.time()
        else:
            self.timestamp = timestamp

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the event into a plain dictionary."""
        return {
            "event_type": self.event_type,
            "payload": self.payload,
            "source": self.source,
            "timestamp": self.timestamp
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        """Rebuild an event from its dictionary form."""
        return cls(
            event_type=data["event_type"],
            payload=data["payload"],
            source=data.get("source", ""),
            timestamp=data.get("timestamp", 0.0)
        )

    def __repr__(self) -> str:
        return f"Event(type={self.event_type}, source={self.source})"
