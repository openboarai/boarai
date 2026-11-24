"""
LLM protocol definitions for BoarAi system.

This module defines the abstract interfaces for LLM providers.
All LLM implementations must implement these protocols.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple, Union

from .catalog import get_context_window as _catalog_context_window

class MessageRole(str, Enum):
    """Message roles in LLM conversation."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

@dataclass
class Message:
    """Message in LLM conversation."""
    role: MessageRole
    content: Union[str, List[Dict[str, Any]]]
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    reasoning_content: Optional[str] = None
    """Assistant thinking text echoed back to the API (not visible content)."""

    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary."""
        result = {
            "role": self.role.value,
            "content": self.content,
        }
        if self.name:
            result["name"] = self.name
        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            result["tool_calls"] = self.tool_calls
        if self.reasoning_content:
            result["reasoning_content"] = self.reasoning_content
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """Create message from dictionary."""
        return cls(
            role=MessageRole(data["role"]),
            content=data.get("content") or "",
            name=data.get("name"),
            tool_call_id=data.get("tool_call_id"),
            tool_calls=data.get("tool_calls"),
            reasoning_content=data.get("reasoning_content") or data.get("thinking"),
        )

@dataclass
class LLMResponse:
    """Response from LLM provider."""
    content: str
    model: str
    provider: str
    usage: Dict[str, Any]  # token counts plus provider detail maps (reasoning/cache)
    finish_reason: str  # "stop", "length", "tool_calls", etc.
    tool_calls: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary."""
        result = {
            "content": self.content,
            "model": self.model,
            "provider": self.provider,
            "usage": self.usage,
            "finish_reason": self.finish_reason,
        }
        if self.tool_calls:
            result["tool_calls"] = self.tool_calls
        if self.metadata:
            result["metadata"] = self.metadata
        return result

