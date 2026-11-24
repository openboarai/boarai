"""
Shell execution tool — communicates via ctx (no core business imports).
Self-registers with ToolRegistry on import.

Execution goes through ``core.shell.get_shell_backend()``: the tool owns
platform rewriting, sandbox checks and result formatting; spawning, draining,
timeout and tree-kill belong to the backend.
"""

import asyncio
import base64
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from boarai.core.extension.tool import (
    CompressStrategy,
    SecurityProfile,
    SuggestedCategory,
    ToolParameter,
    tool,
)
from boarai.core.shell import ShellRequest, get_shell_backend

logger = logging.getLogger(__name__)

# Capture shell redirect targets: >, >>, 1>, 2>, etc.
_REDIRECT_TARGET = re.compile(
    r'(?:(?:\d)?>{1,2}|>>)\s*([^\s|&;<>]+)',
    re.IGNORECASE,
)


def _is_windows() -> bool:
    import platform
    return platform.system() == "Windows"


def _decode_process_bytes(data: Optional[bytes]) -> str:
    """Decode subprocess stdout/stderr with Windows-friendly fallbacks."""
    if not data:
        return ""
    for encoding in ("utf-8", "gbk", "cp936", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _looks_like_powershell(cmd: str) -> bool:
    """Heuristic: command is meant for PowerShell, not cmd.exe."""
    s = (cmd or "").strip()
    if not s:
        return False
    lower = s.lower()
    if lower.startswith("powershell") or lower.startswith("pwsh"):
        return True
    # Common PS cmdlets / syntax that fail under cmd.exe with exit 255.
    ps_markers = (
        "get-", "set-", "select-", "where-object", "foreach-object",
        "write-host", "write-output", "out-file", "convertto-",
        "import-module", "export-", "new-item", "remove-item",
        "test-path", "resolve-path", "join-path", "split-path",
        "$_", " | %", " |%", " -eq ", " -ne ", " -like ", " -match ",
        " -notmatch ", " -contains ", " -notlike ",
    )
    return any(m in lower for m in ps_markers)


# cmd-native batch syntax PowerShell cannot parse at all (wrapping it into
# EncodedCommand yields exit 1 — real incident 2026-09-24).
_CMD_SYNTAX_MARKERS = ("for %", "for /", "%~", "@echo")


def _looks_like_cmd_syntax(cmd: str) -> bool:
    """Heuristic: command is cmd.exe batch syntax (for %d, @echo, %~dp0 ...)."""
    lower = (cmd or "").strip().lower()
    if not lower:
        return False
    return any(m in lower for m in _CMD_SYNTAX_MARKERS)


def _prepare_windows_command(cmd: str) -> str:
    """Prepare command for Windows default shell (cmd.exe via asyncio)."""
    stripped = (cmd or "").strip()
    if not stripped:
        return cmd

    lower = stripped.lower()
    if lower.startswith("powershell") or lower.startswith("pwsh"):
        return cmd

    if _looks_like_cmd_syntax(stripped):
        return cmd

    needs_ps = _looks_like_powershell(stripped)
    if not needs_ps:
        # cmd.exe interprets > < | & even inside double quotes in some cases.
        # Arrows (=> / ->) are NOT redirection — treat them as plain text.
        if re.search(r'["\'].*?(?:(?<![=\-])>|<|\||&&).*?["\']', stripped):
            needs_ps = True

    if not needs_ps:
        return cmd

    try:
        # PowerShell EncodedCommand requires Base64(UTF-16LE), not hex.
        encoded = base64.b64encode(stripped.encode("utf-16-le")).decode("ascii")
        return (
            "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass "
            "-OutputFormat Text "
            f"-EncodedCommand {encoded}"
        )
    except Exception:
        escaped = stripped.replace("'", "''")
        return (
            "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass "
            "-OutputFormat Text "
            f"-Command \"{escaped}\""
        )


def _strip_powershell_clixml(text: str) -> str:
    """Drop PowerShell CLIXML progress noise from stderr when useful text is absent."""
    if not text:
        return ""
    stripped = text.strip()
    if stripped.startswith("#< CLIXML") or stripped.startswith("<Objs "):
        # Keep only non-XML looking lines if any; otherwise empty.
        useful = []
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("#<") or s.startswith("<") or s.endswith(">"):
                continue
            useful.append(line)
        return "\n".join(useful).strip()
    return text


# --- Streaming execution helpers ---

# Per-stream accumulation cap (bytes). Bounds memory for runaway commands;
# the LLM-visible result is trimmed by tool_result_trimmer anyway.
_MAX_ACCUM_BYTES = 2 * 1024 * 1024


def _max_command_timeout() -> int:
    """Upper bound for one shell command (ShellSafetyConfig, default 600s)."""
    try:
        from boarai.core.security import get_security_manager

        sec = get_security_manager()
        if sec and getattr(sec, "_initialized", False):
            value = getattr(
                sec.tool_guard._shell_config, "max_command_timeout", 600.0
            )
            return max(1, int(float(value)))
    except Exception:  # noqa: BLE001 — config unavailable → default cap
        pass
    return 600


def _clamp_timeout(timeout: Any) -> int:
    """Clamp the model-requested timeout into [1, max_command_timeout]."""
    try:
        value = int(timeout)
    except (TypeError, ValueError):
        value = 30
    return max(1, min(value, _max_command_timeout()))


class _LiveToolOutput:
    """Throttled ``tool.output`` fan-out for one running shell command."""

    _FLUSH_INTERVAL = 0.4
    _FLUSH_BYTES = 8192
    _MAX_DELTA_CHARS = 4000  # per-frame cap (matches _compact_tool_results)

    def __init__(self, ctx: Any, name: str) -> None:
        self._run_id = getattr(ctx, "run_id", None) or ""
        self._tool_call_id = getattr(ctx, "tool_call_id", None) or ""
        self._session_id = getattr(ctx, "session_id", None) or ""
        self._name = name
        self._enabled = bool(self._run_id and self._tool_call_id)
        self._pending: Dict[str, List[str]] = {"stdout": [], "stderr": []}
        self._sizes: Dict[str, int] = {"stdout": 0, "stderr": 0}
        # Partial line carried over between chunks: the backend drains in
        # 64 KiB blocks, so decoding per chunk would split multi-byte
        # characters and desynchronise stderr/stdout line framing.
        self._residue: Dict[str, bytes] = {"stdout": b"", "stderr": b""}
        self._last_flush = time.monotonic()
        self._flush_task: Optional["asyncio.Task"] = None

    def feed(self, kind: str, data: bytes) -> None:
        """Backend ``on_data`` sink: buffer whole lines, schedule a flush."""
        if not self._enabled or not data:
            return
        buffered = self._residue[kind] + data
        lines = buffered.split(b"\n")
        self._residue[kind] = lines.pop()
        for line in lines:
            text = _decode_process_bytes(line + b"\n")
            if not text:
                continue
            self._pending[kind].append(text)
            self._sizes[kind] += len(text)
        if self.due():
            self._schedule_flush()

    def due(self) -> bool:
        if not self._enabled:
            return False
        if not any(self._pending[k] for k in self._pending):
            return False
        return (
            time.monotonic() - self._last_flush >= self._FLUSH_INTERVAL
            or max(self._sizes.values()) >= self._FLUSH_BYTES
        )

    def _schedule_flush(self) -> None:
        """Run a flush in the background without blocking the pipe reader."""
        if self._flush_task is not None and not self._flush_task.done():
            return
        try:
            self._flush_task = asyncio.create_task(self.flush())
        except RuntimeError:  # no running loop (defensive)
            self._flush_task = None

    def _drain_residue(self) -> None:
        """Fold the unterminated tail into pending before the final flush."""
        for kind in ("stdout", "stderr"):
            tail = self._residue[kind]
            if not tail:
                continue
            self._residue[kind] = b""
            text = _decode_process_bytes(tail)
            if text:
                self._pending[kind].append(text)
                self._sizes[kind] += len(text)

    async def flush(self, final: bool = False) -> None:
        if not self._enabled:
            return
        if final:
            pending_task = self._flush_task
            if pending_task is not None and not pending_task.done():
                await asyncio.gather(pending_task, return_exceptions=True)
            self._drain_residue()
        self._last_flush = time.monotonic()
        for kind in ("stdout", "stderr"):
            chunks = self._pending[kind]
            if not chunks:
                continue
            self._pending[kind] = []
            self._sizes[kind] = 0
            delta = "".join(chunks)
            if len(delta) > self._MAX_DELTA_CHARS:
                delta = (
                    delta[: self._MAX_DELTA_CHARS]
                    + "\n…(live output truncated for this frame)"
                )
            try:
                from boarai.core.session.run_manager import get_run_manager

                broadcaster = getattr(get_run_manager(), "_broadcaster", None)
                if broadcaster is not None:
                    await broadcaster.broadcast_tool_output(
                        run_id=self._run_id,
                        session_id=self._session_id,
                        tool_call_id=self._tool_call_id,
                        name=self._name,
                        stream=kind,
                        delta=delta,
                    )
            except Exception as e:  # noqa: BLE001 — fan-out must never fail the tool
                logger.debug("tool.output fan-out skipped: %s", e)


def _shell_result(
    *,
    command: str,
    executed_command: str,
    exit_code: Optional[int],
    stdout: str = "",
    stderr: str = "",
    error: Optional[str] = None,
    success: Optional[bool] = None,
) -> Dict[str, Any]:
    """Canonical shell payload: always includes command + execution outcome."""
    if success is None:
        success = (exit_code == 0) and not error
    payload: Dict[str, Any] = {
        "success": bool(success),
        "command": command,
        "executed_command": executed_command,
        "exit_code": exit_code,
        "stdout": stdout or "",
        "stderr": stderr or "",
    }
    if error:
        payload["error"] = error
    return payload


def _resolve_workdir(root: Path, workdir: str) -> Tuple[Optional[Path], Optional[str]]:
    """Resolve workdir and ensure it stays inside the session workspace sandbox."""
    try:
        root_resolved = Path(root).resolve()
    except OSError as e:
        return None, f"Invalid session workspace: {e}"

    if not workdir or workdir == ".":
        return root_resolved, None

    candidate = Path(workdir)
    if not candidate.is_absolute():
        candidate = root_resolved / candidate

    try:
        resolved = candidate.resolve()
        resolved.relative_to(root_resolved)
    except (ValueError, OSError):
        return None, f"Working directory escapes session workspace: {workdir}"

    if not resolved.exists() or not resolved.is_dir():
        return None, f"Working directory not found: {workdir}"

    return resolved, None


def _redirect_target_outside_root(command: str, roots: "SandboxRoots") -> Optional[str]:
    """Return first redirect target that resolves outside the sandbox roots."""
    from boarai.core.security.path_utils import is_within

    for match in _REDIRECT_TARGET.finditer(command or ""):
        raw = match.group(1).strip().strip('"').strip("'")
        if not raw or raw in ("&1", "&2", "/dev/null", "nul", "NUL"):
            continue
        # Relative redirects land under cwd (sandbox) — allow
        p = Path(raw)
        if not p.is_absolute() and not (len(raw) >= 2 and raw[1] == ":"):
            continue
        try:
            target = p.expanduser().resolve()
        except (ValueError, OSError):
            return raw
        if not is_within(target, roots.readable()):
            return raw
    return None


@tool(name="shell_execute",
      description="Execute a shell command in the session workspace. Returns stdout, stderr, and exit code. Use for file listing, git operations, running scripts, etc.",
      security_profile=SecurityProfile.SHELL,
      shell_param="command",
      compress_strategy=CompressStrategy.SHELL,
      suggested_category=SuggestedCategory.DANGEROUS,
      parameters=[
          ToolParameter(name="command", type="string", required=True,
                        description="Shell command to execute"),
          ToolParameter(name="timeout", type="integer", required=False, default=30,
                        description="Timeout in seconds (max 600)"),
          ToolParameter(name="workdir", type="string", required=False, default=".",
                        description="Working directory (relative to the session workspace)"),
      ])
async def shell_execute(ctx, command: str, timeout: int = 30, workdir: str = ".") -> Dict:
    original_command = command or ""
    # P6: trigger ext.shell_tools.pre_execute hook (blockable) — other
    # extensions can veto shell commands without modifying this extension.
    try:
        from boarai.core.extension import get_hook_registry
        reg = get_hook_registry()
        if reg.is_declared("ext.shell_tools.pre_execute"):
            hook = await reg.trigger(
                "ext.shell_tools.pre_execute",
                {"command": original_command, "workdir": workdir},
                timeout=2.0,
            )
            if hook and hook.blocked:
                return _shell_result(
                    command=original_command,
                    executed_command=original_command,
                    exit_code=None,
                    error=hook.reason or "blocked by extension hook",
                    success=False,
                )
    except Exception:  # noqa: BLE001
        pass
    # Sandbox roots: workspace (writable) + .boarai/temp (writable) +
    # project_root (read-only reach). file tools and shell tools share this
    # single source of truth so `.boarai/temp` is writable AND executable.
    ws = getattr(ctx, "workspace", None)
    pr = getattr(ctx, "project_root", None)
    if not pr and not ws:
        return _shell_result(
            command=original_command,
            executed_command=original_command,
            exit_code=None,
            error="Session workspace not set",
            success=False,
        )

    from boarai.core.security.path_utils import resolve_sandbox_roots, get_temp_dir

    roots = resolve_sandbox_roots(
        workspace=ws,
        project_root=pr,
        temp_dir=get_temp_dir(pr),
    )

    # workdir is anchored to the session workspace (project_root is only a
    # last-resort fallback when workspace resolution returned nothing).
    anchor = ws if ws is not None else pr
    cwd, wd_err = _resolve_workdir(Path(anchor), workdir)
    if wd_err:
        return _shell_result(
            command=original_command,
            executed_command=original_command,
            exit_code=None,
            error=wd_err,
            success=False,
        )

    # Block absolute redirect targets outside the sandbox (PathGuard does not cover shell)
    bad_redirect = _redirect_target_outside_root(original_command, roots)
    if bad_redirect:
        return _shell_result(
            command=original_command,
            executed_command=original_command,
            exit_code=None,
            error=f"Security: shell redirect target outside sandbox roots: {bad_redirect}",
            success=False,
        )

    # Reads outside the sandbox are NOT blocked here (v1.12 "reads stay free",
    # matching PathGuard): `cat /etc/hosts` / `ls C:\Users\...` must run when
    # the guard allowed them. Writes still are — the redirect fence below is
    # the write half of the same rule, and the credential-read floor /
    # external-write confirm tier are enforced by ToolGuard upstream.

    # Windows: prefer PowerShell so PS cmdlets and quoted redirects work.
    executed_command = (
        _prepare_windows_command(original_command)
        if _is_windows()
        else original_command
    )

    # Windows CMD does not support UNC paths as current directory
    cwd_str = str(cwd)
    if cwd_str.startswith("\\\\") or cwd_str.startswith("//"):
        # pushd/popd is a cmd construct; keep UNC wrap only for the cmd shell path.
        if _is_windows() and executed_command.lower().startswith("powershell"):
            # PowerShell can Set-Location to UNC; inject into encoded command body.
            # Simpler: set cwd=None and prefix with Set-Location.
            ps_body = (
                f"Set-Location -LiteralPath '{cwd_str.replace(chr(39), chr(39)+chr(39))}'; "
                f"{original_command}"
            )
            executed_command = _prepare_windows_command(ps_body)
            cwd_str = None
            logger.info("UNC path detected, injecting Set-Location for PowerShell")
        else:
            executed_command = f'pushd "{cwd_str}" && {executed_command} && popd'
            cwd_str = None
            logger.info("UNC path detected, wrapping with pushd/popd")

    # Clamp the model-requested timeout into [1, max_command_timeout]: long
    # builds/test suites are legitimate, but the model must never park a run
    # forever. Cap comes from ShellSafetyConfig (default 600s).
    timeout = _clamp_timeout(timeout)

    # Execution itself belongs to the configured backend (host or container).
    # Spawn / drain / tree-kill / timeout discipline lives there so the shell
    # tool and any future caller cannot drift into different process handling.
    request = ShellRequest(
        command=executed_command,
        cwd=cwd_str if isinstance(cwd_str, str) else None,
        timeout=float(timeout),
        max_output_bytes=_MAX_ACCUM_BYTES,
    )
    live = _LiveToolOutput(ctx, "shell_execute")

    try:
        logger.info(
            "Shell command (timeout=%ss, backend=%s): %s",
            timeout,
            get_shell_backend().name,
            original_command[:200],
        )
        if executed_command != original_command:
            logger.debug("Executed shell form: %s", executed_command[:200])

        outcome = await get_shell_backend().execute(request, on_data=live.feed)
        await live.flush(final=True)
    except asyncio.CancelledError:
        # The backend already reaped the tree before propagating; the run
        # layer needs the cancellation to keep travelling.
        raise
    except RuntimeError as e:
        # A configured-but-unreachable backend (docker daemon down) must say
        # so instead of masquerading as a missing command.
        msg = f"Shell backend unavailable: {e}"
        return _shell_result(
            command=original_command,
            executed_command=executed_command,
            exit_code=None,
            stdout="",
            stderr=msg,
            error=msg,
            success=False,
        )
    except FileNotFoundError:
        missing = (original_command.split() or ["?"])[0]
        msg = f"Command not found: {missing}"
        return _shell_result(
            command=original_command,
            executed_command=executed_command,
            exit_code=None,
            stdout="",
            stderr=msg,
            error=msg,
            success=False,
        )
    except Exception as e:
        return _shell_result(
            command=original_command,
            executed_command=executed_command,
            exit_code=None,
            stdout="",
            stderr=str(e),
            error=f"Shell execution failed: {e}",
            success=False,
        )

    exit_code = outcome.exit_code
    if outcome.timed_out:
        timeout_msg = f"Command timed out after {timeout}s"
        out_tail = _decode_process_bytes(outcome.stdout)
        err_tail = _decode_process_bytes(outcome.stderr)
        return _shell_result(
            command=original_command,
            executed_command=executed_command,
            exit_code=exit_code,
            stdout=out_tail,
            stderr=(err_tail + "\n" + timeout_msg).strip(),
            error=timeout_msg,
            success=False,
        )

    out_text = _decode_process_bytes(outcome.stdout)
    err_text = _strip_powershell_clixml(_decode_process_bytes(outcome.stderr))
    # PowerShell may put CLIXML progress on stderr even on success.
    success = exit_code == 0
    error = None
    if not success:
        error = err_text.strip() or f"Command failed with exit code {exit_code}"
    return _shell_result(
        command=original_command,
        executed_command=executed_command,
        exit_code=exit_code,
        stdout=out_text,
        stderr=err_text,
        success=success,
        error=error,
    )
