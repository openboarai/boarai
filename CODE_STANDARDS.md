> **English** | [简体中文](CODE_STANDARDS_zh.md)

# BoarAI Code Standards (derived from the codebase)

> **Audience**: everyone — human or agent — contributing code to BoarAI. Rules come in three tiers: **[Hard boundary]** violation means rework; **[Convention]** new code must comply; **[Style]** keep things consistent.

---

## 1. Repository layout: where code goes

| Purpose | Path | Notes |
|---------|------|-------|
| Distribution package / console entry | `boarai/` (`__main__.py`) | PyPI name `boarai`; `main.py` is a thin source-tree wrapper — all three entries (`python main.py` / the `boarai` script / `python -m boarai`) share ONE implementation (`main.py:4-9`) |
| Microkernel | `boarai/core/` | Import prefix is always `boarai.core.*`; the kernel is not a top-level `core` package so the generic name never lands in site-packages (`boarai/__init__.py:5-7`) |
| System extensions (shipped, read-only) | `boar/extensions/{channels,tools,plugins,skills,mcp}/` | The directory name IS the extension type (`core/extension/discovery.py:26-32`) |
| Extension scaffolding templates | `boar/extensions/templates/{hello,full}/` | New extensions copy their skeleton from here |
| Machine prompt assets | `boar/prompts/` | Loaded centrally by `core/prompt/loader.py`; prompt text is never hardcoded in py files (`loader.py:4-12`) |
| First-run seed data | `boar/defaults/` | Copied once on first run, never overwritten afterwards (`core/runtime/bootstrap.py:4-12`) |
| Built-in model catalog | `boar/config/models.yaml` | Per-install corrections go to `llm.yaml` in the user data dir, never here (`core/llm/catalog.py:13`) |
| Tests | workspace-level `tests/` | Outside the repo; the runner walks up to find the root (`tests/run.py:41-51`) |
| Runtime user data | `~/.boarai/` (the data-dir anchor) | config/sessions/logs/memory/trash/agents/extensions; **no runtime data may ever be tracked in the repo** |
| Human-readable design docs | `docs/` | Bilingual pairs, named `xxx.md` + `xxx_zh.md` |

**Placement rules (where new features go)**

- Kernel-level infrastructure (cross-subsystem, business-free) → `boarai/core/runtime/` (atomic writes, LRU, background tasks, data anchors all live here).
- The whole LLM chain (protocol/providers/routing/facade) → `boarai/core/llm/`; **no other kernel directory may contain LLM calls**.
- REST endpoints → `boarai/core/api/<domain>.py`, one file per domain, exporting an `XxxAPIHandler + get_xxx_api_handler()` singleton pair (`core/api/__init__.py:8-15`).
- Session/run-loop logic → `boarai/core/session/`; per-turn phases live in `session/run/turn_*.py`, not piled into `manager.py`.
- Prompt assembly/compression → `boarai/core/prompt/`.
- Security guards → `boarai/core/security/`; text file operations → `core/fs/`; recycle bin → `core/trash/`; shell execution backends → `core/shell/`.
- All business tools (file, shell, web, memory, task, skill) → **extensions**, never the kernel (`boarai/core/__init__.py:8`: "All business logic should be implemented as plugins").

---

## 2. Dependency hard boundaries **[Hard boundary]**

| Direction | Rule | Evidence |
|-----------|------|----------|
| `core/` → extensions | **Forbidden** to import or call directly. The whole core tree has zero imports of `boar.extensions` (only strings in log categorization) | `core/logging/manager.py:137` is a string map, not an import; `core/extension/api.py:12-15` states the one-way layered model |
| Extensions → `core/` | **Allowed** to import any `boarai.core.*`, no whitelist; the loader's import check is a debug-level audit that never warns or blocks | `core/extension/api.py:12-19`; example `boar/extensions/tools/file_tools/tools.py:17-23` |
| Extension A → Extension B | **Forbidden** to connect directly; only `api.call_service` (request/response) or the event bus (broadcast) | `core/extension/api.py:412-428`, `core/event/bus.py:1-7` |
| Inside `core/` | Layered direct imports allowed; avoid cycles (heavy modules use function-local lazy imports, e.g. `extension/base.py:122`) | — |

