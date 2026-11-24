"""Log inspection API for BoarAi — read-only.

Endpoints (all behind the same access gate as the rest of ``/api``):

    GET /api/logs/files   — inventory of one day's log tree (+ available dates)
    GET /api/logs         — tail one file, with level / search / since filters
    GET /api/logs/recent  — the in-memory ring (no disk I/O, includes records
                            still sitting in the write queue)
    GET /api/logs/stats   — sink health: queue depth, degradations, retention

Why an endpoint at all: the logs live under the data directory, which on a NAS
or a container is not the machine the operator is sitting at. Without this the
only way to read them is shell access to the host.

Safety rules, in the order they are enforced:

* the ``file`` argument must resolve *inside* the log directory — a name is
  matched against the real inventory, then re-checked with ``is_relative_to``,
  so neither ``../`` nor an absolute path nor a symlinked name can escape;
* every count is clamped (lines, scan bytes), because the caller is remote;
* content is already redacted at write time (``SafeFormatter``), so this layer
  never sees a plaintext credential — it must not become the place that does.

HTTP-only by design: this is a pull surface with no server-initiated state, so
there is no WebSocket view to keep in sync. A live tail, if it is ever added,
becomes a ``log.*`` bus event registered in ``EventType`` and mirrored here.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import web

from boarai.core.logging import get_log_manager
from boarai.core.logging.reader import (
    available_dates,
    filter_lines,
    list_log_files,
    parse_since,
    read_tail,
)

logger = logging.getLogger(__name__)

#: Hard ceiling on one response. ``read_tail`` also bounds how much of the file
#: it scans, so a large ``lines`` value costs memory, not disk.
MAX_LINES = 1000
DEFAULT_LINES = 200

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _clamp_lines(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LINES
    return max(1, min(value, MAX_LINES))


def _resolve_log_file(log_dir: Path, name: str, date: Optional[str]) -> Optional[Path]:
    """Resolve a requested file name to a real path inside *log_dir*."""
    if not name:
        return None
    candidate = Path(name)
    if candidate.is_absolute() or candidate.name != str(name):
        # An absolute path or anything carrying a separator is not a file name.
        return None
    # With no explicit date, search newest-first for the requested file. A new
    # day may contain only fatal.log so far; all.log must still be readable
    # from yesterday instead of returning a misleading 404.
    days = [date] if date else available_dates(log_dir)
    for day in days:
        for entry in list_log_files(log_dir, date=day):
            if entry["name"] == candidate.name:
                path = Path(entry["path"]).resolve()
                try:
                    if not path.is_relative_to(log_dir.resolve()):
                        return None
                except (OSError, ValueError):
                    return None
                return path
    return None


async def handle_list_files(request: web.Request) -> web.Response:
    """GET /api/logs/files — what exists, how big, and which dates are kept."""
    manager = get_log_manager()
    log_dir = manager.log_dir
    date = request.query.get("date") or None
    try:
        files = await asyncio.to_thread(list_log_files, log_dir, date)
        dates = await asyncio.to_thread(available_dates, log_dir)
    except Exception as exc:  # noqa: BLE001 - a listing must never 500 hard
        logger.warning("log inventory failed: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)

    return web.json_response({
        "log_dir": str(log_dir),
        "date": date or (files[0]["date"] if files else (dates[0] if dates else None)),
        "dates": dates,
        "files": files,
        "total_bytes": sum(int(f.get("bytes", 0)) for f in files),
    })


async def handle_read(request: web.Request) -> web.Response:
    """GET /api/logs — tail one file with optional level/search/since filters."""
    manager = get_log_manager()
    query = request.query
    name = query.get("file", "all.log")
    date = query.get("date") or None
    lines = _clamp_lines(query.get("lines"))
    level = (query.get("level") or "").upper() or None
    search = query.get("search") or None
    since_raw = query.get("since") or None

    if level and level not in _VALID_LEVELS:
        return web.json_response(
            {"error": f"Invalid level: {level}", "allowed": sorted(_VALID_LEVELS)},
            status=400,
        )
    since = parse_since(since_raw) if since_raw else None
    if since_raw and since is None:
        return web.json_response(
            {"error": f"Invalid since: {since_raw}", "hint": "30s / 15m / 6h / 7d"},
            status=400,
        )

    path = _resolve_log_file(manager.log_dir, name, date)
    if path is None:
        return web.json_response(
            {"error": f"Unknown log file: {name}", "hint": "see GET /api/logs/files"},
            status=404,
        )

    # Always over-read: a filtered read has to scan more lines than it returns,
    # and an unfiltered one still needs to know whether more exist above (that
    # is what ``truncated`` tells a viewer). Bounded by MAX_LINES * 5 so a large
    # ``lines`` value costs memory, not an unbounded disk scan — ``read_tail``
    # caps its own scan window on top of this.
    want = min(max(lines * 10, lines + 1), MAX_LINES * 5)

    try:
        raw = await asyncio.to_thread(read_tail, path, want)
        filtered = filter_lines(raw, level=level, search=search, since=since)
    except Exception as exc:  # noqa: BLE001
        logger.warning("log read failed for %s: %s", name, exc)
        return web.json_response({"error": str(exc)}, status=500)

    return web.json_response({
        "file": Path(name).name,
        "date": date or (path.parent.name if path.parent != manager.log_dir else None),
        "path": str(path),
        "count": len(filtered[-lines:]),
        "scanned": len(raw),
        "truncated": len(filtered) > lines,
        "lines": filtered[-lines:],
    })


async def handle_recent(request: web.Request) -> web.Response:
    """GET /api/logs/recent — the in-memory ring (no disk I/O)."""
    manager = get_log_manager()
    limit = _clamp_lines(request.query.get("limit"))
    level = (request.query.get("level") or "").upper() or None
    if level and level not in _VALID_LEVELS:
        return web.json_response({"error": f"Invalid level: {level}"}, status=400)
    search = request.query.get("search") or None

    entries: List[Dict[str, Any]] = manager.recent(limit=limit, level=level, search=search)
    return web.json_response({"count": len(entries), "entries": entries})


async def handle_stats(request: web.Request) -> web.Response:
    """GET /api/logs/stats — sink health, queue depth, retention, degradations."""
    manager = get_log_manager()
    try:
        stats = manager.get_stats()
    except Exception as exc:  # noqa: BLE001
        logger.warning("log stats failed: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)

    # The full config echoes paths and sizes; that is useful for support and
    # contains no credential (keys are never part of LoggingConfig).
    return web.json_response(stats)
