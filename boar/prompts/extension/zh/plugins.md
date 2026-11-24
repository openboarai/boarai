# 插件能力开发（plugins）

> 用途：开发者中文参考版；LLM 消费英文版，如有出入以英文版为准。
> 位置：boar/prompts/extension/plugins.md（英文权威源）

## Hooks（管道拦截）

```python
async def initialize(self, api=None) -> None:
    # 声明自有 hook 点供他人绑定
    api.declare_hook("pre_send", "blockable", description="before send; return {'blocked': true} to veto")
    # 绑定到核心 hook 点
    api.register_hook("core.tool.pre_execute", self.before_tool, order=10)

async def before_tool(self, payload: dict) -> dict:
    # blockable: return {"blocked": True, "reason": "..."} to veto; else continue
    return payload
```

语义：`observe`（仅通知）/ `blockable`（首个 blocked 即停，first-deny-wins）/ `transform`（payload 依 order 流过各 handler）。

**15 个核心 hook 点全部已接线**：
- `tool.pre_execute`（可否决）/ `post_execute` / `on_error`
- `run.on_start` / `on_complete` / `on_error`
- `llm.pre_request`（上下文注入）/ `post_response` / `transform_output`
- `session.start` / `end` / `on_message`（消息拦截改写：`{"blocked": True}` 否决、`{"data": {"content": ...}}` 改写）
- `approval.pre_request`（可否决）
- `fs.pre_write`（可否决）/ `post_write`

注意：`terminal.transform_output` 已移除（无终端子系统）。

## HTTP 路由

```python
async def initialize(self, api=None) -> None:
    api.register_route("GET", "/summary", self.summary, use_prefix=True)

async def summary(self, request) -> "web.Response":
    from aiohttp import web
    return web.json_response({"ok": True})
```

- handler 签名：`async def xxx(request) -> aiohttp.web.Response`
- `use_prefix=True`（默认）挂 `/api/ext/<name>` 下；`False` 挂根路径（仅限 system 层受信扩展）
- 静态资源自动挂载：扩展内 `static/` 目录 → `/static/<name>/` 下；`api.static_url()` 取 URL
- 全局 middleware：`self.register_middleware` / `unregister_middleware`（ExtensionBase 方法），热加载扩展也可运行时注册

## Services（扩展间请求-响应）

```python
async def initialize(self, api=None) -> None:
    api.register_service({"stats": self.stats})

async def stats(self, params) -> dict:
    return {"count": 42}

# 另一扩展调用：
result = await api.call_service("my_ext", "stats", {}, timeout=5)
```

## 事件总线（广播）

```python
async def start(self) -> None:
    from boarai.core.event import EventType
    await self.event_bus.subscribe(EventType.LLM_REQUEST, self.on_llm_request)

async def on_llm_request(self, event) -> None:
    ...
```

- 发布：`await api.publish("my.custom.event", {...})`
- **新事件必须登记** `core/event/types.py` 的 `EventType`
- 用前先查消费者：`user.input` 等事件当前无人订阅，不能当通道用

## 扩展设置（可在 WebUI 配置）

一处**可选**声明让扩展在扩展页可配置：`contributes.ui` —— **自定义设置面板**（你自己的页面装在 iframe 里）+ 可选的动作按钮。没有它的扩展不显示「设置」按钮。原声明式 `config_schema` 表单已移除：需要比静态输入框更多交互的设置页，自己写 HTML，并经 `/static/js/boar-sdk.js` 与核心通信。

### 面板 —— `contributes.ui`

```yaml
contributes:
  ui:
    panel:
      title: "WeChat iLink"
      title_i18n: {zh: "微信 iLink"}
      icon: "qr-code"              # lucide 图标名
      kind: custom                 # 唯一取值（默认）
      custom_url: "/static/<id>/settings.html"
      custom_height: 480
    actions:
      - id: login_qr
        label: "Get QR code"
        label_i18n: {zh: "获取二维码"}
        icon: "qr-code"
        style: primary             # primary | default | danger
        confirm: "确定重新登录？"    # 可选二次确认文案
        timeout: 20                # 秒，钳制在 1..120
        handler: "code.py#action_login_qr"
```

面板是一个 iframe，加载你自己的页面（`static/` 目录自动挂载在 `/static/<id>/`）。UI 完全自由：输入框、拖拽排序、图表、流式日志、多步向导。URL 必须以 `/static/<id>/` 或 `/api/ext/<id>/` 开头，否则被拒（面板显示错误并记一条 warning）。

### 页面 —— `boar-sdk.js`

先加载 SDK，然后使用全局 `boar` 对象。扩展 id 从 iframe URL 取（`?ext=<id>`，设置弹窗会自动带上）：

```html
<script src="/static/js/boar-sdk.js"></script>
<script>
  const cfg = await boar.getConfig();            // 你的 config.json 值
  await boar.saveConfig({ key: value });         // 合并写入 + 实时生效
  const v = await boar.getData("cache");         // 私有数据 map 的一个值
  await boar.saveData("cache", { a: 1 });        // 合并写入 data.json
  const r = await boar.callAction("login_qr");   // 执行声明过的动作
</script>
```

