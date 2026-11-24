> [English](runtime.md) | **简体中文**

# 运行时基础设施（core/runtime）

内核级基础设施的集中地：路径锚、原子写、后台任务、有界缓存、首启引导与服务器启动编排。跨子系统复用、无业务语义的代码放这里。

## 模块构成

| 文件 | 职责 |
|------|------|
| `data_paths.py` | 数据目录锚的唯一真源 |
| `root.py` | 进程级项目根锚的唯一真源 |
| `atomic_write.py` | 崩溃安全的文本写入 |
| `bg.py` | 抗 GC 的 fire-and-forget 任务派生 |
| `lru.py` | 按 session 键控的有界 LRU 缓存 |
| `json_body.py` | HTTP API 共享的 JSON 请求体解析（畸形输入必出 4xx） |
| `artifact_mark.py` | 后台产物（技能/任务输出/分块）的完成标记 |
| `bootstrap.py` | 首启引导：从 `boar/defaults/` 播种数据目录 + 核心依赖安装 |
| `serve.py` | 前台服务器启动编排与事件→WS 转发接线 |

## 数据目录锚

- 全部运行时状态落在唯一数据目录，锚定在源码树之外；解析序：`--data-dir` > 环境变量 `BOARAI_DATA_DIR` > `Path.home()/.boarai`。
- 子系统一律经 `get_data_dir()` / `resolve_data_path()` 取路径，禁止硬编码 `.boarai` 位置；配置里的相对 `.boarai/...` 值由 `resolve_data_path` 落到数据目录。
- Windows 上无盘符的绝对路径（如 NAS 配置带来的 `/vol1/...`）降级映射进数据目录 `_mapped_/`，绝不静默落到当前盘根；真 UNC 路径原样保留。
- 扩展私有数据目录：`extension_data_dir(ext_id)` → `<数据目录>/extensions/data/<ext_id>`，id 含 `.`、`..`、路径分隔符直接 `ValueError`。
- 旧源码树内 `<仓库根>/.boarai` 不做自动迁移（系统里禁止写数据迁移逻辑）：手动挪或用 `BOARAI_DATA_DIR` 指回。

## 项目根锚

`set_project_root()` 启动时钉一次（入口负责），所有子系统经 `get_project_root()` 读取；禁止用 `Path.cwd()` 或 `__file__` 层级推算当项目根。`resolve_project_root()` 的 cwd 兜底只属于锚解析自身。

## 原子写

`open(path, "w")` 先截断后写，崩溃会留半截文件——所有配置/状态落盘走 `write_text_atomic` / `write_text_atomic_async`（临时文件 + 替换）。写失败按调用方语义决定是 best-effort 还是上抛，但绝不允许非原子直写。

## 首启引导

- `copy_defaults()` 把 `boar/defaults/*` 播种进数据目录，以 `config.yaml` 的 `bootstrap.initialized` 标记；一旦置位**永不重拷**，用户数据永不覆盖。
- 启动顺序上 `copy_defaults` 必须先于目录骨架创建：`is_initialized()` 把已存在的 sessions/agents/memory/logs 目录视为"已引导"，先建骨架会让全新安装跳过播种。
- 核心依赖安装只走显式 `python main.py --ensure-deps`（`ensure_core_dependencies`），入口模块 import 期绝不触网；`CORE_DEPENDENCIES` 与 pyproject 依赖保持一致。

## 启动编排 serve.py

前台启动流程（配置装载 → 日志 → 安全 → 会话/任务子系统 → 扩展加载 → 统一服务器）在此收口；并负责把 task / session / skill / update / auth / 终态 run 事件订阅转发给 WS 客户端（转发清单是事件登记链的一环）。
