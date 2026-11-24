"""
LLM API endpoints for BoarAi server.

Provides HTTP endpoints for LLM operations including chat, model listing,
and provider CRUD management. Follows direct-call pattern for simplicity.

Architecture:
    HTTP Request -> Server API -> LLMService -> LLMManager -> LLMProvider
"""

import json
import logging
from typing import Any, Dict, Optional

from aiohttp import web

from boarai.core.llm import get_llm_service
from boarai.core.event import Event, EventType, get_event_bus

logger = logging.getLogger(__name__)

_API_PREFIX = "/api/llm"


class LLMAPIMixin:
    """LLM API handlers mixin - chat, models, provider CRUD, WebSocket."""
    
    def __init__(self):
        """Initialize LLM API mixin."""
        self._llm = get_llm_service()
        self._event_bus = get_event_bus()
    
    # ==================== Chat Endpoints ====================
    
    async def handle_llm_chat(self, request: web.Request) -> web.Response:
        """POST /api/llm/chat - Chat completion."""
        try:
            data = await request.json()
            messages = data.get("messages", [])
            if not messages:
                return self._llm_error(400, "INVALID_REQUEST", "messages is required")
            
            response = await self._llm.chat(
                messages=messages,
                model=data.get("model"),
                temperature=data.get("temperature", 0.7),
                max_tokens=data.get("max_tokens"),
                top_p=data.get("top_p", 1.0),
            )
            
            return self._llm_success({
                "content": response.content,
                "model": response.model,
                "provider": response.provider,
                "usage": response.usage,
                "finish_reason": response.finish_reason,
            })
        except json.JSONDecodeError:
            return self._llm_error(400, "INVALID_JSON", "Request body must be valid JSON")
        except Exception as e:
            logger.error(f"LLM chat error: {e}")
            return self._llm_error(500, "INTERNAL_ERROR", str(e))

    # ==================== Provider CRUD Endpoints ====================
    
    async def handle_llm_providers_list(self, request: web.Request) -> web.Response:
        """GET /api/llm/providers - List all providers with details."""
        try:
            providers = self._llm.get_providers_detail()
            status = self._llm.get_status()
            return self._llm_success({
                "providers": providers,
                "default_provider": status.get("default_provider"),
                "default_model": status.get("default_model"),
            })
        except Exception as e:
            logger.error(f"Error listing providers: {e}")
            return self._llm_error(500, "INTERNAL_ERROR", str(e))
    
    async def handle_llm_providers_add(self, request: web.Request) -> web.Response:
        """POST /api/llm/providers - Add a new provider."""
        try:
            data = await request.json()
            name = data.get("name", "").strip()
            if not name:
                return self._llm_error(400, "INVALID_REQUEST", "name is required")
            
            api_key = data.get("api_key", "").strip()
            if not api_key:
                return self._llm_error(400, "INVALID_REQUEST", "api_key is required")
            
            provider = await self._llm.add_provider(
                name=name,
                api_key=api_key,
                api_base=data.get("api_base", "").strip(),
                provider_type=data.get("provider_type", "openai"),
                default_model=data.get("default_model"),
                supported_models=data.get("supported_models"),
                context_windows=data.get("context_windows"),
                options=data.get("options"),
                model_info=data.get("model_info"),
                capabilities=data.get("capabilities"),
            )
            
            # Publish provider event
            await self._publish_provider_event("provider.registered", provider)
            
            return self._llm_success(provider, status=201)
        except json.JSONDecodeError:
            return self._llm_error(400, "INVALID_JSON", "Request body must be valid JSON")
        except Exception as e:
            logger.error(f"Error adding provider: {e}")
            return self._llm_error(500, "INTERNAL_ERROR", str(e))
    
    async def handle_llm_providers_update(self, request: web.Request) -> web.Response:
        """PUT /api/llm/providers/{name} - Update a provider."""
        try:
            name = request.match_info["name"]
            data = await request.json()
            
            provider = await self._llm.update_provider(
                name=name,
                api_key=data.get("api_key"),
                api_base=data.get("api_base"),
                default_model=data.get("default_model"),
                supported_models=data.get("supported_models"),
                context_windows=data.get("context_windows"),
                options=data.get("options"),
                model_info=data.get("model_info"),
                capabilities=data.get("capabilities"),
            )
            
            if not provider:
                return self._llm_error(404, "NOT_FOUND", f"Provider '{name}' not found")
            
            await self._publish_provider_event("provider.updated", provider)
            
            return self._llm_success(provider)
        except json.JSONDecodeError:
            return self._llm_error(400, "INVALID_JSON", "Request body must be valid JSON")
        except Exception as e:
            logger.error(f"Error updating provider: {e}")
            return self._llm_error(500, "INTERNAL_ERROR", str(e))
    
    async def handle_llm_providers_delete(self, request: web.Request) -> web.Response:
        """DELETE /api/llm/providers/{name} - Remove a provider."""
        try:
            name = request.match_info["name"]
            
            if not self._llm.get_provider_detail(name):
                return self._llm_error(404, "NOT_FOUND", f"Provider '{name}' not found")
            
            await self._llm.remove_provider(name)
            
            await self._publish_provider_event("provider.unregistered", {"name": name})
            
            return self._llm_success({"name": name}, message="Provider removed")
        except Exception as e:
            logger.error(f"Error deleting provider: {e}")
            return self._llm_error(500, "INTERNAL_ERROR", str(e))
    
    async def handle_llm_providers_set_default(self, request: web.Request) -> web.Response:
        """PUT /api/llm/providers/{name}/default - Set as default provider."""
        try:
            name = request.match_info["name"]
            
            # Try to read optional body
            data = {}
            try:
                body = await request.read()
                if body:
                    data = await request.json()
            except (json.JSONDecodeError, ValueError, Exception):
                data = {}
            
            success = await self._llm.set_default_provider(
                name=name,
                default_model=data.get("default_model"),
            )
            
            if not success:
                return self._llm_error(404, "NOT_FOUND", f"Provider '{name}' not found")
            
            provider = self._llm.get_provider_detail(name) or {}
            await self._publish_provider_event("provider.updated", provider)
            
            return self._llm_success(provider)
        except Exception as e:
            logger.error(f"Error setting default provider: {e}")
            return self._llm_error(500, "INTERNAL_ERROR", str(e))
    
    async def handle_llm_providers_validate(self, request: web.Request) -> web.Response:
        """POST /api/llm/providers/{name}/validate - Test provider connection."""
        try:
            name = request.match_info["name"]
            result = await self._llm.validate_provider(name)
            return self._llm_success(result)
        except Exception as e:
            logger.error(f"Error validating provider: {e}")
            return self._llm_error(500, "INTERNAL_ERROR", str(e))

    async def handle_llm_providers_fetch_models(self, request: web.Request) -> web.Response:
        """POST /api/llm/providers/fetch-models - Fetch models from a provider API."""
        try:
            data = await request.json()
            provider_name = data.get("provider_name", "").strip()
            api_key = data.get("api_key", "").strip()
            api_base = data.get("api_base", "").strip()
            provider_type = data.get("provider_type", "openai")

            if not provider_name:
                if not api_key:
                    return self._llm_error(400, "INVALID_REQUEST", "api_key is required")
                if not api_base:
                    return self._llm_error(400, "INVALID_REQUEST", "api_base is required")

            models = await self._llm.fetch_models(
                api_key=api_key,
                api_base=api_base,
                provider_type=provider_type,
                provider_name=provider_name or None,
            )
            return self._llm_success({"models": models})
        except json.JSONDecodeError:
            return self._llm_error(400, "INVALID_JSON", "Request body must be valid JSON")
        except Exception as e:
            logger.error(f"Error fetching models: {e}")
            return self._llm_error(500, "INTERNAL_ERROR", str(e))
    
    # ==================== Model Listing ====================
    
    async def handle_llm_models(self, request: web.Request) -> web.Response:
        """GET /api/llm/models - List available models."""
        try:
            models = self._llm.list_models()
            return self._llm_success({"models": models})
        except Exception as e:
            logger.error(f"Error listing models: {e}")
            return self._llm_error(500, "INTERNAL_ERROR", str(e))
    
    async def handle_llm_status(self, request: web.Request) -> web.Response:
        """GET /api/llm/status - LLM system status."""
        try:
            status = self._llm.get_status()
            return self._llm_success(status)
        except Exception as e:
            logger.error(f"Error getting LLM status: {e}")
            return self._llm_error(500, "INTERNAL_ERROR", str(e))
    
    # ==================== Capability Models ====================
    
    async def handle_capability_models_get(self, request: web.Request) -> web.Response:
        """GET /api/llm/capability-models — Get all capability model mappings."""
        try:
            models = self._llm.get_capability_models()
            return self._llm_success({"capability_models": models})
        except Exception as e:
            logger.error(f"Error getting capability models: {e}")
            return self._llm_error(500, "INTERNAL_ERROR", str(e))
    
    async def handle_capability_models_put(self, request: web.Request) -> web.Response:
        """PUT /api/llm/capability-models — Set capability model mapping."""
        try:
            data = await request.json()
            capability = data.get("capability", "").strip()
            provider = data.get("provider", "").strip()
            model = data.get("model", "").strip()
            
            if not capability:
                return self._llm_error(400, "INVALID_REQUEST", "capability is required")
            if not provider:
                return self._llm_error(400, "INVALID_REQUEST", "provider is required")
            if not model:
                return self._llm_error(400, "INVALID_REQUEST", "model is required")
            
            success = self._llm.set_capability_model(capability, provider, model)
            if not success:
                return self._llm_error(400, "INVALID_REQUEST", f"Provider '{provider}' not found")
            
            return self._llm_success({
                "capability": capability,
                "provider": provider,
                "model": model,
            })
        except json.JSONDecodeError:
            return self._llm_error(400, "INVALID_JSON", "Request body must be valid JSON")
        except Exception as e:
            logger.error(f"Error setting capability model: {e}")
            return self._llm_error(500, "INTERNAL_ERROR", str(e))

    async def handle_llm_thinking(self, request: web.Request) -> web.Response:
        """GET /api/llm/thinking?provider=&model= — thinking mode mapping."""
        try:
            provider = (request.query.get("provider") or "").strip() or None
            model = (request.query.get("model") or "").strip() or None
            data = self._llm.get_thinking_capabilities(provider, model)
            if not data:
                return self._llm_error(404, "NOT_FOUND", "Provider not found")
            return self._llm_success(data)
        except Exception as e:
            logger.error(f"Error resolving thinking capabilities: {e}")
            return self._llm_error(500, "INTERNAL_ERROR", str(e))
    
    # ==================== Helpers ====================
    
    async def _publish_provider_event(self, event_type: str, payload: Any) -> None:
        """Publish provider event via EventBus and WebSocket."""
        try:
            await self._event_bus.publish(Event(
                event_type=f"llm.{event_type}",
                payload=payload if isinstance(payload, dict) else {},
                source="llm_api",
            ))
        except Exception as e:
            logger.warning(f"Failed to publish provider event: {e}")
    
    def _llm_success(self, data: Any, message: str = None, status: int = 200) -> web.Response:
        response = {"success": True, "data": data}
        if message:
            response["message"] = message
        return web.json_response(response, status=status)
    
    def _llm_error(self, status: int, code: str, message: str) -> web.Response:
        return web.json_response(
            {"success": False, "error": {"code": code, "message": message}},
            status=status,
        )


