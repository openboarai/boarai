"""
First-run bootstrapper — seeds .boarai/ from defaults/ templates.

Uses .boarai/config/config.yaml with a `bootstrap.initialized` field
to track whether first-run setup has completed. Once set, defaults
are NEVER copied again, even if files were manually deleted.

Architecture:
    boar/defaults/*             → shipped with the project, editable
    .boarai/*                   → user runtime data, never overwritten
    .boarai/config/config.yaml  → settings config, stores bootstrap state
    copy_defaults()             → called once at startup from main.py
"""

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_CONFIG_DIR = "config"
_CONFIG_FILE = "config.yaml"

# Core dependencies that must be installed before the server modules import.
# Kept here (not in main.py) so the entry module has no install side effect at
# import time (C7-11d): ``python main.py --ensure-deps`` is the explicit
# installer, and a missing dependency otherwise fails with a printed remedy.
CORE_DEPENDENCIES = [
    "aiohttp>=3.8.0",  # HTTP/WebSocket server
    "pyyaml>=6.0",  # YAML configuration support
    "aiofiles>=23.0.0",  # Async file operations
]

# Map pip package names to their Python import names (when they differ)
_PACKAGE_IMPORT_NAMES = {
    "pyyaml": "yaml",
    "pillow": "PIL",
    "beautifulsoup4": "bs4",
    "scikit-learn": "sklearn",
    "opencv-python": "cv2",
}


def is_in_virtualenv() -> bool:
    """Whether the interpreter runs inside a virtual environment."""
    return hasattr(sys, "real_prefix") or (
        hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
    )


def _get_import_name(pkg_name: str) -> str:
    """Get the Python import name of a pip package."""
    return _PACKAGE_IMPORT_NAMES.get(pkg_name, pkg_name.replace("-", "_"))


def _dep_pkg_name(dep: str) -> str:
    return dep.split(">=")[0].split("<=")[0].split("==")[0].split("!=")[0].strip()


def missing_core_dependencies() -> list:
    """CORE_DEPENDENCIES entries whose import name cannot be imported now."""
    missing = []
    for dep in CORE_DEPENDENCIES:
        try:
            __import__(_get_import_name(_dep_pkg_name(dep)))
        except ImportError:
            missing.append(dep)
    return missing


