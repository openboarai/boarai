> [English](CODE_STANDARDS.md) | **简体中文**

# BoarAI 代码规范（代码实况版）

> **适用对象**：所有向 BoarAI 提交代码的人与 Agent。规范分三级：【硬边界】违反即返工；【约定】新代码必须遵守；【风格】保持一致性。

---

## 一、仓库布局：代码写在哪

| 用途 | 路径 | 说明 |
|------|------|------|
| 分发包 / 控制台入口 | `boarai/`（`__main__.py`） | PyPI 包名 `boarai`；`main.py` 只是源码树薄包装，三种入口（`python main.py` / `boarai` 脚本 / `python -m boarai`）共享同一实现（`main.py:4-9`） |
| 微内核 | `boarai/core/` | import 前缀一律 `boarai.core.*`；`core` 不叫顶层 `core` 是为了防撞进 site-packages（`boarai/__init__.py:5-7`） |
| 系统扩展（随包只读） | `boar/extensions/{channels,tools,plugins,skills,mcp}/` | 目录名即扩展类型（`core/extension/discovery.py:26-32`） |
| 扩展脚手架模板 | `boar/extensions/templates/{hello,full}/` | 新扩展从这里抄骨架 |
| 机器提示词资产 | `boar/prompts/` | 由 `core/prompt/loader.py` 集中加载，提示词文本不写死在 py 里（`loader.py:4-12`） |
| 首启种子数据 | `boar/defaults/` | 仅首次运行复制一次，之后永不覆盖（`core/runtime/bootstrap.py:4-12`） |
| 内置模型目录 | `boar/config/models.yaml` | 每安装的修正写用户数据目录的 `llm.yaml`，不改这里（`core/llm/catalog.py:13`） |
| 测试 | 工作区根 `tests/` | 位于仓库外；runner 自行向上寻根（`tests/run.py:41-51`） |
| 运行时用户数据 | `~/.boarai/`（数据目录锚） | config/sessions/logs/memory/trash/agents/extensions；**仓库内不得跟踪任何运行时数据** |
| 人读设计文档 | `docs/` | 中英双版，命名 `xxx.md` + `xxx_zh.md` |

**放置规则（新功能进哪）**

- 内核级基础设施（跨子系统复用、无业务语义）→ `boarai/core/runtime/`（原子写、LRU、后台任务、数据锚均在此）。
- LLM 全链路（协议/供应商/路由/门面）→ `boarai/core/llm/`；**内核其他目录禁止出现 LLM 调用**。
- REST 端点 → `boarai/core/api/<domain>.py`，一个领域一个文件，导出 `XxxAPIHandler + get_xxx_api_handler()` 单例对（`core/api/__init__.py:8-15`）。
- 会话/运行循环逻辑 → `boarai/core/session/`；单个 turn 的阶段拆在 `session/run/turn_*.py`，不往 `manager.py` 里堆。
- 提示词组装/压缩 → `boarai/core/prompt/`。
- 安全守卫 → `boarai/core/security/`；文件文本操作 → `core/fs/`；回收站 → `core/trash/`；shell 执行后端 → `core/shell/`。
- 一切业务工具（文件、shell、web、记忆、任务、技能）→ **扩展**，不进内核（`boarai/core/__init__.py:8`：“All business logic should be implemented as plugins”）。

---

## 二、依赖硬边界【硬边界】

| 方向 | 规则 | 证据 |
|------|------|------|
| `core/` → 扩展 | **禁止** import / 直接调用。core 全树对 `boar.extensions` 零 import（仅日志分类里出现字符串） | `core/logging/manager.py:137` 是字符串映射而非 import；`core/extension/api.py:12-15` 声明单向分层模型 |
| 扩展 → `core/` | **允许** import 任意 `boarai.core.*`，无白名单；loader 的 import 检查只是 debug 审计，不告警不拦截 | `core/extension/api.py:12-19`；实例 `boar/extensions/tools/file_tools/tools.py:17-23` |
| 扩展 A → 扩展 B | **禁止**直连；只能走 `api.call_service`（请求-响应）或事件总线（广播） | `core/extension/api.py:412-428`、`core/event/bus.py:1-7` |
| `core/` 内部 | 允许分层直接 import，避免循环依赖（重模块在函数内延迟 import，如 `extension/base.py:122`） | — |

