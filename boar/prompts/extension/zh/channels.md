# 通道扩展开发（channels）

> 用途：开发者中文参考版；LLM 消费英文版，如有出入以英文版为准。
> 位置：boar/prompts/extension/channels.md（英文权威源）

通道 = 把外部消息平台桥接进核心的扩展。两种形态——按平台性质选，不按口味选：

| 形态 | 适用 | 基类 |
|---|---|---|
| **消息适配器** | 请求/应答型平台；长连接、长轮询或 webhook 由你持有 | `ChannelAdapter` + `api.register_channel` |
| **普通扩展** | HTTP/WS 前端（webui），不是请求/应答平台 | 仅 `ExtensionBase` |

## 适配器契约

分工：**run 全归核心，传输全归适配器。** 连飞书、微信扫码登录、35s 长轮询、webhook 路由——都是你的事，核心不感知。核心只要求一个方法：

```python
import asyncio
from boarai.core.extension import ChannelAdapter, InboundMessage, OutboundMessage

class MyPlatformAdapter(ChannelAdapter):
    source_id = "myplatform"          # 必须与 manifest 的 source_id 一致

    async def start(self, ctx):       # 可选：打开传输
        self._ctx = ctx
        self._task = asyncio.create_task(self._poll_loop())

    async def send(self, message: OutboundMessage) -> bool:   # 必须实现
        """投递一条出站消息；传输失败返回 False。"""
        return await self._post(message.reply_token, message.text)

    async def stop(self):             # 可选：释放传输
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)

    async def _poll_loop(self):
        while True:
            for raw in await self._fetch():
                await self._ctx.submit(InboundMessage(
                    conversation_id=raw["chat_id"],   # 每个会话/用户稳定不变
                    text=raw["text"],
                    sender_id=raw["from"],
                    sender_name=raw["nickname"],
                    reply_token=raw["token"],         # 不透明；send 时原样带回
                ))
```

在 `initialize(api)` 里注册——这次调用就是插入口，返回值是你推入站消息用的 context：

```python
self._ctx = await api.register_channel(
    MyPlatformAdapter(), agent_name="default", run_timeout=300,
)
```

交消息进去有两种方式：

- `await ctx.submit(inbound)`——发后即忘；核心跑完这一轮并通过你的 `send()` 投递回复（或错误提示）。
- `result = await ctx.ask(inbound)`——返回 `ChannelRunResult`（`.text` / `.status` / `.error` / `.session_id` / `.run_id`），**不投递**，适合自己要分片、限速或改写答案的适配器。

`ctx.notice(conversation_id, text)` 用于主动推送（无入站轮次）。

## 宿主已经做完的事——不要重复实现

`api.register_channel` 给的 `ChannelContext` 承担整轮流程：

- **会话映射**：`ctx.session_id_for(conversation_id)`——推导式（对 `source_id:conversation_id` 取 uuid5），重启后稳定，无需映射表，两个平台不可能撞车。
- **会话初始化**：首次接触时创建，并套用与 WebUI 会话**相同**的全局默认值（model / agent / workspace）。
- **入站轮次**：落库用户消息并补发 `session.message` 事件，WebUI 因此能看到从你平台进来的轮次。（`add_message` 自身不发事件，宿主补发。）
- **run**：带 completion future 调 `create_run`，等到**完整最终答案**——不是流式碎片，无需过滤工具噪音。
- **串行化**：同一会话同时只跑一轮（否则 `create_run` 会打断活跃 run）；不同会话并行。
- **必回话**：超时、run 失败、流程异常、`send()` 抛错，全都会走一次投递——外部用户永远不会石沉大海。超时会取消 run。
- **task 卫生**：派发出去的轮次持强引用，不会被 GC 中途回收。
- **生命周期**：注册时 `start(ctx)`；禁用/卸载/被替换时 `stop()`（先取消在飞轮次）。`start()` 抛错**不会**取消注册——需要扫码/OAuth 握手的平台本来就是先注册后连上。

## 通道专属 manifest 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `source_id` | 通道 ✅ | 消息源身份（如 `wechat`）；启用扩展时自动注册进 SourceRegistry。必须与适配器的 `source_id` 相同 |
| `source_display` | 通道 ✅ | 人类可读名（如 `WeChat`） |

```yaml
name: wechat_bridge
extension_type: channel
source_id: "wechat"
source_display: "WeChat"
```

一个 `source_id` 只能有一个适配器。用别的扩展已占用的 source_id 注册会抛 `ChannelConflict`。

## 仍然适用的规则

- `reply_token` 对核心不透明：从 `InboundMessage` 带到你的 `send()`（宿主会在 `OutboundMessage.reply_token` 原样返回）。
- 一个外部会话 ↔ 一个 BoarAI session。**不要**让不同用户复用同一个 `conversation_id`，否则他们共享历史。
- `asyncio.CancelledError` 继承自 `BaseException`：`stop()` 里的 `except Exception` 抓不到取消——await 自己的 task 时要配套处理。
- 通道同样遵守单向依赖：可 import `core.*`，勿被 core import。

## 静态资源（Web 类通道）

扩展内 `static/` 目录由 loader 自动挂载到 `/static/<name>/` 下；代码里用 `api.static_url("index.html")` 取 URL（勿硬编码扩展 URL）。

## 底层配方（仅当你绕开宿主时）

需要宿主不提供的 run 控制时才用（例如流式碎片、一条消息跑多个 run）。否则 `ctx.submit` / `ctx.ask` 全包了：

```python
from boarai.core.event import EventType, publish_event
from boarai.core.session import get_run_manager, get_session_manager
from boarai.core.session.service import apply_session_defaults

sm, rm = get_session_manager(), get_run_manager()
session = await sm.get_session(session_id)
if session is None:
    session = await sm.create_session(session_id=session_id, agent_name="default")
    apply_session_defaults(session)      # model / agent / workspace

session.ensure_session_capability_inject()
message = session.add_message("user", content, metadata=metadata)
session.maybe_auto_title_from_user(content)
await sm.save_session(session_id)
await publish_event(EventType.SESSION_MESSAGE, {          # 补发，否则 WebUI
    "session_id": session_id,                             # 看不到这一轮
    "message_id": f"msg_{int(message.timestamp.timestamp() * 1000)}",
    "role": "user", "content": content,
    "timestamp": message.timestamp.isoformat(),
    "metadata": metadata, "seq": getattr(message, "seq", 0),
}, "<你的 source_id>")

future = asyncio.get_running_loop().create_future()
run_id = await rm.create_run(session_id, agent_name="default",
                             completion_future=future)
try:
    run = await asyncio.wait_for(future, timeout=run_timeout)
except asyncio.TimeoutError:
    await rm.cancel_run(run_id)
    run = None
reply = (run.full_response or "").strip() if run is not None \
    and run.status.value == "completed" else f"error: {run.error if run else 'timeout'}"
```

走这条路就要自己承担宿主替你做的四件事：按会话串行、失败必回话、task 强引用、超时取消 run。

## Pitfalls

- 带路径参数的路由（`/static/{path:.*}`）由 RouteRegistry 按前缀解析——不需要、也无法再走原生路由注册。
- **不要**用 `user.input` 事件投递入站消息：它没有消费者。用 `ctx.submit`（或上面的配方）。

## 参考实现

- 适配器契约：`core/extension/channel/adapter.py`（要实现什么）、`core/extension/channel/host.py`（核心替你做什么）。
- 普通扩展形态的通道：`boar/extensions/channels/webui/`——source_id/source_display + 路由注册 + 静态资源服务。
