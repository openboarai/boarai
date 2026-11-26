"""Detached update worker — ``python -m boarai.core.update.worker --target <ver>``.

Spawned by the server (``core.update.service``) as a DETACHED child that
OUTLIVES it (the opm principle: the worker stops the old server, installs,
verifies, and restarts — quitting the UI mid-download never strands a
half-run package manager).

Channels (``steward.detect_install_kind``, captured BEFORE any replacement):
  pip    — ``pip install --upgrade boarai==<target>`` into site-packages.
           Stop FIRST: on Windows pip cannot replace a package whose files a
           live server holds;
  git    — ``git fetch --tags`` + ``git checkout <tag>`` in the running tree.
           The fetch happens while the server still runs; only the checkout
           needs the stop. Rollback anchor = the pre-update HEAD commit;
  source — download the release archive (failover order: configured
           ``archive_mirrors`` → PyPI sdist → TUNA/Aliyun host swaps →
           GitHub tag archive → GitHub via proxy prefixes), sha256-verify
           when known, extract + pre-verify into staging while the server
           still runs, then stop → back the old tree up → swap → verify →
           restart. The old tree survives as ``<name>.bak-<attempt>``
           (retention: ``update.backup_keep``).

Hermetic rule (the hermes hand-off lesson): this interpreter runs OLD code.
Once the on-disk package has been replaced (pip / checkout / swap), NOTHING
new may be imported from ``core`` here — verification and restart run in
fresh subprocesses / over HTTP, and every path and config value this worker
needs is captured BEFORE the replacement step.

Flow (the ompweb self-update sequence): lock → checking → install media →
stopping → replace → verifying → restarting (spawn the server detached) →
poll /api/status until the NEW version answers → done | failed. ANY failure
after the stop re-spawns the server so the machine is never left down; a
failed checkout/swap restores the previous tree automatically. Every
terminal outcome writes a receipt; the status file mirrors each phase for
the UI (the phase NAME set stays the pip-channel one — no new UI states).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

# Pre-replacement imports ONLY (old code, safe). After the replacement step
# this module must not import anything from boarai.core.* again.
from boarai.core.process.manager import DEFAULT_PORT
from boarai.core.update import state as update_state

PIP_TIMEOUT_S = 30 * 60        # a full dependency resolve on a slow line
VERIFY_TIMEOUT_S = 120
RESTART_POLL_TIMEOUT_S = 90
RESTART_POLL_INTERVAL_S = 1.5
DOWNLOAD_SOCKET_TIMEOUT_S = 30.0   # per-socket-op; slow drips never trip it
GIT_FETCH_TIMEOUT_S = 600
MAX_ARCHIVE_BYTES = 300 * 1024 * 1024
NOWINDOW = 0x08000000 if sys.platform == "win32" else 0

_VERSION_RE = re.compile(r"^__version__\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)


class SourceUpdateError(Exception):
    """Source-channel preparation failed; the live server was NOT touched."""


def _popen_kwargs() -> dict:
    kwargs: dict = {}
    if sys.platform == "win32":
        # The server runs detached (no console) — every child must hide its
        # console window too (CREATE_NO_WINDOW; house rule).
        kwargs["creationflags"] = NOWINDOW
    return kwargs


def _server_port(data_dir: Path) -> int:
    """Port of the server that spawned us: env → server.json → default."""
    env_port = os.environ.get("BOARAI_PORT", "").strip()
    if env_port.isdigit():
        return int(env_port)
    try:
        cfg = json.loads((data_dir / "config" / "server.json").read_text(encoding="utf-8"))
        port = int(cfg.get("port", DEFAULT_PORT))
        if 0 < port < 65536:
            return port
    except (OSError, ValueError, TypeError):
        pass
    return DEFAULT_PORT


def _base_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _api_token(base_url: str, timeout: float = 5.0) -> Optional[str]:
    """Fetch the loopback bootstrap token (re-fetched per call: a restarted server mints a NEW random token)."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"{base_url}/api/auth/token", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        token = str(data.get("token", "")).strip()
        return token or None
    except Exception:  # noqa: BLE001
        return None