**内核第三方依赖白名单**：`aiohttp` / `pyyaml` / `aiofiles` / `tiktoken`（可选 `psutil`，import 必须带守卫、缺了降级）。禁止引入 vendor LLM SDK（openai/anthropic/langchain 等）——providers 全部基于 aiohttp 手写（`pyproject.toml:14-23`、`core/llm/providers/base.py:31`）。扩展自有依赖写进 manifest 的 `pip_dependencies`（`requirements.txt:20-21`）。

---

## 三、内核模块分工表

| 目录 | 职责 | 关键符号 |
|------|------|----------|
| `core/event/` | 事件目录 + 异步总线 + JSONL 审计 | `EventType`、`Event`、`EventBus`、`publish_event` |
| `core/config/` | config.yaml 解析/schema/损坏回退 | `ConfigParser`、`BoarAiConfig` |
| `core/runtime/` | 数据锚、项目根锚、原子写、首启引导、前台 serve、WS 转发接线 | `get_data_dir`、`resolve_data_path`、`set_project_root`、`write_text_atomic_async`、`copy_defaults` |
| `core/server/` | HTTP+WS 同端口统一服务器（默认 0.0.0.0:26620）、动态 middleware、广播 | `UnifiedServer`、`broadcast` |
| `core/api/` | 各领域 REST handler + WS 会话消息处理 | `SessionAPIHandler` 等 |
| `core/security/` | PathGuard（路径沙箱）/ ToolGuard（工具权限）/ AuditLog / 访问门禁 / 脱敏 | `SecurityManager`、`get_access_store`、`redact` |
| `core/llm/` | Provider 协议、注册路由、门面、thinking 线协议 | `LLMProvider`、`LLMRequest/Response/StreamChunk`、`LLMError` |
| `core/session/` | 会话/消息/run 生命周期；`run/` 执行链；`thinking/` 推理流 | `SessionManager`、`RunBroadcaster`、turn_* 阶段件 |
| `core/prompt/` | 三区组装、压缩、agent 存储（soul.md + agent.yaml）、token 计数 | `PromptAssembler`、`AgentStore` |
| `core/extension/` | 扩展发现/加载/manifest/API 门面/hook-路由-服务注册表/tool 注册表/channel host/MCP 客户端/skill 体系 | `ExtensionBase`、`ExtensionAPI`、`ToolRegistry`、`HookRegistry` |
| `core/memory/` | 记忆 pod 存储、突触网格、蒸馏、 consolidation | `soft_delete_entry`（`manager.py:136`） |
| `core/task/` | 任务模型/调度/执行器（ai + script） | `TaskScheduler`、executor/ |
| `core/trash/` | 软删除回收站 + 写前快照；分类型 TTL（会话 7 天、agent/记忆/技能 30 天、文件 48h） | `trash_manager.py:36-45` |
| `core/command/` | 聊天内 `/命令` 拦截（handler/prompt 两种 kind）+ 进程管理子命令 | `chat.py:1-22` |
| `core/logging/` | 日志管道、脱敏、保留策略、读取 API | `get_logger`、`redact` |
| `core/update/` | 在线更新（服务器不跑 pip，spawn 独立 worker） | `service.py:1-19` |
| `core/fs/` | 文本读写 + `core.fs.pre_write/post_write` hook 接线 | `textops.py:306,333` |
| `core/shell/`、`core/process/`、`core/dependency/`、`core/curator/` | shell 后端、进程管理、依赖检查、记忆后台整理 | — |

---

## 四、事件系统【硬边界】

1. **凡上总线的事件必须在 `core/event/types.py::EventType` 登记**，发布/订阅两端禁止魔法字符串（`types.py:1-7` 明文要求）。
2. 命名 `{domain}.{action}`（`run.chunk`、`memory.synapse.created`），按所属子系统分组注释（`types.py:13-177`）。
3. 处理器必须 `async def`，签名收 `Event` 信封（`bus.py:19-20`）；处理器按优先级顺序执行，单个失败不得中断发布链（`bus.py:5-7`）。
4. 高频事件（`run.chunk`、`thinking.delta`、`tool.output`）在审计日志跳过名单里，新增同类流式事件要同步进 `SKIP_EVENT_TYPES`（`bus.py:29-33`）。
5. 需要 WS 镜像的事件：在 `core/runtime/serve.py::_setup_task_ws_forwarding` 的转发清单登记（`serve.py:310-376`），并同步前端 `store.js` 分发（见第十一节）。三处（EventType / serve 转发 / store.js）缺一即视为未完成。
6. `plugin.*` 事件是历史兼容别名，新代码一律用 `extension.*`（`types.py:21-26`）。

