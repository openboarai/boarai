"""
Message-channel adapters: contract, context and host.

* :mod:`~core.extension.channel.adapter` — what an adapter implements
  (``ChannelAdapter`` + the inbound/outbound message types).
* :mod:`~core.extension.channel.host` — what the core provides in return
  (``ChannelContext`` for one adapter, ``ChannelHost`` for registration and
  lifecycle).

Adapters get both through ``api.register_channel(adapter)``; the core never
imports an extension and never names a platform.
"""

from .adapter import (
    ChannelAdapter,
    ChannelConflict,
    ChannelError,
    ChannelRunResult,
    InboundMessage,
    OutboundMessage,
)
from .host import (
    DEFAULT_ERROR_REPLY,
    DEFAULT_RUN_TIMEOUT,
    ChannelContext,
    ChannelHost,
    get_channel_host,
    reset_channel_host,
)

__all__ = [
    "ChannelAdapter",
    "ChannelConflict",
    "ChannelContext",
    "ChannelError",
    "ChannelHost",
    "ChannelRunResult",
    "DEFAULT_ERROR_REPLY",
    "DEFAULT_RUN_TIMEOUT",
    "InboundMessage",
    "OutboundMessage",
    "get_channel_host",
    "reset_channel_host",
]