def run_pip_install(spec: str, extra_args: List[str], cwd: Optional[str] = None) -> Tuple[int, str]:
    """Run pip in THIS environment; returns (returncode, output tail)."""
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--upgrade", "--disable-pip-version-check",
        *extra_args, spec,
    ]
    print(f"[worker] pip: {' '.join(cmd)}", flush=True)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=PIP_TIMEOUT_S, cwd=cwd, **_popen_kwargs(),
        )
        tail = "\n".join(
            (proc.stdout or "").splitlines()[-15:]
            + (proc.stderr or "").splitlines()[-15:]
        )
        return proc.returncode, tail
    except subprocess.TimeoutExpired:
        return 124, f"pip install timed out after {PIP_TIMEOUT_S}s"
    except OSError as e:
        return 1, f"pip install could not run: {e}"


def verify_install(cwd: Optional[str] = None) -> Optional[str]:
    """Fresh interpreter: smoke-import the server stack, print the version."""
    code = (
        "import boarai.core, boarai.core.server.unified_server; "
        "print(boarai.core.__version__)"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=VERIFY_TIMEOUT_S, cwd=cwd, **_popen_kwargs(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        print(f"[worker] verify failed: {(proc.stderr or '')[-500:]}", flush=True)
        return None
    lines = [l.strip() for l in (proc.stdout or "").splitlines() if l.strip()]
    return lines[-1] if lines else None


def request_stop(base_url: str) -> Tuple[bool, str]:
    """Ask the running server to shut down gracefully; returns (ok, failure reason)."""
    import urllib.request

    token = _api_token(base_url)
    if not token:
        return False, f"could not fetch a loopback token from {base_url}/api/auth/token"
    request = urllib.request.Request(
        f"{base_url}/api/server/stop",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            if resp.status == 200:
                return True, ""
            return False, f"POST /api/server/stop returned HTTP {resp.status}"
    except Exception as e:  # noqa: BLE001
        return False, f"POST /api/server/stop failed: {e}"


def _stop_and_wait(base_url: str, port: int, stop_timeout_s: float) -> Optional[str]:
    """Graceful stop + port wait; None on success, a diagnostic string on failure."""
    ok, reason = request_stop(base_url)
    if not ok:
        print(f"[worker] stop request failed: {reason}", flush=True)
    if wait_port_free(port, timeout_s=stop_timeout_s):
        return None
    detail = f"server port still held after graceful stop; update aborted"
    detail += f" (stop request: {'accepted' if ok else reason})"
    detail += f"; waited {stop_timeout_s:.0f}s"
    return detail


def _stop_timeout_s(cfg) -> float:
    """How long to wait for the graceful stop to release the port (config)."""
    try:
        return max(30.0, float(getattr(cfg, "stop_timeout_s", 120.0)))
    except (TypeError, ValueError):
        return 120.0


def wait_port_free(port: int, timeout_s: float = 30.0) -> bool:
    """Wait until nothing listens on ``port`` (the graceful stop completed)."""
    import socket

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return True
        time.sleep(0.5)
    return False


def spawn_server_detached(cwd: Optional[str] = None, prefix: Optional[List[str]] = None) -> bool:
    """Start the server detached after the install (the worker owns the stop → install → start cycle; nobody else is left to start it)."""
    cmd = list(prefix) if prefix else [sys.executable, "-m", "boarai"]
    data_dir = os.environ.get("BOARAI_DATA_DIR", "").strip()
    if data_dir:
        cmd += ["--data-dir", data_dir]
    cmd.append("start")
    env = {**os.environ, "BOARAI_RESTART_TAKEOVER": "1"}
    try:
        if sys.platform == "win32":
            flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
            subprocess.Popen(cmd, creationflags=flags, close_fds=True, env=env, cwd=cwd)
        else:
            subprocess.Popen(cmd, start_new_session=True, close_fds=True, env=env, cwd=cwd)
        return True
    except OSError as e:
        print(f"[worker] detached spawn failed: {e}", flush=True)
        return False


def poll_new_version(base_url: str, expected: str) -> bool:
    """Poll /api/status until the restarted server reports ``expected``."""
    import urllib.request

    deadline = time.time() + RESTART_POLL_TIMEOUT_S
    while time.time() < deadline:
        token = _api_token(base_url, timeout=3.0)
        if token:
            try:
                request = urllib.request.Request(
                    f"{base_url}/api/status",
                    headers={"Authorization": f"Bearer {token}"},
                )
                with urllib.request.urlopen(request, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                if str(data.get("version", "")) == expected:
                    return True
            except Exception:  # noqa: BLE001 - server mid-restart
                pass
        time.sleep(RESTART_POLL_INTERVAL_S)
    return False


# ---------------------------------------------------------------------------
# source channel: candidate resolution, download, extraction, tree swap
# ---------------------------------------------------------------------------

def tag_for(cfg, version: str) -> str:
    """The git tag / GitHub archive tag for a version (default ``v0.1.1``)."""
    return f"{getattr(cfg, 'git_tag_prefix', 'v') or ''}{version}"


def build_source_candidates(cfg, target: str) -> List[Tuple[str, str]]:
    """(url, sha256|"") failover list for the source channel."""
    from boarai.core.update import checker as upd_checker

    tag = tag_for(cfg, target)
    out: List[Tuple[str, str]] = []
    for tpl in getattr(cfg, "archive_mirrors", None) or []:
        url = str(tpl).replace("{version}", target).replace("{tag}", tag).strip()
        if url:
            out.append((url, ""))

    base_url = str(getattr(cfg, "pypi_url", "") or "").strip()
    sdist: Optional[dict] = None
    if base_url:
        for url in (upd_checker.version_json_url(base_url, target), base_url):
            try:
                data = upd_checker.fetch_release_json(url)
                reported = str(((data.get("info") or {}).get("version") or "")).strip()
                if reported and reported != target:
                    continue  # latest-only endpoint at a different version
                sdist = upd_checker.sdist_from_json(data)
            except Exception:  # noqa: BLE001 - candidate resolution is best effort
                sdist = None
            if sdist:
                break
    if sdist:
        sha = str(sdist.get("sha256") or "")
        out.append((sdist["url"], sha))
        for mirror in upd_checker.mirror_file_urls(sdist["url"]):
            out.append((mirror, sha))

    repo = str(getattr(cfg, "github_repo", "") or "").strip().strip("/")
    if repo and "/" in repo:
        gh = f"https://github.com/{repo}/archive/refs/tags/{tag}.zip"
        out.append((gh, ""))
        for proxy in getattr(cfg, "github_proxies", None) or []:
            proxy = str(proxy).strip()
            if proxy:
                out.append((proxy.rstrip("/") + "/" + gh, ""))
    return out


def download_to_file(
    url: str, dest: Path,
    timeout_s: float = DOWNLOAD_SOCKET_TIMEOUT_S, total_s: float = 20 * 60,
) -> int:
    """Stream a URL to ``dest`` (size- and total-time-capped)."""
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "boarai-update"})
    total = 0
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout_s) as response, open(dest, "wb") as fh:
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError(f"archive exceeds the {MAX_ARCHIVE_BYTES}-byte cap")
            if time.monotonic() - started > total_s:
                raise TimeoutError(f"download exceeded the {total_s}s total deadline")
            fh.write(chunk)
    if total == 0:
        raise ValueError("archive download was empty")
    return total


def sha256_of(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(256 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_name(url: str) -> str:
    from urllib.parse import urlsplit

    return Path(urlsplit(url).path).name.strip() or "archive.download"


def _assert_tar_members_safe(tf, dest_dir: Path) -> None:
    """Reject tar members that escape ``dest_dir`` or use links."""
    base = dest_dir.resolve()
    for member in tf.getmembers():
        target = (dest_dir / member.name).resolve()
        if base != target and base not in target.parents:
            raise ValueError(f"unsafe tar member path: {member.name}")
        if member.islnk() or member.issym():
            raise ValueError(f"links are not allowed in update archives: {member.name}")


def extract_archive(archive: Path, dest_dir: Path) -> Path:
    """Extract a .tar.gz/.zip release archive; return the payload root."""
    import tarfile
    import zipfile

    name = archive.name.lower()
    if name.endswith((".tar.gz", ".tgz", ".tar")) or tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            try:
                tf.extractall(dest_dir, filter="data")
            except TypeError:  # Python < 3.12 has no filter kwarg
                # No filter= on this interpreter → enforce the equivalent
                # safety by hand: no path traversal, no links (a mirror
                # without sha256 is NOT grounds to trust its tar members).
                _assert_tar_members_safe(tf, dest_dir)
                tf.extractall(dest_dir)  # noqa: S202 - members validated above
    elif name.endswith(".zip") or zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest_dir)
    else:
        raise ValueError(f"{archive.name} is neither a tar nor a zip archive")

    tops = [p for p in dest_dir.iterdir() if p.is_dir()]
    root = tops[0] if len(tops) == 1 else dest_dir
    if not (root / "main.py").is_file() or not (root / "boarai" / "core" / "__init__.py").is_file():
        raise ValueError("archive payload is not a complete BoarAI source tree")
    return root


def sniff_version(root: Path) -> str:
    """Read ``__version__`` from a tree WITHOUT importing it (hermetic rule)."""
    try:
        text = (root / "boarai" / "core" / "__init__.py").read_text(encoding="utf-8")
    except OSError:
        return ""
    match = _VERSION_RE.search(text)
    return match.group(1) if match else ""


def prepare_source_update(cfg, target: str, stage_dir: Path) -> Tuple[Path, str]:
    """Download + verify + extract into staging WHILE the server still runs."""
    import shutil

    if stage_dir.exists():
        shutil.rmtree(stage_dir, ignore_errors=True)
    stage_dir.mkdir(parents=True, exist_ok=True)

    candidates = build_source_candidates(cfg, target)
    if not candidates:
        raise SourceUpdateError(
            "no download candidates: the registry JSON is unreachable and no "
            "archive_mirrors/github_repo are configured"
        )
    errors: List[str] = []
    for url, sha256 in candidates:
        archive = stage_dir / _archive_name(url)
        payload = stage_dir / "payload"
        try:
            print(f"[worker] source: trying {url}", flush=True)
            download_to_file(url, archive)
            if sha256:
                actual = sha256_of(archive)
                if actual != sha256:
                    raise ValueError(f"sha256 mismatch: got {actual[:16]}…, want {sha256[:16]}…")
            if payload.exists():
                shutil.rmtree(payload, ignore_errors=True)
            payload.mkdir(parents=True)
            root = extract_archive(archive, payload)
            found = sniff_version(root)
            if found != target:
                raise ValueError(f"archive reports version {found!r}, want {target!r}")
            return root, url
        except Exception as e:  # noqa: BLE001 - failover to the next candidate
            errors.append(f"{url}: {e}")
        finally:
            try:
                archive.unlink()
            except OSError:
                pass
    raise SourceUpdateError("all sources failed: " + " | ".join(errors[-4:]))


class TreeSwapError(OSError):
    """Swap failed AFTER the old tree was renamed aside."""

    def __init__(self, message: str, restored: bool):
        super().__init__(message)
        self.restored = restored


def swap_source_tree(root: Path, payload_root: Path, backup: Path) -> None:
    """root → backup (same-volume rename), payload → root (may cross volumes)."""
    import shutil

    os.rename(root, backup)
    try:
        shutil.move(str(payload_root), str(root))
    except Exception as e:
        restored = False
        try:
            if root.exists():
                shutil.rmtree(root, ignore_errors=True)
            os.rename(backup, root)
            restored = True
        except OSError:
            pass
        raise TreeSwapError(str(e), restored) from e


def restore_source_tree(root: Path, backup: Path) -> bool:
    """Put ``backup`` back at ``root`` (the failed new tree is kept aside as ``<name>.failed-<ts>`` for post-mortem)."""
    try:
        if root.exists():
            failed = root.parent / f"{root.name}.failed-{int(time.time())}"
            os.rename(root, failed)
        os.rename(backup, root)
        return True
    except OSError:
        return False


def prune_backups(root: Path, keep: int) -> None:
    """Retain the newest ``keep`` ``<name>.bak-*`` snapshots; drop the rest."""
    import shutil

    try:
        backups = sorted(
            root.parent.glob(f"{root.name}.bak-*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for old in backups[max(0, int(keep)):]:
        shutil.rmtree(old, ignore_errors=True)


# ---------------------------------------------------------------------------
# channel flows
# ---------------------------------------------------------------------------

def _pip_channel_update(
    cfg, target: str, from_version: str, extra_args: List[str],
    set_phase, finish, respawn, port: int, base_url: str, neutral_cwd: str,
) -> int:
    # 1) STOP first: on Windows pip cannot replace a package whose files a
    #    live server process holds; and even where it could, replacing the
    #    code under a running agent is asking for a mixed-state crash.
    set_phase(update_state.PHASE_STOPPING)
    stop_failure = _stop_and_wait(base_url, port, _stop_timeout_s(cfg))
    if stop_failure:
        # Installing against a held port is how Windows pip explodes
        # (SHFileOperationW); abort cleanly and put the server back up.
        respawn()
        return finish(update_state.PHASE_FAILED, stop_failure)

    # 2) INSTALL — pip uninstalls BEFORE installing: if the install half
    #    fails after the uninstall succeeded, the package is GONE, so the
    #    failure path first tries to restore the old version before giving up.
    set_phase(update_state.PHASE_INSTALLING)
    rc, output = run_pip_install(f"boarai=={target}", extra_args, cwd=neutral_cwd)
    if rc != 0:
        restored = False
        if verify_install(cwd=neutral_cwd) != from_version:
            print(f"[worker] pip failed and package looks damaged; reinstalling {from_version}", flush=True)
            r_rc, r_out = run_pip_install(f"boarai=={from_version}", extra_args, cwd=neutral_cwd)
            restored = r_rc == 0 and verify_install(cwd=neutral_cwd) == from_version
        else:
            restored = True  # old package still importable — install never tore it down
        respawn()  # never leave the machine down
        return finish(
            update_state.PHASE_FAILED,
            f"pip install failed ({rc})"
            + ("" if restored else f"; REINSTALL of {from_version} also failed — environment needs attention"),
            pip_output=output[-2000:],
            restored=restored,
        )

    # 3) VERIFY in a fresh interpreter (this one must stay on old code)
    set_phase(update_state.PHASE_VERIFYING)
    installed = verify_install(cwd=neutral_cwd)
    if installed != target:
        # One automatic rollback to the version we came from, then give up.
        print(f"[worker] verify mismatch (got {installed!r}, want {target!r}); rolling back", flush=True)
        rb_rc, rb_out = run_pip_install(f"boarai=={from_version}", extra_args, cwd=neutral_cwd)
        rolled_back = rb_rc == 0 and verify_install(cwd=neutral_cwd) == from_version
        respawn()
        return finish(
            update_state.PHASE_FAILED,
            f"post-install verification failed (reported {installed!r}); "
            + ("rolled back to " + from_version if rolled_back else "ROLLBACK FAILED — environment needs attention"),
            rolled_back=rolled_back,
        )

    # 4) RESTART on the new code and prove the version over HTTP
    set_phase(update_state.PHASE_RESTARTING)
    if not respawn() or not poll_new_version(base_url, target):
        return finish(
            update_state.PHASE_FAILED,
            f"installed {target} but the restarted server did not report it "
            f"within {RESTART_POLL_TIMEOUT_S}s",
            install_succeeded=True,
        )
    return finish(update_state.PHASE_DONE)


def _git_channel_update(
    cfg, target: str, from_version: str, attempt_id: str,
    set_phase, finish, respawn, root: Path, port: int, base_url: str,
) -> int:
    from boarai.core.update import steward

    remote = str(getattr(cfg, "git_remote", "origin") or "origin")
    tag = tag_for(cfg, target)

    # 1) ONLINE steps while the server still runs: precheck, fetch, tag probe
    set_phase(update_state.PHASE_CHECKING)
    pre = steward.git_precheck(root, remote=remote)
    if pre:
        return finish(
            update_state.PHASE_FAILED,
            f"{pre['code']}: {pre['message']}"
            + (f" Run: {pre['update_command']}" if pre.get("update_command") else ""),
        )
    head = steward.git_head_commit(root)
    rc, out = steward.run_git(["fetch", "--tags", remote], root, timeout=GIT_FETCH_TIMEOUT_S)
    if rc != 0:
        return finish(update_state.PHASE_FAILED, f"git fetch --tags {remote} failed ({rc}): {out[-500:]}")
    if not steward.git_tag_exists(root, tag):
        return finish(update_state.PHASE_FAILED, f"tag {tag} not found on {remote} after fetch")

    # 2) STOP before mutating the tree (a live server holds files on Windows)
    set_phase(update_state.PHASE_STOPPING)
    stop_failure = _stop_and_wait(base_url, port, _stop_timeout_s(cfg))
    if stop_failure:
        respawn()
        return finish(update_state.PHASE_FAILED, stop_failure)

    # 3) CHECKOUT the tag (detached HEAD; no --force: never eat local state)
    set_phase(update_state.PHASE_INSTALLING)
    rc, out = steward.run_git(["checkout", tag], root, timeout=GIT_FETCH_TIMEOUT_S)
    if rc != 0:
        if head:  # put the tree back regardless of how far the checkout got
            steward.run_git(["checkout", head], root)
        respawn()
        return finish(update_state.PHASE_FAILED, f"git checkout {tag} failed ({rc}): {out[-500:]}",
                      tag=tag, head=head)

    # 4) VERIFY in a fresh interpreter (cwd=root: the checkout IS the code)
    set_phase(update_state.PHASE_VERIFYING)
    installed = verify_install(cwd=str(root))
    if installed != target:
        rolled_back = False
        if head:
            rb_rc, _ = steward.run_git(["checkout", head], root)
            rolled_back = rb_rc == 0 and verify_install(cwd=str(root)) == from_version
        respawn()
        return finish(
            update_state.PHASE_FAILED,
            f"post-checkout verification failed (reported {installed!r}); "
            + (f"rolled back to {head[:12]}" if rolled_back else "ROLLBACK FAILED — tree needs attention"),
            rolled_back=rolled_back, tag=tag, head=head,
        )

    # 5) RESTART on the new tree and prove the version over HTTP
    set_phase(update_state.PHASE_RESTARTING)
    if not respawn() or not poll_new_version(base_url, target):
        return finish(
            update_state.PHASE_FAILED,
            f"checked out {tag} but the restarted server did not report {target} "
            f"within {RESTART_POLL_TIMEOUT_S}s",
            install_succeeded=True, tag=tag,
        )
    return finish(update_state.PHASE_DONE, tag=tag, head=head)


def _source_channel_update(
    cfg, target: str, attempt_id: str,
    set_phase, finish, respawn, root: Path, port: int, base_url: str, data_dir: Path,
) -> int:
    import shutil

    from boarai.core.update import steward

    set_phase(update_state.PHASE_CHECKING)
    pre = steward.source_precheck(root)
    if pre:
        return finish(update_state.PHASE_FAILED, f"{pre['code']}: {pre['message']}")

    # 1) DOWNLOAD + sha256 + extract + version-sniff into staging while the
    #    server still runs (a failed download must never take the server down)
    set_phase(update_state.PHASE_INSTALLING)
    stage_dir = Path(data_dir) / "update" / f"stage_{attempt_id}"
    try:
        payload_root, used_url = prepare_source_update(cfg, target, stage_dir)
    except (SourceUpdateError, OSError) as e:
        shutil.rmtree(stage_dir, ignore_errors=True)
        return finish(update_state.PHASE_FAILED, str(e))

    try:
        # 2) STOP (the swap renames the very tree the server was launched from)
        set_phase(update_state.PHASE_STOPPING)
        stop_failure = _stop_and_wait(base_url, port, _stop_timeout_s(cfg))
        if stop_failure:
            respawn()
            return finish(update_state.PHASE_FAILED, stop_failure)

        # 3) SWAP + VERIFY (no extra phase name for the swap — the UI phase
        #    set stays the pip-channel one; the receipt records the details)
        set_phase(update_state.PHASE_VERIFYING)
        backup = root.parent / f"{root.name}.bak-{attempt_id}"
        try:
            swap_source_tree(root, payload_root, backup)
        except OSError as e:
            respawn()
            # Pre-swap rename failure leaves the live tree untouched (no
            # TreeSwapError.restored attribute → "intact"); post-rename
            # failures must tell the truth about the rollback outcome.
            restored = getattr(e, "restored", True)
            detail = ("previous tree intact/restored" if restored
                      else f"RESTORE FAILED — restore manually from backup {backup}")
            return finish(update_state.PHASE_FAILED,
                          f"tree swap failed: {e}; {detail}",
                          source_url=used_url, backup=str(backup), restored=restored)

        installed = verify_install(cwd=str(root))
        if installed != target:
            restored = restore_source_tree(root, backup)
            respawn()
            return finish(
                update_state.PHASE_FAILED,
                f"post-swap verification failed (reported {installed!r}); "
                + (f"restored backup {backup.name}" if restored else "RESTORE FAILED — tree needs attention"),
                rolled_back=restored, source_url=used_url,
            )

        # 4) RESTART on the new tree and prove the version over HTTP
        set_phase(update_state.PHASE_RESTARTING)
        if not respawn() or not poll_new_version(base_url, target):
            return finish(
                update_state.PHASE_FAILED,
                f"swapped in {target} but the restarted server did not report it "
                f"within {RESTART_POLL_TIMEOUT_S}s (backup kept at {backup.name})",
                install_succeeded=True, source_url=used_url,
            )
        prune_backups(root, int(getattr(cfg, "backup_keep", 1) or 0))
        return finish(update_state.PHASE_DONE, source_url=used_url, backup=str(backup))
    finally:
        # Every terminal path (done/failed incl. restart failure) drops the
        # staging dir — leftover staging is pure temp data, never a backup.
        shutil.rmtree(stage_dir, ignore_errors=True)


def run_update(
    target: str,
    from_version: str,
    extra_args: List[str],
    attempt_id: str,
    kind: str = "apply",
    cfg=None,
    channel: Optional[str] = None,
    tree_root: Optional[Path] = None,
    launch_prefix: Optional[List[str]] = None,
) -> int:
    """The whole worker flow, dispatched by install channel. Exit code."""
    from boarai.core.runtime import get_data_dir
    from boarai.core.update import steward

    if cfg is None:
        from boarai.core.update.checker import get_update_config

        cfg = get_update_config()
    data_dir = get_data_dir()
    # Neutral cwd for pip subprocesses: the worker inherits the server's
    # cwd, which in a dev/e2e setup can be a SOURCE CHECKOUT — cwd lands
    # first on sys.path for `python -c` / `-m boarai` and would shadow the
    # installed package (wrong version reports, wrong code launched).
    neutral_cwd = str(data_dir)
    port = _server_port(data_dir)
    base_url = _base_url(port)

    if channel is None:
        channel = steward.detect_install_kind()
    if channel != "pip" and tree_root is None:
        tree_root = steward.running_tree_root()
    tree_root = Path(tree_root) if tree_root else None
    if channel != "pip" and tree_root is None:
        # Cannot locate the tree we are supposed to replace — refuse loudly.
        update_state.write_state({
            "attempt_id": attempt_id, "phase": update_state.PHASE_FAILED, "kind": kind,
            "from_version": from_version, "target_version": target, "worker_pid": os.getpid(),
            "error": "cannot locate the running source tree (boarai.core.__file__ anchor failed)",
        })
        return 1
    if launch_prefix is None:
        launch_prefix = (
            [sys.executable, str(tree_root / "main.py")]
            if channel != "pip" and tree_root
            else [sys.executable, "-m", "boarai"]
        )
    restart_cwd = str(tree_root) if channel != "pip" and tree_root else neutral_cwd

    def set_phase(phase: str, error: Optional[str] = None) -> None:
        update_state.write_state({
            "attempt_id": attempt_id,
            "phase": phase,
            "kind": kind,
            "channel": channel,
            "from_version": from_version,
            "target_version": target,
            "worker_pid": os.getpid(),
            **({"error": error} if error else {}),
        })

    def finish(phase: str, error: Optional[str] = None, **extra) -> int:
        set_phase(phase, error)
        update_state.write_receipt({
            "attempt_id": attempt_id,
            "kind": kind,
            "channel": channel,
            "outcome": phase,
            "from_version": from_version,
            "target_version": target,
            **({"error": error} if error else {}),
            **extra,
        })
        print(f"[worker] {phase}" + (f": {error}" if error else ""), flush=True)
        return 0 if phase in (update_state.PHASE_DONE, update_state.PHASE_CURRENT) else 1

    def respawn() -> bool:
        return spawn_server_detached(cwd=restart_cwd, prefix=launch_prefix)

    if channel == "git":
        return _git_channel_update(
            cfg, target, from_version, attempt_id,
            set_phase, finish, respawn, tree_root, port, base_url,
        )
    if channel == "source":
        return _source_channel_update(
            cfg, target, attempt_id,
            set_phase, finish, respawn, tree_root, port, base_url, data_dir,
        )
    return _pip_channel_update(
        cfg, target, from_version, extra_args,
        set_phase, finish, respawn, port, base_url, neutral_cwd,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m boarai.core.update.worker")
    parser.add_argument("--target", required=True, help="version to install")
    parser.add_argument("--from-version", default="", help="version being replaced (rollback anchor)")
    parser.add_argument("--attempt-id", default="", help="attempt id shared with the server/UI")
    parser.add_argument("--kind", default="apply", choices=("apply", "rollback"))
    parser.add_argument("--pip-arg", action="append", default=[], help="extra pip args (repeatable)")
    args = parser.parse_args(argv)

    from boarai.core import __version__ as from_version_default
    from boarai.core.update import steward
    from boarai.core.update.checker import get_update_config

    # Capture EVERYTHING config- and channel-derived before the replacement
    # step (hermetic rule: after pip/checkout/swap this interpreter must not
    # import fresh core code).
    cfg = get_update_config()
    extra_args = list(args.pip_arg) + [str(a) for a in (getattr(cfg, "pip_extra_args", []) or [])]
    from_version = args.from_version or str(from_version_default)
    attempt_id = args.attempt_id or uuid.uuid4().hex[:12]
    channel = steward.detect_install_kind()
    tree_root = steward.running_tree_root() if channel != "pip" else None
    launch_prefix = (
        [sys.executable, str(tree_root / "main.py")]
        if channel != "pip" and tree_root
        else [sys.executable, "-m", "boarai"]
    )

    lock = update_state.FileLock(update_state.lock_path())
    if not lock.acquire():
        print("[worker] another worker holds the lock; exiting", flush=True)
        return 0
    try:
        phase = (update_state.PHASE_CURRENT if args.target == from_version
                 else update_state.PHASE_CHECKING)
        update_state.write_state({
            "attempt_id": attempt_id,
            "phase": phase,
            "kind": args.kind,
            "channel": channel,
            "from_version": from_version,
            "target_version": args.target,
            "worker_pid": os.getpid(),
        })
        if phase == update_state.PHASE_CURRENT:
            return 0
        return run_update(
            args.target, from_version, extra_args, attempt_id, args.kind,
            cfg=cfg, channel=channel, tree_root=tree_root, launch_prefix=launch_prefix,
        )
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
