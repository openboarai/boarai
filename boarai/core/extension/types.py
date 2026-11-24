"""
Extension type definitions for BoarAi extension system.

This module defines all extension types and statuses used in the system.
"""

from enum import Enum

class ExtensionType(str, Enum):
    """Extension type enumeration."""
    CHANNEL = "channel"
    PLUGIN = "plugin"
    TOOL = "tool"
    SKILL = "skill"
    MCP = "mcp"

class ExtensionStatus(str, Enum):
    """Extension status enumeration."""
    DISCOVERED = "discovered"
    LOADED = "loaded"
    INITIALIZED = "initialized"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    DISABLED = "disabled"
