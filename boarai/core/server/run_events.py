"""WebSocket payload builders for terminal conversation run events."""

from collections.abc import Mapping
from typing import Any

TERMINAL_RUN_EVENT_STATUS = {
    "run.completed": "completed",
    "run.error": "failed",
    "run.cancelled": "cancelled",
}

def build_run_status_payload(event_type: str, event: Any) -> dict[str, str]:
    """Build the browser-facing status payload for a terminal run event."""
    status = TERMINAL_RUN_EVENT_STATUS.get(event_type)
    if status is None:
        raise ValueError(f"Unsupported terminal run event: {event_type}")

    source_payload = getattr(event, "payload", event)
    payload = source_payload if isinstance(source_payload, Mapping) else {}
    status_payload = {
        "event": event_type,
        "session_id": str(payload.get("session_id") or ""),
        "run_id": str(payload.get("run_id") or ""),
        "status": status,
    }

    if status == "failed":
        status_payload["error"] = str(payload.get("error") or "Run failed")
    elif status == "cancelled":
        status_payload["reason"] = str(payload.get("reason") or "user_cancelled")

    return status_payload

async def forward_terminal_run_status(server_manager: Any, event_type: str, event: Any) -> bool:
    """Broadcast one terminal run status when a unified server is available."""
    server = getattr(server_manager, "server", None)
    if server is None:
        return False

    await server.broadcast({
        "type": "session.run.status",
        "payload": build_run_status_payload(event_type, event),
    })
    return True
