"""
Onion model pipeline for BoarAi extension system.

Implements Koa-style middleware chain using recursive async dispatch.
Middlewares wrap around the core handler like layers of an onion.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .tool.registry import ToolContext, ToolExecutionResult

logger = logging.getLogger(__name__)

# Type aliases
NextCall = Callable[[Dict[str, Any]], Awaitable[Any]]
"""next(modified_args) → call the next inner layer."""

Middleware = Callable[[str, Dict[str, Any], ToolContext, NextCall], Awaitable[Any]]
"""middleware(tool_name, args, ctx, next) → pre-process → await next(args) → post-process."""


async def execute_pipeline(
    tool_name: str,
    args: Dict[str, Any],
    ctx: ToolContext,
    middlewares: List[Middleware],
    handler: Callable[..., Any],
) -> Any:
    """Execute the onion pipeline."""
    index = -1

    async def dispatch(i: int, current_args: Dict[str, Any]) -> Any:
        nonlocal index
        if i <= index:
            raise RuntimeError("next() can only be called once per middleware!")
        index = i

        # Reached the core — call the actual handler
        if i == len(middlewares):
            if asyncio.iscoroutinefunction(handler):
                return await handler(ctx, **current_args)
            # Sync handlers must not block the event loop
            return await asyncio.to_thread(handler, ctx, **current_args)

        # Define the next() function for this layer
        async def next_call(new_args: Optional[Dict[str, Any]] = None) -> Any:
            return await dispatch(i + 1, new_args if new_args is not None else current_args)

        # Execute this middleware layer
        return await middlewares[i](tool_name, current_args, ctx, next_call)

    return await dispatch(0, args)
