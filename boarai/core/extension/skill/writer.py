"""Serialize a Skill back into SKILL.md markdown (create / update)."""

from __future__ import annotations

from typing import Dict

from .types import Skill


def _yaml_quote(value: str) -> str:
    """Render a value as an always-double-quoted single-line YAML scalar."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", " ")
        .replace("\n", " ")
    )
    return f'"{escaped}"'


def render_skill_md(skill: Skill) -> str:
    """Render a Skill as SKILL.md (YAML frontmatter + markdown body)."""
    fm: Dict[str, str] = {
        "name": skill.name,
        "description": skill.description,
    }

    lines = ["---"]
    for key in ("name", "description"):
        lines.append(f"{key}: {_yaml_quote(fm[key])}")
    lines.append("---")
    lines.append("")
    if skill.body:
        lines.append(skill.body.strip())
    lines.append("")
    return "\n".join(lines)
