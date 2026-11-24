"""
Memory tools extension.
"""
import logging

from boarai.core.extension import ExtensionBase, ExtensionManifest

logger = logging.getLogger(__name__)

_MANIFEST = ExtensionManifest(
    name="memory_tools",
    version="1.0.0",
    extension_type="tool",
    description="Memory tools — recall, remember, list and forget long-term memory entries",
)


class MemoryToolsExtension(ExtensionBase):
    @property
    def manifest(self) -> ExtensionManifest:
        return _MANIFEST

    async def initialize(self, api=None) -> None:
        from boar.extensions.tools.memory_tools import tools  # noqa: F401
        logger.info("Memory tools initialized")

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def cleanup(self) -> None:
        pass
