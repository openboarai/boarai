"""Dependency probing and installation for extension manifests."""

from .manager import DependencyManager, get_dependency_manager

__all__ = [
    "DependencyManager",
    "get_dependency_manager",
]