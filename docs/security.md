> **English** | [简体中文](security_zh.md)

# Security System (core/security)

Layered security: transport-level access gate, file path sandbox, tool permission guard, shell command classification, audit and redaction.

## Modules

| File | Responsibility |
|------|----------------|
| `__init__.py` | `SecurityManager` facade: aggregates PathGuard / ToolGuard / AuditLog, `get_security_manager()` |
| `gate.py` | HTTP + WS edge access gate: single source of truth for "may this request proceed?" |
| `access.py` | Edge access control: access key, device sessions, throttling (`get_access_store()`) |
| `path_guard.py` | `PathGuard` file path sandbox |
| `path_utils.py` | Shared L2-fence helpers: canonicalize-then-contain and absolute-path checks |
| `tool_guard.py` | `ToolGuard` runtime tool execution permission checks |
| `shell_tokenize.py` | Conservative shell command tokenizer for permission classification |
| `audit.py` | `AuditLog` structured security event logging |
| `redact.py` | Redaction of audit records before persistence (approval entries persist raw command strings and tool args) |

## Access key and gate

- The gate lives at the transport layer only: API, WS and extension routes all pass through it; the key is stored as a scrypt hash and **never displayed**; `rotate` is the only way to obtain a new key.
- Key rotation is two-phase: prepare shows the new key while the old one still works; confirm applies it. "Generated-and-effective-immediately + shown once" is a self-locking trap and is forbidden.
- The first key is created by the browser setup page (the backend never auto-generates); an unclaimed instance is fully closed. Loopback claims it freely; a remote browser must add the one-shot setup code printed in the server startup log (so headless / external-network installs work without opening the console claim to whoever arrives first). Headless deployments may also use `boarai key init` explicitly.
- The `BOARAI_API_TOKEN` env var must be at least 32 base64url characters or startup refuses; on first run it is adopted as the key.
- Key rotation / device revocation publishes `auth.revoked`, mirrored to every WS client: revoked devices must drop their sockets instead of serving dead sessions.
- Device sessions can be listed and revoked individually; revoking sessions leaves the key untouched.

## Guard model

- `SecurityManager.initialize(config, project_root)` builds the three guards from config; **runtime updates mutate the same live config object** (the WebUI settings tab edits live state, not a detached copy).
- Tool permissions route by the tool's self-declared `security_profile` metadata (file_read / file_write / file_delete / shell / none); **policies are never hardcoded by tool name**; on denial the result metadata carries `security_block` + `reason`, and the upper layer classifies it as a `permission` error kind.
- Path checks uniformly "canonicalize-then-contain"; shell commands are conservatively tokenized into independent segments before classification; unclassifiable commands are treated as unclassifiable (covered by a dedicated test matrix).
- Approval policy (ask/deny/allow) and the default security mode for new sessions live in the security config section; enforcement lives in the run loop's confirmation gate.

## Audit

`AuditLog` records structured security events (approval decisions, guard denials, ...); entries pass through `redact.py` before persistence; the facade rebuilds the AuditLog when its path changes.
