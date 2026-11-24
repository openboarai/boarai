"""
Thinking configuration for BoarAi.

Session thinking_mode is a UI/session label. The wire layer
(``core.llm.thinking``) maps it to each vendor's native request fields:

  auto / none / empty → send nothing; the model default applies
  off                 → explicit disable (or the model's lowest level when
                        thinking cannot be disabled)
  light               → low
  deep                → medium
  ultra               → xhigh (clamped to max/high per model)

Providers:
  - OpenAI-compatible: reasoning_effort and/or a vendor toggle
  - Anthropic: adaptive thinking + output_config.effort, or budget_tokens
  - GLM/Z.AI: thinking {type: enabled|disabled} + reasoning_effort

Thinking *text* is reasoning_content on assistant messages, echoed in history.
Not prompt injection ("please think deeply").
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

class ThinkingMode(str, Enum):
    """Session/UI thinking mode."""
    AUTO = "none"       # default: no thinking field
    NONE = "none"       # legacy alias for AUTO
    OFF = "off"         # explicit disable
    LIGHT = "light"     # → low
    DEEP = "deep"       # → medium
    ULTRA = "ultra"     # → xhigh (vendor max/high)

# Reasoning model prefixes that support native reasoning_effort
REASONING_MODEL_PREFIXES = frozenset({
    "o1", "o3",                 # OpenAI reasoning models
    "o1-mini", "o3-mini",
    "deepseek-r1",              # DeepSeek R1
    "deepseek-reasoner",
    "kimi-k2", "kimi-reasoning",  # Kimi reasoning
    "gemini-2.5-pro",           # Gemini deep thinking
    "grok-3-reasoning",         # xAI Grok
    "claude-3-opus",            # Anthropic extended thinking
    "claude-3.5-opus",
    "claude-4",
})

# Models known to emit reasoning_content in stream delta
REASONING_CONTENT_MODELS = REASONING_MODEL_PREFIXES | {
    "deepseek-chat",            # DeepSeek V3
    "moonshot-v1",              # Moonshot AI
    "qwen-turbo", "qwen-plus",  # Qwen
    "qwen-max",
}

# UI / session thinking_mode → requested API level.
# Session stores none|off|light|deep|ultra; the wire layer clamps the level to
# the model's supported set (e.g. GLM-5.3 accepts low|high|max, so deep clamps
# to high and ultra maps to max).
REASONING_EFFORT_MAP: Dict[ThinkingMode, str] = {
    ThinkingMode.LIGHT: "low",
    ThinkingMode.DEEP: "medium",
    ThinkingMode.ULTRA: "xhigh",
}

# API effort strings. off/none = thinking disabled (still written to body)
VALID_REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "off", "none"})
REASONING_EFFORT_OFF = "none"

# Anthropic / Bedrock only accept low|medium|high (no xhigh)
ANTHROPIC_EFFORTS = frozenset({"low", "medium", "high"})

# Legacy token budgets (unused by main path; kept for optional callers)
THINKING_TOKEN_BUDGET: Dict[ThinkingMode, Optional[int]] = {
    ThinkingMode.AUTO: None,
    ThinkingMode.OFF: None,
    ThinkingMode.LIGHT: 2048,
    ThinkingMode.DEEP: 8192,
    ThinkingMode.ULTRA: 32768,
}

# Deprecated: thinking mode is API body reasoning_effort, not prompt text.
THINKING_CONSTRAINT_PROMPTS: Dict[ThinkingMode, str] = {
    ThinkingMode.AUTO: "",
    ThinkingMode.OFF: "",
    ThinkingMode.LIGHT: "",
    ThinkingMode.DEEP: "",
    ThinkingMode.ULTRA: "",
}

@dataclass
class ThinkingConfig:
    """Thinking configuration for a conversation turn."""
    mode: ThinkingMode = ThinkingMode.AUTO
    """Selected thinking mode."""

    show_reasoning: bool = True
    """Whether to display reasoning/thinking blocks to the user in UI."""

    max_tokens: Optional[int] = None
    """Override LLM max_tokens. If None, determined by THINKING_TOKEN_BUDGET."""

    budget_tokens: Optional[int] = None
    """Anthropic extended thinking budget_tokens. Used when model supports it."""

    reasoning_effort: Optional[str] = None
    """Requested wire level (low|medium|high|xhigh|none) or None for auto."""

    def __post_init__(self) -> None:
        """Normalize a raw string mode to the enum."""
        if not isinstance(self.mode, ThinkingMode):
            try:
                self.mode = ThinkingMode(str(self.mode).strip().lower())
            except ValueError:
                self.mode = ThinkingMode.AUTO

    def apply_to_turn(self, model: str = "", max_tokens: Optional[int] = None) -> Dict[str, Any]:
        """Return LLM kwargs for this thinking mode."""
        if self.mode == ThinkingMode.AUTO:
            return {}
        if self.mode == ThinkingMode.OFF:
            return {"reasoning_effort": REASONING_EFFORT_OFF}
        effort = normalize_reasoning_effort(
            self.reasoning_effort or REASONING_EFFORT_MAP.get(self.mode)
        )
        if not effort:
            return {}
        return {"reasoning_effort": effort}

    def get_constraint_prompt(self, model: str = "") -> str:
        """Deprecated: thinking mode is not prompt text. Always empty."""
        return ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        mode = (
            self.mode.value
            if isinstance(self.mode, ThinkingMode)
            else normalize_thinking_mode(self.mode)
        )
        return {
            "mode": mode,
            "show_reasoning": self.show_reasoning,
            "max_tokens": self.max_tokens,
            "budget_tokens": self.budget_tokens,
            "reasoning_effort": self.reasoning_effort,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "ThinkingConfig":
        """Deserialize from dict."""
        if not data:
            return cls()
        mode_str = data.get("mode", "none")
        try:
            mode = ThinkingMode(mode_str)
        except ValueError:
            mode = ThinkingMode.NONE
        return cls(
            mode=mode,
            show_reasoning=data.get("show_reasoning", True),
            max_tokens=data.get("max_tokens"),
            budget_tokens=data.get("budget_tokens"),
            reasoning_effort=data.get("reasoning_effort"),
        )

def normalize_thinking_mode(raw: Optional[str]) -> str:
    """Normalize a session/UI thinking mode to its persisted value."""
    if raw is None:
        return ThinkingMode.AUTO.value
    s = raw.value if isinstance(raw, ThinkingMode) else str(raw)
    s = s.strip().lower()
    if s in ("", "auto", "default"):
        return ThinkingMode.AUTO.value
    if s in ("false", "0"):
        return ThinkingMode.OFF.value
    try:
        return ThinkingMode(s).value
    except ValueError:
        return ThinkingMode.AUTO.value


def load_thinking_settings() -> tuple[ThinkingMode, bool]:
    """Read the global thinking defaults from ``config.yaml``."""
    default_mode = ThinkingMode.AUTO
    enabled = True
    try:
        from boarai.core.config import get_config_parser

        parser = get_config_parser()
        config = getattr(parser, "config", None) if parser else None
        thinking = getattr(config, "thinking", None) if config else None
        if thinking is not None:
            raw_value = getattr(thinking, "default_mode", "")
            raw = (
                raw_value.value
                if isinstance(raw_value, ThinkingMode)
                else str(raw_value or "")
            )
            raw = raw.strip().lower()
            if raw:
                try:
                    default_mode = ThinkingMode(raw)
                except ValueError:
                    pass
            raw_enabled = getattr(thinking, "enabled", True)
            if isinstance(raw_enabled, str):
                enabled = raw_enabled.strip().lower() not in (
                    "",
                    "0",
                    "false",
                    "off",
                    "no",
                )
            else:
                enabled = bool(raw_enabled)
    except Exception as exc:  # noqa: BLE001 - config absence must not break a run
        logger.debug("Thinking defaults fallback: %s", exc)
    return default_mode, enabled


def normalize_reasoning_effort(raw: Optional[str]) -> Optional[str]:
    """Normalize session/API effort to a wire level."""
    if raw is None:
        return None
    s = raw.value if isinstance(raw, ThinkingMode) else str(raw)
    s = s.strip().lower()
    if not s or s in ("auto", "default"):
        return None
    if s in ("none", "off", "false", "0"):
        return REASONING_EFFORT_OFF
    ui_map = {
        "light": "low",
        "deep": "medium",
        "ultra": "xhigh",
    }
    if s in ui_map:
        return ui_map[s]
    if s in ("low", "medium", "high", "xhigh"):
        return s
    logger.debug("Unknown reasoning_effort %r; treating as auto", raw)
    return None

def is_reasoning_model(model: str) -> bool:
    """Heuristic: model name likely supports reasoning_effort (optional hint)."""
    model_lower = (model or "").lower().strip()
    for prefix in REASONING_MODEL_PREFIXES:
        if model_lower.startswith(prefix) or f"/{prefix}" in model_lower:
            return True
    return False

def emits_reasoning_content(model: str) -> bool:
    """Heuristic: model may emit reasoning_content in stream deltas."""
    model_lower = (model or "").lower().strip()
    for prefix in REASONING_CONTENT_MODELS:
        if model_lower.startswith(prefix) or f"/{prefix}" in model_lower:
            return True
    return False

def resolve_thinking_config(
    mode: ThinkingMode,
    model: str = "",
) -> ThinkingConfig:
    """Build ThinkingConfig for a session mode."""
    config = ThinkingConfig(mode=mode)
    if config.mode == ThinkingMode.AUTO:
        config.reasoning_effort = None
        return config
    if config.mode == ThinkingMode.OFF:
        config.reasoning_effort = REASONING_EFFORT_OFF
        return config
    config.reasoning_effort = REASONING_EFFORT_MAP.get(config.mode)
    return config
