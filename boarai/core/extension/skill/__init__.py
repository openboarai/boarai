"""Skill system (self-improvement, Phase 2).

A skill is a reusable named procedure (SKILL.md) the agent can discover via a
prompt-prefix index and load on demand with the ``skill_view`` tool. System
skills ship read-only under ``boar/extensions/skills/``; agent-generated skills
live writable under ``.boarai/extensions/skills/``.

Post-run self-evolution is orchestrated by ``evolution``: a deterministic
trigger gate (a size floor PLUS a recurrence/intent signal) → one isolated
authoring run fed the real trajectory → a persisted session card.

Writes go through ``manage`` and nowhere else. There is no review pass, no
staging area and no repair run: ``manage`` validates at write time and returns
its findings to the model in the same tool result, so the author fixes its own
output while the evidence is still in context.
"""

from .types import SOURCE_AGENT, SOURCE_SYSTEM, Skill
from .loader import parse_skill_md, scan_dir
from .writer import render_skill_md
from .store import SkillStore, get_skill_store
from .authoring import SKILL_AUTHORING_STANDARDS
from .evolution import (
    extract_viewed_skills,
    schedule_skill_evolution,
    skill_used_this_run,
)
from . import manage
from .validate import validate_skill_bundle

__all__ = [
    "SOURCE_AGENT",
    "SOURCE_SYSTEM",
    "Skill",
    "parse_skill_md",
    "scan_dir",
    "render_skill_md",
    "SkillStore",
    "get_skill_store",
    "SKILL_AUTHORING_STANDARDS",
    "extract_viewed_skills",
    "schedule_skill_evolution",
    "skill_used_this_run",
    "manage",
    "validate_skill_bundle",
]
