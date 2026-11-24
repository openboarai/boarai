"""
Extension API endpoints for BoarAi server.

Provides HTTP endpoints for extension management:
- List, get, enable, disable, reload, uninstall extensions

Architecture:
    HTTP Request -> ExtensionAPIHandler -> ExtensionManager -> ExtensionBase
"""

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from aiohttp import web

from boarai.core.extension.manager import get_extension_manager
from boarai.core.extension.types import ExtensionStatus

logger = logging.getLogger(__name__)

# boar.extensions.tools.file_tools.tools → file_tools
_EXT_MODULE_RE = re.compile(
    r"(?:^|\.)extensions\.(?:channels|tools|plugins|skills|mcp)\.([^.]+)"
)
# Loader-created modules: boarai_ext_{source}_{type}_{name}
# (core/extension/loader.py) — no ".extensions." segment at all.
_EXT_LOADER_RE = re.compile(r"^boarai_ext_(?:system|user)_[a-z]+_(.+)$")


def _extension_from_module(handler: Any) -> str:
    """Best-effort owning-extension name for a tool handler.

    Two module shapes exist: package-imported system extensions
    (``boar.extensions.tools.file_tools.tools``) and loader-created ones
    (``boarai_ext_user_tool_my_tool``). Core-registered tools return "".
    """
    module = getattr(handler, "__module__", "") or ""
    match = _EXT_MODULE_RE.search(module)
    if match:
        return match.group(1)
    match = _EXT_LOADER_RE.match(module)
    return match.group(1) if match else ""


