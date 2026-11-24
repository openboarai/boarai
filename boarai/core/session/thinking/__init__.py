"""
Thinking system for BoarAi (session sub-module).

Provides thinking mode configuration, intent-based channel routing,
streaming think-block processing, silence detection for reasoning models,
and sub-agent delegation with restricted tool sets.

Architecture:
    ThinkingConfig → ThinkingMode (auto/off/light/deep/ultra) + adaptive routing
    ThinkingRouter → Intent-based channel selection (fast/standard/plan)
    StreamingThinkProcessor → Stream state machine for <think> tag extraction
    SilenceDetector → Detects reasoning-only responses and triggers prefill
    SubAgentSession → Independent sub-agent execution with restricted tools
"""

from .config import (
    ThinkingMode,
    ThinkingConfig,
    load_thinking_settings,
    normalize_thinking_mode,
    resolve_thinking_config,
    is_reasoning_model,
    REASONING_EFFORT_MAP,
    THINKING_TOKEN_BUDGET,
)
from .processor import (
    StreamingThinkProcessor,
    SilenceDetector,
    ThinkProcessorResult,
    ReasoningContentManager,
)
from .engine import (
    ThinkingRouter,
    ThinkingChannel,
    RouterResult,
    ThinkingEngine,
)
from .delegation import (
    Delegation,
    DelegationPool,
    DelegationStatus,
    get_delegation_pool,
    reset_delegation_pool,
)
from .subagent import (
    SubAgentPreset,
    SubAgentProfile,
    SubAgentSession,
    get_preset,
    register_subagent_tool,
    register_subagent_result_tool,
    GET_SUBAGENT_RESULT_TOOL_SCHEMA,
    SUBAGENT_PRESETS,
    SUBAGENT_TOOL_SCHEMA,
)
from .fallback import (
    FallbackConfig,
    FallbackState,
    FallbackStateMachine,
    FallbackStateEntry,
    safe_compress,
    build_continuation_messages,
    CONTINUATION_PROMPT,
    get_fallback_machine,
    reset_fallback_machine,
)

__all__ = [
    # Config
    "ThinkingMode",
    "ThinkingConfig",
    "load_thinking_settings",
    "normalize_thinking_mode",
    "resolve_thinking_config",
    "is_reasoning_model",
    "REASONING_EFFORT_MAP",
    "THINKING_TOKEN_BUDGET",

    # Processor
    "StreamingThinkProcessor",
    "SilenceDetector",
    "ThinkProcessorResult",
    "ReasoningContentManager",

    # Engine
    "ThinkingRouter",
    "ThinkingChannel",
    "RouterResult",
    "ThinkingEngine",

    # SubAgent
    "SubAgentPreset",
    "SubAgentProfile",
    "SubAgentSession",
    "get_preset",
    "register_subagent_tool",
    "SUBAGENT_PRESETS",
    "SUBAGENT_TOOL_SCHEMA",
    "register_subagent_result_tool",
    "GET_SUBAGENT_RESULT_TOOL_SCHEMA",

    # Background delegation
    "Delegation",
    "DelegationPool",
    "DelegationStatus",
    "get_delegation_pool",
    "reset_delegation_pool",

    # Fallback
    "FallbackConfig",
    "FallbackState",
    "FallbackStateMachine",
    "FallbackStateEntry",
    "safe_compress",
    "build_continuation_messages",
    "CONTINUATION_PROMPT",
    "get_fallback_machine",
    "reset_fallback_machine",
]
