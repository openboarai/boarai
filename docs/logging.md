> **English** | [简体中文](logging_zh.md)

# Logging System (core/logging)

One root logger, several sinks, one writer thread; logs land under the dated tree `<data>/logs/<YYYY-MM-DD>/`.

## Modules

| File | Responsibility |
|------|----------------|
| `manager.py` | `LogManager` pipeline wiring (`get_log_manager()` / `get_logger()`), logger-name → category-sink mapping |
| `config.py` | `LoggingConfig` / `LogLevel`: every field is wired — a knob that exists but does nothing is worse than no knob |
| `handlers.py` | `DatedCategoryHandler` and friends: per-date, per-category handlers |
| `formatters.py` | `SafeFormatter`: the wrapper every sink uses |
| `context.py` | Per-coroutine log context (disambiguates sources in the mixed `all.log`) |
| `redact.py` | Secret redaction for every log sink |
| `crash.py` | Crash and uncaught-exception capture (the three failure paths that bypass the logging system) |
| `retention.py` | Retention: age budget plus a total-size budget |
| `reader.py` | Read-only access to the log tree (pure functions, serving the log inspection API) |

## Behaviour contract

- Logs are the one artifact that outlives a run and leaves the machine — **every sink redacts before writing**; the sensitive pattern list is shared with the config schema.
- Retention is double-bounded: age alone is not a bound — a runaway retry loop can write gigabytes in a day, so a total-size budget is mandatory.
- The category mapping folds logger names into sinks (e.g. `core.extension` / `boarai.core.extension` / `boar.extensions` / `extensions` all land in `extension.log`); new top-level package names must be added to the mapping table.
- The read side (reader) is a set of pure functions over the dated directory layout and holds no state; the logs API is read-only.
- The event bus keeps its own JSONL audit trail (see the event system doc), outside this pipeline.

## Coding convention

Business modules use `logger = logging.getLogger(__name__)`; `print` is forbidden (sole exception: CLI user output in entry modules).
