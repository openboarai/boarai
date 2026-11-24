"""
Tool kernel registry for BoarAi — abstract interface, no implementations.

A tool registers itself by declaring metadata (security_profile, compress_strategy,
suggested_category, etc.). Core uses this metadata to make decisions — NEVER by
checking tool names. This keeps the core extension-agnostic.

Moved from core/extension/registry/tool_registry.py during architecture refactor.
"""

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from boarai.core.event import EventBus, EventType, get_event_bus, publish_event

logger = logging.getLogger(__name__)


class ToolStatus(str, Enum):
    REGISTERED = "registered"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class SecurityProfile(str, Enum):
    """Abstract security profile a tool declares at registration. Core checks this — never checks tool names — to apply security policies."""
    NONE = "none"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    FILE_DELETE = "file_delete"
    SHELL = "shell"


class CompressStrategy(str, Enum):
    """Output compression strategy a tool declares."""
    DEFAULT = "default"
    FILE = "file"
    SHELL = "shell"
    GREP = "grep"
    DIFF = "diff"
    LIST = "list"
    JSON = "json"


class SuggestedCategory(str, Enum):
    """Safety category a tool suggests for itself. User config can override. Used by ToolGuard as fallback."""
    READONLY = "readonly"
    SAFE = "safe"
    DANGEROUS = "dangerous"


class ExecutionMode(str, Enum):
    """Whether a tool may run concurrently with its batch siblings."""
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


@dataclass
class ToolParameter:
    name: str
    type: str
    description: str
    required: bool = True
    default: Any = None
    enum: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {"name": self.name, "type": self.type, "description": self.description, "required": self.required}
        if self.default is not None:
            result["default"] = self.default
        if self.enum:
            result["enum"] = self.enum
        return result


@dataclass
class ToolDefinition:
    """Complete self-declaration of a tool's capabilities and requirements."""
    # === Identity ===
    name: str
    description: str
    parameters: List[ToolParameter] = field(default_factory=list)

    # === Security: what kind of operations does this tool perform? ===
    security_profile: str = SecurityProfile.NONE.value
    """Abstract profile: none / file_read / file_write / file_delete / shell.
    Core dispatches PathGuard / ShellGuard based on this."""

    path_param: Optional[str] = None
    """For file tools: name of the parameter that holds the file path."""

    shell_param: Optional[str] = None
    """For shell tools: name of the parameter that holds the command."""

    # === Permission: what safety category does this tool suggest? ===
    suggested_category: str = SuggestedCategory.SAFE.value
    """readonly / safe / dangerous. ToolGuard uses this as fallback when
    the user config doesn't explicitly categorize this tool."""

    # === Output: how should the core compress this tool's results? ===
    compress_strategy: str = CompressStrategy.DEFAULT.value
    """default / file / shell / grep / diff / list / json.

    Honoured by ``core.prompt.tool_result_trimmer`` for ``list`` (structure
    aware slimming); the other values are informational."""

    compress_max_chars: int = 8000
    """Results longer than this trigger compression."""

    # === Execution ===
    required_permissions: List[str] = field(default_factory=list)
    timeout: float = 30.0
    retry_count: int = 3

    execution_mode: Optional[str] = None
    """sequential / parallel; None derives it from suggested_category (see
    :func:`resolve_execution_mode`).

    An explicit declaration is the escape hatch for tools whose category says
    nothing about concurrency: a tool declared readonly that mutates shared
    state internally must be able to force serial execution, while a tool
    declared safe with a purely read-only implementation (session_search /
    session_read) should be able to opt into parallelism without changing the
    category that drives approval decisions.
    """

    # === Exposure ===
    llm_visible: bool = True
    """Whether this tool's schema is injected into LLM tool lists.
    Hidden tools remain callable by name (internal callers, HTTP API,
    WebUI) — they just never cost prompt tokens."""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "description": self.description,
            "parameters": [p.to_dict() for p in self.parameters],
            "security_profile": self.security_profile,
            "suggested_category": self.suggested_category,
            "compress_strategy": self.compress_strategy,
            "required_permissions": self.required_permissions,
            "timeout": self.timeout, "retry_count": self.retry_count,
            "llm_visible": self.llm_visible,
        }

    def to_openai_function(self) -> Dict[str, Any]:
        properties = {}
        required = []
        for param in self.parameters:
            p = {"type": param.type, "description": param.description}
            if param.enum:
                p["enum"] = param.enum
            properties[param.name] = p
            if param.required:
                required.append(param.name)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description,
                         "parameters": {"type": "object", "properties": properties, "required": required}},
        }


