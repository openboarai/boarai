> **English** | [简体中文](fs_zh.md)

# Kernel File Operations (core/fs)

Filesystem operations owned by the kernel. Kernel modules must be packages (directory + `__init__.py`) — **loose `.py` files at the `core/` root are never acceptable**; this package is where the former loose `core/fsops.py` was rehomed.

## Modules

| File | Responsibility |
|------|----------------|
| `textops.py` | Text file read/write primitives: backend for the WebUI file manager (session-page file explorer) |

## Security model

- Writes pass through the `core.fs.pre_write` hook first (blockable; `{"blocked": true}` vetoes), and `core.fs.post_write` (observe) fires afterwards — this is the only trigger site of these two core hook points.
- Path checks go through the security subsystem's path sandbox and the canonicalize-then-contain helpers.
- A successful text write publishes `fs.changed`, mirrored to every WS client (other tabs refresh their file tree and stale badges); the payload carries path / mtime / session_id.
- Disk writes use the atomic-write primitives; a crash never leaves a half file.

## Boundaries

- LLM-facing file tools (read/write/edit/glob/grep...) are NOT here — they live in the file-tools extension; this package serves only the kernel's own file-management surface.
- Deletion does not go through here: everything enters the recycle bin as a soft delete.
