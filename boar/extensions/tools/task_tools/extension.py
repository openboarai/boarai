"""
Task tools extension.
"""
import logging
from typing import Any, Dict

from boarai.core.extension import ExtensionBase, ExtensionManifest

logger = logging.getLogger(__name__)

_MANIFEST = ExtensionManifest(
    name="task_tools",
    version="1.0.0",
    extension_type="tool",
    description="Task management tools — create, update, list tasks",
)


class TaskToolsExtension(ExtensionBase):
    @property
    def manifest(self) -> ExtensionManifest:
        return _MANIFEST

    async def initialize(self, api=None) -> None:
        from boar.extensions.tools.task_tools import tools  # noqa: F401
        logger.info("Task tools initialized")

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def cleanup(self) -> None:
        pass
