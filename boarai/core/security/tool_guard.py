"""
ToolGuard — Runtime tool execution permission check.

Performs layered checks before a tool is allowed to run:
1. Agent tool_allowlist (zone2_tool_names)
2. Global forbidden_tools
3. Tool category (readonly/safe/dangerous)
4. Approval mode (auto / ask user)
5. Rate limits (max calls per run, consecutive errors)
"""

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from boarai.core.config.schema import ShellSafetyConfig, ToolGuardConfig
from boarai.core.security.shell_tokenize import segment_text, tokenize_shell_segments

logger = logging.getLogger(__name__)

# v1.13: reason prefix stamped on confirm-floor hits. Kept as a stable marker
# for confirm-tier reasons (tests + any future triage layer key off it).
CONFIRM_REASON_PREFIX = "Shell command matches confirm pattern"

# Home-directory spellings that all denote the same place as ``~``. The
# credential-read confirm floor is written against ``~/.ssh/`` and friends, so
# every equivalent spelling has to reach that pattern or it falls through to the
# readonly auto-allow and reads a private key with no prompt.
_HOME_ALIAS_RE = re.compile(
    r"""
      \$\{HOME\}                                    # ${HOME}
    | \$HOME\b                                      # $HOME
    | \$env:USERPROFILE\b                           # PowerShell
    | \$env:HOME\b                                  # PowerShell
    | %USERPROFILE%                                 # cmd.exe
    | /root\b                                       # root's home
    | /home/[A-Za-z0-9._-]+                         # any POSIX user home
    | [A-Za-z]:[\\/]+Users[\\/]+[A-Za-z0-9._-]+     # any Windows profile
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _fold_home_aliases(command: str) -> str:
    """Rewrite every home-directory spelling in ``command`` to ``~``."""
    folded = _HOME_ALIAS_RE.sub("~", command)
    try:
        literal = str(Path.home())
    except (RuntimeError, OSError):
        return folded
    if len(literal) > 1 and literal in folded:
        folded = folded.replace(literal, "~")
        # Windows reports C:\Users\name; a command may spell it with forward
        # slashes, which the literal replace above would not catch.
        alt = literal.replace("\\", "/")
        if alt != literal and alt in folded:
            folded = folded.replace(alt, "~")
    return folded


def _posix_separators(command: str) -> str:
    """Fold backslashes to forward slashes."""
    return command.replace("\\", "/")


@dataclass
class ToolPermissionResult:
    """Result of a tool permission check."""
    allowed: bool
    reason: str = ""
    requires_approval: bool = False
    category: str = "unknown"  # "readonly", "safe", "dangerous", "forbidden"
    warning: str = ""  # non-blocking hint (error-streak "warn" policy)
    grantable: bool = True  # may a session-scope "always allow" cover this?


@dataclass
class ApprovalDecision:
    """Single-tool verdict from the authoritative approval evaluator."""

    tool_name: str
    verdict: str = "need_approval"
    category: str = "unknown"
    reason: str = ""
    grantable: bool = True


class ToolGuard:
    """Runtime tool permission checker."""

    def __init__(self, config: ToolGuardConfig,
                 shell_config: Optional[ShellSafetyConfig] = None,
                 tool_registry: Any = None,
                 path_guard: Any = None):
        self._config = config
        self._shell_config = shell_config or ShellSafetyConfig()
        self._tool_registry = tool_registry
        self._path_guard = path_guard
        self._build_category_index()

    def set_tool_registry(self, tool_registry: Any) -> None:
        """Set the tool registry for category fallback lookups."""
        self._tool_registry = tool_registry

    def set_path_guard(self, path_guard: Any) -> None:
        """Set the PathGuard so file-tool path classification can run in the gate (v1.14): confirm-tier file ops must prompt under 'ask', not be hard-denied after approval."""
        self._path_guard = path_guard

    @property
    def approval_timeout(self) -> float:
        """User approval timeout in seconds (config-backed)."""
        return float(getattr(self._config, "approval_timeout", 30.0) or 30.0)

    @property
    def pending_timeout_seconds(self) -> float:
        """How long a gated batch may suspend awaiting a user decision."""
        return float(getattr(self._config, "pending_timeout", 600.0) or 600.0)

    @property
    def unattended_policy_value(self) -> str:
        """Normalized unattended policy: one of ``ask`` / ``deny`` / ``approve``."""
        policy = str(
            getattr(self._config, "unattended_policy", "ask") or "ask"
        ).lower()
        return policy if policy in ("ask", "deny", "approve") else "ask"

    def extract_shell_command(
        self, tool_call: Dict[str, Any], tool_name: str
    ) -> "tuple[Optional[str], Optional[str]]":
        """Public face of the shell-command extraction used by grant signatures."""
        return self._extract_shell_command(tool_call, tool_name)

    def _build_category_index(self) -> None:
        """Build a reverse index: tool_name → category (from user config)."""
        self._category_map: Dict[str, str] = {}
        for name in self._config.readonly_tools or []:
            self._category_map[name] = "readonly"
        for name in self._config.safe_tools or []:
            self._category_map[name] = "safe"
        for name in self._config.dangerous_tools or []:
            self._category_map[name] = "dangerous"
        for name in self._config.forbidden_tools or []:
            self._category_map[name] = "forbidden"

    # ---- Public API ----

    def check_tool(
        self,
        tool_name: str,
        agent_allowlist: Optional[List[str]] = None,
        session_overrides: Optional[Dict[str, Any]] = None,
    ) -> ToolPermissionResult:
        """Legacy shape of :meth:`evaluate_tool` — kept for callers/tests only."""
        from types import SimpleNamespace

        agent_config = SimpleNamespace(
            zone2_tool_names=agent_allowlist,
        )
        decision = self.evaluate_tool(
            tool_name, agent_config, session_overrides=session_overrides
        )
        return ToolPermissionResult(
            allowed=decision.verdict != "denied",
            reason=decision.reason,
            requires_approval=decision.verdict == "need_approval",
            category=decision.category,
            grantable=decision.grantable,
        )

    # ---- Approval evaluation (authoritative source) ----

    def _resolve_category(self, tool_name: str) -> str:
        """Resolve a tool's safety category."""
        category = self._category_map.get(tool_name)
        if category is None and self._tool_registry:
            try:
                tool = self._tool_registry.get_tool(tool_name)
                if tool and getattr(tool, "definition", None):
                    category = getattr(tool.definition, "suggested_category", None)
            except Exception:  # noqa: BLE001
                category = None
        return category or "unknown"

    def evaluate_tool(
        self,
        tool_name: str,
        agent_config: Any = None,
        session_overrides: Optional[Dict[str, Any]] = None,
    ) -> ApprovalDecision:
        """Decide whether one tool call needs user approval."""
        # 1. Global / session forbidden — hard block in every mode.
        s_overrides = (
            session_overrides if isinstance(session_overrides, dict) else None
        )
        session_forbidden = (s_overrides or {}).get("forbidden_tools") or []
        if tool_name in session_forbidden:
            return ApprovalDecision(
                tool_name, "denied", category="forbidden",
                reason=f"Tool '{tool_name}' is forbidden for this session",
            )
        if self._category_map.get(tool_name) == "forbidden" or tool_name in (
            self._config.forbidden_tools or []
        ):
            return ApprovalDecision(
                tool_name, "denied", category="forbidden",
                reason=f"Tool '{tool_name}' is globally forbidden",
            )

        allowlist = getattr(agent_config, "zone2_tool_names", None)
        allowed_categories = getattr(agent_config, "allowed_categories", None) or []
        can_auto_approve = bool(getattr(agent_config, "can_auto_approve", False))

        # 2. Agent hard boundary (the tool allowlist) runs BEFORE any
        #    session-mode logic: a session override governs *prompting*, it
        #    can never loosen the agent's tool-access capability. "*" (the
        #    default) means every tool; an explicit list is always enforced.
        if allowlist is not None and "*" not in allowlist:
            if tool_name not in allowlist:
                return ApprovalDecision(
                    tool_name, "denied", category="denied_by_agent",
                    reason=f"Agent not allowed to use tool '{tool_name}'",
                )

        # 3. Session approval mode — explicit user intent, authoritative over
        #    the agent allow/ask/can_auto_approve shortcuts and the global
        #    auto_approve shortcut below.
        session_mode = None
        if s_overrides:
            m = s_overrides.get("approval_mode")
            if m in ("on", "off", "deny"):
                session_mode = m

        # Session "deny": strictest mode — readonly/safe tools never require
        # approval so they stay allowed; everything else is rejected without
        # prompting.
        if session_mode == "deny":
            category = self._resolve_category(tool_name)
            if category in ("readonly", "safe"):
                return ApprovalDecision(
                    tool_name, "auto_allow", category=category,
                    reason=f"Category '{category}' is safe (session deny mode)",
                )
            return ApprovalDecision(
                tool_name, "denied", category=category,
                reason="Session approval mode: deny",
            )

        # Session "off": the user explicitly disabled prompting for this
        # session — no agent shortcut or content check may resurrect a prompt
        # (forbidden lists and shell hard-denials still apply elsewhere).
        if session_mode == "off":
            return ApprovalDecision(
                tool_name, "auto_allow",
                category=self._resolve_category(tool_name),
                reason="Session approval mode is off",
            )

        # Session "on": gate by category, suppressing every looseness
        # shortcut (agent allow/ask, allowed_categories, can_auto_approve,
        # global auto_approve) — an explicit session mode re-enables gating.
        if session_mode == "on":
            category = self._resolve_category(tool_name)
            if category in ("readonly", "safe"):
                return ApprovalDecision(
                    tool_name, "auto_allow", category=category,
                    reason=f"Category '{category}' is safe",
                )
            return ApprovalDecision(
                tool_name, "need_approval", category=category,
                reason=f"Tool '{tool_name}' requires user approval",
            )

        # 4. No session override → the agent/global ladder.
        category = self._resolve_category(tool_name)

        # Agent allowed_categories (readonly/safe auto-approve).
        if allowed_categories and category in allowed_categories:
            return ApprovalDecision(
                tool_name, "auto_allow", category=category,
                reason=f"Category '{category}' allowed for agent",
            )

        # Global approval mode.
        gbl_mode = self._config.approval_mode or "on"
        if gbl_mode == "off":
            return ApprovalDecision(
                tool_name, "auto_allow", category=category,
                reason="Global approval disabled",
            )
        if self._config.auto_approve:
            return ApprovalDecision(
                tool_name, "auto_allow", category=category,
                reason="Global approval disabled",
            )

        # Agent can_auto_approve.
        if can_auto_approve:
            return ApprovalDecision(
                tool_name, "auto_allow", category=category,
                reason="Agent can auto-approve",
            )

        # Category-based decision.
        if category in ("readonly", "safe"):
            return ApprovalDecision(
                tool_name, "auto_allow", category=category,
                reason=f"Category '{category}' is safe",
            )
        return ApprovalDecision(
            tool_name, "need_approval", category=category,
            reason=f"Tool '{tool_name}' requires user approval",
        )

    def effective_approval_mode(
        self, session_overrides: Optional[Dict[str, Any]] = None
    ) -> str:
        """Effective approval mode: explicit session override > global config."""
        if isinstance(session_overrides, dict):
            m = session_overrides.get("approval_mode")
            if m in ("on", "off", "deny"):
                return m
        return self._config.approval_mode or "on"

    def evaluate_batch(
        self,
        tool_calls: List[Dict[str, Any]],
        agent_config: Any = None,
        session_overrides: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> List[ApprovalDecision]:
        """Evaluate a whole tool-call batch (one LLM turn)."""
        # F1: when the user explicitly disabled approvals (session override or
        # global config), content checks may still HARD-DENY the absolute floor
        # but must never resurrect a prompt. Session "deny" shares the waiver:
        # its readonly/safe auto-allows must not be re-escalated into a prompt
        # (deny mode is by definition prompt-free), and its confirm-tier ops
        # are rejected rather than prompted.
        _mode = self.effective_approval_mode(session_overrides)
        approval_off = _mode in ("off", "deny")
        decisions: List[ApprovalDecision] = []
        for tc in tool_calls or []:
            func = tc.get("function", {}) or {}
            name = func.get("name", "") or tc.get("name", "")
            if not name:
                decisions.append(
                    ApprovalDecision(
                        "", "denied", category="unknown",
                        reason="Tool call missing a name",
                    )
                )
                continue
            decision = self.evaluate_tool(
                name, agent_config, session_overrides=session_overrides
            )
            # Content-level refinement. A `denied` verdict is never downgraded
            # EXCEPT the session-"deny" readonly rescue (a readonly shell/file
            # op still runs — "deny" blocks dangerous ops, not `ls`).
            if decision.verdict != "denied":
                self._refine_decision(
                    decision, tc, name, _mode, approval_off, session_id,
                    agent_config=agent_config,
                )
            elif decision.reason == "Session approval mode: deny":
                self._refine_decision(
                    decision, tc, name, _mode, approval_off, session_id,
                    readonly_only=True, agent_config=agent_config,
                )
            decisions.append(decision)
        return decisions

    def _refine_decision(
        self,
        decision: "ApprovalDecision",
        tool_call: Dict[str, Any],
        tool_name: str,
        mode: str,
        approval_off: bool,
        session_id: Optional[str] = None,
        readonly_only: bool = False,
        agent_config: Any = None,
    ) -> None:
        """Dispatch content refinement by the tool's security profile."""
        profile = self._tool_profile(tool_name)
        if profile == "shell":
            self._refine_shell_decision(
                decision, tool_call, tool_name, approval_off, readonly_only,
                session_id=session_id,
            )
        elif profile in ("file_read", "file_write", "file_delete"):
            self._refine_file_decision(
                decision, tool_call, tool_name, profile,
                mode, readonly_only, session_id,
                agent_config=agent_config,
            )

    def _tool_profile(self, tool_name: str) -> Optional[str]:
        """Return the tool's security_profile value, or None."""
        if self._tool_registry is None:
            return None
        try:
            tool = self._tool_registry.get_tool(tool_name)
        except Exception:  # noqa: BLE001
            return None
        if not tool or not getattr(tool, "definition", None):
            return None
        return getattr(tool.definition, "security_profile", None)

    def _refine_file_decision(
        self,
        decision: "ApprovalDecision",
        tool_call: Dict[str, Any],
        tool_name: str,
        profile: str,
        mode: str,
        readonly_only: bool,
        session_id: Optional[str],
        agent_config: Any = None,
    ) -> None:
        """Refine a file-tool verdict via PathGuard three-state classification."""
        if self._path_guard is None:
            return
        op = {"file_read": "read", "file_write": "write", "file_delete": "delete"}[profile]
        # v1.15 fix: classify EVERY path argument, not just path_param. Tools
        # like file_copy/file_move declare path_param="source" but also take a
        # "destination" that is a WRITE target — leaving it unchecked let a
        # copy/move write into .boarai/config (protect-the-protector) or
        # another session via an absolute destination under project root.
        paths = self._extract_file_paths(tool_call, tool_name)
        if not paths:
            return
        # v1.16: the agent's own path allowlist must expand this gate too.
        # Without it the registry (which does pass it) and this gate disagreed:
        # a path the agent was configured to use still prompted here, so
        # "always allow" looked broken while the call ran fine anyway.
        agent_paths = (
            getattr(agent_config, "allowed_paths", None)
            if agent_config is not None
            else None
        )
        worst = None  # (rank, result); deny > confirm > allow
        for path in paths:
            try:
                res = self._path_guard.classify(
                    op, path, session_id=session_id,
                    agent_allowed_paths=agent_paths,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("PathGuard classify failed for %s: %s", tool_name, e)
                continue
            rank = 0 if res.allowed and not res.needs_approval else (
                1 if res.needs_approval else 2
            )
            if worst is None or rank > worst[0]:
                worst = (rank, res)
        if worst is None:
            return
        res = worst[1]

        if not res.allowed:
            decision.verdict = "denied"
            decision.category = "dangerous"
            decision.reason = res.reason
        elif res.needs_approval:
            if mode == "off":
                decision.reason = (
                    f"{decision.reason}; " if decision.reason else ""
                ) + "file path check waived by approval_mode=off"
                return
            if mode == "deny" or readonly_only:
                decision.verdict = "denied"
                decision.category = "dangerous"
                decision.reason = res.reason + " [session deny mode]"
                return
            decision.verdict = "need_approval"
            decision.category = "dangerous"
            decision.reason = res.reason
            decision.grantable = False
        elif decision.verdict == "need_approval" and decision.category in (
            "dangerous", "unknown"
        ):
            # In-workspace op: don't prompt for the agent's ordinary file work.
            decision.verdict = "auto_allow"
            decision.category = "safe"
            decision.reason = "File operation inside workspace"

    def _extract_file_paths(
        self, tool_call: Dict[str, Any], tool_name: str
    ) -> List[str]:
        """Return ALL path arguments for a file-profile tool."""
        if self._tool_registry is None:
            return []
        try:
            tool = self._tool_registry.get_tool(tool_name)
        except Exception:  # noqa: BLE001
            return []
        if not tool or not getattr(tool, "definition", None):
            return []
        path_param = getattr(tool.definition, "path_param", None) or "path"
        func = tool_call.get("function", {}) or {}
        arguments = func.get("arguments", "")
        if isinstance(arguments, str):
            try:
                import json as _json

                args = _json.loads(arguments) if arguments else {}
            except (ValueError, TypeError):
                args = {}
        elif isinstance(arguments, dict):
            args = arguments
        else:
            args = {}
        out: List[str] = []
        for key in (path_param, "destination"):
            val = args.get(key)
            if isinstance(val, str) and val.strip() and val not in out:
                out.append(val)
        return out

    #: File-tool profiles whose path argument can be granted at folder scope.
    #: Reads are EXCLUDED: a "read this folder" grant would silently waive the
    #: credential-confirm tier for every sibling file (approve ``known_hosts``
    #: once, read ``id_rsa`` forever after). Shell commands have no directory
    #: semantic at all — their grant is the command headword.
    _FOLDER_GRANT_PROFILES = frozenset({"file_write", "file_delete"})

    def folder_grant_for(
        self,
        tool_call: Dict[str, Any],
        tool_name: str,
        session_id: Optional[str] = None,
    ) -> Optional[str]:
        """Directory an "always allow this folder" grant would cover, or None."""
        profile = self._tool_profile(tool_name)
        if profile not in self._FOLDER_GRANT_PROFILES:
            return None
        paths = self._extract_file_paths(tool_call, tool_name)
        if not paths:
            return None
        resolved: List[Path] = []
        for raw in paths:
            try:
                path = Path(raw).expanduser()
                if not path.is_absolute():
                    return None
                resolved.append(path.resolve())
            except (OSError, ValueError, RuntimeError):
                return None
        if len({item.parent for item in resolved}) > 1:
            return None  # copy/move across directories: destination unpinned
        if self._path_guard is None:
            return None  # no classifier → no persistent waiver (fail closed)
        op = "delete" if profile == "file_delete" else "write"
        try:
            res = self._path_guard.classify(
                op, str(resolved[0]), session_id=session_id
            )
        except Exception:  # noqa: BLE001 - a failed classification grants nothing
            return None
        if not res.allowed or not res.needs_approval:
            return None
        if not res.reason.startswith("External"):
            return None
        return str(resolved[0].parent)

    @staticmethod
    def folder_grant_key(tool_name: str, folder: str) -> str:
        """Stable grant key for one tool + directory."""
        return f"{tool_name}:dir:{os.path.normcase(folder)}"

    def _refine_shell_decision(
        self,
        decision: "ApprovalDecision",
        tool_call: Dict[str, Any],
        tool_name: str,
        approval_off: bool = False,
        readonly_only: bool = False,
        session_id: Optional[str] = None,
    ) -> None:
        """Refine a verdict with shell content inspection (P0-1 fix)."""
        cmd, shell_param = self._extract_shell_command(tool_call, tool_name)
        if cmd is None:
            return
        sandbox = self._sandbox_roots(session_id)
        roots = sandbox.readable() if sandbox is not None else ()
        writable = sandbox.writable() if sandbox is not None else ()
        check = self.check_shell_command(
            cmd, project_root=None, readable_roots=roots,
            writable_roots=writable,
        )
        if not check.allowed:
            # Absolute floor (machine-destroying): denies in EVERY mode,
            # including a session "deny" whose tool verdict is already denied.
            decision.verdict = "denied"
            decision.category = check.category or "dangerous"
            decision.reason = check.reason
        elif check.requires_approval:
            if approval_off or readonly_only:
                # Off means off: keep the existing verdict, note the waiver.
                # readonly_only (session "deny"): no prompt may be created —
                # the denied verdict stands for gated commands.
                decision.reason = (
                    f"{decision.reason}; " if decision.reason else ""
                ) + (
                    "shell content check waived by approval_mode=off"
                    if approval_off else
                    "shell content check waived by session deny mode"
                )
                return
            decision.verdict = "need_approval"
            decision.category = check.category or "dangerous"
            decision.reason = check.reason
            # Grantable follows the content check: a network-approval command
            # is grantable because the 2-token signature pins the host
            # (``shell_execute:curl api.com``); a chained command is NOT
            # grantable because the signature only covers the first segment
            # — the tail of the chain is re-evaluated every time.
            decision.grantable = check.grantable
        elif check.category == "readonly" and decision.category in ("dangerous", "unknown"):
            # Readonly command downgrades a dangerous/unknown *tool* category to
            # auto_allow — but never overrides agent "ask" mode (category="ask").
            decision.verdict = "auto_allow"
            decision.category = "readonly"
            decision.reason = "Shell command is readonly"
        elif check.category == "contained" and decision.category in ("dangerous", "unknown"):
            # Same downgrade shape as readonly, but the reason names the actual
            # justification: an audit log that said "readonly" for a command we
            # could not even parse would be actively misleading.
            decision.verdict = "auto_allow"
            decision.category = "contained"
            decision.reason = check.reason

    def _extract_shell_command(
        self, tool_call: Dict[str, Any], tool_name: str
    ) -> "tuple[Optional[str], Optional[str]]":
        """Return (command, shell_param) for a shell-profile tool, else (None, None)."""
        if self._tool_registry is None:
            return None, None
        try:
            tool = self._tool_registry.get_tool(tool_name)
        except Exception:  # noqa: BLE001
            return None, None
        if not tool or not getattr(tool, "definition", None):
            return None, None
        if tool.definition.security_profile != "shell":
            return None, None
        shell_param = tool.definition.shell_param or "command"
        func = tool_call.get("function", {}) or {}
        arguments = func.get("arguments", "")
        if isinstance(arguments, str):
            try:
                import json as _json

                args = _json.loads(arguments) if arguments else {}
            except (ValueError, TypeError):
                args = {}
        elif isinstance(arguments, dict):
            args = arguments
        else:
            args = {}
        cmd = args.get(shell_param)
        if not isinstance(cmd, str) or not cmd.strip():
            return None, None
        return cmd, shell_param

    def _sandbox_roots(self, session_id: Optional[str] = None):
        """Resolve the sandbox roots for shell content checks, or ``None``."""
        try:
            from boarai.core.runtime import get_project_root
            from boarai.core.security.path_utils import get_temp_dir, resolve_sandbox_roots

            pr = get_project_root()
            workspace = None
            if session_id:
                try:
                    from boarai.core.session.manager import get_session_manager
                    from boarai.core.session.workspace import resolve_session_workspace

                    session = get_session_manager().peek_session(session_id)
                    workspace = resolve_session_workspace(
                        session=session, session_id=session_id
                    )
                except Exception as exc:  # noqa: BLE001 - project root still applies
                    logger.debug("Session workspace roots unavailable: %s", exc)
                    workspace = None
            return resolve_sandbox_roots(
                workspace=workspace, project_root=pr, temp_dir=get_temp_dir(pr)
            )
        except Exception:  # noqa: BLE001
            return None

    def _readable_roots(self, session_id: Optional[str] = None) -> "tuple":
        """Resolve the readable sandbox roots for shell content checks."""
        roots = self._sandbox_roots(session_id)
        return roots.readable() if roots is not None else ()

    @staticmethod
    def _write_roots(writable_roots: "tuple") -> "tuple":
        """Writable roots plus the platform temp areas."""
        out = list(writable_roots or ())
        candidates = []
        try:
            import tempfile

            candidates.append(Path(tempfile.gettempdir()))
        except Exception:  # noqa: BLE001
            pass
        candidates.append(Path("/tmp"))
        for candidate in candidates:
            try:
                resolved = candidate.expanduser().resolve()
            except (OSError, ValueError, RuntimeError):
                continue
            if resolved not in out:
                out.append(resolved)
        return tuple(out)

    # Installer verbs whose 2-token prefix would be too coarse for a grant:
    # "pip install" must NOT waive approval for every future package.
    _PKG_MANAGERS = frozenset({
        "pip", "pip3", "npm", "pnpm", "yarn", "bun", "apt", "apt-get",
        "brew", "gem", "cargo", "uv", "poetry", "go", "dotnet", "nuget",
    })
    _INSTALL_SUBCOMMANDS = frozenset({
        "install", "add", "i", "in", "uninstall", "remove", "update", "upgrade",
    })

    @staticmethod
    def shell_command_signature(tool_name: str, command: Optional[str]) -> str:
        """Grant signature for a shell tool = ``tool:<command head tokens>``."""
        prefix = ""
        if command:
            tokens = [t for t in command.strip().split() if not t.startswith("-")]
            if (
                len(tokens) >= 3
                and tokens[0].lower() in ToolGuard._PKG_MANAGERS
                and tokens[1].lower() in ToolGuard._INSTALL_SUBCOMMANDS
            ):
                prefix = " ".join(tokens[:3])
            else:
                prefix = " ".join(tokens[:2])
        return f"{tool_name}:{prefix}" if prefix else tool_name

    @staticmethod
    def _merge_short_flags(command: str) -> str:
        """Merge adjacent single-dash short flags: ``rm -r -f /`` → ``rm -rf /``."""
        tokens = command.split()
        out: List[str] = []
        for tok in tokens:
            if (
                out
                and re.fullmatch(r"-[A-Za-z0-9]", tok)
                and re.fullmatch(r"-[A-Za-z0-9]+", out[-1])
                and not out[-1].startswith("--")
            ):
                out[-1] = out[-1] + tok[1:]
            else:
                out.append(tok)
        return " ".join(out)

    def _deny_variants(self, command: str) -> List[str]:
        """Normalization variants a command is deny-matched against (H1)."""
        base = [command]
        no_quotes = command.replace('"', "").replace("'", "")
        if no_quotes != command:
            base.append(no_quotes)
        merged = self._merge_short_flags(command)
        if merged != command:
            base.append(merged)
        merged_nq = self._merge_short_flags(no_quotes)
        if merged_nq not in base:
            base.append(merged_nq)

        variants = list(base)
        for form in base:
            folded = _fold_home_aliases(form)
            if folded not in variants:
                variants.append(folded)
            # A folded Windows path still carries backslashes, which the
            # POSIX-separator patterns cannot match; keep both spellings.
            posix = _posix_separators(folded)
            if posix not in variants:
                variants.append(posix)
        return variants

    @staticmethod
    def _unclassifiable_result() -> ToolPermissionResult:
        """Approval verdict for a command whose structure cannot be determined."""
        try:
            from boarai.core.shell import backend_allows_contained_relaxation

            contained = backend_allows_contained_relaxation()
        except Exception:  # noqa: BLE001 - fail closed to "ask"
            contained = False

        if contained:
            return ToolPermissionResult(
                True,
                requires_approval=False,
                category="contained",
                grantable=False,
                reason=(
                    "Shell command structure could not be classified, but the "
                    "active backend exposes nothing of the host, so the blast "
                    "radius is bounded to a disposable filesystem"
                ),
            )
        return ToolPermissionResult(
            True,
            requires_approval=True,
            category="dangerous",
            grantable=False,
            reason=(
                "Shell command contains a construct that cannot be safely "
                "classified (heredoc, command or process substitution, or an "
                "unterminated quote); approval required"
            ),
        )

    def check_shell_command(
        self,
        command: str,
        agent_shell_allowlist: Optional[List[str]] = None,
        project_root: Optional[Any] = None,
        readable_roots: Optional["tuple"] = None,
        writable_roots: Optional["tuple"] = None,
    ) -> ToolPermissionResult:
        """Check a shell command for safety."""
        stripped = command.strip()

        # Agent-level shell allowlist
        if agent_shell_allowlist and "*" not in agent_shell_allowlist:
            allowed = any(
                stripped.startswith(prefix) or
                (prefix.endswith("*") and stripped.startswith(prefix[:-1]))
                for prefix in agent_shell_allowlist
            )
            if not allowed:
                return ToolPermissionResult(
                    False, reason=f"Shell command not in agent allowlist",
                    category="dangerous",
                )

        # Global deny patterns (hard block, unskippable). Matched against
        # normalization variants (H1): quote-insertion (r""m) and split-flag
        # (rm -r -f) evasions hit the same floor as the canonical form.
        # v1.13: this list is the ABSOLUTE floor (machine-destroying, no
        # recovery path) — the only commands denied in every mode including
        # a session set to "allow".
        for variant in self._deny_variants(stripped):
            for pattern in self._shell_config.deny_patterns:
                try:
                    if re.search(pattern, variant, re.IGNORECASE):
                        return ToolPermissionResult(
                            False,
                            reason=f"Shell command matches deny pattern: {pattern}",
                            category="dangerous",
                        )
                except re.error:
                    continue

        # v1.13 confirm floor: system-affecting but recoverable. Returns
        # requires_approval=True (never a hard deny) — the approval mode
        # decides: on prompts, session off/deny waives (F1). Matches
        # codex semantics ("dangerous → Prompt, not Forbidden").
        # grantable=False: the confirm floor is content-sensitive. The grant
        # signature (tool + head tokens) does NOT pin the sensitive part for
        # several of these commands — `echo x > ~/.ssh/authorized_keys` signs
        # as ``shell_execute:echo x`` (covering ANY redirect target) and
        # ``net user guest /delete`` signs as ``shell_execute:net user``
        # (covering user creation). Each occurrence must be approved on its
        # own; "always allow" is not offered for confirm-floor commands.
        for variant in self._deny_variants(stripped):
            for pattern in getattr(
                self._shell_config, "confirm_patterns", None
            ) or []:
                try:
                    if re.search(pattern, variant, re.IGNORECASE):
                        return ToolPermissionResult(
                            True, requires_approval=True,
                            category="dangerous", grantable=False,
                            reason=(
                                f"{CONFIRM_REASON_PREFIX} "
                                f"(system-affecting): {pattern}"
                            ),
                        )
                except re.error:
                    continue

        # Absolute path outside sandbox (when roots provided)
        if getattr(self._shell_config, "block_external_absolute_paths", True):
            roots = readable_roots
            if roots is None and project_root:
                try:
                    roots = (Path(project_root).resolve(),)
                except (OSError, ValueError, TypeError):
                    roots = ()
            if roots:
                external = self._find_external_absolute_path(stripped, roots)
                if external:
                    return ToolPermissionResult(
                        False,
                        reason=f"Shell command references path outside project root: {external}",
                        category="dangerous",
                    )

        # Write target outside the sandbox (v1.16). PathGuard confines the
        # *file tools*, but a shell command's effects bypass it entirely: the
        # command text is the only place a write shows up, and a directive
        # such as ``cp secret ~/.config/app/config`` or
        # ``git diff --output=/etc/passwd`` names no deny pattern. Confirm
        # tier — never a hard deny — matching an external write through a file
        # tool (the approval mode decides; ``off`` waives it).
        #
        # Checked BEFORE the network tier on purpose: a command can hit both
        # (``curl -o /outside https://host``), and the write verdict is the
        # stricter one (grantable=False) — it must win, or the prompt would
        # offer "always allow" for a command that also writes out of bounds.
        write_roots = self._write_roots(writable_roots) if writable_roots else ()
        if write_roots:
            external_write = self._first_external_write_target(
                stripped, write_roots
            )
            if external_write:
                return ToolPermissionResult(
                    True, requires_approval=True,
                    category="dangerous", grantable=False,
                    reason=(
                        "Shell command writes outside the workspace: "
                        f"{external_write}"
                    ),
                )

        # External network access requires approval (fail-open: loopback free).
        if getattr(self._shell_config, "network_approval", True):
            ext_hosts = self._extract_external_network_targets(stripped)
            if ext_hosts:
                return ToolPermissionResult(
                    True, requires_approval=True,
                    category="dangerous",
                    reason=(
                        "Shell command accesses external network host(s): "
                        + ", ".join(ext_hosts[:5])
                    ),
                )

        # Shell joiners defeat prefix-based readonly classification:
        # `ls; git clone evil && ./run` starts with an allowed command but
        # chains a dangerous one. Segmented analysis: split the chain and
        # require EVERY segment to independently classify readonly for the
        # whole chain to auto-allow (`ls && git status` runs free). Any
        # non-readonly segment escalates to user approval.
        if self._has_shell_joiners(stripped):
            token_segments = tokenize_shell_segments(stripped)
            if token_segments is None:
                return self._unclassifiable_result()
            if token_segments and all(
                self._segment_is_readonly(segment_text(seg))
                for seg in token_segments
            ):
                return ToolPermissionResult(
                    True, category="readonly",
                    reason="Chained command — all segments readonly",
                )
            return ToolPermissionResult(
                True, requires_approval=True,
                category="dangerous", grantable=False,
                reason="Shell command contains chained operations; approval required",
            )

        # Readonly auto-allow needs the command text to mean what it looks like
        # it means. A construct the tokenizer does not model (heredoc, command
        # or process substitution, unterminated quote) can carry arbitrary work
        # behind a readonly-looking verb, so it is never auto-allowed.
        if tokenize_shell_segments(stripped) is None:
            return self._unclassifiable_result()

        # Readonly patterns → auto-approve
        for pattern in self._shell_config.readonly_patterns:
            try:
                if re.search(pattern, stripped, re.IGNORECASE):
                    return ToolPermissionResult(True, category="readonly")
            except re.error:
                continue

        # Default: needs approval
        return ToolPermissionResult(
            True, requires_approval=True,
            category="dangerous",
            reason="Shell command requires approval",
        )

    @staticmethod
    def _find_external_absolute_path(command: str, project_root) -> Optional[str]:
        """Return first absolute path token that resolves outside the roots."""
        from boarai.core.security.path_utils import first_external_absolute_path

        if isinstance(project_root, tuple):
            return first_external_absolute_path(command, project_root)
        try:
            root = Path(project_root).resolve()
        except (OSError, ValueError, TypeError):
            return None
        return first_external_absolute_path(command, (root,))

    #: Commands whose last non-flag argument is a write target.
    _WRITE_TARGET_VERBS = frozenset(
        {"cp", "mv", "install", "ln", "rsync", "scp"}
    )
    #: Long flags whose value is a write target (``git diff --output=…``,
    #: ``wget --output-document=…``, ``pylint --output-file=…``). Unambiguous
    #: for any verb, so they are checked everywhere. ``--file`` is deliberately
    #: ABSENT: for docker-compose/tar-style tools it names a file to READ
    #: (a config or archive), and flagging it would escalate ordinary reads.
    _OUTPUT_FLAGS = frozenset(
        {"--output", "--out", "--output-document", "--output-file"}
    )
    #: Verbs whose SHORT ``-o``/``-O`` flag takes an output path. Short flags
    #: are verb-specific — ``find -o /etc`` is a boolean OR and ``grep -o`` a
    #: match flag, so applying ``-o`` universally would escalate reads.
    _OUTPUT_FLAG_VERBS = frozenset(
        {"curl", "wget", "gcc", "g++", "cc", "clang", "clang++", "ld",
         "zip", "sort", "ffmpeg", "convert", "magick", "gs", "pandoc"}
    )

    def _first_external_write_target(
        self, command: str, roots: "tuple"
    ) -> Optional[str]:
        """First absolute write target in ``command`` outside ``roots``."""
        # The tokenizer is bash-semantic: an unquoted ``\`` escapes the next
        # character, so ``--output=C:\dir\x`` arrives as ``C:dirx`` (a
        # drive-relative path, i.e. invisible to an absolute check). Scan the
        # POSIX-separator spelling too — the same trick ``_deny_variants``
        # uses — so a Windows-style command is still classified.
        variants = [command]
        posix = command.replace("\\", "/")
        if posix != command:
            variants.append(posix)
        for variant in variants:
            found = self._external_write_target_in(variant, roots)
            if found:
                return found
        return None

    def _external_write_target_in(self, command: str, roots: "tuple") -> Optional[str]:
        """``_first_external_write_target`` for one already-normalized spelling."""
        from boarai.core.security.path_utils import is_within

        def _check(raw: str) -> Optional[str]:
            token = raw.strip().strip("'\"")
            if not token or token.startswith("-"):
                return None
            if token in ("/dev/null", "nul", "NUL") or token.startswith("/dev/"):
                return None
            try:
                path = Path(token).expanduser()
                if not path.is_absolute():
                    return None
                resolved = path.resolve()
            except (OSError, ValueError, RuntimeError):
                return None
            return None if is_within(resolved, roots) else token

        segments = tokenize_shell_segments(command)
        if segments is None:
            return None
        for tokens in segments:
            if not tokens:
                continue
            verb = Path(tokens[0]).name.lower()
            if verb.endswith(".exe"):
                verb = verb[:-4]
            # Long output flags — unambiguous for any verb (git --output=,
            # wget --output-document=, pylint --output-file=).
            for index, token in enumerate(tokens[1:], start=1):
                if token.startswith("--") and "=" in token:
                    flag, _, value = token.partition("=")
                    if flag in self._OUTPUT_FLAGS:
                        found = _check(value)
                        if found:
                            return found
                elif token in self._OUTPUT_FLAGS and index + 1 < len(tokens):
                    found = _check(tokens[index + 1])
                    if found:
                        return found
            # Short output flags are verb-specific: `find -o /etc` is a
            # boolean OR, `grep -o` a match flag — only the verbs that write
            # through -o/-O are inspected (see _OUTPUT_FLAG_VERBS).
            if verb in self._OUTPUT_FLAG_VERBS:
                for index, token in enumerate(tokens[1:], start=1):
                    if token in ("-o", "-O") and index + 1 < len(tokens):
                        found = _check(tokens[index + 1])
                        if found:
                            return found
            if verb == "tee":
                for token in tokens[1:]:
                    found = _check(token)
                    if found:
                        return found
                continue
            if verb == "dd":
                for token in tokens[1:]:
                    if token.startswith("of="):
                        found = _check(token[3:])
                        if found:
                            return found
                continue
            if verb not in self._WRITE_TARGET_VERBS:
                continue
            args = [token for token in tokens[1:] if not token.startswith("-")]
            if not args:
                continue
            found = _check(args[-1])
            if found:
                return found
        return None

    @staticmethod
    def _extract_external_network_targets(command: str) -> List[str]:
        """Return non-local hosts referenced by network commands."""
        hosts: List[str] = []
        seen: set = set()

        def add(host: str) -> None:
            if host and host not in seen:
                seen.add(host)
                hosts.append(host)

        # scheme://host form (http, https, ftp)
        for m in re.finditer(r"[a-z][a-z0-9+.-]*://([^\s/\"'<>()\[\]]+)", command):
            add(m.group(1))
        # user@host[:path] form (scp/git/ssh)
        for m in re.finditer(r"(?:^|[\s;|&])[a-z0-9_.-]+@([^\s:]+)", command):
            add(m.group(1))
        # bare domain directly after a fetch/download command word
        for m in re.finditer(
            r"(?:^|[\s;|&])(?:curl|wget|pip|pip3|npm|git)\s+(?:--?[^\s]+\s+)*"
            r"([a-z0-9.-]+\.[a-z]{2,})(?=[\s/\"'&|;]|$)",
            command,
            re.IGNORECASE,
        ):
            add(m.group(1))
        return [h for h in hosts if not ToolGuard._is_local_host(h)]

    @staticmethod
    def _is_local_host(host: str) -> bool:
        """True for loopback / localhost hosts (and host:port / userinfo forms)."""
        h = host.split("@")[-1]  # strip userinfo
        h = h.strip("[]")
        if ":" in h and h.count(":") == 1:
            h = h.rsplit(":", 1)[0]  # strip a single trailing port
        h = h.lower().rstrip(".")
        if h in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return True
        return h.startswith("127.")

    def _segment_is_readonly(self, segment: str) -> bool:
        """True when one chain segment independently classifies readonly."""
        seg = segment.strip()
        if not seg:
            return False
        # Command substitution (anywhere, incl. inside double quotes):
        # the inner command executes when the segment runs.
        if "$(" in seg or "`" in seg:
            return False
        # Redirection inside a segment = write intent
        for m in re.finditer(r"(?<![<>])>{1,2}(?![<(])", seg):
            return False
        # Deny patterns also match the H1 normalization variants here, so a
        # quoted/split-flag segment can never classify as readonly.
        for variant in self._deny_variants(seg):
            for pattern in self._shell_config.deny_patterns:
                try:
                    if re.search(pattern, variant, re.IGNORECASE):
                        return False
                except re.error:
                    continue
        # v1.13: confirm-floor segments are not readonly either (a chain
        # like ``ls && type ~/.ssh/id_rsa`` must escalate to a prompt /
        # denial instead of riding a readonly auto-allow).
        for variant in self._deny_variants(seg):
            for pattern in getattr(
                self._shell_config, "confirm_patterns", None
            ) or []:
                try:
                    if re.search(pattern, variant, re.IGNORECASE):
                        return False
                except re.error:
                    continue
        for pattern in self._shell_config.readonly_patterns:
            try:
                if re.search(pattern, seg, re.IGNORECASE):
                    return True
            except re.error:
                continue
        return False

    @staticmethod
    def _has_shell_joiners(command: str) -> bool:
        """Detect shell chaining operators outside quotes."""
        in_single = False
        in_double = False
        i = 0
        n = len(command)
        while i < n:
            ch = command[i]
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif not in_single and not in_double:
                if ch in ";|&\n":
                    return True
                if ch == ">":
                    return True  # redirect (incl. >>, 2>) — write intent
            # Command substitution: outside SINGLE quotes it executes in both
            # bash and PowerShell — including inside double quotes
            # ("$(rm -rf ~)" / "`rm -rf ~`" expand). Always a joiner so the
            # command escalates to user approval instead of readonly-matching
            # the outer verb.
            if not in_single and ch in ("$", "`"):
                if ch == "`" or (i + 1 < n and command[i + 1] == "("):
                    return True
            i += 1
        return False

    def check_rate_limit(
        self,
        tool_name: str,
        run_call_count: int,
        consecutive_errors: int,
    ) -> ToolPermissionResult:
        """Check if tool has exceeded rate limits."""
        limit = int(self._config.max_tool_calls_per_run or 0)
        if limit > 0 and run_call_count >= limit:
            return ToolPermissionResult(
                False, reason=f"Max tool calls per run exceeded ({run_call_count})",
            )
        threshold = int(self._config.max_consecutive_errors or 5)
        if consecutive_errors >= threshold:
            policy = getattr(self._config, "error_streak_policy", "warn") or "warn"
            if policy == "block":
                return ToolPermissionResult(
                    False,
                    reason=f"Too many consecutive errors ({consecutive_errors}) for '{tool_name}'",
                )
            # "warn" (default): fail-open — a single environmental failure
            # (missing python, GBK decode, …) must not lock the agent out of
            # every subsequent shell command. Only attach a non-blocking hint.
            return ToolPermissionResult(
                True,
                category="warning",
                warning=(
                    f"'{tool_name}' failed {consecutive_errors} times in a row; "
                    "consider a different command or approach."
                ),
            )
        return ToolPermissionResult(True)
