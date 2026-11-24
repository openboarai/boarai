"""
Task executors for BoarAi task system.

Core built-in executors:
- ScriptExecutor: Run Python scripts in subprocess
- AIExecutor: Send prompts to LLM/Agent

Extensions can create custom executors and register them.
"""

from .base import BaseTaskExecutor, ExecContext
from .script_executor import ScriptExecutor
from .ai_executor import AIExecutor

__all__ = [
    "BaseTaskExecutor",
    "ExecContext",
    "ScriptExecutor",
    "AIExecutor",
]
