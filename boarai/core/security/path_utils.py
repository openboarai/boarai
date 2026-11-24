"""Shared path-containment utilities for the L2 security fence.

These helpers implement "canonicalize-then-contain" and correct absolute-path
token extraction. They are defense-in-depth (fail-open), NOT a security
boundary: shell tools run under the same OS user as the agent, so any
path/command check only prevents accidents, not a determined payload.

The single correctness invariant these functions must uphold:

    A *relative* path fragment (e.g. the ``/temp/x.py`` inside
    ``.boarai/temp/x.py``) must NEVER be parsed as an absolute path.

The previous regex-based extractor broke this invariant by matching any
``/...`` token regardless of what preceded it, which falsely blocked commands
like ``python .boarai/temp/bili_hot.py`` as "path outside project root".
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# Absolute path tokens: quoted, UNC, drive-letter, and genuinely-absolute
# POSIX paths (a leading "/" preceded by whitespace, "=", or start-of-string).
# A "/" directly after a word character is the tail of a relative path
# (".boarai/temp/x.py") and is deliberately excluded.
_ABS_PATH_TOKEN = re.compile(
    r'(?:"([^"]+)"'
    r"|'([^']+)'"
    r"|(\\\\[^\s|;]+)"
    r"|([A-Za-z]:[\\/](?!/)[^\s|;]*)"
    r"|((?:(?<=[\s=])|^)/[^\s|;]+))"
)


@dataclass(frozen=True)
class SandboxRoots:
    """Writable/readable roots resolved once per tool call."""

    workspace: Optional[Path]
    temp_dir: Optional[Path]
    project_root: Optional[Path]

    def writable(self) -> Tuple[Path, ...]:
        roots: List[Path] = []
        if self.workspace is not None:
            roots.append(self.workspace)
        if self.temp_dir is not None:
            roots.append(self.temp_dir)
        return tuple(roots)

    def readable(self) -> Tuple[Path, ...]:
        roots = list(self.writable())
        if self.project_root is not None:
            roots.append(self.project_root)
        return tuple(roots)


def _resolve(p: Optional[Path]) -> Optional[Path]:
    if p is None:
        return None
    try:
        return Path(p).expanduser().resolve()
    except (OSError, ValueError, TypeError):
        return None


def resolve_sandbox_roots(
    workspace=None,
    project_root=None,
    temp_dir=None,
) -> SandboxRoots:
    """Resolve the three roots from raw values (str / Path / None)."""
    pr = _resolve(project_root)
    td = None
    if temp_dir is not None:
        p = Path(temp_dir)
        if not p.is_absolute() and pr is not None:
            p = pr / p
        td = _resolve(p)
    return SandboxRoots(
        workspace=_resolve(workspace),
        temp_dir=td,
        project_root=pr,
    )


def get_temp_dir(project_root=None) -> Optional[Path]:
    """Resolve the config-backed temp dir (default ``.boarai/temp``)."""
    default = ".boarai/temp"
    raw = default
    try:
        from boarai.core.config import get_config_parser

        cp = get_config_parser()
        cfg = getattr(cp, "_config", None)
        if cfg is not None:
            sys_cfg = getattr(cfg, "system", None)
            raw = getattr(sys_cfg, "temp_dir", None) or default
    except Exception:
        raw = default
    p = Path(raw)
    if not p.is_absolute():
        # Relative values (including the default ".boarai/temp") resolve
        # against the DATA-DIR anchor, not the source tree — a tree-relative
        # join would recreate ".boarai" inside the repo after the home-anchor
        # unification and point the sandbox at a directory nothing uses.
        try:
            from boarai.core.runtime import resolve_data_path
            return _resolve(resolve_data_path(raw))
        except Exception:
            pr = _resolve(project_root)
            if pr is not None:
                p = pr / p
    return _resolve(p)


def is_within(target: Path, roots: Tuple[Path, ...]) -> bool:
    """canonicalize-then-contain: True if target resolves under any root."""
    resolved = _resolve(target)
    if resolved is None:
        return False
    for root in roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _is_cmd_switch(token: str) -> bool:
    """True for Windows cmd/PowerShell switches like /b /s /a-d (not paths)."""
    if not token.startswith("/") or token.startswith("//"):
        return False
    if token in ("/dev/null",) or token.startswith("/dev/"):
        return False
    body = token[1:]
    if not body:
        return True
    if "/" in body or "\\" in body:
        return False  # /usr/bin/foo or mixed — treat as path
    if os.name != "nt":
        return False  # POSIX: single-segment /x is a path, not a switch
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,8}", body))


def extract_absolute_path_tokens(command: str) -> List[str]:
    """Return absolute path tokens found in ``command`` (quoted/bare)."""
    tokens: List[str] = []
    for match in _ABS_PATH_TOKEN.finditer(command or ""):
        raw = next((g for g in match.groups() if g), "")
        if not raw:
            continue
        if raw in ("/dev/null", "nul", "NUL") or raw.startswith("/dev/"):
            continue
        if raw.startswith("-"):
            continue
        if _is_cmd_switch(raw):
            continue
        tokens.append(raw)
    return tokens


def token_outside_roots(raw: str, roots: Tuple[Path, ...]) -> bool:
    """True if an absolute path token resolves outside every root."""
    p = Path(raw)
    if not p.is_absolute():
        return False
    try:
        target = p.expanduser().resolve()
    except (OSError, ValueError, RuntimeError):
        return False
    return not is_within(target, roots)


def first_external_absolute_path(
    command: str, roots: Tuple[Path, ...]
) -> Optional[str]:
    """Return the first absolute path token that escapes every root, if any."""
    for raw in extract_absolute_path_tokens(command):
        if token_outside_roots(raw, roots):
            return raw
    return None
