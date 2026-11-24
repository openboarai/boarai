"""Completion marks for background artifacts (skills, task outputs, chunks).

A mark answers one question the filesystem cannot: *was this artifact finished,
by this configuration, and is it still the same bytes?* Presence of files is not
proof of completion — a killed process leaves a half-written skill or workbook
behind, and a later run must not adopt it as a result.

Marks live under ``<data_dir>/state/artifacts/<kind>/<key-hash>.json`` rather
than inside the artifact directory: a skill dir is scanned for
``references/scripts/assets`` resources, so a marker file there would either be
served as a resource or filtered out by name guesswork.

Writes are atomic (``tempfile`` + ``os.replace``) and platform-neutral; nothing
here uses ``fcntl`` or process groups.

Usage::

    mark = write_mark("skill", name, run_id=run_id, config_fp=cfgf,
                      output_fp=fingerprint_path(skill_dir))
    state = classify("skill", name, config_fp=cfgf,
                     output_fp=fingerprint_path(skill_dir), artifact=skill_dir)
    if state != "complete":
        logger.info("not adopting %s: %s", name, why_not(...))
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .data_paths import resolve_data_path

MARK_VERSION = 1

# Files/dirs that never belong to an artifact's identity. A log written next to
# a skill, or a __pycache__ produced by running its script, must not make the
# artifact look "changed".
_IGNORED_PARTS = frozenset({
    "__pycache__", ".git", ".pytest_cache", "logs", "temp", "node_modules",
})
_IGNORED_SUFFIXES = frozenset({".pyc", ".pyo", ".log", ".tmp"})
_IGNORED_NAMES = frozenset({".DS_Store"})

_KIND_RE = re.compile(r"[^a-z0-9_-]+")

STATES = ("complete", "died", "changed", "missing")


@dataclass(frozen=True)
class Mark:
    """One recorded completion."""

    version: int
    kind: str
    key: str
    run_id: str
    at: float
    config_fp: str = ""
    output_fp: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "kind": self.kind,
            "key": self.key,
            "run_id": self.run_id,
            "at": self.at,
            "config_fp": self.config_fp,
            "output_fp": self.output_fp,
            "extra": dict(self.extra or {}),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Mark":
        return cls(
            version=int(data.get("version") or 0),
            kind=str(data.get("kind") or ""),
            key=str(data.get("key") or ""),
            run_id=str(data.get("run_id") or ""),
            at=float(data.get("at") or 0.0),
            config_fp=str(data.get("config_fp") or ""),
            output_fp=data.get("output_fp"),
            extra=dict(data.get("extra") or {}),
        )


def _state_root() -> Path:
    return resolve_data_path("state/artifacts")


def _kind_token(kind: str) -> str:
    token = _KIND_RE.sub("_", str(kind or "").strip().lower()).strip("_")
    if not token:
        raise ValueError("artifact kind must not be empty")
    return token


def _mark_path(kind: str, key: str) -> Path:
    digest = hashlib.sha1(str(key).encode("utf-8", errors="replace")).hexdigest()[:16]
    return _state_root() / _kind_token(kind) / f"{digest}.json"


def _ignored(relative: Path) -> bool:
    """Whether a path, RELATIVE to the artifact root, is noise."""
    if relative.name in _IGNORED_NAMES:
        return True
    if relative.suffix.lower() in _IGNORED_SUFFIXES:
        return True
    return any(part in _IGNORED_PARTS for part in relative.parts)


def fingerprint_path(path: Path | str | None) -> str:
    """Digest an artifact's bytes; ``""`` when it does not exist."""
    if path is None:
        return ""
    root = Path(path)
    if root.is_file():
        try:
            return hashlib.sha1(root.read_bytes()).hexdigest()[:16]
        except OSError:
            return ""
    if not root.is_dir():
        return ""
    h = hashlib.sha1()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        try:
            rel_path = p.relative_to(root)
        except ValueError:
            continue
        if _ignored(rel_path):
            continue
        rel = rel_path.as_posix()
        h.update(rel.encode("utf-8", errors="replace"))
        h.update(b"\0")
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\0")
    return h.hexdigest()[:16]