**Kernel third-party whitelist**: `aiohttp` / `pyyaml` / `aiofiles` / `tiktoken` (optional `psutil` — its import must be guarded and degrade when absent). Vendor LLM SDKs (openai/anthropic/langchain...) are forbidden — providers are hand-written on aiohttp (`pyproject.toml:14-23`, `core/llm/providers/base.py:31`). Extension-specific dependencies go in the manifest's `pip_dependencies` (`requirements.txt:20-21`).

---

## 3. Kernel module responsibility table

| Directory | Responsibility | Key symbols |
|-----------|----------------|-------------|
| `core/event/` | Event catalogue + async bus + JSONL audit | `EventType`, `Event`, `EventBus`, `publish_event` |
| `core/config/` | config.yaml parsing/schema/corruption fallback | `ConfigParser`, `BoarAiConfig` |
| `core/runtime/` | Data anchor, project-root anchor, atomic writes, first-run bootstrap, foreground serve, WS forwarding wiring | `get_data_dir`, `resolve_data_path`, `set_project_root`, `write_text_atomic_async`, `copy_defaults` |
| `core/server/` | Unified HTTP+WS server on one port (default 0.0.0.0:26620), dynamic middleware, broadcast | `UnifiedServer`, `broadcast` |
| `core/api/` | Per-domain REST handlers + WS session message handling | `SessionAPIHandler` etc. |
| `core/security/` | PathGuard (path sandbox) / ToolGuard (tool permissions) / AuditLog / access gate / redaction | `SecurityManager`, `get_access_store`, `redact` |
| `core/llm/` | Provider protocol, registration/routing, facade, thinking wire protocol | `LLMProvider`, `LLMRequest/Response/StreamChunk`, `LLMError` |
| `core/session/` | Session/message/run lifecycle; the `run/` execution chain; `thinking/` reasoning stream | `SessionManager`, `RunBroadcaster`, turn_* phase files |
| `core/prompt/` | Three-zone assembly, compression, agent storage (soul.md + agent.yaml), token counting | `PromptAssembler`, `AgentStore` |
| `core/extension/` | Discovery/loading/manifest/API facade/hook-route-service registries/tool registry/channel host/MCP client/skill system | `ExtensionBase`, `ExtensionAPI`, `ToolRegistry`, `HookRegistry` |
| `core/memory/` | Memory pod storage, synaptic mesh, distillation, consolidation | `soft_delete_entry` (`manager.py:136`) |
| `core/task/` | Task models/scheduling/executors (ai + script) | `TaskScheduler`, executor/ |
| `core/trash/` | Soft-delete recycle bin + pre-write snapshots; per-type TTLs (sessions 7d, agents/memory/skills 30d, files 48h) | `trash_manager.py:36-45` |
| `core/command/` | In-chat `/command` interception (handler/prompt kinds) + process-management subcommands | `chat.py:1-22` |
| `core/logging/` | Log pipeline, redaction, retention, reader API | `get_logger`, `redact` |
| `core/update/` | Online update (the server never runs pip; it spawns a detached worker) | `service.py:1-19` |
| `core/fs/` | Text read/write + `core.fs.pre_write/post_write` hook wiring | `textops.py:306,333` |
| `core/shell/`, `core/process/`, `core/dependency/`, `core/curator/` | Shell backends, process management, dependency checks, background memory curation | — |

---

## 4. Event system **[Hard boundary]**

1. **Every event travelling over the bus must be registered in `core/event/types.py::EventType`**; magic strings on either side are forbidden (`types.py:1-7` says so explicitly).
2. Naming is `{domain}.{action}` (`run.chunk`, `memory.synapse.created`), grouped by owning subsystem in comments (`types.py:13-177`).
3. Handlers must be `async def` taking the `Event` envelope (`bus.py:19-20`); handlers run in priority order and a single failure must never abort the publish chain (`bus.py:5-7`).
4. High-frequency events (`run.chunk`, `thinking.delta`, `tool.output`) are on the audit skip list; new streaming events of the same kind must be added to `SKIP_EVENT_TYPES` (`bus.py:29-33`).
5. Events needing a WS mirror: register in the forwarding lists of `core/runtime/serve.py::_setup_task_ws_forwarding` (`serve.py:310-376`) and wire the frontend dispatch in `store.js` (see §11). Missing any of the three (EventType / serve forwarding / store.js) means the work is not done.
6. `plugin.*` events are legacy compatibility aliases; new code always uses `extension.*` (`types.py:21-26`).