def resolve_execution_mode(definition: Any) -> str:
    """Whether a tool may run concurrently with its batch siblings."""
    explicit = getattr(definition, "execution_mode", None)
    if explicit:
        return str(explicit)
    category = str(getattr(definition, "suggested_category", "") or "")
    if category == SuggestedCategory.READONLY.value:
        return ExecutionMode.PARALLEL.value
    return ExecutionMode.SEQUENTIAL.value


@dataclass
class ToolExecutionResult:
    success: bool
    result: Any = None
    error: Optional[str] = None
    execution_time: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"success": self.success, "result": self.result, "error": self.error,
                "execution_time": self.execution_time, "metadata": self.metadata}


ToolHandler = Callable[..., Any]


class ToolContext:
    """Execution context injected into every tool handler as the first argument."""

    def __init__(
        self,
        *,
        session_id: Optional[str] = None,
        workspace: Optional[Any] = None,
        run_id: Optional[str] = None,
        tool_call_id: Optional[str] = None,
    ) -> None:
        self._session_id = session_id
        self._workspace_override = workspace
        self._run_id = run_id
        self._tool_call_id = tool_call_id

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    @property
    def run_id(self) -> Optional[str]:
        """Run that requested this call — lets long tools fan out live ``tool.output`` deltas to the run's WS clients."""
        return self._run_id

    @property
    def tool_call_id(self) -> Optional[str]:
        """LLM tool_call id — pairs live output frames with the UI card."""
        return self._tool_call_id

    @property
    def task_manager(self):
        from boarai.core.task.manager import get_task_manager
        return get_task_manager()

    @property
    def project_root(self):
        from boarai.core.runtime import get_project_root
        return get_project_root()

    @property
    def workspace(self):
        """Effective cwd for tools (session workspace or project_root)."""
        if self._workspace_override is not None:
            from pathlib import Path
            return Path(self._workspace_override)
        try:
            from boarai.core.session.workspace import resolve_session_workspace
            from boarai.core.session.manager import get_session_manager

            session = None
            if self._session_id:
                sm = get_session_manager()
                # sync cache only — avoid async in property
                session = sm._sessions.get(self._session_id)
            # Pass session_id explicitly so the default-workspace fallback works
            # even when session is None (not in the in-memory cache).
            return resolve_session_workspace(session, session_id=self._session_id)
        except Exception:
            root = self.project_root
            if root is not None:
                from pathlib import Path
                return Path(root)
            from pathlib import Path
            return Path.cwd()

    @property
    def session_manager(self):
        from boarai.core.session.manager import get_session_manager
        return get_session_manager()

    @property
    def llm_service(self):
        from boarai.core.llm.service import get_llm_service
        return get_llm_service()

    @property
    def tool_registry(self):
        from .registry import get_tool_registry
        return get_tool_registry()


class Tool:
    def __init__(self, definition: ToolDefinition, handler: ToolHandler, status: ToolStatus = ToolStatus.REGISTERED):
        self.definition = definition
        self.handler = handler
        self.status = status
        self._execution_count = 0
        self._error_count = 0

    @property
    def name(self) -> str:
        return self.definition.name

    async def execute(self, ctx: Optional[ToolContext] = None, **kwargs: Any) -> ToolExecutionResult:
        import time
        start_time = time.time()
        try:
            if ctx is None:
                ctx = ToolContext()
            if asyncio.iscoroutinefunction(self.handler):
                result = await self.handler(ctx, **kwargs)
            else:
                result = self.handler(ctx, **kwargs)
            execution_time = time.time() - start_time
            self._execution_count += 1
            return ToolExecutionResult(success=True, result=result, execution_time=execution_time)
        except Exception as e:
            execution_time = time.time() - start_time
            self._error_count += 1
            logger.error(f"Tool {self.name} execution failed: {e}")
            return ToolExecutionResult(success=False, error=str(e), execution_time=execution_time)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self.name, "status": self.status.value,
            "execution_count": self._execution_count, "error_count": self._error_count,
            "success_rate": ((self._execution_count - self._error_count) / max(self._execution_count, 1)),
        }


