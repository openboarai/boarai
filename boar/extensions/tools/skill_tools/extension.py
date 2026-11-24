"""Skill tools extension — reusable skill discovery, reading and authoring."""

import logging

from boarai.core.extension import ExtensionBase, ExtensionManifest

logger = logging.getLogger(__name__)

_MANIFEST = ExtensionManifest(
    name="skill_tools",
    version="1.0.0",
    extension_type="tool",
    description="Skill tools — list, view, read resources and manage reusable skills",
)


class SkillToolsExtension(ExtensionBase):
    @property
    def manifest(self) -> ExtensionManifest:
        return _MANIFEST

    async def initialize(self, api=None) -> None:
        from boar.extensions.tools.skill_tools import tools  # noqa: F401
        logger.info("Skill tools initialized")

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def cleanup(self) -> None:
        pass
