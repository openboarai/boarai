"""Online update subsystem (pip / git / source channels).

Pieces:
  - ``steward``  — install-kind detection, per-channel prechecks, launch prefixes
  - ``checker``  — PyPI-JSON version check with cache/TTL + sdist/mirror resolution
  - ``state``    — worker status file, OS lock, receipts
  - ``worker``   — the detached installer process (``python -m boarai.core.update.worker``)
  - ``service``  — server-side orchestration + auto-check loop
  - ``version``  — numeric version parse/compare

Every install kind updates IN PLACE through its own channel: pip reinstalls
the package, git fetches + checks out the release tag, source swaps the
verified release archive into the tree (backup kept). Cross-channel
"updates" are deliberately impossible — they would leave a second, diverged
copy behind.
"""

from .service import (
    UpdateError,
    get_status,
    start_auto_check,
    stop_auto_check,
    trigger_apply,
    trigger_check,
    worker_in_progress,
)
from .steward import detect_install_kind, git_precheck, refusal_for, source_precheck
from .version import current_version, is_newer, parse_version

__all__ = [
    "UpdateError",
    "get_status",
    "trigger_check",
    "trigger_apply",
    "worker_in_progress",
    "start_auto_check",
    "stop_auto_check",
    "detect_install_kind",
    "git_precheck",
    "source_precheck",
    "refusal_for",
    "current_version",
    "is_newer",
    "parse_version",
]
