"""Process-level project root (single source of truth).

Set once at startup via ``set_project_root``; all subsystems should
read via ``get_project_root`` instead of deriving from ``__file__``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

_project_root: Optional[Path] = None


def set_project_root(path: Path) -> None:
    """Set the project root (called once at startup)."""
    global _project_root
    _project_root = Path(path).resolve()


def get_project_root() -> Optional[Path]:
    """Get the project root path, or None if not set."""
    return _project_root


def resolve_project_root(*, fallback: Optional[Path] = None) -> Path:
    """Return project root, falling back to ``fallback`` or cwd."""
    if _project_root is not None:
        return _project_root
    if fallback is not None:
        return Path(fallback).resolve()
    return Path.cwd().resolve()