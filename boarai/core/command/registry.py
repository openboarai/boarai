"""Command registry for BoarAI.

The registry holds the IN-SESSION (chat) commands contributed by core and
extensions. A message whose text is a single ``/token`` matching a
registered command is intercepted at the message surfaces (WebUI WS
``session.run.create``, ChannelHost turns) BEFORE any LLM run.

There is no terminal/CLI command domain: server process management lives
in ``main.py`` (start/stop/restart/status) and calls the dispatcher
functions directly - it never goes through this registry.

Two command kinds:

``handler``
    A callable executes deterministically; the result goes straight back
    into the conversation as the assistant reply and never reaches the
    LLM.
``prompt``
    A template expanded with the user's arguments (``$@`` slot) and fed
    into the normal run pipeline as the user message - reaching the LLM
    by design, like a saved instruction.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

KIND_HANDLER = "handler"
KIND_PROMPT = "prompt"
VALID_KINDS = (KIND_HANDLER, KIND_PROMPT)


@dataclass
class CommandInfo:
    """One registered chat command."""

    name: str
    description: str
    source: str  # "core" or the extension id
    handler: Optional[Callable] = None  # required for kind="handler"
    kind: str = KIND_HANDLER  # "handler" | "prompt"
    template: str = ""  # required for kind="prompt" ($@ = argument slot)
    aliases: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Public representation (no handler, no template body)."""
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "kind": self.kind,
            "aliases": list(self.aliases),
        }


class CommandRegistry:
    """Registry managing chat commands and their aliases."""

    def __init__(self):
        self._commands: Dict[str, CommandInfo] = {}
        self._aliases: Dict[str, str] = {}  # alias -> command name

    # --- Registration ---

    def register(
        self,
        name: str,
        handler: Optional[Callable] = None,
        description: str = "",
        source: str = "core",
        aliases: Optional[List[str]] = None,
        kind: str = KIND_HANDLER,
        template: str = "",
    ) -> CommandInfo:
        """Register (or replace) a chat command."""
        name = str(name or "").strip()
        if not name:
            raise ValueError("command name must be a non-empty string")

        if kind not in VALID_KINDS:
            raise ValueError(f"invalid kind {kind!r} (expected one of {VALID_KINDS})")
        if kind == KIND_HANDLER and not callable(handler):
            raise ValueError(f"command {name!r}: kind='handler' requires a callable handler")
        if kind == KIND_PROMPT and not str(template or "").strip():
            raise ValueError(f"command {name!r}: kind='prompt' requires a template")
        if kind == KIND_PROMPT:
            handler = None

        alias_list = [str(a).strip() for a in (aliases or []) if str(a).strip()]

        # Replacing a command: drop the OLD alias mappings first so stale
        # aliases never resolve to the new registration.
        old = self._commands.get(name)
        if old is not None:
            logger.warning("Command '%s' already registered, overwriting", name)
            for alias in old.aliases:
                if self._aliases.get(alias) == name:
                    del self._aliases[alias]

        # Alias bookkeeping: an alias colliding with a real command name is
        # rejected (the command name always wins in get()); an alias owned by
        # another command is taken over with a warning.
        clean_aliases: List[str] = []
        for alias in alias_list:
            if alias == name:
                continue
            if alias in self._commands:
                logger.warning(
                    "Command %r: alias %r collides with a registered command "
                    "name and was dropped", name, alias,
                )
                continue
            owner = self._aliases.get(alias)
            if owner is not None and owner != name:
                logger.warning(
                    "Command %r: alias %r reassigned from command %r",
                    name, alias, owner,
                )
            self._aliases[alias] = name
            clean_aliases.append(alias)

        info = CommandInfo(
            name=name,
            description=description,
            source=source,
            handler=handler,
            kind=kind,
            template=str(template or ""),
            aliases=clean_aliases,
        )
        self._commands[name] = info
        logger.info("Registered chat command: %s (source: %s, kind: %s)", name, source, kind)
        return info

    def unregister(self, name: str) -> bool:
        """Remove a command and the aliases that still point at it."""
        info = self._commands.pop(name, None)
        if info is None:
            return False
        for alias in info.aliases:
            # Only drop the alias when it still resolves to THIS command -
            # a later registration may have legitimately taken it over.
            if self._aliases.get(alias) == name:
                del self._aliases[alias]
        logger.info("Unregistered command: %s", name)
        return True

    # --- Lookup ---

    def get(self, name: str) -> Optional[CommandInfo]:
        """Get a command by canonical name or alias."""
        info = self._commands.get(name)
        if info is not None:
            return info
        actual = self._aliases.get(name)
        if actual:
            return self._commands.get(actual)
        return None

    def list_commands(self) -> List[CommandInfo]:
        """All registered commands."""
        return list(self._commands.values())

    def unregister_all_for(self, source: str) -> int:
        """Remove every command owned by ``source`` (extension unload sweep)."""
        names = [n for n, c in self._commands.items() if c.source == source]
        for name in names:
            self.unregister(name)
        return len(names)

    def clear(self) -> None:
        """Drop every command and alias (tests / shutdown)."""
        self._commands.clear()
        self._aliases.clear()
        logger.info("All commands cleared")


# Global command registry instance
_command_registry: Optional[CommandRegistry] = None


def get_command_registry() -> CommandRegistry:
    """Get the global command registry instance."""
    global _command_registry
    if _command_registry is None:
        _command_registry = CommandRegistry()
    return _command_registry
