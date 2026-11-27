# Extension Common Spec (read first for every extension type)

> Purpose: `file_read` this file before developing any BoarAI extension, then
> read the type-specific doc.
> Location: `boar/prompts/extension/common.md`

## Mental model: declare + mount + communicate

| Concern | Mechanism | One-liner |
|---|---|---|
| Declare | `manifest.yaml` `contributes` | tell core "what I have" |
| Mount | `initialize(api)` registers handlers | attach capabilities to core call points |
| Communicate | event bus / service call / hook | interact with core & other extensions |

Extensions have **zero direct dependency** on each other — they never import
each other's code, only namespaces.

Dependency direction (v1.5 one-way layering): extensions may freely import
any `core.*`; **core never imports extensions** (the ONE hard boundary).

## Directory layout and two-layer loading

```
my_ext/
├── manifest.yaml          # declaration (required)
├── extension.py           # entry: Extension class (required)
├── code/                  # business code (optional, referenced by contributes handlers)
└── requirements.txt       # own pip deps (optional)
```

Directory name and `name` are both **snake_case** (`my_ext/`, not `my-ext/`).

| Layer | Path | Permissions |
|---|---|---|
| system | `boar/extensions/{type_dir}/` | read-only, shipped with the repo |
| user | `.boarai/extensions/{type_dir}/` | installable/removable |

`type_dir`: channel→channels, plugin→plugins, tool→tools, skill→skills,
mcp→mcp. Same-name override: user > system. The manifest filename is
`manifest.yaml` (`extension.yaml` is a legacy alias only — never use it for
new extensions).

## Full manifest.yaml fields

| Field | Required | Notes |
|---|---|---|
| `name` | ✅ | unique ID, snake_case |
| `version` | ✅ | semver |
| `extension_type` | ❌ | channel/plugin/tool/skill/mcp, default plugin |
| `description` | ❌ | one-line summary |
| `author` | ❌ | author |
| `enabled` | ❌ | default true |
| `dependencies` | ❌ | other extension names this depends on |
| `pip_dependencies` | ❌ | own pip deps (e.g. `- aiohttp>=3.8.0`); auto-installed on load — slow installs continue in the background and the extension hot-loads when ready |
| `min_boarai_version` | ❌ | minimum core version |
| `config_defaults` | ❌ | default-value seed for `_config` (below caller config and below the persisted config.json; runtime via `api.get_config`/`set_config`, persisted to `.boarai/extensions/data/<id>/config.json`, auto-merged across restarts/hot-reloads) |
| `config_schema` | ⛔ removed | the declarative form is gone — settings are a custom iframe panel (`contributes.ui`); a legacy key is ignored with a verifier warning |
| `provides` / `permissions` | ❌ | declared capabilities/permissions |
| `source_id` / `source_display` | ❌ | **channel-only**: message source identity and display name (see channels.md) |
| `contributes` | ❌ | declarative capability list (below) |

The 7 `contributes` kinds (aligned with `Contributes` in
`core/extension/manifest.py`):

| Kind | Notes |
|---|---|
| `tools` | tool, `handler: code/tools.py#fn` string reference (see tools.md) |
| `routes` | HTTP routes |
| `hooks` | bind to core/other extensions' hook points |
| `hook_points` | own hook points for others to bind |
| `services` | extension-to-extension request/response |
| `mcp_servers` | external MCP server bridging (see mcp.md) |
| `ui` | WebUI settings panel + action buttons (`panel` + `actions`, see plugins.md "Extension settings") |

> `channels` / `skills` are **NOT** contributes kinds: a channel is a plain
> extension (via the `source_id` field + route registration, see
> channels.md); skills do not go through manifests (see skills.md).

## Entry skeleton and lifecycle

```python
from boarai.core.extension import ExtensionBase, ExtensionManifest

_MANIFEST = ExtensionManifest(
    name="my_ext", version="1.0.0",
    extension_type="plugin", description="one-line summary",
)

class MyExtExtension(ExtensionBase):
    @property
    def manifest(self) -> ExtensionManifest:
        return _MANIFEST

    async def initialize(self, api=None) -> None:
        ...  # register tools/hooks/routes/services (exactly one param: api)

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def cleanup(self) -> None: ...
```

- `initialize` takes exactly one param `api` (the ExtensionAPI facade); the
  event bus is NOT a param — access it via `self.event_bus` (loader-injected)
- On unload the core sweeps EVERYTHING registered under your extension's
  ownership: manifest contributes AND dynamically registered routes/tools
  (`api.register_route` / `api.register_tool`), hook bindings, services,
  middleware. (Dynamic registrations used to leak and make the next load
  fail with "already registered" — fixed.) Cleaning up your OWN external
  resources in `stop/cleanup` (poll tasks, sockets, event subscriptions)
  remains your responsibility