---

## 5. HTTP / WS dual channel **[Hard boundary]**

- HTTP and WS share one port and one process (`core/server/unified_server.py:1-6`); WS endpoint `/ws`, subprotocol `boarai.v1` (`unified_server.py:174-175,763`).
- Inbound WS messages dispatch by `type` prefix: `session.*` / `tool.*` delegate to `SessionAPIHandler._process_ws_message` (`unified_server.py:795-799`); everything else gets an explicit Unsupported error — new WS actions must register on this chain.
- The same business action shares handler-layer semantics across REST and WS: e.g. run creation exists as REST and as the `session.run.create` WS message with response type `session.run.create.response` (`core/api/session.py:1363-1658`); chat commands share the single `core/command/chat.py` implementation across all three surfaces (WS / REST / ChannelHost) (`chat.py:18-21`).
- **Adding/changing an API requires dual-channel sync**: fields, semantics and broadcast scope must match; a parity contract test watches this (`tests/test_session_service_parity.py`). Streaming is WS-only; there is no SSE.
- Broadcast has two levels: global `UnifiedServer.broadcast` (`unified_server.py:812-825`) and session-scoped (the subscription set kept by the session API; disconnects must clean up via `_discard_ws_client`, `unified_server.py:778-784`). The chosen level goes into the event registration comment (follow the style of `types.py:47-57`).
- Extension routes exist ONLY in the `RouteRegistry` (middleware-backed; hot add/remove without router rebuilds) (`unified_server.py:103-104`); extension middleware uses the dynamic pool (`unified_server.py:97-102`).
- The single source of truth for the listen address is `core/server/unified_server.py` (`BOARAI_HOST`/`BOARAI_PORT`, default `0.0.0.0:26620`); **extensions and their manifests must not declare host/port** (`boar/extensions/channels/webui/extension.yaml:12-14` and `extension.py:35-39` — both comments state this is deliberate). The bind default has a contract test (`tests/test_server_bind_default.py`) that must not be relaxed.

---

## 6. Extension development standards

### 6.1 Directories and discovery

- Two scan layers: system = `boar/extensions/<type>/<name>/`, user = `<data>/extensions/<type>/<name>/`; same name: user overrides system (`discovery.py:20-23,76-89`).
- An extension directory **must** contain a manifest (`manifest.yaml` canonical, `extension.yaml` legacy alias) + an entry `extension.py` (with an `ExtensionBase` subclass); missing either means it is not discovered (`discovery.py:130-144`).
- The skills type bypasses the manifest loader; `SkillStore` manages `SKILL.md` files (`discovery.py:60-64`).
- Per-extension private data dir: `<data>/extensions/data/<ext_id>/`, obtained via `api.data_dir()`; ids containing path separators are rejected outright (`runtime/data_paths.py:73-78`, `api.py:83-88`).

### 6.2 Manifest (declarative capabilities)

Fields are defined by the dataclasses in `core/extension/manifest.py`: top level `name/version/description/extension_type(channel|plugin|tool|skill|mcp)/author/enabled/dependencies/pip_dependencies/min_boarai_version/config_defaults/provides/permissions/source_id/source_display` + `contributes` (`manifest.py:200-226`).

The only valid `contributes` kinds: `tools / routes / hooks / hook_points / services / mcp_servers / ui`; unknown kinds are discarded with a warning, items missing key fields are discarded, but **the overall manifest parse must never fail** (`manifest.py:270-293,384-392`). Parsing/validation code follows the same principle: degrade locally, never brick the load.

