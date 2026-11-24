"""
Agent configuration and registry for BoarAi — Multi-Agent support.

Architecture:
    AgentRegistry (singleton, in-memory)  ←  loaded from AgentStore
        └── seeded from boar/defaults/agents/ on first run

Zone 1 assembly order:
    [0] Core System Prompt  (static constant, from boar/prompts/system_core.md)
    [1] Agent Identity      (role-specific persona, from soul.md)
    [2] Agent Rules         (role-specific behavior, from soul.md)

Data flow:
    copy_defaults() → .boarai/agents/{name}/soul.md (editable)
    AgentStore      →  AgentRegistry (in-memory)
    HTTP API        →  AgentAPIHandler → AgentRegistry → AgentStore
"""

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ==================== Agent Config ====================


@dataclass
class AgentConfig:
    """Configuration for a single agent type."""
    name: str
    avatar: str = "bot"
    is_default: bool = False
    zone1_identity: str = ""
    zone1_rules: str = ""
    zone2_tool_names: List[str] = field(default_factory=lambda: ["*"])
    created_at: str = ""
    updated_at: str = ""
    # --- Security fields ---
    # Approval prompting is a session concern (session.security_overrides);
    # an agent only bounds *which* tools it may use (zone2_tool_names).
    allowed_categories: List[str] = field(default_factory=list)  # empty=all categories
    shell_allowlist: List[str] = field(default_factory=list)    # empty=all commands, ["*"]=all
    allowed_paths: List[str] = field(default_factory=list)      # file path patterns ("partial" mode)
    can_auto_approve: bool = False                               # auto-approve tool calls?
    max_tool_calls: int = 0                                      # 0=inherit global default

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary (excludes None defaults)."""
        d = asdict(self)
        # Remove None values for cleaner JSON
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentConfig":
        """Deserialize from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def resolve_identity(self, registry: "AgentRegistry") -> str:
        """Final Zone 1 identity."""
        return self.zone1_identity

    def resolve_rules(self, registry: "AgentRegistry") -> str:
        """Final Zone 1 rules (own text only — no inheritance)."""
        return self.zone1_rules

    def filter_tools(self, all_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter tool list to only allowed tools for this agent."""
        if "*" in self.zone2_tool_names:
            return all_tools

        allowed = set(self.zone2_tool_names)
        return [t for t in all_tools if t.get("function", {}).get("name", "") in allowed]


# ==================== Agent Registry ====================


class AgentRegistry:
    """In-memory registry of available agent types."""

    def __init__(self):
        self._agents: Dict[str, AgentConfig] = {}

    def register(self, config: AgentConfig) -> None:
        """Register (or update) an agent config."""
        if not config.name:
            raise ValueError("Agent name cannot be empty")
        self._agents[config.name] = config
        logger.info(f"Registered agent: {config.name}")

    def unregister(self, name: str) -> bool:
        """Unregister an agent.

        Any agent may be removed: the default role is chosen by ``is_default``,
        not by a hardcoded name. When the default one is deleted the API layer
        hands the flag to a remaining agent.
        """
        if name in self._agents:
            del self._agents[name]
            logger.info(f"Unregistered agent: {name}")
            return True
        return False

    def get(self, name: str) -> Optional[AgentConfig]:
        """Get an agent config by name."""
        return self._agents.get(name)

    def list(self) -> List[str]:
        """List all registered agent names."""
        return list(self._agents.keys())

    def list_all(self) -> List[AgentConfig]:
        """List all registered agent configs."""
        return list(self._agents.values())

    def set_default(self, name: str) -> Optional[str]:
        """Set an agent as the default (clear others)."""
        old_default = None
        for agent in self._agents.values():
            if agent.is_default:
                old_default = agent.name
                agent.is_default = False
        agent = self._agents.get(name)
        if agent:
            agent.is_default = True
        return old_default

    def get_default(self) -> Optional[AgentConfig]:
        """Get the default (main) agent.

        ``is_default`` decides. If none is flagged (e.g. the default agent was
        just deleted) fall back to the first registered agent instead of a
        hardcoded name — role names must not be baked into core.
        """
        for agent in self._agents.values():
            if agent.is_default:
                return agent
        return next(iter(self._agents.values()), None)


# ==================== Global Instance ====================


_agent_registry: Optional[AgentRegistry] = None


def get_agent_registry() -> AgentRegistry:
    """Get the global agent registry instance."""
    global _agent_registry
    if _agent_registry is None:
        _agent_registry = AgentRegistry()
    return _agent_registry


def reset_agent_registry() -> None:
    """Reset the global agent registry (for testing)."""
    global _agent_registry
    _agent_registry = None
