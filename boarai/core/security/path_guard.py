"""
PathGuard — File path sandbox for BoarAi.

Enforces workspace-scoped file access:
- Read: workspace_root ± allow_external_read (default allowed)
- Write: workspace_root only ± allow_external_write (default denied)
- Delete: workspace_root only (always denied for external)
- Forbidden paths: never accessible regardless of other rules

All paths are resolved to absolute before checking.
Glob patterns in forbidden lists support ** (recursive) and * (wildcard).
"""

import logging
import os
import re
import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PathCheckResult:
    """Result of a path safety check (v1.14 three-state)."""
    allowed: bool
    path: str = ""
    resolved_path: str = ""
    reason: str = ""
    needs_approval: bool = False

    @property
    def verdict(self) -> str:
        if not self.allowed:
            return "deny"
        return "confirm" if self.needs_approval else "allow"


class PathGuard:
    """File path sandbox enforcing workspace-scoped access rules."""

    def __init__(self, config, project_root: Path):
        self._project_root = Path(project_root).resolve()
        self._config = config
        self._workspace_root: Path = self._resolve_workspace()
        self._forbidden_read: List[str] = list(config.forbidden_read_paths or [])
        self._forbidden_write: List[str] = list(config.forbidden_write_paths or [])
        self._absolute_write: List[str] = list(
            getattr(config, "absolute_write_paths", None) or []
        )
        logger.info(
            f"PathGuard: workspace={self._workspace_root}, "
            f"read_external={config.allow_external_read}, "
            f"write_external={config.allow_external_write}"
        )

    def _resolve_workspace(self) -> Path:
        raw = self._config.workspace_root or "auto"
        if raw == "auto":
            return self._project_root
        return Path(raw).expanduser().resolve()

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    # ---- Public API ----

    def classify(
        self,
        op: str,
        path: str,
        session_id: Optional[str] = None,
        agent_allowed_paths: Optional[List[str]] = None,
    ) -> PathCheckResult:
        """Mode-independent three-state classification of one file operation."""
        resolved = self._resolve(path)
        if not resolved:
            return PathCheckResult(False, path=path, reason="Path resolution failed")

        def _allow(reason: str = "") -> PathCheckResult:
            return PathCheckResult(True, path=path, resolved_path=str(resolved), reason=reason)

        def _confirm(reason: str) -> PathCheckResult:
            return PathCheckResult(
                True, path=path, resolved_path=str(resolved),
                reason=reason, needs_approval=True,
            )

        def _deny(reason: str) -> PathCheckResult:
            return PathCheckResult(False, path=path, resolved_path=str(resolved), reason=reason)

        is_ws_leaf = self._is_session_workspace_path(resolved)
        is_own = self._is_own_session_path(resolved, session_id)

        # Absolute write floor — denies in EVERY mode (irreversible / self-mod).
        if op in ("write", "delete") and self._matches_any(resolved, self._absolute_write):
            return _deny(
                f"Absolute write floor (irreversible/OS-critical): {self._shorten(resolved)}"
            )

        # Agent allowlist expands access (never overrides the absolute floor).
        if agent_allowed_paths and self._matches_any(resolved, agent_allowed_paths):
            return _allow("Agent allowed path")

        if op == "read":
            # Reads are free; only credentials are confirm-tier (checked first
            # so an own-session-uploaded key still prompts under "ask").
            if self._matches_any(resolved, self._forbidden_read):
                return _confirm(f"Credential/sensitive read: {self._shorten(resolved)}")
            if is_ws_leaf or is_own or self._in_workspace(resolved):
                return _allow()
            if self._config.allow_external_read:
                return _allow("External read allowed by config")
            return _confirm("External read (allow_external_read=false)")

        # write / delete
        if is_ws_leaf:
            # Own (or legacy/no-session) workspace leaf is the agent cwd → allow;
            # ANOTHER session's workspace is a cross-session write → confirm.
            if session_id and not is_own:
                return _confirm("Cross-session workspace write")
            return _allow("Session workspace path")
        if self._matches_any(resolved, self._forbidden_write):
            return _confirm(f"Sensitive write target: {self._shorten(resolved)}")
        if self._in_workspace(resolved):
            return _allow()
        if self._config.allow_external_write:
            return _allow("External write allowed by config")
        return _confirm(f"External write: {self._shorten(resolved)}")

    def _apply_mode(self, res: PathCheckResult, mode: str) -> PathCheckResult:
        """Fold a mode-independent classification into an allowed/denied result."""
        if not res.allowed or not res.needs_approval:
            return res
        if mode == "off":
            return PathCheckResult(
                True, path=res.path, resolved_path=res.resolved_path,
                reason=(res.reason + " [waived by approval_mode=off]"),
                needs_approval=False,
            )
        if mode == "deny":
            return PathCheckResult(
                False, path=res.path, resolved_path=res.resolved_path,
                reason=(res.reason + " [session deny mode]"),
            )
        return res

    def check_read(
        self,
        path: str,
        agent_allowed_paths: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        mode: str = "on",
    ) -> PathCheckResult:
        """Read check (mode-aware convenience wrapper over ``classify``)."""
        return self._apply_mode(
            self.classify("read", path, session_id, agent_allowed_paths), mode
        )

    def check_write(
        self,
        path: str,
        agent_allowed_paths: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        mode: str = "on",
    ) -> PathCheckResult:
        """Write check (mode-aware convenience wrapper over ``classify``)."""
        return self._apply_mode(
            self.classify("write", path, session_id, agent_allowed_paths), mode
        )

    def check_delete(
        self,
        path: str,
        agent_allowed_paths: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        mode: str = "on",
    ) -> PathCheckResult:
        """Delete check (mode-aware convenience wrapper over ``classify``)."""
        return self._apply_mode(
            self.classify("delete", path, session_id, agent_allowed_paths), mode
        )

    def sandbox_path(self, path: str) -> str:
        """Resolve a possibly-relative path to an absolute workspace path."""
        p = Path(path)
        if p.is_absolute():
            return str(p.resolve())
        return str(self._workspace_root / p)

    # ---- Internal helpers ----

    def _resolve(self, path: str) -> Optional[Path]:
        try:
            p = Path(path).expanduser().resolve()
            return p
        except (OSError, ValueError, RuntimeError) as e:
            logger.warning(f"Path resolution failed for {path}: {e}")
            return None

    def _in_workspace(self, resolved: Path) -> bool:
        try:
            resolved.relative_to(self._workspace_root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _is_session_workspace_path(resolved: Path) -> bool:
        """Allow .../sessions/{session_id}/workspace and descendants only."""
        try:
            from boarai.core.session.workspace import is_session_workspace_path

            return is_session_workspace_path(resolved)
        except Exception:
            parts = [p.lower() for p in resolved.parts]
            try:
                idx = parts.index("sessions")
            except ValueError:
                return False
            return idx + 2 < len(parts) and parts[idx + 2] == "workspace"

    @staticmethod
    def _is_own_session_path(resolved: Path, session_id: Optional[str]) -> bool:
        """True when the path lives under .../sessions/{session_id}/."""
        if not session_id:
            return False
        parts = [p.lower() for p in resolved.parts]
        try:
            idx = parts.index("sessions")
        except ValueError:
            return False
        return idx + 1 < len(parts) and parts[idx + 1] == session_id.lower()

    @staticmethod
    def _norm_sep(s: str) -> str:
        """Normalize path separators so `/` and `\\` compare equal (esp. Windows)."""
        return s.replace("\\", "/")

    def _matches_any(self, resolved: Path, patterns: List[str]) -> bool:
        """Check if a resolved path matches any of the glob patterns."""
        # Case + separator normalize
        if os.name == "nt":
            def _normalize(s: str) -> str:
                return self._norm_sep(s).lower()
        else:
            def _normalize(s: str) -> str:
                return self._norm_sep(s)

        str_path_norm = _normalize(str(resolved))

        for pattern in patterns:
            expanded = Path(pattern).expanduser()
            # Prefer string form of original pattern for relative globs so
            # "**/*.key" style rules keep their wildcards.
            pattern_str = self._norm_sep(str(pattern))
            expanded_str = self._norm_sep(str(expanded))
            if expanded.is_absolute():
                if fnmatch.fnmatch(str_path_norm, _normalize(expanded_str)):
                    return True
                # Prefix match for directory roots (e.g. /etc/, C:/Windows/
                # System32/). MUST be a prefix, NOT a substring: a substring
                # would false-deny a project's own lib/ sbin/ usr/bin/ dirs
                # (v1.15 bug: ".../proj/lib/x.py" contains "/lib/").
                abs_norm = _normalize(expanded_str)
                if abs_norm and str_path_norm.startswith(abs_norm):
                    return True
            else:
                # Relative pattern — match against the tail of the path
                # Support **/pattern (recursive) and plain pattern
                if pattern_str.startswith("**/"):
                    tail = _normalize(pattern_str[3:])
                    if tail and tail in str_path_norm:
                        return True
                    if fnmatch.fnmatch(str_path_norm, _normalize(f"**/{pattern_str[3:]}")):
                        return True
                elif pattern_str.startswith("/"):
                    # Posix-absolute style pattern ("/etc/", "/lib/"). On
                    # Windows Path("/lib/").is_absolute() is False (no drive),
                    # so it lands here; a SUBSTRING match would false-deny a
                    # project's own lib/ sbin/ dirs (v1.15 bug). Only a PREFIX
                    # match is correct — and on Windows drive-lettered paths
                    # never prefix-match a leading "/", so these patterns
                    # correctly stay inert there.
                    pattern_norm = _normalize(pattern_str)
                    if pattern_norm and str_path_norm.startswith(pattern_norm):
                        return True
                else:
                    pattern_norm = _normalize(pattern_str)
                    # Directory-prefix style: ".boarai/sessions/" inside path
                    if pattern_norm and pattern_norm in str_path_norm:
                        return True
                    if str_path_norm.endswith(pattern_norm.rstrip("/")):
                        return True
                    if fnmatch.fnmatch(str_path_norm, f"**/{pattern_norm}"):
                        return True
        return False

    @staticmethod
    def _shorten(path: Path) -> str:
        """Shorten path for log messages — replace home with ~."""
        try:
            home = Path.home()
            return f"~/{path.relative_to(home)}"
        except (ValueError, RuntimeError):
            return str(path)
