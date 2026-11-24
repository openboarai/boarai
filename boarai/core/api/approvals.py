"""Out-of-band approval API — list and resolve pending tool approvals.

Routes (registered via :func:`register_approval_routes`):

- ``GET  /api/approvals/pending``        — unresolved confirmations
- ``POST /api/approvals/{id}/decide``    — approve / reject (with grant scope)

The WebUI already approves via WS ``tool.confirm``; these endpoints serve
clients without a WS channel (CLI, scripts, mobile, curl) and make the
pending queue inspectable — previously the ONLY way to see a pending
approval was the in-page popup.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from aiohttp import web

logger = logging.getLogger(__name__)


def register_approval_routes(app: web.Application) -> None:
    app.router.add_get("/api/approvals/pending", handle_list_pending)
    app.router.add_post("/api/approvals/{confirmation_id}/decide", handle_decide)


def _confirm_gate() -> Any:
    """The RunManager's ToolConfirmGate (single authority for pending state)."""
    from boarai.core.session import get_run_manager

    rm = get_run_manager()
    return getattr(rm, "_confirm", None)


def _json(payload: Dict[str, Any], status: int = 200) -> web.Response:
    return web.json_response(payload, status=status)


async def handle_list_pending(request: web.Request) -> web.Response:
    gate = _confirm_gate()
    if gate is None:
        return _json({"ok": True, "data": {"pending": []}})
    try:
        pending = gate.list_pending()
        return _json({"ok": True, "data": {"pending": pending, "count": len(pending)}})
    except Exception:  # noqa: BLE001 — details stay in the server log
        logger.exception("List pending approvals failed")
        return _json(
            {"ok": False, "error": "INTERNAL", "message": "internal error"},
            status=500,
        )


async def handle_decide(request: web.Request) -> web.Response:
    confirmation_id = request.match_info.get("confirmation_id", "")
    gate = _confirm_gate()
    if gate is None:
        return _json(
            {"ok": False, "error": "UNAVAILABLE", "message": "approval gate not running"},
            status=503,
        )
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    approved = bool(body.get("approved", body.get("approve", False)))
    grant_scope = body.get("grant_scope", "once")
    if grant_scope not in ("once", "session", "folder"):
        grant_scope = "once"
    run_id = str(body.get("run_id", ""))

    # Find the run id when the caller does not know it (pending list has it).
    if not run_id:
        for entry in gate.list_pending():
            if entry.get("confirmation_id") == confirmation_id:
                run_id = entry.get("run_id", "")
                break

    resolved = gate.confirm_tool(
        run_id, confirmation_id, approved, grant_scope=grant_scope
    )
    if not resolved:
        return _json(
            {"ok": False, "error": "NOT_FOUND",
             "message": f"no pending confirmation {confirmation_id}"},
            status=404,
        )
    return _json(
        {"ok": True, "data": {"confirmation_id": confirmation_id, "approved": approved,
                              "grant_scope": grant_scope}}
    )
