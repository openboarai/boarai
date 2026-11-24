"""Session workspace resolution, validation, and directory browse.

Cross-platform helpers for:
- Default workspace policy (session_dir / shared / custom)
- Effective tool cwd for a session
- Workspace path validation
- Directory picker roots / listing (WebUI FS API)
"""
from __future__ import annotations

import logging
import platform
import string
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Hidden / noisy dirs when listing (still navigable if user enters path)
DEFAULT_HIDE_NAMES = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".idea",
        ".vscode",
        ".DS_Store",
        "Thumbs.db",
    }
)


def _ensure_dir(path_str: str) -> str:
    p = Path(path_str)
    p.mkdir(parents=True, exist_ok=True)
    return str(p.resolve())


def get_default_workspace_for_session(session_id: str) -> Optional[str]:
    """Get the configured default workspace path for a session."""
    try:
        from boarai.core.config import get_config_parser
        from boarai.core.runtime import get_data_dir

        cp = get_config_parser()
        if not cp or not cp._config or not hasattr(cp._config, "session"):
            logger.warning("get_default_workspace: ConfigParser unavailable")
            # Still provide per-session workspace under the data dir
            ws_path = get_data_dir() / "sessions" / session_id / "workspace"
            return _ensure_dir(str(ws_path))

        session_cfg = cp._config.session
        mode = getattr(session_cfg, "default_workspace_mode", "session_dir") or "session_dir"
        # Dropped "system default" (project root); old configs map to session_dir
        if mode == "default":
            mode = "session_dir"

        data_dir = get_data_dir()

        if mode == "session_dir":
            ws_path = data_dir / "sessions" / session_id / "workspace"
            return _ensure_dir(str(ws_path))

        if mode == "shared_workspace":
            ws_path = data_dir / "workspace"
            return _ensure_dir(str(ws_path))

        if mode == "custom" and session_cfg.default_workspace_path:
            custom = str(session_cfg.default_workspace_path).strip()
            if custom:
                try:
                    return _ensure_dir(custom)
                except OSError as e:
                    logger.warning("Custom default workspace not usable %r: %s", custom, e)

        # Unknown mode → session_dir
        ws_path = data_dir / "sessions" / session_id / "workspace"
        return _ensure_dir(str(ws_path))
    except Exception as e:
        logger.warning("Failed to get default workspace for %s: %s", session_id, e)
        return None


