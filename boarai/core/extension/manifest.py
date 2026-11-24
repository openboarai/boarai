"""
Extension manifest data class for BoarAi extension system (v2).

Parsed from manifest.yaml (``extension.yaml`` is a legacy alias). The v2
manifest adds a declarative ``contributes`` model: instead of a fixed
single extension_type, an extension declares exactly which capabilities it
contributes (tools, routes, hooks, hook_points, services, mcp_servers).
Core mount points consume this declaration — this is what makes "one
manifest, many capabilities" possible. (channels/skills contribution kinds
are not implemented; channel identity is declared via the manifest's
source_id/source_display fields instead.)
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class ContributedTool:
    """A tool contributed by an extension."""

    id: str
    handler: str  # "code.py#func" — sync or async func(ctx, **kwargs)
    description: str = ""
    schema: Optional[Dict[str, Any]] = None  # parameters {name: {type, desc, ...}}
    security_profile: str = "none"  # none/file_read/file_write/file_delete/shell
    path_param: Optional[str] = None
    shell_param: Optional[str] = None
    suggested_category: str = "safe"  # readonly/safe/dangerous
    compress_strategy: str = "default"
    timeout: float = 30.0

@dataclass
class ContributedRoute:
    """An HTTP route contributed by an extension."""

    method: str  # GET/POST/PUT/DELETE/...
    path: str  # "/summary" or "/static/{path:.*}"
    handler: str  # "code.py#func" — async func(request) -> web.Response
    description: str = ""
    use_prefix: bool = True  # mount at /api/ext/<id><path>

@dataclass
class ContributedHook:
    """A hook handler bound to a core or extension hook point."""

    event: str  # "core.tool.pre_execute" or "ext.<other>.<event>"
    handler: str  # "code.py#func" — async func(payload) -> result
    order: int = 0

@dataclass
class ContributedHookPoint:
    """A hook point THIS extension declares (P6) for others to bind."""

    name: str  # short name -> ext.<id>.<name>
    semantics: str = "observe"  # observe/blockable/transform
    description: str = ""
    visibility: str = "public"  # public/private

@dataclass
class ContributedService:
    """Service surface THIS extension exposes to other extensions."""

    methods: List[str] = field(default_factory=list)


@dataclass
class ContributedMCPServer:
    """An external MCP server bridged in by this extension."""

    name: str                        # short id, snake_case (tool name prefix)
    command: str                     # executable (stdio transport)
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None
    timeout: float = 30.0            # per JSON-RPC call timeout
    tool_timeout: float = 120.0      # per tool-call execution timeout

@dataclass
class ContributedUIAction:
    """A button in this extension's settings panel."""

    id: str
    handler: str
    label: str = ""
    label_i18n: Dict[str, str] = field(default_factory=dict)
    icon: str = ""
    style: str = "default"  # primary | default | danger
    confirm: str = ""       # optional confirmation text shown before running
    timeout: float = 15.0   # seconds; long work must return fast + poll


@dataclass
class ContributedUIPanel:
    """Panel-level metadata for this extension's settings dialog."""

    title: str = ""
    title_i18n: Dict[str, str] = field(default_factory=dict)
    icon: str = ""
    kind: str = "custom"  # only "custom" is valid
    custom_url: str = ""
    custom_height: int = 480


@dataclass
class ContributedUI:
    """The settings surface THIS extension contributes to the WebUI."""

    panel: ContributedUIPanel = field(default_factory=ContributedUIPanel)
    actions: List[ContributedUIAction] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "panel": {
                "title": self.panel.title,
                "title_i18n": self.panel.title_i18n,
                "icon": self.panel.icon,
                "kind": self.panel.kind,
                "custom_url": self.panel.custom_url,
                "custom_height": self.panel.custom_height,
            },
            "actions": [
                {
                    "id": a.id, "handler": a.handler, "label": a.label,
                    "label_i18n": a.label_i18n, "icon": a.icon,
                    "style": a.style, "confirm": a.confirm,
                    "timeout": a.timeout,
                }
                for a in self.actions
            ],
        }


