# 扩展通用规范（所有类型必读）

> 用途：开发任何 BoarAI 扩展前，先 `file_read` 本文件，再读对应类型文档。
> 位置：`boar/prompts/extension/common.md`（本目录为开发者中文参考版，与英文版一比一对应；LLM 读英文版）

## 心智模型：声明 + 挂载 + 通信

| 关注点 | 机制 | 一句话 |
|---|---|---|
| 声明 | `manifest.yaml` 的 `contributes` | 告诉核心"我有什么" |
| 挂载 | `initialize(api)` 注册 handler | 把能力接到核心调用点 |
| 通信 | 事件总线 / service / hook | 与核心及其他扩展交互 |

扩展之间**零直接依赖**：互相不 import 代码，只认命名空间（`api.call_service` / 事件总线）。

依赖方向（v1.5 单向分层）：扩展可自由 import 任意 `core.*`；**core 永不 import 扩展**（唯一硬边界）。

## 目录布局与两层加载

```
my_ext/
├── manifest.yaml          # 声明（必需）
├── extension.py           # 入口：Extension 类（必需）
├── code/                  # 业务代码（可选，配 contributes handler 引用）
└── requirements.txt       # 自带 pip 依赖（可选）
```

目录名与 `name` 均为 **snake_case**（`my_ext/`，不是 `my-ext/`）。

| 层 | 路径 | 权限 |
|---|---|---|
| system | `boar/extensions/{type_dir}/` | 只读、随仓库分发 |
| user | `.boarai/extensions/{type_dir}/` | 可安装/卸载/启停 |

`type_dir`：channel→channels、plugin→plugins、tool→tools、skill→skills、mcp→mcp。
同名覆盖：user > system。manifest 文件名以 `manifest.yaml` 为准（`extension.yaml` 仅历史别名，新扩展勿用）。

## manifest.yaml 全字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✅ | 唯一 ID，snake_case |
| `version` | ✅ | semver |
| `extension_type` | ❌ | channel/plugin/tool/skill/mcp，默认 plugin |
| `description` | ❌ | 一行摘要 |
| `author` | ❌ | 作者 |
| `enabled` | ❌ | 默认 true |
| `dependencies` | ❌ | 依赖的其他扩展名列表 |
| `pip_dependencies` | ❌ | 自带 pip 依赖（如 `- aiohttp>=3.8.0`） |
| `min_boarai_version` | ❌ | 最低核心版本 |
| `config_defaults` | ❌ | `_config` 的默认值种子（低于调用方配置与持久化 config.json；运行时经 `api.get_config`/`set_config` 读写，持久化 `.boarai/extensions/data/<id>/config.json`，重启/热重载自动合并） |
| `config_schema` | ⛔ 已移除 | 声明式表单已删除——设置为自定义 iframe 面板（`contributes.ui`）；遗留键被忽略并记 verifier 警告 |
| `provides` / `permissions` | ❌ | 声明的能力/权限 |
| `source_id` / `source_display` | ❌ | **仅 channel**：消息源身份与显示名（见 channels.md） |
| `contributes` | ❌ | 声明式能力清单（下表） |

`contributes` 支持的 7 种（与 `core/extension/manifest.py` 的 `Contributes` 对齐）：

| 种类 | 说明 |
|---|---|
| `tools` | 工具，`handler: code/tools.py#fn` 字符串引用（见 tools.md） |
| `routes` | HTTP 路由 |
| `hooks` | 绑定到核心/他人 hook 点 |
| `hook_points` | 自有 hook 点，供他人绑定 |
| `services` | 扩展间请求-响应服务 |
| `mcp_servers` | 外部 MCP server 桥接（见 mcp.md） |
| `ui` | WebUI 设置面板与动作按钮（`panel` + `actions`，见 plugins.md「扩展设置」） |

> `channels` / `skills` **不是** contributes 种类：通道就是普通扩展（走 `source_id` 字段 + 路由注册，见 channels.md）；技能不经 manifest（见 skills.md）。

## 入口骨架与生命周期

```python
from boarai.core.extension import ExtensionBase, ExtensionManifest

_MANIFEST = ExtensionManifest(
    name="my_ext", version="1.0.0",
    extension_type="plugin", description="one-line summary",
)

class MyExtExtension(ExtensionBase):
    @property
    def manifest(self) -> ExtensionManifest:
        return _MANIFEST

    async def initialize(self, api=None) -> None:
        ...  # 注册 tools/hooks/routes/services（只收 api 一个参数）

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def cleanup(self) -> None: ...
```

- `initialize` 只收 `api`（ExtensionAPI 门面）；事件总线不是参数，经 `self.event_bus`（loader 注入）
- 卸载时核心按**归属**清扫你注册的一切：manifest contributes 与 `api.register_route/register_tool` 动态注册的路由/工具、hook 绑定、service、middleware 全部自动卸载（动态注册曾会泄漏并让下一次加载报 "already registered"，已修）。`stop/cleanup` 里显式清理自己的**外部资源**（轮询任务、网络连接、事件订阅）仍是你的责任