def normalize_user_path(raw: str) -> Optional[Path]:
    """Expand ~ and resolve to absolute Path. Returns None if invalid."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        if platform.system() == "Windows":
            is_unc = text.startswith("\\\\") or text.startswith("//")
            if is_unc:
                body = text.lstrip("/\\")
                text = "\\\\" + body.replace("/", "\\")
            else:
                text = text.replace("/", "\\")
        p = Path(text).expanduser()
        try:
            return p.resolve(strict=False)
        except TypeError:
            return p.resolve()
    except (OSError, RuntimeError, ValueError) as e:
        logger.debug("normalize_user_path failed for %r: %s", raw, e)
        return None


def _project_root() -> Optional[Path]:
    try:
        from boarai.core.runtime import get_project_root

        root = get_project_root()
        return Path(root).resolve() if root else None
    except Exception:
        return None


def _sandbox_root() -> Optional[Path]:
    """PathGuard workspace_root if available, else project_root."""
    try:
        from boarai.core.security import get_security_manager

        sm = get_security_manager()
        guard = getattr(sm, "path_guard", None) or getattr(sm, "_path_guard", None)
        if guard is not None:
            wr = getattr(guard, "workspace_root", None)
            if wr:
                return Path(wr).resolve()
    except Exception:
        pass
    return _project_root()


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def is_session_workspace_path(path: Path) -> bool:
    """True if path is .../sessions/{session_id}/workspace or under it."""
    try:
        parts_lower = [p.lower() for p in path.resolve().parts]
    except (OSError, RuntimeError, ValueError):
        parts_lower = [p.lower() for p in path.parts]
    try:
        idx = parts_lower.index("sessions")
    except ValueError:
        return False
    # sessions / {id} / workspace [ / ... ]
    if idx + 2 >= len(parts_lower):
        return False
    return parts_lower[idx + 2] == "workspace"


def _forbidden_for_workspace(path: Path) -> Optional[str]:
    """Hard blocks: BoarAi internal session store etc."""
    name = path.name.lower()
    parts_list = list(path.parts)
    parts_lower = [p.lower() for p in parts_list]
    if "sessions" in parts_lower:
        if is_session_workspace_path(path):
            pass  # sessions/{id}/workspace is the intentional session_dir cwd
        else:
            return "Cannot use .boarai/sessions storage as workspace"
    if name in (".ssh", ".aws", ".kube", ".docker"):
        return f"Cannot use sensitive directory: {path.name}"
    return None


def validate_workspace_path(
    raw: str,
    *,
    allow_outside_sandbox: bool = False,
) -> Tuple[bool, str, Optional[str]]:
    """Validate a directory for use as session workspace."""
    p = normalize_user_path(raw)
    if p is None:
        return False, "", "Invalid path"

    try:
        if not p.exists():
            return False, str(p), "Path does not exist"
        if not p.is_dir():
            return False, str(p), "Path is not a directory"
    except OSError as e:
        return False, str(p), f"Cannot access path: {e}"

    forbid = _forbidden_for_workspace(p)
    if forbid:
        return False, str(p), forbid

    sandbox = _sandbox_root()
    if sandbox and not allow_outside_sandbox and not _is_under(p, sandbox):
        if p.resolve() != sandbox.resolve():
            return (
                False,
                str(p),
                f"Path is outside sandbox ({sandbox}). "
                "Enable external access or choose a path under the sandbox.",
            )

    try:
        from boarai.core.security import get_security_manager

        sm = get_security_manager()
        guard = getattr(sm, "path_guard", None) or getattr(sm, "_path_guard", None)
        if guard is not None and hasattr(guard, "check_read"):
            check = guard.check_read(str(p))
            if not check.allowed:
                return False, str(p), check.reason or "Read not allowed by PathGuard"
    except Exception:
        pass

    return True, str(p.resolve()), None


def resolve_session_workspace(
    session: Any = None,
    workspace_path: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Path:
    """Effective tool cwd: session.workspace_path if set, else the session's default workspace, else project_root / cwd (last resort only)."""
    raw = workspace_path
    if raw is None and session is not None:
        raw = getattr(session, "workspace_path", None) or None
        if not raw and isinstance(getattr(session, "metadata", None), dict):
            raw = session.metadata.get("workspace_path")

    if raw:
        ok, resolved, _err = validate_workspace_path(str(raw), allow_outside_sandbox=True)
        if ok and resolved:
            return Path(resolved)
        p = normalize_user_path(str(raw))
        if p is not None:
            try:
                if p.is_dir():
                    return p
            except OSError:
                pass

    # workspace_path unset → prefer the session's default workspace (not
    # project_root), so relative paths still land in the session sandbox.
    # Resolve the session id from the explicit arg OR the session object, so
    # this works even when session is None (not in the in-memory cache).
    sid = session_id or (getattr(session, "session_id", None) if session is not None else None)
    if sid:
        try:
            default_ws = get_default_workspace_for_session(sid)
            if default_ws:
                return Path(default_ws)
        except Exception:
            logger.debug("Default workspace resolution failed for %s", sid)

    root = _project_root()
    if root:
        return root
    return Path.cwd().resolve()


def list_browse_roots(session_id: Optional[str] = None) -> List[Dict[str, str]]:
    """Entry points for the directory picker (cross-platform)."""
    roots: List[Dict[str, str]] = []
    seen: set[str] = set()

    def add(label: str, path: Path, kind: str) -> None:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen:
            return
        try:
            if not path.exists() or not path.is_dir():
                return
        except OSError:
            return
        seen.add(key)
        roots.append({"label": label, "path": key, "kind": kind})

    proj = _project_root()
    if proj:
        add("Project root", proj, "project")

    if session_id and proj:
        try:
            from boarai.core.runtime import get_data_dir
            # Prefer sessions/{id}/workspace; keep legacy workspaces/{id} if present
            candidates = [
                get_data_dir() / "sessions" / session_id / "workspace",
                get_data_dir() / "workspaces" / session_id,
            ]
            for session_ws in candidates:
                if session_ws.exists() and session_ws.is_dir():
                    add("Session workspace", session_ws, "session_workspace")
                    break
        except Exception:
            pass

    if proj:
        try:
            from boarai.core.runtime import get_data_dir
            shared_ws = get_data_dir() / "workspace"
            if shared_ws.exists() and shared_ws.is_dir():
                add("Shared workspace", shared_ws, "shared_workspace")
        except Exception:
            pass

    sandbox = _sandbox_root()
    if sandbox and (not proj or sandbox.resolve() != proj.resolve()):
        add("Sandbox", sandbox, "sandbox")

    home = Path.home()
    add("Home", home, "home")

    system = platform.system()
    if system == "Windows":
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            try:
                if drive.exists():
                    add(f"{letter}:", drive, "drive")
            except OSError:
                continue
    else:
        add("Root /", Path("/"), "root")
        vol = Path("/Volumes")
        if system == "Darwin" and vol.is_dir():
            try:
                for child in sorted(vol.iterdir()):
                    if child.is_dir():
                        add(child.name, child, "volume")
            except OSError:
                pass
        for media in (Path("/media"), Path("/mnt")):
            if media.is_dir():
                try:
                    for child in sorted(media.iterdir()):
                        if child.is_dir():
                            add(child.name, child, "media")
                except OSError:
                    pass

    return roots


