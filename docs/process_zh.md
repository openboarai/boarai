> [English](process.md) | **简体中文**

# 进程管理（core/process）

服务器进程探测：**端口权威**，不用 PID 文件。

## 原则

- 服务器端口的监听者是唯一真源：谁持有端口谁是候选；候选的命令行还必须证明自己是 BoarAI 服务器入口（`_cmdline_is_boar`），运行时才报 "running" 或执行 kill。
- **没有 PID 文件、没有锁文件**：两者在崩溃后都会过期残留，会授权错误的 kill。

## 入口形态识别

命令行匹配三种服务器入口形态（小写化、basename 锚定）：

| 形态 | 示例 |
|------|------|
| 源码树 | `python <root>/main.py start` |
| pip 模块 | `python -m boarai start` |
| 控制台脚本 | `.../boarai[.exe] start` |

basename 锚定保证：路径里仅仅**包含** "boarai" 的检出目录（或 `tests/run.py` 这类测试 runner）不会冒充服务器；`-m` 形态要求裸包名，`-m boarai.core.update.worker`（更新 worker 子进程）永不被误认成服务器。

## 消费方

入口的 stop / restart / status 子命令与重启时的"验证性 kill 兜底"都经 `ProcessManager`（`get_process_manager()`）；restart 重新 spawn 时选择与当前渠道一致的入口形态并透传数据目录参数。
