"""
BoarAI Command Module.

The command system is the IN-SESSION (chat) domain only: commands
registered by core/extensions, intercepted at the message surfaces
(WebUI WS ``session.run.create``, ChannelHost turns) before any LLM run.

There is no CLI command surface. Server process management
(start/stop/restart/status/ext) lives in ``main.py`` and calls the
dispatcher functions directly.
"""

from .registry import (
    KIND_HANDLER,
    KIND_PROMPT,
    CommandInfo,
    CommandRegistry,
    get_command_registry,
)
from .chat import (
    CommandTurnResult,
    execute_chat_command,
    expand_prompt,
    format_command_output,
    match_chat_command,
    run_command_turn,
)
from .dispatcher import (
    cmd_restart,
    cmd_status,
    cmd_stop,
)
from .bootstrap import create_directories

__all__ = [
    "CommandRegistry", "CommandInfo", "get_command_registry",
    "KIND_HANDLER", "KIND_PROMPT",
    "match_chat_command", "expand_prompt", "execute_chat_command",
    "format_command_output", "run_command_turn", "CommandTurnResult",
    "cmd_status", "cmd_stop", "cmd_restart",
    "create_directories",
]
