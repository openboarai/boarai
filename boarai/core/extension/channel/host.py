"""
Channel host — the core side of the message-adapter contract.

The host is what makes an adapter small. Everything a messaging platform needs
besides its own transport lives here, once:

* **conversation → session mapping.** Derived, not stored: the same
  ``(source_id, conversation_id)`` always maps to the same session id, so a
  restart resumes the same conversation with no mapping table to persist or
  corrupt.
* **the inbound turn.** Ensure the session exists (with the same global
  model/agent/workspace defaults a WebUI session gets), persist the user
  message, mirror the ``session.message`` event so the WebUI shows turns that
  arrived from WeChat/Feishu/DingTalk, then start a run and await its
  completion future — the complete final answer, never a stream fragment.
* **one turn at a time per conversation.** ``create_run`` interrupts an active
  run on the same session, so concurrent inbound messages from one chat would
  cancel each other. The host serializes per conversation; different
  conversations still run in parallel.
* **always reply.** A timeout, a failed run, an exception in the pipeline or a
  transport error all end in a delivery attempt, so an external user is never
  left in silence.
* **task hygiene.** Dispatched turns are held in a strong-reference set with a
  done-callback: the event loop keeps only weak references, and an
  unreferenced task can be garbage-collected mid-run.

Adapters never import this module — they receive a :class:`ChannelContext`
from ``api.register_channel``. The host never imports an extension.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional, Set

from .adapter import (
    ChannelAdapter,
    ChannelConflict,
    ChannelError,
    ChannelRunResult,
    InboundMessage,
    OutboundMessage,
)

logger = logging.getLogger(__name__)

# Fixed namespace for the derived conversation → session mapping. Changing it
# would orphan every existing channel conversation, so it never changes.
_SESSION_NAMESPACE = uuid.UUID("7d444840-9dc0-11d1-b245-5ffdce74fad2")

DEFAULT_RUN_TIMEOUT = 300.0
"""Seconds to wait for one turn before cancelling the run and replying."""

DEFAULT_ERROR_REPLY = (
    "Sorry — I could not finish that request. Please try again."
)
"""What an external user sees when a turn fails. Deliberately plain: it is read
by a person on a phone, not by an operator with logs."""


class ChannelContext:
    """The core-facing handle handed to one registered adapter."""

    def __init__(
        self,
        *,
        adapter: ChannelAdapter,
        source_id: str,
        extension_id: str,
        agent_name: str = "default",
        run_timeout: float = DEFAULT_RUN_TIMEOUT,
        error_reply: str = DEFAULT_ERROR_REPLY,
    ) -> None:
        self._adapter = adapter
        self._source_id = source_id
        self._extension_id = extension_id
        self._agent_name = agent_name
        self._run_timeout = float(run_timeout or DEFAULT_RUN_TIMEOUT)
        self._error_reply = error_reply or DEFAULT_ERROR_REPLY
        self._locks: Dict[str, asyncio.Lock] = {}
        self._tasks: Set[asyncio.Task] = set()
        self._closed = False

    # --- Identity ---

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def adapter(self) -> ChannelAdapter:
        return self._adapter

    def session_id_for(self, conversation_id: str) -> str:
        """Stable BoarAI session id for one external conversation."""
        return str(
            uuid.uuid5(_SESSION_NAMESPACE, f"{self._source_id}:{conversation_id}")
        )

    # --- Inbound ---

    async def submit(self, inbound: InboundMessage) -> Optional[asyncio.Task]:
        """Run one inbound turn in the background and deliver the reply."""
        if self._closed:
            logger.debug(
                "channel %s: submit() after close, dropping message", self._source_id
            )
            return None
        task = asyncio.create_task(
            self._turn(inbound), name=f"channel-turn-{self._source_id}"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def ask(
        self, inbound: InboundMessage, timeout: Optional[float] = None
    ) -> ChannelRunResult:
        """Run one inbound turn and return the result without delivering it."""
        async with self._lock_for(inbound.conversation_id):
            return await self._run_turn(
                inbound, self._run_timeout if timeout is None else float(timeout)
            )

    # --- Outbound ---

    async def reply(
        self,
        inbound: InboundMessage,
        text: str,
        *,
        kind: str = "reply",
        result: Optional[ChannelRunResult] = None,
    ) -> bool:
        """Send one message back into the conversation it came from."""
        return await self._deliver(
            OutboundMessage(
                conversation_id=inbound.conversation_id,
                text=text,
                kind=kind,
                reply_token=inbound.reply_token,
                inbound=inbound,
                session_id=result.session_id if result else "",
                run_id=result.run_id if result else "",
                metadata=dict(inbound.metadata or {}),
            )
        )

    async def notice(self, conversation_id: str, text: str) -> bool:
        """Push an unsolicited message (no inbound turn behind it)."""
        return await self._deliver(
            OutboundMessage(
                conversation_id=conversation_id, text=text, kind="notice"
            )
        )

    async def _deliver(self, outbound: OutboundMessage) -> bool:
        """Hand one outbound message to the adapter."""
        try:
            return bool(await self._adapter.send(outbound))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — transport is the adapter's own
            logger.warning(
                "channel %s: adapter.send raised for conversation %s: %s",
                self._source_id, outbound.conversation_id, e,
            )
            return False

    # --- Shutdown ---

    async def close(self) -> None:
        """Cancel in-flight turns, then release the adapter's transport."""
        self._closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._locks.clear()
        try:
            await self._adapter.stop()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — shutdown must complete
            logger.warning("channel %s: adapter.stop() raised: %s", self._source_id, e)

    @property
    def pending_turns(self) -> int:
        """In-flight turns (introspection for the owning extension)."""
        return len(self._tasks)

    # --- Internals ---

    def _lock_for(self, conversation_id: str) -> asyncio.Lock:
        lock = self._locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[conversation_id] = lock
        return lock

    async def _turn(self, inbound: InboundMessage) -> None:
        """One dispatched turn: run it, then always deliver something."""
        async with self._lock_for(inbound.conversation_id):
            try:
                result = await self._run_turn(inbound, self._run_timeout)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — never leave the user silent
                logger.exception(
                    "channel %s: turn failed for conversation %s",
                    self._source_id, inbound.conversation_id,
                )
                await self.reply(inbound, self._error_reply, kind="error")
                return

            if result.ok:
                await self.reply(inbound, result.text, result=result)
                return
            logger.info(
                "channel %s: turn ended as '%s' for conversation %s: %s",
                self._source_id, result.status, inbound.conversation_id,
                result.error or "(no detail)",
            )
            await self.reply(
                inbound, result.error or self._error_reply, kind="error",
                result=result,
            )

    async def _mirror_message_event(
        self,
        session_id: str,
        message: Any,
        role: str,
        content: str,
        metadata: Dict[str, Any],
    ) -> None:
        """Mirror one persisted message as a ``session.message`` event."""
        from boarai.core.event import EventType, publish_event

        await publish_event(
            EventType.SESSION_MESSAGE,
            {
                "session_id": session_id,
                "message_id": f"msg_{int(message.timestamp.timestamp() * 1000)}",
                "role": role,
                "content": content,
                "timestamp": message.timestamp.isoformat(),
                "metadata": metadata,
                "seq": getattr(message, "seq", 0),
            },
            self._source_id,
        )

    async def _run_command_turn(
        self,
        session: Any,
        session_manager: Any,
        session_id: str,
        inbound: InboundMessage,
        cmd_info: Any,
        cmd_args: str,
        metadata: Dict[str, Any],
    ) -> ChannelRunResult:
        """Execute one handler-kind chat command as a complete turn."""
        from boarai.core.command import run_command_turn

        # Persistence + execution: the SAME single source the WebUI WS and
        # REST run-creation paths use (channel provenance rides in base_meta).
        turn = await run_command_turn(
            session_manager, session, session_id,
            cmd_info, cmd_args,
            raw_text=inbound.text,
            base_meta=metadata,
        )

        await self._mirror_message_event(
            session_id, turn.user_message, "user", inbound.text, turn.user_meta
        )
        await self._mirror_message_event(
            session_id, turn.reply_message, "assistant",
            turn.reply_text, turn.reply_meta
        )

        if turn.ok:
            return ChannelRunResult(
                text=turn.reply_text, status="completed",
                session_id=session_id, run_id="",
            )
        return ChannelRunResult(
            text="", status="failed", error=turn.reply_text,
            session_id=session_id, run_id="",
        )

    async def _run_turn(
        self, inbound: InboundMessage, timeout: float
    ) -> ChannelRunResult:
        """Persist the inbound turn, run it, and return the final answer."""
        from boarai.core.session import get_run_manager, get_session_manager
        from boarai.core.session.service import apply_session_defaults

        session_id = self.session_id_for(inbound.conversation_id)
        session_manager = get_session_manager()
        run_manager = get_run_manager()

        session = await session_manager.get_session(session_id)
        if session is None:
            session = await session_manager.create_session(
                session_id=session_id, agent_name=self._agent_name
            )
            session.source = self._source_id
            # Same defaults a WebUI-created session gets (model/agent/workspace).
            apply_session_defaults(session)

        metadata = self._message_metadata(inbound)

        # --- Chat-scope command interception (BEFORE any LLM run) ---
        # Same single-source rules as the WebUI WS path: a "/cmd" text turn
        # that resolves to a registered chat command never reaches the
        # model. Handler commands reply in place; prompt commands expand
        # into the run content (the journal stores what the LLM sees, the
        # raw "/cmd" text rides in metadata). Attachments disable the
        # interception - commands are plain text turns.
        run_text = inbound.text
        if not inbound.attachments:
            from boarai.core.command import expand_prompt, match_chat_command

            matched = match_chat_command(inbound.text)
            if matched is not None:
                cmd_info, cmd_args = matched
                if cmd_info.kind == "prompt":
                    run_text = expand_prompt(cmd_info.template, cmd_args)
                    metadata = {
                        **metadata,
                        "chat_command": {
                            "name": cmd_info.name,
                            "kind": "prompt",
                            "raw": inbound.text,
                        },
                    }
                else:
                    return await self._run_command_turn(
                        session, session_manager, session_id, inbound,
                        cmd_info, cmd_args, metadata,
                    )

        session.ensure_session_capability_inject()
        message = session.add_message("user", run_text, metadata=metadata)
        session.maybe_auto_title_from_user(run_text)
        await session_manager.save_session(session_id)

        # add_message publishes nothing — the HTTP layer does. Mirror it, or the
        # WebUI never shows the turns that arrived through this channel.
        await self._mirror_message_event(session_id, message, "user", run_text, metadata)

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        run_id = await run_manager.create_run(
            session_id,
            agent_name=self._agent_name,
            completion_future=future,
        )
        try:
            run = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            await self._cancel_run(run_manager, run_id)
            return ChannelRunResult(
                status="timeout",
                error=f"no answer within {timeout:.0f}s",
                session_id=session_id,
                run_id=run_id,
            )
        except asyncio.CancelledError:
            # Shutdown or a replaced registration: do not leave the run going.
            await self._cancel_run(run_manager, run_id)
            raise

        if run is None:
            return ChannelRunResult(
                status="failed",
                error="run finished without a result",
                session_id=session_id,
                run_id=run_id,
            )
        status = run.status.value if hasattr(run.status, "value") else str(run.status)
        return ChannelRunResult(
            text=(run.full_response or "").strip(),
            status=status,
            error=run.error or "",
            session_id=session_id,
            run_id=run_id,
        )

    def _message_metadata(self, inbound: InboundMessage) -> Dict[str, Any]:
        """Message metadata: channel provenance + whatever the adapter added."""
        metadata: Dict[str, Any] = dict(inbound.metadata or {})
        metadata["channel"] = {
            "source_id": self._source_id,
            "conversation_id": inbound.conversation_id,
            "sender_id": inbound.sender_id,
            "sender_name": inbound.sender_name,
        }
        if inbound.attachments:
            metadata["attachments"] = list(inbound.attachments)
        return metadata

    @staticmethod
    async def _cancel_run(run_manager: Any, run_id: str) -> None:
        try:
            await run_manager.cancel_run(run_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — best effort on the failure path
            logger.warning("channel: cancel_run(%s) failed: %s", run_id, e)


class _Registration:
    """One registered adapter and the context the host built for it."""

    __slots__ = ("adapter", "context", "extension_id", "source_id")

    def __init__(
        self,
        extension_id: str,
        source_id: str,
        adapter: ChannelAdapter,
        context: ChannelContext,
    ) -> None:
        self.extension_id = extension_id
        self.source_id = source_id
        self.adapter = adapter
        self.context = context


class ChannelHost:
    """Registry + lifecycle for message adapters."""

    def __init__(self) -> None:
        self._by_extension: Dict[str, _Registration] = {}
        self._by_source: Dict[str, str] = {}

    async def register(
        self,
        extension_id: str,
        adapter: ChannelAdapter,
        *,
        source_id: Optional[str] = None,
        agent_name: str = "default",
        run_timeout: float = DEFAULT_RUN_TIMEOUT,
        error_reply: str = DEFAULT_ERROR_REPLY,
    ) -> ChannelContext:
        """Register an adapter and start it. Returns its context."""
        if not isinstance(adapter, ChannelAdapter):
            raise TypeError(
                "register_channel expects a ChannelAdapter instance, got "
                f"{type(adapter).__name__}"
            )
        resolved = (
            source_id or getattr(adapter, "source_id", "") or extension_id
        ).strip()
        if not resolved:
            raise ChannelError(
                "a channel needs a source_id (pass one or set the adapter's "
                "source_id attribute)"
            )

        owner = self._by_source.get(resolved)
        if owner and owner != extension_id:
            raise ChannelConflict(
                f"source_id '{resolved}' is already registered by '{owner}'"
            )

        if extension_id in self._by_extension:
            await self.unregister(extension_id)

        context = ChannelContext(
            adapter=adapter,
            source_id=resolved,
            extension_id=extension_id,
            agent_name=agent_name,
            run_timeout=run_timeout,
            error_reply=error_reply,
        )
        self._by_extension[extension_id] = _Registration(
            extension_id, resolved, adapter, context
        )
        self._by_source[resolved] = extension_id
        logger.info(
            "Channel registered: %s (source_id=%s, extension=%s)",
            type(adapter).__name__, resolved, extension_id,
        )

        try:
            await adapter.start(context)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — a failed start is not a failed registration
            logger.error(
                "Channel '%s' start() failed (stays registered; the adapter may "
                "connect later): %s", resolved, e,
            )
        return context

    async def unregister(self, extension_id: str) -> int:
        """Stop and drop every adapter registered by one extension."""
        registration = self._by_extension.pop(extension_id, None)
        if registration is None:
            return 0
        if self._by_source.get(registration.source_id) == extension_id:
            del self._by_source[registration.source_id]
        await registration.context.close()
        logger.info(
            "Channel unregistered: %s (source_id=%s)",
            extension_id, registration.source_id,
        )
        return 1

    async def unregister_all(self) -> int:
        """Stop every adapter (core shutdown)."""
        count = 0
        for extension_id in list(self._by_extension):
            count += await self.unregister(extension_id)
        return count

    def list_channels(self) -> List[Dict[str, Any]]:
        """Registered adapters (introspection; no platform knowledge)."""
        return [
            {
                "extension_id": r.extension_id,
                "source_id": r.source_id,
                "adapter": type(r.adapter).__name__,
                "pending_turns": r.context.pending_turns,
            }
            for r in self._by_extension.values()
        ]


_channel_host: Optional[ChannelHost] = None


def get_channel_host() -> ChannelHost:
    """The global ChannelHost singleton."""
    global _channel_host
    if _channel_host is None:
        _channel_host = ChannelHost()
    return _channel_host


def reset_channel_host() -> None:
    """Drop the singleton (tests and core shutdown)."""
    global _channel_host
    _channel_host = None
