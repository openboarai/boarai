"""Run background extraction: lessons, automatic skill creation and skill
reflection (all fire-and-forget)."""
from __future__ import annotations

import logging

from typing import Any, Optional
from boarai.core.runtime import spawn
from .context_prep import RunContext

logger = logging.getLogger(__name__)



class TurnBackgroundMixin:
    """Background extraction phase of RunExecutor (mixed in via multiple inheritance)."""

    def _schedule_lesson_extraction(
        self,
        session: Any,
        run: Any,
        ctx: Optional[RunContext],
    ) -> None:
        """Best-effort: distill a failed run into a ``lesson`` memory entry."""
        error = getattr(run, "error", "") or ""
        if not session or not error:
            return
        session_id = getattr(run, "session_id", "") or ""
        agent_name = getattr(session, "agent_name", None) or "default"
        channel = getattr(session, "source", None) or "system"
        context = (getattr(ctx, "user_input", "") if ctx else "") or ""

        async def _worker() -> None:
            try:
                from boarai.core.llm import get_llm_service
                from boarai.core.memory import get_memory_manager
                from boarai.core.memory.lesson import extract_lesson_entry

                mm = get_memory_manager()
                if mm is None:
                    return
                entry = await extract_lesson_entry(
                    get_llm_service(),
                    error=error,
                    context=context,
                    session_id=session_id,
                    agent_name=agent_name,
                    channel=channel,
                )
                if entry is None:
                    return
                pod_uri = f"pod://session/{session_id}"
                await mm.remember(pod_uri, entry, publish=False)
                logger.info(
                    "Lesson recorded for failed run %s: %s",
                    getattr(run, "run_id", "?"),
                    entry.topic,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("Lesson extraction skipped: %s", e)

        try:
            spawn(_worker())
        except Exception as e:  # noqa: BLE001
            logger.debug("Lesson extraction scheduling failed: %s", e)

    def _schedule_skill_evolution(
        self,
        session: Any,
        run: Any,
        ctx: Optional[RunContext],
        metrics: Any,
    ) -> None:
        """Best-effort: post-run skill self-evolution (create OR reflect)."""
        try:
            from boarai.core.extension.skill.evolution import schedule_skill_evolution

            schedule_skill_evolution(session, run, ctx, metrics)
        except Exception as e:  # noqa: BLE001
            logger.debug("Skill evolution scheduling failed: %s", e)
