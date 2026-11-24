"""
LLM Service layer for BoarAi system.

Provides a clean facade for LLM operations with simple function calls.
This is the recommended way to interact with LLMs throughout the system.

Architecture:
    callers (API/events) -> LLMService -> LLMManager -> LLMProvider -> LLM API

Usage:
    service = get_llm_service()

    # Simple chat
    response = await service.chat("Hello!", model="mimo-v2.5")

    # Full control
    response = await service.chat(
        messages=[
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello!"}
        ],
        model="mimo/mimo-v2.5",  # provider/model format
        temperature=0.7,
        max_tokens=2048
    )

    # Streaming
    async for chunk in service.chat_stream("Tell me a story"):
        print(chunk)

    # Provider management
    await service.add_provider("my-provider", api_key="sk-xxx", ...)
    await service.remove_provider("my-provider")
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Union

from .manager import LLMManager, get_llm_manager
from .catalog import get_context_window
from .thinking import (
    decide_thinking,
    requested_level,
    resolve_thinking_spec,
)
from .protocol import (
    LLMError,
    LLMRequest,
    LLMResponse,
    Message,
    MessageRole,
    StreamChunk,
)
from boarai.core.runtime import spawn, write_text_atomic_async

logger = logging.getLogger(__name__)


def _protect_api_key(value: Optional[str]) -> None:
    """Register a provider key with the log redactor before any network call."""
    if not value:
        return
    try:
        from boarai.core.logging import register_log_secret

        register_log_secret(value)
    except Exception:  # noqa: BLE001 - never turn a provider action into a failure
        pass


# Capability keys the WebUI can toggle per model. They live under
# ``model_info[model]["capabilities"]`` in llm.yaml; unknown keys are dropped
# on write so a stale client cannot smuggle arbitrary flags into the config.
CAPABILITY_KEYS = ("image", "audio", "video", "image_gen", "video_gen")

def attach_catalog_windows(models: List[Any]) -> List[Any]:
    """Add each listed model's catalog context window, for display."""
    for entry in models:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("id") or "").strip()
        if not model_id:
            continue
        window = get_context_window(model_id)
        if window is not None:
            entry["context_window"] = window
    return models

def _parse_model_spec(model: Optional[str]) -> tuple:
    """Parse model specification into (provider, model)."""
    if not model:
        return (None, None)

    if "/" in model:
        parts = model.split("/", 1)
        return (parts[0], parts[1])

    return (None, model)

def _to_message_dicts(messages: Any) -> List[Dict[str, Any]]:
    """Convert various message formats to list of dicts."""
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]

    result = []
    for msg in messages:
        if isinstance(msg, dict):
            result.append(msg)
        elif isinstance(msg, Message):
            result.append(msg.to_dict())
        else:
            raise LLMError(f"Invalid message type: {type(msg)}")

    return result

def _to_message_objects(messages: List[Dict[str, Any]]) -> List[Message]:
    """Convert message dicts to Message objects."""
    result = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        result.append(Message(
            role=MessageRole(role),
            content=content if content is not None else "",
            name=msg.get("name"),
            tool_call_id=msg.get("tool_call_id"),
            tool_calls=msg.get("tool_calls"),
            reasoning_content=msg.get("reasoning_content") or msg.get("thinking"),
        ))
    return result

