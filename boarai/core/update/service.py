"""Server-side update orchestrator: status, manual check, apply, auto-check.

The server NEVER runs pip itself: ``apply`` spawns the detached worker
(``python -m boarai.core.update.worker``) and then mirrors the worker's status
file onto the event bus (``update.progress`` / ``update.done`` /
``update.failed``) until it reaches a terminal phase. Because the worker
asks THIS server to restart at the end, the poller publishes ``done`` via
the event bus right before the process is re-exec'd; a client that misses
it re-reads ``GET /api/update/status`` after reconnecting (the status
endpoint reports the terminal state from disk).

Guards (hermes-style refusals):
  - ``update.enabled`` config flag;
  - container environments: pip channel updates IN PLACE (core/update/
    container.py — install while running, then exit for the restart policy);
    git/source containers → 400 (rebuild the image instead);
  - per-channel precheck (git: binary/remote/dirty-tree; source: complete
    tree) → 400 with the refusal contract naming the fix;
  - a live worker (lock held) → 409, follow it instead of starting another;
  - active runs → 409 (never replace the package under a working agent).
  A finished attempt never blocks a retry — the worker lock is the only
  serialization.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time
import uuid
from typing import Any, Dict, Optional

from boarai.core import EventType

from . import state as update_state
from .checker import check as check_latest
from .checker import get_update_config
from .steward import (
    detect_install_kind,
    git_precheck,
    in_container,
    refusal_for,
    running_tree_root,
    source_precheck,
)
from .version import current_version, is_newer

logger = logging.getLogger(__name__)

# Poll cadence while mirroring a worker, and the hard stop for the mirror
# (the worker has its own internal deadlines; this only bounds the task).
_WATCH_INTERVAL_S = 1.0
_WATCH_DEADLINE_S = 40 * 60
# First auto-check delay after startup (the codex INITIAL_UPDATE_DELAY idea:
# never compete with boot), then every check_interval_hours.
_AUTO_CHECK_INITIAL_DELAY_S = 5 * 60


class UpdateError(Exception):
    """A refused update request; ``http_status`` maps straight to the API."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def _live_run_count() -> int:
    """Runs currently QUEUED/RUNNING (best effort; 0 on any introspection failure)."""
    try:
        from boarai.core.session import get_run_manager
        from boarai.core.session.run_models import RunStatus

        registry = get_run_manager()._registry
        live = (RunStatus.QUEUED, RunStatus.RUNNING)
        return sum(1 for run in registry._runs.values() if run.status in live)
    except Exception:  # noqa: BLE001
        return 0


def worker_in_progress() -> bool:
    """A live worker: lock held, or a non-terminal status whose lock probe failed."""
    if update_state.lock_is_held():
        return True
    state = update_state.read_state()
    return bool(state and state.get("phase") not in update_state.FINAL_PHASES
                and time.time() - float(state.get("at", 0)) < 120)


def _previous_version() -> Optional[str]:
    """Rollback anchor: the ``from_version`` of the newest receipt."""
    receipt = update_state.last_receipt()
    if receipt and receipt.get("from_version"):
        return str(receipt["from_version"])
    return None


async def get_status() -> Dict[str, Any]:
    """Full update picture for the API/WebUI (never raises)."""
    cfg = get_update_config()
    kind = detect_install_kind()
    cached = None
    try:
        cached = await check_latest(force=False)
    except Exception as e:  # noqa: BLE001
        cached = {"error": str(e)}
    return {
        "current_version": current_version(),
        "install_kind": kind,
        "can_update": refusal_for(kind) is None and bool(getattr(cfg, "enabled", True)),
        "refusal": refusal_for(kind),
        "config": {
            "enabled": bool(getattr(cfg, "enabled", True)),
            "auto_check": bool(getattr(cfg, "auto_check", True)),
            "check_interval_hours": float(getattr(cfg, "check_interval_hours", 24.0)),
        },
        "check": cached,
        "worker": update_state.read_state(),
        "worker_active": worker_in_progress(),
        "previous_version": _previous_version(),
    }


