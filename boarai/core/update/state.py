"""Update worker state: OS file lock + status file + receipts.

Layout (all under the data-dir anchor):
  ``<data>/update/state.json``           — the worker's live status
  ``<data>/update/state.lock``           — OS advisory lock; "lock held"
                                           means a worker is running (the OS
                                           drops it when the process exits,
                                           even after a crash — the opm
                                           principle: one status file
                                           connects the server UI and the
                                           detached worker)
  ``<data>/logs/update_receipts/*.json`` — append-only outcome receipts
  ``<data>/logs/update_worker.log``      — the worker's stdout/stderr

Only the lock holder writes the status file. The server side never writes
it; it reads and mirrors phases onto the event bus.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Worker phases; done / current / failed end a run.
PHASE_CHECKING = "checking"
PHASE_STOPPING = "stopping"      # graceful server stop + wait for port free
PHASE_INSTALLING = "installing"
PHASE_VERIFYING = "verifying"
PHASE_RESTARTING = "restarting"
PHASE_DONE = "done"          # target installed, server restarted, version proven
PHASE_CURRENT = "current"    # nothing to do (already up to date)
PHASE_FAILED = "failed"
FINAL_PHASES = (PHASE_DONE, PHASE_CURRENT, PHASE_FAILED)
ALL_PHASES = (
    PHASE_CHECKING, PHASE_STOPPING, PHASE_INSTALLING, PHASE_VERIFYING,
    PHASE_RESTARTING, PHASE_DONE, PHASE_CURRENT, PHASE_FAILED,
)


def update_dir() -> Path:
    from boarai.core.runtime import get_data_dir

    return get_data_dir() / "update"


def state_path() -> Path:
    return update_dir() / "state.json"


def lock_path() -> Path:
    return update_dir() / "state.lock"


def receipts_dir() -> Path:
    from boarai.core.runtime import get_data_dir

    return get_data_dir() / "logs" / "update_receipts"


def worker_log_path() -> Path:
    from boarai.core.runtime import get_data_dir

    return get_data_dir() / "logs" / "update_worker.log"


def read_state() -> Optional[dict]:
    """The status file; None when absent or unreadable (e.g. read mid-write)."""
    try:
        raw = state_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_state(state: dict) -> None:
    """Atomic status write (tmp + replace); stamps ``at``."""
    payload = dict(state)
    payload["at"] = time.time()
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def write_receipt(payload: dict) -> Optional[Path]:
    """Append-only JSON receipt; best effort (never raises)."""
    try:
        directory = receipts_dir()
        directory.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = directory / f"update_{ts}_{os.getpid()}.json"
        body = {"at": time.time(), **payload}
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except OSError as e:
        logger.warning("update receipt write failed: %s", e)
        return None


def last_receipt(outcome: Optional[str] = None) -> Optional[dict]:
    """Most recent receipt (optionally filtered by outcome); None when absent."""
    try:
        files = sorted(receipts_dir().glob("update_*.json"), reverse=True)
    except OSError:
        return None
    for path in files[:20]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and (outcome is None or data.get("outcome") == outcome):
            return data
    return None


class FileLock:
    """Cross-platform OS advisory lock (msvcrt on Windows, flock on POSIX)."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._fd: Optional[int] = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> bool:
        """Non-blocking acquire; True when this instance now holds the lock."""
        if self._fd is not None:
            return True
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self._path), os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            return False
        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass


def lock_is_held() -> bool:
    """Whether some live process holds the worker lock (probe without stealing)."""
    probe = FileLock(lock_path())
    if probe.acquire():
        probe.release()
        return False
    return True