| 方法 | 端点 | 语义 |
|---|---|---|
| `boar.getConfig()` | `GET /api/extensions/{id}` → `settings.values` | 无持久化时为 `{}` |
| `boar.saveConfig(values)` | `PUT .../settings` | 合并进 `config.json`，回调你的 `on_config_changed`，发布 `extension.config_changed` |
| `boar.getData(key)` | `GET .../data` | 取私有 map 的一个值 |
| `boar.saveData(values)` | `PUT .../data` | 合并进 `data.json` —— 核心不解释内容 |
| `boar.callAction(id, params)` | `POST .../actions/{id}` | 返回结果信封（见下） |

存储：`<data>/extensions/data/<id>/config.json` 与 `data.json` —— 扩展私有数据目录（与源码目录分离，**卸载不删数据**）。Python 侧读用 `api.get_config()`；写用 `await api.set_config({...})`（同样的落盘 + 实时生效语义）。**没有 schema 校验**：类型、默认值、错误提示全由你的页面自己负责。两个 map 都拒绝超过 256 KB 的载荷。

错误：每个 SDK 调用在失败时 reject，错误信息来自服务端；在页面里 catch 后就地展示。

**面板多语言。** 设置弹窗会在 iframe URL 上追加 `lang=<当前 WebUI 语言>`。文案由你的页面自己负责：在 `static/i18n/<lang>.json`（`zh.json`、`en.json`…）里自带词条，读 `lang` 参数后 fetch 自己的 bundle，缺失时回退英文字面量。**不要**把扩展文案塞进 WebUI 的全局 i18n 文件。

参考实现：`boar/extensions/tools/web_tools/static/settings.html`（搜索引擎优先级编辑器）。

### 动作 handler 契约

```python
async def action_login_qr(ctx) -> dict:
    # ctx = {"extension_id", "config", "params", "request_id"}
    return {
        "ok": True,
        "message": "请用微信扫码",             # toast
        "view": {"type": "image", "src": "/api/ext/<id>/qr.png"},
        "poll": {"action": "login_status", "interval_ms": 2000, "max_ms": 150000},
        "reload": True,                        # 成功后重拉设置
    }
```

`view.type` 只能是 `image`（`src`/`alt`）、`kv`（`rows: [{label, value}]`）、`markdown`（`text`）、`text`（`text`）——单个 dict 或列表；其它类型被丢弃。`poll` 让前端每 `interval_ms` 重跑另一个动作，直到该动作不再返回 `poll` 或超过 `max_ms`。

以下是**强制**的，不是建议：

- **必须快速返回。** 超时默认 15 秒、上限 120 秒。长等待（扫码、设备配对、构建）必须立即返回并交给 `poll` —— 阻塞 handler 就是阻塞 HTTP 请求。
- **不要塞负载。** 信封上限 64 KB，图片必须是同源 URL，**绝不 base64**；字节从你自己的路由提供。
- **失败写在信封里。** 返回 `{"ok": False, "message": …}` 或直接抛异常——两者都会变成 `ok: false` + HTTP 200，面板得以在按钮旁显示原因。只有扩展/动作 id 不存在才是 404。

### 运行时注册

manifest 声明走的就是下面这些门面方法，所以你也可以在运行时（`initialize()` / `start()` 里）注册——用于只有了解环境后才有意义的面板或按钮：

```python
api.register_settings(panel={"title": "我的扩展",
                             "custom_url": "/static/<id>/settings.html"})
api.register_settings_action("ping", self._action_ping, label="Ping", icon="activity")
api.unregister_settings_action("ping")
```

运行时 panel 键按 key **覆盖** `contributes.ui.panel`，且不持久化——每次 `initialize()` 都要重新注册。旧的 `register_settings(fields)` 参数是弃用的 no-op。

### HTTP 面

| 端点 | 用途 |
|---|---|
| `GET /api/extensions/{id}` | `settings: {panel, actions, values, loaded}` |
| `PUT /api/extensions/{id}/settings` | `{"values": {...}}` → 落盘、实时生效 |
| `GET/PUT /api/extensions/{id}/data` | 扩展私有数据 map（`{"values": {...}}`） |
| `POST /api/extensions/{id}/actions/{action_id}` | `{"params": {...}}` → 结果信封 |

配置写入对**未加载**的扩展同样有效（已停用、或被信任门禁拦住）：直接写入数据目录，下次加载时合并——这正是「首次启动前先补上缺失的 API Key」的路径。动作则需要扩展已加载。

## 已知边界

涉及能力可用性的全局限制（WebUI 动态 tab、`user.input` 无消费者、自定义 provider 注册不等于生效、深度沙箱等）统一见 common.md 的"已知边界"表。

## 参考实现

- `boar/extensions/channels/webui/extension.py` —— event_bus.subscribe
- `boar/extensions/tools/file_tools/` —— declare_hook
