"""
TagIndex — pure JSON inverted index for semantic memory retrieval.

Provides O(1) keyword → entry lookup across all pods, powered by
LLM-generated semantic expansion tags during memory distillation.

Storage: .boarai/memory/index.json  (< 50 KB for typical workloads)
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class TagIndex:
    """Pure JSON inverted index for semantic tag lookup."""

    def __init__(self, index_path: Optional[Path] = None):
        self._path: Optional[Path] = index_path
        self._index: Dict[str, Set[str]] = {}
        self._lock = asyncio.Lock()
        self._loaded = False

    async def load(self) -> None:
        """Load index from disk into memory."""
        if not self._path:
            return
        async with self._lock:
            try:
                if self._path.exists():
                    content = await asyncio.to_thread(
                        self._path.read_text, encoding="utf-8",
                    )
                    if content.strip():
                        raw = json.loads(content)
                        self._index = {k: set(v) for k, v in raw.items()}
                        logger.debug(
                            f"TagIndex loaded: {len(self._index)} tags, "
                            f"{sum(len(v) for v in self._index.values())} entries"
                        )
            except (json.JSONDecodeError, FileNotFoundError, UnicodeDecodeError) as e:
                logger.warning(f"TagIndex load failed, starting fresh: {e}")
                self._index = {}
            self._loaded = True

    async def clear(self) -> None:
        """Clear the in-memory index (used before a full rebuild)."""
        async with self._lock:
            self._index = {}

    async def add(self, entry_id: str, tags: List[str]) -> None:
        """Add an entry's tags to the index and auto-flush."""
        if not tags:
            return
        async with self._lock:
            for tag in tags:
                tag_lower = tag.lower().strip()
                if not tag_lower:
                    continue
                if tag_lower not in self._index:
                    self._index[tag_lower] = set()
                self._index[tag_lower].add(entry_id)
        await self._async_flush()

    async def remove(self, entry_id: str) -> None:
        """Remove an entry from all tag mappings and auto-flush."""
        async with self._lock:
            for tags in self._index.values():
                tags.discard(entry_id)
            empty = [t for t, ids in self._index.items() if not ids]
            for t in empty:
                del self._index[t]
        await self._async_flush()

    def search(self, keywords: List[str], top_k: int = 20) -> Dict[str, Set[str]]:
        """O(1) dictionary lookup for matching entry IDs."""
        results: Dict[str, Set[str]] = {}
        for kw in keywords:
            kw_lower = kw.lower().strip()
            if not kw_lower:
                continue
            ids = self._index.get(kw_lower, set())
            if ids:
                results[kw] = set(list(ids)[:top_k])
        return results

    async def _async_flush(self) -> None:
        """Non-blocking flush (background task)."""
        try:
            await self.flush()
        except Exception as e:
            logger.debug(f"TagIndex background flush skipped: {e}")

    async def flush(self) -> None:
        """Persist index to disk."""
        if not self._path:
            return
        async with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                serializable = {k: list(v) for k, v in self._index.items()}
                await asyncio.to_thread(
                    lambda: self._path.write_text(
                        json.dumps(serializable, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    ),
                )
                logger.debug(f"TagIndex flushed: {len(self._index)} tags")
            except Exception as e:
                logger.error(f"TagIndex flush failed: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Get index statistics."""
        total_entries = len({eid for ids in self._index.values() for eid in ids})
        return {
            "total_tags": len(self._index),
            "total_entries_indexed": total_entries,
            "top_tags": sorted(
                self._index.keys(),
                key=lambda k: len(self._index[k]),
                reverse=True,
            )[:20],
        }