## Import semantics (differs per layer — common pitfall)

The entry `extension.py` is loaded via `spec_from_file_location` with **no
package context** → `from . import xxx` always fails.

- **system layer**: the project root is on sys.path, so
  `from boar.extensions.<type_dir>.<name> import xxx` works
- **user layer**: `.boarai/extensions/` is NOT on sys.path — that import
  raises ImportError. Three supported patterns:
  1. **Single file** (recommended): put `@tool` registrations directly at the
     top of `extension.py` — registration happens at module load
  2. **Multi-file via manifest**: declare
     `contributes.tools[].handler: code/tools.py#my_tool`; the loader
     resolves it relative to the extension dir (handlers must be TOP-LEVEL
     functions in flat modules)
  3. **Sibling module from the entry** (when extension.py itself needs a
     helper module): load it explicitly with importlib — and pop any stale
     module first so hot reload cannot keep old code:

     ```python
     import importlib.util, sys
     from pathlib import Path
     _EXT_DIR = Path(__file__).parent

     def _load_sibling(name: str):
         mod_name = f"boarai_ext_<extname>_{name}"
         sys.modules.pop(mod_name, None)   # hot-reload safety
         spec = importlib.util.spec_from_file_location(mod_name, _EXT_DIR / f"{name}.py")
         module = importlib.util.module_from_spec(spec)
         sys.modules[mod_name] = module
         spec.loader.exec_module(module)
         return module
     ```

     Never load the same sibling twice from different places: two copies mean
     two distinct exception class objects, so `except YourError` silently
     misses instances raised through the other copy.

## Authoring workflow (scaffold → verify → load → prove)

**Docs-first contract (iron rules):**

1. **Do NOT read `core/` source to infer behavior.** This spec set plus the
   scaffold templates are the contract. If a needed detail is missing,
   ambiguous, or contradictory: STOP and report it as *unverified* (ask the
   operator or flag the doc gap) — never guess from implementation, and never
   probe the running server internals to fill the hole.
2. **Verification is evidence, not prediction.** Distinguish "statically
   verified", "loaded and observed working", and "unverified" in every
   handoff. A promise to follow a rule is not evidence the code follows it.
3. **Never silently set trust flags.** Loading user-layer extensions executes
   Python in the server process; the operator decides (see the trust gate
   below).

**Writing the files: THIS SPEC is the contract.** Author `manifest.yaml` +
`extension.py` by hand directly from this document and the type guides —
that is the supported, first-class path; reading `core/` source is never
part of it. There is NO backend CLI for extensions (removed 2026-11-15) —
the feedback loop runs over the REST API (auth per step 0 below) plus one
Python entry for scaffolding:

| Step | How | Notes |
|---|---|---|
| Starting skeleton | `py -3 -c "from boarai.core.extension.scaffold import scaffold_extension; print(scaffold_extension('<id>', '<type>', full=False)['path'])"` (run at the repo root) | renders a RUNNABLE template into the user layer; `full=True` = every contributes kind. Copying `boar/extensions/templates/hello/` by hand from this doc is equally valid |
| Static verify | `GET /api/extensions/verify?name=<name>` (or `&path=<dir>` for an unloaded dir) | structured issue stream (`code`/`message`/`file`/`hint`); AST-only, never executes extension code; MANDATORY after every edit, loop until `errors == 0`. Last resort without a server: `py -m py_compile` + the acceptance checklist below |
| Load state | `GET /api/extensions` | grouped by section, failed list included |

**User-layer trust gate:** the loader REFUSES to execute any non-system
extension until the operator grants trust, either:

- **Per extension (recommended):** the **Trust & load** button on the
  extension's failure card in the WebUI (`POST /api/extensions/<name>/trust`)
  — persisted in state.json, effective immediately, revocable
  (`.../untrust` stops and unloads it);
- **Globally:** `BOARAI_ALLOW_INPROCESS_USER_EXTENSIONS=1` (trusts ALL user
  extensions) + a server restart **from a new terminal** (an in-server
  restart re-inherits the old environment).

**Ask the operator — never grant trust yourself**: do not set the env var,
and do not call the trust endpoint; both are operator decisions (the grant
is audited server-side at WARNING). Static verification needs no trust, so
you can fully validate before asking.

**Load failures are visible** (no more log-only black holes):
`GET /api/extensions` carries a `failed` list; the WebUI extensions page
renders a red failure card (error + hint + retry / stop-retries); the WS
pushes `extension.load_failed`. A gate rejection appears with code
`gate.inprocess_untrusted`.

## Security red lines (6 rules, mandatory for every type)