class LLMAPIHandler:
    """Standalone LLM API handler for route registration."""
    
    def __init__(self):
        self._mixin = LLMAPIMixin()
    
    @property
    def handle_llm_chat(self):
        return self._mixin.handle_llm_chat
    @property
    def handle_llm_providers_list(self):
        return self._mixin.handle_llm_providers_list
    @property
    def handle_llm_providers_add(self):
        return self._mixin.handle_llm_providers_add
    @property
    def handle_llm_providers_update(self):
        return self._mixin.handle_llm_providers_update
    @property
    def handle_llm_providers_delete(self):
        return self._mixin.handle_llm_providers_delete
    @property
    def handle_llm_providers_set_default(self):
        return self._mixin.handle_llm_providers_set_default
    @property
    def handle_llm_providers_validate(self):
        return self._mixin.handle_llm_providers_validate
    @property
    def handle_llm_providers_fetch_models(self):
        return self._mixin.handle_llm_providers_fetch_models
    @property
    def handle_llm_models(self):
        return self._mixin.handle_llm_models
    @property
    def handle_llm_status(self):
        return self._mixin.handle_llm_status
    @property
    def handle_capability_models_get(self):
        return self._mixin.handle_capability_models_get
    @property
    def handle_capability_models_put(self):
        return self._mixin.handle_capability_models_put
    @property
    def handle_llm_thinking(self):
        return self._mixin.handle_llm_thinking


_llm_handler: Optional[LLMAPIHandler] = None


def get_llm_api_handler() -> LLMAPIHandler:
    global _llm_handler
    if _llm_handler is None:
        _llm_handler = LLMAPIHandler()
    return _llm_handler
