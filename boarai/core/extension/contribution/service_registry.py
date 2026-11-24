"""
ServiceRegistry for BoarAi extension system (v2).

Extension-to-extension request/response communication. An extension declares
services it provides (manifest ``contributes.services``); other extensions
call them through ``call_service`` with a timeout. This complements the
event bus (broadcast notifications) and hooks (in-flow interception) as the
third communication channel: direct, typed, audited calls.

Zero-dependency: caller and callee never reference each other's code; the
callee registers handler callables, the caller resolves by extension id +
method name through this registry.
"""

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_SERVICE_TIMEOUT = 5.0


class ServiceError(Exception):
    """Base error for service calls."""


class ServiceTimeoutError(ServiceError):
    """Raised when a service call exceeds its timeout."""


class ServiceNotFoundError(ServiceError):
    """Raised when the target extension or method is not registered."""


class ServiceRegistry:
    """Registry of extension-provided services."""

    def __init__(self) -> None:
        # extension_id -> {method_name -> handler}
        self._services: Dict[str, Dict[str, Callable[..., Any]]] = {}

    def register_service(self, extension_id: str,
                         methods: Dict[str, Callable[..., Any]]) -> None:
        """Register a service surface for an extension."""
        existing = self._services.setdefault(extension_id, {})
        for method, handler in methods.items():
            existing[method] = handler
        logger.info(
            f"Service registered: {extension_id} -> "
            f"{', '.join(methods.keys()) or '(none)'}"
        )

    def unregister_service(self, extension_id: str) -> bool:
        """Remove all services of an extension (on unload)."""
        if extension_id in self._services:
            del self._services[extension_id]
            logger.info(f"Service unregistered: {extension_id}")
            return True
        return False

    def has_service(self, extension_id: str, method: str = "") -> bool:
        services = self._services.get(extension_id)
        if not services:
            return False
        if method:
            return method in services
        return bool(services)

    def list_services(self) -> List[Dict[str, Any]]:
        return [
            {
                "extension_id": ext_id,
                "methods": sorted(methods.keys()),
            }
            for ext_id, methods in self._services.items()
        ]

    async def call(self, extension_id: str, method: str, params: Any = None,
                   *, timeout: float = DEFAULT_SERVICE_TIMEOUT) -> Any:
        """Call a service method provided by another extension."""
        services = self._services.get(extension_id)
        if not services:
            raise ServiceNotFoundError(
                f"Extension '{extension_id}' provides no services"
            )
        handler = services.get(method)
        if handler is None:
            raise ServiceNotFoundError(
                f"Extension '{extension_id}' has no service method '{method}' "
                f"(available: {', '.join(sorted(services)) or 'none'})"
            )
        try:
            if asyncio.iscoroutinefunction(handler):
                return await asyncio.wait_for(handler(params), timeout=timeout)
            return await asyncio.wait_for(
                asyncio.to_thread(handler, params), timeout=timeout,
            )
        except asyncio.TimeoutError:
            raise ServiceTimeoutError(
                f"Service call {extension_id}.{method} timed out "
                f"after {timeout}s"
            )
        except ServiceError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ServiceError(
                f"Service call {extension_id}.{method} failed: {e}"
            ) from e


_service_registry: Optional[ServiceRegistry] = None


def get_service_registry() -> ServiceRegistry:
    """Get the global ServiceRegistry singleton."""
    global _service_registry
    if _service_registry is None:
        _service_registry = ServiceRegistry()
    return _service_registry


def reset_service_registry() -> None:
    """Reset the global ServiceRegistry (for testing)."""
    global _service_registry
    _service_registry = None
