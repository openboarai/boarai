"""Handlers referenced by manifest.yaml (contributes.*)."""

import logging

from aiohttp import web

logger = logging.getLogger(__name__)


# --- tools (LLM-callable) ---

def echo_tool(ctx, message: str = "") -> dict:
    """contributes.tools — func(ctx, **kwargs); return JSON-able data."""
    return {"echo": message}


# --- routes (HTTP under /api/ext/{{name}}) ---

async def echo_route(request) -> web.Response:
    return web.json_response({"echo": request.query.get("message", "")})


async def status_route(request) -> web.Response:
    return web.json_response({"status": "ok"})


# --- hooks (observe core events) ---

async def on_tool_executed(payload) -> None:
    """contributes.hooks — observe semantics; the return value is ignored."""
    logger.debug("tool executed: %s", (payload or {}).get("tool", ""))


# --- services (other extensions call these via api.call_service) ---

def get_status(params=None) -> dict:
    return {"status": "ok"}


# --- settings action (button under the custom panel) ---

async def show_state(ctx) -> dict:
    """contributes.ui.actions — ctx = {extension_id, config, params, request_id}."""
    greeting = (ctx.get("config") or {}).get("greeting", "")
    return {
        "ok": True,
        "message": "Current state",
        "view": {"type": "kv", "rows": [{"label": "greeting", "value": greeting}]},
    }