- tool item: `id` + `handler: "code.py#func"` + `schema` + `security_profile(none|file_read|file_write|file_delete|shell)` + `path_param/shell_param` + `suggested_category(readonly|safe|dangerous)` + `compress_strategy` + `timeout` (`manifest.py:21-34`).
- route item: `method/path/handler/use_prefix`, mounted at `/api/ext/<id><path>` by default (`manifest.py:36-44`).
- hook item: `event` (`core.*` or `ext.<other>.*`) + `handler` + `order` (`manifest.py:46-52`).
- hook_point item: `name` (auto-prefixed to `ext.<id>.<name>`) + `semantics(observe|blockable|transform)` + `visibility` (`manifest.py:54-61`, `api.py:249-257`).
- mcp_server item: `name` (snake_case, becomes the tool-name prefix) + `command/args/env/cwd/timeout/tool_timeout` (`manifest.py:70-80,361-368`).
- ui item: panel supports only `kind: custom` (your own iframe page) + action buttons (`manifest.py:96-113`).
- There is no `config_schema` — the settings panel is the extension's own page (`templates/full/manifest.yaml:7-9`).

### 6.3 Lifecycle and the API facade

- Must implement the `manifest` property and the four async methods `initialize(api=None) / start / stop / cleanup` (`extension/base.py:96-114`); optional `on_server_ready / on_config_changed / on_event`.
- All registration goes through the injected `ExtensionAPI`: `register_tool / register_route / register_hook / declare_hook / trigger_hook / register_service / call_service / register_channel / register_command / register_settings* / publish / subscribe / get_config / set_config / static_url / data_dir` (all of `api.py`).
- The facade keeps ownership lists for the tools/commands it registered; the loader sweeps them on unload — **extensions must not build a second registry of their own** (`api.py:55-65,397-410`).
- `set_config` persists atomically to the private `config.json`; write failures never raise to the caller; it then calls back `on_config_changed` and publishes `extension.config_changed`; extensions caching derived state must override that callback (`api.py:97-160`).
- Global HTTP middleware goes through `ExtensionBase.register_middleware`; unload cleans it up automatically to prevent stacking (`base.py:120-161`).
- Extension enable/disable state persists at `<data>/extensions/state.json` (`core/extension/state_store.py`).

### 6.4 Tools

- Two definition styles: the `@tool` decorator or a `BaseTool` subclass (self-registers on import) (`extension/tool/base.py:14-36`); the declarative manifest style uses `code.py#func`.
- The handler's first argument is the injected `ToolContext` (`ctx`) carrying `session_id / workspace / run_id / project_root` etc.; relative paths resolve against the session workspace, not the project root (`tool/registry.py:195-203`, `file_tools/tools.py:1-5,66-79`).
- **Capabilities are self-declared via metadata; core applies policy by metadata (security_profile/path_param/shell_param/compress_strategy/suggested_category), never by hardcoded tool names** (`tool/base.py:4-12`).
- `_`-prefixed keys in tool result dicts are internal fields: core strips them before LLM-facing content and persisted messages (`core/session/run_tools.py:18-22,137-141`); file-change records use the convention keys `_path/_change_type/_diff/_additions/_deletions/_existed/_snapshot/_trash_id` (`run_tools.py:25-52`). Large text, diffs and snapshots always go in `_` keys, never in plain keys.
- The execution-side kwargs `_agent_*` / `_approval_granted` / `_run_id` / `_tool_call_id` are a private core→tool channel; do not expose them to the model in tool documentation (`run_tools.py:117-132`).
- MCP tool names are fixed as `mcp_<server>_<tool>` (`extension/loader.py:935-937,1059`).

### 6.5 Channels

- Adapter contract: implement `ChannelAdapter.send` (the only required method); `start(ctx)/stop()` are optional; inbound work enters through exactly two entries: `ctx.submit()` (fire and forget) or `ctx.ask()` (I want the text back) (`extension/channel/adapter.py:4-19,96-116`).
- Iron division of labour: **core owns every run** (session mapping, persistence, event mirroring, serialization, always replying even on timeout); **the adapter owns only its transport**; core never knows which adapters exist (`adapter.py:4-19`).
- `reply_token` is an opaque platform handle passed through untouched, never interpreted (`adapter.py:54-59`).
- Channel identity uses the manifest top-level `source_id/source_display`, not a contributes kind (`manifest.py:8-11`).