---

## 五、HTTP / WS 双通道【硬边界】

- HTTP 与 WS 同端口同进程（`core/server/unified_server.py:1-6`）；WS 端点 `/ws`，子协议 `boarai.v1`（`unified_server.py:174-175,763`）。
- WS 入站消息按 `type` 前缀分派：`session.*` / `tool.*` 委托给 `SessionAPIHandler._process_ws_message`（`unified_server.py:795-799`），其余类型显式报 Unsupported——新增 WS 动作必须在这条链上登记。
- 同一业务动作 REST 与 WS 两个入口共享同一 handler 层语义：如 run 创建同时有 REST 与 `session.run.create` WS 消息，响应类型 `session.run.create.response`（`core/api/session.py:1363-1658`）；聊天命令三入口（WS / REST / ChannelHost）共用 `core/command/chat.py` 单一实现（`chat.py:18-21`）。
- **新增/修改 API 必须双通道同步**：字段、语义、广播范围一致；有 parity 契约测试盯着（`tests/test_session_service_parity.py`）。流式只走 WS，没有 SSE。
- 广播分两级：全局 `UnifiedServer.broadcast`（`unified_server.py:812-825`）与会话作用域（session API 维护的订阅集，断连必须 `_discard_ws_client` 清理，`unified_server.py:778-784`）。选择哪级要写进事件登记注释（参照 `types.py:47-57` 的写法）。
- 扩展路由只存在于 `RouteRegistry`（middleware 兜底、热增删不重建 router）（`unified_server.py:103-104`）；扩展 middleware 走动态池（`unified_server.py:97-102`）。
- 监听地址唯一真源在 `core/server/unified_server.py`（`BOARAI_HOST`/`BOARAI_PORT`，默认 `0.0.0.0:26620`）；**扩展与其 manifest 不得声明 host/port**（`boar/extensions/channels/webui/extension.yaml:12-14`、`extension.py:35-39` 两处注释明说这是故意的）。绑定默认值有契约测试（`tests/test_server_bind_default.py`），不许放宽。

---

## 六、扩展开发规范

### 6.1 目录与发现

- 两层扫描：system = `boar/extensions/<type>/<name>/`，user = `<数据目录>/extensions/<type>/<name>/`；同名 user 覆盖 system（`discovery.py:20-23,76-89`）。
- 一个扩展目录**必须**有：manifest（`manifest.yaml` 为正名，`extension.yaml` 为 legacy 别名）+ 入口 `extension.py`（含 `ExtensionBase` 子类），缺任一即不发现（`discovery.py:130-144`）。
- skills 类型不走 manifest 加载器，由 `SkillStore` 管理 `SKILL.md`（`discovery.py:60-64`）。
- 扩展私有数据目录：`<数据目录>/extensions/data/<ext_id>/`，经 `api.data_dir()` 取用，id 含路径分隔符直接拒绝（`runtime/data_paths.py:73-78`、`api.py:83-88`）。

### 6.2 manifest（声明式能力）

字段以 `core/extension/manifest.py` 的数据类为准：顶层 `name/version/description/extension_type(channel|plugin|tool|skill|mcp)/author/enabled/dependencies/pip_dependencies/min_boarai_version/config_defaults/provides/permissions/source_id/source_display` + `contributes`（`manifest.py:200-226`）。

`contributes` 合法 kind 只有：`tools / routes / hooks / hook_points / services / mcp_servers / ui`；未知 kind 丢弃并告警，条目缺关键字段丢弃但**绝不使整个 manifest 解析失败**（`manifest.py:270-293,384-392`）。写解析/校验代码时遵守同一原则：局部降级，不 brick 加载。

