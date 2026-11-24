"""Skill write gate — the only path that mutates the agent skill layer.

Two tiers, deliberately:

* **hard** — the write is refused when the result would be *objectively*
  broken (the loader would silently drop the file, a resource the body names
  is not shipped, the path escapes the skills root, a banned command is
  embedded) or when it is a permission question (system layer, an unattended
  fork editing content it never loaded). A refusal means "this file could not
  work", never "this file is not good enough".
* **advisory** — everything that is a matter of taste: body length, naming
  style, near-duplicates, an over-long description on an *existing* skill.
  The write SUCCEEDS and the findings ride back in ``warnings`` so the model
  decides. This mirrors hermes' linter, whose own hint reads "The write
  succeeded. These are advisory authoring-convention findings (not blockers)".

Nothing here calls an LLM: every check is deterministic, so the author gets
the exact problem list inside the same tool result and can fix it in the same
run — which is why no separate review pass or repair run exists.

Rollback contract: a hard failure after the write leaves nothing behind.
``create`` removes the directory it just made; ``update`` restores the
previous ``SKILL.md`` and every resource file it touched.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .loader import RESOURCE_DIRS, SKILL_FILENAME
from .store import ARCHIVED_DIR_NAME, get_skill_store
from .types import MAX_INDEX_DESCRIPTION_LENGTH, Skill
from .validate import validate_skill_bundle
from .writer import render_skill_md

logger = logging.getLogger(__name__)

# Whole SKILL.md cap (UTF-8 bytes of the rendered file). omp uses 64 KB for the
# same purpose; hermes allows 100 k chars. 64 KB is ~23 k tokens — past that a
# single skill body competes with the conversation for context.
MAX_SKILL_BYTES = 64_000
# Per resource file. Scripts and reference notes are larger than SKILL.md but
# still bounded; hermes caps supporting files at 1 MiB.
MAX_RESOURCE_BYTES = 256_000

# Resource roots a skill may carry (same tuple as loader.RESOURCE_DIRS).
_RESOURCE_ROOTS: Tuple[str, ...] = tuple(RESOURCE_DIRS)

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Session-artifact naming. Narrow on purpose: ``test-*`` and ``tmp-*`` are
# legitimate class-level names, so they are NOT listed here.
_SESSION_NAME_RE = re.compile(
    r"^(?:fix|debug|repair|troubleshoot)-"
    r"|(?:^|-)(?:pr|issue|ticket)-\d+"
    r"|^\d{4}-\d{2}-\d{2}"
)

# Control + format characters (zero-width, bidi overrides, BOM) and the tokens
# that could close the ``<available-skills>`` block or a markdown fence in the
# prompt prefix. Mirrors omp's ``sanitizeManagedDescription``.
_UNSAFE_DESC_RE = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u00ad\u0600-\u0605\u200b-\u200f"
    r"\u2028-\u202e\u2060-\u2064\ufeff]"
)

# validate_skill_bundle returns flat strings. Two of them are dropped rather
# than demoted: ``_advisories`` raises the same concerns with more useful
# thresholds (body < 400 chars; description > 60 chars), and reporting both
# would put two warnings for one problem in front of the model. Everything
# else it reports means the file cannot work as a skill — and any check added
# to the validator in the future lands on the hard side by default.
_DROPPED_VALIDATE_MARKERS = ("body too short", "description too long")

# Lexical overlap above which an existing skill is worth naming in a warning.
_SIMILARITY_THRESHOLD = 0.35
_SIMILARITY_TOP_N = 3

# Read marks backing the unattended read-before-write guard, keyed by run id.
# Thread boundary: written from the event-loop thread (skill_view tool) and
# read from asyncio.to_thread workers (skill_manage). Safe because every access
# is a single atomic dict/set operation and eviction runs only on the writing
# thread. Bounded so a long-lived process does not accumulate one set per run.
_READ_MARKS: Dict[str, Set[str]] = {}
_MAX_TRACKED_RUNS = 64

_HINT = "fix these and call skill_manage again"


class _Rollback(Exception):
    """Internal signal: post-write validation found a hard error."""

    def __init__(self, errors: List[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


# --- read-before-write bookkeeping -------------------------------------------

def mark_read(run_id: str, name: str) -> None:
    """Record that *run_id* loaded *name* (called by the ``skill_view`` tool)."""
    if not run_id or not name:
        return
    while len(_READ_MARKS) >= _MAX_TRACKED_RUNS and run_id not in _READ_MARKS:
        _READ_MARKS.pop(next(iter(_READ_MARKS)), None)
    _READ_MARKS.setdefault(run_id, set()).add(name)


def has_read(run_id: str, name: str) -> bool:
    return name in _READ_MARKS.get(run_id, ())


def clear_read_marks() -> None:
    """Drop every read mark (tests and run teardown)."""
    _READ_MARKS.clear()


# --- helpers -----------------------------------------------------------------

def sanitize_description(text: Any) -> str:
    """Collapse a description to one safe line for the prompt-prefix index."""
    s = _UNSAFE_DESC_RE.sub(" ", str(text or ""))
    s = s.replace("<", "").replace(">", "").replace("`", "")
    s = re.sub(r"~{2,}", "~", s)
    return re.sub(r"\s+", " ", s).strip()


def _ok(action: str, name: str, **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": True, "action": action, "name": name}
    out.update(extra)
    return out


def _refused(action: str, errors: List[str]) -> Dict[str, Any]:
    logger.debug("skill_manage(%s) refused: %s", action, "; ".join(errors))
    return {"ok": False, "action": action, "errors": errors, "hint": _HINT}


def _resolve_dir(
    store: Any, name: str, *, existing: Any = None
) -> Tuple[Path, List[str]]:
    """Skill directory plus hard errors."""
    root = Path(store.agent_dir)
    if existing is not None and getattr(existing, "path", ""):
        target = Path(existing.path).parent
    else:
        target = root / name
    try:
        resolved_root = root.resolve()
        resolved = target.resolve()
    except OSError as exc:
        return target, [f"cannot resolve the skills root: {exc}"]
    # is_relative_to (not parent equality): a nested skill dir legitimately
    # sits one level deeper. The root itself is still refused — a delete that
    # resolves to the root would remove every skill.
    if resolved == resolved_root or not resolved.is_relative_to(resolved_root):
        return target, [
            f"skill directory {resolved} escapes the skills root {resolved_root}"
        ]
    return target, []


def _check_files(
    files: Any, skill_dir: Path
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Normalize ``files`` into write plans; collect hard errors."""
    plans: List[Dict[str, Any]] = []
    errors: List[str] = []
    if files is None:
        return plans, errors
    if not isinstance(files, list):
        return plans, ["files must be a list of {path, content} objects"]

    resolved_root = skill_dir.resolve()
    for item in files:
        if not isinstance(item, dict):
            errors.append("each files[] entry must be an object with 'path' and 'content'")
            continue
        rel = str(item.get("path") or "").strip().replace("\\", "/")
        content = item.get("content")
        if not rel or content is None:
            errors.append("each files[] entry needs both 'path' and 'content'")
            continue
        if not isinstance(content, str):
            errors.append(
                f"files[] '{rel}': content must be a string (binary assets are not "
                f"supported through skill_manage)"
            )
            continue
        if rel.split("/", 1)[0] not in _RESOURCE_ROOTS:
            errors.append(
                f"files[] path '{rel}' must start with one of: "
                f"{', '.join(_RESOURCE_ROOTS)}"
            )
            continue
        target = (skill_dir / rel).resolve()
        if not target.is_relative_to(resolved_root):
            errors.append(f"files[] path '{rel}' escapes the skill directory")
            continue
        size = len(content.encode("utf-8"))
        if size > MAX_RESOURCE_BYTES:
            errors.append(
                f"files[] '{rel}' is {size} bytes (limit {MAX_RESOURCE_BYTES}); "
                f"split it or reference an external source instead"
            )
            continue
        plans.append({"rel": rel, "content": content, "target": target, "size": size})
    return plans, errors