### 6.6 Skills

- A skill = a `SKILL.md` (Anthropic Agent Skills open standard); frontmatter carries only `name` + `description`, and the "when to use" semantics live in the description (`extension/skill/types.py:1-11,22-23`).
- System skills (`boar/extensions/skills/`) are read-only; agent-generated skills live in `<data>/extensions/skills/` and only those are writable (`types.py:18-20,55-58`).
- Only a one-line index is injected into the prompt prefix (description truncated to 60 chars); full bodies load on demand via the `skill_view` tool (`types.py:9-10,29-32,60-65`).
- Skill deletion goes through the recycle bin (soft delete; `core/trash/`, TTL_SKILL 30 days, `trash_manager.py:42`); **no timer-driven auto-archiving** — a documented design decision (`core/curator/curator.py:7-24`).

### 6.7 Settings UI

- The panel = a same-origin iframe loading the extension's own `static/settings.html`; include `/static/js/boar-sdk.js` and use the global `boar` object (getConfig/saveConfig/getData/saveData/callAction); auth rides the HttpOnly session cookie — page JS never gets the access key (`boar-sdk.js:1-22,36-41`).
- Action buttons default to a 15s timeout; long work must return fast + poll (`manifest.py:93`).
- Extension-owned copy goes in its own `static/i18n/{en,zh}.json` (see `boar/extensions/tools/web_tools/static/i18n/`).

---

## 7. Data directory and paths **[Hard boundary]**

1. **Single source of truth for the data anchor** is `core/runtime/data_paths.py`: resolution order `--data-dir` > `BOARAI_DATA_DIR` > `Path.home()/.boarai` (`data_paths.py:1-14`). All subsystems resolve paths via `get_data_dir()/resolve_data_path()`; **hardcoding a `.boarai` location is forbidden**; relative `.boarai/...` values in config are resolved against the data dir by `resolve_data_path` (`data_paths.py:81-108`).
2. On Windows, drive-less absolute paths (e.g. `/vol1/...` carried over from NAS configs) must degrade into the data dir instead of silently landing on the current drive root (`data_paths.py:91-101`).
3. **Project-root anchor**: `set_project_root()` pins it once at startup and all subsystems read `get_project_root()`; deriving the project root from `Path.cwd()` or `__file__` levels is forbidden (`runtime/root.py:1-5`). `Path.cwd()` is only allowed in the anchor resolution's fallback branch (`root.py:32`; `discovery.py:37-45` comments explain why a cwd fallback is dangerous).
4. Filesystem operations always use `pathlib.Path`; string-concatenated paths are forbidden; mind separators across platforms (consistent repo-wide).
5. Text writes use `write_text_atomic_async` (`runtime/atomic_write.py`) — a crash must never leave a half file (`api.py:110-123` is the reference example).
6. A legacy in-tree `<repo>/.boarai` is **not auto-migrated**; the system must not contain data-migration logic (`data_paths.py:9-11`, `__main__.py:154-158`).
7. First-run seeding: `copy_defaults` runs BEFORE `create_directories`; once `bootstrap.initialized` is set, defaults are never re-copied; user data is never overwritten (`bootstrap.py:4-12`, `__main__.py:163-171`). A missing runtime config file is the normal state of a fresh install: generate lazily, fall back to defaults on corruption, and never rewrite a user's dirty file (`core/config/parser.py:39-72`).
8. Deletion defaults to soft delete into `core/trash/` (per-type TTLs, `trash_manager.py:36-45`); memory deletion is soft too (`core/memory/synaptic_mesh.py:380-399`); business code must never `shutil.rmtree`/`unlink` user data directly.

---

## 8. LLM and run chain

### 8.1 LLM layer

