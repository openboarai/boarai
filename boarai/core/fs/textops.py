"""Text file read/write primitives for the WebUI file manager.

Phase-2 backend of the session-page file explorer. Security model:

- Paths are shape-validated (no NTFS alternate data streams, no Windows
  reserved device names), resolved, and must be regular files.
- ``PathGuard.classify`` decides; the session workspace (the directory the
  session is configured to work in) waives the CONFIRM tier for both read and
  write (owner decision 2026-11-13), while the absolute write floor still
  denies in every mode. Outside the session workspace a CONFIRM verdict is
  rejected with 403 — the file manager has no approval prompt channel.
- Writes are optimistic-lock guarded (``base_mtime``), atomic (temp file +
  ``os.replace``), preserve the file's original line ending and UTF-8 BOM,
  and fire the vetoable ``core.fs.pre_write`` / observe ``post_write`` hooks
  plus a security audit record.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Single cap for both read payload and write body. aiohttp's default request
# body limit is 1 MiB; JSON escaping inflates worst-case ~1.2x for code, so
# 768 KiB of content stays safely below it without touching the global limit.
FS_TEXT_MAX = 768 * 1024

_WIN_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class FsError(Exception):
    """Base error carrying an HTTP status + stable code for the API layer."""

    status = 400
    code = "FS_ERROR"

    def __init__(self, message: str, **data: Any) -> None:
        super().__init__(message)
        self.message = message
        self.data = data


class FsBadRequest(FsError):
    status = 400
    code = "FS_BAD_REQUEST"


class FsDenied(FsError):
    status = 403
    code = "FS_DENIED"


class FsNotFound(FsError):
    status = 404
    code = "FS_NOT_FOUND"


class FsConflict(FsError):
    status = 409
    code = "FS_CONFLICT"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _shape_validate(raw: str) -> Path:
    """Reject ADS suffixes / reserved names before any filesystem touch."""
    text = str(raw or "").strip()
    if not text:
        raise FsBadRequest("path is required")
    # Drive-letter colon is fine; any other colon means ADS or junk on Win.
    stripped = text[2:] if re.match(r"^[A-Za-z]:[\\/]", text) else text
    if ":" in stripped:
        raise FsBadRequest("Alternate data streams are not supported")
    name = Path(text).name.lower()
    base = name.split(".")[0]
    if base in _WIN_RESERVED:
        raise FsBadRequest(f"Reserved device name: {name}")
    return Path(text)


def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError as e:
        raise FsBadRequest(f"Path resolution failed: {e}") from e


def _is_under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _get_guard(guard: Any = None):
    if guard is not None:
        return guard
    from boarai.core.security import get_security_manager

    sm = get_security_manager()
    found = getattr(sm, "path_guard", None) or getattr(sm, "_path_guard", None)
    if found is None:
        raise FsDenied("PathGuard unavailable")
    return found


def _policy_check(op: str, resolved: Path, session_ws: Optional[Path],
                  session_id: Optional[str], guard: Any = None) -> None:
    """Apply PathGuard; waive the confirm tier inside the session workspace."""
    guard = _get_guard(guard)
    result = guard.classify(op, str(resolved), session_id)
    if not result.allowed:
        raise FsDenied(result.reason or f"{op} denied")
    if result.needs_approval:
        if session_ws is not None and _is_under(resolved, session_ws):
            # Owner decision: the session's own directory is fully readable
            # and writable from the file manager (floor denials already
            # returned above).
            return
        raise FsDenied(
            (result.reason or f"{op} requires confirmation")
            + " (no approval channel in file manager)"
        )


def _detect_text(raw: bytes) -> Tuple[bool, bool, str]:
    """Return (binary, bom, newline) for a raw byte prefix/full buffer."""
    if b"\x00" in raw:
        return True, False, "\n"
    bom = raw.startswith(b"\xef\xbb\xbf")
    newline = "\r\n" if b"\r\n" in raw else "\n"
    return False, bom, newline


def _read_sync(path: Path, max_bytes: int) -> Dict[str, Any]:
    with open(path, "rb") as fh:
        raw = fh.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
    binary, bom, newline = _detect_text(raw)
    content = ""
    if not binary:
        try:
            decoded = raw.decode("utf-8-sig" if bom else "utf-8")
        except UnicodeDecodeError:
            binary = True
            decoded = ""
        # The editor works in LF; write_text translates back to the file's
        # original line ending, so CRLF files never double up on save.
        content = decoded.replace("\r\n", "\n").replace("\r", "\n")
    stat = path.stat()
    return {
        "content": content,
        "binary": binary,
        "truncated": truncated,
        "bom": bom,
        "newline": newline,
        "size": stat.st_size,
        "mtime": stat.st_mtime_ns,
    }


def _write_sync(path: Path, content: str, newline: str, bom: bool) -> int:
    tmp = path.with_name(f"{path.name}.fm-tmp-{uuid.uuid4().hex[:8]}")
    try:
        with open(tmp, "w", encoding="utf-8-sig" if bom else "utf-8",
                  newline=newline) as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path.stat().st_mtime_ns


async def _trigger_hook(point: str, payload: Dict[str, Any]) -> Any:
    try:
        from boarai.core.extension import get_hook_registry

        return await get_hook_registry().trigger(point, payload, timeout=2.0)
    except Exception as e:  # noqa: BLE001 — hooks must never brick the save
        logger.debug("fs hook %s failed: %s", point, e)
        return None


def _hook_vetoed(result: Any) -> Optional[str]:
    """HookResult (or dict) -> veto reason, else None."""
    if result is None:
        return None
    blocked = getattr(result, "blocked", None)
    reason = getattr(result, "reason", None)
    if blocked is None and isinstance(result, dict):
        blocked = result.get("blocked")
        reason = result.get("reason")
    if blocked:
        return str(reason or "Blocked by extension hook")
    return None


def _audit(event: str, data: Dict[str, Any]) -> None:
    try:
        from boarai.core.security import get_security_manager

        sm = get_security_manager()
        audit = getattr(sm, "audit", None)
        if audit is not None:
            audit.record(event, data)
    except Exception as e:  # noqa: BLE001
        logger.debug("fs audit failed: %s", e)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Raster images the viewer may serve inline via /api/fs/raw. SVG is
# deliberately excluded: same-origin image/svg+xml is an XSS vector.
RAW_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
}


def check_raw_image(path: str, session_ws: Optional[str] = None,
                    session_id: Optional[str] = None,
                    guard: Any = None) -> Path:
    """Policy-check an inline image request; return the resolved Path."""
    resolved = _resolve(_shape_validate(path))
    if not resolved.exists():
        raise FsNotFound("Path does not exist")
    if not resolved.is_file():
        raise FsBadRequest("Path is not a regular file")
    mime = RAW_IMAGE_TYPES.get(resolved.suffix.lower())
    if mime is None:
        raise FsBadRequest("Only raster images can be served inline")
    ws = _resolve(Path(session_ws)) if session_ws else None
    _policy_check("read", resolved, ws, session_id, guard)
    return resolved


async def read_text(path: str, session_ws: Optional[str] = None,
                    session_id: Optional[str] = None,
                    max_bytes: int = FS_TEXT_MAX,
                    guard: Any = None) -> Dict[str, Any]:
    """Read a text file for the viewer. Raises FsError subclasses."""
    resolved = _resolve(_shape_validate(path))
    if not resolved.exists():
        raise FsNotFound("Path does not exist")
    if not resolved.is_file():
        raise FsBadRequest("Path is not a regular file")
    ws = _resolve(Path(session_ws)) if session_ws else None
    _policy_check("read", resolved, ws, session_id, guard)
    info = await asyncio.to_thread(_read_sync, resolved, max_bytes)
    return {
        "path": str(resolved),
        "content": info["content"],
        "size": info["size"],
        # ns precision exceeds JS safe integers, so mtime travels as an
        # opaque string token; clients only echo/compare it.
        "mtime": str(info["mtime"]),
        "encoding": "utf-8-sig" if info["bom"] else "utf-8",
        "binary": info["binary"],
        "truncated": info["truncated"],
    }


async def write_text(path: str, content: str, session_ws: Optional[str] = None,
                     session_id: Optional[str] = None,
                     base_mtime: Optional[int] = None,
                     guard: Any = None) -> Dict[str, Any]:
    """Atomically save an existing text file. Raises FsError subclasses."""
    if not isinstance(content, str):
        raise FsBadRequest("content must be a string")
    encoded_len = len(content.encode("utf-8", errors="strict"))
    if encoded_len > FS_TEXT_MAX:
        raise FsBadRequest(
            f"Content exceeds the {FS_TEXT_MAX // 1024} KiB editor limit")
    resolved = _resolve(_shape_validate(path))
    if not resolved.exists():
        raise FsNotFound("Path does not exist (creating files is not supported)")
    if not resolved.is_file():
        raise FsBadRequest("Path is not a regular file")
    ws = _resolve(Path(session_ws)) if session_ws else None
    _policy_check("write", resolved, ws, session_id, guard)

    veto = _hook_vetoed(await _trigger_hook("core.fs.pre_write", {
        "tool": "webui.file_save",
        "path": str(resolved),
        "session_id": session_id,
        "agent": "webui",
        "operation": "write",
        "source": "webui_file_manager",
    }))
    if veto:
        _audit("fs_write_ui_denied", {"path": str(resolved), "reason": veto})
        raise FsDenied(veto)

    def _probe() -> Tuple[int, str, bool]:
        stat = resolved.stat()
        with open(resolved, "rb") as fh:
            head = fh.read(65536)
        _, bom, newline = _detect_text(head)
        return stat.st_mtime_ns, newline, bom

    current_mtime, newline, bom = await asyncio.to_thread(_probe)
    if base_mtime is not None and str(base_mtime) != str(current_mtime):
        raise FsConflict(
            "File changed on disk since it was loaded",
            mtime=str(current_mtime))

    new_mtime = await asyncio.to_thread(
        _write_sync, resolved, content, newline, bom)
    await _trigger_hook("core.fs.post_write", {
        "tool": "webui.file_save",
        "path": str(resolved),
        "session_id": session_id,
        "agent": "webui",
    })
    _audit("fs_write_ui", {
        "path": str(resolved),
        "session_id": session_id,
        "size": encoded_len,
    })
    return {"path": str(resolved), "size": encoded_len, "mtime": str(new_mtime)}
