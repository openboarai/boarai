> **English** | [简体中文](server_zh.md)

# Unified Server (core/server)

A single-port HTTP + WebSocket server hosting every REST endpoint, the WS channel and extension routes.

## Modules

| File | Responsibility |
|------|----------------|
| `unified_server.py` | `UnifiedServer`: route mounting, dynamic middleware pool, WS endpoint, broadcast |
| `manager.py` | `ServerManager` singleton (`get_server_manager()`), hot middleware add/remove entry |
| `run_events.py` | WS payload builders and global fallback forwarding for terminal run events |

## Listen address

- Single source of truth lives in this module: `BOARAI_HOST` / `BOARAI_PORT` env vars, default `0.0.0.0:26620`.
- Extensions and their manifests must NOT declare host/port — the WebUI shares the core server's port; declaring it would be a knob that does nothing.
- The default bind is non-loopback, backstopped by the transport-level access gate; the bind contract is pinned by tests and must not be relaxed in either direction.

## Route organisation

- Kernel REST routes are mounted centrally in `_setup_routes()`, pointing at the per-domain handlers in `core/api/`; gate endpoints (auth) live in this layer too.
- Extension routes exist ONLY in the RouteRegistry (single source of truth), served through middleware — hot add/remove needs no aiohttp router rebuild.
- Dynamic middleware pool: aiohttp freezes `app.middlewares` once the router starts, so runtime registrations (hot-loaded extensions) go through `_dynamic_middleware_dispatch`, which consults a mutable list per request; the dispatcher is mounted once in `__init__`.

## WebSocket

- Endpoint `GET /ws`, subprotocol `boarai.v1`; client set `_ws_clients`; on connect the client is also registered into the session API's broadcast set and the heartbeat starts.
- Inbound messages are dispatched by `type` prefix: `session.*` / `tool.*` delegate to `SessionAPIHandler._process_ws_message`; anything else gets an explicit `Unsupported message type` error; request/response messages echo their `id`.
- Disconnects must drop per-session subscriptions (`_discard_ws_client`): a reload/tab close never sends unsubscribe, and a zombie client makes later run events log "Cannot write to closing transport".
- `broadcast()` snapshots the client set before sending; failed sends are batch-removed.
- Streaming goes over WS only; there is no SSE.

## Platform noise handling

Windows' proactor loop logs an ERROR traceback whenever a client resets a connection (page refresh). `_loop_exception_handler` swallows only this known teardown noise (ConnectionReset/Aborted/BrokenPipe plus `_call_connection_lost`); everything else keeps the default handler.

## Shutdown

`stop()` closes all WS connections (snapshot iteration, since close() may discard) and tears down the runner; hot cleanup of extension middleware and routes belongs to the extension loader.

## Reverse proxies and the Origin check

- Requests carrying an `Origin` header (every browser POST) must be same-host with the instance's **public hostname**; only the host is compared, and an http/https difference is tolerated (TLS usually terminates at the proxy, so the server socket sees plain http).
- **The port stays in the comparison**: cookies ignore ports, so a malicious page on the same host but another port is same-site (SameSite=Strict still sends the session cookie) and only the port stops its POST; an omitted port normalises to the scheme default (`https://host` matches `host:443`, while `https://host:8443` does not match a bare `host`).
- The public hostname is derived with zero config: the `Host` header plus the **last** comma-separated entry of `X-Forwarded-Host` (the hop closest to the server in the proxy append chain; a client-forged first entry does not count). Caddy / Traefik / Cloudflare / nginx with `proxy_set_header Host $host` work out of the box.
- With a bare `proxy_pass` (nginx defaults Host to the internal upstream and adds no X-Forwarded-Host) the server cannot know the public name: declare it with the env var `BOARAI_ALLOWED_ORIGINS=https://your.domain`, comma-separated for multiple.
- A refusal answers 403 with `error.code = origin_not_allowed` and a message naming both escapes; the setup/unlock pages translate it.