1. One-way dependency: extensions prefer the stable surface
   (`core.extension`/`core.event`/`core.trash`, ...); internal modules
   (`core.session`/`core.memory`, ...) are usable but carry no compat
   promise — use at your own risk
2. Tools MUST declare `security_profile` (file ops →
   file_read/write/delete + `path_param`; shell → shell; see tools.md)
3. Dangerous capabilities are opt-in by default
4. The capability facade is a standardization boundary, NOT a security
   sandbox; real security lives at the execution egress
   (PathGuard/ToolGuard/Audit)
5. Always use `pathlib.Path`, never string concatenation; extension data
   goes to `api.data_dir()` (`.boarai/extensions/data/<id>/`) — never into
   the extension's own source directory
6. Clean up resources explicitly in `stop/cleanup`

## Known boundaries (check before designing, 2026-09 state)

| What you want | Status | Workaround |
|---|---|---|
| Extension page inside the WebUI navigation | ❌ tabs hardcoded (navbar.js); dynamic tabs are a TODO | Island URL: `/static/<name>/page.html` |
| Subscribe to `user.input` for messages | ❌ enum exists but is never published | `SESSION_MESSAGE` event or a WS client |
| Register terminal commands (register_terminal_command) | ⚠️ mechanism works, but the audience is only the semi-retired CLI subsystem; WebUI users never see it | Expose to the LLM via `@tool` instead |
| Custom LLM provider | ⚠️ registrable (`get_llm_manager().register_provider`), but routing picks by `request.provider`/default — **registered ≠ active** | Set as default in config, or name it explicitly in requests |
| Deep sandbox (subprocess isolation of extension code) | ❌ architectural limit | Execution-egress auditing (PathGuard/ToolGuard/Audit) as the safety net |

## Acceptance (after writing/modifying ANY manifest extension — mandatory)

The docs tell you how to write; these checks tell you whether it loaded.
Do not report success without them. Endpoints are on the local server
(same port as the WebUI); use shell_execute (curl) or file tools on logs.

0. **Auth first** — every `/api/*` route except `/api/auth/token` is behind the
   access gate and answers 401 without a credential. From the server host
   (loopback) either rely on `security.web.trust_loopback` (default on) or do
   `curl -s http://127.0.0.1:<port>/api/auth/token` → `{"token": "..."}` and
   send `-H "Authorization: Bearer <token>"` on every request below. That token
   is the **process launch token** (loopback only, dies with the server), not
   the WebUI access key; a remote caller must use the access key itself.
0.5. **Static verify BEFORE load** — `GET /api/extensions/verify?name=<name>`
   must report `errors == 0`. This works
   even while the trust gate is closed, so "is it my code or my declaration"
   is answered before any restart.
1. **Extension is live**: `GET /api/extensions/<name>` — exists, enabled,
   and its `error` field is empty. If it answers `status: "failed"` (or the
   name appears in `GET /api/extensions` `failed`), the response carries
   `failure_code` + `hint` — fix per the hint, do not dig through logs.
   Hot reload runs every ~5s; failed loads retry with backoff (60 s for
   transient faults, exponentially up to 1 h for deterministic ones like the
   trust gate); force with `POST /api/extensions/<name>/reload`.
2. **Each contributed capability is actually mounted** (registered state,
   not your declaration):
   - hooks → `GET /api/extensions/hooks` shows your bindings
   - services → `GET /api/extensions/services` lists your methods
   - routes → call one: `GET /api/ext/<name>/<path>` answers
   - tools → no HTTP listing; grep today's server log under `.boarai/logs/`
     for the tool name, then confirm it appears in the next run's tool list
   - mcp_servers → log shows the `mcp_<server>_<tool>` registrations
     (a server failing to start only logs an error — check, don't assume)
   - ui → `GET /api/extensions/<name>` carries a `settings` block with your
     `panel` / `actions`, and the extensions page shows a Settings button
     (the panel needs `/static/<name>/…` to answer — check that URL directly;
     a rejected `custom_url` is blanked and logs a warning)
3. **Log scan**: any warning line containing `contributes` + `discarded`
   naming your extension means a malformed declaration was dropped — fix
   the manifest and reload. Silence + missing capability = you wrote a key
   the loader never saw.
4. **Channels**: after both halves are wired, send one test message through
   the bridge and confirm the reply flows back (see channels.md).

## Reference implementations

- Tool + hook: `boar/extensions/tools/file_tools/` (@tool + declare_hook, system-layer import pattern)
- Event subscription: `boar/extensions/channels/webui/extension.py` (event_bus.subscribe in initialize)
- Channel: `boar/extensions/channels/webui/` (source_id + routes + static assets)
- Skill toolchain: `boar/extensions/tools/skill_tools/` (asyncio.to_thread wrapping sync store, event publishing)

