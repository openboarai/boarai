"""

Agent API endpoints for BoarAi server.



Provides HTTP endpoints for agent CRUD operations:

- List, get, create, update, delete agents

- Set/get default (main) agent



Architecture:

    HTTP Request -> AgentAPIHandler -> AgentStore -> .boarai/agents/{name}/agent.json

                                       AgentRegistry (in-memory cache)

"""


import json

import logging

import re

from typing import Any, Dict, Optional


from aiohttp import web


from boarai.core.prompt.agent import AgentConfig

from boarai.core.prompt.agent_store import get_agent_store


logger = logging.getLogger(__name__)

# An agent name is used verbatim as a folder name under .boarai/agents/, so it
# must not be able to escape that directory. Creation is strict; lookups only
# reject traversal, so pre-existing roles with other characters stay usable.
_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fa5]{1,48}$")


def _unsafe_agent_name(name: str) -> bool:
    """True when ``name`` could escape the agents directory."""
    return (
        not name
        or "/" in name
        or "\\" in name
        or "\x00" in name
        or ".." in name
    )


class AgentAPIHandler:

    """Agent CRUD API handlers."""

    def __init__(self):
        """Bind the global agent store for CRUD handlers."""
        self._store = get_agent_store()

    # ----- Response Helpers -----

    @staticmethod
    def _success(data: Any, status: int = 200) -> web.Response:
        """Build a success JSON response with a ``data`` payload."""
        return web.json_response({"success": True, "data": data}, status=status)

    @staticmethod
    def _error(status: int, code: str, message: str) -> web.Response:
        """Build an error JSON response with ``code`` and ``message``."""
        return web.json_response(
            {"success": False, "error": {"code": code, "message": message}},
            status=status,
        )

    # ----- List Agents -----

    async def handle_list(self, request: web.Request) -> web.Response:

        """GET /api/agents — List all agents with details."""

        try:

            agents = self._store.load_all()

            default_agent = self._store.get_default()

            agent_list = [a.to_dict() for a in agents]

            return self._success({

                "agents": agent_list,

                "default": default_agent.name if default_agent else None,

                "total": len(agent_list),

            })

        except Exception as e:

            logger.error(f"Error listing agents: {e}")

            return self._error(500, "INTERNAL_ERROR", str(e))

    # ----- Get Single Agent -----

    async def handle_get(self, request: web.Request) -> web.Response:

        """GET /api/agents/{name} — Get a single agent."""

        try:

            name = request.match_info.get("name", "")

            if _unsafe_agent_name(name):

                return self._error(400, "INVALID_NAME", "invalid agent name")

            agent = self._store.load_agent(name)

            if not agent:

                return self._error(404, "NOT_FOUND", f"Agent '{name}' not found")

            return self._success(agent.to_dict())

        except Exception as e:

            logger.error(f"Error getting agent: {e}")

            return self._error(500, "INTERNAL_ERROR", str(e))

    # ----- Create Agent -----

    async def handle_create(self, request: web.Request) -> web.Response:

        """POST /api/agents — Create a new agent."""

        try:

            data = await request.json()

            name = data.get("name", "").strip()

            if not name:

                return self._error(400, "INVALID_REQUEST", "name is required")

            if not _AGENT_NAME_RE.match(name):

                return self._error(
                    400,
                    "INVALID_NAME",
                    "name may only contain letters, digits, '_', '-' or CJK "
                    "characters (max 48)",
                )

            # Check for existing agent

            existing = self._store.load_agent(name)

            if existing:

                return self._error(409, "CONFLICT", f"Agent '{name}' already exists")

            config = AgentConfig(

                name=name,

                avatar=data.get("avatar", "bot"),

                is_default=False,

                zone1_identity=data.get("zone1_identity", ""),

                zone1_rules=data.get("zone1_rules", ""),

                zone2_tool_names=data.get("zone2_tool_names", ["*"]),

                allowed_paths=data.get("allowed_paths", []),

                allowed_categories=data.get("allowed_categories", []),

                shell_allowlist=data.get("shell_allowlist", []),

                can_auto_approve=data.get("can_auto_approve", False),

                max_tool_calls=data.get("max_tool_calls", 0),

            )

            self._store.save_agent(config)

            return self._success(config.to_dict(), status=201)

        except json.JSONDecodeError:

            return self._error(400, "INVALID_JSON", "Request body must be valid JSON")

        except Exception as e:

            logger.error(f"Error creating agent: {e}")

            return self._error(500, "INTERNAL_ERROR", str(e))

    # ----- Update Agent -----

    async def handle_update(self, request: web.Request) -> web.Response:

        """PUT /api/agents/{name} — Update an existing agent."""

        try:

            name = request.match_info.get("name", "")

            if _unsafe_agent_name(name):

                return self._error(400, "INVALID_NAME", "invalid agent name")

            existing = self._store.load_agent(name)

            if not existing:

                return self._error(404, "NOT_FOUND", f"Agent '{name}' not found")

            data = await request.json()

            # Build updated config from existing + provided fields

            updated = AgentConfig(

                name=name,

                avatar=data.get("avatar", existing.avatar),

                is_default=data.get("is_default", existing.is_default),

                zone1_identity=data.get("zone1_identity", existing.zone1_identity),

                zone1_rules=data.get("zone1_rules", existing.zone1_rules),

                zone2_tool_names=data.get(

                    "zone2_tool_names", existing.zone2_tool_names

                ),

                allowed_paths=data.get("allowed_paths", existing.allowed_paths),

                allowed_categories=data.get(

                    "allowed_categories", existing.allowed_categories

                ),

                shell_allowlist=data.get("shell_allowlist", existing.shell_allowlist),

                can_auto_approve=data.get(

                    "can_auto_approve", existing.can_auto_approve

                ),

                max_tool_calls=data.get("max_tool_calls", existing.max_tool_calls),

                created_at=existing.created_at,

                updated_at=existing.updated_at,

            )

            self._store.save_agent(updated)

            return self._success(updated.to_dict())

        except json.JSONDecodeError:

            return self._error(400, "INVALID_JSON", "Request body must be valid JSON")

        except Exception as e:

            logger.error(f"Error updating agent: {e}")

            return self._error(500, "INTERNAL_ERROR", str(e))

    # ----- Delete Agent -----

    async def handle_delete(self, request: web.Request) -> web.Response:

        """DELETE /api/agents/{name} — Delete an agent.

        Deleting the default agent is allowed (the flag moves to a remaining
        one); the last remaining agent cannot be deleted.
        """

        try:

            name = request.match_info.get("name", "")

            if not name:

                return self._error(400, "INVALID_REQUEST", "Agent name is required")

            if _unsafe_agent_name(name):

                return self._error(400, "INVALID_NAME", "invalid agent name")

            existing = self._store.load_agent(name)

            if not existing:

                return self._error(404, "NOT_FOUND", f"Agent '{name}' not found")

            # An instance must always keep at least one role: with zero agents
            # there is nothing to assemble a system prompt for new sessions.
            remaining = [a for a in self._store.load_all() if a.name != name]

            if not remaining:

                return self._error(
                    400, "LAST_AGENT", "At least one agent must remain"
                )

            distill = request.query.get("distill", "").lower() == "true"

            deleted = await self._store.delete_agent(name, distill=distill)

            if not deleted:

                return self._error(404, "NOT_FOUND", f"Agent '{name}' not found")

            data = {"name": name, "deleted": True}

            # Deleting the default agent is allowed: hand the flag to a
            # remaining role so the next session still has one. No role name
            # is hardcoded anywhere in core.
            if existing.is_default:
                new_default = remaining[0].name
                self._store.set_default(new_default)
                data["default"] = new_default

            if distill:
                data["distill"] = "pending"

            return self._success(data)

        except Exception as e:

            logger.error(f"Error deleting agent: {e}")

            return self._error(500, "INTERNAL_ERROR", str(e))

    async def handle_delete_no_name(self, request: web.Request) -> web.Response:

        """DELETE /api/agents — Missing agent name, fallback handler."""

        return self._error(400, "INVALID_REQUEST", "Agent name is required in the URL path, e.g. DELETE /api/agents/my-agent")

    # ----- Set Default Agent -----

    async def handle_set_default(self, request: web.Request) -> web.Response:

        """PUT /api/agents/{name}/default — Set as default (main) agent."""

        try:

            name = request.match_info.get("name", "")

            if _unsafe_agent_name(name):

                return self._error(400, "INVALID_NAME", "invalid agent name")

            agent = self._store.load_agent(name)

            if not agent:

                return self._error(404, "NOT_FOUND", f"Agent '{name}' not found")

            old_default = self._store.set_default(name)

            return self._success({

                "name": name,

                "previous_default": old_default,

                "is_default": True,

            })

        except Exception as e:

            logger.error(f"Error setting default agent: {e}")

            return self._error(500, "INTERNAL_ERROR", str(e))

    # ----- Get Default Agent -----

    async def handle_get_default(self, request: web.Request) -> web.Response:

        """GET /api/agents/default — Get the current default (main) agent."""

        try:

            agent = self._store.get_default()

            if not agent:

                return self._error(404, "NOT_FOUND", "No default agent configured")

            return self._success(agent.to_dict())

        except Exception as e:

            logger.error(f"Error getting default agent: {e}")

            return self._error(500, "INTERNAL_ERROR", str(e))


# ==================== Global Instance ====================


_agent_handler: Optional[AgentAPIHandler] = None


def get_agent_api_handler() -> AgentAPIHandler:

    """Get the global agent API handler instance."""

    global _agent_handler

    if _agent_handler is None:

        _agent_handler = AgentAPIHandler()

    return _agent_handler

