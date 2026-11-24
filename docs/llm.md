> **English** | [简体中文](llm_zh.md)

# LLM Abstraction Layer (core/llm)

The sole home of the LLM chain: protocol, provider implementations, registration/routing, the public facade and the thinking wire layer. No other kernel directory may call LLMs; vendor SDKs are forbidden — providers are hand-written on aiohttp.

## Modules

| File | Responsibility |
|------|----------------|
| `protocol.py` | `Message` / `LLMRequest` / `LLMResponse` / `StreamChunk` / `LLMProvider` ABC / `LLMError` |
| `providers/base.py` | Shared aiohttp HTTP plumbing for providers (retries, error translation) |
| `providers/openai_compatible.py` | OpenAI-compatible provider |
| `providers/anthropic.py` | Anthropic provider |
| `manager.py` | Provider registration/routing/events; reads `llm.yaml` (context_windows, capabilities, vision governance) |
| `service.py` | Public facade (chat / chat_stream) and `llm.yaml` persistence |
| `catalog.py` | Model catalog: vendor facts the core cannot infer from a request; read-only `boar/config/models.yaml` |
| `thinking/formats.py` | Wire-format vocabulary shared by catalog data and the thinking interpreter |
| `thinking/wire.py` | Provider-native thinking parameters (the thinking wire layer) |

## Provider protocol

A new provider must implement the `LLMProvider` ABC:

- `name` / `supported_models` properties;
- `async generate(LLMRequest) -> LLMResponse`;
- `async generate_stream(LLMRequest) -> AsyncIterator[StreamChunk]`;
- optional overrides: `validate_connection` / `get_model_info` / `get_available_models`.

Context window: `get_context_window()` prefers the user's `context_windows` override in `llm.yaml`, then the model catalog; YAML may parse bare keys as float, so override keys are always normalized to str.

## Request/response models

- `LLMRequest`: messages / model / provider / temperature / max_tokens / tools / tool_choice / stream / cache_breakpoints (provider-specific cache breakpoints) / `reasoning_effort` (low|medium|high|xhigh|none|off) / `thinking_budget` (legacy Anthropic) / `on_retry` callback (sync or async; not serialized in to_dict).
- `StreamChunk`: `content` (visible text), `thinking` (extracted by the `<think>` state machine), `reasoning` (native API reasoning field; None = unavailable, "" = available but empty), `tool_calls` (OpenAI standard format), `finish_reason`, `usage` (finish chunk only).
- `Message.reasoning_content` is assistant thinking text echoed back to the API, not visible content.

## Errors and retries

- Unified `LLMError`: provider / model / status_code / `retryable` (derived from HTTP status: 408/429/5xx retryable) / `vision_rejected` (set when the provider rejects image_url blocks; the executor strips images and retries — no separate exception type).
- `is_retryable_http_status` is the single implementation of the retryability decision.

## Keys and configuration

- Keys and Base URLs live only in `<data>/config/llm.yaml`: never committed, never logged, on the redaction list.
- Saving `llm.yaml` must preserve existing configuration of unregistered providers verbatim (a provider not currently loaded must not have its user config wiped).
- Per-model capabilities (vision / thinking / context window) are declared in `llm.yaml` under `model_info` / `capability_models`; runtime code reads declarations instead of guessing model names; unknown capability keys are dropped.

## Thinking wire layer

The session layer normalizes the UI thinking mode (auto / off / light / ...) and `thinking/wire.py` translates it into provider-native parameters; override precedence: `llm.yaml` `model_info.<model>.thinking` > catalog defaults. The vocabulary lives in `formats.py` so a catalog file cannot name a shape the request side does not know.

## Events

Provider registration/unregistration and request lifecycle publish `llm.provider.registered/unregistered` and `llm.request/response/error` (registered in the event catalogue).
