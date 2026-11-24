"""
Shared HTTP plumbing for the aiohttp-based LLM providers.

An OpenAI-compatible endpoint and the Anthropic Messages API differ in body
shape, usage keys and stream events, but the operational envelope is the same:

* one lazily-created ``aiohttp.ClientSession`` per provider, with the idle-read
  timeout contract (``sock_read`` bounds the gap BETWEEN chunks; ``total`` is
  None so a long generation or a long thinking phase is never killed);
* exponential backoff over the same four failure classes — HTTP status,
  ``LLMError``, idle timeout, transport error — plus the optional ``on_retry``
  callback the run layer uses to surface "retrying" to the UI;
* one mapping from a non-200 body to ``LLMError``.

Each of those used to be copied per provider and per code path inside a
provider (16 backoff ladders in total). This module is the single
implementation; subclasses own everything provider-specific — request body,
response/stream parsing, usage keys, and which error bodies mean "retry
without images" (:meth:`_looks_like_vision_rejection`).

Subclasses provide these attributes from their own ``__init__``:
``_name``, ``_timeout``, ``_max_retries``, ``_session``, ``_session_lock``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp

from ..protocol import LLMError, LLMProvider, LLMRequest

logger = logging.getLogger(__name__)


class HTTPProviderBase(LLMProvider):
    """Base for providers that speak JSON over ``aiohttp``."""

    # Supplied by subclasses (see the module docstring).
    _name: str
    _timeout: float
    _max_retries: int
    _session: Optional[aiohttp.ClientSession]
    _session_lock: asyncio.Lock

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the HTTP session (thread-safe with a lock)."""
        async with self._session_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(
                        total=None,
                        connect=15.0,
                        sock_read=self._timeout,
                    ),
                )
            return self._session

    async def close(self) -> None:
        """Close the HTTP session (idempotent)."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _notify_retry(
        self,
        request: LLMRequest,
        attempt: int,
        error: Exception,
        delay_seconds: float,
    ) -> None:
        """Invoke the optional ``on_retry`` callback before sleeping."""
        cb = getattr(request, "on_retry", None)
        if not cb:
            return
        try:
            result = cb(
                attempt=attempt,
                max_attempts=self._max_retries,
                error=str(error),
                delay_seconds=delay_seconds,
            )
            if asyncio.iscoroutine(result):
                await result
        except Exception as cb_err:  # noqa: BLE001 — a callback must not break a run
            logger.debug("on_retry callback failed: %s", cb_err)

    async def _retry_delay(
        self,
        request: LLMRequest,
        attempt: int,
        error: Exception,
        *,
        phase: str = "Request",
        detail: str = "",
    ) -> None:
        """One backoff step: log, notify, sleep."""
        delay = float(2 ** attempt)
        suffix = f" ({detail})" if detail else ""
        logger.warning(
            "%s attempt %s failed%s: %s, retrying in %.0fs...",
            phase,
            attempt + 1,
            suffix,
            error,
            delay,
        )
        await self._notify_retry(request, attempt + 1, error, delay)
        await asyncio.sleep(delay)

    def _looks_like_vision_rejection(self, error_text: str) -> bool:
        """Whether a non-200 body means "this model cannot take image blocks"."""
        return False

    def _http_error(
        self, status: int, error_text: str, request: LLMRequest
    ) -> LLMError:
        """Map a non-200 body to ``LLMError`` (status → retryable)."""
        err = LLMError(
            f"API request failed with status {status}: {error_text}",
            self._name,
            request.model,
            status_code=status,
        )
        if self._looks_like_vision_rejection(error_text):
            err.vision_rejected = True
        return err
