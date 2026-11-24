"""
Assistant response compressor — generates a compact "context version"
of the assistant's response for use in the next prompt.

The user sees the full response. But when the response is included in
the next prompt's Recent Exchange, a compressed version is used instead.

Compression rules (preserve editing capability):
    - Code blocks (```...```): KEPT IN FULL
    - Tables, lists, URLs, file paths: KEPT IN FULL
    - Command outputs: Keep head + tail (5 lines each)
    - Pure prose explanations: First line + [N lines truncated]

This is the single biggest cache optimization after Task State:
it reduces the Recent Exchange from ~500 tokens to ~200 tokens
while preserving the code the user might want to modify.
"""

import logging
import re
from typing import List

logger = logging.getLogger(__name__)

# URL pattern: matches http/https/ftp URLs
_URL_RE = re.compile(r'https?://\S+', re.IGNORECASE)


class AssistantCompressor:
    """Generate compressed version of assistant response."""

    def compress(self, content: str) -> str:
        """Compress assistant response for context reuse."""
        if not content or len(content) < 300:
            # Short responses don't need compression
            return content

        lines = content.split("\n")
        result: List[str] = []
        prose_buffer: List[str] = []
        in_code_block = False

        for line in lines:
            stripped = line.strip()

            # Code block boundary
            if stripped.startswith("```"):
                # Flush accumulated prose
                self._flush_prose(result, prose_buffer)
                prose_buffer = []
                in_code_block = not in_code_block
                result.append(line)
                continue

            if in_code_block:
                # Inside code block: keep everything
                result.append(line)
            else:
                # Outside code block: it's prose
                # Check if it's a structured line (URL, file path, list item, table)
                if self._is_structured_line(stripped):
                    # Flush prose, keep structured line
                    self._flush_prose(result, prose_buffer)
                    prose_buffer = []
                    result.append(line)
                else:
                    prose_buffer.append(line)

        # Flush remaining prose
        self._flush_prose(result, prose_buffer)

        compressed = "\n".join(result)

        # Log compression stats
        original_tokens = len(content) // 4
        compressed_tokens = len(compressed) // 4
        if original_tokens > 0:
            ratio = round(compressed_tokens / original_tokens * 100, 1)
            logger.debug(
                f"Assistant compressed: {original_tokens} -> {compressed_tokens} "
                f"tokens ({ratio}% of original)"
            )

        return compressed

    @staticmethod
    def _is_structured_line(line: str) -> bool:
        """Check if a line is structured (not prose)."""
        if not line:
            return True  # Empty line (formatting)
        if line.startswith(("#", "-", "*", "1.", "2.", "3.", "4.", "5.")):
            return True  # List or heading
        if "|" in line and line.count("|") >= 2:
            return True  # Table row
        if _URL_RE.search(line):
            return True  # URL
        if "/" in line and "." in line and len(line) < 200:
            return True  # File path
        if line.endswith(":") and len(line) < 100:
            return True  # Key-value header
        return False

    @staticmethod
    def _flush_prose(result: List[str], prose_buffer: List[str]) -> None:
        """Flush accumulated prose lines to result."""
        if not prose_buffer:
            return

        # Filter out empty lines from count
        non_empty = [l for l in prose_buffer if l.strip()]
        if not non_empty:
            # All empty lines, keep them (formatting)
            result.extend(prose_buffer)
            return

        # Keep first non-empty line (skip leading blanks)
        first_real = next((l for l in prose_buffer if l.strip()), prose_buffer[0])
        result.append(first_real)

        if len(non_empty) > 1:
            result.append(f"[...{len(non_empty) - 1} lines of explanation truncated]")


# Global instance
_assistant_compressor: AssistantCompressor = None  # type: ignore


def get_assistant_compressor() -> AssistantCompressor:
    """Get the global assistant compressor instance."""
    global _assistant_compressor
    if _assistant_compressor is None:
        _assistant_compressor = AssistantCompressor()
    return _assistant_compressor
