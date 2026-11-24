"""
Anthropic LLM Provider for BoarAi system.

This provider supports Anthropic's Claude API.
"""

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from ..catalog import forbids_sampling, get_max_output_tokens
from ..protocol import (
    LLMError,
    LLMRequest,
    LLMResponse,
    Message,
    MessageRole,
    StreamChunk,
)
from ..thinking import decide_thinking, resolve_thinking_spec
from .base import HTTPProviderBase

logger = logging.getLogger(__name__)

# Anthropic API version
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(HTTPProviderBase):
    """Anthropic Claude LLM provider."""
    
    
    def __init__(
        self,
        name: str,
        api_key: str,
        api_base: str = "https://api.anthropic.com",
        supported_models: Optional[List[str]] = None,
        default_model: str = "claude-3-5-sonnet-20241022",
        timeout: float = 300.0,
        max_retries: int = 3,
        options: Optional[Dict[str, Any]] = None,
        model_info: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        """Initialize Anthropic provider."""
        self._name = name
        self._api_key = api_key
        self._api_base = api_base.rstrip("/")
        # Distinguish None (unspecified → fallback) from [] (explicit empty list)
        if supported_models is None:
            self._supported_models = [
                "claude-3-5-sonnet-20241022",
                "claude-3-opus-20240229",
                "claude-3-sonnet-20240229",
                "claude-3-haiku-20240307",
            ]
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
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
        # Add custom headers from options
        if "headers" in self._options:
            headers.update(self._options["headers"])
        return headers

    @staticmethod
    def _convert_multimodal_content(content: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert OpenAI-style multimodal content blocks to Anthropic blocks."""
        blocks: List[Dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                blocks.append({"type": "text", "text": item.get("text", "")})
            elif item.get("type") == "image_url":
                url = (item.get("image_url") or {}).get("url") or ""
                if url.startswith("data:"):
                    header, _, data = url[len("data:"):].partition(",")
                    media_type = header.split(";")[0] or "image/png"
                    blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": data,
                        },
                    })
        return blocks
    
    def _convert_messages(self, messages: List[Message]) -> tuple[Optional[str], List[Dict[str, Any]]]:
        """Convert internal message format to Anthropic format."""
        system_parts: List[str] = []
        result = []

        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                # Merge all system messages (fixes overwrite bug)
                if isinstance(msg.content, str) and msg.content:
                    system_parts.append(msg.content)
            elif msg.role == MessageRole.USER:
                content = msg.content
                if isinstance(content, list):
                    content = self._convert_multimodal_content(content)
                result.append({"role": "user", "content": content})
            elif msg.role == MessageRole.ASSISTANT:
                content = msg.content
                if isinstance(content, list):
                    content = self._convert_multimodal_content(content)
                result.append({"role": "assistant", "content": content})
            elif msg.role == MessageRole.TOOL:
                # Anthropic handles tool results differently
                result.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id or "",
                            "content": msg.content,
                        }
                    ]
                })

        system_prompt = "\n\n".join(system_parts) if system_parts else None
        return system_prompt, result
    
    def _build_request_body(self, request: LLMRequest) -> Dict[str, Any]:
        """Build request body for API call."""
        system_prompt, messages = self._convert_messages(request.messages)
        model_name = request.model or self._default_model

        # Extended thinking via the session thinking mode.
        # Adaptive models (Claude 4.6+) use ``thinking: {type: adaptive}`` plus
        # ``output_config.effort``; older models use an opt-in token budget. An
        # explicit off sends ``thinking: {type: disabled}`` where the model
        # accepts it and omits the field elsewhere.
        spec = resolve_thinking_spec(
            model_name,
            provider_name=self._name,
            api_base=self._api_base,
            provider_type="anthropic",
            options=self._options,
            model_info=self._model_info,
        )
        decision = decide_thinking(spec, request.reasoning_effort)
        thinking_fields = dict(decision.fields)
        if not thinking_fields and request.thinking_budget and request.thinking_budget > 0:
            # Legacy explicit budget path (the wire layer produced nothing).
            thinking_fields = {
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": int(request.thinking_budget),
                }
            }
        block = thinking_fields.get("thinking")
        budget = (
            int(block["budget_tokens"])
            if isinstance(block, dict) and isinstance(block.get("budget_tokens"), int)
            else 0
        )

        max_tokens = int(request.max_tokens or 4096)
        if budget > 0:
            # Thinking tokens count toward max_tokens, so the answer needs room
            # beyond the budget or it is truncated mid-sentence. The catalog's
            # output ceiling bounds both: a budget that cannot fit under the
            # model's own limit is shrunk instead of requesting an impossible
            # max_tokens.
            ceiling = get_max_output_tokens(model_name)
            if ceiling and ceiling > 0:
                max_tokens = min(max(max_tokens, budget + 4096), ceiling)
                budget = min(budget, max(1024, max_tokens - 4096))
                block["budget_tokens"] = budget
            else:
                max_tokens = max(max_tokens, budget + 4096)

        body: Dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        # Claude 4.7+ rejects any non-default sampling parameter, so both fields
        # are omitted rather than set. Budget-mode thinking requires
        # temperature=1; adaptive thinking leaves the caller's value alone.
        if not forbids_sampling(model_name):
            body["temperature"] = 1 if budget > 0 else request.temperature
            body["top_p"] = request.top_p

        if system_prompt:
            body["system"] = system_prompt

        if thinking_fields:
            body.update(thinking_fields)
        if decision.note:
            logger.info("Thinking wire: %s", decision.note)
        
        if request.stop:
            body["stop_sequences"] = request.stop
        
        # Handle tools (Anthropic format) with optional cache_control
        if request.tools:
            anthropic_tools = []
            for tool in request.tools:
                if tool.get("type") == "function":
                    func = tool.get("function", {})
                    anthropic_tools.append({
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "input_schema": func.get("parameters", {}),
                    })
            
            # Apply cache_control to last tool if breakpoints request it
            cache_bps = request.cache_breakpoints or {}
            anthropic_bps = cache_bps.get("anthropic", {})
            if anthropic_tools and anthropic_bps.get("tool_last"):
                anthropic_tools[-1]["cache_control"] = {"type": "ephemeral"}
            
            if anthropic_tools:
                body["tools"] = anthropic_tools
        
        # Apply cache_control to message breakpoints
        cache_bps = request.cache_breakpoints or {}
        anthropic_bps = cache_bps.get("anthropic", {})
        for msg_idx in anthropic_bps.get("message_indices", []):
            if 0 <= msg_idx < len(body["messages"]):
                body["messages"][msg_idx]["cache_control"] = {"type": "ephemeral"}
        
        # Add extra options from provider config. This is the highest-priority
        # layer: an explicit extra_body thinking field wins over the wire layer.
        if "extra_body" in self._options:
            body.update(self._options["extra_body"])
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
        url = f"{self._api_base}/v1/messages"
        headers = self._build_headers()
        body = self._build_request_body(request)
        
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
        """Generate streaming response from LLM (Anthropic SSE)."""
        url = f"{self._api_base}/v1/messages"
        headers = self._build_headers()
        body = self._build_request_body(request)
        body["stream"] = True

        last_error: Optional[LLMError] = None
        for attempt in range(self._max_retries + 1):
            # Reset per-attempt stream state (only retry before any yield)
            text_acc = ""
            tool_acc: Dict[str, Dict[str, Any]] = {}  # tool_use_id -> partial tool call
            tool_order: List[str] = []  # preserve insertion order
            current_block_index: Optional[int] = None
            current_block_type: Optional[str] = None
            stop_reason: Optional[str] = None
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

                    async for raw_line in response.content:
                        line = raw_line.decode("utf-8").strip()
                        if not line:
                            continue
                        if line.startswith("data: "):
                            data = line[6:]
                            try:
                                event = json.loads(data)
                            except json.JSONDecodeError:
                                logger.warning(f"Failed to parse event: {data}")
                                continue

                            event_type = event.get("type")

                            # --- content_block_start: tool_use or text block ---
                            if event_type == "content_block_start":
                                current_block_index = event.get("index")
                                block = event.get("content_block", {})
                                current_block_type = block.get("type")
                                if current_block_type == "tool_use":
                                    tid = block.get("id", "")
                                    tool_acc[tid] = {
                                        "id": tid,
                                        "type": "function",
                                        "function": {
                                            "name": block.get("name", ""),
                                            "arguments": "",
                                        },
                                    }
                                    tool_order.append(tid)
                                elif current_block_type == "text":
                                    text_acc += block.get("text", "")
                                    if block.get("text"):
                                        yielded_any = True
                                        yield StreamChunk(content=block["text"])
                                elif current_block_type == "thinking":
                                    # Extended thinking block. The text may be
                                    # summarized (adaptive) or full (budget).
                                    initial = block.get("thinking", "")
                                    if initial:
                                        yielded_any = True
                                        yield StreamChunk(reasoning=initial)
                                elif current_block_type == "redacted_thinking":
                                    yielded_any = True
                                    yield StreamChunk(reasoning="[Reasoning redacted]")

                            # --- content_block_delta: text or input_json fragments ---
                            elif event_type == "content_block_delta":
                                delta = event.get("delta", {})
                                dt = delta.get("type")
                                if dt == "text_delta":
                                    frag = delta.get("text", "")
                                    text_acc += frag
                                    if frag:
                                        yielded_any = True
                                        yield StreamChunk(content=frag)
                                elif dt == "thinking_delta":
                                    frag = delta.get("thinking", "")
                                    if frag:
                                        yielded_any = True
                                        yield StreamChunk(reasoning=frag)
                                elif dt == "input_json_delta":
                                    # Find the tool being accumulated (by index or last)
                                    idx = event.get("index")
                                    # Find the tool_use matching this block index
                                    for tid in reversed(tool_order):
                                        if tid in tool_acc:
                                            frag = delta.get("partial_json", "")
                                            if frag:
                                                tool_acc[tid]["function"]["arguments"] += frag
                                            break

                            # --- content_block_stop: a block finished ---
                            elif event_type == "content_block_stop":
                                current_block_type = None
                                current_block_index = None

                            # --- message_delta: stop_reason + usage ---
                            elif event_type == "message_delta":
                                delta = event.get("delta", {})
                                stop_reason = delta.get("stop_reason")
                                usage = event.get("usage", {})
                                if usage:
                                    final_usage = {
                                        "prompt_tokens": usage.get("input_tokens", 0),
                                        "completion_tokens": usage.get("output_tokens", 0),
                                        "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                                        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                                        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                                    }

                            # --- message_stop: end of stream ---
                            elif event_type == "message_stop":
                                # Emit final chunk with tool_calls if any
                                final_tcs = None
                                if tool_acc:
                                    final_tcs = [tool_acc[tid] for tid in tool_order if tid in tool_acc]
                                yielded_any = True
                                yield StreamChunk(
                                    content="",
                                    tool_calls=final_tcs,
                                    finish_reason=stop_reason or "end_turn",
                                    usage=final_usage,
                                )
                                return

                # Success
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
    
    def _parse_response(self, data: Dict[str, Any], requested_model: Optional[str]) -> LLMResponse:
        """Parse API response into LLMResponse."""
        content = ""
        thinking = ""
        tool_calls = []

        for block in data.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    }
                })
            elif block.get("type") == "thinking":
                thinking += block.get("thinking", "")

        usage = data.get("usage", {})

        return LLMResponse(
            content=content,
            model=data.get("model", requested_model or self._default_model),
            provider=self._name,
            usage={
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
            },
            finish_reason=data.get("stop_reason", "end_turn"),
            tool_calls=tool_calls if tool_calls else None,
            metadata={
                **data,
                "thinking": thinking,
            } if thinking else data,
        )
    
    async def validate_connection(self) -> bool:
        """Validate connection to LLM provider."""
        try:
            # Make a minimal request to test connection
            session = await self._get_session()
            url = f"{self._api_base}/v1/messages"
            headers = self._build_headers()
            body = {
                "model": self._default_model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "Hi"}],
            }
            
            async with session.post(url, headers=headers, json=body) as response:
                return response.status == 200
        except Exception as e:
            logger.error(f"Connection validation failed for {self._name}: {e}")
            return False
    
    async def get_available_models(self) -> List[Dict[str, Any]]:
        """Get available models from Anthropic."""
        # Anthropic has no models endpoint, return configured models
        model_descriptions = {
            "claude-3-5-sonnet-20241022": "Claude 3.5 Sonnet - Best balance of intelligence and speed",
            "claude-3-opus-20240229": "Claude 3 Opus - Most powerful model for complex tasks",
            "claude-3-sonnet-20240229": "Claude 3 Sonnet - Balance of speed and intelligence",
            "claude-3-haiku-20240307": "Claude 3 Haiku - Fastest model for simple tasks",
            "claude-3-5-haiku-20241022": "Claude 3.5 Haiku - Fast with improved capabilities",
        }
        
        return [
            {
                "id": model,
                "name": model,
                "description": model_descriptions.get(model, ""),
                "provider": self._name,
            }
            for model in self._supported_models
        ]
    
