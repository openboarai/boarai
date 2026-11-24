# Channel Extension Development (channels)

> Purpose: `file_read` this file when bridging an external messaging platform
> (WeChat/Feishu/DingTalk/Telegram/terminal) (read common.md first).
> Location: `boar/prompts/extension/channels.md`

A channel is an extension that bridges an external messaging platform into the
core. Two shapes — pick by what the platform is, not by taste:

| Shape | Use when | Base |
|---|---|---|
| **Message adapter** | request–reply platform; you own a long connection, long-poll or webhook | `ChannelAdapter` + `api.register_channel` |
| **Plain extension** | HTTP/WS front-end (webui), not a request–reply platform | `ExtensionBase` only |

## The adapter contract

Division of labour: **the core owns every run, the adapter owns its transport.**
Connecting to Feishu, WeChat QR login, a 35 s long-poll, a webhook route — all
yours, and the core never sees it. What the core needs is one method:

```python
import asyncio
from boarai.core.extension import ChannelAdapter, InboundMessage, OutboundMessage

class MyPlatformAdapter(ChannelAdapter):
    source_id = "myplatform"          # must match the manifest source_id

    async def start(self, ctx):       # optional: open the transport
        self._ctx = ctx
        self._task = asyncio.create_task(self._poll_loop())

    async def send(self, message: OutboundMessage) -> bool:   # REQUIRED
        """Deliver one outbound message; return False on transport failure."""
        return await self._post(message.reply_token, message.text)

    async def stop(self):             # optional: release the transport
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)

    async def _poll_loop(self):
        while True:
            for raw in await self._fetch():
                await self._ctx.submit(InboundMessage(
                    conversation_id=raw["chat_id"],   # stable per chat/user
                    text=raw["text"],
                    sender_id=raw["from"],
                    sender_name=raw["nickname"],
                    reply_token=raw["token"],         # opaque; comes back on send
                ))
```

Register it in `initialize(api)` — that call is the insertion point, and it
returns the context you push inbound messages into:

```python
self._ctx = await api.register_channel(
    MyPlatformAdapter(), agent_name="default", run_timeout=300,
)
```

Two ways to hand a message in:

- `await ctx.submit(inbound)` — fire and forget; the core runs the turn and
  delivers the reply (or the error notice) through your `send()`.
- `result = await ctx.ask(inbound)` — returns `ChannelRunResult`
  (`.text` / `.status` / `.error` / `.session_id` / `.run_id`) and delivers
  nothing, for adapters that split, throttle or reformat the answer themselves.

`ctx.notice(conversation_id, text)` pushes an unsolicited message.

## What the host already does — do NOT re-implement

`api.register_channel` gives you a `ChannelContext` that owns the whole turn:

- **conversation → session**: `ctx.session_id_for(conversation_id)` — derived
  (uuid5 over `source_id:conversation_id`), stable across restarts, no mapping
  table, and two platforms can never collide.
- **session bootstrap**: creates it on first contact with the *same* global
  defaults a WebUI session gets (model / agent / workspace).
- **inbound turn**: persists the user message and mirrors the
  `session.message` event, so the WebUI shows turns that arrived from your
  platform. (`add_message` publishes nothing on its own — the host does it.)
- **run**: `create_run` with a completion future, awaited to the **complete
  final answer** — never a stream fragment, no tool-noise filtering.
- **serialization**: one turn at a time per conversation (`create_run` would
  interrupt an active run otherwise); different conversations run in parallel.
- **always replies**: timeout, failed run, pipeline exception or a raising
  `send()` all end in a delivery attempt — an external user is never left in
  silence. Timeouts cancel the run.
- **task hygiene**: dispatched turns are held by strong reference, so they
  cannot be GC'd mid-run.
- **lifecycle**: `start(ctx)` on registration, `stop()` on disable / unload /
  replacement (in-flight turns are cancelled first). A `start()` that raises
  does NOT unregister — platforms needing a QR/OAuth handshake legitimately
  begin disconnected.

## Channel manifest fields

| Field | Required | Notes |
|---|---|---|
| `source_id` | channel ✅ | message source identity (e.g. `wechat`); auto-registered into the SourceRegistry when the extension is enabled. Must equal the adapter's `source_id` |
| `source_display` | channel ✅ | human-readable name (e.g. `WeChat`) |

```yaml
name: wechat_bridge
extension_type: channel
source_id: "wechat"
source_display: "WeChat"
```

One `source_id` = one adapter. Registering a second adapter under a source id
another extension owns raises `ChannelConflict`.

## Rules that still apply

- `reply_token` is opaque to the core: carry it from `InboundMessage` to your
  `send()` (the host returns it on `OutboundMessage.reply_token`).
- One external conversation ↔ one BoarAI session. Never reuse a
  `conversation_id` across different users, or they share history.
- `asyncio.CancelledError` derives from `BaseException`: `except Exception` in
  your `stop()` will not swallow a cancellation — await your own tasks.
- Channels follow the one-way dependency: may import `core.*`, must never be
  imported by core.

## Static assets (web-facing channels)

A `static/` dir inside the extension is auto-mounted by the loader under
`/static/<name>/`; get URLs in code via `api.static_url("index.html")`
(never hardcode extension URLs).

## Low-level recipe (only when you bypass the host)

Use this if you need run control the host does not offer (e.g. streaming
fragments, several runs per message). Otherwise `ctx.submit` / `ctx.ask` do all
of it:

```python
from boarai.core.event import EventType, publish_event
from boarai.core.session import get_run_manager, get_session_manager
from boarai.core.session.service import apply_session_defaults

sm, rm = get_session_manager(), get_run_manager()
session = await sm.get_session(session_id)
if session is None:
    session = await sm.create_session(session_id=session_id, agent_name="default")
    apply_session_defaults(session)      # model / agent / workspace

session.ensure_session_capability_inject()
message = session.add_message("user", content, metadata=metadata)
session.maybe_auto_title_from_user(content)
await sm.save_session(session_id)
await publish_event(EventType.SESSION_MESSAGE, {          # mirror, or the
    "session_id": session_id,                             # WebUI never sees it
    "message_id": f"msg_{int(message.timestamp.timestamp() * 1000)}",
    "role": "user", "content": content,
    "timestamp": message.timestamp.isoformat(),
    "metadata": metadata, "seq": getattr(message, "seq", 0),
}, "<your source_id>")

future = asyncio.get_running_loop().create_future()
run_id = await rm.create_run(session_id, agent_name="default",
                             completion_future=future)
try:
    run = await asyncio.wait_for(future, timeout=run_timeout)
except asyncio.TimeoutError:
    await rm.cancel_run(run_id)
    run = None
reply = (run.full_response or "").strip() if run is not None \
    and run.status.value == "completed" else f"error: {run.error if run else 'timeout'}"
```

If you take this path, you also take on the four rules the host enforces for
you: per-conversation serialization, always reply on failure, strong task
references, and cancelling the run on timeout.

## Pitfalls

- Parameterized routes (`/static/{path:.*}`) resolve through the RouteRegistry
  by prefix — no native-router registration is needed or possible.
- Do **not** deliver inbound messages via the `user.input` event: it has no
  consumer. Use `ctx.submit` (or the recipe above).

## Reference implementation

- Message adapter contract: `core/extension/channel/adapter.py` (what to
  implement) and `core/extension/channel/host.py` (what the core does).
- Plain-extension channel: `boar/extensions/channels/webui/` — source_id /
  source_display + route registration + static asset serving.
