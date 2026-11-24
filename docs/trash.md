> **English** | [简体中文](trash_zh.md)

# Recycle Bin (core/trash)

Soft-delete recycle bin plus pre-write file snapshots. The single exit for deleting user data across the system: **business code must never hard-delete**.

## Storage layout

```
<data>/trash/
├── trash.json          ← index of all trash entries
├── sessions/{ts}_{session_id}/
├── agents/{ts}_{agent_name}/
├── skills/{ts}_{skill_name}/     ← whole directory
└── files/{ts}_{hash8}__{filename}

<data>/snapshots/files/{ts}_{hash8}__{filename}   ← pre-write snapshots
```

## TTLs (per type)

| Type | Retention |
|------|-----------|
| Sessions | 7 days |
| Agents / memory / skills / extensions | 30 days |
| Files / snapshots | 48 hours |

Expired entries are cleaned hourly (`CLEANUP_INTERVAL`).

## Key contracts

- Deletion of sessions, agents, skills, memory and files all goes through `TrashManager` into the bin and is restorable; after restoring an agent, derived state such as the default flag is repaired by the respective API layer.
- Snapshots back write/edit undo: file tools store a snapshot before mutating; run-level revert and the session changes panel consume it (consumers live in the session subsystem).
- Memory distillation consumes trash entries as input (see the memory doc); entries carry a trash_id through distillation state.
- Blocking file moves run via `asyncio.to_thread`, never synchronously on the event loop.
- The REST surface is provided by the trash domain API (list/restore/empty).
