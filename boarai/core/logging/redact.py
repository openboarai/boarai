"""Secret redaction for every log sink.

Logs are the one artifact that outlives a run and leaves the machine (a user
attaches them to a bug report), so credentials must never reach them. Two
complementary passes run on the *formatted* line, which is the only place that
sees everything at once — message, lazy ``%s`` args, and the traceback:

1. **Literal secrets** registered at runtime (the WebUI access key, every
   configured provider API key, sensitive environment values). Pattern matching
   cannot catch an opaque key that carries no recognisable shape, so the actual
   value is substituted.
2. **Structural patterns** delegated to :mod:`boarai.core.security.redact`
   (``Bearer …``, ``api_key=…``, ``scheme://user:pass@host``, ``sk-…``, JWT …),
   which already guards the security audit trail. Redaction rules therefore stay
   in ONE module instead of drifting apart per sink.

Redaction runs before truncation: a truncated line must never keep the prefix
of a secret (pi does the same, and for the same reason).
"""

from __future__ import annotations

import os
import re
import threading
from typing import Any, Callable, Iterable, List, Optional

#: Values shorter than this are not registered: substituting a 4-char literal
#: would mangle ordinary prose and produce useless output.
MIN_SECRET_LEN = 8

#: Registry cap. Provider keys plus the access key are a handful; the bound
#: keeps a pathological caller (registering per-request tokens) from growing
#: the substitution loop without limit.
MAX_SECRETS = 256

_ENV_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|refresh[_-]?token"
    r"|secret|client[_-]?secret|password|passwd|private[_-]?key|credential|_token$|^token$)"
)

#: Mapping keys whose *value* is a credential even when the value has no
#: recognisable shape (``{"api_key": "opaque..."}``). Structural patterns only
#: see the value, so without this pass a nested JSON payload keeps the secret.
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|refresh[_-]?token"
    r"|secret|client[_-]?secret|password|passwd|pwd|private[_-]?key|credential"
    r"|authorization|proxy[-_]?authorization|cookie|_token$|^token$)"
)

_lock = threading.Lock()
_secrets: List[str] = []
_secrets_sorted: List[str] = []
_env_seeded = False
_structural: Optional[Callable[[str], str]] = None
_enabled = True


def set_redaction_enabled(enabled: bool) -> None:
    """Master switch for redaction (``logging.redact_enabled`` config)."""
    global _enabled
    _enabled = bool(enabled)


def redaction_enabled() -> bool:
    """Whether redaction currently runs."""
    return _enabled


def _load_structural() -> Callable[[str], str]:
    """Lazily resolve :func:`core.security.redact.redact_secrets`."""
    global _structural
    if _structural is None:
        try:
            from boarai.core.security.redact import redact_secrets

            _structural = redact_secrets
        except Exception:  # noqa: BLE001 - logging must never fail on import
            # Do NOT cache the fallback: an early import-cycle failure must not
            # disable structural redaction for the rest of the process.
            return lambda text: text  # noqa: E731
    return _structural


def register_secret(value: Optional[str]) -> bool:
    """Add a literal secret to the substitution set. Returns True when stored."""
    global _secrets_sorted
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if len(candidate) < MIN_SECRET_LEN:
        return False
    with _lock:
        if candidate in _secrets:
            return False
        _secrets.append(candidate)
        if len(_secrets) > MAX_SECRETS:
            del _secrets[: len(_secrets) - MAX_SECRETS]
        # Longest first: overlapping secrets cannot leave an unredacted suffix.
        _secrets_sorted = sorted(_secrets, key=len, reverse=True)
    return True


def register_secrets(values: Iterable[Optional[str]]) -> int:
    """Register many values; returns how many were newly stored."""
    return sum(1 for value in values if register_secret(value))


def clear_secrets() -> None:
    """Drop every registered literal (test isolation, credential rotation)."""
    global _secrets_sorted, _env_seeded
    with _lock:
        _secrets.clear()
        _secrets_sorted = []
        _env_seeded = False


def registered_secret_count() -> int:
    """How many literal secrets are currently registered."""
    with _lock:
        return len(_secrets)


def seed_secrets_from_env() -> int:
    """Register sensitive-looking environment values (once per process)."""
    global _env_seeded
    with _lock:
        if _env_seeded:
            return 0
        _env_seeded = True
    found = 0
    for name, value in list(os.environ.items()):
        if value and _ENV_SECRET_RE.search(name):
            if register_secret(value):
                found += 1
    return found


#: Cheap pre-screen for the structural pass. Every pattern in
#: ``core.security.redact`` starts with one of these literals, so a line that
#: contains none of them cannot match any of them — the gate is
#: false-negative-free by construction, and it keeps the common case (an
#: ordinary sentence) off seven regex substitutions. Redaction runs per sink on
#: every record, so this is the difference between free and measurable.
_STRUCTURAL_HINTS = (
    "bearer", "basic", "api", "key", "token", "secret", "passw", "pwd",
    "private", "credential", "://", "@", "sk-", "gh", "xox", "akia", "aiza",
    "eyj",
)


def _may_contain_structural(lowered: str) -> bool:
    for hint in _STRUCTURAL_HINTS:
        if hint in lowered:
            return True
    return False


def redact_text(text: Optional[str]) -> str:
    """Mask literal secrets, then structural credential shapes, in *text*."""
    if not text:
        return text or ""
    if not _enabled:
        return text
    out = text
    with _lock:
        candidates = _secrets_sorted
    for secret in candidates:
        if secret in out:
            out = out.replace(secret, "[REDACTED]")
    if not _may_contain_structural(out.lower()):
        return out
    return _load_structural()(out)


def redact_args(args: Optional[tuple]) -> Optional[tuple]:
    """Redact the string members of a lazy-format argument tuple."""
    if not args or not _enabled:
        return args
    out = []
    changed = False
    for item in args:
        if isinstance(item, str) and item:
            masked = redact_text(item)
            if masked != item:
                changed = True
            out.append(masked)
        else:
            out.append(item)
    return tuple(out) if changed else args


def redact_structure(value: Any, _depth: int = 0, _seen: Optional[set] = None) -> Any:
    """Redact strings inside a mapping/sequence without changing its shape."""
    if isinstance(value, str):
        return redact_text(value)
    if not _enabled or value is None:
        return value
    if _depth >= 16:
        return "[nested]"
    if not isinstance(value, (dict, list, tuple, set, frozenset)):
        return value

    seen = _seen if _seen is not None else set()
    identity = id(value)
    if identity in seen:
        return "[recursive]"
    seen.add(identity)
    try:
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                if _SENSITIVE_KEY_RE.search(str(key)):
                    # The key names the field as a credential; the value may be
                    # an opaque token with no pattern to match.
                    out[key] = "[REDACTED]" if item not in (None, "") else item
                else:
                    out[key] = redact_structure(item, _depth + 1, seen)
            return out
        if isinstance(value, list):
            return [redact_structure(item, _depth + 1, seen) for item in value]
        if isinstance(value, tuple):
            return tuple(redact_structure(item, _depth + 1, seen) for item in value)
        return type(value)(redact_structure(item, _depth + 1, seen) for item in value)
    finally:
        seen.discard(identity)
