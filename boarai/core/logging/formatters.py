"""Log formatters for BoarAi system.

``SafeFormatter`` is the wrapper every sink uses. It runs the three things that
must happen to *every* line regardless of which handler emits it:

1. guarantee the context fields exist (records created before the record
   factory was installed would otherwise KeyError inside the logging machinery),
2. redact secrets,
3. cap the line length.

Order matters: redaction runs before truncation, so a capped line can never
keep the leading half of a credential.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, Optional

from .context import ensure_record_fields
from .redact import redact_structure, redact_text

#: Default cap for one rendered line. A single upstream error body can be
#: megabytes, and it would be written to ``all.log``, ``error.log`` and the
#: domain file alike — one 1 MB record costs as much as ten thousand normal
#: lines. Truncation belongs here rather than at each call site because call
#: sites are the thing that forgets.
DEFAULT_MAX_RECORD_BYTES = 8 * 1024

_RESET = "\033[0m"


def truncate_line(text: str, max_bytes: int = DEFAULT_MAX_RECORD_BYTES) -> str:
    """Byte-accurate truncation of *text* to *max_bytes* (UTF-8 safe)."""
    if max_bytes <= 0 or not text:
        return text
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    marker = f"...[truncated {len(encoded) - max_bytes} bytes]"
    budget = max(0, max_bytes - len(marker.encode("utf-8")))
    # errors="ignore" drops a multi-byte character cut in half by the slice.
    head = encoded[:budget].decode("utf-8", errors="ignore")
    out = head + marker
    if "\033[" in head and not out.endswith(_RESET):
        # Cutting a coloured line mid-escape would bleed the colour into every
        # following line of the terminal.
        out += _RESET
    return out


def _windows_vt_available() -> bool:
    """True when the Windows console accepts ANSI escapes."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-12)  # STD_ERROR_HANDLE
        if handle in (0, -1):
            return False
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enabled = ctypes.c_uint32(mode.value | 0x0004)
        return bool(kernel32.SetConsoleMode(handle, enabled))
    except Exception:  # noqa: BLE001 - no console, no colour
        return False


class SafeFormatter(logging.Formatter):
    """Wrap another formatter with context repair, redaction and truncation."""

    def __init__(
        self,
        inner: Optional[logging.Formatter] = None,
        *,
        fmt: Optional[str] = None,
        datefmt: Optional[str] = None,
        max_bytes: int = DEFAULT_MAX_RECORD_BYTES,
        redact: bool = True,
    ):
        if inner is None:
            inner = logging.Formatter(
                fmt=fmt or "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt=datefmt,
            )
        super().__init__(fmt=inner._fmt, datefmt=inner.datefmt)
        self._inner = inner
        self._max_bytes = max_bytes
        self._redact = redact

    def format(self, record: logging.LogRecord) -> str:
        ensure_record_fields(record)
        try:
            text = self._inner.format(record)
        except Exception:  # noqa: BLE001 - a bad record must not kill the sink
            text = f"{record.levelname} {record.name} <unformattable record>"
        if self._redact:
            text = redact_text(text)
        return truncate_line(text, self._max_bytes)


class JSONFormatter(logging.Formatter):
    """JSON log formatter."""

    #: ``LogRecord`` attributes that are not caller-supplied extras.
    _RESERVED = {
        "name", "msg", "args", "created", "relativeCreated", "levelname",
        "levelno", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "msecs", "message", "filename", "module", "pathname", "process",
        "processName", "thread", "threadName", "taskName", "ctx", "ctx_tag",
    }

    def __init__(
        self,
        include_timestamp: bool = True,
        max_bytes: int = DEFAULT_MAX_RECORD_BYTES,
        redact: bool = True,
    ):
        super().__init__()
        self.include_timestamp = include_timestamp
        self.max_bytes = max_bytes
        self.redact = redact

    def _safe(self, value: Any) -> Any:
        if not isinstance(value, str) or not value:
            return value
        out = redact_text(value) if self.redact else value
        return truncate_line(out, self.max_bytes)

    def format(self, record: logging.LogRecord) -> str:
        ensure_record_fields(record)
        log_data: Dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": self._safe(record.getMessage()),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "pid": record.process,
            "thread": record.threadName,
        }

        if self.include_timestamp:
            log_data["timestamp"] = datetime.fromtimestamp(record.created).isoformat(
                timespec="milliseconds"
            )

        ctx = getattr(record, "ctx", None)
        if ctx:
            log_data["ctx"] = dict(ctx)

        if record.exc_info and record.exc_info[0] is not None:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": self._safe(str(record.exc_info[1])),
                "traceback": self._safe(self.formatException(record.exc_info)),
            }

        for key, value in record.__dict__.items():
            if key in self._RESERVED:
                continue
            # ``extra`` is often a nested payload (headers, request metadata).
            # Mask it recursively instead of only masking top-level strings;
            # otherwise a JSON sink silently becomes the redaction bypass.
            log_data[key] = redact_structure(value) if self.redact else value

        def _default(value: Any) -> str:
            # ``default=str`` can call an arbitrary object's __str__; mask that
            # final conversion too, while keeping ordinary JSON scalars intact.
            return redact_text(str(value)) if self.redact else str(value)

        return json.dumps(log_data, ensure_ascii=False, default=_default)


class ColoredFormatter(logging.Formatter):
    """Colored console formatter."""

    # ANSI color codes
    COLORS = {
        "DEBUG": "\033[36m",      # Cyan
        "INFO": "\033[32m",       # Green
        "WARNING": "\033[33m",    # Yellow
        "ERROR": "\033[31m",      # Red
        "CRITICAL": "\033[35m",   # Magenta
    }
    RESET = "\033[0m"

    def __init__(
        self,
        fmt: Optional[str] = None,
        datefmt: Optional[str] = None,
        stream: Any = None,
    ):
        if fmt is None:
            fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        super().__init__(fmt, datefmt)
        self._color = self._detect_color(stream)

    @staticmethod
    def _detect_color(stream: Any) -> bool:
        if os.environ.get("NO_COLOR"):
            return False
        if os.environ.get("BOARAI_LOG_COLOR") == "1":
            return True
        target = stream if stream is not None else sys.stderr
        try:
            is_tty = bool(getattr(target, "isatty", lambda: False)())
        except Exception:  # noqa: BLE001
            is_tty = False
        return is_tty and _windows_vt_available()

    def format(self, record: logging.LogRecord) -> str:
        ensure_record_fields(record)
        if not self._color:
            return super().format(record)

        original_levelname = record.levelname
        color = self.COLORS.get(record.levelname, "")
        if color:
            record.levelname = f"{color}{record.levelname}{self.RESET}"
        try:
            return super().format(record)
        finally:
            record.levelname = original_levelname


class CompactFormatter(logging.Formatter):
    """Compact log formatter."""

    def __init__(self):
        super().__init__(
            fmt="%(asctime)s [%(levelname).1s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )


class DetailedFormatter(logging.Formatter):
    """Detailed log formatter."""

    def __init__(self):
        super().__init__(
            fmt=(
                "%(asctime)s.%(msecs)03d [%(levelname)-8s] "
                "[p%(process)d]%(ctx_tag)s %(name)s:%(lineno)d - %(message)s"
            ),
            datefmt="%Y-%m-%d %H:%M:%S",
        )
