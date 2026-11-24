"""Background curation that keeps the self-improvement stores from rotting.

One pass runs on a coarse timer: duplicate lessons sharing a topic in
``pod://global`` are merged — the most important (then newest) entry survives,
the rest are soft-deleted.

Skill archiving used to live here too (an agent skill with ``use_count == 0``
and an mtime older than 30 days went to the recycle bin). It is gone on purpose:

* it measured the wrong thing. ``skill_view`` bumps ``use_count``, so "used"
  meant "read at least once", and a skill that had never been *offered* a
  matching task was indistinguishable from one that had been offered and
  ignored;
* it destroyed work on a timer, with no signal in the conversation and no way
  for the user to see why a skill disappeared;
* a second archiver (``core/extension/skill/curator.py``, 90 days to
  ``archived/``) ran alongside it unaware of its existence, so the same skill
  carried two different expiry dates and the harsher one won.

Keeping the index small is now an authoring decision rather than a cron job:
the distillation prompt prefers updating an existing skill over creating a
near-duplicate, ``skill_manage`` reports similar skills when one is created,
and ``skill_manage(action="delete")`` is available to the model when a skill is
genuinely dead. Nothing is auto-deleted any more.

Design constraints:

- The timer is coarse and never runs inside a conversation turn, so curation
  adds no latency to chat.
- Every action is recoverable: soft-delete, never a hard delete.
- The rules are deterministic and skip any auxiliary LLM call, which keeps a
  pass cheap; an LLM umbrella-merge can be layered on later.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL = 3600        # Seconds between two curation passes


class Curator:
    """Timer-driven deduplication of global lessons."""

    def __init__(self, interval_seconds: int = DEFAULT_INTERVAL) -> None:
        self._interval = interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._running = False

    # --- Lifecycle ---

    def start(self) -> None:
        """Start the background loop; idempotent when already running."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Curator started (interval=%ss)", self._interval)

    async def stop(self) -> None:
        """Cancel the loop and wait for it to unwind."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Curator stopped")

    async def _loop(self) -> None:
        # The first pass waits one full interval: startup must not compete with
        # store scans, and a short-lived process should never curate at all.
        try:
            await asyncio.sleep(self._interval)
            while self._running:
                await self.curate_once()
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning("Curator loop error: %s", e)

    # --- Entry point ---

    async def curate_once(self) -> dict:
        """Run a single curation pass."""
        result = {"lessons_merged": 0}
        try:
            result["lessons_merged"] = await self._merge_duplicate_lessons()
        except Exception as e:  # noqa: BLE001
            logger.debug("Lesson curation skipped: %s", e)
        return result

    # --- Lesson deduplication ---

    async def _merge_duplicate_lessons(self) -> int:
        """Collapse global lessons that share a topic."""
        try:
            from boarai.core.memory import get_memory_manager
            mm = get_memory_manager()
        except Exception:  # noqa: BLE001
            return 0
        if mm is None:
            return 0

        entries = await mm.pod_store.read_entries("pod://global")
        lessons = [
            e for e in entries
            if getattr(e, "entry_type", "") == "lesson"
            and getattr(e, "lifecycle_state", "active") == "active"
        ]
        if len(lessons) < 2:
            return 0

        groups: dict = {}
        for e in lessons:
            key = (e.topic or "").strip().lower()
            if key:
                groups.setdefault(key, []).append(e)

        merged = 0
        for group in groups.values():
            if len(group) < 2:
                continue
            group.sort(
                key=lambda e: (e.importance, e.updated_at or e.created_at),
                reverse=True,
            )
            for dup in group[1:]:
                try:
                    if await mm.soft_delete_entry(dup.entry_id, pod_uri="pod://global"):
                        merged += 1
                except Exception as e:  # noqa: BLE001
                    logger.debug("Lesson dedupe failed for %s: %s", dup.entry_id, e)

        if merged:
            logger.info("Curator merged %d duplicate lessons", merged)
        return merged


# --- Global singleton ---

_curator: Optional[Curator] = None


def get_curator() -> Curator:
    """Return the process-wide curator, creating it on first use."""
    global _curator
    if _curator is None:
        _curator = Curator()
    return _curator
