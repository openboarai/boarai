"""Failure-derived lesson extraction (self-improvement, Phase 1).

Turns a run/tool/LLM failure into a reusable ``lesson`` memory entry so the
agent can recall "what went wrong and how to avoid it" in future sessions.

A lesson is the *negative* counterpart of a ``procedure`` (reusable positive
flow): it records trigger / root_cause / solution / avoid, distilled from a
concrete failure instead of being a step-by-step how-to.

Design constraints:
- Extraction runs AFTER the run has already failed and broadcast ``run.error``
  so it never blocks or delays the user-visible failure path.
- Best-effort: an LLM distillation is attempted; on any failure (LLM down,
  parse error, timeout) it falls back to a rule-based template so a lesson is
  still captured.
- Lessons are written to the session pod with importance ~0.7 so the existing
  ``preserve_session_memory`` migrates them to ``pod://global`` on deletion.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional

from .types import EntryType, MemoryEntry

logger = logging.getLogger(__name__)

# Lesson JSON schema the distillation prompt asks for.
_LESSON_PROMPT = (
    "You distill failures into reusable lessons so an agent never repeats the "
    "same mistake. Given the error and the surrounding context, output a single "
    "JSON object (no markdown, no commentary) with exactly these keys:\n"
    '{"topic": "short label <= 12 words", '
    '"trigger": "the situation/input that caused it", '
    '"root_cause": "the underlying reason", '
    '"solution": "what to do instead", '
    '"avoid": "what NOT to do"}.\n'
    "Be concrete and generic enough to transfer to future, similar situations. "
    "Reply with the JSON object only."
)


def _parse_lesson_json(raw: str) -> Optional[Dict[str, str]]:
    """Parse the LLM's JSON reply into a flat dict of the 5 lesson fields."""
    if not raw:
        return None
    text = raw.strip()
    # Strip ```json ... ``` fences if the model wrapped the payload.
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Find the first balanced-ish JSON object as a fallback for stray prose.
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    keys = ("topic", "trigger", "root_cause", "solution", "avoid")
    result = {k: str(data.get(k) or "").strip() for k in keys}
    if not any(result.values()):
        return None
    return result


def _render_lesson_content(fields: Dict[str, str]) -> str:
    """Render structured lesson fields into a compact, readable content body."""
    lines = []
    if fields.get("trigger"):
        lines.append(f"Trigger: {fields['trigger']}")
    if fields.get("root_cause"):
        lines.append(f"Root cause: {fields['root_cause']}")
    if fields.get("solution"):
        lines.append(f"Solution: {fields['solution']}")
    if fields.get("avoid"):
        lines.append(f"Avoid: {fields['avoid']}")
    return "\n".join(lines)


def build_lesson_entry(
    topic: str,
    content: str,
    *,
    session_id: str = "",
    agent_name: str = "default",
    channel: str = "system",
    importance: float = 0.7,
    metadata: Optional[Dict[str, Any]] = None,
) -> MemoryEntry:
    """Construct a ``lesson`` MemoryEntry bound to a session pod."""
    meta: Dict[str, Any] = {"channel": channel}
    if metadata:
        meta.update(metadata)
    return MemoryEntry(
        topic=topic,
        content=content,
        entry_type=EntryType.LESSON.value,
        importance=max(0.0, min(1.0, importance)),
        tags=["lesson", agent_name],
        metadata=meta,
        source_session=session_id,
        source_agent=agent_name,
    )


async def distill_lesson_from_failure(
    llm_service: Any,
    *,
    error: str,
    context: str = "",
    timeout: float = 20.0,
) -> Optional[Dict[str, str]]:
    """Distill a failure into structured lesson fields via the LLM."""
    import asyncio

    if llm_service is None:
        return None

    user_payload = f"Error:\n{error or '(none)'}"
    if context:
        user_payload += f"\n\nContext:\n{context[:2000]}"
    messages = [
        {"role": "system", "content": _LESSON_PROMPT},
        {"role": "user", "content": user_payload},
    ]

    try:
        response = await asyncio.wait_for(
            llm_service.chat(messages), timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.debug("Lesson distillation timed out")
        return None
    except Exception as e:  # noqa: BLE001
        logger.debug("Lesson distillation failed: %s", e)
        return None

    content = getattr(response, "content", "") or ""
    return _parse_lesson_json(content)


def template_lesson_fields(error: str, context: str = "") -> Dict[str, str]:
    """Rule-based fallback when LLM distillation is unavailable."""
    err = (error or "unknown error").strip()
    short = err[:200]
    topic = short[:80].split("\n")[0].strip() or "Recurring failure"
    trigger = (context or "similar task")[:200]
    return {
        "topic": topic,
        "trigger": trigger,
        "root_cause": short,
        "solution": "Investigate the root cause before retrying the same approach",
        "avoid": "Repeating the exact failing action without change",
    }


async def extract_lesson_entry(
    llm_service: Any,
    *,
    error: str,
    context: str = "",
    session_id: str = "",
    agent_name: str = "default",
    channel: str = "system",
    importance: float = 0.7,
) -> Optional[MemoryEntry]:
    """Distill a failure into a ``lesson`` MemoryEntry (LLM first, then template)."""
    if not error:
        return None

    fields = await distill_lesson_from_failure(
        llm_service, error=error, context=context,
    )
    if fields is None:
        fields = template_lesson_fields(error, context)

    topic = fields.get("topic") or "Recurring failure"
    content = _render_lesson_content(fields) or error[:300]
    return build_lesson_entry(
        topic,
        content,
        session_id=session_id,
        agent_name=agent_name,
        channel=channel,
        importance=importance,
        metadata={
            "source": "run_failure",
            "raw_error": error[:500],
        },
    )
