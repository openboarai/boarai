> [English](security.md) | **简体中文**

# 安全系统（core/security）

多层安全：传输层访问门禁、文件路径沙箱、工具权限守卫、shell 命令分类、审计与脱敏。

## 模块构成

| 文件 | 职责 |
|------|------|
| `__init__.py` | `SecurityManager` 门面：聚合 PathGuard / ToolGuard / AuditLog，`get_security_manager()` |
| `gate.py` | HTTP + WS 边缘访问门禁："这个请求能不能过"的唯一真源 |
| `access.py` | 边缘访问控制：访问钥匙、设备会话、限流（`get_access_store()`） |
| `path_guard.py` | `PathGuard` 文件路径沙箱 |
| `path_utils.py` | L2 围栏共享工具："先规范化再判包含"与绝对路径判定 |
| `tool_guard.py` | `ToolGuard` 运行时工具执行权限检查 |
| `shell_tokenize.py` | 保守 shell 命令分词器，供权限分类 |
| `audit.py` | `AuditLog` 结构化安全事件审计 |
| `redact.py` | 审计记录落盘前脱敏（审批/智能审批条目持久化原始命令串与工具参数） |

## 访问钥匙与门禁

- 门禁只在传输层：API、WS、扩展路由统一过 gate；钥匙只存 scrypt 哈希，**永不回显**，`rotate` 是取新钥唯一途径。
- 钥匙轮换必须两阶段：prepare 展示新钥时旧钥仍有效，confirm 才生效——"生成即生效 + 只显示一次"是自锁陷阱，禁止。
- 首钥由浏览器引导页创建（后端永不自动生成）；未认领实例全封闭：回环可自由认领，远程浏览器必须附加服务器启动日志中打印的一次性设置码（无头/外网安装因此可用，且"谁先碰到谁认领"的窗口不会重开）。无头部署也可用 `boarai key init` 显式生成。
- 环境变量 `BOARAI_API_TOKEN` 必须 ≥32 位 base64url，否则启动即拒；首启时被收养为钥匙。
- 钥匙轮换/设备吊销发布 `auth.revoked` 事件并镜像到所有 WS，被踢设备必须掉线而不是继续服务死会话。
- 设备会话可列出、可单独吊销；吊销只删会话不动钥匙。

## 守卫模型

- `SecurityManager.initialize(config, project_root)` 用配置构建三个守卫；**运行时更新原地改同一 config 对象**（WebUI 设置页编辑的是活状态，不做脱钩副本）。
- 工具权限按工具自声明的 `security_profile` 元数据路由（file_read / file_write / file_delete / shell / none），**绝不按工具名 hardcode 策略**；守卫拒绝时结果 metadata 带 `security_block` + `reason`，上层归为 `permission` 错误类。
- 路径判定统一"canonicalize-then-contain"；shell 命令用保守分词切成独立段再分类，分不出来的按不可分类处理（有专门测试矩阵）。
- 审批（ask/deny/allow）策略与会话默认安全模式的配置面在安全配置分节，执行面在 run 循环的确认门。

## 审计

`AuditLog` 结构化记录安全事件（审批裁决、守卫拒绝等），落盘前过 `redact.py` 脱敏；审计路径变更时门面负责重建 AuditLog。
