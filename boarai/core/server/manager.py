"""
Server Manager for BoarAi.

Manages unified HTTP/WebSocket server lifecycle.
"""

import logging
from typing import Optional

from .unified_server import UnifiedServer, HOST, PORT

logger = logging.getLogger(__name__)


class ServerManager:
    """Manages unified HTTP/WebSocket server."""

    def __init__(self, host: str = HOST, port: int = PORT):
        self._host = host
        self._port = port
        # Create UnifiedServer eagerly so extensions can register routes
        # before the server starts (router is not yet frozen).
        self._server: Optional[UnifiedServer] = UnifiedServer(self._host, self._port)
        self._running = False

    @property
    def server(self) -> Optional[UnifiedServer]:
        """Get unified server instance."""
        return self._server

    @property
    def is_running(self) -> bool:
        """Check if server is running."""
        return self._running
    
    @property
    def port(self) -> int:
        """Get server port."""
        return self._port
    
    @property
    def host(self) -> str:
        """Get server host."""
        return self._host

    async def start(self) -> None:
        """Start the unified server."""
        if self._running:
            logger.warning("Server already running")
            return

        try:
            await self._server.start()
            self._running = True
            logger.info(f"Server started on port {self._port}")
        except Exception as e:
            logger.error(f"Failed to start server: {e}")
            await self.stop()
            raise

    async def stop(self) -> None:
        """Stop the server."""
        if self._server:
            await self._server.stop()
            self._server = None

        self._running = False
        logger.info("Server stopped")

    # Commands live ONLY in core.command.CommandRegistry (chat domain,
    # intercepted at the message surfaces). The old register_command/
    # unregister_command wrappers fed a per-server handler dict that has
    # been removed along with the CLI command surface.

    def register_route(self, method: str, path: str, handler, extension_name: str) -> bool:
        """Register an HTTP route from an extension."""
        if self._server:
            return self._server.register_route(method, path, handler, extension_name)
        return False

    def unregister_route(self, method: str, path: str, extension_name: str) -> bool:
        """Unregister an HTTP route from an extension."""
        if self._server:
            return self._server.unregister_route(method, path, extension_name)
        return False

    def add_middleware(self, middleware_factory, position: str = "append") -> None:
        """Add an aiohttp middleware to the underlying server."""
        if self._server:
            self._server.add_middleware(middleware_factory, position)

    def get_extension_routes(self, extension_name: str) -> list:
        """Get all routes registered by a specific extension."""
        if self._server:
            return self._server.get_extension_routes(extension_name)
        return []


# Global server manager instance
_server_manager: Optional[ServerManager] = None


def get_server_manager() -> ServerManager:
    """Get the global server manager instance."""
    global _server_manager
    if _server_manager is None:
        _server_manager = ServerManager()
    return _server_manager