- A new provider must implement the `LLMProvider` ABC: `name / supported_models / generate(LLMRequest)→LLMResponse / generate_stream(LLMRequest)→AsyncIterator[StreamChunk]` (`llm/protocol.py:200-223`), on aiohttp via `providers/base.py`.
- Errors are unified in `LLMError`; `retryable` is derived from the HTTP status (408/429/5xx) (`protocol.py:267-302`); rejected multimodal input uses the `vision_rejected` flag so the executor retries downgraded — no separate exception type (`protocol.py:299-302`).
- Keys and Base URLs live only in `<data>/config/llm.yaml`, never persisted elsewhere and never logged; the file is on the redaction list (`core/llm/service.py:827`, `core/config/schema.py:189`). Saving must preserve the configuration of unregistered providers verbatim (`service.py:897`).
- Per-model capabilities (vision/thinking/context window) are declared in `llm.yaml`; runtime code reads declarations instead of guessing from model names (`llm/thinking/wire.py:26`, `session/run/model_resolver.py:7,32`).

### 8.2 Hook pipeline

- The 15 core hook points are declared centrally in `extension/contribution/core_hooks.py::CORE_HOOK_POINTS` with one of three semantics: `observe` (notify-only) / `blockable` (veto with `{"blocked": True, "reason": ...}`) / `transform` (payload flows through and may be rewritten) (`core_hooks.py:8-51`). Session message rewrites use `{"data": {"content": ...}}` (`core_hooks.py:43-45`).
- Trigger sites: run lifecycle in `session/run/executor.py:117` + `loop_config.py:256` (2s timeout), tools in `run/turn_tool_round.py:107,162`, LLM in `turn_preflight.py:88` / `turn_stream.py:280` / `turn_finalize.py:431,571`, file writes in `fs/textops.py:306,333`. A new core hook must be registered in `CORE_HOOK_POINTS` and called at a single trigger site — never scattered.
- `llm.transform_output` is opt-in: it only enters the hot path when a binding exists (`core_hooks.py:13-14,38-39`). All hot-path hooks follow this pattern.

### 8.3 Runs and compression

- The run event family: `run.created/chunk/done/completed/error/cancelled/retry/diff/usage/vision_downgrade` (`event/types.py:59-78`); transient events like `run.usage` and `run.vision_downgrade` are **never persisted** (stated in their registration comments, `types.py:71-78`).
- WS coalescing of streamed text: dual bounds of 40ms / 512 chars + a timer-guaranteed drain; changes must preserve the "a stalled stream still drains" invariant (`session/run/broadcaster.py:17-25,49-69`).
- Compression results are written to `session.compressed_summary` and **must never enter history as messages**; runtime system injection is append-only (files involved: `run/compression_gate.py`, `prompt/fragments.py`, `session/manager.py`).
- Empty-reply and approval-timeout fallbacks live in `run/turn_finalize.py:143-184`; fallback messages must carry a `metadata.fallback` marker.

---

## 9. Security and the access gate

- The `SecurityManager` facade aggregates PathGuard/ToolGuard/AuditLog; runtime updates **mutate the same live config object** (the WebUI edits live state, not a detached copy) (`security/__init__.py:36-60`).
- The access key is stored only as a scrypt hash and never displayed; `rotate` is the only way to obtain a new key and it is two-phase (prepare shows the new key while the old one still works; confirm applies it) — the "generate-and-effective-immediately + shown once" self-locking pattern is explicitly forbidden (`__main__.py:54-57,100-102`, `unified_server.py:128-130`).
- The `BOARAI_API_TOKEN` env var must be at least 32 base64url characters or startup refuses (`unified_server.py:78-85`).
- The gate lives at the transport layer only: API, WS and extension routes all pass through it (`unified_server.py:397`); key revocation/rotation must mirror `auth.revoked` to drop every WS client (`event/types.py:95-100`). Public-path changes must update the gate and its tests together (`tests/test_access_gate.py`).
- Tool security routes by the `security_profile` metadata to the matching guard (see §6.4); a guard denial carries `security_block` + `reason` in the result metadata and the upper layer classifies it as a `permission` error kind (`run_tools.py:158-162`).
- Logs and events are redacted before hitting disk (`core/logging/redact.py`, `core/security/redact.py`; the sensitive pattern list is in `config/schema.py:189`).

---

## 10. Coding style **[Convention]**

