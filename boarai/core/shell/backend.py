"""Pluggable shell execution backends.

Running every command through ``asyncio.create_subprocess_shell`` on the host
left two questions unanswered:

- Where does a command actually run? A backend makes the execution surface
  explicit and gives deployments a seam for container isolation.
- May a command run without approval? The guard used to judge the command text
  alone, but the same text is catastrophic on the host and harmless inside a
  container that mounts nothing. Each backend therefore reports a
  :class:`BackendInfo` and the guard consults it instead of guessing.

Relaxation is one-directional: isolation may loosen a decision only when no
host surface is reachable. A container with a bind mount, host networking, or a
shared PID namespace is not a boundary, and treating it as one is how a
"sandboxed" command ends up rewriting ``~/.ssh``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import posixpath
import re
import signal
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple, runtime_checkable

logger = logging.getLogger(__name__)

# ``docker run -v host:container[:ro|rw]``. The container side is always an
# absolute POSIX path, so anchoring on ``/`` keeps Windows drive letters from
# being mistaken for the separator.
_MOUNT_SPEC_RE = re.compile(
    r"^(?P<host>.+?):(?P<cwd>/[^:]*)(?::(?P<mode>ro|rw))?$"
)


class IsolationLevel(str, Enum):
    """How much of a boundary a backend puts between a command and the host."""

    NONE = "none"
    """Same host, same user, same filesystem (the default local backend)."""

    PROCESS = "process"
    """A separate process or user, but the host filesystem is still reachable."""

    CONTAINER = "container"
    """A container with its own filesystem and namespace."""

    SANDBOX = "sandbox"
    """OS-level confinement (Landlock / Seatbelt / job objects)."""


_ORDER: Tuple[IsolationLevel, ...] = (
    IsolationLevel.NONE,
    IsolationLevel.PROCESS,
    IsolationLevel.CONTAINER,
    IsolationLevel.SANDBOX,
)


def isolation_at_least(level: IsolationLevel, minimum: IsolationLevel) -> bool:
    """Compare two isolation levels by their position in the ordered scale."""
    return _ORDER.index(level) >= _ORDER.index(minimum)


DEFAULT_MAX_OUTPUT_BYTES = 1_048_576
"""Per-stream retention bound (1 MiB) when a caller does not set one."""

@dataclass(frozen=True, slots=True)
class BackendInfo:
    """What a backend exposes, for the approval policy to reason about."""

    name: str
    isolation: IsolationLevel
    available: bool = True
    reason: str = ""
    bind_mounts: Tuple[str, ...] = ()
    host_network: bool = False

    @property
    def host_exposed(self) -> bool:
        """Whether a command run here can touch the host."""
        if not isolation_at_least(self.isolation, IsolationLevel.CONTAINER):
            return True
        return bool(self.bind_mounts) or self.host_network

    @property
    def contained(self) -> bool:
        """True only when the backend is a real boundary with nothing exposed."""
        return self.available and not self.host_exposed


@dataclass(frozen=True, slots=True)
class ShellRequest:
    """A fully-prepared command for a backend to execute."""

    command: str
    cwd: Optional[str] = None
    timeout: float = 30.0
    env: Optional[Dict[str, str]] = None
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    """Per-stream accumulation bound.

    The live progress callback still sees every chunk; this only bounds what
    is retained for the final result, so a chatty command cannot exhaust
    memory. 0 or negative means unbounded.
    """


@dataclass(slots=True)
class ShellOutcome:
    """Raw execution result, before any tool-level formatting."""

    exit_code: Optional[int]
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    duration: float = 0.0
    cancelled: bool = False


DataSink = Callable[[str, bytes], None]
"""``(stream_name, chunk)`` progress callback; may be None."""


@runtime_checkable
class ShellBackend(Protocol):
    """A place where shell commands can run."""

    name: str

    def info(self) -> BackendInfo:
        """Isolation and host-exposure facts for the approval policy."""

    def map_cwd(self, host_cwd: Optional[str]) -> Optional[str]:
        """Translate a host working directory into this backend's own view."""

    async def execute(
        self, request: ShellRequest, on_data: Optional[DataSink] = None,
    ) -> ShellOutcome:
        """Run one command to completion, timeout, or cancellation."""


