"""Log handlers for BoarAi system.

``DatedCategoryHandler``
    File sink for the ``<base>/<YYYY-MM-DD>/<name>`` layout. One directory per
    local day, size rotation inside the day, fails soft on every I/O error.

``NonFormattingQueueHandler``
    Hands records to the writer thread intact, so tracebacks survive the queue.

``MemoryLogBuffer``
    Bounded in-memory ring of recent records. It exists because a file is the
    wrong source for "what just happened": reading it back needs disk I/O and
    tail parsing, and the newest lines may still be sitting in the write queue.
    The ring is what a status endpoint or a diagnostics bundle reads.

``LoggerPrefixFilter``
    Domain routing: which records a per-domain file accepts.

The file and memory handlers are normally reached through the manager's queue
listener, so ``emit`` never performs disk I/O on the event loop. The date
helpers (``today_str`` / ``dated_path`` / ``iter_date_dirs``) are shared with
the other owners of the same dated tree — the EventBus audit trail, the usage
ledger and the security audit log all write into it.
"""

from __future__ import annotations

import copy
import logging
import logging.handlers
import queue
import re
import sys
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Sequence

# Date-directory naming: <base>/<YYYY-MM-DD>/<filename>
_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


class LoggerPrefixFilter(logging.Filter):
    """Pass only records whose logger name matches one of the prefixes."""

    def __init__(self, prefixes: Sequence[str]):
        super().__init__()
        self._prefixes = tuple(prefixes)

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        for prefix in self._prefixes:
            if name == prefix or name.startswith(prefix + "."):
                return True
        return False


def today_str() -> str:
    """Current local date as a directory name (YYYY-MM-DD)."""
    return datetime.now().strftime("%Y-%m-%d")


def dated_path(base_dir: Path, filename: str) -> Path:
    """Path of *filename* inside today's date directory under *base_dir*."""
    return base_dir / today_str() / filename


def iter_date_dirs(base_dir: Path) -> List[Path]:
    """All date-named directories under *base_dir*, oldest first."""
    try:
        entries = list(base_dir.iterdir())
    except OSError:
        return []
    dirs = [p for p in entries if p.is_dir() and _DATE_DIR_RE.match(p.name)]
    return sorted(dirs, key=lambda p: p.name)


class DatedCategoryHandler(logging.Handler):
    """File handler writing to ``<base_dir>/<YYYY-MM-DD>/<filename>``."""

    def __init__(
        self,
        base_dir: Path,
        filename: str,
        level: int = logging.DEBUG,
        max_size: int = 20 * 1024 * 1024,
        backup_count: int = 3,
        encoding: str = "utf-8",
    ):
        super().__init__(level)
        self.base_dir = Path(base_dir)
        self.filename = filename
        self.max_size = max_size
        self.backup_count = backup_count
        self.encoding = encoding
        self._current_date: Optional[str] = None
        self._file: Optional[Any] = None
        self._rotate_warned = False

    # ------------------------------------------------------------------ core

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with self.lock:
                self._write_line(msg + "\n")
        except Exception:  # noqa: BLE001 - a sink must never propagate
            self.handleError(record)

    def _write_line(self, line: str) -> None:
        today = today_str()
        if self._file is None or today != self._current_date:
            self._open_for(today)
        if self.max_size > 0 and self._file.tell() >= self.max_size:
            self._rotate()
        self._file.write(line)
        self._file.flush()

    def _open_for(self, date_str: str) -> None:
        self._close_file()
        path = self.base_dir / date_str / self.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(path, "a", encoding=self.encoding, newline="")
        self._current_date = date_str

    def _rotate(self) -> None:
        self._close_file()
        path = self.base_dir / (self._current_date or today_str()) / self.filename
        try:
            if self.backup_count <= 0:
                path.unlink(missing_ok=True)
            else:
                for i in range(self.backup_count - 1, 0, -1):
                    src = path.with_name(f"{path.stem}.{i}{path.suffix}")
                    dst = path.with_name(f"{path.stem}.{i + 1}{path.suffix}")
                    if src.exists():
                        src.replace(dst)
                if path.exists():
                    path.replace(path.with_name(f"{path.stem}.1{path.suffix}"))
        except OSError as exc:
            # Keep appending to the un-rotated file: losing the cap is far
            # better than losing records. Report once per handler.
            if not self._rotate_warned:
                self._rotate_warned = True
                logging.getLogger(__name__).warning(
                    "Log rotation failed for %s (%s); appending past the %d-byte cap",
                    path, exc, self.max_size,
                )
        self._file = open(path, "a", encoding=self.encoding, newline="")

    def _close_file(self) -> None:
        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            except Exception:  # noqa: BLE001
                pass
            self._file = None

    # ------------------------------------------------------------- lifecycle

    def flush(self) -> None:
        with self.lock:
            if self._file is not None:
                try:
                    self._file.flush()
                except Exception:  # noqa: BLE001
                    pass

    def close(self) -> None:
        with self.lock:
            self._close_file()
            self._current_date = None
        super().close()


