"""Update API for BoarAi — check / apply / status / release notes.

Endpoints (all auth-gated like the rest of /api):
    GET  /api/update/status — current version, install kind, cached check,
                              worker state
    POST /api/update/check  — force a registry check now {"force": bool?}
    POST /api/update/apply  — spawn the detached update worker
                              {"target": "1.2.3"?}
    GET  /api/update/notes  — release notes of a version (?version=1.2.3,
                              default: the latest known one)

WS mirror: the service publishes update.available / update.progress /
update.done / update.failed on the bus; serve.py forwards them to every WS
client (same path as session.deleted). A client that reconnects after the
server restarted mid-update re-reads GET /api/update/status for the
terminal state. The notes endpoint is HTTP-only by design (a read-only
query, not a business action that needs a WS twin).
"""

import logging

from aiohttp import web

from boarai.core.update import service as update_service
from boarai.core.update.service import UpdateError

logger = logging.getLogger(__name__)


async def handle_get_status(request: web.Request) -> web.Response:
    try:
        return web.json_response(await update_service.get_status())
    except Exception as e:  # noqa: BLE001 - status must never 500 hard
        logger.warning("update status failed: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def handle_check(request: web.Request) -> web.Response:
    force = True
    try:
        body = await request.json()
        if isinstance(body, dict):
            force = bool(body.get("force", True))
    except Exception:  # noqa: BLE001 - empty/invalid body → force check
        pass
    try:
        result = await update_service.trigger_check(force=force)
        return web.json_response(result)
    except Exception as e:  # noqa: BLE001
        logger.warning("update check failed: %s", e)
        return web.json_response({"error": str(e)}, status=502)


async def handle_apply(request: web.Request) -> web.Response:
    target = None
    try:
        body = await request.json()
        if isinstance(body, dict):
            target = body.get("target") or None
    except Exception:  # noqa: BLE001 - empty body → latest
        pass
    try:
        attempt = await update_service.trigger_apply(target=target)
        return web.json_response(attempt)
    except UpdateError as e:
        return web.json_response({"error": e.message, "code": e.code}, status=e.http_status)
    except Exception as e:  # noqa: BLE001
        logger.warning("update apply failed: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def handle_notes(request: web.Request) -> web.Response:
    version = request.query.get("version")
    try:
        result = await update_service.get_release_notes(version)
        return web.json_response(result)
    except Exception as e:  # noqa: BLE001
        logger.warning("update notes failed: %s", e)
        return web.json_response({"error": str(e)}, status=500)
