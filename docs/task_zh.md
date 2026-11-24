> [English](task.md) | **简体中文**

# 任务系统（core/task）

定时/计划任务：模型、状态机、调度器、执行器与任务 API。

## 模块构成

| 文件 | 职责 |
|------|------|
| `models.py` | 任务数据模型 |
| `kind.py` | 任务种类定义 |
| `status.py` | 任务状态机定义 |
| `manager.py` | 任务管理器（CRUD、生命周期） |
| `scheduler.py` | 任务调度器 |
| `storage.py` | 任务持久化 |
| `api.py` | 任务 REST handler（经 core/api 包 re-export） |
| `events.py` | 任务事件助手 |
| `timeutil.py` | 时区助手：目标时区墙钟的 naive datetime |
| `executor/base.py` | 执行器 ABC |
| `executor/ai_executor.py` | AI 任务执行器（走会话 run） |
| `executor/script_executor.py` | 脚本任务执行器 |

## 关键契约

- 任务事件族 `task.created/updated/status_changed/progress/completed/failed/cancelled/step_completed/sub_created` 登记在事件目录，并由启动编排转发给 WS 客户端（实时 UI 更新）。
- 执行器按任务种类分派：AI 任务复用会话 run 链路，脚本任务独立执行；新种类加执行器而不是改调度器。
- 调度时间语义是"目标时区墙钟"：存 naive datetime，换算集中在 `timeutil.py`，不散落各处。
- 任务恢复（进程重启后的补偿）有专门契约测试盯守。
