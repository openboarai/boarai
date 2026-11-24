"""Log retention: age budget plus a total-size budget.

Age alone is not a bound. A day with a runaway retry loop can write gigabytes,
and ``retention_days=30`` would keep all of it — so the sweep also enforces a
total byte budget for the log tree. When the budget is exceeded the retention
window is halved and the sweep repeats, which converges in a few passes and
always keeps today's directory (codex uses the same halving rule for its SQLite
log database).

One file is exempt: ``usage.jsonl``. It shares the dated directory with the
diagnostic logs but is not a log — the monitoring API aggregates token history
from it, so deleting it silently shrinks the reported lifetime totals. It gets
its own, much longer, retention.

Never raises: retention is housekeeping, and housekeeping must not be able to
break startup or kill the periodic task.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import date as date_cls, timedelta
from pathlib import Path
from typing import List, Optional, Set

from .handlers import iter_date_dirs, today_str

logger = logging.getLogger(__name__)

PRESERVED_FILES = frozenset({"usage.jsonl"})
"""Files inside a dated directory that log retention must not delete."""

DEFAULT_LEDGER_RETENTION_DAYS = 365
"""How long the preserved usage ledger is kept (tiny: one line per run)."""


@dataclass
class PruneReport:
    """What one sweep removed."""

    dirs_removed: int = 0
    files_removed: int = 0
    bytes_removed: int = 0
    preserved_dirs: List[str] = field(default_factory=list)
    #: Retention actually applied, which shrinks when the budget forces it.
    effective_retention_days: Optional[int] = None

    @property
    def removed_anything(self) -> bool:
        return bool(self.files_removed or self.dirs_removed)

    def summary(self) -> str:
        return (
            f"dirs={self.dirs_removed} files={self.files_removed} "
            f"freed={self.bytes_removed / (1024 * 1024):.1f}MB "
            f"retention={self.effective_retention_days}d"
        )


def file_size(path: Path) -> int:
    """Size of *path* in bytes (0 when it vanished or is unreadable)."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def tree_size(base_dir: Path) -> int:
    """Total bytes under *base_dir*, following one level of date directories."""
    total = 0
    try:
        for entry in base_dir.rglob("*"):
            if entry.is_file():
                total += file_size(entry)
    except OSError:
        return total
    return total


def _cutoff_iso(retention_days: int) -> str:
    days = retention_days if retention_days >= 0 else 0
    return (date_cls.today() - timedelta(days=days)).isoformat()


def prune_logs_dir(
    base_dir: Path,
    retention_days: int = 30,
    total_budget_bytes: Optional[int] = None,
    ledger_retention_days: int = DEFAULT_LEDGER_RETENTION_DAYS,
) -> PruneReport:
    """Sweep *base_dir*: age first, then halve the window until under budget."""
    report = PruneReport(effective_retention_days=max(retention_days, 0))
    try:
        base_dir = Path(base_dir)
        if not base_dir.is_dir():
            return report
    except OSError:
        return report

    today = today_str()
    retention = max(int(retention_days), 0)
    ledger_cutoff = _cutoff_iso(max(int(ledger_retention_days), 0))

    for _ in range(12):  # halving converges long before this
        cutoff = _cutoff_iso(retention)
        for day_dir in iter_date_dirs(base_dir):
            if day_dir.name >= cutoff or day_dir.name == today:
                continue
            report_dir = _prune_one_dir(day_dir, ledger_cutoff, today)
            report.dirs_removed += report_dir[0]
            report.files_removed += report_dir[1]
            report.bytes_removed += report_dir[2]
            if report_dir[3]:
                report.preserved_dirs.append(day_dir.name)

        report.effective_retention_days = retention
        if not total_budget_bytes or total_budget_bytes <= 0:
            return report
        if tree_size(base_dir) <= total_budget_bytes or retention <= 0:
            return report
        # Over budget: shorten the window and sweep again. Never below 0 —
        # today's directory survives every pass by construction.
        retention = retention // 2

    return report


def _prune_one_dir(day_dir: Path, ledger_cutoff: str, today: str) -> tuple:
    """Delete one expired date directory, keeping preserved files in date."""
    files_removed = 0
    bytes_removed = 0
    preserved = False
    try:
        entries = list(day_dir.iterdir())
    except OSError:
        return (0, 0, 0, False)

    for entry in entries:
        try:
            if entry.is_dir():
                continue
            if entry.name in PRESERVED_FILES:
                # The ledger outlives the logs it shares a directory with.
                if day_dir.name >= ledger_cutoff or day_dir.name == today:
                    preserved = True
                    continue
            size = file_size(entry)
            entry.unlink()
            files_removed += 1
            bytes_removed += size
        except OSError:
            preserved = True  # something is still in there; keep the directory

    if not preserved:
        try:
            day_dir.rmdir()
            return (1, files_removed, bytes_removed, False)
        except OSError:
            return (0, files_removed, bytes_removed, True)
    return (0, files_removed, bytes_removed, True)


def prune_old_date_dirs(base_dir: Path, retention_days: int) -> int:
    """Age-only sweep; returns the number of date directories removed."""
    report = prune_logs_dir(Path(base_dir), retention_days=retention_days)
    return report.dirs_removed


def preserved_names() -> Set[str]:
    """The file names retention never deletes (introspection / tests)."""
    return set(PRESERVED_FILES)
