"""
Streaming think-block processor for BoarAi.

Handles three key concerns during LLM streaming:
1. <think> block extraction: State machine that tracks open/close tags
   across chunk boundaries
2. reasoning_content extraction: Captures field-level reasoning
   from models like DeepSeek, Qwen, Moonshot
3. Silence detection: Detects reasoning-only responses where
   model emitted reasoning but no content or tool_calls

The StreamingThinkProcessor is designed as a stateful streaming
state machine that processes each StreamChunk as it arrives,
accumulating thinking content and producing cleaned text output.

State machine states:
    IDLE      - Outside any think block
    TAG_OPEN  - Detected '<think' prefix, accumulating tag chars
    THINKING  - Inside an active <think> block
    TAG_CLOSE - Detected '</' prefix, closing tag
"""

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

class ThinkState(Enum):
    """Stream state machine states for think-block parsing."""
    IDLE = auto()
    TAG_OPEN = auto()       # Detected '<think', accumulating
    THINKING = auto()       # Inside <think> block
    TAG_CLOSE = auto()      # Detected '</', closing

@dataclass
class ThinkProcessorResult:
    """Result from processing a single stream chunk."""
    content: str = ""
    """Clean text content (think-blocks stripped)."""
    thinking: str = ""
    """Extracted thinking/reasoning content from this chunk."""
    think_opened: bool = False
    """True if a think block started in this chunk (for UI)."""
    think_closed: bool = False
    """True if a think block ended in this chunk (for UI)."""

