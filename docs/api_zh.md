> [English](api.md) | **简体中文**

# REST/WS API 层（core/api）

系统级 API handler 层：一个领域一个文件，供任意渠道扩展（WebUI、CLI 等）复用。业务语义在领域服务里，handler 只做传输适配。

## 模块构成

| 文件 | 领域 |
|------|------|
| `session.py` | 会话 CRUD、消息、run 创建/订阅；WS `session.*` / `tool.*` 消息处理与按会话广播 |
| `run.py` | run 记录查询 |
| `agent.py` | 角色（agent）管理 |
| `llm.py` | 供应商/模型配置与连通性 |
| `auth.py` | 访问钥匙端点（纯传输层：钥匙换设备会话） |
| `owner.py` | 用户资料与设置 |
| `security.py` | 安全配置（守卫、审批策略） |
| `approvals.py` | 带外审批：待批工具列表与裁决 |
| `extension.py` | 扩展管理（列表/启停/装卸/设置/数据/动作，含加载失败可见性） |
| `memory.py` | 记忆查询与管理 |
| `files.py` | 会话附件上传/下载 |
| `fs.py` | WebUI 文件管理器的目录浏览/读/写 |
| `logs.py` | 日志只读检视 |
| `monitoring.py` | token 与缓存统计 |
| `trash.py` | 回收站列表/恢复/清空 |
| `update.py` | 在线更新 check/apply/status/release notes |

（任务领域 handler 在 `core/task/api.py`，随任务子系统存放，经本包 re-export。）

## 组织约定

- 每个文件导出 `XxxAPIHandler` 类 + `get_xxx_api_handler()` 单例函数，`__init__.py` 统一 re-export。
- 路由挂载在统一服务器的 `_setup_routes()`；本层不自己起服务器、不声明监听地址。
- JSON 请求体解析用 `core/runtime/json_body.py` 共享实现：畸形输入必须 4xx，绝不 500。
- 全部 `/api` 端点在同一访问门禁之后；公开路径的增减必须同步门禁与其测试。

## HTTP / WS 双通道契约

HTTP 与 WS 是同一领域的两个视图：

- 同一业务动作两个入口共享同一领域服务实现（如 run 创建：REST 与 WS 消息 `session.run.create`，响应类型 `session.run.create.response`；WS 侧还有 `session.run.get/cancel/subscribe` 族）。
- **新增/修改 API 必须双通道同步**：字段、语义、广播范围一致；有 parity 契约测试盯守。
- 广播范围两级：全局（所有 WS 客户端）与按会话订阅；选哪级要写进事件登记注释。会话删除等全局事件同时走 HTTP 响应与 WS 广播。
- 领域服务是单一实现：`core/session/service.py` 同时喂 REST 与 WS，handler 不得各写一份业务逻辑。
