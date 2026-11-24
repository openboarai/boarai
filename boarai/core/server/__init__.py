"""
BoarAi Server Module.

Provides unified HTTP and WebSocket server on a single port (26620).
"""

from .unified_server import UnifiedServer, HOST, PORT
from .manager import ServerManager, get_server_manager

__all__ = [
    "UnifiedServer", "ServerManager", "get_server_manager",
    "HOST", "PORT",
]