## import 语义（两层不同，易踩坑）

入口 `extension.py` 由 loader 经 `spec_from_file_location` 加载，**无包上下文** → `from . import xxx` 必失败。

- **system 层**：项目根在 sys.path，`from boar.extensions.<type_dir>.<name> import xxx` 可用
- **user 层**：`.boarai/extensions/` 不在 sys.path，上式抛 ImportError。三种支持的模式：
  1. **单文件**（推荐）：`@tool` 等注册直接写在 `extension.py` 顶部，模块加载即注册
  2. **多文件走 manifest**：`contributes.tools[].handler: code/tools.py#my_tool`，loader 相对扩展目录解析（handler 必须是扁平模块里的**顶层**函数）
  3. **入口内加载同级模块**（extension.py 自己要引用 helper 时）：用 importlib 显式加载，并先 pop 旧模块，否则热重载留着旧代码：

     ```python
     import importlib.util, sys
     from pathlib import Path
     _EXT_DIR = Path(__file__).parent

     def _load_sibling(name: str):
         mod_name = f"boarai_ext_<extname>_{name}"
         sys.modules.pop(mod_name, None)   # 热重载安全
         spec = importlib.util.spec_from_file_location(mod_name, _EXT_DIR / f"{name}.py")
         module = importlib.util.module_from_spec(spec)
         sys.modules[mod_name] = module
         spec.loader.exec_module(module)
         return module
     ```

     同一 sibling 绝不许从两处各加载一份：两份副本意味着两个不同的异常类对象，
     `except YourError` 会静默漏掉另一份副本抛出的实例。

## 创作工作流（脚手架 → 验证 → 加载 → 证明）

**文档优先契约（铁律）：**

1. **禁止读 `core/` 源码推断行为。** 本规范 + 脚手架模板即契约。所需细节缺失、含糊或矛盾时：停下并报告为**未验证**（问操作员或指出文档缺口）——绝不从实现里猜，也不探测运行中服务器的内部来补洞。
2. **验证是证据，不是预测。** 每次交付都要区分"静态验证过"、"已加载且观察到工作"、"未验证"。承诺遵守规则不等于代码遵守了规则。
3. **绝不静默设置信任开关。** 用户层扩展加载即在服务器进程内执行 Python；由操作员决定（见下方信任门）。

**写文件：本规范就是契约。** 直接照本文档和类型指南手写
`manifest.yaml` + `extension.py`——这是一等公民路径，全程不需要读
`core/` 源码。扩展没有后台 CLI（2026-11-15 已删）——反馈循环走 REST
（鉴权见验收第 0 步）加一个脚手架 Python 入口：

| 步骤 | 方式 | 说明 |
|---|---|---|
| 起点骨架 | 仓库根执行 `py -3 -c "from boarai.core.extension.scaffold import scaffold_extension; print(scaffold_extension('<id>', '<type>', full=False)['path'])"` | 渲染**可运行**模板到用户层；`full=True`=全部 contributes 类型。照文档手抄 `boar/extensions/templates/hello/` 同样有效 |
| 静态验证 | `GET /api/extensions/verify?name=<name>`（未加载目录用 `&path=<dir>`） | 结构化 Issue 流（`code`/`message`/`file`/`hint`）；纯 AST，绝不执行扩展代码；**每次改完必跑**，循环到 `errors == 0`。无服务器时最后手段：`py -m py_compile` + 下方验收清单 |
| 加载状态 | `GET /api/extensions` | 按分组返回，含 failed 列表 |

**用户层信任门**：loader 拒绝执行任何非 system 扩展，直到操作员授权，两种方式：

- **按扩展授权（推荐）**：WebUI 扩展页失败卡片上的**信任并加载**按钮
  （`POST /api/extensions/<name>/trust`）——持久化进 state.json、立即生效、
  可撤销（`.../untrust` 会停止并卸载）；
- **全局放行**：`BOARAI_ALLOW_INPROCESS_USER_EXTENSIONS=1`（信任**所有**
  用户扩展）+ **从新终端**重启服务器（服务器内 restart 继承旧进程环境，
  带不进新变量）。

**要问操作员，绝不自行授权**：不要设环境变量，也不要调用 trust 端点——
两者都是操作员决策（授权在服务端以 WARNING 级审计）。静态验证不需要
信任——先把验证做满，再去申请加载。

**加载失败全程可见**（不再是只有日志的黑洞）：`GET /api/extensions` 带
`failed` 列表；WebUI 扩展页渲染红色失败卡片（错误+hint+重试/停止重试）；
WS 推送 `extension.load_failed`。门禁拒绝的 code 是 `gate.inprocess_untrusted`。

## 安全红线（6 条，所有类型必守）

