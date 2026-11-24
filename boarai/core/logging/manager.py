"""Logging manager for BoarAi system.

Wiring overview (one root logger, several sinks, one writer thread):

    logger.*  ──►  root logger  ──►  QueueHandler ──► SimpleQueue ──┐
                        │                                          │
                        └──► console handler (direct)              ▼
                                                    QueueListener thread
                                                           │
                            ┌──────────────┬───────────────┼──────────────┐
                            ▼              ▼               ▼              ▼
                        all.log       error.log      <domain>.log    memory ring
                        (+ events.log / usage.jsonl / fatal.log are written by
                         their own owners into the same dated directory)

File sinks go through the queue because ``emit`` on the event loop is a
synchronous write plus flush, and a record that matches three sinks costs three
of them — during a streaming run that is loop jitter the whole system can feel.
The console handler stays direct so terminal ordering matches the process's own
``print`` output.

Every sink's formatter is a :class:`SafeFormatter`, so redaction and the
per-record byte cap apply uniformly, including to tracebacks (which is where an
upstream URL with an embedded token would otherwise land).
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import logging.handlers
import os
import queue
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import LoggingConfig, LogLevel
from .context import set_context_enabled
from .crash import close_faulthandler, install_crash_hooks
from .formatters import (
    DEFAULT_MAX_RECORD_BYTES,
    ColoredFormatter,
    JSONFormatter,
    SafeFormatter,
)
from .handlers import (
    DatedCategoryHandler,
    LoggerPrefixFilter,
    MemoryLogBuffer,
    NonFormattingQueueHandler,
    today_str,
)
from .redact import (
    redact_structure,
    register_secret,
    seed_secrets_from_env,
    set_redaction_enabled,
)
from .retention import PruneReport, prune_logs_dir

_AT_EXIT_REGISTERED = False


def _resolve_log_path(
    path: str,
    project_root: Optional[Path] = None,
    warnings: Optional[List[str]] = None,
) -> str:
    """Resolve log path to absolute path."""
    try:
        from boarai.core.runtime import resolve_data_path

        p = resolve_data_path(path)
    except Exception:  # noqa: BLE001 - a broken runtime import must not kill logging
        p = Path(path)
    if p.is_absolute():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if os.access(p.parent, os.W_OK):
                return str(p)
        except OSError:
            pass
        fallback = _fallback_log_path(p, project_root)
        message = f"Log path {p} is not writable; falling back to {fallback}"
        if warnings is not None:
            warnings.append(message)
        else:
            logging.getLogger(__name__).warning(message)
        return str(fallback)

    root = project_root
    if root is None:
        try:
            from boarai.core.runtime import resolve_project_root

            root = resolve_project_root()
        except Exception:  # noqa: BLE001
            root = Path.cwd()
    return str(Path(root) / path)


def _fallback_log_path(original: Path, project_root: Optional[Path]) -> Path:
    """Data-dir fallback path for an unwritable log file."""
    try:
        from boarai.core.runtime import get_data_dir

        fallback = get_data_dir() / "logs" / (original.name or "boarai.log")
        fallback.parent.mkdir(parents=True, exist_ok=True)
        return fallback
    except Exception:  # noqa: BLE001
        # Last resort when core.runtime itself is unimportable: the home
        # anchor (stdlib-only, cannot fail) — never the source tree.
        fallback = Path.home() / ".boarai" / "logs" / (original.name or "boarai.log")
        try:
            fallback.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return fallback


class LogManager:
    """Central logging manager."""

    # Per-domain routing for the dated layout. Every record also lands in
    # all.log (fallback — nothing is ever lost), and WARNING+ additionally
    # lands in error.log (single place to look for problems). Prefixes are
    # matched as exact logger names or dotted parents.
    # Both spellings are live: hardcoded getLogger("core.…") names kept
    # their historical prefix, while __name__-based loggers now report
    # "boarai.core.…" after the package rename (R1, 2026-11-18).
    DEFAULT_LOG_CATEGORIES = {
        "session.log": ("core.session", "boarai.core.session"),
        "llm.log": ("core.llm", "boarai.core.llm"),
        "extension.log": ("core.extension", "boarai.core.extension", "boar.extensions", "extensions"),
        "server.log": ("core.server", "boarai.core.server", "aiohttp"),
    }

    #: Written by faulthandler, not by a logging handler.
    FATAL_LOG_NAME = "fatal.log"

    #: Backpressure bound for the in-process log queue. A stalled disk must not
    #: turn into unbounded heap growth; overflow is counted by the queue handler.
    QUEUE_CAPACITY = 4096

    def __init__(
        self,
        config: Optional[LoggingConfig] = None,
        event_bus: Any = None,
        project_root: Optional[Path] = None,
    ):
        """Initialize logging manager."""
        # Never mutate the module-level DEFAULT_LOGGING_CONFIG: _apply_paths
        # resolves paths in place, and sharing it would leak one manager's data
        # directory into the next manager (tests and multi-anchor embedders).
        self._config = config if config is not None else LoggingConfig()
        self._handlers: List[logging.Handler] = []
        self._initialized = False
        self._project_root = project_root
        self._pending_warnings: List[str] = []
        self._degraded: List[str] = []
        self._queue: Optional["queue.Queue[Any]"] = None
        self._queue_handler: Optional[logging.Handler] = None
        self._listener: Optional[logging.handlers.QueueListener] = None
        self._memory: Optional[MemoryLogBuffer] = None
        self._prune_task: Optional["asyncio.Task[None]"] = None
        self._last_prune: Optional[PruneReport] = None

        self._pending_info: List[str] = []
        set_context_enabled(bool(self._config.context_enabled))
        set_redaction_enabled(bool(self._config.redact_enabled))
        # Seed before the first possible log line, not after the sinks are up:
        # an early path/degradation warning may itself contain an env credential.
        seed_secrets_from_env()
        self._apply_paths()
        self._setup_logging()

    # ------------------------------------------------------------------ paths

    def _apply_paths(self) -> None:
        """Resolve the configured log paths against the data-dir anchor."""
        self._config.file_path = _resolve_log_path(
            self._config.file_path, self._project_root, self._pending_warnings
        )
        self._config.json_path = _resolve_log_path(
            self._config.json_path, self._project_root, self._pending_warnings
        )

    @property
    def log_dir(self) -> Path:
        """The directory holding the dated log subtrees."""
        return Path(self._config.file_path).parent

    @property
    def config(self) -> LoggingConfig:
        """The active configuration (read-only use)."""
        return self._config

    # ------------------------------------------------------------------ setup

    def _setup_logging(self) -> None:
        """Setup logging system."""
        if self._initialized:
            return

        cfg = self._config
        root_logger = logging.getLogger()
        root_logger.setLevel(self._get_log_level(cfg.level))

        # Noise suppression — per-request HTTP access logs (the WebUI polls
        # every few seconds) and asyncio's poll traces add nothing. Keep them
        # at WARNING unless debug mode is on.
        for noisy in ("aiohttp.access", "asyncio"):
            if cfg.level != LogLevel.DEBUG:
                logging.getLogger(noisy).setLevel(logging.WARNING)

        # Clear existing handlers. This is deliberate — the manager owns the
        # root logger — but it is now recorded, because it also drops a handler
        # another component installed before us and that used to be invisible.
        stale = list(root_logger.handlers)
        if stale:
            logging.getLogger(__name__).debug(
                "Replacing %d pre-existing root handler(s): %s",
                len(stale), [type(h).__name__ for h in stale],
            )
        root_logger.handlers.clear()

        # Sinks that write through the queue (everything but the console).
        queued: List[logging.Handler] = []

        # Console first: it is the only sink that survives a broken data dir,
        # and setup failures below must be visible somewhere.
        if cfg.console_enabled and sys.stderr is not None:
            self._setup_console_handler()

        if cfg.file_enabled:
            if cfg.dated_dir_enabled:
                self._setup_dated_handlers(queued)
            else:
                self._setup_legacy_file_handler(queued)

        if cfg.json_enabled:
            self._setup_json_handler(queued)

        if cfg.recent_buffer_size > 0:
            # The ring follows the root level: an operator who turned on DEBUG
            # expects to see DEBUG in "recent" too, and a fixed INFO floor would
            # silently disagree with the rest of the system.
            self._memory = MemoryLogBuffer(
                level=self._get_log_level(cfg.level),
                capacity=cfg.recent_buffer_size,
            )
            self._memory.setFormatter(self._wrap(logging.Formatter("%(name)s - %(message)s")))
            queued.append(self._memory)
            self._handlers.append(self._memory)

        self._attach(queued)

        # Secrets that reach a log line only through a lazy %s argument are
        # redacted on the rendered string, but a record whose args hold the
        # value is masked here too so a handler that formats differently (or a
        # third-party one) cannot leak it.
        logging.setLogRecordFactory(_redacting_record_factory(logging.getLogRecordFactory()))

        self._initialized = True

        # Report degradations now that sinks exist (they could not be reported
        # while the paths were still being resolved, and the retention sweep
        # runs before the file handlers are attached).
        for message in self._pending_warnings:
            logging.getLogger(__name__).warning("%s", message)
        self._degraded.extend(self._pending_warnings)
        self._pending_warnings.clear()
        for message in self._pending_info:
            logging.getLogger(__name__).info("%s", message)
        self._pending_info.clear()

        if cfg.faulthandler_enabled and cfg.file_enabled:
            install_crash_hooks(self.log_dir / today_str() / self.FATAL_LOG_NAME)

        self._maybe_start_prune_task()

    def _wrap(self, formatter: logging.Formatter) -> SafeFormatter:
        """Apply redaction + the per-record byte cap to *formatter*."""
        return SafeFormatter(
            formatter,
            max_bytes=self._config.max_record_bytes or DEFAULT_MAX_RECORD_BYTES,
            redact=bool(self._config.redact_enabled),
        )

    def _text_formatter(self) -> SafeFormatter:
        return self._wrap(logging.Formatter(fmt=self._config.format, datefmt=self._config.date_format))

    def _setup_console_handler(self) -> None:
        """Setup console handler (attached directly, not through the queue)."""
        handler = logging.StreamHandler()
        handler.setLevel(self._get_log_level(self._config.console_level))
        handler.setFormatter(self._wrap(ColoredFormatter(
            fmt=self._config.format,
            datefmt=self._config.date_format,
            stream=sys.stderr,
        )))
        logging.getLogger().addHandler(handler)
        self._handlers.append(handler)

    def _setup_dated_handlers(self, sinks: List[logging.Handler]) -> None:
        """Setup the dated category layout under the file_path directory."""
        base_dir = self.log_dir
        self._run_prune()

        file_level = self._get_log_level(self._config.file_level)

        def _make(filename: str, level: int, prefixes=None) -> None:
            handler = DatedCategoryHandler(
                base_dir=base_dir,
                filename=filename,
                level=level,
                max_size=self._config.category_max_size,
                backup_count=self._config.category_backup_count,
                encoding=self._config.file_encoding,
            )
            handler.setFormatter(self._text_formatter())
            if prefixes:
                handler.addFilter(LoggerPrefixFilter(prefixes))
            sinks.append(handler)
            self._handlers.append(handler)

        # Fallback first: every record lands here regardless of category.
        _make("all.log", file_level)
        # Single place to look for problems: WARNING+ from every domain.
        _make("error.log", logging.WARNING)
        # Domain routing.
        for filename, prefixes in self.DEFAULT_LOG_CATEGORIES.items():
            _make(filename, file_level, prefixes)

    def _setup_legacy_file_handler(self, sinks: List[logging.Handler]) -> None:
        """Setup the pre-dated single rotating file layout."""
        log_path = Path(self._config.file_path)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._degraded.append(f"Cannot create {log_path.parent}: {exc}")
            return

        handler = logging.handlers.RotatingFileHandler(
            filename=self._config.file_path,
            maxBytes=self._config.file_max_size,
            backupCount=self._config.file_backup_count,
            encoding=self._config.file_encoding,
        )
        handler.setLevel(self._get_log_level(self._config.file_level))
        handler.setFormatter(self._text_formatter())
        sinks.append(handler)
        self._handlers.append(handler)

    def _setup_json_handler(self, sinks: List[logging.Handler]) -> None:
        """Setup the JSON sink in the same dated layout as every other file."""
        filename = Path(self._config.json_path).name or "boarai.jsonl"
        base_dir = self.log_dir if self._config.dated_dir_enabled else Path(self._config.json_path).parent

        if self._config.dated_dir_enabled:
            handler: logging.Handler = DatedCategoryHandler(
                base_dir=base_dir,
                filename=filename,
                level=self._get_log_level(self._config.json_level),
                max_size=self._config.json_max_size,
                backup_count=self._config.json_backup_count,
                encoding=self._config.file_encoding,
            )
        else:
            try:
                base_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._degraded.append(f"Cannot create {base_dir}: {exc}")
                return
            handler = logging.handlers.RotatingFileHandler(
                filename=str(base_dir / filename),
                maxBytes=self._config.json_max_size,
                backupCount=self._config.json_backup_count,
                encoding=self._config.file_encoding,
            )
            handler.setLevel(self._get_log_level(self._config.json_level))

        # JSONFormatter redacts and caps its own big fields: truncating the
        # serialised line instead would emit invalid JSON.
        handler.setFormatter(JSONFormatter(max_bytes=self._config.max_record_bytes))
        sinks.append(handler)
        self._handlers.append(handler)

    # ------------------------------------------------------------------ queue

    def _attach(self, sinks: List[logging.Handler]) -> None:
        """Publish *sinks* to the root logger, through the queue when enabled."""
        if not sinks:
            return
        if not self._config.async_enabled:
            for handler in sinks:
                logging.getLogger().addHandler(handler)
            return

        if self._queue is None:
            # Bounded and non-blocking: a stalled disk cannot block the event
            # loop, and cannot grow the heap without limit either. Overflow is
            # dropped by NonFormattingQueueHandler and surfaced in get_stats().
            self._queue = queue.Queue(maxsize=self.QUEUE_CAPACITY)
            self._queue_handler = NonFormattingQueueHandler(self._queue)
            logging.getLogger().addHandler(self._queue_handler)

        self._stop_listener()
        self._listener = logging.handlers.QueueListener(
            self._queue, *sinks, respect_handler_level=True
        )
        self._listener.start()
        self._register_at_exit()

    def _register_at_exit(self) -> None:
        """Drain the queue at interpreter exit, before ``logging.shutdown``."""
        global _AT_EXIT_REGISTERED
        if _AT_EXIT_REGISTERED:
            return
        atexit.register(_drain_at_exit)
        _AT_EXIT_REGISTERED = True

    def _stop_listener(self) -> None:
        """Stop the writer thread, draining everything already enqueued."""
        listener, self._listener = self._listener, None
        if listener is None:
            return
        try:
            listener.stop()
        except Exception:  # noqa: BLE001 - teardown must not raise
            pass

    # ------------------------------------------------------------ maintenance

    def _run_prune(self) -> PruneReport:
        """One retention sweep (age + total-size budget). Never raises."""
        try:
            report = prune_logs_dir(
                self.log_dir,
                retention_days=self._config.retention_days,
                total_budget_bytes=self._config.total_budget_mb * 1024 * 1024,
                ledger_retention_days=self._config.ledger_retention_days,
            )
        except Exception as exc:  # noqa: BLE001 - retention must never break startup
            self._degraded.append(f"retention sweep failed: {exc}")
            return PruneReport()
        self._last_prune = report
        if report.removed_anything:
            message = f"Log retention: {report.summary()}"
            if self._initialized:
                logging.getLogger(__name__).info("%s", message)
            else:
                # The startup sweep runs while the file sinks are still being
                # built; logging now would be dropped on the floor.
                self._pending_info.append(message)
        return report

    def prune_now(self) -> PruneReport:
        """Run a retention sweep immediately (public: CLI, diagnostics)."""
        return self._run_prune()

    def start_maintenance(self) -> bool:
        """Start the periodic retention task. Returns False without a loop."""
        if self._prune_task is not None and not self._prune_task.done():
            return True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        interval = max(60.0, float(self._config.prune_interval_hours) * 3600.0)
        self._prune_task = loop.create_task(self._prune_loop(interval), name="log-retention")
        return True

    def _maybe_start_prune_task(self) -> None:
        """Start the periodic sweep when a loop happens to be running."""
        try:
            self.start_maintenance()
        except Exception:  # noqa: BLE001
            pass

    async def _prune_loop(self, interval: float) -> None:
        while True:
            try:
                await asyncio.sleep(interval)
                await asyncio.to_thread(self._run_prune)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a failed sweep retries next tick
                logging.getLogger(__name__).debug("Periodic log retention failed: %s", exc)

    def _stop_maintenance(self) -> None:
        task, self._prune_task = self._prune_task, None
        if task is not None and not task.done():
            task.cancel()

    # ------------------------------------------------------------------ level

    def _get_log_level(self, level: LogLevel) -> int:
        """Convert LogLevel to logging level."""
        level_map = {
            LogLevel.DEBUG: logging.DEBUG,
            LogLevel.INFO: logging.INFO,
            LogLevel.WARNING: logging.WARNING,
            LogLevel.ERROR: logging.ERROR,
            LogLevel.CRITICAL: logging.CRITICAL,
        }
        if isinstance(level, int):
            return level
        return level_map.get(level, logging.INFO)

    def get_logger(self, name: str) -> logging.Logger:
        """Get or create a logger."""
        return logging.getLogger(name)

    def set_level(self, level: LogLevel, logger_name: Optional[str] = None) -> None:
        """Set log level (root when *logger_name* is None)."""
        log_level = self._get_log_level(level)
        if logger_name:
            logging.getLogger(logger_name).setLevel(log_level)
        else:
            logging.getLogger().setLevel(log_level)

    def add_handler(self, handler: logging.Handler, logger_name: Optional[str] = None) -> None:
        """Add a handler to a logger (root when *logger_name* is None)."""
        if logger_name:
            logging.getLogger(logger_name).addHandler(handler)
        else:
            logging.getLogger().addHandler(handler)
        self._handlers.append(handler)

    def remove_handler(self, handler: logging.Handler, logger_name: Optional[str] = None) -> None:
        """Remove a handler from a logger (root when *logger_name* is None)."""
        if logger_name:
            logging.getLogger(logger_name).removeHandler(handler)
        else:
            logging.getLogger().removeHandler(handler)
        if handler in self._handlers:
            self._handlers.remove(handler)

    # ----------------------------------------------------------------- config

    def update_config(self, config: LoggingConfig) -> None:
        """Update logging configuration."""
        self._teardown()
        self._config = config
        self._pending_info = []
        self._apply_paths()
        set_context_enabled(bool(config.context_enabled))
        set_redaction_enabled(bool(config.redact_enabled))
        self._setup_logging()

    def _teardown(self) -> None:
        """Detach and close every handler this manager installed."""
        self._stop_maintenance()
        self._stop_listener()
        # faulthandler is not a logging.Handler, so closing the handler list
        # alone would leave fatal.log open (and locked on Windows).
        close_faulthandler()
        root_logger = logging.getLogger()
        for handler in list(self._handlers):
            if handler in root_logger.handlers:
                root_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # noqa: BLE001
                pass
        if self._queue_handler is not None and self._queue_handler in root_logger.handlers:
            root_logger.removeHandler(self._queue_handler)
        # Drop the queue too: leaving it attached to a handler nobody reads
        # would make every later record pile up in an orphan queue forever.
        self._queue = None
        self._queue_handler = None
        self._handlers.clear()
        self._memory = None
        self._initialized = False

    # ------------------------------------------------------------------- read

    def recent(
        self,
        limit: int = 200,
        level: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Recent records from the in-memory ring (newest last)."""
        if self._memory is None:
            return []
        return self._memory.snapshot(limit=limit, level=level, search=search)

    def get_stats(self) -> Dict[str, Any]:
        """Get logging statistics and health."""
        loggers = logging.Logger.manager.loggerDict
        try:
            queued = self._queue.qsize() if self._queue is not None else 0
        except (AttributeError, NotImplementedError):
            # qsize is not implemented on every platform (notably macOS).
            queued = 0
        return {
            "loggers": len(loggers),
            "handlers": len(self._handlers),
            "async": bool(self._config.async_enabled and self._listener is not None),
            "queued": queued,
            "queue_dropped": getattr(self._queue_handler, "dropped", 0),
            "recent": len(self._memory) if self._memory else 0,
            "recent_dropped": self._memory.dropped if self._memory else 0,
            "log_dir": str(self.log_dir),
            "degraded": list(self._degraded),
            "last_prune": self._last_prune.summary() if self._last_prune else None,
            "config": self._config.to_dict(),
        }

    # -------------------------------------------------------------- lifecycle

    def flush(self) -> None:
        """Block until every enqueued record has been written."""
        listener = self._listener
        if listener is None:
            for handler in self._handlers:
                try:
                    handler.flush()
                except Exception:  # noqa: BLE001
                    pass
            return
        # stop() drains the queue and joins the writer thread; start() resumes.
        try:
            listener.stop()
            listener.start()
        except Exception:  # noqa: BLE001
            pass

    def drain(self, timeout: float = 1.0) -> None:
        """Time-bounded drain for hard-exit paths (signal, watchdog, restart)."""
        listener = self._listener
        if listener is None:
            return
        try:
            worker = threading.Thread(
                target=self._stop_listener, name="boarai-log-drain", daemon=True
            )
            worker.start()
            worker.join(timeout)
        except Exception:  # noqa: BLE001
            pass

    async def flush_all(self) -> None:
        """Flush every handler (async façade kept for existing callers)."""
        await asyncio.to_thread(self.flush)

    def _close_sync(self) -> None:
        """Drain with a deadline before detaching sinks."""
        self.drain(timeout=2.0)
        self._teardown()

    async def close(self) -> None:
        """Close all handlers, stop the writer thread and the retention task."""
        await asyncio.to_thread(self._close_sync)

    def shutdown(self) -> None:
        """Synchronous close for non-async teardown paths (``atexit``)."""
        self._close_sync()


