"""
Base tool class and @tool decorator for BoarAi tool system.

Tools self-declare their capabilities through metadata fields:
  - security_profile: "file_read" | "file_write" | "file_delete" | "shell" | "none"
  - path_param: which parameter holds the file path (for file tools)
  - shell_param: which parameter holds the command (for shell tools)
  - compress_strategy: "file" | "shell" | "grep" | "diff" | "list" | "json" | "default"
    ("list" drives structure-aware result trimming)
  - suggested_category: "readonly" | "safe" | "dangerous"

Core uses these metadata fields — NEVER tool names — to apply policies.

Usage:
    # Option 1: Decorator (simplest)
    from boarai.core.extension.tool import tool, ToolParameter, SecurityProfile, CompressStrategy

    @tool(name="my_tool", description="Does something",
          security_profile=SecurityProfile.FILE_READ,
          path_param="path",
          compress_strategy=CompressStrategy.FILE,
          suggested_category="readonly",
          parameters=[ToolParameter(name="input", type="string")])
    async def my_tool(input: str) -> str:
        return f"Got: {input}"

    # Option 2: Subclass
    from boarai.core.extension.tool import BaseTool

    class MyTool(BaseTool):
        name = "my_tool"
        description = "Does something"
        security_profile = "file_read"
        parameters = [ToolParameter(name="input", type="string")]
        async def execute(self, input: str) -> str:
            return f"Got: {input}"
"""

import logging
from typing import Any, Callable, List, Optional

from .registry import ToolDefinition, ToolParameter, get_tool_registry

logger = logging.getLogger(__name__)


class BaseTool:
    """Abstract base class for tools."""

    name: str = ""
    description: str = ""
    parameters: List[ToolParameter] = []
    security_profile: str = "none"
    path_param: Optional[str] = None
    shell_param: Optional[str] = None
    compress_strategy: str = "default"
    suggested_category: str = "safe"

    def __init_subclass__(cls, **kwargs):
        """Auto-register tool subclasses on module import."""
        super().__init_subclass__(**kwargs)
        if cls.name:
            definition = ToolDefinition(
                name=cls.name,
                description=cls.description,
                parameters=cls.parameters,
                security_profile=cls.security_profile,
                path_param=cls.path_param,
                shell_param=cls.shell_param,
                compress_strategy=cls.compress_strategy,
                suggested_category=cls.suggested_category,
            )
            registry = get_tool_registry()
            registry.register_tool_sync(definition, cls._run)
            logger.debug(f"Auto-registered tool class: {cls.name}")

    @classmethod
    async def _run(cls, **kwargs) -> Any:
        """Wrapper that instantiates the class and calls execute()."""
        instance = cls()
        return await instance.execute(**kwargs)

    async def execute(self, **kwargs) -> Any:
        raise NotImplementedError


def tool(
    name: str,
    description: str,
    parameters: Optional[List[ToolParameter]] = None,
    security_profile: str = "none",
    path_param: Optional[str] = None,
    shell_param: Optional[str] = None,
    compress_strategy: str = "default",
    suggested_category: str = "safe",
    execution_mode: Optional[str] = None,
    llm_visible: bool = True,
):
    """Decorator to register a sync/async function as a tool."""
    def decorator(func: Callable) -> Callable:
        definition = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters or [],
            security_profile=security_profile,
            path_param=path_param,
            shell_param=shell_param,
            compress_strategy=compress_strategy,
            suggested_category=suggested_category,
            execution_mode=execution_mode,
            llm_visible=llm_visible,
        )
        registry = get_tool_registry()
        registry.register_tool_sync(definition, func)
        logger.debug(f"Registered tool via decorator: {name} (profile={security_profile})")
        return func
    return decorator
