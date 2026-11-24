"""
Run domain models — status enum and in-memory Run record.

Separated from RunManager so lifecycle orchestration stays in run_manager.py
while status/serialization remain reusable (API, tests, cleanup).
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Any, Dict, List, Optional

class RunStatus(str, Enum):
    """Run execution status."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class Run:
    """Represents a single conversation run (server-side, client-independent)."""

    def __init__(
        self,
        run_id: str,
        session_id: str,
        task_id: Optional[str] = None,
    ):
        self.run_id = run_id
        self.session_id = session_id
        self.task_id = task_id  # Link to Task (for AI task execution)
        self.status = RunStatus.QUEUED
        self.created_at = time.time()
        self.updated_at = time.time()
        self.completed_at: Optional[float] = None
        self.task: Optional[asyncio.Task] = None
        self.error: Optional[str] = None
        self.model: Optional[str] = None
        self.full_response: str = ""
        self.full_thinking: str = ""  # Accumulated thinking content (across all turns)
        # Live text of the turn currently streaming (reset at every turn start).
        # Reconnecting clients resume from THIS, not from full_response: the
        # latter is cumulative over the whole run, so splatting it onto the
        # in-progress bubble duplicates every earlier turn into one message.
        self.current_turn_text: str = ""
        self.completion_future: Optional[asyncio.Future] = None
        self._in_tool_execution: bool = False  # True while asyncio.gather of tools runs
        self._cancelling: bool = False  # Graceful cancellation signal
        self._terminal_event_emitted: bool = False
        self.cancel_reason: Optional[str] = None
        self._agent_name: str = "default"
        # Run kind: "chat" (normal conversation) | "skill_authoring" (end-of-session
        # skill extraction/improvement). A non-chat run must not trigger skill
        # evolution again on completion, or authoring would recurse into itself.
        self.kind: str = "chat"
        # Model provenance: "default" | "native" | "vision_sidecar" | "text_only".
        # Lets UI/observability show how image attachments were routed
        # (three-state capability routing) for an image-bearing run.
        self._model_source: str = "default"
        # Set by the executor's strip-and-retry fuse when the provider
        # REJECTED image pixels despite their declaration (a lying
        # capability): later turns and tool-side reads must stop sending
        # pixels (omission notes instead) instead of repeating the 400
        # round-trip every turn.
        self._vision_pixels_refused: bool = False
        # Transient wire-only image messages queued by tools (e.g. file_read
        # on an image while the main model is vision-capable). Consumed by
        # _build_wire_history on every later turn of THIS run; never
        # persisted to session history and never serialized to the API.
        self._pending_wire_images: List[Dict[str, Any]] = []
        # Run budget snapshot (None when no budget is configured), so the API
        # can show "used / limit" and why the run stopped.
        self._budget: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert run to dict for API responses."""
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "status": self.status.value,
            "kind": self.kind,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "cancel_reason": self.cancel_reason,
            "model": self.model,
            "model_source": self._model_source,
            **({"budget": self._budget} if self._budget else {}),
        }
