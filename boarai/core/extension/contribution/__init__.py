"""
Contribution point framework for BoarAi extension system (v2).

Extension capabilities are contributed to core through typed registries:
- HookRegistry      : named hook points (core.* / ext.<name>.*) with block/transform/observe
- RouteRegistry     : HTTP routes, middleware-backed, hot add/remove without core restart
- ServiceRegistry   : extension-to-extension request/response calls

All registries are owned by core and consumed through ExtensionAPI (injected,
never imported). Extensions never hold references to core internals.
"""

from .base import ContributionPoint, ContributionError
from .hook_registry import (
    HookRegistry,
    HookPoint,
    HookBinding,
    HookResult,
    HookSemantics,
    get_hook_registry,
)
from .route_registry import (
    RouteRegistry,
    RouteEntry,
    RouteConflictError,
    RouteForbiddenError,
    get_route_registry,
)
from .service_registry import (
    ServiceRegistry,
    ServiceError,
    ServiceNotFoundError,
    ServiceTimeoutError,
    get_service_registry,
)

__all__ = [
    "ContributionPoint",
    "ContributionError",
    "HookRegistry",
    "HookPoint",
    "HookBinding",
    "HookResult",
    "HookSemantics",
    "get_hook_registry",
    "RouteRegistry",
    "RouteEntry",
    "RouteConflictError",
    "RouteForbiddenError",
    "get_route_registry",
    "ServiceRegistry",
    "ServiceError",
    "ServiceNotFoundError",
    "ServiceTimeoutError",
    "get_service_registry",
]
