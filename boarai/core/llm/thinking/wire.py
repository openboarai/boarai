"""Provider-native thinking parameters (the thinking wire layer).

The session layer normalizes a UI thinking mode (``auto`` / ``off`` / ``light``
/ ``deep`` / ``ultra``) into one standard intermediate value, a requested
"thinking level". Different vendors expose that same semantic through very
different request-body shapes:

  openai        -> body["reasoning_effort"] = "low|medium|high"
  openrouter    -> body["reasoning"] = {"effort": "..."}
  deepseek      -> body["thinking"] = {"type": "enabled|disabled"}
                   + body["reasoning_effort"] (both, on every request)
  moonshot      -> body["reasoning_effort"] alone when a level is requested,
                   body["thinking"] alone for off (the endpoint 400s on both)
  zai (GLM)     -> body["thinking"] = {"type": "enabled|disabled"}
                   (+ effort for GLM-5.2+; 5.2 takes high|max, 5.3 low..max)
  qwen          -> body["enable_thinking"] = true|false
                   (+ effort when the provider accepts it)
  qwen-template -> body["chat_template_kwargs"] = {"enable_thinking": ...}
  anthropic     -> adaptive: thinking {"type": "adaptive"} + output_config.effort
                   legacy:   thinking {"type": "enabled", "budget_tokens": N}
  together      -> body["reasoning"] = {"enabled": bool} (+ effort)
  string        -> body["thinking"] = "<vendor string>"

Resolution precedence (highest first):

  1. ``model_info.<model>.thinking``   — per-provider override in llm.yaml
  2. ``options.thinking``              — per-provider override
  3. the model catalog                 — boar/config/models.yaml
  4. the endpoint default              — OpenRouter's reasoning shape, else
                                         nothing

An override is either a format name (``"zai"``) or a dict
(``{"format": "zai", "levels": ["low", "high", "max"], ...}``), and merges onto
the catalog record. A model the catalog does not cover gets **no** thinking
field: a guessed ``reasoning_effort`` is a 400 on a non-reasoning model, and
losing a cosmetic setting is cheaper than losing the request.

``off`` is never expressed by a value the endpoint may reject. A wire with a
real disable sends it; a wire where thinking is opt-in (Anthropic budget mode)
sends nothing; a model that reasons no matter what (o-series, Grok, Gemini,
Claude Fable) is clamped up to its lowest level; and a wire with no off spelling
at all omits the field. Every downgrade is reported in the decision note, which
``GET /api/llm/thinking`` returns to the UI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Optional, Tuple

from ..catalog import get_model_record

logger = logging.getLogger(__name__)

# Ascending order. Used for clamping and for picking the lowest level.
LEVELS: Tuple[str, ...] = (
    "off",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)

_LEVEL_INDEX: Dict[str, int] = {level: index for index, level in enumerate(LEVELS)}

# Wire effort value -> requested level. Only an explicit effort reaches this
# layer: AUTO arrives as ``None`` (no field at all), OFF arrives as ``"none"``.
MODE_TO_LEVEL: Dict[str, Optional[str]] = {
    "auto": None,
    "default": None,
    "": None,
    "none": "off",
    "off": "off",
    "light": "low",
    "deep": "medium",
    "ultra": "xhigh",
    # Direct level spellings (accepted from session overrides and API callers).
    "minimal": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "max",
}

_DEFAULT_LEVELS: Tuple[str, ...] = ("low", "medium", "high")

# Anthropic budget-mode defaults. The provider keeps max_tokens above the
# budget so the answer still has room.
_DEFAULT_BUDGETS: Dict[str, int] = {
    "minimal": 1024,
    "low": 2048,
    "medium": 8192,
    "high": 16384,
    "xhigh": 32768,
    "max": 32768,
}


@dataclass(frozen=True)
class ThinkingSpec:
    """How one model exposes thinking control on the wire."""

    format: str = "openai"
    """Wire shape: openai | openrouter | deepseek | moonshot | zai | qwen |
    qwen-template | together | anthropic | string | none."""

    levels: Tuple[str, ...] = _DEFAULT_LEVELS
    """Normalized levels this model supports (ascending order)."""

    can_disable: bool = True
    """True when ``off`` has a real wire representation for this model."""

    supports_effort: bool = True
    """True when the model accepts a graded effort value in addition to a
    toggle. False sends only the toggle (or nothing)."""

    level_map: Dict[str, str] = field(default_factory=dict)
    """requested level -> vendor value. Missing keys pass the level through."""

    effort_field: str = "reasoning_effort"
    """Body field carrying the effort value."""

    budget_map: Dict[str, int] = field(default_factory=dict)
    """level -> thinking token budget (anthropic budget mode)."""

    adaptive: bool = False
    """True for Anthropic adaptive-thinking models."""

    disable_by_omission: bool = False
    """True when sending no field at all IS the disable (Anthropic budget mode:
    extended thinking is opt-in there, so an omitted ``thinking`` means off)."""

    toggle_field: str = "thinking"
    """Body field carrying the enable/disable object."""

    enable_value: Any = None
    """Value for the toggle when thinking is on (defaults per format)."""

    disable_value: Any = None
    """Value for the toggle when thinking is off (defaults per format)."""

    enable_field: str = "enable_thinking"
    """Boolean field used by qwen-style formats."""

    extra_fields: Dict[str, Any] = field(default_factory=dict)
    """Additional top-level fields merged into the request body last."""

    def supports(self, level: str) -> bool:
        """Return True when this model supports ``level``."""
        return not self.levels or level in self.levels

    def lowest(self) -> Optional[str]:
        """Lowest supported level, or None when the model has no levels."""
        if not self.levels:
            return None
        return min(self.levels, key=lambda level: _LEVEL_INDEX.get(level, len(LEVELS)))

    def clamp(self, level: str) -> str:
        """Clamp a requested level to the nearest supported one."""
        if self.supports(level):
            return level
        if not self.levels:
            return level
        requested = _LEVEL_INDEX.get(level, _LEVEL_INDEX["medium"])
        for candidate in LEVELS[requested::1]:
            if candidate in self.levels:
                return candidate
        for candidate in reversed(LEVELS[:requested]):
            if candidate in self.levels:
                return candidate
        return self.lowest() or level

    def budget_for(self, level: str) -> int:
        """Thinking token budget for ``level``."""
        return int(
            self.budget_map.get(
                level,
                _DEFAULT_BUDGETS.get(level, _DEFAULT_BUDGETS["medium"]),
            )
        )


@dataclass(frozen=True)
class ThinkingDecision:
    """The fields a provider should merge into one request body."""

    fields: Dict[str, Any] = field(default_factory=dict)
    level: Optional[str] = None
    """Effective normalized level, or None when no thinking field is sent."""

    note: str = ""
    """Human-readable downgrade/override note for logs."""

    format: str = "none"
    """Wire format that produced the fields."""


def _merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge ``extra`` into a copy of ``base``."""
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def requested_level(effort: Optional[str]) -> Optional[str]:
    """Normalize a session/API effort value to a requested level."""
    if effort is None:
        return None
    raw = getattr(effort, "value", effort)
    raw = str(raw).strip().lower()
    if raw in MODE_TO_LEVEL:
        return MODE_TO_LEVEL[raw]
    logger.debug("Unknown thinking effort %r; treating as auto", effort)
    return None


