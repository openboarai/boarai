"""Context Fragment interface — composable Layer 4/5 context sources.

Analogous to Codex's WorldStateSection trait: each fragment provides a
render/diff/snapshot contract. The FragmentRegistry manages all fragments
so that Layer 4/5 assembly is extensible without modifying assembler.py.

All render methods are synchronous to match PromptAssembler.assemble_messages()
which is a sync method (called from both async executors and sync tests).
"""

from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# --- Data types ---


@dataclass
class FragmentContent:
    """Output produced by a ContextFragment."""

    text: str
    """The text to inject into the prompt."""
    role: str = "system"
    """Message role (system / user / developer)."""
    priority: int = 50
    """Lower = earlier in the prompt (keeps stable ordering)."""
    name: str = ""
    """Identifier for debugging / tracing."""
    stability: int = 3
    """PromptDocument stability hint (3 = Layer 4, 2 = Layer 5)."""
    inject_as_suffix: bool = False
    """If True, inject as suffix on the last non-system message
    (used by task_state for structured data suffix)."""


# --- ContextFragment — abstract base ---


class ContextFragment(ABC):
    """Base class for all composable context sources."""

    # --- Identity ---

    @property
    @abstractmethod
    def fragment_id(self) -> str:
        """Unique identifier, e.g. 'compressed_summary', 'task_state'."""
        ...

    @property
    def default_enabled(self) -> bool:
        """Whether this fragment is enabled by default. Override as needed."""
        return True

    @property
    def default_priority(self) -> int:
        """Default ordering priority (lower = earlier). Override as needed."""
        return 50

    # --- Render (all sync) ---

    @abstractmethod
    def render(
        self,
        assembler: Any,
        **kwargs: Any,
    ) -> Optional[FragmentContent]:
        """Full render: produce content or None if nothing to inject."""
        ...

    def render_diff(
        self,
        assembler: Any,
        previous_snapshot: Optional[bytes],
        **kwargs: Any,
    ) -> Optional[FragmentContent]:
        """Incremental render when a previous snapshot exists."""
        return self.render(assembler, **kwargs)

    def snapshot(
        self,
        assembler: Any,
        **kwargs: Any,
    ) -> Optional[bytes]:
        """Compute current state hash for next-turn diff comparison."""
        content = self.render(assembler, **kwargs)
        if content is None:
            return None
        return hashlib.sha1(content.text.encode()).digest()


# --- FragmentRegistry ---


class FragmentRegistry:
    """Manages ContextFragment instances."""

    def __init__(self) -> None:
        self._fragments: Dict[str, ContextFragment] = {}

    # --- CRUD ---

    def register(self, fragment: ContextFragment) -> None:
        """Register a fragment. Replaces any existing with the same id."""
        fid = fragment.fragment_id
        if fid in self._fragments:
            logger.warning("Fragment '%s' already registered — replacing", fid)
        self._fragments[fid] = fragment
        logger.info(
            "Fragment registered: %s (priority=%d)", fid, fragment.default_priority
        )

    def unregister(self, fragment_id: str) -> bool:
        """Remove a fragment by id. Returns True if it was registered."""
        exists = fragment_id in self._fragments
        if exists:
            del self._fragments[fragment_id]
            logger.info("Fragment unregistered: %s", fragment_id)
        return exists

    def get(self, fragment_id: str) -> Optional[ContextFragment]:
        """Get a fragment by id."""
        return self._fragments.get(fragment_id)

    def list_all(self) -> List[ContextFragment]:
        """Return all fragments sorted by (priority, fragment_id)."""
        return sorted(
            self._fragments.values(),
            key=lambda f: (f.default_priority, f.fragment_id),
        )

    # --- Batch operations ---

    def render_all(
        self,
        assembler: Any,
        *,
        snapshots: Optional[Dict[str, Optional[bytes]]] = None,
        diff_mode: bool = False,
        **kwargs: Any,
    ) -> List[FragmentContent]:
        """Render all enabled fragments, returning content list."""
        results: List[FragmentContent] = []
        snapshots = snapshots or {}

        for fragment in self.list_all():
            if not fragment.default_enabled:
                continue
            try:
                if diff_mode:
                    prev = snapshots.get(fragment.fragment_id)
                    content = fragment.render_diff(assembler, prev, **kwargs)
                else:
                    content = fragment.render(assembler, **kwargs)

                if content is not None:
                    if not content.name:
                        content.name = fragment.fragment_id
                    if content.priority == 50:  # still default
                        content.priority = fragment.default_priority
                    results.append(content)

            except Exception:
                logger.exception(
                    "Fragment '%s' render failed — skipping this turn",
                    fragment.fragment_id,
                )

        return results

    def compute_snapshots(
        self,
        assembler: Any,
        **kwargs: Any,
    ) -> Dict[str, Optional[bytes]]:
        """Compute snapshot hashes for all fragments (for next-turn diff)."""
        snapshots: Dict[str, Optional[bytes]] = {}
        for fragment in self.list_all():
            try:
                snap = fragment.snapshot(assembler, **kwargs)
                snapshots[fragment.fragment_id] = snap
            except Exception:
                logger.exception(
                    "Fragment '%s' snapshot failed", fragment.fragment_id
                )
                snapshots[fragment.fragment_id] = None
        return snapshots

    def clear(self) -> None:
        """Remove all registered fragments."""
        self._fragments.clear()
        logger.info("FragmentRegistry cleared")


