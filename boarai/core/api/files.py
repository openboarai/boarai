"""
Session attachment upload / download HTTP endpoints.

Routes (registered via :func:`register_files_routes`):

- ``POST /api/sessions/{session_id}/files`` — multipart upload (field "file"),
  streamed to a temp file then atomically renamed into the session's
  ``files/`` directory. Returns attachment reference metadata that callers
  embed in ``SessionMessage.metadata["attachments"]``.
- ``GET /api/sessions/{session_id}/files/{stored_name}`` — streamed download
  (inline for images / attachment disposition otherwise).

Binary payloads intentionally bypass the WebSocket JSON channel; the WS
``session.run.create`` protocol only ever carries the small reference metadata
produced by the upload endpoint.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from aiohttp import web

from boarai.core.session import attachments
from boarai.core.session.manager import get_session_manager

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 1024 * 1024  # 1 MiB streaming chunks (bounded memory)


# --- Route registration ---


def register_files_routes(app: web.Application) -> None:
    """Attach session attachment routes to the app router."""
    app.router.add_post(
        "/api/sessions/{session_id}/files", handle_upload_file
    )
    app.router.add_get(
        "/api/sessions/{session_id}/files/{stored_name}", handle_download_file
    )


# --- Handlers ---


async def handle_upload_file(request: web.Request) -> web.Response:
    """POST /api/sessions/{session_id}/files — streamed multipart upload."""
    session_id = request.match_info.get("session_id", "")

    session = await get_session_manager().get_session(session_id)
    if not session:
        return _err(404, "SESSION_NOT_FOUND", f"Session {session_id} not found")

    files_dir = attachments.get_files_dir(session_id)

    try:
        reader = await request.multipart()
    except Exception as e:  # noqa: BLE001 - malformed multipart from client
        logger.warning("multipart parse failed for %s: %s", session_id, e)
        return _err(400, "INVALID_UPLOAD", "Expected multipart/form-data")

    file_part = None
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "file":
            file_part = part
            break

    if file_part is None or not file_part.filename:
        return _err(400, "MISSING_FILE", "No 'file' field found in upload")

    original_name = str(file_part.filename or "upload")
    stored_name = attachments.allocate_stored_name(files_dir, original_name)
    target = files_dir / stored_name

    size = 0
    tmp_path = files_dir / f".tmp_{uuid4().hex}"
    try:
        with open(tmp_path, "wb") as fh:
            while True:
                chunk = await file_part.read_chunk(_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > attachments.MAX_FILE_BYTES:
                    raise _UploadTooLarge(size)
                fh.write(chunk)
        if size == 0:
            return _err(400, "EMPTY_FILE", "Uploaded file is empty")
        tmp_path.replace(target)
    except _UploadTooLarge:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return _err(
            413,
            "FILE_TOO_LARGE",
            f"File exceeds {attachments.MAX_FILE_BYTES // (1024 * 1024)} MB limit",
        )
    except Exception as e:  # noqa: BLE001 - disk / stream failures
        logger.error("Failed to store upload %s in %s: %s", stored_name, session_id, e)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return _err(500, "UPLOAD_FAILED", "Failed to persist uploaded file")

    # MIME detection only needs a signature prefix; never reload the whole
    # uploaded file into RAM after it has already been streamed to disk.
    with target.open("rb") as uploaded:
        head = uploaded.read(12)
    mime_type = attachments.detect_mime_type(original_name, head)
    kind = attachments.detect_kind(original_name, mime_type)

    logger.info(
        "Uploaded attachment %s (%s bytes) to session %s", stored_name, size, session_id
    )
    return _ok({
        "name": original_name,
        "stored_name": stored_name,
        "size": size,
        "mime_type": mime_type,
        "kind": kind,
        "url": f"/api/sessions/{session_id}/files/{stored_name}",
    })


async def handle_download_file(request: web.Request) -> web.Response:
    """GET /api/sessions/{session_id}/files/{stored_name} — streamed download."""
    session_id = request.match_info.get("session_id", "")
    stored_name = request.match_info.get("stored_name", "")

    session = await get_session_manager().get_session(session_id)
    if not session:
        return _err(404, "SESSION_NOT_FOUND", f"Session {session_id} not found")

    path = attachments.resolve_attachment_path(session_id, stored_name)
    if path is None:
        return _err(404, "FILE_NOT_FOUND", f"Attachment {stored_name} not found")

    disposition = "inline"
    safe_name = attachments.sanitize_filename(stored_name)
    if not attachments.detect_kind(safe_name, "").startswith("image"):
        disposition = "attachment"

    return web.FileResponse(
        path,
        headers={
            "Content-Disposition": f'{disposition}; filename="{_quote_filename(safe_name)}"',
            "Cache-Control": "private, max-age=3600",
        },
    )


# --- Helpers ---


class _UploadTooLarge(Exception):
    """Internal sentinel for the size limit breach."""


def _quote_filename(name: str) -> str:
    """Percent-encode characters unsafe inside a quoted Content-Disposition."""
    from urllib.parse import quote

    quoted = quote(name, safe="")
    if len(quoted) > 150:  # RFC 6266 suggests keeping header lines short
        quoted = quoted[:150]
    return quoted


def _ok(data: Any, status: int = 200) -> web.Response:
    return web.json_response({"success": True, "data": data}, status=status)


def _err(status: int, code: str, message: str) -> web.Response:
    return web.json_response(
        {"success": False, "error": {"code": code, "message": message}},
        status=status,
    )