class StreamingThinkProcessor:
    """Stateful streaming processor for <think> tag extraction."""

    # Minimum chars to form a tag (used in early detection)
    _TAG_OPEN_MIN = len("<think")
    _TAG_CLOSE_MIN = len("</")

    def __init__(self) -> None:
        self._state = ThinkState.IDLE
        self._buf = ""                     # Tag character buffer
        self._full_thinking: List[str] = []  # All thinking content
        self._current_thinking: List[str] = []  # Current block content
        self._think_blocks: int = 0
        self._chunks_since_flush: int = 0

    # ---- Public API ----

    def feed(self, chunk_text: str) -> ThinkProcessorResult:
        """Process a text chunk through the state machine."""
        result = ThinkProcessorResult()
        self._chunks_since_flush += 1

        remaining = chunk_text
        while remaining:
            consumed, emitted_text, emitted_think = self._step(remaining)
            remaining = remaining[consumed:]

            if emitted_text:
                result.content += emitted_text
            if emitted_think:
                result.thinking += emitted_think

        return result

    def feed_chunk(self, chunk_text: str) -> str:
        """Convenience: feed a chunk and get only the cleaned text."""
        result = self.feed(chunk_text)
        return result.content

    def get_thinking(self) -> str:
        """Get all accumulated thinking content so far."""
        return "".join(self._full_thinking + self._current_thinking)

    def flush(self) -> str:
        """Flush any pending thinking content."""
        pending = self._current_thinking
        if pending:
            text = "".join(pending)
            self._full_thinking.append(text)
            self._current_thinking.clear()
            return text
        return ""

    def reset(self) -> None:
        """Reset processor state for a new stream."""
        self._state = ThinkState.IDLE
        self._buf = ""
        self._full_thinking.clear()
        self._current_thinking.clear()
        self._think_blocks = 0
        self._chunks_since_flush = 0

    @property
    def is_thinking(self) -> bool:
        """True if currently inside a think block."""
        return self._state in (ThinkState.THINKING, ThinkState.TAG_OPEN)

    @property
    def think_block_count(self) -> int:
        """Number of completed think blocks processed."""
        return self._think_blocks

    # ---- State machine core ----

    def _step(self, text: str) -> tuple:
        """Single step of the state machine."""
        if self._state == ThinkState.IDLE:
            return self._step_idle(text)
        elif self._state == ThinkState.TAG_OPEN:
            return self._step_tag_open(text)
        elif self._state == ThinkState.THINKING:
            return self._step_thinking(text)
        elif self._state == ThinkState.TAG_CLOSE:
            return self._step_tag_close(text)
        return (0, "", "")

    def _step_idle(self, text: str) -> tuple:
        """IDLE state: scanning for '<think', '<thinking', '<reasoning' sequences."""
        # Save original length for cross-chunk adjustment
        original_len = len(text)

        # Merge any buffered partial tag from previous cross-chunk boundary
        buf_len = len(self._buf)
        if self._buf:
            text = self._buf + text
            self._buf = ""

        idx = self._find_tag_open(text)
        if idx < 0:
            # Check for potential cross-chunk tag start (bare '<' near end)
            # This handles cases like '<th' at chunk boundary
            last_lt = text.rfind("<")
            if last_lt >= 0 and last_lt >= len(text) - 5:
                # '<' is within the last 5 chars — could be start of <think
                consumed_merged = len(text)
                # Buffer the potential tag start for next chunk
                prefix = text[:last_lt]
                self._buf = text[last_lt:]
                # Return consumed relative to original text
                consumed_original = max(0, consumed_merged - buf_len)
                return (consumed_original, prefix, "")
            consumed_merged = len(text)
            consumed_original = max(0, consumed_merged - buf_len)
            return (consumed_original, text, "")

        # Tag prefix found at idx: emit text before the tag as content
        prefix = text[:idx]
        after_open = text[idx:]  # starts with <think, <thinking, etc.

        # Find the closing '>' to complete the opening tag
        close_bracket = after_open.find(">")
        if close_bracket >= 0:
            # Complete opening tag found, e.g. '<think>' or '<thinking>'
            open_tag_len = close_bracket + 1  # includes the '>'
            self._state = ThinkState.THINKING
            self._current_thinking = []
            self._think_blocks += 1
            # Consume only the opening tag; THINKING state handles the rest
            consumed_merged = idx + open_tag_len
            # Adjust for buffer: consumed is relative to original text
            consumed_original = max(0, consumed_merged - buf_len)
            return (consumed_original, prefix, "")

        # No '>' found — partial tag at chunk boundary
        consumed_merged = len(text)
        self._buf = after_open
        consumed_original = max(0, consumed_merged - buf_len)
        return (consumed_original, prefix, "")

    def _step_thinking(self, text: str) -> tuple:
        """THINKING state: eating everything until closing tag ('</think>', etc.)."""
        idx, tag_len = self._find_tag_close(text)
        if idx < 0:
            # No closing tag → buffer as thinking
            nest_idx = self._find_tag_open_simple(text)
            if nest_idx >= 0:
                # Nested open tag — treat as regular thinking text
                self._current_thinking.append(text[:nest_idx + 6])
                remaining = text[nest_idx + 6:]
                return self._step_thinking(remaining)
            self._current_thinking.append(text)
            return (len(text), "", "")

        # Closing tag found at idx, with tag_len chars
        thinking_prefix = text[:idx]
        if thinking_prefix:
            self._current_thinking.append(thinking_prefix)

        # Complete the thinking block
        think_text = "".join(self._current_thinking)
        self._full_thinking.append(think_text)
        self._current_thinking.clear()

        # Skip past the closing tag
        consumed = idx + tag_len
        self._state = ThinkState.IDLE

        # Return remaining text after tag
        remaining = text[consumed:]
        if remaining:
            # Continue processing remaining text in IDLE state
            sub_result = ThinkProcessorResult()
            rem_text = remaining
            while rem_text:
                c, et, _ = self._step_idle(rem_text)
                if et:
                    sub_result.content += et
                rem_text = rem_text[c:]
            return (len(text), sub_result.content, think_text)

        return (len(text), "", think_text)

    def _step_tag_open(self, text: str) -> tuple:
        """TAG_OPEN: accumulating chars after '<think'."""
        # This state should be rarely reached since most tags
        # complete within a single chunk. Handles edge case.
        buf_len = len(self._buf)
        needed = self._TAG_OPEN_MIN - buf_len
        if len(text) >= needed:
            self._buf = ""
            # Ingest needed chars
            rest = text[needed:]
            if rest.startswith(">"):
                self._state = ThinkState.THINKING
                self._current_thinking = []
                self._think_blocks += 1
                return (needed + 1, "", "")
            else:
                # False alarm — emit all buffered chars
                full = self._buf + text[:needed] + rest
                self._buf = ""
                self._state = ThinkState.IDLE
                return (len(text), full, "")
        else:
            # Still not enough chars
            self._buf += text
            return (len(text), "", "")

    def _step_tag_close(self, text: str) -> tuple:
        """TAG_CLOSE: accumulating after '</think'."""
        buf_len = len(self._buf)
        needed = 7 - buf_len  # think = 5, > = 1, total 7 after </
        if len(text) >= needed:
            self._buf = ""
            candidate = text[:needed]
            if candidate == "think>" or candidate == "think>":
                self._state = ThinkState.IDLE
                return (needed, "", "")
            return (0, "", "")
        self._buf += text
        return (len(text), "", "")

    # ---- Helpers ----

    def _find_tag_open(self, text: str) -> int:
        """Find '<think' or '<thinking' or '<reasoning' position. Returns -1 if not found."""
        for start_tag in ("<think", "<thinking", "<reasoning"):
            idx = text.find(start_tag)
            if idx >= 0:
                return idx
        return -1

    def _find_tag_close(self, text: str) -> tuple:
        """Find closing tag position and its length."""
        for end_tag in ("</think>", "</thinking>", "</reasoning>"):
            idx = text.find(end_tag)
            if idx >= 0:
                return (idx, len(end_tag))
        return (-1, 0)

    def _find_tag_open_simple(self, text: str) -> int:
        """Quick find for '<think' within thinking content."""
        return text.find("<think")

