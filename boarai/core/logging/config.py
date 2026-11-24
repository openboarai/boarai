"""Logging configuration for BoarAi system.

Every field here is wired: a knob that exists but does nothing is worse than no
knob, because an operator turns it and concludes the logging system is broken.
The previous ``buffer_size`` / ``flush_interval`` / ``event_*`` fields were
exactly that (the event handler they configured published to an event with no
subscriber, and its 100-record buffer was never flushed), so they are gone.

Layout of the defaults:

``<data_dir>/logs/<YYYY-MM-DD>/``
    ``all.log``        every record that passes ``file_level``
    ``error.log``      WARNING and above, from every domain
    ``<domain>.log``   per-domain routing (see ``LogManager.DEFAULT_LOG_CATEGORIES``)
    ``events.log``     EventBus JSONL audit trail (written by ``core.event``)
    ``usage.jsonl``    token ledger — NOT a log, exempt from log retention
    ``fatal.log``      native crash tracebacks via ``faulthandler``
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List


class LogLevel(str, Enum):
    """Log levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


#: Milliseconds, pid and the context tag are part of the default line: without
#: them a streaming run produces dozens of same-second records that cannot be
#: ordered, and concurrent runs in one process cannot be told apart.
DEFAULT_LOG_FORMAT = (
    "%(asctime)s.%(msecs)03d %(levelname)-5s [p%(process)d]%(ctx_tag)s "
    "%(name)s - %(message)s"
)
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass
class LoggingConfig:
    """Logging configuration."""

    # Log level (root logger — this is the gate every record passes first, so a
    # per-sink level below it can only narrow, never widen).
    level: LogLevel = LogLevel.INFO

    # Log format
    format: str = DEFAULT_LOG_FORMAT
    date_format: str = DEFAULT_DATE_FORMAT

    # Console output
    console_enabled: bool = True
    # INFO by default: the terminal shows the normal INFO stream (init
    # progress, server address, runtime status). Structural noise is
    # demoted to DEBUG at the source, and per-request chatter
    # (aiohttp.access / asyncio) is pinned to WARNING, so INFO stays clean.
    # (Only meaningful when a console exists — the detached server skips
    # the console handler entirely.)
    console_level: LogLevel = LogLevel.INFO

    # File output
    file_enabled: bool = True
    file_path: str = ".boarai/logs/boarai.log"
    file_level: LogLevel = LogLevel.DEBUG
    file_encoding: str = "utf-8"
    # Legacy single-file layout only (``dated_dir_enabled = False``).
    file_max_size: int = 10 * 1024 * 1024  # 10MB
    file_backup_count: int = 5

    # Dated category layout: logs go to <file_path dir>/<YYYY-MM-DD>/
    #   all.log (everything) + error.log (WARNING+) + per-domain files.
    # When False, legacy single-file behavior (file_path + rotation) is used.
    dated_dir_enabled: bool = True
    retention_days: int = 30
    category_max_size: int = 20 * 1024 * 1024  # 20MB per file per day
    category_backup_count: int = 3

    # Whole-tree ceiling for <file_path dir>. Age alone is not a bound: one
    # runaway retry loop can write gigabytes inside the retention window. When
    # the tree exceeds this, the sweep halves the retention window and repeats
    # until it fits (today's directory always survives).
    total_budget_mb: int = 500
    # usage.jsonl shares the dated directory but is a ledger, not a log: the
    # monitoring API aggregates lifetime token totals from it, so it gets its
    # own much longer retention instead of being deleted with the logs.
    ledger_retention_days: int = 365
    # Re-sweep while running. The startup sweep alone never fires on a server
    # that stays up for weeks.
    prune_interval_hours: float = 24.0

    # Per-record cap, applied after redaction. One multi-megabyte upstream
    # error body would otherwise be written to all.log, error.log and the
    # domain file at once.
    max_record_bytes: int = 8 * 1024
    # Mask registered literal secrets and structural credential shapes on every
    # sink. Turning this off is a debugging aid, not a deployment option.
    redact_enabled: bool = True
    # Stamp run/session/task/extension/channel onto every record.
    context_enabled: bool = True
    # In-memory ring of recent rendered records (what a status/diagnostics
    # reader consults; never written to disk).
    recent_buffer_size: int = 1000

    # JSON output (dated layout, like every other sink)
    json_enabled: bool = False
    json_path: str = ".boarai/logs/boarai.jsonl"
    json_level: LogLevel = LogLevel.DEBUG
    json_max_size: int = 20 * 1024 * 1024  # 20MB per day
    json_backup_count: int = 3

    # Route file sinks through a queue + single listener thread, so emit never
    # performs disk I/O on the event loop. Disable only to debug the sink
    # itself (a synchronous sink makes a failing write immediately visible).
    async_enabled: bool = True

    # Enable faulthandler into <log dir>/fatal.log so a native crash (segfault,
    # abort) still leaves Python-level tracebacks on disk.
    faulthandler_enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "level": self.level.value,
            "format": self.format,
            "date_format": self.date_format,
            "console_enabled": self.console_enabled,
            "console_level": self.console_level.value,
            "file_enabled": self.file_enabled,
            "file_path": self.file_path,
            "file_level": self.file_level.value,
            "file_encoding": self.file_encoding,
            "file_max_size": self.file_max_size,
            "file_backup_count": self.file_backup_count,
            "dated_dir_enabled": self.dated_dir_enabled,
            "retention_days": self.retention_days,
            "category_max_size": self.category_max_size,
            "category_backup_count": self.category_backup_count,
            "total_budget_mb": self.total_budget_mb,
            "ledger_retention_days": self.ledger_retention_days,
            "prune_interval_hours": self.prune_interval_hours,
            "max_record_bytes": self.max_record_bytes,
            "redact_enabled": self.redact_enabled,
            "context_enabled": self.context_enabled,
            "recent_buffer_size": self.recent_buffer_size,
            "json_enabled": self.json_enabled,
            "json_path": self.json_path,
            "json_level": self.json_level.value,
            "json_max_size": self.json_max_size,
            "json_backup_count": self.json_backup_count,
            "async_enabled": self.async_enabled,
            "faulthandler_enabled": self.faulthandler_enabled,
        }

    #: Keys whose value is a LogLevel rather than a plain scalar.
    _ENUM_KEYS = ("level", "console_level", "file_level", "json_level")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LoggingConfig":
        """Create from dictionary (unknown keys are ignored)."""
        config = cls()
        if not isinstance(data, dict):
            return config

        for key, value in data.items():
            if not hasattr(config, key) or key.startswith("_"):
                continue
            try:
                if key in cls._ENUM_KEYS:
                    setattr(config, key, LogLevel(str(value).upper()))
                elif isinstance(getattr(config, key), bool):
                    setattr(config, key, bool(value))
                elif isinstance(getattr(config, key), int):
                    setattr(config, key, int(value))
                elif isinstance(getattr(config, key), float):
                    setattr(config, key, float(value))
                else:
                    setattr(config, key, value)
            except (TypeError, ValueError):
                # One bad value keeps the rest of the section usable; the
                # caller's validate() reports it.
                continue
        return config

    def validate(self) -> List[str]:
        """Validate configuration."""
        errors = []

        if self.file_max_size <= 0:
            errors.append("file_max_size must be positive")
        if self.file_backup_count < 0:
            errors.append("file_backup_count cannot be negative")
        if self.retention_days < 0:
            errors.append("retention_days cannot be negative")
        if self.ledger_retention_days < 0:
            errors.append("ledger_retention_days cannot be negative")
        if self.category_max_size <= 0:
            errors.append("category_max_size must be positive")
        if self.category_backup_count < 0:
            errors.append("category_backup_count cannot be negative")
        if self.total_budget_mb < 0:
            errors.append("total_budget_mb cannot be negative")
        if self.prune_interval_hours <= 0:
            errors.append("prune_interval_hours must be positive")
        if self.max_record_bytes < 256:
            errors.append("max_record_bytes must be at least 256")
        # 0 is a supported value: it disables the in-memory ring. Only a
        # negative count is nonsense.
        if self.recent_buffer_size < 0:
            errors.append("recent_buffer_size cannot be negative")
        if self.json_max_size <= 0:
            errors.append("json_max_size must be positive")
        if self.json_backup_count < 0:
            errors.append("json_backup_count cannot be negative")

        return errors


# Default logging configuration
DEFAULT_LOGGING_CONFIG = LoggingConfig()
