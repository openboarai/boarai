"""
User profile and settings API handler for BoarAi.

Provides:
- GET/PUT /api/owner — read/write .boarai/user/user.md
- GET/PUT /api/settings/session — read/write the new-session defaults
  (compression_threshold / default_workspace_* / default_approval_mode)
"""

import logging
from pathlib import Path

from aiohttp import web

from boarai.core.runtime import get_data_dir

logger = logging.getLogger(__name__)

_USER_FILE = "user/user.md"

# Session settings constraints
_COMPRESSION_THRESHOLD_MIN = 0.50
_COMPRESSION_THRESHOLD_MAX = 0.80
_COMPRESSION_THRESHOLD_DEFAULT = 0.70

#: New-session tool-approval modes (UI wording: 拒绝 / 询问 / 通过).
_APPROVAL_MODES = frozenset({"deny", "ask", "allow"})
_APPROVAL_MODE_DEFAULT = "ask"


def _get_user_path() -> Path:
    return get_data_dir() / _USER_FILE


def _get_config_path() -> Path:
    return get_data_dir() / "config" / "config.yaml"


async def handle_get_owner(request: web.Request) -> web.Response:
    """GET /api/owner — Read user profile content."""
    try:
        import aiofiles
        path = _get_user_path()
        if path.exists():
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                content = await f.read()
        else:
            content = ""
        return web.json_response({"success": True, "data": {"content": content}})
    except Exception as e:
        logger.error(f"Failed to read user profile: {e}")
        return web.json_response({"success": False, "error": {"message": str(e)}}, status=500)


async def handle_put_owner(request: web.Request) -> web.Response:
    """PUT /api/owner — Save user profile content."""
    try:
        import aiofiles
        body = await request.json()
        content = body.get("content", "")
        path = _get_user_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(content)
        logger.info(f"User profile saved ({len(content)} chars)")
        return web.json_response({"success": True, "data": {"content": content}})
    except Exception as e:
        logger.error(f"Failed to save user profile: {e}")
        return web.json_response({"success": False, "error": {"message": str(e)}}, status=500)


# ---- Session settings handlers ----


async def _read_session_settings_from_config() -> dict:
    """Read session settings from the global ConfigParser."""
    try:
        from boarai.core.config import get_config_parser
        cp = get_config_parser()
        if cp and cp._config and hasattr(cp._config, 'session'):
            session_cfg = cp._config.session
            mode = getattr(session_cfg, "default_workspace_mode", "session_dir") or "session_dir"
            if mode == "default":
                mode = "session_dir"
            return {
                "compression_threshold": session_cfg.compression_threshold,
                "default_workspace_mode": mode,
                "default_workspace_path": getattr(session_cfg, "default_workspace_path", ""),
                "default_approval_mode": _normalize_approval_mode(
                    getattr(session_cfg, "default_approval_mode", _APPROVAL_MODE_DEFAULT)
                ),
            }
    except Exception as e:
        logger.debug(f"Failed to read session settings from config: {e}")
    return {
        "compression_threshold": _COMPRESSION_THRESHOLD_DEFAULT,
        "default_workspace_mode": "session_dir",
        "default_workspace_path": "",
        "default_approval_mode": _APPROVAL_MODE_DEFAULT,
    }


def _normalize_approval_mode(value: object) -> str:
    """Unknown / missing / non-string values fall back to the default mode."""
    mode = str(value or "").strip().lower()
    return mode if mode in _APPROVAL_MODES else _APPROVAL_MODE_DEFAULT


async def _write_session_settings_to_config(settings: dict) -> None:
    """Write session settings to config file via ConfigParser."""
    try:
        from boarai.core.config import get_config_parser
        cp = get_config_parser()
        if not cp or not cp.config:
            # Must NOT return quietly: the caller answers success:true and
            # echoes back the (unchanged) defaults, so the WebUI would report
            # a saved setting that was actually discarded.
            raise RuntimeError("ConfigParser not available; settings not saved")
        # Update in-memory config
        if hasattr(cp._config, 'session'):
            if "compression_threshold" in settings:
                cp._config.session.compression_threshold = settings["compression_threshold"]
            if "default_workspace_mode" in settings:
                cp._config.session.default_workspace_mode = settings["default_workspace_mode"]
            if "default_workspace_path" in settings:
                cp._config.session.default_workspace_path = settings["default_workspace_path"]
            if "default_approval_mode" in settings:
                cp._config.session.default_approval_mode = settings["default_approval_mode"]
        # Persist to config.yaml
        config_path = _get_config_path()
        await cp.save(cp._config, config_path)
        logger.info(f"Saved session settings to {config_path}")
    except Exception as e:
        logger.error(f"Failed to save session settings: {e}")
        raise


async def handle_get_session_settings(request: web.Request) -> web.Response:
    """GET /api/settings/session — Read session settings."""
    try:
        settings = await _read_session_settings_from_config()
        return web.json_response({
            "success": True,
            "data": settings
        })
    except Exception as e:
        logger.error(f"Failed to read session settings: {e}")
        return web.json_response(
            {"success": False, "error": {"message": str(e)}}, status=500
        )


async def handle_put_session_settings(request: web.Request) -> web.Response:
    """PUT /api/settings/session — Update session settings."""
    try:
        body = await request.json()
        if not isinstance(body, dict):
            # `null` / arrays must be 400 (client bug), not a 500 from
            # `"key" in None` inside the field checks below.
            return web.json_response(
                {"success": False, "error": {"message": "Invalid JSON body: expected an object"}},
                status=400
            )
        settings = {}

        # Validate and process compression_threshold
        if "compression_threshold" in body:
            try:
                raw = float(body["compression_threshold"])
                settings["compression_threshold"] = max(
                    _COMPRESSION_THRESHOLD_MIN, 
                    min(_COMPRESSION_THRESHOLD_MAX, raw)
                )
            except (TypeError, ValueError) as e:
                return web.json_response(
                    {"success": False, "error": {"message": f"Invalid compression_threshold: {e}"}},
                    status=400
                )

        # Validate and process default_workspace_mode
        if "default_workspace_mode" in body:
            mode = str(body["default_workspace_mode"])
            # legacy "default" (project root) removed — map to session_dir
            if mode == "default":
                mode = "session_dir"
            if mode not in ["session_dir", "shared_workspace", "custom"]:
                return web.json_response(
                    {"success": False, "error": {"message": f"Invalid workspace mode: {mode}"}},
                    status=400
                )
            settings["default_workspace_mode"] = mode

        # Process default_workspace_path
        if "default_workspace_path" in body:
            settings["default_workspace_path"] = str(body["default_workspace_path"])

        # Validate and process default_approval_mode (new-session approval mode)
        if "default_approval_mode" in body:
            mode = str(body["default_approval_mode"]).strip().lower()
            if mode not in _APPROVAL_MODES:
                return web.json_response(
                    {"success": False, "error": {"message": f"Invalid approval mode: {mode}"}},
                    status=400
                )
            settings["default_approval_mode"] = mode

        if not settings:
            return web.json_response(
                {"success": False, "error": {"message": "No valid settings provided"}},
                status=400
            )

        await _write_session_settings_to_config(settings)
        
        # Return all current settings
        current_settings = await _read_session_settings_from_config()
        return web.json_response({
            "success": True,
            "data": current_settings
        })
    except Exception as e:
        logger.error(f"Failed to update session settings: {e}")
        return web.json_response(
            {"success": False, "error": {"message": str(e)}}, status=500
        )
