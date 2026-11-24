"""Token counting using tiktoken for accurate pre-call estimation."""

import logging
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

# cl100k_base is the encoding used by GPT-4, GPT-3.5-turbo, and most modern models.
# It serves as a close approximation for non-OpenAI models (DeepSeek, Anthropic, etc.).
_ENCODING_NAME = "cl100k_base"

# Fixed token estimate per image block (bounded vision cost; see count_tokens).
IMAGE_TOKEN_ESTIMATE = 1024


@lru_cache(maxsize=1)
def _get_encoding():
    """Lazily load and cache the tiktoken encoding."""
    try:
        import tiktoken
        return tiktoken.get_encoding(_ENCODING_NAME)
    except ImportError:
        logger.warning("tiktoken not installed, falling back to char-based estimate")
        return None


def count_tokens(text: Any) -> int:
    """Count tokens in text using tiktoken (cl100k_base encoding)."""
    if not text:
        return 0
    if isinstance(text, list):
        total = 0
        for block in text:
            if isinstance(block, dict):
                block_text = block.get("text") or ""
                if block.get("type") == "image_url":
                    # Fixed vision cost approximation (OpenAI high-detail
                    # images cost ~765-1105 tokens; Anthropic similar).
                    total += IMAGE_TOKEN_ESTIMATE
                else:
                    total += count_tokens(block_text)
            else:
                total += count_tokens(str(block))
        return total
    if not isinstance(text, str):
        text = str(text)
    enc = _get_encoding()
    if enc is not None:
        return len(enc.encode(text))
    # Fallback: rough estimate (~4 chars per token for English)
    return len(text) // 4