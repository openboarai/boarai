"""
Idle compression timer for BoarAi — OpenClacky-inspired 266s delay.

Triggers conversation history compression after a period of inactivity.
The 266-second delay is intentionally below Anthropic's 5-minute cache TTL,
ensuring compression happens WHILE the cache is still warm.

Rationale:
    Anthropic cache TTL = ~5 minutes (300 seconds)
    OpenClacky idle delay = 266 seconds (~4.4 minutes)
    
    By compressing before the cache expires, the compression LLM call
    reuses the Z1+Z2 cache → compression is nearly FREE.

Key behaviors:
    - Each session has exactly ONE timer
    - New user activity cancels & restarts the timer
    - Timer execution triggers InsertThenCompress
    - On server stop, all timers are cancelled
"""

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

IDLE_DELAY = 300  # seconds (5 min, aligns with Anthropic cache TTL)


class IdleCompressionTimer:
    """Per-session idle compression timer."""

    def __init__(self, idle_delay: int = IDLE_DELAY):
        """Initialize the timer manager."""
        self._idle_delay = idle_delay
        self._timers: Dict[str, asyncio.Task] = {}
        self._callbacks: Dict[str, Callable] = {}

    def start(
        self,
        session_id: str,
        on_compress: Callable,
    ) -> None:
        """Start or restart the idle timer for a session."""
        # Cancel existing timer first
        self.cancel(session_id)

        # Store callback
        self._callbacks[session_id] = on_compress

        # Create new timer task
        self._timers[session_id] = asyncio.create_task(
            self._run_timer(session_id)
        )

        logger.debug(f"Started idle timer for session {session_id} ({self._idle_delay}s)")

    def cancel(self, session_id: str) -> None:
        """Cancel the idle timer for a session."""
        task = self._timers.pop(session_id, None)
        if task and not task.done():
            task.cancel()

        self._callbacks.pop(session_id, None)

    def reset(
        self,
        session_id: str,
        on_compress: Optional[Callable] = None,
    ) -> None:
        """Reset the timer — cancel old, start new."""
        callback = on_compress or self._callbacks.get(session_id)
        if callback:
            self.start(session_id, callback)
        else:
            logger.debug(
                f"Timer reset skipped for session {session_id}: no callback registered"
            )

    async def _run_timer(self, session_id: str) -> None:
        """Async timer task — sleeps then fires callback."""
        try:
            await asyncio.sleep(self._idle_delay)

            callback = self._callbacks.get(session_id)
            if callback:
                logger.info(
                    f"Idle timer fired for session {session_id} "
                    f"({self._idle_delay}s inactivity)"
                )
                if asyncio.iscoroutinefunction(callback):
                    await callback(session_id)
                else:
                    callback(session_id)

        except asyncio.CancelledError:
            logger.debug(f"Idle timer cancelled for session {session_id}")
            pass
        except Exception as e:
            logger.error(f"Idle timer error for session {session_id}: {e}")

    async def cancel_all(self) -> None:
        """Cancel all active timers. Called on server shutdown."""
        session_ids = list(self._timers.keys())
        for sid in session_ids:
            self.cancel(sid)
        logger.info(f"Cancelled all {len(session_ids)} idle timers")

    def is_active(self, session_id: str) -> bool:
        """Check if a session has an active timer."""
        task = self._timers.get(session_id)
        return task is not None and not task.done()

    @property
    def active_count(self) -> int:
        """Number of currently active timers."""
        return len([t for t in self._timers.values() if not t.done()])


# Global instance
_idle_timer: Optional[IdleCompressionTimer] = None


def get_idle_timer() -> IdleCompressionTimer:
    """Get the global idle compression timer instance."""
    global _idle_timer
    if _idle_timer is None:
        _idle_timer = IdleCompressionTimer()
    return _idle_timer
