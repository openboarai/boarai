"""
Configuration management for BoarAi system.

This module provides configuration loading, validation, and management.
"""

from typing import Optional

from .parser import ConfigParser, ConfigError
from .schema import (
    BoarAiConfig,
    ConfigFormat,
    LLMConfig,
    LLMProviderConfig,
    LoggingConfig,
    PluginConfig,
    RunConfig,
    SessionConfig,
    SystemConfig,
    DEFAULT_CONFIG,
)

# Global config_parser reference — set by main.py after init
_config_parser: Optional[ConfigParser] = None


def get_config_parser() -> Optional[ConfigParser]:
    """Get the global ConfigParser instance (set by main.py after system init)."""
    return _config_parser


def set_config_parser(parser: ConfigParser) -> None:
    """Set the global ConfigParser instance."""
    global _config_parser
    _config_parser = parser


__all__ = [
    "ConfigParser",
    "ConfigError",
    "BoarAiConfig",
    "ConfigFormat",
    "LLMConfig",
    "LLMProviderConfig",
    "LoggingConfig",
    "PluginConfig",
    "RunConfig",
    "SessionConfig",
    "SystemConfig",
    "DEFAULT_CONFIG",
    "get_config_parser",
    "set_config_parser",
]