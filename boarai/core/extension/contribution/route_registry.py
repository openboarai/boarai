"""
RouteRegistry for BoarAi extension system (v2).

All extension HTTP routes go through a single middleware-backed registry.
Simple paths are exact-matched; parameterized paths (e.g. /static/{path:.*})
are prefix-matched by the middleware. This makes routes hot-addable and
hot-removable WITHOUT restarting the core server — unlike aiohttp's frozen
native router, which cannot remove registered routes after start.

Conflict detection: registering the same method+prefixed path twice raises
RouteConflictError so two extensions can never silently fight over a path.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RouteConflictError(Exception):
    """Raised when two routes claim the same method+path."""


class RouteForbiddenError(Exception):
    """Raised when an untrusted extension tries to mount a reserved path."""


# Path prefixes reserved for the core server. Untrusted extensions may NOT
# mount these without use_prefix (i.e. as a root-level route).
RESERVED_PREFIXES = ("/api/", "/ws", "/static/")


@dataclass
class RouteEntry:
    """A registered extension route."""

    method: str
    path: str          # prefixed path actually matched by middleware
    original_path: str  # path as declared by the extension
    handler: Callable[..., Any]
    extension_id: str
    has_parameters: bool = False
    description: str = ""


class RouteRegistry:
    """Middleware-backed route registry with conflict detection."""

    def __init__(self) -> None:
        # method -> {path -> RouteEntry}; parameterized routes stored too
        # with has_parameters=True and matched by prefix.
        self._routes: Dict[str, Dict[str, RouteEntry]] = {}

    def register(self, method: str, path: str, handler: Callable[..., Any],
                 extension_id: str, description: str = "",
                 use_prefix: bool = True, trusted: bool = False) -> str:
        """Register a route. Returns the prefixed path actually mounted."""
        method = method.upper()
        if not path.startswith("/"):
            path = "/" + path
        prefixed = f"/api/ext/{extension_id}{path}" if use_prefix else path

        # Reserved-prefix protection (V1 fix): untrusted extensions may not
        # claim core API / WS / static root paths. ONE exception: an extension
        # always owns its own static namespace, ``/static/<its id>/...`` — that
        # is exactly what the loader's automatic asset mount uses. Without this
        # exception that mount raised RouteForbiddenError for every user-layer
        # extension, the loader swallowed it into a warning, and their assets
        # (including any custom settings panel) 404'd.
        if not use_prefix and not trusted:
            lowered = prefixed.lower()
            own_static = lowered.startswith(f"/static/{extension_id.lower()}/")
            if not own_static and any(
                lowered.startswith(r) for r in RESERVED_PREFIXES
            ):
                raise RouteForbiddenError(
                    f"Extension '{extension_id}' may not mount reserved path "
                    f"'{prefixed}'. Root-level /api/, /ws and /static/ belong "
                    f"to the core server; use use_prefix=True, a distinct "
                    f"path, or /static/{extension_id}/ for your own assets."
                )

        has_parameters = "{" in path and "}" in path
        m = self._routes.setdefault(method, {})
        if prefixed in m:
            existing = m[prefixed]
            raise RouteConflictError(
                f"Route {method} {prefixed} already registered by "
                f"extension '{existing.extension_id}'"
            )
        m[prefixed] = RouteEntry(
            method=method, path=prefixed, original_path=path,
            handler=handler, extension_id=extension_id,
            has_parameters=has_parameters, description=description,
        )
        logger.info(
            f"Route registered: {method} {prefixed} "
            f"({extension_id}, parameters={has_parameters}, trusted={trusted})"
        )
        return prefixed

    def unregister(self, method: str, path: str, extension_id: str,
                   use_prefix: bool = True) -> bool:
        """Remove a route by its declared path."""
        method = method.upper()
        if not path.startswith("/"):
            path = "/" + path
        prefixed = f"/api/ext/{extension_id}{path}" if use_prefix else path
        m = self._routes.get(method)
        if not m:
            return False
        entry = m.get(prefixed)
        if not entry or entry.extension_id != extension_id:
            return False
        del m[prefixed]
        if not m:
            self._routes.pop(method, None)
        logger.info(f"Route unregistered: {method} {prefixed} ({extension_id})")
        return True

    def unregister_all_for(self, extension_id: str) -> int:
        """Remove all routes owned by an extension (on unload)."""
        count = 0
        for method, m in list(self._routes.items()):
            for path, entry in list(m.items()):
                if entry.extension_id == extension_id:
                    del m[path]
                    count += 1
            if not m:
                self._routes.pop(method, None)
        if count:
            logger.info(f"Unregistered {count} routes for '{extension_id}'")
        return count

    def resolve(self, method: str, path: str) -> Optional[Callable[..., Any]]:
        """Middleware hook: find a handler for (method, path)."""
        m = self._routes.get(method.upper())
        if not m:
            return None
        # Exact
        entry = m.get(path)
        if entry is not None:
            return entry.handler
        # Parameterized prefix match
        for p, entry in m.items():
            if entry.has_parameters:
                prefix = p.split("{")[0] if "{" in p else p
                if prefix and path.startswith(prefix):
                    return entry.handler
        return None

    def get_extension_routes(self, extension_id: str) -> List[Dict[str, Any]]:
        """List routes owned by an extension."""
        result = []
        for m in self._routes.values():
            for entry in m.values():
                if entry.extension_id == extension_id:
                    result.append({
                        "method": entry.method,
                        "path": entry.path,
                        "original_path": entry.original_path,
                        "description": entry.description,
                        "parameters": entry.has_parameters,
                    })
        return result

    def list_routes(self) -> List[Dict[str, Any]]:
        """List all extension routes."""
        result = []
        for m in self._routes.values():
            for entry in m.values():
                result.append({
                    "method": entry.method,
                    "path": entry.path,
                    "extension_id": entry.extension_id,
                    "parameters": entry.has_parameters,
                })
        return result

    def has_conflict(self, method: str, path: str,
                     use_prefix: bool = True) -> bool:
        """Check whether a route would conflict before registering."""
        method = method.upper()
        if not path.startswith("/"):
            path = "/" + path
        # Determine prefixed path: /api/ext/<id> requires extension_id, which
        # we don't have here, so for use_prefix=True we conservatively check
        # the declared path among all prefixed routes' original_paths.
        if use_prefix:
            for m in self._routes.values():
                for entry in m.values():
                    if entry.original_path == path:
                        return True
            return False
        m = self._routes.get(method)
        return bool(m and path in m)


_route_registry: Optional[RouteRegistry] = None


def get_route_registry() -> RouteRegistry:
    """Get the global RouteRegistry singleton."""
    global _route_registry
    if _route_registry is None:
        _route_registry = RouteRegistry()
    return _route_registry


def reset_route_registry() -> None:
    """Reset the global RouteRegistry (for testing)."""
    global _route_registry
    _route_registry = None