class LLMService:
    """Clean facade for LLM operations."""

    def __init__(self):
        """Initialize LLM service."""
        self._manager = get_llm_manager()

    # ==================== Quick-Call Functions ====================

    async def chat(
        self,
        messages: Union[str, List[Dict[str, Any]], List[Message]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        top_p: float = 1.0,
        tools: Optional[List[Dict[str, Any]]] = None,
        cache_breakpoints: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Send a chat request and get a complete response."""
        message_dicts = _to_message_dicts(messages)
        provider_name, model_name = _parse_model_spec(model)

        request = LLMRequest(
            messages=_to_message_objects(message_dicts),
            model=model_name,
            provider=provider_name,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            tools=tools,
            cache_breakpoints=cache_breakpoints,
            **kwargs,
        )

        return await self._manager.generate(request)

    async def chat_stream(
        self,
        messages: Union[str, List[Dict[str, Any]], List[Message]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        top_p: float = 1.0,
        tools: Optional[List[Dict[str, Any]]] = None,
        cache_breakpoints: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        """Send a chat request and stream the response."""
        message_dicts = _to_message_dicts(messages)
        provider_name, model_name = _parse_model_spec(model)

        request = LLMRequest(
            messages=_to_message_objects(message_dicts),
            model=model_name,
            provider=provider_name,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            stream=True,
            tools=tools,
            cache_breakpoints=cache_breakpoints,
            **kwargs,
        )

        async for chunk in self._manager.generate_stream(request):
            yield chunk

    # ==================== Convenience Methods ====================

    async def simple_chat(self, prompt: str, model: Optional[str] = None) -> str:
        """Simplest possible chat - just prompt and get response text."""
        response = await self.chat(prompt, model=model)
        return response.content

    async def system_chat(
        self,
        system_prompt: str,
        user_prompt: str,
        model: Optional[str] = None,
    ) -> str:
        """Chat with a system prompt."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        response = await self.chat(messages, model=model)
        return response.content

    # ==================== Model and Provider Management ====================

    def list_providers(self) -> List[str]:
        """List all registered providers."""
        return self._manager.list_providers()

    def list_models(self) -> List[Dict[str, Any]]:
        """List all available models with provider info."""
        models = []
        for provider_name in self._manager.list_providers():
            provider = self._manager.get_provider(provider_name)
            if provider:
                provider_caps = self._provider_capabilities(provider)
                for model in provider.supported_models:
                    entry: Dict[str, Any] = {
                        "name": f"{provider_name}/{model}",
                        "short_name": model,
                        "provider": provider_name,
                    }
                    # Attach known context window if available
                    ctx_win = provider.get_context_window(model)
                    if ctx_win is not None:
                        entry["context_window"] = ctx_win
                    # Capability flags so clients (session toolbar) can show
                    # multimodal icons without a second fetch.
                    caps = provider_caps.get(model)
                    if caps:
                        entry["capabilities"] = caps
                    models.append(entry)

        return models

    def get_status(self) -> Dict[str, Any]:
        """Get service status summary."""
        stats = self._manager.get_stats()
        return {
            "providers": stats.get("providers", []),
            "default_provider": stats.get("default_provider"),
            "default_model": stats.get("default_model"),
            "capability_models": stats.get("capability_models", {}),
            "total_providers": stats.get("total_providers", 0),
            "available_models": [m["name"] for m in self.list_models()],
        }

    # ==================== Capability Model Management ====================

    def set_capability_model(self, capability: str, provider: str, model: str) -> bool:
        """Set the model for a specific capability."""
        if provider not in self._manager._providers:
            return False
        self._manager.set_capability_model(capability, provider, model)
        spawn(self._save_config())
        return True

    def get_capability_models(self) -> Dict[str, Dict[str, str]]:
        """Get all capability model mappings."""
        return self._manager.get_capability_models()

    def get_thinking_capabilities(
        self,
        provider_name: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Describe how the session thinking modes map to one provider/model."""
        model = (model or "").strip() or None
        provider = None
        if model and "/" in model:
            prefix, _, rest = model.partition("/")
            provider = self._manager.get_provider(prefix)
            if provider is None:
                wanted = prefix.strip().lower()
                for candidate in self._manager.list_providers():
                    if candidate.strip().lower() == wanted:
                        provider = self._manager.get_provider(candidate)
                        break
            if provider is not None:
                provider_name = provider.name
                model = rest
            # else: keep the full "prefix/model" so family detection can use it
        if provider is None:
            if not provider_name:
                provider_name = self._manager._default_provider
            provider = (
                self._manager.get_provider(provider_name) if provider_name else None
            )
            if provider is None and provider_name:
                wanted = str(provider_name).strip().lower()
                for candidate in self._manager.list_providers():
                    if candidate.strip().lower() == wanted:
                        provider = self._manager.get_provider(candidate)
                        break
        if not provider:
            return None

        model_name = model or getattr(provider, "_default_model", "") or ""
        spec = resolve_thinking_spec(
            model_name,
            provider_name=provider.name,
            api_base=getattr(provider, "_api_base", ""),
            options=getattr(provider, "_options", {})
            if isinstance(getattr(provider, "_options", {}), dict)
            else {},
            model_info=getattr(provider, "_model_info", {})
            if isinstance(getattr(provider, "_model_info", {}), dict)
            else {},
        )
        try:
            from boarai.core.session.thinking.config import load_thinking_settings

            default_mode, default_enabled = load_thinking_settings()
            server_default = default_mode.value
        except Exception:  # noqa: BLE001 - display metadata only
            default_mode = None
            default_enabled = True
            server_default = "none"
        auto_level = None
        if (
            default_enabled
            and default_mode is not None
            and default_mode.value != "none"
        ):
            auto_level = requested_level(default_mode.value)
        modes = {
            "none": auto_level,
            "off": "none",
            "light": "low",
            "deep": "medium",
            "ultra": "xhigh",
        }
        effective: Dict[str, Dict[str, Any]] = {}
        for mode, effort in modes.items():
            decision = decide_thinking(spec, effort)
            effective[mode] = {
                "level": decision.level,
                "fields": decision.fields,
                "note": decision.note,
            }
        off = effective["off"]
        # Report what the wire actually does, not what the spec claims: an off
        # request either sends a disable field, relies on omission being the
        # disable (Anthropic budget mode), or fell back to a level.
        can_disable = off["level"] == "off" and (
            bool(off["fields"]) or spec.disable_by_omission
        )
        return {
            "provider": provider.name,
            "model": model_name,
            "format": spec.format,
            "levels": list(spec.levels),
            "can_disable": can_disable,
            "off_clamped": off["level"] not in (None, "off"),
            "supports_effort": spec.supports_effort,
            "server_default": server_default,
            "effective": effective,
        }

    async def validate_connections(self) -> Dict[str, bool]:
        """Validate all provider connections."""
        return await self._manager.validate_providers()

    # ==================== Provider CRUD Management ====================

    def get_providers_detail(self) -> List[Dict[str, Any]]:
        """Get detailed info for all providers."""
        providers = []
        for name in self._manager.list_providers():
            provider = self._manager.get_provider(name)
            if not provider:
                continue

            providers.append({
                "name": provider.name,
                "api_base": getattr(provider, '_api_base', ''),
                "default_model": getattr(provider, '_default_model', ''),
                "supported_models": list(provider.supported_models or []),
                "context_windows": self._provider_context_windows(provider),
                "context_window_defaults": self._provider_context_defaults(provider),
                "capabilities": self._provider_capabilities(provider),
                "provider_type": (
                    "anthropic"
                    if "anthropic" in type(provider).__module__.lower()
                    else "openai"
                ),
            })

        return providers

    def get_provider_detail(self, name: str) -> Optional[Dict[str, Any]]:
        """Get detailed info for a specific provider."""
        provider = self._manager.get_provider(name)
        if not provider:
            return None

        return {
            "name": provider.name,
            "api_base": getattr(provider, '_api_base', ''),
            "default_model": getattr(provider, '_default_model', ''),
            "supported_models": list(provider.supported_models or []),
            "context_windows": self._provider_context_windows(provider),
            "context_window_defaults": self._provider_context_defaults(provider),
            "capabilities": self._provider_capabilities(provider),
            "model_info": dict(getattr(provider, '_model_info', None) or {}),
            "provider_type": (
                "anthropic"
                if "anthropic" in type(provider).__module__.lower()
                else "openai"
            ),
        }

    async def add_provider(
        self,
        name: str,
        api_key: str,
        api_base: str = "https://api.openai.com/v1",
        provider_type: str = "openai",
        default_model: Optional[str] = None,
        supported_models: Optional[List[str]] = None,
        context_windows: Optional[Dict[str, Any]] = None,
        options: Optional[Dict[str, Any]] = None,
        model_info: Optional[Dict[str, Dict[str, Any]]] = None,
        capabilities: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Add a new provider."""
        if name in self._manager._providers:
            raise LLMError(f"Provider '{name}' already exists")

        _protect_api_key(api_key)

        models = list(supported_models or [])
        # Prefer explicit default, else first supported model.
        # Do not invent a hardcoded model when the list is empty.
        if default_model and models and default_model not in models:
            models = [default_model] + models
        if not default_model and models:
            default_model = models[0]
        if not default_model:
            default_model = ""

        if provider_type == "anthropic":
            from .providers import AnthropicProvider
            provider = AnthropicProvider(
                name=name,
                api_key=api_key,
                api_base=api_base or "https://api.anthropic.com",
                supported_models=models,
                default_model=default_model or (models[0] if models else ""),
                options=options or {},
                model_info=model_info or {},
            )
        else:
            from .providers import OpenAICompatibleProvider
            provider = OpenAICompatibleProvider(
                name=name,
                api_key=api_key,
                api_base=api_base or "https://api.openai.com/v1",
                supported_models=models,
                default_model=default_model or (models[0] if models else ""),
                options=options or {},
                model_info=model_info or {},
            )

        await self._manager.register_provider(provider)

        # Apply per-model context window overrides (global by model name)
        if context_windows is not None:
            self._apply_context_windows(
                provider, context_windows, list(provider.supported_models or [])
            )

        # Apply per-model capability flags
        if capabilities is not None:
            self._apply_capabilities(
                provider, capabilities, list(provider.supported_models or [])
            )

        # Auto-save config
        await self._save_config()

        return self.get_provider_detail(name) or {}

    async def remove_provider(self, name: str) -> bool:
        """Remove a provider."""
        result = await self._manager.unregister_provider(name)
        if result:
            await self._save_config()
        return result

    async def update_provider(
        self,
        name: str,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        default_model: Optional[str] = None,
        supported_models: Optional[List[str]] = None,
        context_windows: Optional[Dict[str, Any]] = None,
        options: Optional[Dict[str, Any]] = None,
        model_info: Optional[Dict[str, Dict[str, Any]]] = None,
        capabilities: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update a provider's configuration."""
        provider = self._manager.get_provider(name)
        if not provider:
            return None

        # Update fields
        if api_key is not None:
            _protect_api_key(api_key)
            provider._api_key = api_key
        if api_base is not None:
            provider._api_base = api_base.rstrip("/")
        if options is not None:
            provider._options = dict(options) if isinstance(options, dict) else {}
        if model_info is not None:
            provider._model_info = (
                dict(model_info) if isinstance(model_info, dict) else {}
            )
        if supported_models is not None:
            provider._supported_models = list(supported_models)
            # Keep default_model valid relative to the new list
            current_default = getattr(provider, "_default_model", "") or ""
            if provider._supported_models:
                if default_model is not None:
                    if default_model and default_model not in provider._supported_models:
                        provider._supported_models = [default_model] + provider._supported_models
                    provider._default_model = default_model or provider._supported_models[0]
                elif current_default not in provider._supported_models:
                    provider._default_model = provider._supported_models[0]
            else:
                # Empty model list: clear provider-level default unless explicitly set
                provider._default_model = default_model if default_model is not None else ""
        elif default_model is not None:
            provider._default_model = default_model

        logger.info(f"Updated provider: {name}")

        # Apply per-model context window overrides (global by model name)
        if context_windows is not None:
            self._apply_context_windows(
                provider, context_windows, list(provider.supported_models or [])
            )

        # Apply per-model capability flags
        if capabilities is not None:
            self._apply_capabilities(
                provider, capabilities, list(provider.supported_models or [])
            )

        await self._save_config()

        return self.get_provider_detail(name)

    async def set_default_provider(self, name: str, default_model: Optional[str] = None) -> bool:
        """Set a provider as default."""
        if name not in self._manager._providers:
            return False

        self._manager._default_provider = name
        if default_model:
            self._manager._default_model = default_model
        elif self._manager._default_model is None:
            provider = self._manager.get_provider(name)
            if provider:
                self._manager._default_model = provider._default_model

        logger.info(f"Set default provider: {name}, model: {self._manager._default_model}")

        await self._save_config()
        return True

    async def validate_provider(self, name: str) -> Dict[str, Any]:
        """Validate a specific provider connection."""
        provider = self._manager.get_provider(name)
        if not provider:
            return {"valid": False, "error": "Provider not found"}

        try:
            valid = await provider.validate_connection()
            return {"valid": valid}
        except Exception as e:
            return {"valid": False, "error": str(e)}

    async def fetch_models(
        self,
        api_key: str = "",
        api_base: str = "",
        provider_type: str = "openai",
        provider_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch available models from a provider API without registering it."""
        if provider_name:
            provider = self._manager.get_provider(provider_name)
            if not provider:
                raise LLMError(f"Provider '{provider_name}' not found")
            api_key = getattr(provider, "_api_key", "") or ""
            api_base = getattr(provider, "_api_base", "") or ""
            mod = type(provider).__module__.lower()
            provider_type = "anthropic" if "anthropic" in mod else "openai"

        if not api_key:
            raise LLMError("api_key is required")
        if not api_base:
            raise LLMError("api_base is required")

        # This path deliberately does NOT register a provider, so the manager's
        # registration hook cannot protect the temporary key.
        _protect_api_key(api_key)

        if provider_type == "anthropic":
            from .providers import AnthropicProvider
            provider = AnthropicProvider(
                name="__temp_fetch__",
                api_key=api_key,
                api_base=api_base or "https://api.anthropic.com",
                supported_models=[],
                default_model="claude-3-5-sonnet-20241022",
            )
        else:
            from .providers import OpenAICompatibleProvider
            provider = OpenAICompatibleProvider(
                name="__temp_fetch__",
                api_key=api_key,
                api_base=api_base or "https://api.openai.com/v1",
                supported_models=[],
                default_model="gpt-3.5-turbo",
            )

        try:
            models = await provider.get_available_models()
            return attach_catalog_windows(models)
        finally:
            # Clean up the temporary session
            close = getattr(provider, "close", None)
            if close:
                await close()

    def _collect_context_windows(self) -> Dict[str, int]:
        """Collect the context windows the user actually set."""
        merged: Dict[str, int] = {}

        def _put(model: object, size: object) -> None:
            key = str(model).strip()
            if not key:
                return
            try:
                merged[key] = int(size)
            except (TypeError, ValueError):
                return

        for provider in self._manager._providers.values():
            overrides = getattr(provider, '_context_window_overrides', None) or {}
            for model, size in overrides.items():
                _put(model, size)
        return dict(sorted(merged.items(), key=lambda kv: kv[0]))

    def _provider_context_windows(self, provider: Any) -> Dict[str, int]:
        """The user's stored context-window overrides for this provider's models."""
        overrides = getattr(provider, '_context_window_overrides', None) or {}
        out: Dict[str, int] = {}
        for m in (getattr(provider, 'supported_models', None) or []):
            key = str(m).strip()
            if not key or key not in overrides:
                continue
            try:
                out[key] = int(overrides[key])
            except (TypeError, ValueError):
                continue
        return out

    def _provider_context_defaults(self, provider: Any) -> Dict[str, int]:
        """Catalog context window per supported model, for display only."""
        out: Dict[str, int] = {}
        for m in (getattr(provider, 'supported_models', None) or []):
            key = str(m).strip()
            if not key:
                continue
            cw = get_context_window(key)
            if cw is None:
                continue
            out[key] = int(cw)
        return out

    def _apply_context_windows(
        self,
        provider: Any,
        context_windows: Optional[Dict[str, Any]],
        models: List[str],
    ) -> None:
        """Set/clear per-model context window overrides."""
        incoming = context_windows or {}
        changes: Dict[str, Optional[int]] = {}
        for m in models:
            key = str(m).strip()
            if not key:
                continue
            val = incoming.get(key)
            if val is None:
                changes[key] = None
                continue
            try:
                iv = int(val)
            except (TypeError, ValueError):
                continue
            changes[key] = iv if iv > 0 else None

        if not changes:
            return

        for p in self._manager._providers.values():
            ov = dict(getattr(p, '_context_window_overrides', None) or {})
            for key, iv in changes.items():
                if iv is None:
                    ov.pop(key, None)
                else:
                    ov[key] = iv
            p._context_window_overrides = ov

    # ==================== Per-model capabilities ====================

    @staticmethod
    def _normalize_capabilities(raw: Any) -> Dict[str, bool]:
        """Coerce an incoming capability map to ``{key: bool}`` on known keys."""
        out: Dict[str, bool] = {}
        if not isinstance(raw, dict):
            return out
        for key in CAPABILITY_KEYS:
            if key not in raw:
                continue
            value = raw[key]
            # Never let a string like "false" become True through bool().
            if isinstance(value, str):
                value = value.strip().lower() in ("1", "true", "yes", "on")
            out[key] = bool(value)
        return out

    def _provider_capabilities(self, provider: Any) -> Dict[str, Dict[str, bool]]:
        """Stored per-model capability flags for this provider's models."""
        info = getattr(provider, '_model_info', None) or {}
        out: Dict[str, Dict[str, bool]] = {}
        for model, meta in info.items():
            if not isinstance(meta, dict):
                continue
            caps = self._normalize_capabilities(meta.get("capabilities"))
            if caps:
                out[str(model)] = caps
        return out

    def _apply_capabilities(
        self,
        provider: Any,
        capabilities: Optional[Dict[str, Any]],
        models: List[str],
    ) -> None:
        """Write per-model capability flags into ``model_info``."""
        info = dict(getattr(provider, '_model_info', None) or {})
        supported = {str(m).strip() for m in (models or []) if str(m).strip()}
        incoming = capabilities if isinstance(capabilities, dict) else {}

        for model in list(info.keys()):
            if model in supported:
                continue
            meta = info.get(model)
            if not isinstance(meta, dict) or "capabilities" not in meta:
                continue
            meta = dict(meta)
            meta.pop("capabilities", None)
            if meta:
                info[model] = meta
            else:
                info.pop(model, None)

        for model in sorted(supported):
            meta = dict(info.get(model) or {})
            meta["capabilities"] = self._normalize_capabilities(incoming.get(model))
            info[model] = meta

        provider._model_info = info

    def get_model_context_window(self, model: str) -> Optional[int]:
        """Resolve a model's context window from user overrides / builtins."""
        if not model:
            return None
        key = str(model).strip()
        if not key:
            return None
        short = key.split("/", 1)[1] if "/" in key else key
        for p in self._manager._providers.values():
            try:
                cw = p.get_context_window(short)
            except Exception:
                cw = None
            if cw is None:
                continue
            try:
                return int(cw)
            except (TypeError, ValueError):
                continue
        return None

    async def _save_config(self) -> None:
        """Save current provider configuration to YAML file."""
        try:
            from boarai.core.runtime import get_data_dir
            config_path = get_data_dir() / "config" / "llm.yaml"
            config_path.parent.mkdir(parents=True, exist_ok=True)

            import yaml

            # Keep unrelated top-level keys (``settings``, custom sections) that
            # this writer does not own. A provider edit must not erase them.
            existing: Dict[str, Any] = {}
            try:
                if config_path.exists():
                    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        existing = loaded
            except Exception as exc:  # noqa: BLE001 - a corrupt file is replaced
                logger.debug("Could not read existing llm.yaml: %s", exc)

            # Force string keys in context_windows so YAML reloads cleanly
            context_windows = {
                str(k): int(v) for k, v in self._collect_context_windows().items()
            }
            config = {
                **existing,
                "default_provider": self._manager._default_provider or "",
                "default_model": self._manager._default_model or "",
                "capability_models": self._manager.get_capability_models(),
                "providers": {},
                "context_windows": context_windows,
            }

            existing_providers = existing.get("providers")
            existing_providers = existing_providers if isinstance(existing_providers, dict) else {}

            for name in self._manager.list_providers():
                provider = self._manager.get_provider(name)
                if not provider:
                    continue

                # Detect provider type
                provider_type = "openai"
                if "anthropic" in type(provider).__module__.lower():
                    provider_type = "anthropic"

                # Keep provider keys this writer does not own (``enabled``,
                # ``rate_limit``, a list-format ``name``, future fields).
                previous = existing_providers.get(name)
                previous_provider = previous if isinstance(previous, dict) else {}

                provider_options = getattr(provider, '_options', None) or {}
                model_info = getattr(provider, '_model_info', None) or {}
                new_provider = {
                    "provider_type": provider_type,
                    "api_key": getattr(provider, '_api_key', ''),
                    "api_base": getattr(provider, '_api_base', ''),
                    "default_model": getattr(provider, '_default_model', ''),
                    "supported_models": [str(m) for m in (provider.supported_models or [])],
                    "timeout": getattr(provider, '_timeout', None),
                    "max_retries": getattr(provider, '_max_retries', None),
                    # Written even when empty so clearing an override in the UI
                    # actually removes it on the next load.
                    "options": provider_options,
                    "model_info": model_info,
                }
                config["providers"][name] = {**previous_provider, **new_provider}

            # A provider that is not registered — no api_key yet, so the loader
            # skipped it — must survive a save. Dropping it would delete the
            # stored configuration of a provider the user has not filled in.
            for name, entry in existing_providers.items():
                if name not in config["providers"]:
                    config["providers"][name] = entry
                    logger.info("Preserved unregistered provider %s in llm.yaml", name)

            content = yaml.dump(config, default_flow_style=False, allow_unicode=True)
            async with _config_lock:
                # This file holds every provider's plaintext api_key. A plain
                # open(..., "w") truncates before writing, so a crash mid-write
                # would zero out all stored credentials with no recovery path.
                await write_text_atomic_async(config_path, content)

            logger.info(f"Saved provider config to {config_path}")

        except Exception as e:
            logger.warning(f"Failed to save provider config: {e}", exc_info=True)

# Global lock for config file access
_config_lock = asyncio.Lock()

# Global instance
_service: Optional[LLMService] = None

def get_llm_service() -> LLMService:
    """Get the global LLM service instance."""
    global _service
    if _service is None:
        _service = LLMService()
    return _service