- **Docstrings**: module-level may span several lines and state responsibility, division of labour and "why"; function/class docstrings are always a **single-line English summary** — no Args/Returns blocks, no long rationale (the whole repo has been trimmed to this; examples `runtime/root.py`, `session/run_tools.py`). Design motivation goes in ordinary `#` comments next to the code being explained (examples `api.py:55-59`, `broadcaster.py:17-23`).
- **Language**: identifiers, docstrings, comments, log messages and exception messages are English; end-user-facing UI copy belongs to frontend i18n.
- **Logging**: `logger = logging.getLogger(__name__)` at module top; `print` is forbidden (sole exception: CLI user output in `main.py` / `__main__.py`). Broad exception catches must carry `# noqa: BLE001` and state the best-effort reason (examples `api.py:124,146,158`).
- **Async**: I/O, event dispatch and file access are all async; blocking calls are isolated with `asyncio.to_thread` (30+ established uses in core). Event handlers and extension lifecycle methods must be `async def`.
- **Types**: new modules use `from __future__ import annotations`; prefer `@dataclass` for data structures (`slots=True` allowed, see `channel/adapter.py:46`); public protocols use ABC + `@abstractmethod`.
- **Naming**: constants UPPER_SNAKE; internal symbols `_`-prefixed; singleton accessors uniformly `get_xxx()` (`get_event_bus/get_tool_registry/get_access_store/...`); manager/registry/store files named `*_manager.py / *_registry.py / *_store.py`; run-loop phase files `turn_*.py`.
- **Defensive parsing**: external input (YAML/JSON/manifests) is validated item by item; bad items are discarded with a warning; the whole load must never raise (`manifest.py:270-393` is the reference implementation).
- **Explicit zero vs missing**: distinguish "not provided" from "explicit 0"; blanket `or default` is forbidden (`manifest.py:414-420,439-445`).
- **Single version source**: `boarai/core/__init__.py::__version__`, read by pyproject via AST; git tags / PyPI releases must stay in lock-step (`core/__init__.py:21-26`, `pyproject.toml:31-34`).

---

## 11. Frontend WebUI standards

- Stack: Vue 3 global build + vue-router + native ES modules, **no bundler**; all assets live in `boar/extensions/channels/webui/static/` (js/css dirs + `index.html`). Third-party libs (vue/lucide/marked/tailwind) are vendored into `static/js/`; no CDN dependency.
- Organisation: `js/store.js` (the single global store + WS channel), `js/app.js` (app shell), `js/components/*` (reusable components), `js/pages/*` (route pages), `js/i18n/{en,zh}.json` + `js/i18n.js`.
- **WS event dispatch**: `store.js` dispatches with a `msg.type === '...'` chain (`store.js:207-423` covers run.* / session.* / fs.changed / auth.revoked / update.* / skill.* / extension.load_failed). A new backend WS-mirrored event **must** be registered in this dispatch, otherwise the frontend side is considered unfinished.
- Request/response WS messages echo their `id` (the error response shape in `unified_server.py:792-805` is the contract).
- i18n: `t()` lookups; language persisted in localStorage with browser-language fallback (`i18n.js:15-36`); new copy is added to both en/zh json files together. Full-screen pages without a navbar (setup/unlock) must carry their own language switcher; labels derived from `t()` use computed properties, never static data.
- Extension settings panels: see §6.7; panel pages must never assume access to the access key.

---

## 12. Testing standards **[Convention]**