- tool 条目：`id` + `handler: "code.py#func"` + `schema` + `security_profile(none|file_read|file_write|file_delete|shell)` + `path_param/shell_param` + `suggested_category(readonly|safe|dangerous)` + `compress_strategy` + `timeout`（`manifest.py:21-34`）。
- route 条目：`method/path/handler/use_prefix`，默认挂载到 `/api/ext/<id><path>`（`manifest.py:36-44`）。
- hook 条目：`event`（`core.*` 或 `ext.<other>.*`）+ `handler` + `order`（`manifest.py:46-52`）。
- hook_point 条目：`name`（自动加前缀成 `ext.<id>.<name>`）+ `semantics(observe|blockable|transform)` + `visibility`（`manifest.py:54-61`、`api.py:249-257`）。
- mcp_server 条目：`name`（snake_case，成为工具名前缀）+ `command/args/env/cwd/timeout/tool_timeout`（`manifest.py:70-80,361-368`）。
- ui 条目：panel 只支持 `kind: custom`（自家 iframe 页）+ actions 按钮（`manifest.py:96-113`）。
- 没有 `config_schema`——设置面板就是扩展自己的页面（`templates/full/manifest.yaml:7-9`）。

### 6.3 生命周期与 API 门面

- 必须实现 `manifest` property 与 `initialize(api=None) / start / stop / cleanup` 四个 async 方法（`extension/base.py:96-114`）；可选 `on_server_ready / on_config_changed / on_event`。
- 一切注册走注入的 `ExtensionAPI`：`register_tool / register_route / register_hook / declare_hook / trigger_hook / register_service / call_service / register_channel / register_command / register_settings* / publish / subscribe / get_config / set_config / static_url / data_dir`（`api.py` 全文）。
- 门面为自己注册的工具/命令维护 ownership 列表，卸载时由 loader 统一清扫——**扩展不要自己造第二套注册表**（`api.py:55-65,397-410`）。
- `set_config` 原子落盘到私有 `config.json`，写失败不抛给调用方，随后回调 `on_config_changed` 并发布 `extension.config_changed`；缓存派生状态的扩展必须覆写该回调（`api.py:97-160`）。
- 全局 HTTP middleware 经 `ExtensionBase.register_middleware`，卸载自动清理防堆叠（`base.py:120-161`）。
- 扩展启停状态持久化在 `<数据目录>/extensions/state.json`（`core/extension/state_store.py`）。

### 6.4 工具（tool）

- 定义方式二选一：`@tool` 装饰器或 `BaseTool` 子类（import 即自注册）（`extension/tool/base.py:14-36`）；manifest `contributes.tools` 声明式则写 `code.py#func`。
- handler 第一个参数是注入的 `ToolContext`（`ctx`），携带 `session_id / workspace / run_id / project_root` 等；相对路径按会话 workspace 解析而不是项目根（`tool/registry.py:195-203`、`file_tools/tools.py:1-5,66-79`）。
- **能力用元数据自声明，core 按元数据（security_profile/path_param/shell_param/compress_strategy/suggested_category）施加策略，绝不按工具名 hardcode**（`tool/base.py:4-12`）。
- 工具结果 dict 中 `_` 前缀键是内部字段：core 在进 LLM/落消息前统一剥离（`core/session/run_tools.py:18-22,137-141`）；文件变更记录用 `_path/_change_type/_diff/_additions/_deletions/_existed/_snapshot/_trash_id` 约定键（`run_tools.py:25-52`）。大文本、diff、快照一律放 `_` 键，禁止放普通键。
- 执行侧注入的 `_agent_*` / `_approval_granted` / `_run_id` / `_tool_call_id` kwargs 是 core→tool 私有通道，工具文档里不要暴露给模型（`run_tools.py:117-132`）。
- MCP 工具命名固定 `mcp_<server>_<tool>`（`extension/loader.py:935-937,1059`）。

### 6.5 渠道（channel）

- 适配器契约：实现 `ChannelAdapter.send`（唯一必须），`start(ctx)/stop()` 可选；入站交活只有 `ctx.submit()`（发后即忘）或 `ctx.ask()`（要回文本）两个入口（`extension/channel/adapter.py:4-19,96-116`）。
- 分工铁律：**core 拥有一切 run**（会话映射、持久化、事件镜像、串行化、超时也必回复），**adapter 只拥有自己的传输层**；core 永远不知道存在哪些 adapter（`adapter.py:4-19`）。
- `reply_token` 是平台不透明句柄，core 原样往返、绝不解释（`adapter.py:54-59`）。
- channel 身份用 manifest 顶层 `source_id/source_display`，不是 contributes kind（`manifest.py:8-11`）。