def _split_validation(errors: List[str]) -> List[str]:
    """Hard validation errors (see ``_DROPPED_VALIDATE_MARKERS`` for the two findings that are dropped instead — ``_advisories`` covers them better)."""
    return [
        err for err in errors
        if not any(m in err for m in _DROPPED_VALIDATE_MARKERS)
    ]


def _similar_skills(store: Any, name: str, description: str) -> List[str]:
    """Near-duplicate advisory via stdlib lexical overlap (no embeddings)."""
    probe = f"{name} {description}".lower()
    scored: List[Tuple[float, str]] = []
    for skill in store.list_all():
        if skill.name == name:
            continue
        target = f"{skill.name} {skill.description}".lower()
        ratio = difflib.SequenceMatcher(None, probe, target).ratio()
        if ratio >= _SIMILARITY_THRESHOLD:
            scored.append((ratio, skill.name))
    scored.sort(reverse=True)
    if not scored:
        return []
    listed = ", ".join(f"{n} ({r:.2f})" for r, n in scored[:_SIMILARITY_TOP_N])
    return [
        f"similar skills already exist — consider action='update' on one of "
        f"them instead of creating a near-duplicate: {listed}"
    ]


def _write_resources(plans: List[Dict[str, Any]]) -> Dict[Path, Optional[str]]:
    """Write every resource; return the previous content for rollback."""
    previous: Dict[Path, Optional[str]] = {}
    for plan in plans:
        target: Path = plan["target"]
        old = None
        if target.is_file():
            try:
                old = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                old = None
        previous[target] = old
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(plan["content"], encoding="utf-8")
    return previous


