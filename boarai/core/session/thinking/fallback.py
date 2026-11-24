"""Fallback and resilience system for BoarAi.

A three-state fallback machine that switches model/provider when the primary
fails repeatedly, then probes for recovery after a cooldown.

Architecture:
    primary_ok --3 consecutive failures--> fallback_active --30 min cooldown--> probing
        ^                                                                                |
        +----------------------------- successful probe ----------------------------------+

Also provides:
- Compressor error resilience (graceful degradation)
- Stream continuation recovery (network interruption handling)
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class FallbackState(str, Enum):
    """State of a provider/model in the fallback machine."""
    PRIMARY_OK = "primary_ok"
    FALLBACK_ACTIVE = "fallback_active"
    PROBING = "probing"


@dataclass
class FallbackConfig:
    """Configuration for the fallback state machine."""
    enabled: bool = True
    consecutive_failures: int = 3
    """Number of consecutive failures before triggering fallback."""
    cooldown_seconds: int = 1800
    """Seconds in fallback state before attempting probe (30 min default)."""
    probing_interval: int = 300
    """Interval between probe attempts in seconds (5 min default)."""
    fallback_chain: List[str] = field(default_factory=list)
    """Ordered list of fallback model names. Empty = retry only, no model change."""
    max_fallback_turns: int = 5
    """Maximum turns to stay in fallback before forcing primary retry."""


@dataclass
class FallbackStateEntry:
    """Runtime state for a single session+provider pair."""
    state: FallbackState = FallbackState.PRIMARY_OK
    failure_count: int = 0
    last_failure_at: float = 0.0
    last_fallback_at: float = 0.0
    current_fallback: str = ""
    """Active fallback model name (empty = no fallback)."""
    fallback_turns: int = 0
    """Turns spent in fallback state."""
    probe_attempts: int = 0
    """Probe attempts made in current probing cycle."""


class FallbackStateMachine:
    """Three-state fallback machine for model/provider resilience."""

    def __init__(self, config: Optional[FallbackConfig] = None) -> None:
        self._config = config or FallbackConfig()
        self._entries: Dict[str, FallbackStateEntry] = {}
        """key = f"{session_id}:{provider_name}" """

    # ---- Public API ----

    def record_failure(
        self,
        session_id: str,
        provider: str,
        model: str,
    ) -> str:
        """Record a failure and return the suggested model to use next."""
        if not self._config.enabled:
            return model

        entry = self._get_entry(session_id, provider)
        entry.failure_count += 1
        entry.last_failure_at = time.time()

        logger.debug(
            f"Fallback[{session_id}:{provider}]: failure #{entry.failure_count}, "
            f"state={entry.state.value}"
        )

        if entry.state == FallbackState.FALLBACK_ACTIVE:
            # Already in fallback — check cooldown for probing
            elapsed = time.time() - entry.last_fallback_at
            if elapsed > self._config.cooldown_seconds:
                entry.state = FallbackState.PROBING
                logger.info(
                    f"Fallback[{session_id}:{provider}]: cooldown elapsed, "
                    f"entering PROBING state"
                )
                return model  # Try primary model for probing
            # Stay in fallback
            entry.fallback_turns += 1
            if entry.fallback_turns > self._config.max_fallback_turns:
                # Force probe even if cooldown not elapsed
                entry.state = FallbackState.PROBING
                logger.info(
                    f"Fallback[{session_id}:{provider}]: max fallback turns "
                    f"reached, forcing PROBE"
                )
                return model
            return self._get_fallback_model(entry, model)

        if entry.state == FallbackState.PROBING:
            # Probing failed — back to fallback
            entry.state = FallbackState.FALLBACK_ACTIVE
            entry.last_fallback_at = time.time()
            entry.probe_attempts = 0
            return self._get_fallback_model(entry, model)

        # PRIMARY_OK state
        if entry.failure_count >= self._config.consecutive_failures:
            # Trigger fallback
            entry.state = FallbackState.FALLBACK_ACTIVE
            entry.last_fallback_at = time.time()
            entry.fallback_turns = 1
            fallback_model = self._get_fallback_model(entry, model)
            logger.info(
                f"Fallback[{session_id}:{provider}]: {entry.failure_count} failures, "
                f"entering FALLBACK state: {model} → {fallback_model}"
            )
            return fallback_model

        return model  # Stay on same model, retry

    def record_success(
        self,
        session_id: str,
        provider: str,
        model: str,
    ) -> None:
        """Record a successful call — reset failure count and check probing."""
        if not self._config.enabled:
            return

        entry = self._get_entry(session_id, provider)

        if entry.state == FallbackState.PROBING:
            # Primary model recovered
            entry.state = FallbackState.PRIMARY_OK
            entry.current_fallback = ""
            logger.info(
                f"Fallback[{session_id}:{provider}]: probe SUCCESS, "
                f"returning to PRIMARY_OK"
            )

        entry.failure_count = 0
        entry.fallback_turns = 0
        entry.probe_attempts = 0

    def get_suggested_model(
        self,
        session_id: str,
        provider: str,
        default_model: str,
    ) -> str:
        """Get the current suggested model for this session+provider."""
        if not self._config.enabled:
            return default_model

        entry = self._get_entry(session_id, provider)

        if entry.state in (FallbackState.FALLBACK_ACTIVE,):
            return self._get_fallback_model(entry, default_model)

        return default_model

    def fallback_models(self) -> List[str]:
        """The configured fallback chain, as a read-only copy."""
        return list(self._config.fallback_chain)

    def get_state(self, session_id: str, provider: str) -> FallbackState:
        """Get current state for debugging/monitoring."""
        entry = self._get_entry(session_id, provider)
        return entry.state

    def get_stats(self) -> Dict[str, Any]:
        """Get fallback statistics for dashboard/monitoring."""
        stats = {}
        for key, entry in self._entries.items():
            stats[key] = {
                "state": entry.state.value,
                "failure_count": entry.failure_count,
                "fallback_model": entry.current_fallback or "none",
                "fallback_turns": entry.fallback_turns,
            }
        return stats

    def reset(self, session_id: str, provider: str) -> None:
        """Reset state for a session+provider pair."""
        key = f"{session_id}:{provider}"
        self._entries.pop(key, None)

    def reset_all(self) -> None:
        """Reset all states (e.g., after configuration change)."""
        self._entries.clear()

    # ---- Internal ----

    def _get_entry(self, session_id: str, provider: str) -> FallbackStateEntry:
        """Get or create state entry for a session+provider pair."""
        key = f"{session_id}:{provider}"
        if key not in self._entries:
            self._entries[key] = FallbackStateEntry()
        return self._entries[key]

    def _get_fallback_model(self, entry: FallbackStateEntry, current: str) -> str:
        """Get the next fallback model from the chain."""
        if not self._config.fallback_chain:
            return current  # No fallback chain configured → keep current

        if entry.current_fallback:
            # Already using a fallback — check if we can advance
            try:
                idx = self._config.fallback_chain.index(entry.current_fallback)
                if idx + 1 < len(self._config.fallback_chain):
                    next_model = self._config.fallback_chain[idx + 1]
                    entry.current_fallback = next_model
                    return next_model
            except ValueError:
                pass
            return entry.current_fallback  # Already at last resort

        # First fallback — take from chain
        first = self._config.fallback_chain[0]
        entry.current_fallback = first
        return first


# ==================== Compressor Resilience ====================


async def safe_compress(
    compressor: Any,
    session_messages: List[Dict[str, str]],
    zone1_zone2: List[Dict[str, str]],
    llm_service: Any,
    model: Optional[str] = None,
    memory_context: Optional[Dict[str, str]] = None,
    task_context: Optional[Dict[str, Any]] = None,
    max_retries: int = 2,
) -> Optional[Dict[str, Any]]:
    """Safely execute Insert-then-Compress with graceful degradation."""
    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            result = await compressor.compress(
                session_messages=session_messages,
                zone1_zone2=zone1_zone2,
                llm_service=llm_service,
                model=model,
                memory_context=memory_context,
                task_context=task_context,
            )
            if attempt > 0:
                logger.info(f"LLM compression succeeded on attempt {attempt + 1}")
            return result

        except Exception as e:
            last_error = e
            if attempt < max_retries:
                delay = 0.5 * (2 ** attempt)  # 0.5s, 1.0s
                logger.warning(
                    f"LLM compression attempt {attempt + 1}/{max_retries + 1} "
                    f"failed: {e}, retrying in {delay:.1f}s"
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    f"LLM compression failed after {max_retries + 1} attempts: "
                    f"{last_error}, skipping compression"
                )

    return None


# ==================== Stream Continuation ====================

CONTINUATION_PROMPT = (
    "\n\n[BoarAi_System] The previous response was interrupted before completion. "
    "Continue from where you left off. Do NOT repeat content already shown above. "
    "Mark the continuation clearly with '[Continued...]' at the start."
)


def build_continuation_messages(
    original_messages: List[Dict[str, Any]],
    partial_response: str,
) -> List[Dict[str, Any]]:
    """Build messages for stream continuation after interruption."""
    messages = list(original_messages)
    if partial_response:
        messages.append({
            "role": "assistant",
            "content": partial_response,
        })
        messages.append({
            "role": "user",
            "content": CONTINUATION_PROMPT,
        })
    return messages


# ==================== Global singleton ====================

_fallback_machine: Optional[FallbackStateMachine] = None


def get_fallback_machine() -> FallbackStateMachine:
    """Get the global fallback state machine instance."""
    global _fallback_machine
    if _fallback_machine is None:
        _fallback_machine = FallbackStateMachine()
    return _fallback_machine


def reset_fallback_machine() -> None:
    """Reset the global fallback machine (for testing)."""
    global _fallback_machine
    if _fallback_machine:
        _fallback_machine.reset_all()
    _fallback_machine = FallbackStateMachine()
