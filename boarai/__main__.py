"""Console entry for BoarAi: ``python -m boarai`` / the ``boarai`` script.

The source tree's ``main.py`` is a thin wrapper around :func:`main` so a
pip install and a checkout share ONE process-management implementation.

Under a pip install there is no ``main.py`` on disk: the project root
(the resource anchor for ``boar/prompts``, ``boar/defaults``,
``boar/extensions``) is the parent of the ``boarai`` package — i.e.
site-packages — and the data dir stays ``~/.boarai``
(boarai.core.runtime anchor).

``main`` is process management, NOT a command system: there is no injected
shell command and no terminal command registry. The in-session (chat)
command domain lives in core/command/ and is intercepted at the message
surfaces before any LLM run.

Subcommands:
    start (default)   Serve in the foreground
    stop              Stop the running server (graceful, verified kill fallback)
    restart           Stop then re-spawn detached
    status            Report whether the server process is up
    key               WebUI access key (init|show|rotate|revoke)

Extension management has no backend-command surface: it lives in the
WebUI / the ``/api/extensions`` REST endpoints (including static
verification via ``GET /api/extensions/verify``).
"""

import argparse
import os
import sys
from pathlib import Path


def _project_root() -> Path:
    """Resource anchor: repo root (source tree) or site-packages (pip)."""
    return Path(__file__).resolve().parent.parent


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="BoarAi - AI Agent Framework",
        prog="boarai",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    subparsers.add_parser("start", help="Start the BoarAi server (default)")
    subparsers.add_parser("stop", help="Stop the running server")
    subparsers.add_parser("restart", help="Restart the server")
    subparsers.add_parser("status", help="Show server status")

    # WebUI access key. The key itself is never recoverable (only its scrypt
    # hash is stored), so ``rotate`` is the only way to obtain a new one.
    key_parser = subparsers.add_parser(
        "key", help="Manage the WebUI access key (init|show|rotate|revoke)"
    )
    key_parser.add_argument(
        "action",
        nargs="?",
        choices=["init", "show", "rotate", "revoke"],
        default="show",
        help="init = create if missing, show = report state, "
             "rotate = new key (all devices dropped), revoke = drop sessions only",
    )

    # Data directory (overrides BOARAI_DATA_DIR env var and the default)
    parser.add_argument(
        "--data-dir",
        help="Custom data directory (default: <user home>/.boarai). "
             "Overrides the BOARAI_DATA_DIR environment variable.",
    )

    from boarai.core import __version__
    parser.add_argument("--version", action="version", version=f"BoarAi {__version__}")

    return parser.parse_known_args()


def _run_key_command(args) -> int:
    """``boarai key`` — WebUI access key lifecycle."""
    from boarai.core.security.access import get_access_store

    store = get_access_store()
    action = getattr(args, "action", "show") or "show"

    try:
        if action == "init":
            # Explicit opt-in generation for headless deployments. This is the
            # only path that auto-generates a key; the browser setup page is the
            # normal first-run flow.
            try:
                key = store.generate_and_store()
                print(f"Access key (shown once): {key}")
            except RuntimeError as exc:
                print(f"Access key already configured: {exc}")
            return 0

        if action == "rotate":
            print(f"New access key (shown once): {store.rotate()}")
            print("Every signed-in device was dropped; unlock again with the new key.")
            return 0

        if action == "revoke":
            count = store.revoke_all_sessions()
            print(f"Revoked {count} device session(s); the access key is unchanged.")
            return 0

        print(f"Access key: {'configured' if store.configured else 'not configured'}")
        print(f"  source: {store.source}")
        print(f"  generation: {store.generation}")
        print(f"  devices: {len(store.list_sessions())} active session(s)")
        print(
            "  note: only the hash is stored, so the key cannot be displayed; "
            "run 'boarai key rotate' for a new one."
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Key command failed: {exc}", file=sys.stderr)
        return 1


def main() -> None:
    """Main entry point - parse and dispatch process management."""
    project_root = _project_root()
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    # Core dependency installation lives in core.runtime.bootstrap (C7-11d):
    # importing this entry module must never reach the network. Explicit
    # entry:  python main.py --ensure-deps  /  python -m boarai --ensure-deps
    if "--ensure-deps" in sys.argv[1:]:
        from boarai.core.runtime.bootstrap import ensure_core_dependencies

        ensure_core_dependencies()
        sys.exit(0)

    args, unknown = parse_arguments()

    try:
        from boarai.core.command.dispatcher import cmd_restart, cmd_status, cmd_stop
    except ImportError as _missing:  # environment guard, not a runtime path
        _name = getattr(_missing, "name", None) or _missing
        print(f"BoarAi is missing a core dependency: {_name}", file=sys.stderr)
        print(
            'Install with: python -m pip install "aiohttp>=3.8.0" "pyyaml>=6.0" '
            '"aiofiles>=23.0.0"',
            file=sys.stderr,
        )
        print("Or run: python main.py --ensure-deps", file=sys.stderr)
        sys.exit(1)

    # Resolve the data-dir anchor FIRST so every subsystem (logging, PID
    # detection, config, sessions…) lands in the same place. The anchor
    # lives OUTSIDE the source tree: --data-dir > BOARAI_DATA_DIR >
    # ~/.boarai. An old in-tree .boarai is NOT auto-migrated — move it
    # manually or point BOARAI_DATA_DIR at it.
    from boarai.core.runtime import set_data_dir, set_project_root
    set_project_root(project_root)
    set_data_dir(args.data_dir if args.data_dir else os.environ.get("BOARAI_DATA_DIR"))

    # Seed defaults BEFORE creating the skeleton: is_initialized() treats
    # existing sessions/agents/memory/logs dirs as "already bootstrapped",
    # so create_directories first would make a fresh install skip seeding
    # entirely (empty skeleton == false positive).
    from boarai.core.runtime import copy_defaults
    copy_defaults(project_root)

    from boarai.core.command import create_directories
    create_directories(project_root)

    command = args.command or "start"
    if unknown and command == "start":
        print(f"Unknown arguments: {' '.join(unknown)}", file=sys.stderr)
        sys.exit(2)

    if command == "key":
        sys.exit(_run_key_command(args))

    if command == "stop":
        cmd_stop()
        return
    if command == "restart":
        cmd_restart()
        return
    if command == "status":
        sys.exit(cmd_status())

    # start (default): serve in the foreground.
    from boarai.core.runtime.serve import serve_foreground
    sys.exit(serve_foreground(project_root))


if __name__ == "__main__":
    main()
