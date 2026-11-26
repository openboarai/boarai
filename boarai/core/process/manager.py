"""Port-authoritative process detection for the BoarAi server.

The listener on the server port is the single source of truth: whoever holds
it is the candidate, and its command line must prove it is a BoarAi server
entry (see :func:`_cmdline_is_boar`) before the runtime reports "running" or
kills anything. No PID file, no lock file: both go stale after a crash and
would authorise a wrong kill.
"""

import os
import re
import sys
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

DEFAULT_PORT = 26620

# Server entry shapes, matched against the joined command line (lowercased):
#   - source tree:  python <root>/main.py start
#   - pip module:   python -m boarai start
#   - console script (pip): .../boarai[.exe] start
# Basename-anchored so a checkout path merely CONTAINING "boarai" (or a test
# runner like tests/run.py) never impersonates the server; the module shape
# requires the bare package name so `-m boarai.core.update.worker` (the
# updater subprocess) is never mistaken for a server.
_BOAR_CMD_PATTERNS = (
    re.compile(r"(^|[\s\\/])main\.py([\s]|$)"),
    re.compile(r"-m\s+boarai(\s|$)"),
    re.compile(r"(^|[\s\\/])boarai(\.exe)?([\s]|$)"),
)


def _cmdline_is_boar(cmd: str) -> bool:
    """Whether a joined command line proves a BoarAi server entry."""
    low = (cmd or "").lower()
    if not low:
        return False
    return any(p.search(low) for p in _BOAR_CMD_PATTERNS)


