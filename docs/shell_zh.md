> [English](shell.md) | **简体中文**

# Shell 执行后端（core/shell）

可插拔的 shell 执行后端：`ShellBackend` 是回答"这条命令在哪跑"的接缝。

## 模块构成

| 符号 | 职责 |
|------|------|
| `ShellBackend` | 后端协议（`ShellRequest` → `ShellOutcome`） |
| `LocalBackend` | 宿主机直接执行（默认） |
| `DockerBackend` | 经 `docker` CLI 进容器执行 |
| `BackendInfo` / `IsolationLevel` | 后端自报隔离级别与暴露的宿主面 |
| `register_backend` / `configure_backend` / `get_shell_backend` / `available_backends` | 注册与选择 |

## 设计要点

- 每个后端报告 `BackendInfo`（隔离级别 + 暴露了哪些宿主面），工具守卫据此把审批策略与**实际隔离**耦合，而不是只判命令文本——同一条命令在宿主机是灾难、在不挂载任何东西的容器里无害。
- **放宽是单向的**：只有在没有任何宿主面可达时，隔离才可以放松审批决定。带 bind mount、host 网络或共享 PID namespace 的容器不是边界——把它当边界就是"沙箱命令改写 `~/.ssh`"的由来。
- 隔离级别枚举：`NONE`（同宿主同用户同文件系统）→ `PROCESS`（独立进程/用户但宿主文件系统仍可达）→ 容器级。
- Docker 挂载规格解析锚定容器侧绝对 POSIX 路径（`/` 开头），防止 Windows 盘符被误当分隔符。

## 边界

- 命令的权限分类与审批在安全子系统（保守分词 + ToolGuard），执行在 shell 工具扩展，本模块只提供"在哪跑、跑完什么结果"。
- 后端选择是部署配置，扩展与工具不自行挑后端。
