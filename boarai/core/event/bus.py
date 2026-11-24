"""Async publish/subscribe bus that decouples the runtime from its extensions.

The bus is the default cross-boundary channel: extensions reach each other by
publishing events instead of importing sibling packages, while core modules may
still call each other directly. Handlers run sequentially in priority order and
a failing handler never aborts the publish path.
"""

import asyncio
import json
import logging
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from .types import Event, EventType

# Every subscriber receives the event envelope and returns a coroutine.
EventHandler = Callable[[Event], Awaitable[None]]

logger = logging.getLogger(__name__)


class EventBusLogger:
    """Append-only JSONL audit trail for bus traffic."""

    #: High-frequency event types never written to the audit trail.
    SKIP_EVENT_TYPES = frozenset({
        "run.chunk",
        "thinking.delta",
        "tool.output",
    })

    #: Ceiling for one day's audit file.
    MAX_DAILY_BYTES = 20 * 1024 * 1024

    def __init__(self, log_file: Optional[Path] = None):
        """Configure the audit trail."""
        self._log_file = log_file
        self._enabled = log_file is not None
        self._day = ""
        self._day_bytes = 0
        self._capped = False
        self._skipped = 0
        self._dropped_by_cap = 0

        if self._enabled:
            # Date subdirectories are created on the first append of each day
            # so idle days leave no empty directories behind.
            self._base_dir = log_file.parent
            self._filename = log_file.name

    @property
    def skipped(self) -> int:
        """Events not written because they are on the skip list."""
        return self._skipped

    @property
    def dropped_by_cap(self) -> int:
        """Events not written because today's file hit the byte cap."""
        return self._dropped_by_cap

    def _begin_day(self, day: str, path: Path) -> None:
        """Seed today's byte counter (one stat per day, not per event)."""
        self._day = day
        self._capped = False
        try:
            self._day_bytes = path.stat().st_size if path.exists() else 0
        except OSError:
            self._day_bytes = 0
        if self._day_bytes >= self.MAX_DAILY_BYTES:
            self._capped = True

    async def log_publish(self, event_type: str, source: str, payload: Dict[str, Any]) -> None:
        """Record one PUBLISH line, including the serialised payload size."""
        if not self._enabled:
            return
        if event_type in self.SKIP_EVENT_TYPES:
            self._skipped += 1
            return

        try:
            # ``default=str`` because a payload holding a non-JSON object used
            # to raise here, and the audit trail would lose the event entirely
            # rather than record its size.
            payload_size = len(json.dumps(payload, ensure_ascii=False, default=str))
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "action": "PUBLISH",
                "event_type": event_type,
                "source": source,
                "payload_size": payload_size,
            }

            await self._append_log(log_entry)
        except Exception as e:
            logger.error("Failed to log publish: %s", e)

    async def log_subscribe(self, event_type: str, handler_name: str) -> None:
        """Retained no-op: subscriptions are structural and never logged."""
        return

    async def log_unsubscribe(self, event_type: str, handler_name: str) -> None:
        """Retained no-op; see :meth:`log_subscribe`."""
        return

    async def log_handler_execution(self, event_type: str, handler_name: str,
                                     success: bool, duration_ms: float,
                                     error: Optional[str] = None) -> None:
        """Record a failed handler execution; successful runs are dropped."""
        if not self._enabled:
            return
        # run.chunk and friends fire per streamed token per subscriber, so
        # successes would dwarf everything else in the log. Failures only.
        if success:
            return

        try:
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "action": "HANDLER_EXEC",
                "event_type": event_type,
                "handler": handler_name,
                "success": success,
                "duration_ms": round(duration_ms, 2),
            }

            if error:
                log_entry["error"] = error

            await self._append_log(log_entry)
        except Exception as e:
            logger.error(f"Failed to log handler execution: {e}")

    async def _append_log(self, entry: Dict[str, Any]) -> None:
        """Append one entry to today's event log without blocking the loop."""
        try:
            import aiofiles
            day = datetime.now().strftime("%Y-%m-%d")
            path = self._base_dir / day / self._filename
            if day != self._day:
                self._begin_day(day, path)
            if self._capped:
                self._dropped_by_cap += 1
                return

            line = json.dumps(entry, ensure_ascii=False) + '\n'
            path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(path, 'a', encoding='utf-8') as f:
                await f.write(line)
            self._day_bytes += len(line.encode('utf-8'))
            if self._day_bytes >= self.MAX_DAILY_BYTES:
                self._capped = True
                logger.warning(
                    "Event audit trail reached its %d-byte daily cap; further "
                    "events today are counted but not written",
                    self.MAX_DAILY_BYTES,
                )
        except Exception as e:
            logger.error("Failed to append log: %s", e)