def _default_spec(format_name: str) -> Optional[ThinkingSpec]:
    """Default spec for a wire format name."""
    name = (format_name or "").strip().lower()
    if name in ("", "auto"):
        return None
    if name == "none":
        return ThinkingSpec(format="none", levels=(), can_disable=False, supports_effort=False)
    if name == "openai":
        return ThinkingSpec(format="openai")
    if name == "openrouter":
        return ThinkingSpec(format="openrouter")
    if name == "deepseek":
        return ThinkingSpec(format="deepseek")
    if name in ("moonshot", "kimi"):
        return ThinkingSpec(
            format="moonshot",
            levels=("low", "high", "max"),
            level_map={"medium": "high", "xhigh": "max"},
            disable_value={"type": "disabled"},
        )
    if name in ("zai", "glm"):
        return ThinkingSpec(
            format="zai",
            enable_value={"type": "enabled"},
            disable_value={"type": "disabled"},
        )
    if name == "qwen":
        return ThinkingSpec(format="qwen")
    if name in ("qwen-template", "qwen_chat_template"):
        return ThinkingSpec(format="qwen-template")
    if name == "together":
        return ThinkingSpec(format="together")
    if name == "anthropic":
        return ThinkingSpec(format="anthropic", adaptive=True)
    if name in ("anthropic-budget", "anthropic_budget"):
        return ThinkingSpec(format="anthropic", adaptive=False, supports_effort=False)
    if name in ("string", "string-thinking"):
        return ThinkingSpec(format="string")
    return None


