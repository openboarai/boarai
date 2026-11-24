> [English](prompt.md) | **简体中文**

# 提示词系统（core/prompt）

提示词三区组装、角色（agent）存储、历史压缩、上下文片段与 token 计数。

## 模块构成

| 文件 | 职责 |
|------|------|
| `assembler.py` | v5 有状态提示词引擎：分区组装最终 system/messages |
| `document.py` | `PromptDocument`：供应商中立的提示词 AST 表示 |
| `loader.py` | 集中式文件提示词加载：`boar/prompts/` 下纯文本，import 期加载为常量 |
| `fragments.py` | 上下文片段接口：可组合的 Layer 4/5 上下文源 |
| `agent.py` | `AgentConfig` + `AgentRegistry`（多角色支持） |
| `agent_store.py` | `AgentStore`：角色的文件夹持久化 |
| `compressor.py` | 会话历史压缩（Insert-then-Compress） |
| `assistant_compress.py` | 助手回复压缩：生成紧凑"上下文版" |
| `tool_result_trimmer.py` | WOTR：工具结果的确定性内联分级保留 |
| `task_state.py` | 结构化会话状态管理 |
| `context_detector.py` | 上下文窗口探测（读数据不猜） |
| `token_counter.py` | tiktoken 精确预估 |
| `cache.py` | 会话级提示词前缀缓存 |
| `timer.py` | 闲置压缩定时器 |

## 提示词资产

- 全部提示词文本住 `boar/prompts/`（`system_core.md`、`compression/compress_instruction.md`、`compression/summary_template.md`、`extension/*.md`），提示词不写死在 py 文件里；目录定位从本模块文件位置上溯项目根，候选含 pip 安装的父目录。
- 运行时 system 注入 **append-only**：稳定前缀（角色/工具/配置）之后只追加，不改写前部——前缀漂移会让下游每个 turn 失去缓存命中，漂移本身作为可观测事件发布。

## 角色（Agent）

- 每个角色一个目录：`<数据目录>/agents/{name}/`，`soul.md`（纯 Markdown 人格：Identity + Behavior Rules，对应 Zone 1 两段文本）+ `agent.yaml`（全部结构化字段）。
- `AgentConfig` 是**扁平模型**：角色间无继承链；人格设定与行为规则完全独立。
- 核心代码里不允许出现具体角色名；"必须存在一个角色"由 API 层保证，默认归属由 `is_default` 标记决定。
- 角色删除走回收站软删除。

## 压缩

- 历史压缩走 Insert-then-Compress；压缩结果写 `session.compressed_summary`，**禁止作为消息进入历史**（触发与门控在会话 run 子系统，压缩指令与模板在 `boar/prompts/compression/`）。
- 工具结果修剪是确定性的分级内联保留（WOTR），不再二次询问模型。
- 闲置压缩由定时器驱动，绝不在会话 turn 内同步执行。

## 上下文窗口

窗口大小是供应商事实：优先读用户 `llm.yaml` 的 `context_windows` 覆写，其次模型目录；探测逻辑不在代码里 hardcode 模型名。
