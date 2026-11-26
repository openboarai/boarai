"""Server startup orchestration for BoarAI.

Moved out of ``main.py`` so the repo-root entry stays a thin shell.
``main.py`` is the single server PROCESS entry: the process-identity check
(``ProcessManager.is_boar_pid``) recognises servers by ``main.py`` on their
command line, and ``handle_server_restart`` re-execs ``main.py``.

``serve_foreground(project_root)`` is the whole start flow: already-running
detection (port + identity authority), interactive restart confirmation,
takeover kill, then initialization and the main loop in ONE event loop.
"""

import asyncio
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

# Module logger for the start flow. ``serve_foreground`` runs BEFORE
# initialize_system() configures the LogManager, so INFO here may not be
# emitted yet — which is why the failure paths below use warning/error: those
# reach stderr via logging's last-resort handler even with no configuration,
# and land in the log file once it exists. Previously they were bare prints
# and a fatal startup error never reached the log file at all.
logger = logging.getLogger(__name__)


async def initialize_system(project_root: Path):
    """Initialize the BoarAI system and start servers."""
    from boarai.core import get_event_bus, get_log_manager
    from boarai.core.config import ConfigParser, set_config_parser
    from boarai.core.extension import ExtensionManager, get_extension_manager
    from boarai.core.llm import get_llm_manager
    from boarai.core.runtime import get_data_dir, set_project_root
    from boarai.core.server import get_server_manager

    project_root = Path(project_root)
    data_dir = get_data_dir()

    # A container-channel update finalizes HERE: the previous process pip-
    # upgraded in place and exited; the restart policy brought us back on the
    # new code, so the status file can now flip to done.
    try:
        from boarai.core.update.container import finalize_restart

        finalize_restart()
    except Exception as e:  # noqa: BLE001 — a stale status file must not block startup
        logger.warning("container update finalize failed: %s", e)

    # Order matters: the bus takes its audit-trail path at construction and
    # ignores it on every later call, so it must be created with events.log
    # FIRST — otherwise the audit trail stays empty for the whole process.
    get_event_bus(data_dir / "logs" / "events.log")

    log_manager = get_log_manager(project_root)

    logger.info("Initializing BoarAi... (data dir: %s)", data_dir)

    try:
        # Initialize agent store (seeds default agents from core prompt templates)
        from boarai.core.prompt import get_agent_store
        agents_dir = data_dir / "agents"
        get_agent_store(agents_dir)

        # Initialize session manager with data dir
        from boarai.core.session import get_session_manager
        get_session_manager(project_root, data_dir)
        logger.info("Session manager initialized")

        # Upgrade any v3 single-JSON session transcripts to the v4
        # append-only journal. Idempotent, keeps the original as
        # {id}.json.v3.bak, and a single unreadable session is reported and
        # skipped rather than aborting startup.
        try:
            from boarai.core.session.migrate import migrate_sessions
            _mig = await migrate_sessions(data_dir / "sessions")
            if _mig.migrated or _mig.failed:
                logger.info("Session migration: %s", _mig.summary())
        except Exception as e:  # noqa: BLE001
            logger.warning("Session migration skipped: %s", e)

        # Migrate pre-ledger usage entries so historical token totals
        # survive deletion of old sessions (idempotent, ledger = marker).
        try:
            from boarai.core.session.usage_ledger import get_usage_ledger
            _sm = get_session_manager()
            await get_usage_ledger().migrate_legacy_entries(
                await _sm.legacy_usage_entries()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Usage ledger legacy migration skipped: %s", e)

        # Initialize trash/recycle bin FIRST (MemoryManager depends on it via DistillWorker)
        from boarai.core.trash import get_trash_manager
        tm = get_trash_manager(project_root, data_dir)
        await tm.start()

        # Initialize memory system (Synaptic Memory Mesh)
        from boarai.core.memory import get_memory_manager, get_memory_weaver
        mm = get_memory_manager(data_dir=data_dir, event_bus=get_event_bus())
        await mm.start()
        # Link weaver to manager
        mw = get_memory_weaver()
        mw.set_manager(mm)
        logger.info("Memory system (SMM) initialized")

        # Load configuration from config.yaml (persists user settings across restarts)
        config_path = data_dir / "config" / "config.yaml"
        config_parser = ConfigParser(config_path)
        config = await config_parser.load()

        # Register config_parser globally so components (API, run_manager) can access it
        set_config_parser(config_parser)

        if config and hasattr(config, 'logging'):
            log_manager.update_config(config.logging)

        # Retention runs once at setup; a server that stays up for weeks would
        # otherwise never sweep again. Started here (inside the loop) rather
        # than in the manager, which may be constructed without one.
        log_manager.start_maintenance()

        # Initialize security manager (PathGuard, ToolGuard, AuditLog).
        # Always initialize — uses defaults when no security config provided.
        # Fail-closed: a missing/empty security block still results in a
        # functional SecurityManager with safe defaults, rather than silent
        # bypass of all path/tool/shell guards.
        from boarai.core.security import get_security_manager
        sec_config = config.security if hasattr(config, 'security') else None
        get_security_manager().initialize(sec_config, project_root)

        # Select the shell execution backend. Configured before any tool can
        # run, and before the security manager is consulted, because the
        # guard's containment relaxation reads the active backend.
        try:
            from boarai.core.shell import configure_backend

            _sec_cfg = getattr(config, "security", None)
            # The schema field (and the YAML key) is ``shell_safety``. The
            # older ``shell`` spelling is accepted for hand-written configs;
            # reading only ``shell`` meant this whole block silently never ran
            # — every deployment was local, including the ones that asked for
            # a container.
            _shell_cfg = None
            if _sec_cfg is not None:
                _shell_cfg = getattr(_sec_cfg, "shell_safety", None) or getattr(
                    _sec_cfg, "shell", None
                )
            if _shell_cfg is not None:
                _backend = configure_backend(
                    getattr(_shell_cfg, "backend", "local"),
                    image=getattr(_shell_cfg, "docker_image", "python:3.12-slim"),
                    container=getattr(_shell_cfg, "docker_container", "") or None,
                    workdir=getattr(_shell_cfg, "docker_workdir", "/work"),
                    mounts=tuple(getattr(_shell_cfg, "docker_mounts", []) or ()),
                    network=bool(getattr(_shell_cfg, "docker_network", False)),
                )
                # Probe before any tool can ask for approval: an unverified
                # container must not be treated as a containment boundary.
                _probe = getattr(_backend, "probe", None)
                if _probe is not None:
                    if not await _probe():
                        logger.warning(
                            "Shell backend %r configured but unreachable; "
                            "commands will fail until it answers. Approval "
                            "relaxation stays disabled.",
                            _backend.name,
                        )
        except Exception as e:  # noqa: BLE001
            logger.warning("Shell backend configuration failed: %s", e)

        # Initialize fallback state machine for model/provider resilience
        from boarai.core.session.thinking import get_fallback_machine
        fallback_config = None
        if config and hasattr(config, 'thinking') and hasattr(config.thinking, 'fallback'):
            fb_data = config.thinking.__dict__.get('fallback', {})
            if fb_data:
                from boarai.core.session.thinking import FallbackConfig
                fallback_config = FallbackConfig(**{
                    k: v for k, v in fb_data.items()
                    if k in FallbackConfig.__dataclass_fields__
                })
        fm = get_fallback_machine()
        if fallback_config:
            fm._config = fallback_config
            logger.info("Fallback state machine initialized with config")
        else:
            logger.info("Fallback state machine initialized (default config)")

        # Load LLM configuration from user config file
        llm_manager = get_llm_manager()
        llm_config_path = data_dir / "config" / "llm.yaml"
        if llm_config_path.exists():
            try:
                await llm_manager.load_from_config(llm_config_path)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Failed to load LLM config from file: {e}")
        else:
            logger.info("No LLM config file found — configure a provider via WebUI")

        # No fallback — user must configure their own provider

        # Inject LLM service into MemoryManager (for Skill Forge & LLM distillation)
        from boarai.core.llm import get_llm_service
        get_memory_manager().set_llm_service(get_llm_service())
        logger.info("LLM service injected into MemoryManager")

        # Start the self-improvement curator (dedupes global lessons; it no
        # longer touches skills — see docs/self_improvement.md §5.3)
        from boarai.core.curator import get_curator
        get_curator().start()

        # Start the skill hot-refresh watcher (external edits to skill dirs)
        try:
            from boarai.core.extension.skill.watcher import get_skill_watcher
            watch_interval = getattr(getattr(config, "skills", None), "watch_interval", 5.0)
            await get_skill_watcher().start(interval=watch_interval)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Skill watcher start failed: {e}")

        # Initialize Run Manager before task manager (AI tasks depend on it)
        from boarai.core.session import get_run_manager
        run_manager = get_run_manager()
        await run_manager.start()

        # Initialize task manager (with session + run manager for AI task support)
        from boarai.core.task import get_task_manager
        task_manager = get_task_manager(
            project_root=project_root,
            session_manager=get_session_manager(),
            run_manager=run_manager,
        )
        await task_manager.start()

        # Start task scheduler (checks scheduled tasks every 30s)
        from boarai.core.task import get_task_scheduler
        scheduler = get_task_scheduler()
        await scheduler.start()

        # Forward task events to WebSocket clients for real-time UI updates
        from boarai.core.server import get_server_manager as _gsm
        await _setup_task_ws_forwarding(get_event_bus(), _gsm())

        # Register built-in tool extensions (self-register via @tool decorator)
        set_project_root(project_root)
        # Tool discovery delegated to ExtensionManager (via extension.yaml)
        logger.info("Tool discovery delegated to ExtensionManager")

        # Initialize extension manager
        extension_manager = get_extension_manager(project_root)
        await extension_manager.setup()
        logger.info("Extension manager initialized")

        # Discover and load all extensions
        loaded_extensions = await extension_manager.discover_and_load_all()
        logger.info(f"Loaded extensions: {loaded_extensions}")

        # Register subagent tool (core system primitive, not an extension)
        from boarai.core.session.thinking import (
            register_subagent_result_tool,
            register_subagent_tool,
        )
        register_subagent_tool()
        # A background delegation is useless without a way to read it back.
        register_subagent_result_tool()
        logger.info("SubAgent tool registered")

        # --- Built-in chat command (the command system is chat-only) ---
        # Server process management is NOT in the registry: main.py calls
        # the dispatcher functions directly, and the HTTP lifecycle
        # endpoints (/api/server/stop|restart) are implemented below.
        from boarai.core.command import get_command_registry
        command_registry = get_command_registry()

        async def chat_help_handler(args):
            """Built-in chat command: list the in-session command catalog."""
            lines = []
            for c in sorted(command_registry.list_commands(), key=lambda x: x.name):
                kind_tag = " [prompt]" if c.kind == "prompt" else ""
                alias_str = f" (aliases: {', '.join(c.aliases)})" if c.aliases else ""
                lines.append(f"/{c.name}{kind_tag} - {c.description}{alias_str}")
            if not lines:
                return "No chat commands registered."
            return "Available commands (they run here, without the LLM):\n" + "\n".join(lines)

        command_registry.register(
            "help", chat_help_handler, "List available chat commands",
            source="core", aliases=["commands"],
        )

        # Start unified server (HTTP + WebSocket on single port) AFTER all
        # extension routes are registered, so parameterized routes
        # (e.g. /static/{path:.*}) get native aiohttp routing instead of
        # the slower middleware fallback.
        server_manager = get_server_manager()
        logger.info(f"Starting to enable extensions. Total extensions: {len(extension_manager.list_extensions())}")
        for ext_name in extension_manager.list_extensions():
            logger.debug(f"Enabling extension: {ext_name}")
            try:
                result = await extension_manager.enable_extension(ext_name)
                logger.debug(f"Extension {ext_name} enabled: {result}")
            except Exception as e:  # noqa: BLE001
                logger.error(f"Failed to enable extension {ext_name}: {e}")
        logger.info("All extensions enabled")

        await server_manager.start()

        logger.info("BoarAi initialized successfully")

        return extension_manager

    except Exception as e:  # noqa: BLE001
        logger.critical(f"Failed to initialize BoarAi: {e}")
        raise


async def _setup_task_ws_forwarding(event_bus, server_manager) -> None:
    """Subscribe to task, session, and run events and forward them to WebSocket clients."""
    from boarai.core import get_logger
    from boarai.core.server.run_events import TERMINAL_RUN_EVENT_STATUS, forward_terminal_run_status

    TASK_EVENTS = [
        "task.created", "task.updated", "task.status_changed",
        "task.progress", "task.step_completed", "task.completed",
        "task.failed", "task.cancelled", "task.sub_created",
    ]
    SESSION_EVENTS = [
        "session.created", "session.updated", "session.deleted",
        "session.message.new",
    ]
    SKILL_EVENTS = [
        "skill.created", "skill.updated", "skill.archived",
    ]
    UPDATE_EVENTS = [
        "update.available", "update.progress", "update.done", "update.failed",
    ]
    # Access-gate revocation (key rotated / device revoked): every open socket
    # must be told to re-authenticate instead of serving a dead session.
    AUTH_EVENTS = [
        "auth.revoked",
    ]
    async def _forward(event_type, event):
        try:
            server = server_manager.server
            if server:
                await server.broadcast({
                    "type": event_type,
                    "payload": {
                        "event": event_type,
                        "data": event.payload if hasattr(event, 'payload') else event,
                    },
                })
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to forward %s to WebSocket clients: %s", event_type, e)

    for event_type in TASK_EVENTS:
        async def fwd_task(event, _et=event_type): await _forward(_et, event)
        await event_bus.subscribe(event_type, fwd_task)

    for event_type in SESSION_EVENTS:
        async def fwd_session(event, _et=event_type): await _forward(_et, event)
        await event_bus.subscribe(event_type, fwd_session)

    for event_type in SKILL_EVENTS:
        async def fwd_skill(event, _et=event_type): await _forward(_et, event)
        await event_bus.subscribe(event_type, fwd_skill)

    for event_type in UPDATE_EVENTS:
        async def fwd_update(event, _et=event_type): await _forward(_et, event)
        await event_bus.subscribe(event_type, fwd_update)

    for event_type in AUTH_EVENTS:
        async def fwd_auth(event, _et=event_type): await _forward(_et, event)
        await event_bus.subscribe(event_type, fwd_auth)

    # Global terminal status recovers clients that missed a session-scoped frame.
    for run_evt in TERMINAL_RUN_EVENT_STATUS:
        async def fwd_run(event, _evt=run_evt):
            try:
                await forward_terminal_run_status(server_manager, _evt, event)
            except Exception as error:  # noqa: BLE001
                logger.warning("Failed to forward %s to WebSocket clients: %s", _evt, error)
        await event_bus.subscribe(run_evt, fwd_run)


async def run_system(extension_manager) -> None:
    """Run the main BoarAI system loop."""
    from boarai.core import get_event_bus, get_log_manager
    from boarai.core.event import EventType
    from boarai.core.server import HOST, PORT

    event_bus = get_event_bus()

    shutdown_flag = asyncio.Event()

    async def on_shutdown(event):
        shutdown_flag.set()

    await event_bus.subscribe(EventType.SYSTEM_SHUTDOWN, on_shutdown)

    # Background update auto-check (core.update): first probe 5 min after
    # boot, then per update.check_interval_hours; publishes update.available.
    from boarai.core.update import start_auto_check
    start_auto_check()

    logger.info("BoarAi is running. Extensions will start via EventBus...")
    logger.info(f"Server: http://{HOST}:{PORT} (HTTP + WebSocket)")

    try:
        await shutdown_flag.wait()

    except KeyboardInterrupt:
        logger.info("Shutdown requested by user")
    except Exception as e:  # noqa: BLE001
        logger.critical(f"Unexpected error: {e}")
        raise
    finally:
        logger.info("Shutting down BoarAi...")
        try:
            from boarai.core.update import stop_auto_check
            await stop_auto_check()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Update auto-check stop failed: {e}")
        await extension_manager.disable_all_extensions()

        from boarai.core.server import get_server_manager
        server_manager = get_server_manager()
        await server_manager.stop()

        # Stop active runs / scheduled tasks before memory/trash
        try:
            from boarai.core.session import get_run_manager
            await get_run_manager().stop()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Run manager stop failed: {e}")

        try:
            from boarai.core.task import get_task_scheduler, get_task_manager
            await get_task_scheduler().stop()
            await get_task_manager().stop()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Task manager/scheduler stop failed: {e}")

        # Stop TrashManager
        from boarai.core.trash import get_trash_manager
        await get_trash_manager().stop()

        # Stop MemoryManager (forge consumer, distill worker, reap tasks)
        from boarai.core.memory import get_memory_manager
        await get_memory_manager().stop()

        # Stop the self-improvement curator
        try:
            from boarai.core.curator import get_curator
            await get_curator().stop()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Curator stop failed: {e}")

        # Stop the skill hot-refresh watcher
        try:
            from boarai.core.extension.skill.watcher import get_skill_watcher
            await get_skill_watcher().stop()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Skill watcher stop failed: {e}")

        logger.info("BoarAi shutdown complete")
        await get_log_manager().close()


async def _serve(project_root: Path) -> None:
    """Initialize system and run main loop in a single event loop."""
    extension_manager = await initialize_system(project_root)
    await run_system(extension_manager)


# =========================================================================
# HTTP lifecycle endpoints (POST /api/server/stop | /api/server/restart)
#
# These replace the old "stop"/"restart" command-registry handlers: the
# command system is chat-only now, and process lifecycle is a dedicated
# (auth-gated) API surface called by `main.py stop|restart`.
# =========================================================================


async def handle_server_stop() -> Dict[str, Any]:
    """Request a graceful shutdown (the main loop exits via SYSTEM_SHUTDOWN)."""
    from boarai.core import EventType, publish_event

    logger.info("Stop requested via HTTP lifecycle endpoint")
    await publish_event(EventType.SYSTEM_SHUTDOWN, {"source": "api"}, "server")
    return {"status": "ok", "message": "Server stopping..."}


async def handle_server_restart() -> Dict[str, Any]:
    """Spawn a detached re-exec of main.py, then request shutdown."""
    from boarai.core import EventType, publish_event
    from boarai.core.runtime import data_dir_spawn_args, get_project_root

    logger.info("Restart requested via HTTP lifecycle endpoint")

    root = Path(get_project_root() or Path.cwd())

    # Channel-aware launch prefix: a pip install has no main.py on disk and
    # re-execs through the package module (python -m boarai); a source tree
    # re-execs main.py. Resolved BEFORE the shutdown so a half-torn-down
    # runtime is never consulted.
    from boarai.core.update.steward import server_launch_prefix
    launch_prefix = server_launch_prefix()

    def _spawn_restart():
        import time
        time.sleep(1.5)  # allow current process to release the port
        cmd = list(launch_prefix)
        cmd.extend(data_dir_spawn_args(root))
        cmd.append("start")
        # Takeover flag: the spawned instance may find our own port still
        # occupied while we shut down — it must take over without an
        # interactive prompt.
        env = {**os.environ, "BOARAI_RESTART_TAKEOVER": "1"}
        try:
            if sys.platform == "win32":
                # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                flags = 0x00000008 | 0x00000200
                subprocess.Popen(
                    cmd, cwd=str(root), creationflags=flags,
                    close_fds=True, env=env,
                )
            else:
                subprocess.Popen(
                    cmd, cwd=str(root), start_new_session=True,
                    close_fds=True, env=env,
                )
        except Exception as spawn_err:  # noqa: BLE001
            # Best-effort log path — process may already be shutting down
            try:
                logger.error(f"Failed to spawn restart process: {spawn_err}")
            except Exception:  # noqa: BLE001
                pass

    # Detach spawn so HTTP can return before the process dies
    threading.Thread(target=_spawn_restart, daemon=True).start()
    await publish_event(
        EventType.SYSTEM_SHUTDOWN,
        {"source": "api", "reason": "restart"},
        "server",
    )
    return {"status": "ok", "message": "Server restarting..."}


def _kill_process(pid: int) -> bool:
    """Terminate a process by PID (cross-platform)."""
    if not pid:
        return False
    try:
        if sys.platform == "win32":
            # /T: kill the whole tree — the server owns tool subprocesses;
            # killing the parent alone would orphan them.
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to kill PID %s: %s", pid, e)
        return False


def _wait_port_free(process_manager, port: int, pid: int, timeout: float = 10.0) -> bool:
    """Wait until the port is released; escalate to SIGKILL on POSIX."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process_manager.is_port_available(port):
            return True
        time.sleep(0.2)
    if sys.platform != "win32":
        try:
            import signal
            os.kill(pid, signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if process_manager.is_port_available(port):
                return True
            time.sleep(0.2)
    return process_manager.is_port_available(port)


def serve_foreground(project_root: Path) -> int:
    """The full ``start`` flow: takeover detection, then serve in-process."""
    from boarai.core.process import get_process_manager

    project_root = Path(project_root)
    process_manager = get_process_manager(project_root)
    config = process_manager.get_server_config()
    port = config["port"]

    pid_hint = (
        process_manager.get_running_pid() if process_manager.is_running() else None
    )
    port_pid = (
        None
        if process_manager.is_port_available(port)
        else process_manager.find_pid_on_port(port)
    )

    if pid_hint or port_pid:
        shown = port_pid or pid_hint
        logger.warning(
            "BoarAi server is already running (PID: %s, port: %s)", shown, port
        )
        confirmed = False
        if os.environ.get("BOARAI_RESTART_TAKEOVER") and port_pid:
            # Detached self-restart (spawned by the restart handler with the
            # takeover flag): the port holder is our own dying instance.
            confirmed = True
        elif sys.stdin.isatty():
            try:
                confirmed = input("Restart it now? [y/N]: ").strip().lower() == "y"
            except (EOFError, KeyboardInterrupt):
                confirmed = False
        else:
            # Detached / piped launches must never block on a prompt
            logger.error(
                "Non-interactive terminal: aborting because an instance is "
                "already running."
            )
        if not confirmed:
            logger.warning("Aborted. The running server was left untouched.")
            return 0

        target = port_pid or pid_hint
        logger.warning("Stopping old server (PID %s)...", target)
        if not _kill_process(target):
            logger.error("Failed to kill process %s", target)
            return 1
        if not _wait_port_free(process_manager, port, target):
            logger.error(
                "Port %s is still in use after killing %s", port, target
            )
            return 1
        logger.info("Old server stopped. Starting fresh...")

    try:
        # Run initialization and main loop in the SAME event loop.
        # Using two separate asyncio.run() calls would bind the servers to
        # the first loop, which gets closed before run_system() starts a
        # new loop, leaving the HTTP/WS sockets dead while the process
        # keeps running.
        asyncio.run(_serve(project_root))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as e:  # noqa: BLE001
        # exc_info so the traceback reaches the log file, not just a one-liner.
        logger.error("Fatal error during startup: %s", e, exc_info=True)
        return 1