async def trigger_check(force: bool = True) -> Dict[str, Any]:
    """Manual/forced check; publishes ``update.available`` when a newer version shows."""
    result = await check_latest(force=force)
    if result.get("update_available"):
        await _publish(EventType.UPDATE_AVAILABLE, {
            "latest": result.get("latest"),
            "current": result.get("current"),
        })
    return result


async def trigger_apply(target: Optional[str] = None, rollback: bool = False) -> Dict[str, Any]:
    """Spawn the detached worker. Returns the attempt descriptor; raises UpdateError."""
    cfg = get_update_config()
    if not getattr(cfg, "enabled", True):
        raise UpdateError("updates_disabled", "Updates are disabled in config (update.enabled)", 403)

    kind = detect_install_kind()
    refusal = refusal_for(kind)
    # Prechecks spawn subprocesses (git remote/status) — NEVER run them on
    # the event loop: a hung git would freeze the whole server, not just
    # this request.
    if refusal is None and kind == "git":
        refusal = await asyncio.to_thread(git_precheck)
    elif refusal is None and kind == "source":
        refusal = await asyncio.to_thread(source_precheck)
    if refusal:
        raise UpdateError(refusal["code"], refusal["message"]
                          + (f" Run: {refusal['update_command']}" if refusal.get("update_command") else ""),
                          400)

    if worker_in_progress():
        raise UpdateError("worker_running", "An update worker is already running; follow its progress", 409)

    current = current_version()
    if rollback:
        target = target or _previous_version()
        if not target:
            raise UpdateError("no_rollback_target", "No previous version recorded to roll back to", 400)
        kind_label = "rollback"
    else:
        if not target:
            check = await check_latest(force=False)
            target = check.get("latest")
        if not target:
            raise UpdateError("no_target", "No target version available (check failed or up to date)", 400)
        if not is_newer(target, current):
            raise UpdateError("not_newer", f"Target {target} is not newer than current {current}", 400)
        kind_label = "apply"

    live = _live_run_count()
    if live:
        raise UpdateError(
            "runs_active",
            f"{live} run(s) are active — stop them before updating (the package is replaced under the server)",
            409,
        )

    attempt_id = uuid.uuid4().hex[:12]
    if kind == "pip" and in_container():
        # Containers: IN-PLACE pip update inside this process, then exit so
        # the restart policy brings the new code up. No detached worker (it
        # would die with the container), no stop phase, no watch task — the
        # UI polls the status file across the restart.
        from . import container as container_update

        asyncio.get_running_loop().create_task(
            container_update.run_pip_update(target, current, attempt_id, kind_label))
    else:
        _spawn_worker(target, current, attempt_id, kind_label)
        asyncio.get_running_loop().create_task(_watch_worker(attempt_id))
    return {
        "attempt_id": attempt_id,
        "kind": kind_label,
        "from_version": current,
        "target_version": target,
        "worker_log": str(update_state.worker_log_path()),
    }


