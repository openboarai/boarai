"""
Source registry for channel identification.

Part of the core extension framework. Channels declare their source identity
in extension.yaml (source_id / source_display). ExtensionManager auto-registers
during enable_extension() — channels never need to write registration code.

Usage (in core, to look up a source):
    from boarai.core.extension import get_source_registry
    get_source_registry().get("webui")  # → "Web Browser Interface"
"""

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class SourceRegistry:
    """Registry for channel source identifiers."""

    SYSTEM_SOURCE = "system"
    """Generic fallback source when no channel has registered."""

    def __init__(self):
        self._sources: Dict[str, str] = {self.SYSTEM_SOURCE: "System"}
        self._default: str = self.SYSTEM_SOURCE

    def register(self, source_id: str, display_name: str) -> None:
        """Register a channel source."""
        if source_id == self.SYSTEM_SOURCE:
            logger.warning(f"Cannot override system source '{source_id}'")
            return
        self._sources[source_id] = display_name
        logger.info(f"Source registered: {source_id} → {display_name}")
        if self._default == self.SYSTEM_SOURCE:
            self._default = source_id

    def unregister(self, source_id: str) -> bool:
        """Unregister a source (called when channel shuts down)."""
        if source_id == self.SYSTEM_SOURCE:
            return False
        if source_id in self._sources:
            del self._sources[source_id]
            if self._default == source_id:
                self._default = self.SYSTEM_SOURCE
            return True
        return False

    def get(self, source_id: str) -> Optional[str]:
        """Get the display name for a source_id. Returns None if not registered."""
        return self._sources.get(source_id)

    def is_registered(self, source_id: str) -> bool:
        """Check if a source_id has been registered."""
        return source_id in self._sources

    def get_default(self) -> str:
        """Get the default source_id."""
        return self._default

    def list_sources(self) -> Dict[str, str]:
        """List all registered sources (source_id → display_name)."""
        return dict(self._sources)


_source_registry: Optional[SourceRegistry] = None


def get_source_registry() -> SourceRegistry:
    """Get the global SourceRegistry singleton."""
    global _source_registry
    if _source_registry is None:
        _source_registry = SourceRegistry()
    return _source_registry


def reset_source_registry() -> None:
    """Reset the global SourceRegistry (for testing)."""
    global _source_registry
    _source_registry = None
