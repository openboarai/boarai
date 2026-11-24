"""{{Name}} — minimal extension container."""

import logging

from boarai.core.extension import ExtensionBase, ExtensionManifest

logger = logging.getLogger(__name__)

_MANIFEST = ExtensionManifest(
    name="{{name}}",
    version="0.1.0",
    extension_type="{{type}}",
    description="Hello extension",
)


class {{Name}}Extension(ExtensionBase):
    @property
    def manifest(self) -> ExtensionManifest:
        return _MANIFEST

    async def initialize(self, api=None) -> None:
        # Manifest-contributed routes/tools are mounted by the loader — nothing
        # to do here unless you need runtime registration.
        logger.info("{{name}} initialized")

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def cleanup(self) -> None:
        pass