def browse_directory(
    raw_path: Optional[str] = None,
    *,
    show_hidden: bool = False,
    max_entries: int = 500,
) -> Dict[str, Any]:
    """List child directories (and optional files metadata as type only)."""
    if raw_path:
        path = normalize_user_path(raw_path)
    else:
        path = _project_root() or Path.cwd()

    if path is None:
        return {
            "ok": False,
            "error": "Invalid path",
            "path": raw_path or "",
            "parent": None,
            "entries": [],
        }

    try:
        path = path.resolve()
    except OSError as e:
        return {
            "ok": False,
            "error": str(e),
            "path": str(path),
            "parent": None,
            "entries": [],
        }

    if not path.exists():
        return {
            "ok": False,
            "error": "Path does not exist",
            "path": str(path),
            "parent": str(path.parent) if path.parent != path else None,
            "entries": [],
        }

    if not path.is_dir():
        return {
            "ok": False,
            "error": "Path is not a directory",
            "path": str(path),
            "parent": str(path.parent),
            "entries": [],
        }

    try:
        from boarai.core.security import get_security_manager

        sm = get_security_manager()
        guard = getattr(sm, "path_guard", None) or getattr(sm, "_path_guard", None)
        if guard is not None and hasattr(guard, "check_read"):
            check = guard.check_read(str(path))
            if not check.allowed:
                return {
                    "ok": False,
                    "error": check.reason or "Browse not allowed",
                    "path": str(path),
                    "parent": str(path.parent) if path.parent != path else None,
                    "entries": [],
                }
    except Exception:
        pass

    entries: List[Dict[str, Any]] = []
    truncated = False
    try:
        children = list(path.iterdir())
    except PermissionError:
        return {
            "ok": False,
            "error": "Permission denied",
            "path": str(path),
            "parent": str(path.parent) if path.parent != path else None,
            "entries": [],
        }
    except OSError as e:
        return {
            "ok": False,
            "error": str(e),
            "path": str(path),
            "parent": str(path.parent) if path.parent != path else None,
            "entries": [],
        }

    def sort_key(c: Path):
        try:
            is_dir = c.is_dir()
        except OSError:
            is_dir = False
        return (0 if is_dir else 1, c.name.lower())

    children.sort(key=sort_key)

    for child in children:
        name = child.name
        if not show_hidden:
            if name.startswith(".") and name not in (".", ".."):
                continue
            if name in DEFAULT_HIDE_NAMES:
                continue
        try:
            is_dir = child.is_dir()
            is_file = child.is_file()
        except OSError:
            continue
        if not is_dir and not is_file:
            continue
        # size/mtime feed the file-manager tree (WebUI hides them when absent;
        # broken symlinks / permission quirks just yield nulls).
        size: Optional[int] = None
        mtime: Optional[int] = None
        try:
            st = child.stat()
            mtime = st.st_mtime_ns
            if is_file:
                size = st.st_size
        except OSError:
            pass
        entries.append(
            {
                "name": name,
                "path": str(child.resolve()) if is_dir else str(child),
                "type": "dir" if is_dir else "file",
                "size": size,
                "mtime": mtime,
            }
        )
        if len(entries) >= max_entries:
            truncated = True
            break

    parent = None
    if path.parent != path:
        parent = str(path.parent)

    sandbox = _sandbox_root()
    in_sandbox = bool(sandbox and _is_under(path, sandbox))

    return {
        "ok": True,
        "path": str(path),
        "parent": parent,
        "entries": entries,
        "truncated": truncated,
        "in_sandbox": in_sandbox,
        "platform": platform.system(),
    }