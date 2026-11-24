"""SkillStore — in-memory skill index + agent-skill CRUD + usage tracking.

Single source of truth for the skill system:

- ``system`` skills: ``boar/extensions/skills/<name>/SKILL.md`` (read-only).
- ``agent`` skills: ``.boarai/extensions/skills/<name>/SKILL.md``
  (agent-generated, writable — aligned with the two-layer extension layout).
- Deleting an agent skill moves its directory into the TrashManager recycle
  bin (recoverable until TTL expiry) — see ``delete()`` /
  ``TrashManager.trash_skill``.

Usage counts live in ``.boarai/extensions/skills/usage.json`` and drive Phase 3
curation (active vs stale detection).

Legacy note: agent skills created before the layout unification lived in
``.boarai/skills/``; ``reload()`` migrates that directory once, automatically.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from boarai.core.runtime import get_data_dir, resolve_project_root

from .loader import SKILL_FILENAME, parse_skill_md, scan_dir
from .types import MAX_NAME_LENGTH, SOURCE_AGENT, SOURCE_SYSTEM, Skill
from .writer import render_skill_md

logger = logging.getLogger(__name__)

_USAGE_FILENAME = "usage.json"

# Reserved dir name under agent_dir (kept for legacy archives created before
# the trash-based delete). Excluded from every scan so those skills never
# resurrect on reload; also rejected as a skill name.
ARCHIVED_DIR_NAME = "archived"

# Cap on the number of skills injected into the prompt index; the rest stay
# reachable via skill_list (overflow hint is rendered when truncated).
INDEX_SKILL_LIMIT = 50


class SkillStore:
    """Skill registry with lazy reload and agent-skill persistence."""

    def __init__(self) -> None:
        self._skills: Dict[str, Skill] = {}
        self._usage: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    # --- Paths ---

    @property
    def system_dir(self) -> Path:
        return resolve_project_root() / "boar" / "extensions" / "skills"

    @property
    def agent_dir(self) -> Path:
        return Path(get_data_dir()) / "extensions" / "skills"

    @property
    def legacy_agent_dir(self) -> Path:
        """Pre-unification agent-skill dir (``.boarai/skills``), migrated once."""
        return Path(get_data_dir()) / "skills"

    @property
    def archived_dir(self) -> Path:
        return self.agent_dir / ARCHIVED_DIR_NAME

    @property
    def _usage_path(self) -> Path:
        return self.agent_dir / _USAGE_FILENAME

    # --- Loading ---

    def _migrate_legacy_dir(self) -> None:
        """Move ``.boarai/skills/*`` into ``.boarai/extensions/skills/`` once."""
        legacy = self.legacy_agent_dir
        if not legacy.is_dir():
            return

        target = self.agent_dir
        try:
            target.mkdir(parents=True, exist_ok=True)
            for item in legacy.iterdir():
                if item.name == ARCHIVED_DIR_NAME and item.is_dir():
                    # Legacy archived tree: merge into the new archived dir.
                    self.archived_dir.mkdir(parents=True, exist_ok=True)
                    for sub in item.iterdir():
                        dest = self.archived_dir / sub.name
                        if dest.exists():
                            logger.warning(
                                "Legacy archived skill %s not migrated — %s "
                                "already exists",
                                sub.name,
                                dest,
                            )
                            continue
                        shutil.move(str(sub), str(dest))
                    if not any(item.iterdir()):
                        item.rmdir()
                    continue
                dest = target / item.name
                if dest.exists():
                    logger.warning(
                        "Legacy skill item %s not migrated — %s already exists",
                        item.name,
                        dest,
                    )
                    continue
                shutil.move(str(item), str(dest))
            if not any(legacy.iterdir()):
                legacy.rmdir()
            logger.info("Migrated legacy skill dir %s -> %s", legacy, target)
        except OSError as e:
            logger.warning("Legacy skill dir migration failed: %s", e)

    def reload(self) -> None:
        """Re-scan system + agent skill dirs and load usage stats."""
        self._migrate_legacy_dir()
        skills: Dict[str, Skill] = {}
        for skill in scan_dir(self.system_dir, SOURCE_SYSTEM):
            skills[skill.name] = skill
        for skill in scan_dir(
            self.agent_dir, SOURCE_AGENT, exclude_dirs=(ARCHIVED_DIR_NAME,)
        ):
            skills[skill.name] = skill  # agent overrides same-named system skill
        self._skills = skills
        self._load_usage()
        self._loaded = True
        logger.debug(
            "SkillStore reloaded: %d skills (%d system, %d agent)",
            len(skills),
            sum(1 for s in skills.values() if s.source == SOURCE_SYSTEM),
            sum(1 for s in skills.values() if s.source == SOURCE_AGENT),
        )

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self.reload()

    # --- Read ---

    def get(self, name: str) -> Optional[Skill]:
        self.ensure_loaded()
        return self._skills.get(name)

    def list_all(self) -> List[Skill]:
        """All skills (management view: includes disabled ones)."""
        self.ensure_loaded()
        return sorted(
            self._skills.values(),
            key=lambda s: (s.source != SOURCE_SYSTEM, s.name.lower()),
        )

    def list_enabled(self) -> List[Skill]:
        """Skills visible to the model (disabled skills hidden)."""
        self.ensure_loaded()
        return [s for s in self.list_all() if not s.disabled]

    def set_disabled(self, name: str, disabled: bool) -> Optional[Skill]:
        """Enable/disable a skill (any layer, incl. read-only system skills)."""
        self.ensure_loaded()
        skill = self._skills.get(name)
        if skill is None:
            return None
        rec = self._usage.setdefault(name, {})
        rec["disabled"] = bool(disabled)
        skill.disabled = bool(disabled)
        try:
            self._save_usage()
        except Exception as e:  # noqa: BLE001
            logger.debug("Usage save failed: %s", e)
        logger.info("Skill %s disabled=%s", name, disabled)
        return skill

    def read_resource(self, name: str, resource: str) -> Optional[str]:
        """Read a skill's resource file content (references/scripts/assets)."""
        self.ensure_loaded()
        skill = self._skills.get(name)
        if skill is None or not skill.path:
            return None
        skill_dir = Path(skill.path).parent
        # Only allow resources already listed (defense-in-depth) + resolve guard.
        if resource not in skill.resources:
            return None
        target = (skill_dir / resource).resolve()
        if skill_dir.resolve() not in target.parents:
            return None
        try:
            return target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def build_index_text(self) -> str:
        """Render the skill index for prompt-prefix injection (no bodies)."""
        self.ensure_loaded()
        skills = self.list_enabled()
        if not skills:
            return ""

        used = sorted(
            [s for s in skills if s.last_used_at],
            key=lambda s: s.last_used_at,
            reverse=True,
        )
        unused = sorted(
            [s for s in skills if not s.last_used_at],
            key=lambda s: s.name.lower(),
        )
        ordered = used + unused

        total = len(ordered)
        ordered = ordered[:INDEX_SKILL_LIMIT]

        lines = [
            "<available-skills>",
            "Reusable skills are listed below. To use one, call skill_view with "
            "its name to read the full step-by-step instructions.",
            "",
        ]
        lines.extend(s.index_line() for s in ordered)
        if total > INDEX_SKILL_LIMIT:
            dropped = total - INDEX_SKILL_LIMIT
            lines.append(
                f"({dropped} more skill(s) not shown — call skill_list to see "
                "them all.)"
            )
        lines.append("</available-skills>")
        return "\n".join(lines)

    # --- Usage ---

    def _bump(self, name: str, key: str) -> None:
        """Increment a usage counter (use_count / view_count / patch_count)."""
        self.ensure_loaded()
        skill = self._skills.get(name)
        if skill is None:
            return
        from datetime import datetime

        now = datetime.now().isoformat()
        rec = self._usage.setdefault(
            name,
            {"use_count": 0, "view_count": 0, "patch_count": 0, "last_used_at": ""},
        )
        rec[key] = int(rec.get(key, 0)) + 1
        rec["last_used_at"] = now
        skill.use_count = int(rec.get("use_count", 0))
        skill.view_count = int(rec.get("view_count", 0))
        skill.patch_count = int(rec.get("patch_count", 0))
        skill.last_used_at = now
        try:
            self._save_usage()
        except Exception as e:  # noqa: BLE001
            logger.debug("Usage save failed: %s", e)

    def bump_usage(self, name: str) -> None:
        """Record a full skill use (use_count + view_count)."""
        self._bump(name, "use_count")
        self._bump(name, "view_count")

    def bump_view(self, name: str) -> None:
        """Record a view (body read) without a full use."""
        self._bump(name, "view_count")

    def bump_patch(self, name: str) -> None:
        """Record an edit (patch) of the skill."""
        self._bump(name, "patch_count")

    def should_reflect(self, name: str, interval: int = 3) -> bool:
        """Whether this skill is due for reflection (throttled cadence)."""
        self.ensure_loaded()
        skill = self._skills.get(name)
        if skill is None or not skill.is_writable:
            return False
        rec = self._usage.get(name, {})
        last = int(rec.get("last_reflected_use", 0))
        return skill.use_count - last >= interval

    def mark_reflected(self, name: str) -> None:
        """Record that a skill was just reflected (reset the cadence window)."""
        self.ensure_loaded()
        skill = self._skills.get(name)
        if skill is None:
            return
        rec = self._usage.setdefault(name, {})
        rec["last_reflected_use"] = int(skill.use_count)
        try:
            self._save_usage()
        except Exception as e:  # noqa: BLE001
            logger.debug("Usage save failed: %s", e)

    def _load_usage(self) -> None:
        self._usage = {}
        path = self._usage_path
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._usage = data
        except Exception as e:  # noqa: BLE001
            logger.debug("Usage load failed: %s", e)
        healed = False
        for name, skill in self._skills.items():
            rec = self._usage.get(name)
            if not rec:
                continue
            if rec.get("archived_at"):
                # Legacy migration: ``archived_at`` was written by the archiver
                # that moved unused skills to ``archived/``. That archiver is
                # gone, but a usage.json written by it may still carry the key.
                # The directory exists again, so the skill was re-created and
                # the old stats describe a dead incarnation — drop them.
                rec = {}
                self._usage[name] = rec
                healed = True
            skill.use_count = int(rec.get("use_count", 0))
            skill.view_count = int(rec.get("view_count", 0))
            skill.patch_count = int(rec.get("patch_count", 0))
            skill.last_used_at = str(rec.get("last_used_at", ""))
            skill.disabled = bool(rec.get("disabled", False))
        if healed:
            try:
                self._save_usage()
            except Exception as e:  # noqa: BLE001
                logger.debug("Usage heal save failed: %s", e)

    def _save_usage(self) -> None:
        self.agent_dir.mkdir(parents=True, exist_ok=True)
        path = self._usage_path
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._usage, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    # --- Agent-skill CRUD (writable layer only) ---

    def _validate(self, name: str, description: str) -> Optional[str]:
        """Return an error string, or None if valid."""
        import re

        if not name or not name.strip():
            return "name is required"
        if not description or not description.strip():
            return "description is required"
        if len(name) > MAX_NAME_LENGTH:
            return f"name must be <= {MAX_NAME_LENGTH} chars"
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
            return "name must be kebab-case (lowercase letters, digits, hyphens)"
        if name == ARCHIVED_DIR_NAME:
            return f"name '{ARCHIVED_DIR_NAME}' is reserved for soft-deleted skills"
        return None

    def create(
        self,
        name: str,
        description: str,
        body: str = "",
    ) -> Skill:
        """Create (or overwrite) an agent skill on disk and in the index."""
        self.ensure_loaded()
        name = name.strip()
        description = description.strip()
        err = self._validate(name, description)
        if err:
            raise ValueError(err)

        skill = Skill(
            name=name,
            description=description,
            body=(body or "").strip(),
            source=SOURCE_AGENT,
        )
        target_dir = self.agent_dir / name
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / SKILL_FILENAME
        content = render_skill_md(skill)
        tmp = target_file.with_suffix(".md.tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(target_file)

        skill.path = str(target_file)
        self._skills[name] = skill
        # A re-created name must not inherit the previous incarnation's stats:
        # a trash delete keeps usage.json, and an old last_used_at would both
        # misorder the LRU index (build_index_text sorts on it) and misreport
        # use_count for a skill nobody has used since.
        self._usage.pop(name, None)
        self._usage[name] = {
            "use_count": 0, "view_count": 0, "patch_count": 0, "last_used_at": "",
        }
        try:
            self._save_usage()
        except Exception as e:  # noqa: BLE001
            logger.debug("Usage save failed: %s", e)
        logger.info("Skill created: %s", name)
        return skill

    def update(
        self,
        name: str,
        *,
        description: Optional[str] = None,
        body: Optional[str] = None,
    ) -> Optional[Skill]:
        """Update an existing agent skill (only writable layer)."""
        self.ensure_loaded()
        skill = self._skills.get(name)
        if skill is None:
            return None
        if not skill.is_writable:
            raise ValueError(f"System skill '{name}' is read-only")
        if description is not None:
            skill.description = description.strip()
        if body is not None:
            skill.body = body.strip()

        target_file = Path(skill.path) if skill.path else (self.agent_dir / name / SKILL_FILENAME)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        content = render_skill_md(skill)
        tmp = target_file.with_suffix(".md.tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(target_file)
        skill.path = str(target_file)
        self._skills[name] = skill
        logger.info("Skill updated: %s", name)
        return skill

    def delete(self, name: str) -> Optional[Path]:
        """Remove an agent skill from disk (hard delete)."""
        self.ensure_loaded()
        skill = self._skills.get(name)
        if skill is None:
            return None
        if not skill.is_writable:
            raise ValueError(f"System skill '{name}' is read-only")

        src = Path(skill.path).parent if skill.path else (self.agent_dir / name)
        try:
            if src.is_dir():
                shutil.rmtree(src)
        except OSError as e:
            logger.error("Delete skill %s failed: %s", name, e)
            return None
        self._skills.pop(name, None)
        self._usage.pop(name, None)
        try:
            self._save_usage()
        except Exception as e:  # noqa: BLE001
            logger.debug("Usage save failed: %s", e)
        logger.info("Skill deleted: %s", name)
        return src


# --- Global singleton ---

_skill_store: Optional[SkillStore] = None


def get_skill_store() -> SkillStore:
    """Get the global SkillStore singleton."""
    global _skill_store
    if _skill_store is None:
        _skill_store = SkillStore()
    return _skill_store
