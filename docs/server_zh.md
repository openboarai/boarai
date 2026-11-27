> [English](server.md) | **简体中文**

# 统一服务器（core/server）

HTTP 与 WebSocket 同端口同进程的统一服务器，是全部 REST 端点、WS 通道与扩展路由的宿主。

## 模块构成

| 文件 | 职责 |
|------|------|
| `unified_server.py` | `UnifiedServer`：路由挂载、动态中间件池、WS 端点与广播 |
| `manager.py` | `ServerManager` 单例（`get_server_manager()`），中间件热增删入口 |
| `run_events.py` | 终态 run 事件的 WS 载荷构造与全局兜底转发 |

## 监听地址

- 唯一真源在本模块：`BOARAI_HOST` / `BOARAI_PORT` 环境变量，默认 `0.0.0.0:26620`。
- 扩展与其 manifest 不得声明 host/port——WebUI 共享核心服务器端口，声明了也是不起作用的假旋钮。
- 默认绑定非回环，由传输层访问门禁兜底；绑定契约有测试盯守，两个方向都不许放宽。

## 路由组织

- 内核 REST 路由在 `_setup_routes()` 集中挂载，指向 `core/api/` 各领域 handler；门禁端点（auth）也在这一层。
- 扩展路由**只存在于 RouteRegistry**（单一真源），由中间件兜底分发——热增删不需要重建 aiohttp router。
- 动态中间件池：aiohttp 的 `app.middlewares` 在 router 启动后冻结，运行时注册（热加载扩展）经 `_dynamic_middleware_dispatch` 走可变列表；该分发器只在 `__init__` 挂一次。

## WebSocket

- 端点 `GET /ws`，子协议 `boarai.v1`；客户端集合 `_ws_clients`，连接时同步注册进会话 API 的广播集并启动心跳。
- 入站消息按 `type` 前缀分派：`session.*` / `tool.*` 委托给 `SessionAPIHandler._process_ws_message`，其余类型显式回 `Unsupported message type`；请求-响应消息带 `id` 回显。
- 断连必须清理按会话订阅（`_discard_ws_client`）：刷新/关页不会发 unsubscribe，僵尸客户端会让后续 run 事件报 "Cannot write to closing transport"。
- `broadcast()` 群发前对客户端集合做快照，发送失败的连接批量剔除。
- 流式只走 WS，没有 SSE。

## 平台噪声治理

Windows proactor 在客户端断连（页面刷新）时会向事件循环抛 ERROR traceback——`_loop_exception_handler` 只吞这类已知拆卸噪声（ConnectionReset/Aborted/BrokenPipe + `_call_connection_lost`），其余照旧走默认处理。

## 关停

`stop()` 关闭全部 WS 连接（快照迭代，close 可能触发 discard）、卸载 runner；扩展中间件与路由的热清理归扩展加载器负责。

## 反向代理与 Origin 校验

- 带 `Origin` 头的请求（浏览器发出的一切 POST）必须与实例的**公网主机名**同源；比较只认主机名，http/https 差异被容忍（TLS 通常在反代终结，服务端 socket 看到的是 http）。
- **端口仍在比较中**：Cookie 不区分端口，同主机异端口的恶意页面属于同站（SameSite=Strict 照样带会话 Cookie），端口是唯一能挡它 POST 的东西；省略端口按 scheme 默认归一（`https://host` 匹配 `host:443`，`https://host:8443` 不匹配裸 `host`）。
- 公网主机名零配置推导：`Host` 头 + `X-Forwarded-Host` 的**最后一个**逗号分量（代理追加链中离服务端最近的一跳；客户端伪造的首分量不作数）。Caddy / Traefik / Cloudflare / 配了 `proxy_set_header Host $host` 的 nginx 开箱即用。
- 裸 `proxy_pass`（nginx 默认把 Host 写成内部 upstream、又不加 X-Forwarded-Host）时服务端无从得知公网域名：用环境变量 `BOARAI_ALLOWED_ORIGINS=https://你的域名` 显式声明，逗号分隔多个。
- 拒绝返回 403 + `error.code = origin_not_allowed`，文案直接给出上面两条出路；前端 setup/unlock 页有对应翻译。