def _clamp_int(raw: Any, lo: int, hi: int, default: int) -> int:
    """Coerce a client/handler-supplied number into [lo, hi]."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))

# Map backend extension types to frontend section names
TYPE_GROUP_MAP: Dict[str, str] = {
    "channel": "adapters",
    "tool": "tools",
    "plugin": "plugins",
    "skill": "skills",
    "mcp": "mcp",
}

REVERSE_TYPE_MAP: Dict[str, str] = {v: k for k, v in TYPE_GROUP_MAP.items()}


class ExtensionAPIHandler:
    """Extension CRUD API handlers."""

    def __init__(self):
        self._manager = get_extension_manager()

    # ----- Response Helpers -----

    @staticmethod
    def _success(data: Any, status: int = 200) -> web.Response:
        return web.json_response({"success": True, "data": data}, status=status)

    @staticmethod
    def _error(status: int, code: str, message: str) -> web.Response:
        return web.json_response(
            {"success": False, "error": {"code": code, "message": message}},
            status=status,
        )

    # ----- Serialization helpers -----

    def _ext_to_dict(self, ext) -> Dict[str, Any]:
        """Serialize an extension to a dictionary for API responses."""
        # Prefer the loader-set authoritative manifest (carries v2
        # contributes + source). Fall back to the extension's own property.
        m = getattr(ext, "_manifest", None) or ext.manifest
        c = m.contributes
        return {
            "id": ext.name,
            "name": ext.name,
            "description": m.description,
            "version": m.version,
            "enabled": ext.status == ExtensionStatus.RUNNING,
            "status": ext.status.value,
            "type": m.extension_type,
            "author": m.author,
            "dependencies": m.dependencies,
            "pip_dependencies": m.pip_dependencies,
            "min_boarai_version": m.min_boarai_version,
            "provides": m.provides,
            "permissions": m.permissions,
            "source": m.source,
            "contributes": {
                "tools": [t.id for t in c.tools],
                "routes": [f"{r.method} {r.path}" for r in c.routes],
                "hooks": [h.event for h in c.hooks],
                "hook_points": [p.name for p in c.hook_points],
                "services": [s.methods for s in c.services],
            },
            "unbound_hooks": list(getattr(ext, "_unbound_hooks", []) or []),
            "error": ext.error,
            "trusted": self._manager.is_trusted(ext.name, m.source),
            # Card-level hint so the list can hide the Settings button for
            # extensions that declare nothing to configure (opening an empty
            # dialog is worse than no button). Settings are a custom iframe
            # panel now — the declarative form is gone.
            "has_settings": m.contributes.ui is not None,
        }

    def _ext_to_detail(self, ext) -> Dict[str, Any]:
        """Serialize extension with full detail (settings surface)."""
        d = self._ext_to_dict(ext)
        d["settings"] = self._settings_payload(ext)
        return d

    # ----- Settings surface (custom panel + actions) -----

    # View primitives the WebUI knows how to render. Anything else a handler
    # returns is dropped rather than forwarded: an unrenderable type would show
    # up as a blank block, and a base64 image would bloat the response (the
    # contract is "images are same-origin URLs, never payloads").
    _VIEW_TYPES = frozenset({"image", "kv", "markdown", "text"})
    _MAX_RESULT_BYTES = 64 * 1024

    @staticmethod
    def _safe_custom_url(ext_id: str, url: Any) -> str:
        """A same-origin URL of THIS extension, or "" when rejected."""
        u = str(url or "").strip()
        if not u.startswith("/"):
            return ""
        allowed = (f"/static/{ext_id}/", f"/api/ext/{ext_id}/")
        return u if u.startswith(allowed) else ""

    def _settings_payload(self, ext) -> Dict[str, Any]:
        """Settings surface of a LOADED extension."""
        api = getattr(ext, "api", None)
        ui = (
            api.settings_ui() if api is not None
            else {"panel": {}, "actions": []}
        )
        values = (
            api.get_config() if api is not None
            else dict(getattr(ext, "_config", {}) or {})
        )

        panel = dict(ui.get("panel") or {})
        panel["kind"] = "custom"
        url = self._safe_custom_url(ext.name, panel.get("custom_url"))
        if url:
            panel["custom_url"] = url
        else:
            logger.warning(
                "Extension %s: custom settings URL %r rejected (must be "
                "/static/%s/... or /api/ext/%s/...)", ext.name,
                panel.get("custom_url"), ext.name, ext.name,
            )
            panel["custom_url"] = ""

        return {
            "loaded": True,
            "panel": panel,
            # Never leak the handler reference (a source path) to a client.
            "actions": [
                {k: v for k, v in a.items() if k != "handler"}
                for a in (ui.get("actions") or [])
            ],
            "values": values,
        }

    @staticmethod
    def _empty_settings() -> Dict[str, Any]:
        """Settings surface for an extension that is not loaded."""
        return {
            "loaded": False, "panel": {}, "actions": [], "values": {},
        }

    # ----- List Extensions -----

    @staticmethod
    def _skill_to_dict(skill) -> Dict[str, Any]:
        """Serialize a SkillStore skill into the extension-card shape."""
        return {
            "id": skill.name,
            "name": skill.name,
            "description": skill.description,
            "version": "",  # skills carry no version
            "enabled": not skill.disabled,
            "status": "running" if not skill.disabled else "disabled",
            "type": "skill",
            "source": skill.source,  # system | agent
            "contributes": {},
            "error": "",
            "usage": {
                "use_count": skill.use_count,
                "view_count": skill.view_count,
                "patch_count": skill.patch_count,
                "last_used_at": skill.last_used_at,
            },
        }

    async def _skills_group(self) -> List[Dict[str, Any]]:
        """List skills from the store (thread offload: first scan hits disk)."""
        try:
            from boarai.core.extension.skill import get_skill_store

            store = get_skill_store()
            skills = await asyncio.to_thread(store.list_all)
            return [self._skill_to_dict(s) for s in skills]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to list skills for extensions page: {e}")
            return []

    async def handle_list(self, request: web.Request) -> web.Response:
        """GET /api/extensions — List all extensions grouped by type."""
        try:
            all_exts = self._manager._extensions.values()

            # Build grouped response
            grouped: Dict[str, List[Dict[str, Any]]] = {
                "adapters": [],
                "tools": [],
                "plugins": [],
                "skills": [],
                "mcp": [],
            }

            for ext in all_exts:
                section = TYPE_GROUP_MAP.get(ext.extension_type, "plugins")
                grouped[section].append(self._ext_to_dict(ext))

            # Discovered-but-disabled extensions never enter _extensions;
            # surface them (manifest-only) so the UI can list and re-enable.
            for meta in self._manager.list_discovered_disabled():
                section = TYPE_GROUP_MAP.get(meta.get("extension_type", ""), "plugins")
                grouped[section].append(meta)

            grouped["skills"].extend(await self._skills_group())

            # Discovered-but-FAILED loads are not a log-only black hole:
            # the registry entry (code/message/hint/attempts) rides along so
            # the UI can render a failure card with an actionable next step.
            failed = self._manager.list_failed()
            for entry in failed:
                entry["trusted"] = self._manager.is_trusted(
                    entry.get("name", ""), entry.get("source") or ""
                )
            grouped["failed"] = failed

            return self._success(grouped)
        except Exception as e:
            logger.error(f"Failed to list extensions: {e}")
            return self._error(500, "LIST_ERROR", str(e))

    # ----- Registered Tools -----

    async def handle_tools(self, request: web.Request) -> web.Response:
        """GET /api/tools — List registered tools with their owning extension.

        Hidden tools (``llm_visible=False``) are excluded by default: they are
        not offered to the model, so listing them in the agent allowlist picker
        would suggest they are selectable for prompting. ``?all=1`` returns
        the full registry (they still matter for execution-time gating).
        """
        try:
            from boarai.core.extension.tool.registry import get_tool_registry

            registry = get_tool_registry()
            include_hidden = request.query.get("all", "").lower() in ("1", "true", "yes")

            # name → owning extension. Three sources, most authoritative first:
            #   1. the handler's module path (boar.extensions.tools.file_tools
            #      .tools → file_tools) — covers decorator-registered tools,
            #      which is how nearly every extension actually registers;
            #   2. tools registered through an ExtensionAPI instance;
            #   3. the declarative ``contributes.tools`` ids.
            owners: Dict[str, str] = {}
            for ext in self._manager._extensions.values():
                manifest = getattr(ext, "_manifest", None) or ext.manifest
                for decl in manifest.contributes.tools:
                    owners.setdefault(decl.id, ext.name)
                api = getattr(ext, "_api", None)
                for name in getattr(api, "_my_tool_names", None) or []:
                    owners.setdefault(name, ext.name)

            tools = []
            for name in registry.list_tools():
                tool = registry.get_tool(name)
                if tool is None:
                    continue
                d = tool.definition
                if not include_hidden and not d.llm_visible:
                    continue
                tools.append({
                    "name": d.name,
                    "description": d.description,
                    "category": d.suggested_category,
                    "security_profile": d.security_profile,
                    "llm_visible": bool(d.llm_visible),
                    "extension": _extension_from_module(tool.handler) or owners.get(d.name, ""),
                })
            tools.sort(key=lambda t: (t["extension"] or "~core", t["name"]))
            return self._success({"tools": tools, "total": len(tools)})
        except Exception as e:
            logger.error(f"Failed to list tools: {e}")
            return self._error(500, "TOOLS_ERROR", str(e))

    # ----- Get Extension Detail -----

    async def handle_get_detail(self, request: web.Request) -> web.Response:
        """GET /api/extensions/{name} — Get extension detail."""
        name = request.match_info.get("name", "")
        if not name:
            return self._error(400, "MISSING_NAME", "Extension name is required")

        ext = self._manager.get_extension(name)
        if not ext:
            # A discovered-but-failed extension answers with its failure
            # record instead of a bare 404 (the answer to "is it the code or
            # the declaration?" must not require reading server logs).
            failed = self._manager.get_failed(name)
            if failed is not None:
                return self._success({
                    "id": name,
                    "name": name,
                    "enabled": False,
                    "status": "failed",
                    "source": failed.get("source", ""),
                    "error": failed.get("error", ""),
                    "failure_code": failed.get("code", ""),
                    "hint": failed.get("hint", ""),
                    "attempts": failed.get("attempts", 0),
                    "last_attempt": failed.get("last_attempt", 0),
                    "trusted": self._manager.is_trusted(
                        name, failed.get("source") or ""
                    ),
                    "contributes": {},
                    # Nothing configurable while not loaded (its iframe asset
                    # is not mounted); config can still be pre-filled via PUT.
                    "settings": self._empty_settings(),
                })
            # Discovered-but-disabled: answer so the card can render; the
            # settings surface is empty (nothing mounted) instead of a 404.
            if self._manager._locate_discovered(name)[0] is not None:
                return self._success({
                    "id": name,
                    "name": name,
                    "enabled": False,
                    "status": "disabled",
                    "contributes": {},
                    "settings": self._empty_settings(),
                })
            return self._error(404, "NOT_FOUND", f"Extension '{name}' not found")

        return self._success(self._ext_to_detail(ext))

    # ----- Settings write -----

    # Config and private-data payloads share one size floor: these endpoints
    # are a settings channel, not a document store.
    _MAX_DATA_BYTES = 256 * 1024

    async def handle_update_settings(self, request: web.Request) -> web.Response:
        """PUT /api/extensions/{name}/settings — write config values."""
        name = request.match_info.get("name", "")
        if not name:
            return self._error(400, "MISSING_NAME", "Extension name is required")
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return self._error(
                400, "INVALID_JSON", "Request body must be valid JSON"
            )
        values = body.get("values") if isinstance(body, dict) else None
        if not isinstance(values, dict) or not values:
            return self._error(
                400, "settings.empty",
                "Body must carry a non-empty 'values' object",
            )
        if len(json.dumps(values, ensure_ascii=False).encode("utf-8")) > self._MAX_DATA_BYTES:
            return self._error(
                413, "settings.too_large",
                f"'values' exceeds {self._MAX_DATA_BYTES // 1024} KB",
            )

        ext = self._manager.get_extension(name)
        api = getattr(ext, "api", None) if ext is not None else None
        if api is not None:
            await api.set_config(values)
            return self._success(self._settings_payload(ext))

        # Not loaded (or a legacy extension with no facade): persist directly.
        if self._manager._locate_discovered(name)[0] is None:
            return self._error(404, "NOT_FOUND", f"Extension '{name}' not found")
        try:
            from boarai.core.runtime import extension_data_dir, write_text_atomic_async

            loader = self._manager._loader
            current = loader._read_persisted_config(name)
            current.update(values)
            target = extension_data_dir(name)
            target.mkdir(parents=True, exist_ok=True)
            await write_text_atomic_async(
                target / "config.json",
                json.dumps(current, ensure_ascii=False, indent=2),
            )
        except Exception as e:  # noqa: BLE001
            logger.error("Settings write for '%s' failed: %s", name, e)
            return self._error(
                500, "settings.write_failed", f"Failed to persist settings: {e}"
            )
        logger.info(
            "Extension %s: settings written while not loaded (%d key(s)); "
            "they take effect on the next load", name, len(values),
        )
        return self._success({
            "loaded": False, "panel": {}, "actions": [], "values": current,
        })

    # ----- Extension private data (GET/PUT /api/extensions/{name}/data) -----

    @staticmethod
    def _read_ext_data(name: str) -> Dict[str, Any]:
        """Read ``<data>/extensions/data/<id>/data.json`` ({} when absent)."""
        try:
            from boarai.core.runtime import extension_data_dir

            f = extension_data_dir(name) / "data.json"
        except Exception:  # noqa: BLE001 — bad id/data root: no data
            return {}
        if not f.is_file():
            return {}
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001 — corrupted file degrades to empty
            logger.warning("Extension %s: unreadable data.json", name)
            return {}

    async def handle_get_data(self, request: web.Request) -> web.Response:
        """GET /api/extensions/{name}/data — the extension's private data map."""
        name = request.match_info.get("name", "")
        if not name:
            return self._error(400, "MISSING_NAME", "Extension name is required")
        return self._success({"values": self._read_ext_data(name)})

    async def handle_put_data(self, request: web.Request) -> web.Response:
        """PUT /api/extensions/{name}/data — merge into the private data map."""
        name = request.match_info.get("name", "")
        if not name:
            return self._error(400, "MISSING_NAME", "Extension name is required")
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return self._error(
                400, "INVALID_JSON", "Request body must be valid JSON"
            )
        values = body.get("values") if isinstance(body, dict) else None
        if not isinstance(values, dict) or not values:
            return self._error(
                400, "data.empty",
                "Body must carry a non-empty 'values' object",
            )
        current = self._read_ext_data(name)
        current.update(values)
        payload = json.dumps(current, ensure_ascii=False, indent=2)
        if len(payload.encode("utf-8")) > self._MAX_DATA_BYTES:
            return self._error(
                413, "data.too_large",
                f"data.json would exceed {self._MAX_DATA_BYTES // 1024} KB",
            )
        try:
            from boarai.core.runtime import extension_data_dir, write_text_atomic_async

            target = extension_data_dir(name)
            target.mkdir(parents=True, exist_ok=True)
            await write_text_atomic_async(target / "data.json", payload)
        except Exception as e:  # noqa: BLE001
            logger.error("Data write for '%s' failed: %s", name, e)
            return self._error(
                500, "data.write_failed", f"Failed to persist data: {e}"
            )
        return self._success({"values": current})

    # ----- Settings actions -----

    @classmethod
    def _normalize_action_result(cls, raw: Any) -> Dict[str, Any]:
        """Coerce a handler's return value into the documented envelope."""
        if not isinstance(raw, dict):
            text = "" if raw is None else str(raw)
            return {"ok": True, "message": text[:500]}

        out: Dict[str, Any] = {"ok": bool(raw.get("ok", True))}
        message = raw.get("message")
        if isinstance(message, str) and message:
            out["message"] = message[:500]

        views = raw.get("view")
        if isinstance(views, dict):
            views = [views]
        kept: List[Dict[str, Any]] = []
        for view in views if isinstance(views, list) else []:
            if not isinstance(view, dict):
                continue
            if str(view.get("type") or "") in cls._VIEW_TYPES:
                kept.append(view)
            else:
                logger.debug(
                    "action view type %r dropped (not renderable)",
                    view.get("type"),
                )
        if kept:
            out["view"] = kept

        poll = raw.get("poll")
        if isinstance(poll, dict) and poll.get("action"):
            out["poll"] = {
                "action": str(poll["action"]),
                "interval_ms": _clamp_int(
                    poll.get("interval_ms"), 500, 60_000, 2000),
                "max_ms": _clamp_int(poll.get("max_ms"), 1_000, 600_000, 120_000),
            }
        if raw.get("reload"):
            out["reload"] = True

        if len(json.dumps(out, ensure_ascii=False).encode("utf-8")) > cls._MAX_RESULT_BYTES:
            out.pop("view", None)
            out["message"] = (
                f"{out.get('message', '')} [result too large; views dropped]"
            )[:500]
        return out

    async def handle_run_action(self, request: web.Request) -> web.Response:
        """POST /api/extensions/{name}/actions/{action_id} — run a settings action."""
        name = request.match_info.get("name", "")
        action_id = request.match_info.get("action_id", "")
        ext = self._manager.get_extension(name)
        if ext is None:
            return self._error(
                404, "NOT_FOUND",
                f"Extension '{name}' is not loaded; its actions are unavailable",
            )
        api = getattr(ext, "api", None)
        if api is None:
            return self._error(
                409, "action.unavailable",
                f"Extension '{name}' exposes no settings actions",
            )
        action = next(
            (a for a in api.settings_ui()["actions"] if a.get("id") == action_id),
            None,
        )
        if action is None:
            return self._error(
                404, "action.not_found",
                f"Extension '{name}' has no settings action '{action_id}'",
            )

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — params are optional
            body = {}
        params = body.get("params") if isinstance(body, dict) else None
        if not isinstance(params, dict):
            params = {}

        handler = action.get("handler")
        timeout = float(action.get("timeout") or 15.0)
        ctx = {
            "extension_id": name,
            "config": api.get_config(),
            "params": params,
            "request_id": uuid.uuid4().hex,
        }
        try:
            if asyncio.iscoroutinefunction(handler):
                raw = await asyncio.wait_for(handler(ctx), timeout=timeout)
            else:
                raw = await asyncio.wait_for(
                    asyncio.to_thread(handler, ctx), timeout=timeout,
                )
        except asyncio.TimeoutError:
            logger.warning(
                "Extension %s: settings action '%s' timed out after %.1fs",
                name, action_id, timeout,
            )
            return self._success({
                "ok": False, "code": "action.timeout",
                "message": f"Action '{action_id}' timed out after {timeout:g}s",
            })
        except Exception as e:  # noqa: BLE001 — never a 500 to the panel
            logger.warning(
                "Extension %s: settings action '%s' failed: %s",
                name, action_id, e,
            )
            return self._success({
                "ok": False, "code": "action.failed", "message": str(e),
            })
        return self._success(self._normalize_action_result(raw))

    # ----- Static verification (dry-run) -----

    async def handle_verify(self, request: web.Request) -> web.Response:
        """GET /api/extensions/verify[?name=<id>|&path=<dir>] — static checks."""
        name = request.query.get("name", "").strip()
        raw_path = request.query.get("path", "").strip()
        try:
            from boarai.core.extension.verifier import (
                LEVEL_ERROR,
                LEVEL_WARNING,
                resolve_within_roots,
                verify_all,
                verify_by_name,
                verify_extension,
            )
            from boarai.core.runtime import get_project_root

            # Confine the client-supplied path to the project/data roots so
            # the verifier cannot be aimed at arbitrary OS directories.
            target_path = None
            if raw_path:
                target_path = resolve_within_roots(raw_path)
                if target_path is None:
                    return self._error(
                        400, "PATH_OUTSIDE_ROOTS",
                        "path must be inside the project root or the "
                        ".boarai data dir",
                    )

            def _run() -> Dict[str, List[Any]]:
                if target_path is not None:
                    return {str(target_path): verify_extension(target_path)}
                if name:
                    return {name: verify_by_name(name, get_project_root())}
                return verify_all(get_project_root())

            groups = await asyncio.to_thread(_run)
            issues: List[Dict[str, Any]] = []
            errors = warnings = 0
            for key in sorted(groups):
                for issue in groups[key]:
                    if issue.level == LEVEL_ERROR:
                        errors += 1
                    elif issue.level == LEVEL_WARNING:
                        warnings += 1
                    issues.append(issue.to_dict())
            return self._success({
                "ok": errors == 0,
                "checked": len(groups),
                "errors": errors,
                "warnings": warnings,
                "issues": issues[:200],
            })
        except Exception as e:
            logger.error(f"Extension verify failed: {e}")
            return self._error(500, "VERIFY_ERROR", str(e))

    # ----- Enable Extension -----

    async def handle_enable(self, request: web.Request) -> web.Response:
        """POST /api/extensions/{name}/enable — Enable an extension."""
        name = request.match_info.get("name", "")
        if not name:
            return self._error(400, "MISSING_NAME", "Extension name is required")

        # Skills: enable = clear the store's disabled flag.
        try:
            from boarai.core.extension.skill import get_skill_store

            skill = await asyncio.to_thread(get_skill_store().get, name)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Skill lookup for enable failed: {e}")
            skill = None
        if skill is not None:
            enabled_skill = await asyncio.to_thread(
                get_skill_store().set_disabled, name, False
            )
            return self._success(self._skill_to_dict(enabled_skill))

        ext = self._manager.get_extension(name)
        if not ext:
            # Persistently disabled extensions are never loaded; load them
            # from the discovery source so enabling works without restart
            ext = await self._manager.load_discovered_extension(name)

        if ext is None:
            # The load was attempted and failed (trust gate, manifest,
            # syntax...): report the structured failure instead of a bare
            # 404, and honor the explicit enable intent by clearing any
            # persisted disable so the watcher keeps retrying.
            failed = self._manager.get_failed(name)
            if failed is not None:
                store = getattr(self._manager, "_state_store", None)
                if store is not None:
                    try:
                        store.set_enabled(name, source=failed.get("source") or "")
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"Enable-intent state clear failed: {e}")
                return web.json_response(
                    {
                        "success": False,
                        "error": {
                            "code": failed.get("code") or "LOAD_FAILED",
                            "message": failed.get("error") or "load failed",
                            "hint": failed.get("hint") or "",
                        },
                    },
                    status=409,
                )
            # Neither loaded nor discoverable: the client named an extension
            # that does not exist, which is a 404 rather than a server fault.
            return self._error(
                404, "NOT_FOUND", f"Extension '{name}' not found"
            )

        result = await self._manager.enable_extension(name)
        if result:
            ext = self._manager.get_extension(name)
            return self._success(self._ext_to_detail(ext))
        else:
            return self._error(
                500, "ENABLE_FAILED",
                f"Failed to enable extension '{name}'"
            )

    # ----- Per-extension trust (operator decision via WebUI) -----

    async def handle_trust(self, request: web.Request) -> web.Response:
        """POST /api/extensions/{name}/trust — grant in-process trust + load."""
        name = request.match_info.get("name", "")
        if not name:
            return self._error(400, "MISSING_NAME", "Extension name is required")
        result = await self._manager.trust_extension(name)
        if result.get("ok"):
            ext = self._manager.get_extension(name)
            if ext is not None:
                return self._success(self._ext_to_detail(ext))
            return self._success(result)
        code = str(result.get("code") or "TRUST_FAILED")
        status = 404 if code == "not_found" else (
            400 if code in ("trust.not_needed", "state.unavailable") else 409
        )
        return web.json_response(
            {
                "success": False,
                "error": {
                    "code": code,
                    "message": result.get("message") or "trust failed",
                    "hint": result.get("hint") or "",
                },
            },
            status=status,
        )

    async def handle_untrust(self, request: web.Request) -> web.Response:
        """POST /api/extensions/{name}/untrust — revoke trust (+ unload)."""
        name = request.match_info.get("name", "")
        if not name:
            return self._error(400, "MISSING_NAME", "Extension name is required")
        result = await self._manager.untrust_extension(name)
        if result.get("ok"):
            return self._success(result)
        code = str(result.get("code") or "UNTRUST_FAILED")
        status = 404 if code == "not_found" else 400
        return self._error(status, code, result.get("message") or "untrust failed")

    # ----- Disable Extension -----

    async def handle_disable(self, request: web.Request) -> web.Response:
        """POST /api/extensions/{name}/disable — Disable an extension."""
        name = request.match_info.get("name", "")
        if not name:
            return self._error(400, "MISSING_NAME", "Extension name is required")

        # Skills: disable = set the store's disabled flag (hidden from model).
        try:
            from boarai.core.extension.skill import get_skill_store

            skill = await asyncio.to_thread(get_skill_store().get, name)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Skill lookup for disable failed: {e}")
            skill = None
        if skill is not None:
            disabled_skill = await asyncio.to_thread(
                get_skill_store().set_disabled, name, True
            )
            return self._success(self._skill_to_dict(disabled_skill))

        result = await self._manager.disable_extension(name)
        if result:
            ext = self._manager.get_extension(name)
            if ext is None:
                # A never-loaded (failed) extension was persistently disabled
                # — the retry loop stops. Report the discovered-disabled
                # manifest metadata when available.
                discovered_disabled = await asyncio.to_thread(
                    self._manager.list_discovered_disabled
                )
                meta = next(
                    (i for i in discovered_disabled if i.get("name") == name),
                    None,
                )
                return self._success(meta or {
                    "id": name, "name": name, "enabled": False,
                    "status": "disabled", "error": "",
                })
            return self._success(self._ext_to_detail(ext))

        # ``disable_extension`` returns False both for an unknown name and for
        # one already persistently disabled (never loaded, so nothing to turn
        # off). Only the first is a client error; the second is idempotent.
        # ``list_discovered_disabled`` reads manifests without loading, so it
        # tells the two apart with no side effect.
        discovered_disabled = await asyncio.to_thread(
            self._manager.list_discovered_disabled
        )
        already = next(
            (item for item in discovered_disabled if item.get("name") == name),
            None,
        )
        if already is not None:
            return self._success(already)
        return self._error(
            404, "NOT_FOUND", f"Extension '{name}' not found"
        )

    # ----- Reload Extension -----

    async def handle_reload(self, request: web.Request) -> web.Response:
        """POST /api/extensions/{name}/reload — Reload an extension."""
        name = request.match_info.get("name", "")
        if not name:
            return self._error(400, "MISSING_NAME", "Extension name is required")

        result = await self._manager.reload_extension(name)
        if result:
            return self._success({"name": name, "reloaded": True})
        else:
            return self._error(
                404, "RELOAD_FAILED",
                f"Failed to reload extension '{name}'"
            )

    # ----- Uninstall Extension -----

    async def handle_uninstall(self, request: web.Request) -> web.Response:
        """DELETE /api/extensions/{name} — Uninstall an extension."""
        name = request.match_info.get("name", "")
        if not name:
            return self._error(400, "MISSING_NAME", "Extension name is required")

        # Skills: recycle-bin delete (TrashManager) for agent skills.
        try:
            from boarai.core.extension.skill import get_skill_store

            skill = await asyncio.to_thread(get_skill_store().get, name)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Skill lookup for uninstall failed: {e}")
            skill = None
        if skill is not None:
            if not skill.is_writable:
                return self._error(
                    403, "SYSTEM_EXTENSION",
                    f"System skill '{name}' is built-in and cannot be uninstalled"
                )
            from pathlib import Path

            skill_dir = (
                Path(skill.path).parent
                if skill.path
                else get_skill_store().agent_dir / name
            )
            from boarai.core.trash import get_trash_manager

            trash_id = await get_trash_manager().trash_skill(name, skill_dir)
            if trash_id is None:
                return self._error(
                    500, "UNINSTALL_FAILED",
                    f"Failed to move skill '{name}' to trash"
                )
            # Drop index/usage entries AFTER the dir is safely in the bin.
            await asyncio.to_thread(get_skill_store().delete, name)
            try:
                from boarai.core.event import publish_event

                await publish_event(
                    "skill.archived",
                    {"name": name, "trash_id": trash_id},
                    "extension_api",
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("skill.archived publish skipped: %s", e)
            return self._success({"name": name, "uninstalled": True, "trash_id": trash_id})

        ext = self._manager.get_extension(name)
        if not ext:
            return self._error(404, "NOT_FOUND", f"Extension '{name}' not found")

        m = getattr(ext, "_manifest", None) or getattr(ext, "manifest", None)
        source = getattr(m, "source", None) if m is not None else None
        if source == "system":
            return self._error(
                403, "SYSTEM_EXTENSION",
                f"System extension '{name}' is built-in and cannot be uninstalled"
            )

        result = await self._manager.uninstall_extension(name)
        if result.get("ok"):
            return self._success({
                "name": name, "uninstalled": True,
                "trash_id": result.get("trash_id") or "",
            })
        code = str(result.get("code") or "UNINSTALL_FAILED")
        status = 404 if code == "not_found" else 500
        return self._error(
            status, code.upper(),
            result.get("message") or f"Failed to uninstall extension '{name}'"
        )

    # ----- Get Extension Stats -----

    async def handle_stats(self, request: web.Request) -> web.Response:
        """GET /api/extensions/stats — Get extension statistics."""
        try:
            stats = self._manager.get_stats()
            return self._success(stats)
        except Exception as e:
            logger.error(f"Failed to get extension stats: {e}")
            return self._error(500, "STATS_ERROR", str(e))

    # ----- Extension State (persistent enable/disable) -----

    async def handle_state(self, request: web.Request) -> web.Response:
        """GET /api/extensions/state — Get persistent enable/disable state."""
        try:
            store = getattr(self._manager, "_state_store", None)
            if store is None:
                return self._error(404, "NO_STATE_STORE", "State store unavailable")
            return self._success(store.to_dict())
        except Exception as e:
            logger.error(f"Failed to get extension state: {e}")
            return self._error(500, "STATE_ERROR", str(e))

    # ----- Hook points introspection -----

    async def handle_hooks(self, request: web.Request) -> web.Response:
        """GET /api/extensions/hooks — List all hook points and bindings."""
        try:
            from boarai.core.extension import get_hook_registry
            reg = get_hook_registry()
            points = reg.list_points()
            for p in points:
                p["bindings_detail"] = reg.list_bindings(p["name"])
            return self._success(points)
        except Exception as e:
            logger.error(f"Failed to list hooks: {e}")
            return self._error(500, "HOOKS_ERROR", str(e))

    # ----- Services introspection -----

    async def handle_services(self, request: web.Request) -> web.Response:
        """GET /api/extensions/services — List registered extension services."""
        try:
            from boarai.core.extension import get_service_registry
            return self._success(get_service_registry().list_services())
        except Exception as e:
            logger.error(f"Failed to list services: {e}")
            return self._error(500, "SERVICES_ERROR", str(e))


# Global instance
_extension_api_handler: Optional[ExtensionAPIHandler] = None


def get_extension_api_handler() -> ExtensionAPIHandler:
    """Get the global extension API handler instance."""
    global _extension_api_handler
    if _extension_api_handler is None:
        _extension_api_handler = ExtensionAPIHandler()
    return _extension_api_handler
