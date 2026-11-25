"""Access gate for the HTTP + WebSocket edge.

Single source of truth for "may this request proceed?". Both the middleware in
:mod:`boarai.core.server.unified_server` and ``GET /api/auth/status`` ask this
module, so the page can never disagree with the transport about whether a
credential is required.

Lives under ``core.security`` (not ``core.server``) because the API handler
imports it: ``core.server`` → ``core.api`` → ``core.server`` would be a cycle.

Scope (deliberate): HTTP routes and the WebSocket upgrade only. Process
management, extension loading, scheduled tasks, MCP subprocesses and LLM
egress never consult the gate — a browser credential must not be able to stop
a running agent.

Decision lattice, first match wins:

    public path          ``/``, ``/static/**``, ``/auth/launch``, the two
                         auth endpoints the unlock page calls
    loopback             ``trust_loopback`` and the peer is 127.0.0.1/::1
    session              valid signed cookie (the normal browser case)
    throttle             a presented key while the source is in backoff → 429
    key                  ``Authorization: Bearer`` / ``Basic`` (scripts, curl)
    ticket               single-use WebSocket upgrade ticket
    denied               401 (+ advisory ``Retry-After`` on a fresh failure)

A *wrong* bearer/basic credential counts as a failed attempt; a stale or
forged cookie does not (the browser simply re-authenticates, and counting it
would lock the operator out of their own machine). The backoff is checked
**before** verification, so it actually rate-limits guessing instead of merely
annotating the answer.
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from aiohttp import web

from boarai.core.security.access import SESSION_COOKIE, AccessStore, get_access_store

logger = logging.getLogger(__name__)

#: Console-printed one-shot hand-off: ``/auth/launch?key=<process token>``.
LAUNCH_PATH = "/auth/launch"

#: Public ``(method, path)`` pairs. The SPA shell must load before a session
#: exists, otherwise the unlock page could never render.
_PUBLIC_EXACT: frozenset[Tuple[str, str]] = frozenset(
    {
        ("GET", "/"),
        ("GET", "/favicon.ico"),
        ("GET", "/api/auth/status"),
        ("POST", "/api/auth/session"),
        ("GET", LAUNCH_PATH),
        # Local tools (boarai stop/restart, the update worker) read the process
        # token here. The handler itself refuses every non-loopback peer, so
        # the gate only has to stop it from answering 401 first.
        ("GET", "/api/auth/token"),
        # First-run setup: claim the key. Loopback needs nothing extra; a
        # remote peer must add the console-printed one-shot setup code
        # (checked in the handler, so the gate stays public here).
        ("POST", "/api/auth/setup"),
        # Recovery path for "the terminal never showed the code": loopback-
        # only in the handler (same trust domain as /api/auth/token).
        ("GET", "/api/auth/setup-code"),
    }
)

#: Prefixes served without a credential (hashed build output only).
_PUBLIC_PREFIXES = ("/static/",)

_BEARER_PREFIX = "boarai-auth."
_TICKET_PREFIX = "boarai-ticket."


@dataclass(frozen=True)
class GateDecision:
    """Outcome of one request's access check."""

    allowed: bool
    mode: str
    """``public`` | ``loopback`` | ``session`` | ``key`` | ``ticket`` | ``denied``."""
    required: bool
    """Whether this request needed (and satisfied) a credential."""
    session: Optional[Dict[str, Any]] = None
    retry_after: int = 0
    """Seconds the source must wait. On a *fresh* failure this is advisory
    (the answer is still 401 + ``Retry-After``); ``throttled`` marks the case
    where the attempt was refused without even being checked."""
    throttled: bool = False

    @property
    def session_id(self) -> Optional[str]:
        return (self.session or {}).get("id")


