"""Context window detection.

The window is a vendor fact, so it is read from data rather than guessed in
code. Detection chain:

1. ``context_windows`` in llm.yaml — what the user set for their endpoint
   (a relay may expose a different window than the vendor's own API).
2. The model catalog (``boar/config/models.yaml`` plus the user's override
   file). This tier works with no provider registered, so a headless run still
   sizes its prompt correctly.
3. ``max_context`` / per-model info declared inline on the provider config.
4. :data:`DEFAULT_CONTEXT_WINDOW`.

An output reservation is subtracted from whichever total wins, because the
answer has to fit in the same window as the prompt.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_WINDOW = 128000
DEFAULT_MAX_OUTPUT = 4096


def detect_context_window(
    model: str,
    provider: str = "",
    provider_config: Optional[dict] = None,
) -> int:
    """Detect the effective context window for a model."""
    total = _get_max_context(model, provider, provider_config)
    return max(total - DEFAULT_MAX_OUTPUT, 4096)


def _get_max_context(
    model: str,
    provider: str = "",
    provider_config: Optional[dict] = None,
) -> int:
    """Get the raw context window, without the output reservation."""

    # Tier 1: user-configured context windows (llm.yaml ``context_windows``),
    # resolved by model name through the LLM service. This is the value set in
    # the WebUI provider editor, so it wins over the catalog.
    try:
        from boarai.core.llm import get_llm_service

        ctx = get_llm_service().get_model_context_window(model)
        if ctx:
            logger.debug(f"Context window from llm.yaml override: {ctx}")
            return ctx
    except Exception:
        pass

    # Tier 2: the model catalog. Consulted directly rather than through a
    # provider so it still answers when no provider is registered.
    try:
        from boarai.core.llm.catalog import get_context_window

        ctx = get_context_window(model)
        if ctx:
            logger.debug(f"Context window from model catalog: {ctx}")
            return ctx
    except Exception as e:  # noqa: BLE001 - detection must never abort a run
        logger.debug(f"Model catalog lookup failed for {model}: {e}")

    # Tier 3: provider config explicitly declares max_context
    if provider_config and provider_config.get("max_context"):
        ctx = provider_config["max_context"]
        logger.debug(f"Context window from provider config: {ctx}")
        return ctx

    # Tier 4: provider config carries per-model context info
    if provider_config:
        supported = provider_config.get("supported_models", [])
        if isinstance(supported, dict):
            # {"model_name": {"context_window": 128000}}
            if model in supported and isinstance(supported[model], dict):
                ctx = supported[model].get("context_window", 0)
                if ctx:
                    return ctx

    # Tier 5: default
    logger.debug(
        f"No context window info for {provider}/{model}, "
        f"using default {DEFAULT_CONTEXT_WINDOW}"
    )
    return DEFAULT_CONTEXT_WINDOW


def get_usage_info(
    model: str,
    estimated_tokens: int,
    provider: str = "",
    provider_config: Optional[dict] = None,
) -> dict:
    """Get context usage information for monitoring."""
    effective = detect_context_window(model, provider, provider_config)
    ratio = estimated_tokens / max(effective, 1)

    if ratio >= 0.90:
        state = "critical"
    elif ratio >= 0.60:
        state = "warning"
    else:
        state = "ok"

    return {
        "effective_window": effective,
        "estimated_tokens": estimated_tokens,
        "usage_ratio": round(ratio, 3),
        "state": state,
        "output_reserved": DEFAULT_MAX_OUTPUT,
    }
