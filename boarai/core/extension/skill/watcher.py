"""SkillWatcher — polling-based hot refresh for the skill index.

Microkernel constraint: stdlib only (no watchdog), so change detection is a
periodic **signature scan** of both skill roots (cheap ``stat``/``listdir``
calls), following the same background-timer pattern as the Curator.

Signature covers:
- each skill dir's ``SKILL.md`` ``(mtime_ns, size)`` — frontmatter/body edits;
- each skill dir's resource file NAMES under ``references/scripts/assets`` —
  ``Skill.resources`` is frozen at load time and ``read_resource`` rejects
  unlisted files, so an added/removed resource must trigger a reload.
  Resource file *content* is NOT in the signature: ``read_resource`` reads
  live from disk, so content edits need no reload.

The soft-delete ``archived`` dir is excluded (mirrors ``scan_dir`` exclusion)
so curator archiving never resurrects skills.

On change: debounce (let transient tmp-replace writes settle) → rescan →
``SkillStore.reload()`` in a worker thread → publish ``skill.reloaded``.
In-process skill writes through the store already update the index
immediately; this watcher only covers EXTERNAL changes (manual edits,
git pull, other processes — including SKILL.md files written with
``file_write`` by the model).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Iterable, Optional, Tuple

from .loader import RESOURCE_DIRS, SKILL_FILENAME
from .store import ARCHIVED_DIR_NAME, SkillStore, get_skill_store

logger = logging.getLogger(__name__)

DEFAULT_WATCH_INTERVAL = 5.0    # seconds between signature scans (aligned
                                 # with the extension manager's watcher)
DEBOUNCE_SECONDS = 2.0          # settle window after a change is detected

_EXCLUDED_DIRS = frozenset({ARCHIVED_DIR_NAME})

# One signature entry per skill dir:
# (dir_path, SKILL.md mtime_ns, SKILL.md size, sorted resource names)
_SkillEntry = Tuple[str, int, int, Tuple[str, ...]]


def _resource_names(skill_dir: Path) -> Tuple[str, ...]:
    """Sorted relative names of resource files under the standard dirs."""
    names = []
    for sub in RESOURCE_DIRS:
        sub_dir = skill_dir / sub
        if not sub_dir.is_dir():
            continue
        try:
            for p in sub_dir.rglob("*"):
                if p.is_file():
                    names.append(p.relative_to(skill_dir).as_posix())
        except OSError:
            continue
    return tuple(sorted(names))


def _entry(skill_dir: Path, skill_md: Path) -> _SkillEntry:
    try:
        st = skill_md.stat()
        return (str(skill_dir), st.st_mtime_ns, st.st_size, _resource_names(skill_dir))
    except OSError:
        return (str(skill_dir), 0, 0, ())


def scan_signature(roots: Iterable[Path]) -> Tuple[_SkillEntry, ...]:
    """Build the change-detection signature for all skill dirs under *roots*."""
    entries = []
    for root in roots:
        try:
            if not root.is_dir():
                continue
            children = sorted(root.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir() or child.name in _EXCLUDED_DIRS:
                continue
            skill_md = child / SKILL_FILENAME
            if skill_md.is_file():
                entries.append(_entry(child, skill_md))
                continue
            try:
                nested_dirs = sorted(child.iterdir())
            except OSError:
                continue
            for nested in nested_dirs:
                if not nested.is_dir() or nested.name in _EXCLUDED_DIRS:
                    continue
                nested_md = nested / SKILL_FILENAME
                if nested_md.is_file():
                    entries.append(_entry(nested, nested_md))
    return tuple(entries)


class SkillWatcher:
    """Background hot-refresh of the SkillStore index (poll + debounce)."""

    def __init__(
        self,
        store: Optional[SkillStore] = None,
        interval: float = DEFAULT_WATCH_INTERVAL,
        debounce: float = DEBOUNCE_SECONDS,
    ) -> None:
        self._store = store if store is not None else get_skill_store()
        self._interval = float(interval)
        self._debounce = float(debounce)
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self._last_sig: Optional[Tuple[_SkillEntry, ...]] = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, interval: Optional[float] = None) -> bool:
        """Start watching; returns False (no-op) when disabled (interval <= 0)."""
        if interval is not None:
            self._interval = float(interval)
        if self.running:
            return True
        if self._interval <= 0:
            logger.info("SkillWatcher disabled (watch_interval <= 0)")
            return False
        self._stopping = False
        # Establish the baseline BEFORE the task starts, so changes made
        # between start() and the first poll are still detected (also makes
        # the startup window race-free and tests deterministic).
        self._last_sig = await asyncio.to_thread(self._signature)
        self._task = asyncio.create_task(self._run(), name="skill-watcher")
        logger.info("SkillWatcher started (interval=%.1fs)", self._interval)
        return True

    async def stop(self) -> None:
        """Stop the background task (idempotent)."""
        self._stopping = True
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001
                logger.debug("SkillWatcher stop: %s", e)
        logger.info("SkillWatcher stopped")

    def _signature(self) -> Tuple[_SkillEntry, ...]:
        return scan_signature((self._store.system_dir, self._store.agent_dir))

    async def _run(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(self._interval)
                if self._stopping:
                    break
                sig = await asyncio.to_thread(self._signature)
                if self._last_sig is None:
                    self._last_sig = sig
                    continue
                if sig == self._last_sig:
                    continue
                # Debounce: let transient writes (tmp-replace, multi-file
                # edits) settle, then rescan and reload.
                await asyncio.sleep(self._debounce)
                if self._stopping:
                    break
                new_sig = await asyncio.to_thread(self._signature)
                await asyncio.to_thread(self._store.reload)
                # Baseline is updated only AFTER a successful reload: a failed
                # reload leaves the OLD baseline in place, so the change is
                # retried on the next pass instead of being consumed. (A write
                # landing between new_sig and reload at worst costs one
                # redundant reload later — never a missed one.)
                self._last_sig = new_sig
                logger.info("SkillWatcher: change detected — skill index reloaded")
                await self._publish_reloaded()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.debug("SkillWatcher pass failed: %s", e)

    @staticmethod
    async def _publish_reloaded() -> None:
        try:
            from boarai.core.event import publish_event
            from boarai.core.event.types import EventType

            await publish_event(
                EventType.SKILL_RELOADED.value,
                {"source": "watcher"},
                "skill_watcher",
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("skill.reloaded publish skipped: %s", e)


# --- Global singleton ---

_skill_watcher: Optional[SkillWatcher] = None


def get_skill_watcher() -> SkillWatcher:
    """Get the global SkillWatcher singleton."""
    global _skill_watcher
    if _skill_watcher is None:
        _skill_watcher = SkillWatcher()
    return _skill_watcher