def _spawn_worker(target: str, from_version: str, attempt_id: str, kind: str) -> None:
    """Detached worker spawn (house rules: CREATE_NO_WINDOW on Windows, start_new_session on POSIX, stdout appended to the worker log)."""
    cmd = [
        sys.executable, "-m", "boarai.core.update.worker",
        "--target", target,
        "--from-version", from_version,
        "--attempt-id", attempt_id,
        "--kind", kind,
    ]
    log_path = update_state.worker_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "ab")  # noqa: SIM115 - handed to the detached child
    log.write(f"\n===== attempt {attempt_id} ({kind} -> {target}) =====\n".encode("utf-8"))
    log.flush()
    # Neutral cwd (data dir): `python -m` puts cwd first on sys.path — the
    # server's cwd may be a source checkout whose `core/` would shadow the
    # INSTALLED package the worker is supposed to operate on.
    from boarai.core.runtime import get_data_dir
    neutral_cwd = str(get_data_dir())
    # git/source channels: the package is NOT in site-packages, so the
    # detached worker (neutral cwd) needs the tree explicitly on PYTHONPATH
    # to import the OLD code it will replace.
    env = None
    if detect_install_kind() != "pip":
        tree = running_tree_root()
        if tree:
            existing = os.environ.get("PYTHONPATH", "")
            env = {**os.environ,
                   "PYTHONPATH": str(tree) + (os.pathsep + existing if existing else "")}
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_PROCESS_GROUP | NO_WINDOW
        subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, creationflags=flags, close_fds=True, cwd=neutral_cwd, env=env)
    else:
        subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True, cwd=neutral_cwd, env=env)
    # The child inherited the fd; the parent must not keep it open (a fd
    # leak per apply in this long-lived server process).
    log.close()
    logger.info("Update worker spawned: attempt=%s target=%s kind=%s", attempt_id, target, kind)


async def _publish(event_type: str, payload: Dict[str, Any]) -> None:
    try:
        from boarai.core import publish_event

        await publish_event(event_type, payload, "update")
    except Exception as e:  # noqa: BLE001 - publishing must never break the flow
        logger.debug("publish %s failed: %s", event_type, e)


async def _watch_worker(attempt_id: str) -> None:
    """Mirror one worker's status file onto the event bus until terminal."""
    deadline = time.time() + _WATCH_DEADLINE_S
    last_phase: Optional[str] = None
    while time.time() < deadline:
        state = update_state.read_state()
        if state and state.get("attempt_id") == attempt_id:
            phase = state.get("phase")
            if phase != last_phase:
                last_phase = phase
                if phase in update_state.FINAL_PHASES:
                    event = (EventType.UPDATE_FAILED
                             if phase == update_state.PHASE_FAILED else EventType.UPDATE_DONE)
                    await _publish(event, dict(state))
                    return
                await _publish(EventType.UPDATE_PROGRESS, dict(state))
        await asyncio.sleep(_WATCH_INTERVAL_S)
    await _publish(EventType.UPDATE_FAILED, {
        "attempt_id": attempt_id, "phase": "failed",
        "error": f"worker mirror timed out after {_WATCH_DEADLINE_S}s",
    })


# ---------------------------------------------------------------------------
# Background auto-check
# ---------------------------------------------------------------------------

_auto_check_task: Optional[asyncio.Task] = None
_last_notified_version: Optional[str] = None


async def _auto_check_loop() -> None:
    global _last_notified_version
    await asyncio.sleep(_AUTO_CHECK_INITIAL_DELAY_S)
    while True:
        cfg = get_update_config()
        if getattr(cfg, "enabled", True) and getattr(cfg, "auto_check", True):
            try:
                # force=True: the cache TTL equals check_interval_hours, so a
                # cached read here would ALWAYS hit (age≈0 at each wake-up)
                # and the auto-check would never touch the network again.
                result = await check_latest(force=True)
                latest = result.get("latest")
                if result.get("update_available") and latest != _last_notified_version:
                    _last_notified_version = latest
                    await _publish(EventType.UPDATE_AVAILABLE, {
                        "latest": latest,
                        "current": result.get("current"),
                        "source": "auto_check",
                    })
            except Exception as e:  # noqa: BLE001
                logger.debug("auto update check failed: %s", e)
        interval = max(600.0, float(getattr(cfg, "check_interval_hours", 24.0)) * 3600.0)
        await asyncio.sleep(interval)


def start_auto_check() -> Optional[asyncio.Task]:
    """Start the background auto-check loop (idempotent). Called from run_system."""
    global _auto_check_task
    if _auto_check_task is None or _auto_check_task.done():
        _auto_check_task = asyncio.get_running_loop().create_task(_auto_check_loop())
    return _auto_check_task


async def stop_auto_check() -> None:
    global _auto_check_task
    task, _auto_check_task = _auto_check_task, None
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
