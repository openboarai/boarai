"""

Extension loader for BoarAi extension system.

Handles dynamic loading of extensions with:

- Manifest validation (name, version, type)

- Dependency resolution (other extensions)

- Version compatibility check (min_boarai_version)

- Config schema validation

- pip dependency auto-install

"""

import asyncio

import importlib

import importlib.util

import logging

import os

import re

import subprocess

import sys

from pathlib import Path

from typing import Any, Callable, Dict, List, Optional, Set, Type

from boarai.core.event import EventBus, EventType, get_event_bus, publish_event

from .types import ExtensionType, ExtensionStatus

from .base import ExtensionBase, ExtensionError

from .manifest import (
    ExtensionManifest,
    ContributedTool,
    ContributedRoute,
    ContributedHook,
)

logger = logging.getLogger(__name__)

# Matches the PEP 508 distribution-name prefix of a pip requirement
# (letters/digits/._-) — stdlib-only, avoids importing `packaging`.
_PKG_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def _per_extension_trusted(name: str, source: str) -> bool:
    """state.json per-extension trust list (operator grant via WebUI/API)."""
    try:
        from boarai.core.runtime import get_data_dir

        from .state_store import ExtensionStateStore

        return ExtensionStateStore(Path(get_data_dir())).is_trusted(name, source)
    except Exception:  # noqa: BLE001
        return False