### 6.6 技能（skill）

- 技能 = `SKILL.md`（Anthropic Agent Skills 开放标准），frontmatter 只有 `name` + `description` 两键，"何时用"语义写进 description（`extension/skill/types.py:1-11,22-23`）。
- system 技能（`boar/extensions/skills/`）只读；agent 生成的技能在 `<数据目录>/extensions/skills/`，只有后者可写（`types.py:18-20,55-58`）。
- 提示词前缀只注入一行索引（description 截断 60 字符），全文按需经 `skill_view` 工具加载（`types.py:9-10,29-32,60-65`）。
- 技能删除走回收站软删除（`core/trash/`，TTL_SKILL 30 天，`trash_manager.py:42`）；**不做定时自动归档**——这是有记录的设计决定（`core/curator/curator.py:7-24`）。

### 6.7 设置 UI

- 面板 = 同源 iframe 加载自家 `static/settings.html`，引入 `/static/js/boar-sdk.js` 后用全局 `boar` 对象（getConfig/saveConfig/getData/saveData/callAction）；鉴权靠 HttpOnly 会话 cookie，页面 JS 永远拿不到访问钥匙（`boar-sdk.js:1-22,36-41`）。
- action 按钮默认 15s 超时，长任务必须快速返回 + 轮询（`manifest.py:93`）。
- 扩展自带文案走自家 `static/i18n/{en,zh}.json`（参照 `boar/extensions/tools/web_tools/static/i18n/`）。

---

## 七、数据目录与路径【硬边界】

1. **数据锚唯一真源** `core/runtime/data_paths.py`：解析序 `--data-dir` > `BOARAI_DATA_DIR` > `Path.home()/.boarai`（`data_paths.py:1-14`）。一切子系统经 `get_data_dir()/resolve_data_path()` 取路径，**禁止硬编码 `.boarai` 位置**；配置里的相对 `.boarai/...` 值经 `resolve_data_path` 落到数据目录（`data_paths.py:81-108`）。
2. Windows 上无盘符的绝对路径（如 NAS 配置带来的 `/vol1/...`）必须降级映射进数据目录，不许静默落到当前盘根（`data_paths.py:91-101`）。
3. **项目根锚**：`set_project_root()` 启动时钉一次，所有子系统 `get_project_root()` 读取；禁止用 `Path.cwd()` 或 `__file__` 层级推算当项目根（`runtime/root.py:1-5`）。`Path.cwd()` 只允许出现在锚解析的兜底分支（`root.py:32`、`discovery.py:37-45` 注释解释了 cwd 兜底的危害）。
4. 文件系统操作一律 `pathlib.Path`，禁止字符串拼路径；跨平台注意分隔符（全仓实况一致）。
5. 写文本用 `write_text_atomic_async`（`runtime/atomic_write.py`）——崩溃不得留下半截文件（`api.py:110-123` 是范例）。
6. 旧树内 `<仓库根>/.boarai` **不做自动迁移**；系统里禁止写数据迁移逻辑（`data_paths.py:9-11`、`__main__.py:154-158`）。
7. 首启种子：`copy_defaults` 在 `create_directories` **之前**执行，`bootstrap.initialized` 置位后永不重拷；用户数据永不覆盖（`bootstrap.py:4-12`、`__main__.py:163-171`）。运行时配置文件缺失是全新安装的正常状态，惰性生成、损坏回退默认值，绝不重写用户脏文件（`core/config/parser.py:39-72`）。
8. 删除默认软删除进 `core/trash/`（分类型 TTL，`trash_manager.py:36-45`），记忆删除同走软删（`core/memory/synaptic_mesh.py:380-399`）；禁止业务代码直接 `shutil.rmtree`/`unlink` 用户数据。

---

## 八、LLM 与 Run 链路

### 8.1 LLM 层

