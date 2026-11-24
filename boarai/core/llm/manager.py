"""
LLM manager for BoarAi system.

This module manages LLM providers and handles request routing.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import ConfigParser
from ..event import EventBus, EventType, get_event_bus, publish_event
from ..config.schema import LLMConfig, LLMProviderConfig
from .protocol import LLMError, LLMProvider, LLMRequest, LLMResponse

logger = logging.getLogger(__name__)


class LLMManager:
    """Manager for LLM providers."""
    
    def __init__(self, event_bus: Optional[EventBus] = None):
        """Initialize LLM manager."""
        self._providers: Dict[str, LLMProvider] = {}
        self._default_provider: Optional[str] = None
        self._default_model: Optional[str] = None
        self._capability_models: Dict[str, Dict[str, str]] = {}
        self._vision_cfg: Dict[str, Any] = {}
        self._event_bus = event_bus or get_event_bus()
        self._lock = asyncio.Lock()
        
        # Register event handlers
        self._setup_event_handlers()
    
    def _setup_event_handlers(self) -> None:
        """Setup event handlers for LLM manager."""
        # We'll register handlers when the event bus is ready
        pass
    
    async def register_provider(self, provider: LLMProvider) -> None:
        """Register an LLM provider."""
        async with self._lock:
            if provider.name in self._providers:
                raise LLMError(f"Provider {provider.name} is already registered")

            # A provider key reaches log lines through paths nobody planned for
            # (an aiohttp error that echoes the request, a debug dump of the
            # provider object). Registering the value makes every sink mask it.
            try:
                from boarai.core.logging import register_log_secret

                # Providers store the secret as ``_api_key``; there is no
                # public ``api_key`` property on either built-in provider.
                # Reading only the public name silently registered None.
                secret = getattr(provider, "api_key", None) or getattr(provider, "_api_key", None)
                register_log_secret(secret)
            except Exception:  # noqa: BLE001 - never fail a registration on this
                pass

            self._providers[provider.name] = provider
            
            # Set as default if it's the first provider
            if self._default_provider is None:
                self._default_provider = provider.name
            
            logger.info(f"Registered LLM provider: {provider.name}")
            
            # Publish event
            await publish_event(
                EventType.LLM_PROVIDER_REGISTERED,
                {"provider": provider.name, "models": provider.supported_models},
                "llm_manager"
            )
    
    async def unregister_provider(self, provider_name: str) -> bool:
        """Unregister an LLM provider."""
        async with self._lock:
            if provider_name not in self._providers:
                return False
            
            del self._providers[provider_name]
            
            # Update default provider if needed
            if self._default_provider == provider_name:
                self._default_provider = next(iter(self._providers)) if self._providers else None
            
            logger.info(f"Unregistered LLM provider: {provider_name}")
            
            # Publish event
            await publish_event(
                EventType.LLM_PROVIDER_UNREGISTERED,
                {"provider": provider_name},
                "llm_manager"
            )
            
            return True
    
    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate response from LLM."""
        # Determine provider
        provider_name = request.provider or self._default_provider
        
        if not provider_name:
            raise LLMError("No LLM provider available")
        
        if provider_name not in self._providers:
            raise LLMError(f"Provider {provider_name} not found")
        
        provider = self._providers[provider_name]
        
        # Set default model if not specified
        if not request.model and self._default_model:
            request.model = self._default_model
        
        try:
            # Publish request event with full messages content
            await publish_event(
                EventType.LLM_REQUEST,
                {
                    "provider": provider_name,
                    "model": request.model,
                    "messages": [m.to_dict() for m in request.messages],
                    "temperature": request.temperature,
                    "max_tokens": request.max_tokens,
                    "reasoning_effort": request.reasoning_effort,
                    "thinking_budget": request.thinking_budget,
                    "tools": request.tools,
                },
                "llm_manager"
            )
            
            # Generate response
            response = await provider.generate(request)
            
            # Publish response event with full content
            await publish_event(
                EventType.LLM_RESPONSE,
                {
                    "provider": provider_name,
                    "model": response.model,
                    "content": response.content,
                    "finish_reason": response.finish_reason,
                    "usage": response.usage,
                    "tool_calls": response.tool_calls,
                },
                "llm_manager"
            )
            
            return response
            
        except Exception as e:
            # Publish error event
            await publish_event(
                EventType.LLM_ERROR,
                {
                    "provider": provider_name,
                    "error": str(e),
                },
                "llm_manager"
            )
            raise LLMError(f"Generation failed: {e}", provider_name, request.model)
    
    async def generate_stream(self, request: LLMRequest):
        """Generate streaming response from LLM."""
        # Determine provider
        provider_name = request.provider or self._default_provider
        
        if not provider_name:
            raise LLMError("No LLM provider available")
        
        if provider_name not in self._providers:
            raise LLMError(f"Provider {provider_name} not found")
        
        provider = self._providers[provider_name]
        
        # Set default model if not specified
        if not request.model and self._default_model:
            request.model = self._default_model
        
        try:
            # Publish request event with full messages content (streaming)
            await publish_event(
                EventType.LLM_REQUEST,
                {
                    "provider": provider_name,
                    "model": request.model,
                    "messages": [m.to_dict() for m in request.messages],
                    "temperature": request.temperature,
                    "max_tokens": request.max_tokens,
                    "reasoning_effort": request.reasoning_effort,
                    "thinking_budget": request.thinking_budget,
                    "tools": request.tools,
                    "stream": True,
                },
                "llm_manager"
            )
            
            # Generate streaming response
            async for chunk in provider.generate_stream(request):
                yield chunk
            
            # Publish response event
            await publish_event(
                EventType.LLM_RESPONSE,
                {
                    "provider": provider_name,
                    "model": request.model,
                    "stream": True,
                },
                "llm_manager"
            )
            
        except Exception as e:
            # Publish error event
            await publish_event(
                EventType.LLM_ERROR,
                {
                    "provider": provider_name,
                    "error": str(e),
                    "stream": True,
                },
                "llm_manager"
            )
            raise LLMError(f"Streaming generation failed: {e}", provider_name, request.model)
    
    def get_provider(self, provider_name: str) -> Optional[LLMProvider]:
        """Get an LLM provider by name."""
        return self._providers.get(provider_name)
    
    def list_providers(self) -> List[str]:
        """List all registered providers."""
        return list(self._providers.keys())
    
    def list_models(self, provider_name: Optional[str] = None) -> List[str]:
        """List all available models."""
        models = []
        
        if provider_name:
            provider = self._providers.get(provider_name)
            if provider:
                models.extend(provider.supported_models)
        else:
            for provider in self._providers.values():
                models.extend(provider.supported_models)
        
        return models
    
    def set_default_provider(self, provider_name: str) -> bool:
        """Set default LLM provider."""
        if provider_name not in self._providers:
            return False
        
        self._default_provider = provider_name
        logger.info(f"Set default LLM provider: {provider_name}")
        return True
    
    @property
    def default_model(self) -> Optional[str]:
        """Configured default model, or None when nothing is set."""
        return self._default_model

    def set_default_model(self, model: str) -> None:
        """Set default LLM model."""
        self._default_model = model
        logger.info(f"Set default LLM model: {model}")
    
    async def validate_providers(self) -> Dict[str, bool]:
        """Validate all registered providers."""
        results = {}
        
        for name, provider in self._providers.items():
            try:
                results[name] = await provider.validate_connection()
            except Exception as e:
                logger.error(f"Provider {name} validation failed: {e}")
                results[name] = False
        
        return results
    
    async def get_all_models(self, provider_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get available models from all registered providers (or a specific one)."""
        all_models = []
        
        if provider_name:
            provider = self._providers.get(provider_name)
            if provider:
                try:
                    models = await provider.get_available_models()
                    all_models.extend(models)
                except Exception as e:
                    logger.error(f"Failed to get models from {provider_name}: {e}")
            else:
                logger.warning(f"Provider {provider_name} not found")
        else:
            for name, provider in self._providers.items():
                try:
                    models = await provider.get_available_models()
                    all_models.extend(models)
                except Exception as e:
                    logger.error(f"Failed to get models from {name}: {e}")
        
        return all_models
    
    async def load_from_config(self, config_path: Optional[Path] = None) -> None:
        """Load LLM providers from configuration file."""
        if config_path is None:
            from boarai.core.runtime import resolve_data_dir
            data_dir = resolve_data_dir(get_project_root())
            config_path = data_dir / "config" / "llm.yaml"
        
        if not config_path.exists():
            logger.warning(f"LLM config file not found: {config_path}")
            return
        
        try:
            # Load config using ConfigParser
            parser = ConfigParser(config_path)
            boarai_config = await parser.load()
            llm_config = boarai_config.llm
            
            # Set defaults
            self._default_model = llm_config.default_model or None
            
            # Create and register providers
            from .providers import OpenAICompatibleProvider, AnthropicProvider
            
            for name, provider_config in llm_config.providers.items():
                if not provider_config.api_key:
                    logger.warning(f"Skipping provider {name}: no API key configured")
                    continue
                
                # Provider protocol: explicit provider_type wins, then options.type
                # (legacy), then OpenAI-compatible.
                provider_options = (
                    provider_config.options
                    if isinstance(provider_config.options, dict)
                    else {}
                )
                provider_type = (
                    str(provider_config.provider_type or "").strip().lower()
                    or str(provider_options.get("type", "")).strip().lower()
                    or "openai"
                )
                
                if provider_type == "anthropic":
                    provider = AnthropicProvider(
                        name=name,
                        api_key=provider_config.api_key,
                        api_base=provider_config.api_base or "https://api.anthropic.com",
                        supported_models=provider_config.supported_models or [],
                        default_model=provider_config.default_model or llm_config.default_model or "claude-3-5-sonnet-20241022",
                        timeout=provider_config.timeout or llm_config.timeout,
                        max_retries=provider_config.max_retries or llm_config.max_retries,
                        options=provider_config.options or {},
                        model_info=provider_config.model_info or {},
                    )
                else:  # openai compatible
                    provider = OpenAICompatibleProvider(
                        name=name,
                        api_key=provider_config.api_key,
                        api_base=provider_config.api_base or "https://api.openai.com/v1",
                        supported_models=provider_config.supported_models or [],
                        default_model=provider_config.default_model or llm_config.default_model or "gpt-3.5-turbo",
                        timeout=provider_config.timeout or llm_config.timeout,
                        max_retries=provider_config.max_retries or llm_config.max_retries,
                        options=provider_config.options or {},
                        model_info=provider_config.model_info or {},
                    )
                
                await self.register_provider(provider)
                logger.info(f"Loaded LLM provider from config: {name} (type: {provider_type})")
            
            # Inject user-configured context windows from llm.yaml
            context_windows = llm_config.context_windows or {}
            if context_windows:
                for provider in self._providers.values():
                    provider.set_context_window_overrides(context_windows)
                logger.info(f"Loaded {len(context_windows)} context window overrides from config")

            # History-image replay governance (vision section)
            self._vision_cfg = dict(llm_config.vision or {})
            
            # Set default provider
            if llm_config.default_provider and llm_config.default_provider in self._providers:
                self._default_provider = llm_config.default_provider
                logger.info(f"Set default LLM provider: {llm_config.default_provider}")
            
            # Load capability model mappings
            for cap_key, cap_cfg in (llm_config.capability_models or {}).items():
                provider = cap_cfg.get("provider", "")
                model = cap_cfg.get("model", "")
                if provider and model:
                    self.set_capability_model(cap_key, provider, model)
            
            logger.info(f"Loaded {len(self._providers)} LLM providers from {config_path}")
        
        except Exception as e:
            logger.error(f"Failed to load LLM config: {e}")
            raise LLMError(f"Failed to load LLM config: {e}")
    
    def set_capability_model(self, capability: str, provider: str, model: str) -> None:
        """Set the model for a specific capability type."""
        self._capability_models[capability] = {"provider": provider, "model": model}
        logger.info(f"Set capability model: {capability} -> {provider}/{model}")
    
    def remove_capability_model(self, capability: str) -> None:
        """Remove a capability model mapping."""
        self._capability_models.pop(capability, None)
        logger.info(f"Removed capability model: {capability}")
    
    def get_capability_models(self) -> Dict[str, Dict[str, str]]:
        """Get all capability model mappings."""
        return dict(self._capability_models)
    
    def get_capability_model(self, capability: str) -> Optional[Dict[str, str]]:
        """Get model mapping for a specific capability."""
        return self._capability_models.get(capability)

    def get_model_capabilities(
        self, provider_name: Optional[str], model: str
    ) -> Dict[str, bool]:
        """Per-model capability flags declared in llm.yaml."""
        provider = self._providers.get(provider_name or "")
        if provider is None:
            model_key = str(model or "").strip()
            for cand in self._providers.values():
                supported = getattr(cand, "supported_models", None) or []
                if model_key in {str(m) for m in supported}:
                    provider = cand
                    break
            else:
                provider = self._providers.get(self._default_provider or "")
        if provider is None:
            return {}
        info = getattr(provider, "_model_info", None) or {}
        meta = info.get(str(model or ""))
        caps = meta.get("capabilities") if isinstance(meta, dict) else None
        if not isinstance(caps, dict):
            return {}
        return {k: bool(v) for k, v in caps.items() if isinstance(v, bool)}

    #: Defaults for the llm.yaml ``vision`` replay-governance section.
    VISION_REPLAY_WINDOW_DEFAULT = 3
    VISION_REPLAY_MAX_BYTES_DEFAULT = 12 * 1024 * 1024

    def get_vision_replay_window(self) -> int:
        """Newest N history images re-injected as pixels on every run."""
        raw = (self._vision_cfg or {}).get("replay_recent_images")
        if raw is None:
            return self.VISION_REPLAY_WINDOW_DEFAULT
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            logger.warning(
                "vision.replay_recent_images invalid (%r); replay disabled", raw
            )
            return 0
        return raw

    def get_vision_replay_max_bytes(self) -> int:
        """Per-run byte budget for re-injected history image pixels."""
        raw = (self._vision_cfg or {}).get("replay_max_bytes")
        if raw is None:
            return self.VISION_REPLAY_MAX_BYTES_DEFAULT
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            logger.warning(
                "vision.replay_max_bytes invalid (%r); replay disabled", raw
            )
            return 0
        return raw
    
    def get_stats(self) -> Dict[str, Any]:
        """Get LLM manager statistics."""
        return {
            "providers": list(self._providers.keys()),
            "default_provider": self._default_provider,
            "default_model": self._default_model,
            "capability_models": self.get_capability_models(),
            "total_providers": len(self._providers),
        }


# Global LLM manager instance
_llm_manager: Optional[LLMManager] = None


def get_llm_manager() -> LLMManager:
    """Get the global LLM manager instance."""
    global _llm_manager
    if _llm_manager is None:
        _llm_manager = LLMManager()
    return _llm_manager