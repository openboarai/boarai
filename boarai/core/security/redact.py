"""Secret redaction for security audit records.

Approval / smart-approval audit entries persist raw command strings and tool
arguments into ``.boarai/logs/security.log``. Those payloads routinely carry
credentials (``curl -H "Authorization: Bearer ..."``、``git clone
https://user:token@host``、``pip install --index-url https://user:key@...``).
This module masks the common shapes BEFORE anything is written, so the audit
trail stays useful (structure preserved) without becoming a credential store.

Scope: audit-side only. The live WebUI approval prompt still shows the raw
command — the user must see exactly what they are approving (hermes redacts
only for remote gateway display, same split).
"""
from __future__ import annotations

import re
from typing import Any

#: Recursion cap for :func:`redact_deep`. Deep enough for any real LLM payload
#: (message -> content blocks -> tool arguments), shallow enough that a
#: self-referential structure degrades instead of raising RecursionError.
_MAX_REDACT_DEPTH = 32

# (pattern, replacement) — order matters: specific token shapes first.
_PATTERNS = [    # Bearer / basic auth headers
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/-]+=*"), r"\1 [REDACTED]"),
    # key=value / key: value secrets (json, query strings, env exports)
    (
        re.compile(
            r"(?i)\b(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|refresh[_-]?token"
            r"|secret|client[_-]?secret|password|passwd|pwd|private[_-]?key)"
            r"(\s*[\"']?\s*[:=]\s*[\"']?)([^\s\"',&;]+)"
        ),
        r"\1\2[REDACTED]",
    ),
    # user:password@host (git clone / curl / pip index URLs)
    (re.compile(r"([a-z][a-z0-9+.-]*://[^/\s:@\"']+):([^@\s\"']+)@"), r"\1:[REDACTED]@"),
    # Well-known token prefixes
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"), "[REDACTED]"),          # OpenAI-style
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED]"),      # GitHub
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}"), "[REDACTED]"),     # Slack
    (re.compile(r"\bAKIA[0-9A-Z]{12,}"), "[REDACTED]"),               # AWS access key
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"), "[REDACTED]"),          # Google API key
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"), "[REDACTED]"),  # JWT
]


def redact_secrets(text: Any) -> Any:
    """Mask credential-shaped substrings in ``text`` (non-str passes through)."""
    if not isinstance(text, str) or not text:
        return text
    out = text
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out


def redact_deep(value: Any, *, _depth: int = 0) -> Any:
    """Recursively redact every string inside nested dicts and lists."""
    if _depth > _MAX_REDACT_DEPTH:
        return "[REDACTED: too deep]"
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {
            (redact_secrets(k) if isinstance(k, str) else k):
            redact_deep(v, _depth=_depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        redacted = [redact_deep(v, _depth=_depth + 1) for v in value]
        return redacted if isinstance(value, list) else tuple(redacted)
    return value
