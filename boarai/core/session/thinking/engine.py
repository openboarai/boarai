"""
Thinking engine for BoarAi (session sub-module).

Provides:
1. ThinkingRouter - Intent-based channel selection
2. ThinkingEngine - ReAct loop that orchestrates thinking, tool calling,
   and intent routing

The ThinkingEngine replaces the raw LLM call loop in run_manager with
a managed thinking process that handles:
- Intent routing (fast/standard/plan channels)
- Thinking mode injection
- Stream think-block processing
- Silence detection and recovery
- Reasoning content persistence
"""

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple

from .config import (
    ThinkingConfig,
    ThinkingMode,
    resolve_thinking_config,
    emits_reasoning_content,
)
from .processor import (
    ReasoningContentManager,
    SilenceDetector,
    StreamingThinkProcessor,
    ThinkProcessorResult,
)

logger = logging.getLogger(__name__)


class ThinkingChannel(str, Enum):
    """Channel selected by the intent router."""
    FAST = "fast"           # Direct response, no tools
    STANDARD = "standard"   # Full ReAct loop with tools
    PLAN = "plan"           # Planning mode: subagent + sequential execution


@dataclass
class RouterResult:
    """Result from intent routing."""
    channel: ThinkingChannel = ThinkingChannel.STANDARD
    thinking: ThinkingConfig = field(default_factory=ThinkingConfig)
    reason: str = ""


class ThinkingRouter:
    """Intent-based channel router."""

    # Keywords that suggest tool usage → STANDARD channel
    _TOOL_TRIGGERS = frozenset({
        "搜索", "查找", "查询",
        "search", "find", "lookup", "grep", "glob",
        "读取", "读文件", "打开",
        "read", "open", "show me",
        "list", "目录",
        "下载", "fetch", "web",
        # Debug/troubleshoot → needs tools (grep, shell, read), not multi-step planning
        "调试", "排查", "修复", "修复bug",
        "debug", "troubleshoot", "fix bug",
    })

    # Keywords that suggest multi-step planning → PLAN channel
    _PLAN_TRIGGERS = frozenset({
        "重构", "重写", "搭建", "创建项目",
        "refactor", "rewrite", "create project", "build from scratch",
        "设计", "架构", "分析整个",
        "design", "architecture", "analyze entire",
        "迁移", "升级", "迁移到",
        "migrate", "upgrade", "convert",
        "实现一个", "开发一个",
        "implement a", "build a", "develop a",
        "编写单元测试", "写测试",
        "write tests", "unit test",
    })

    # Short queries that are likely simple Q&A → FAST channel
    _SHORT_QA_PATTERNS = frozenset({
        "什么是",
        "what is", "what's", "what are",
        "解释", "解释一下",
        "explain", "describe",
        "为什么",
        "why", "why does", "why is",
        "如何", "怎么",
        "how to", "how do", "how can",
        "你好", "hello", "hi", "hey",
    })

    # Minimum length for a query to be considered for standard/plan channel
    _SHORT_INPUT_THRESHOLD = 80  # characters

    def __init__(
        self,
        default_mode: ThinkingMode = ThinkingMode.NONE,
        enable_auto: bool = True,
    ) -> None:
        """Initialize the router."""
        self._default_mode = default_mode
        self._enable_auto = enable_auto

    def route(
        self,
        user_input: str,
        thinking_mode: Optional[ThinkingMode] = None,
        model: str = "",
    ) -> RouterResult:
        """Route a user input to the appropriate channel."""
        mode = thinking_mode or self._default_mode
        thinking = ThinkingConfig(mode=mode)

        if not self._enable_auto or mode != ThinkingMode.AUTO:
            # Explicit mode → use it directly, fall back to STANDARD
            channel = self._determine_channel_for_mode(mode, user_input)
            return RouterResult(
                channel=channel,
                thinking=resolve_thinking_config(mode, model),
                reason=f"explicit_mode={mode.value}",
            )

        # Auto-detect channel
        input_lower = user_input.strip().lower()
        input_len = len(user_input)

        # 1. Very short Q&A → FAST
        if input_len < self._SHORT_INPUT_THRESHOLD:
            if any(input_lower.startswith(p) for p in self._SHORT_QA_PATTERNS):
                return RouterResult(
                    channel=ThinkingChannel.FAST,
                    thinking=ThinkingConfig(mode=ThinkingMode.NONE),
                    reason="short_qa_detected",
                )

        # 2. Complex/multi-step → PLAN
        if any(trigger in input_lower for trigger in self._PLAN_TRIGGERS):
            return RouterResult(
                channel=ThinkingChannel.PLAN,
                thinking=resolve_thinking_config(ThinkingMode.DEEP, model),
                reason="plan_trigger_detected",
            )

        # 3. Tool-requiring → STANDARD
        if any(trigger in input_lower for trigger in self._TOOL_TRIGGERS):
            return RouterResult(
                channel=ThinkingChannel.STANDARD,
                thinking=resolve_thinking_config(ThinkingMode.LIGHT, model),
                reason="tool_trigger_detected",
            )

        # 4. Long input, no specific trigger → STANDARD (safe default)
        return RouterResult(
            channel=ThinkingChannel.STANDARD,
            thinking=resolve_thinking_config(ThinkingMode.LIGHT, model),
            reason="default_standard",
        )

    def _determine_channel_for_mode(
        self,
        mode: ThinkingMode,
        user_input: str,
    ) -> ThinkingChannel:
        """Determine channel based on explicit thinking mode."""
        if mode == ThinkingMode.ULTRA:
            return ThinkingChannel.PLAN
        return ThinkingChannel.STANDARD




