"""
Memory API endpoints for BoarAi server.

Flat API structure - exposes the Synaptic Memory Mesh to WebUI
and other channel extensions via RESTful endpoints.
"""

import json
import logging
from typing import Any, Dict, Optional

from aiohttp import web

from boarai.core.memory import (
    get_memory_manager,
    get_memory_weaver,
    MemoryQuery,
    ReincarnationEngine,
)

logger = logging.getLogger(__name__)


class MemoryAPIHandler:
    """Handles memory API requests (Synaptic Memory Mesh)."""

    def __init__(self):
        self._manager = get_memory_manager()
        self._weaver = get_memory_weaver()

    async def handle_stats(self, request: web.Request) -> web.Response:
        """GET /api/memory/stats - Memory system statistics."""
        try:
            stats = await self._manager.get_stats()
            return web.json_response({"success": True, "data": stats.__dict__})
        except Exception as e:
            logger.error(f"Memory stats error: {e}")
            return web.json_response(
                {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}},
                status=500,
            )

    async def handle_list_entries(self, request: web.Request) -> web.Response:
        """GET /api/memory/entries - Search/list memory entries."""
        try:
            query = MemoryQuery(
                text=request.query.get("text"),
                entry_type=request.query.get("entry_type"),
                agent_name=request.query.get("agent"),
                limit=int(request.query.get("limit", 50)),
                offset=int(request.query.get("offset", 0)),
            )
            tags_str = request.query.get("tags")
            if tags_str:
                query.tags = [t.strip() for t in tags_str.split(",") if t.strip()]

            pod_uris = request.query.get("pod")
            pods = [pod_uris] if pod_uris else None

            entries = await self._manager.recall(query, pod_uris=pods)
            return web.json_response({
                "success": True,
                "data": {
                    "entries": [e.to_dict() for e in entries],
                    "count": len(entries),
                },
            })
        except Exception as e:
            logger.error(f"Memory list error: {e}")
            return web.json_response(
                {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}},
                status=500,
            )

    async def handle_delete_entry(self, request: web.Request) -> web.Response:
        """DELETE /api/memory/entries/{entry_id} - Delete a memory entry."""
        entry_id = request.match_info.get("entry_id", "")
        if not entry_id:
            return web.json_response(
                {"success": False, "error": {"code": "MISSING_PARAM", "message": "entry_id required"}},
                status=400,
            )
        pod_uri = request.query.get("pod")
        permanent = request.query.get("permanent", "0") == "1"
        try:
            if permanent:
                deleted = await self._manager.forget(entry_id, pod_uri)
            else:
                deleted = await self._manager.soft_delete_entry(entry_id, pod_uri)
            if not deleted:
                return web.json_response(
                    {"success": False, "error": {"code": "NOT_FOUND", "message": f"Entry {entry_id} not found"}},
                    status=404,
                )
            return web.json_response({"success": True, "data": {"entry_id": entry_id, "deleted": True}})
        except Exception as e:
            logger.error(f"Memory delete error: {e}")
            return web.json_response(
                {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}},
                status=500,
            )

    async def handle_update_entry(self, request: web.Request) -> web.Response:
        """PATCH /api/memory/entries/{entry_id} - Update a memory entry."""
        entry_id = request.match_info.get("entry_id", "")
        if not entry_id:
            return web.json_response(
                {"success": False, "error": {"code": "MISSING_PARAM", "message": "entry_id required"}},
                status=400,
            )
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"success": False, "error": {"code": "INVALID_JSON", "message": "Invalid JSON body"}},
                status=400,
            )
        try:
            ok = await self._manager.update_entry(
                entry_id=entry_id,
                pod_uri=data.get("pod") or None,
                topic=data.get("topic"),
                content=data.get("content"),
                entry_type=data.get("entry_type"),
                tags=data.get("tags"),
                importance=data.get("importance"),
            )
            if not ok:
                return web.json_response(
                    {"success": False, "error": {"code": "NOT_FOUND", "message": "Memory entry not found"}},
                    status=404,
                )
            return web.json_response({"success": True, "data": {"entry_id": entry_id, "updated": True}})
        except Exception as e:
            logger.error(f"Memory update error: {e}")
            return web.json_response(
                {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}},
                status=500,
            )

    async def handle_list_synapses(self, request: web.Request) -> web.Response:
        """GET /api/memory/synapses - List synapses."""
        try:
            context = request.query.get("context")
            synapses = await self._manager.list_synapses(context=context)
            return web.json_response({
                "success": True,
                "data": {
                    "synapses": [s.to_dict() for s in synapses],
                    "count": len(synapses),
                },
            })
        except Exception as e:
            logger.error(f"Synapse list error: {e}")
            return web.json_response(
                {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}},
                status=500,
            )

    async def handle_create_synapse(self, request: web.Request) -> web.Response:
        """POST /api/memory/synapses - Mount a pod."""
        try:
            data = await request.json()
            source_pod = data.get("source_pod", "")
            target_context = data.get("target_context", "")
            if not source_pod or not target_context:
                return web.json_response(
                    {"success": False, "error": {"code": "MISSING_PARAM",
                     "message": "source_pod and target_context required"}},
                    status=400,
                )
            synapse = await self._manager.create_synapse(
                source_pod_uri=source_pod,
                target_context=target_context,
                label=data.get("label", ""),
                weight=float(data.get("weight", 0.5)),
                decay_rate=float(data.get("decay_rate", 0.02)),
                filters=data.get("filters"),
                ttl=data.get("ttl"),
            )
            return web.json_response({"success": True, "data": synapse.to_dict()})
        except json.JSONDecodeError:
            return web.json_response(
                {"success": False, "error": {"code": "INVALID_JSON", "message": "Invalid JSON body"}},
                status=400,
            )
        except Exception as e:
            logger.error(f"Synapse create error: {e}")
            return web.json_response(
                {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}},
                status=500,
            )

    async def handle_delete_synapse(self, request: web.Request) -> web.Response:
        """DELETE /api/memory/synapses/{synapse_id} - Remove a synapse."""
        synapse_id = request.match_info.get("synapse_id", "")
        if not synapse_id:
            return web.json_response(
                {"success": False, "error": {"code": "MISSING_PARAM", "message": "synapse_id required"}},
                status=400,
            )
        try:
            removed = await self._manager.remove_synapse(synapse_id)
            if not removed:
                return web.json_response(
                    {"success": False, "error": {"code": "NOT_FOUND", "message": f"Synapse {synapse_id} not found"}},
                    status=404,
                )
            return web.json_response({"success": True, "data": {"synapse_id": synapse_id, "removed": True}})
        except Exception as e:
            logger.error(f"Synapse delete error: {e}")
            return web.json_response(
                {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}},
                status=500,
            )

    async def handle_list_staging(self, request: web.Request) -> web.Response:
        """GET /api/memory/staging - List pending reincarnation insights."""
        try:
            staging_path = self._manager.pod_store.boarai_dir / "memory" / "staging" / "global_insights.json"
            if not staging_path.exists():
                return web.json_response({"success": True, "data": {"insights": [], "count": 0}})
            import aiofiles
            async with aiofiles.open(staging_path, "r", encoding="utf-8") as f:
                content = await f.read()
            data = json.loads(content) if content.strip() else {"insights": []}
            insights = data.get("insights", [])
            return web.json_response({
                "success": True,
                "data": {"insights": insights, "count": len(insights)},
            })
        except Exception as e:
            logger.error(f"Staging list error: {e}")
            return web.json_response(
                {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}},
                status=500,
            )

    async def handle_confirm_staging(self, request: web.Request) -> web.Response:
        """POST /api/memory/staging/{id}/confirm - Confirm a staging insight."""
        entry_id = request.match_info.get("id", "")
        if not entry_id:
            return web.json_response(
                {"success": False, "error": {"code": "MISSING_PARAM", "message": "id required"}},
                status=400,
            )
        try:
            engine = ReincarnationEngine(self._manager)
            result = await engine.confirm_staging(entry_id, approve=True)
            if not result:
                return web.json_response(
                    {"success": False, "error": {"code": "NOT_FOUND", "message": f"Staging entry {entry_id} not found"}},
                    status=404,
                )
            return web.json_response({"success": True, "data": {"id": entry_id, "status": "confirmed"}})
        except Exception as e:
            logger.error(f"Staging confirm error: {e}")
            return web.json_response(
                {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}},
                status=500,
            )

    async def handle_reject_staging(self, request: web.Request) -> web.Response:
        """POST /api/memory/staging/{id}/reject - Reject a staging insight."""
        entry_id = request.match_info.get("id", "")
        if not entry_id:
            return web.json_response(
                {"success": False, "error": {"code": "MISSING_PARAM", "message": "id required"}},
                status=400,
            )
        try:
            engine = ReincarnationEngine(self._manager)
            result = await engine.confirm_staging(entry_id, approve=False)
            if not result:
                return web.json_response(
                    {"success": False, "error": {"code": "NOT_FOUND", "message": f"Staging entry {entry_id} not found"}},
                    status=404,
                )
            return web.json_response({"success": True, "data": {"id": entry_id, "status": "rejected"}})
        except Exception as e:
            logger.error(f"Staging reject error: {e}")
            return web.json_response(
                {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}},
                status=500,
            )

    async def handle_list_retired(self, request: web.Request) -> web.Response:
        """GET /api/memory/retired - List retired agents."""
        try:
            retired_dir = self._manager.pod_store.boarai_dir / "memory" / "retired_agents"
            if not retired_dir.exists():
                return web.json_response({"success": True, "data": {"agents": [], "count": 0}})
            agents = []
            for f in sorted(retired_dir.glob("*.json"), reverse=True):
                agents.append({
                    "file": f.name,
                    "path": str(f),
                    "size_bytes": f.stat().st_size if f.exists() else 0,
                })
            return web.json_response({
                "success": True,
                "data": {"agents": agents, "count": len(agents)},
            })
        except Exception as e:
            logger.error(f"Retired list error: {e}")
            return web.json_response(
                {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}},
                status=500,
            )

    async def handle_query_retired(self, request: web.Request) -> web.Response:
        """POST /api/memory/retired/{name}/query - Query a retired agent's memory."""
        agent_name = request.match_info.get("name", "")
        if not agent_name:
            return web.json_response(
                {"success": False, "error": {"code": "MISSING_PARAM", "message": "name required"}},
                status=400,
            )
        try:
            data = await request.json() if request.can_read_body else {}
            query = MemoryQuery(
                text=data.get("text", ""),
                limit=int(data.get("limit", 10)),
            )
            results = await self._manager.summon_retired(agent_name, query)
            return web.json_response({
                "success": True,
                "data": {"entries": results, "count": len(results)},
            })
        except json.JSONDecodeError:
            return web.json_response(
                {"success": False, "error": {"code": "INVALID_JSON", "message": "Invalid JSON body"}},
                status=400,
            )
        except Exception as e:
            logger.error(f"Retired query error: {e}")
            return web.json_response(
                {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}},
                status=500,
            )

    async def handle_consolidate(self, request: web.Request) -> web.Response:
        """POST /api/memory/consolidate - Trigger memory consolidation."""
        try:
            from boarai.core.memory import ConsolidationEngine
            engine = ConsolidationEngine(self._manager)
            data = await request.json() if request.can_read_body else {}
            pod_uri = data.get("pod_uri", "pod://global")
            report = await engine.consolidate_pod(pod_uri)
            return web.json_response({"success": True, "data": report.__dict__})
        except json.JSONDecodeError:
            return web.json_response(
                {"success": False, "error": {"code": "INVALID_JSON", "message": "Invalid JSON body"}},
                status=400,
            )
        except Exception as e:
            logger.error(f"Consolidate error: {e}")
            return web.json_response(
                {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}},
                status=500,
            )


    async def handle_export(self, request: web.Request) -> web.Response:
        """GET /api/memory/export - Export all memory pods as JSON backup."""
        try:
            data = await self._manager.export_pods()
            return web.json_response(
                {"success": True, "data": data, "count": sum(len(v) for v in data.values())},
            )
        except Exception as e:
            logger.error(f"Memory export error: {e}")
            return web.json_response(
                {"success": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}},
                status=500,
            )


_memory_handler: Optional[MemoryAPIHandler] = None


def get_memory_api_handler() -> MemoryAPIHandler:
    global _memory_handler
    if _memory_handler is None:
        _memory_handler = MemoryAPIHandler()
    return _memory_handler
