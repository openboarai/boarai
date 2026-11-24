"""Provider thinking wire layer for BoarAi.

The session layer normalizes a UI thinking mode (``auto`` / ``off`` / ``light``
/ ``deep`` / ``ultra``) into one requested thinking level. This package maps
that level to each vendor's native request-body shape so the mode actually
controls thinking across providers (GLM, DeepSeek, OpenAI, Anthropic, Qwen,
OpenRouter, ...).

The per-model facts (format, supported levels, level mapping, whether thinking
can be disabled) are data in ``boar/config/models.yaml``, read through
:mod:`core.llm.catalog`. This package is the interpreter: it turns a resolved
spec into request-body fields.

Resolution precedence: per-provider ``model_info.<model>.thinking``, then
per-provider ``options.thinking``, then the model catalog, then the endpoint
default. See :mod:`core.llm.thinking.wire` for the format table.
"""

from .formats import KNOWN_FORMATS, canonical_format
from .wire import (
    LEVELS,
    MODE_TO_LEVEL,
    ThinkingDecision,
    ThinkingSpec,
    build_spec,
    catalog_spec,
    decide_thinking,
    requested_level,
    resolve_thinking_spec,
)

__all__ = [
    "KNOWN_FORMATS",
    "LEVELS",
    "MODE_TO_LEVEL",
    "ThinkingDecision",
    "ThinkingSpec",
    "build_spec",
    "canonical_format",
    "catalog_spec",
    "decide_thinking",
    "requested_level",
    "resolve_thinking_spec",
]
