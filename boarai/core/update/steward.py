"""Install steward: which channel owns this install, and how it updates.

All three install kinds self-update IN PLACE through their own channel —
an update never crosses channels (a cross-channel "update" would leave a
second, diverged copy behind, the hermes hand-off lesson):

  - ``pip``    — the ``boarai`` package lives under this interpreter's
                 site-packages: the worker runs ``pip install -U boarai==x``;
  - ``git``    — a source checkout (``.git`` at the running tree's root):
                 the worker runs ``git fetch --tags`` + ``git checkout <tag>``
                 (dirty trees are REFUSED — an update must never eat
                 uncommitted work);
  - ``source`` — a source directory without git (zip download / extracted
                 sdist): the worker downloads the release archive (PyPI
                 sdist first, mirror/GitHub failover), verifies it, backs
                 the old tree up and swaps the archive in.

An editable install (``pip install -e``) resolves to ``git``/``source``:
its files live in the checkout, so the checkout's channel owns it.

The refusal contract survives for states that genuinely cannot update
(no git binary, no remote, dirty tree, incomplete source dir): each says
so loudly and names the command that fixes it — never a silent non-update.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

PACKAGE_NAME = "boarai"


def running_tree_root() -> Optional[Path]:
    """Root of the RUNNING code tree; None for pip (site-packages) installs."""
    import boarai.core as core

    try:
        path = Path(core.__file__).resolve()
        root = path.parents[2]
    except (AttributeError, IndexError, OSError, TypeError):
        return None
    if (root / "boarai" / "core").is_dir():
        return root
    return None


def detect_install_kind() -> str:
    """Return ``'pip' | 'git' | 'source'`` for the running install."""
    import boarai.core as core

    try:
        import sysconfig

        purelib = Path(sysconfig.get_paths()["purelib"]).resolve()
        core_path = Path(core.__file__).resolve()
        if purelib in core_path.parents:
            return "pip"
    except Exception:  # noqa: BLE001 - detection must never raise
        pass
    root = running_tree_root() or _project_root()
    if (root / ".git").exists():
        return "git"
    return "source"


def _project_root() -> Path:
    from boarai.core.runtime import get_project_root

    return Path(get_project_root() or Path.cwd())


def in_container() -> bool:
    """Whether this process runs inside a container (Docker/Podman style)."""
    if sys.platform == "win32":
        return False
    try:
        if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
            return True
    except OSError:
        pass
    return False


def refusal_for(kind: Optional[str] = None) -> Optional[dict]:
    """Static refusal for a kind, or None when the kind can self-update."""
    kind = kind or detect_install_kind()
    # Containers use the IN-PLACE pip channel (core/update/container.py):
    # no detached worker, no stop phase — pip replaces the package while the
    # server runs, then the process exits and the restart policy brings the
    # new code up. git/source containers keep the rebuild-only contract.
    if in_container() and kind != "pip":
        return {
            "code": "container-unsupported",
            "message": "In-container online updates support the pip channel only; "
                       "update a git/source container by rebuilding its image.",
            "update_command": "docker compose up -d --build",
        }
    if kind in ("pip", "git", "source"):
        return None
    return {
        "code": "unknown-channel",
        "message": f"Unknown install kind {kind!r} — cannot self-update.",
        "update_command": "",
    }


# ---------------------------------------------------------------------------
# git channel helpers (stdlib subprocess only; core must stay dependency-free)
# ---------------------------------------------------------------------------

def run_git(args: List[str], root: Path, timeout: float = 300.0) -> Tuple[int, str]:
    """``git -C <root> <args>`` → (returncode, combined output)."""
    import shutil

    git = shutil.which("git")
    if not git:
        return -1, "git is not available on PATH"
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW (house rule)
    try:
        proc = subprocess.run(
            [git, "-C", str(root), *args],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, **kwargs,
        )
    except subprocess.TimeoutExpired:
        return 1, f"git {' '.join(args)} timed out after {timeout}s"
    except OSError as e:
        return 1, f"git could not run: {e}"
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode, output


def git_head_commit(root: Path) -> Optional[str]:
    """Current HEAD commit hash (the restore anchor); None when unreadable."""
    rc, out = run_git(["rev-parse", "HEAD"], root)
    return out.strip() if rc == 0 and out.strip() else None


def git_tag_exists(root: Path, tag: str) -> bool:
    rc, _ = run_git(["rev-parse", "-q", "--verify", f"refs/tags/{tag}"], root)
    return rc == 0


def git_precheck(root: Optional[Path] = None, remote: str = "origin") -> Optional[dict]:
    """None when the git channel can update; refusal-shaped dict otherwise."""
    tree = Path(root or running_tree_root() or _project_root())
    rc, out = run_git(["--version"], tree)
    if rc != 0:
        return {
            "code": "git-missing",
            "message": "git is not available on PATH — install git to update this checkout.",
            "update_command": "",
        }
    rc, out = run_git(["remote"], tree)
    if rc != 0 or remote not in out.split():
        return {
            "code": "git-no-remote",
            "message": f"This checkout has no '{remote}' remote — the git update channel "
                       "needs a remote to fetch tags from.",
            "update_command": f"git -C \"{tree}\" remote add {remote} <url>",
        }
    rc, out = run_git(["status", "--porcelain", "--untracked-files=no"], tree)
    if rc != 0:
        return {
            "code": "git-state-unreadable",
            "message": f"git status failed: {out[-300:]}",
            "update_command": "",
        }
    if out.strip():
        return {
            "code": "git-dirty",
            "message": "The working tree has uncommitted changes — an update must never "
                       "eat your work. Commit or stash them first.",
            "update_command": f"git -C \"{tree}\" status",
        }
    return None


def source_precheck(root: Optional[Path] = None) -> Optional[dict]:
    """None when the source tree looks replaceable; refusal-shaped dict otherwise."""
    tree = Path(root or running_tree_root() or _project_root())
    if not (tree / "main.py").is_file() or not (tree / "boarai" / "core" / "__init__.py").is_file():
        return {
            "code": "source-incomplete",
            "message": f"{tree} does not look like a complete BoarAI source tree "
                       "(main.py / boarai/core missing) — replace it manually.",
            "update_command": "",
        }
    return None


# ---------------------------------------------------------------------------
# command builders
# ---------------------------------------------------------------------------

def pip_install_command(
    target: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
) -> List[str]:
    """The pip command that installs ``target`` (default: latest) into THIS env."""
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--upgrade", "--disable-pip-version-check",
    ]
    cmd.extend(str(a) for a in (extra_args or []))
    cmd.append(f"{PACKAGE_NAME}=={target}" if target else PACKAGE_NAME)
    return cmd


def server_launch_prefix() -> List[str]:
    """Command prefix that (re)starts the server for THIS install channel."""
    if detect_install_kind() == "pip":
        return [sys.executable, "-m", "boarai"]
    root = running_tree_root() or _project_root()
    return [sys.executable, str(root / "main.py")]