def ensure_core_dependencies() -> None:
    """Install missing core dependencies via pip (explicit entry point only)."""
    in_venv = is_in_virtualenv()
    for dep in CORE_DEPENDENCIES:
        import_name = _get_import_name(_dep_pkg_name(dep))
        try:
            __import__(import_name)
        except ImportError:
            print(f"Installing {dep}...")
            install_args = [sys.executable, "-m", "pip", "install"]
            if in_venv:
                cmd = install_args + [dep]
            else:
                cmd = install_args + ["--user", dep]
            try:
                subprocess.check_call(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except subprocess.CalledProcessError as e:
                print(f"Failed to install {dep}: {e}")
                print(f"Please install it manually: pip install {dep}")
                sys.exit(1)


def _get_config_path(project_root: Path) -> Path:
    """Config path inside the data dir."""
    from .data_paths import resolve_data_dir
    return resolve_data_dir(project_root) / _CONFIG_DIR / _CONFIG_FILE


def _read_config(project_root: Path) -> Dict[str, Any]:
    """Read .boarai/config/config.yaml, return dict."""
    path = _get_config_path(project_root)
    if not path.exists():
        return {}
    try:
        import yaml
        data = yaml.safe_load(path.read_text("utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("Failed to parse config.yaml during bootstrap: %s", e)
        return {}


def _write_config(project_root: Path, data: Dict[str, Any]) -> None:
    """Merge-write .boarai/config/config.yaml (never drop unrelated keys)."""
    import yaml
    path = _get_config_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: Dict[str, Any] = {}
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text("utf-8")) or {}
            if isinstance(raw, dict):
                existing = raw
        except Exception:
            # Corrupt file (e.g. legacy !!python/object tags): rewrite from data only
            existing = {}
    merged = {**existing, **data}
    path.write_text(
        yaml.dump(merged, default_flow_style=False, allow_unicode=True, sort_keys=False),
        "utf-8",
    )


def is_initialized(project_root: Path) -> bool:
    """Check if first-run setup has already completed."""
    cfg = _read_config(project_root)
    if cfg.get("bootstrap", {}).get("initialized", False):
        return True
    from .data_paths import resolve_data_dir
    data_dir = resolve_data_dir(project_root)
    # Existing runtime tree ⇒ treat as already bootstrapped
    for name in ("sessions", "agents", "memory", "logs"):
        if (data_dir / name).exists():
            return True
    # Non-empty config with more than bootstrap is also initialized
    if cfg and set(cfg.keys()) - {"bootstrap"}:
        return True
    return False


def mark_initialized(project_root: Path) -> None:
    """Mark first-run setup as complete in config (merge-write)."""
    _write_config(project_root, {"bootstrap": {"initialized": True}})
    logger.info("Bootstrap marked as initialized in config")


def copy_defaults(project_root: Path) -> None:
    """Copy missing defaults from boar/defaults/ into the data dir, once only."""
    defaults_dir = project_root / "boar" / "defaults"
    from .data_paths import resolve_data_dir
    data_dir = resolve_data_dir(project_root)

    if is_initialized(project_root):
        logger.debug("Bootstrap already initialized, skipping")
        # is_initialized() also answers True by SNIFFING the runtime tree
        # (sessions/agents/memory/logs), so a data dir created before the
        # marker existed — or one seeded by an older build — reaches here
        # without config.yaml ever having been written. Create it now, but
        # ONLY when the file is genuinely absent: a config that merely failed
        # to parse must never be rewritten blindly, that would drop settings.
        if not _get_config_path(project_root).exists():
            logger.info("config.yaml missing in an existing data dir; creating it")
            mark_initialized(project_root)
        return

    if not defaults_dir.exists():
        logger.warning("boar/defaults/ not found at %s, skipping bootstrap", defaults_dir)
        return

    _copy_tree(defaults_dir, data_dir)
    mark_initialized(project_root)
    logger.info("Bootstrap complete: boar/defaults/ -> .boarai/")
    _migrate_agent_files(data_dir / "agents")


def _copy_tree(src: Path, dst: Path) -> None:
    """Recursively copy files from src to dst (never overwrites)."""
    for item in src.rglob("*"):
        if not item.is_file():
            continue
        rel = item.relative_to(src)
        target = dst / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        logger.debug("Seeded: %s", rel)


def _migrate_agent_files(agents_dir: Path) -> None:
    """Convert old agent.md files to soul.md + agent.yaml on first boot."""
    if not agents_dir or not agents_dir.exists():
        return
    try:
        import yaml  # noqa: F401
    except ImportError:
        return

    for agent_dir in agents_dir.iterdir():
        if not agent_dir.is_dir():
            continue
        old_md = agent_dir / "agent.md"
        new_yaml = agent_dir / "agent.yaml"
        if old_md.exists() and not new_yaml.exists():
            try:
                _convert_agent_md(old_md, agent_dir)
                logger.info("Migrated: %s", agent_dir.name)
            except Exception as e:
                logger.warning("Migration failed for %s: %s", agent_dir.name, e)


def _convert_agent_md(old_md: Path, agent_dir: Path) -> None:
    """Convert a single agent.md to soul.md + agent.yaml."""
    import yaml

    raw = old_md.read_text("utf-8")
    lines = raw.splitlines()
    fm_start = fm_end = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s == "---":
            if fm_start is None:
                fm_start = i
            elif fm_end is None:
                fm_end = i
                break

    if fm_start is None or fm_end is None:
        return

    fm_text = "\n".join(lines[fm_start + 1 : fm_end])
    data = yaml.safe_load(fm_text) or {}

    identity = data.pop("zone1_identity", "") or ""
    rules = data.pop("zone1_rules", "") or ""

    soul_parts = []
    if identity:
        soul_parts.append("## Identity\n")
        soul_parts.append(identity.strip())
    if rules:
        soul_parts.append("")
        soul_parts.append("## Behavior Rules\n")
        soul_parts.append(rules.strip())
    if soul_parts:
        (agent_dir / "soul.md").write_text("\n".join(soul_parts) + "\n", "utf-8")
    else:
        (agent_dir / "soul.md").write_text("", "utf-8")

    yaml_str = yaml.dump(
        data,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=80,
    )
    (agent_dir / "agent.yaml").write_text(yaml_str.strip() + "\n", "utf-8")
    old_md.unlink()