- 新供应商必须实现 `LLMProvider` ABC：`name / supported_models / generate(LLMRequest)→LLMResponse / generate_stream(LLMRequest)→AsyncIterator[StreamChunk]`（`llm/protocol.py:200-223`），基于 aiohttp 继承 `providers/base.py`。
- 错误统一 `LLMError`，`retryable` 由 HTTP 状态推导（408/429/5xx）（`protocol.py:267-302`）；多模态被拒走 `vision_rejected` 标志由 executor 降级重试，不另造异常类型（`protocol.py:299-302`）。
- 密钥与 Base URL 只存 `<数据目录>/config/llm.yaml`，不入库不入日志；该文件在脱敏名单里（`core/llm/service.py:827`、`core/config/schema.py:189`）。保存时未注册 provider 的配置要原样保留（`service.py:897`）。
- 每模型能力（vision/thinking/context window）声明在 `llm.yaml`，运行时代码读声明而非猜模型名（`llm/thinking/wire.py:26`、`session/run/model_resolver.py:7,32`）。

### 8.2 hook 管道

- 15 个核心 hook 点集中声明在 `extension/contribution/core_hooks.py::CORE_HOOK_POINTS`，语义三选一：`observe`（只读通知）/ `blockable`（返回 `{"blocked": True, "reason": ...}` 否决）/ `transform`（payload 顺链可改写）（`core_hooks.py:8-51`）。会话消息改写用 `{"data": {"content": ...}}`（`core_hooks.py:43-45`）。
- 触发点位置：run 生命周期在 `session/run/executor.py:117` + `loop_config.py:256`（带 2s 超时），工具在 `run/turn_tool_round.py:107,162`，LLM 在 `turn_preflight.py:88` / `turn_stream.py:280` / `turn_finalize.py:431,571`，文件写在 `fs/textops.py:306,333`。新增核心 hook 必须登记进 `CORE_HOOK_POINTS` 并在唯一触发处调用，不散落。
- `llm.transform_output` 是 opt-in：只有存在绑定时才进热路径（`core_hooks.py:13-14,38-39`）。热路径 hook 一律照此办理。

### 8.3 run 与压缩

- run 事件族：`run.created/chunk/done/completed/error/cancelled/retry/diff/usage/vision_downgrade`（`event/types.py:59-78`）；`run.usage`、`run.vision_downgrade` 等瞬态事件**永不持久化**（登记注释明说，`types.py:71-78`）。
- 流式文本 WS 合帧：40ms / 512 字符双界 + 定时器兜底排空，改动须保持"停流也能排空"不变量（`session/run/broadcaster.py:17-25,49-69`）。
- 压缩结果写 `session.compressed_summary`，**禁止作为消息进入历史**；运行时 system 注入 append-only（涉及文件：`run/compression_gate.py`、`prompt/fragments.py`、`session/manager.py`）。
- 空回复兜底、审批超时兜底在 `run/turn_finalize.py:143-184`，兜底消息必须带 `metadata.fallback` 标记。

---

## 九、安全与门禁

- 安全门面 `SecurityManager` 聚合 PathGuard/ToolGuard/AuditLog，运行时更新**原地改同一 config 对象**（WebUI 编辑的是活状态，不做副本）（`security/__init__.py:36-60`）。
- 访问钥匙只存 scrypt 哈希、永不回显；`rotate` 是唯一取新钥途径，且是两阶段（prepare 展示新钥时旧钥仍有效，confirm 才生效）——"生成即生效+只显示一次"的自锁模式被明令禁止（`__main__.py:54-57,100-102`、`unified_server.py:128-130`）。
- 环境变量 `BOARAI_API_TOKEN` 必须 ≥32 位 base64url，否则启动即拒（`unified_server.py:78-85`）。
- 门禁只在传输层：API、WS、扩展路由统一过 gate（`unified_server.py:397`）；钥匙吊销/轮换必须镜像 `auth.revoked` 踢掉所有 WS（`event/types.py:95-100`）。公开路径改动要同步 gate 与其测试（`tests/test_access_gate.py`）。
- 工具安全按 `security_profile` 元数据路由到对应守卫（见 6.4），守卫拒绝时结果 metadata 带 `security_block` + `reason`，上层归为 `permission` 错误类（`run_tools.py:158-162`）。
- 日志与事件落盘前过脱敏（`core/logging/redact.py`、`core/security/redact.py`；敏感 pattern 名单在 `config/schema.py:189`）。

---

## 十、编码风格【约定】

