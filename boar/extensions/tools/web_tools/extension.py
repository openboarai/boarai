"""
Web tools extension — wraps web_tools.py for the extension system.
"""
import logging

from boarai.core.extension import ExtensionBase, ExtensionManifest

logger = logging.getLogger(__name__)

_MANIFEST = ExtensionManifest(
    name="web_tools",
    version="1.0.0",
    extension_type="tool",
    description="Web access — key-free web_search (configurable engines: baidu/so360/sogou/bing/google, tried in priority order; the first three are China-reachable) and web_fetch (anti-bot tiered chain: direct / curl_cffi impersonation / reader relay)",
)


class WebToolsExtension(ExtensionBase):
    """Web tools extension."""

    @property
    def manifest(self) -> ExtensionManifest:
        return _MANIFEST

    async def initialize(self, api=None) -> None:
        # Import triggers @tool decorator self-registration
        from boar.extensions.tools.web_tools import tools  # noqa: F811
        # Apply the configured engines (one full SERP link per engine, in
        # priority order; falls back to the defaults when the setting is
        # absent). The legacy `search_engines` string stays accepted.
        if api is not None:
            cfg = api.get_config() or {}
            tools.set_search_engines(cfg.get("engine_urls")
                                     or cfg.get("search_engines"))
        logger.info("Web tools initialized")

    async def on_config_changed(self, config) -> None:
        """Live-apply the engine setting without a restart (PUT .../settings)."""
        await super().on_config_changed(config)
        try:
            from boar.extensions.tools.web_tools import tools
            tools.set_search_engines((config or {}).get("engine_urls")
                                     or (config or {}).get("search_engines"))
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to apply engine setting change: %s", e)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def cleanup(self) -> None:
        pass