def _restore_resources(previous: Dict[Path, Optional[str]]) -> None:
    for target, old in previous.items():
        try:
            if old is None:
                target.unlink(missing_ok=True)
            else:
                target.write_text(old, encoding="utf-8")
        except OSError as exc:
            logger.warning("Resource rollback failed for %s: %s", target, exc)


def _advisories(
    store: Any,
    name: str,
    description: str,
    body: str,
    *,
    shadowed_system: bool = False,
) -> List[str]:
    """Taste-level findings. The write already succeeded; these are information."""
    warnings: List[str] = []
    if shadowed_system:
        warnings.append(
            f"a system skill named '{name}' exists — this agent copy shadows it in "
            f"the index (that override is supported, but confirm it is what you meant)"
        )
    if _SESSION_NAME_RE.search(name):
        warnings.append(
            f"name '{name}' looks session-specific; a class-level name routes to "
            f"future tasks, a one-off name does not"
        )
    if len(description) > MAX_INDEX_DESCRIPTION_LENGTH:
        warnings.append(
            f"description is {len(description)} chars — the index truncates it to "
            f"{MAX_INDEX_DESCRIPTION_LENGTH - 3} chars + '...', so put the trigger "
            f"first"
        )
    if len(body.strip()) < 400:
        warnings.append(
            "body is very short; a skill that only restates the task is not worth "
            "an index slot"
        )
    warnings.extend(_similar_skills(store, name, description))
    return warnings


def _index_line(store: Any, name: str) -> str:
    skill = store.get(name)
    if skill is None:   # unreachable after a successful write; kept for safety
        return f"- **{name}**"
    return skill.index_line()


def _payload(store: Any, name: str, description: str, path: Path,
             warnings: List[str]) -> Dict[str, Any]:
    return {
        "path": str(path),
        "index_line": _index_line(store, name),
        "desc_len": len(description),
        "warnings": warnings,
    }


# --- synchronous implementations (run under asyncio.to_thread) ---------------