class AccessGate:
    """Credential decision logic; stateless apart from the shared store."""

    def __init__(self, store: Optional[AccessStore] = None) -> None:
        self._store = store

    @property
    def store(self) -> AccessStore:
        if self._store is None:
            self._store = get_access_store()
        return self._store

    # ---- configuration ----

    def settings(self):
        """Effective ``WebAccessConfig`` (defaults when nothing is loaded)."""
        from boarai.core.config.schema import WebAccessConfig

        try:
            from boarai.core.config import get_config_parser

            parser = get_config_parser()
            loaded = getattr(parser, "_config", None) or getattr(parser, "config", None)
            web_cfg = getattr(getattr(loaded, "security", None), "web", None)
            if isinstance(web_cfg, WebAccessConfig):
                return web_cfg
        except Exception as exc:  # noqa: BLE001 - defaults are always usable
            logger.debug("Access gate using default settings: %s", exc)
        return WebAccessConfig()

    # ---- primitives ----

    @staticmethod
    def is_public(method: str, path: str) -> bool:
        """Whether a path is reachable before any credential exists."""
        verb = "GET" if method.upper() == "HEAD" else method.upper()
        if (verb, path) in _PUBLIC_EXACT:
            return True
        return any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES)

    @staticmethod
    def peer_ip(request: web.Request) -> str:
        """Peer address used for throttling and the device list."""
        transport = request.transport
        peer = transport.get_extra_info("peername") if transport is not None else None
        if peer:
            return str(peer[0])
        return str(request.remote or "")

    @classmethod
    def is_loopback(cls, request: web.Request) -> bool:
        """Whether the request arrived over the loopback interface."""
        candidate = cls.peer_ip(request).split("%", 1)[0]
        try:
            return ipaddress.ip_address(candidate).is_loopback
        except ValueError:
            return False

    # ---- credentials ----

    @staticmethod
    def _presented_key(request: web.Request) -> Tuple[str, str]:
        """Extract ``(scheme, credential)`` from the Authorization header."""
        header = request.headers.get("Authorization", "")
        scheme, _, credential = header.partition(" ")
        scheme = scheme.strip().lower()
        credential = credential.strip()
        if scheme == "basic" and credential:
            try:
                import base64

                decoded = base64.b64decode(credential, validate=True).decode(
                    "utf-8", "replace"
                )
            except Exception:  # noqa: BLE001
                return "basic", ""
            # ``-u boar:<key>`` — the username is fixed, the key is the secret.
            _, _, credential = decoded.partition(":")
        return scheme, credential

    @staticmethod
    def _ws_protocols(request: web.Request) -> list:
        raw = request.headers.get("Sec-WebSocket-Protocol", "")
        return [item.strip() for item in raw.split(",") if item.strip()]

    def _check_ws_ticket(self, request: web.Request) -> bool:
        for protocol in self._ws_protocols(request):
            if protocol.startswith(_TICKET_PREFIX):
                return self.store.consume_ticket(protocol[len(_TICKET_PREFIX):])
        return False

    def _legacy_ws_key(self, request: web.Request) -> str:
        """Pre-gate pages passed the key itself as a subprotocol."""
        for protocol in self._ws_protocols(request):
            if protocol.startswith(_BEARER_PREFIX):
                return protocol[len(_BEARER_PREFIX):]
        return ""

    # ---- decision ----

    def authorize(self, request: web.Request) -> GateDecision:
        """Decide whether ``request`` may reach its handler."""
        path = request.path
        method = request.method.upper()

        if self.is_public(method, path):
            return GateDecision(True, "public", required=False)

        store = self.store
        if not store.configured:
            # First run: nobody has claimed the instance yet. Everything except
            # the public shell and the setup endpoints stays CLOSED — an
            # unclaimed server must not be usable by whoever reaches it first.
            # ``POST /api/auth/setup`` is public at the gate; the handler lets
            # loopback claim freely but demands the console-printed one-shot
            # setup code from any remote peer, so merely reaching the box is
            # still not enough to take it.
            #
            # One exception, and it is the documented boundary: process
            # management must keep working. `boarai stop|restart` and the update
            # worker dial loopback with the process launch token; refusing them
            # here would downgrade a graceful shutdown to a force-kill. The
            # token is only obtainable from loopback, i.e. from the same trust
            # domain that is allowed to claim the key.
            if self.is_loopback(request):
                _scheme, credential = self._presented_key(request)
                if credential and store.check_launch_token(credential):
                    return GateDecision(True, "key", required=True)
            return GateDecision(False, "setup_required", required=True)

        settings = self.settings()
        loopback = self.is_loopback(request)
        if settings.trust_loopback and loopback:
            return GateDecision(True, "loopback", required=False)

        # A signed cookie is not a guess: verifying it costs an HMAC, and
        # throttling it would let anyone on the same NAT lock the operator out
        # of their own browser.
        record = store.verify_session(request.cookies.get(SESSION_COOKIE))
        if record:
            return GateDecision(True, "session", required=True, session=record)

        ip = self.peer_ip(request)
        scheme, credential = self._presented_key(request)
        legacy_ws = (
            self._legacy_ws_key(request) if path == "/ws" and not credential else ""
        )
        guess = credential or legacy_ws

        if guess:
            # Every presented key is a guess, so it shares the login form's
            # backoff — and the backoff is checked BEFORE verifying, otherwise
            # the throttle is advisory and the caller can brute-force at full
            # speed (each attempt also burns a scrypt on the event loop).
            wait = store.retry_after(ip)
            if wait > 0:
                return GateDecision(
                    False, "denied", required=True, retry_after=wait, throttled=True
                )
            if store.check_key(guess):
                store.record_success(ip)
                return GateDecision(True, "key", required=True)
            # Local CLI / updater: they read the process launch token from
            # /api/auth/token over loopback. Honoured only from loopback, so
            # the token never becomes a remote credential even when
            # `trust_loopback` is off (that setting is about *no* credential).
            if loopback and store.check_launch_token(guess):
                return GateDecision(True, "key", required=True)
            if credential and scheme not in ("bearer", "basic"):
                return GateDecision(False, "denied", required=True)
            delay = store.record_failure(ip)
            # Fresh failure: 401 with an advisory Retry-After, not 429. The
            # next attempt inside the window is the one that gets throttled.
            return GateDecision(False, "denied", required=True, retry_after=delay)

        if path == "/ws" and self._check_ws_ticket(request):
            # A ticket is 24 random bytes, single-use and 30 s lived: not
            # guessable, so it does not consume the password backoff.
            return GateDecision(True, "ticket", required=True)

        return GateDecision(False, "denied", required=True)


_gate: Optional[AccessGate] = None


def get_access_gate() -> AccessGate:
    """Process-wide gate (shares the access store singleton)."""
    global _gate
    if _gate is None:
        _gate = AccessGate()
    return _gate


def reset_access_gate() -> None:
    """Drop the singleton (tests)."""
    global _gate
    _gate = None


__all__ = [
    "AccessGate",
    "GateDecision",
    "LAUNCH_PATH",
    "get_access_gate",
    "reset_access_gate",
]
