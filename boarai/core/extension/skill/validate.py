"""Static validation for an agent-authored skill bundle.

The old gate returned the first problem it found, so an author had to fix them
one per round. This collects every problem in one pass and returns them
together, the way Harness² feeds a rejected proposal back verbatim.

Two scopes:

* **text only** (``skill_dir=None``): what a SKILL.md must look like anywhere.
* **bundle**: additionally, every resource the body tells the agent to open
  must be shipped with the skill. A body that references
  ``scripts/normalize.py`` without shipping it fails at the first real use,
  long after the authoring run was paid for.

Deliberately conservative on references: script invocations are always checked;
``references/`` and ``assets/`` are checked only for concrete file paths (no
wildcards, placeholders, or URLs), so illustrative names do not reject a good
skill.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)

MAX_DESCRIPTION = 200
MIN_BODY = 100

# Destructive commands: never acceptable in a skill body, no judgement needed.
BANNED_PATTERNS = (
    re.compile(r"rm\s+-rf\s+/(?:\s|$)"),
    re.compile(r"curl[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z)?sh"),
    re.compile(r"wget[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z)?sh"),
    re.compile(r"dd\s+if=\S*\s+of=/dev/"),
    re.compile(r"mkfs\."),
    re.compile(r">\s*/dev/sd[a-z]"),
)

# Resource roots a skill may carry (kept in sync with loader.RESOURCE_DIRS).
RESOURCE_ROOTS = ("references", "scripts", "assets")

_SCRIPT_REF_RE = re.compile(
    r"(?<![\w/.-])(scripts/[A-Za-z0-9_.\-]+\.(?:py|sh|js|ts))(?![\w/.-])"
)
_RESOURCE_REF_RE = re.compile(
    r"(?<![\w/.-])((?:references|assets)/[A-Za-z0-9_.\-/]+"
    r"\.(?:md|txt|csv|json|yaml|yml|py|sh|js|ts|html|css|png|jpg|jpeg|gif|webp|pdf))"
    r"(?![\w/.-])"
)
_PLACEHOLDERS = ("<", ">", "{", "}", "*", "?", "...", "$")


def _frontmatter(text: str) -> tuple[dict, str, int]:
    """(meta, body, frontmatter_end_offset); meta is {} without frontmatter."""
    # Strip a UTF-8 BOM first ('\ufeff' is not whitespace for str.lstrip in
    # Python 3) — must match loader.parse_skill_md, or a BOM file would load
    # fine but fail validation.
    stripped = text.lstrip("\ufeff").lstrip()
    m = FRONTMATTER_RE.match(stripped)
    if not m:
        return {}, stripped, 0
    meta: dict = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip('"').strip("'")
    body = stripped[m.end():].strip()
    return meta, body, m.end()


def _check_text(name: str, text: str, dir_name: Optional[str]) -> List[str]:
    errs: List[str] = []
    if not text or not text.strip():
        return ["SKILL.md is empty"]

    meta, body, _ = _frontmatter(text)
    if not meta:
        errs.append("missing YAML frontmatter (--- name/description ---)")

    fm_name = str(meta.get("name") or "")
    if not fm_name:
        errs.append("frontmatter is missing `name`")
    elif not NAME_RE.match(fm_name):
        errs.append(
            f"invalid skill name {fm_name!r} "
            "(lowercase letters, digits and single hyphens only)"
        )
    if dir_name and fm_name and fm_name != dir_name:
        errs.append(f"frontmatter name {fm_name!r} != directory name {dir_name!r}")

    desc = str(meta.get("description") or "")
    if not desc:
        errs.append("missing `description` (a skill without one is dropped at load)")
    elif len(desc) > MAX_DESCRIPTION:
        errs.append(f"description too long ({len(desc)} > {MAX_DESCRIPTION} chars)")

    if len(body) < MIN_BODY:
        errs.append(f"body too short ({len(body)} < {MIN_BODY} chars, no real steps)")

    for pat in BANNED_PATTERNS:
        if pat.search(text):
            errs.append(f"dangerous command pattern: {pat.pattern}")
    return errs


def _referenced_paths(text: str) -> Iterable[tuple[str, bool]]:
    """(relative path, must_exist) pairs referenced by the body."""
    for m in _SCRIPT_REF_RE.finditer(text):
        yield m.group(1), True
    for m in _RESOURCE_REF_RE.finditer(text):
        ref = m.group(1)
        if any(token in ref for token in _PLACEHOLDERS):
            continue
        yield ref, True


def _check_resources(
    text: str, skill_dir: Path, errs: List[str]
) -> None:
    for ref, must_exist in _referenced_paths(text):
        target = (skill_dir / ref).resolve()
        try:
            target.relative_to(skill_dir.resolve())
        except ValueError:
            errs.append(f"resource reference escapes the skill directory: {ref}")
            continue
        if must_exist and not target.is_file():
            errs.append(
                f"body references `{ref}` but that file is not shipped with the "
                f"skill; put it under `{ref.split('/', 1)[0]}/`"
            )


def validate_skill_bundle(
    name: str,
    skill_dir: Optional[Path | str],
    text: Optional[str] = None,
) -> List[str]:
    """Every problem with this skill, or ``[]``."""
    if text is None:
        if skill_dir is None:
            raise ValueError("validate_skill_bundle needs text or a skill_dir")
        path = Path(skill_dir) / "SKILL.md"
        try:
            text = path.read_text(encoding="utf-8").lstrip("\ufeff")
        except OSError as exc:
            return [f"cannot read {path.name}: {exc}"]

    dir_name = Path(skill_dir).name if skill_dir is not None else (name or None)
    errs = _check_text(name, text, dir_name)

    if skill_dir is not None:
        root = Path(skill_dir)
        if not root.is_dir():
            errs.append(f"skill directory does not exist: {root}")
        else:
            _check_resources(text, root, errs)
    return errs


def first_error(name: str, text: str) -> Optional[str]:
    """Backward-compatible single-string form used by the legacy gate."""
    errs = validate_skill_bundle(name, None, text)
    return errs[0] if errs else None


__all__ = [
    "NAME_RE",
    "BANNED_PATTERNS",
    "RESOURCE_ROOTS",
    "validate_skill_bundle",
    "first_error",
]
