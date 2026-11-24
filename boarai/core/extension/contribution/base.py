"""
Contribution point abstraction for BoarAi extension system.

A ContributionPoint is the unit of extension capability mounting. Every
capability type (tool, route, hook, service, skill, fragment, ...) implements
a ContributionPoint subclass and a corresponding Registry that holds handlers
by extension id.

Adding a new capability type = subclass ContributionPoint + add a Registry.
Core execution bodies never change.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ContributionPoint(ABC):
    """Abstract base for a single extension capability contribution."""

    kind: str = "base"

    def __init__(self, extension_id: str) -> None:
        self.extension_id = extension_id

    @abstractmethod
    async def mount(self) -> None:
        """Register this contribution into its registry."""

    @abstractmethod
    async def unmount(self) -> None:
        """Remove this contribution from its registry."""


class ContributionError(Exception):
    """Raised when a contribution cannot be mounted/unmounted."""
