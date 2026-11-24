"""
Security API handlers for BoarAi.

Provides:
- GET  /api/security/audit  — query audit log
- GET  /api/security/config — get current security config
- PUT  /api/security/config — update security config at runtime
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from aiohttp import web

logger = logging.getLogger(__name__)

_ALLOWED_APPROVAL_MODES = frozenset({"on", "off"})
_ALLOWED_SHELL_BACKENDS = frozenset({"local", "docker"})
_ALLOWED_STREAK_POLICIES = frozenset({"warn", "block"})
#: The event types the audit writer can record. Anything else would be
#: silently dropped by ``AuditLog.record``, so it is rejected here instead.
_ALLOWED_AUDIT_EVENTS = frozenset(
    {"tool_execute", "tool_deny", "path_block", "approval", "pip_install"}
)
_PATH_LIST_FIELDS = (
    "absolute_write_paths",
    "forbidden_read_paths",
    "forbidden_write_paths",
)
_MAX_SESSION_TTL = 2592000  # 30 days, mirrors MAX_TTL_SECONDS


def _validate_path_list(value: object, field: str) -> list:
    """Validate one glob-pattern list field (non-empty strings)."""
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of strings")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must contain only strings")
    out = [item.strip() for item in value if item.strip()]
    if not out:
        raise ValueError(f"{field} must not be empty")
    return out


def _get_config_path():
    """Absolute path of the settings config: ``<data>/config/config.yaml``."""
    from boarai.core.runtime import get_data_dir

    return get_data_dir() / "config" / "config.yaml"


def register_security_routes(app: web.Application) -> None:
    """Register security-related API routes."""
    app.router.add_get("/api/security/audit", handle_audit_query)
    app.router.add_get("/api/security/config", handle_get_config)
    app.router.add_put("/api/security/config", handle_update_config)


def _parse_limit(
    raw: str | None,
    default: int = 50,
    *,
    min_v: int = 1,
    max_v: int = 500,
) -> int:
    """Parse and clamp a positive integer limit from query string."""
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"limit must be an integer, got {raw!r}") from exc
    if value < min_v or value > max_v:
        raise ValueError(f"limit must be between {min_v} and {max_v}")
    return value


def _validate_security_updates(data: object) -> Dict[str, Any]:
    """Validate PUT body for security config."""
    if not isinstance(data, dict):
        raise ValueError("Body must be a JSON object")
    if not data:
        raise ValueError("Body must include at least one field to update")

    out: Dict[str, Any] = {}
    if "approval_mode" in data:
        mode = data["approval_mode"]
        if not isinstance(mode, str) or mode not in _ALLOWED_APPROVAL_MODES:
            raise ValueError(
                f"approval_mode must be one of {sorted(_ALLOWED_APPROVAL_MODES)}"
            )
        out["approval_mode"] = mode
    if "auto_approve" in data:
        if not isinstance(data["auto_approve"], bool):
            raise ValueError("auto_approve must be a boolean")
        out["auto_approve"] = data["auto_approve"]
    if "max_tool_calls" in data:
        try:
            n = int(data["max_tool_calls"])
        except (TypeError, ValueError) as exc:
            raise ValueError("max_tool_calls must be an integer") from exc
        if n < 0 or n > 100000:
            raise ValueError("max_tool_calls must be between 0 (unlimited) and 100000")
        out["max_tool_calls"] = n
    if "approval_timeout" in data:
        # Legacy field (superseded by pending_timeout): accepted for
        # backward compatibility, no runtime effect on the gate.
        try:
            t = float(data["approval_timeout"])
        except (TypeError, ValueError) as exc:
            raise ValueError("approval_timeout must be a number") from exc
        if t < 1 or t > 3600:
            raise ValueError("approval_timeout must be between 1 and 3600 seconds")
        out["approval_timeout"] = t
    if "pending_timeout" in data:
        try:
            t = float(data["pending_timeout"])
        except (TypeError, ValueError) as exc:
            raise ValueError("pending_timeout must be a number") from exc
        if t < 1 or t > 86400:
            raise ValueError("pending_timeout must be between 1 and 86400 seconds")
        out["pending_timeout"] = t
    if "network_approval" in data:
        if not isinstance(data["network_approval"], bool):
            raise ValueError("network_approval must be a boolean")
        out["network_approval"] = data["network_approval"]
    if "block_external_absolute_paths" in data:
        if not isinstance(data["block_external_absolute_paths"], bool):
            raise ValueError("block_external_absolute_paths must be a boolean")
        out["block_external_absolute_paths"] = data["block_external_absolute_paths"]
    if "allow_external_write" in data:
        if not isinstance(data["allow_external_write"], bool):
            raise ValueError("allow_external_write must be a boolean")
        out["allow_external_write"] = data["allow_external_write"]
    if "allow_external_read" in data:
        if not isinstance(data["allow_external_read"], bool):
            raise ValueError("allow_external_read must be a boolean")
        out["allow_external_read"] = data["allow_external_read"]
    if "unattended_policy" in data:
        policy = data["unattended_policy"]
        if not isinstance(policy, str) or policy.lower() not in (
            "ask", "deny", "approve",
        ):
            raise ValueError(
                "unattended_policy must be one of ['ask', 'deny', 'approve']"
            )
        out["unattended_policy"] = policy.lower()

    # ---- WebUI access gate / audit / shell backend / path floors (v1.16) ----
    if "trust_loopback" in data:
        if not isinstance(data["trust_loopback"], bool):
            raise ValueError("trust_loopback must be a boolean")
        out["trust_loopback"] = data["trust_loopback"]
    if "session_ttl_max_seconds" in data:
        try:
            ttl_max = int(data["session_ttl_max_seconds"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "session_ttl_max_seconds must be an integer"
            ) from exc
        if ttl_max < 60 or ttl_max > _MAX_SESSION_TTL:
            raise ValueError(
                f"session_ttl_max_seconds must be between 60 and {_MAX_SESSION_TTL}"
            )
        out["session_ttl_max_seconds"] = ttl_max
    if "audit_enabled" in data:
        if not isinstance(data["audit_enabled"], bool):
            raise ValueError("audit_enabled must be a boolean")
        out["audit_enabled"] = data["audit_enabled"]
    if "audit_events" in data:
        events = data["audit_events"]
        if not isinstance(events, list) or not events:
            raise ValueError("audit_events must be a non-empty list")
        unknown = [
            event for event in events
            if not isinstance(event, str) or event not in _ALLOWED_AUDIT_EVENTS
        ]
        if unknown:
            raise ValueError(
                "audit_events contains unsupported event(s): "
                f"{unknown}; allowed: {sorted(_ALLOWED_AUDIT_EVENTS)}"
            )
        # De-duplicate, keep the caller's order (the audit file reads better).
        out["audit_events"] = list(dict.fromkeys(events))
    if "audit_log_path" in data:
        path = data["audit_log_path"]
        if not isinstance(path, str) or not path.strip():
            raise ValueError("audit_log_path must be a non-empty string")
        out["audit_log_path"] = path.strip()
    if "shell_backend" in data:
        backend = data["shell_backend"]
        if not isinstance(backend, str) or backend.lower() not in _ALLOWED_SHELL_BACKENDS:
            raise ValueError(
                f"shell_backend must be one of {sorted(_ALLOWED_SHELL_BACKENDS)}"
            )
        out["shell_backend"] = backend.lower()
    if "docker_image" in data:
        image = data["docker_image"]
        if not isinstance(image, str) or not image.strip() or any(
            ch.isspace() for ch in image.strip()
        ):
            raise ValueError("docker_image must be a non-empty image reference")
        out["docker_image"] = image.strip()
    if "docker_network" in data:
        if not isinstance(data["docker_network"], bool):
            raise ValueError("docker_network must be a boolean")
        out["docker_network"] = data["docker_network"]
    if "error_streak_policy" in data:
        policy = data["error_streak_policy"]
        if not isinstance(policy, str) or policy.lower() not in _ALLOWED_STREAK_POLICIES:
            raise ValueError(
                f"error_streak_policy must be one of {sorted(_ALLOWED_STREAK_POLICIES)}"
            )
        out["error_streak_policy"] = policy.lower()
    for field in _PATH_LIST_FIELDS:
        if field in data:
            out[field] = _validate_path_list(data[field], field)

    if not out:
        raise ValueError("No recognized fields to update")
    return out


async def handle_audit_query(request: web.Request) -> web.Response:
    """GET /api/security/audit — Query security audit log."""
    try:
        limit = _parse_limit(request.query.get("limit"), 50)
    except ValueError as e:
        return web.json_response(
            {"success": False, "error": {"message": str(e)}},
            status=400,
        )
    event_type = request.query.get("event", None)

    try:
        from boarai.core.security import get_security_manager

        sec = get_security_manager()
        if not sec._initialized:
            return web.json_response({"success": True, "data": {"records": []}})
        if sec.audit is None:
            return web.json_response(
                {"success": True, "data": {"records": [], "total": 0}}
            )
        records = sec.audit.query(limit=limit, event_type=event_type)
        return web.json_response(
            {
                "success": True,
                "data": {"records": records, "total": len(records)},
            }
        )
    except Exception as e:
        logger.error(f"Audit query failed: {e}")
        return web.json_response(
            {"success": False, "error": {"message": str(e)}},
            status=500,
        )


async def handle_get_config(request: web.Request) -> web.Response:
    """GET /api/security/config — Get current security configuration."""
    try:
        from boarai.core.config import get_config_parser
        from boarai.core.config.parser import ConfigParser

        # The INITIALIZED global parser, whose in-memory config mirrors
        # runtime changes (SecurityManager._persist_config writes back into
        # it). Constructing a fresh pathless ConfigParser here — as this used
        # to — always yielded hardcoded defaults, so the WebUI security page
        # never reflected what was actually configured or running.
        parser = get_config_parser()
        config = parser.config if parser else None
        if config is None:
            config = await ConfigParser(_get_config_path()).load()
        if not config or not hasattr(config, "security"):
            return web.json_response({"success": True, "data": {}})
        cfg = config.security
        return web.json_response(
            {
                "success": True,
                "data": {
                    "approval_mode": cfg.tool_guard.approval_mode,
                    "auto_approve": cfg.tool_guard.auto_approve,
                    "workspace_root": cfg.path_guard.workspace_root,
                    "allow_external_write": cfg.path_guard.allow_external_write,
                    "allow_external_read": cfg.path_guard.allow_external_read,
                    "max_tool_calls": cfg.tool_guard.max_tool_calls_per_run,
                    "audit_enabled": cfg.audit.enabled,
                    "unattended_policy": getattr(
                        cfg.tool_guard, "unattended_policy", "ask"
                    ),
                    # How long a gated batch may wait for a user decision
                    # (the gate's actual wait — supersedes the legacy
                    # approval_timeout, which is no longer returned).
                    "pending_timeout": float(
                        getattr(cfg.tool_guard, "pending_timeout", 600.0) or 600.0
                    ),
                    # Shell-safety toggles (v1.12 semantics)
                    "network_approval": bool(
                        getattr(cfg.shell_safety, "network_approval", True)
                    ),
                    "block_external_absolute_paths": bool(
                        getattr(
                            cfg.shell_safety,
                            "block_external_absolute_paths",
                            False,
                        )
                    ),
                    # v1.16: the rest of the surface the Settings → Security
                    # tab can now edit. Every one of these was previously
                    # YAML-only, which made the page look like the whole
                    # policy when it was a subset.
                    "trust_loopback": bool(
                        getattr(cfg.web, "trust_loopback", True)
                    ),
                    "session_ttl_max_seconds": int(
                        getattr(cfg.web, "session_ttl_max_seconds", 2592000)
                    ),
                    "audit_events": list(getattr(cfg.audit, "events", []) or []),
                    "audit_log_path": str(
                        getattr(cfg.audit, "log_path", "") or ""
                    ),
                    "shell_backend": str(
                        getattr(cfg.shell_safety, "backend", "local") or "local"
                    ),
                    "docker_image": str(
                        getattr(cfg.shell_safety, "docker_image", "") or ""
                    ),
                    "docker_network": bool(
                        getattr(cfg.shell_safety, "docker_network", False)
                    ),
                    "error_streak_policy": str(
                        getattr(cfg.tool_guard, "error_streak_policy", "warn")
                        or "warn"
                    ),
                    "absolute_write_paths": list(
                        getattr(cfg.path_guard, "absolute_write_paths", []) or []
                    ),
                    "forbidden_read_paths": list(
                        getattr(cfg.path_guard, "forbidden_read_paths", []) or []
                    ),
                    "forbidden_write_paths": list(
                        getattr(cfg.path_guard, "forbidden_write_paths", []) or []
                    ),
                },
            }
        )
    except Exception as e:
        logger.error(f"Security config query failed: {e}")
        return web.json_response(
            {"success": False, "error": {"message": str(e)}},
            status=500,
        )


async def handle_update_config(request: web.Request) -> web.Response:
    """PUT /api/security/config — Update security config at runtime."""
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response(
            {"success": False, "error": {"message": "Invalid JSON"}},
            status=400,
        )
    except Exception as e:
        return web.json_response(
            {
                "success": False,
                "error": {"message": f"Invalid request body: {e}"},
            },
            status=400,
        )

    try:
        updates = _validate_security_updates(data)
    except ValueError as e:
        return web.json_response(
            {"success": False, "error": {"message": str(e)}},
            status=400,
        )

    try:
        from boarai.core.security import get_security_manager

        sec = get_security_manager()
        if not sec._initialized:
            return web.json_response(
                {
                    "success": False,
                    "error": {"message": "Security not initialized"},
                },
                status=503,
            )

        sec.update_config(updates, persist=True)
        logger.info(f"Security config updated via API: {updates}")
        return web.json_response(
            {
                "success": True,
                "data": {
                    "updated": list(updates.keys()),
                    "persisted": True,
                },
            }
        )
    except ValueError as e:
        return web.json_response(
            {"success": False, "error": {"message": str(e)}},
            status=400,
        )
    except Exception as e:
        logger.error(f"Security config update failed: {e}")
        return web.json_response(
            {"success": False, "error": {"message": str(e)}},
            status=500,
        )
