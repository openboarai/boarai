"""
Shell tools extension.
"""
import logging
from typing import Any, Dict

from boarai.core.extension import ExtensionBase, ExtensionManifest

logger = logging.getLogger(__name__)

_MANIFEST = ExtensionManifest(
    name="shell_tools",
    version="1.0.0",
    extension_type="tool",
    description="Shell command execution with safety checks",
)


class ShellToolsExtension(ExtensionBase):
    @property
    def manifest(self) -> ExtensionManifest:
        return _MANIFEST

    async def initialize(self, api=None) -> None:
        from boar.extensions.tools.shell_tools import tools  # noqa: F401
        # P6: expose a hook point so other extensions can veto shell execution.
        if api is not None:
            api.declare_hook("pre_execute", "blockable",
                             description="Called before shell command execution; "
                                         "return {'blocked': true} to veto")
        logger.info("Shell tools initialized")

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def cleanup(self) -> None:
        pass