- **docstring**：模块级可多行，写清职责、分工与"为什么"；函数/类一律**单行英文摘要**，禁 Args/Returns 块与长篇原理（全仓已按此精简；范例 `runtime/root.py`、`session/run_tools.py`）。设计动机写成普通 `#` 注释放在被解释的代码旁（范例 `api.py:55-59`、`broadcaster.py:17-23`）。
- **语言**：标识符、docstring、注释、日志消息、异常消息一律英文；面向最终用户的界面文案归前端 i18n 管。
- **日志**：模块顶部 `logger = logging.getLogger(__name__)`；禁止 `print`（唯一例外：`main.py` / `__main__.py` 的 CLI 用户输出）。宽异常捕获必须 `# noqa: BLE001` 并注明 best-effort 原因（范例 `api.py:124,146,158`）。
- **异步**：I/O、事件派发、文件读写全 async；阻塞调用用 `asyncio.to_thread` 隔离（core 内 30+ 处既成事实）。事件处理器、扩展生命周期方法必须 `async def`。
- **类型**：新模块 `from __future__ import annotations`；数据结构优先 `@dataclass`（可加 `slots=True`，见 `channel/adapter.py:46`）；公开协议用 ABC + `@abstractmethod`。
- **命名**：常量 UPPER_SNAKE；内部符号 `_` 前缀；单例入口统一 `get_xxx()`（`get_event_bus/get_tool_registry/get_access_store/...`）；管理/注册/存储类命名 `*_manager.py / *_registry.py / *_store.py`；run 循环阶段件 `turn_*.py`。
- **防御式解析**：外部输入（YAML/JSON/manifest）逐条校验，坏条目丢弃 + warning，绝不整体抛异常阻断加载（`manifest.py:270-393` 是标准范本）。
- **显式 0 与缺省**：区分"未提供"与"显式 0"，不许 `or default` 一把梭（`manifest.py:414-420,439-445`）。
- **版本单源**：`boarai/core/__init__.py::__version__`，pyproject 用 AST 动态读取，git tag / PyPI 必须与它锁步（`core/__init__.py:21-26`、`pyproject.toml:31-34`）。

---

## 十一、前端 WebUI 规范

- 技术栈：Vue 3 全局构建 + vue-router + 原生 ES 模块，**无打包器**；全部资产在 `boar/extensions/channels/webui/static/`（js/css 目录 + `index.html`）。第三方库（vue/lucide/marked/tailwind）直接落 `static/js/`，不自建 CDN 依赖。
- 组织：`js/store.js`（唯一全局 store + WS 通道）、`js/app.js`（应用壳）、`js/components/*`（可复用组件）、`js/pages/*`（路由页）、`js/i18n/{en,zh}.json` + `js/i18n.js`。
- **WS 事件分发**：`store.js` 以 `msg.type === '...'` 链式分派（`store.js:207-423` 覆盖 run.* / session.* / fs.changed / auth.revoked / update.* / skill.* / extension.load_failed）。后端新增 WS 镜像事件，**必须**同步在此登记分发，否则前端视为未完成。
- 请求-响应类 WS 消息带 `id` 回显（`unified_server.py:792-805` 的 error 响应形态即契约）。
- i18n：`t()` 取词，语言 localStorage 持久化 + 浏览器语言兜底（`i18n.js:15-36`）；新增文案 en/zh 两份 json 同步加。无 navbar 的全屏页（setup/unlock）必须自带语言切换组件；由 `t()` 派生的标签用 computed，不塞静态 data。
- 扩展设置面板见 6.7；面板页不得假设能拿到访问钥匙。

---

## 十二、测试规范【约定】

