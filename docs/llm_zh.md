> [English](llm.md) | **简体中文**

# LLM 抽象层（core/llm）

LLM 全链路的唯一居所：协议、供应商实现、注册路由、对外门面与思考线协议。内核其他目录禁止出现 LLM 调用；禁止引入 vendor SDK，供应商全部基于 aiohttp 手写。

## 模块构成

| 文件 | 职责 |
|------|------|
| `protocol.py` | `Message` / `LLMRequest` / `LLMResponse` / `StreamChunk` / `LLMProvider` ABC / `LLMError` |
| `providers/base.py` | aiohttp 供应商共享 HTTP 管道（重试、错误翻译） |
| `providers/openai_compatible.py` | OpenAI-compatible 供应商 |
| `providers/anthropic.py` | Anthropic 供应商 |
| `manager.py` | 供应商注册/路由/事件，读取 `llm.yaml`（context_windows、capabilities、vision 治理） |
| `service.py` | 对外门面（chat / chat_stream）与 `llm.yaml` 持久化 |
| `catalog.py` | 模型目录：核心无法从请求推断的供应商事实，只读文件 `boar/config/models.yaml` |
| `thinking/formats.py` | 目录数据与思考解释器共享的线格式词表 |
| `thinking/wire.py` | 供应商原生思考参数（thinking 线层） |

## Provider 协议

新供应商必须实现 `LLMProvider` ABC：

- `name` / `supported_models` property；
- `async generate(LLMRequest) -> LLMResponse`；
- `async generate_stream(LLMRequest) -> AsyncIterator[StreamChunk]`；
- 可选覆写 `validate_connection` / `get_model_info` / `get_available_models`。

上下文窗口：`get_context_window()` 优先读用户在 `llm.yaml` 的 `context_windows` 覆写，其次查模型目录；YAML 裸键可能被解析成 float，覆写键一律 str 归一。

## 请求/响应模型

- `LLMRequest`：messages / model / provider / temperature / max_tokens / tools / tool_choice / stream / cache_breakpoints（供应商特定缓存断点）/ `reasoning_effort`（low|medium|high|xhigh|none|off）/ `thinking_budget`（legacy Anthropic）/ `on_retry` 回调（可 sync 可 async，不进 to_dict）。
- `StreamChunk`：`content`（可见文本）、`thinking`（`<think>` 块状态机提取）、`reasoning`（API 原生推理字段；None=不可用，""=可用但空）、`tool_calls`（OpenAI 标准格式）、`finish_reason`、`usage`（仅结束块）。
- `Message.reasoning_content` 是回传给 API 的助手思考文本，不是可见内容。

## 错误与重试

- 统一 `LLMError`：provider / model / status_code / `retryable`（由 HTTP 状态推导：408/429/5xx 可重试）/ `vision_rejected`（供应商拒绝 image_url 块时置位，执行侧剥图降级重试，不另造异常类型）。
- `is_retryable_http_status` 是可重试判定的唯一实现。

## 密钥与配置

- 密钥与 Base URL 只存 `<数据目录>/config/llm.yaml`：不入库、不入日志，且列入脱敏名单。
- 保存 `llm.yaml` 时未注册 provider 的既有配置必须原样保留（不能因为当前没加载就抹掉用户配置）。
- 每模型能力（vision / thinking / context window）声明在 `llm.yaml` 的 `model_info` / `capability_models`，运行时代码读声明而不是猜模型名；未知能力键丢弃。

## 思考线层

会话层把 UI 思考模式（auto / off / light / …）归一化后，由 `thinking/wire.py` 翻译成各供应商原生参数；覆写优先级：`llm.yaml` 的 `model_info.<model>.thinking` > 目录默认。词表集中在 `formats.py`，目录文件不能命名请求侧不认识的格式。

## 事件

供应商注册/注销与请求生命周期发布 `llm.provider.registered/unregistered`、`llm.request/response/error`（登记在事件目录）。
