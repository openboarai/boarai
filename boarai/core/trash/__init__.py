"""
Trash / Recycle Bin for BoarAi.

Soft-delete sessions, agents, and files with restore capability.
Also provides file snapshots for write/edit undo.
"""

from .trash_manager import TrashManager, TrashEntry, get_trash_manager

__all__ = [
    "TrashManager",
    "TrashEntry",
    "get_trash_manager",
]
