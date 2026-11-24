> **English** | [简体中文](session_zh.md)

# Sessions and the Run Loop (core/session)

Session lifecycle, message persistence, the run execution chain (tool loop / streaming / compression / approvals / change tracking) and the thinking subsystem.

## Top-level modules

| File | Responsibility |
|------|----------------|
| `manager.py` | `SessionManager`: session CRUD, message persistence, lifecycle events |
| `service.py` | Session domain service: the **single implementation** behind both REST and WS |
| `storage.py` | Session persistence (aiofiles async I/O) |
| `journal.py` | Append-only JSONL session journal |
| `migrate.py` | v3 single-file JSON → v4 append-only JSONL migration |
| `tree.py` | Session tree: fork, branch enumeration, navigate_tree |
| `artifact.py` | Per-session file metadata and symbol index (incl. the changes.json change log) |
| `attachments.py` | Attachment storage layout, filename safety, LLM loading |
| `workspace.py` | Session workspace resolution/validation/browse (session_dir / shared / custom modes) |
| `orphan_tools.py` | Repairs assistant tool_calls with no matching tool-role result |
| `usage_ledger.py` | Append-only global LLM usage ledger |
| `run_manager.py` | Run facade |
| `run_models.py` | `Run` record and status enum |
| `run_tools.py` | Tool execution helpers: argument parsing, security kwargs, registry call, result normalization |

## run/ execution chain

| File | Responsibility |
|------|----------------|
| `registry.py` | Run lifecycle state, cancel, TTL cleanup |
| `executor.py` | Conversation tool-loop body (hook triggering via `loop_config`) |
| `loop_config.py` | Injectable loop configuration and state (incl. timeout-bounded hook triggering) |
| `context_prep.py` | Run context bag and pre-run preparation (memory / task state / routing) |
| `turn_preflight.py` | Turn preflight: run-start broadcast, wire history construction, request assembly |
| `turn_stream.py` | Turn streaming: consuming the LLM stream (vision downgrade, stream-break continuation) |
| `turn_tool_round.py` | Turn tool phase: batch scheduling (parallel/serial), execution, result normalization, termination decision |
| `turn_finalize.py` | Turn terminal phase: completed/cancelled/failed paths, interruption persistence, empty-reply fallback |
| `turn_usage.py` | Usage accounting: tokens/cache into the session, the global ledger and message metadata |
| `turn_background.py` | Background extraction: lessons, automatic skill creation and reflection (all fire-and-forget) |
| `broadcaster.py` | Three-way run event fan-out: EventBus + per-run callbacks + session WS clients |
| `tool_confirm.py` | Tool execution confirmation gate (approvals) |
| `termination.py` | Termination policy for the single-agent tool loop (pure decision: TurnState + config → action) |
| `compression_gate.py` | Idle compression gate: threshold, compress, create_run race guard |
| `diff_tracker.py` | Per-run file-change aggregation |
| `revert.py` | Snapshot-based revert (no git): each touched file restored to its before-run state |
| `model_resolver.py` | Capability-driven three-state vision routing |
| `vision_sidecar.py` | Vision sidecar: describes image attachments with the configured vision model and injects the *text description* into the main model's request |
| `route.py` | Run-time intent routing for the single-agent main path (reuses keyword rules, zero extra LLM round-trips) |
| `messages.py` | System injection templates and message normalization |

## thinking/ subsystem

| File | Responsibility |
|------|----------------|
| `engine.py` | Thinking engine (reasoning-mode decisions) |
| `processor.py` | Streaming think-block state machine |
| `config.py` | Thinking configuration (session-level, not per-agent) |
| `delegation.py` | Background sub-agent delegation with a per-parent concurrency cap |
| `subagent.py` | Sub-agent sessions |
| `fallback.py` | Three-state fallback machine: switches model/provider when the primary fails |

## Key contracts

- **Events**: `session.created/updated/message/deleted/...` are published by the API layer after persistence; the run family is `run.created/chunk/done/completed/error/cancelled/retry/diff/usage/vision_downgrade`; transient events are never persisted.
- **Stream coalescing**: WS text frames are coalesced with dual bounds (40ms / 512 chars) plus a timer-guaranteed drain; "a stalled stream must still drain" is an invariant.
- **Tool results**: `_`-prefixed keys are internal fields, stripped before reaching the LLM or persisted messages; file-change records use the `_path/_change_type/_diff/_additions/_deletions/_existed/_snapshot/_trash_id` convention keys; execution-side private kwargs `_agent_*` / `_approval_granted` / `_run_id` / `_tool_call_id` are never exposed to the model.
- **Compression**: results go only into `session.compressed_summary`; they must never enter history as messages.
- **Hooks**: run/tool/LLM/session hook points are triggered in this chain (`core.*` namespace); blockable vetoes use `{"blocked": True}`, rewrites use `{"data": {"content": ...}}`; hot-path hooks (e.g. `llm.transform_output`) run only when bound.
- **Fallback messages**: empty-reply / approval-timeout fallbacks must carry a `metadata.fallback` marker.
- **Workspace**: tool relative paths resolve against the session workspace (not the project root); the default workspace mode is configurable, falling back to `<data>/sessions/<id>/workspace` when config is unavailable.