@dataclass
class Contributes:
    """Declarative capability list of an extension."""

    tools: List[ContributedTool] = field(default_factory=list)
    routes: List[ContributedRoute] = field(default_factory=list)
    hooks: List[ContributedHook] = field(default_factory=list)
    hook_points: List[ContributedHookPoint] = field(default_factory=list)
    services: List[ContributedService] = field(default_factory=list)
    mcp_servers: List[ContributedMCPServer] = field(default_factory=list)
    ui: Optional[ContributedUI] = None

    @property
    def has_any(self) -> bool:
        return bool(
            self.tools or self.routes or self.hooks or self.hook_points
            or self.services or self.mcp_servers or self.ui
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tools": [
                {
                    "id": t.id, "handler": t.handler,
                    "description": t.description, "schema": t.schema,
                    "security_profile": t.security_profile,
                    "path_param": t.path_param, "shell_param": t.shell_param,
                    "suggested_category": t.suggested_category,
                    "compress_strategy": t.compress_strategy,
                    "timeout": t.timeout,
                }
                for t in self.tools
            ],
            "routes": [
                {
                    "method": r.method, "path": r.path, "handler": r.handler,
                    "description": r.description, "use_prefix": r.use_prefix,
                }
                for r in self.routes
            ],
            "hooks": [
                {"event": h.event, "handler": h.handler, "order": h.order}
                for h in self.hooks
            ],
            "hook_points": [
                {
                    "name": p.name, "semantics": p.semantics,
                    "description": p.description, "visibility": p.visibility,
                }
                for p in self.hook_points
            ],
            "services": [{"methods": s.methods} for s in self.services],
            "mcp_servers": [
                {
                    "name": m.name, "command": m.command, "args": m.args,
                    "env": m.env, "cwd": m.cwd, "timeout": m.timeout,
                    "tool_timeout": m.tool_timeout,
                }
                for m in self.mcp_servers
            ],
            "ui": self.ui.to_dict() if self.ui is not None else None,
        }

@dataclass
class ExtensionManifest:
    """Extension manifest data class."""

    name: str
    version: str
    description: str = ""
    extension_type: str = "plugin"  # channel/plugin/tool/skill/mcp (primary)

    # Optional fields
    author: str = ""
    enabled: bool = True
    dependencies: List[str] = field(default_factory=list)
    pip_dependencies: List[str] = field(default_factory=list)
    min_boarai_version: Optional[str] = None
    config_defaults: Dict[str, Any] = field(default_factory=dict)
    provides: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    source_id: str = ""
    source_display: str = ""

    # v2: declarative capability list
    contributes: Contributes = field(default_factory=Contributes)

    # Runtime fields (not from YAML)
    path: Optional[Path] = None
    source: str = ""  # "system" / "user"

    @classmethod
    def from_yaml(cls, yaml_data: Dict[str, Any], path: Optional[Path] = None) -> "ExtensionManifest":
        """Create manifest from YAML data."""
        contributes = _parse_contributes(yaml_data.get("contributes", {}))
        manifest = cls(
            name=yaml_data["name"],
            version=yaml_data["version"],
            description=yaml_data.get("description", ""),
            extension_type=yaml_data.get("extension_type", "plugin"),
            author=yaml_data.get("author", ""),
            enabled=yaml_data.get("enabled", True),
            dependencies=yaml_data.get("dependencies", []),
            pip_dependencies=yaml_data.get("pip_dependencies", []),
            min_boarai_version=yaml_data.get("min_boarai_version"),
            config_defaults=yaml_data.get("config_defaults", {}) or {},
            provides=yaml_data.get("provides", []),
            permissions=yaml_data.get("permissions", []),
            source_id=yaml_data.get("source_id", ""),
            source_display=yaml_data.get("source_display", ""),
            contributes=contributes,
        )
        manifest.path = path
        return manifest

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "extension_type": self.extension_type,
            "description": self.description,
            "author": self.author,
            "enabled": self.enabled,
            "dependencies": self.dependencies,
            "pip_dependencies": self.pip_dependencies,
            "min_boarai_version": self.min_boarai_version,
            "config_defaults": self.config_defaults,
            "provides": self.provides,
            "permissions": self.permissions,
            "contributes": self.contributes.to_dict(),
        }

