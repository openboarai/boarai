"""SKILL.md discovery and parsing for the skill system.

Scans skill directories for ``<name>/SKILL.md`` files (Claude Code layout) and
parses their YAML frontmatter + markdown body into ``Skill`` objects.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List, Optional

from .types import Skill, SOURCE_SYSTEM

logger = logging.getLogger(__name__)

SKILL_FILENAME = "SKILL.md"

# Resource dirs under a skill that are listed (not injected) as progressive
# disclosure: the body references them; the model reads them on demand.
RESOURCE_DIRS = ("references", "scripts", "assets")


def _collect_resources(skill_dir: Path) -> List[str]:
    """List relative paths of resource files under a skill dir (for references)."""
    resources: List[str] = []
    for sub in RESOURCE_DIRS:
        sub_dir = skill_dir / sub
        if not sub_dir.is_dir():
            continue
        for p in sorted(sub_dir.rglob("*")):
            if p.is_file():
                try:
                    resources.append(str(p.relative_to(skill_dir)).replace("\\", "/"))
                except ValueError:
                    continue
    return resources


def parse_skill_md(path: Path, source: str = SOURCE_SYSTEM) -> Optional[Skill]:
    """Parse a single SKILL.md file into a Skill."""
    try:
        # Strip a UTF-8 BOM (common from Windows editors) — it would defeat
        # the startswith("---") frontmatter check below and silently drop
        # the skill to the dir-name fallback.
        raw = path.read_text(encoding="utf-8").lstrip("\ufeff")
    except OSError as e:
        logger.debug("Failed to read skill file %s: %s", path, e)
        return None

    frontmatter: dict = {}
    body = raw
    # Parse leading YAML frontmatter block (--- ... ---).
    if raw.startswith("---"):
        lines = raw.splitlines()
        if len(lines) > 2:
            end = None
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    end = i
                    break
            if end is not None:
                fm_text = "\n".join(lines[1:end])
                try:
                    import yaml

                    parsed = yaml.safe_load(fm_text)
                    if isinstance(parsed, dict):
                        frontmatter = parsed
                    body = "\n".join(lines[end + 1:]).strip()
                except Exception as e:  # noqa: BLE001
                    logger.debug("Failed to parse frontmatter for %s: %s", path, e)

    name = str(frontmatter.get("name") or "").strip()
    description = str(frontmatter.get("description") or "").strip()

    if not name:
        # Fall back to the directory name.
        name = path.parent.name
    if not name or not description:
        # WARNING (not debug): a hand-written SKILL.md that silently drops out
        # of the index is invisible otherwise — the authoring flow relies on
        # the hot-reload watcher + this log to surface malformed files.
        logger.warning("Skill %s missing name/description — skipped", path)
        return None

    return Skill(
        name=name,
        description=description,
        body=body,
        path=str(path),
        source=source,
        resources=_collect_resources(path.parent),
    )


def scan_dir(
    root: Path,
    source: str = SOURCE_SYSTEM,
    exclude_dirs: Optional[Iterable[str]] = None,
) -> List[Skill]:
    """Scan a skill root for ``<name>/SKILL.md`` files (recursive, one level)."""
    if not root.is_dir():
        return []
    excluded = frozenset(exclude_dirs or ())
    skills: List[Skill] = []

    # Direct child dirs each holding SKILL.md (Claude Code layout).
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in excluded:
            continue
        skill_file = child / SKILL_FILENAME
        if skill_file.is_file():
            skill = parse_skill_md(skill_file, source=source)
            if skill is not None:
                skills.append(skill)
            continue
        # Also support a flat SKILL.md directly under nested dirs (one extra level).
        for nested in sorted(child.iterdir()):
            if nested.is_dir() and nested.name not in excluded and (nested / SKILL_FILENAME).is_file():
                skill = parse_skill_md(nested / SKILL_FILENAME, source=source)
                if skill is not None:
                    skills.append(skill)

    return skills
