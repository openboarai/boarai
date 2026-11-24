"""
BoarAi WebUI Extension - Web interface.

Provides a web-based user interface with HTTP routes.
Uses the extension HTTP route registration system.

Features:
- HTTP route registration
- Web interface serving
- EventBus integration for message passing
- Static file serving for CSS, JavaScript, and assets
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from aiohttp import web

from boarai.core.event import Event, EventType
from boarai.core.extension import ExtensionBase, ExtensionManifest, ExtensionStatus

logger = logging.getLogger(__name__)

# Get the directory containing this file
_CURRENT_DIR = Path(__file__).parent
_STATIC_DIR = _CURRENT_DIR / "static"

class WebUIChannelAdapter(ExtensionBase):
    """WebUI adapter that uses EventBus for communication."""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        # No host/port here on purpose: this adapter does not bind a socket.
        # It mounts "/" and "/static/**" onto the core aiohttp app, so the
        # listen address belongs to core.server.unified_server alone
        # (BOARAI_HOST / BOARAI_PORT). A second copy here would only look
        # configurable while changing nothing.
        self._start_time = None

    @property
    def manifest(self) -> ExtensionManifest:
        """Get extension manifest."""
        if self._manifest is not None:
            return self._manifest
        return ExtensionManifest(
            name="webui",
            version="1.0.0",
            extension_type="channel",
            description="WebUI channel adapter with HTTP interface",
            author="BoarAi Team",
            config_defaults={},
            provides=["web", "http"],
            permissions=["http_routes", "static_files"],
            pip_dependencies=["aiohttp>=3.8.0"]
        )

    async def initialize(self, api=None) -> None:
        """Initialize the WebUI adapter."""
        logger.info("WebUI adapter initializing...")
        self.set_status(ExtensionStatus.INITIALIZED)

    async def start(self) -> None:
        """Start the WebUI adapter."""
        logger.info("WebUI adapter starting...")

        # Record start time
        import time
        self._start_time = time.time()

        # Subscribe to system startup event
        await self.event_bus.subscribe(EventType.SYSTEM_STARTUP, self._on_system_startup)

        # Register HTTP routes via injected API (v2, hot-swappable).
        if self.api is not None:
            # Register index page to root path (no prefix)
            self.api.register_route(
                method="GET",
                path="/",
                handler=self._handle_index,
                description="Main index page",
                use_prefix=False
            )

            # Register static file routes
            self.api.register_route(
                method="GET",
                path="/static/{path:.*}",
                handler=self._handle_static,
                description="Static files (CSS, JS, assets)",
                use_prefix=False
            )
        else:
            # Fallback to legacy registration (non-v2 loader)
            self.register_http_route(
                method="GET",
                path="/",
                handler=self._handle_index,
                description="Main index page",
                use_prefix=False
            )
            self.register_http_route(
                method="GET",
                path="/static/{path:.*}",
                handler=self._handle_static,
                description="Static files (CSS, JS, assets)",
                use_prefix=False
            )

        self.set_status(ExtensionStatus.RUNNING)
        logger.info("WebUI adapter started")

    async def _on_system_startup(self, event: Event) -> None:
        """Handle system startup event."""
        logger.info(f"WebUI adapter received system startup: {event.payload}")

    async def _handle_index(self, request: web.Request) -> web.Response:
        """Handle index page request."""
        logger.info("Index page requested")

        # Serve the static HTML file
        index_file = _STATIC_DIR / "index.html"

        if not index_file.exists():
            logger.error(f"Index file not found: {index_file}")
            return web.Response(
                text="Index file not found",
                status=404
            )

        try:
            # V8: read off the event loop to avoid blocking high-concurrency requests
            content = await asyncio.to_thread(index_file.read_text, encoding='utf-8')
            return web.Response(
                text=content,
                content_type='text/html',
                charset='utf-8',
                # Asset URLs carry no version query; the HTML itself must
                # always be revalidated so an updated tree is picked up.
                headers={'Cache-Control': 'no-cache'},
            )
        except Exception as e:
            logger.error(f"Error reading index file: {e}")
            return web.Response(
                text="Error loading page",
                status=500
            )

    async def _handle_static(self, request: web.Request) -> web.Response:
        """Handle static file requests."""
        # Try to get path from match_info (native router) or extract from URL (middleware)
        file_path = request.match_info.get('path', '')

        if not file_path:
            # Extract path from URL for middleware-based routing
            # URL format: /static/{path}
            path = request.path
            if path.startswith('/static/'):
                file_path = path[8:]  # Remove '/static/' prefix
            elif path.startswith('/static'):
                file_path = path[7:]  # Remove '/static' prefix

        if not file_path:
            return web.Response(status=404)

        # Build full file path
        full_path = _STATIC_DIR / file_path

        # Security check - prevent directory traversal
        # V7 fix: is_relative_to() is a true path containment check (catches
        # sibling-prefix dirs like static_evil that startswith() would miss).
        try:
            static_root = _STATIC_DIR.resolve()
            full_path = full_path.resolve()
            if not full_path.is_relative_to(static_root):
                logger.warning(f"Directory traversal attempt: {file_path}")
                return web.Response(status=403)
        except Exception:
            return web.Response(status=400)

        # Check if file exists
        if not full_path.exists() or not full_path.is_file():
            # Another extension's asset ("/static/<ext_id>/...") can land on
            # this catch-all when it was registered before that extension's
            # own static route (aiohttp matches in registration order).
            alt = self._resolve_extension_asset(file_path)
            if alt is None:
                return web.Response(status=404)
            full_path = alt

        # Determine content type
        content_type = self._get_content_type(full_path)
        # No version query on any asset URL, so nothing here may be cached
        # without revalidation (a stale JS/CSS copy would survive an update).
        headers = {'Cache-Control': 'no-cache'}

        try:
            # Read and serve file (V8: async to_thread I/O, no event-loop block)
            if content_type.startswith('text/') or content_type in ['application/javascript', 'application/json']:
                content = await asyncio.to_thread(full_path.read_text, encoding='utf-8')
                return web.Response(
                    text=content,
                    content_type=content_type,
                    charset='utf-8',
                    headers=headers
                )
            else:
                content = await asyncio.to_thread(full_path.read_bytes)
                return web.Response(
                    body=content,
                    content_type=content_type,
                    headers=headers
                )
        except Exception as e:
            logger.error(f"Error serving static file {file_path}: {e}")
            return web.Response(status=500)

    @staticmethod
    def _resolve_extension_asset(file_path: str) -> Optional[Path]:
        """Resolve ``<ext_id>/<rel>`` to that extension's own static dir."""
        ext_id, sep, rel = file_path.partition("/")
        if not sep or not rel:
            return None
        try:
            from boarai.core.extension.manager import get_extension_manager

            ext_dir, _src = get_extension_manager()._locate_discovered(ext_id)
        except Exception as e:  # noqa: BLE001 — degrade to 404, but say why
            logger.debug(f"Extension asset lookup failed for {ext_id!r}: {e}")
            return None
        if ext_dir is None:
            return None
        try:
            root = (Path(ext_dir) / "static").resolve()
            target = (root / rel).resolve()
            if not target.is_relative_to(root):
                logger.warning(f"Directory traversal attempt: {file_path}")
                return None
        except Exception:  # noqa: BLE001 — bad path shapes degrade to 404
            return None
        return target if target.is_file() else None

    def _get_content_type(self, file_path: Path) -> str:
        """Get content type based on file extension."""
        ext = file_path.suffix.lower()
        content_types = {
            '.html': 'text/html',
            '.css': 'text/css',
            '.js': 'application/javascript',
            '.json': 'application/json',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.svg': 'image/svg+xml',
            '.ico': 'image/x-icon',
            '.woff': 'font/woff',
            '.woff2': 'font/woff2',
            '.ttf': 'font/ttf',
            '.eot': 'application/vnd.ms-fontobject',
        }
        return content_types.get(ext, 'application/octet-stream')

    async def stop(self) -> None:
        """Stop the WebUI adapter."""
        logger.info("WebUI adapter stopping...")

        # Unsubscribe from events
        await self.event_bus.unsubscribe(EventType.SYSTEM_STARTUP, self._on_system_startup)

        # Unregister all HTTP routes (v2 API-first, legacy fallback)
        if self.api is not None:
            try:
                self.api.unregister_route("GET", "/", use_prefix=False)
                self.api.unregister_route("GET", "/static/{path:.*}", use_prefix=False)
            except Exception as e:
                logger.debug(f"WebUI route unregister: {e}")
        else:
            self.unregister_all_http_routes()

        self.set_status(ExtensionStatus.STOPPED)
        logger.info("WebUI adapter stopped")

    async def cleanup(self) -> None:
        """Cleanup WebUI adapter resources."""
        if self.status == ExtensionStatus.RUNNING:
            await self.stop()
        self.set_status(ExtensionStatus.DISCOVERED)
        logger.info("WebUI adapter cleaned up")

# Note: Extension lifecycle is managed by ExtensionLoader/ExtensionManager.
# The loader creates instances, sets event_bus, and calls initialize().
# The manager calls start()/stop() via enable_extension()/disable_extension().
# No auto-registration needed here.
