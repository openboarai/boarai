> [English](command.md) | **简体中文**

# 命令系统（core/command）

两个互不相干的域住在这里：会话内（聊天）`/命令`，以及入口的服务器进程管理。**没有注入式 shell 命令，也没有终端命令注册表。**

## 模块构成

| 文件 | 职责 |
|------|------|
| `registry.py` | 会话内命令注册表：core 与扩展贡献的 `/命令`（`CommandInfo`，带 source 归属） |
| `chat.py` | 聊天命令拦截与执行——完整的命令运行时 |
| `dispatcher.py` | `main.py` 入口背后的服务器进程管理（stop / restart / status），不是命令系统 |
| `bootstrap.py` | 目录布局的单一真源：每个入口子命令统一调 `create_directories` |

## 聊天命令语义

- 消息文本是单个 `/token`（可跟参数）且 token 已注册时，在**任何 LLM run 之前**被拦截。
- `handler` kind：进程内执行，输出作为助手回复持久化，LLM 完全不参与。
- `prompt` kind：模板用原始参数文本展开后**替换**消息内容，走正常 run 管道（按设计到达 LLM）。
- 匹配器刻意保守：`/path/to/file` 永不匹配（token 后必须是空白或文本结尾）；未注册的 `/token` 作为普通文本落给模型。
- 三个消息入口——WebUI WS `session.run.create`、REST run 创建、ChannelHost turn 管道——共用这里的匹配/展开/持久化实现，保持单一真源。

## 扩展贡献

扩展经门面 `api.register_command(name, handler, description, aliases=..., kind=..., template=...)` 注册；门面记录归属，卸载时清扫（被他人接管的命令不代删）。

## 进程管理

`cmd_stop / cmd_restart / cmd_status` 服务 `boarai` 控制台子命令；restart 重新 spawn 时按渠道选择正确入口形态并透传 `--data-dir`。