def fingerprint_text(text: Optional[str]) -> str:
    """Digest in-memory artifact text (a task's answer, a rendered result)."""
    return hashlib.sha1((text or "").encode("utf-8", errors="replace")).hexdigest()[:16]


def config_fingerprint(
    *,
    model: str = "",
    agent_name: str = "",
    tool_names: Iterable[str] = (),
    extra: str = "",
) -> str:
    """Digest the configuration an artifact was produced under."""
    names = sorted({str(n) for n in tool_names if n})
    blob = "\0".join([
        f"v{MARK_VERSION}",
        str(model or ""),
        str(agent_name or ""),
        ",".join(names),
        str(extra or ""),
    ])
    return hashlib.sha1(blob.encode("utf-8", errors="replace")).hexdigest()[:16]


def write_mark(
    kind: str,
    key: str,
    *,
    run_id: str,
    config_fp: str = "",
    output_fp: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    """Record a completion atomically. Returns the mark path, or None on failure."""
    try:
        mark = Mark(
            version=MARK_VERSION,
            kind=_kind_token(kind),
            key=str(key),
            run_id=str(run_id or ""),
            at=time.time(),
            config_fp=str(config_fp or ""),
            output_fp=output_fp,
            extra=dict(extra or {}),
        )
        target = _mark_path(kind, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.stem}.", suffix=".tmp", dir=str(target.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(mark.to_dict(), fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, target)
        finally:
            if os.path.exists(tmp_name):
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
        return target
    except Exception:  # noqa: BLE001 — bookkeeping must not break the caller
        return None


def read_mark(kind: str, key: str) -> Optional[Mark]:
    """Read a mark; None when absent, unreadable, or not an object."""
    try:
        path = _mark_path(kind, key)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return Mark.from_dict(data)
    except (OSError, ValueError, TypeError):
        return None


def clear_mark(kind: str, key: str) -> bool:
    """Remove a mark (artifact superseded or deleted)."""
    try:
        _mark_path(kind, key).unlink(missing_ok=True)
        return True
    except OSError:
        return False


def classify(
    kind: str,
    key: str,
    *,
    config_fp: str = "",
    output_fp: Optional[str] = None,
    artifact: Path | str | None = None,
) -> str:
    """Classify an artifact against its mark: one of :data:`STATES`."""
    mark = read_mark(kind, key)
    if mark is None:
        return "missing"
    if mark.version != MARK_VERSION:
        return "changed"
    if config_fp and mark.config_fp and mark.config_fp != config_fp:
        return "changed"
    if output_fp is not None and mark.output_fp != output_fp:
        return "changed"
    if artifact is not None:
        root = Path(artifact)
        if root.is_file():
            if root.stat().st_size == 0:
                return "died"
        elif root.is_dir():
            if not any(p.is_file() for p in root.rglob("*")):
                return "died"
        else:
            return "died"
    return "complete"


def why_not(
    kind: str,
    key: str,
    *,
    config_fp: str = "",
    output_fp: Optional[str] = None,
    artifact: Path | str | None = None,
) -> str:
    """A one-sentence, human-readable reason for a non-complete state."""
    state = classify(
        kind, key, config_fp=config_fp, output_fp=output_fp, artifact=artifact
    )
    if state == "complete":
        return "complete"
    if state == "missing":
        return (
            "no completion mark — the producing run never finished, or finished "
            "before marks were recorded"
        )
    mark = read_mark(kind, key)
    if state == "died":
        return "completion mark exists but the artifact is missing or empty"
    if mark is not None and mark.version != MARK_VERSION:
        return f"mark version {mark.version} != {MARK_VERSION}"
    if mark is not None and config_fp and mark.config_fp != config_fp:
        return "produced by a different configuration (model/agent/tools changed)"
    if mark is not None and output_fp is not None and mark.output_fp != output_fp:
        return "artifact bytes changed after the mark was written"
    return state


__all__ = [
    "MARK_VERSION",
    "STATES",
    "Mark",
    "config_fingerprint",
    "fingerprint_path",
    "fingerprint_text",
    "write_mark",
    "read_mark",
    "clear_mark",
    "classify",
    "why_not",
]
