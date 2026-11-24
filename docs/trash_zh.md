> [English](trash.md) | **简体中文**

# 回收站（core/trash）

软删除回收站 + 写前文件快照。全系统删除用户数据的唯一出口：**禁止业务代码直接硬删**。

## 存储布局

```
<数据目录>/trash/
├── trash.json          ← 全部条目的索引
├── sessions/{ts}_{session_id}/
├── agents/{ts}_{agent_name}/
├── skills/{ts}_{skill_name}/     ← 整目录
└── files/{ts}_{hash8}__{filename}

<数据目录>/snapshots/files/{ts}_{hash8}__{filename}   ← 写前快照
```

## TTL（分类型）

| 类型 | 保留期 |
|------|--------|
| 会话 | 7 天 |
| 角色 / 记忆 / 技能 / 扩展 | 30 天 |
| 文件 / 快照 | 48 小时 |

过期清理每小时一轮（`CLEANUP_INTERVAL`）。

## 关键契约

- 会话、角色、技能、记忆、文件的删除全部经 `TrashManager` 进回收站，可恢复（restore）；恢复角色后默认标记等派生状态由各自 API 层补全。
- 快照支撑写/编辑撤销：文件工具改动前存快照，run 级回滚与会话变更面板消费它（消费方在会话子系统）。
- 记忆蒸馏以回收站条目为输入（见记忆系统文档），条目带 trash_id 贯穿蒸馏状态。
- 阻塞的文件操作用 `asyncio.to_thread` 隔离，不在事件循环里同步搬文件。
- REST 面由 trash 领域 API 提供（列表/恢复/清空）。
