"""
BoarAi Application Entry Point (source tree).

Thin wrapper: the real process-management entry lives in
``boarai/__main__.py`` so the source tree (``python main.py``), the pip
install (``boarai`` console script) and ``python -m boarai`` all share
ONE implementation. ``ProcessManager.is_boar_pid`` recognises running
servers by any of these entry shapes on their command line, and the
in-server restart handler re-execs the channel-appropriate one.

``--ensure-deps`` is handled BEFORE any other kernel import so a missing
dependency never reaches the network implicitly (C7-11d).
"""

import sys
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent.resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

if "--ensure-deps" in sys.argv[1:]:
    from boarai.core.runtime.bootstrap import ensure_core_dependencies

    ensure_core_dependencies()
    sys.exit(0)

try:
    # Re-exported for the frozen entry surface (tests pin main.main and
    # main.parse_arguments as importable names of this module).
    from boarai.__main__ import main, parse_arguments  # noqa: F401
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


if __name__ == "__main__":
    main()
