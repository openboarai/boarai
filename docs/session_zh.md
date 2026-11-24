> [English](session.md) | **简体中文**

# 会话与运行循环（core/session）

会话生命周期、消息持久化、run 执行链（工具循环/流式/压缩/审批/变更追踪）与思考子系统。

## 顶层模块

| 文件 | 职责 |
|------|------|
| `manager.py` | `SessionManager`：会话 CRUD、消息落库、生命周期事件 |
| `service.py` | 会话领域服务：REST 与 WS 两通道背后的**单一实现** |
| `storage.py` | 会话持久化（aiofiles 异步 I/O） |
| `journal.py` | 追加式 JSONL 会话日志 |
| `migrate.py` | v3 单文件 JSON → v4 追加式 JSONL 迁移 |
| `tree.py` | 会话树：fork、分支枚举、navigate_tree |
| `artifact.py` | 每会话文件元数据与符号索引（含 changes.json 变更记录） |
| `attachments.py` | 附件存储布局、文件名安全、LLM 装载 |
| `workspace.py` | 会话工作区解析/校验/目录浏览（session_dir / shared / custom 三种模式） |
| `orphan_tools.py` | 修复没有 tool 结果的孤儿 assistant tool_calls |
| `usage_ledger.py` | 追加式全局 LLM 用量账本 |
| `run_manager.py` | run 门面 |
| `run_models.py` | `Run` 记录与状态枚举 |
| `run_tools.py` | 工具执行助手：参数解析、安全 kwargs、注册表调用、结果规范化 |

## run/ 执行链

| 文件 | 职责 |
|------|------|
| `registry.py` | run 生命周期状态、取消、TTL 清理 |
| `executor.py` | 会话工具循环主体（hook 触发经 `loop_config`） |
| `loop_config.py` | 可注入的循环配置与状态（含带超时的 hook 触发） |
| `context_prep.py` | run 上下文包与运行前准备（记忆/任务状态/路由） |
| `turn_preflight.py` | turn 前置：run-start 广播、线历史构建、请求组装 |
| `turn_stream.py` | turn 流式：消费 LLM 流（视觉降级、断流续接） |
| `turn_tool_round.py` | turn 工具轮：批量调度（并行/串行）、执行、结果归一、终止决策 |
| `turn_finalize.py` | turn 终态：completed/cancelled/failed 路径、中断持久化、空回复兜底 |
| `turn_usage.py` | 用量核算：token/缓存进会话、全局账本与消息 metadata |
| `turn_background.py` | 后台提取：教训、自动技能创建与反思（全部 fire-and-forget） |
| `broadcaster.py` | run 事件三路扇出：EventBus + 按 run 回调 + 会话 WS 客户端 |
| `tool_confirm.py` | 工具执行确认门（审批） |
| `termination.py` | 单 agent 工具循环终止策略（纯决策：TurnState + config → 动作） |
| `compression_gate.py` | 闲置压缩门：阈值、压缩、create_run 竞态守卫 |
| `diff_tracker.py` | 按 run 的文件变更聚合 |
| `revert.py` | 快照式回滚（无 git）：每个被改文件恢复到 run 前状态 |
| `model_resolver.py` | 能力驱动的三态视觉路由 |
| `vision_sidecar.py` | 视觉旁路：用视觉模型描述图片附件，把文本注入主模型请求 |
| `route.py` | 单 agent 主路径的意图路由（复用关键词规则，零额外 LLM 往返） |
| `messages.py` | system 注入模板与消息规范化 |

## thinking/ 思考子系统

| 文件 | 职责 |
|------|------|
| `engine.py` | 思考引擎（推理模式决策） |
| `processor.py` | 流式 think 块状态机 |
| `config.py` | 思考配置（会话级，不属角色） |
| `delegation.py` | 后台子代理委派（按父会话并发上限） |
| `subagent.py` | 子代理会话 |
| `fallback.py` | 三态回退机：主模型失败时切换模型/供应商 |

## 关键契约

- **事件**：`session.created/updated/message/deleted/...` 由 API 层在落库后发布；run 事件族 `run.created/chunk/done/completed/error/cancelled/retry/diff/usage/vision_downgrade`，瞬态事件永不持久化。
- **流式合帧**：WS 文本合帧双界（40ms / 512 字符）+ 定时器兜底排空；"停流也必须排空"是不变量。
- **工具结果**：`_` 前缀键是内部字段，进 LLM/落消息前统一剥离；文件变更记录用 `_path/_change_type/_diff/_additions/_deletions/_existed/_snapshot/_trash_id` 约定键抽取；执行侧注入 `_agent_*` / `_approval_granted` / `_run_id` / `_tool_call_id` 私有 kwargs，不暴露给模型。
- **压缩**：结果只写 `session.compressed_summary`，禁止作为消息入历史。
- **hook**：run/工具/LLM/会话各 hook 点在本链路触发（`core.*` 命名空间），blockable 用 `{"blocked": True}` 否决、`{"data": {"content": ...}}` 改写；热路径 hook（如 `llm.transform_output`）仅在有绑定时执行。
- **兜底消息**：空回复/审批超时兜底必须带 `metadata.fallback` 标记。
- **工作区**：工具相对路径按会话工作区解析（不是项目根）；默认工作区模式可配置，配置不可用时兜底 `<数据目录>/sessions/<id>/workspace`。