def _create_sync(name: str, description: str, body: str, files: Any) -> Dict[str, Any]:
    store = get_skill_store()
    store.ensure_loaded()

    errors: List[str] = []
    if not _NAME_RE.match(name or ""):
        errors.append(
            "invalid skill name: use lowercase letters, digits and single hyphens, "
            "1-64 chars, starting with a letter or digit"
        )
    if name == ARCHIVED_DIR_NAME:
        errors.append(f"'{ARCHIVED_DIR_NAME}' is reserved for soft-deleted skills")

    clean_desc = sanitize_description(description)
    if not clean_desc:
        errors.append("description is required (a skill without one is dropped at load)")
    elif len(clean_desc) > MAX_INDEX_DESCRIPTION_LENGTH:
        # Routing signal, not taste: the index truncates to 57 chars + '...', so a
        # longer description means the skill can never be selected. hermes hard-
        # rejects this on create and relaxes it on update so existing skills stay
        # maintainable.
        errors.append(
            f"description is {len(clean_desc)} chars; a NEW skill must fit the "
            f"{MAX_INDEX_DESCRIPTION_LENGTH}-char index budget (one sentence, "
            f"trigger first) or it can never be routed to"
        )
    if not (body or "").strip():
        errors.append("body is required (SKILL.md needs content after the frontmatter)")

    # Captured before the write: once the agent copy exists, store.get(name)
    # returns IT, so the system skill it shadows can no longer be observed.
    shadowed = store.get(name)
    shadowed_system = shadowed is not None and not shadowed.is_writable

    skill_dir, path_errors = _resolve_dir(store, name)
    errors.extend(path_errors)
    if errors:
        return _refused("create", errors)

    # Collision check reads the DISK, not the index: an agent copy of a system
    # skill name is a supported override, so only a real agent-layer directory
    # blocks a create. Refusing here (instead of overwriting) keeps rollback
    # trivial and pushes the model to action='update', which loads the old
    # content first.
    if skill_dir.is_dir():
        return _refused("create", [
            f"skill '{name}' already exists at {skill_dir}; "
            f"use action='update' to change it"
        ])

    rendered = render_skill_md(
        Skill(name=name, description=clean_desc, body=(body or "").strip())
    )
    size = len(rendered.encode("utf-8"))
    if size > MAX_SKILL_BYTES:
        return _refused("create", [
            f"SKILL.md would be {size} bytes (limit {MAX_SKILL_BYTES}); move detail "
            f"into references/ and keep the body to the core steps"
        ])

    plans, file_errors = _check_files(files, skill_dir)
    if file_errors:
        return _refused("create", file_errors)

    try:
        skill_dir.mkdir(parents=True, exist_ok=True)
        _write_resources(plans)
        store.create(name, clean_desc, (body or "").strip())
        # Reload so the resources just written are listed: read_resource refuses
        # anything outside Skill.resources, which is frozen at load time.
        store.reload()
        hard = _split_validation(validate_skill_bundle(name, skill_dir))
        if hard:
            raise _Rollback(hard)
    except _Rollback as roll:
        shutil.rmtree(skill_dir, ignore_errors=True)
        store.reload()
        return _refused("create", roll.errors)
    except Exception as exc:  # noqa: BLE001 — never leave a half-written skill
        # rmtree already removed every resource written in this call.
        shutil.rmtree(skill_dir, ignore_errors=True)
        store.reload()
        logger.warning("Skill create failed for %s: %s", name, exc)
        return _refused("create", [f"write failed: {exc}"])

    warnings = _advisories(
        store, name, clean_desc, body, shadowed_system=shadowed_system,
    )
    logger.info("Skill created: %s (%d warnings)", name, len(warnings))
    return _ok("create", name, **_payload(store, name, clean_desc,
                                         skill_dir / SKILL_FILENAME, warnings))


def _update_sync(
    name: str, description: str, body: str, files: Any, run_id: str,
    unattended: bool,
) -> Dict[str, Any]:
    store = get_skill_store()
    store.ensure_loaded()

    errors: List[str] = []
    if not _NAME_RE.match(name or ""):
        errors.append(
            "invalid skill name: use lowercase letters, digits and single hyphens, "
            "1-64 chars, starting with a letter or digit"
        )
    if errors:
        return _refused("update", errors)

    existing = store.get(name)
    if existing is None:
        return _refused("update", [
            f"skill '{name}' does not exist; use action='create' to add it"
        ])
    if not existing.is_writable:
        return _refused("update", [
            f"system skill '{name}' is read-only; copy it into the agent layer "
            f"with action='create' under the same name to override it"
        ])
    # After the existence check: existing.path carries the real (possibly
    # nested) directory; resolving before it would guess a flat path.
    skill_dir, path_errors = _resolve_dir(store, name, existing=existing)
    errors.extend(path_errors)
    if errors:
        return _refused("update", errors)
    if unattended and not has_read(run_id, name):
        # Only the unattended authoring fork is gated: a foreground session has a
        # user in the loop. hermes applies exactly this split.
        return _refused("update", [
            f"call skill_view('{name}') in this run first, then retry the update "
            f"once using the content it returned"
        ])

    # Partial update: an omitted field keeps the current value, so patching one
    # section never requires re-typing the whole skill (re-typing is where
    # content silently drifts). Both fields empty AND no usable previous value
    # is the only refusal here.
    clean_desc = sanitize_description(description) or existing.description
    clean_body = (body or "").strip() or existing.body
    if not clean_desc:
        errors.append("description is required (a skill without one is dropped at load)")
    if not clean_body:
        errors.append("body is required (SKILL.md needs content after the frontmatter)")
    if errors:
        return _refused("update", errors)

    rendered = render_skill_md(
        Skill(name=name, description=clean_desc, body=clean_body)
    )
    size = len(rendered.encode("utf-8"))
    if size > MAX_SKILL_BYTES:
        return _refused("update", [
            f"SKILL.md would be {size} bytes (limit {MAX_SKILL_BYTES}); move detail "
            f"into references/ and keep the body to the core steps"
        ])

    plans, file_errors = _check_files(files, skill_dir)
    if file_errors:
        return _refused("update", file_errors)

    skill_md = skill_dir / SKILL_FILENAME
    try:
        previous_md = skill_md.read_text(encoding="utf-8")
    except OSError:
        previous_md = None
    written: Dict[Path, Optional[str]] = {}
    try:
        written = _write_resources(plans)
        store.update(name, description=clean_desc, body=clean_body)
        store.reload()
        hard = _split_validation(validate_skill_bundle(name, skill_dir))
        if hard:
            raise _Rollback(hard)
    except _Rollback as roll:
        _rollback_update(store, skill_md, previous_md, written)
        return _refused("update", roll.errors)
    except Exception as exc:  # noqa: BLE001
        _rollback_update(store, skill_md, previous_md, written)
        logger.warning("Skill update failed for %s: %s", name, exc)
        return _refused("update", [f"write failed: {exc}"])

    warnings = _advisories(store, name, clean_desc, clean_body)
    logger.info("Skill updated: %s (%d warnings)", name, len(warnings))
    return _ok("update", name, **_payload(store, name, clean_desc, skill_md, warnings))