@dataclass
class ThinkingEngineResult:
    """Final result from the ThinkingEngine."""
    content: str = ""
    """Complete visible response."""
    thinking: str = ""
    """Complete extracted thinking content."""
    turns: int = 0
    """Number of ReAct turns executed."""
    channel: ThinkingChannel = ThinkingChannel.STANDARD
    finish_reason: str = "stop"
    tool_calls: Optional[List[Dict[str, Any]]] = None
    """Tool calls extracted from the last stream (when finish_reason='tool_calls')."""


class ThinkingEngine:
    """Orchestrates the thinking + tool calling loop."""

    # Maximum characters for a FAST channel response before fallback
    _FAST_MAX_CHARS = 2000

    def __init__(
        self,
        router: Optional[ThinkingRouter] = None,
    ) -> None:
        self._router = router or ThinkingRouter()
        self._think_processor: Optional[StreamingThinkProcessor] = None
        self._silence_detector: Optional[SilenceDetector] = None
        self._reasoning_manager: Optional[ReasoningContentManager] = None

    async def run(
        self,
        llm_call: Callable[..., AsyncIterator],
        user_input: str,
        session_messages: List[Dict[str, Any]],
        model: str = "",
        tools: Optional[List[Dict[str, Any]]] = None,
        cache_breakpoints: Optional[Dict[str, Any]] = None,
        thinking_mode: Optional[ThinkingMode] = None,
        max_turns: int = 0,  # 0 = engine default (100 turns)
        on_chunk: Optional[Callable[[str], None]] = None,
        on_thinking: Optional[Callable[[str], None]] = None,
        on_tool_result: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_channel_switch: Optional[Callable[[ThinkingChannel], None]] = None,
        text_buffer: Optional[List[str]] = None,
        thinking_buffer: Optional[List[str]] = None,
    ) -> ThinkingEngineResult:
        """Execute a complete thinking + tool calling run."""
        self._think_processor = StreamingThinkProcessor()
        self._silence_detector = SilenceDetector()
        self._reasoning_manager = ReasoningContentManager()

        # --- Step 1: Route intent ---
        routing = self._router.route(user_input, thinking_mode, model)
        # Auto is an explicit "send nothing" request. The keyword router still
        # chooses the channel, but it must not inject a thinking level of its
        # own (the global kill switch and the sub-agent auto mode rely on this).
        auto_requested = thinking_mode == ThinkingMode.AUTO or (
            isinstance(thinking_mode, str)
            and thinking_mode.strip().lower() in ("auto", "default")
        )
        if thinking_mode is not None and auto_requested:
            routing.thinking = ThinkingConfig(mode=ThinkingMode.AUTO)
        if on_channel_switch:
            on_channel_switch(routing.channel)

        logger.info(
            f"ThinkingEngine: channel={routing.channel.value}, "
            f"mode={routing.thinking.mode.value}, reason={routing.reason}"
        )

        # --- Step 2: Execute channel ---
        if routing.channel == ThinkingChannel.FAST:
            return await self._execute_fast(
                llm_call=llm_call,
                session_messages=session_messages,
                model=model,
                tools=tools,
                thinking_config=routing.thinking,
                on_chunk=on_chunk,
                on_thinking=on_thinking,
                text_buffer=text_buffer,
                thinking_buffer=thinking_buffer,
            )

        # Standard & Plan channels share the ReAct loop
        return await self._execute_react(
            llm_call=llm_call,
            session_messages=session_messages,
            model=model,
            tools=tools,
            cache_breakpoints=cache_breakpoints,
            thinking_config=routing.thinking,
            max_turns=max_turns,
            on_chunk=on_chunk,
            on_thinking=on_thinking,
            on_tool_result=on_tool_result,
            text_buffer=text_buffer,
            thinking_buffer=thinking_buffer,
        )

    async def _execute_fast(
        self,
        llm_call: Callable[..., AsyncIterator],
        session_messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]],
        thinking_config: ThinkingConfig,
        on_chunk: Optional[Callable[[str], None]],
        on_thinking: Optional[Callable[[str], None]],
        text_buffer: Optional[List[str]],
        thinking_buffer: Optional[List[str]],
    ) -> ThinkingEngineResult:
        """Execute a fast (direct response) channel."""
        # Apply thinking constraint prompt if needed
        if thinking_config.mode != ThinkingMode.NONE:
            constraint = thinking_config.get_constraint_prompt(model)
            if constraint and session_messages:
                # Add constraint to the last user message
                last_user = None
                for i in range(len(session_messages) - 1, -1, -1):
                    if session_messages[i].get("role") == "user":
                        last_user = i
                        break
                if last_user is not None:
                    old_content = session_messages[last_user].get("content", "")
                    session_messages[last_user]["content"] = old_content + constraint

        content, thinking = await self._stream_llm(
            llm_call=llm_call,
            messages=session_messages,
            model=model,
            tools=tools,
            thinking_config=thinking_config,
            on_chunk=on_chunk,
            on_thinking=on_thinking,
            text_buffer=text_buffer,
            thinking_buffer=thinking_buffer,
        )

        # Check for silence
        is_silent = self._silence_detector.check(content, thinking)
        if is_silent and not self._silence_detector.is_exhausted:
            # Retry with prefill
            prefill = self._silence_detector.get_prefill()
            session_messages.append({
                "role": "assistant",
                "content": prefill,
            })
            content, thinking = await self._stream_llm(
                llm_call=llm_call,
                messages=session_messages,
                model=model,
                tools=tools,
                thinking_config=thinking_config,
                on_chunk=on_chunk,
                on_thinking=on_thinking,
                text_buffer=text_buffer,
                thinking_buffer=thinking_buffer,
            )

        self._reasoning_manager = ReasoningContentManager()

        return ThinkingEngineResult(
            content=content,
            thinking=thinking,
            turns=1,
            channel=ThinkingChannel.FAST,
            finish_reason="stop",
        )

    async def _execute_react(
        self,
        llm_call: Callable[..., AsyncIterator],
        session_messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]],
        cache_breakpoints: Optional[Dict[str, Any]],
        thinking_config: ThinkingConfig,
        max_turns: int,
        on_chunk: Optional[Callable[[str], None]],
        on_thinking: Optional[Callable[[str], None]],
        on_tool_result: Optional[Callable[[Dict[str, Any]], None]],
        text_buffer: Optional[List[str]],
        thinking_buffer: Optional[List[str]],
    ) -> ThinkingEngineResult:
        """Execute the ReAct (think→act→observe) loop."""
        full_content = ""
        full_thinking = ""
        # The engine is also called without an explicit limit (legacy
        # callers); a zero here used to leave ``turn`` unbound after the
        # loop. Keep one bounded default for every caller.
        effective_max_turns = max_turns if max_turns > 0 else 100
        completed = False

        for turn in range(effective_max_turns):
            logger.debug(f"ThinkingEngine ReAct turn {turn}")

            # Apply thinking config
            config_mods = thinking_config.apply_to_turn(model)

            content, thinking = await self._stream_llm(
                llm_call=llm_call,
                messages=session_messages,
                model=model,
                tools=tools,
                cache_breakpoints=cache_breakpoints,
                thinking_config=thinking_config,
                on_chunk=on_chunk,
                on_thinking=on_thinking,
                text_buffer=text_buffer,
                thinking_buffer=thinking_buffer,
                extra_config=config_mods,
            )

            full_content += content
            full_thinking += thinking

            # Check for silence (reasoning-only response)
            if self._silence_detector.check(content, thinking):
                if self._silence_detector.is_exhausted:
                    logger.warning("Silence detection exhausted, stopping")
                    break
                # Inject prefill and continue
                prefill = self._silence_detector.get_prefill()
                logger.info("Silence detected, injecting prefill")
                session_messages.append({
                    "role": "assistant",
                    "content": prefill,
                })
                continue

            # Check if LLM returned tool_calls (last chunk should have them)
            turn_tool_calls = self._last_tool_calls
            if turn_tool_calls:
                # Tools will be executed by the caller (run_manager)
                # Return here so caller can execute tools and call us again
                # with the updated session_messages
                logger.debug(
                    f"ReAct turn {turn}: {len(turn_tool_calls)} tool call(s)"
                )
                # Signal caller to continue by returning with tool_calls
                return ThinkingEngineResult(
                    content="",
                    thinking=full_thinking,
                    turns=turn + 1,
                    channel=ThinkingChannel.STANDARD,
                    finish_reason="tool_calls",
                    tool_calls=list(turn_tool_calls),
                )

            # Text response → done (for this round)
            completed = True
            break

        # Apply reasoning consistency padding
        try:
            padded = self._reasoning_manager.get_padding_messages(session_messages)
            if padded != session_messages:
                session_messages[:] = padded
        except Exception:
            pass

        return ThinkingEngineResult(
            content=full_content,
            thinking=full_thinking,
            turns=turn + 1,
            channel=ThinkingChannel.STANDARD,
            finish_reason="stop" if completed else "length",
        )

    async def _stream_llm(
        self,
        llm_call: Callable[..., AsyncIterator],
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]],
        cache_breakpoints: Optional[Dict[str, Any]] = None,
        thinking_config: Optional[ThinkingConfig] = None,
        on_chunk: Optional[Callable[[str], None]] = None,
        on_thinking: Optional[Callable[[str], None]] = None,
        text_buffer: Optional[List[str]] = None,
        thinking_buffer: Optional[List[str]] = None,
        extra_config: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str]:
        """Stream LLM response with think-block processing."""
        processor = StreamingThinkProcessor()
        reasoning_mgr = ReasoningContentManager()

        self._last_tool_calls = None
        accumulated_text = ""
        accumulated_thinking = ""

        resolve = self._has_reasoning
        has_reasoning = emits_reasoning_content(model) if resolve else False

        # Track whether a think block was opened at all
        # (for UI event broadcasting)
        think_was_opened = False

        try:
            async for chunk in llm_call(
                messages=messages,
                model=model,
                tools=tools,
                cache_breakpoints=cache_breakpoints,
                **(extra_config or {}),
            ):
                # --- Reasoning content from API field (native) ---
                # Only applicable for providers that set chunk.reasoning
                chunk_reasoning = getattr(chunk, "reasoning", None) or ""
                if chunk_reasoning:
                    reasoning_mgr.feed_reasoning_delta(chunk_reasoning)
                    accumulated_thinking += chunk_reasoning
                    if on_thinking:
                        on_thinking(chunk_reasoning)
                    if thinking_buffer is not None:
                        thinking_buffer.append(chunk_reasoning)

                # --- Reasoning content from stream field (OpenAI reasoning_content) ---
                # For OpenAI-compatible providers that expose reasoning_content
                chunk_reasoning_content = getattr(chunk, "reasoning_content", None) or ""
                if chunk_reasoning_content:
                    reasoning_mgr.feed_reasoning_delta(chunk_reasoning_content)
                    accumulated_thinking += chunk_reasoning_content
                    if on_thinking:
                        on_thinking(chunk_reasoning_content)
                    if thinking_buffer is not None:
                        thinking_buffer.append(chunk_reasoning_content)

                # --- Text content: process through think-block state machine ---
                if chunk.content:
                    result = processor.feed(chunk.content)

                    if result.content:
                        reasoning_mgr.feed_text_delta(result.content)
                        accumulated_text += result.content
                        if on_chunk:
                            on_chunk(result.content)
                        if text_buffer is not None:
                            text_buffer.append(result.content)

                    if result.thinking:
                        accumulated_thinking += result.thinking
                        if on_thinking:
                            on_thinking(result.thinking)
                        if thinking_buffer is not None:
                            thinking_buffer.append(result.thinking)

                    # Track think block open/close for UI events
                    if result.think_opened:
                        think_was_opened = True
                    if result.think_closed:
                        think_was_opened = True

                    # Also feed to reasoning_manager for completeness
                    if result.thinking:
                        reasoning_mgr.feed_text_delta("")

                # --- Tool calls (accumulated across chunks) ---
                if chunk.tool_calls:
                    self._last_tool_calls = chunk.tool_calls

        except Exception as e:
            logger.error(f"Stream LLM error in ThinkingEngine: {e}")
            # Still return whatever we've accumulated
            return accumulated_text, accumulated_thinking

        # Flush any remaining thinking content
        flush = processor.flush()
        if flush:
            accumulated_thinking += flush
            if on_thinking:
                on_thinking(flush)
            if thinking_buffer is not None:
                thinking_buffer.append(flush)

        return accumulated_text, accumulated_thinking

    @property
    def _has_reasoning(self) -> bool:
        """Check if reasoning pipeline is needed."""
        return True

    @property
    def _last_tool_calls(self) -> Optional[List[Dict[str, Any]]]:
        """Last tool calls detected in streaming (set by _stream_llm)."""
        return getattr(self, "_last_tool_calls_val", None)

    @_last_tool_calls.setter
    def _last_tool_calls(self, value: Optional[List[Dict[str, Any]]]) -> None:
        self._last_tool_calls_val = value
