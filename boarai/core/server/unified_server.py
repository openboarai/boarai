"""
Unified Server for BoarAi.

Provides both HTTP and WebSocket on a single port (26620).
This is the core server module - all configuration is self-contained.
"""

import asyncio
import ipaddress
import json
import logging
import os
import platform
import re
import sys
import time
from typing import Any, Callable, Dict, Optional, Set
from urllib.parse import urlsplit

from aiohttp import web

from boarai.core.api import get_session_api_handler, get_llm_api_handler, get_run_api_handler, get_task_api_handler, get_agent_api_handler

logger = logging.getLogger(__name__)

# Server configuration - hardcoded, no config file needed.
# Non-loopback binds require an explicitly configured API token.
HOST = os.getenv("BOARAI_HOST", "0.0.0.0")
PORT = int(os.getenv("BOARAI_PORT", "26620"))


def _host_is_loopback(host: str) -> bool:
    """Whether a bind address keeps the server on this machine only."""
    import ipaddress

    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


_DISCONNECT_NOISE_ERRORS = (
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
)


def _is_proactor_disconnect_noise(context: Dict[str, Any], platform: str) -> bool:
    """Whether a loop-callback exception is Windows proactor teardown noise."""
    if platform != "win32":
        return False
    if not isinstance(context.get("exception"), _DISCONNECT_NOISE_ERRORS):
        return False
    return "_call_connection_lost" in str(context.get("message", ""))


def _loop_exception_handler(
    loop: asyncio.AbstractEventLoop, context: Dict[str, Any]
) -> None:
    """Loop exception handler that swallows only proactor disconnect noise."""
    if _is_proactor_disconnect_noise(context, sys.platform):
        logger.debug("Suppressed proactor disconnect noise: %s", context.get("exception"))
        return
    loop.default_exception_handler(context)

