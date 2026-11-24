"""Handlers referenced by manifest.yaml (contributes.*)."""

import logging

from aiohttp import web

logger = logging.getLogger(__name__)


async def ping(request) -> web.Response:
    """GET /api/ext/{{name}}/ping — health check."""
    return web.json_response({"ok": True, "extension": "{{name}}"})