class NonFormattingQueueHandler(logging.handlers.QueueHandler):
    """``QueueHandler`` for an in-process queue."""

    def __init__(self, q: "queue.Queue[Any]"):
        super().__init__(q)
        self.dropped = 0
        self._warned_full = False

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return copy.copy(record)

    def emit(self, record: logging.LogRecord) -> None:
        """Enqueue without ever blocking the caller, including when full."""
        # stdlib QueueHandler.emit catches every enqueue exception and routes it
        # to handleError(), which would make a full queue invisible. Override
        # the enqueue step itself so overflow stays observable.
        try:
            self.enqueue(self.prepare(record))
        except queue.Full:
            self.dropped += 1
            if not self._warned_full:
                self._warned_full = True
                try:
                    if sys.stderr is not None:
                        sys.stderr.write(
                            "BoarAI log queue is full; dropping new log records "
                            "(disk writer is not keeping up)\n"
                        )
                except Exception:
                    pass
        except Exception:  # noqa: BLE001 - a sink must never propagate
            self.handleError(record)


class MemoryLogBuffer(logging.Handler):
    """Bounded ring of recent records, newest last."""

    def __init__(self, level: int = logging.INFO, capacity: int = 1000):
        super().__init__(level)
        self.capacity = max(1, int(capacity))
        self._entries: Deque[Dict[str, Any]] = deque(maxlen=self.capacity)
        self._lock = threading.Lock()
        self._dropped = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "ts": record.created,
                "time": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
                "ctx": dict(getattr(record, "ctx", {}) or {}),
            }
            with self._lock:
                if len(self._entries) == self.capacity:
                    self._dropped += 1
                self._entries.append(entry)
        except Exception:  # noqa: BLE001
            self.handleError(record)

    def __len__(self) -> int:
        """How many records are currently buffered."""
        return len(self._entries)

    @property
    def dropped(self) -> int:
        """How many records were evicted since startup (observability)."""
        return self._dropped

    def snapshot(
        self,
        limit: int = 200,
        level: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Newest-last entries, optionally filtered by minimum level/substring."""
        with self._lock:
            entries = list(self._entries)
        min_rank = _LEVEL_ORDER.get((level or "").upper(), 0)
        needle = (search or "").lower()
        out: List[Dict[str, Any]] = []
        for entry in reversed(entries):
            if min_rank and _LEVEL_ORDER.get(entry["level"], 0) < min_rank:
                continue
            if needle and needle not in entry["message"].lower():
                continue
            out.append(entry)
            if len(out) >= max(1, int(limit)):
                break
        out.reverse()
        return out

    def clear(self) -> None:
        """Drop every buffered entry (test isolation)."""
        with self._lock:
            self._entries.clear()
            self._dropped = 0

    def close(self) -> None:
        self.clear()
        super().close()
