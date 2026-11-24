"""
Extension base class for BoarAi extension system.

This module defines the abstract base class for all extensions.
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional

from boarai.core.event import EventBus, get_event_bus
from .types import ExtensionType, ExtensionStatus
from .manifest import ExtensionManifest

logger = logging.getLogger(__name__)


class ExtensionBase(ABC):
    """Abstract base class for all BoarAi extensions."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize extension."""
        self._config = config or {}
        self._status = ExtensionStatus.DISCOVERED
        self._event_bus: Optional[EventBus] = None
        self._error: Optional[str] = None
        self._manifest: Optional[ExtensionManifest] = None
        self._data_dir: Optional[Path] = None
        self._api: Any = None
        self._http_middlewares: list = []  # registered via register_middleware
    
    @property
    @abstractmethod
    def manifest(self) -> ExtensionManifest:
        """Get extension manifest."""
        pass
    
    @property
    def name(self) -> str:
        """Get extension name."""
        return self.manifest.name
    
    @property
    def version(self) -> str:
        """Get extension version."""
        return self.manifest.version
    
    @property
    def extension_type(self) -> str:
        """Get extension type."""
        return self.manifest.extension_type
    
    @property
    def status(self) -> ExtensionStatus:
        """Get extension status."""
        return self._status
    
    @property
    def error(self) -> Optional[str]:
        """Get error message."""
        return self._error
    
    @property
    def config(self) -> Dict[str, Any]:
        """Get extension configuration."""
        return self._config
    
    @property
    def event_bus(self) -> Optional[EventBus]:
        """Get event bus."""
        return self._event_bus
    
    @property
    def data_dir(self) -> Optional[Path]:
        """Get the project data directory (.boarai/)."""
        return self._data_dir

    def set_data_dir(self, path: Path) -> None:
        """Set the project data directory. Called by ExtensionManager."""
        self._data_dir = path

    async def set_event_bus(self, event_bus: EventBus) -> None:
        """Set event bus for the extension."""
        self._event_bus = event_bus

    def set_api(self, api: Any) -> None:
        """Set the ExtensionAPI facade (v2). Called by the loader."""
        self._api = api

    @property
    def api(self) -> Optional[Any]:
        """Get the injected ExtensionAPI facade (None for legacy extensions)."""
        return self._api
    
    @abstractmethod
    async def initialize(self, api=None) -> None:
        """Initialize the extension."""
        pass
    
    @abstractmethod
    async def start(self) -> None:
        """Start the extension."""
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop the extension."""
        pass
    
    @abstractmethod
    async def cleanup(self) -> None:
        """Cleanup extension resources."""
        pass
    
    async def on_server_ready(self, server_info: Dict[str, Any]) -> None:
        """Called when the server is ready for middleware/route registration."""
        pass

    def register_middleware(self, middleware_factory, position: str = "prepend") -> bool:
        """Register an HTTP middleware for this extension."""
        from boarai.core.server import get_server_manager
        server_mgr = get_server_manager()
        if not server_mgr or not server_mgr.server:
            logger.warning(
                f"Extension {self.name}: server not available, "
                f"middleware not registered"
            )
            return False
        server_mgr.add_middleware(middleware_factory, position)
        self._http_middlewares.append(middleware_factory)
        logger.info(
            f"Extension {self.name}: registered HTTP middleware "
            f"(position={position})"
        )
        return True

    def unregister_middleware(self, middleware_factory) -> bool:
        """Remove a previously registered HTTP middleware."""
        removed = False
        if middleware_factory in self._http_middlewares:
            self._http_middlewares.remove(middleware_factory)
            removed = True
        try:
            from boarai.core.server import get_server_manager
            server_mgr = get_server_manager()
            if server_mgr and server_mgr.server:
                server_mgr.server.remove_middleware(middleware_factory)
                removed = True
        except Exception as e:  # noqa: BLE001 — cleanup is best-effort
            logger.debug(f"Extension {self.name}: middleware removal skipped: {e}")
        return removed

    def _cleanup_registered_middlewares(self) -> None:
        """Remove all middlewares this extension registered (loader unload)."""
        for factory in list(self._http_middlewares):
            try:
                self.unregister_middleware(factory)
            except Exception:  # noqa: BLE001
                pass
        self._http_middlewares.clear()

    async def on_config_changed(self, config: Dict[str, Any]) -> None:
        """Handle configuration changes."""
        self._config = config
    
    async def on_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Handle events."""
        pass
    
    # Command registration lives on the injected API facade
    # (``api.register_command(...)`` in ``initialize(api)``): the command
    # system is chat-only, and the facade tracks ownership so the loader
    # sweeps an extension's commands on unload. The old
    # register_terminal_command / unregister_terminal_command wrappers are
    # gone with the CLI domain.

    def register_http_route(
        self,
        method: str,
        path: str,
        handler: callable,
        description: Optional[str] = None,
        use_prefix: bool = True
    ) -> bool:
        """Register an HTTP route for this extension."""
        from boarai.core.server import get_server_manager
        server_manager = get_server_manager()
        
        if not server_manager or not server_manager.server:
            logger.error("Server not running, cannot register route")
            return False
        
        # Register the route with the server
        success = server_manager.server.register_route(
            method=method,
            path=path,
            handler=handler,
            extension_name=self.name,
            use_prefix=use_prefix
        )
        
        if success:
            # Store route info for cleanup
            if not hasattr(self, '_registered_routes'):
                self._registered_routes = []
            self._registered_routes.append({
                'method': method,
                'path': path,
                'handler': handler,
                'description': description,
                'use_prefix': use_prefix
            })
            logger.info(f"Extension {self.name} registered route: {method.upper()} {path}")
        
        return success
    
    def unregister_http_route(self, method: str, path: str) -> bool:
        """Unregister an HTTP route for this extension."""
        from boarai.core.server import get_server_manager
        server_manager = get_server_manager()
        
        if not server_manager or not server_manager.server:
            logger.error("Server not running, cannot unregister route")
            return False
        
        # Find use_prefix from stored routes
        use_prefix = True
        if hasattr(self, '_registered_routes'):
            for r in self._registered_routes:
                if r['method'] == method and r['path'] == path:
                    use_prefix = r.get('use_prefix', True)
                    break
        
        # Unregister the route from the server
        success = server_manager.server.unregister_route(
            method=method,
            path=path,
            extension_name=self.name,
            use_prefix=use_prefix
        )
        
        if success:
            # Remove from stored routes
            if hasattr(self, '_registered_routes'):
                self._registered_routes = [
                    r for r in self._registered_routes 
                    if not (r['method'] == method and r['path'] == path)
                ]
            logger.info(f"Extension {self.name} unregistered route: {method.upper()} {path}")
        
        return success
    
    def unregister_all_http_routes(self) -> None:
        """Unregister all HTTP routes for this extension."""
        if not hasattr(self, '_registered_routes'):
            return
        
        from boarai.core.server import get_server_manager
        server_manager = get_server_manager()
        
        if not server_manager or not server_manager.server:
            logger.error("Server not running, cannot unregister routes")
            return
        
        # Unregister all routes
        for route_info in self._registered_routes[:]:
            success = server_manager.server.unregister_route(
                method=route_info['method'],
                path=route_info['path'],
                extension_name=self.name,
                use_prefix=route_info.get('use_prefix', True)
            )
            if success:
                self._registered_routes.remove(route_info)
                logger.info(f"Extension {self.name} unregistered route: {route_info['method'].upper()} {route_info['path']}")
        
        # Clear the list
        self._registered_routes.clear()
    
    def set_status(self, status: ExtensionStatus, error: Optional[str] = None) -> None:
        """Set extension status."""
        self._status = status
        self._error = error
    
    def get_config_value(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        return self._config.get(key, default)
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name}, type={self.extension_type}, status={self.status})"


class ExtensionError(Exception):
    """Extension related errors."""
    
    def __init__(self, message: str, extension: Optional[str] = None, code: str = ""):
        """Initialize extension error."""
        super().__init__(message)
        self.extension = extension
        self.code = code
