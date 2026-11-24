"""Pod storage facet: persistence, path resolution and concurrency control.

Storage used to live inside ``MemoryManager`` next to retrieval and lifecycle
orchestration, so the four engines (weaver, consolidation, reincarnation,
distill worker) reached into its private members (``_read_entries``,
``_write_entries``, ``_get_pod_lock``, ``_boarai_dir``, ``_tag_index``,
``_pod_cache_dirty``) and four production call sites did the same from the
outside.

``PodStore`` turns that layer into an explicit facet: engines depend on the
contract, so the storage backend can be replaced without touching any engine.

Lock identity is a hard invariant: the read-modify-write in ``consolidation``
and ``remember`` must take the **same** per-pod lock, otherwise the two
interleave and overwrite each other. The store owns and hands out the locks so
that this invariant has exactly one implementation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..runtime import write_text_atomic_async
from .types import MemoryEntry

logger = logging.getLogger(__name__)


class PodStore(ABC):
    """Persistence facet for memory pods."""

    @property
    @abstractmethod
    def boarai_dir(self) -> Path:
        """Memory data root (``.boarai``)."""

    @property
    @abstractmethod
    def cache_dirty(self) -> Set[str]:
        """Pods whose entries changed since the last weave snapshot."""

    @property
    @abstractmethod
    def tag_index(self) -> Any:
        """Inverted tag index, injected by the provider during ``start()``."""

    @abstractmethod
    def resolve_pod_path(self, pod_uri: str) -> Optional[Path]:
        """Map a pod URI to its file path; ``None`` when it cannot be resolved."""

    @abstractmethod
    def build_pod_uri(self, pod_type: str, pod_id: str) -> str:
        """Build a pod URI from ``(type, id)``."""

    @abstractmethod
    def parse_pod_uri(self, pod_uri: str) -> Tuple[str, str]:
        """Split a pod URI into ``(type, id)``."""

    @abstractmethod
    def pod_lock(self, pod_uri: str) -> asyncio.Lock:
        """Write lock for a pod; the same pod must always yield the same lock."""

    @abstractmethod
    async def pod_file_exists(self, pod_uri: str) -> bool:
        """Report whether the pod's backing file exists."""

    @abstractmethod
    async def read_entries(self, pod_uri: str) -> List[MemoryEntry]:
        """Read every entry of one pod."""

    @abstractmethod
    async def write_entries(self, pod_uri: str, entries: List[MemoryEntry]) -> None:
        """Replace a pod's entries atomically."""

    @abstractmethod
    async def find_entry_by_id(self, entry_id: str) -> Optional[MemoryEntry]:
        """Look one entry up by id across the standard pods."""