class ExtensionLoader:

    """Dynamic extension loader."""

    def __init__(self, event_bus: Optional[EventBus] = None,
                 strict_import_check: bool = False):
        """Initialize extension loader."""
        self._event_bus = event_bus or get_event_bus()
        self._loaded_modules: Dict[str, Any] = {}
        # MCP bridge state: ext_name -> list of (client, [registered tool names])
        self._mcp_clients: Dict[str, List[Any]] = {}
        # V11: handler modules created by _resolve_handler ("code.py#func"),
        # tracked per extension so unload drops them from sys.modules.
        self._loaded_handler_modules: Dict[str, Set[str]] = {}
        self._strict_import_check = strict_import_check

    async def load_extension_from_path(

        self,

        extension_path: Path,

        config: Optional[Dict[str, Any]] = None

    ) -> ExtensionBase:

        """Load extension from path."""

        extension_path = Path(extension_path)

        if not extension_path.exists():

            raise ExtensionError(f"Extension directory not found: {extension_path}")

        manifest_file = self._resolve_manifest_file(extension_path)

        if not manifest_file.exists():

            raise ExtensionError(f"Extension manifest not found: {manifest_file}")

        try:

            manifest_data = self._read_manifest(manifest_file)

            manifest = ExtensionManifest.from_yaml(manifest_data, extension_path)

            # v2: infer source layer from path (user > system)
            manifest.source = self._infer_source(extension_path)

            # Python extensions execute with the full authority of this
            # process; route/capability restrictions are not a sandbox. Keep
            # non-system code disabled unless the operator explicitly trusts
            # it — either THIS extension (state.json trust list, granted via
            # the WebUI "Trust & load" / POST /api/extensions/<name>/trust)
            # or ALL user extensions (env var, needs a new-terminal restart).
            # Every extension that passes through this loader executes
            # Python as part of initialization.
            if (
                manifest.source != "system"
                and os.getenv("BOARAI_ALLOW_INPROCESS_USER_EXTENSIONS", "") != "1"
                and not _per_extension_trusted(manifest.name, manifest.source)
            ):
                raise ExtensionError(
                    "Loading user extensions executes Python in the BoarAI "
                    "process. Trust THIS extension from the WebUI extensions "
                    "page (Trust & load) or POST /api/extensions/"
                    f"{manifest.name}/trust; alternatively set "
                    "BOARAI_ALLOW_INPROCESS_USER_EXTENSIONS=1 to trust ALL "
                    "user extensions (requires a restart from a NEW "
                    "terminal).",
                    code="gate.inprocess_untrusted",
                )

            # 1. Validate manifest fields

            self._validate_manifest(manifest)

            # 2. Check min_boarai_version

            self._check_version(manifest)

            # 3. Install pip dependencies

            if manifest.pip_dependencies:

                await self._install_pip_deps(manifest)

            # 4. Resolve config: manifest defaults, overridden by the
            # caller-supplied config, then by the persisted config.json
            # (api.set_config writes to
            # <data>/extensions/data/<name>/config.json; persisted values win
            # so settings survive restarts and hot-reloads). There is no
            # schema validation anymore — settings pages are custom iframes
            # and the extension owns its own config semantics.

            resolved_config = (
                dict(config) if config is not None
                else dict(manifest.config_defaults or {})
            )

            try:
                persisted = self._read_persisted_config(manifest.name)
                if persisted:
                    if isinstance(resolved_config, dict):
                        resolved_config = {**resolved_config, **persisted}
                    else:
                        resolved_config = dict(persisted)
            except Exception:  # noqa: BLE001 — config merge is best-effort
                pass

            # 5. Find entry point (extension.py, __init__.py, or tools.py)

            entry_point = None

            for candidate in ("extension.py", "__init__.py", "tools.py"):

                fp = extension_path / candidate

                if fp.exists():

                    entry_point = fp

                    break

            if not entry_point:

                raise ExtensionError(f"No entry point found in {extension_path}")

            # 6. Load module — include the layer source in the module name so
            # a user extension overriding a system one never replaces the
            # system module in sys.modules (it can then be unloaded cleanly).

            module_name = f"boarai_ext_{manifest.source}_{manifest.extension_type}_{manifest.name}"

            spec = importlib.util.spec_from_file_location(module_name, entry_point)

            if spec is None or spec.loader is None:

                raise ExtensionError(f"Failed to load module spec: {entry_point}")

            module = importlib.util.module_from_spec(spec)

            sys.modules[module_name] = module

            spec.loader.exec_module(module)

            self._loaded_modules[module_name] = module

            # 6.5 Import-core post-check (standardization boundary, v2)
            self._check_core_imports(module, manifest.name, extension_path)

            # 7. Find extension class

            extension_class = self._find_extension_class(module, manifest.extension_type)

            if extension_class is None:

                raise ExtensionError(f"No extension class found for {manifest.name}")

            # 8. Create instance

            extension = extension_class(resolved_config)

            extension._manifest = manifest

            await extension.set_event_bus(self._event_bus)

            # 8.1 Build ExtensionAPI facade and inject (v2)
            api = self._build_api(extension, manifest, extension_path)

            extension.set_api(api)

            await self._call_initialize(extension, api)

            # 8.2 Mount declarative contributes (v2)
            await self._mount_contributes(extension, manifest, extension_path)

            # Collection only: warnings run after the whole batch loads, so a
            # legitimate forward binding (A binds a point B declares later)
            # does not look like a typo on every startup.
            self._collect_unbound_hooks(extension, manifest)

            extension.set_status(ExtensionStatus.INITIALIZED)

            logger.info(f"Loaded extension: {manifest.name} v{manifest.version} ({manifest.extension_type})")

            await publish_event(

                EventType.EXTENSION_LOADED,

                {"extension": manifest.name, "version": manifest.version,

                 "type": manifest.extension_type, "path": str(extension_path)},

                "extension_loader"

            )

            return extension

        except Exception as e:

            # Keep the real cause (e.g. SyntaxError has empty str()); chain
            # the original exception so diagnostics stay meaningful.
            detail = f"{type(e).__name__}: {e}" if str(e) else repr(e)
            error_msg = f"Failed to load extension from {extension_path}: {detail}"

            logger.error(error_msg)

            await publish_event(EventType.EXTENSION_ERROR,

                                {"path": str(extension_path), "error": detail},

                                "extension_loader")

            # PROPAGATE the structured code (gate.inprocess_untrusted,
            # manifest.*, syntax.*...): consumers (failure registry, 409
            # responses, backoff classification) key on it, and re-wrapping
            # without it silently downgraded every coded error to a plain
            # message.
            raise ExtensionError(
                error_msg, code=str(getattr(e, "code", "") or "")
            ) from e

    # --- Manifest validation ---

    def _validate_manifest(self, manifest: ExtensionManifest) -> None:

        """Validate required manifest fields."""

        if not manifest.name:

            raise ExtensionError("Extension name is required")

        if not manifest.version:

            raise ExtensionError("Extension version is required")

        if not manifest.extension_type:

            raise ExtensionError("Extension type is required")

        valid_types = [t.value for t in ExtensionType]

        if manifest.extension_type not in valid_types:

            raise ExtensionError(

                f"Invalid extension type: {manifest.extension_type}. "

                f"Valid types: {valid_types}"

            )

    # --- Version compatibility ---

    def _check_version(self, manifest: ExtensionManifest) -> None:

        """Check min_boarai_version against current core version."""

        min_ver = manifest.min_boarai_version

        if not min_ver:

            return

        try:

            from boarai.core import __version__ as core_version

        except ImportError:

            core_version = "1.5.0"

        if self._compare_versions(core_version, min_ver) < 0:

            raise ExtensionError(

                f"Extension '{manifest.name}' requires BoarAi >= {min_ver}, "

                f"current version is {core_version}"

            )

    @staticmethod

    def _compare_versions(v1: str, v2: str) -> int:

        """Compare two semver strings. Returns -1/0/1."""

        def parse(v: str) -> tuple:

            parts = v.split(".")

            return tuple(int(p) if p.isdigit() else 0 for p in parts)

        p1, p2 = parse(v1), parse(v2)

        # Pad to same length

        max_len = max(len(p1), len(p2))

        p1 = p1 + (0,) * (max_len - len(p1))

        p2 = p2 + (0,) * (max_len - len(p2))

        if p1 < p2: return -1

        if p1 > p2: return 1

        return 0

    # --- Pip dependency installation ---

    def _extract_pkg_name(self, dep: str) -> str:
        """Extract the pip distribution name from a requirement string."""
        match = _PKG_NAME_RE.match(dep.strip())
        return match.group(0) if match else dep.strip()

    def _resolve_pip_policy(self) -> str:
        """Return the effective pip_auto_install policy ("off"/"system"/"all")."""
        policy = "system"
        try:
            from boarai.core.config import get_config_parser
            cp = get_config_parser()
            if cp is not None and cp.config is not None:
                raw = getattr(cp.config.plugin, "pip_auto_install", "system")
                if isinstance(raw, str) and raw in ("off", "system", "all"):
                    policy = raw
        except Exception:  # noqa: BLE001
            pass
        return policy

    def _audit_pip_install(self, manifest: ExtensionManifest,
                           missing: List[str], *, ok: bool,
                           detail: str = "") -> None:
        """Record a pip install attempt/result in the security audit log."""
        record = {
            "extension": manifest.name,
            "source": manifest.source or "",
            "extension_type": manifest.extension_type,
            "packages": missing,
            "ok": ok,
            "detail": detail,
        }
        try:
            from boarai.core.security import get_security_manager
            sm = get_security_manager()
            if sm is not None and sm.audit is not None:
                sm.audit.record("pip_install", record)
                return
        except Exception:  # noqa: BLE001
            pass
        logger.info(f"pip install audit (security unavailable): {record}")

    async def _install_pip_deps(self, manifest: ExtensionManifest) -> None:
        """Install missing pip dependencies for an extension (supply-chain guard)."""
        missing = [
            dep for dep in manifest.pip_dependencies
            if not self._is_package_installed(self._extract_pkg_name(dep))
        ]
        if not missing:
            return

        policy = self._resolve_pip_policy()
        allowed = (policy == "all") or (
            policy == "system" and manifest.source == "system"
        )
        if not allowed:
            raise ExtensionError(
                f"Extension '{manifest.name}' (source={manifest.source or 'unknown'}) "
                f"requires missing pip dependencies: {', '.join(missing)}. "
                f"Auto-install is disabled for this layer (policy='{policy}'). "
                f"Install manually, e.g.: {sys.executable} -m pip install "
                f"{' '.join(missing)}, or set plugin.pip_auto_install to 'all'."
            )

        logger.info(f"Installing pip dependencies for '{manifest.name}': {missing}")
        self._audit_pip_install(manifest, missing, ok=True, detail="attempt")

        cmd = [sys.executable, "-m", "pip", "install",
               "--no-input", "--disable-pip-version-check"]
        # Outside a virtualenv, install into the user site-packages instead of
        # polluting the shared interpreter environment.
        if not self._in_virtualenv():
            cmd.append("--user")
        cmd.extend(missing)

        try:
            # Run pip in a worker thread: subprocess is blocking and must not
            # stall the async load flow / event loop.
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True, text=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            self._audit_pip_install(manifest, missing, ok=False, detail="timeout")
            raise ExtensionError(
                f"pip install timed out for '{manifest.name}' dependencies: "
                f"{', '.join(missing)}"
            ) from None
        except FileNotFoundError:
            self._audit_pip_install(manifest, missing, ok=False, detail="pip not found")
            raise ExtensionError(
                f"pip not found; cannot install dependencies for "
                f"'{manifest.name}': {', '.join(missing)}"
            ) from None

        if result.returncode != 0:
            err = (result.stderr or result.stdout or "")[:300]
            self._audit_pip_install(manifest, missing, ok=False, detail=err)
            raise ExtensionError(
                f"pip install failed for '{manifest.name}' dependencies "
                f"{missing}: {err}"
            )
        self._audit_pip_install(manifest, missing, ok=True, detail="installed")
        logger.info(f"pip dependencies installed for '{manifest.name}'")

    @staticmethod
    def _in_virtualenv() -> bool:
        """Check if running inside a virtual environment."""
        return hasattr(sys, "real_prefix") or (
            hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
        )

    @staticmethod
    def _is_package_installed(package_name: str) -> bool:

        """Check if a pip package is installed."""

        try:

            import importlib.metadata as md

            md.distribution(package_name)

            return True

        except (md.PackageNotFoundError, ImportError):

            try:

                import pkg_resources

                pkg_resources.get_distribution(package_name)

                return True

            except Exception:

                return False

        except Exception:

            return False

    # --- Module loading ---

    @staticmethod
    def _resolve_manifest_file(extension_path: Path) -> Path:
        """Return the extension's manifest file path."""
        for name in ("manifest.yaml", "extension.yaml"):
            candidate = extension_path / name
            if candidate.exists():
                return candidate
        return extension_path / "manifest.yaml"

    def _read_manifest(self, manifest_file: Path) -> Dict[str, Any]:

        try:

            import yaml

            with open(manifest_file, 'r', encoding='utf-8') as f:

                data = yaml.safe_load(f)

                if not isinstance(data, dict):

                    raise ExtensionError(f"Invalid manifest format: {manifest_file}")

                return data

        except ImportError:

            raise ExtensionError("PyYAML is required to read extension manifests")

        except ExtensionError:

            raise

        except Exception as e:

            raise ExtensionError(f"Failed to read manifest {manifest_file}: {e}")

    def _find_extension_class(self, module: Any, extension_type: str) -> Optional[Type[ExtensionBase]]:

        # When manifest is an instance attribute (set in __init__) we cannot
        # verify it via __new__(); remember the first such candidate and only
        # fall back to it if no class matches the requested type. This avoids
        # prematurely returning a wrong-type class from a multi-class module.
        fallback: Optional[Type[ExtensionBase]] = None

        for attr_name in dir(module):

            attr = getattr(module, attr_name)

            if isinstance(attr, type) and issubclass(attr, ExtensionBase) and attr is not ExtensionBase:

                try:

                    temp = attr.__new__(attr)

                    mf = temp.manifest

                except (AttributeError, TypeError):

                    # __init__() not called (self._manifest unset) or __new__()
                    # requires arguments — defer decision to the fallback.

                    if fallback is None:

                        fallback = attr

                    continue

                except Exception:

                    # Unexpected error during manifest check — skip safely

                    logger.debug(f"Skipping {attr.__name__}: manifest check failed")

                    continue

                if getattr(mf, "extension_type", None) == extension_type:

                    return attr

        return fallback

    async def _call_initialize(self, extension: ExtensionBase, api: Any) -> None:
        """Call initialize(), adapting to legacy (no-arg) signatures."""
        import inspect
        try:
            sig = inspect.signature(extension.initialize)
            params = list(sig.parameters.values())
            # NOTE: for a bound method inspect.signature() already excludes
            # 'self', so we inspect ALL params — skipping params[1:] again
            # would hide the api param (V-bug fix).
            accepts_api = any(
                p.name == "api"
                or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
                for p in params
            )
        except (TypeError, ValueError):
            accepts_api = False
        if accepts_api:
            await extension.initialize(api)
        else:
            await extension.initialize()

    # --- V2: import boundary + api injection + contributes mounting ---
    #
    # Isolation model (v1.5): ONE-WAY LAYERED DEPENDENCY (relaxed whitelist).
    #   * core/ never imports extensions (kernel stays stable/self-contained).
    #     This is the ONLY hard boundary — enforced by convention + review.
    #   * Extensions may import ANY core.* module directly (user-owned code,
    #     free to reuse core internals). The v1.4 whitelist/blacklist gate
    #     was removed: it treated extensions as untrusted and blocked them
    #     from useful core surfaces, while the real threat (core→ext) is
    #     already prevented by the one-way dependency.
    # The constants below are retained for backward compatibility of tooling
    # that may still reference them; they are no longer enforced at load time.

    # Legacy: sanctioned extension surface (no longer enforced since v1.5).
    _ALLOWED_CORE_PREFIXES = (
        "boarai.core.extension",
        "boarai.core.event",
        "boarai.core.trash",
        "boarai.core.server",
        "boarai.core.task.models",
        "boarai.core.task.timeutil",
    )

    # Legacy: business internals previously flagged (no longer enforced).
    _FLAGGED_CORE_PREFIXES = (
        "boarai.core.llm",
        "boarai.core.session",
        "boarai.core.security",
        "boarai.core.task",
        "boarai.core.prompt",
        "boarai.core.memory",
        "boarai.core.server",
        "boarai.core.command",
        "boarai.core.config",
        "boarai.core.process",
        "boarai.core.dependency",
    )

    def _check_core_imports(self, module: Any, ext_name: str,
                            extension_path: Path) -> None:
        """Post-load check (v1.5: relaxed to a no-op boundary audit)."""
        # v1.5: one-way isolation only — core never imports extensions.
        # Extensions may freely import core.* (user-owned code). This stub
        # just audits the extension's top-level core imports at debug level
        # for visibility; it never warns or blocks loading.
        imports = set()
        for val in getattr(module, "__dict__", {}).values():
            mod_name = getattr(val, "__module__", "")
            if mod_name.startswith("boarai.core."):
                imports.add(mod_name)
        if imports:
            logger.debug(
                f"Extension '{ext_name}' imports core modules: "
                f"{', '.join(sorted(imports))} (allowed under v1.5 one-way "
                f"isolation)."
            )

    def _strict_hooks(self) -> bool:
        """Whether an unbound hook should fail the load (config opt-in)."""
        try:
            from boarai.core.config import get_config_parser

            parser = get_config_parser()
            cfg = getattr(parser, "config", None) if parser else None
            plugin = getattr(cfg, "plugin", None) if cfg else None
            return bool(getattr(plugin, "strict_hooks", False))
        except Exception:  # noqa: BLE001
            return False

    def _collect_unbound_hooks(self, extension: ExtensionBase,
                               manifest: ExtensionManifest) -> List[str]:
        """Record hooks bound to a point nobody has declared (yet)."""
        try:
            from .contribution import get_hook_registry

            pending = get_hook_registry().pending_for(manifest.name)
        except Exception as e:  # noqa: BLE001
            logger.debug("Unbound-hook check skipped for %s: %s", manifest.name, e)
            extension._unbound_hooks = []
            return []
        names = sorted({b.point_name for b in pending})
        extension._unbound_hooks = names
        return names

    async def enforce_hook_binding(self, extension: ExtensionBase,
                                   manifest: ExtensionManifest) -> List[str]:
        """Warn (or, under ``strict_hooks``, roll back and fail) after loading."""
        # Recompute rather than trust the load-time snapshot: between the two
        # moments, a later-loaded extension may have declared the point.
        declared = []
        try:
            from .contribution import get_hook_registry

            registry = get_hook_registry()
            names = sorted(
                {b.point_name for b in registry.pending_for(manifest.name)}
            )
            declared = registry.declared_points()
        except Exception:  # noqa: BLE001
            names = list(getattr(extension, "_unbound_hooks", None) or [])
        extension._unbound_hooks = names
        if not names:
            return []
        logger.warning(
            "Extension '%s' binds %d hook(s) that are not declared and will "
            "never fire: %s. Declared points: %s",
            manifest.name, len(names), ", ".join(names),
            ", ".join(declared) or "(none)",
        )
        if self._strict_hooks():
            try:
                await self._unmount_contributes(extension)
            except Exception as e:  # noqa: BLE001 — best-effort rollback
                logger.error(
                    "Rollback after hook validation failed for '%s': %s",
                    manifest.name, e,
                )
            # Drop the loaded module too: a half-live extension must not keep
            # executing on a later reload under a stale sys.modules entry.
            for module_name in list(self._loaded_modules):
                if module_name.endswith(f"_{manifest.name}"):
                    self._loaded_modules.pop(module_name, None)
                    sys.modules.pop(module_name, None)
            raise ExtensionError(
                f"Extension '{manifest.name}' declares hook(s) with no matching "
                f"point: {', '.join(names)}. Fix the manifest or disable "
                f"plugin.strict_hooks."
            )
        return names

    def _read_persisted_config(self, name: str) -> Dict[str, Any]:
        """Read the persisted config for an extension (api.set_config)."""
        try:
            from boarai.core.runtime import extension_data_dir

            cfg_file = extension_data_dir(name) / "config.json"
        except Exception as e:  # noqa: BLE001 — bad id/data root: no config
            logger.debug(f"Persisted config path for '{name}' unavailable: {e}")
            return {}
        if not cfg_file.is_file():
            return {}
        try:
            import json

            parsed = json.loads(cfg_file.read_text(encoding="utf-8"))
            return parsed if isinstance(parsed, dict) else {}
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Persisted config for '{name}' unreadable: {e}")
            return {}

    @staticmethod
    def _infer_source(extension_path: Path) -> str:
        """Infer the source layer (system/user) from the path."""
        try:
            path = Path(extension_path).resolve()
        except (OSError, RuntimeError):
            return ""
        project_root = None
        try:
            from boarai.core.runtime import get_data_dir, get_project_root

            project_root = get_project_root()
            data_extensions = Path(get_data_dir()).resolve() / "extensions"
            path.relative_to(data_extensions)
            return "user"
        except Exception:
            pass

        # Resolve and compare actual directory boundaries. A substring test
        # would let an arbitrary extension at e.g. /tmp/boar/extensions/evil
        # inherit the system trust level.
        #
        # The __file__ fallback is parents[3]: this file sits at
        # <root>/boarai/core/extension/loader.py. It was left at parents[2]
        # when the R1 package rename (core → boarai.core) added a level, so
        # the computed "system" dir was <root>/boarai/boar/extensions — which
        # never exists. Every bundled extension then inferred as "" and the
        # in-process trust gate rejected the whole set (WebUI included).
        if project_root is None:
            project_root = Path(__file__).resolve().parents[3]
        system_extensions = Path(project_root) / "boar" / "extensions"
        try:
            path.relative_to(system_extensions.resolve())
            return "system"
        except (OSError, ValueError, RuntimeError):
            pass
        return ""

    def _build_api(self, extension: ExtensionBase, manifest: ExtensionManifest,
                   extension_path: Path) -> Any:
        """Create the injected ExtensionAPI facade for an extension."""
        from boarai.core.runtime import extension_data_dir

        from .api import ExtensionAPI
        from .contribution import (
            get_hook_registry,
            get_route_registry,
            get_service_registry,
        )
        from .tool import get_tool_registry

        try:
            data_dir = extension_data_dir(manifest.name)
        except Exception as e:  # noqa: BLE001 — never brick a load over a path
            logger.warning(
                f"Extension '{manifest.name}': private data dir unavailable "
                f"({e}); api.data_dir() returns None and config will not persist"
            )
            data_dir = None
        static_base = extension_path / "static" if (extension_path / "static").exists() else None

        return ExtensionAPI(
            manifest.name,
            event_bus=self._event_bus,
            hook_registry=get_hook_registry(),
            route_registry=get_route_registry(),
            service_registry=get_service_registry(),
            tool_registry=get_tool_registry(),
            data_dir=data_dir,
            static_base=static_base,
            trusted=(manifest.source == "system"),
            extension=extension,
        )

    async def _mount_contributes(self, extension: ExtensionBase,
                                 manifest: ExtensionManifest,
                                 extension_path: Path) -> None:
        """Mount every declared contributes kind."""
        if not manifest.contributes.has_any:
            return
        api = extension.api
        if api is None:
            logger.warning(
                f"Extension '{manifest.name}' declares contributes but has "
                f"no API facade; skipping mount"
            )
            return
        contrib = manifest.contributes

        # Tools (idempotent: skip if already registered by @tool decorator)
        for t in contrib.tools:
            if api._tools.get_tool(t.id) is not None:
                logger.debug(f"Tool '{t.id}' already registered, skipping mount")
                continue
            handler = self._resolve_handler(extension_path, t.handler, manifest.name)
            if handler is None:
                continue
            definition = self._build_tool_definition(t)
            try:
                await api.register_tool(definition, handler)
            except Exception as e:  # noqa: BLE001
                logger.error(f"Failed to register tool '{t.id}': {e}")

        # Routes
        for r in contrib.routes:
            handler = self._resolve_handler(extension_path, r.handler, manifest.name)
            if handler is None:
                continue
            try:
                api.register_route(
                    r.method, r.path, handler,
                    description=r.description, use_prefix=r.use_prefix,
                )
            except Exception as e:  # noqa: BLE001
                logger.error(f"Failed to register route '{r.method} {r.path}': {e}")

        # Hooks (bind to core.* or ext.* points)
        for h in contrib.hooks:
            handler = self._resolve_handler(extension_path, h.handler, manifest.name)
            if handler is None:
                continue
            try:
                api.register_hook(h.event, handler, order=h.order)
            except Exception as e:  # noqa: BLE001
                logger.error(f"Failed to bind hook '{h.event}': {e}")

        # Hook points (P6: this extension declares points for others)
        for p in contrib.hook_points:
            try:
                api.declare_hook(
                    p.name, p.semantics,
                    description=p.description, visibility=p.visibility,
                )
            except Exception as e:  # noqa: BLE001
                logger.error(f"Failed to declare hook point '{p.name}': {e}")

        # Services (request/response surface for other extensions).
        # Each entry may be a method name (resolved from the extension entry
        # module) or a "code.py#func" reference.
        for s in contrib.services:
            methods: Dict[str, Callable[..., Any]] = {}
            for method in s.methods:
                handler = None
                if "#" in method:
                    handler = self._resolve_handler(extension_path, method, manifest.name)
                else:
                    handler = self._resolve_service_method(extension, method)
                if handler is not None:
                    methods[method] = handler
            if methods:
                api.register_service(methods)

        # MCP servers (external tool providers over stdio JSON-RPC).
        # Each server is spawned, initialized, and its tools registered as
        # BoarAi tools (name prefix mcp_<server>_); unmount closes them.
        for m in contrib.mcp_servers:
            try:
                await self._mount_mcp_server(api, m, manifest.name)
            except Exception as e:  # noqa: BLE001 — one bad server never
                logger.error(  # blocks the rest of the extension
                    f"Failed to mount MCP server '{m.name}' "
                    f"(ext {manifest.name}): {e}"
                )

        # Settings UI (panel metadata + action buttons). Manifest declarations
        # go through the SAME api.register_settings* entry points a runtime
        # registration uses, so there is exactly one storage location and one
        # merge rule (a later registration wins per key).
        if contrib.ui is not None:
            ui_dict = contrib.ui.to_dict()
            try:
                api.register_settings(panel=ui_dict["panel"])
            except Exception as e:  # noqa: BLE001
                logger.error(
                    f"Failed to register the settings panel for "
                    f"'{manifest.name}': {e}"
                )
            for a in ui_dict["actions"]:
                handler = self._resolve_handler(
                    extension_path, a["handler"], manifest.name
                )
                if handler is None:
                    continue
                try:
                    api.register_settings_action(
                        a["id"], handler,
                        label=a.get("label", ""),
                        label_i18n=a.get("label_i18n") or {},
                        icon=a.get("icon", ""),
                        style=a.get("style", "default"),
                        confirm=a.get("confirm", ""),
                        timeout=a.get("timeout", 15.0),
                    )
                except Exception as e:  # noqa: BLE001
                    logger.error(
                        f"Failed to register settings action '{a.get('id')}' "
                        f"for '{manifest.name}': {e}"
                    )

        # Static assets (V10 fix): automatically mount /static/<ext_id>/{path:.*}
        # for extensions that ship a static/ directory, so api.static_url()
        # returns a working URL. Skipped if the extension already registered
        # its own static route (e.g. webui uses use_prefix=False /static/).
        static_dir = extension_path / "static"
        if static_dir.is_dir():
            already_mounted = any(
                r.get("original_path", "").startswith("/static")
                for r in api.list_my_routes()
            )
            if not already_mounted:
                async def _serve_static(request: Any, _static_dir=static_dir) -> Any:
                    from aiohttp import web
                    rel = request.match_info.get("path", "")
                    # Normalize URL path: fall back to raw query-free path
                    if not rel:
                        p = request.path
                        marker = f"/static/{manifest.name}/"
                        if p.startswith(marker):
                            rel = p[len(marker):]
                    target = (_static_dir / rel).resolve()
                    try:
                        if not target.is_relative_to(_static_dir.resolve()):
                            return web.Response(status=403)
                    except Exception:  # noqa: BLE001
                        return web.Response(status=400)
                    if not target.is_file():
                        return web.Response(status=404)
                    try:
                        data = await asyncio.to_thread(target.read_bytes)
                        import mimetypes
                        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                        # Extension assets carry no ?v= cache buster (the
                        # iframe URL is the manifest's custom_url verbatim),
                        # so a heuristic-cached copy would silently serve a
                        # stale settings page. Always revalidate.
                        return web.Response(
                            body=data, content_type=ctype,
                            headers={"Cache-Control": "no-cache"},
                        )
                    except Exception:  # noqa: BLE001
                        return web.Response(status=500)

                try:
                    api.register_route(
                        "GET", f"/static/{manifest.name}/{{path:.*}}",
                        _serve_static,
                        description="Automatic static assets (v2)",
                        use_prefix=False,
                    )
                    logger.info(
                        f"Auto-mounted static assets for '{manifest.name}' "
                        f"at /static/{manifest.name}/"
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Failed to auto-mount static for '{manifest.name}': {e}")

    async def _mount_mcp_server(self, api: Any, m: Any, ext_name: str) -> None:
        """Start one contributed MCP server and register its tools."""
        from .mcp import StdioMCPClient

        client = StdioMCPClient(
            command=m.command, args=m.args, env=m.env or None,
            cwd=m.cwd, timeout=m.timeout,
        )
        await client.start()
        tools = await client.list_tools()
        if not tools:
            logger.warning(
                f"MCP server '{m.name}' (ext {ext_name}) exposes no tools"
            )

        registered: List[str] = []
        for tool in tools:
            if not isinstance(tool, dict) or not tool.get("name"):
                continue
            tool_name = str(tool["name"])
            boarai_name = f"mcp_{m.name}_{tool_name}"
            definition = self._mcp_tool_definition(
                boarai_name, tool, tool_timeout=m.tool_timeout,
            )
            handler = self._mcp_tool_handler(client, tool_name, m.tool_timeout)
            try:
                await api.register_tool(definition, handler)
                registered.append(boarai_name)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    f"Failed to register MCP tool '{boarai_name}': {e}"
                )

        self._mcp_clients.setdefault(ext_name, []).append((client, registered))
        logger.info(
            f"MCP server '{m.name}' mounted (ext {ext_name}): "
            f"{len(registered)} tools {registered}"
        )

    def _mcp_tool_definition(self, name: str, tool: Dict[str, Any],
                             tool_timeout: float) -> Any:
        """Build a ToolDefinition from an MCP tool manifest."""
        from .tool import ToolDefinition, ToolParameter

        params: List[Any] = []
        schema = tool.get("inputSchema")
        if isinstance(schema, dict):
            properties = schema.get("properties")
            if isinstance(properties, dict):
                required = set(schema.get("required", []) or [])
                for pname, pdef in properties.items():
                    if not isinstance(pdef, dict):
                        continue
                    params.append(
                        ToolParameter(
                            name=str(pname),
                            type=str(pdef.get("type", "string")),
                            description=str(pdef.get("description", "")),
                            required=pname in required,
                        )
                    )
        desc = str(tool.get("description") or f"MCP tool '{name}'")
        return ToolDefinition(
            name=name,
            description=desc[:200],
            parameters=params,
            security_profile="none",
            suggested_category="dangerous",
            timeout=tool_timeout,
        )

    @staticmethod
    def _mcp_tool_handler(client: Any, tool_name: str,
                          tool_timeout: float) -> Any:
        """Build the async handler forwarding a BoarAi tool call to MCP."""

        async def _handler(ctx: Any, **kwargs: Any) -> Dict[str, Any]:
            import asyncio as _asyncio

            try:
                result = await _asyncio.wait_for(
                    client.call_tool(tool_name, dict(kwargs)),
                    timeout=tool_timeout,
                )
            except Exception as e:  # noqa: BLE001 — surface as tool error
                return {"error": f"MCP tool '{tool_name}' failed: {e}"}
            if result.get("is_error"):
                return {"error": result.get("content") or "MCP tool error"}
            return {"result": result.get("content", "")}

        return _handler

    @staticmethod
    def _find_loaded_module_by_file(target: Path) -> Optional[Any]:
        """Return an already-imported module whose ``__file__`` is ``target``."""
        try:
            target_norm = os.path.normcase(str(target.resolve()))
        except OSError:
            return None
        for mod in list(sys.modules.values()):
            mod_file = getattr(mod, "__file__", None)
            if not mod_file:
                continue
            try:
                if os.path.normcase(str(Path(mod_file).resolve())) == target_norm:
                    return mod
            except OSError:
                continue
        return None

    def _resolve_handler(self, extension_path: Path, reference: str,
                         ext_name: str) -> Optional[Callable[..., Any]]:
        """Resolve "code.py#func" to a callable within the extension dir."""
        if not reference or "#" not in reference:
            logger.error(
                f"Extension '{ext_name}': handler reference '{reference}' "
                f"must be 'module.py#function'"
            )
            return None
        file_part, _, func_part = reference.partition("#")
        if not file_part or not func_part:
            return None
        target = (extension_path / file_part).resolve()
        if not target.exists():
            logger.error(
                f"Extension '{ext_name}': handler file not found: {target}"
            )
            return None

        # Reuse an already-imported module for this exact file instead of
        # blindly re-executing it under a synthetic name. The same file is
        # routinely imported normally too — e.g. an extension's initialize()
        # does `from pkg import tools` to trigger `@tool` self-registration.
        # Executing it a second time here re-fires those import-time side
        # effects (hence spurious "Tool X already registered, skipping"
        # warnings) and, worse, forks the module's global state: config
        # applied to the real module (set_search_engines, feature flags,
        # caches) becomes invisible to this duplicate copy, so a
        # contributes.ui action handler can silently disagree with the tool
        # it is supposed to test.
        mod = self._find_loaded_module_by_file(target)
        if mod is None:
            spec = importlib.util.spec_from_file_location(
                f"boarai_ext_handler_{ext_name}_{func_part}", target
            )
            if spec is None or spec.loader is None:
                return None
            try:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = mod
                spec.loader.exec_module(mod)
                # V11: track handler modules so unload can drop them from
                # sys.modules. Only modules THIS call created are tracked —
                # a reused package module must never be popped on unload,
                # since other live references (e.g. @tool-decorated handlers
                # bound to its globals) still depend on it.
                self._loaded_handler_modules.setdefault(ext_name, set()).add(spec.name)
            except Exception as e:  # noqa: BLE001
                logger.error(f"Failed to resolve handler '{reference}': {e}")
                return None

        try:
            obj = mod
            for attr in func_part.split("."):
                obj = getattr(obj, attr)
            return obj if callable(obj) else None
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to resolve handler '{reference}': {e}")
            return None

    def _resolve_service_method(self, extension: ExtensionBase,
                                method: str) -> Optional[Callable[..., Any]]:
        """Resolve a service method from the extension's own module."""
        module = getattr(extension, "__module__", "")
        mod = sys.modules.get(module)
        if mod is None:
            return None
        attr = getattr(mod, method, None)
        return attr if callable(attr) else None

    @staticmethod
    def _build_tool_definition(t: ContributedTool) -> Any:
        """Build a ToolDefinition from a contributed tool declaration."""
        from .tool import (
            ToolDefinition, ToolParameter,
            CompressStrategy, SecurityProfile, SuggestedCategory,
        )
        params = []
        for pname, p in (t.schema or {}).items():
            if not isinstance(p, dict):
                continue
            params.append(
                ToolParameter(
                    name=pname,
                    type=p.get("type", "string"),
                    description=p.get("description", ""),
                    required=bool(p.get("required", True)),
                    default=p.get("default"),
                    enum=p.get("enum"),
                )
            )
        return ToolDefinition(
            name=t.id,
            description=t.description,
            parameters=params,
            security_profile=t.security_profile,
            path_param=t.path_param,
            shell_param=t.shell_param,
            suggested_category=t.suggested_category,
            compress_strategy=t.compress_strategy,
            timeout=t.timeout,
        )

    async def _unmount_contributes(self, extension: ExtensionBase) -> None:
        """Reverse-mount contributes on unload (hooks → services → routes → tools)."""
        api = extension.api
        manifest = getattr(extension, "_manifest", None)
        if api is None or manifest is None:
            return
        contrib = manifest.contributes
        try:
            # Middlewares registered via ExtensionBase.register_middleware:
            # remove them so hot-reload never stacks stale closures.
            cleanup_mw = getattr(extension, "_cleanup_registered_middlewares", None)
            if callable(cleanup_mw):
                try:
                    cleanup_mw()
                except Exception:  # noqa: BLE001
                    pass
            # Hooks: remove this extension's bindings
            from .contribution import get_hook_registry
            get_hook_registry().unregister_all_for(manifest.name)
            # Hook points declared by this extension
            for p in contrib.hook_points:
                api.undeclare_hook(p.name)
            # Services
            api.unregister_service()
            # Commands: sweep by ownership. Covers api.register_command
            # (facade-tracked) plus any direct registry writes carrying
            # source=extension name, so an unload never leaks a command
            # into the next load.
            try:
                api.unregister_my_commands()
            except Exception:  # noqa: BLE001
                pass
            try:
                from boarai.core.command import get_command_registry
                get_command_registry().unregister_all_for(manifest.name)
            except Exception:  # noqa: BLE001
                pass
            # Channels: stop the adapter's transport and cancel in-flight turns
            # BEFORE its routes/tools are swept — a turn still delivering a
            # reply would otherwise lose the route it answers through.
            try:
                await api.unregister_channel()
            except Exception:  # noqa: BLE001
                logger.debug(f"Channel sweep failed for '{manifest.name}'")
            # Routes — sweep by OWNERSHIP, not by manifest declaration:
            # extensions may register routes dynamically (api.register_route
            # in initialize/start), and a per-declaration loop leaks those
            # into the next load ("Route ... already registered by <self>").
            # unregister_all_for is idempotent and covers both kinds.
            try:
                api.unregister_all_routes()
            except Exception:  # noqa: BLE001
                for r in contrib.routes:
                    try:
                        api.unregister_route(
                            r.method, r.path, use_prefix=r.use_prefix)
                    except Exception:  # noqa: BLE001
                        pass
            # Tools: manifest-declared ids first, then the facade's dynamic
            # registration sweep (api.register_tool tracks ownership).
            for t in contrib.tools:
                try:
                    await api.unregister_tool(t.id)
                except Exception:  # noqa: BLE001
                    pass
            try:
                await api.unregister_my_tools()
            except Exception:  # noqa: BLE001
                pass
            # MCP servers: unregister bridged tools, then close the clients
            for client, registered in self._mcp_clients.pop(manifest.name, []):
                for tool_name in registered:
                    try:
                        await api.unregister_tool(tool_name)
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    await client.close()
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"Failed to unmount contributes for '{manifest.name}': {e}"
            )

    async def unload_extension(self, extension: ExtensionBase) -> bool:

        try:

            if extension.status == ExtensionStatus.RUNNING:

                await extension.stop()

            await extension.cleanup()

            await self._unmount_contributes(extension)

            # Drop module cache so hot-reload can re-import a fresh copy.
            module_keys = [
                key for key, module in list(self._loaded_modules.items())
                if getattr(module, "__file__", None)
                and extension.name in key
            ]
            # Prefer exact module name from manifest type when available.
            manifest = getattr(extension, "_manifest", None)
            if manifest is not None:
                exact = f"boarai_ext_{manifest.source}_{manifest.extension_type}_{manifest.name}"
                if exact not in module_keys:
                    module_keys.append(exact)
            for key in module_keys:
                self._loaded_modules.pop(key, None)
                sys.modules.pop(key, None)

            # V11: drop tracked handler modules (code.py#func) too
            handler_keys = self._loaded_handler_modules.pop(extension.name, set())
            for hkey in handler_keys:
                sys.modules.pop(hkey, None)

            logger.info(f"Unloaded extension: {extension.name}")

            await publish_event(EventType.EXTENSION_UNLOADED,

                                {"extension": extension.name}, "extension_loader")

            return True

        except Exception as e:

            logger.error(f"Failed to unload extension {extension.name}: {e}")

            await publish_event(EventType.EXTENSION_ERROR,

                                {"extension": extension.name, "error": str(e)}, "extension_loader")

            return False

    def get_loaded_modules(self) -> Dict[str, Any]:

        return self._loaded_modules.copy()

class ExtensionLoaderError(ExtensionError):

    pass
