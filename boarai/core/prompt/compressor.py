"""
Conversation history compressor for BoarAi — Insert-then-Compress.

Inspired by OpenClacky's message_compressor.rb.

The problem:
    Without compression, cache hit rate DECLINES as conversation grows:
    Turn 20: Z3=4000 tokens → cache hit drops to ~50%
    Turn 100: Z3=20000 tokens → cache hit drops to ~10%

The solution (Insert-then-Compress):
    1. Detect that history has grown too large (idle trigger or threshold)
    2. Build a fixed-format compression instruction (cache-friendly, tiny)
    3. Insert instruction into existing conversation (Z1+Z2 unchanged → cache HIT)
    4. LLM generates a structured summary (only ~50 new tokens computed)
    5. Replace old history with [summary + last KEEP_RAW_TURNS raw messages]

After first compression, subsequent compressions cost virtually nothing
because the compressed summary itself is cache-friendly (fixed format).

All prompt content (compress_instruction, summary_template) is loaded
from boar/prompts/compression/ by the loader module.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from .loader import COMPRESS_INSTRUCTION as _LOADED_COMPRESS_INSTRUCTION
from .loader import SUMMARY_TEMPLATE as _LOADED_SUMMARY_TEMPLATE

logger = logging.getLogger(__name__)

# ==================== Constants ====================

KEEP_RAW_TURNS = 10  # Always keep last N user/assistant turns in raw form
MAX_RAW_MESSAGES_BEFORE_COMPRESS = 20  # Compress when session has more messages than this

# Prompt content loaded from boar/prompts/compression/
SUMMARY_TEMPLATE: str = _LOADED_SUMMARY_TEMPLATE
COMPRESS_INSTRUCTION: str = _LOADED_COMPRESS_INSTRUCTION

from .token_counter import count_tokens

class InsertThenCompress:
    """Conversation history compressor using Insert-then-Compress strategy."""

    def __init__(
        self,
        keep_raw_turns: int = KEEP_RAW_TURNS,
        max_raw_messages: int = MAX_RAW_MESSAGES_BEFORE_COMPRESS,
        summary_template: Optional[str] = None,
        compress_instruction: Optional[str] = None,
    ):
        """Initialize the compressor."""
        self._keep_raw_turns = keep_raw_turns
        self._max_raw_messages = max_raw_messages
        self._summary_template = summary_template or SUMMARY_TEMPLATE
        self._compress_instruction = compress_instruction or COMPRESS_INSTRUCTION

    def should_compress(
        self,
        session_messages: List[Dict[str, str]],
        force: bool = False,
    ) -> bool:
        """Check if session history exceeds compression threshold."""
        if force:
            return True

        # Count non-system messages (user + assistant turns)
        non_system = [m for m in session_messages if m.get("role") != "system"]
        total_raw = len(non_system)

        if total_raw < self._keep_raw_turns * 2:
            return False  # Too few messages (need at least KEEP_RAW_TURNS*2)

        if total_raw >= self._max_raw_messages:
            return True  # Too many messages

        # Check token estimate for the raw history
        history_content = sum(
            count_tokens(m["content"]) for m in non_system
        )
        return history_content >= 2048  # Compress when raw history > 2K tokens

    def _parse_summary(self, llm_content: str) -> Optional[Dict[str, Any]]:
        """Parse LLM's compression response (v3 JSON schema)."""
        default = {
            "new_background": "",
            "new_decisions": "",
            "new_preferences": "",
            "referenced_files": "",
            "updated_task": None,
        }

        stripped = llm_content.strip()
        # Robust markdown code block stripping (handles ```json, ```, etc.)
        import re as _re
        stripped = _re.sub(r'^```[a-zA-Z]*\s*\n', '', stripped)
        stripped = _re.sub(r'\n```\s*$', '', stripped)

        try:
            data = json.loads(stripped)
            if isinstance(data, dict):
                result = {
                    "new_background": str(data.get("new_background", "")),
                    "new_decisions": str(data.get("new_decisions", "")),
                    "new_preferences": str(data.get("new_preferences", "")),
                    "referenced_files": str(data.get("referenced_files", "")),
                }
                ts = data.get("updated_task")
                if isinstance(ts, dict) and ts:
                    result["updated_task"] = {
                        "goal": str(ts.get("goal", "")),
                        "current_step": str(ts.get("current_step", "")),
                        "working_files": ts.get("working_files", []) if isinstance(ts.get("working_files"), list) else [],
                        "decisions": ts.get("decisions", []) if isinstance(ts.get("decisions"), list) else [],
                        "completed": ts.get("completed", []) if isinstance(ts.get("completed"), list) else [],
                        "is_blocked": bool(ts.get("is_blocked", False)),
                    }
                return result
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Compression summary JSON parse failed ({e}).")

        return None

    def render_summary(self, data: Dict[str, Any]) -> str:
        """Render structured summary into the [BoarAi_System] template."""
        ts = data.get("updated_task") or {}

        background = str(data.get("new_background", "")).strip()
        task_goal = str(ts.get("goal", "")).strip()
        task_step = str(ts.get("current_step", "")).strip()
        task_files = ", ".join(ts.get("working_files", [])).strip()
        decisions = str(data.get("new_decisions", "")).strip()
        preferences = str(data.get("new_preferences", "")).strip()

        # Check if task snapshot has any content
        has_task = bool(task_goal or task_step or task_files)

        parts: List[str] = ["[BoarAi_System] Compressed History Summary", ""]

        if background:
            parts.append("══ BACKGROUND ══")
            parts.append(background)
            parts.append("")

        if has_task:
            parts.append("══ ARCHIVED TASK SNAPSHOT ══")
            if task_goal:
                parts.append(f"Goal: {task_goal}")
            if task_step:
                parts.append(f"Step: {task_step}")
            if task_files:
                parts.append(f"Files: {task_files}")
            parts.append("")

        if decisions:
            parts.append("══ KEY DECISIONS ══")
            parts.append(decisions)
            parts.append("")

        if preferences:
            parts.append("══ USER PREFERENCES ══")
            parts.append(preferences)
            parts.append("")

        return "\n".join(parts).rstrip() + "\n"

    def make_summary(self, data: Dict[str, Any]) -> str:
        """Convenience alias for render_summary."""
        return self.render_summary(data)

    def _build_compression_messages(
        self,
        zone1_zone2: List[Dict[str, str]],
        session_messages: List[Dict[str, str]],
        instruction: str,
    ) -> List[Dict[str, str]]:
        """Build messages for the compression LLM call."""
        messages = list(zone1_zone2)
        messages.extend(session_messages)
        messages.append({"role": "user", "content": instruction})
        return messages

    async def compress(
        self,
        session_messages: List[Dict[str, str]],
        zone1_zone2: List[Dict[str, str]],
        llm_service: Any,
        model: Optional[str] = None,
        memory_context: Optional[Dict[str, str]] = None,
        task_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Execute Insert-then-Compress."""
        # Inject memory + task into instruction
        mc = memory_context or {}
        tc = task_context or {}
        instruction = self._compress_instruction
        instruction = instruction.replace(
            "{existing_decisions}", mc.get("decisions", "none")
        ).replace(
            "{existing_preferences}", mc.get("preferences", "none")
        ).replace(
            "{existing_environment}", mc.get("environment", "none")
        ).replace(
            "{task_goal}", tc.get("goal", "")
        ).replace(
            "{task_step}", tc.get("current_step", "")
        ).replace(
            "{task_files}", ", ".join(tc.get("working_files", []))
        ).replace(
            "{task_blocked}", str(tc.get("is_blocked", False))
        )

        # Build messages via shared helper (cache trick: Z1+Z2 prepended unchanged)
        messages = self._build_compression_messages(
            zone1_zone2, session_messages, instruction,
        )

        total_before = sum(count_tokens(m.get("content", "")) for m in session_messages)
        logger.info(f"Compressing {len(session_messages)} messages (~{total_before} tokens)")

        response = await llm_service.chat(
            messages=messages,
            model=model,
            temperature=0.3,
            max_tokens=1024,
        )

        data = self._parse_summary(response.content)
        if data is None:
            logger.warning("Compression discarded: LLM response was not valid JSON")
            return None

        summary = self.render_summary(data)

        total_after = count_tokens(summary)
        logger.info(
            f"Compression complete: {total_before} -> ~{total_after} tokens "
            f"({round((1 - total_after/max(total_before,1))*100, 1)}% reduction)"
        )

        return {
            "summary": summary,
            "new_background": data.get("new_background", ""),
            "new_decisions": data.get("new_decisions", ""),
            "new_preferences": data.get("new_preferences", ""),
            "updated_task": data.get("updated_task"),
            "referenced_files": data.get("referenced_files", ""),
            # Usage of the compression call itself, so the caller can ledger
            # it (compression input = Z1+Z2+full history — a major consumer
            # that used to be invisible to token statistics).
            "_usage": getattr(response, "usage", None) or {},
            "_model": getattr(response, "model", None) or model or "unknown",
        }
