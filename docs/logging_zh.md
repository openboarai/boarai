> [English](logging.md) | **简体中文**

# 日志系统（core/logging）

一个根 logger、多个 sink、一个写线程的日志管道，落盘在 `<数据目录>/logs/<YYYY-MM-DD>/` 日期目录树下。

## 模块构成

| 文件 | 职责 |
|------|------|
| `manager.py` | `LogManager` 管道接线（`get_log_manager()` / `get_logger()`），logger 名 → 类别 sink 的映射 |
| `config.py` | `LoggingConfig` / `LogLevel`：每个字段都已接线——存在却不起作用的旋钮比没有旋钮更糟 |
| `handlers.py` | `DatedCategoryHandler` 等按日期分类别的 handler |
| `formatters.py` | `SafeFormatter`：所有 sink 统一包裹的格式化器 |
| `context.py` | 每协程日志上下文（all.log 混流时靠它区分来源） |
| `redact.py` | 全部 sink 落盘前的密钥脱敏 |
| `crash.py` | 崩溃与未捕获异常兜底捕获（绕过日志系统的三条失败路径） |
| `retention.py` | 保留策略：账龄预算 + 总大小预算双界 |
| `reader.py` | 日志树的只读访问（纯函数，供日志检视 API 用） |

## 行为约定

- 日志是唯一比 run 长寿且会离开机器的产物——**任何 sink 落盘前必过脱敏**，敏感 pattern 名单与配置 schema 共享。
- 保留双界：只按天数不够，失控重试循环一天能写出 GB 级日志，必须有总大小预算。
- 类别映射把 logger 名归并到 sink（如 `core.extension` / `boarai.core.extension` / `boar.extensions` / `extensions` 全部归 `extension.log`）；新增顶层包名要同步进映射表。
- 读取侧（reader）是对日期目录布局的纯函数，不持有状态；日志 API 只读。
- 事件总线另有自己的 JSONL 审计（见事件系统文档），不走本管道。

## 编码约定

业务模块一律 `logger = logging.getLogger(__name__)`，禁止 `print`（唯一例外：入口模块的 CLI 用户输出）。
