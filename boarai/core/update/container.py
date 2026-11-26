"""In-container pip-channel update: replace in place, then let the supervisor restart us.

The detached stop→install→start worker cannot work in a container: the moment
the server process exits, PID 1 (tini) exits too and the whole container —
worker included — goes down. This module therefore runs INSIDE the server
process:

1. ``pip install --upgrade boarai==<target>`` while the server keeps running
   (POSIX does not lock loaded module files; the service's runs_active guard
   already keeps working agents out of the replacement window);
2. verify the target version in a FRESH interpreter;
3. mark the state ``restarting`` and exit — the container restart policy
   (``restart: unless-stopped`` in every shipped compose file) brings the
   SAME writable layer back up, so the pip upgrade survives (a restart is
   not a rebuild);
4. the next process marks the state ``done`` (``finalize_restart``) as soon
   as it reports the target version.

A failure NEVER exits the process: pip uninstalls before installing, so a
failed install first tries to restore ``from_version`` and then reports
``failed`` with the server left running. The update holds the same OS file
lock the detached worker uses, so the service's worker_in_progress guard
serializes both shapes.
"""

from __future__ import annotations

import asyncio
import logging
import os

from . import state as update_state
from .version import current_version

logger = logging.getLogger(__name__)


async def run_pip_update(target: str, from_version: str, attempt_id: str, kind: str = "apply") -> None:
    """In-place pip update for containers; exits the process on success for the supervisor to restart."""
    from boarai.core.runtime import get_data_dir
    from boarai.core.update import worker as worker_ops
    from boarai.core.update.checker import get_update_config

    lock = update_state.FileLock(update_state.lock_path())
    if not lock.acquire():
        return  # the service guard already refused; belt and braces
    neutral_cwd = str(get_data_dir())

    def set_phase(phase: str, error: str | None = None, **extra) -> None:
        update_state.write_state({
            "attempt_id": attempt_id,
            "phase": phase,
            "kind": kind,
            "channel": "pip",
            "container": True,
            "from_version": from_version,
            "target_version": target,
            "worker_pid": os.getpid(),
            **({"error": error} if error else {}),
            **extra,
        })

    def finish_failed(error: str, **extra) -> None:
        logger.error("container update failed: %s", error)
        set_phase(update_state.PHASE_FAILED, error, **extra)
        update_state.write_receipt({
            "attempt_id": attempt_id, "kind": kind, "channel": "pip",
            "container": True, "outcome": update_state.PHASE_FAILED,
            "from_version": from_version, "target_version": target,
            "error": error, **extra,
        })

    try:
        cfg = get_update_config()
        extra_args = [str(a) for a in (getattr(cfg, "pip_extra_args", []) or [])]

        set_phase(update_state.PHASE_INSTALLING)
        rc, output = await asyncio.to_thread(
            worker_ops.run_pip_install, f"boarai=={target}", extra_args, neutral_cwd)
        if rc != 0:
            restored = False
            if await asyncio.to_thread(worker_ops.verify_install, neutral_cwd) != from_version:
                r_rc, _ = await asyncio.to_thread(
                    worker_ops.run_pip_install, f"boarai=={from_version}", extra_args, neutral_cwd)
                restored = r_rc == 0 and await asyncio.to_thread(
                    worker_ops.verify_install, neutral_cwd) == from_version
            else:
                restored = True  # old package still importable — install never tore it down
            finish_failed(
                f"pip install failed ({rc})"
                + ("" if restored else f"; REINSTALL of {from_version} also failed — environment needs attention"),
                pip_output=output[-2000:], restored=restored)
            return

        set_phase(update_state.PHASE_VERIFYING)
        installed = await asyncio.to_thread(worker_ops.verify_install, neutral_cwd)
        if installed != target:
            rb_rc, _ = await asyncio.to_thread(
                worker_ops.run_pip_install, f"boarai=={from_version}", extra_args, neutral_cwd)
            rolled_back = rb_rc == 0 and await asyncio.to_thread(
                worker_ops.verify_install, neutral_cwd) == from_version
            finish_failed(
                f"post-install verification failed (reported {installed!r}); "
                + ("rolled back to " + from_version if rolled_back else "ROLLBACK FAILED — environment needs attention"),
                rolled_back=rolled_back)
            return

        set_phase(update_state.PHASE_RESTARTING)
        logger.info("container update installed %s; exiting so the restart policy brings the new code up", target)
        await asyncio.sleep(2.0)  # let a last status poll observe `restarting`
        os._exit(0)  # the writable layer is already durable; no graceful teardown needed
    except Exception as e:  # noqa: BLE001 — a crashed update must not kill the server
        finish_failed(f"container update crashed: {e}")
    finally:
        lock.release()


def finalize_restart() -> None:
    """Mark a container update done when the restarted process reports the target version."""
    state = update_state.read_state()
    if not state or state.get("phase") != update_state.PHASE_RESTARTING or not state.get("container"):
        return
    target = str(state.get("target_version") or "")
    if not target or current_version() != target:
        return
    update_state.write_state({**state, "phase": update_state.PHASE_DONE})
    update_state.write_receipt({
        "attempt_id": state.get("attempt_id"), "kind": state.get("kind"),
        "channel": "pip", "container": True, "outcome": update_state.PHASE_DONE,
        "from_version": state.get("from_version"), "target_version": target,
    })
    logger.info("container update finalized: now running %s", target)
