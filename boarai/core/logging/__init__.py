"""
BoarAi Logging System.

Layers, and who owns each:

``manager``      root-logger wiring: sinks, the writer queue, retention, health
``context``      per-coroutine fields (run/session/task/extension/channel)
``redact``       literal-secret registry + structural masking for every sink
``formatters``   ``SafeFormatter`` (context repair, redaction, byte cap) + JSON
``handlers``     dated file sink, non-formatting queue handler, memory ring
``retention``    age + total-size budget sweep over the dated tree
``crash``        excepthooks and ``faulthandler`` so a crash still leaves a log
``reader``       read-only tail/filter/inventory of the log tree

``core.event`` (events.log), ``core.session.usage_ledger`` (usage.jsonl) and
``core.security.audit`` (security.log) write their own files into the same dated
tree; ``retention`` is the single owner of what gets deleted from it.
"""

from .config import (
    DEFAULT_DATE_FORMAT,
    DEFAULT_LOG_FORMAT,
    DEFAULT_LOGGING_CONFIG,
    LoggingConfig,
    LogLevel,
)
from .context import (
    CONTEXT_FIELDS,
    bind_log_context,
    current_log_context,
    install_record_factory,
    log_context,
    render_tag,
    reset_log_context,
    set_context_enabled,
    set_log_context,
    short_id,
)
from .crash import install_crash_hooks
from .formatters import (
    DEFAULT_MAX_RECORD_BYTES,
    ColoredFormatter,
    CompactFormatter,
    DetailedFormatter,
    JSONFormatter,
    SafeFormatter,
    truncate_line,
)
from .handlers import (
    DatedCategoryHandler,
    LoggerPrefixFilter,
    MemoryLogBuffer,
    NonFormattingQueueHandler,
    dated_path,
    iter_date_dirs,
    today_str,
)
from .manager import (
    LogManager,
    LogManagerError,
    get_log_manager,
    get_logger,
    register_log_secret,
    set_log_manager,
)
from .reader import (
    available_dates,
    filter_lines,
    list_log_files,
    parse_since,
    read_tail,
)
from .redact import (
    clear_secrets,
    redact_text,
    register_secret,
    register_secrets,
    seed_secrets_from_env,
    set_redaction_enabled,
)
from .retention import PRESERVED_FILES, PruneReport, prune_logs_dir, prune_old_date_dirs

__all__ = [
    # Configuration
    "LoggingConfig",
    "LogLevel",
    "DEFAULT_LOGGING_CONFIG",
    "DEFAULT_LOG_FORMAT",
    "DEFAULT_DATE_FORMAT",

    # Context
    "CONTEXT_FIELDS",
    "log_context",
    "bind_log_context",
    "reset_log_context",
    "set_log_context",
    "current_log_context",
    "render_tag",
    "short_id",
    "set_context_enabled",
    "install_record_factory",

    # Redaction
    "register_secret",
    "register_secrets",
    "clear_secrets",
    "redact_text",
    "seed_secrets_from_env",
    "set_redaction_enabled",

    # Formatters
    "SafeFormatter",
    "JSONFormatter",
    "ColoredFormatter",
    "CompactFormatter",
    "DetailedFormatter",
    "truncate_line",
    "DEFAULT_MAX_RECORD_BYTES",

    # Handlers
    "DatedCategoryHandler",
    "NonFormattingQueueHandler",
    "MemoryLogBuffer",
    "LoggerPrefixFilter",
    "today_str",
    "dated_path",
    "iter_date_dirs",

    # Retention
    "prune_logs_dir",
    "prune_old_date_dirs",
    "PruneReport",
    "PRESERVED_FILES",

    # Crash capture
    "install_crash_hooks",

    # Read-only access
    "list_log_files",
    "available_dates",
    "read_tail",
    "filter_lines",
    "parse_since",

    # Manager
    "LogManager",
    "LogManagerError",
    "get_log_manager",
    "set_log_manager",
    "get_logger",
    "register_log_secret",
]
