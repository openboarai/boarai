> **English** | [简体中文](runtime_zh.md)

# Runtime Infrastructure (core/runtime)

Home of kernel-level infrastructure: path anchors, atomic writes, background tasks, bounded caches, first-run bootstrapping and server startup orchestration. Cross-subsystem, business-free code belongs here.

## Modules

| File | Responsibility |
|------|----------------|
| `data_paths.py` | Single source of truth for the data-directory anchor |
| `root.py` | Single source of truth for the process-level project root |
| `atomic_write.py` | Crash-safe text writes |
| `bg.py` | Fire-and-forget task spawning that survives garbage collection |
| `lru.py` | Bounded LRU map for per-session in-memory caches |
| `json_body.py` | JSON request-body parsing shared by HTTP API handlers (malformed input must surface as 4xx) |
| `artifact_mark.py` | Completion marks for background artifacts (skills, task outputs, chunks) |
| `bootstrap.py` | First-run bootstrapper: seeds the data dir from `boar/defaults/` + core dependency install |
| `serve.py` | Foreground server startup orchestration and event→WS forwarding wiring |

## Data-directory anchor

- All runtime state lives under ONE data directory anchored OUTSIDE the source tree; resolution order: `--data-dir` > `BOARAI_DATA_DIR` env var > `Path.home()/.boarai`.
- Subsystems resolve paths via `get_data_dir()` / `resolve_data_path()` only; hardcoding a `.boarai` location is forbidden; relative `.boarai/...` values in config are resolved against the data dir by `resolve_data_path`.
- On Windows, drive-less absolute paths (e.g. `/vol1/...` carried over from NAS configs) degrade into the data dir under `_mapped_/` instead of silently landing on the current drive root; true UNC paths are kept as-is.
- Per-extension private data dir: `extension_data_dir(ext_id)` → `<data>/extensions/data/<ext_id>`; ids containing `.`, `..` or path separators raise `ValueError`.
- A legacy in-tree `<repo>/.boarai` is NOT auto-migrated (the system must not contain data-migration logic): move it manually or point `BOARAI_DATA_DIR` at it.

## Project-root anchor

`set_project_root()` is pinned once at startup (by the entry point); all subsystems read via `get_project_root()`. Deriving the project root from `Path.cwd()` or `__file__` levels is forbidden. The cwd fallback in `resolve_project_root()` belongs to anchor resolution itself only.

## Atomic writes

`open(path, "w")` truncates before writing — a crash leaves a half file. All config/state persistence goes through `write_text_atomic` / `write_text_atomic_async` (temp file + replace). Whether a failed write is best-effort or raised is the caller's semantic choice, but direct non-atomic writes are never acceptable.

## First-run bootstrap

- `copy_defaults()` seeds `<data>` from `boar/defaults/*`, tracked by `bootstrap.initialized` in `config.yaml`; once set, defaults are NEVER copied again and user data is never overwritten.
- Startup order matters: `copy_defaults` runs BEFORE skeleton creation, because `is_initialized()` treats existing sessions/agents/memory/logs dirs as "already bootstrapped" — creating the skeleton first would make a fresh install skip seeding.
- Core dependency installation only happens via explicit `python main.py --ensure-deps` (`ensure_core_dependencies`); entry modules must never reach the network at import time; `CORE_DEPENDENCIES` stays in lock-step with pyproject dependencies.

## Startup orchestration serve.py

Owns the foreground startup sequence (config → logging → security → session/task subsystems → extension loading → unified server) and subscribes task / session / skill / update / auth / terminal-run events for forwarding to WS clients (the forwarding list is part of the event registration chain).
