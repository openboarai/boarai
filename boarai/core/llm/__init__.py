"""
LLM abstract interface layer for BoarAi system.

This module provides the abstract interfaces and management for LLM providers.
It acts as the system's "brain" interface, defining how LLM operations are performed.

Recommended Usage:
    from boarai.core.llm import get_llm_service
    
    service = get_llm_service()
    response = await service.chat("Hello!")
    # or async for chunk in service.chat_stream("Hello!")
"""

from ..config.schema import LLMConfig, LLMProviderConfig
from .manager import LLMManager, get_llm_manager
from .protocol import (
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    Message,
    MessageRole,
)
from .service import LLMService, get_llm_service
from .providers import OpenAICompatibleProvider, AnthropicProvider

__all__ = [
    # Protocol
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "Message",
    "MessageRole",
    "LLMError",
    
    # Manager
    "LLMManager",
    "get_llm_manager",
    
    # Service (recommended entry point)
    "LLMService",
    "get_llm_service",
    
    # Config
    "LLMConfig",
    "LLMProviderConfig",
    # Providers
    "OpenAICompatibleProvider",
    "AnthropicProvider",
]