class SilenceDetector:
    """Detects reasoning-only LLM responses."""

    MAX_SILENCE_RETRIES = 2
    """Maximum number of silence retries before raising an error."""

    def __init__(self, max_retries: int = MAX_SILENCE_RETRIES) -> None:
        self._silence_count = 0
        self._max_retries = max_retries

    def check(
        self,
        content: str,
        thinking: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Check if the response is a silence (reasoning-only)."""
        is_silent = (
            bool(thinking) and
            not content.strip() and
            not tool_calls
        )
        if is_silent:
            self._silence_count += 1
        else:
            self._silence_count = 0  # Reset on non-silent response
        return is_silent

    @property
    def is_exhausted(self) -> bool:
        """True if max silence retries exceeded."""
        return self._silence_count > self._max_retries

    def get_prefill(self) -> str:
        """Get the prefill text to inject for silence recovery."""
        return "\nBased on my reasoning, here is my response:"

    def reset(self) -> None:
        """Reset silence counter."""
        self._silence_count = 0

class ReasoningContentManager:
    """Manages reasoning_content across the stream lifecycle."""

    def __init__(self) -> None:
        self._stream_reasoning: List[str] = []
        self._stream_text: List[str] = []
        self._has_reasoning_field = False

    def feed_reasoning_delta(self, text: str) -> None:
        """Accumulate a reasoning_content delta from stream."""
        if text:
            self._stream_reasoning.append(text)
            self._has_reasoning_field = True

    def feed_text_delta(self, text: str) -> None:
        """Accumulate a text delta from stream."""
        if text:
            self._stream_text.append(text)

    @property
    def reasoning(self) -> str:
        """Get accumulated reasoning_content."""
        return "".join(self._stream_reasoning)

    @property
    def text(self) -> str:
        """Get accumulated text content."""
        return "".join(self._stream_text)

    @property
    def has_reasoning(self) -> bool:
        """True if any reasoning_content was received from the API."""
        return self._has_reasoning_field

    def combine_with_think(self, think_text: str) -> str:
        """Combine reasoning_content with <think> extracted text."""
        if self._stream_reasoning:
            return self.reasoning
        return think_text

    def get_padding_messages(
        self,
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Ensure all assistant messages have reasoning_content field."""
        # Check if any assistant msg has reasoning_content
        has_any = False
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("reasoning_content"):
                has_any = True
                break

        if not has_any:
            return messages

        # Add padding to assistant messages without reasoning_content
        result = []
        for msg in messages:
            if msg.get("role") == "assistant" and "reasoning_content" not in msg:
                msg = {**msg, "reasoning_content": ""}
            result.append(msg)
        return result

    def reset(self) -> None:
        """Reset accumulators for a new stream."""
        self._stream_reasoning.clear()
        self._stream_text.clear()
        self._has_reasoning_field = False