# --- Shared process plumbing ---


def _is_windows() -> bool:
    return os.name == "nt"


async def _taskkill_tree(pid: int) -> bool:
    """Windows: force-kill a whole process tree via ``taskkill /F /T``."""
    try:
        tk = await asyncio.create_subprocess_exec(
            "taskkill", "/F", "/T", "/PID", str(pid),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        await asyncio.wait_for(tk.wait(), timeout=5.0)
        return tk.returncode == 0
    except Exception:  # noqa: BLE001 - caller falls back to a direct kill
        return False


async def _kill_process_tree(proc: "asyncio.subprocess.Process") -> None:
    """Terminate a child and everything it spawned."""
    if proc.returncode is not None:
        return
    try:
        if _is_windows():
            if not await _taskkill_tree(proc.pid):
                proc.kill()
        else:
            try:
                pgid = os.getpgid(proc.pid)
            except (ProcessLookupError, OSError):
                pgid = None
            if pgid is not None:
                try:
                    os.killpg(pgid, signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                    return
                except asyncio.TimeoutError:
                    pass
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
            proc.kill()
    except Exception:  # noqa: BLE001 - last-resort direct kill
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


async def _drain(
    stream: Optional[asyncio.StreamReader],
    sink: List[bytes],
    on_data: Optional[DataSink],
    name: str,
    max_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> None:
    """Read a child stream to EOF, forwarding every chunk and retaining a bound."""
    if stream is None:
        return
    retained = 0
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return
        if max_bytes <= 0 or retained < max_bytes:
            if max_bytes > 0 and retained + len(chunk) > max_bytes:
                chunk = chunk[: max_bytes - retained]
            sink.append(chunk)
            retained += len(chunk)
        if on_data is not None:
            try:
                on_data(name, chunk)
            except Exception as e:  # noqa: BLE001 - progress must not kill a run
                logger.debug("shell data sink failed (%s): %s", name, e)


async def _join_readers(*tasks: "asyncio.Task[Any]", grace: float = 5.0) -> None:
    """Bounded join of the stream readers."""
    pending = [t for t in tasks if t is not None and not t.done()]
    if not pending:
        return
    _, still = await asyncio.wait(set(pending), timeout=grace)
    for t in still:
        t.cancel()


async def _run_subprocess(
    argv_or_shell: Any,
    *,
    shell: bool,
    cwd: Optional[str],
    timeout: float,
    env: Optional[Dict[str, str]],
    on_data: Optional[DataSink],
    extra_popen: Optional[Dict[str, Any]] = None,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> ShellOutcome:
    """Spawn, drain, and enforce a timeout on one child process."""
    started = time.monotonic()
    popen_kwargs: Dict[str, Any] = dict(extra_popen or {})
    # Windows: the server runs detached, so without CREATE_NO_WINDOW every
    # child gets its own visible console (the black-window flash).
    # POSIX: a new session gives the child its own process group, which is what
    # makes killpg able to reap the whole tree on timeout.
    if _is_windows():
        popen_kwargs.setdefault("creationflags", 0x08000000)
    else:
        popen_kwargs.setdefault("start_new_session", True)
    if env is not None:
        popen_kwargs["env"] = env

    if shell:
        proc = await asyncio.create_subprocess_shell(
            argv_or_shell,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            **popen_kwargs,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            *argv_or_shell,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            **popen_kwargs,
        )

    out_parts: List[bytes] = []
    err_parts: List[bytes] = []
    read_out = asyncio.create_task(
        _drain(proc.stdout, out_parts, on_data, "stdout", max_output_bytes)
    )
    read_err = asyncio.create_task(
        _drain(proc.stderr, err_parts, on_data, "stderr", max_output_bytes)
    )

    timed_out = False
    exit_code: Optional[int] = None
    try:
        try:
            exit_code = await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            await _kill_process_tree(proc)
            # Killing the tree closes the pipes; drain whatever tail is left.
            await _join_readers(read_out, read_err, grace=3.0)
            try:
                exit_code = await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                exit_code = None
        except asyncio.CancelledError:
            await _kill_process_tree(proc)
            raise

        await _join_readers(read_out, read_err, grace=5.0)
        if exit_code is None:
            exit_code = proc.returncode
        return ShellOutcome(
            exit_code=exit_code,
            stdout=b"".join(out_parts),
            stderr=b"".join(err_parts),
            timed_out=timed_out,
            duration=round(time.monotonic() - started, 3),
        )
    except asyncio.CancelledError:
        # A cancelled run must not leak its process group, and the
        # cancellation must keep propagating: swallowing it here would let a
        # user-stopped run continue with a synthesized "cancelled" result.
        await _kill_process_tree(proc)
        for task in (read_out, read_err):
            if not task.done():
                task.cancel()
        raise


# --- Local backend ---


class LocalBackend:
    """Runs commands on the host as the server's user. The default."""

    name = "local"

    def info(self) -> BackendInfo:
        return BackendInfo(
            name=self.name,
            isolation=IsolationLevel.NONE,
            available=True,
            reason="runs on the host as the server user",
        )

    def map_cwd(self, host_cwd: Optional[str]) -> Optional[str]:
        """Identity: the host path is the same path for a host process."""
        return host_cwd

    async def execute(
        self, request: ShellRequest, on_data: Optional[DataSink] = None,
    ) -> ShellOutcome:
        return await _run_subprocess(
            request.command,
            shell=True,
            cwd=request.cwd,
            timeout=request.timeout,
            env=request.env,
            on_data=on_data,
            max_output_bytes=request.max_output_bytes,
        )


# --- Docker backend ---


class DockerBackend:
    """Runs commands inside a container through the ``docker`` CLI."""

    name = "docker"

    def __init__(
        self,
        image: str = "python:3.12-slim",
        *,
        container: Optional[str] = None,
        workdir: str = "/work",
        mounts: Tuple[str, ...] = (),
        network: bool = False,
        docker_bin: str = "docker",
        extra_args: Tuple[str, ...] = (),
    ) -> None:
        self.image = image
        self.container = container
        self.workdir = workdir
        self.mounts = tuple(mounts)
        self.network = network
        self.docker_bin = docker_bin
        self.extra_args = tuple(extra_args)
        self._available: Optional[bool] = None

    def info(self) -> BackendInfo:
        # ``available`` is False until ``probe()`` actually got an answer:
        # an unverified container must never justify relaxing approvals.
        return BackendInfo(
            name=self.name,
            isolation=IsolationLevel.CONTAINER,
            available=self._available is True,
            reason=(
                "docker CLI not reachable" if self._available is False
                else "docker CLI not probed yet" if self._available is None
                else f"image={self.image}"
                + (f" container={self.container}" if self.container else " ephemeral")
            ),
            bind_mounts=self.mounts,
            host_network=self.network,
        )

    async def probe(self) -> bool:
        """Check the docker CLI answers. Cached for the process lifetime."""
        if self._available is not None:
            return self._available
        try:
            proc = await asyncio.create_subprocess_exec(
                self.docker_bin, "version", "--format", "{{.Server.Version}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10.0)
            self._available = proc.returncode == 0
        except (OSError, asyncio.TimeoutError, Exception):  # noqa: BLE001
            self._available = False
        if not self._available:
            logger.warning(
                "DockerBackend unavailable (%s did not answer); commands will fail "
                "until a docker daemon is reachable", self.docker_bin,
            )
        return self._available

    def _mount_pairs(self) -> List[Tuple[str, str]]:
        """(host_root, container_root) pairs parsed out of ``-v`` specs."""
        pairs: List[Tuple[str, str]] = []
        for spec in self.mounts:
            match = _MOUNT_SPEC_RE.match(spec)
            if match:
                pairs.append((match.group("host"), match.group("cwd")))
        return pairs

    def map_cwd(self, host_cwd: Optional[str]) -> Optional[str]:
        """Map a host directory into the container through the bind mounts."""
        if not host_cwd:
            return self.workdir
        try:
            host_norm = os.path.normcase(os.path.normpath(host_cwd))
        except (OSError, ValueError):
            return self.workdir
        best: Optional[Tuple[int, str, str]] = None
        for host_root, container_root in self._mount_pairs():
            try:
                root_norm = os.path.normcase(os.path.normpath(host_root))
            except (OSError, ValueError):
                continue
            if host_norm == root_norm or host_norm.startswith(root_norm + os.sep):
                if best is None or len(root_norm) > best[0]:
                    best = (len(root_norm), host_root, container_root)
        if best is None:
            return self.workdir
        try:
            rel = os.path.relpath(host_cwd, best[1])
        except ValueError:
            return best[2]
        if rel in (".", ""):
            return best[2]
        return posixpath.join(best[2], rel.replace(os.sep, "/"))

    def _argv(self, request: ShellRequest) -> List[str]:
        """Build the docker CLI argv for one command."""
        container_cwd = self.map_cwd(request.cwd) or self.workdir
        if self.container:
            argv = [self.docker_bin, "exec", "-i", "-w", container_cwd]
            if request.env:
                for key, value in request.env.items():
                    argv += ["-e", f"{key}={value}"]
            argv += list(self.extra_args)
            argv += [self.container, "sh", "-c", request.command]
            return argv

        argv = [self.docker_bin, "run", "--rm", "-i", "-w", container_cwd]
        if not self.network:
            argv += ["--network", "none"]
        for mount in self.mounts:
            argv += ["-v", mount]
        if request.env:
            for key, value in request.env.items():
                argv += ["-e", f"{key}={value}"]
        argv += list(self.extra_args)
        argv += [self.image, "sh", "-c", request.command]
        return argv

    async def execute(
        self, request: ShellRequest, on_data: Optional[DataSink] = None,
    ) -> ShellOutcome:
        if not await self.probe():
            # Fail loudly instead of spawning a `docker` that is not there:
            # the caller must see "backend unavailable", not "command not found".
            raise RuntimeError(
                f"shell backend 'docker' is unavailable: {self.docker_bin!r} "
                "did not answer"
            )
        # argv form, never a shell string: the command travels as one argument
        # to `sh -c`, so nothing in it can be re-interpreted by the HOST shell.
        return await _run_subprocess(
            self._argv(request),
            shell=False,
            cwd=None,
            timeout=request.timeout,
            env=None,
            on_data=on_data,
            max_output_bytes=request.max_output_bytes,
        )


# --- Registry ---

_BACKEND_FACTORIES: Dict[str, Callable[..., ShellBackend]] = {
    "local": lambda **kw: LocalBackend(),
    "docker": lambda **kw: DockerBackend(**kw),
}

_active: Optional[ShellBackend] = None


def register_backend(name: str, factory: Callable[..., ShellBackend]) -> None:
    """Register a backend factory (plugins and tests)."""
    _BACKEND_FACTORIES[name] = factory


def available_backends() -> List[str]:
    return sorted(_BACKEND_FACTORIES)


def configure_backend(
    name: Optional[str] = None, **options: Any,
) -> ShellBackend:
    """Select and cache the process-wide backend."""
    global _active
    resolved = (name or "local").strip().lower()
    if resolved not in _BACKEND_FACTORIES:
        raise KeyError(
            f"unknown shell backend {name!r}; available: {available_backends()}"
        )
    _active = _BACKEND_FACTORIES[resolved](**options)
    logger.info("Shell backend: %s", _active.info().reason)
    return _active


def get_shell_backend() -> ShellBackend:
    """The active backend, defaulting to local."""
    global _active
    if _active is None:
        _active = LocalBackend()
    return _active


def reset_shell_backend() -> None:
    """Drop the cached backend (tests and config reloads)."""
    global _active
    _active = None


def backend_allows_contained_relaxation(info: Optional[BackendInfo] = None) -> bool:
    """Whether the active backend is a boundary the guard may relax against."""
    return (info or get_shell_backend().info()).contained


__all__ = [
    "BackendInfo",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DataSink",
    "DockerBackend",
    "IsolationLevel",
    "LocalBackend",
    "ShellBackend",
    "ShellOutcome",
    "ShellRequest",
    "available_backends",
    "backend_allows_contained_relaxation",
    "configure_backend",
    "get_shell_backend",
    "isolation_at_least",
    "register_backend",
    "reset_shell_backend",
]
