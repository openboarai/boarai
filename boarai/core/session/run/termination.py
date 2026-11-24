"""Termination policy for the single-agent tool loop.

Pure decision: TurnState + config → TerminateAction.
Side effects (inject messages, break loop) stay in RunExecutor.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from .messages import (
    BUDGET_FORCE_FINAL,
    MAX_TOOLS_FORCE_FINAL,
    MAX_TURNS_FORCE_FINAL,
    STAGNATION_FORCE_FINAL,
    TEXT_STAGNATION_ABORT,
)


class TerminateKind(str, Enum):
    CONTINUE = "continue"
    FORCE_FINAL_TEXT = "force_final_text"
    ABORT = "abort"


@dataclass(frozen=True)
class TerminateAction:
    kind: TerminateKind = TerminateKind.CONTINUE
    reason: str = ""
    inject_message: str = ""


@dataclass
class TerminationConfig:
    max_turns: int = 100
    max_tools: int = 0  # 0 = unlimited tool calls; stagnation/max_turns still guard
    stagnation_threshold: int = 3
    force_final_on_max: bool = True
    # Per-run budgets. 0 = unlimited, so every existing run is unchanged.
    max_input_tokens: int = 0
    max_total_tokens: int = 0
    max_cost_usd: float = 0.0
    budget_soft_ratio: float = 0.8


@dataclass
class TurnState:
    """Mutable loop counters owned by the executor, read by policy."""

    turn: int = 0  # 0-based index of current loop iteration
    tool_call_total: int = 0
    tool_signatures: List[str] = field(default_factory=list)
    denied_signatures: List[str] = field(default_factory=list)
    text_signatures: List[str] = field(default_factory=list)
    stagnation_hits: int = 0
    force_final_injected: bool = False
    finish_reason: str = ""
    # Cumulative usage of this run, refreshed from run._usage_agg each turn.
    input_tokens_total: int = 0
    output_tokens_total: int = 0
    cost_usd_total: float = 0.0


_WS_RE = re.compile(r"\s+")


def _normalize_command(cmd: str) -> str:
    s = (cmd or "").strip().lower()
    s = _WS_RE.sub(" ", s)
    return s


def _stable_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(obj)


def fingerprint_tool_calls(tool_calls: Sequence[Dict[str, Any]]) -> str:
    """Fingerprint a batch of tool calls (name + normalized args)."""
    parts: List[str] = []
    for tc in tool_calls or []:
        func = tc.get("function") or {}
        name = func.get("name") or tc.get("name") or "?"
        raw_args = func.get("arguments", tc.get("arguments", "{}"))
        try:
            if isinstance(raw_args, str) and raw_args:
                args = json.loads(raw_args)
            elif isinstance(raw_args, dict):
                args = raw_args
            else:
                args = {}
        except Exception:
            args = {"_raw": str(raw_args)[:200]}

        if name == "shell_execute":
            cmd = ""
            if isinstance(args, dict):
                cmd = str(args.get("command") or args.get("cmd") or "")
            parts.append(f"shell_execute:{_normalize_command(cmd)}")
        else:
            parts.append(f"{name}:{_stable_json(args)}")

    blob = "|".join(sorted(parts))
    return hashlib.sha256(blob.encode("utf-8", errors="replace")).hexdigest()[:24]


def fingerprint_text(text: str, tail: int = 200) -> str:
    t = (text or "")[-tail:]
    return hashlib.sha256(t.encode("utf-8", errors="replace")).hexdigest()[:16]


def record_tool_batch(state: TurnState, tool_calls: Sequence[Dict[str, Any]]) -> str:
    """Append tool-batch signature; returns the signature used."""
    sig = fingerprint_tool_calls(tool_calls)
    state.tool_signatures.append(sig)
    state.tool_call_total += len(tool_calls or [])
    state.denied_signatures.clear()
    return sig


def record_text(state: TurnState, text: str) -> str:
    sig = fingerprint_text(text)
    state.text_signatures.append(sig)
    return sig


def record_denied_batch(state: TurnState, tool_calls: Sequence[Dict[str, Any]]) -> str:
    """Append the fingerprint of a batch that was rejected without executing."""
    sig = fingerprint_tool_calls(tool_calls)
    state.denied_signatures.append(sig)
    return sig


def _recent_all_same(sigs: List[str], n: int) -> bool:
    if len(sigs) < n:
        return False
    window = sigs[-n:]
    return len(set(window)) == 1


def decide_after_tools(
    state: TurnState,
    config: TerminationConfig,
) -> TerminateAction:
    """After a tool batch is executed and written to session."""
    n = max(2, int(config.stagnation_threshold or 3))

    if _recent_all_same(state.tool_signatures, n):
        state.stagnation_hits += 1
        if not state.force_final_injected:
            state.force_final_injected = True
            return TerminateAction(
                kind=TerminateKind.FORCE_FINAL_TEXT,
                reason=f"tool_stagnation_{n}x",
                inject_message=STAGNATION_FORCE_FINAL,
            )
        # Already asked for a final answer but model kept repeating tools
        return TerminateAction(
            kind=TerminateKind.ABORT,
            reason=f"tool_stagnation_{n}x_after_force_final",
        )

    if config.max_tools > 0 and state.tool_call_total >= config.max_tools:
        if config.force_final_on_max and not state.force_final_injected:
            state.force_final_injected = True
            return TerminateAction(
                kind=TerminateKind.FORCE_FINAL_TEXT,
                reason="max_tools",
                inject_message=MAX_TOOLS_FORCE_FINAL,
            )
        return TerminateAction(
            kind=TerminateKind.ABORT,
            reason="max_tools",
        )

    # turn is 0-based; after tools we continue unless next turn would exceed
    if state.turn + 1 >= config.max_turns:
        if config.force_final_on_max and not state.force_final_injected:
            state.force_final_injected = True
            return TerminateAction(
                kind=TerminateKind.FORCE_FINAL_TEXT,
                reason="max_turns",
                inject_message=MAX_TURNS_FORCE_FINAL,
            )
        return TerminateAction(kind=TerminateKind.ABORT, reason="max_turns")

    return TerminateAction(kind=TerminateKind.CONTINUE)


def decide_after_text(
    state: TurnState,
    config: TerminationConfig,
    *,
    had_tools: bool = False,
) -> TerminateAction:
    """After a text-only completion (or stop)."""
    if had_tools:
        return TerminateAction(kind=TerminateKind.CONTINUE)

    n = max(2, int(config.stagnation_threshold or 3))
    if _recent_all_same(state.text_signatures, n):
        state.stagnation_hits += 1
        return TerminateAction(
            kind=TerminateKind.ABORT,
            reason=f"text_stagnation_{n}x",
            inject_message=TEXT_STAGNATION_ABORT,
        )
    return TerminateAction(kind=TerminateKind.CONTINUE)


def decide_after_denied(
    state: TurnState,
    config: TerminationConfig,
) -> TerminateAction:
    """After a tool batch was rejected (policy / timeout / user) unexecuted."""
    n = max(2, int(config.stagnation_threshold or 3))
    if _recent_all_same(state.denied_signatures, n):
        state.stagnation_hits += 1
        return TerminateAction(
            kind=TerminateKind.ABORT,
            reason=f"repeated_denied_batch_{n}x",
        )
    return TerminateAction(kind=TerminateKind.CONTINUE)


def budget_ratio(state: TurnState, config: TerminationConfig) -> float:
    """Worst consumption ratio across the configured budgets (0 when unset)."""
    ratios: List[float] = []
    if config.max_input_tokens > 0:
        ratios.append(state.input_tokens_total / config.max_input_tokens)
    if config.max_total_tokens > 0:
        ratios.append(
            (state.input_tokens_total + state.output_tokens_total)
            / config.max_total_tokens
        )
    if config.max_cost_usd > 0:
        ratios.append(state.cost_usd_total / config.max_cost_usd)
    return max(ratios) if ratios else 0.0


def decide_after_usage(
    state: TurnState,
    config: TerminationConfig,
) -> TerminateAction:
    """After this turn's usage is aggregated."""
    ratio = budget_ratio(state, config)
    if ratio <= 0.0:
        return TerminateAction(kind=TerminateKind.CONTINUE)
    if ratio >= 1.0:
        return TerminateAction(kind=TerminateKind.ABORT, reason="budget")
    soft = config.budget_soft_ratio
    if not (0.0 < soft <= 1.0):
        soft = 0.8
    if ratio >= soft and not state.force_final_injected:
        state.force_final_injected = True
        return TerminateAction(
            kind=TerminateKind.FORCE_FINAL_TEXT,
            reason="budget_soft",
            inject_message=BUDGET_FORCE_FINAL,
        )
    return TerminateAction(kind=TerminateKind.CONTINUE)