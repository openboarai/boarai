"""Version check against a PyPI-style JSON endpoint (stdlib urllib only).

The check is read-only and network-flaky by nature: every failure degrades
to "no update known" with the error surfaced in the status payload — an
unreachable registry must never break startup or spam retries (the cache
TTL bounds the churn).

Config (``update`` section of config.yaml, see core.config.schema):
  ``pypi_url``           — the JSON endpoint (default PyPI; any
                           ``{"info": {"version": ...}}`` responder works,
                           including a ``file://`` URL for offline tests).
                           It is the SINGLE version oracle for ALL channels;
                           the source channel also resolves its sdist
                           download URL + sha256 from the per-version JSON.
  ``check_interval_hours`` / ``auto_check`` / ``enabled``
  ``pip_extra_args``     — extra args for the worker's pip install
                           (e.g. ``["--no-index", "--find-links", <dir>]``)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .version import current_version, is_newer, parse_version

logger = logging.getLogger(__name__)

DEFAULT_PYPI_URL = "https://pypi.org/pypi/boarai/json"

_CACHE_RELPATH = Path("cache") / "update_check.json"
_CHECK_TIMEOUT_S = 15.0


def get_update_config():
    """The ``update`` config section (defaults when unset/unreadable)."""
    try:
        from boarai.core.config import get_config_parser

        cp = get_config_parser()
        cfg = getattr(cp, "_config", None)
        upd = getattr(cfg, "update", None)
        if upd is not None:
            return upd
    except Exception:  # noqa: BLE001 - config must never break the check
        pass
    from boarai.core.config.schema import UpdateConfig

    fallback = UpdateConfig()
    try:
        import yaml

        from boarai.core.runtime import get_data_dir

        path = get_data_dir() / "config" / "config.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        section = raw.get("update") if isinstance(raw, dict) else None
        if isinstance(section, dict):
            for key, value in section.items():
                if not hasattr(fallback, key):
                    continue
                if key in ("pip_extra_args", "github_proxies", "archive_mirrors"):
                    value = [str(a) for a in value] if isinstance(value, list) else []
                elif key == "backup_keep":
                    try:
                        value = max(0, int(value))
                    except (TypeError, ValueError):
                        continue
                elif key in ("check_interval_hours", "stop_timeout_s"):
                    try:
                        value = max(0.0, float(value))
                    except (TypeError, ValueError):
                        continue
                setattr(fallback, key, value)
    except Exception:  # noqa: BLE001 - unreadable config → defaults
        pass
    return fallback


def fetch_release_json(url: str, timeout: float = _CHECK_TIMEOUT_S) -> Dict[str, Any]:
    """GET a registry JSON endpoint and return the parsed dict (raises on failure)."""
    import urllib.request

    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "boarai-update-check"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"registry response from {url} is not a JSON object")
    return data


def fetch_latest(url: Optional[str] = None, timeout: float = _CHECK_TIMEOUT_S) -> str:
    """GET the registry JSON and return ``info.version`` (sync; raises on failure)."""
    target = url or DEFAULT_PYPI_URL
    data = fetch_release_json(target, timeout=timeout)
    version = str(((data.get("info") or {}).get("version") or "")).strip()
    if not version:
        raise ValueError(f"no info.version in response from {target}")
    return version


def version_json_url(base_url: Optional[str], version: str) -> str:
    """Per-version registry JSON for sdist resolution."""
    base = str(base_url or DEFAULT_PYPI_URL).strip().rstrip("/")
    if base.endswith("/json"):
        stem = base[: -len("/json")].rstrip("/")
        return f"{stem}/{version}/json"
    return base


def sdist_from_json(data: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """The sdist entry ``{"url", "sha256", "filename"}`` from registry JSON."""
    for entry in data.get("urls") or []:
        if not isinstance(entry, dict) or entry.get("packagetype") != "sdist":
            continue
        url = str(entry.get("url") or "").strip()
        if not url:
            continue
        digests = entry.get("digests") if isinstance(entry.get("digests"), dict) else {}
        sha = str(digests.get("sha256") or "").strip()
        return {"url": url, "sha256": sha or "", "filename": str(entry.get("filename") or "")}
    return None


# files.pythonhosted.org is the actual PyPI package-file host; these mirrors
# keep the SAME ``/packages/<hash path>`` layout, so a host swap is a valid
# China-friendly failover for sdist downloads (TUNA first: official mirror).
PACKAGE_FILE_HOST = "files.pythonhosted.org"
PACKAGE_HOST_MIRRORS = (
    ("pypi.tuna.tsinghua.edu.cn", ""),
    ("mirrors.aliyun.com", "/pypi"),
)


def mirror_file_urls(url: str) -> list:
    """Host-swap variants of a files.pythonhosted.org package URL ([] otherwise)."""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(str(url or ""))
    if parts.netloc != PACKAGE_FILE_HOST or not parts.path.startswith("/packages/"):
        return []
    return [
        urlunsplit(("https", host, prefix + parts.path, "", ""))
        for host, prefix in PACKAGE_HOST_MIRRORS
    ]


def _cache_path() -> Path:
    from boarai.core.runtime import get_data_dir

    return get_data_dir() / _CACHE_RELPATH


def read_cache() -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_cache(payload: Dict[str, Any]) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        logger.debug("update check cache write failed: %s", e)


async def check(force: bool = False) -> Dict[str, Any]:
    """The (cached) check result: latest/current/update_available/error."""
    cfg = get_update_config()
    ttl_s = max(60.0, float(getattr(cfg, "check_interval_hours", 24.0)) * 3600.0)
    current = current_version()

    cached = read_cache()
    if not force and cached:
        try:
            age = time.time() - float(cached.get("at", 0))
        except (TypeError, ValueError):
            age = ttl_s
        if age < ttl_s and cached.get("current") == current:
            return cached

    url = str(getattr(cfg, "pypi_url", "") or DEFAULT_PYPI_URL)
    error: Optional[str] = None
    latest: Optional[str] = None
    try:
        latest = await asyncio.to_thread(fetch_latest, url)
        if parse_version(latest) is None:
            # Non-numeric (pre-release etc.): not an update target.
            latest = None
    except Exception as e:  # noqa: BLE001 - network flakiness is expected
        error = str(e)

    result: Dict[str, Any] = {
        "at": time.time(),
        "current": current,
        "latest": latest,
        "update_available": bool(latest and is_newer(latest, current)),
        "url": url,
        "error": error,
    }
    _write_cache(result)
    return result