def build_spec(override: Any, base: Optional[ThinkingSpec] = None) -> Optional[ThinkingSpec]:
    """Build a spec from a user override (format name or dict)."""
    if override is None:
        return base
    if isinstance(override, str):
        named = _default_spec(override)
        if named is None:
            logger.warning("Unknown thinking format %r; ignoring", override)
            return base
        if base is not None and base.format == named.format:
            return base
        return named
    if not isinstance(override, dict):
        logger.warning(
            "thinking override must be a format name or dict, got %r",
            type(override).__name__,
        )
        return base

    style_name = str(override.get("style") or "").strip().lower()
    format_name = str(override.get("format") or "").strip().lower()
    # Legacy ``thinking_profile`` profiles used ``style`` names and
    # ``effort_map``; accept them so an existing llm.yaml keeps working.
    legacy_formats = {
        "effort": "openai",
        "deepseek_v4": "deepseek",
        "gemini_level": "openai",
        "gemini_budget": "openai",
    }
    if not format_name and style_name:
        format_name = legacy_formats.get(style_name, style_name)
    if format_name:
        named = _default_spec(format_name)
        if named is None:
            named = base
        elif base is not None and base.format == named.format:
            # Keep the builtin family's level map when only the format is
            # restated (e.g. {"format": "zai"} on a GLM-5.3 model).
            named = base
        base = named
    elif base is None:
        base = _default_spec("openai")
    if base is None:
        return None

    changes: Dict[str, Any] = {}
    if isinstance(override.get("levels"), (list, tuple)):
        levels = tuple(
            str(level).strip().lower()
            for level in override["levels"]
            if str(level).strip().lower() in _LEVEL_INDEX
            and str(level).strip().lower() != "off"
        )
        changes["levels"] = levels
    if "can_disable" in override:
        changes["can_disable"] = bool(override["can_disable"])
    if "supports_effort" in override:
        changes["supports_effort"] = bool(override["supports_effort"])
    level_map_override = override.get("level_map")
    if level_map_override is None and isinstance(override.get("effort_map"), dict):
        level_map_override = override["effort_map"]
    if isinstance(level_map_override, dict):
        merged_map = dict(base.level_map)
        merged_map.update(
            {
                str(key).strip().lower(): str(value)
                for key, value in level_map_override.items()
            }
        )
        changes["level_map"] = merged_map
    if isinstance(override.get("budget_map"), dict):
        merged_budgets = dict(base.budget_map)
        merged_budgets.update(
            {
                str(key).strip().lower(): int(value)
                for key, value in override["budget_map"].items()
            }
        )
        changes["budget_map"] = merged_budgets
    if "effort_field" in override:
        changes["effort_field"] = str(override["effort_field"])
    if "adaptive" in override:
        changes["adaptive"] = bool(override["adaptive"])
    if "disable_by_omission" in override:
        changes["disable_by_omission"] = bool(override["disable_by_omission"])
    if "toggle_field" in override:
        changes["toggle_field"] = str(override["toggle_field"])
    if "enable_field" in override:
        changes["enable_field"] = str(override["enable_field"])
    if "enable_value" in override:
        changes["enable_value"] = override["enable_value"]
    if "disable_value" in override:
        changes["disable_value"] = override["disable_value"]
    if isinstance(override.get("extra_fields"), dict):
        changes["extra_fields"] = _merge(base.extra_fields, override["extra_fields"])

    return replace(base, **changes) if changes else base


#: Spec for a model nothing knows: no thinking field is sent at all.
#:
#: A guessed ``reasoning_effort`` is a 400 on a non-reasoning model, and a
#: guessed toggle is a 400 on an endpoint that never defined one. Losing a
#: cosmetic setting is cheaper than losing the request, so an uncataloged model
#: gets nothing and ``GET /api/llm/thinking`` reports it as not covered.
_UNKNOWN_SPEC = ThinkingSpec(
    format="none",
    levels=(),
    can_disable=False,
    supports_effort=False,
)


#: Protocols a catalog record may declare a separate thinking shape for.
_PROTOCOL_KEYS = frozenset({"openai", "anthropic"})


