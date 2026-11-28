"""
BoarAi Core System.

This is the microkernel of BoarAi. It provides only essential
services: event system, configuration, plugin loading, session management,
prompt assembly, LLM interface, tool registration, task system, and logging.

All business logic should be implemented as plugins.
"""

from .event import EventBus, Event, EventType, get_event_bus, publish_event
from .logging import (
    LoggingConfig,
    LogLevel,
    LogManager,
    get_log_manager,
    get_logger,
)
from .dependency import get_dependency_manager

# Single version authority: pyproject.toml reads this via
# [tool.setuptools.dynamic] version = {attr = "boarai.core.__version__"},
# and the update worker verifies a pip install by having a FRESH
# interpreter print it — keep it in lock-step with the published git tags
# / PyPI releases.
__version__ = "0.1.2.10"
__author__ = "BoarAi Team"

__all__ = [
    # Event system
    "EventBus",
    "Event",
    "EventType",
    "get_event_bus",
    "publish_event",
    
    # Logging system
    "LoggingConfig",
    "LogLevel",
    "LogManager",
    "get_log_manager",
    "get_logger",
    
    # Dependency management
    "get_dependency_manager",
]