class EventBus:
    """Priority-aware async dispatcher shared by the whole runtime."""

    def __init__(self, event_log_file: Optional[Path] = None):
        """Create a bus with an empty handler table."""
        self._handlers: Dict[str, List[EventHandler]] = {}
        self._handler_priorities: Dict[str, List[int]] = {}
        self._global_handlers: List[EventHandler] = []
        self._lock = asyncio.Lock()
        self._event_history: deque[Event] = deque()
        self._max_history_size = 1000
        self._event_logger = EventBusLogger(event_log_file)
        
    async def publish(self, event: Event) -> None:
        """Deliver an event to every matching subscriber and global handler."""
        # Phase 1 (locked): snapshot handlers and record history. Log file I/O
        # stays outside the lock so run.chunk-heavy traffic is not serialised
        # behind disk writes.
        async with self._lock:
            self._event_history.append(event)
            if len(self._event_history) > self._max_history_size:
                self._event_history.popleft()

            handlers = self._handlers.get(event.event_type, []).copy()
            handlers.extend(self._global_handlers)

        logger.debug(f"Publishing event: {event.event_type} from {event.source}")

        await self._event_logger.log_publish(
            event.event_type, event.source, event.payload
        )

        # Phase 2 (unlocked): sequential dispatch keeps the documented
        # priority ordering and lets handlers publish further events.
        if not handlers:
            logger.debug(f"No handlers for event: {event.event_type}")
            return

        for handler in handlers:
            await self._execute_handler(handler, event)
    
    async def subscribe(
        self,
        event_type: str,
        handler: EventHandler,
        priority: int = 0
    ) -> None:
        """Register a handler for one event type."""
        async with self._lock:
            if event_type not in self._handlers:
                self._handlers[event_type] = []
                self._handler_priorities[event_type] = []

            self._handlers[event_type].append(handler)
            self._handler_priorities.setdefault(event_type, []).append(priority)
            # Keep handlers and their priorities aligned while sorting by
            # descending priority.
            pairs = list(zip(self._handlers[event_type], self._handler_priorities[event_type]))
            pairs.sort(key=lambda p: (-p[1],))
            self._handlers[event_type] = [h for h, _ in pairs]
            self._handler_priorities[event_type] = [pr for _, pr in pairs]

        # Audit logging happens outside the lock; it is a no-op today but the
        # call site must not hold the lock if that ever changes.
        await self._event_logger.log_subscribe(event_type, handler.__name__)
        logger.debug(
            f"Subscribed handler {handler.__name__} to event {event_type} "
            f"(priority={priority})"
        )
    
    async def unsubscribe(
        self,
        event_type: str,
        handler: EventHandler
    ) -> bool:
        """Remove a handler from one event type."""
        async with self._lock:
            if event_type not in self._handlers:
                return False

            try:
                idx = self._handlers[event_type].index(handler)
                self._handlers[event_type].pop(idx)
                prios = self._handler_priorities.get(event_type)
                if prios is not None and idx < len(prios):
                    prios.pop(idx)
                removed = True
            except ValueError:
                removed = False

        if removed:
            # Log unsubscription OUTSIDE the lock (file I/O must not hold it)
            await self._event_logger.log_unsubscribe(event_type, handler.__name__)
            logger.debug(f"Unsubscribed handler {handler.__name__} from event {event_type}")
            return True
        return False
    
    async def subscribe_all(self, handler: EventHandler) -> None:
        """Register a handler that receives every event on the bus."""
        async with self._lock:
            self._global_handlers.append(handler)
            logger.debug(f"Subscribed global handler {handler.__name__}")
    
    async def unsubscribe_all(self, handler: EventHandler) -> bool:
        """Remove a previously registered global handler."""
        async with self._lock:
            try:
                self._global_handlers.remove(handler)
                logger.debug(f"Unsubscribed global handler {handler.__name__}")
                return True
            except ValueError:
                return False
    
    async def _execute_handler(self, handler: EventHandler, event: Event) -> None:
        """Run one handler, isolating its failure from the publisher."""
        import time
        
        handler_name = handler.__name__
        start_time = time.time()
        success = True
        error_msg = None
        
        try:
            await handler(event)
        except Exception as e:
            success = False
            error_msg = str(e)
            logger.error(f"Error in event handler {handler_name}: {e}")
        finally:
            # Fire-and-forget audit write: log I/O must never gate the publish
            # path, so the coroutine is scheduled instead of awaited.
            duration_ms = (time.time() - start_time) * 1000
            asyncio.ensure_future(
                self._event_logger.log_handler_execution(
                    event.event_type, handler_name, success, duration_ms, error_msg
                )
            )
    
    def get_subscribers(self, event_type: str) -> List[EventHandler]:
        """Return a snapshot of the handlers registered for one event type."""
        return self._handlers.get(event_type, []).copy()
    
    def get_event_history(self, event_type: Optional[str] = None) -> List[Event]:
        """Return buffered history, newest last."""
        if event_type:
            return [e for e in self._event_history if e.event_type == event_type]
        return list(self._event_history)
    
    def clear_history(self) -> None:
        """Drop every buffered event, keeping the handler table intact."""
        self._event_history.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Summarise the current wiring for diagnostics endpoints."""
        return {
            "total_handlers": sum(len(handlers) for handlers in self._handlers.values()),
            "event_types": list(self._handlers.keys()),
            "global_handlers": len(self._global_handlers),
            "history_size": len(self._event_history)
        }


class EventBusError(Exception):
    """Raised when bus wiring cannot be completed."""


# Process-wide singleton; created lazily on the first get_event_bus() call.
_event_bus: Optional[EventBus] = None


def get_event_bus(event_log_file: Optional[Path] = None) -> EventBus:
    """Return the process-wide bus, creating it on first use."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus(event_log_file)
    elif event_log_file is not None and _event_bus._event_logger._log_file != event_log_file:
        logger.warning(
            "get_event_bus called with event_log_file=%s but bus already "
            "configured with %s — ignoring",
            event_log_file, _event_bus._event_logger._log_file,
        )
    return _event_bus


async def publish_event(event_type: str, payload: Dict[str, Any], source: str = "") -> None:
    """Publish on the shared bus without fetching the singleton first."""
    event = Event(event_type=event_type, payload=payload, source=source)
    await get_event_bus().publish(event)