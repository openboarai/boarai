"""
Session-level prompt prefix cache for BoarAi.

Caches Zone 1 + Zone 2 (the static prefix) per session, so that
repeated turns and cross-run requests don't reassemble the prefix
unnecessarily.

Cache file location:
    .boarai/sessions/{session_id}/prompt_cache.json

Cache invalidation:
    - Agent switch (agent_name changes)
    - Agent config edit (soul.md / agent.yaml mtime changes)
    - Extension load/unload (extension list changes)
    - Tool register/unregister (tool list changes)
    - system_core.md mtime changes
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CACHE_FILENAME = "prompt_cache.json"


class CachedPrefix:
    """In-memory representation of a cached prompt prefix."""

    def __init__(
        self,
        signature: Dict[str, Any],
        zone1_message: Dict[str, str],
        tools: List[Dict[str, Any]],
        cached_at: str = "",
    ):
        self.signature = signature
        self.zone1_message = zone1_message
        self.tools = tools
        self.cached_at = cached_at or datetime.now().isoformat()

    @property
    def messages(self) -> List[Dict[str, str]]:
        """Combined cached prefix messages."""
        return [self.zone1_message]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signature": self.signature,
            "zone1": self.zone1_message,
            "tools": self.tools,
            "cached_at": self.cached_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CachedPrefix":
        return cls(
            signature=data.get("signature", {}),
            zone1_message=data.get("zone1", {}),
            tools=data.get("tools", []),
            cached_at=data.get("cached_at", ""),
        )


class SessionPromptCache:
    """Per-session prompt prefix cache stored on disk."""

    def __init__(self, sessions_dir: Path):
        """Initialize the cache manager."""
        self._sessions_dir = Path(sessions_dir)

    def _get_cache_path(self, session_id: str) -> Path:
        """Get the cache file path for a session."""
        safe_id = session_id
        for char in ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]:
            safe_id = safe_id.replace(char, "_")
        return self._sessions_dir / safe_id / _CACHE_FILENAME

    def compute_signature(
        self,
        agent_name: str,
        agent_config: Any = None,
        extension_manager: Any = None,
        tool_registry: Any = None,
    ) -> Dict[str, Any]:
        """Compute a cache signature from current system state."""
        sig: Dict[str, Any] = {
            "agent_name": agent_name,
        }

        # Agent config file mtime
        if agent_config:
            sig["agent_config_mtime"] = getattr(
                agent_config, "updated_at", ""
            )
        else:
            sig["agent_config_mtime"] = ""

        # Extensions key
        ext_names: List[str] = []
        if extension_manager:
            try:
                ext_names = sorted(extension_manager.list_extensions())
            except Exception:
                pass
        sig["extensions_key"] = ",".join(ext_names)

        # Tools key
        tool_names: List[str] = []
        if tool_registry:
            try:
                defs = tool_registry.get_tool_definitions()
                for t in defs:
                    name = t.get("function", {}).get("name", "")
                    if name:
                        tool_names.append(name)
                tool_names.sort()
            except Exception:
                pass
        sig["tools_key"] = ",".join(tool_names)

        # System core prompt mtime
        try:
            from .loader import _find_prompts_dir
            core_path = _find_prompts_dir() / "system_core.md"
            if core_path.exists():
                sig["system_core_mtime"] = datetime.fromtimestamp(
                    core_path.stat().st_mtime
                ).isoformat()
            else:
                sig["system_core_mtime"] = ""
        except Exception:
            sig["system_core_mtime"] = ""

        return sig

    def load(
        self,
        session_id: str,
        expected_signature: Dict[str, Any],
    ) -> Optional[CachedPrefix]:
        """Load cached prefix if signature matches."""
        cache_path = self._get_cache_path(session_id)
        if not cache_path.exists():
            return None

        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            cached = CachedPrefix.from_dict(data)

            # Compare signatures
            if cached.signature == expected_signature:
                logger.debug(
                    f"Prompt cache HIT for session {session_id} "
                    f"(cached at {cached.cached_at})"
                )
                return cached
            else:
                logger.debug(
                    f"Prompt cache MISS for session {session_id} "
                    f"(signature changed)"
                )
                return None
        except Exception as e:
            logger.warning(f"Failed to load prompt cache for {session_id}: {e}")
            return None

    def save(
        self,
        session_id: str,
        signature: Dict[str, Any],
        zone1_message: Dict[str, str],
        tools: List[Dict[str, Any]],
    ) -> None:
        """Save assembled prefix to session cache file."""
        cache_path = self._get_cache_path(session_id)
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cached = CachedPrefix(
                signature=signature,
                zone1_message=zone1_message,
                tools=tools,
            )
            cache_path.write_text(
                json.dumps(cached.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.debug(
                f"Prompt cache saved for session {session_id} "
                f"({len(tools)} tools)"
            )
        except Exception as e:
            logger.warning(f"Failed to save prompt cache for {session_id}: {e}")

    def invalidate(self, session_id: str) -> None:
        """Delete cache file for a session."""
        cache_path = self._get_cache_path(session_id)
        try:
            if cache_path.exists():
                cache_path.unlink()
                logger.debug(f"Prompt cache invalidated for session {session_id}")
        except Exception as e:
            logger.warning(f"Failed to invalidate prompt cache: {e}")


# ==================== Global Instance ====================

_session_prompt_cache: Optional[SessionPromptCache] = None


def get_session_prompt_cache(
    sessions_dir: Optional[Path] = None,
) -> SessionPromptCache:
    """Get the global session prompt cache instance."""
    global _session_prompt_cache
    if _session_prompt_cache is None:
        if sessions_dir is None:
            # Try to discover from the data dir anchor
            try:
                from boarai.core.runtime import get_data_dir
                sessions_dir = get_data_dir() / "sessions"
            except Exception:
                raise RuntimeError(
                    "SessionPromptCache not initialized. "
                    "Call get_session_prompt_cache(sessions_dir) first."
                )
        _session_prompt_cache = SessionPromptCache(sessions_dir)
    return _session_prompt_cache


def reset_session_prompt_cache() -> None:
    """Reset the global cache instance (for testing)."""
    global _session_prompt_cache
    _session_prompt_cache = None
