"""Skill tools — discovery, reading and the single write gate.

The agent reads the skill index from its prompt prefix (auto-injected) and
uses ``skill_view`` to load a skill's full steps on demand; execution = the
model follows the steps after reading (no dedicated exec tool).

Writing goes through ``skill_manage`` and nowhere else: it is the only path
that mutates the agent skill layer, it validates before and after the write,
and it returns the exact problem list so the author can fix it in the same
turn. ``file_write`` must not be used for ``SKILL.md`` — it bypasses every
gate (naming, description sanitizing, resource completeness, path escape) and
can silently land a relative ``.boarai/...`` path inside the session
workspace, where the index never scans it. See
``core.extension.skill.manage`` and ``boar/prompts/extension/skills.md``.
Enable/disable lives in the WebUI/API; delete is a recycle-bin move.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from boarai.core.extension.tool import (
    CompressStrategy,
    SecurityProfile,
    SuggestedCategory,
    ToolParameter,
    tool,
)
from boarai.core.extension.skill import get_skill_store, manage

logger = logging.getLogger(__name__)


def _format_skill(skill, full: bool = False) -> Dict[str, Any]:
    data = {
        "name": skill.name,
        "description": skill.description,
        "source": skill.source,
        "use_count": skill.use_count,
    }
    if full:
        data["body"] = skill.body
    return data


def _in_authoring_run(ctx) -> bool:
    """True when this call happens inside a background skill-authoring run."""
    run_id = getattr(ctx, "run_id", None)
    if not run_id:
        return False
    try:
        from boarai.core.session import get_run_manager

        run = get_run_manager().get_run(run_id)
        if run is None:
            return False
        kind = run.get("kind") if isinstance(run, dict) else getattr(run, "kind", None)
        return kind == "skill_authoring"
    except Exception:  # noqa: BLE001 — never block a read on bookkeeping
        return False


@tool(name="skill_list",
      description="List available reusable skills (name and description). "
                  "Use skill_view to read a skill's full steps.",
      security_profile=SecurityProfile.NONE,
      compress_strategy=CompressStrategy.LIST,
      suggested_category=SuggestedCategory.READONLY,
      parameters=[])
async def skill_list(ctx, **kwargs) -> Dict:
    """Enumerate all skills from the store index (disabled skills hidden)."""
    store = get_skill_store()
    skills = await asyncio.to_thread(store.list_enabled)
    return {
        "count": len(skills),
        "skills": [_format_skill(s) for s in skills],
    }


@tool(name="skill_view",
      description="Read a skill's full step-by-step instructions, then EXECUTE "
                  "by following those steps. Call this before performing any task "
                  "a skill covers; the prompt prefix only carries a one-line index.",
      security_profile=SecurityProfile.NONE,
      compress_strategy=CompressStrategy.DEFAULT,
      suggested_category=SuggestedCategory.READONLY,
      parameters=[
          ToolParameter(name="name", type="string", required=True,
                        description="Skill name (from the index or skill_list)"),
      ])
async def skill_view(ctx, **kwargs) -> Dict:
    """Return a skill's full body and bump its usage count."""
    name = str(kwargs.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}
    store = get_skill_store()
    skill = await asyncio.to_thread(store.get, name)
    if skill is None:
        return {"error": f"Skill not found: {name}"}
    if skill.disabled:
        return {"error": f"Skill '{name}' is disabled"}
    # Usage counts real consumption only: an authoring run's self-check view
    # must not inflate use_count / last_used_at (observation polluting the
    # observed data).
    if not _in_authoring_run(ctx):
        await asyncio.to_thread(store.bump_usage, name)
    # Read-before-write ledger: skill_manage(action="update") refuses to patch
    # a skill this run has never loaded.
    manage.mark_read(str(getattr(ctx, "run_id", "") or ""), name)
    data = _format_skill(skill, full=True)
    data["resources"] = skill.resources
    return {"skill": data}


@tool(name="skill_read_resource",
      description="Read a skill's reference/script/asset file (progressive "
                  "disclosure). Use after skill_view when the body points to a "
                  "referenced file for extra detail.",
      security_profile=SecurityProfile.NONE,
      compress_strategy=CompressStrategy.DEFAULT,
      suggested_category=SuggestedCategory.READONLY,
      parameters=[
          ToolParameter(name="name", type="string", required=True,
                        description="Skill name"),
          ToolParameter(name="resource", type="string", required=True,
                        description="Relative resource path (e.g. references/aws.md)"),
      ])
async def skill_read_resource(ctx, **kwargs) -> Dict:
    """Read a referenced resource file inside a skill directory."""
    name = str(kwargs.get("name") or "").strip()
    resource = str(kwargs.get("resource") or "").strip()
    if not name or not resource:
        return {"error": "name and resource are required"}
    store = get_skill_store()
    content = await asyncio.to_thread(store.read_resource, name, resource)
    if content is None:
        return {"error": f"Resource not found: {name}/{resource}"}
    return {"name": name, "resource": resource, "content": content}


@tool(name="skill_manage",
      description="Create/update/delete a reusable skill. The ONLY way to write "
                  "SKILL.md — never use file_write for skills. Ships the skill's "
                  "resource files in the same call. Runs every check up front and "
                  "returns the exact errors; fix them and call again.",
      security_profile=SecurityProfile.NONE,
      compress_strategy=CompressStrategy.DEFAULT,
      suggested_category=SuggestedCategory.SAFE,
      parameters=[
          ToolParameter(name="action", type="string", required=True,
                        enum=["create", "update", "delete"],
                        description="create = new skill (rejected if the name is taken); "
                                    "update = rewrite an existing one (call skill_view on "
                                    "it first); delete = move it to the recycle bin"),
          ToolParameter(name="name", type="string", required=True,
                        description="Skill name: kebab-case and class-level — not "
                                    "fix-x, not a PR/issue number, not a date"),
          ToolParameter(name="description", type="string", required=False,
                        description="ONE sentence <=60 chars: what it does AND when to "
                                    "use it. Omit on update to keep the current one."),
          ToolParameter(name="body", type="string", required=False,
                        description="Markdown body: Intro / When to Use / Prerequisites / "
                                    "Procedure / Pitfalls / Verification. Omit on update "
                                    "to keep the current one."),
          ToolParameter(name="files", type="array", required=False,
                        description='Resource files shipped with the skill, e.g. '
                                    '[{"path": "scripts/fetch.py", "content": "..."}]. '
                                    'path must start with references/, scripts/ or '
                                    'assets/. Every script or reference the body names '
                                    'must be included in the SAME call.'),
      ])
async def skill_manage(ctx, **kwargs) -> Dict:
    """Route to the core write gate; the gate owns every check and the rollback."""
    action = str(kwargs.get("action") or "").strip().lower()
    name = str(kwargs.get("name") or "").strip()
    if action == "create":
        return await manage.create(
            name, kwargs.get("description"), kwargs.get("body"), kwargs.get("files"),
        )
    if action == "update":
        # Only the unattended authoring fork is required to have loaded the
        # skill first; a foreground session has a user in the loop. hermes
        # splits read-before-write exactly this way.
        return await manage.update(
            name, kwargs.get("description"), kwargs.get("body"), kwargs.get("files"),
            run_id=str(getattr(ctx, "run_id", "") or ""),
            unattended=_in_authoring_run(ctx),
        )
    if action == "delete":
        return await manage.delete(name)
    return {
        "ok": False, "action": action,
        "errors": [f"unknown action {action!r}"],
        "hint": "use create, update or delete",
    }
