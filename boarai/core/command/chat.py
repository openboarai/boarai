"""Chat command interception and execution - the whole command runtime.

A message whose text is a single ``/token`` (optionally followed by
arguments) and whose token resolves to a registered command is
intercepted BEFORE any LLM run:

* ``handler`` kind - executed in-process; the output is persisted as the
  assistant reply. The LLM is never involved.
* ``prompt`` kind - the template is expanded with the raw argument text
  and the result REPLACES the message content, which then flows through
  the normal run pipeline (reaching the LLM by design).

The matcher is deliberately conservative: ``/path/to/file`` never matches
(the token must be followed by whitespace or end-of-text), and an
unregistered ``/token`` falls through to the model as ordinary text, so a
user can still talk about slash-prefixed strings.

All three message surfaces - the WebUI WS ``session.run.create`` path,
the REST ``POST /api/chat`` run creation and the ChannelHost turn
pipeline - use these helpers, so the matching rule, the expansion
semantics and the persisted turn shape stay single-source.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .registry import KIND_HANDLER, CommandInfo, get_command_registry

logger = logging.getLogger(__name__)

# One token after the slash, then either end-of-text or whitespace. A path
# like /home/x/file.txt never matches (the second '/' is not whitespace).
_COMMAND_RE = re.compile(r"^/([A-Za-z0-9_-]+)(?:\s+([\s\S]*))?$")


def match_chat_command(text: Any) -> Optional[Tuple[CommandInfo, str]]:
    """Resolve ``text`` to a registered chat command, if it is one."""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    m = _COMMAND_RE.match(stripped)
    if not m:
        return None
    cmd = get_command_registry().get(m.group(1))
    if cmd is None:
        return None
    return cmd, (m.group(2) or "").strip()


def expand_prompt(template: str, args: str) -> str:
    """Replace every ``$@`` in ``template`` with the raw argument text."""
    return template.replace("$@", args)


def format_command_output(output: Any) -> str:
    """Render a handler's return value as reply text."""
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(output)


async def execute_chat_command(cmd: CommandInfo, args: str) -> Tuple[bool, str]:
    """Run one handler-kind chat command."""
    if cmd.kind != KIND_HANDLER:
        raise ValueError(f"execute_chat_command expects a handler command, got {cmd.kind!r}")

    call_args = [args] if args else []
    try:
        if asyncio.iscoroutinefunction(cmd.handler):
            result = await cmd.handler(call_args)
        else:
            result = await asyncio.to_thread(cmd.handler, call_args)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - contained in the reply
        logger.exception("Chat command execution error: %s", cmd.name)
        return False, f"Command '{cmd.name}' failed: {e}"

    return True, format_command_output(result)


@dataclass
class CommandTurnResult:
    """The persisted pieces of one completed handler-command turn."""

    ok: bool
    reply_text: str
    user_message: Any
    user_meta: Dict[str, Any]
    reply_message: Any
    reply_meta: Dict[str, Any]
    titled: bool


async def run_command_turn(
    session_manager: Any,
    session: Any,
    session_id: str,
    cmd: CommandInfo,
    args: str,
    raw_text: str,
    base_meta: Optional[Dict[str, Any]] = None,
) -> CommandTurnResult:
    """Execute one handler-kind command turn and persist both messages."""
    base = dict(base_meta or {})
    user_meta = {**base, "chat_command": {"name": cmd.name, "kind": "handler"}}
    session.ensure_session_capability_inject()
    user_message = session.add_message("user", raw_text, metadata=user_meta)
    titled = session.maybe_auto_title_from_user(raw_text)

    ok, reply_text = await execute_chat_command(cmd, args)
    if not reply_text.strip():
        # An empty bubble is not a reply: give the turn a minimal answer so
        # the always-reply invariant holds on every transport.
        reply_text = f"/{cmd.name}: done" if ok else f"/{cmd.name}: failed"

    reply_meta = {**base, "chat_command": {"name": cmd.name, "ok": ok}}
    reply_message = session.add_message("assistant", reply_text, metadata=reply_meta)
    await session_manager.save_session(session_id)
    return CommandTurnResult(
        ok=ok,
        reply_text=reply_text,
        user_message=user_message,
        user_meta=user_meta,
        reply_message=reply_message,
        reply_meta=reply_meta,
        titled=titled,
    )
