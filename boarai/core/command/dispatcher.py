"""Server process management for the ``main.py`` entry point.

This is NOT a command system - it is the implementation behind
``python main.py stop|restart|status``. There is no injected shell
command, no ``python -m boarai.core.command`` module entry, no ``ext``
subcommand and no terminal command registry: the in-session (chat)
command domain lives in :mod:`core.command.registry` /
:mod:`core.command.chat`, and process management calls these functions
directly.

Extension management is a WebUI/REST concern (``/api/extensions`` and
friends); it has no backend-command surface.
"""

import asyncio
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_project_root() -> Path:
    """Get project root (runtime single source; file-path fallback)."""
    from boarai.core.runtime import get_project_root

    root = get_project_root()
    if root is not None:
        return Path(root)
    return Path(__file__).resolve().parent.parent.parent.parent


def _server_base_url() -> str:
    """Loopback base URL of the (presumed) running server."""
    from boarai.core.process import get_process_manager
    port = get_process_manager(_get_project_root()).server_port()
    return f"http://127.0.0.1:{port}"


async def _api_auth_headers(session, base_url: str) -> dict:
    """Read the local bootstrap token or use the configured remote token."""
    import os
    import aiohttp

    token = os.getenv("BOARAI_API_TOKEN", "").strip()
    if not token:
        async with session.get(
            f"{base_url}/api/auth/token",
            timeout=aiohttp.ClientTimeout(total=5),
        ) as response:
            response.raise_for_status()
            token = str((await response.json()).get("token", "")).strip()
    if len(token) < 32:
        raise RuntimeError("BoarAI API token is missing or invalid")
    return {"Authorization": f"Bearer {token}"}


# =========================================================================
# Process management: status / stop / restart
# =========================================================================


def cmd_status() -> int:
    """``main.py status``: report whether the server process is up."""
    from boarai.core.process import get_process_manager

    pm = get_process_manager(_get_project_root())
    if pm.is_running():
        pid = pm.get_running_pid()
        config = pm.get_server_config()
        print(f"BoarAi server is running (PID: {pid})")
        print(f"Server: http://{config['host']}:{config['port']} (HTTP + WebSocket)")
    else:
        port = pm.server_port()
        if not pm.is_port_available(port):
            other = pm.find_pid_on_port(port)
            print(f"Port {port} is held by another program (PID: {other}); BoarAi server is not running")
        else:
            print("BoarAi server is not running")
    return 0


def _force_terminate(pid: int) -> None:
    """Last-resort terminate. MUST only be called after the process has been verified (is_boar_pid) and still owns the server port."""
    import subprocess
    import time

    if sys.platform == "win32":
        # Kill the whole process tree (server was spawned detached)
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True, text=True,
        )
        return

    import os
    import signal

    def _alive() -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    os.kill(pid, signal.SIGTERM)
    # A hung server may ignore SIGTERM; escalate to SIGKILL so `stop`
    # always terminates instead of silently leaving the process up.
    deadline = time.time() + 3.0
    while time.time() < deadline and _alive():
        time.sleep(0.2)
    if _alive():
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def cmd_stop() -> None:
    """Stop the running server (``main.py stop``)."""
    project_root = _get_project_root()

    from boarai.core.process import get_process_manager
    process_manager = get_process_manager(project_root)

    port = process_manager.server_port()
    pid = process_manager.get_running_pid()
    if pid is None:
        if not process_manager.is_port_available(port):
            other = process_manager.find_pid_on_port(port)
            logger.info(f"Port {port} is held by another program (PID: {other}); not a BoarAi server, nothing to stop")
        else:
            logger.info("BoarAi server is not running")
        return

    async def send_stop():
        import aiohttp

        base_url = _server_base_url()
        try:
            async with aiohttp.ClientSession() as session:
                headers = await _api_auth_headers(session, base_url)
                async with session.post(
                    f"{base_url}/api/server/stop",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as response:
                    result = await response.json()
                    if result.get("status") == "ok":
                        logger.info("Server stopping...")
                    else:
                        logger.error(f"Error: {result.get('message', 'Unknown error')}")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - ANY failure (transport, auth,
            # token RuntimeError) takes the verified-force-kill path instead
            # of leaking a traceback.
            # Graceful stop unreachable. Re-verify before force-killing so a
            # PID that got recycled onto an unrelated process is never shot.
            if process_manager.is_port_owned_by_pid(pid, port) and process_manager.is_boar_pid(pid):
                logger.warning("Cannot connect to server, terminating verified process...")
                _force_terminate(pid)
            else:
                logger.error(
                    "Safety check failed: PID no longer owns the port or is "
                    "no longer a BoarAi process; refusing to kill"
                )
                sys.exit(1)

    asyncio.run(send_stop())


def cmd_restart() -> None:
    """Restart the running server: stop then re-exec start."""
    import time

    project_root = _get_project_root()
    from boarai.core.process import get_process_manager
    process_manager = get_process_manager(project_root)

    if not process_manager.is_running() and process_manager.find_pid_on_port(
        process_manager.server_port()
    ) is None:
        logger.info("Server not running — starting fresh")
        _start_server_process(project_root)
        return

    logger.info("Stopping server for restart...")
    cmd_stop()

    # Wait until port/PID are free (up to ~15s)
    port = process_manager.server_port()
    deadline = time.time() + 15
    while time.time() < deadline:
        still = process_manager.is_running() or not process_manager.is_port_available(port)
        if not still:
            break
        time.sleep(0.4)

    if process_manager.is_running() or not process_manager.is_port_available(port):
        logger.error("Server did not stop in time; aborting restart")
        sys.exit(1)

    logger.info("Starting server...")
    _start_server_process(project_root)


def _start_server_process(project_root) -> None:
    """Spawn the server (``main.py start`` / ``-m boarai start``) detached."""
    import subprocess

    project_root = Path(project_root)
    # Channel-aware: pip installs launch via the package module, source
    # trees via main.py (single implementation shared with the in-server
    # restart handler).
    from boarai.core.update.steward import server_launch_prefix
    cmd = list(server_launch_prefix())
    from boarai.core.runtime import data_dir_spawn_args
    cmd.extend(data_dir_spawn_args(project_root))
    cmd.append("start")
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(cmd, cwd=str(project_root), creationflags=flags, close_fds=True)
    else:
        subprocess.Popen(cmd, cwd=str(project_root), start_new_session=True, close_fds=True)
    print("BoarAi server restart initiated")
