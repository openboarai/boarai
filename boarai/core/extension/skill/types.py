"""Skill data types for the self-improvement skill system (Phase 2).

A skill is a reusable, named procedure stored in the industry-standard
``SKILL.md`` format (Anthropic Agent Skills open standard, 2025-12). The
frontmatter carries only the two standard fields — ``name`` and
``description`` — where ``description`` states *what it does and when it
applies* (the "when" semantics live in description, per the standard).

Only the index (name + description) is injected into the prompt prefix; the
full body is loaded on demand via the ``skill_view`` tool to avoid bloat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Where a skill lives.
SOURCE_SYSTEM = "system"   # boar/extensions/skills/ — read-only, shipped
SOURCE_AGENT = "agent"     # .boarai/extensions/skills/ — agent-generated, writable

# Standard frontmatter keys parsed from SKILL.md (Agent Skills spec).
FRONTMATTER_KEYS = ("name", "description")

# Industry-standard constraints (aligned with Codex / Anthropic Agent Skills).
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024

# Index-injection constraint: descriptions are truncated to this length in the
# one-line index (aligned with Hermes' SKILL_PROMPT_DESC_LIMIT), so a malformed
# long description can never bloat the injected prompt.
MAX_INDEX_DESCRIPTION_LENGTH = 60


@dataclass
class Skill:
    """A single reusable skill (SKILL.md)."""

    name: str
    description: str
    body: str = ""
    path: str = ""                 # absolute file path of SKILL.md
    source: str = SOURCE_SYSTEM    # SOURCE_SYSTEM | SOURCE_AGENT
    use_count: int = 0
    view_count: int = 0
    patch_count: int = 0
    last_used_at: str = ""
    disabled: bool = False         # disabled skills are hidden from the model
    resources: List[str] = field(default_factory=list)  # references/scripts/assets

    @property
    def is_system(self) -> bool:
        return self.source == SOURCE_SYSTEM

    @property
    def is_writable(self) -> bool:
        """Only agent-generated skills may be created/edited/archived."""
        return self.source == SOURCE_AGENT

    def index_line(self) -> str:
        """One-line index entry (injected into the prompt prefix)."""
        desc = self.description
        if len(desc) > MAX_INDEX_DESCRIPTION_LENGTH:
            desc = desc[: MAX_INDEX_DESCRIPTION_LENGTH - 3] + "..."
        return f"- **{self.name}**: {desc}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "body": self.body,
            "source": self.source,
            "use_count": self.use_count,
            "view_count": self.view_count,
            "patch_count": self.patch_count,
            "last_used_at": self.last_used_at,
            "disabled": self.disabled,
            "resources": self.resources,
        }
