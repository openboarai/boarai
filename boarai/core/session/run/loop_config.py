"""Injectable configuration and loop state for the run loop.

``RunExecutor.execute()`` used to be a single 1593-line method: roughly 40 local
variables carried state across six responsibilities, and every dependency came
from a module-level singleton (session manager / llm service / hook registry /
memory manager / skill store / event bus). Together that meant driving one
conversation required assembling the whole runtime first, so the core loop
stayed at zero coverage.

This module separates the two concerns:

``RunLoopConfig``
    Every outbound side effect and service dependency of execute(), fully
    injectable. The WebUI wires the real implementations through
    :func:`build_default_config`; an alternate runtime swaps ``broadcast`` for a
    stdout writer and ``save_session`` for a no-op and reuses the same loop;
    tests inject doubles instead of monkeypatching module globals.

``RunLoopState`` / ``TurnRequest`` / ``TurnFrame``
    The mutable state of the loop, split by lifetime into three layers: the
    whole run, one request, and one turn's streaming buffer. Once the loop is
    split into methods these variables need a home, and they must be **shared
    by reference**: the cancel and failure handlers read the values as they are
    mid-stream (``current_turn_text``, ``full_content``,
    ``pending_tool_calls``), and passing them back as return values loses data
    on the exception path (the exception travels straight through the phase
    methods, so the write-back statements never run).

``LoopStep``
    The control-flow answer a phase method gives back to the loop. The
    ``break`` / ``continue`` scattered through 1500 lines become explicit
    return values, leaving execute() with nothing but control flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)


class LoopStep(str, Enum):
    """Control flow a phase method returns to execute()."""

    NEXT = "next"
    """This turn is done; start the next one (the phase increments ``st.turn``)."""

    BREAK = "break"
    """End the whole loop."""

    FALLTHROUGH = "fallthrough"
    """This phase makes no decision; execute() applies the check that follows."""


@runtime_checkable
class BroadcastSink(Protocol):
    """The broadcast surface execute() needs."""

    async def broadcast_chunk(self, run: Any, text: str, **kw: Any) -> None: ...
    async def broadcast_thinking(self, run: Any, text: str, **kw: Any) -> None: ...
    async def broadcast_retry(self, run: Any, **kw: Any) -> None: ...
    async def broadcast_vision_downgrade(self, run: Any, **kw: Any) -> None: ...
    async def broadcast_diff(self, run: Any, payload: Dict[str, Any]) -> None: ...
    async def broadcast_tool_event(
        self, run: Any, event: str, tool_calls: Any, **kw: Any
    ) -> None: ...
    async def broadcast_to_ws(self, run_id: str, event: Dict[str, Any]) -> None: ...
    async def flush_chunks(self, run_id: str) -> None: ...


SaveSessionFn = Callable[[str], Awaitable[Any]]
RecordUsageFn = Callable[..., Awaitable[Any]]
ExtractLessonFn = Callable[..., Awaitable[Any]]
ExtractSkillFn = Callable[..., Awaitable[Any]]
TriggerHookFn = Callable[..., Awaitable[Any]]
PrepareContextFn = Callable[[Any], Awaitable[Any]]
ExecuteToolFn = Callable[..., Awaitable[Any]]
PublishEventFn = Callable[..., Awaitable[Any]]
SpawnFn = Callable[..., Any]


@dataclass
class RunLoopConfig:
    """Every replaceable boundary of execute()."""

    # --- Six outbound side effects ---
    save_session: SaveSessionFn
    broadcast: BroadcastSink
    record_usage: RecordUsageFn
    extract_lesson: ExtractLessonFn
    extract_skill: ExtractSkillFn
    trigger_hook: TriggerHookFn
    # --- Service dependencies ---
    llm: Any
    prepare_context: PrepareContextFn
    execute_tool: ExecuteToolFn
    publish_event: PublishEventFn
    fallback_machine: Callable[[], Any]
    spawn_background: SpawnFn


@dataclass
class RunLoopState:
    """Mutable state of one ``execute()`` call (run lifetime)."""

    run: Any
    # --- Produced by prepare_context (defaults survive a failure) ---
    ctx: Any = None
    session: Any = None
    model: Optional[str] = None
    agent_name: str = "default"
    agent_config: Any = None
    user_input: str = ""
    last_user_input: str = ""
    last_assistant_response: str = ""
    memory_horizon: str = ""
    proactive_memory: str = ""
    task_state: Any = None
    task_state_mgr: Any = None
    assembler: Any = None
    ext_mgr: Any = None
    tool_reg: Any = None
    max_turns: int = 100
    max_tools: int = 0
    term_cfg: Any = None
    turn_state: Any = None
    metrics: Any = None
    # --- Routing and approval ---
    tools_enabled: bool = True
    wire_tools: bool = True
    pending_inject: str = ""
    readonly_auto: bool = False
    # --- Loop counters ---
    turn: int = 0
    turn_budget: int = 0
    silence_detector: Any = None
    attach_files_dir: Any = None
    # --- Streaming accumulators (read by cancel/failure handlers, so live, not
    # --- written back at the end)
    full_content: str = ""
    current_turn_text: str = ""
    chunk_count: int = 0
    pending_tool_calls: Optional[List[Dict[str, Any]]] = None
    turn0_token_estimates: Any = None
    # Tool names last put on the wire for this run, in schema order.
    # Compared each turn so a change can be journaled; None means
    # nothing has been logged yet.
    wired_tool_names: Optional[List[str]] = None
    # --- File change aggregation ---
    diff_tracker: Any = field(default=None)

    def __post_init__(self) -> None:
        if self.diff_tracker is None:
            from .diff_tracker import RunDiffTracker

            self.diff_tracker = RunDiffTracker()
        if self.model is None:
            self.model = getattr(self.run, "model", None)

    def adopt(self, ctx: Any) -> None:
        """Take over the output of ``ContextPrep.prepare``."""
        self.ctx = ctx
        self.session = ctx.session
        self.model = ctx.model
        self.agent_name = ctx.agent_name
        self.agent_config = ctx.agent_config
        self.user_input = ctx.user_input
        self.last_user_input = ctx.last_user_input
        self.last_assistant_response = ctx.last_assistant_response
        self.memory_horizon = ctx.memory_horizon
        self.proactive_memory = ctx.proactive_memory
        self.task_state = ctx.task_state
        self.task_state_mgr = ctx.task_state_mgr
        self.assembler = ctx.assembler
        self.ext_mgr = ctx.ext_mgr
        self.tool_reg = ctx.tool_reg
        self.max_turns = ctx.max_turns
        self.max_tools = ctx.max_tools
        self.term_cfg = ctx.termination
        self.turn_state = ctx.turn_state
        self.metrics = ctx.metrics


@dataclass
class TurnRequest:
    """Resolved form of one LLM request (request lifetime)."""

    assembled: Any = None
    llm_tools: Optional[List[Dict[str, Any]]] = None
    provider: str = "default"
    resolved_model: Optional[str] = None
    fallback_fm: Any = None


@dataclass
class TurnFrame:
    """Streaming buffer and message anchors of a single turn (turn lifetime)."""

    think_processor: Any = None
    reasoning_mgr: Any = None
    thinking_buffer: str = ""
    text_buffer: str = ""
    thinking_started_at: Optional[float] = None
    thinking_ended_at: Optional[float] = None
    thinking_kwargs: Dict[str, Any] = field(default_factory=dict)
    finish_reason: Optional[str] = None
    usage_counted: bool = False
    # Latest usage block of this turn's stream; some relays emit several
    # (zero-filled first, real totals last), so counting waits for stream end.
    pending_turn_usage: Optional[Dict[str, Any]] = None
    chunks_since_save: int = 0
    last_stream_save: float = 0.0
    assistant_msg_idx: int = -1
    cur_msg_id: str = ""
    cur_msg_seq: int = 0
    stream_completed: bool = False
    vision_stripped: bool = False


class _LazyLLM:
    """Defer resolving the LLM service until the first call on it."""

    __slots__ = ("_executor",)

    def __init__(self, executor: Any) -> None:
        self._executor = executor

    def __getattr__(self, name: str) -> Any:
        return getattr(self._executor._llm, name)


def build_default_config(executor: Any, broadcaster: BroadcastSink) -> RunLoopConfig:
    """Wire RunExecutor's existing implementations into the default config."""
    from boarai.core.event import publish_event as _publish_event
    from boarai.core.runtime import spawn as _spawn
    from boarai.core.session.run_tools import execute_single_tool as _execute_single_tool
    from boarai.core.session.thinking import get_fallback_machine as _get_fallback_machine

    async def _save_session(session_id: str) -> Any:
        return await executor._session_manager.save_session(session_id)

    async def _extract_lesson(session: Any, run: Any, ctx: Any) -> None:
        executor._schedule_lesson_extraction(session, run, ctx)

    async def _extract_skill(session: Any, run: Any, ctx: Any, metrics: Any) -> None:
        # Skill evolution (create and reflect are mutually exclusive) goes
        # through the evolution orchestrator.
        executor._schedule_skill_evolution(session, run, ctx, metrics)

    async def _trigger_hook(name: str, payload: Any = None, timeout: float = 2.0) -> Any:
        return await executor._trigger_core_hook(name, payload, timeout=timeout)

    async def _prepare_context(run: Any) -> Any:
        return await executor._prep.prepare(run)

    return RunLoopConfig(
        save_session=_save_session,
        broadcast=broadcaster,
        record_usage=executor._record_run_cache_usage,
        extract_lesson=_extract_lesson,
        extract_skill=_extract_skill,
        trigger_hook=_trigger_hook,
        llm=_LazyLLM(executor),
        prepare_context=_prepare_context,
        execute_tool=_execute_single_tool,
        publish_event=_publish_event,
        fallback_machine=_get_fallback_machine,
        spawn_background=_spawn,
    )


__all__ = [
    "BroadcastSink",
    "LoopStep",
    "RunLoopConfig",
    "RunLoopState",
    "TurnFrame",
    "TurnRequest",
    "build_default_config",
]
