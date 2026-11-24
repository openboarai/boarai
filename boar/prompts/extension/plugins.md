# Plugin Capability Development (plugins)

> Purpose: `file_read` this file when developing hooks / HTTP routes /
> services / event subscriptions / extension config (read common.md first).
> Location: `boar/prompts/extension/plugins.md`

## Hooks (pipeline interception)

```python
async def initialize(self, api=None) -> None:
    # Declare your own hook point for others to bind
    api.declare_hook("pre_send", "blockable", description="before send; return {'blocked': true} to veto")
    # Bind to a core hook point
    api.register_hook("core.tool.pre_execute", self.before_tool, order=10)

async def before_tool(self, payload: dict) -> dict:
    # blockable: return {"blocked": True, "reason": "..."} to veto; else continue
    return payload
```

Semantics: `observe` (notify only) / `blockable` (first blocked stops,
first-deny-wins) / `transform` (payload flows through handlers in order).

**All 15 core hook points are wired**:
- `tool.pre_execute` (vetoable) / `post_execute` / `on_error`
- `run.on_start` / `on_complete` / `on_error`
- `llm.pre_request` (context injection) / `post_response` / `transform_output`
- `session.start` / `end` / `on_message` (message interception/rewrite: veto
  with `{"blocked": True}`, rewrite with `{"data": {"content": ...}}`)
- `approval.pre_request` (vetoable)
- `fs.pre_write` (vetoable) / `post_write`

Note: `terminal.transform_output` has been removed (no terminal subsystem).

## HTTP routes

```python
async def initialize(self, api=None) -> None:
    api.register_route("GET", "/summary", self.summary, use_prefix=True)

async def summary(self, request) -> "web.Response":
    from aiohttp import web
    return web.json_response({"ok": True})
```

- Handler signature: `async def xxx(request) -> aiohttp.web.Response`
- `use_prefix=True` (default) mounts at `/api/ext/<name><path>`; `False`
  mounts at root (system-layer trusted only)
- Static assets auto-mounted: a `static/` dir inside the extension is served
  under `/static/<name>/`; get URLs via `api.static_url()`
- Global middleware: `self.register_middleware` / `unregister_middleware`
  (ExtensionBase methods) — hot-loaded extensions can register at runtime too

## Services (extension-to-extension request/response)

```python
async def initialize(self, api=None) -> None:
    api.register_service({"stats": self.stats})

async def stats(self, params) -> dict:
    return {"count": 42}

# Another extension calls it:
result = await api.call_service("my_ext", "stats", {}, timeout=5)
```

## Event bus (broadcast)

```python
async def start(self) -> None:
    from boarai.core.event import EventType
    await self.event_bus.subscribe(EventType.LLM_REQUEST, self.on_llm_request)

async def on_llm_request(self, event) -> None:
    ...
```

- Publish: `await api.publish("my.custom.event", {...})`
- **New events MUST be registered** in `core/event/types.py` as `EventType`
- Check consumers first: events like `user.input` currently have no
  subscribers and cannot be used as a channel

## Extension settings (WebUI-configurable)

One optional declaration makes an extension configurable from the extensions
page: `contributes.ui` — a **custom settings panel** (your own page in an
iframe) plus optional action buttons. An extension without it shows no
Settings button. The former declarative `config_schema` form was removed:
a settings page that needs more than static input boxes writes its own HTML
and talks to the core through `/static/js/boar-sdk.js`.

### Panel — `contributes.ui`

```yaml
contributes:
  ui:
    panel:
      title: "WeChat iLink"
      title_i18n: {zh: "微信 iLink"}
      icon: "qr-code"              # lucide icon name
      kind: custom                 # the only kind (default)
      custom_url: "/static/<id>/settings.html"
      custom_height: 480
    actions:
      - id: login_qr
        label: "Get QR code"
        label_i18n: {zh: "获取二维码"}
        icon: "qr-code"
        style: primary             # primary | default | danger
        confirm: "Re-login now?"   # optional confirmation text
        timeout: 20                # seconds, clamped to 1..120
        handler: "code.py#action_login_qr"
```

The panel is an iframe of your own page, auto-mounted from your `static/`
dir at `/static/<id>/`. Full UI freedom: inputs, drag-and-drop ordering,
charts, streaming logs, multi-step wizards. The URL must start with
`/static/<id>/` or `/api/ext/<id>/` — anything else is rejected (the panel
shows an error and a warning is logged).

### The page — `boar-sdk.js`

Load the SDK first, then use the global `boar` object. Your extension id
comes from the iframe URL (`?ext=<id>`), which the settings dialog adds
automatically:

```html
<script src="/static/js/boar-sdk.js"></script>
<script>
  const cfg = await boar.getConfig();            // your config.json values
  await boar.saveConfig({ key: value });         // merge + live-apply
  const v = await boar.getData("cache");         // private data map value
  await boar.saveData("cache", { a: 1 });        // merge into data.json
  const r = await boar.callAction("login_qr");   // run a declared action
</script>
```

| Method | Endpoint | Semantics |
|---|---|---|
| `boar.getConfig()` | `GET /api/extensions/{id}` → `settings.values` | `{}` when nothing persisted |
| `boar.saveConfig(values)` | `PUT .../settings` | merges into `config.json`, calls your `on_config_changed`, publishes `extension.config_changed` |
| `boar.getData(key)` | `GET .../data` | one value from your private map |
| `boar.saveData(values)` | `PUT .../data` | merges into `data.json` — opaque to the core |
| `boar.callAction(id, params)` | `POST .../actions/{id}` | result envelope below |

Storage: `<data>/extensions/data/<id>/config.json` and `data.json` — the
extension's private data dir (separate from its source tree, so uninstalling
never deletes it). Read config from Python with `api.get_config()`; write
with `await api.set_config({...})` (same persist + live-apply semantics).
There is **no schema validation**: your page owns the types, defaults and
error messages. Both maps refuse payloads over 256 KB.

Errors: every SDK call rejects with the server's error message; catch and
show it inline in your page.

**Panel i18n.** The settings dialog appends `lang=<active WebUI language>` to
the iframe URL. Your page owns its strings: ship `static/i18n/<lang>.json`
(`zh.json`, `en.json`, …), read the `lang` param, fetch your own bundle and
fall back to English literals when it is missing. Do NOT put extension copy
into the WebUI's global i18n files.

Reference implementation: `boar/extensions/tools/web_tools/static/settings.html`
(the search-engine priority order editor).

### Action handler contract

```python
async def action_login_qr(ctx) -> dict:
    # ctx = {"extension_id", "config", "params", "request_id"}
    return {
        "ok": True,
        "message": "Scan with WeChat",        # toast
        "view": {"type": "image", "src": "/api/ext/<id>/qr.png"},
        "poll": {"action": "login_status", "interval_ms": 2000, "max_ms": 150000},
        "reload": True,                        # re-fetch settings on success
    }
```

`view.type` is one of `image` (`src`/`alt`), `kv` (`rows: [{label, value}]`),
`markdown` (`text`), `text` (`text`) — a single dict or a list. Anything else
is dropped. `poll` makes the client re-run another action every
`interval_ms` until that action stops returning `poll` or `max_ms` elapses.

Enforced, not suggested:

- **Return fast.** Timeout defaults to 15 s, max 120 s. A long wait (QR scan,
  device pairing, a build) must return immediately and hand off to `poll` —
  blocking the handler blocks the HTTP request.
- **No payloads.** The envelope is capped at 64 KB and images must be
  same-origin URLs, never base64; serve the bytes from your own route.
- **Failures ride inside the envelope.** Return `{"ok": False, "message": …}`
  or raise — both become `ok: false` with HTTP 200, so the panel shows the
  reason next to the button. Only an unknown extension/action id is a 404.

### Runtime registration

Manifest declarations are routed through these same facade methods, so you
can also register at runtime (in `initialize()` / `start()`) for a panel or
buttons that only make sense once the environment is known:

```python
api.register_settings(panel={"title": "My extension",
                             "custom_url": "/static/<id>/settings.html"})
api.register_settings_action("ping", self._action_ping, label="Ping", icon="activity")
api.unregister_settings_action("ping")
```

Runtime panel keys merge OVER `contributes.ui.panel` per key and are not
persisted — re-register them on every `initialize()`. The legacy
`register_settings(fields)` argument is a deprecated no-op.

### HTTP surface

| Endpoint | Purpose |
|---|---|
| `GET /api/extensions/{id}` | `settings: {panel, actions, values, loaded}` |
| `PUT /api/extensions/{id}/settings` | `{"values": {...}}` → persist, live-apply |
| `GET/PUT /api/extensions/{id}/data` | private data map (`{"values": {...}}`) |
| `POST /api/extensions/{id}/actions/{action_id}` | `{"params": {...}}` → result envelope |

Config writes also work for an extension that is **not loaded** (disabled,
or failing the trust gate): values are written to the data dir and merged on
the next load — that is how a missing API key gets filled in before the
first successful start. Actions require a loaded extension.

## Known boundaries

Global capability limits (WebUI dynamic tabs, `user.input` having no
consumers, custom providers being registered ≠ active, deep sandbox, etc.)
live in the "Known boundaries" table in common.md.

## Reference implementations

- `boar/extensions/channels/webui/extension.py` — event_bus.subscribe
- `boar/extensions/tools/file_tools/` — declare_hook