- runner：`py tests\run.py`（可带过滤串或单个文件名）。纯标准库自研，**不用 pytest**；自动发现 `tests/test_*.py` 里的模块级 `test_*` 函数，async 函数原生 `asyncio.run`（`run.py:19-23,144-158`）。
- **每个测试文件一个子进程**：core 有约 40 个模块级单例，跨文件污染无法靠重置根治，子进程边界一次性切断——新增测试文件不要试图共享进程状态（`run.py:8-17`）。文件内部仍保留 `_RuntimeIsolation` + `_reset_singletons()`（`run.py:76-107`）。
- runner 把 `HOME/USERPROFILE` 钉到每轮临时目录，防止测试写脏开发机真实 `~/.boarai`；真实家目录经 `BOARAI_REAL_HOME` 透传给部署一致性测试（`run.py:252-293`）。
- 共享基建在 `tests/harness.py`：`tmp_project()`（钉临时 project_root/data_dir）、`real_data_anchor()`（只读解除钉制）、技能文件助手。测试环境搭建一律走 harness，不自造锚点（`harness.py:39-88`）。
- 测试外置于仓库（工作区根 `tests/`），import 路径由 harness 自动注入（`harness.py:18-36`）。
- **契约测试与行为锁死**：绑定默认值（`test_server_bind_default.py`）、HTTP/WS parity（`test_session_service_parity.py`）、数据锚（`test_data_dir_anchor.py`）、门禁（`test_access_gate.py`）、run 循环不变量（`test_run_loop_invariants.py`）等。改这些行为必须同步改对应测试，且两半都不许单方面放宽。
- 失败输出规范：traceback 走 stdout 与 `[FAIL]` 行同流；子进程无汇总行按 `[CRASH]` 计一个失败，不许静默少报（`run.py:199-202,267-272,317-319`）。

---

## 十三、打包与发布

- setuptools 命名空间模式收 `boarai*` 与 `boar*`（`boar/` 有无 `__init__.py` 的目录，必须 namespace 才能带上）（`pyproject.toml:39-46`）；package-data 全收、排除 `__pycache__`/`*.pyc`（`pyproject.toml:48-52`）。
- `import boarai` 必须无副作用，不拉内核（`boarai/__init__.py:16-17`）；入口模块 import 期禁止触网，装依赖只有显式 `--ensure-deps`（`main.py:11-13,23-27`、`bootstrap.py:27-35`）。
- 资源锚（`boar/prompts` 等）在 pip 安装下是 site-packages（`boarai` 包的父目录），数据锚仍是 `~/.boarai`——两者不得混用（`__main__.py:6-10`）。
- 在线更新：服务器进程永不自己跑 pip，spawn 独立 worker；活动 run 期间拒绝更新（409）（`update/service.py:1-19`）。

---

## 十四、任务收尾（每次改动必做）

1. 当日变更记录追加到工作区 `.codebuddy/memory/YYYY-MM-DD.md`（只追加：改了什么 / 为什么 / 怎么验证 / 影响与约定）——该目录已有连续记录，是既成约定。
2. 更新 agent 项目记忆文件与其 `MEMORY.md` 索引（记忆只存不可从代码推导的事实：用户偏好、设计决策动机、外部指针）。
3. 新增事件 / 端点 / hook 时，按第四、五、八节把登记链走全（EventType + serve 转发 + store.js；REST + WS；CORE_HOOK_POINTS + 触发处）。
4. 纯只读任务豁免以上记录；只要动过仓库文件即必须执行。

---

## 附录：高频红线速查

| # | 红线 | 出处 |
|---|------|------|
| 1 | core 禁止 import/调用扩展 | `core/extension/api.py:12-15` |
| 2 | 扩展间禁止直连，走 call_service / 事件 | `core/extension/api.py:412-428` |
| 3 | 新事件必须登记 EventType + WS 转发 + store.js 分发 | `event/types.py:1-7`、`serve.py:310-376`、`store.js:207-423` |
| 4 | API 改动 REST/WS 双通道同步 | `api/session.py:1363+`、`tests/test_session_service_parity.py` |
| 5 | 工具内部字段 `_` 前缀，大文本禁放普通键 | `session/run_tools.py:18-52` |
| 6 | 压缩结果只写 `compressed_summary`，禁入消息历史 | `run/compression_gate.py` |
| 7 | 路径只经 data_paths / root 锚，禁硬编码 `.boarai`、禁 cwd 推算 | `runtime/data_paths.py:13-14`、`runtime/root.py:3-4` |
| 8 | 删除默认软删除进回收站，禁硬删用户数据 | `trash/trash_manager.py`、`memory/manager.py:136` |
| 9 | core 第三方依赖白名单 + 禁 vendor LLM SDK | `pyproject.toml:14-23`、`llm/providers/base.py:31` |
| 10 | 监听地址唯一真源 core.server，扩展不得声明 host/port | `unified_server.py:28-29`、`webui/extension.yaml:12-14` |
| 11 | 钥匙两阶段轮换，禁"生成即生效+只显示一次" | `unified_server.py:128-130` |
| 12 | 函数/类 docstring 单行英文摘要 | 全仓风格 + `runtime/root.py` 范例 |
