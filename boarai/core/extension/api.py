"""
ExtensionAPI for BoarAi extension system (v2).

The single injected capability facade. Every extension receives an
``ExtensionAPI`` instance via ``initialize(api)``. It exposes a closed
set of capabilities — tools, routes, hooks, hook points, services,
event publishing, extension config read/write (``get_config``/
``set_config``, persisted), and resource access — WITHOUT exposing any
core internals. (MCP servers are bridged by the loader from the
manifest's ``contributes.mcp_servers`` declarations, not via this API.)

Isolation model (v1.5, one-way layered dependency): core/ never imports
extensions; extensions MAY freely import any ``core.*`` module (they are
user-owned code). The loader's import check is a debug-level audit only —
it never warns and never blocks loading.

Note: the capability set is a standardization boundary, not a security
sandbox — developers who override core internals do so at their own
compatibility risk (per confirmed design decision).
"""

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from boarai.core.event import EventType, publish_event

logger = logging.getLogger(__name__)


class ExtensionAPI:
    """Injected capability facade for a single extension instance."""

    def __init__(
        self,
        extension_id: str,
        *,
        event_bus: Any,
        hook_registry: Any,
        route_registry: Any,
        service_registry: Any,
        tool_registry: Any,
        data_dir: Optional[Path] = None,
        static_base: Optional[Path] = None,
        trusted: bool = False,
        extension: Any = None,
    ) -> None:
        """Initialize the API facade."""
        self._id = extension_id
        self._event_bus = event_bus
        self._hooks = hook_registry
        self._routes = route_registry
        self._services = service_registry
        self._tools = tool_registry
        # Ownership tracking for DYNAMICALLY registered tools: the global
        # ToolRegistry has no per-extension index, so without this list a
        # tool registered via api.register_tool (initialize/start) could
        # never be swept on unload — it would leak into the next load and
        # collide ("already registered").
        self._my_tool_names: List[str] = []
        # Ownership tracking for commands registered via api.register_command:
        # the global CommandRegistry has no per-extension index, so without
        # this list a command could never be swept on unload and would leak
        # into the next load ("already registered, overwriting").
        self._my_command_names: List[str] = []
        self._data_dir = Path(data_dir) if data_dir else None
        self._static_base = Path(static_base) if static_base else None
        self._trusted = trusted
        self._extension = extension
        # Settings-UI descriptors. The loader routes manifest
        # ``contributes.ui`` through the same register_settings* methods a
        # runtime registration uses, so this is the single storage location.
        self._ui_panel: Dict[str, Any] = {}
        self._ui_actions: Dict[str, Dict[str, Any]] = {}

    # --- Identity / resources ---

    @property
    def extension_id(self) -> str:
        """Get this extension's id (namespacing root)."""
        return self._id

    def data_dir(self) -> Optional[Path]:
        """Get this extension's private data directory."""
        if self._data_dir is None:
            return None
        self._data_dir.mkdir(parents=True, exist_ok=True)
        return self._data_dir

    # --- Extension config (read/write, persisted) ---

    def get_config(self) -> Dict[str, Any]:
        """Get this extension's current merged config (dict copy)."""
        cfg = getattr(self._extension, "_config", None) if self._extension else None
        return dict(cfg) if isinstance(cfg, dict) else {}

    async def set_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Merge ``updates`` into this extension's config and persist it."""
        if not isinstance(updates, dict) or not updates:
            return self.get_config()

        current = self.get_config()
        current.update(updates)

        # Update the live extension instance first
        if self._extension is not None:
            self._extension._config = dict(current)

        # Persist beside this extension's own data, atomically: a crash during
        # the write must never leave a truncated config behind. Best-effort in
        # the sense that a failed write never fails the caller — the in-memory
        # value and the change event still land.
        if self._data_dir is not None:
            try:
                import json

                from boarai.core.runtime import write_text_atomic_async

                self._data_dir.mkdir(parents=True, exist_ok=True)
                await write_text_atomic_async(
                    self._data_dir / "config.json",
                    json.dumps(current, ensure_ascii=False, indent=2),
                )
            except Exception as e:  # noqa: BLE001 — persistence is best-effort
                logger.warning(
                    "Extension %s: config persist failed: %s", self._id, e
                )
        else:
            # Facade built without a data dir (tests / a bad extension id):
            # the config stays in memory only. Debug, not warning — this is a
            # legitimate construction, not a runtime fault.
            logger.debug(
                "Extension %s: no data dir, config kept in memory only", self._id
            )

        # Let the running extension react without a restart or hot reload. The
        # base implementation just rebinds ``_config``; an extension caching
        # derived state (clients, thresholds, paths) overrides it. A raising
        # callback must not fail a write that already succeeded. Looked up with
        # getattr so a duck-typed extension object without the hook degrades
        # silently instead of logging a warning on every save.
        hook = getattr(self._extension, "on_config_changed", None)
        if hook is not None:
            try:
                await hook(dict(current))
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Extension %s: on_config_changed failed: %s", self._id, e
                )

        # Notify listeners (WebUI settings page, other extensions)
        try:
            await publish_event(
                EventType.EXTENSION_CONFIG_CHANGED,
                {"extension": self._id, "config": dict(current)},
                self._id,
            )
        except Exception:  # noqa: BLE001
            pass
        return current

    # --- Settings UI (declared in manifest, or registered at runtime) ---

    def register_settings(
        self,
        fields: Optional[Dict[str, Any]] = None,
        *,
        panel: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Declare (or update) what the WebUI settings dialog shows."""
        if fields is not None:
            logger.warning(
                "Extension %s: register_settings(fields=...) is deprecated and "
                "ignored — settings are a custom iframe panel now "
                "(contributes.ui.panel.kind: custom + boar-sdk.js)", self._id,
            )
        if isinstance(panel, dict):
            self._ui_panel.update(
                {k: v for k, v in panel.items() if v is not None}
            )

    def register_settings_action(
        self,
        action_id: str,
        handler: Callable[..., Any],
        *,
        label: str = "",
        label_i18n: Optional[Dict[str, str]] = None,
        icon: str = "",
        style: str = "default",
        confirm: str = "",
        timeout: float = 15.0,
    ) -> str:
        """Add a button to this extension's settings panel."""
        aid = str(action_id or "").strip()
        if not aid:
            raise ValueError("action_id must be a non-empty string")
        if not callable(handler):
            raise TypeError(f"handler for settings action '{aid}' is not callable")
        try:
            timeout_f = float(timeout)
        except (TypeError, ValueError):
            timeout_f = 15.0
        self._ui_actions[aid] = {
            "id": aid,
            "label": str(label or ""),
            "label_i18n": dict(label_i18n or {}),
            "icon": str(icon or ""),
            "style": str(style or "default"),
            "confirm": str(confirm or ""),
            "timeout": min(max(timeout_f, 1.0), 120.0),
            "handler": handler,
        }
        return aid

    def unregister_settings_action(self, action_id: str) -> bool:
        """Remove a settings action button."""
        return self._ui_actions.pop(str(action_id), None) is not None

    def settings_ui(self) -> Dict[str, Any]:
        """This extension's registered UI descriptors (core/API consumption)."""
        return {
            "panel": dict(self._ui_panel),
            "actions": [dict(a) for a in self._ui_actions.values()],
        }

    def static_url(self, path: str) -> str:
        """Build a URL for a file under this extension's static/ directory."""
        if not self._static_base:
            raise ValueError("This extension has no static/ directory")
        return f"/static/{self._id}/{path.lstrip('/')}"

    # --- Broadcast channel ---

    async def publish(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Publish a broadcast event on the core event bus."""
        await publish_event(event_type, payload, self._id)

    async def subscribe(self, event_type: str, handler) -> None:
        """Subscribe to a core event (notifications only)."""
        await self._event_bus.subscribe(event_type, handler)

    async def unsubscribe(self, event_type: str, handler) -> None:
        """Unsubscribe from a core event."""
        await self._event_bus.unsubscribe(event_type, handler)

    # --- Hooks (P3 core points + P6 extension-declared points) ---

    def declare_hook(self, name: str, semantics: str,
                     description: str = "", visibility: str = "public") -> str:
        """Declare a hook point owned by THIS extension (P6)."""
        full = f"ext.{self._id}.{name}"
        self._hooks.declare(
            full, semantics, owner=self._id,
            description=description, visibility=visibility,
        )
        return full

    def undeclare_hook(self, name: str) -> bool:
        """Remove a hook point declared by this extension."""
        return self._hooks.undeclare(f"ext.{self._id}.{name}", self._id)

    def register_hook(self, hook: str, handler: Callable[..., Any],
                      order: int = 0) -> str:
        """Bind a handler to a hook point."""
        return self._hooks.register(hook, handler, owner=self._id, order=order)

    async def trigger_hook(self, name: str, payload: Any = None) -> Any:
        """Trigger a hook point declared by this extension."""
        full = f"ext.{self._id}.{name}"
        return await self._hooks.trigger(full, payload)

    # --- Routes (hot add/remove, no core restart) ---

    def register_route(self, method: str, path: str, handler: Callable[..., Any],
                       description: str = "", use_prefix: bool = True) -> str:
        """Register an HTTP route."""
        return self._routes.register(
            method, path, handler, self._id,
            description=description, use_prefix=use_prefix,
            trusted=self._trusted,
        )

    def unregister_route(self, method: str, path: str,
                         use_prefix: bool = True) -> bool:
        """Remove an HTTP route registered by this extension."""
        return self._routes.unregister(method, path, self._id, use_prefix=use_prefix)

    # --- Message channels (external platforms: WeChat / Feishu / DingTalk …) ---

    async def register_channel(
        self,
        adapter: Any,
        *,
        source_id: Optional[str] = None,
        agent_name: str = "default",
        run_timeout: Optional[float] = None,
        error_reply: Optional[str] = None,
    ) -> Any:
        """Register a message adapter and get its :class:`ChannelContext`."""
        from .channel import DEFAULT_ERROR_REPLY, DEFAULT_RUN_TIMEOUT, get_channel_host

        return await get_channel_host().register(
            self._id,
            adapter,
            source_id=source_id,
            agent_name=agent_name,
            run_timeout=DEFAULT_RUN_TIMEOUT if run_timeout is None else run_timeout,
            error_reply=DEFAULT_ERROR_REPLY if error_reply is None else error_reply,
        )

    async def unregister_channel(self) -> int:
        """Stop and remove every channel this extension registered."""
        from .channel import get_channel_host

        return await get_channel_host().unregister(self._id)

    def list_my_channels(self) -> List[Dict[str, Any]]:
        """This extension's registered channels (introspection)."""
        from .channel import get_channel_host

        return [
            entry for entry in get_channel_host().list_channels()
            if entry["extension_id"] == self._id
        ]

    # --- Tools ---

    async def register_tool(self, definition: Any, handler: Callable[..., Any]) -> Any:
        """Register a tool with the core ToolRegistry."""
        result = await self._tools.register_tool(definition, handler)
        name = getattr(definition, "name", None)
        if name and name not in self._my_tool_names:
            self._my_tool_names.append(name)
        return result

    async def unregister_tool(self, tool_name: str) -> bool:
        """Remove a tool registered by this extension."""
        if tool_name in self._my_tool_names:
            self._my_tool_names.remove(tool_name)
        return await self._tools.unregister_tool(tool_name)

    # --- Chat commands (the in-session command domain) ---

    def register_command(
        self,
        name: str,
        handler: Optional[Callable] = None,
        description: str = "",
        *,
        aliases: Optional[List[str]] = None,
        kind: str = "handler",
        template: str = "",
    ) -> None:
        """Register an in-session (chat) command contributed by this extension."""
        from boarai.core.command import get_command_registry

        get_command_registry().register(
            name,
            handler,
            description,
            source=self._id,
            aliases=aliases,
            kind=kind,
            template=template,
        )
        if name not in self._my_command_names:
            self._my_command_names.append(name)

    def unregister_command(self, name: str) -> bool:
        """Remove a command THIS extension registered."""
        from boarai.core.command import get_command_registry

        registry = get_command_registry()
        if name in self._my_command_names:
            self._my_command_names.remove(name)
        cmd = registry.get(name)
        if cmd is not None and cmd.source != self._id:
            return False
        return registry.unregister(name)

    def unregister_my_commands(self) -> None:
        """Remove every command this facade still owns (unload sweep)."""
        from boarai.core.command import get_command_registry

        registry = get_command_registry()
        for name in list(self._my_command_names):
            self._my_command_names.remove(name)
            cmd = registry.get(name)
            if cmd is not None and cmd.source != self._id:
                continue  # taken over by someone else - not ours to drop
            try:
                registry.unregister(name)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"Command sweep failed for '{name}': {e}")

    # --- Bulk teardown (unload fallbacks) ---

    def unregister_all_routes(self) -> int:
        """Remove EVERY route owned by this extension."""
        return self._routes.unregister_all_for(self._id)

    async def unregister_my_tools(self) -> None:
        """Remove every tool this facade registered (dynamic- registration sweep on unload)."""
        for name in list(self._my_tool_names):
            try:
                await self._tools.unregister_tool(name)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"Tool sweep failed for '{name}': {e}")
        self._my_tool_names.clear()

    # --- Services (extension-to-extension request/response) ---

    def register_service(self, methods: Dict[str, Callable[..., Any]]) -> None:
        """Expose service methods callable by other extensions."""
        self._services.register_service(self._id, methods)

    def unregister_service(self) -> bool:
        """Remove all services exposed by this extension."""
        return self._services.unregister_service(self._id)

    async def call_service(self, extension_id: str, method: str,
                           params: Any = None, timeout: float = 5.0) -> Any:
        """Call a service method on another extension."""
        from .service_registry import ServiceError, ServiceNotFoundError
        return await self._services.call(
            extension_id, method, params, timeout=timeout,
        )

    # --- Introspection (for the extension itself) ---

    def list_my_routes(self) -> List[Dict[str, Any]]:
        return self._routes.get_extension_routes(self._id)

    def list_my_hook_bindings(self) -> List[Dict[str, Any]]:
        points = self._hooks.list_points()
        return [p for p in points if p["owner"] == self._id]
