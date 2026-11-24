"""Crash and uncaught-exception capture.

Three ways a failure can bypass the logging system entirely:

* an exception escaping the main thread — Python prints it to ``sys.stderr``
  and the detached server has no stderr at all;
* an exception escaping a worker thread — ``threading`` prints it the same way;
* a native crash (segfault, ``abort()``) — no Python handler runs at all.

The first two are routed through the logger so they land in ``all.log`` /
``error.log`` with full context; the third is covered by ``faulthandler``,
which writes Python-level tracebacks for every thread straight to a file
descriptor, independent of the interpreter's shutdown path.

Idempotent: ``LogManager`` re-runs setup on every config reload, and a doubled
hook would log each crash twice.
"""

from __future__ import annotations

import faulthandler
import logging
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_installed = False
#: Held for the process lifetime: closing it would leave faulthandler writing
#: to a dead descriptor.
_fatal_stream: Any = None


def install_crash_hooks(fatal_log_path: Optional[Path] = None) -> Dict[str, bool]:
    """Install the process-wide crash hooks. Returns what was installed."""
    global _installed, _fatal_stream
    result = {"excepthook": False, "thread_excepthook": False, "faulthandler": False}
    if _installed:
        # A config reload may have changed the log directory. The Python hooks
        # stay installed, but faulthandler is allowed to reopen on the new path
        # after close_faulthandler() released the old descriptor.
        if fatal_log_path is not None and _fatal_stream is None:
            result["faulthandler"] = _enable_faulthandler(fatal_log_path)
        return result

    previous_excepthook = sys.excepthook
    previous_thread_hook = threading.excepthook

    def _excepthook(exc_type, exc_value, exc_tb) -> None:
        # Log first: the default hook writes to stderr, which does not exist in
        # the detached server, so this is the only durable copy.
        try:
            logger.critical(
                "Unhandled exception on the main thread",
                exc_info=(exc_type, exc_value, exc_tb),
            )
        except Exception:  # noqa: BLE001 - never mask the original crash
            pass
        try:
            if previous_excepthook is not None:
                previous_excepthook(exc_type, exc_value, exc_tb)
        except Exception:  # noqa: BLE001
            pass

    def _thread_excepthook(args: Any) -> None:
        try:
            logger.error(
                "Unhandled exception in thread %s",
                getattr(args.thread, "name", "?"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        except Exception:  # noqa: BLE001
            pass
        try:
            if previous_thread_hook is not None:
                previous_thread_hook(args)
        except Exception:  # noqa: BLE001
            pass

    try:
        sys.excepthook = _excepthook
        result["excepthook"] = True
    except Exception:  # noqa: BLE001
        pass

    try:
        threading.excepthook = _thread_excepthook
        result["thread_excepthook"] = True
    except Exception:  # noqa: BLE001
        pass

    if fatal_log_path is not None:
        result["faulthandler"] = _enable_faulthandler(fatal_log_path)

    _installed = True
    return result


def _enable_faulthandler(fatal_log_path: Path) -> bool:
    """Open and enable the native-crash sink; never raises."""
    global _fatal_stream
    try:
        path = Path(fatal_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _fatal_stream = open(path, "a", encoding="utf-8")
        faulthandler.enable(file=_fatal_stream, all_threads=True)
        return True
    except Exception as exc:  # noqa: BLE001 - housekeeping must not fail startup
        _fatal_stream = None
        logger.debug("faulthandler not enabled: %s", exc)
        return False


def close_faulthandler() -> None:
    """Disable faulthandler and release its descriptor (safe to call twice)."""
    global _fatal_stream
    try:
        faulthandler.disable()
    except Exception:  # noqa: BLE001
        pass
    stream, _fatal_stream = _fatal_stream, None
    if stream is not None:
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            pass


def is_installed() -> bool:
    """Whether the crash hooks are already in place."""
    return _installed


def reset_for_tests() -> None:
    """Drop the installed flag and the fatal-log stream (test isolation)."""
    global _installed, _fatal_stream
    try:
        faulthandler.disable()
    except Exception:  # noqa: BLE001
        pass
    if _fatal_stream is not None:
        try:
            _fatal_stream.close()
        except Exception:  # noqa: BLE001
            pass
    _fatal_stream = None
    _installed = False