def _pids_from_procfs(port: int, proc_root: str = "/proc") -> list:
    """PIDs LISTENING on ``port`` parsed straight from /proc (slim containers ship neither psutil nor lsof)."""
    inodes = set()
    try:
        for table in ("net/tcp", "net/tcp6"):
            table_path = Path(proc_root) / table
            if not table_path.is_file():
                continue
            for line in table_path.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
                fields = line.split()
                if len(fields) < 10 or fields[3] != "0A":  # 0A = TCP_LISTEN
                    continue
                try:
                    local_port = int(fields[1].rsplit(":", 1)[1], 16)
                except (IndexError, ValueError):
                    continue
                if local_port == port:
                    inodes.add(fields[9])
    except OSError:
        return []
    if not inodes:
        return []
    pids = []
    try:
        entries = list(Path(proc_root).iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            for fd in (entry / "fd").iterdir():
                try:
                    target = os.readlink(str(fd))
                except OSError:
                    continue  # other users' fds or races — skip silently
                if target.startswith("socket:[") and target[8:-1] in inodes:
                    pids.append(int(entry.name))
                    break
        except OSError:
            continue
    return pids


class ProcessManager:
    """Answer "is the server up, and which PID owns it" without trusting disk."""

    def __init__(self, project_root: Optional[Path] = None):
        if project_root is None:
            try:
                from boarai.core.runtime import get_project_root

                project_root = get_project_root()
            except Exception:
                project_root = None
            if project_root is None:
                project_root = Path(__file__).resolve().parent.parent.parent.parent

        self._project_root = Path(project_root)
        from boarai.core.runtime import resolve_data_dir
        data_dir = resolve_data_dir(self._project_root)
        self._config_file = data_dir / "config" / "server.json"

    @property
    def config_file(self) -> Path:
        """Path of ``server.json`` inside the resolved data directory."""
        return self._config_file

    def is_running(self) -> bool:
        """Report whether a verified BoarAi server owns the server port."""
        pid = self.find_pid_on_port(self.server_port())
        if pid is None:
            return False

        if not self.is_boar_pid(pid):
            # Port is held by some OTHER process; that is not "us running".
            logger.debug(
                f"Port in use by non-BoarAi process {pid}; not ours"
            )
            return False

        return True

    def get_running_pid(self) -> Optional[int]:
        """Get the PID of the verified running BoarAi process."""
        if not self.is_running():
            return None
        return self.find_pid_on_port(self.server_port())

    def is_boar_pid(self, pid: Optional[int]) -> bool:
        """Decide whether a PID is safe to treat as the BoarAi server."""
        if pid is None or pid <= 0:
            return False

        try:
            import psutil

            try:
                proc = psutil.Process(pid)
                cmd = " ".join(proc.cmdline() or [])
                return _cmdline_is_boar(cmd)
            except (psutil.NoSuchProcess, psutil.AccessDenied,
                    psutil.ZombieProcess, psutil.TimeoutExpired):
                return False
        except ImportError:
            pass

        import subprocess

        if sys.platform == "win32":
            probes = (
                (
                    "powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "(Get-CimInstance Win32_Process -Filter "
                    f"\"ProcessId = {pid}\").CommandLine",
                ),
                (
                    "wmic", "process", "where", f"processid={pid}",
                    "get", "commandline", "/format:list",
                ),
            )
            for probe in probes:
                try:
                    result = subprocess.run(
                        probe, capture_output=True, text=True, timeout=5
                    )
                    if _cmdline_is_boar(result.stdout or ""):
                        return True
                    if result.returncode == 0 and not (result.stdout or "").strip():
                        # The probe succeeded but the process exposes no
                        # command line, so identity cannot be proven: refuse.
                        return False
                except (OSError, subprocess.TimeoutExpired):
                    continue
            return False

        # POSIX: /proc cmdline probe
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().decode("utf-8", errors="ignore").replace("\0", " ")
            return _cmdline_is_boar(cmd)
        except OSError:
            return False

    def server_port(self) -> int:
        """Resolve the listening port from the same chain the server uses."""
        try:
            env_port = os.environ.get("BOARAI_PORT")
            if env_port:
                return int(env_port)
        except (TypeError, ValueError):
            pass
        try:
            return int(self.get_server_config().get("port", DEFAULT_PORT))
        except (TypeError, ValueError):
            return DEFAULT_PORT

    def get_server_config(self) -> Dict[str, Any]:
        """Load ``server.json`` merged over the built-in defaults."""
        from boarai.core.server import HOST, PORT
        
        default_config = {
            "host": HOST,
            "port": PORT
        }
        
        if not self._config_file.exists():
            return default_config
        
        try:
            config = json.loads(self._config_file.read_text())
            if not isinstance(config, dict):
                return default_config
            # Convert the legacy http_port spelling BEFORE merging: after the
            # merge the default "port" key is always present, so checking the
            # merged dict made this conversion dead logic and a legacy
            # server.json silently fell back to the default port.
            if "port" not in config and "http_port" in config:
                config = {**config, "port": config["http_port"]}
            merged = {**default_config, **config}
            merged.pop("http_port", None)
            merged.pop("ws_port", None)  # unified server: HTTP+WS share one port
            return merged
        except (json.JSONDecodeError, OSError):
            return default_config
    
    def find_pid_on_port(self, port: int) -> Optional[int]:
        """Find the PID of a process listening on the given port."""
        pids = self._listening_pids(port)
        return pids[0] if pids else None

    def is_port_owned_by_pid(self, pid: int, port: int) -> bool:
        """Check whether a specific PID currently holds the given listener port."""
        return pid in self._listening_pids(port)

    def _listening_pids(self, port: int) -> list:
        """Return the PIDs of all processes LISTENING on ``port``."""
        pids = []

        try:
            import psutil
        except ImportError:
            psutil = None

        if psutil is not None:
            try:
                for conn in psutil.net_connections("tcp"):
                    if (conn.status == psutil.CONN_LISTEN
                            and conn.pid
                            and conn.laddr
                            and conn.laddr.port == port):
                        pids.append(conn.pid)
                return pids
            except (psutil.AccessDenied, psutil.Error):
                pass  # fall through to procfs/lsof

        if sys.platform != "win32":
            pids = _pids_from_procfs(port)
            if pids:
                return pids

        import subprocess

        try:
            if sys.platform == "win32":
                result = subprocess.run(
                    ["netstat", "-ano"], capture_output=True, text=True
                )
                for line in result.stdout.splitlines():
                    if f":{port}" in line and "LISTENING" in line:
                        parts = line.split()
                        if parts:
                            try:
                                pids.append(int(parts[-1]))
                            except ValueError:
                                continue
            else:
                result = subprocess.run(
                    ["lsof", "-i", f":{port}", "-sTCP:LISTEN", "-t"],
                    capture_output=True, text=True
                )
                for token in (result.stdout or "").split():
                    try:
                        pids.append(int(token))
                    except ValueError:
                        continue
        except Exception:
            pass
        return pids

    def is_port_available(self, port: int, host: str = "127.0.0.1") -> bool:
        """Check if a port is available."""
        import socket
        
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                result = s.connect_ex((host, port))
                return result != 0
        except OSError:
            return True


# Process-wide singleton; the first caller pins the project root it resolved.
_process_manager: Optional[ProcessManager] = None


def get_process_manager(project_root: Optional[Path] = None) -> ProcessManager:
    """Return the shared process manager, creating it on first use."""
    global _process_manager
    if _process_manager is None:
        _process_manager = ProcessManager(project_root)
    return _process_manager