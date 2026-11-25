"""Access-key endpoints for the WebUI edge.

Everything here is transport-level: mint a device session from the access key,
list/revoke sessions, rotate the key, mint WebSocket tickets. No session,
run, tool or LLM code is involved.

Response envelope matches the rest of the API (``{"success", "data"}``) so the
WebUI store can treat auth like any other endpoint. Error bodies always carry
``error.code`` — the frontend keys off the code, never the message.

Endpoints::

    GET    /api/auth/status              public   gate state for this browser
    POST   /api/auth/setup               public   first-run claim; remote peers
                                                  add the console setup code
    POST   /api/auth/session             public   {key, ttl_seconds, remember}
    DELETE /api/auth/session             auth     log this device out
    POST   /api/auth/ws-ticket           auth     single-use upgrade ticket
    GET    /api/auth/sessions            auth     device list
    DELETE /api/auth/sessions/{id}       auth     revoke one device
    POST   /api/auth/key/rotate          auth     new key, all devices dropped
    GET    /api/auth/settings            auth     session lifetime policy
    PUT    /api/auth/settings            auth     {session_ttl_seconds, sliding}
    GET    /auth/launch?key=<token>      public   console hand-off → cookie
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from aiohttp import web

from boarai.core.event import Event, EventType, get_event_bus
from boarai.core.security.access import (
    DEFAULT_TTL_SECONDS,
    MAX_TTL_SECONDS,
    MIN_KEY_LENGTH,
    ROTATION_PENDING_SECONDS,
    SESSION_COOKIE,
    get_access_store,
)
from boarai.core.security.gate import get_access_gate

logger = logging.getLogger(__name__)

#: Lifetimes the unlock page may offer, filtered by the configured maximum.
_TTL_OPTIONS: tuple = (3600, 8 * 3600, 86400, 604800, 2592000)


class AuthAPIHandler:
    """Access-key HTTP endpoints (registered by ``UnifiedServer``)."""

    def __init__(self) -> None:
        self._store = get_access_store()
        self._gate = get_access_gate()
        self._event_bus = get_event_bus()

    # ==================== Helpers ====================

    @staticmethod
    def _ok(data: Any = None, status: int = 200) -> web.Response:
        return web.json_response({"success": True, "data": data}, status=status)

    @staticmethod
    def _err(status: int, code: str, message: str, **extra: Any) -> web.Response:
        body: Dict[str, Any] = {"success": False, "error": {"code": code, "message": message}}
        body["error"].update(extra)
        return web.json_response(body, status=status)

    @staticmethod
    def _client_ip(request: web.Request) -> str:
        return get_access_gate().peer_ip(request)

    def _ttl_seconds(self, requested: Optional[Any]) -> int:
        """Clamp a requested lifetime into the configured policy."""
        settings = self._gate.settings()
        default = int(getattr(settings, "session_ttl_seconds", DEFAULT_TTL_SECONDS) or DEFAULT_TTL_SECONDS)
        maximum = int(getattr(settings, "session_ttl_max_seconds", MAX_TTL_SECONDS) or MAX_TTL_SECONDS)
        default = max(60, min(default, maximum, MAX_TTL_SECONDS))
        if requested is None:
            return default
        try:
            value = int(requested)
        except (TypeError, ValueError):
            return default
        return max(60, min(value, maximum, MAX_TTL_SECONDS))

    def _set_session_cookie(
        self,
        response: web.Response,
        request: web.Request,
        value: str,
        *,
        max_age: Optional[int],
    ) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            value,
            max_age=max_age,
            path="/",
            httponly=True,
            secure=request.secure,
            samesite="Strict",
        )

    def _current_session(self, request: web.Request) -> Optional[Dict[str, Any]]:
        return self._store.verify_session(request.cookies.get(SESSION_COOKIE))

    async def _publish_revoked(self, reason: str, session_ids: Optional[List[str]] = None) -> None:
        """Tell every connected client to re-authenticate."""
        try:
            await self._event_bus.publish(
                Event(
                    event_type=EventType.AUTH_REVOKED,
                    payload={
                        "reason": reason,
                        "session_ids": session_ids or [],
                    },
                    source="auth_api",
                )
            )
        except Exception as exc:  # noqa: BLE001 - never fail the request over a broadcast
            logger.warning("Failed to publish auth.revoked: %s", exc)

    # ==================== Status ====================

    async def handle_auth_status(self, request: web.Request) -> web.Response:
        """GET /api/auth/status — what the unlock gate should show."""
        try:
            settings = self._gate.settings()
            session = self._current_session(request)
            loopback = self._gate.is_loopback(request)
            key_configured = self._store.configured
            bypass = bool(getattr(settings, "trust_loopback", True)) and loopback

            # Deliberately does NOT verify an Authorization header. This route
            # is public, so accepting a key here would expose an unthrottled
            # guess oracle (and burn a scrypt per probe on the event loop).
            # Scripts authenticate on the gated routes themselves; this
            # endpoint only answers "does this browser need to unlock?".
            # While unclaimed there is nothing to unlock and nothing to trust:
            # `trust_loopback` must NOT hand out access before a key exists.
            authenticated = (bool(session) or bypass) and key_configured
            maximum = int(
                getattr(settings, "session_ttl_max_seconds", MAX_TTL_SECONDS)
                or MAX_TTL_SECONDS
            )
            default_ttl = int(
                getattr(settings, "session_ttl_seconds", DEFAULT_TTL_SECONDS)
                or DEFAULT_TTL_SECONDS
            )
            response = self._ok(
                {
                    "key_configured": key_configured,
                    "key_source": self._store.source,
                    "loopback_bypass": bypass,
                    "authenticated": authenticated,
                    "must_unlock": key_configured and not authenticated,
                    # First run: no key exists yet, so the SPA shows the setup
                    # page (create a key) rather than the unlock page (type one).
                    "must_setup": not key_configured,
                    # The instance is still claimable via the setup page.
                    "setup_allowed": not key_configured,
                    # Remote browsers may claim too, but only with the
                    # console-printed one-shot code; loopback needs nothing.
                    "setup_code_required": (not key_configured) and not loopback,
                    "min_key_length": MIN_KEY_LENGTH,
                    "expires_at": float(session["expires_at"]) if session else None,
                    "session_id": session.get("id") if session else None,
                    "ttl_seconds": max(60, min(default_ttl, maximum, MAX_TTL_SECONDS)),
                    "ttl_max_seconds": max(60, min(maximum, MAX_TTL_SECONDS)),
                    "ttl_options": [
                        value
                        for value in _TTL_OPTIONS
                        if value <= max(60, min(maximum, MAX_TTL_SECONDS))
                    ],
                    "sliding": bool(getattr(settings, "sliding", False)),
                    "generation": self._store.generation,
                }
            )
            response.headers["Cache-Control"] = "no-store"
            return response
        except Exception as exc:  # noqa: BLE001
            logger.error("auth status failed: %s", exc)
            return self._err(500, "INTERNAL_ERROR", "Could not read access state")

    # ==================== First-run setup ====================

    async def handle_auth_setup(self, request: web.Request) -> web.Response:
        """POST /api/auth/setup — create the access key on first run.

        Loopback claims need nothing extra. A remote claim must present the
        one-shot setup code printed in the server startup log, so "whoever
        reaches an unclaimed box first" still cannot take it — only whoever
        can read the console can.
        """
        if self._store.configured:
            return self._err(
                409, "auth_already_configured", "An access key already exists"
            )
        ip = self._client_ip(request)
        loopback = self._gate.is_loopback(request)
        if not loopback:
            wait = self._store.retry_after(ip)
            if wait > 0:
                response = self._err(
                    429, "auth_throttled", "Too many failed attempts", retry_after=wait
                )
                response.headers["Retry-After"] = str(wait)
                return response

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return self._err(400, "INVALID_JSON", "Request body must be valid JSON")
        if not isinstance(body, dict):
            return self._err(400, "INVALID_REQUEST", "Body must be a JSON object")

        if not loopback:
            code = str(body.get("setup_code") or "").strip()
            if not code:
                return self._err(
                    403,
                    "auth_setup_code_required",
                    "Remote first-run setup needs the setup code printed in "
                    "the server startup log",
                )
            if not self._store.check_setup_code(code):
                delay = self._store.record_failure(ip)
                response = self._err(
                    403,
                    "auth_setup_code_invalid",
                    "That setup code is not correct",
                    retry_after=delay,
                )
                if delay > 0:
                    response.headers["Retry-After"] = str(delay)
                return response

        key = str(body.get("key") or "").strip()
        if not key:
            return self._err(400, "auth_key_required", "Access key is required")

        ttl = self._ttl_seconds(body.get("ttl_seconds"))
        remember = body.get("remember", True) is not False
        try:
            value, record = self._store.claim_key(
                key,
                ttl,
                remember=remember,
                user_agent=request.headers.get("User-Agent", ""),
                ip=ip,
            )
        except ValueError as exc:
            return self._err(400, "auth_key_weak", str(exc))
        except RuntimeError as exc:
            # Lost the race with another tab: the key now exists.
            return self._err(409, "auth_already_configured", str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.error("First-run setup failed: %s", exc)
            return self._err(500, "INTERNAL_ERROR", "Could not store the access key")

        response = self._ok(
            {
                "session_id": record["id"],
                "expires_at": record["expires_at"],
                "ttl_seconds": ttl,
                "generation": self._store.generation,
            }
        )
        self._set_session_cookie(
            response, request, value, max_age=ttl if remember else None
        )
        response.headers["Cache-Control"] = "no-store"
        # Never log the key itself — not even a masked tail on this path.
        logger.info("First-run setup completed from %s (ttl=%ss)", ip or "?", ttl)
        return response

    # ==================== Session (login / logout) ====================

    async def handle_auth_session_create(self, request: web.Request) -> web.Response:
        """POST /api/auth/session — trade the access key for a device session."""
        ip = self._client_ip(request)
        retry_after = self._store.retry_after(ip)
        if retry_after > 0:
            response = self._err(
                429, "auth_throttled", "Too many failed attempts", retry_after=retry_after
            )
            response.headers["Retry-After"] = str(retry_after)
            return response

        if not self._store.configured:
            return self._err(409, "auth_not_configured", "No access key is configured")

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return self._err(400, "INVALID_JSON", "Request body must be valid JSON")
        if not isinstance(body, dict):
            return self._err(400, "INVALID_REQUEST", "Body must be a JSON object")

        key = str(body.get("key") or "").strip()
        if not key:
            return self._err(400, "auth_key_required", "Access key is required")

        if not self._store.check_key(key):
            retry_after = self._store.record_failure(ip)
            response = self._err(
                401, "auth_invalid", "Access key is incorrect", retry_after=retry_after
            )
            if retry_after > 0:
                response.headers["Retry-After"] = str(retry_after)
            return response

        self._store.record_success(ip)
        ttl = self._ttl_seconds(body.get("ttl_seconds"))
        remember = body.get("remember", True) is not False
        value, record = self._store.create_session(
            ttl,
            remember=remember,
            user_agent=request.headers.get("User-Agent", ""),
            ip=ip,
        )
        response = self._ok(
            {
                "expires_at": record["expires_at"],
                "ttl_seconds": ttl,
                "session_id": record["id"],
            }
        )
        self._set_session_cookie(
            response, request, value, max_age=ttl if remember else None
        )
        logger.info("Device session created from %s (ttl=%ss)", ip or "?", ttl)
        return response

    async def handle_auth_session_delete(self, request: web.Request) -> web.Response:
        """DELETE /api/auth/session — log the current device out."""
        session = self._current_session(request)
        if session:
            self._store.revoke_session(str(session.get("id") or ""))
            await self._publish_revoked("logout", [str(session.get("id") or "")])
        response = self._ok({"logged_out": True})
        response.del_cookie(SESSION_COOKIE, path="/")
        return response

    # ==================== WebSocket ticket ====================

    async def handle_auth_ws_ticket(self, request: web.Request) -> web.Response:
        """POST /api/auth/ws-ticket — one-shot credential for the upgrade."""
        ticket, expires_at = self._store.issue_ticket(30)
        return self._ok({"ticket": ticket, "expires_at": expires_at, "expires_in": 30})

    # ==================== Device list ====================

    async def handle_auth_sessions_list(self, request: web.Request) -> web.Response:
        session = self._current_session(request)
        current_id = session.get("id") if session else None
        devices = []
        for record in self._store.list_sessions():
            devices.append(
                {
                    "id": record.get("id"),
                    "created_at": record.get("created_at"),
                    "expires_at": record.get("expires_at"),
                    "user_agent": record.get("ua") or "",
                    "ip": record.get("ip") or "",
                    "remember": bool(record.get("remember")),
                    "current": record.get("id") == current_id,
                }
            )
        return self._ok({"sessions": devices})

    async def handle_auth_session_revoke(self, request: web.Request) -> web.Response:
        session_id = str(request.match_info.get("session_id") or "").strip()
        if not session_id:
            return self._err(400, "INVALID_REQUEST", "session_id is required")
        if not self._store.revoke_session(session_id):
            return self._err(404, "NOT_FOUND", "Session not found")
        await self._publish_revoked("session_revoked", [session_id])
        return self._ok({"revoked": session_id})

    # ==================== Key rotation ====================

    async def handle_auth_key_rotate(self, request: web.Request) -> web.Response:
        """POST /api/auth/key/rotate — PREPARE a new key, apply nothing."""
        if not self._store.configured:
            return self._err(409, "auth_not_configured", "No access key exists yet")
        try:
            new_key, expires_at = self._store.prepare_rotation()
        except Exception as exc:  # noqa: BLE001
            logger.error("Access key rotation prepare failed: %s", exc)
            return self._err(500, "INTERNAL_ERROR", "Could not prepare a new key")
        response = self._ok(
            {
                "key": new_key,
                "pending": True,
                "expires_at": expires_at,
                "expires_in": int(ROTATION_PENDING_SECONDS),
                "generation": self._store.generation,
                "note": (
                    "Not applied yet. Save this key, then confirm. "
                    "The current key keeps working until you do."
                ),
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    async def handle_auth_key_rotate_confirm(
        self, request: web.Request
    ) -> web.Response:
        """POST /api/auth/key/rotate/confirm — apply the prepared key."""
        try:
            _key, generation = self._store.commit_rotation()
        except RuntimeError as exc:
            return self._err(409, "auth_rotation_not_pending", str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.error("Access key rotation commit failed: %s", exc)
            return self._err(500, "INTERNAL_ERROR", "Could not apply the new key")
        await self._publish_revoked("key_rotated")
        response = self._ok(
            {
                "applied": True,
                "generation": generation,
                "note": "All devices must re-enter the new key.",
            }
        )
        response.headers["Cache-Control"] = "no-store"
        response.del_cookie(SESSION_COOKIE, path="/")
        return response

    async def handle_auth_key_rotate_cancel(
        self, request: web.Request
    ) -> web.Response:
        """DELETE /api/auth/key/rotate — discard the prepared candidate."""
        cancelled = self._store.cancel_rotation()
        return self._ok({"cancelled": cancelled})

    # ==================== Settings ====================

    async def handle_auth_settings_get(self, request: web.Request) -> web.Response:
        settings = self._gate.settings()
        maximum = max(
            60,
            min(
                int(getattr(settings, "session_ttl_max_seconds", MAX_TTL_SECONDS) or MAX_TTL_SECONDS),
                MAX_TTL_SECONDS,
            ),
        )
        return self._ok(
            {
                "session_ttl_seconds": max(
                    60,
                    min(
                        int(getattr(settings, "session_ttl_seconds", DEFAULT_TTL_SECONDS) or DEFAULT_TTL_SECONDS),
                        maximum,
                    ),
                ),
                "session_ttl_max_seconds": maximum,
                "ttl_options": [value for value in _TTL_OPTIONS if value <= maximum],
                "sliding": bool(getattr(settings, "sliding", False)),
                "trust_loopback": bool(getattr(settings, "trust_loopback", True)),
            }
        )

    async def handle_auth_settings_update(self, request: web.Request) -> web.Response:
        """PUT /api/auth/settings — session lifetime policy."""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return self._err(400, "INVALID_JSON", "Request body must be valid JSON")
        if not isinstance(body, dict):
            return self._err(400, "INVALID_REQUEST", "Body must be a JSON object")

        from boarai.core.config import get_config_parser

        parser = get_config_parser()
        loaded = getattr(parser, "_config", None) if parser else None
        if loaded is None:
            return self._err(409, "config_unavailable", "Configuration is not loaded")

        web_cfg = loaded.security.web
        maximum = max(
            60, min(int(getattr(web_cfg, "session_ttl_max_seconds", MAX_TTL_SECONDS)), MAX_TTL_SECONDS)
        )
        changed: Dict[str, Any] = {}
        if "session_ttl_seconds" in body:
            try:
                value = int(body["session_ttl_seconds"])
            except (TypeError, ValueError):
                return self._err(400, "INVALID_REQUEST", "session_ttl_seconds must be an integer")
            if value < 60 or value > maximum:
                return self._err(
                    400,
                    "INVALID_REQUEST",
                    f"session_ttl_seconds must be between 60 and {maximum}",
                )
            web_cfg.session_ttl_seconds = value
            changed["session_ttl_seconds"] = value
        if "sliding" in body:
            web_cfg.sliding = bool(body["sliding"])
            changed["sliding"] = web_cfg.sliding

        if not changed:
            return self._err(400, "INVALID_REQUEST", "Nothing to update")

        path = getattr(parser, "config_path", None)
        if path is not None:
            try:
                await parser.save(loaded, path)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to persist access settings: %s", exc)
                return self._err(500, "INTERNAL_ERROR", "Could not persist settings")

        return self._ok(
            {
                "session_ttl_seconds": web_cfg.session_ttl_seconds,
                "sliding": web_cfg.sliding,
                "session_ttl_max_seconds": maximum,
            }
        )

    # ==================== Console hand-off ====================

    async def handle_auth_launch(self, request: web.Request) -> web.Response:
        """GET /auth/launch?key=<launch token> — mint a session, then redirect."""
        if not self._store.configured:
            # First run has exactly one path: the setup page. Minting a session
            # before a key exists would create a credential nobody can revoke.
            raise web.HTTPFound(location="/")
        token = (request.query.get("key") or "").strip()
        if not self._store.check_launch_token(token):
            return web.Response(
                status=401,
                content_type="text/html",
                text=(
                    "<!doctype html><meta charset=utf-8>"
                    "<title>BoarAI</title>"
                    "<body style='font-family:system-ui;padding:2rem'>"
                    "<h3>This sign-in link is no longer valid</h3>"
                    "<p>It belongs to a previous server run (or the key was "
                    "rotated). Copy the link printed in the terminal, or enter "
                    "the access key on the unlock page.</p>"
                    "<p><a href='/'>Go to the unlock page</a></p>"
                ),
            )
        ttl = self._ttl_seconds(None)
        value, _record = self._store.create_session(
            ttl,
            remember=True,
            user_agent=request.headers.get("User-Agent", ""),
            ip=self._client_ip(request),
        )
        response = web.Response(
            status=303,
            headers={
                "Location": "/",
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
            },
        )
        self._set_session_cookie(response, request, value, max_age=ttl)
        return response

    # ==================== Legacy ====================

    async def handle_auth_token(self, request: web.Request) -> web.Response:
        """GET /api/auth/token — loopback-only bearer for local tools."""
        if not self._gate.is_loopback(request):
            return self._err(
                403, "auth_loopback_only", "This endpoint is available only from loopback"
            )
        response = self._ok({"token": self._store.launch_token()})
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return response


_auth_api: Optional[AuthAPIHandler] = None


def get_auth_api_handler() -> AuthAPIHandler:
    """Process-wide auth handler (lazy)."""
    global _auth_api
    if _auth_api is None:
        _auth_api = AuthAPIHandler()
    return _auth_api


__all__ = ["AuthAPIHandler", "get_auth_api_handler"]
