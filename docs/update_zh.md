> [English](update.md) | **简体中文**

# 在线更新（core/update）

pip / git / source 三渠道的自更新子系统。原则：**服务器进程永不自己跑 pip**，更新由分离的 worker 子进程执行。

## 模块构成

| 文件 | 职责 |
|------|------|
| `service.py` | 服务端编排：status / 手动 check / apply / 自动检查 |
| `worker.py` | 分离更新 worker（`python -m boarai.core.update.worker --target <ver>`） |
| `checker.py` | 版本检查：PyPI 风格 JSON 端点，仅标准库 urllib |
| `steward.py` | 安装管家：判定安装种类、渠道归属与预检 |
| `state.py` | worker 状态：OS 文件锁 + 状态文件 + 回执（全在数据目录锚下） |
| `version.py` | 版本解析比较（仅标准库） |

## 工作方式

- `apply` spawn 分离 worker，然后把 worker 的状态文件镜像到事件总线（`update.progress` / `update.done` / `update.failed`）直至终态。
- worker 结束时请求本服务器重启；`done` 在进程 re-exec 前经事件总线发布，错过它的客户端重连后从 `GET /api/update/status`（磁盘终态）补读。
- 版本检查是只读且天然网络易碎的：所有失败都降级，不抛给用户。
- 只有纯数字点分版本（如 `1.12.0`）参与更新比较。

## 守卫（拒绝契约）

- `update.enabled` 配置开关。
- 按渠道预检：git 渠道查二进制/远端/脏树，source 渠道查树完整性——不满足回 400，拒绝信息必须指名修复动作。
- 已有存活 worker（持锁）→ 409，跟随它而不是再起一个。
- 有活动 run → 409：绝不在工作中的 agent 脚下换包。
- 冷却期内对同一目标的重复尝试 → 409。

## 渠道规则

三种安装种类都经**自己的渠道就地自更新**，更新永不跨渠道（跨渠道"更新"会留下半套安装）。PyPI 是唯一版本源。