1. 依赖方向单向：扩展优先用稳定面（`core.extension`/`core.event`/`core.trash`…）；`core.session`/`core.memory` 等内部模块可用但无兼容承诺，自担风险
2. 工具必须声明 `security_profile`（文件→file_read/write/delete + `path_param`；shell→shell；详见 tools.md）
3. 危险能力默认 opt-in，用户允许才启用
4. 能力门面是标准化边界**而非安全沙箱**；真安全在执行出口（PathGuard/ToolGuard/Audit）
5. 一律 `pathlib.Path`，勿字符串拼接；扩展数据写 `api.data_dir()`（`.boarai/extensions/data/<id>/`），**禁止写进自己的源码目录**
6. `stop/cleanup` 显式清理资源

## 已知边界（设计前必查，2026-09 现状）

| 你想要 | 状态 | 替代方案 |
|---|---|---|
| WebUI 导航内嵌扩展页 | ❌ tab 硬编码（navbar.js），动态 tab 是 TODO | 孤岛 URL：`/static/<name>/page.html` |
| 订阅 `user.input` 拿消息 | ❌ 枚举存在但从不发布 | `SESSION_MESSAGE` 事件或 WS 客户端 |
| 注册终端命令（register_terminal_command） | ⚠️ 机制可用但受众仅 CLI 子系统（半退役），WebUI 用户看不到 | 改用 `@tool` 暴露给 LLM |
| 自定义 LLM provider | ⚠️ 可注册（`get_llm_manager().register_provider`），但路由按 `request.provider`/默认 provider 选择——**注册 ≠ 生效** | 配置设为默认，或请求显式指名 |
| 深度沙箱（扩展代码子进程隔离） | ❌ 架构限制 | 执行出口审计（PathGuard/ToolGuard/Audit）兜底 |

## 验收（写完/改完任何 manifest 扩展后必做）

文档教你怎么写；以下检查告诉你是否真的加载了。未跑完不得报告成功。端点在本地服务器（与 WebUI 同端口）；用 shell_execute（curl）或文件工具查日志。

0. **先取鉴权** —— 除 `/api/auth/token` 外所有 `/api/*` 路由都在访问门禁之后，没凭据一律 401。在服务器本机（回环）可以依赖 `security.web.trust_loopback`（默认开启），或 `curl -s http://127.0.0.1:<port>/api/auth/token` → `{"token": "..."}`，然后每个请求带 `-H "Authorization: Bearer <token>"`。该 token 是**进程启动令牌**（仅回环、随服务重启失效），不是网页访问钥匙；远程调用方必须用访问钥匙本身。
0.5. **加载前先静态验证** —— `GET /api/extensions/verify?name=<name>` 必须 `errors == 0`。信任门关闭时也能跑，所以"是代码问题还是声明问题"在任何重启之前就有答案。
1. **扩展已上线**：`GET /api/extensions/<name>` —— 存在、enabled、且 `error` 字段为空。若返回 `status: "failed"`（或名字出现在 `GET /api/extensions` 的 `failed` 列表），响应带 `failure_code` + `hint`——按 hint 修，别翻日志。热刷新约 5 秒一轮；失败加载按退避重试（瞬态 60 秒，确定性失败指数退避至 1 小时封顶，如信任门）；可用 `POST /api/extensions/<name>/reload` 强制。
2. **每个贡献的能力真实挂载**（看注册态，不看声明）：
   - hooks → `GET /api/extensions/hooks` 出现你的绑定
   - services → `GET /api/extensions/services` 列出你的方法
   - routes → 实际调用一个：`GET /api/ext/<name>/<path>` 有响应
   - tools → 无 HTTP 列表端点；grep `.boarai/logs/` 当日日志中的工具名，再确认下一次 run 的工具列表出现它
   - mcp_servers → 日志出现 `mcp_<server>_<tool>` 注册记录（单个 server 启动失败只记 error——要查，别假设）
   - ui → `GET /api/extensions/<name>` 的 `settings` 段列出你的 `panel` / `actions`，且扩展页出现「设置」按钮（面板还需 `/static/<name>/…` 能响应，直接访问该 URL 确认——`custom_url` 被拒时会被清空并记一条 warning）
3. **日志扫描**：任何包含 `contributes` + `discarded` 且点名你扩展的 warning，都意味着一条畸形声明被丢弃——修 manifest 后 reload。日志安静但能力缺失 = 你写了 loader 根本不认识的键。
4. **通道**：两半接好后，经桥发一条测试消息并确认回流（见 channels.md）。

## 参考实现

- 工具 + hook：`boar/extensions/tools/file_tools/`（@tool + declare_hook，system 层 import 模式）
- 事件订阅：`boar/extensions/channels/webui/extension.py`（initialize 内 event_bus.subscribe）
- 通道：`boar/extensions/channels/webui/`（source_id + 路由 + 静态资源）
- 技能工具链：`boar/extensions/tools/skill_tools/`（asyncio.to_thread 包同步 store、事件发布）
