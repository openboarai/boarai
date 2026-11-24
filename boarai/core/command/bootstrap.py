"""Bootstrap utilities for BoarAI.

The SINGLE directory-layout source: every ``main.py`` subcommand calls
:func:`create_directories` before doing anything, so the data dir layout
is identical no matter which entry created it. Extension-type
subdirectory names come from the discovery layer's ``TYPE_DIRS`` mapping
(the enum VALUE is not the directory name, e.g. CHANNEL -> "channels").
"""

import logging
from pathlib import Path

from boarai.core.extension.types import ExtensionType

logger = logging.getLogger(__name__)

# Runtime data subdirectories under the resolved data dir.
DATA_DIR_SUBDIRS = [
    "config", "logs", "sessions", "agents", "temp", "cache",
    "memory",
    "memory/staging", "memory/graveyard", "memory/graveyard/session",
    "memory/graveyard/agent", "memory/synapses", "memory/shared",
    "memory/retired_agents",
    "extensions",
    "extensions/data", "extensions/configs",
    "trash", "trash/sessions", "trash/agents", "trash/files",
    "snapshots", "snapshots/files",
]


def create_directories(project_root: Path) -> None:
    """Create the full BoarAI runtime directory tree (data dir based)."""
    from boarai.core.runtime import resolve_data_dir

    project_root = Path(project_root)
    data_dir = resolve_data_dir(project_root)
    data_dir.mkdir(parents=True, exist_ok=True)

    for sub in DATA_DIR_SUBDIRS:
        (data_dir / sub).mkdir(parents=True, exist_ok=True)

    # Extension type directories: derive the names from the discovery
    # layer's TYPE_DIRS mapping (the single source of truth - the enum
    # VALUE is not the directory name, e.g. CHANNEL -> "channels").
    # A fresh data dir then matches exactly what discovery scans.
    from boarai.core.extension.discovery import ExtensionDiscovery

    ext_dir = data_dir / "extensions"
    for ext_type in ExtensionType:
        type_dir = ExtensionDiscovery.TYPE_DIRS.get(ext_type, ext_type.value)
        (ext_dir / type_dir).mkdir(parents=True, exist_ok=True)

    # A custom data dir must not starve the default-anchor log fallback
    # that early startup (before the data dir anchor resolves) writes into.
    from boarai.core.runtime import default_data_dir

    default_dir = default_data_dir()
    if data_dir != default_dir:
        (default_dir / "logs").mkdir(parents=True, exist_ok=True)
