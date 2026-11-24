> [English](memory.md) | **简体中文**

# 记忆系统（core/memory）

突触记忆网格：pod 存储、突触检索、融合、巩固、转生、闲置蒸馏与教训提取。

## 分层

`MemoryProvider`（可替换检索契约）→ `SynapticMeshProvider`（pod + 突触的默认实现）→ `MemoryManager`（编排门面，自身不再实现检索）。领域保留生物学词表：pod、synapse、weave。

## 模块构成

| 文件 | 职责 |
|------|------|
| `manager.py` | 子系统编排门面（`soft_delete_entry` 等入口） |
| `provider.py` | 可替换的检索后端契约 |
| `synaptic_mesh.py` | pod + 突触检索后端（原始记忆实现的落点） |
| `pod_store.py` | pod 持久化、路径解析与并发控制 |
| `tag_index.py` | 纯 JSON 倒排索引（语义检索） |
| `types.py` | 数据类型 |
| `weaver.py` | `MemoryWeaver` 融合引擎 |
| `consolidation.py` | `ConsolidationEngine` 记忆自演化 |
| `reincarnation.py` | `ReincarnationEngine` 记忆生命周期管理 |
| `distill_worker.py` | 后台闲置蒸馏：从进回收站的会话/角色提炼记忆，带重试上限与蒸馏标记 |
| `lesson.py` | 失败教训提取：把 run/工具/LLM 失败转成可复用的 lesson 记忆条目 |
| `events.py` | 记忆域事件常量 |

## 关键契约

- **删除默认软删除**：记忆条目经回收站（TTL 30 天），禁止硬删；蒸馏以回收站条目为输入（trash_id 贯穿），失败重试到上限后打 `distill_failed` 标记而不是无限循环。
- 记忆事件族 `memory.stored/deleted/accessed`、`memory.synapse.created/removed/orphaned/redirected`、`memory.consolidation.done`、`memory.insight.generated`、`memory.pod.recycled/marked`、`memory.reap.done`、`memory.skill.forge_request` 全部登记在事件目录。
- 后台工作（蒸馏/巩固）在闲置时段跑，绝不进会话 turn 热路径。
- 全局教训去重由 curator 子系统负责（见其文档），本模块只产出条目。
