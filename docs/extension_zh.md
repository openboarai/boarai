> [English](extension.md) | **简体中文**

# 扩展系统（core/extension）

扩展的发现、加载、manifest 契约、能力门面、钩子/路由/服务注册表、工具注册表、渠道宿主、MCP 桥接与技能体系。

## 隔离模型（单向分层依赖）

- core 永不 import 扩展；扩展可自由 import 任意 `boarai.core.*`（用户自有代码）。
- loader 的 import 检查只是 debug 级审计，不告警不拦截。
- 能力集是标准化边界而非安全沙箱：覆写 core 内部者自担兼容风险。
- 扩展间禁止直连：广播走事件总线，请求-响应走 `api.call_service`（只认命名空间，零直接依赖）。

## 模块构成

| 文件/目录 | 职责 |
|------|------|
| `base.py` | `ExtensionBase` ABC + `ExtensionError` |
| `manifest.py` | manifest 数据类与防御式解析 |
| `discovery.py` | 两层发现（system / user） |
| `loader.py` | 加载/卸载/热重载、contributes 挂载、MCP spawn、卸载清扫 |
| `manager.py` | 扩展生命周期管理、启停 |
| `api.py` | `ExtensionAPI` 能力门面（每扩展一个实例） |
| `state_store.py` | 启停状态持久化 `<数据目录>/extensions/state.json` |
| `verifier.py` | 扩展静态校验 |
| `scaffold.py` | 从模板生成扩展骨架 |
| `source_registry.py` | 渠道 source_id 注册 |
| `contribution/` | `HookRegistry` / `RouteRegistry` / `ServiceRegistry` + 核心 hook 点声明 |
| `tool/` | `ToolRegistry` / `ToolDefinition` / `ToolContext` / `@tool` 装饰器 |
| `channel/` | `ChannelAdapter` 契约 + `ChannelHost` |
| `mcp/` | `StdioMCPClient`（newline JSON-RPC over 子进程） |
| `skill/` | SKILL.md 技能存储/校验/管理/演化/监视 |

## 发现与目录

- system 层 `boar/extensions/<type>/<name>/`（随包只读），user 层 `<数据目录>/extensions/<type>/<name>/`（可装卸启停）；type 目录名固定：channels / plugins / tools / skills / mcp；同名 user 覆盖 system。
- 一个扩展必须有 manifest（`manifest.yaml` 正名，`extension.yaml` legacy 别名）+ 入口 `extension.py`，缺任一不发现。
- skills 类型不走 manifest 加载器，由 SkillStore 管理。

## manifest 与 contributes

顶层字段：name / version / description / extension_type(channel|plugin|tool|skill|mcp) / author / enabled / dependencies / pip_dependencies / min_boarai_version / config_defaults / provides / permissions / source_id / source_display + `contributes`。

`contributes` 合法 kind：`tools / routes / hooks / hook_points / services / mcp_servers / ui`。未知 kind 丢弃告警；条目缺关键字段丢弃；**局部降级，绝不使整个 manifest 解析失败**。

- tool：`id` + `handler: "code.py#func"` + schema + security_profile + path_param/shell_param + suggested_category + compress_strategy + timeout。
- route：method/path/handler/use_prefix（默认挂 `/api/ext/<id><path>`）。
- hook：event（`core.*` 或 `ext.<other>.*`）+ handler + order。
- hook_point：name（自动前缀 `ext.<id>.<name>`）+ semantics(observe|blockable|transform) + visibility。
- mcp_server：name（snake_case，成为工具名前缀）+ command/args/env/cwd/timeout/tool_timeout。
- ui：panel（仅 `kind: custom`，自家 iframe 页）+ actions 按钮；没有 config_schema——设置面板就是扩展自己的页面。

## 生命周期与门面

- 必须实现 `manifest` property 与 `initialize(api=None) / start / stop / cleanup` 四个 async 方法。
- 一切注册走注入的 `ExtensionAPI`：register_tool / register_route / register_hook / declare_hook / trigger_hook / register_service / call_service / register_channel / register_command / register_settings* / publish / subscribe / get_config / set_config / static_url / data_dir。
- 门面对动态注册的工具/命令维护 ownership 列表，卸载由 loader 统一清扫（路由/hook/service/middleware/命令逆序卸载）——扩展不要自造第二套注册表。
- `set_config` 原子落盘私有 `config.json`；写失败不上抛；随后回调 `on_config_changed`（缓存派生状态的扩展必须覆写）并发布 `extension.config_changed`；校验失败回退默认值，不 brick 加载。
- 全局 HTTP middleware 经 `ExtensionBase.register_middleware`，卸载自动清理防堆叠。

## 工具注册表

- 定义二选一：`@tool` 装饰器或 `BaseTool` 子类（import 即自注册）；声明式则走 manifest `contributes.tools`。
- handler 第一参数是注入的 `ToolContext`（session_id / workspace / run_id / project_root 等）。
- 能力用元数据自声明，core 按元数据施策略，绝不按工具名 hardcode。
- 注册表是洋葱模型 middleware 管道，异步/同步双锁保护。

## 渠道契约

- adapter 只实现 `send`（必须）+ `start(ctx)/stop()`（可选）；入站交活只有 `ctx.submit()` / `ctx.ask()` 两个入口。
- 分工铁律：core 拥有一切 run（会话映射、持久化、事件镜像、串行化、超时必回复），adapter 只拥有传输层；`reply_token` 是不透明句柄，core 原样往返。
- 渠道身份用 manifest 顶层 `source_id/source_display`。

## MCP 桥接

manifest 声明 `contributes.mcp_servers` → loader 自动 spawn 子进程、握手、注册工具（命名固定 `mcp_<server>_<tool>`）、卸载时注销工具并关闭客户端；一个坏 server 不阻塞同扩展其余 server。

## 技能体系

- 技能 = `SKILL.md`（Anthropic Agent Skills 开放标准）：frontmatter 只有 name + description，"何时用"写进 description。
- system 技能（`boar/extensions/skills/`）只读；agent 生成技能（`<数据目录>/extensions/skills/`）才可写。
- 提示词只注入一行索引（description 截断 60 字符），全文按需经技能查看工具加载。
- 技能删除走回收站软删除；不做定时自动归档（设计决定：按计时器销毁工作是错的信号）。