- Runner: `py tests\run.py` (optionally with a filter string or a single filename). Pure-stdlib, homegrown; **pytest is not used**; it auto-discovers module-level `test_*` functions in `tests/test_*.py`; async functions run natively via `asyncio.run` (`run.py:19-23,144-158`).
- **One subprocess per test file**: core has ~40 module-level singletons; cross-file pollution cannot be cured by resets, so the subprocess boundary cuts it off once and for all — new test files must not try to share process state (`run.py:8-17`). Within a file, `_RuntimeIsolation` + `_reset_singletons()` still apply (`run.py:76-107`).
- The runner pins `HOME/USERPROFILE` to a per-run temp dir so tests never dirty the developer's real `~/.boarai`; the real home is passed through as `BOARAI_REAL_HOME` for deployment-consistency tests (`run.py:252-293`).
- Shared infrastructure lives in `tests/harness.py`: `tmp_project()` (pins temp project_root/data_dir), `real_data_anchor()` (read-only unpinning), skill file helpers. Test environments are always built through the harness, never with hand-rolled anchors (`harness.py:39-88`).
- Tests live outside the repo (workspace-level `tests/`); import paths are injected by the harness (`harness.py:18-36`).
- **Contract tests pin behaviour**: bind defaults (`test_server_bind_default.py`), HTTP/WS parity (`test_session_service_parity.py`), the data anchor (`test_data_dir_anchor.py`), the gate (`test_access_gate.py`), run-loop invariants (`test_run_loop_invariants.py`), etc. Changing these behaviours requires updating the matching tests, and neither side may unilaterally relax them.
- Failure output rules: tracebacks go to stdout alongside the `[FAIL]` line; a child without a summary line counts as one `[CRASH]` failure — never silently under-report (`run.py:199-202,267-272,317-319`).

---

## 13. Packaging and release

- setuptools namespace discovery collects `boarai*` and `boar*` (`boar/` has directories without `__init__.py`, so namespace mode is required) (`pyproject.toml:39-46`); package-data collects everything and excludes `__pycache__`/`*.pyc` (`pyproject.toml:48-52`).
- `import boarai` must be side-effect free and must not pull in the kernel (`boarai/__init__.py:16-17`); entry modules must never reach the network at import time — dependency installation happens only via the explicit `--ensure-deps` (`main.py:11-13,23-27`, `bootstrap.py:27-35`).
- Under a pip install the resource anchor (`boar/prompts` etc.) is site-packages (the parent of the `boarai` package) while the data anchor stays `~/.boarai` — the two must never be conflated (`__main__.py:6-10`).
- Online update: the server process never runs pip itself; it spawns a detached worker; updates are refused (409) while runs are active (`update/service.py:1-19`).

---

## 14. Task wrap-up (mandatory after every change)

1. Append the day's change record to the workspace `.codebuddy/memory/YYYY-MM-DD.md` (append-only: what changed / why / how verified / impact and conventions) — the directory holds a continuous record; this is an established convention.
2. Update the agent project memory files and their `MEMORY.md` index (memory stores only facts not derivable from code: user preferences, design-decision rationale, external pointers).
3. When adding events / endpoints / hooks, complete the registration chains per §4, §5 and §8 (EventType + serve forwarding + store.js; REST + WS; CORE_HOOK_POINTS + trigger site).
4. Pure read-only tasks are exempt; touching any repo file makes all of the above mandatory.

---

## Appendix: red-line quick reference

| # | Red line | Source |
|---|----------|--------|
| 1 | core must not import/call extensions | `core/extension/api.py:12-15` |
| 2 | No direct extension-to-extension links; use call_service / events | `core/extension/api.py:412-428` |
| 3 | New events: register EventType + WS forwarding + store.js dispatch | `event/types.py:1-7`, `serve.py:310-376`, `store.js:207-423` |
| 4 | API changes sync both REST and WS | `api/session.py:1363+`, `tests/test_session_service_parity.py` |
| 5 | Internal tool fields are `_`-prefixed; large text never in plain keys | `session/run_tools.py:18-52` |
| 6 | Compression results go only to `compressed_summary`, never into message history | `run/compression_gate.py` |
| 7 | Paths only via the data_paths / root anchors; no hardcoded `.boarai`, no cwd derivation | `runtime/data_paths.py:13-14`, `runtime/root.py:3-4` |
| 8 | Deletion defaults to soft delete into the recycle bin; never hard-delete user data | `trash/trash_manager.py`, `memory/manager.py:136` |
| 9 | Kernel third-party whitelist + no vendor LLM SDKs | `pyproject.toml:14-23`, `llm/providers/base.py:31` |
| 10 | Listen address single source is core.server; extensions must not declare host/port | `unified_server.py:28-29`, `webui/extension.yaml:12-14` |
| 11 | Two-phase key rotation; "generate-and-effective + shown once" forbidden | `unified_server.py:128-130` |
| 12 | Function/class docstrings are single-line English summaries | Repo-wide style + `runtime/root.py` example |
