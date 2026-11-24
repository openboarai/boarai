"""
BoarAi Runtime Module.

Process-level concerns:
- project root (single source of truth)
- data dir anchor
- first-run bootstrap (seed .boarai from defaults)
"""

from .root import get_project_root, resolve_project_root, set_project_root

# Dependency-free leaves are imported BEFORE .bootstrap.
# It reaches core.logging, whose handlers import `spawn` from this
# package; binding it late would make the partially-initialized module fail
# with "cannot import name 'spawn' from 'core.runtime'".
from .atomic_write import write_text_atomic, write_text_atomic_async
from .bg import spawn, drain, pending_count
from .lru import BoundedMap, DEFAULT_MAXSIZE
from .json_body import parse_json_object

from .data_paths import (
    data_dir_spawn_args,
    default_data_dir,
    extension_data_dir,
    get_data_dir,
    resolve_data_dir,
    resolve_data_path,
    set_data_dir,
    ENV_DATA_DIR,
)
from .bootstrap import copy_defaults

__all__ = [
    "get_project_root",
    "set_project_root",
    "resolve_project_root",
    "get_data_dir",
    "default_data_dir",
    "data_dir_spawn_args",
    "set_data_dir",
    "resolve_data_dir",
    "resolve_data_path",
    "extension_data_dir",
    "ENV_DATA_DIR",
    "copy_defaults",
    "write_text_atomic",
    "write_text_atomic_async",
    "spawn",
    "drain",
    "pending_count",
    "BoundedMap",
    "DEFAULT_MAXSIZE",
    "parse_json_object",
]