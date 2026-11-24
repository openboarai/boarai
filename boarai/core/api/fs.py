"""Filesystem browse / read / write API for the WebUI file manager."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from aiohttp import web

logger = logging.getLogger(__name__)


def _ok(data: Any, status: int = 200) -> web.Response:
    return web.json_response({"success": True, "data": data}, status=status)


def _err(status: int, code: str, message: str) -> web.Response:
    return web.json_response(
        {"success": False, "error": {"code": code, "message": message}},
        status=status,
    )


async def handle_fs_roots(request: web.Request) -> web.Response:
    """GET /api/fs/roots?session_id=xxx — browse entry points (project, home, drives…)."""
    try:
        from boarai.core.session.workspace import list_browse_roots

        session_id = request.query.get("session_id")
        return _ok({"roots": list_browse_roots(session_id=session_id)})
    except Exception:  # noqa: BLE001 — details stay in the server log
        logger.exception("fs roots failed")
        return _err(500, "INTERNAL_ERROR", "internal error")


async def handle_fs_default_workspace(request: web.Request) -> web.Response:
    """GET /api/fs/default-workspace?session_id=xxx — get configured default workspace."""
    try:
        from boarai.core.session.workspace import get_default_workspace_for_session

        session_id = request.query.get("session_id")
        if not session_id:
            return _err(400, "MISSING_PARAMETER", "session_id is required")
        
        default_path = get_default_workspace_for_session(session_id)
        return _ok({"path": default_path or ""})
    except Exception:  # noqa: BLE001
        logger.exception("fs default-workspace failed")
        return _err(500, "INTERNAL_ERROR", "internal error")


async def handle_fs_browse(request: web.Request) -> web.Response:
    """GET /api/fs/browse?path=&show_hidden=0 — list directory children."""
    try:
        from boarai.core.session.workspace import browse_directory

        path = request.query.get("path") or ""
        show_hidden = request.query.get("show_hidden", "0").lower() in (
            "1",
            "true",
            "yes",
        )
        result = browse_directory(path or None, show_hidden=show_hidden)
        if not result.get("ok"):
            return _err(400, "BROWSE_FAILED", result.get("error") or "Browse failed")
        return _ok(result)
    except Exception:  # noqa: BLE001
        logger.exception("fs browse failed")
        return _err(500, "INTERNAL_ERROR", "internal error")


async def handle_fs_validate(request: web.Request) -> web.Response:
    """POST /api/fs/validate — {path, allow_outside?}."""
    try:
        from boarai.core.session.workspace import validate_workspace_path

        body: Dict[str, Any] = await request.json()
        path = body.get("path") or ""
        allow_outside = bool(body.get("allow_outside_sandbox", False))
        ok, resolved, err = validate_workspace_path(
            path, allow_outside_sandbox=allow_outside
        )
        return _ok(
            {
                "valid": ok,
                "path": resolved,
                "error": err,
            }
        )
    except Exception:  # noqa: BLE001
        logger.exception("fs validate failed")
        return _err(500, "INTERNAL_ERROR", "internal error")


async def _session_workspace_for(session_id: Optional[str]) -> Optional[str]:
    """Effective workspace of a session (custom > default), None if unknown."""
    if not session_id:
        return None
    try:
        from boarai.core.session.manager import get_session_manager
        from boarai.core.session.workspace import resolve_session_workspace

        manager = get_session_manager()
        session = await manager.get_session(session_id)
        return str(resolve_session_workspace(
            session=session, session_id=session_id))
    except Exception:  # noqa: BLE001 — policy then falls back to strict mode
        logger.debug("session workspace resolution failed for %s", session_id)
        return None


def _fs_error_response(e: Exception) -> web.Response:
    from boarai.core.fs import FsError

    if isinstance(e, FsError):
        return _err(e.status, e.code, e.message)
    return _err(500, "INTERNAL_ERROR", "internal error")


async def handle_fs_read(request: web.Request) -> web.Response:
    """GET /api/fs/read?path=&session_id= — text content for the viewer."""
    try:
        from boarai.core import fs

        path = request.query.get("path") or ""
        session_id = request.query.get("session_id") or None
        session_ws = await _session_workspace_for(session_id)
        data = await fs.read_text(path, session_ws=session_ws,
                                  session_id=session_id)
        return _ok(data)
    except Exception as e:  # noqa: BLE001
        from boarai.core.fs import FsError

        if not isinstance(e, FsError):
            logger.exception("fs read failed")
        return _fs_error_response(e)


async def handle_fs_write(request: web.Request) -> web.Response:
    """PUT /api/fs/write — {path, content, session_id?, base_mtime?}."""
    try:
        from boarai.core import fs
        from boarai.core.event.types import EventType
        from boarai.core.runtime import spawn

        body: Dict[str, Any] = await request.json()
        path = body.get("path") or ""
        content = body.get("content")
        session_id = body.get("session_id") or None
        base_mtime = body.get("base_mtime")
        if content is None:
            return _err(400, "MISSING_PARAMETER", "content is required")
        session_ws = await _session_workspace_for(session_id)
        data = await fs.write_text(
            path, content, session_ws=session_ws, session_id=session_id,
            base_mtime=base_mtime)
        # fs.changed is emitted from the core.fs.post_write hook binding
        # (single emission point covering UI saves AND agent tool writes).
        return _ok(data)
    except Exception as e:  # noqa: BLE001
        from boarai.core.fs import FsError

        if not isinstance(e, FsError):
            logger.exception("fs write failed")
        return _fs_error_response(e)


_FS_HOOK_BOUND = False


def _bind_fs_changed_broadcast() -> None:
    """Mirror every file write (UI save or agent tool) to all WS clients."""
    global _FS_HOOK_BOUND
    if _FS_HOOK_BOUND:
        return
    _FS_HOOK_BOUND = True

    from pathlib import Path

    from boarai.core.extension import get_hook_registry

    async def _on_post_write(payload: Any) -> Any:
        try:
            path = (payload or {}).get("path")
            if not path:
                return None
            mtime: Optional[str] = None
            try:
                mtime = str(Path(str(path)).stat().st_mtime_ns)
            except OSError:
                pass
            from boarai.core.api import get_session_api_handler
            from boarai.core.event.types import EventType
            from boarai.core.runtime import spawn

            handler = get_session_api_handler()
            if handler is not None:
                spawn(handler._safe_broadcast(EventType.FS_CHANGED.value, {
                    "path": str(path),
                    "mtime": mtime,
                    "session_id": (payload or {}).get("session_id"),
                }))
        except Exception:  # noqa: BLE001 — broadcast must never break writes
            logger.debug("fs.changed broadcast failed", exc_info=True)
        return None

    get_hook_registry().register(
        "core.fs.post_write", _on_post_write, owner="core")


async def handle_fs_raw(request: web.Request) -> web.StreamResponse:
    """GET /api/fs/raw?path=&session_id= — inline image bytes for the viewer."""
    try:
        from boarai.core import fs

        path = request.query.get("path") or ""
        session_id = request.query.get("session_id") or None
        session_ws = await _session_workspace_for(session_id)
        resolved = fs.check_raw_image(path, session_ws=session_ws,
                                      session_id=session_id)
        mime = fs.RAW_IMAGE_TYPES[resolved.suffix.lower()]
        response = web.FileResponse(resolved, chunk_size=64 * 1024)
        response.headers["Content-Type"] = mime
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except Exception as e:  # noqa: BLE001
        from boarai.core.fs import FsError

        if not isinstance(e, FsError):
            logger.exception("fs raw failed")
        return _fs_error_response(e)


def register_fs_routes(app: web.Application) -> None:
    _bind_fs_changed_broadcast()
    app.router.add_get("/api/fs/roots", handle_fs_roots)
    app.router.add_get("/api/fs/default-workspace", handle_fs_default_workspace)
    app.router.add_get("/api/fs/browse", handle_fs_browse)
    app.router.add_post("/api/fs/validate", handle_fs_validate)
    app.router.add_get("/api/fs/read", handle_fs_read)
    app.router.add_put("/api/fs/write", handle_fs_write)
    app.router.add_get("/api/fs/raw", handle_fs_raw)