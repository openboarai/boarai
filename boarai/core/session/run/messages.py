"""System injection templates + message normalization for the single-agent run loop.

Centralize recover/reject/stagnate/limit messages so executor stays free of
scattered string literals.

Also provides normalize_messages() — ensures tool call/output pairing before
prompt assembly, analogous to Codex normalize.rs.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# --- System injection templates ---

TOOL_REJECTED = "[Tool execution rejected by user]"

TOOL_CANCELLED = (
    "[Tool execution cancelled — conversation was interrupted. "
    "Please retry if needed.]"
)

STAGNATION_FORCE_FINAL = (
    "[BoarAi_System] You have called the same tools repeatedly "
    "without making progress. Stop calling tools. "
    "Provide a final answer based on all information "
    "already gathered in this conversation."
)

TEXT_STAGNATION_ABORT = (
    "[BoarAi_System] Repeated identical text responses detected. "
    "Stop and provide a concise final answer without further tool use."
)

SILENCE_NUDGE = (
    "[BoarAi_System] The previous response contained reasoning but no visible "
    "output. Please provide your final answer now."
)

RETRY_NUDGE = (
    "[BoarAi_System] The user requested a retry of the previous turn. "
    "Provide a fresh response to the last user message above."
)

MAX_TURNS_FORCE_FINAL = (
    "[BoarAi_System] Maximum conversation turns reached. "
    "Stop calling tools. Summarize progress and give a final answer now."
)

MAX_TOOLS_FORCE_FINAL = (
    "[BoarAi_System] Maximum tool-call budget for this run is exhausted. "
    "Stop calling tools. Summarize what you have and give a final answer."
)

BUDGET_FORCE_FINAL = (
    "[BoarAi_System] This run has reached its configured token budget. "
    "Stop calling tools. Summarize what you have and give a final answer now."
)

PLAN_CONSTRAINT = (
    "\n\n[Plan Channel]\n"
    "Before large edits, outline a short numbered plan (goal → steps → risks). "
    "Then execute step by step. Prefer targeted tool use; avoid aimless search."
)

# --- Vision Downscaling Fallback ---

IMAGE_OMITTED_PLACEHOLDER = (
    "[Image attachment omitted — the active model does not support vision. "
    "The file stays attached to this session and can be read with file tools.]"
)


def has_image_blocks(messages: List[Dict[str, Any]]) -> bool:
    """Return True when any message content array carries an ``image_url`` block."""
    for msg in messages or []:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    return True
    return False


def strip_image_blocks(
    messages: List[Dict[str, Any]],
) -> "NormalizeResult":
    """Replace ``image_url`` content blocks with a text placeholder (in memory)."""
    if not messages:
        return NormalizeResult(normalized=[])
    replaced = 0
    out: List[Dict[str, Any]] = []
    for msg in messages:
        new_msg = dict(msg)
        content = new_msg.get("content")
        if isinstance(content, list):
            cleaned: List[Dict[str, Any]] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    # Keep the caption (a sibling text block) intact; replace the
                    # image block itself so the model still sees context.
                    cleaned.append({"type": "text", "text": IMAGE_OMITTED_PLACEHOLDER})
                    replaced += 1
                else:
                    cleaned.append(block)
            new_msg["content"] = cleaned
        out.append(new_msg)
    if replaced:
        logger.warning(
            "strip_image_blocks: replaced %s image block(s) for text-only model",
            replaced,
        )
    return NormalizeResult(normalized=out, added_outputs=replaced)


# --- Message Normalization ---

# Stable namespace UUID for synthetic tool_call_id generation.
# Using UUID v5 ensures identical synthetic IDs across runs for the same
# tool-name + args pair, protecting prompt cache.
NORMALIZE_NS = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef0123456789")

SYNTHETIC_OUTPUT_MSG = (
    "[BoarAi] This tool output was unavailable (tool: {tool_name})."
)


@dataclass
class NormalizeResult:
    """Result of message normalization."""
    normalized: List[Dict[str, Any]]
    added_outputs: int = 0
    removed_orphans: int = 0
    synthetic_ids: Set[str] = field(default_factory=set)


def _make_stable_tool_call_id(tc: Dict[str, Any]) -> str:
    """Generate stable UUID v5 from tool call content."""
    func = tc.get("function", {}) or {}
    name = func.get("name", "") or tc.get("name", "")
    args_str = func.get("arguments", "") or tc.get("arguments", "")
    if isinstance(args_str, dict):
        args_str = json.dumps(args_str, sort_keys=True)
    seed = f"{name}:{args_str}"
    return str(uuid.uuid5(NORMALIZE_NS, seed))


def normalize_messages(
    messages: List[Dict[str, Any]],
    *,
    synth_missing: bool = True,
    remove_orphans: bool = True,
) -> NormalizeResult:
    """Normalize message list: fill missing tool outputs + remove orphans."""
    if not messages:
        return NormalizeResult(normalized=[])

    # --- Pass 1: Build maps ---
    call_map: Dict[str, int] = {}       # tool_call_id → assistant msg idx
    output_present: Dict[str, bool] = {}  # tool_call_id → has tool output?
    synthetic_ids: Set[str] = set()

    for idx, msg in enumerate(messages):
        role = msg.get("role", "")

        if role == "assistant":
            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                tc_id = tc.get("id") or ""
                if not tc_id:
                    tc_id = _make_stable_tool_call_id(tc)
                    synthetic_ids.add(tc_id)
                    tc["id"] = tc_id  # fix in place for stability
                call_map[tc_id] = idx

        elif role == "tool":
            tc_id = msg.get("tool_call_id") or ""
            if not tc_id:
                # Some sessions may store tool_call_id in metadata
                meta = msg.get("metadata") or {}
                tc_id = meta.get("tool_call_id", "") if isinstance(meta, dict) else ""
            if tc_id:
                output_present[tc_id] = True

    # --- Pass 2: Rebuild normalized list ---
    added_outputs = 0
    removed_orphans_cnt = 0
    normalized: List[Dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "")

        # Skip orphan tool messages (has no matching call)
        if role == "tool" and remove_orphans:
            tc_id = msg.get("tool_call_id") or ""
            if not tc_id:
                meta = msg.get("metadata") or {}
                tc_id = meta.get("tool_call_id", "") if isinstance(meta, dict) else ""
            if tc_id and tc_id not in call_map:
                removed_orphans_cnt += 1
                continue
            elif not tc_id:
                # tool message without any tool_call_id — definitely an orphan
                removed_orphans_cnt += 1
                continue

        normalized.append(msg)

        # After assistant with tool_calls, insert missing outputs
        if role == "assistant" and synth_missing:
            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                tc_id = tc.get("id") or ""
                if not tc_id:
                    continue
                if tc_id not in output_present:
                    func = tc.get("function", {}) or {}
                    tool_name = func.get("name", "") or tc.get("name", "")
                    normalized.append({
                        "role": "tool",
                        "content": SYNTHETIC_OUTPUT_MSG.format(
                            tool_name=tool_name or "unknown"
                        ),
                        "tool_call_id": tc_id,
                        "metadata": {
                            "tool_call_id": tc_id,
                            "synthetic": True,
                            "tool_name": tool_name,
                        },
                    })
                    output_present[tc_id] = True  # prevent double-insert
                    added_outputs += 1

    # --- Pass 3: Log if any changes ---
    if added_outputs or removed_orphans_cnt:
        logger.info(
            "Message normalization: +%d synthetic tool outputs, "
            "-%d orphan tool messages (total %d → %d messages)",
            added_outputs, removed_orphans_cnt,
            len(messages), len(normalized),
        )

    return NormalizeResult(
        normalized=normalized,
        added_outputs=added_outputs,
        removed_orphans=removed_orphans_cnt,
        synthetic_ids=synthetic_ids,
    )