class FilePodStore(PodStore):
    """``PodStore`` backed by one JSON file per pod under ``.boarai``."""

    def __init__(self, data_dir: Path) -> None:
        self._boarai_dir = Path(data_dir)
        # Per-pod locks: the consolidation read-modify-write must not interleave
        # with remember, or one update silently overwrites the other.
        self._pod_locks: Dict[str, asyncio.Lock] = {}
        # pod_uri -> ((mtime_ns, size), entries). Every recall touches all
        # mounted pods, so without this cache retrieval cost grows with the
        # total memory stored instead of with the result size. Invalidated by
        # write_entries; bounded by the number of pods, which operators control.
        self._entries_cache: Dict[str, Any] = {}
        self._pod_cache_dirty: Set[str] = set()
        self._tag_index = None

    @property
    def boarai_dir(self) -> Path:
        return self._boarai_dir

    @property
    def cache_dirty(self) -> Set[str]:
        return self._pod_cache_dirty

    @property
    def tag_index(self) -> Any:
        return self._tag_index

    @tag_index.setter
    def tag_index(self, value: Any) -> None:
        self._tag_index = value

    @property
    def entries_cache(self) -> Dict[str, Any]:
        """Read-only view of the parse cache; lets tests assert hits and misses."""
        return self._entries_cache

    def resolve_pod_path(self, pod_uri: str) -> Optional[Path]:
        """Resolve a pod URI to its actual file path."""
        if not pod_uri.startswith("pod://"):
            return None

        rest = pod_uri[len("pod://"):]

        if rest.startswith("session/"):
            sid = rest[len("session/"):]
            return self.boarai_dir / "sessions" / sid / "memory.json"

        elif rest.startswith("agent/"):
            name = rest[len("agent/"):]
            return self.boarai_dir / "agents" / name / "memory.json"

        elif rest == "global":
            return self.boarai_dir / "memory" / "global.json"

        elif rest.startswith("shared/"):
            mid = rest[len("shared/"):]
            return self.boarai_dir / "memory" / "shared" / f"{mid}.json"

        elif rest.startswith("staging/"):
            name = rest[len("staging/"):]
            return self.boarai_dir / "memory" / "staging" / name

        elif rest.startswith("skill/"):
            skill_id = rest[len("skill/"):]
            return self.boarai_dir / "memory" / "skills" / f"{skill_id}.json"

        return None

    def build_pod_uri(self, pod_type: str, pod_id: str) -> str:
        """Build a pod URI from type and id."""
        if pod_type == "session":
            return f"pod://session/{pod_id}"
        elif pod_type == "agent":
            return f"pod://agent/{pod_id}"
        elif pod_type == "shared":
            return f"pod://shared/{pod_id}"
        return f"pod://{pod_type}"

    def parse_pod_uri(self, pod_uri: str) -> Tuple[str, str]:
        """Parse a pod URI into (type, id). e.g. pod://session/abc → ("session", "abc")"""
        rest = pod_uri[len("pod://"):]
        if "/" in rest:
            ptype, pid = rest.split("/", 1)
            return ptype, pid
        return rest, ""

    async def pod_file_exists(self, pod_uri: str) -> bool:
        """Check if a pod's storage file exists."""
        path = self.resolve_pod_path(pod_uri)
        if not path:
            return False
        return path.exists()

    async def find_entry_by_id(self, entry_id: str) -> Optional[MemoryEntry]:
        """Find an entry by its ID across all standard pods."""
        for pod in ["pod://global", "pod://shared"]:
            entries = await self.read_entries(pod)
            for e in entries:
                if e.entry_id == entry_id:
                    return e
        return None

    async def read_entries(self, pod_uri: str) -> List[MemoryEntry]:
        """Read all entries from a pod's JSON file."""
        path = self.resolve_pod_path(pod_uri)
        if not path or not path.exists():
            return []
        try:
            stat = path.stat()
            fingerprint = (stat.st_mtime_ns, stat.st_size)
        except OSError as e:
            logger.debug(f"Failed to stat pod {pod_uri}: {e}")
            fingerprint = None

        if fingerprint is not None:
            cached = self._entries_cache.get(pod_uri)
            if cached is not None and cached[0] == fingerprint:
                return list(cached[1])

        try:
            import aiofiles
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                content = await f.read()
            if not content.strip():
                return []
            data = json.loads(content)
            if isinstance(data, list):
                entries = [MemoryEntry.from_dict(e) for e in data]
                if fingerprint is not None:
                    self._entries_cache[pod_uri] = (fingerprint, list(entries))
                return entries
            return []
        except Exception as e:
            logger.error(f"Failed to read pod {pod_uri}: {e}")
            return []

    async def write_entries(self, pod_uri: str, entries: List[MemoryEntry]) -> None:
        """Write entries to a pod's JSON file atomically (tmp + os.replace)."""
        path = self.resolve_pod_path(pod_uri)
        if not path:
            logger.error(f"Cannot resolve pod path: {pod_uri}")
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = [e.to_dict() for e in entries]
            await write_text_atomic_async(
                path, json.dumps(data, ensure_ascii=False, indent=2)
            )
            # The file just changed, so any cached parse of it is stale. Dropping
            # it rather than replacing it keeps the next read authoritative even
            # if the filesystem mtime resolution is coarse.
            self._entries_cache.pop(pod_uri, None)
            # Invalidate weave pod cache for this pod
            self.cache_dirty.add(pod_uri)
        except Exception as e:
            logger.error(f"Failed to write pod {pod_uri}: {e}")

    def pod_lock(self, pod_uri: str) -> asyncio.Lock:
        """Return the per-pod lock (created lazily)."""
        lock = self._pod_locks.get(pod_uri)
        if lock is None:
            lock = asyncio.Lock()
            self._pod_locks[pod_uri] = lock
        return lock
