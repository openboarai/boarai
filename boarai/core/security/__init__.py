"""
BoarAi Security Module.

Multi-layer security system for tool execution, file access, and shell commands.
Provides PathGuard (file path sandbox), ToolGuard (permission checking), and
audit logging.

Usage:
    from boarai.core.security import get_security_manager
    guard = get_security_manager()
    result = guard.check_file_read("/etc/passwd")
    if not result.allowed:
        logger.warning(result.reason)
"""

import logging
from typing import Any, Dict, List, Optional

from .path_guard import PathCheckResult, PathGuard
from .audit import AuditLog
from .tool_guard import ToolGuard, ToolPermissionResult
from .access import (
    AccessStore,
    SESSION_COOKIE,
    generate_key,
    get_access_store,
    hash_key,
    mask_key,
    reset_access_store,
    verify_key,
)

logger = logging.getLogger(__name__)


class SecurityManager:
    """Top-level security orchestrator."""

    def __init__(self):
        self._path_guard: Optional[PathGuard] = None
        self._tool_guard: Optional[ToolGuard] = None
        self._audit: Optional[AuditLog] = None
        self._initialized = False
        #: The SecurityConfig the guards were built from. Kept so runtime
        #: updates can mutate the same object the guards read (the WebUI
        #: Settings tab edits live state, not a detached copy).
        self._config = None
        #: Install root, needed to rebuild the AuditLog when its path changes.
        self._project_root = None

    def initialize(self, config, project_root) -> None:
        """Initialize security subsystems with configuration."""
        if config is None:
            from boarai.core.config.schema import SecurityConfig
            config = SecurityConfig()  # safe defaults

        self._path_guard = PathGuard(config.path_guard, project_root)
        self._tool_guard = ToolGuard(config.tool_guard, shell_config=config.shell_safety)
        self._audit = AuditLog(config.audit, project_root)
        self._config = config
        self._project_root = project_root
        self._initialized = True

        # Wire the tool registry into the guard so shell content-level checks
        # (deny patterns / joiners / network / readonly refinement inside
        # _refine_shell_decision) actually evaluate. Without this,
        # _extract_shell_command saw tool_registry=None and the ENTIRE
        # approval-layer shell inspection silently no-op'd — shell commands
        # were approved purely on the coarse agent/global posture
        # (audit-log proven).
        try:
            from boarai.core.extension.tool.registry import get_tool_registry

            self._tool_guard.set_tool_registry(get_tool_registry())
        except Exception as e:  # noqa: BLE001
            logger.warning("ToolGuard registry wiring skipped: %s", e)

        # v1.14: wire PathGuard into ToolGuard so file-tool path classification
        # runs in the gate (confirm-tier file ops prompt under "ask" instead of
        # being hard-denied after approval; "allow" waives them).
        self._tool_guard.set_path_guard(self._path_guard)

        logger.info(
            f"Security initialized: workspace={self._path_guard.workspace_root}, "
            f"approval_mode={config.tool_guard.approval_mode}"
        )

    @property
    def is_initialized(self) -> bool:
        """Whether initialize() has run; guards raise before that point."""
        return self._initialized

    @property
    def path_guard(self) -> PathGuard:
        if not self._initialized:
            raise RuntimeError("SecurityManager not initialized")
        return self._path_guard

    @property
    def tool_guard(self) -> ToolGuard:
        if not self._initialized:
            raise RuntimeError("SecurityManager not initialized")
        return self._tool_guard

    @property
    def audit(self) -> AuditLog:
        if not self._initialized:
            raise RuntimeError("SecurityManager not initialized")
        return self._audit

    def check_file_read(
        self,
        path: str,
        agent_allowed_paths: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        mode: str = "on",
    ) -> PathCheckResult:
        return self.path_guard.check_read(
            path,
            agent_allowed_paths=agent_allowed_paths,
            session_id=session_id,
            mode=mode,
        )

    def check_file_write(
        self,
        path: str,
        agent_allowed_paths: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        mode: str = "on",
    ) -> PathCheckResult:
        return self.path_guard.check_write(
            path,
            agent_allowed_paths=agent_allowed_paths,
            session_id=session_id,
            mode=mode,
        )

    def check_file_delete(
        self,
        path: str,
        agent_allowed_paths: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        mode: str = "on",
    ) -> PathCheckResult:
        return self.path_guard.check_delete(
            path,
            agent_allowed_paths=agent_allowed_paths,
            session_id=session_id,
            mode=mode,
        )

    def classify_file(
        self,
        op: str,
        path: str,
        session_id: Optional[str] = None,
        agent_allowed_paths: Optional[List[str]] = None,
    ) -> PathCheckResult:
        """Mode-independent three-state file classification (see PathGuard.classify)."""
        return self.path_guard.classify(
            op, path, session_id=session_id, agent_allowed_paths=agent_allowed_paths
        )

    def sandbox_path(self, path: str) -> str:
        return self.path_guard.sandbox_path(path)

    def check_tool(
        self,
        tool_name: str,
        agent_allowlist: Optional[List[str]] = None,
        session_overrides: Optional[Dict[str, Any]] = None,
    ) -> ToolPermissionResult:
        """Legacy result shape; the verdict itself comes from evaluate_tool."""
        return self.tool_guard.check_tool(
            tool_name, agent_allowlist,
            session_overrides=session_overrides,
        )

    def evaluate_tool(
        self,
        tool_name: str,
        agent_config: Any = None,
        session_overrides: Optional[Dict[str, Any]] = None,
    ):
        """Authoritative single-tool approval verdict (see ToolGuard.evaluate_tool)."""
        return self.tool_guard.evaluate_tool(
            tool_name, agent_config, session_overrides=session_overrides
        )

    def evaluate_batch(
        self,
        tool_calls: List[Dict[str, Any]],
        agent_config: Any = None,
        session_overrides: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ):
        """Authoritative batch approval verdict (see ToolGuard.evaluate_batch)."""
        return self.tool_guard.evaluate_batch(
            tool_calls, agent_config,
            session_overrides=session_overrides, session_id=session_id,
        )

    def audit_record(self, event: str, data: Dict[str, Any]) -> None:
        self.audit.record(event, data)

    def update_config(self, updates: Dict[str, Any], persist: bool = True) -> None:
        """Update security config at runtime and optionally persist to config.yaml."""
        if not self._initialized:
            raise RuntimeError("Security not initialized")
        if not isinstance(updates, dict) or not updates:
            raise ValueError("updates must be a non-empty dict")
        if self._tool_guard is None or self._path_guard is None:
            raise RuntimeError("Security guards not available")

        tg = updates
        if "approval_mode" in tg:
            mode = tg["approval_mode"]
            if mode not in ("on", "off"):
                raise ValueError(f"Invalid approval_mode: {mode!r}")
            self._tool_guard._config.approval_mode = mode
        if "auto_approve" in tg:
            self._tool_guard._config.auto_approve = bool(tg["auto_approve"])
        if "max_tool_calls" in tg:
            n = int(tg["max_tool_calls"])
            if n < 0 or n > 100000:
                raise ValueError("max_tool_calls must be between 0 (unlimited) and 100000")
            self._tool_guard._config.max_tool_calls_per_run = n
        if "approval_timeout" in tg:
            # Legacy (superseded by pending_timeout) — kept for compat.
            t = float(tg["approval_timeout"])
            if t < 1 or t > 3600:
                raise ValueError("approval_timeout must be between 1 and 3600")
            self._tool_guard._config.approval_timeout = t
        if "pending_timeout" in tg:
            t = float(tg["pending_timeout"])
            if t < 1 or t > 86400:
                raise ValueError("pending_timeout must be between 1 and 86400")
            self._tool_guard._config.pending_timeout = t
        if "network_approval" in tg:
            if not isinstance(tg["network_approval"], bool):
                raise ValueError("network_approval must be a boolean")
            self._tool_guard._shell_config.network_approval = tg["network_approval"]
        if "block_external_absolute_paths" in tg:
            if not isinstance(tg["block_external_absolute_paths"], bool):
                raise ValueError(
                    "block_external_absolute_paths must be a boolean"
                )
            self._tool_guard._shell_config.block_external_absolute_paths = (
                tg["block_external_absolute_paths"]
            )
        if "unattended_policy" in tg:
            policy = str(tg["unattended_policy"]).lower()
            if policy not in ("ask", "deny", "approve"):
                raise ValueError(f"Invalid unattended_policy: {policy!r}")
            self._tool_guard._config.unattended_policy = policy
        if "allow_external_write" in tg:
            self._path_guard._config.allow_external_write = bool(tg["allow_external_write"])
        if "allow_external_read" in tg:
            self._path_guard._config.allow_external_read = bool(tg["allow_external_read"])
        if "error_streak_policy" in tg:
            policy = str(tg["error_streak_policy"]).lower()
            if policy not in ("warn", "block"):
                raise ValueError(
                    "error_streak_policy must be one of ['warn', 'block']"
                )
            self._tool_guard._config.error_streak_policy = policy

        # ---- WebUI access gate / audit / shell backend / path lists (v1.16).
        # Every branch mutates the LIVE object the guard reads, not just the
        # config dataclass: a save must take effect without a restart, or the
        # Settings page would lie about the policy in force.
        sec_cfg = self._config
        if sec_cfg is None:
            raise RuntimeError("Security config unavailable")

        web_cfg = getattr(sec_cfg, "web", None)
        if "trust_loopback" in tg:
            if not isinstance(tg["trust_loopback"], bool):
                raise ValueError("trust_loopback must be a boolean")
            if web_cfg is None:
                raise RuntimeError("Web access config unavailable")
            web_cfg.trust_loopback = tg["trust_loopback"]
        if "session_ttl_max_seconds" in tg:
            try:
                n = int(tg["session_ttl_max_seconds"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "session_ttl_max_seconds must be an integer"
                ) from exc
            if n < 60 or n > 2592000:
                raise ValueError(
                    "session_ttl_max_seconds must be between 60 and 2592000"
                )
            if web_cfg is None:
                raise RuntimeError("Web access config unavailable")
            web_cfg.session_ttl_max_seconds = n
            # A cap below the current lifetime would leave every new cookie
            # longer-lived than the policy allows.
            if int(getattr(web_cfg, "session_ttl_seconds", 0) or 0) > n:
                web_cfg.session_ttl_seconds = n

        audit_cfg = getattr(sec_cfg, "audit", None)
        if "audit_enabled" in tg:
            if not isinstance(tg["audit_enabled"], bool):
                raise ValueError("audit_enabled must be a boolean")
            if audit_cfg is None:
                raise RuntimeError("Audit config unavailable")
            audit_cfg.enabled = tg["audit_enabled"]
            self._audit.set_enabled(tg["audit_enabled"])
        if "audit_events" in tg:
            events = self._string_list(tg["audit_events"], "audit_events")
            if audit_cfg is None:
                raise RuntimeError("Audit config unavailable")
            audit_cfg.events = events
            self._audit.set_events(events)
        if "audit_log_path" in tg:
            path = str(tg["audit_log_path"] or "").strip()
            if not path:
                raise ValueError("audit_log_path must not be empty")
            if audit_cfg is None:
                raise RuntimeError("Audit config unavailable")
            audit_cfg.log_path = path
            # The writer keeps the resolved base dir, so the path change only
            # takes effect through a new instance.
            self._audit = AuditLog(audit_cfg, self._project_root)

        shell_cfg = getattr(sec_cfg, "shell_safety", None)
        backend_changed = any(
            key in tg for key in ("shell_backend", "docker_image", "docker_network")
        )
        if backend_changed:
            if shell_cfg is None:
                raise RuntimeError("Shell safety config unavailable")
            if "shell_backend" in tg:
                backend = str(tg["shell_backend"] or "").strip().lower()
                if backend not in ("local", "docker"):
                    raise ValueError(
                        "shell_backend must be one of ['local', 'docker']"
                    )
                shell_cfg.backend = backend
            if "docker_image" in tg:
                image = str(tg["docker_image"] or "").strip()
                if not image or any(ch.isspace() for ch in image):
                    raise ValueError("docker_image must be a non-empty image reference")
                shell_cfg.docker_image = image
            if "docker_network" in tg:
                if not isinstance(tg["docker_network"], bool):
                    raise ValueError("docker_network must be a boolean")
                shell_cfg.docker_network = tg["docker_network"]
            self._reconfigure_shell_backend()

        if "absolute_write_paths" in tg:
            paths = self._string_list(tg["absolute_write_paths"], "absolute_write_paths")
            # Protect-the-protector: the floor exists so the agent cannot
            # rewrite the guards that police it. Dropping that entry turns
            # every other check into a suggestion. The trailing slash matters:
            # a substring check would let ".boarai/config-other/" satisfy it
            # while the real config directory stays unprotected.
            if not any(
                ".boarai/config/" in entry.replace("\\", "/") for entry in paths
            ):
                raise ValueError(
                    "absolute_write_paths must keep the security-config floor "
                    "(.boarai/config/); removing it would let the agent "
                    "rewrite its own guards"
                )
            self._path_guard._absolute_write = paths
            self._path_guard._config.absolute_write_paths = paths
        if "forbidden_read_paths" in tg:
            paths = self._string_list(
                tg["forbidden_read_paths"], "forbidden_read_paths"
            )
            self._path_guard._forbidden_read = paths
            self._path_guard._config.forbidden_read_paths = paths
        if "forbidden_write_paths" in tg:
            paths = self._string_list(
                tg["forbidden_write_paths"], "forbidden_write_paths"
            )
            self._path_guard._forbidden_write = paths
            self._path_guard._config.forbidden_write_paths = paths

        logger.info(
            "Security config updated at runtime: %s",
            {k: updates[k] for k in updates},
        )

        if persist:
            self._persist_config(updates)

    @staticmethod
    def _string_list(value: Any, field: str, allow_empty: bool = False) -> List[str]:
        """Validate a list-of-strings config field (trimmed, blanks dropped)."""
        if not isinstance(value, list):
            raise ValueError(f"{field} must be a list of strings")
        out = [str(item).strip() for item in value if str(item).strip()]
        if not out and not allow_empty:
            raise ValueError(f"{field} must not be empty")
        return out

    def _reconfigure_shell_backend(self) -> None:
        """Re-select the shell backend from the live shell_safety config."""
        from boarai.core.shell import configure_backend

        cfg = self._config.shell_safety
        configure_backend(
            getattr(cfg, "backend", "local"),
            image=getattr(cfg, "docker_image", "python:3.12-slim"),
            container=getattr(cfg, "docker_container", "") or None,
            workdir=getattr(cfg, "docker_workdir", "/work"),
            mounts=tuple(getattr(cfg, "docker_mounts", []) or ()),
            network=bool(getattr(cfg, "docker_network", False)),
        )
        logger.info("Shell backend switched at runtime: %s", cfg.backend)

    def _persist_config(self, updates: Dict[str, Any]) -> None:
        """Mirror runtime security updates into ConfigParser in-memory config."""
        try:
            from boarai.core.config import get_config_parser
            cp = get_config_parser()
            if not cp or not getattr(cp, "_config", None):
                logger.warning("ConfigParser unavailable; security update not persisted")
                return

            cfg = cp._config
            sec = getattr(cfg, "security", None)
            if sec is None:
                return

            if "approval_mode" in updates:
                sec.tool_guard.approval_mode = updates["approval_mode"]
            if "auto_approve" in updates:
                sec.tool_guard.auto_approve = bool(updates["auto_approve"])
            if "max_tool_calls" in updates:
                sec.tool_guard.max_tool_calls_per_run = int(updates["max_tool_calls"])
            if "approval_timeout" in updates:
                sec.tool_guard.approval_timeout = float(updates["approval_timeout"])
            if "pending_timeout" in updates:
                sec.tool_guard.pending_timeout = float(updates["pending_timeout"])
            if "network_approval" in updates:
                if hasattr(sec, "shell_safety"):
                    sec.shell_safety.network_approval = bool(
                        updates["network_approval"]
                    )
            if "block_external_absolute_paths" in updates:
                if hasattr(sec, "shell_safety"):
                    sec.shell_safety.block_external_absolute_paths = bool(
                        updates["block_external_absolute_paths"]
                    )
            if "unattended_policy" in updates:
                sec.tool_guard.unattended_policy = str(updates["unattended_policy"])
            if "allow_external_write" in updates:
                sec.path_guard.allow_external_write = bool(updates["allow_external_write"])
            if "allow_external_read" in updates:
                sec.path_guard.allow_external_read = bool(updates["allow_external_read"])
            if "error_streak_policy" in updates:
                sec.tool_guard.error_streak_policy = str(
                    updates["error_streak_policy"]
                )
            if "trust_loopback" in updates:
                sec.web.trust_loopback = bool(updates["trust_loopback"])
            if "session_ttl_max_seconds" in updates:
                sec.web.session_ttl_max_seconds = int(
                    updates["session_ttl_max_seconds"]
                )
            if "audit_enabled" in updates:
                sec.audit.enabled = bool(updates["audit_enabled"])
            if "audit_events" in updates:
                sec.audit.events = list(updates["audit_events"])
            if "audit_log_path" in updates:
                sec.audit.log_path = str(updates["audit_log_path"])
            if "shell_backend" in updates:
                sec.shell_safety.backend = str(updates["shell_backend"])
            if "docker_image" in updates:
                sec.shell_safety.docker_image = str(updates["docker_image"])
            if "docker_network" in updates:
                sec.shell_safety.docker_network = bool(updates["docker_network"])
            if "absolute_write_paths" in updates:
                sec.path_guard.absolute_write_paths = list(
                    updates["absolute_write_paths"]
                )
            if "forbidden_read_paths" in updates:
                sec.path_guard.forbidden_read_paths = list(
                    updates["forbidden_read_paths"]
                )
            if "forbidden_write_paths" in updates:
                sec.path_guard.forbidden_write_paths = list(
                    updates["forbidden_write_paths"]
                )

            import asyncio
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and not loop.is_closed():
                loop.create_task(self._async_save_config(cp))
            else:
                logger.warning("No running event loop; security config only updated in memory")
        except Exception as e:
            logger.error(f"Failed to persist security config: {e}")

    @staticmethod
    async def _async_save_config(cp) -> None:
        try:
            path = getattr(cp, "config_path", None)
            if path is None:
                logger.warning("No config_path on ConfigParser; skip security persist")
                return
            await cp.save(cp._config, path)
            logger.info(f"Persisted security config to {path}")
        except Exception as e:
            logger.error(f"Security config save failed: {e}")


_security_manager: Optional[SecurityManager] = None


def get_security_manager() -> SecurityManager:
    global _security_manager
    if _security_manager is None:
        _security_manager = SecurityManager()
    return _security_manager


def reset_security_manager() -> None:
    global _security_manager
    _security_manager = None


__all__ = [
    "SecurityManager", "get_security_manager", "reset_security_manager",
    "PathCheckResult", "PathGuard", "AuditLog",
    # WebUI edge access gate
    "AccessStore", "SESSION_COOKIE",
    "generate_key", "get_access_store", "hash_key", "mask_key",
    "reset_access_store", "verify_key",
]