def _drain_at_exit() -> None:
    """Flush the writer thread before ``logging.shutdown`` closes the handlers."""
    manager = _log_manager
    if manager is None:
        return
    try:
        manager.drain(timeout=2.0)
    except Exception:  # noqa: BLE001
        pass


def _redacting_record_factory(previous):
    """Wrap a record factory so lazy ``%s`` args are masked at creation time."""
    marker = "_boarai_log_redacting"
    if getattr(previous, marker, False):
        return previous

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        try:
            if record.args:
                # Preserve the container type. In particular, a mapping is the
                # supported form for ``%(key)s`` formatting; wrapping it in a
                # tuple makes ``record.getMessage()`` raise TypeError.
                record.args = redact_structure(record.args)
            if isinstance(record.msg, str):
                record.msg = redact_structure(record.msg)
        except Exception:  # noqa: BLE001 - redaction must never break a log call
            pass
        return record

    setattr(factory, marker, True)
    return factory


# Global logging manager instance
_log_manager: Optional[LogManager] = None


def get_log_manager(project_root: Optional[Path] = None) -> LogManager:
    """Get the global logging manager instance."""
    global _log_manager
    if _log_manager is None:
        _log_manager = LogManager(project_root=project_root)
    return _log_manager


def set_log_manager(manager: Optional[LogManager]) -> None:
    """Replace the global manager (tests pass None to force a rebuild)."""
    global _log_manager
    _log_manager = manager


def get_logger(name: str) -> logging.Logger:
    """Get a logger by name."""
    return logging.getLogger(name)


def register_log_secret(value: Optional[str]) -> bool:
    """Register a literal secret so it is masked on every sink."""
    return register_secret(value)


class LogManagerError(Exception):
    """Logging manager related errors."""
    pass
