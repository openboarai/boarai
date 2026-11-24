"""Per-coroutine log context.

A log line without context is only half a diagnostic: ``all.log`` mixes the
server, the detached update worker and every concurrent run, and the record
carries no clue about which one produced it. Callers used to hand-write ids
into messages (``"Run %s failed"``), which is unsearchable and easy to forget.

This module binds a small field set to the *current asyncio context* and
injects it into every ``LogRecord`` through a record factory:

    with log_context(run=short_id(run_id), sess=short_id(session_id)):
        ...                      # every record in here carries the tag

``contextvars`` (not thread-locals) is the right primitive for an asyncio
runtime: ``Task`` creation and ``asyncio.to_thread`` both copy the current
context, so background work spawned inside a run inherits its tag without any
plumbing, and two concurrent runs never see each other's ids.

The factory adds ``record.ctx`` (dict) and ``record.ctx_tag`` (pre-rendered
``" [run=… sess=…]"`` or ``""``). Rendering in the factory rather than in the
formatter means a format string can reference ``%(ctx_tag)s`` on EVERY record
in the process — including third-party ones — without a KeyError.
"""

from __future__ import annotations

import contextvars
import logging
import re
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Mapping, Optional

CONTEXT_FIELDS = ("run", "sess", "task", "ext", "chan")
"""Field names a caller may bind, in the order they render into ``ctx_tag``.

Kept deliberately short: the tag is on every line, so each character costs
disk on every record. Full ids are logged once at the boundary that owns them
(``run.start`` logs the complete run/session id).
"""

MAX_FIELD_LEN = 40
"""Hard cap per field value, so a bound value cannot blow up the line budget."""

_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_.:@-]")

_ctx_var: "contextvars.ContextVar[Optional[Mapping[str, str]]]" = contextvars.ContextVar(
    "boarai_log_context", default=None
)


def _clean(value: Any) -> str:
    """Render *value* as a tag-safe string (no spaces, brackets or newlines)."""
    text = value if isinstance(value, str) else str(value)
    text = _UNSAFE_RE.sub("_", text)
    return text[:MAX_FIELD_LEN]


def short_id(value: Any, length: int = 8) -> str:
    """First *length* chars of an id — enough to correlate, cheap to print."""
    text = value if isinstance(value, str) else str(value or "")
    return text[:length]


def current_log_context() -> Dict[str, str]:
    """Copy of the fields bound to the current context ({} when none)."""
    ctx = _ctx_var.get()
    return dict(ctx) if ctx else {}


def set_log_context(fields: Optional[Mapping[str, Any]]) -> contextvars.Token:
    """Replace the current context; returns the token for :func:`reset_log_context`."""
    if not fields:
        return _ctx_var.set(None)
    return _ctx_var.set({k: _clean(v) for k, v in fields.items() if k in CONTEXT_FIELDS and v})


def reset_log_context(token: contextvars.Token) -> None:
    """Restore the context captured by *token* (never raises)."""
    try:
        _ctx_var.reset(token)
    except (ValueError, LookupError):
        # Token from another context (cross-task reset) — nothing to restore.
        pass


def bind_log_context(**fields: Any) -> contextvars.Token:
    """Merge *fields* over the enclosing context; returns a reset token."""
    merged = current_log_context()
    merged.update({k: _clean(v) for k, v in fields.items() if k in CONTEXT_FIELDS and v})
    return _ctx_var.set(merged or None)


@contextmanager
def log_context(**fields: Any) -> Iterator[Dict[str, str]]:
    """Bind *fields* for the duration of the ``with`` block."""
    token = bind_log_context(**fields)
    try:
        yield current_log_context()
    finally:
        reset_log_context(token)


def render_tag(ctx: Optional[Mapping[str, str]] = None) -> str:
    """Render the context as ``" [run=… sess=…]"`` (``""`` when empty/off)."""
    if not _enabled:
        return ""
    fields = ctx if ctx is not None else _ctx_var.get()
    if not fields:
        return ""
    parts = [f"{name}={fields[name]}" for name in CONTEXT_FIELDS if fields.get(name)]
    return f" [{' '.join(parts)}]" if parts else ""


_enabled = True


def set_context_enabled(enabled: bool) -> None:
    """Turn context stamping on or off (``logging.context_enabled`` config)."""
    global _enabled
    _enabled = bool(enabled)


def context_enabled() -> bool:
    """Whether context fields are currently stamped onto records."""
    return _enabled


_FACTORY_MARKER = "_boarai_log_context_factory"


def install_record_factory() -> None:
    """Chain a factory that stamps ``ctx``/``ctx_tag`` onto every record."""
    current = logging.getLogRecordFactory()
    if getattr(current, _FACTORY_MARKER, False):
        return

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = current(*args, **kwargs)
        ctx = _ctx_var.get() if _enabled else None
        record.ctx = dict(ctx) if ctx else {}
        record.ctx_tag = render_tag(ctx)
        return record

    setattr(factory, _FACTORY_MARKER, True)
    logging.setLogRecordFactory(factory)


def ensure_record_fields(record: logging.LogRecord) -> logging.LogRecord:
    """Guarantee the context attributes exist on *record*."""
    if not hasattr(record, "ctx_tag"):
        record.ctx_tag = render_tag()
    if not hasattr(record, "ctx"):
        record.ctx = current_log_context() if _enabled else {}
    return record


# Install on import: ``boarai.core.logging`` is imported by the runtime before
# any handler is configured, so every record in the process carries the fields.
install_record_factory()
