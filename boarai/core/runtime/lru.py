"""Bounded LRU map for per-session in-memory caches.

Several subsystems key a cache by ``session_id`` and never evict: on a
long-running server every session that ever ran leaves an entry behind, and the
entries are heavy (a prompt prefix holds the whole system message plus every
tool schema). This is a dict with a maximum size that drops the
least-recently-used entry, so the bound holds without a background reaper.

Insertion and lookup are O(1). Access counts as use, so a session that is still
streaming is not evicted in favour of one that finished an hour ago.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Dict, Iterator, Optional, TypeVar

K = TypeVar("K")
V = TypeVar("V")

#: Default cap for per-session caches. A single operator rarely has more than a
#: handful of sessions open at once; the tail is history that can be rebuilt
#: from disk on demand.
DEFAULT_MAXSIZE = 32


class BoundedMap(Dict[K, V]):
    """Insertion-ordered mapping that evicts its least-recently-used entry."""

    def __init__(self, maxsize: int = DEFAULT_MAXSIZE) -> None:
        if maxsize < 1:
            raise ValueError(f"maxsize must be >= 1, got {maxsize}")
        super().__init__()
        self._maxsize = maxsize
        # Recency order is tracked separately because dict itself does not
        # reorder on read.
        self._order: "OrderedDict[K, None]" = OrderedDict()

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def __getitem__(self, key: K) -> V:
        value = super().__getitem__(key)
        self._order.move_to_end(key)
        return value

    def get(self, key: K, default: Optional[V] = None) -> Optional[V]:  # type: ignore[override]
        if not super().__contains__(key):
            return default
        self._order.move_to_end(key)
        return super().__getitem__(key)

    def __setitem__(self, key: K, value: V) -> None:
        super().__setitem__(key, value)
        self._order[key] = None
        self._order.move_to_end(key)
        self._evict()

    def __delitem__(self, key: K) -> None:
        super().__delitem__(key)
        self._order.pop(key, None)

    def pop(self, key: K, *args):  # type: ignore[override]
        self._order.pop(key, None)
        return super().pop(key, *args)

    def clear(self) -> None:
        self._order.clear()
        super().clear()

    def _evict(self) -> None:
        while len(self) > self._maxsize:
            oldest, _ = self._order.popitem(last=False)
            super().__delitem__(oldest)

    def __repr__(self) -> str:
        return f"BoundedMap(maxsize={self._maxsize}, len={len(self)})"
