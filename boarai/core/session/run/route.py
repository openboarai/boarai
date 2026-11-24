"""Run-time intent routing for the single-agent main path.

Reuses ThinkingRouter keyword rules (no extra LLM round-trip).
Produces a thin RunRouteDecision consumed by ContextPrep / RunExecutor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from boarai.core.session.thinking.config import (
    ThinkingConfig,
    ThinkingMode,
    resolve_thinking_config,
)
from boarai.core.session.thinking.engine import ThinkingChannel, ThinkingRouter

from .messages import PLAN_CONSTRAINT

logger = __import__("logging").getLogger(__name__)


@dataclass(frozen=True)
class RunRouteDecision:
    """Resolved channel + thinking + tool policy for one Run."""

    channel: str = ThinkingChannel.STANDARD.value
    reason: str = "default_standard"
    tools_enabled: bool = True
    thinking_mode: str = ThinkingMode.NONE.value
    thinking_config: Optional[ThinkingConfig] = None
    plan_constraint: str = ""
    auto_route: bool = True


def _parse_thinking_mode(raw: Optional[str]) -> Optional[ThinkingMode]:
    """Parse a persisted/UI thinking mode."""
    if raw is None:
        return None
    s = raw.value if isinstance(raw, ThinkingMode) else str(raw)
    s = s.strip().lower()
    if not s:
        return None
    if s in ("auto", "default"):
        return ThinkingMode.AUTO
    try:
        return ThinkingMode(s)
    except ValueError:
        return None


def resolve_run_route(
    user_input: str,
    *,
    session_thinking_mode: Optional[str] = None,
    model: str = "",
    auto_route: bool = True,
    default_mode: ThinkingMode = ThinkingMode.AUTO,
    thinking_enabled: bool = True,
) -> RunRouteDecision:
    """Route one user turn for the main RunExecutor path."""
    if not thinking_enabled:
        return RunRouteDecision(
            channel=ThinkingChannel.STANDARD.value,
            reason="thinking_disabled",
            tools_enabled=True,
            thinking_mode=ThinkingMode.AUTO.value,
            thinking_config=ThinkingConfig(mode=ThinkingMode.AUTO),
            auto_route=auto_route,
        )

    session_mode = _parse_thinking_mode(session_thinking_mode)

    explicit: Optional[ThinkingMode] = None
    for candidate in (session_mode, default_mode):
        if candidate is not None and candidate != ThinkingMode.AUTO:
            explicit = candidate
            break

    router = ThinkingRouter(default_mode=ThinkingMode.AUTO, enable_auto=True)

    if not auto_route:
        # Channel auto-routing is off, but the thinking level is still the
        # user's explicit choice. The old code read default_mode here, which
        # silently turned every run off.
        thinking = resolve_thinking_config(explicit or ThinkingMode.AUTO, model or "")
        channel = (
            ThinkingChannel.PLAN
            if explicit == ThinkingMode.ULTRA
            else ThinkingChannel.STANDARD
        )
        return RunRouteDecision(
            channel=channel.value,
            reason=(
                "auto_route_disabled"
                if explicit is None
                else f"explicit_mode={explicit.value}"
            ),
            tools_enabled=True,
            thinking_mode=thinking.mode.value,
            thinking_config=thinking,
            plan_constraint=PLAN_CONSTRAINT if channel == ThinkingChannel.PLAN else "",
            auto_route=False,
        )

    if explicit is None:
        # Automatic: choose the execution channel by keywords, but send no
        # thinking field. "Default" means the model's default, not "low".
        routing = router.route(
            user_input=user_input or "",
            thinking_mode=None,
            model=model or "",
        )
        channel = routing.channel
        thinking = ThinkingConfig(mode=ThinkingMode.AUTO)
        reason = routing.reason
    else:
        channel = (
            ThinkingChannel.PLAN
            if explicit == ThinkingMode.ULTRA
            else ThinkingChannel.STANDARD
        )
        thinking = resolve_thinking_config(explicit, model or "")
        reason = f"explicit_mode={explicit.value}"

    # Tools are ALWAYS available, regardless of channel/thinking level.
    # The thinking level only controls reasoning depth — never the agent's
    # ability to call tools. Keeping tools on the wire also stabilizes the
    # prompt cache for DeepSeek-style providers.
    tools_enabled = True
    plan_constraint = PLAN_CONSTRAINT if channel == ThinkingChannel.PLAN else ""

    return RunRouteDecision(
        channel=channel.value if hasattr(channel, "value") else str(channel),
        reason=reason,
        tools_enabled=tools_enabled,
        thinking_mode=thinking.mode.value,
        thinking_config=thinking,
        plan_constraint=plan_constraint,
        auto_route=True,
    )


def load_run_config() -> dict:
    """Load run-related settings from global config (safe defaults)."""
    out = {
        "auto_route": True,
        "max_turns": 100,
        "stagnation_threshold": 3,
        "force_final_on_max": True,
        "readonly_auto_approve": False,
        "max_tool_calls_per_run": 0,  # 0 = unlimited
        # Per-run budgets; 0 = unlimited (keeps the pre-budget behaviour).
        "max_input_tokens": 0,
        "max_total_tokens": 0,
        "max_cost_usd": 0.0,
        "budget_soft_ratio": 0.8,
        # Skill-evolution overrides (merged over the worker's defaults).
        "skill_evolution": {},
        # Per-parent cap on background sub-agents; see DelegationPool.
        "max_concurrent_children": 3,
    }
    try:
        from boarai.core.config import get_config_parser

        cp = get_config_parser()
        cfg = getattr(cp, "config", None) if cp else None
        if not cfg:
            return out
        thinking = getattr(cfg, "thinking", None)
        if thinking is not None and hasattr(thinking, "auto_route"):
            val = getattr(thinking, "auto_route")
            if isinstance(val, str):
                out["auto_route"] = val.strip().lower() not in (
                    "",
                    "0",
                    "false",
                    "off",
                    "no",
                )
            else:
                out["auto_route"] = bool(val)
        run_cfg = getattr(cfg, "run", None)
        if run_cfg is not None:
            for key in list(out.keys()):
                if hasattr(run_cfg, key):
                    val = getattr(run_cfg, key)
                    if val is not None:
                        if key == "auto_route" and isinstance(val, str):
                            out[key] = val.strip().lower() not in (
                                "",
                                "0",
                                "false",
                                "off",
                                "no",
                            )
                        else:
                            out[key] = val
        sec = getattr(cfg, "security", None)
        if sec is not None:
            tg = getattr(sec, "tool_guard", None)
            if tg is not None and hasattr(tg, "max_tool_calls_per_run"):
                out["max_tool_calls_per_run"] = int(tg.max_tool_calls_per_run or 0)
    except Exception as e:
        logger.debug("load_run_config fallback: %s", e)
    return out


def resolve_max_tools(agent_config: Any, run_cfg: dict) -> int:
    """effective max tools = agent.max_tool_calls if >0 else global."""
    if agent_config is not None:
        n = int(getattr(agent_config, "max_tool_calls", 0) or 0)
        if n > 0:
            return n
    raw = run_cfg.get("max_tool_calls_per_run")
    if raw is None:
        return 0
    return max(0, int(raw))