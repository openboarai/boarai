> **English** | [简体中文](event_zh.md)

# Event System (core/event)

The event bus is BoarAI's default cross-boundary channel: extensions reach each other by publishing events instead of importing sibling packages, while core modules may still call each other directly.

## Modules

| File | Responsibility |
|------|----------------|
| `types.py` | Canonical event catalogue (`EventType`) and the event envelope (`Event`) |
| `bus.py` | Async publish/subscribe `EventBus` + JSONL audit trail `EventBusLogger` |

## Event catalogue EventType

- Every event travelling over the bus must be declared in `EventType` (a `str, Enum`) as a string constant; publishers and subscribers must never use magic strings.
- Naming follows `{domain}.{action}`, grouped by owning subsystem: system / llm / session / fs / run / tool / config / auth / update / thinking / user / task / extension / agent / memory / prompt / skill.
- WebSocket mirrors reuse the same string value; when the wire name differs, the translation lives in the transport layer.
- The five `plugin.*` events are legacy aliases from before the rename; new code must use `extension.*`.
- Transient events (e.g. `run.usage`, `run.vision_downgrade`) are marked "never persisted" in their registration comments.

## Event envelope Event

Fields: `event_type` / `payload` (dict) / `source` / `timestamp`, with a `to_dict()` / `from_dict()` serialization pair. Message payloads should carry `text` / `content` keys.

## EventBus behaviour contract

- Handlers must be `async def` taking the `Event` envelope (`EventHandler = Callable[[Event], Awaitable[None]]`).
- Handlers run sequentially in priority order; a failing handler never aborts the publish path.
- Module-level helpers: `get_event_bus()` for the singleton, `publish_event(type, payload, source)` to publish.

## Audit trail EventBusLogger

- Append-only JSONL audit of bus traffic; publish records include the serialized payload size; handler executions are logged on failure only.
- High-frequency streaming events (`run.chunk`, `thinking.delta`, `tool.output`) are on the `SKIP_EVENT_TYPES` skip list; new streaming events of the same kind must be added to it.
- Each day's audit file is capped at 20MB (`MAX_DAILY_BYTES`); over-cap events are dropped and counted. Date subdirectories are created lazily so idle days leave no empty dirs.
