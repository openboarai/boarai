"""
Extension system for BoarAi.

This module provides the unified extension framework including:
- Extension discovery, loading, and lifecycle management
- Channel adapters for communication (CLI, WebUI, WeChat, etc.)
- Registry system for tools and capabilities
"""

# New extension system
from .types import ExtensionType, ExtensionStatus
from .base import ExtensionBase, ExtensionError
from .manifest import ExtensionManifest
from .loader import ExtensionLoader
from .manager import ExtensionManager, get_extension_manager
from .discovery import ExtensionDiscovery

# v2: ExtensionAPI facade + contribution registries + persistent state
from .api import ExtensionAPI
from .state_store import ExtensionStateStore
from .contribution import (
    HookRegistry,
    HookPoint,
    HookBinding,
    HookResult,
    HookSemantics,
    get_hook_registry,
    RouteRegistry,
    RouteEntry,
    RouteConflictError,
    RouteForbiddenError,
    get_route_registry,
    ServiceRegistry,
    ServiceError,
    ServiceNotFoundError,
    ServiceTimeoutError,
    get_service_registry,
)

# Onion pipeline
from .pipeline import Middleware, NextCall, execute_pipeline

# Source registry
from .source_registry import SourceRegistry, get_source_registry, reset_source_registry

# Message-channel adapters (contract + host)
from .channel import (
    ChannelAdapter,
    ChannelContext,
    ChannelHost,
    ChannelRunResult,
    InboundMessage,
    OutboundMessage,
    get_channel_host,
    reset_channel_host,
)
from .tool import (
    Tool,
    ToolDefinition,
    ToolError,
    ToolExecutionResult,
    ToolParameter,
    ToolRegistry,
    ToolStatus,
    get_tool_registry,
)

__all__ = [
    # New extension system
    "ExtensionType",
    "ExtensionStatus",
    "ExtensionBase",
    "ExtensionError",
    "ExtensionManifest",
    "ExtensionLoader",
    "ExtensionManager",
    "get_extension_manager",
    "ExtensionDiscovery",
    # v2
    "ExtensionAPI",
    "ExtensionStateStore",
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
    # Onion pipeline
    "Middleware",
    "NextCall",
    "execute_pipeline",

    # Source registry
    "SourceRegistry",
    "get_source_registry",
    "reset_source_registry",
    
    # Retained components
    "ChannelAdapter",
    "ChannelContext",
    "ChannelHost",
    "ChannelRunResult",
    "InboundMessage",
    "OutboundMessage",
    "get_channel_host",
    "reset_channel_host",
    "Tool",
    "ToolDefinition",
    "ToolError",
    "ToolExecutionResult",
    "ToolParameter",
    "ToolRegistry",
    "ToolStatus",
    "get_tool_registry",
]