@dataclass
class LLMRequest:
    """Request to LLM provider."""
    messages: List[Message]
    model: Optional[str] = None
    provider: Optional[str] = None
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    top_p: float = 1.0
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    stop: Optional[List[str]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    stream: bool = False
    metadata: Optional[Dict[str, Any]] = None
    cache_breakpoints: Optional[Dict[str, Any]] = None
    """Provider-specific cache breakpoint configuration.

    Example:
    {
        "anthropic": {
            "message_indices": [4],   # Apply cache_control to messages[4]
            "tool_last": True,          # Apply cache_control to last tool
        }
    }
    """
    reasoning_effort: Optional[str] = None
    """API reasoning_effort: low|medium|high|xhigh|none|off; none/off = thinking off."""
    thinking_budget: Optional[int] = None
    """Legacy Anthropic budget_tokens; prefer reasoning_effort + adaptive thinking."""
    on_retry: Optional[Callable[..., Any]] = field(
        default=None,
        repr=False,
        compare=False,
    )
    """Optional callback before a provider retry.

    Signature (keyword args)::
        on_retry(attempt, max_attempts, error, delay_seconds)

    - attempt: 1-based retry index (1 = first retry after initial failure)
    - max_attempts: configured max_retries (how many retries are allowed)
    - error: short error string
    - delay_seconds: backoff before the next try

    May be sync or async; providers await coroutines. Not serialized in to_dict.
    """

    def to_dict(self) -> Dict[str, Any]:
        """Convert request to dictionary."""
        result = {
            "messages": [msg.to_dict() for msg in self.messages],
            "temperature": self.temperature,
            "top_p": self.top_p,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
            "stream": self.stream,
        }
        if self.model:
            result["model"] = self.model
        if self.provider:
            result["provider"] = self.provider
        if self.max_tokens:
            result["max_tokens"] = self.max_tokens
        if self.stop:
            result["stop"] = self.stop
        if self.tools:
            result["tools"] = self.tools
        if self.tool_choice:
            result["tool_choice"] = self.tool_choice
        if self.metadata:
            result["metadata"] = self.metadata
        if self.reasoning_effort:
            result["reasoning_effort"] = self.reasoning_effort
        if self.thinking_budget:
            result["thinking_budget"] = self.thinking_budget
        return result

@dataclass
class StreamChunk:
    """A single chunk from a streaming LLM response."""
    content: str = ""
    thinking: str = ""
    """Extracted thinking content from <think> tags (set by StreamingThinkProcessor).

    This is populated AFTER the stream chunk passes through the think-block
    state machine. Carries text that was wrapped in <think> tags.
    """
    reasoning: Optional[str] = None
    """Native reasoning/reasoning_content from the API response.

    - For Anthropic: the `thinking` field in extended thinking mode
    - For OpenAI-compatible: the `reasoning_content` field (DeepSeek, Qwen, etc.)

    None = not available on this chunk.
    Empty string = available but empty.
    """
    tool_calls: Optional[List[Dict[str, Any]]] = None
    """Tool calls detected during streaming (OpenAI standard format).

    Each entry::
        {
            "id": "call_xxx",
            "type": "function",
            "function": {"name": "tool_name", "arguments": '{"key": "val"}'}
        }
    """
    finish_reason: Optional[str] = None
    """"stop", "tool_calls", "length", "end_turn", or None during streaming."""
    usage: Optional[Dict[str, Any]] = None
    """Usage stats (only on the finish chunk when available)."""

class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Get provider name."""
        pass

    @property
    @abstractmethod
    def supported_models(self) -> List[str]:
        """Get list of supported models."""
        pass

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate response from LLM."""
        pass

    @abstractmethod
    async def generate_stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:
        """Generate streaming response from LLM."""
        pass

    async def validate_connection(self) -> bool:
        """Validate connection to LLM provider."""
        return True

    async def get_model_info(self, model: str) -> Dict[str, Any]:
        """Get information about a model."""
        return {"model": model, "provider": self.name}

    async def get_available_models(self) -> List[Dict[str, Any]]:
        """Get available models from provider API."""
        return [
            {
                "id": model,
                "name": model,
                "description": "",
                "provider": self.name,
            }
            for model in self.supported_models
        ]

    def get_context_window(self, model: str) -> Optional[int]:
        """Get the context window size for a model."""
        overrides = getattr(self, '_context_window_overrides', None)
        model_key = str(model) if model is not None else ""
        if overrides and model_key in overrides:
            return overrides[model_key]
        return _catalog_context_window(model_key)

    def set_context_window_overrides(self, overrides: Dict[str, int]) -> None:
        """Set user-configured context window overrides."""
        # YAML may parse bare keys like 5.2 as float; always keep str keys.
        normalized: Dict[str, int] = {}
        for model, size in (overrides or {}).items():
            key = str(model).strip()
            if not key:
                continue
            try:
                normalized[key] = int(size)
            except (TypeError, ValueError):
                continue
        self._context_window_overrides = normalized

# HTTP statuses that are safe to retry (gateway / rate limit / transient).
RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 429, 500, 501, 502, 503, 504})

def is_retryable_http_status(status: int) -> bool:
    """Return True for transient HTTP failures worth retrying."""
    if status in RETRYABLE_HTTP_STATUS_CODES:
        return True
    # Other 5xx (e.g. 520 from CDN) are usually transient
    return 500 <= int(status) < 600

class LLMError(Exception):
    """LLM related errors."""

    def __init__(
        self,
        message: str,
        provider: str = "",
        model: str = "",
        status_code: Optional[int] = None,
        retryable: Optional[bool] = None,
    ):
        """Initialize LLM error."""
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.status_code = status_code
        if retryable is not None:
            self.retryable = bool(retryable)
        elif status_code is not None:
            self.retryable = is_retryable_http_status(status_code)
        else:
            self.retryable = False
        # Set by HTTPProviderBase._http_error when the upstream rejected
        # multimodal blocks; the executor retries the turn without images.
        # Declared here so callers never have to use getattr defensively.
        self.vision_rejected = False
