"""
Trash API endpoints for BoarAi server.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from aiohttp import web

from boarai.core.trash import get_trash_manager

logger = logging.getLogger(__name__)


class TrashAPIHandler:
    """Handles trash/recycle bin API requests."""

    def __init__(self):
        self._manager: Optional[Any] = None

    @property
    def manager(self):
        if self._manager is None:
            self._manager = get_trash_manager()
        return self._manager

    async def handle_list(self, request: web.Request) -> web.Response:
        """GET /api/trash — list trash entries."""
        try:
            item_type = request.query.get("type", None)
            limit = min(int(request.query.get("limit", 50)), 200)
            entries = self.manager.list_trash(item_type=item_type, limit=limit)
            # Backfill session titles from trashed session files (best-effort,
            # off the event loop) so the UI can show session names.
            await asyncio.to_thread(self.manager.backfill_session_titles, entries)
            return web.json_response({
                "success": True,
                "data": {
                    "entries": entries,
                    "stats": self.manager.get_stats(),
                },
            })
        except Exception as e:
            logger.error(f"Trash list error: {e}")
            return web.json_response(
                {"success": False, "error": {"message": str(e)}},
                status=500,
            )

    async def handle_get(self, request: web.Request) -> web.Response:
        """GET /api/trash/{trash_id} — get single trash entry."""
        trash_id = request.match_info.get("trash_id", "")
        entry = self.manager.get_trash(trash_id)
        if not entry:
            return web.json_response(
                {"success": False, "error": {"message": "Trash entry not found"}},
                status=404,
            )
        await asyncio.to_thread(self.manager.backfill_session_titles, [entry])
        return web.json_response({"success": True, "data": entry})

    async def handle_restore(self, request: web.Request) -> web.Response:
        """POST /api/trash/{trash_id}/restore — restore a trashed item."""
        trash_id = request.match_info.get("trash_id", "")
        entry = self.manager.get_trash(trash_id)
        if not entry:
            return web.json_response(
                {"success": False, "error": {"message": "Trash entry not found"}},
                status=404,
            )
        if entry.get("type") == "memory":
            # Memory entries are stored as JSON payloads; restore via memory manager.
            payload = await self.manager.read_trash_payload(trash_id)
            if not payload:
                return web.json_response(
                    {"success": False, "error": {"message": "Trashed memory payload missing"}},
                    status=400,
                )
            from boarai.core.memory import get_memory_manager
            mm = get_memory_manager()
            if not mm:
                return web.json_response(
                    {"success": False, "error": {"message": "Memory manager unavailable"}},
                    status=500,
                )
            ok = await mm.restore_entry(payload)
            if ok:
                # Clean up the trash copy + index entry
                await self.manager.purge(trash_id)
        else:
            # Reset distill mode on restore (only if not already distilled)
            await self.manager.reset_distill_mode(trash_id)
            ok = await self.manager.restore(trash_id)
        if not ok:
            return web.json_response(
                {"success": False, "error": {"message": "Restore failed"}},
                status=400,
            )
        return web.json_response({"success": True, "message": "Restored"})

    async def handle_distill(self, request: web.Request) -> web.Response:
        """POST /api/trash/{trash_id}/distill — manually trigger distillation on a trashed item."""
        trash_id = request.match_info.get("trash_id", "")
        entry = self.manager.get_trash(trash_id)
        if not entry:
            return web.json_response(
                {"success": False, "error": {"message": "Trash entry not found"}},
                status=404,
            )
        if entry.get("distill_mode") == "distilled":
            return web.json_response(
                {"success": False, "error": {"message": "Already distilled"}},
                status=400,
            )
        if entry.get("type") not in ("session", "agent"):
            return web.json_response(
                {"success": False, "error": {"message": "Only sessions and agents can be distilled"}},
                status=400,
            )

        # Mark as pending (distill worker will pick it up on next idle scan)
        async with self.manager._lock:
            e = self.manager._entries.get(trash_id)
            if e:
                e.distill_mode = "pending"
                await self.manager._save_index()
        return web.json_response({
            "success": True,
            "message": "Distillation queued",
            "distill_mode": "pending",
        })

    async def handle_purge_one(self, request: web.Request) -> web.Response:
        """DELETE /api/trash/{trash_id} — permanently delete one trash entry."""
        trash_id = request.match_info.get("trash_id", "")
        ok = await self.manager.purge(trash_id)
        if not ok:
            return web.json_response(
                {"success": False, "error": {"message": "Purge failed"}},
                status=400,
            )
        return web.json_response({"success": True, "message": "Purged"})

    async def handle_purge_all(self, request: web.Request) -> web.Response:
        """DELETE /api/trash — permanently delete all trash entries."""
        count = await self.manager.purge_all()
        return web.json_response({
            "success": True,
            "message": f"Purged {count} entries",
            "count": count,
        })

    async def handle_cleanup(self, request: web.Request) -> web.Response:
        """POST /api/trash/cleanup — manually trigger expired cleanup."""
        from boarai.core.trash.trash_manager import TrashManager
        mgr = self.manager
        if isinstance(mgr, TrashManager):
            count = await mgr._auto_cleanup()
            return web.json_response({
                "success": True,
                "message": f"Cleaned up {count} expired entries",
            })
        return web.json_response(
            {"success": False, "error": {"message": "Trash manager not available"}},
            status=500,
        )

    async def handle_snapshots(self, request: web.Request) -> web.Response:
        """GET /api/trash/snapshots — list file snapshots."""
        try:
            limit = min(int(request.query.get("limit", 50)), 200)
            snapshots = await self.manager.list_snapshots(limit=limit)
            return web.json_response({"success": True, "data": {"snapshots": snapshots}})
        except Exception as e:
            logger.error(f"Snapshots list error: {e}")
            return web.json_response(
                {"success": False, "error": {"message": str(e)}},
                status=500,
            )


# Singleton
_trash_api_handler: Optional[TrashAPIHandler] = None


def get_trash_api_handler() -> TrashAPIHandler:
    global _trash_api_handler
    if _trash_api_handler is None:
        _trash_api_handler = TrashAPIHandler()
    return _trash_api_handler
