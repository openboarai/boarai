"""{{Name}} — kitchen-sink extension: tools/routes/hooks/hook_points/services/ui."""

import logging

from boarai.core.extension import ExtensionBase, ExtensionManifest

logger = logging.getLogger(__name__)

_MANIFEST = ExtensionManifest(
    name="{{name}}",
    version="0.1.0",
    extension_type="{{type}}",
    description="Kitchen-sink extension",
)


class {{Name}}Extension(ExtensionBase):
    @property
    def manifest(self) -> ExtensionManifest:
        return _MANIFEST

    async def initialize(self, api=None) -> None:
        # Manifest contributions (tools/routes/hooks/services/ui) are mounted
        # by the loader through the same api.register_* methods. Register
        # anything runtime-only here instead.
        logger.info("{{name}} initialized (config: %s)", self._config)

    async def on_config_changed(self, config) -> None:
        """Live-apply settings saved from the custom panel (boar.saveConfig)."""
        await super().on_config_changed(config)
        logger.info("{{name}} config changed: %s", config)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def cleanup(self) -> None:
        pass
