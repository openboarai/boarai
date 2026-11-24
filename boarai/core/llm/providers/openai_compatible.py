"""
OpenAI-compatible LLM Provider for BoarAi system.

This provider supports any LLM API that follows the OpenAI API format,
including OpenAI, DeepSeek, Moonshot, Claude (via proxy), etc.
"""

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from ..catalog import echoes_reasoning_content, forbids_sampling, get_reasoning_field
from ..protocol import (
    LLMError,
    LLMRequest,
    LLMResponse,
    Message,
    StreamChunk,
)
from ..thinking import decide_thinking, resolve_thinking_spec
from .base import HTTPProviderBase

logger = logging.getLogger(__name__)


def _is_vision_rejection(error_text: str) -> bool:
    """Detect upstream rejection of multimodal content blocks."""
    t = (error_text or "").lower()
    return "image_url" in t or "unknown variant" in t or "expected `text`" in t


class OpenAICompatibleProvider(HTTPProviderBase):
    """OpenAI-compatible LLM provider."""
    
    
    def __init__(
        self,
        name: str,
        api_key: str,
        api_base: str = "https://api.openai.com/v1",
        supported_models: Optional[List[str]] = None,
        default_model: str = "gpt-3.5-turbo",
        timeout: float = 300.0,
        max_retries: int = 3,
        options: Optional[Dict[str, Any]] = None,
        model_info: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        """Initialize OpenAI-compatible provider."""
        self._name = name
        self._api_key = api_key
        self._api_base = api_base.rstrip("/")
        # Distinguish None (unspecified → fallback) from [] (explicit empty list)
        if supported_models is None:
            self._supported_models = [default_model] if default_model else []
        else:
            self._supported_models = list(supported_models)
        self._default_model = default_model
        self._timeout = timeout
        self._max_retries = max_retries
        self._options = options if isinstance(options, dict) else {}
        self._model_info = model_info if isinstance(model_info, dict) else {}
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()
    
    @property
    def name(self) -> str:
        """Get provider name."""
        return self._name
    
    @property
    def supported_models(self) -> List[str]:
        """Get list of supported models."""
        return self._supported_models
    
    def _build_headers(self) -> Dict[str, str]:
        """Build request headers."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        # Add custom headers from options
        if "headers" in self._options:
            headers.update(self._options["headers"])
        return headers
    
    def _convert_messages(
        self, messages: List[Message], keep_reasoning: bool = False
    ) -> List[Dict[str, Any]]:
        """Convert internal message format to OpenAI format."""
        result = []
        for msg in messages:
            converted = {"role": msg.role.value, "content": msg.content}
            if msg.name:
                converted["name"] = msg.name
            if msg.tool_call_id:
                converted["tool_call_id"] = msg.tool_call_id
            if msg.tool_calls:
                converted["tool_calls"] = msg.tool_calls
            if msg.reasoning_content and keep_reasoning:
                converted["reasoning_content"] = msg.reasoning_content
            result.append(converted)
        return result
    
    def _build_request_body(self, request: LLMRequest) -> Dict[str, Any]:
        """Build request body for API call."""
        model_name = request.model or self._default_model

        # Session thinking_mode -> provider-native thinking params. The wire
        # layer resolves the model's catalog record (plus any provider-level or
        # per-model override) and clamps the requested level to the levels that
        # model actually supports.
        spec = resolve_thinking_spec(
            model_name,
            provider_name=self._name,
            api_base=self._api_base,
            options=self._options,
            model_info=self._model_info,
        )
        decision = decide_thinking(spec, request.reasoning_effort)

        body = {
            "model": model_name,
            "messages": self._convert_messages(
                request.messages,
                keep_reasoning=(
                    bool(self._options.get("keep_reasoning_content"))
                    or echoes_reasoning_content(model_name)
                ),
            ),
            "temperature": request.temperature,
            "top_p": request.top_p,
            "stream": request.stream,
        }

        # Streaming: ask the API to include usage (with cache hit/miss token
        # details) in the final chunk. OpenAI-standard; supported by the
        # official DeepSeek API. Without it, streaming responses omit usage
        # entirely so token/cache statistics report 0 hits.
        if request.stream:
            body["stream_options"] = {"include_usage": True}
        
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        
        if request.frequency_penalty != 0.0:
            body["frequency_penalty"] = request.frequency_penalty
        
        if request.presence_penalty != 0.0:
            body["presence_penalty"] = request.presence_penalty
        
        if request.stop:
            body["stop"] = request.stop
        
        if request.tools:
            body["tools"] = request.tools
        
        if request.tool_choice is not None:
            body["tool_choice"] = request.tool_choice
        
        # Session thinking_mode -> provider-native thinking params (resolved
        # above, before the body was built, because the message conversion
        # depends on the model's catalog record).
        if decision.fields:
            body.update(decision.fields)
        if forbids_sampling(model_name):
            # Moonshot rejects a sampling temperature; the field is omitted
            # rather than set to a value the endpoint may refuse.
            body.pop("temperature", None)
            body.pop("top_p", None)
        
        # Add extra options from provider config. This is the highest-priority
        # layer: an explicit extra_body thinking field wins over the wire layer.
        if "extra_body" in self._options:
            body.update(self._options["extra_body"])
        if decision.note:
            logger.info("Thinking wire: %s", decision.note)
        final_fields = {
            key: body[key]
            for key in (
                "reasoning_effort",
                "thinking",
                "reasoning",
                "enable_thinking",
                "output_config",
                "chat_template_kwargs",
            )
            if key in body
        }
        if final_fields or decision.note:
            logger.info(
                "Thinking request: provider=%s model=%s effort=%s -> format=%s level=%s fields=%s",
                self._name,
                model_name,
                request.reasoning_effort,
                decision.format,
                decision.level,
                final_fields,
            )
        
        return body

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate response from LLM."""
        url = f"{self._api_base}/chat/completions"
        headers = self._build_headers()
        body = self._build_request_body(request)
        
        # Disable streaming for non-stream call
        body["stream"] = False
        
        last_error: Optional[LLMError] = None
        for attempt in range(self._max_retries + 1):
            try:
                session = await self._get_session()
                async with session.post(url, headers=headers, json=body) as response:
                    if response.status != 200:
                        err = self._http_error(
                            response.status, await response.text(), request
                        )
                        if not err.retryable or attempt >= self._max_retries:
                            raise err
                        last_error = err
                        await self._retry_delay(
                            request, attempt, err, detail=f"HTTP {response.status}"
                        )
                        continue

                    data = await response.json()
                    return self._parse_response(data, request.model)

            except LLMError as e:
                if not getattr(e, "retryable", False) or attempt >= self._max_retries:
                    raise
                last_error = e
                await self._retry_delay(request, attempt, e)
            except asyncio.TimeoutError:
                last_error = LLMError(
                    f"Request idle-timed out after {self._timeout}s without data",
                    self._name,
                    request.model,
                    retryable=True,
                )
                if attempt >= self._max_retries:
                    raise last_error
                await self._retry_delay(request, attempt, last_error, detail="timeout")
            except (aiohttp.ClientError, ConnectionError, OSError) as e:
                last_error = LLMError(
                    f"Request failed: {e}",
                    self._name,
                    request.model,
                    retryable=True,
                )
                if attempt >= self._max_retries:
                    raise last_error
                await self._retry_delay(request, attempt, last_error)
            except Exception as e:
                # Unknown errors: do not spin forever; one-shot fail
                raise LLMError(
                    f"Request failed: {e}",
                    self._name,
                    request.model,
                    retryable=False,
                ) from e

        raise last_error or LLMError(
            "Request failed after retries",
            self._name,
            request.model,
        )
    
    async def generate_stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:
        """Generate streaming response from LLM."""
        url = f"{self._api_base}/chat/completions"
        headers = self._build_headers()
        body = self._build_request_body(request)
        body["stream"] = True
        # A catalog entry can pin the delta field carrying reasoning for a relay
        # that renames it; without one the three common spellings are tried.
        reasoning_field = get_reasoning_field(request.model or self._default_model)

        last_error: Optional[LLMError] = None
        for attempt in range(self._max_retries + 1):
            # Reset per-attempt stream state (only retry before any yield)
            tool_acc: Dict[int, Dict[str, Any]] = {}
            accumulated_content = ""
            accumulated_reasoning = ""
            finish_reason: Optional[str] = None
            final_usage: Optional[Dict[str, int]] = None
            yielded_any = False

            try:
                session = await self._get_session()
                async with session.post(url, headers=headers, json=body) as response:
                    if response.status != 200:
                        err = self._http_error(
                            response.status, await response.text(), request
                        )
                        if not err.retryable or attempt >= self._max_retries:
                            raise err
                        last_error = err
                        await self._retry_delay(
                            request, attempt, err,
                            phase="Stream", detail=f"HTTP {response.status}",
                        )
                        continue

                    # Read lines from SSE stream
                    buf = b""
                    async for chunk in response.content:
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            line_str = line.decode("utf-8").strip()
                            if not line_str:
                                continue
                            # Accept both "data: {...}" and "data:{...}" — some
                            # providers omit the space after the colon.
                            if line_str.startswith("data:"):
                                data = line_str[5:].lstrip()
                                if not data:
                                    continue
                                if data == "[DONE]":
                                    # Flush accumulated tool_calls before exit
                                    if tool_acc:
                                        yielded_any = True
                                        yield StreamChunk(
                                            tool_calls=list(tool_acc.values()),
                                        )
                                    return
                                try:
                                    chunk_data = json.loads(data)
                                    choices = chunk_data.get("choices", [])
                                    if not choices:
                                        # Some providers send usage in a chunk with empty choices
                                        # after the final content chunk. Don't skip it.
                                        # NOTE: no finish_reason here — forcing "stop"
                                        # used to overwrite the real terminal reason
                                        # ("tool_calls" / "length") downstream.
                                        usage_only = chunk_data.get("usage")
                                        if usage_only:
                                            yielded_any = True
                                            yield StreamChunk(
                                                usage=self._usage_dict(usage_only),
                                            )
                                        continue

                                    choice = choices[0]
                                    delta = choice.get("delta", {})

                                    # --- Reasoning content (DeepSeek R1, Qwen, OpenRouter, etc.) ---
                                    reasoning = (
                                        (delta.get(reasoning_field) if reasoning_field else "")
                                        or delta.get("reasoning_content")
                                        or delta.get("reasoning")
                                        or ""
                                    )
                                    if not reasoning:
                                        details = delta.get("reasoning_details")
                                        if isinstance(details, list):
                                            reasoning = "".join(
                                                str(
                                                    detail.get("text")
                                                    or detail.get("summary")
                                                    or ""
                                                )
                                                for detail in details
                                                if isinstance(detail, dict)
                                            )
                                    if reasoning:
                                        accumulated_reasoning += reasoning
                                        yielded_any = True
                                        yield StreamChunk(
                                            reasoning=reasoning,
                                        )

                                    # --- Text content ---
                                    content = delta.get("content", "")
                                    if content:
                                        accumulated_content += content
                                        yielded_any = True
                                        yield StreamChunk(content=content)

                                    # --- Tool calls (incremental arguments) ---
                                    tc_list = delta.get("tool_calls")
                                    if tc_list:
                                        for tc in tc_list:
                                            idx = tc.get("index", 0)
                                            if idx not in tool_acc:
                                                tool_acc[idx] = {}
                                            acc = tool_acc[idx]
                                            func = tc.get("function", {})
                                            # Accumulate id (usually on first chunk)
                                            if tc.get("id"):
                                                acc["id"] = tc["id"]
                                            # Accumulate name
                                            if func.get("name"):
                                                acc.setdefault("function", {})["name"] = func["name"]
                                            # Accumulate arguments fragments
                                            if func.get("arguments"):
                                                frag = func["arguments"]
                                                acc.setdefault("function", {}).setdefault("arguments", "")
                                                acc["function"]["arguments"] += frag
                                            # Set type
                                            acc.setdefault("type", "function")

                                    # --- Finish reason & usage (last chunk) ---
                                    finish_reason = choice.get("finish_reason")
                                    if finish_reason:
                                        final_usage = chunk_data.get("usage")
                                        # Build final tool_calls if finish_reason == "tool_calls"
                                        final_tcs = None
                                        if finish_reason == "tool_calls" and tool_acc:
                                            final_tcs = list(tool_acc.values())
                                        yielded_any = True
                                        yield StreamChunk(
                                            content="",
                                            tool_calls=final_tcs,
                                            finish_reason=finish_reason,
                                            usage=(
                                                self._usage_dict(final_usage)
                                                if final_usage else None
                                            ),
                                        )

                                except json.JSONDecodeError:
                                    continue

                # Stream closed cleanly without [DONE] / finish chunk (some
                # gateways truncate the tail) — flush accumulated tool_calls
                # so a clean-but-truncated stream does not silently drop them.
                if tool_acc:
                    yielded_any = True
                    yield StreamChunk(tool_calls=list(tool_acc.values()))
                # Success - exit retry loop
                return

            except LLMError as e:
                if yielded_any or not getattr(e, "retryable", False) or attempt >= self._max_retries:
                    raise
                last_error = e
                await self._retry_delay(request, attempt, e, phase="Stream")
            except asyncio.TimeoutError:
                last_error = LLMError(
                    f"Streaming request idle-timed out after {self._timeout}s without data",
                    self._name,
                    request.model,
                    retryable=True,
                )
                if yielded_any or attempt >= self._max_retries:
                    raise last_error
                await self._retry_delay(
                    request, attempt, last_error, phase="Stream", detail="timeout"
                )
            except (aiohttp.ClientError, ConnectionError, OSError) as e:
                last_error = LLMError(
                    f"Streaming request failed: {e}",
                    self._name,
                    request.model,
                    retryable=True,
                )
                if yielded_any or attempt >= self._max_retries:
                    raise last_error
                await self._retry_delay(
                    request, attempt, last_error, phase="Stream"
                )
            except Exception as e:
                raise LLMError(
                    f"Streaming request failed: {e}",
                    self._name,
                    request.model,
                    retryable=False,
                ) from e

        raise last_error or LLMError(
            "Streaming request failed after retries",
            self._name,
            request.model,
        )
    
    def _looks_like_vision_rejection(self, error_text: str) -> bool:
        """OpenAI-compatible relays are the ones that phrase it this way."""
        return _is_vision_rejection(error_text)

    @staticmethod
    def _usage_dict(usage: Optional[Dict[str, Any]]) -> Dict[str, int]:
        """Normalize an OpenAI-shaped usage block."""
        usage = usage or {}
        return {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            # Mimo/OpenAI: cached_tokens in nested prompt_tokens_details
            "cached_tokens": (usage.get("prompt_tokens_details") or {}).get(
                "cached_tokens", 0
            ),
            # DeepSeek: cache hit/miss at the top level
            "cache_hit_tokens": usage.get("prompt_cache_hit_tokens", 0),
            "cache_miss_tokens": usage.get("prompt_cache_miss_tokens", 0),
            "completion_tokens_details": usage.get("completion_tokens_details") or {},
            "reasoning_tokens": (
                (usage.get("completion_tokens_details") or {}).get(
                    "reasoning_tokens", 0
                )
                or usage.get("reasoning_tokens", 0)
            ),
        }

    def _parse_response(self, data: Dict[str, Any], requested_model: Optional[str]) -> LLMResponse:
        """Parse API response into LLMResponse."""
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})

        # Extract reasoning content from non-streaming response
        reasoning = message.get("reasoning_content") or message.get("reasoning")

        return LLMResponse(
            content=message.get("content", ""),
            model=data.get("model", requested_model or self._default_model),
            provider=self._name,
            usage=self._usage_dict(usage),
            finish_reason=choice.get("finish_reason", "stop"),
            tool_calls=message.get("tool_calls"),
            metadata={
                **data,
                "reasoning_content": reasoning,
            } if reasoning else data,
        )
    
    async def validate_connection(self) -> bool:
        """Validate connection to LLM provider."""
        try:
            # Try to list models as a connection test
            session = await self._get_session()
            url = f"{self._api_base}/models"
            headers = self._build_headers()
            
            async with session.get(url, headers=headers) as response:
                return response.status == 200
        except Exception as e:
            logger.error(f"Connection validation failed for {self._name}: {e}")
            return False
    
    async def get_available_models(self) -> List[Dict[str, Any]]:
        """Get available models from provider API."""
        try:
            session = await self._get_session()
            url = f"{self._api_base}/models"
            headers = self._build_headers()
            
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.warning(
                        f"Failed to fetch models from {self._name} (status {response.status}): {error_text}"
                    )
                    # Fall back to static list
                    return await super().get_available_models()
                
                data = await response.json()
                models = []
                
                for model_data in data.get("data", []):
                    model_id = model_data.get("id", "")
                    if not model_id:
                        continue
                    models.append({
                        "id": model_id,
                        "name": model_id,
                        "description": f"Owned by: {model_data.get('owned_by', 'unknown')}",
                        "provider": self._name,
                        "created": model_data.get("created"),
                        "object": model_data.get("object", "model"),
                    })
                
                logger.info(f"Fetched {len(models)} models from {self._name}")
                return models
        
        except Exception as e:
            logger.warning(f"Failed to fetch models from {self._name}: {e}")
            # Fall back to static list
            return await super().get_available_models()
    
