> [English](event.md) | **简体中文**

# 事件系统（core/event）

事件总线是 BoarAI 跨边界通信的默认通道：扩展之间靠发布事件互相触达而不是互相 import，核心模块之间仍允许直接调用。

## 模块构成

| 文件 | 职责 |
|------|------|
| `types.py` | 事件目录（`EventType`）与事件信封（`Event`） |
| `bus.py` | 异步发布/订阅总线 `EventBus` + JSONL 审计 `EventBusLogger` |

## 事件目录 EventType

- 凡上总线的事件必须在 `EventType`（`str, Enum`）登记为字符串常量，发布/订阅两端禁止魔法字符串。
- 命名 `{domain}.{action}`，按所属子系统分组：system / llm / session / fs / run / tool / config / auth / update / thinking / user / task / extension / agent / memory / prompt / skill。
- WebSocket 镜像事件复用同一字符串值；线名不同时在传输层翻译。
- `plugin.*` 五个事件是重命名前的历史兼容别名，新代码一律使用 `extension.*`。
- 瞬态事件（如 `run.usage`、`run.vision_downgrade`）在登记注释中标明"永不持久化"。

## 事件信封 Event

字段：`event_type` / `payload`（dict）/ `source` / `timestamp`，提供 `to_dict()` / `from_dict()` 序列化对。消息类载荷宜含 `text` / `content` 键。

## EventBus 行为约定

- 处理器必须 `async def`，签名接收 `Event` 信封（`EventHandler = Callable[[Event], Awaitable[None]]`）。
- 处理器按优先级顺序串行执行；单个处理器失败绝不中断发布链路。
- 模块级便捷入口：`get_event_bus()` 取单例、`publish_event(type, payload, source)` 发布。

## 审计 EventBusLogger

- 追加式 JSONL 审计总线流量；发布记录含载荷序列化大小，处理器只记失败不记成功。
- 高频流式事件（`run.chunk`、`thinking.delta`、`tool.output`）在 `SKIP_EVENT_TYPES` 跳过名单内；新增同类流式事件须同步进名单。
- 单日审计文件有 20MB 上限（`MAX_DAILY_BYTES`），超限后当日丢弃计数；按日期建子目录，闲置日不留空目录。
