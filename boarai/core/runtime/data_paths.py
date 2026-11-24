"""Data-directory anchor — single source of truth for the BoarAi data dir.

All runtime state lives under ONE data directory, anchored OUTSIDE the
source tree: default ``Path.home()/.boarai`` on every platform
(``%USERPROFILE%\\.boarai`` on Windows, ``~/.boarai`` elsewhere). The
location can be customized via the ``BOARAI_DATA_DIR`` environment
variable or the ``--data-dir`` CLI flag.

A pre-unification store inside a source tree (``<project_root>/.boarai``)
is NOT auto-migrated: move it to the home anchor manually, or point
``BOARAI_DATA_DIR`` at it to keep using it in place.

Every subsystem must resolve paths through :func:`get_data_dir` /
:func:`resolve_data_path` instead of hardcoding a ``.boarai`` location.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

ENV_DATA_DIR = "BOARAI_DATA_DIR"

# Data dir name under the home anchor.
DATA_DIR_NAME = ".boarai"

_data_dir: Optional[Path] = None


def resolve_data_dir(project_root: Optional[Path] = None) -> Path:
    """Resolve the data dir anchor."""
    if _data_dir is not None:
        return _data_dir
    env = os.environ.get(ENV_DATA_DIR)
    if env:
        return Path(env).expanduser().resolve()
    return default_data_dir()


def set_data_dir(path: Optional[Union[str, Path]] = None) -> Path:
    """Pin the data directory (called once at startup). Returns it."""
    global _data_dir
    if path is None:
        _data_dir = resolve_data_dir()
    else:
        _data_dir = Path(path).expanduser().resolve()
    return _data_dir


def get_data_dir() -> Path:
    """Get the data directory, resolving the default if not yet set."""
    return resolve_data_dir()


def default_data_dir(project_root: Optional[Path] = None) -> Path:
    """The DEFAULT data dir: ``Path.home()/.boarai`` (outside the tree)."""
    return Path.home().resolve() / DATA_DIR_NAME


def data_dir_spawn_args(project_root: Optional[Path] = None) -> list:
    """``["--data-dir", <dir>]`` when re-spawning a child entry (restart)."""
    data_dir = resolve_data_dir(project_root)
    if data_dir != default_data_dir(project_root):
        return ["--data-dir", str(data_dir)]
    return []


def extension_data_dir(ext_id: str) -> Path:
    """Per-extension private data dir: ``<data>/extensions/data/<ext_id>``."""
    ident = str(ext_id or "").strip()
    if not ident or ident == "." or ".." in ident or "/" in ident or "\\" in ident:
        raise ValueError(f"invalid extension id for data dir: {ext_id!r}")
    return get_data_dir() / "extensions" / "data" / ident


def resolve_data_path(value: Optional[str] = None) -> Path:
    """Resolve a configured/legacy path against the data dir."""
    data = get_data_dir()
    if not value:
        return data
    text = str(value).strip()
    if not text:
        return data
    p = Path(text)
    if p.is_absolute():
        if sys.platform == "win32" and not p.drive and not p.anchor.startswith("\\\\"):
            # Drive-less rooted path (e.g. "/vol1/..." from a Unix/NAS config):
            # ambiguous on Windows — would silently land on the current drive
            # root. Degrade into the data dir instead of scattering files.
            # (True UNC paths like \\server\share are kept as-is.)
            logger.warning(
                "Path %r is drive-less absolute; mapped under data dir %s",
                text, data,
            )
            parts = [part for part in p.parts if part not in ("/", "\\")]
            return data.joinpath("_mapped_", *parts)
        return p
    parts = p.parts
    if parts and parts[0] in (".boarai", ".boar"):
        parts = parts[1:]
    if not parts:
        return data
    return data.joinpath(*parts)
