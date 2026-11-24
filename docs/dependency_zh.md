> [English](dependency.md) | **简体中文**

# 依赖管理（core/dependency）

扩展声明的 Python 包（manifest `pip_dependencies`）的安装与验证。

## 原则

- **检查用 import 探针，不读元数据**：半卸载或损坏的包仍有 metadata 但运行时会炸——`__import__` 失败即视为缺失（包名连字符转下划线后探测）。
- **安装串行化**：防止多个 pip 进程争抢同一目标目录。
- 探测结果进缓存（`_installed_cache`），同一包不重复探针。

## 接口

`DependencyManager`（`get_dependency_manager()`）：`check_package(spec)` 报告当前可否 import；安装路径接收完整版本 spec（如 `aiohttp>=3.8.0`），提取包名后验证。

## 边界

- **核心依赖不归这里装**：入口 import 期绝不触网，核心依赖只有显式 `--ensure-deps` 一条安装路径（在运行时引导模块）。
- 本模块服务的是扩展加载链路：loader 在装载声明了 `pip_dependencies` 的扩展前经这里检查/安装。
- 任务系统的脚本执行器有独立的运行环境处理，不共用本缓存。