def _select_protocol(
    thinking: Dict[str, Any], protocol: str
) -> Optional[Dict[str, Any]]:
    """Pick the thinking variant for the endpoint protocol."""
    if thinking and set(thinking) <= _PROTOCOL_KEYS:
        variant = thinking.get(protocol)
        return variant if isinstance(variant, dict) else None
    return thinking


def catalog_spec(model: str, protocol: str = "openai") -> Optional[ThinkingSpec]:
    """Resolve one model's catalog record into a wire spec."""
    record = get_model_record(model)
    if not record:
        return None
    thinking = record.get("thinking")
    if not isinstance(thinking, dict):
        return None
    variant = _select_protocol(thinking, (protocol or "openai").strip().lower())
    if not isinstance(variant, dict):
        return None
    return build_spec(variant)


def _endpoint_default_spec(provider_name: str, api_base: str) -> ThinkingSpec:
    """Shape implied by the endpoint itself, for a model the catalog lacks."""
    provider = (provider_name or "").strip().lower()
    base = (api_base or "").strip().lower()
    if "openrouter" in provider or "openrouter.ai" in base:
        return ThinkingSpec(format="openrouter")
    return _UNKNOWN_SPEC


def resolve_thinking_spec(
    model: str,
    *,
    provider_name: str = "",
    api_base: str = "",
    provider_type: str = "openai",
    options: Optional[Dict[str, Any]] = None,
    model_info: Optional[Dict[str, Any]] = None,
) -> ThinkingSpec:
    """Resolve the effective thinking spec for one request."""
    opts = options if isinstance(options, dict) else {}
    entry: Dict[str, Any] = {}
    if isinstance(model_info, dict):
        entry = model_info.get(model) or {}
        if not entry and "/" in (model or ""):
            # Accept provider/model spellings from callers while model_info
            # stays keyed by the bare model id.
            entry = model_info.get(model.split("/", 1)[1]) or {}
        if not entry:
            # YAML keys are hand-written; a casing mismatch must not silently
            # drop the user's per-model override.
            wanted = (model or "").strip().lower()
            wanted_short = wanted.rsplit("/", 1)[-1]
            for key, value in model_info.items():
                candidate = str(key).strip().lower()
                if candidate in (wanted, wanted_short):
                    entry = value or {}
                    break
    base = catalog_spec(model, provider_type)
    if base is None:
        base = _endpoint_default_spec(provider_name, api_base)

    # Per-model override: either {"thinking": {...}} or the spec dict itself.
    model_override = entry.get("thinking") if isinstance(entry, dict) else None
    if model_override is None and isinstance(entry, dict) and any(
        key in entry for key in ("format", "style", "levels", "level_map")
    ):
        model_override = entry
    if model_override is not None:
        spec = build_spec(model_override, base)
        if spec is not None:
            return spec

    provider_override = opts.get("thinking")
    if provider_override is None:
        provider_override = opts.get("thinking_profile")
    if provider_override is not None:
        spec = build_spec(provider_override, base)
        if spec is not None:
            return spec

    format_name = opts.get("thinking_format")
    if format_name:
        spec = build_spec(str(format_name), base)
        if spec is not None:
            return spec

    return base


def _off_fields(spec: ThinkingSpec) -> Dict[str, Any]:
    """Wire fields for an explicit disable."""
    fmt = spec.format
    if fmt == "openai":
        mapped = spec.level_map.get("off")
        return {spec.effort_field: str(mapped)} if isinstance(mapped, str) else {}
    if fmt == "openrouter":
        return {"reasoning": {"effort": "none"}}
    if fmt in ("deepseek", "moonshot"):
        return {
            spec.toggle_field: spec.disable_value
            if spec.disable_value is not None
            else {"type": "disabled"}
        }
    if fmt == "zai":
        return {
            spec.toggle_field: spec.disable_value
            if spec.disable_value is not None
            else {"type": "disabled"}
        }
    if fmt == "qwen":
        return {spec.enable_field: False}
    if fmt == "qwen-template":
        return {"chat_template_kwargs": {"enable_thinking": False, "preserve_thinking": True}}
    if fmt == "together":
        return {"reasoning": {"enabled": False}}
    if fmt == "anthropic":
        # Adaptive models think by default, so "off" needs an explicit
        # disabled block. Budget-mode extended thinking is opt-in, so
        # omitting the field is the correct disable there (and older
        # Claude 3.7 endpoints may not accept a disabled block).
        return {"thinking": {"type": "disabled"}} if spec.adaptive else {}
    if fmt == "string":
        return {"thinking": str(spec.level_map.get("off") or "none")}
    return {}


