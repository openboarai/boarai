"""
The message-adapter contract.

Division of labour, stated once because it decides every signature below:

* **The core owns every run.** Session mapping, persisting the inbound turn,
  mirroring the ``session.message`` event, starting the run, awaiting the
  final answer, serializing per conversation, and always replying — including
  on timeout — all live in :class:`~core.extension.channel.host.ChannelContext`.
* **The adapter owns its transport.** The long connection to Feishu or
  DingTalk, a WeChat long-poll, a webhook route, a QR login, a serial port:
  the core never sees it and never needs to.

So an adapter implements exactly one required method — :meth:`ChannelAdapter.send`
— and calls exactly one entry point to hand work in: ``ctx.submit()`` (fire and
forget) or ``ctx.ask()`` (I want the text back and will deliver it myself).

Nothing here knows which adapters exist. ``api.register_channel(adapter)`` is
the insertion point; the core never imports an extension.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, Optional

__all__ = [
    "ChannelAdapter",
    "ChannelConflict",
    "ChannelError",
    "ChannelRunResult",
    "InboundMessage",
    "OutboundMessage",
]


class ChannelError(Exception):
    """Base for channel-contract violations."""


class ChannelConflict(ChannelError):
    """Another extension already owns this ``source_id``."""


@dataclass(slots=True)
class InboundMessage:
    """One message arriving from the external platform."""

    conversation_id: str
    text: str = ""
    sender_id: str = ""
    sender_name: str = ""
    reply_token: Any = None
    """Opaque platform handle for replying (webhook URL, context token, …).

    Returned untouched on :attr:`OutboundMessage.reply_token`; the core never
    interprets it.
    """
    metadata: Dict[str, Any] = field(default_factory=dict)
    attachments: list = field(default_factory=list)


@dataclass(slots=True)
class OutboundMessage:
    """One message the core asks an adapter to deliver."""

    conversation_id: str
    text: str
    kind: str = "reply"
    """``reply`` (answer to an inbound turn), ``error`` (failure notice),
    ``notice`` (unsolicited push)."""
    reply_token: Any = None
    inbound: Optional[InboundMessage] = None
    session_id: str = ""
    run_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChannelRunResult:
    """What came back from one inbound turn."""

    text: str = ""
    status: str = ""
    error: str = ""
    session_id: str = ""
    run_id: str = ""

    @property
    def ok(self) -> bool:
        """True when a completed answer is available."""
        return self.status == "completed" and bool(self.text)


class ChannelAdapter(ABC):
    """What a message-platform adapter implements."""

    source_id: ClassVar[str] = ""
    """Fallback identity when ``api.register_channel`` is not given one.

    The manifest's ``source_id`` (declared by the extension, registered into
    the SourceRegistry by the manager) and this attribute should agree; the
    registration argument wins.
    """

    @abstractmethod
    async def send(self, message: OutboundMessage) -> bool:
        """Deliver one outbound message. Return False if delivery failed."""

    async def start(self, ctx: Any) -> None:
        """Begin intake. ``ctx`` is this adapter's :class:`ChannelContext`."""

    async def stop(self) -> None:
        """Release the transport (close sockets, cancel poll tasks)."""
