"""Version parsing and comparison for the update subsystem (stdlib only).

Only plain numeric dot versions (``1.12.0``) participate in update
decisions; anything with a pre-release/build suffix (``1.12.0rc1``,
``1.12.0+local``) is rejected by :func:`parse_version` and therefore
ignored as an update target — matching the "stable channel only" default.
"""

from __future__ import annotations

from typing import Optional, Tuple


def parse_version(text: object) -> Optional[Tuple[int, ...]]:
    """Parse ``"1.12.0"`` into ``(1, 12, 0)``; None for anything else."""
    raw = str(text or "").strip()
    if not raw:
        return None
    parts = raw.split(".")
    out = []
    for part in parts:
        if not part.isdigit():
            return None
        out.append(int(part))
    return tuple(out) if out else None


def is_newer(candidate: object, current: object) -> bool:
    """True when ``candidate`` parses and compares strictly greater."""
    cand = parse_version(candidate)
    cur = parse_version(current)
    if cand is None or cur is None:
        return False
    return cand > cur


def current_version() -> str:
    """The running build's version (single source: ``boarai.core.__version__``)."""
    from boarai.core import __version__

    return str(__version__)
