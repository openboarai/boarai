> **English** | [简体中文](api_zh.md)

# REST/WS API Layer (core/api)

System-level API handlers: one file per domain, reusable by any channel extension (WebUI, CLI, ...). Business semantics live in domain services; handlers only adapt the transport.

## Modules

| File | Domain |
|------|--------|
| `session.py` | Session CRUD, messages, run create/subscribe; WS `session.*` / `tool.*` message handling and per-session broadcast |
| `run.py` | Run record queries |
| `agent.py` | Agent (persona) management |
| `llm.py` | Provider/model configuration and connectivity |
| `auth.py` | Access-key endpoints (pure transport: mint device sessions from the access key) |
| `owner.py` | User profile and settings |
| `security.py` | Security configuration (guards, approval policy) |
| `approvals.py` | Out-of-band approvals: list and resolve pending tool approvals |
| `extension.py` | Extension management (list/enable/install/settings/data/actions, incl. load-failure visibility) |
| `memory.py` | Memory queries and management |
| `files.py` | Session attachment upload/download |
| `fs.py` | Directory browse/read/write for the WebUI file manager |
| `logs.py` | Read-only log inspection |
| `monitoring.py` | Token & cache statistics |
| `trash.py` | Recycle bin list/restore/empty |
| `update.py` | Online update check/apply/status/release notes |

(The task-domain handler lives in `core/task/api.py` with its subsystem and is re-exported through this package.)

## Organisation conventions

- Every file exports an `XxxAPIHandler` class plus a `get_xxx_api_handler()` singleton function; `__init__.py` re-exports them uniformly.
- Routes are mounted by the unified server's `_setup_routes()`; this layer never starts a server and never declares a listen address.
- JSON request bodies are parsed with the shared `core/runtime/json_body.py`: malformed input must produce 4xx, never 500.
- All `/api` endpoints sit behind the same access gate; adding/removing public paths must update the gate and its tests together.

## HTTP / WS dual-channel contract

HTTP and WS are two views of the same domain:

- The same business action shares one domain-service implementation across both entries (e.g. run creation: REST and the WS message `session.run.create`, response type `session.run.create.response`; the WS side also has the `session.run.get/cancel/subscribe` family).
- **Adding/changing an API requires dual-channel sync**: fields, semantics and broadcast scope must match; a parity contract test guards this.
- Broadcast scope has two levels: global (all WS clients) and per-session subscription; the chosen level is documented in the event registration comment. Global events such as session deletion go out over both the HTTP response and the WS broadcast.
- The domain service is the single implementation: `core/session/service.py` feeds both REST and WS; handlers must not each carry their own copy of business logic.
