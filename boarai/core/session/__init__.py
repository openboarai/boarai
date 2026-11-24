"""
Session management for BoarAi system.

This module provides session creation, storage, lifecycle management,
background conversation execution (Run), and the thinking/reasoning system.
"""

from .manager import Session, SessionManager, SessionMessage, get_session_manager
from .run_manager import RunManager, get_run_manager
from .run_models import Run, RunStatus
from .storage import SessionStorage, SessionStorageError
from .tree import (
    BranchChild,
    BranchPoint,
    SessionTree,
    SessionTreeError,
    fork_session,
    navigate_tree,
    read_tree,
    session_context,
)
from .artifact import ArtifactMetadata, ArtifactStore, SymbolEntry, SymbolIndex
from .workspace import (
    browse_directory,
    get_default_workspace_for_session,
    is_session_workspace_path,
    list_browse_roots,
    normalize_user_path,
    resolve_session_workspace,
    validate_workspace_path,
)
from .thinking import (
    ThinkingMode,
    ThinkingConfig,
    StreamingThinkProcessor,
    SilenceDetector,
    ThinkProcessorResult,
    ReasoningContentManager,
    ThinkingRouter,
    ThinkingChannel,
    RouterResult,
    ThinkingEngine,
    resolve_thinking_config,
    is_reasoning_model,
    SubAgentPreset,
    SubAgentProfile,
    SubAgentSession,
    get_preset,
    register_subagent_tool,
    register_subagent_result_tool,
    SUBAGENT_TOOL_SCHEMA,
    GET_SUBAGENT_RESULT_TOOL_SCHEMA,
    Delegation,
    DelegationPool,
    DelegationStatus,
    get_delegation_pool,
    reset_delegation_pool,
    FallbackConfig,
    FallbackState,
    FallbackStateMachine,
    FallbackStateEntry,
    safe_compress,
    build_continuation_messages,
    CONTINUATION_PROMPT,
    get_fallback_machine,
)

__all__ = [
    # Manager
    "Session",
    "SessionManager",
    "SessionMessage",
    "get_session_manager",

    # Run
    "Run",
    "RunManager",
    "RunStatus",
    "get_run_manager",

    # Storage
    "SessionStorage",
    "SessionStorageError",

    # Session tree (fork / branch / navigate)
    "BranchChild",
    "BranchPoint",
    "SessionTree",
    "SessionTreeError",
    "fork_session",
    "navigate_tree",
    "read_tree",
    "session_context",

    # Artifact
    "ArtifactMetadata",
    "ArtifactStore",
    "SymbolEntry",
    "SymbolIndex",

    # Workspace
    "browse_directory",
    "get_default_workspace_for_session",
    "is_session_workspace_path",
    "list_browse_roots",
    "normalize_user_path",
    "resolve_session_workspace",
    "validate_workspace_path",

    # Thinking
    "ThinkingMode",
    "ThinkingConfig",
    "StreamingThinkProcessor",
    "SilenceDetector",
    "ThinkProcessorResult",
    "ReasoningContentManager",
    "ThinkingRouter",
    "ThinkingChannel",
    "RouterResult",
    "ThinkingEngine",
    "resolve_thinking_config",
    "is_reasoning_model",

    # SubAgent
    "SubAgentPreset",
    "SubAgentProfile",
    "SubAgentSession",
    "get_preset",
    "register_subagent_tool",
    "register_subagent_result_tool",
    "SUBAGENT_TOOL_SCHEMA",
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
]