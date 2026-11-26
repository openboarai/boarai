"""Run context bag + pre-run preparation (memory / task state / route)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from boarai.core.prompt import get_prompt_assembler, get_task_state_manager
from boarai.core.session.thinking.config import load_thinking_settings

from .route import (
    RunRouteDecision,
    load_run_config,
    resolve_max_tools,
    resolve_run_route,
)
from .termination import TerminationConfig, TurnState

logger = logging.getLogger(__name__)


@dataclass
class RunMetrics:
    """Accumulated run summary for observability (WS / events / logs)."""

    channel: str = "standard"
    route_reason: str = ""
    turns: int = 0
    tool_calls: int = 0
    tool_failures: int = 0
    stagnation_hits: int = 0
    finish_reason: str = ""
    tools_enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "channel": self.channel,
            "route_reason": self.route_reason,
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "tool_failures": self.tool_failures,
            "stagnation_hits": self.stagnation_hits,
            "finish_reason": self.finish_reason,
            "tools_enabled": self.tools_enabled,
        }


@dataclass
class RunContext:
    """Per-run prep data shared across tool-loop turns."""

    run: Any
    session: Any
    agent_name: str = "default"
    agent_config: Any = None
    model: Optional[str] = None
    user_input: str = ""
    last_user_input: str = ""
    last_assistant_response: str = ""
    memory_horizon: str = ""
    proactive_memory: str = ""
    task_state: Any = None
    task_state_mgr: Any = None
    max_turns: int = 100
    max_tools: int = 0  # 0 = unlimited
    assembler: Any = None
    ext_mgr: Any = None
    tool_reg: Any = None
    route: Optional[RunRouteDecision] = None
    run_cfg: dict = field(default_factory=dict)
    termination: Optional[TerminationConfig] = None
    turn_state: TurnState = field(default_factory=TurnState)
    metrics: RunMetrics = field(default_factory=RunMetrics)
    # mutable loop state (legacy fields kept for compatibility)
    full_content: str = ""
    chunk_count: int = 0
    pending_tool_calls: Any = None
    prev_turn_outputs: list = field(default_factory=list)
    turn0_token_estimates: Any = None


class ContextPrep:
    """Load session + weave memory + task state + intent route for a run."""

    def __init__(
        self,
        assembler: Any = None,
        extension_manager: Any = None,
        tool_registry: Any = None,
        memory_manager: Any = None,
        memory_weaver: Any = None,
    ) -> None:
        """Optional service injection (C7-11a)."""
        self._sm: Any = None
        self._assembler = assembler
        self._extension_manager = extension_manager
        self._tool_registry = tool_registry
        self._memory_manager = memory_manager
        self._memory_weaver = memory_weaver

    @property
    def _session_manager(self) -> Any:
        if self._sm is None:
            from boarai.core.session.manager import get_session_manager

            self._sm = get_session_manager()
        return self._sm

    async def prepare(self, run: Any) -> RunContext:
        session = await self._session_manager.get_session(run.session_id)
        if not session:
            raise Exception(f"Session {run.session_id} not found")

        if session.status != "active":
            session.status = "active"
            await self._session_manager.save_session(run.session_id)

        # Runs started without an explicit model (channel hosts, tasks, skill
        # evolution) ledgered "unknown" while the wire call silently used the
        # provider default. Resolve once here so usage/events/logs carry the
        # model actually requested; session.model stays the user preference.
        model = run.model or session.model or ""
        if not model:
            try:
                from boarai.core.llm import get_llm_manager

                model = get_llm_manager().default_model or ""
            except Exception:  # noqa: BLE001 — no LLM configured yet
                model = ""
        if model and model != run.model:
            run.model = model
        agent_name = getattr(run, "_agent_name", "default")
        assembler = self._assembler or get_prompt_assembler()

        from boarai.core.extension import get_extension_manager, get_tool_registry

        ext_mgr = self._extension_manager or get_extension_manager()
        tool_reg = self._tool_registry or get_tool_registry()

        agent_config = None
        try:
            from boarai.core.prompt.agent import get_agent_registry

            reg = get_agent_registry()
            agent_config = reg.get(agent_name)
        except Exception:
            pass

        memory_horizon = ""
        proactive_memory = ""
        user_input = ""
        for m in reversed(session.messages):
            if m.role == "user":
                user_input = m.content
                break

        last_user_input = ""
        last_assistant_response = ""
        found_current_user = False
        for m in reversed(session.messages):
            if m.role == "user" and not found_current_user:
                found_current_user = True
                continue
            if m.role == "user" and found_current_user:
                last_user_input = m.content
                break
            if m.role == "assistant" and found_current_user:
                last_assistant_response = m.content

        try:
            task_state_mgr = get_task_state_manager()
            task_state = task_state_mgr.load(run.session_id)
        except Exception:
            task_state_mgr = None
            task_state = None

        # v2: task-state is no longer rendered into the prompt here. Deltas are
        # emitted as incremental [BoarAi_System] user messages by the executor
        # when the goal actually changes. Keep task_state (object) + mgr for
        # that executor-side delta detection.

        turns_elapsed = len([m for m in session.messages if m.role == "user"])
        try:
            from boarai.core.memory import get_memory_manager, get_memory_weaver

            mgr = self._memory_manager or get_memory_manager()
            weaver = self._memory_weaver or get_memory_weaver()
            if mgr and weaver and user_input:
                context = f"session:{run.session_id}"
                # Auto-mount global + agent memory to this session context
                # (idempotent) so weave can actually recall stored memories.
                # Go through the provider's session hook rather than a concrete
                # backend's ensure_default_mounts: what memory prep a session
                # start needs is the backend's own decision.
                await mgr.on_session_start(run.session_id, agent_name, context)
                result = await weaver.weave(
                    context=context,
                    user_input=user_input,
                    turns_elapsed=turns_elapsed,
                )
                if result and result.knowledge_text:
                    memory_horizon = result.knowledge_text
                # v1.5 session-freeze: after the FIRST run of a session the
                # memory-horizon prefix is locked for the whole session.
                # Later runs reuse the frozen result (byte-stable prefix →
                # provider prefix cache stays hot). New memories remain
                # reachable via memory_recall tools.
                weaver.freeze_context(context)
        except ImportError:
            pass
        except Exception as e:
            logger.debug("Memory weave skipped: %s", e)

        run_cfg = load_run_config()
        default_thinking_mode, thinking_enabled = load_thinking_settings()
        session_tm = getattr(session, "thinking_mode", None)
        route = resolve_run_route(
            user_input,
            session_thinking_mode=session_tm,
            model=model or "",
            auto_route=bool(run_cfg.get("auto_route", True)),
            default_mode=default_thinking_mode,
            thinking_enabled=thinking_enabled,
        )

        max_turns = int(run_cfg.get("max_turns") or 100)
        max_tools = resolve_max_tools(agent_config, run_cfg)
        term = TerminationConfig(
            max_turns=max_turns,
            max_tools=max_tools,
            stagnation_threshold=int(run_cfg.get("stagnation_threshold") or 3),
            force_final_on_max=bool(run_cfg.get("force_final_on_max", True)),
            max_input_tokens=int(run_cfg.get("max_input_tokens") or 0),
            max_total_tokens=int(run_cfg.get("max_total_tokens") or 0),
            max_cost_usd=float(run_cfg.get("max_cost_usd") or 0.0),
            budget_soft_ratio=float(run_cfg.get("budget_soft_ratio") or 0.8),
        )

        metrics = RunMetrics(
            channel=route.channel,
            route_reason=route.reason,
            tools_enabled=route.tools_enabled,
        )

        logger.info(
            "Run %s prep: channel=%s reason=%s tools=%s max_turns=%s max_tools=%s",
            getattr(run, "run_id", "?"),
            route.channel,
            route.reason,
            route.tools_enabled,
            max_turns,
            max_tools,
        )

        return RunContext(
            run=run,
            session=session,
            agent_name=agent_name,
            agent_config=agent_config,
            model=model,
            user_input=user_input,
            last_user_input=last_user_input,
            last_assistant_response=last_assistant_response,
            memory_horizon=memory_horizon,
            proactive_memory=proactive_memory,
            task_state=task_state,
            task_state_mgr=task_state_mgr,
            max_turns=max_turns,
            max_tools=max_tools,
            assembler=assembler,
            ext_mgr=ext_mgr,
            tool_reg=tool_reg,
            route=route,
            run_cfg=run_cfg,
            termination=term,
            metrics=metrics,
        )