class ToolRegistry:
    """Central tool registry with onion-model middleware pipeline."""

    def __init__(self, event_bus: Optional[EventBus] = None):
        self._event_bus = event_bus or get_event_bus()
        self._tools: Dict[str, Tool] = {}
        self._middlewares: List = []  # onion middleware list
        self._async_lock = asyncio.Lock()
        self._sync_lock = threading.Lock()
        # Rate-limit state: run_id -> tool call count; tool_name -> consecutive errors
        self._run_call_counts: Dict[str, int] = {}
        self._tool_error_streak: Dict[str, int] = {}

    def use(self, middleware, tool_name: Optional[str] = None) -> None:
        """Register a middleware in the onion pipeline."""
        if tool_name:
            # Wrap to filter by tool name
            async def wrapped(t, args, ctx, nxt):
                if t == tool_name:
                    return await middleware(t, args, ctx, nxt)
                return await nxt(args)
            self._middlewares.append(wrapped)
        else:
            self._middlewares.append(middleware)

    def register_tool_sync(self, definition: ToolDefinition, handler: ToolHandler) -> Tool:
        """Synchronous registration (used during module import, no event loop needed)."""
        with self._sync_lock:
            if definition.name in self._tools:
                logger.warning(f"Tool {definition.name} already registered, skipping")
                return self._tools[definition.name]
            tool = Tool(definition=definition, handler=handler)
            self._tools[definition.name] = tool
            # Structural noise: import-time registrations are fixed by code
            # and identical every startup (dozens of lines). Runtime
            # (re)registrations go through register_tool/unregister_tool,
            # which stay at INFO because they reflect dynamic state.
            logger.debug(f"Registered tool: {definition.name}")
            return tool

    async def register_tool(self, definition: ToolDefinition, handler: ToolHandler) -> Tool:
        """Async registration (used during runtime, publishes event)."""
        async with self._async_lock:
            if definition.name in self._tools:
                raise ToolError(f"Tool {definition.name} is already registered")
            tool = Tool(definition=definition, handler=handler)
            self._tools[definition.name] = tool
            logger.info(f"Registered tool: {definition.name}")
            await publish_event(EventType.TOOL_REGISTERED,
                                {"tool": definition.name, "definition": definition.to_dict()}, "tool_registry")
            return tool

    async def unregister_tool(self, tool_name: str) -> bool:
        async with self._async_lock:
            if tool_name not in self._tools:
                return False
            del self._tools[tool_name]
            logger.info(f"Unregistered tool: {tool_name}")
            await publish_event(EventType.TOOL_UNREGISTERED, {"tool": tool_name}, "tool_registry")
            return True

    async def execute_tool(
        self,
        tool_name: str,
        agent_name: Optional[str] = None,
        session_id: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecutionResult:
        """Execute a tool with optional security checks."""
        tool = self.get_tool(tool_name)
        if not tool:
            return ToolExecutionResult(success=False, error=f"Tool not found: {tool_name}")

        # --- Security check (if SecurityManager is initialized) ---
        try:
            from boarai.core.security import get_security_manager
            sec = get_security_manager()
        except ImportError:
            sec = None

        # Pop internal control kwargs (always, even without security manager)
        agent_tools = kwargs.pop("_agent_tools", None)
        agent_shell = kwargs.pop("_agent_shell", None)
        agent_paths = kwargs.pop("_agent_paths", None)
        agent_categories = kwargs.pop("_agent_allowed_categories", None)
        agent_auto_approve = bool(kwargs.pop("_agent_can_auto_approve", False))
        approval_granted = bool(kwargs.pop("_approval_granted", False))
        run_id = kwargs.pop("_run_id", None)
        tool_call_id = kwargs.pop("_tool_call_id", None)
        # Rate-limit bucket key (run, falling back to session) — distinct from
        # the raw run_id passed to the ToolContext, which stays None outside a
        # run so tools never fan out live events with a bogus run id.
        rate_bucket = run_id or session_id or "global"

        # Error-streak signature: same tool + same canonical args. Different
        # argument values are independent, so retrying with a different command
        # never inherits the previous failure's streak (aligns with Hermes'
        # ToolCallSignature). Prevents one bad command from locking out all
        # subsequent calls to the same tool.
        try:
            import hashlib
            import json as _json

            # default=repr keeps non-JSON values (Path, float, custom obj)
            # deterministically typed instead of str()-ing them, so the same
            # logical command always maps to the same streak signature.
            _args_key = _json.dumps(kwargs, sort_keys=True, default=repr)
        except Exception:
            _args_key = repr(kwargs)
        streak_key = hashlib.sha256(
            f"{tool_name}|{_args_key}".encode("utf-8")
        ).hexdigest()

        if sec and getattr(sec, '_initialized', False):
            # --- Rate limit (before any side effects) ---
            call_count = self._run_call_counts.get(rate_bucket, 0)
            err_streak = self._tool_error_streak.get(streak_key, 0)
            rate = sec.tool_guard.check_rate_limit(tool_name, call_count, err_streak)
            if not rate.allowed:
                sec.audit_record("tool_deny", {
                    "tool": tool_name, "agent": agent_name,
                    "reason": rate.reason, "session": session_id,
                })
                return ToolExecutionResult(
                    success=False,
                    error=f"Security: {rate.reason}",
                    metadata={"security_block": True, "reason": rate.reason, "rate_limit": True},
                )
            if rate.warning:
                logger.warning(
                    "Tool %s (run=%s): %s", tool_name, rate_bucket, rate.warning
                )

            # --- PathGuard: driven by security_profile (NOT tool name) ---
            profile = tool.definition.security_profile
            if profile in (SecurityProfile.FILE_READ.value,
                           SecurityProfile.FILE_WRITE.value,
                           SecurityProfile.FILE_DELETE.value):
                path_param = tool.definition.path_param or "path"
                # v1.15 fix: classify EVERY path argument. file_copy/file_move
                # declare path_param="source" but their "destination" is a
                # write target; checking only the source let a copy/move write
                # into .boarai/config or another session via an absolute
                # destination under project root (_safe_path compat branch).
                _arg_names = [path_param]
                if kwargs.get("destination"):
                    _arg_names.append("destination")
                _resolved: Dict[str, str] = {}
                for _aname in _arg_names:
                    _raw = kwargs.get(_aname, "")
                    if not _raw:
                        continue
                    # Resolve RELATIVE paths against the SESSION workspace (not
                    # the process cwd / project_root). The file-tool handler does
                    # the same via ctx.workspace; resolving here keeps the
                    # security check and the actual write consistent.
                    _rp = _raw
                    try:
                        from pathlib import Path as _Path

                        if not _Path(_raw).is_absolute():
                            from boarai.core.session.workspace import resolve_session_workspace

                            _ws = resolve_session_workspace(session_id=session_id)
                            _rp = str((_Path(_ws) / _raw).resolve())
                    except Exception:
                        _rp = _raw
                    _resolved[_aname] = _rp

                if _resolved:
                    # v1.14: registry is the SECOND gate — it enforces only the
                    # ABSOLUTE write floor (OS-bricking / security-config self-mod),
                    # which denies in every mode. The confirm tier (credential /
                    # external / cross-session) was already decided upstream by
                    # ToolConfirmGate via PathGuard.classify (prompt under "ask",
                    # waived under "allow"), so classify's confirm verdict
                    # (allowed=True, needs_approval=True) passes through here.
                    _op = (
                        "delete" if profile == SecurityProfile.FILE_DELETE.value
                        else "write" if profile == SecurityProfile.FILE_WRITE.value
                        else "read"
                    )
                    for _aname, _rp in _resolved.items():
                        check = sec.classify_file(
                            _op, _rp,
                            session_id=session_id,
                            agent_allowed_paths=agent_paths,
                        )
                        if not check.allowed:
                            sec.audit_record("path_block", {
                                "tool": tool_name, "path": _rp, "agent": agent_name,
                                "reason": check.reason, "session": session_id,
                            })
                            return ToolExecutionResult(
                                success=False,
                                error=(
                                    f"[security: blocked] {check.reason}\n"
                                    "[security: hint] absolute write floor — this "
                                    "target is OS-critical or the security config "
                                    "itself; it is never writable in any mode. "
                                    "Choose a path inside the workspace."
                                ),
                                metadata={"security_block": True, "reason": check.reason},
                            )
                    # Pass resolved absolute paths through so the tool operates on
                    # its sandbox, never the install root.
                    for _aname, _rp in _resolved.items():
                        kwargs[_aname] = _rp

            # --- Approval verdict (single source: ToolGuard.evaluate_tool) ---
            # The session's own on/off/deny choice is part of the verdict, so
            # the registry path and the run-gate path can never disagree.
            _decision = sec.evaluate_tool(
                tool_name,
                _agent_config_stub(agent_tools, agent_categories, agent_auto_approve),
                session_overrides=_session_overrides(session_id),
            )
            if _decision.verdict == "denied":
                sec.audit_record("tool_deny", {
                    "tool": tool_name, "agent": agent_name,
                    "reason": _decision.reason, "session": session_id,
                })
                return ToolExecutionResult(
                    success=False,
                    error=f"Security: {_decision.reason}",
                    metadata={"security_block": True, "reason": _decision.reason},
                )

            # need_approval without prior batch/user approval → deny
            # (RunManager passes _approval_granted=True after confirm; the
            # evaluator already folded approval_mode/auto_approve in, so no
            # private config peeking is needed here.)
            if _decision.verdict == "need_approval" and not approval_granted:
                sec.audit_record("tool_deny", {
                    "tool": tool_name, "agent": agent_name,
                    "reason": _decision.reason or "approval required",
                    "session": session_id,
                })
                return ToolExecutionResult(
                    success=False,
                    error=f"Security: approval required for tool '{tool_name}'",
                    metadata={
                        "security_block": True,
                        "requires_approval": True,
                        "reason": _decision.reason,
                    },
                )

            # --- ShellGuard: driven by security_profile (NOT tool name) ---
            # This is the registry's own content scan. It is NOT a duplicate of
            # the run-layer gate: evaluate_tool (above) decides by tool name,
            # agent posture and session mode only — the content refinement
            # (deny floor / confirm tier / external paths / chained segments)
            # lives in evaluate_batch, which this layer does not call. So a
            # tool executed outside a run (headless, task executor, direct API)
            # gets its floor enforcement here, and exactly once.
            if profile == SecurityProfile.SHELL.value:
                shell_param = tool.definition.shell_param or "command"
                cmd = kwargs.get(shell_param, "")
                try:
                    from boarai.core.runtime import get_project_root
                    from boarai.core.security.path_utils import get_temp_dir, resolve_sandbox_roots
                    from boarai.core.session.workspace import resolve_session_workspace

                    pr = get_project_root()
                    ws = None
                    if session_id:
                        from boarai.core.session.manager import get_session_manager

                        # resolve_session_workspace, not the raw field: a
                        # default-workspace session stores "" until something
                        # resolves it, and the file tools already resolve it
                        # this way. Both families must see one root set.
                        ws = resolve_session_workspace(session_id=session_id)
                    sandbox = resolve_sandbox_roots(
                        workspace=ws,
                        project_root=pr,
                        temp_dir=get_temp_dir(pr),
                    )
                    roots = sandbox.readable()
                    writable = sandbox.writable()
                except Exception:
                    roots = None
                    writable = None
                shell_check = sec.tool_guard.check_shell_command(
                    cmd,
                    agent_shell_allowlist=agent_shell,
                    readable_roots=roots,
                    writable_roots=writable,
                )
                if not shell_check.allowed:
                    sec.audit_record("tool_deny", {
                        "tool": tool_name, "command": cmd[:200],
                        "agent": agent_name, "reason": shell_check.reason,
                        "session": session_id,
                    })
                    # v1.12 (C): denial marker with a recovery path so the
                    # model can self-correct instead of dead-ending.
                    return ToolExecutionResult(
                        success=False,
                        error=(
                            f"[security: blocked] {shell_check.reason}\n"
                            "[security: hint] irreversible-operation deny "
                            "floor — do NOT retry unchanged; rewrite to a "
                            "reversible form, skip this step, or ask the "
                            "user to adjust shell_safety.deny_patterns."
                        ),
                        metadata={
                            "security_block": True,
                            "reason": shell_check.reason,
                        },
                    )

            # Audit log
            sec.audit_record("tool_execute", {
                "tool": tool_name, "agent": agent_name,
                "session": session_id,
            })

        # Count this call toward rate limit (after security gates pass)
        self._run_call_counts[rate_bucket] = self._run_call_counts.get(rate_bucket, 0) + 1

        # --- V2 hook: fs.pre_write (blockable) — file write/delete tools ---
        # Fires after all security gates pass but BEFORE the handler runs, so
        # extensions can veto or observe pending filesystem mutations.
        _profile = getattr(getattr(tool, "definition", None), "security_profile", "")
        _fs_write = _profile in (
            SecurityProfile.FILE_WRITE.value, SecurityProfile.FILE_DELETE.value,
        )
        _fs_path = ""
        if _fs_write:
            _fs_path = str(kwargs.get(tool.definition.path_param or "path", "") or "")
        if _fs_write and _fs_path:
            try:
                from boarai.core.extension import get_hook_registry

                _pre = await get_hook_registry().trigger(
                    "core.fs.pre_write",
                    {
                        "tool": tool_name,
                        "path": _fs_path,
                        "session_id": session_id,
                        "agent": agent_name,
                        "operation": "delete" if _profile == SecurityProfile.FILE_DELETE.value else "write",
                    },
                    timeout=2.0,
                )
                if _pre is not None and _pre.blocked:
                    # Audit is best-effort and must NEVER swallow the veto
                    # decision (an uninitialized SecurityManager raises).
                    if sec:
                        try:
                            sec.audit_record("tool_deny", {
                                "tool": tool_name, "path": _fs_path,
                                "agent": agent_name,
                                "reason": _pre.reason or "blocked by fs hook",
                                "session": session_id,
                            })
                        except Exception:  # noqa: BLE001
                            pass
                    return ToolExecutionResult(
                        success=False,
                        error=f"Security: {_pre.reason or 'blocked by fs hook'}",
                        metadata={
                            "security_block": True,
                            "hook_block": True,
                            "reason": _pre.reason,
                        },
                    )
            except Exception:  # noqa: BLE001 — hooks must never break tools
                pass

        # --- Execute via onion pipeline ---
        import time
        start_time = time.time()

        # Session workspace for tools (relative paths / shell cwd)
        workspace_path = None
        if session_id:
            try:
                sm = self._session_manager if hasattr(self, "_session_manager") else None
                if sm is None:
                    from boarai.core.session.manager import get_session_manager
                    sm = get_session_manager()
                sess = sm._sessions.get(session_id)
                if sess is not None and getattr(sess, "workspace_path", None):
                    workspace_path = sess.workspace_path
            except Exception:
                workspace_path = None

        ctx = ToolContext(
            session_id=session_id,
            workspace=workspace_path,
            run_id=run_id,
            tool_call_id=tool_call_id,
        )

        try:
            await publish_event(
                EventType.TOOL_EXECUTED,
                {
                    "tool": tool_name,
                    "parameters": self._redact_parameters(kwargs),
                },
                "tool_registry",
            )

            if self._middlewares:
                # Run through onion pipeline
                from ..pipeline import execute_pipeline

                result = await execute_pipeline(
                    tool_name=tool_name,
                    args=kwargs,
                    ctx=ctx,
                    middlewares=self._middlewares,
                    handler=tool.handler,
                )
            else:
                # No middlewares — direct execution
                if asyncio.iscoroutinefunction(tool.handler):
                    result = await tool.handler(ctx, **kwargs)
                else:
                    # Sync handlers must not block the event loop
                    result = await asyncio.to_thread(tool.handler, ctx, **kwargs)

            execution_time = time.time() - start_time
            tool._execution_count += 1
            # Treat soft-failure dicts (e.g. shell {"error": ...}) as errors for
            # the streak. Security blocks are NOT counted — they are the fence
            # working as intended, not execution failures.
            _soft_error = self._result_indicates_error(result)
            if _soft_error:
                self._tool_error_streak[streak_key] = self._tool_error_streak.get(streak_key, 0) + 1
            else:
                self._tool_error_streak[streak_key] = 0

            # --- V2 hooks: fs.post_write / tool.on_error (observe) ---
            try:
                from boarai.core.extension import get_hook_registry

                _hooks = get_hook_registry()
                if _fs_write and _fs_path and not _soft_error:
                    await _hooks.trigger(
                        "core.fs.post_write",
                        {"tool": tool_name, "path": _fs_path,
                         "session_id": session_id, "agent": agent_name},
                        timeout=2.0,
                    )
                if _soft_error:
                    await _hooks.trigger(
                        "core.tool.on_error",
                        {"tool": tool_name, "run_id": run_id,
                         "session_id": session_id, "agent": agent_name,
                         "error": (result or {}).get("error") if isinstance(result, dict) else str(result)},
                        timeout=2.0,
                    )
            except Exception:  # noqa: BLE001
                pass

            return ToolExecutionResult(success=True, result=result, execution_time=execution_time)

        except Exception as e:
            execution_time = time.time() - start_time
            tool._error_count += 1
            self._tool_error_streak[streak_key] = self._tool_error_streak.get(streak_key, 0) + 1
            error_msg = f"Tool {tool_name} failed: {e}"
            logger.error(error_msg)
            await publish_event(EventType.TOOL_ERROR, {"tool": tool_name, "error": str(e)}, "tool_registry")
            # --- V2 hook: tool.on_error (observe) — raised exception path ---
            try:
                from boarai.core.extension import get_hook_registry

                await get_hook_registry().trigger(
                    "core.tool.on_error",
                    {"tool": tool_name, "run_id": run_id,
                     "session_id": session_id, "agent": agent_name,
                     "error": str(e), "exception": True},
                    timeout=2.0,
                )
            except Exception:  # noqa: BLE001
                pass
            return ToolExecutionResult(success=False, error=str(e), execution_time=execution_time)

    @staticmethod
    def _redact_parameters(kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Redact sensitive payload keys before publishing tool events."""
        sensitive_keys = {
            "command",
            "content",
            "path",
            "source",
            "destination",
            "directory",
            "file",
            "data",
            "text",
            "script",
            "input",
        }
        redacted = {}
        for key, value in kwargs.items():
            if key in sensitive_keys and value is not None:
                redacted[key] = "<redacted>"
            else:
                redacted[key] = value
        return redacted

    @staticmethod
    def _result_indicates_error(result: Any) -> bool:
        """Heuristic: tool returned a structured error payload without raising."""
        if isinstance(result, dict):
            err = result.get("error")
            if err:
                if isinstance(err, str) and err.lower().startswith("security:"):
                    return False
                return True
            if result.get("success") is False:
                return True
        return False

    def reset_run_rate_limits(self, run_id: str) -> None:
        """Clear rate-limit counters for a finished run."""
        self._run_call_counts.pop(run_id, None)
        self._tool_error_streak.clear()

    def get_tool(self, tool_name: str) -> Optional[Tool]:
        return self._tools.get(tool_name)

    def list_tools(self) -> List[str]:
        return list(self._tools.keys())

    def get_tools_by_status(self, status: ToolStatus) -> List[Tool]:
        return [t for t in self._tools.values() if t.status == status]

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """LLM-facing tool schemas — hidden tools (llm_visible=False) excluded."""
        return [
            tool.definition.to_openai_function()
            for tool in self._tools.values()
            if tool.definition.llm_visible
        ]

    def get_stats(self) -> Dict[str, Any]:
        status_counts = {s.value: len(self.get_tools_by_status(s)) for s in ToolStatus}
        return {"total_tools": len(self._tools), "tools": list(self._tools.keys()), "status_counts": status_counts}


def _agent_config_stub(
    allowlist: Optional[List[str]],
    allowed_categories: Optional[List[str]] = None,
    can_auto_approve: bool = False,
):
    """Minimal agent view for ``ToolGuard.evaluate_tool``.

    Mirrors every agent field the evaluator reads. Omitting one would degrade
    it to "no"/empty on the registry path while the run-gate path (which gets
    the full AgentConfig) would honour it — the two must agree.
    """
    return SimpleNamespace(
        zone2_tool_names=allowlist,
        allowed_categories=allowed_categories or [],
        can_auto_approve=bool(can_auto_approve),
    )


def _session_overrides(session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Cached session's security overrides, or None."""
    if not session_id:
        return None
    try:
        from boarai.core.session.manager import get_session_manager

        session = get_session_manager().peek_session(session_id)
    except Exception:  # noqa: BLE001 — no session context → global policy only
        return None
    overrides = getattr(session, "security_overrides", None) if session else None
    return overrides or None


_tool_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = ToolRegistry()
        # If security was initialized AFTER this registry was first created
        # (tools register at import time, security initializes in main),
        # wire ourselves into ToolGuard here too — keep both hook points
        # so either initialization order ends up connected.
        try:
            from boarai.core.security import get_security_manager

            sec = get_security_manager()
            if getattr(sec, "_initialized", False):
                sec.tool_guard.set_tool_registry(_tool_registry)
        except Exception:  # noqa: BLE001
            pass
    return _tool_registry


def reset_tool_registry() -> None:
    global _tool_registry
    _tool_registry = None


class ToolError(Exception):
    def __init__(self, message: str, tool: Optional[str] = None):
        super().__init__(message)
        self.tool = tool
