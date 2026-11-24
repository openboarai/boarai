"""
Core API module for BoarAi system.

This module provides system-level API handlers that can be used by
any channel extension (WebUI, CLI, etc.).
"""

from .session import SessionAPIHandler, get_session_api_handler
from .llm import LLMAPIHandler, get_llm_api_handler
from .auth import AuthAPIHandler, get_auth_api_handler
from .run import RunAPIHandler, get_run_api_handler
from ..task.api import TaskAPIHandler, get_task_api_handler
from .agent import AgentAPIHandler, get_agent_api_handler
from .extension import ExtensionAPIHandler, get_extension_api_handler
from .memory import MemoryAPIHandler, get_memory_api_handler

__all__ = [
    "AgentAPIHandler",
    "get_agent_api_handler",
    "AuthAPIHandler",
    "get_auth_api_handler",
    "ExtensionAPIHandler",
    "get_extension_api_handler",
    "LLMAPIHandler",
    "get_llm_api_handler",
    "MemoryAPIHandler",
    "get_memory_api_handler",
    "RunAPIHandler",
    "get_run_api_handler",
    "SessionAPIHandler",
    "get_session_api_handler",
    "TaskAPIHandler",
    "get_task_api_handler",
]
