> **English** | [简体中文](extension_zh.md)

# Extension System (core/extension)

Extension discovery, loading, the manifest contract, the capability facade, hook/route/service registries, the tool registry, the channel host, MCP bridging and the skill system.

## Isolation model (one-way layered dependency)

- core never imports extensions; extensions may freely import any `boarai.core.*` (user-owned code).
- The loader's import check is a debug-level audit only: it never warns and never blocks loading.
- The capability set is a standardization boundary, not a security sandbox: overriding core internals is at the developer's own compatibility risk.
- Extensions must not connect to each other directly: broadcasts go over the event bus, request/response over `api.call_service` (namespace-only, zero direct dependency).

## Modules

| File/directory | Responsibility |
|------|----------------|
| `base.py` | `ExtensionBase` ABC + `ExtensionError` |
| `manifest.py` | Manifest dataclasses and defensive parsing |
| `discovery.py` | Two-layer discovery (system / user) |
| `loader.py` | Load/unload/hot-reload, contributes mounting, MCP spawn, unload sweeps |
| `manager.py` | Extension lifecycle management, enable/disable |
| `api.py` | `ExtensionAPI` capability facade (one instance per extension) |
| `state_store.py` | Enable/disable persistence at `<data>/extensions/state.json` |
| `verifier.py` | Static extension verification |
| `scaffold.py` | Extension scaffolding from templates |
| `source_registry.py` | Channel source_id registry |
| `contribution/` | `HookRegistry` / `RouteRegistry` / `ServiceRegistry` + core hook point declarations |
| `tool/` | `ToolRegistry` / `ToolDefinition` / `ToolContext` / `@tool` decorator |
| `channel/` | `ChannelAdapter` contract + `ChannelHost` |
| `mcp/` | `StdioMCPClient` (newline JSON-RPC over a subprocess) |
| `skill/` | SKILL.md skill store/validation/management/evolution/watcher |

## Discovery and directories

- System layer `boar/extensions/<type>/<name>/` (shipped, read-only); user layer `<data>/extensions/<type>/<name>/` (installable/removable); fixed type dir names: channels / plugins / tools / skills / mcp; same name: user overrides system.
- An extension must have a manifest (`manifest.yaml` canonical, `extension.yaml` legacy alias) plus an entry `extension.py`; missing either means it is not discovered.
- The skills type is not loaded by the manifest loader; SkillStore manages it.

## Manifest and contributes

Top-level fields: name / version / description / extension_type(channel|plugin|tool|skill|mcp) / author / enabled / dependencies / pip_dependencies / min_boarai_version / config_defaults / provides / permissions / source_id / source_display + `contributes`.

Valid `contributes` kinds: `tools / routes / hooks / hook_points / services / mcp_servers / ui`. Unknown kinds are discarded with a warning; items missing key fields are discarded; **degrade locally, never fail the whole manifest parse**.

- tool: `id` + `handler: "code.py#func"` + schema + security_profile + path_param/shell_param + suggested_category + compress_strategy + timeout.
- route: method/path/handler/use_prefix (mounted at `/api/ext/<id><path>` by default).
- hook: event (`core.*` or `ext.<other>.*`) + handler + order.
- hook_point: name (auto-prefixed `ext.<id>.<name>`) + semantics(observe|blockable|transform) + visibility.
- mcp_server: name (snake_case, becomes the tool-name prefix) + command/args/env/cwd/timeout/tool_timeout.
- ui: panel (`kind: custom` only — your own iframe page) + action buttons; there is no config_schema — the settings panel is your own page.

## Lifecycle and facade

- Must implement the `manifest` property and the four async methods `initialize(api=None) / start / stop / cleanup`.
- All registration goes through the injected `ExtensionAPI`: register_tool / register_route / register_hook / declare_hook / trigger_hook / register_service / call_service / register_channel / register_command / register_settings* / publish / subscribe / get_config / set_config / static_url / data_dir.
- The facade tracks ownership of dynamically registered tools/commands; unload sweeps them via the loader (routes/hooks/services/middlewares/commands unregistered in reverse order) — extensions must not build a second registry of their own.
- `set_config` atomically persists the private `config.json`; write failures never raise to the caller; it then invokes `on_config_changed` (extensions caching derived state must override it) and publishes `extension.config_changed`; validation failures fall back to defaults without bricking the load.
- Global HTTP middleware goes through `ExtensionBase.register_middleware`; unload cleans it up automatically to prevent stacking.

## Tool registry

- Two definition styles: the `@tool` decorator or a `BaseTool` subclass (self-registers on import); declarative style uses manifest `contributes.tools`.
- The handler's first argument is the injected `ToolContext` (session_id / workspace / run_id / project_root ...).
- Capabilities are self-declared via metadata; core applies policy by metadata, never by hardcoding tool names.
- The registry is an onion-model middleware pipeline guarded by async + sync locks.

## Channel contract

- An adapter implements only `send` (required) plus optional `start(ctx)/stop()`; inbound work enters through exactly two entries: `ctx.submit()` / `ctx.ask()`.
- Division of labour: core owns every run (session mapping, persistence, event mirroring, serialization, always-reply on timeout); the adapter owns only its transport; `reply_token` is an opaque handle passed through untouched.
- Channel identity uses the manifest top-level `source_id/source_display`.

## MCP bridging

Manifest-declared `contributes.mcp_servers` → the loader spawns the subprocess, handshakes, registers tools (names fixed as `mcp_<server>_<tool>`), and on unload unregisters tools and closes clients; one bad server never blocks the rest of the same extension.

## Skill system

- A skill is a `SKILL.md` (Anthropic Agent Skills open standard): frontmatter carries only name + description; the "when to use" semantics live in the description.
- System skills (`boar/extensions/skills/`) are read-only; agent-generated skills (`<data>/extensions/skills/`) are the writable ones.
- Only a one-line index is injected into the prompt (description truncated to 60 chars); full bodies load on demand via the skill view tool.
- Skill deletion goes through the recycle bin (soft delete); there is no timer-driven auto-archiving (design decision: destroying work on a timer is the wrong signal).
