"""Wire format names shared by the catalog data and the thinking interpreter.

One home for the vocabulary so a catalog file cannot name a shape the request
builder does not implement, and the builder cannot grow a shape the catalog
validator does not know.
"""

from __future__ import annotations

from typing import Tuple

#: Request-body shapes :mod:`core.llm.thinking.wire` can emit.
KNOWN_FORMATS: Tuple[str, ...] = (
    "openai",
    "openrouter",
    "deepseek",
    "moonshot",
    "zai",
    "qwen",
    "qwen-template",
    "together",
    "anthropic",
    "string",
    "none",
)

#: Alternative spellings accepted in a catalog or user override.
FORMAT_ALIASES = {
    "": "",
    "auto": "",
    "glm": "zai",
    "z.ai": "zai",
    "zhipu": "zai",
    "kimi": "moonshot",
    "qwen_chat_template": "qwen-template",
    "string-thinking": "string",
}


def canonical_format(name: object) -> str:
    """Normalize a format spelling."""
    raw = str(name or "").strip().lower()
    return FORMAT_ALIASES.get(raw, raw)


__all__ = ["FORMAT_ALIASES", "KNOWN_FORMATS", "canonical_format"]
