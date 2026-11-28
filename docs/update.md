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

## Release notes (`GET /api/update/notes?version=<ver>`)

The publish flow snapshots each PyPI version's description as README + a `# Release notes - <ver>` block; the notes endpoint fetches the per-version registry JSON and returns that block (markdown). Results are cached on disk under `cache/update_notes/` — a published version's description is immutable, so each version is fetched at most once per install. The endpoint is HTTP-only (a read-only query; no WS twin needed).

## Guards (refusal contract)

- The `update.enabled` config flag.
- Container environments: the pip channel updates IN PLACE (see below); git/source containers → 400 (rebuild the image instead).
- Per-channel precheck: git checks binary/remote/dirty-tree, source checks tree completeness — failures return 400 with a refusal naming the fix.
- A live worker (lock held) → 409: follow it instead of starting another.
- Active runs → 409: never replace the package under a working agent.
- A finished attempt never blocks a retry — the worker lock is the only serialization (the retry cooldown was removed).

## In-container pip updates (core/update/container.py)

The detached stop→install→start worker cannot work in a container (the worker dies with the container when the server process exits). The pip channel therefore updates INSIDE the server process: `pip install --upgrade` while running (POSIX does not lock loaded module files), verify in a fresh interpreter, mark `restarting`, then exit — the restart policy (`restart: unless-stopped` in every shipped compose file) brings the SAME writable layer back up, so the upgrade survives. The next process flips the status file to `done` once it reports the target version. Failures never exit the process: the old version is reinstalled when needed and `failed` is reported with the server left running. A container without a restart policy would simply stay stopped after a successful install — start it again manually.

## Worker stop phase

- The graceful-stop request failure is never silent: the refusal reason lands in the failure message and the worker log.
- The port-free wait after the stop defaults to `update.stop_timeout_s` (120s, minimum 30s) — a slow extension shutdown must not abort a healthy update.

## Channel rules

All three install kinds self-update IN PLACE through their own channel; an update never crosses channels (a cross-channel "update" would leave a half install). PyPI is the single version source.