def _rollback_update(
    store: Any, skill_md: Path, previous_md: Optional[str],
    written: Dict[Path, Optional[str]],
) -> None:
    """Restore SKILL.md and every resource this call touched, then re-index."""
    _restore_resources(written)
    if previous_md is not None:
        try:
            skill_md.write_text(previous_md, encoding="utf-8")
        except OSError as exc:
            logger.warning("SKILL.md rollback failed for %s: %s", skill_md, exc)
    try:
        store.reload()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Skill reload after rollback failed: %s", exc)


# --- public async surface ----------------------------------------------------

async def create(
    name: str,
    description: str,
    body: str,
    files: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Write a NEW agent skill (SKILL.md plus any resources, in one call)."""
    return await asyncio.to_thread(_create_sync, name, description, body, files)


async def update(
    name: str,
    description: str,
    body: str,
    files: Optional[List[Dict[str, Any]]] = None,
    *,
    run_id: str = "",
    unattended: bool = False,
) -> Dict[str, Any]:
    """Rewrite an existing agent skill; rolls back on a hard validation error."""
    return await asyncio.to_thread(
        _update_sync, name, description, body, files, run_id, unattended
    )


async def delete(name: str) -> Dict[str, Any]:
    """Move an agent skill to the recycle bin (recoverable, never a hard delete)."""
    store = get_skill_store()
    await asyncio.to_thread(store.ensure_loaded)

    if not _NAME_RE.match(name or ""):
        return _refused("delete", [
            "invalid skill name: use lowercase letters, digits and single hyphens, "
            "1-64 chars, starting with a letter or digit"
        ])

    existing = store.get(name)   # in-memory lookup; no IO
    if existing is None:
        return _refused("delete", [f"skill '{name}' does not exist"])
    if not existing.is_writable:
        return _refused("delete", [f"system skill '{name}' is read-only"])
    # existing.path carries the real (possibly nested) directory.
    skill_dir, errors = _resolve_dir(store, name, existing=existing)
    if errors:
        return _refused("delete", errors)

    try:
        from boarai.core.trash import get_trash_manager

        trash_id = await get_trash_manager().trash_skill(name, skill_dir)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Skill trash failed for %s: %s", name, exc)
        return _refused("delete", [f"could not move '{name}' to the recycle bin: {exc}"])
    if trash_id is None:
        return _refused("delete", [f"could not move '{name}' to the recycle bin"])

    # Drop index/usage entries only after the directory is safely in the bin.
    await asyncio.to_thread(store.delete, name)
    logger.info("Skill deleted: %s (trash_id=%s)", name, trash_id)
    return _ok("delete", name, trash_id=trash_id, warnings=[])


__all__ = [
    "MAX_SKILL_BYTES",
    "MAX_RESOURCE_BYTES",
    "sanitize_description",
    "mark_read",
    "has_read",
    "clear_read_marks",
    "create",
    "update",
    "delete",
]
