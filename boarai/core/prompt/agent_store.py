"""
AgentStore — folder-based agent persistence for BoarAi.

Each agent lives in its own directory under the agents data directory:
    .boarai/agents/{name}/
    ├── soul.md         # Pure Markdown — agent's personality/persona (zone1_identity + rules)
    └── agent.yaml      # YAML — all structured config fields

File format:
    soul.md:
        ## Identity
        You are BoarAi Coder...

        ## Behavior Rules
        1. Write clean code...

    agent.yaml:
        name: coder
        description: Programming expert
        avatar: code
        ...
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .agent import AgentConfig, AgentRegistry, get_agent_registry

logger = logging.getLogger(__name__)

_SOUL_FILE = "soul.md"
_CONFIG_FILE = "agent.yaml"


class AgentStore:
    """Folder-based agent persistence using soul.md + agent.yaml."""

    def __init__(self, agents_dir: Path):
        self._agents_dir = agents_dir
        self._agents_dir.mkdir(parents=True, exist_ok=True)

    # --- Initialization ---

    def initialize_defaults(self) -> None:
        """Load existing agent files from .boarai/agents/ into registry."""
        registry = get_agent_registry()
        for name in self.list_agents():
            config = self.load_agent(name)
            if config:
                registry.register(config)
                logger.debug(f"Loaded agent: {name}")
        logger.info(
            f"Agent store initialized with {len(registry.list())} agents "
            f"at {self._agents_dir}"
        )

    # --- CRUD Operations ---

    def list_agents(self) -> List[str]:
        """List all agent names (directory with agent.yaml)."""
        if not self._agents_dir.exists():
            return []
        return sorted(
            d.name for d in self._agents_dir.iterdir()
            if d.is_dir() and (d / _CONFIG_FILE).exists()
        )

    def load_agent(self, name: str) -> Optional[AgentConfig]:
        """Load a single agent from its folder."""
        agent_dir = self._agents_dir / name
        config_path = agent_dir / _CONFIG_FILE
        soul_path = agent_dir / _SOUL_FILE
        if not config_path.exists():
            return None
        try:
            return self._load_agent(config_path, soul_path)
        except Exception as e:
            logger.error(f"Failed to load agent '{name}': {e}")
            return None

    def load_all(self) -> List[AgentConfig]:
        """Load all agents from disk."""
        return [
            config for name in self.list_agents()
            if (config := self.load_agent(name)) is not None
        ]

    def save_agent(self, config: AgentConfig) -> None:
        """Save a single agent to its folder."""
        agent_dir = self._agents_dir / config.name
        agent_dir.mkdir(parents=True, exist_ok=True)

        config.updated_at = datetime.now().isoformat()
        if not config.created_at:
            config.created_at = config.updated_at

        self._save_agent(config, agent_dir / _CONFIG_FILE, agent_dir / _SOUL_FILE)

        get_agent_registry().register(config)
        logger.info(f"Saved agent: {config.name}")

    async def delete_agent(self, name: str, distill: bool = False) -> bool:
        """Delete an agent folder and all its contents.

        No name is special-cased: removing the default agent is allowed, and
        the API layer reassigns the flag to a remaining agent afterwards.
        """
        agent_dir = self._agents_dir / name
        if not agent_dir.exists():
            return False

        # Try soft-delete via TrashManager
        try:
            from boarai.core.trash import get_trash_manager
            from datetime import datetime
            tm = get_trash_manager()
            if tm:
                await tm.trash_agent(
                    agent_name=name,
                    agent_dir=agent_dir,
                    distill=distill,
                    metadata={
                        "deleted_at": datetime.now().isoformat(),
                        "deleted_by": "api",
                    },
                )
                get_agent_registry().unregister(name)
                logger.info(f"Agent {name} moved to trash")
                return True
        except Exception as e:
            logger.warning(f"Trash move failed for agent, falling back to rmtree: {e}")

        import shutil
        shutil.rmtree(agent_dir)

        get_agent_registry().unregister(name)
        logger.info(f"Deleted agent: {name}")
        return True

    def set_default(self, name: str) -> Optional[str]:
        """Set an agent as the default (persist to all agent files)."""
        registry = get_agent_registry()
        # A role restored from the recycle bin (or otherwise not yet in the
        # registry) must be registered first — otherwise set_default silently
        # flags nothing and the instance reports "no default agent".
        if registry.get(name) is None:
            config = self.load_agent(name)
            if config is not None:
                registry.register(config)
        old_default = registry.set_default(name)

        now = datetime.now().isoformat()
        for agent in registry.list_all():
            agent_dir = self._agents_dir / agent.name
            if agent_dir.exists():
                agent.updated_at = now
                self._save_agent(agent, agent_dir / _CONFIG_FILE, agent_dir / _SOUL_FILE)

        logger.info(f"Set default agent: {name} (was: {old_default})")
        return old_default

    def get_default(self) -> Optional[AgentConfig]:
        """Get the default agent config."""
        return get_agent_registry().get_default()

    # --- Sync Registry from Store ---

    def sync_to_registry(self) -> None:
        """Load all agents from disk into the in-memory registry."""
        registry = get_agent_registry()
        for config in self.load_all():
            registry.register(config)
        logger.info(f"Synced {len(registry.list())} agents to registry")

    # --- File I/O ---

    @staticmethod
    def _save_agent(config: AgentConfig, config_path: Path, soul_path: Path) -> None:
        """Write agent.yaml (config) + soul.md (persona)."""
        data = config.to_dict()

        # Extract persona text → soul.md
        identity = data.pop("zone1_identity", "") or ""
        rules = data.pop("zone1_rules", "") or ""
        soul_parts = []
        if identity:
            soul_parts.append("## Identity\n")
            soul_parts.append(identity.strip())
        if rules:
            soul_parts.append("")
            soul_parts.append("## Behavior Rules\n")
            soul_parts.append(rules.strip())
        soul_content = "\n".join(soul_parts) + "\n" if soul_parts else ""

        # Write soul.md
        soul_path.write_text(soul_content, "utf-8")

        # Write agent.yaml (all remaining fields)
        yaml_str = yaml.dump(
            data,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=80,
        ).strip()
        config_path.write_text(yaml_str + "\n", "utf-8")

    @staticmethod
    def _load_agent(config_path: Path, soul_path: Path) -> AgentConfig:
        """Read agent.yaml (config) + soul.md (persona)."""
        # Read agent.yaml
        raw = config_path.read_text("utf-8")
        data: Dict[str, Any] = yaml.safe_load(raw) or {}

        # Read soul.md — extract identity and rules
        if soul_path.exists():
            soul_text = soul_path.read_text("utf-8")
            identity, rules = AgentStore._parse_soul(soul_text)
            data["zone1_identity"] = identity
            data["zone1_rules"] = rules
        else:
            data["zone1_identity"] = ""
            data["zone1_rules"] = ""

        return AgentConfig.from_dict(data)

    @staticmethod
    def _parse_soul(body: str) -> tuple:
        """Parse soul.md body into zone1_identity and zone1_rules."""
        if not body.strip():
            return "", ""

        lines = body.split("\n")
        identity = ""
        rules = ""
        in_identity = False
        in_rules = False

        for line in lines:
            stripped = line.strip()
            if stripped == "## Identity":
                in_identity = True
                in_rules = False
                continue
            elif stripped == "## Behavior Rules":
                in_identity = False
                in_rules = True
                continue

            if in_identity:
                identity += ("\n" + line) if identity else line
            elif in_rules:
                rules += ("\n" + line) if rules else line

        return identity.strip(), rules.strip()


# --- Global Instance ---

_agent_store: Optional[AgentStore] = None


def get_agent_store(agents_dir: Optional[Path] = None) -> AgentStore:
    """Get the global agent store instance."""
    global _agent_store
    if _agent_store is None:
        if agents_dir is None:
            raise RuntimeError(
                "AgentStore not initialized. "
                "Call get_agent_store(agents_dir) with a valid path first."
            )
        _agent_store = AgentStore(agents_dir)
        _agent_store.initialize_defaults()
    return _agent_store


def reset_agent_store() -> None:
    """Reset the global agent store (for testing)."""
    global _agent_store
    _agent_store = None
