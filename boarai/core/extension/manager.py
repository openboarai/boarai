"""

Extension manager for BoarAi extension system.

Manages extension lifecycle with hot-reload via file system watcher.

"""

import asyncio

import logging

import os

import time

from pathlib import Path

from typing import Any, Dict, List, Optional, Tuple

from boarai.core.event import EventBus, EventType, get_event_bus, publish_event

from .types import ExtensionType, ExtensionStatus

from .base import ExtensionBase, ExtensionError

from .manifest import ExtensionManifest

from .loader import ExtensionLoader

from .discovery import ExtensionDiscovery

logger = logging.getLogger(__name__)

class ExtensionManager:

    """Extension lifecycle manager with hot-reload support."""

    def __init__(self, event_bus: Optional[EventBus] = None, project_root: Optional[Path] = None):
        """Initialize extension manager."""
        self._event_bus = event_bus or get_event_bus()
        self._loader = ExtensionLoader(self._event_bus)
        self._discovery = ExtensionDiscovery(project_root)
        self._extensions: Dict[str, ExtensionBase] = {}
        # Discovered-but-failed loads, keyed by extension name. Failures are
        # NOT log-only: this registry feeds GET /api/extensions (`failed`),
        # the WebUI failure cards and the extension.load_failed event.
        self._failed: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._watcher_task: Optional[asyncio.Task] = None
        self._watch_interval = 5.0  # seconds between polls

        # v2: persistent enable/disable state
        self._project_root = self._discovery._project_root
        self._state_store: Optional[Any] = None
        try:
            from .state_store import ExtensionStateStore
            from boarai.core.runtime import resolve_data_dir
            data_dir = resolve_data_dir(self._project_root)
            self._state_store = ExtensionStateStore(data_dir)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Extension state store disabled: {e}")

        self._setup_event_handlers()

    def _setup_event_handlers(self) -> None:

        """Setup event handlers."""

        self._startup_handler = self._on_system_startup

    async def _on_system_startup(self, event) -> None:

        """System startup event handler."""

        logger.info("System startup: discovering and loading extensions...")

        await self.discover_and_load_all()

    async def setup(self) -> None:

        """Setup extension manager."""

        await self._event_bus.subscribe(

            EventType.SYSTEM_STARTUP,

            self._startup_handler

        )

        # v2: declare core hook points before any extension loads
        try:
            from .contribution.core_hooks import declare_core_hook_points
            declare_core_hook_points()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Core hook points not declared: {e}")

        self.start_watcher()

    def start_watcher(self) -> None:

        """Start the file system watcher for hot-reload."""

        if self._watcher_task is not None:

            return

        self._watcher_task = asyncio.create_task(self._watch_loop())

        logger.info(f"Extension file watcher started (interval={self._watch_interval}s)")

    async def stop_watcher(self) -> None:

        """Stop the file system watcher."""

        if self._watcher_task:

            self._watcher_task.cancel()

            try:

                await self._watcher_task

            except asyncio.CancelledError:

                pass

            self._watcher_task = None

            logger.info("Extension file watcher stopped")

    # Directories/files the watcher NEVER treats as a change trigger. An
    # extension writing to its own dir (logs, data, __pycache__, temp state)
    # must NOT trigger an infinite reload loop (V4 fix).
    _WATCH_EXCLUDED_PARTS = {
        "__pycache__", ".git", "node_modules", "data", "logs", "cache",
        ".boarai", "tmp", "temp", ".venv", "venv",
    }
    _WATCH_EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp", ".swp"}

    @staticmethod
    def _watch_fingerprint(fp: Path) -> Tuple[float, int]:
        """V14 fix: (mtime, size) fingerprint catches same-mtime edits."""
        try:
            st = fp.stat()
            return (st.st_mtime, st.st_size)
        except OSError:
            return (0.0, 0)

    def _watch_should_skip(self, fp: Path) -> bool:
        """Skip files that are not extension source (V4: no self-write loops)."""
        if not fp.is_file():
            return True
        if fp.suffix.lower() in self._WATCH_EXCLUDED_SUFFIXES:
            return True
        # Skip any path component that is a runtime/data dir
        try:
            parts = set(fp.parts)
        except Exception:  # noqa: BLE001
            return True
        if parts & self._WATCH_EXCLUDED_PARTS:
            return True
        # Only watch code/manifest sources
        return fp.suffix.lower() not in {".py", ".yaml", ".yml"}

    def _snapshot_ext_files(
        self,
        extension: ExtensionBase,
        snapshots: Dict[str, Tuple[float, int]],
    ) -> None:
        """Record current fingerprints for an extension's source files."""
        _m = getattr(extension, "_manifest", None) or extension.manifest
        if not _m or not _m.path:
            return
        for fp in _m.path.rglob("*"):
            if not self._watch_should_skip(fp):
                snapshots[str(fp)] = self._watch_fingerprint(fp)

    async def _watch_loop(self) -> None:

        """Poll extension source files for changes and auto-reload."""

        # Snapshot of last known fingerprints: {path: (mtime, size)}

        snapshots: Dict[str, Tuple[float, int]] = {}

        # Track load failures per path so failed hot-discovery is back-off
        # retried instead of hammered every tick: {path: (monotonic_ts,
        # attempts)}. Deterministic failures escalate exponentially.

        known_failures: Dict[str, Tuple[float, int]] = {}

        for ext in self._extensions.values():
            self._snapshot_ext_files(ext, snapshots)

        while True:

            try:

                await asyncio.sleep(self._watch_interval)

                # Hot-discovery: load+enable extension directories that were
                # added while the core was already running.

                await self._discover_new_extensions(snapshots, known_failures)

                for ext_name, ext in list(self._extensions.items()):

                    _m = getattr(ext, "_manifest", None) or ext.manifest

                    if not _m or not _m.path:

                        continue

                    changed = False

                    for fp in _m.path.rglob("*"):

                        if self._watch_should_skip(fp):

                            continue

                        key = str(fp)

                        new_fp = self._watch_fingerprint(fp)

                        old_fp = snapshots.get(key)

                        if old_fp is None or new_fp != old_fp:

                            snapshots[key] = new_fp

                            changed = True

                    if changed:

                        logger.info(f"File change detected in '{ext_name}', reloading...")

                        await self.reload_extension(ext_name)

                        # Refresh snapshots after reload

                        new_ext = self._extensions.get(ext_name)

                        if new_ext:
                            self._snapshot_ext_files(new_ext, snapshots)

            except asyncio.CancelledError:

                break

            except Exception as e:

                logger.warning(f"File watcher error: {e}")

    async def _discover_new_extensions(
        self,
        snapshots: Dict[str, Tuple[float, int]],
        known_failures: Dict[str, float],
    ) -> None:
        """Hot-discover extension directories added at runtime."""
        try:
            discovered = self._discovery.discover_all()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Extension discovery scan failed: {e}")
            return

        # Retire failure records whose directory vanished (extension deleted
        # or moved) — otherwise a ghost failure card outlives the extension.
        if self._failed:
            for name in list(self._failed):
                entry = self._failed[name]
                if name not in self._extensions and not Path(
                    entry.get("path") or ""
                ).is_dir():
                    self._failed.pop(name, None)
                    known_failures.pop(entry.get("path") or "", None)
                    logger.info(
                        f"Failed extension '{name}' removed from disk; "
                        f"failure record cleared")

        for ext_type, paths in discovered.items():
            for ext_path in paths:
                name = ext_path.name
                if name in self._extensions:
                    continue

                try:
                    src = self._loader._infer_source(ext_path)
                except Exception:  # noqa: BLE001
                    src = ""

                # v2: skip extensions disabled persistently (layer-precise)
                if self._state_store:
                    if self._state_store.is_disabled(name, source=src):
                        logger.debug(
                            f"Discovered '{name}' (source={src}) is disabled "
                            f"(persisted), skipping")
                        self._clear_failure(name)
                        continue

                # Back-off retry after a previous load failure
                previous = known_failures.get(str(ext_path))
                if previous:
                    last_ts, attempts = previous
                    entry = self._failed.get(name) or {}
                    interval = self._backoff_interval(
                        bool(entry.get("deterministic")), attempts
                    )
                    if time.monotonic() - last_ts < interval:
                        continue

                try:
                    extension = await self.load_extension(ext_path)
                    enabled = await self.enable_extension(extension.name)
                    if enabled:
                        known_failures.pop(str(ext_path), None)
                        self._snapshot_ext_files(extension, snapshots)
                        logger.info(
                            f"Watcher discovered and enabled new extension: "
                            f"'{extension.name}' ({ext_type})")
                    else:
                        # Loaded but start/route-mount failed (status ERROR
                        # carries the reason, surfaced via the API): never
                        # announce success — a false "enabled" line next to
                        # the real ERROR made production triage misleading.
                        logger.warning(
                            f"Watcher loaded '{extension.name}' but enable "
                            f"FAILED — see GET /api/extensions/{extension.name}"
                            f" (error field)")
                except Exception as e:  # noqa: BLE001
                    attempts = (previous[1] + 1) if previous else 1
                    known_failures[str(ext_path)] = (time.monotonic(), attempts)
                    await self._record_load_failure(name, ext_path, e, source=src)
                    # Log dedup: the registry + API + WebUI carry the durable
                    # state, so warn only on the first failure and every 10th
                    # retry (a closed trust gate otherwise floods ~2 lines/min).
                    if attempts == 1 or attempts % 10 == 0:
                        logger.warning(
                            f"Watcher failed to load new extension '{name}' "
                            f"(attempt {attempts}): {e}")
                    else:
                        logger.debug(
                            f"Watcher retry {attempts} for '{name}' failed: {e}")

    async def discover_and_load_all(self) -> Dict[str, List[str]]:

        """Discover and load all extensions."""

        results = {}

        # Discover all extensions

        discovered = self._discovery.discover_all()

        for ext_type, paths in discovered.items():

            loaded_names = []

            for ext_path in paths:

                # v2: skip extensions disabled persistently (V9: layer-precise)
                if self._state_store:
                    src = self._loader._infer_source(ext_path) if self._loader else ""
                    if self._state_store.is_disabled(ext_path.name, source=src):
                        logger.debug(
                            f"Extension '{ext_path.name}' (source={src}) is disabled "
                            f"(persisted), skipping"
                        )
                        continue

                try:

                    extension = await self.load_extension(ext_path)

                    loaded_names.append(extension.name)

                except Exception as e:

                    logger.error(f"Failed to load extension from {ext_path}: {e}")
                    try:
                        await self._record_load_failure(
                            ext_path.name, ext_path, e,
                            source=self._loader._infer_source(ext_path),
                        )
                    except Exception:  # noqa: BLE001 — bookkeeping only
                        pass

            results[ext_type] = loaded_names

        # Hook binding is validated only now: lazy binding means an extension
        # may bind a point declared by one that loads later in this loop.
        await self.report_unbound_hooks()

        # main.py logs this same summary at INFO with its own context;
        # keeping both would duplicate the line on every startup.
        logger.debug(f"Loaded extensions: {results}")

        return results

    async def report_unbound_hooks(
        self, names: Optional[List[str]] = None
    ) -> Dict[str, List[str]]:
        """Warn about (or, under strict_hooks, reject) unbound hook bindings."""
        if self._loader is None:
            return {}
        targets = (
            list(self._extensions.values())
            if names is None
            else [self._extensions[n] for n in names if n in self._extensions]
        )
        report: Dict[str, List[str]] = {}
        for ext in targets:
            manifest = getattr(ext, "_manifest", None)
            if manifest is None:
                continue
            ext_name = getattr(ext, "name", "?")
            try:
                unbound = await self._loader.enforce_hook_binding(ext, manifest)
            except Exception as e:  # noqa: BLE001 — strict rejection only
                logger.error(
                    "Extension '%s' rejected by strict hook validation: %s",
                    ext_name, e,
                )
                self._extensions.pop(ext_name, None)
                report[ext_name] = list(
                    getattr(ext, "_unbound_hooks", []) or []
                )
                continue
            if unbound:
                report[ext_name] = unbound
        return report

    async def load_discovered_extension(self, name: str) -> Optional[Any]:
        """Load and register a discovered but not-yet-loaded extension."""
        try:
            discovered = self._discovery.discover_all()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Extension discovery failed: {e}")
            return None
        for _ext_type, paths in discovered.items():
            for ext_path in paths:
                if ext_path.name != name:
                    continue
                if name in self._extensions:
                    return self._extensions[name]
                try:
                    extension = await self.load_extension(ext_path)
                    await self.report_unbound_hooks([extension.name])
                    return extension
                except Exception as e:  # noqa: BLE001
                    logger.error(f"Failed to load extension '{name}': {e}")
                    try:
                        await self._record_load_failure(
                            name, ext_path, e,
                            source=self._loader._infer_source(ext_path),
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    return None
        logger.warning(f"Extension not found in discovery: {name}")
        return None

    # --- Per-extension operator trust (WebUI/API grant) ---

    def _locate_discovered(self, name: str) -> Tuple[Optional[Path], str]:
        """(path, source) of a discovered extension directory."""
        entry = self._failed.get(name)
        if entry and entry.get("path"):
            return Path(entry["path"]), str(entry.get("source") or "")
        try:
            discovered = self._discovery.discover_all()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Extension discovery failed: {e}")
            return None, ""
        for _ext_type, paths in discovered.items():
            for ext_path in paths:
                if ext_path.name == name:
                    try:
                        src = self._loader._infer_source(ext_path)
                    except Exception:  # noqa: BLE001
                        src = ""
                    return ext_path, src
        return None, ""

    def is_trusted(self, name: str, source: Optional[str] = None) -> bool:
        """Per-extension trust state (for API/UI serialization)."""
        if source is None:
            source = ""
            ext = self._extensions.get(name)
            if ext is not None:
                m = getattr(ext, "_manifest", None) or ext.manifest
                source = str(getattr(m, "source", "") or "")
            if not source:
                _path, source = self._locate_discovered(name)
        return bool(
            self._state_store
            and self._state_store.is_trusted(name, source or None)
        )

    async def trust_extension(self, name: str) -> Dict[str, Any]:
        """Grant per-extension in-process trust, then load it."""
        if not self._state_store:
            return {"ok": False, "code": "state.unavailable",
                    "message": "extension state store unavailable"}
        ext = self._extensions.get(name)
        if ext is not None:
            m = getattr(ext, "_manifest", None) or ext.manifest
            path = m.path
            source = str(getattr(m, "source", "") or "")
        else:
            path, source = self._locate_discovered(name)
        if path is None:
            return {"ok": False, "code": "not_found",
                    "message": f"no extension named '{name}' discovered"}
        # ONLY the user layer is trustable: system extensions are built-in
        # (always trusted), and an unknown/foreign source must never be
        # grantable — fail closed.
        if source != "user":
            return {"ok": False, "code": "trust.not_needed",
                    "message": "only user-layer extensions need (and accept) "
                               "a trust grant; system extensions are "
                               "built-in and always trusted"}
        self._state_store.set_trusted(name, source=source)
        # Trust intent = "run it": clear any persisted disable (e.g. a
        # previous Stop-retries hemostasis) so discovery stops skipping.
        self._state_store.set_enabled(name, source=source)
        logger.warning(
            "TRUST GRANTED for user extension '%s@%s' — in-process "
            "execution now allowed (revocable via untrust)", name, source,
        )
        self._clear_failure(name)
        if ext is None:
            ext = await self.load_discovered_extension(name)
        if ext is None:
            failed = self.get_failed(name) or {}
            return {
                "ok": False,
                "code": failed.get("code") or "load_failed",
                "message": failed.get("error") or "load failed after trust",
                "hint": failed.get("hint") or "",
            }
        # Best-effort start: the trust decision itself succeeded even if
        # route mounting/startup has issues (those surface as ext.error).
        try:
            await self.enable_extension(name)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Enable after trust failed for '{name}': {e}")
        return {"ok": True, "name": name, "source": source}

    async def untrust_extension(self, name: str) -> Dict[str, Any]:
        """Revoke per-extension trust."""
        if not self._state_store:
            return {"ok": False, "code": "state.unavailable",
                    "message": "extension state store unavailable"}
        ext = self._extensions.get(name)
        if ext is not None:
            m = getattr(ext, "_manifest", None) or ext.manifest
            path = m.path
            source = str(getattr(m, "source", "") or "")
        else:
            path, source = self._locate_discovered(name)
        if path is None and ext is None:
            return {"ok": False, "code": "not_found",
                    "message": f"no extension named '{name}' discovered"}
        if source != "user":
            return {"ok": False, "code": "trust.not_needed",
                    "message": "only user-layer extensions carry a trust "
                               "grant"}
        changed = self._state_store.set_untrusted(name, source=source)
        logger.warning(
            "TRUST REVOKED for user extension '%s@%s'", name, source,
        )
        global_open = (
            os.getenv("BOARAI_ALLOW_INPROCESS_USER_EXTENSIONS", "") == "1"
        )
        if ext is not None and not global_open:
            try:
                if ext.status == ExtensionStatus.RUNNING:
                    await ext.stop()
                    ext.set_status(ExtensionStatus.STOPPED)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Untrust stop failed for '{name}': {e}")
            try:
                await self._loader.unload_extension(ext)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Untrust unload failed for '{name}': {e}")
            self._extensions.pop(name, None)
            try:
                await self._record_load_failure(
                    name, path,
                    ExtensionError(
                        "Trust revoked by the operator; the loader gate "
                        "refuses this extension until it is trusted again.",
                        code="gate.inprocess_untrusted",
                    ),
                    source=source,
                )
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True, "name": name, "revoked": bool(changed)}

    # --- Load-failure registry (failures are a first-class visible state) ---

    # Failure codes that never self-heal: retrying on a fixed 60 s cadence
    # only floods the log. They back off exponentially (capped at 1 h) until
    # the operator changes config/code or disables the extension.
    _DETERMINISTIC_FAILURE_CODES = {"gate.inprocess_untrusted"}

    @staticmethod
    def _classify_failure(error: BaseException) -> Tuple[str, str, bool]:
        """(code, hint, deterministic) for one load error."""
        code = str(getattr(error, "code", "") or "")
        message = str(error)
        if not code:
            if "BOARAI_ALLOW_INPROCESS_USER_EXTENSIONS" in message:
                code = "gate.inprocess_untrusted"
            elif isinstance(error, ExtensionError):
                code = "loader.error"
            else:
                code = "transient.error"
        if code == "gate.inprocess_untrusted":
            hint = (
                "Operator trust decision: click 'Trust & load' on this card "
                "(per-extension, revocable), or set "
                "BOARAI_ALLOW_INPROCESS_USER_EXTENSIONS=1 (User-level env "
                "var) to trust ALL user extensions and restart the server "
                "FROM A NEW TERMINAL, or disable this extension to stop the "
                "retries. Static checks need no trust: GET "
                "/api/extensions/verify?name=<name>."
            )
        elif code == "deps.missing_import":
            hint = (
                "A module this extension imports is not installed. Add the "
                "package to the manifest's pip_dependencies (auto-installed "
                "on the next load for trusted extensions) or install it with "
                "pip; the pip package name may differ from the module name "
                "(cv2 -> opencv-python). Static import report: GET "
                "/api/extensions/verify?name=<name>."
            )
        elif code == "loader.error":
            hint = (
                "Run GET /api/extensions/verify?name=<name> for the "
                "structured issue stream."
            )
        else:
            hint = ""
        deterministic = (
            code in ExtensionManager._DETERMINISTIC_FAILURE_CODES
            or isinstance(error, ExtensionError)
        )
        return code, hint, deterministic

    async def _record_load_failure(
        self,
        name: str,
        path: Any,
        error: BaseException,
        *,
        source: str = "",
    ) -> Dict[str, Any]:
        """Register (or update) a load failure and publish the event."""
        code, hint, deterministic = self._classify_failure(error)
        entry = self._failed.get(name) or {}
        attempts = int(entry.get("attempts", 0)) + 1
        # Best-effort manifest peek so the UI can place the failure card in
        # the right tab (channel→adapters etc.) without loading anything.
        ext_type = str(entry.get("extension_type") or "")
        description = str(entry.get("description") or "")
        version = str(entry.get("version") or "")
        try:
            data = self._loader._read_manifest(
                self._loader._resolve_manifest_file(Path(path))
            )
            ext_type = str(data.get("extension_type") or ext_type)
            description = str(data.get("description") or description)
            version = str(data.get("version") or version)
        except Exception:  # noqa: BLE001 — manifest may be the broken part
            pass
        entry = {
            "name": name,
            "path": str(path),
            "error": str(error),
            "code": code,
            "hint": hint,
            "source": source or entry.get("source", ""),
            "extension_type": ext_type,
            "description": description,
            "version": version,
            "deterministic": deterministic,
            "attempts": attempts,
            "last_attempt": time.time(),
        }
        self._failed[name] = entry
        try:
            await publish_event(
                EventType.EXTENSION_LOAD_FAILED,
                {
                    "name": name,
                    "path": entry["path"],
                    "code": code,
                    "error": entry["error"],
                    "hint": hint,
                    "source": entry["source"],
                    "attempts": attempts,
                },
                "extension_manager",
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"extension.load_failed publish skipped: {e}")
        return entry

    @staticmethod
    def _backoff_interval(deterministic: bool, attempts: int) -> float:
        """Retry interval for a failed hot-discovery load."""
        if not deterministic:
            return 60.0
        return min(60.0 * (2 ** max(0, attempts - 1)), 3600.0)

    def list_failed(self) -> List[Dict[str, Any]]:
        """Snapshot of discovered-but-failed extensions (API/WebUI feed)."""
        return [
            {k: v for k, v in entry.items() if k != "deterministic"}
            for entry in self._failed.values()
        ]

    def get_failed(self, name: str) -> Optional[Dict[str, Any]]:
        entry = self._failed.get(name)
        return dict(entry) if entry else None

    def _clear_failure(self, name: str) -> None:
        self._failed.pop(name, None)

    def list_discovered_disabled(self) -> List[Dict[str, Any]]:
        """Metadata for discovered-but-disabled extensions."""
        items: List[Dict[str, Any]] = []
        if not (self._discovery and self._loader and self._state_store):
            return items
        try:
            discovered = self._discovery.discover_all()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Extension discovery scan failed: {e}")
            return items
        for ext_type, paths in discovered.items():
            for ext_path in paths:
                name = ext_path.name
                if name in self._extensions:
                    continue
                try:
                    src = self._loader._infer_source(ext_path)
                except Exception:  # noqa: BLE001
                    src = ""
                if not self._state_store.is_disabled(name, source=src):
                    continue
                meta: Dict[str, Any] = {
                    "id": name,
                    "name": name,
                    "enabled": False,
                    "status": "disabled",
                    "source": src,
                    "extension_type": ext_type,
                    "description": "",
                    "version": "",
                    "author": "",
                    "trusted": self._state_store.is_trusted(name, src or None),
                }
                try:
                    data = self._loader._read_manifest(
                        self._loader._resolve_manifest_file(ext_path)
                    )
                    meta["description"] = data.get("description", "")
                    meta["version"] = str(data.get("version", ""))
                    meta["author"] = str(data.get("author", ""))
                    # Same hint the loaded cards carry: is there anything to
                    # configure? Settings are a custom iframe panel — a
                    # disabled extension's asset is not mounted, so the WebUI
                    # hides the button anyway; the flag stays manifest-truth.
                    meta["has_settings"] = bool(
                        (data.get("contributes") or {}).get("ui")
                    )
                except Exception:  # noqa: BLE001
                    pass
                items.append(meta)
        return items

    async def load_extension(

        self,

        extension_path: Path,

        config: Optional[Dict[str, Any]] = None

    ) -> ExtensionBase:

        """Load extension."""

        async with self._lock:

            # Load extension

            extension = await self._loader.load_extension_from_path(extension_path, config)

            # Register extension

            await self._register_extension(extension)

            return extension

    async def _register_extension(self, extension: ExtensionBase) -> None:

        """Register extension."""

        # A successful load retires any recorded failure for this name.
        self._clear_failure(extension.name)

        if extension.name in self._extensions:

            raise ExtensionError(f"Extension {extension.name} is already registered")

        self._extensions[extension.name] = extension

        logger.debug(f"Registered extension: {extension.name}")

    @staticmethod
    def _ext_source(extension: Any) -> Optional[str]:
        """Get the source layer (system/user) of an extension."""
        m = getattr(extension, "_manifest", None) or getattr(extension, "manifest", None)
        if m is not None:
            src = getattr(m, "source", None)
            if src:
                return src
        return None

    async def enable_extension(self, extension_name: str) -> bool:

        """Enable extension."""

        extension = self.get_extension(extension_name)

        if not extension:

            logger.warning(f"Extension not found: {extension_name}")

            return False

        if extension.status == ExtensionStatus.RUNNING:

            logger.warning(f"Extension {extension_name} is already running")

            return True

        try:

            # v2: persist enable (V9: layer-precise)
            if self._state_store:
                self._state_store.set_enabled(
                    extension_name, source=self._ext_source(extension),
                )

            # Set project data directory so extensions don't need to guess it

            project_root = self._discovery._project_root

            if project_root:
                from boarai.core.runtime import resolve_data_dir
                extension.set_data_dir(resolve_data_dir(project_root))

            # Notify extension that server is ready

            # (before start() so middleware can be registered before router freeze)

            try:

                from boarai.core.server import get_server_manager

                sm = get_server_manager()

                await extension.on_server_ready({

                    'host': sm.host if sm else '0.0.0.0',

                    'port': sm.port if sm else 26620,

                })

            except Exception as e:

                logger.debug(

                    f"Extension {extension_name}: on_server_ready skipped "

                    f"(server may not be available yet): {e}"

                )

            # Start extension

            await extension.start()

            extension.set_status(ExtensionStatus.RUNNING)

            # Auto-register source from manifest (if declared)

            manifest = extension.manifest

            if manifest.source_id:

                from .source_registry import get_source_registry

                get_source_registry().register(

                    manifest.source_id,

                    manifest.source_display or manifest.source_id,

                )

            # main.py logs the per-extension enable result at INFO; this
            # line would only duplicate it.
            logger.debug(f"Enabled extension: {extension_name}")

            # Publish event

            await publish_event(

                EventType.EXTENSION_ENABLED,

                {"extension": extension_name},

                "extension_manager"

            )

            return True

        except Exception as e:

            error_msg = f"Failed to enable extension {extension_name}: {e}"

            logger.error(error_msg)

            extension.set_status(ExtensionStatus.ERROR, str(e))

            # Publish error event

            await publish_event(

                EventType.EXTENSION_ERROR,

                {"extension": extension_name, "error": str(e)},

                "extension_manager"

            )

            return False

    async def disable_extension(self, extension_name: str, persist: bool = True) -> bool:

        """Disable extension."""

        extension = self.get_extension(extension_name)

        if not extension:

            # Discovered-but-never-loaded extension (trust gate / manifest /
            # syntax failure): persist the disabled state so hot discovery
            # skips it — this is the WebUI "stop the retries" action for a
            # failed extension. Layer-precise via the recorded source.
            failed = self._failed.get(extension_name)
            if failed is not None and self._state_store and persist:
                try:
                    self._state_store.set_disabled(
                        extension_name, source=failed.get("source") or "",
                    )
                    self._clear_failure(extension_name)
                    logger.info(
                        f"Disabled failed extension: {extension_name} "
                        f"(persisted, discovery will skip it)")
                    return True
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"Failed to persist disable for '{extension_name}': {e}")

            logger.warning(f"Extension not found: {extension_name}")

            return False

        if extension.status != ExtensionStatus.RUNNING:

            logger.warning(f"Extension {extension_name} is not running")

            return True

        try:

            if persist:
                # v2: persist disable (V9: layer-precise)
                if self._state_store:
                    self._state_store.set_disabled(
                        extension_name, source=self._ext_source(extension),
                    )

            # Auto-unregister source from manifest

            manifest = extension.manifest

            if manifest.source_id:

                from .source_registry import get_source_registry

                get_source_registry().unregister(manifest.source_id)

            # Stop extension

            await extension.stop()

            extension.set_status(ExtensionStatus.STOPPED)

            logger.info(f"Disabled extension: {extension_name}")

            # Publish event

            await publish_event(

                EventType.EXTENSION_DISABLED,

                {"extension": extension_name},

                "extension_manager"

            )

            return True

        except Exception as e:

            error_msg = f"Failed to disable extension {extension_name}: {e}"

            logger.error(error_msg)

            extension.set_status(ExtensionStatus.ERROR, str(e))

            # Publish error event

            await publish_event(

                EventType.EXTENSION_ERROR,

                {"extension": extension_name, "error": str(e)},

                "extension_manager"

            )

            return False

    async def uninstall_extension(self, extension_name: str) -> Dict[str, Any]:

        """Uninstall: stop + unload + move the USER-layer directory to trash."""

        extension = self.get_extension(extension_name)

        if not extension:

            logger.warning(f"Extension not found: {extension_name}")

            return {"ok": False, "code": "not_found",
                    "message": f"extension '{extension_name}' is not loaded"}

        m = getattr(extension, "_manifest", None) or extension.manifest
        source = str(getattr(m, "source", "") or "")
        ext_path = Path(m.path) if getattr(m, "path", None) else None

        async with self._lock:

            try:

                # Stop if running — WITHOUT persisting a disabled state
                # (the directory is about to leave the tree; a persisted
                # 'disabled' entry would poison a future reinstall).

                if extension.status == ExtensionStatus.RUNNING:

                    await self.disable_extension(extension_name, persist=False)

                # Unload extension

                await self._loader.unload_extension(extension)

                # Remove from registry

                self._extensions.pop(extension_name, None)
                self._clear_failure(extension_name)

                # Purge persisted per-name state (disabled + trusted).
                if self._state_store:
                    try:
                        self._state_store.set_enabled(
                            extension_name, source=source or None)
                        self._state_store.set_untrusted(
                            extension_name, source=source or None)
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"State purge on uninstall failed: {e}")

                # Move the user-layer directory to the recycle bin so hot
                # discovery cannot resurrect it.
                trash_id = None
                if source == "user" and ext_path is not None and ext_path.is_dir():
                    from boarai.core.trash import get_trash_manager

                    trash_id = await get_trash_manager().trash_extension(
                        extension_name, ext_path)
                    if trash_id is None:
                        logger.error(
                            f"Uninstall: failed to move '{extension_name}' to "
                            f"trash — extension stopped but directory remains "
                            f"at {ext_path} (watcher may rediscover it)")

                logger.info(
                    f"Uninstalled extension: {extension_name}"
                    + (f" (trashed: {trash_id})" if trash_id else ""))

                # Publish event

                await publish_event(

                    EventType.EXTENSION_UNINSTALLED,

                    {"extension": extension_name, "trash_id": trash_id or ""},

                    "extension_manager"

                )

                return {"ok": True, "name": extension_name,
                        "trash_id": trash_id or ""}

            except Exception as e:

                error_msg = f"Failed to uninstall extension {extension_name}: {e}"

                logger.error(error_msg)

                # Publish error event

                await publish_event(

                    EventType.EXTENSION_ERROR,

                    {"extension": extension_name, "error": str(e)},

                    "extension_manager"

                )

                return {"ok": False, "code": "uninstall_failed",
                        "message": str(e)}

    async def reload_extension(self, extension_name: str) -> bool:

        """Reload extension."""

        extension = self.get_extension(extension_name)

        if not extension:

            logger.warning(f"Extension not found: {extension_name}")

            return False

        # Prefer the loader-set authoritative manifest (carries path). Some
        # legacy extensions override the manifest property without a path.
        _m = getattr(extension, "_manifest", None) or extension.manifest
        extension_path = _m.path
        was_enabled = extension.status == ExtensionStatus.RUNNING

        try:

            # Stop the running extension WITHOUT touching state.json.
            # (V3 fix: reload must never persist a disabled state.)
            if was_enabled:
                try:
                    await extension.stop()
                    extension.set_status(ExtensionStatus.STOPPED)
                except Exception as stop_e:
                    logger.warning(f"Reload stop failed for {extension_name}: {stop_e}")

            # Unload old modules/contributes (does NOT touch the registry)
            await self._loader.unload_extension(extension)

            # Remove the old entry so load_extension can re-register the name.
            # Keep the reference for rollback.
            self._extensions.pop(extension_name, None)

            # Load new code from the same path
            new_extension = await self.load_extension(extension_path)

            # Re-enable (start) so HTTP routes are registered
            if was_enabled:
                await self.enable_extension(extension_name)

            logger.info(f"Reloaded extension: {extension_name}")

            await publish_event(
                EventType.EXTENSION_RELOADED,
                {"extension": extension_name},
                "extension_manager",
            )

            return True

        except Exception as e:
            error_msg = f"Failed to reload extension {extension_name}: {e}"
            logger.error(error_msg)

            # V3 rollback: re-register the old instance and re-enable it so a
            # failed reload leaves the system exactly as before.
            try:
                if self.get_extension(extension_name) is None:
                    self._extensions[extension_name] = extension
                    if was_enabled:
                        await self.enable_extension(extension_name)
                logger.warning(
                    f"Rolled back reload for '{extension_name}' — "
                    f"old instance kept enabled"
                )
            except Exception as rb_e:
                logger.error(f"Reload rollback failed for {extension_name}: {rb_e}")

            await publish_event(
                EventType.EXTENSION_ERROR,
                {"extension": extension_name, "error": str(e)},
                "extension_manager",
            )
            return False

    def get_extension(self, extension_name: str) -> Optional[ExtensionBase]:

        """Get extension by name."""

        return self._extensions.get(extension_name)

    def list_extensions(self) -> List[str]:

        """List all registered extensions."""

        return list(self._extensions.keys())

    def get_extensions_by_type(self, extension_type: str) -> List[ExtensionBase]:

        """Get extensions by type."""

        return [e for e in self._extensions.values() if e.extension_type == extension_type]

    def get_extensions_by_status(self, status: ExtensionStatus) -> List[ExtensionBase]:

        """Get extensions by status."""

        return [e for e in self._extensions.values() if e.status == status]

    async def disable_all_extensions(self) -> Dict[str, bool]:

        """Disable all running extensions (runtime cleanup, NOT persisted)."""

        results = {}

        for extension_name in list(self._extensions.keys()):

            extension = self._extensions[extension_name]

            if extension.status == ExtensionStatus.RUNNING:

                results[extension_name] = await self.disable_extension(
                    extension_name, persist=False)

            else:

                results[extension_name] = True

        return results

    def get_stats(self) -> Dict[str, Any]:

        """Get statistics."""

        type_counts = {}

        for ext_type in ExtensionType:

            type_counts[ext_type.value] = len(self.get_extensions_by_type(ext_type.value))

        status_counts = {}

        for status in ExtensionStatus:

            status_counts[status.value] = len(self.get_extensions_by_status(status))

        return {

            "total_extensions": len(self._extensions),

            "extensions": list(self._extensions.keys()),

            "type_counts": type_counts,

            "status_counts": status_counts,

        }

# Global extension manager instance

_extension_manager: Optional[ExtensionManager] = None

def get_extension_manager(project_root: Optional[Path] = None) -> ExtensionManager:

    """Get the global extension manager instance."""

    global _extension_manager

    if _extension_manager is None:

        _extension_manager = ExtensionManager(project_root=project_root)

        return _extension_manager

    if project_root is not None:
        wanted = Path(project_root).resolve()
        current = _extension_manager._discovery._project_root
        try:
            same = Path(current).resolve() == wanted
        except (OSError, RuntimeError):
            same = False
        if not same:
            logger.warning(
                "ExtensionManager discovery was bound to %s; re-pointing to %s",
                current, wanted,
            )
            _extension_manager._discovery = ExtensionDiscovery(wanted)
            _extension_manager._project_root = wanted

    return _extension_manager
