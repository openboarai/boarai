> [English](config.md) | **简体中文**

# 配置系统（core/config）

主配置文件 `<数据目录>/config/config.yaml` 的解析、schema 与损坏回退。

## 模块构成

| 文件 | 职责 |
|------|------|
| `schema.py` | `BoarAiConfig` 及各分节 dataclass（system / llm / plugin / session / logging / security / thinking / run）、敏感文件 pattern 名单 |
| `parser.py` | `ConfigParser`：加载、格式探测、校验、保存 |

## 行为约定

- **缺文件是全新安装的正常状态**：不报错（INFO 级），采用内置默认值，文件在首次设置变更时惰性落盘；绝不主动生成 defaults 模板文件。
- **损坏回退默认值，不 brick 启动**：读失败/非映射/YAML 带 legacy python-tag，一律 `logger.error` + 采用默认值，提示从 UI 重新保存以重写干净文件。
- 校验失败的字段回退该字段默认值，不整体拒绝。
- 非 schema 顶层键（如 `bootstrap`）在 `save()` 时原样保留——保存不得吃掉自己不认识的配置。
- 保存走原子写（`write_text_atomic_async`），崩溃不留半截 config.yaml。
- `cp._config` 在缺文件路径也必须被填充：调用方以它作为写路径的门（否则每次保存都成静默 no-op，文件永远造不出来）。

## 边界

- LLM 密钥与 Base URL 不在 config.yaml，单独存 `<数据目录>/config/llm.yaml`（归 LLM 子系统管），并列入脱敏 pattern 名单（`**/auth.json`、`.boarai/config/llm.yaml`）。
- 扩展自己的配置不在这里：走扩展门面的 `get_config/set_config`，持久化在扩展私有数据目录。
- 配置变更发布 `config.changed` / `config.reloaded` 事件（登记在事件目录，发布方在使用侧）。