def _parse_contributes(data: Any) -> Contributes:
    """Parse the contributes section (defensive against missing/malformed)."""
    if not isinstance(data, dict):
        return Contributes()
    c = Contributes()
    known_kinds = (
        "tools", "routes", "hooks", "hook_points", "services", "mcp_servers", "ui",
    )
    for key in data:
        if key not in known_kinds:
            logger.warning(
                "contributes: unknown kind %r discarded (valid: %s)",
                key, ", ".join(known_kinds),
            )
    # tools
    for item in data.get("tools", []) or []:
        if not isinstance(item, dict) or "id" not in item:
            logger.warning("contributes.tools: item discarded (missing 'id'): %r", item)
            continue
        c.tools.append(
            ContributedTool(
                id=item["id"],
                handler=item.get("handler", ""),
                description=item.get("description", ""),
                schema=item.get("schema"),
                security_profile=item.get("security_profile", "none"),
                path_param=item.get("path_param"),
                shell_param=item.get("shell_param"),
                suggested_category=item.get("suggested_category", "safe"),
                compress_strategy=item.get("compress_strategy", "default"),
                timeout=float(item.get("timeout", 30.0)),
            )
        )
    # routes
    for item in data.get("routes", []) or []:
        if not isinstance(item, dict) or "method" not in item or "path" not in item:
            logger.warning(
                "contributes.routes: item discarded (missing 'method'/'path'): %r", item,
            )
            continue
        c.routes.append(
            ContributedRoute(
                method=str(item["method"]).upper(),
                path=item["path"],
                handler=item.get("handler", ""),
                description=item.get("description", ""),
                use_prefix=bool(item.get("use_prefix", True)),
            )
        )
    # hooks
    for item in data.get("hooks", []) or []:
        if not isinstance(item, dict) or "event" not in item:
            logger.warning("contributes.hooks: item discarded (missing 'event'): %r", item)
            continue
        c.hooks.append(
            ContributedHook(
                event=item["event"],
                handler=item.get("handler", ""),
                order=int(item.get("order", 0)),
            )
        )
    # hook_points (P6)
    for item in data.get("hook_points", []) or []:
        if not isinstance(item, dict) or "name" not in item:
            logger.warning(
                "contributes.hook_points: item discarded (missing 'name'): %r", item,
            )
            continue
        c.hook_points.append(
            ContributedHookPoint(
                name=item["name"],
                semantics=item.get("semantics", "observe"),
                description=item.get("description", ""),
                visibility=item.get("visibility", "public"),
            )
        )
    # services
    for item in data.get("services", []) or []:
        if not isinstance(item, dict):
            logger.warning("contributes.services: item discarded (not a mapping): %r", item)
            continue
        c.services.append(
            ContributedService(methods=list(item.get("methods", []) or []))
        )
    # mcp_servers
    for item in data.get("mcp_servers", []) or []:
        if not isinstance(item, dict) or "command" not in item:
            logger.warning(
                "contributes.mcp_servers: item discarded (missing 'command'): %r", item,
            )
            continue
        name = str(item.get("name", "")).strip()
        if not name or " " in name:
            # server id becomes a tool-name prefix — enforce snake_case
            logger.warning(
                "contributes.mcp_servers: item discarded (name %r must be a "
                "non-empty snake_case tool-name prefix)", name,
            )
            continue
        c.mcp_servers.append(
            ContributedMCPServer(
                name=name,
                command=str(item["command"]),
                args=[str(a) for a in (item.get("args", []) or [])],
                env={str(k): str(v) for k, v in (item.get("env", {}) or {}).items()},
                cwd=item.get("cwd"),
                timeout=float(item.get("timeout", 30.0)),
                tool_timeout=float(item.get("tool_timeout", 120.0)),
            )
        )
    # ui (settings panel metadata + action buttons)
    ui_raw = data.get("ui")
    if ui_raw is not None:
        if not isinstance(ui_raw, dict):
            logger.warning("contributes.ui: discarded (not a mapping): %r", ui_raw)
        else:
            # One blanket guard: a malformed number in the panel must discard
            # the UI section, never fail the whole manifest parse (which would
            # stop the extension from loading at all).
            try:
                c.ui = _parse_ui(ui_raw)
            except Exception as e:  # noqa: BLE001
                logger.warning("contributes.ui: discarded (%s)", e)
    return c


def _str_map(raw: Any) -> Dict[str, str]:
    """A language map (``{"zh": "...", "en": "..."}``); {} when malformed."""
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _parse_ui(data: Dict[str, Any]) -> ContributedUI:
    """Parse ``contributes.ui`` (defensive against missing/malformed keys)."""
    ui = ContributedUI()
    panel = data.get("panel")
    if isinstance(panel, dict):
        ui.panel = ContributedUIPanel(
            title=str(panel.get("title", "") or ""),
            title_i18n=_str_map(panel.get("title_i18n")),
            icon=str(panel.get("icon", "") or ""),
            kind=str(panel.get("kind", "custom") or "custom"),
            custom_url=str(panel.get("custom_url", "") or ""),
            # Respect an explicit 0 instead of swallowing it into the default
            # ("or 480" would also rewrite a legitimate 0 / 0.0).
            custom_height=(
                int(panel["custom_height"])
                if panel.get("custom_height") not in (None, "")
                else 480
            ),
        )
    elif panel is not None:
        logger.warning("contributes.ui.panel: discarded (not a mapping): %r", panel)
    for item in data.get("actions", []) or []:
        if not isinstance(item, dict) or not item.get("id"):
            logger.warning(
                "contributes.ui.actions: item discarded (missing 'id'): %r", item
            )
            continue
        ui.actions.append(
            ContributedUIAction(
                id=str(item["id"]),
                handler=str(item.get("handler", "") or ""),
                label=str(item.get("label", "") or ""),
                label_i18n=_str_map(item.get("label_i18n")),
                icon=str(item.get("icon", "") or ""),
                style=str(item.get("style", "default") or "default"),
                confirm=str(item.get("confirm", "") or ""),
                # Same reason as custom_height: do not rewrite an explicit 0.
                timeout=(
                    float(item["timeout"])
                    if item.get("timeout") not in (None, "")
                    else 15.0
                ),
            )
        )
    return ui