def _on_fields(spec: ThinkingSpec, level: str) -> Dict[str, Any]:
    """Wire fields for an enabled thinking level."""
    mapped = str(spec.level_map.get(level, level))
    effort = {spec.effort_field: mapped} if spec.supports_effort else {}
    fmt = spec.format

    if fmt == "none":
        return {}
    if fmt == "openai":
        return effort
    if fmt == "openrouter":
        return {"reasoning": {"effort": mapped}}
    if fmt == "deepseek":
        enable = (
            spec.enable_value
            if spec.enable_value is not None
            else {"type": "enabled"}
        )
        return {spec.toggle_field: enable, **effort}
    if fmt == "moonshot":
        # Moonshot 400s when the toggle and the effort are both present, so an
        # enabled level sends the effort alone.
        if effort:
            return dict(effort)
        return {
            spec.toggle_field: spec.enable_value
            if spec.enable_value is not None
            else {"type": "enabled"}
        }
    if fmt == "zai":
        enable = spec.enable_value
        if enable is None:
            enable = {"type": "enabled"}
        return {spec.toggle_field: enable, **effort}
    if fmt == "qwen":
        return {spec.enable_field: True, **effort}
    if fmt == "qwen-template":
        fields: Dict[str, Any] = {
            "chat_template_kwargs": {
                "enable_thinking": True,
                "preserve_thinking": True,
            }
        }
        fields.update(effort)
        return fields
    if fmt == "together":
        return {"reasoning": {"enabled": True}, **effort}
    if fmt == "anthropic":
        if spec.adaptive:
            fields = {
                "thinking": {"type": "adaptive", "display": "summarized"},
            }
            if spec.supports_effort:
                fields["output_config"] = {"effort": mapped}
            return fields
        return {
            "thinking": {
                "type": "enabled",
                "budget_tokens": spec.budget_for(level),
            }
        }
    if fmt == "string":
        return {"thinking": mapped}
    return effort


def decide_thinking(spec: ThinkingSpec, effort: Optional[str]) -> ThinkingDecision:
    """Map an effort value to the request fields for one model."""
    level = requested_level(effort)
    if level is None:
        return ThinkingDecision(fields={}, level=None, format=spec.format)

    note_parts = []
    if level == "off":
        # ``_off_fields`` is the single source of truth for whether this wire can
        # express a disable; ``can_disable=False`` means the model reasons no
        # matter what we send.
        off_fields = _off_fields(spec) if spec.can_disable else {}
        if off_fields:
            return ThinkingDecision(
                fields=_merge(off_fields, spec.extra_fields),
                level="off",
                format=spec.format,
            )
        if spec.disable_by_omission:
            # Extended thinking is opt-in on this wire, so sending nothing IS
            # the disable.
            return ThinkingDecision(
                fields=dict(spec.extra_fields),
                level="off",
                format=spec.format,
            )
        if spec.format == "none":
            # The model exposes no request-field control (e.g. DeepSeek R1).
            # Sending a fabricated level would be worse than omitting it.
            return ThinkingDecision(
                fields={},
                level="off",
                note="model does not expose thinking control; no field sent",
                format=spec.format,
            )
        if not spec.levels:
            return ThinkingDecision(
                fields=dict(spec.extra_fields),
                level="off",
                note="no disable field on this wire; nothing sent",
                format=spec.format,
            )
        fallback = spec.lowest() or "low"
        note_parts.append(
            f"model cannot disable thinking; clamped off -> {fallback}"
            if not spec.can_disable
            else f"no disable field on this wire; clamped off -> {fallback}"
        )
        level = fallback
    else:
        effective = spec.clamp(level)
        if effective != level:
            note_parts.append(f"{level} unsupported; clamped to {effective}")
        level = effective

    fields = _on_fields(spec, level)
    if spec.extra_fields:
        fields = _merge(fields, spec.extra_fields)
    return ThinkingDecision(
        fields=fields,
        level=level,
        note="; ".join(note_parts),
        format=spec.format,
    )


__all__ = [
    "LEVELS",
    "MODE_TO_LEVEL",
    "ThinkingDecision",
    "ThinkingSpec",
    "build_spec",
    "catalog_spec",
    "decide_thinking",
    "requested_level",
    "resolve_thinking_spec",
]
