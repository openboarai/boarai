> **English** | [简体中文](task_zh.md)

# Task System (core/task)

Scheduled/periodic tasks: models, state machine, scheduler, executors and the task API.

## Modules

| File | Responsibility |
|------|----------------|
| `models.py` | Task data models |
| `kind.py` | Task kind definitions |
| `status.py` | Task state machine definitions |
| `manager.py` | Task manager (CRUD, lifecycle) |
| `scheduler.py` | Task scheduler |
| `storage.py` | Task persistence |
| `api.py` | Task REST handler (re-exported through the core/api package) |
| `events.py` | Task event helpers |
| `timeutil.py` | Timezone helpers: naive datetime in the target timezone's wall clock |
| `executor/base.py` | Executor ABC |
| `executor/ai_executor.py` | AI task executor (runs through the session run chain) |
| `executor/script_executor.py` | Script task executor |

## Key contracts

- The task event family `task.created/updated/status_changed/progress/completed/failed/cancelled/step_completed/sub_created` is registered in the event catalogue and forwarded to WS clients by the startup orchestration (live UI updates).
- Executors dispatch by task kind: AI tasks reuse the session run chain; script tasks execute independently; new kinds add an executor rather than modifying the scheduler.
- Scheduling time semantics are "target-timezone wall clock": naive datetimes are stored and all conversion is centralized in `timeutil.py`, never scattered.
- Task recovery after process restart is pinned by a dedicated contract test.
