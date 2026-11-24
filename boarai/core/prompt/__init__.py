"""
Prompt assembly for BoarAi.

The live path is the layered assembler: ``assembler`` (7 layers) consuming
``fragments`` / ``document``, with ``cache``, ``compressor``,
``assistant_compress``, ``task_state`` and ``timer`` as collaborators.

Removed in the 2026-10-27 dead-surface sweep: ``agent_state``,
``context_budget``, ``context_planner`` and ``renderer`` — a "v6" state-first
architecture that nothing ever consumed (only this package re-exported it).

Modules:
- assembler.py: layered prompt assembly
- fragments.py: ContextFragment interface + FragmentRegistry
- document.py: PromptDocument (provider-neutral AST)
- task_state.py: structured task state
- agent.py / agent_store.py: multi-agent registry + persistence
- cache.py: session-level prefix cache
- compressor.py: insert-then-compress conversation compression
- loader.py: prompt file loader from boar/prompts/
"""

from .agent import (
    AgentConfig,
    AgentRegistry,
    get_agent_registry,
    reset_agent_registry,
)
from .agent_store import AgentStore, get_agent_store, reset_agent_store
from .assistant_compress import (
    AssistantCompressor,
    get_assistant_compressor,
)
from .assembler import (
    AssembledPrompt,
    PromptAssembler,
    PromptError,
    get_prompt_assembler,
)
from .cache import (
    CachedPrefix,
    SessionPromptCache,
    get_session_prompt_cache,
    reset_session_prompt_cache,
)
from .compressor import InsertThenCompress
from .context_detector import (
    detect_context_window,
    get_usage_info,
)
from .document import PromptDocument, PromptSection
from .fragments import (
    ContextFragment,
    FragmentContent,
    FragmentRegistry,
)
from .loader import (
    SYSTEM_CORE_PROMPT,
    get_prompts_dir,
    reload_all,
)
from .task_state import (
    TaskState,
    TaskStateManager,
    get_task_state_manager,
)
from .timer import IdleCompressionTimer, get_idle_timer
from .token_counter import count_tokens

__all__ = [
    "AgentConfig",
    "AgentRegistry",
    "AgentStore",
    "AssembledPrompt",
    "AssistantCompressor",
    "CachedPrefix",
    "ContextFragment",
    "FragmentContent",
    "FragmentRegistry",
    "IdleCompressionTimer",
    "InsertThenCompress",
    "PromptAssembler",
    "PromptDocument",
    "PromptError",
    "PromptSection",
    "SYSTEM_CORE_PROMPT",
    "SessionPromptCache",
    "TaskState",
    "TaskStateManager",
    "count_tokens",
    "detect_context_window",
    "get_agent_registry",
    "get_agent_store",
    "get_assistant_compressor",
    "get_idle_timer",
    "get_prompt_assembler",
    "get_prompts_dir",
    "get_session_prompt_cache",
    "get_task_state_manager",
    "get_usage_info",
    "reload_all",
    "reset_agent_registry",
    "reset_agent_store",
    "reset_session_prompt_cache",
]
