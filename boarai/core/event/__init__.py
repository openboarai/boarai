"""Event bus package: dispatcher plus the canonical event catalogue.

Re-exports the bus, the event envelope and the event types so callers can
``from boarai.core.event import ...`` without reaching into submodules.
"""

from .bus import EventBus, EventBusError, get_event_bus, publish_event
from .types import Event, EventType

__all__ = [
    "EventBus",
    "EventBusError",
    "Event",
    "EventType",
    "get_event_bus",
    "publish_event",
]