"""
File tools extension — wraps file_tools.py for the extension system.
"""
import logging
from typing import Any, Dict

from boarai.core.extension import ExtensionBase, ExtensionManifest, ExtensionStatus

logger = logging.getLogger(__name__)

_MANIFEST = ExtensionManifest(
    name="file_tools",
    version="1.4.0",
    extension_type="tool",
    description="File system operations — read, write, edit, move, copy, delete, "
                "glob, grep, search, trash list/restore",
)


class FileToolsExtension(ExtensionBase):
    """File tools extension."""

    @property
    def manifest(self) -> ExtensionManifest:
        return _MANIFEST

    async def initialize(self, api=None) -> None:
        # Import triggers @tool decorator self-registration
        from boar.extensions.tools.file_tools import tools  # noqa: F811
        # P6: declare our own hook points so other extensions can intercept
        # file writes without modifying this extension.
        if api is not None:
            api.declare_hook("pre_write", "blockable",
                             description="Called before a file write; "
                                         "return {'blocked': true} to veto")
            api.declare_hook("post_write", "observe",
                             description="Called after a file write")
        logger.info("File tools initialized")

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def cleanup(self) -> None:
        pass
