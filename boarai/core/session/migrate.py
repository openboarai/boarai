"""Session migrator: v3 single-file JSON -> v4 append-only JSONL.

Migration is additive: it writes ``{id}.jsonl`` and renames the original
``{id}.json`` to ``{id}.json.v3.bak``. Nothing is deleted, so ``--rollback``
can restore the previous state verbatim.

Three hard constraints:

**Idempotent.** A directory that already has a journal is skipped. The sweep
runs on every startup, so a repeated run must have no side effects and must
not write a second ``.bak``.

**One failure never aborts the sweep.** A single corrupt session must not keep
the service from starting: failed directories are recorded in the report and
the sweep continues. The read path already falls back to v3, so unmigrated
sessions still open.

**Conversion goes through the runtime's own code path.**
``Session.from_dict`` -> ``to_entries``, with no second serialiser: a separate
implementation would silently diverge from the runtime format on some field,
and the round-trip tests would then validate the wrong behaviour.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .journal import SESSION_FORMAT_VERSION, SessionJournal

logger = logging.getLogger(__name__)

LEGACY_SUFFIX = ".json"
JOURNAL_SUFFIX = ".jsonl"
BACKUP_SUFFIX = ".json.v3.bak"

STATUS_MIGRATED = "migrated"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUS_MISSING = "missing"
STATUS_DRY_RUN = "dry_run"


@dataclass(slots=True)
class MigrationReport:
    """Result of one migration sweep."""

    scanned: int = 0
    migrated: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    failed: List[Tuple[str, str]] = field(default_factory=list)
    rolled_back: List[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        """True when nothing failed (skips are a normal, idempotent outcome)."""
        return not self.failed

    def summary(self) -> str:
        parts = [
            f"scanned={self.scanned}",
            f"migrated={len(self.migrated)}",
            f"skipped={len(self.skipped)}",
            f"failed={len(self.failed)}",
        ]
        if self.rolled_back:
            parts.append(f"rolled_back={len(self.rolled_back)}")
        if self.dry_run:
            parts.append("(dry-run: nothing written)")
        return " ".join(parts)


def _session_dirs(storage_path: Path) -> List[Path]:
    """List every session directory, sorted for stable reports and test diffs."""
    try:
        return sorted(
            (d for d in storage_path.iterdir() if d.is_dir()),
            key=lambda p: p.name,
        )
    except OSError as e:
        logger.error("Cannot scan session storage %s: %s", storage_path, e)
        return []


def _paths_for(session_dir: Path) -> Tuple[Path, Path, Path]:
    """Return the ``(legacy_json, journal, backup)`` paths."""
    name = session_dir.name
    return (
        session_dir / f"{name}{LEGACY_SUFFIX}",
        session_dir / f"{name}{JOURNAL_SUFFIX}",
        session_dir / f"{name}{BACKUP_SUFFIX}",
    )


def _rename_backup(legacy: Path, backup: Path) -> None:
    """Rename the v3 file to a backup, numbering it rather than overwriting."""
    if not backup.exists():
        os.replace(str(legacy), str(backup))
        return
    n = 2
    while True:
        candidate = backup.with_name(f"{backup.name}.{n}")
        if not candidate.exists():
            os.replace(str(legacy), str(candidate))
            return
        n += 1


async def migrate_session_dir(
    session_dir: Path, dry_run: bool = False,
) -> str:
    """Migrate one session directory and return a status string."""
    from .manager import Session

    legacy, journal_path, backup = _paths_for(session_dir)

    if journal_path.is_file():
        # Already migrated. But if the v3 file is still here (a previous run was
        # interrupted before the rename), finish the rename: otherwise every read
        # tries the journal first and then falls back, and the directory keeps
        # two sources of truth.
        if legacy.is_file() and not dry_run:
            _rename_backup(legacy, backup)
        return STATUS_SKIPPED

    if not legacy.is_file():
        return STATUS_MISSING

    if dry_run:
        # Parse anyway: a v3 file that cannot be read fails for real too, and
        # dry-run exists to surface that in advance.
        import json

        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
            Session.from_dict(data)
        except Exception as e:  # noqa: BLE001
            logger.warning("Dry-run: %s would fail: %s", session_dir.name, e)
            return STATUS_FAILED
        return STATUS_DRY_RUN

    try:
        import json

        data = json.loads(legacy.read_text(encoding="utf-8"))
        session = Session.from_dict(data)
        entries = session.to_entries()

        journal = SessionJournal(journal_path)
        await journal.rewrite(entries)
        _rename_backup(legacy, backup)
        logger.info(
            "Migrated session %s → v%s journal (%s entries)",
            session_dir.name, SESSION_FORMAT_VERSION, len(entries),
        )
        return STATUS_MIGRATED
    except Exception as e:  # noqa: BLE001
        logger.error("Migration failed for %s: %s", session_dir.name, e)
        # Drop the half-written journal on failure; otherwise the read path would
        # prefer an incomplete file over the v3 fallback that still opens fine.
        try:
            if journal_path.is_file():
                journal_path.unlink()
        except OSError:
            pass
        return STATUS_FAILED


async def migrate_sessions(
    storage_path: Path, dry_run: bool = False,
) -> MigrationReport:
    """Scan and migrate every session under a sessions root."""
    report = MigrationReport(dry_run=dry_run)
    for session_dir in _session_dirs(Path(storage_path)):
        report.scanned += 1
        status = await migrate_session_dir(session_dir, dry_run=dry_run)
        if status == STATUS_MIGRATED:
            report.migrated.append(session_dir.name)
        elif status == STATUS_SKIPPED:
            report.skipped.append(session_dir.name)
        elif status == STATUS_DRY_RUN:
            report.migrated.append(session_dir.name)
        elif status == STATUS_FAILED:
            report.failed.append((session_dir.name, "see log"))
        # STATUS_MISSING: no transcript in the directory (attachment-only dirs),
        # so it belongs to no bucket.
    logger.info("Session migration: %s", report.summary())
    return report


async def rollback_session_dir(session_dir: Path) -> bool:
    """Roll one directory back to v3: drop the journal, restore the backup to ``.json``."""
    _, journal_path, backup = _paths_for(session_dir)
    if not backup.is_file():
        return False
    try:
        if journal_path.is_file():
            journal_path.unlink()
        legacy = session_dir / f"{session_dir.name}{LEGACY_SUFFIX}"
        os.replace(str(backup), str(legacy))
        return True
    except OSError as e:
        logger.error("Rollback failed for %s: %s", session_dir.name, e)
        return False


async def rollback_sessions(storage_path: Path) -> MigrationReport:
    """Roll back every session under a sessions root."""
    report = MigrationReport()
    for session_dir in _session_dirs(Path(storage_path)):
        report.scanned += 1
        if await rollback_session_dir(session_dir):
            report.rolled_back.append(session_dir.name)
    logger.info("Session rollback: %s", report.summary())
    return report


async def verify_migration(session_dir: Path) -> Dict[str, Any]:
    """Check whether a directory's v3 backup and v4 journal are equivalent."""
    import json

    from .manager import Session

    legacy, journal_path, backup = _paths_for(session_dir)
    source = backup if backup.is_file() else legacy
    if not source.is_file() or not journal_path.is_file():
        return {"ok": False, "reason": "missing source or journal"}

    old = Session.from_dict(json.loads(source.read_text(encoding="utf-8")))
    journal = SessionJournal(journal_path)
    new = Session.from_entries(await journal.read_entries())

    problems: List[str] = []
    if len(old.messages) != len(new.messages):
        problems.append(
            f"message count {len(old.messages)} → {len(new.messages)}"
        )
    else:
        for i, (a, b) in enumerate(zip(old.messages, new.messages)):
            for attr in (
                "role", "content", "message_id", "seq",
                "tool_calls", "tool_call_id", "thinking",
            ):
                av, bv = getattr(a, attr), getattr(b, attr)
                if av != bv:
                    problems.append(f"messages[{i}].{attr}: {av!r} → {bv!r}")
    for attr in (
        "session_id", "title", "agent_name", "pinned", "archived", "source",
        "thinking_mode", "model", "workspace_path", "status",
        "compressed_summary", "compression_count", "metadata",
        "security_overrides",
    ):
        av, bv = getattr(old, attr), getattr(new, attr)
        if av != bv:
            problems.append(f"{attr}: {av!r} → {bv!r}")
    if len(old.cache_requests) != len(new.cache_requests):
        problems.append(
            f"cache_requests {len(old.cache_requests)} → {len(new.cache_requests)}"
        )
    if old.created_at != new.created_at:
        problems.append(f"created_at: {old.created_at} → {new.created_at}")

    return {"ok": not problems, "problems": problems}


__all__ = [
    "BACKUP_SUFFIX",
    "JOURNAL_SUFFIX",
    "LEGACY_SUFFIX",
    "MigrationReport",
    "STATUS_DRY_RUN",
    "STATUS_FAILED",
    "STATUS_MIGRATED",
    "STATUS_MISSING",
    "STATUS_SKIPPED",
    "migrate_session_dir",
    "migrate_sessions",
    "rollback_session_dir",
    "rollback_sessions",
    "verify_migration",
]
