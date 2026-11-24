> **English** | [简体中文](curator_zh.md)

# Background Curation (core/curator)

Background curation that keeps the self-improvement stores from rotting: timer-driven deduplication of global lessons.

## Current responsibility

One pass does one thing: duplicate lessons sharing a topic in `pod://global` are merged — the most important (then newest) entry survives, the rest are soft-deleted.

## Explicitly NOT done (design decisions)

- **No timer-driven skill archiving.** An older implementation sent agent skills with `use_count == 0` and an old mtime to the recycle bin; it was removed, for reasons recorded in the module docstring:
  - it measured the wrong thing — `skill_view` also bumps `use_count`, so "read once" was indistinguishable from "offered a matching task and ignored";
  - it destroyed work on a timer with no signal in the conversation and no way for the user to see why a skill disappeared;
  - it ran alongside a second 90-day archiver unaware of its existence, so the same skill carried two expiry dates and the harsher one won.
- Keeping the index small is now an **authoring decision**, not a cron job: the distillation prompt prefers updating an existing skill over creating a near-duplicate; the skill management tool reports similar skills on creation; genuinely dead skills are deleted explicitly by the model.

## Constraints

- The timer is coarse and **never runs inside a conversation turn** — curation adds no latency to chat.
- Every action is recoverable: soft-delete only, never a hard delete.