# --- Built-in Fragments (migrate existing Layer 4/5 logic) ---


class MemoryFragment(ContextFragment):
    """Layer 4a: Long-term memory injection (<memory-horizon>)."""

    fragment_id = "memory"
    default_priority = 41

    def render(
        self,
        assembler: Any,
        **kwargs: Any,
    ) -> Optional[FragmentContent]:
        memory_horizon = kwargs.get("memory_horizon", "")
        proactive_memory = kwargs.get("proactive_memory", "")

        parts = []
        if memory_horizon:
            parts.append(f"<memory-horizon>\n{memory_horizon}\n</memory-horizon>")
        if proactive_memory:
            parts.append(f"<proactive-memory>\n{proactive_memory}\n</proactive-memory>")

        if not parts:
            return None

        return FragmentContent(
            text="\n\n".join(parts),
            name="memory",
            role="system",
            priority=self.default_priority,
            stability=3,
        )

    def render_diff(
        self,
        assembler: Any,
        previous_snapshot: Optional[bytes],
        **kwargs: Any,
    ) -> Optional[FragmentContent]:
        content = self.render(assembler, **kwargs)
        if content is None:
            return None
        # Prefix-cache stability: keep identical content for unchanged fragments.
        return content


class UserProfileFragment(ContextFragment):
    """User info layer: reads .boarai/user/user.md."""

    fragment_id = "user_profile"
    default_priority = 40

    def render(
        self,
        assembler: Any,
        **kwargs: Any,
    ) -> Optional[FragmentContent]:
        from pathlib import Path

        try:
            from boarai.core.runtime import get_data_dir
            path = get_data_dir() / "user" / "user.md"
        except Exception:
            project_root = kwargs.get("project_root")
            if project_root is None:
                return None
            path = Path(project_root) / ".boarai" / "user" / "user.md"
        if not path.is_file():
            return None

        return FragmentContent(
            text=f"<user-info>\n{path.read_text(encoding='utf-8')}\n</user-info>",
            name="user_profile",
            role="system",
            priority=self.default_priority,
            stability=3,
        )


class SkillIndexFragment(ContextFragment):
    """Layer 4c: available-skills index (name/description only)."""

    fragment_id = "skill_index"
    default_priority = 42

    def render(
        self,
        assembler: Any,
        **kwargs: Any,
    ) -> Optional[FragmentContent]:
        try:
            from boarai.core.extension.skill import get_skill_store

            text = get_skill_store().build_index_text()
        except Exception as e:  # noqa: BLE001
            logger.debug("Skill index render skipped: %s", e)
            return None
        if not text:
            return None
        return FragmentContent(
            text=text,
            name="skill_index",
            role="system",
            priority=self.default_priority,
            stability=3,
        )

