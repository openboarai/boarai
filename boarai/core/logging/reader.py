"""Read-only access to the log tree.

Everything here is a pure function over the ``<logs>/<YYYY-MM-DD>/`` layout: it
never writes, never holds a handle open, and never walks more of a file than the
caller asked for. A viewer (HTTP endpoint, CLI, diagnostics bundle) is therefore
a thin shell over these, and the reading rules — which files exist, how much is
scanned, how lines are filtered — live in one place instead of being reinvented
per surface.

``read_tail`` reads backwards in blocks: asking for the last 200 lines of a
20 MB file must not read 20 MB.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .handlers import iter_date_dirs, today_str

#: Block size for the backwards scan.
_TAIL_BLOCK = 64 * 1024
#: Never scan more than this many bytes for one tail request, whatever the
#: caller asked for: a pathological ``lines=100000`` must not read a whole day.
_MAX_SCAN_BYTES = 4 * 1024 * 1024

_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")
_LEVEL_RE = re.compile(r"\s(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b")
_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
_SINCE_RE = re.compile(r"^(\d+)\s*([smhd])$")


def parse_since(value: Optional[str]) -> Optional[datetime]:
    """Parse ``30s`` / ``15m`` / ``6h`` / ``7d`` into a cutoff; None if invalid."""
    if not value:
        return None
    match = _SINCE_RE.match(str(value).strip().lower())
    if not match:
        return None
    unit = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[match.group(2)]
    return datetime.now() - timedelta(**{unit: int(match.group(1))})


def _newest_populated_date(base_dir: Path) -> str:
    """Newest date directory that actually contains a file."""
    for day_dir in reversed(iter_date_dirs(base_dir)):
        try:
            if any(entry.is_file() for entry in day_dir.iterdir()):
                return day_dir.name
        except OSError:
            continue
    return today_str()


def list_log_files(base_dir: Path, date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Inventory of the log tree, newest date first."""
    base = Path(base_dir)
    wanted = date or _newest_populated_date(base)
    out: List[Dict[str, Any]] = []
    for day_dir in reversed(iter_date_dirs(base)):
        if day_dir.name != wanted:
            continue
        try:
            entries = sorted(day_dir.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                stat = entry.stat()
            except OSError:
                continue
            out.append({
                "date": day_dir.name,
                "name": entry.name,
                "path": str(entry),
                "bytes": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            })
    return out


def available_dates(base_dir: Path) -> List[str]:
    """Date directories present under *base_dir*, newest first."""
    return [d.name for d in reversed(iter_date_dirs(Path(base_dir)))]


def read_tail(path: Path, max_lines: int = 200, max_scan_bytes: int = _MAX_SCAN_BYTES) -> List[str]:
    """Last *max_lines* lines of *path*, oldest first, without reading it all."""
    path = Path(path)
    limit = max(1, int(max_lines))
    try:
        if not path.is_file():
            return []
        size = path.stat().st_size
    except OSError:
        return []
    if size == 0:
        return []

    scan_budget = min(max_scan_bytes, size)
    lines: List[str] = []
    remainder = b""
    read_bytes = 0
    try:
        with open(path, "rb") as handle:
            offset = size
            while offset > 0 and len(lines) <= limit and read_bytes < scan_budget:
                block = min(_TAIL_BLOCK, offset, scan_budget - read_bytes)
                offset -= block
                handle.seek(offset)
                chunk = handle.read(block)
                read_bytes += len(chunk)
                parts = (chunk + remainder).split(b"\n")
                remainder = parts[0]
                for raw in reversed(parts[1:]):
                    text = raw.decode("utf-8", errors="replace").rstrip("\r")
                    if text:
                        lines.append(text)
                    if len(lines) > limit:
                        break
            if len(lines) <= limit and remainder:
                # First line of the scanned window (or of a file with no
                # trailing newline) — include it only when it fits the ask.
                text = remainder.decode("utf-8", errors="replace").rstrip("\r")
                if text:
                    lines.append(text)
    except OSError:
        return list(reversed(lines))[:limit]

    lines.reverse()
    return lines[-limit:]


def filter_lines(
    lines: List[str],
    *,
    level: Optional[str] = None,
    search: Optional[str] = None,
    since: Optional[datetime] = None,
) -> List[str]:
    """Filter already-read lines by minimum level, substring and cutoff time."""
    min_rank = _LEVEL_ORDER.get((level or "").upper(), 0)
    needle = (search or "").lower()
    out: List[str] = []
    keep_continuation = False

    for line in lines:
        stamp = _parse_stamp(line)
        if stamp is None:
            if keep_continuation:
                out.append(line)
            continue
        keep_continuation = False
        if min_rank:
            found = _LEVEL_RE.search(line)
            rank = _LEVEL_ORDER.get(found.group(1), 0) if found else 0
            if rank < min_rank:
                continue
        if needle and needle not in line.lower():
            continue
        if since is not None and stamp < since:
            continue
        out.append(line)
        keep_continuation = True

    return out


def _parse_stamp(line: str) -> Optional[datetime]:
    match = _TS_RE.search(line[:40])
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1).replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
