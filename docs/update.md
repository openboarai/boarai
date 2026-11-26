> **English** | [简体中文](update_zh.md)

# Online Update (core/update)

Self-update subsystem for the pip / git / source channels. Principle: **the server process never runs pip itself**; updates run in a detached worker subprocess.

## Modules

| File | Responsibility |
|------|----------------|
| `service.py` | Server-side orchestrator: status / manual check / apply / auto-check |
| `worker.py` | Detached update worker (`python -m boarai.core.update.worker --target <ver>`) |
| `checker.py` | Version check against a PyPI-style JSON endpoint, stdlib urllib only |
| `steward.py` | Install steward: install-kind detection, channel ownership and prechecks |
| `state.py` | Worker state: OS file lock + status file + receipts (all under the data-dir anchor) |
| `version.py` | Version parsing and comparison (stdlib only) |

## How it works

- `apply` spawns the detached worker, then mirrors the worker's status file onto the event bus (`update.progress` / `update.done` / `update.failed`) until a terminal phase.
- The worker asks this server to restart at the end; `done` is published via the event bus right before the process re-execs; a client that misses it re-reads `GET /api/update/status` (terminal state from disk) after reconnecting.
- Version checks are read-only and network-flaky by nature: every failure degrades instead of surfacing to the user.
- Only plain numeric dot versions (e.g. `1.12.0`) participate in update comparison.

## Guards (refusal contract)

- The `update.enabled` config flag.
- Container environments → 400: rebuild the image instead (the worker cannot own the stop→install→start cycle under a container supervisor, and an in-container install dies with the next image rebuild).
- Per-channel precheck: git checks binary/remote/dirty-tree, source checks tree completeness — failures return 400 with a refusal naming the fix.
- A live worker (lock held) → 409: follow it instead of starting another.
- Active runs → 409: never replace the package under a working agent.
- A finished attempt never blocks a retry — the worker lock is the only serialization (the retry cooldown was removed).

## Worker stop phase

- The graceful-stop request failure is never silent: the refusal reason lands in the failure message and the worker log.
- The port-free wait after the stop defaults to `update.stop_timeout_s` (120s, minimum 30s) — a slow extension shutdown must not abort a healthy update.

## Channel rules

All three install kinds self-update IN PLACE through their own channel; an update never crosses channels (a cross-channel "update" would leave a half install). PyPI is the single version source.
