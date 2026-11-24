"""
BoarAi Tool Kernel — abstract interface + registry.

This module defines the core tool abstractions.
All concrete tool implementations live in extensions/tools/.
"""

from .base import BaseTool, tool
from .registry import (
    CompressStrategy,
    ExecutionMode,
    SecurityProfile,
    SuggestedCategory,
    Tool,
    ToolContext,
    ToolDefinition,
    ToolError,
    ToolExecutionResult,
    ToolParameter,
    ToolRegistry,
    ToolStatus,
    get_tool_registry,
    reset_tool_registry,
    resolve_execution_mode,
)

__all__ = [
    "BaseTool",
    "CompressStrategy",
    "ExecutionMode",
    "SecurityProfile",
    "SuggestedCategory",
    "Tool",
    "ToolContext",
    "ToolDefinition",
    "ToolError",
    "ToolExecutionResult",
    "ToolParameter",
    "ToolRegistry",
    "ToolStatus",
    "get_tool_registry",
    "reset_tool_registry",
    "resolve_execution_mode",
    "tool",
]