class UnifiedServer:
    """Unified HTTP and WebSocket server on a single port."""

    def __init__(self, host: str = HOST, port: int = PORT):
        self._host = host
        self._port = port
        # Access gate state lives in core.security.access (one key, N device
        # sessions). BOARAI_API_TOKEN is adopted on first run so an existing
        # deployment keeps working without re-entering anything.
        from boarai.core.security.access import get_access_store

        env_token = os.getenv("BOARAI_API_TOKEN", "").strip()
        if env_token and (
            len(env_token) < 32
            or re.fullmatch(r"[A-Za-z0-9_-]+", env_token) is None
        ):
            raise ValueError(
                "BOARAI_API_TOKEN must be at least 32 base64url characters"
            )
        self._access_store = get_access_store()
        #: Validated ``BOARAI_API_TOKEN``; adopted by ``start()`` on first run.
        self._env_token = env_token
        #: Key source reported by ``ensure_key`` ("env" / "generated" / "unset").
        #: No plaintext is ever held here: the first key is created in the
        #: browser, so the server has nothing to print.
        self._key_source = "unset"
        self._app = web.Application()
        self._runner = None
        self._ws_clients: Set[web.WebSocketResponse] = set()

        # Dynamic middleware pool: aiohttp freezes app.middlewares once the
        # router starts, so runtime registrations (hot-loaded extensions)
        # go through this dispatcher instead. It is mounted ONCE at __init__
        # and consults a mutable list on every request.
        self._dynamic_middlewares: list = []
        self._app.middlewares.append(self._dynamic_middleware_dispatch)
        # Extension routes live ONLY in RouteRegistry (single source of truth);
        # it is middleware-backed, so hot add/remove needs no router rebuild.
        self._started = False  # True after start() completes
        self._start_time = time.time()
        self._setup_routes()
        self._setup_middleware()
        self._setup_auth_middleware()

    def _setup_routes(self) -> None:
        """Setup HTTP and WebSocket routes."""
        # Access gate (core.api.auth). The gate itself lives in
        # core.server.gate; these are only the endpoints the unlock page and
        # the settings "Security" tab call.
        from boarai.core.api import get_auth_api_handler
        from boarai.core.security.gate import LAUNCH_PATH

        auth_api = get_auth_api_handler()
        self._app.router.add_get("/api/auth/status", auth_api.handle_auth_status)
        self._app.router.add_post("/api/auth/session", auth_api.handle_auth_session_create)
        self._app.router.add_delete("/api/auth/session", auth_api.handle_auth_session_delete)
        self._app.router.add_post("/api/auth/ws-ticket", auth_api.handle_auth_ws_ticket)
        self._app.router.add_get("/api/auth/sessions", auth_api.handle_auth_sessions_list)
        self._app.router.add_delete(
            "/api/auth/sessions/{session_id}", auth_api.handle_auth_session_revoke
        )
        # Two-phase rotation: prepare shows the new key while the old one still
        # works; confirm applies it. Committing before the operator saved the
        # key would lock them out for good.
        self._app.router.add_post("/api/auth/key/rotate", auth_api.handle_auth_key_rotate)
        self._app.router.add_post(
            "/api/auth/key/rotate/confirm", auth_api.handle_auth_key_rotate_confirm
        )
        self._app.router.add_delete(
            "/api/auth/key/rotate", auth_api.handle_auth_key_rotate_cancel
        )
        self._app.router.add_get("/api/auth/settings", auth_api.handle_auth_settings_get)
        self._app.router.add_put("/api/auth/settings", auth_api.handle_auth_settings_update)
        # First-run setup: the browser creates the key (loopback only).
        self._app.router.add_post("/api/auth/setup", auth_api.handle_auth_setup)
        # Console hand-off: /auth/launch?key=<process token> → cookie + redirect.
        self._app.router.add_get(LAUNCH_PATH, auth_api.handle_auth_launch)
        # Local-tool bearer (process token), loopback only.
        self._app.router.add_get("/api/auth/token", auth_api.handle_auth_token)

        # HTTP API routes
        self._app.router.add_get("/api/status", self._handle_status)
        self._app.router.add_get("/api/commands", self._handle_list_commands)
        # Process lifecycle (auth-gated; called by `main.py stop|restart`).
        # The command system is chat-only - there is no /api/command surface.
        self._app.router.add_post("/api/server/stop", self._handle_server_stop)
        self._app.router.add_post("/api/server/restart", self._handle_server_restart)

        # Online update (pip channel; core.update). WS mirror: the service
        # publishes update.* bus events, forwarded in serve.py.
        from boarai.core.api import update as update_api
        self._app.router.add_get("/api/update/status", update_api.handle_get_status)
        self._app.router.add_post("/api/update/check", update_api.handle_check)
        self._app.router.add_post("/api/update/apply", update_api.handle_apply)

        # Log inspection (read-only; core.api.logs). HTTP-only by design: it is
        # a pull surface with no server-initiated state, so there is no WS view
        # to keep in sync. Behind the same access gate as every other /api route
        # — logs carry paths, ids and error text, never credentials (masked at
        # write time), but they are still not a public resource.
        from boarai.core.api import logs as logs_api
        self._app.router.add_get("/api/logs/files", logs_api.handle_list_files)
        self._app.router.add_get("/api/logs/recent", logs_api.handle_recent)
        self._app.router.add_get("/api/logs/stats", logs_api.handle_stats)
        # Registered last: "/api/logs" must not shadow the more specific paths.
        self._app.router.add_get("/api/logs", logs_api.handle_read)

        # WebSocket route
        self._app.router.add_get("/ws", self._handle_websocket)

        # System-level Session API routes
        session_api = get_session_api_handler()

        # Session CRUD endpoints
        self._app.router.add_get("/api/sessions", session_api.handle_list_sessions)
        self._app.router.add_post("/api/sessions", session_api.handle_create_session)
        self._app.router.add_get("/api/sessions/{session_id}", session_api.handle_get_session)
        self._app.router.add_put("/api/sessions/{session_id}", session_api.handle_update_session)
        self._app.router.add_delete("/api/sessions/{session_id}", session_api.handle_delete_session)

        # Session message endpoints
        self._app.router.add_get("/api/sessions/{session_id}/messages", session_api.handle_get_messages)
        self._app.router.add_post("/api/sessions/{session_id}/messages", session_api.handle_send_message)
        self._app.router.add_put("/api/sessions/{session_id}/messages/{message_id}", session_api.handle_update_message)
        self._app.router.add_post("/api/sessions/{session_id}/clear", session_api.handle_clear_messages)
        self._app.router.add_post("/api/sessions/{session_id}/archive", session_api.handle_archive_session)
        self._app.router.add_post("/api/sessions/{session_id}/compress", session_api.handle_compress)

        # Session tree: fork / branch listing / branch preview / navigate
        self._app.router.add_post("/api/sessions/{session_id}/fork", session_api.handle_fork_session)
        self._app.router.add_get("/api/sessions/{session_id}/tree", session_api.handle_session_tree)
        self._app.router.add_get("/api/sessions/{session_id}/context", session_api.handle_session_context)
        self._app.router.add_post("/api/sessions/{session_id}/navigate", session_api.handle_navigate_tree)
        self._app.router.add_get("/api/sessions/{session_id}/changes", session_api.handle_get_changes)
        self._app.router.add_post("/api/sessions/{session_id}/changes/revert-all", session_api.handle_revert_all_changes)
        self._app.router.add_post("/api/sessions/{session_id}/changes/accept", session_api.handle_accept_changes)
        self._app.router.add_post("/api/sessions/{session_id}/changes/{run_id}/revert", session_api.handle_revert_changes)

        # Session workspace (working directory for tools)
        self._app.router.add_put(
            "/api/sessions/{session_id}/workspace", session_api.handle_put_workspace
        )

        # Filesystem browse (directory picker)
        from boarai.core.api.fs import register_fs_routes
        register_fs_routes(self._app)

        # Session attachment upload / download
        from boarai.core.api.files import register_files_routes
        register_files_routes(self._app)

        # System-level LLM API routes
        llm_api = get_llm_api_handler()
        self._app.router.add_post("/api/llm/chat", llm_api.handle_llm_chat)
        self._app.router.add_get("/api/llm/models", llm_api.handle_llm_models)
        self._app.router.add_get("/api/llm/status", llm_api.handle_llm_status)
        self._app.router.add_get("/api/llm/thinking", llm_api.handle_llm_thinking)

        # LLM provider CRUD endpoints
        self._app.router.add_get("/api/llm/providers", llm_api.handle_llm_providers_list)
        self._app.router.add_post("/api/llm/providers", llm_api.handle_llm_providers_add)
        self._app.router.add_put("/api/llm/providers/{name}", llm_api.handle_llm_providers_update)
        self._app.router.add_delete("/api/llm/providers/{name}", llm_api.handle_llm_providers_delete)
        self._app.router.add_put("/api/llm/providers/{name}/default", llm_api.handle_llm_providers_set_default)
        self._app.router.add_post("/api/llm/providers/{name}/validate", llm_api.handle_llm_providers_validate)
        self._app.router.add_post("/api/llm/providers/fetch-models", llm_api.handle_llm_providers_fetch_models)

        # Capability model routes
        self._app.router.add_get("/api/llm/capability-models", llm_api.handle_capability_models_get)
        self._app.router.add_put("/api/llm/capability-models", llm_api.handle_capability_models_put)

        # Run (async conversation) endpoints - flat API, IDs in body.
        # Streaming is delivered over the /ws session-scoped run.* channel only;
        # the legacy /api/runs/events (SSE) endpoint was removed (dead channel).
        run_api = get_run_api_handler()
        self._app.router.add_post("/api/chat", run_api.handle_create_run)
        self._app.router.add_post("/api/runs", run_api.handle_run_action)

        # Monitoring API routes
        from boarai.core.api.monitoring import (
            handle_monitoring_stats,
            handle_monitoring_clear,
        )
        self._app.router.add_get("/api/monitoring/stats", handle_monitoring_stats)
        self._app.router.add_post(
            "/api/monitoring/stats/clear", handle_monitoring_clear
        )

        # Security API routes
        from boarai.core.api.security import register_security_routes
        register_security_routes(self._app)

        # Out-of-band approval routes (CLI / scripts / mobile — no WS needed)
        from boarai.core.api.approvals import register_approval_routes
        register_approval_routes(self._app)

        # Owner settings API routes
        from boarai.core.api.owner import handle_get_owner, handle_put_owner, handle_get_session_settings, handle_put_session_settings
        self._app.router.add_get("/api/owner", handle_get_owner)
        self._app.router.add_put("/api/owner", handle_put_owner)
        self._app.router.add_get("/api/settings/session", handle_get_session_settings)
        self._app.router.add_put("/api/settings/session", handle_put_session_settings)

        # Agent API routes
        agent_api = get_agent_api_handler()
        self._app.router.add_get("/api/agents", agent_api.handle_list)
        self._app.router.add_post("/api/agents", agent_api.handle_create)
        self._app.router.add_delete("/api/agents", agent_api.handle_delete_no_name)
        self._app.router.add_get("/api/agents/default", agent_api.handle_get_default)
        self._app.router.add_get("/api/agents/{name}", agent_api.handle_get)
        self._app.router.add_put("/api/agents/{name}", agent_api.handle_update)
        self._app.router.add_delete("/api/agents/{name}", agent_api.handle_delete)
        self._app.router.add_put("/api/agents/{name}/default", agent_api.handle_set_default)

        # Extension API routes (lazy import to avoid circular dependency)
        from boarai.core.api.extension import get_extension_api_handler
        ext_api = get_extension_api_handler()
        # Live tool catalogue for the agent "allowed tools" picker.
        self._app.router.add_get("/api/tools", ext_api.handle_tools)
        self._app.router.add_get("/api/extensions", ext_api.handle_list)
        self._app.router.add_get("/api/extensions/stats", ext_api.handle_stats)
        self._app.router.add_get("/api/extensions/state", ext_api.handle_state)
        # Static dry-run verification (must precede the /{name} dynamic route)
        self._app.router.add_get("/api/extensions/verify", ext_api.handle_verify)
        self._app.router.add_get("/api/extensions/hooks", ext_api.handle_hooks)
        self._app.router.add_get("/api/extensions/services", ext_api.handle_services)
        # Settings surface (a custom iframe panel via contributes.ui, actions
        # included) plus the extension-private data map. Registered before the
        # bare /{name} route.
        self._app.router.add_put(
            "/api/extensions/{name}/settings", ext_api.handle_update_settings
        )
        self._app.router.add_get(
            "/api/extensions/{name}/data", ext_api.handle_get_data
        )
        self._app.router.add_put(
            "/api/extensions/{name}/data", ext_api.handle_put_data
        )
        self._app.router.add_post(
            "/api/extensions/{name}/actions/{action_id}", ext_api.handle_run_action
        )
        self._app.router.add_get("/api/extensions/{name}", ext_api.handle_get_detail)
        self._app.router.add_post("/api/extensions/{name}/enable", ext_api.handle_enable)
        self._app.router.add_post("/api/extensions/{name}/disable", ext_api.handle_disable)
        self._app.router.add_post("/api/extensions/{name}/trust", ext_api.handle_trust)
        self._app.router.add_post("/api/extensions/{name}/untrust", ext_api.handle_untrust)
        self._app.router.add_post("/api/extensions/{name}/reload", ext_api.handle_reload)
        self._app.router.add_delete("/api/extensions/{name}", ext_api.handle_uninstall)

        # Task API routes
        task_api = get_task_api_handler()
        self._app.router.add_get("/api/tasks", task_api.handle_list_tasks)
        self._app.router.add_post("/api/tasks", task_api.handle_create_task)
        self._app.router.add_get("/api/tasks/stats", task_api.handle_task_stats)
        self._app.router.add_get("/api/tasks/{task_id}", task_api.handle_get_task)
        self._app.router.add_patch("/api/tasks/{task_id}", task_api.handle_update_task)
        self._app.router.add_delete("/api/tasks/{task_id}", task_api.handle_delete_task)
        self._app.router.add_post("/api/tasks/{task_id}/execute", task_api.handle_execute_task)
        self._app.router.add_post("/api/tasks/{task_id}/cancel", task_api.handle_cancel_task)
        self._app.router.add_post("/api/tasks/{task_id}/pause", task_api.handle_pause_task)
        self._app.router.add_post("/api/tasks/{task_id}/resume", task_api.handle_resume_task)
        self._app.router.add_get("/api/tasks/{task_id}/steps", task_api.handle_list_steps)
        self._app.router.add_post("/api/tasks/{task_id}/steps/{step_id}/complete", task_api.handle_complete_step)
        self._app.router.add_get("/api/tasks/{task_id}/subtasks", task_api.handle_list_subtasks)
        self._app.router.add_get("/api/tasks/{task_id}/tree", task_api.handle_get_task_tree)
        self._app.router.add_get("/api/tasks/{task_id}/history", task_api.handle_task_history)

        # Memory API routes (Synaptic Memory Mesh)
        from boarai.core.api.memory import get_memory_api_handler
        mem_api = get_memory_api_handler()
        self._app.router.add_get("/api/memory/stats", mem_api.handle_stats)
        self._app.router.add_get("/api/memory/entries", mem_api.handle_list_entries)
        self._app.router.add_delete("/api/memory/entries/{entry_id}", mem_api.handle_delete_entry)
        self._app.router.add_get("/api/memory/synapses", mem_api.handle_list_synapses)
        self._app.router.add_post("/api/memory/synapses", mem_api.handle_create_synapse)
        self._app.router.add_delete("/api/memory/synapses/{synapse_id}", mem_api.handle_delete_synapse)
        self._app.router.add_get("/api/memory/staging", mem_api.handle_list_staging)
        self._app.router.add_post("/api/memory/staging/{id}/confirm", mem_api.handle_confirm_staging)
        self._app.router.add_post("/api/memory/staging/{id}/reject", mem_api.handle_reject_staging)
        self._app.router.add_get("/api/memory/retired", mem_api.handle_list_retired)
        self._app.router.add_post("/api/memory/retired/{name}/query", mem_api.handle_query_retired)
        self._app.router.add_post("/api/memory/consolidate", mem_api.handle_consolidate)
        self._app.router.add_get("/api/memory/export", mem_api.handle_export)
        self._app.router.add_patch("/api/memory/entries/{entry_id}", mem_api.handle_update_entry)

        # Trash API routes
        from boarai.core.api.trash import get_trash_api_handler
        trash_api = get_trash_api_handler()
        self._app.router.add_get("/api/trash", trash_api.handle_list)
        self._app.router.add_get("/api/trash/snapshots", trash_api.handle_snapshots)
        self._app.router.add_get("/api/trash/{trash_id}", trash_api.handle_get)
        self._app.router.add_post("/api/trash/{trash_id}/restore", trash_api.handle_restore)
        self._app.router.add_post("/api/trash/{trash_id}/distill", trash_api.handle_distill)
        self._app.router.add_delete("/api/trash/{trash_id}", trash_api.handle_purge_one)
        self._app.router.add_delete("/api/trash", trash_api.handle_purge_all)
        self._app.router.add_post("/api/trash/cleanup", trash_api.handle_cleanup)

    def _setup_middleware(self) -> None:
        """Setup middleware for dynamic extension route handling."""
        @web.middleware
        async def extension_route_middleware(request, handler):
            """Resolve extension routes from RouteRegistry (the only source)."""
            path = request.path
            method = request.method

            # 0) Defense-in-depth: core API / WS paths are owned by the core
            # server. Extensions can never intercept them, regardless of what
            # a (possibly malicious) extension registers in RouteRegistry.
            # (/static/ is deliberately NOT skipped: webui (system layer,
            # trusted) legitimately mounts /static/{path:.*}; untrusted
            # extensions are blocked at RouteRegistry.register time instead.)
            lowered_path = path.lower()
            if lowered_path.startswith("/api/") and not lowered_path.startswith("/api/ext/"):
                return await handler(request)
            if lowered_path == "/ws" or lowered_path.startswith("/ws/"):
                return await handler(request)

            try:
                from boarai.core.extension import get_route_registry
                ext_handler = get_route_registry().resolve(method, path)
                if ext_handler is not None:
                    return await ext_handler(request)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"RouteRegistry lookup failed for {path}: {e}")

            return await handler(request)

        self._app.middlewares.append(extension_route_middleware)

    def _setup_auth_middleware(self) -> None:
        """Apply the access gate to API, WebSocket and extension routes."""
        from boarai.core.security.access import SESSION_COOKIE
        from boarai.core.security.gate import get_access_gate

        gate = get_access_gate()

        @web.middleware
        async def auth_middleware(request: web.Request, handler):
            decision = gate.authorize(request)

            if not decision.allowed:
                # 429 only when the attempt was refused *because* of backoff.
                # A fresh wrong key is a 401 (the credential was wrong, not the
                # rate) carrying an advisory Retry-After for the next attempt.
                if decision.throttled and decision.retry_after > 0:
                    response = web.json_response(
                        {
                            "success": False,
                            "error": {
                                "code": "auth_throttled",
                                "message": "Too many failed attempts",
                                "retry_after": decision.retry_after,
                            },
                        },
                        status=429,
                    )
                    response.headers["Retry-After"] = str(decision.retry_after)
                    return response
                if request.path.startswith("/api/") or request.path == "/ws":
                    # Unclaimed instance: the SPA must show the setup page, not
                    # the unlock page — there is no key to type yet.
                    setup = decision.mode == "setup_required"
                    error: Dict[str, Any] = {
                        "code": "auth_setup_required" if setup else "auth_required",
                        "message": (
                            "First-run setup required"
                            if setup
                            else "Authentication required"
                        ),
                    }
                    if decision.retry_after > 0:
                        error["retry_after"] = decision.retry_after
                    response = web.json_response(
                        {"success": False, "error": error}, status=401
                    )
                    if decision.retry_after > 0:
                        response.headers["Retry-After"] = str(decision.retry_after)
                    return response
                # A deep link is preserved so the unlock page can return the
                # user to it: /#/sessions/abc → /#/unlock?next=%2Fsessions%2Fabc
                raise web.HTTPTemporaryRedirect(location="/")

            if not self._origin_allowed(request):
                return web.json_response(
                    {"error": "Origin is not allowed"}, status=403
                )

            response = await handler(request)

            # Sliding TTL (opt-in): push the expiry forward and re-issue the
            # cookie, otherwise the signed expiry would stay at the old value.
            try:
                settings = gate.settings()
                ttl = int(getattr(settings, "session_ttl_seconds", 604800))
                expires_at = float((decision.session or {}).get("expires_at") or 0)
                # Renew only once the session is past half its life. Extending
                # on every request would YAML-dump + atomically write the whole
                # device table per request, synchronously on the event loop.
                due = expires_at > 0 and (expires_at - time.time()) < ttl / 2
                if (
                    bool(getattr(settings, "sliding", False))
                    and decision.mode == "session"
                    and decision.session_id
                    and due
                    and isinstance(response, web.Response)
                    and not isinstance(response, web.WebSocketResponse)
                ):
                    renewed = self._access_store.extend_session(
                        decision.session_id, ttl
                    )
                    if renewed:
                        value, expires_at = renewed
                        response.set_cookie(
                            SESSION_COOKIE,
                            value,
                            max_age=ttl,
                            path="/",
                            httponly=True,
                            secure=request.secure,
                            samesite="Strict",
                        )
                        response.headers["X-Session-Expires-At"] = str(
                            int(expires_at)
                        )
            except Exception as exc:  # noqa: BLE001 - never fail a served request
                logger.debug("Sliding session refresh skipped: %s", exc)

            return response

        # This is the outermost middleware, so extension routes cannot bypass
        # the same authentication policy used by core API routes.
        self._auth_middleware = auth_middleware
        self._app.middlewares.insert(0, auth_middleware)

    def _origin_allowed(self, request: web.Request) -> bool:
        origin = request.headers.get("Origin")
        if not origin:
            # Native clients do not send Origin. Their bearer token is still
            # required by the middleware above.
            return True
        try:
            parsed = urlsplit(origin)
            if not parsed.scheme or not parsed.netloc or parsed.path not in ("", "/"):
                return False
            normalized = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
        except (TypeError, ValueError):
            return False
        expected = f"{request.scheme.lower()}://{request.host.lower()}"
        allowed = {
            item.strip().rstrip("/").lower()
            for item in os.getenv("BOARAI_ALLOWED_ORIGINS", "").split(",")
            if item.strip()
        }
        return normalized == expected or normalized in allowed

    @staticmethod
    def _peer_is_loopback(request: web.Request) -> bool:
        transport = request.transport
        peer = transport.get_extra_info("peername") if transport else None
        if not peer:
            return False
        try:
            return ipaddress.ip_address(str(peer[0]).split("%", 1)[0]).is_loopback
        except (ValueError, TypeError, IndexError):
            return False

    @staticmethod
    def _request_host_is_loopback(request: web.Request) -> bool:
        try:
            host = urlsplit(f"//{request.host}").hostname or ""
        except ValueError:
            return False
        return _host_is_loopback(host)

    # Commands live ONLY in core.command.CommandRegistry (chat domain,
    # intercepted at the message surfaces). There is no server command
    # dispatch: the old per-server _handlers dict, POST /api/command and
    # the WS command frames were removed with the CLI domain; process
    # lifecycle uses the dedicated /api/server/stop|restart endpoints.

    def register_route(self, method: str, path: str, handler: Callable, extension_name: str, use_prefix: bool = True) -> bool:
        """Register an HTTP route from an extension (RouteRegistry façade)."""
        try:
            if not path.startswith("/"):
                path = "/" + path
            from boarai.core.extension import get_route_registry

            get_route_registry().register(
                method, path, handler, extension_name,
                description="", use_prefix=use_prefix,
            )
            return True
        except Exception as e:  # noqa: BLE001 — conflict/forbidden → caller decides
            logger.warning(
                "Failed to register route %s %s from %s: %s",
                method.upper(), path, extension_name, e,
            )
            return False

    def unregister_route(self, method: str, path: str, extension_name: str, use_prefix: bool = True) -> bool:
        """Unregister an HTTP route from an extension (RouteRegistry façade)."""
        try:
            if not path.startswith("/"):
                path = "/" + path
            from boarai.core.extension import get_route_registry

            removed = get_route_registry().unregister(
                method, path, extension_name, use_prefix=use_prefix,
            )
            if not removed:
                logger.warning(
                    "Route not found: %s %s from %s", method.upper(), path, extension_name
                )
            return removed
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Failed to unregister route %s %s from %s: %s",
                method.upper(), path, extension_name, e,
            )
            return False

    @web.middleware
    async def _dynamic_middleware_dispatch(self, request, handler):
        """Dispatch through runtime-registered (hot-loaded) middlewares."""
        middlewares = self._dynamic_middlewares
        if not middlewares:
            return await handler(request)

        for mw in reversed(middlewares):
            handler = self._wrap_dynamic(mw, handler)
        return await handler(request)

    @staticmethod
    def _wrap_dynamic(mw, nxt):
        async def wrapped(request):
            return await mw(request, nxt)
        return wrapped

    def add_middleware(self, middleware_factory, position: str = "append") -> None:
        """Add an aiohttp middleware to the server's middleware chain."""
        if getattr(self, "_started", False):
            # Router frozen — register into the dynamic pool instead.
            if position == "prepend":
                self._dynamic_middlewares.insert(0, middleware_factory)
            else:
                self._dynamic_middlewares.append(middleware_factory)
            return
        if position == "prepend":
            # Authentication must remain the outermost middleware. A
            # pre-start extension using "prepend" may wrap the dispatcher,
            # but must not be able to short-circuit requests before auth.
            auth_middleware = getattr(self, "_auth_middleware", None)
            if auth_middleware in self._app.middlewares:
                auth_index = self._app.middlewares.index(auth_middleware)
                self._app.middlewares.insert(auth_index + 1, middleware_factory)
            else:
                self._app.middlewares.insert(0, middleware_factory)
        else:
            self._app.middlewares.append(middleware_factory)

    def remove_middleware(self, middleware_factory) -> bool:
        """Remove a middleware registered earlier via add_middleware."""
        removed = False
        try:
            while middleware_factory in self._dynamic_middlewares:
                self._dynamic_middlewares.remove(middleware_factory)
                removed = True
        except ValueError:
            pass
        try:
            while middleware_factory in self._app.middlewares:
                self._app.middlewares.remove(middleware_factory)
                removed = True
        except ValueError:
            pass
        return removed

    def get_extension_routes(self, extension_name: str) -> list:
        """Get all routes registered by a specific extension."""
        try:
            from boarai.core.extension import get_route_registry

            return [
                {
                    "method": r["method"],
                    "path": r["path"],
                    "handler": None,  # resolved dynamically via registry
                    "is_native": False,
                }
                for r in get_route_registry().get_extension_routes(extension_name)
            ]
        except ImportError:
            return []

    # =========================================================================
    # HTTP Handlers
    # =========================================================================

    async def _handle_server_stop(self, request: web.Request) -> web.Response:
        """POST /api/server/stop — graceful shutdown request."""
        try:
            from boarai.core.runtime.serve import handle_server_stop
            result = await handle_server_stop()
            return web.json_response(result)
        except Exception:  # noqa: BLE001 — never leak internals to the client
            logger.exception("Server stop request failed")
            return web.json_response(
                {"status": "error", "message": "internal error"}, status=500
            )

    async def _handle_server_restart(self, request: web.Request) -> web.Response:
        """POST /api/server/restart — spawn a detached re-exec, then shut down."""
        try:
            from boarai.core.runtime.serve import handle_server_restart
            result = await handle_server_restart()
            return web.json_response(result)
        except Exception:  # noqa: BLE001 — never leak internals to the client
            logger.exception("Server restart request failed")
            return web.json_response(
                {"status": "error", "message": "internal error"}, status=500
            )

    async def _handle_status(self, request: web.Request) -> web.Response:
        """Handle HTTP status request."""
        # Collect extension routes for debugging (RouteRegistry is the source)
        ext_routes = []
        try:
            from boarai.core.extension import get_route_registry

            for entry in get_route_registry().list_routes():
                ext_routes.append(
                    f"{entry['method']} {entry['path']} [middleware]"
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("status: route listing unavailable: %s", e)

        # System info
        try:
            import psutil
            proc = psutil.Process()
            mem_mb = proc.memory_info().rss / (1024 * 1024)
        except ImportError:
            mem_mb = -1

        uptime_sec = int(time.time() - self._start_time)
        hours, rem = divmod(uptime_sec, 3600)
        minutes, seconds = divmod(rem, 60)
        uptime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes:02d}:{seconds:02d}"

        # Chat command names (the registry is the only command table)
        try:
            from boarai.core.command import get_command_registry
            command_names = [c.name for c in get_command_registry().list_commands()]
        except Exception:  # noqa: BLE001
            command_names = []

        from boarai.core import __version__

        return web.json_response({
            "status": "running",
            "version": __version__,
            "host": self._host,
            "port": self._port,
            "commands": command_names,
            "extension_routes": ext_routes,
            "ws_clients": len(self._ws_clients),
            "system": {
                "os": platform.system(),
                "os_release": platform.release(),
                "python": sys.version.split()[0],
                "machine": platform.machine(),
            },
            "server": {
                "uptime_sec": uptime_sec,
                "uptime": uptime_str,
                "memory_mb": round(mem_mb, 1),
                "ws_clients": len(self._ws_clients),
            }
        })

    async def _handle_list_commands(self, request: web.Request) -> web.Response:
        """GET /api/commands — the in-session (chat) command catalog."""
        from boarai.core.command import get_command_registry

        try:
            commands = [c.to_dict() for c in get_command_registry().list_commands()]
        except Exception:  # noqa: BLE001
            commands = []
        return web.json_response({"commands": commands})

    # =========================================================================
    # WebSocket Handlers
    # =========================================================================

    async def _handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """Handle WebSocket connection for all clients (WUI, CLI, etc.)."""
        ws = web.WebSocketResponse(protocols=("boarai.v1",))
        await ws.prepare(request)
        self._ws_clients.add(ws)
        # Register with session API for broadcast support + zombie cleanup
        from boarai.core.api import get_session_api_handler
        get_session_api_handler()._all_ws_clients.add(ws)
        get_session_api_handler()._start_heartbeat()
        logger.info(f"WebSocket client connected. Total: {len(self._ws_clients)}")

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    await self._process_ws_message(ws, msg.data)
                elif msg.type == web.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {ws.exception()}")
        finally:
            self._ws_clients.discard(ws)
            # Also drop the per-session subscriptions: a reload/tab close never
            # sends session.unsubscribe, and a zombie left in _ws_clients made
            # the next run event log "Cannot write to closing transport".
            get_session_api_handler()._discard_ws_client(ws)
            logger.info(f"WebSocket client disconnected. Total: {len(self._ws_clients)}")

        return ws

    async def _process_ws_message(self, ws: web.WebSocketResponse, data: str) -> None:
        """Process incoming WebSocket message."""
        try:
            message = json.loads(data)
            msg_type = message.get("type", "")
            msg_id = message.get("id")

            # Session-related messages → delegate to session API
            if msg_type.startswith("session.") or msg_type.startswith("tool."):
                from boarai.core.api import get_session_api_handler
                await get_session_api_handler()._process_ws_message(ws, data)
                return

            await ws.send_json({
                "id": msg_id,
                "status": "error",
                "message": f"Unsupported message type: {msg_type or '(none)'}",
            })
        except json.JSONDecodeError:
            await ws.send_json({"status": "error", "message": "Invalid JSON"})
        except Exception:  # noqa: BLE001 — never leak internals to the client
            logger.exception("WebSocket message processing failed")
            await ws.send_json({"status": "error", "message": "internal error"})

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """Broadcast message to all connected WebSocket clients."""
        if not self._ws_clients:
            return

        disconnected = set()
        # Snapshot: clients may disconnect (discard) while we await send_json.
        for ws in list(self._ws_clients):
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.add(ws)

        self._ws_clients -= disconnected

    # =========================================================================
    # Server Lifecycle
    # =========================================================================

    async def start(self) -> None:
        """Start the unified server."""
        # Windows proactor logs an ERROR traceback whenever a client resets a
        # connection (page refresh); downgrade that known teardown noise.
        asyncio.get_running_loop().set_exception_handler(_loop_exception_handler)
        # Adopt BOARAI_API_TOKEN if present, but NEVER mint a key here: the
        # first-run key is created by the operator on the WebUI setup page.
        # Constructing/starting a server must not invent a credential.
        try:
            _plaintext, self._key_source = self._access_store.ensure_key(
                self._env_token or None
            )
        except Exception as exc:  # noqa: BLE001 - a broken store must not be silent
            raise RuntimeError(f"Access key initialization failed: {exc}") from exc
        self._started = True
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        # Bus→WS bridge: extension.load_failed (detected manager-side) pushed
        # to every client so an open WebUI shows the failure card/toast live.
        # HTTP counterpart: GET /api/extensions carries the `failed` list.
        try:
            await get_session_api_handler().subscribe_extension_failure_bridge()
        except Exception as e:
            logger.debug(f"extension.load_failed WS bridge skipped: {e}")
        logger.info(f"Unified server started on {self._host}:{self._port}")
        logger.info(f"  HTTP API: http://{self._host}:{self._port}/api")
        logger.info(f"  WebSocket: ws://{self._host}:{self._port}/ws")
        self._log_access_hint()

    def _log_access_hint(self) -> None:
        """Log the WebUI entry point and, on first run, what to do next."""
        # The entry point is for the operator sitting at this machine, so it
        # always dials loopback — "http://0.0.0.0:..." is not a usable address.
        # A non-loopback bind is reported alongside, not instead.
        local_base = f"http://127.0.0.1:{self._port}"
        bound = (
            ""
            if _host_is_loopback(self._host)
            else f" (bound {self._host}:{self._port})"
        )
        logger.info("BoarAi WebUI: %s%s", local_base, bound)
        if not self._access_store.configured:
            logger.info(
                "First run: open the address above on this machine to create "
                "your access key. Until then every API request is refused and "
                "only loopback may complete setup."
            )
        elif self._access_store.source == "env":
            logger.info("Access key comes from BOARAI_API_TOKEN")

    async def stop(self) -> None:
        """Stop the unified server."""
        # Close all WebSocket connections (snapshot: close() may discard)
        for ws in list(self._ws_clients):
            await ws.close()
        self._ws_clients.clear()

        if self._runner:
            await self._runner.cleanup()
            logger.info("Unified server stopped")

    @property
    def url(self) -> str:
        """Get server URL."""
        return f"http://{self._host}:{self._port}"

    @property
    def ws_url(self) -> str:
        """Get WebSocket URL."""
        return f"ws://{self._host}:{self._port}/ws"

    @property
    def host(self) -> str:
        """Get server host."""
        return self._host

    @property
    def port(self) -> int:
        """Get server port."""
        return self._port
