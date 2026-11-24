> **English** | [简体中文](shell_zh.md)

# Shell Execution Backends (core/shell)

Pluggable shell execution backends: `ShellBackend` is the seam answering "where does this command run".

## Modules

| Symbol | Responsibility |
|--------|----------------|
| `ShellBackend` | Backend protocol (`ShellRequest` → `ShellOutcome`) |
| `LocalBackend` | Direct execution on the host (default) |
| `DockerBackend` | Execution inside a container reached through the `docker` CLI |
| `BackendInfo` / `IsolationLevel` | Backend-reported isolation level and exposed host surface |
| `register_backend` / `configure_backend` / `get_shell_backend` / `available_backends` | Registration and selection |

## Design points

- Every backend reports a `BackendInfo` (isolation level + which host surface it exposes) so the tool guard can couple approval policy to **actual containment** instead of judging command text alone — the same command is catastrophic on the host and harmless inside a container that mounts nothing.
- **Relaxation is one-directional**: isolation may loosen a decision only when no host surface is reachable. A container with a bind mount, host networking or a shared PID namespace is not a boundary — treating it as one is how a "sandboxed" command ends up rewriting `~/.ssh`.
- Isolation levels: `NONE` (same host, same user, same filesystem) → `PROCESS` (separate process/user, host filesystem still reachable) → container-grade.
- Docker mount-spec parsing anchors on the container-side absolute POSIX path (`/`-prefixed) so Windows drive letters are never mistaken for the separator.

## Boundaries

- Command permission classification and approvals live in the security subsystem (conservative tokenizer + ToolGuard); execution lives in the shell-tools extension; this module only provides "where it runs and what came back".
- Backend selection is deployment configuration; extensions and tools never pick a backend themselves.
