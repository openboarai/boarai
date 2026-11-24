"""
Session API handlers for BoarAi system.

This module provides system-level HTTP and WebSocket endpoints for session management.
Can be used by any channel extension (WebUI, CLI, etc.).
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from aiohttp import web

from boarai.core.event import EventType, get_event_bus, publish_event
from boarai.core.session import Session, get_run_manager, get_session_manager
from boarai.core.session.service import SessionService, update_changes
from boarai.core.runtime import parse_json_object, spawn

logger = logging.getLogger(__name__)


class SessionAPIHandler:
    """Handles session API requests."""

    def __init__(self):
        """Wire session/run managers, event bus, and WebSocket client sets."""
        self._session_manager = get_session_manager()
        self._run_manager = get_run_manager()
        self._event_bus = get_event_bus()
        self._ws_clients: Dict[str, set] = {}  # session_id -> set of ws clients
        self._all_ws_clients: set = set()  # All connected WS clients
        # One domain implementation for both channels (HTTP + WS). Session
        # defaults live in core.session.service so the message-channel host
        # stamps the same model/agent/workspace onto its own sessions.
        self._service = SessionService(self._session_manager)

    # ==================== HTTP Handlers ====================

    async def handle_list_sessions(self, request: web.Request) -> web.Response:
        """GET /api/sessions - List sessions with pagination and filtering."""
        try:
            # Parse query parameters
            limit = int(request.query.get("limit", 50))
            offset = int(request.query.get("offset", 0))
            sort = request.query.get("sort", "updated_at")
            order = request.query.get("order", "desc")
            status = request.query.get("status")
            search = request.query.get("search")
            source = request.query.get("source")

            # Validate parameters
            if limit < 1 or limit > 100:
                return self._error_response(400, "INVALID_PARAMETER", "limit must be between 1 and 100")
            if offset < 0:
                return self._error_response(400, "INVALID_PARAMETER", "offset must be non-negative")
            if sort not in ("created_at", "updated_at", "title"):
                return self._error_response(400, "INVALID_PARAMETER", "sort must be one of: created_at, updated_at, title")
            if order not in ("asc", "desc"):
                return self._error_response(400, "INVALID_PARAMETER", "order must be one of: asc, desc")

            # Get sessions
            result = await self._session_manager.list_sessions(
                limit=limit,
                offset=offset,
                sort=sort,
                order=order,
                status=status,
                search=search,
                source=source,
            )

            return self._success_response(result)

        except ValueError as e:
            return self._error_response(400, "INVALID_PARAMETER", str(e))
        except Exception as e:
            logger.error(f"Error listing sessions: {e}")
            return self._error_response(500, "INTERNAL_ERROR", "Failed to list sessions")

    async def handle_create_session(self, request: web.Request) -> web.Response:
        """POST /api/sessions - Create a new session."""
        try:
            data = await request.json()
            session = await self._service.create_session(
                title=data.get("title", "New Session"),
                metadata=data.get("metadata", {}),
                source=data.get("source", "api"),
                agent_name=data.get("agent_name", "default"),
            )

            # Return response first, then broadcast in background
            resp = self._success_response(session.to_summary_dict(), status=201)
            spawn(self._safe_broadcast("session.created", session.to_summary_dict()))
            return resp

        except json.JSONDecodeError:
            return self._error_response(400, "INVALID_JSON", "Request body must be valid JSON")
        except Exception as e:
            logger.error(f"Error creating session: {e}")
            return self._error_response(500, "INTERNAL_ERROR", "Failed to create session")

    async def handle_get_session(self, request: web.Request) -> web.Response:
        """GET /api/sessions/{session_id} - Get session details."""
        try:
            session_id = request.match_info["session_id"]
            include_messages = request.query.get("include_messages", "false").lower() == "true"
            message_limit = int(request.query.get("message_limit", 50))

            session = await self._session_manager.get_session(session_id)
            if not session:
                return self._error_response(404, "SESSION_NOT_FOUND", f"Session {session_id} not found")

            if include_messages:
                result = session.to_dict()
                # Apply message limit
                if message_limit and len(result["messages"]) > message_limit:
                    result["messages"] = result["messages"][-message_limit:]
            else:
                result = session.to_summary_dict()

            return self._success_response(result)

        except ValueError as e:
            return self._error_response(400, "INVALID_PARAMETER", str(e))
        except Exception as e:
            logger.error(f"Error getting session: {e}")
            return self._error_response(500, "INTERNAL_ERROR", "Failed to get session")

    async def handle_update_session(self, request: web.Request) -> web.Response:
        """PUT /api/sessions/{session_id} - Update session."""
        try:
            session_id = request.match_info["session_id"]
            data = await request.json()

            session = await self._service.update_session(session_id, data)
            if not session:
                return self._error_response(404, "SESSION_NOT_FOUND", f"Session {session_id} not found")

            resp = self._success_response(session.to_summary_dict())
            # Same field set as the WS session.update broadcast — the constant
            # in core.session.service is the single source for both.
            spawn(self._safe_broadcast("session.updated", {
                "session_id": session_id,
                "changes": update_changes(data),
            }))
            return resp

        except json.JSONDecodeError:
            return self._error_response(400, "INVALID_JSON", "Request body must be valid JSON")
        except Exception as e:
            logger.error(f"Error updating session: {e}")
            return self._error_response(500, "INTERNAL_ERROR", "Failed to update session")

    async def handle_put_workspace(self, request: web.Request) -> web.Response:
        """PUT /api/sessions/{session_id}/workspace — set or clear working directory."""
        try:
            session_id = request.match_info["session_id"]
            data = await request.json()
            raw = data.get("path", None)

            session = await self._session_manager.get_session(session_id)
            if not session:
                return self._error_response(
                    404, "SESSION_NOT_FOUND", f"Session {session_id} not found"
                )

            # Block while run is active
            try:
                active = self._run_manager.get_active_run_for_session(session_id)
                if active:
                    return self._error_response(
                        409,
                        "RUN_ACTIVE",
                        "Cannot change workspace while a run is active",
                    )
            except Exception:
                pass

            if raw is None or raw == "":
                session.workspace_path = ""
                # Insert system message for workspace change
                session.add_message("system", "[BoarAi_System] Workspace: (default)")
                await self._session_manager.save_session(session_id)
                spawn(
                    self._safe_broadcast(
                        "session.updated",
                        {
                            "session_id": session_id,
                            "changes": {"workspace_path": ""},
                        },
                    )
                )
                from boarai.core.session.workspace import resolve_session_workspace

                return self._success_response(
                    {
                        "session_id": session_id,
                        "workspace_path": "",
                        "effective": str(resolve_session_workspace(session)),
                    }
                )

            from boarai.core.session.workspace import validate_workspace_path, resolve_session_workspace

            # allow_outside defaults to False: workspace stays inside the
            # configured sandbox unless the client explicitly opts out
            # (PathGuard still enforces reads everywhere).
            allow_outside = bool(data.get("allow_outside_sandbox", False))
            ok, resolved, err = validate_workspace_path(
                str(raw), allow_outside_sandbox=allow_outside
            )
            if not ok:
                return self._error_response(400, "INVALID_WORKSPACE", err or "Invalid path")

            session.workspace_path = resolved
            # Insert system message for workspace change
            session.add_message("system", f"[BoarAi_System] Workspace: {resolved}")
            await self._session_manager.save_session(session_id)
            spawn(
                self._safe_broadcast(
                    "session.updated",
                    {
                        "session_id": session_id,
                        "changes": {"workspace_path": resolved},
                    },
                )
            )
            return self._success_response(
                {
                    "session_id": session_id,
                    "workspace_path": resolved,
                    "effective": str(resolve_session_workspace(session)),
                }
            )

        except json.JSONDecodeError:
            return self._error_response(400, "INVALID_JSON", "Request body must be valid JSON")
        except Exception as e:
            logger.error("Error setting workspace: %s", e)
            return self._error_response(500, "INTERNAL_ERROR", "Failed to set workspace")

    async def handle_delete_session(self, request: web.Request) -> web.Response:
        """DELETE /api/sessions/{session_id} - Delete session."""
        try:
            session_id = request.match_info["session_id"]
            distill = request.query.get("distill", "").lower() == "true"

            # Delete session (with optional distillation flag)
            deleted = await self._service.delete_session(session_id, distill=distill)
            if not deleted:
                return self._error_response(404, "SESSION_NOT_FOUND", f"Session {session_id} not found")

            data = {"session_id": session_id}
            if distill:
                data["distill"] = "pending"
            resp = self._success_response(data, message="Session deleted")
            spawn(self._safe_broadcast("session.deleted", {"session_id": session_id}))
            return resp

        except Exception as e:
            logger.error(f"Error deleting session: {e}")
            return self._error_response(500, "INTERNAL_ERROR", "Failed to delete session")

    async def handle_get_messages(self, request: web.Request) -> web.Response:
        """GET /api/sessions/{session_id}/messages - Get session messages."""
        try:
            session_id = request.match_info["session_id"]
            limit = int(request.query.get("limit", 50))
            offset = int(request.query.get("offset", 0))
            before = request.query.get("before")  # ISO timestamp (legacy cursor)
            since_seq = self._parse_seq(request.query.get("since_seq"))
            before_seq = self._parse_seq(request.query.get("before_seq"))

            session = await self._session_manager.get_session(session_id)
            if not session:
                return self._error_response(404, "SESSION_NOT_FOUND", f"Session {session_id} not found")

            # Legacy ISO-timestamp cursor: pre-filter before applying seq cursors.
            messages = session.messages
            if before:
                try:
                    before_dt = datetime.fromisoformat(before)
                    messages = [m for m in messages if m.timestamp < before_dt]
                except ValueError:
                    return self._error_response(400, "INVALID_PARAMETER", "Invalid timestamp format for 'before'")

            result = self._build_messages_payload(
                session, messages,
                limit=limit, offset=offset,
                since_seq=since_seq, before_seq=before_seq,
            )
            return self._success_response(result)

        except ValueError as e:
            return self._error_response(400, "INVALID_PARAMETER", str(e))
        except Exception as e:
            logger.error(f"Error getting messages: {e}")
            return self._error_response(500, "INTERNAL_ERROR", "Failed to get messages")

    async def handle_get_changes(self, request: web.Request) -> web.Response:
        """GET /api/sessions/{session_id}/changes - Per-run file-change log."""
        try:
            session_id = request.match_info["session_id"]
            limit = min(int(request.query.get("limit", 50)), 200)
            from boarai.core.session.artifact import get_artifact_store

            store = get_artifact_store()
            records = await asyncio.to_thread(store.list_run_changes, session_id, limit)
            return self._success_response({"changes": records})
        except ValueError as e:
            return self._error_response(400, "INVALID_PARAMETER", str(e))
        except Exception as e:
            logger.error(f"Error getting session changes: {e}")
            return self._error_response(500, "INTERNAL_ERROR", "Failed to get changes")

    async def handle_revert_changes(self, request: web.Request) -> web.Response:
        """POST /api/sessions/{session_id}/changes/{run_id}/revert."""
        try:
            session_id = request.match_info["session_id"]
            run_id = request.match_info["run_id"]
            path = None
            try:
                body = await request.json()
                if isinstance(body, dict) and body.get("path"):
                    path = str(body.get("path"))
            except Exception:
                path = None
            from boarai.core.session.run.revert import revert_run

            result = await revert_run(session_id, run_id, path)
            if not result.get("ok", False) and result.get("error"):
                return self._error_response(400, "REVERT_FAILED", result["error"])
            return self._success_response(result)
        except Exception as e:
            logger.error(f"Error reverting run changes: {e}")
            return self._error_response(500, "INTERNAL_ERROR", "Failed to revert")

    async def handle_revert_all_changes(self, request: web.Request) -> web.Response:
        """POST /api/sessions/{session_id}/changes/revert-all."""
        try:
            session_id = request.match_info["session_id"]
            path = None
            try:
                body = await request.json()
                if isinstance(body, dict) and body.get("path"):
                    path = str(body.get("path"))
            except Exception:
                path = None
            from boarai.core.session.run.revert import revert_session

            result = await revert_session(session_id, paths=[path] if path else None)
            if not result.get("ok", False) and result.get("error"):
                return self._error_response(400, "REVERT_FAILED", result["error"])
            # Session-scoped mirror of the change-log mutation (HTTP half is
            # this response): subscribed clients reload the changes panel.
            spawn(self._safe_broadcast_to_session(session_id, "session.changes.cleared", {
                "session_id": session_id,
                "reason": "reverted",
                "stats": result.get("stats", {}),
                **({"path": path} if path else {}),
            }))
            return self._success_response(result)
        except Exception as e:
            logger.error(f"Error reverting session changes: {e}")
            return self._error_response(500, "INTERNAL_ERROR", "Failed to revert session")

    async def handle_accept_changes(self, request: web.Request) -> web.Response:
        """POST /api/sessions/{session_id}/changes/accept."""
        try:
            session_id = request.match_info["session_id"]
            path = None
            try:
                body = await request.json()
                if isinstance(body, dict) and body.get("path"):
                    path = str(body.get("path"))
            except Exception:
                path = None
            from boarai.core.session.run.revert import accept_session

            result = await accept_session(session_id, paths=[path] if path else None)
            if not result.get("ok", False) and result.get("error"):
                return self._error_response(400, "ACCEPT_FAILED", result["error"])
            spawn(self._safe_broadcast_to_session(session_id, "session.changes.cleared", {
                "session_id": session_id,
                "reason": "accepted",
                **({"path": path} if path else {}),
            }))
            return self._success_response(result)
        except Exception as e:
            logger.error(f"Error accepting session changes: {e}")
            return self._error_response(500, "INTERNAL_ERROR", "Failed to accept changes")

    @staticmethod
    def _parse_seq(raw) -> Optional[int]:
        """Parse an optional seq cursor query/WS param into an int or None."""
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _round_align_window(msgs: List[Any], window: List[Any]) -> List[Any]:
        """Extend a suffix window back to the user message opening its round."""
        if not window or not msgs or len(window) >= len(msgs):
            return window
        if getattr(window[0], "role", None) == "user":
            return window
        start = len(msgs) - len(window)
        i = start
        while i > 0 and getattr(msgs[i], "role", None) != "user":
            i -= 1
        if getattr(msgs[i], "role", None) == "user":
            return msgs[i:]
        return window

    def _build_messages_payload(
        self,
        session,
        messages,
        limit: Optional[int] = 50,
        offset: int = 0,
        since_seq: Optional[int] = None,
        before_seq: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Build the canonical message-window response payload."""
        msgs = list(messages)
        if since_seq is not None:
            # seq==0 messages (turn0 system injects written before seq
            # allocation existed) are ALWAYS included in incremental sync:
            # they render at the top of the stream, and clients dedupe by
            # message_id, so the repeat is harmless — dropping them would
            # lose the inject for incremental-only clients forever.
            msgs = [m for m in msgs if m.seq > since_seq or m.seq == 0]
        if before_seq is not None:
            msgs = [m for m in msgs if m.seq < before_seq or m.seq == 0]

        total = len(msgs)

        if since_seq is not None:
            # Incremental sync: return ALL newer messages (never truncate). The
            # client advances its watermark to the session max seq, so dropping
            # any would lose them permanently. Incremental bursts are small.
            window = msgs
            has_more = False
        elif before_seq is not None:
            # Backward pagination: the `limit` messages immediately before cursor.
            window = msgs[-limit:] if (limit and len(msgs) > limit) else msgs
            window = self._round_align_window(msgs, window)
            has_more = len(window) < total
        else:
            # Default / legacy: latest-N window (chat UX — newest messages first,
            # matching the WS session.get and handle_get_session behavior).
            # `offset` is retained for backward compat but no longer drives
            # pagination (frontend uses before_seq for older history).
            window = msgs[-limit:] if (limit and len(msgs) > limit) else msgs
            window = self._round_align_window(msgs, window)
            has_more = len(window) < total

        return {
            "session_id": session.session_id,
            "messages": [m.to_dict() for m in window],
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "watermark_seq": session.watermark_seq,
        }

    async def _run_on_message_hook(
        self, session_id: str, role: str, content: str
    ) -> tuple:
        """Run the core.session.on_message hook (blockable)."""
        try:
            from boarai.core.extension import get_hook_registry

            res = await get_hook_registry().trigger(
                "core.session.on_message",
                {"session_id": session_id, "role": role, "content": content},
                timeout=2.0,
            )
            if res is None:
                return (False, content, None)
            if res.blocked:
                return (True, content, res.reason)
            if isinstance(res.data, dict):
                new_content = res.data.get("content")
                if isinstance(new_content, str) and new_content:
                    return (False, new_content, None)
            return (False, content, None)
        except Exception:  # noqa: BLE001 — hooks must never break messaging
            return (False, content, None)

    async def _publish_session_message(
        self, session_id: str, message: Any, role: str, content: str,
        metadata: Any = None,
    ) -> None:
        """Publish SESSION_MESSAGE on the event bus (fire-and-forget)."""
        try:
            spawn(publish_event(
                EventType.SESSION_MESSAGE,
                {
                    "session_id": session_id,
                    "message_id": f"msg_{int(message.timestamp.timestamp() * 1000)}",
                    "role": role,
                    "content": content,
                    "timestamp": message.timestamp.isoformat(),
                    "metadata": metadata or {},
                    "seq": getattr(message, "seq", 0),
                },
                "api_session",
            ))
        except Exception:  # noqa: BLE001
            pass

    async def publish_command_turn(
        self, session_id: str, turn: Any, raw_text: str, title: Any = None,
    ) -> None:
        """Mirror one persisted command turn to every interested consumer."""
        await self._publish_session_message(
            session_id, turn.user_message, "user", raw_text, turn.user_meta,
        )
        await self._publish_session_message(
            session_id, turn.reply_message, "assistant", turn.reply_text, turn.reply_meta,
        )
        for _m, _role, _text, _meta in (
            (turn.user_message, "user", raw_text, turn.user_meta),
            (turn.reply_message, "assistant", turn.reply_text, turn.reply_meta),
        ):
            spawn(self._safe_broadcast_to_session(
                session_id, "session.message.new", {
                    "session_id": session_id,
                    "message_id": f"msg_{int(_m.timestamp.timestamp() * 1000)}",
                    "role": _role,
                    "content": _text,
                    "timestamp": _m.timestamp.isoformat(),
                    "metadata": _meta,
                    "seq": getattr(_m, "seq", 0),
                },
            ))
        await self._broadcast_event("session.updated", {
            "session_id": session_id,
            "changes": {"title": title} if turn.titled else {},
        })

    async def handle_send_message(self, request: web.Request) -> web.Response:
        """POST /api/sessions/{session_id}/messages - Send a message."""
        try:
            session_id = request.match_info["session_id"]
            data = await request.json()

            role = data.get("role", "user")
            content = data.get("content", "")
            metadata = data.get("metadata", {})

            if not content:
                return self._error_response(400, "INVALID_PARAMETER", "content is required")
            # External clients must not forge system messages: they would be
            # injected into the LLM context (prompt-injection surface).
            if role not in ("user", "assistant"):
                return self._error_response(400, "INVALID_PARAMETER", "role must be one of: user, assistant")

            # Get session
            session = await self._session_manager.get_session(session_id)
            if not session:
                return self._error_response(404, "SESSION_NOT_FOUND", f"Session {session_id} not found")

            # --- V2 hook: session.on_message (blockable) ---
            blocked, content, hook_reason = await self._run_on_message_hook(
                session_id, role, content
            )
            if blocked:
                return self._error_response(
                    403, "MESSAGE_BLOCKED",
                    hook_reason or "Message blocked by extension hook",
                )

            # Add message (a first user turn opens with the context inject)
            if role == "user":
                session.ensure_session_capability_inject()
            message = session.add_message(role, content, metadata)

            # Auto-generate title from first user message (ignore leading system msgs)
            titled = False
            if role == "user":
                titled = session.maybe_auto_title_from_user(content)

            # Save to storage
            await self._session_manager.save_session(session_id)

            resp = self._success_response({
                "session_id": session_id,
                "role": role,
                "content": content,
                "timestamp": message.timestamp.isoformat(),
            }, status=201)

            # --- EventBus broadcast (SESSION_MESSAGE) for channels/analytics ---
            await self._publish_session_message(session_id, message, role, content, metadata)

            # Messages are pushed to session subscribers only (same semantics as
            # the WS session.message path) — list pages refresh via session.updated.
            spawn(self._safe_broadcast_to_session(session_id, "session.message.new", {
                "session_id": session_id,
                "message_id": f"msg_{int(message.timestamp.timestamp() * 1000)}",
                "role": role,
                "content": content,
                "timestamp": message.timestamp.isoformat(),
                "metadata": metadata,
                "seq": getattr(message, "seq", 0),
            }))
            if titled:
                spawn(self._safe_broadcast("session.updated", {
                    "session_id": session_id,
                    "changes": {"title": session.title},
                }))
            else:
                spawn(self._safe_broadcast("session.updated", {
                    "session_id": session_id,
                    "changes": {},
                }))

            return resp

        except json.JSONDecodeError:
            return self._error_response(400, "INVALID_JSON", "Request body must be valid JSON")
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return self._error_response(500, "INTERNAL_ERROR", "Failed to send message")

    async def handle_clear_messages(self, request: web.Request) -> web.Response:
        """POST /api/sessions/{session_id}/clear - Clear session messages."""
        try:
            session_id = request.match_info["session_id"]

            session = await self._session_manager.get_session(session_id)
            if not session:
                return self._error_response(404, "SESSION_NOT_FOUND", f"Session {session_id} not found")

            # Clear messages
            session.clear_messages()

            # Save to storage
            await self._session_manager.save_session(session_id)

            resp = self._success_response({"session_id": session_id}, message="Messages cleared")
            # Session-scoped broadcast (not global), matching the spec for
            # session.message.new / session.messages_cleared.
            spawn(self._safe_broadcast_to_session(session_id, "session.messages_cleared", {"session_id": session_id}))
            return resp

        except Exception as e:
            logger.error(f"Error clearing messages: {e}")
            return self._error_response(500, "INTERNAL_ERROR", "Failed to clear messages")

    async def handle_update_message(self, request: web.Request) -> web.Response:
        """PUT /api/sessions/{session_id}/messages/{message_id} - Edit the latest user message."""
        try:
            session_id = request.match_info["session_id"]
            message_id = request.match_info["message_id"]
            data = await request.json()
            content = (data.get("content") or "").strip()

            if not content:
                return self._error_response(400, "INVALID_PARAMETER", "content is required")

            session = await self._session_manager.get_session(session_id)
            if not session:
                return self._error_response(404, "SESSION_NOT_FOUND", f"Session {session_id} not found")

            # Only the latest user message can be edited
            last_user = next((m for m in reversed(session.messages) if m.role == "user"), None)
            if last_user is None or last_user.message_id != message_id:
                return self._error_response(
                    400, "EDIT_NOT_LATEST",
                    "Only the latest user message can be edited",
                )

            # Is it the first user message? (re-title the session when editing it)
            first_user = next((m for m in session.messages if m.role == "user"), None)
            is_first = first_user is not None and first_user.message_id == message_id

            kept = session.truncate_messages(message_id)
            if kept is None:
                return self._error_response(404, "MESSAGE_NOT_FOUND", f"Message {message_id} not found")

            # Rewrite the user message with the edited content
            last_user.content = content

            # Re-title from the edited content when this is the first user message
            titled = False
            if is_first:
                text = session._user_text_for_title(content)
                new_title = session.generate_title(text) if text else ""
                if new_title and new_title.strip() not in session._DEFAULT_TITLES:
                    session.title = new_title
                    titled = True

            await self._session_manager.save_session(session_id)

            resp = self._success_response({
                "session_id": session_id,
                "message_id": message_id,
                "content": content,
                "message_count": session.message_count,
                "title": session.title if titled else None,
            }, message="Message updated")

            # Session subscribers drop the truncated tail
            spawn(self._safe_broadcast_to_session(session_id, "session.messages_truncated", {
                "session_id": session_id,
                "keep_message_id": message_id,
            }))
            if titled:
                spawn(self._safe_broadcast("session.updated", {
                    "session_id": session_id,
                    "changes": {"title": session.title},
                }))
            return resp

        except json.JSONDecodeError:
            return self._error_response(400, "INVALID_JSON", "Request body must be valid JSON")
        except Exception as e:
            logger.error(f"Error updating message: {e}")
            return self._error_response(500, "INTERNAL_ERROR", "Failed to update message")

    async def handle_archive_session(self, request: web.Request) -> web.Response:
        """POST /api/sessions/{session_id}/archive - Archive session."""
        try:
            session_id = request.match_info["session_id"]

            session = await self._session_manager.update_session_fields(
                session_id=session_id,
                archived=True,
            )

            if not session:
                return self._error_response(404, "SESSION_NOT_FOUND", f"Session {session_id} not found")

            # Save to storage
            await self._session_manager.save_session(session_id)

            resp = self._success_response(session.to_summary_dict())
            spawn(self._safe_broadcast("session.archived", {"session_id": session_id}))
            return resp

        except Exception as e:
            logger.error(f"Error archiving session: {e}")
            return self._error_response(500, "INTERNAL_ERROR", "Failed to archive session")

    async def handle_compress(self, request: web.Request) -> web.Response:
        """POST /api/sessions/{session_id}/compress — Manual compression trigger."""
        try:
            session_id = request.match_info["session_id"]
            session = await self._session_manager.get_session(session_id)
            if not session:
                return self._error_response(404, "SESSION_NOT_FOUND", f"Session not found: {session_id}")

            if len(session.messages) < 4:
                return self._error_response(400, "TOO_FEW_MESSAGES",
                                            "Need at least 4 messages to compress")

            pre_count = session.compression_count
            pre_msgs = len(session.messages)

            from boarai.core.session.run_manager import get_run_manager
            rm = get_run_manager()
            await rm._on_idle_compress(session_id)

            session = await self._session_manager.get_session(session_id)
            compressed = session.compression_count > pre_count if session else False

            return self._success_response({
                "session_id": session_id,
                "message_count": session.message_count if session else pre_msgs,
                "compression_count": session.compression_count if session else pre_count,
                "compressed_summary_preview": (session.compressed_summary[:200] + "...")
                    if session and session.compressed_summary else None,
                "actually_compressed": compressed,
            }, "Session compressed" if compressed else "Compression skipped — not enough content to compress")

        except Exception as e:
            logger.error(f"Error compressing session: {e}")
            return self._error_response(500, "INTERNAL_ERROR", f"Failed to compress session: {e}")

    # ==================== Session tree: fork / branch / navigate ====================

    async def handle_fork_session(self, request: web.Request) -> web.Response:
        """POST /api/sessions/{session_id}/fork — fork into a new session."""
        try:
            session_id = request.match_info["session_id"]
            try:
                body = await request.json()
            except Exception:
                body = {}
            if not isinstance(body, dict):
                body = {}

            from boarai.core.session.tree import SessionTreeError, fork_session

            try:
                forked = await fork_session(
                    self._session_manager._storage,
                    session_id,
                    at_entry_id=body.get("at_entry_id"),
                    new_session_id=body.get("new_session_id"),
                )
            except SessionTreeError as e:
                status = 409 if e.reason == "target_exists" else (
                    404 if e.reason in ("session_not_found", "unknown_entry",
                                        "entry_not_on_path", "no_journal")
                    else 500
                )
                return self._error_response(status, e.reason.upper(), str(e))

            # Adopt the fork into the manager cache so a follow-up run can use
            # it without a reload round-trip.
            self._session_manager._sessions[forked.session_id] = forked

            resp = self._success_response(forked.to_summary_dict(), "Session forked")
            spawn(self._safe_broadcast("session.created", {
                "session_id": forked.session_id,
                "parent_session": session_id,
            }))
            return resp

        except Exception as e:
            logger.error(f"Error forking session: {e}")
            return self._error_response(500, "INTERNAL_ERROR", f"Failed to fork session: {e}")

    async def handle_session_tree(self, request: web.Request) -> web.Response:
        """GET /api/sessions/{session_id}/tree — branch points and active path."""
        try:
            session_id = request.match_info["session_id"]
            from boarai.core.session.tree import SessionTreeError, read_tree

            try:
                tree = await read_tree(self._session_manager._storage, session_id)
            except SessionTreeError as e:
                return self._error_response(404, e.reason.upper(), str(e))

            return self._success_response({
                "session_id": tree.session_id,
                "activeLeaf": tree.active_leaf,
                "activePath": tree.active_path,
                "messageCount": tree.message_count,
                "totalMessageEntries": tree.total_message_entries,
                "branchPoints": [
                    {
                        "parentEntryId": bp.parent_entry_id,
                        "parentPreview": bp.parent_preview,
                        "children": [
                            {
                                "entryId": c.entry_id,
                                "role": c.role,
                                "preview": c.preview,
                                "seq": c.seq,
                                "active": c.active,
                            }
                            for c in bp.children
                        ],
                    }
                    for bp in tree.branch_points
                ],
            })

        except Exception as e:
            logger.error(f"Error reading session tree: {e}")
            return self._error_response(500, "INTERNAL_ERROR", f"Failed to read session tree: {e}")

    async def handle_session_context(self, request: web.Request) -> web.Response:
        """GET /api/sessions/{session_id}/context?leafId= — view a branch."""
        try:
            session_id = request.match_info["session_id"]
            leaf_id = request.query.get("leafId") or None
            from boarai.core.session.tree import SessionTreeError, session_context

            try:
                ctx = await session_context(
                    self._session_manager._storage, session_id, leaf_id,
                )
            except SessionTreeError as e:
                return self._error_response(404, e.reason.upper(), str(e))
            return self._success_response(ctx)

        except Exception as e:
            logger.error(f"Error reading session context: {e}")
            return self._error_response(500, "INTERNAL_ERROR", f"Failed to read context: {e}")

    async def handle_navigate_tree(self, request: web.Request) -> web.Response:
        """POST /api/sessions/{session_id}/navigate — switch the active branch."""
        try:
            session_id = request.match_info["session_id"]
            body, parse_error = parse_json_object(await request.read())
            if body is None:
                return self._error_response(400, "INVALID_BODY", parse_error)
            leaf_id = body.get("leaf_id") or body.get("leafId")
            if not isinstance(leaf_id, str) or not leaf_id:
                return self._error_response(
                    400, "INVALID_LEAF", "leaf_id is required",
                )

            from boarai.core.session.tree import SessionTreeError, navigate_tree

            try:
                ctx = await navigate_tree(
                    self._session_manager._storage, session_id, leaf_id,
                )
            except SessionTreeError as e:
                status = 404 if e.reason in ("no_journal", "unknown_entry") else 500
                return self._error_response(status, e.reason.upper(), str(e))

            # The cached session still holds the previous active path; drop it
            # so the next get_session reloads the new one.
            self._session_manager._sessions.pop(session_id, None)
            await self._session_manager.save_session(session_id)

            resp = self._success_response(ctx, "Navigated")
            spawn(self._safe_broadcast("session.navigated", {
                "session_id": session_id, "leaf_id": leaf_id,
            }))
            return resp

        except Exception as e:
            logger.error(f"Error navigating session tree: {e}")
            return self._error_response(500, "INTERNAL_ERROR", f"Failed to navigate: {e}")

    # ==================== WebSocket Handlers ====================
    # NOTE: The /ws route is served by UnifiedServer._handle_websocket, which
    # registers clients here (_all_ws_clients) and starts the heartbeat below.
    # The legacy per-handler handle_websocket() was removed (dead code).

    def _start_heartbeat(self) -> None:
        """Start background heartbeat task (runs once, shared across all connections)."""
        if hasattr(self, '_heartbeat_started') and self._heartbeat_started:
            return
        self._heartbeat_started = True
        spawn(self._heartbeat_loop())

    def _discard_ws_client(self, ws) -> None:
        """Unsubscribe a connection from the global pool and every session."""
        self._all_ws_clients.discard(ws)
        for session_id in list(self._ws_clients.keys()):
            clients = self._ws_clients[session_id]
            clients.discard(ws)
            if not clients:
                del self._ws_clients[session_id]

    async def _heartbeat_loop(self) -> None:
        """Periodically ping all WS connections to detect and clean up zombies."""
        while True:
            await asyncio.sleep(30)
            if not self._all_ws_clients:
                continue
            dead = []
            for ws in list(self._all_ws_clients):
                try:
                    await asyncio.wait_for(ws.ping(), timeout=2.0)
                except Exception:
                    dead.append(ws)
            if dead:
                for ws in dead:
                    self._discard_ws_client(ws)
                logger.debug(f"Heartbeat cleaned {len(dead)} zombie connection(s)")

    async def _process_ws_message(self, ws: web.WebSocketResponse, data: str) -> None:
        """Process incoming WebSocket message."""
        try:
            message = json.loads(data)
            msg_type = message.get("type", "")
            payload = message.get("payload", {})
            msg_id = message.get("id")

            if msg_type == "session.subscribe":
                session_id = payload.get("session_id")
                if session_id:
                    if session_id not in self._ws_clients:
                        self._ws_clients[session_id] = set()
                    self._ws_clients[session_id].add(ws)
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.subscribe.response",
                        "status": "ok",
                        "payload": {"session_id": session_id},
                    })

            elif msg_type == "session.unsubscribe":
                session_id = payload.get("session_id")
                if session_id and session_id in self._ws_clients:
                    self._ws_clients[session_id].discard(ws)
                    if not self._ws_clients[session_id]:
                        del self._ws_clients[session_id]
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.unsubscribe.response",
                        "status": "ok",
                        "payload": {"session_id": session_id},
                    })

            elif msg_type == "session.create":
                session = await self._service.create_session(
                    title=payload.get("title", "New Session"),
                    metadata=payload.get("metadata", {}),
                    source=payload.get("source", "system"),
                    agent_name=payload.get("agent_name", "default"),
                )
                # Send response FIRST, then broadcast — this ensures the client
                # always gets a reply even if broadcast encounters stale connections.
                await ws.send_json({
                    "id": msg_id,
                    "type": "session.create.response",
                    "status": "ok",
                    "payload": session.to_summary_dict(),
                })
                await self._broadcast_event("session.created", session.to_summary_dict())

            elif msg_type == "session.update":
                session_id = payload.get("session_id")
                if not session_id:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.update.response",
                        "status": "error",
                        "payload": {"code": "MISSING_PARAMETER", "message": "session_id is required"},
                    })
                    return

                session = await self._service.update_session(session_id, payload)
                if not session:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.update.response",
                        "status": "error",
                        "payload": {"code": "SESSION_NOT_FOUND", "message": f"Session {session_id} not found"},
                    })
                    return

                await ws.send_json({
                    "id": msg_id,
                    "type": "session.update.response",
                    "status": "ok",
                    "payload": session.to_summary_dict(),
                })
                # Same field set as the HTTP session.update broadcast.
                await self._broadcast_event("session.updated", {
                    "session_id": session_id,
                    "changes": update_changes(payload),
                })

            elif msg_type == "session.delete":
                session_id = payload.get("session_id")
                if not session_id:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.delete.response",
                        "status": "error",
                        "payload": {"code": "MISSING_PARAMETER", "message": "session_id is required"},
                    })
                    return

                deleted = await self._service.delete_session(
                    session_id,
                    distill=bool(payload.get("distill", False)),
                )
                if not deleted:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.delete.response",
                        "status": "error",
                        "payload": {"code": "SESSION_NOT_FOUND", "message": f"Session {session_id} not found"},
                    })
                    return

                # Send response first, then broadcast — ensures client always gets a reply
                await ws.send_json({
                    "id": msg_id,
                    "type": "session.delete.response",
                    "status": "ok",
                    "payload": {"session_id": session_id},
                })
                await self._broadcast_event("session.deleted", {"session_id": session_id})

            elif msg_type == "session.list":
                limit = payload.get("limit", 50)
                offset = payload.get("offset", 0)
                sort = payload.get("sort", "updated_at")
                order = payload.get("order", "desc")
                status = payload.get("status")
                search = payload.get("search")
                source = payload.get("source")
                result = await self._session_manager.list_sessions(
                    limit=limit,
                    offset=offset,
                    sort=sort,
                    order=order,
                    status=status,
                    search=search,
                    source=source,
                )
                await ws.send_json({
                    "id": msg_id,
                    "type": "session.list.response",
                    "status": "ok",
                    "payload": result,
                })

            elif msg_type == "session.get":
                session_id = payload.get("session_id")
                if not session_id:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.get.response",
                        "status": "error",
                        "payload": {"code": "MISSING_PARAMETER", "message": "session_id is required"},
                    })
                    return

                # Auto-subscribe: clients that fetch session data should also
                # receive live run events for that session (other tabs, etc.)
                if session_id not in self._ws_clients:
                    self._ws_clients[session_id] = set()
                self._ws_clients[session_id].add(ws)

                session = await self._session_manager.get_session(session_id)
                if not session:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.get.response",
                        "status": "error",
                        "payload": {"code": "SESSION_NOT_FOUND", "message": f"Session {session_id} not found"},
                    })
                    return

                include_messages = bool(payload.get("include_messages", False))
                if include_messages:
                    # `message_limit` absent → full export (e.g. exportSessionJSON);
                    # otherwise window pagination applies. getMessages always sends
                    # an explicit message_limit, so full export stays distinct.
                    raw_limit = payload.get("message_limit")
                    limit = int(raw_limit) if raw_limit is not None else None
                    offset = int(payload.get("offset", 0) or 0)
                    since_seq = self._parse_seq(payload.get("since_seq"))
                    before_seq = self._parse_seq(payload.get("before_seq"))
                    result = self._build_messages_payload(
                        session, session.messages,
                        limit=limit, offset=offset,
                        since_seq=since_seq, before_seq=before_seq,
                    )
                    # Merge summary fields (model / thinking_mode / workspace_path /
                    # message_count) so getSession consumers can reuse this response.
                    result = {**session.to_summary_dict(), **result}
                else:
                    result = session.to_summary_dict()

                await ws.send_json({
                    "id": msg_id,
                    "type": "session.get.response",
                    "status": "ok",
                    "payload": result,
                })

            elif msg_type == "session.message":
                session_id = payload.get("session_id")
                role = payload.get("role", "user")
                content = payload.get("content", "")

                if not session_id or not content:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.message.response",
                        "status": "error",
                        "payload": {"code": "MISSING_PARAMETER", "message": "session_id and content are required"},
                    })
                    return

                # External clients must not forge system messages
                # (prompt-injection surface), mirroring the HTTP endpoint.
                if role not in ("user", "assistant"):
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.message.response",
                        "status": "error",
                        "payload": {"code": "INVALID_PARAMETER", "message": "role must be one of: user, assistant"},
                    })
                    return

                session = await self._session_manager.get_session(session_id)
                if not session:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.message.response",
                        "status": "error",
                        "payload": {"code": "SESSION_NOT_FOUND", "message": f"Session {session_id} not found"},
                    })
                    return

                # --- V2 hook: session.on_message (blockable) ---
                blocked, content, hook_reason = await self._run_on_message_hook(
                    session_id, role, content
                )
                if blocked:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.message.response",
                        "status": "error",
                        "payload": {"code": "MESSAGE_BLOCKED",
                                    "message": hook_reason or "Message blocked by extension hook"},
                    })
                    return

                if role == "user":
                    session.ensure_session_capability_inject()
                message = session.add_message(role, content, payload.get("metadata", {}))

                titled = False
                if role == "user":
                    titled = session.maybe_auto_title_from_user(content)

                await self._session_manager.save_session(session_id)

                # --- EventBus broadcast (SESSION_MESSAGE) for channels/analytics ---
                await self._publish_session_message(
                    session_id, message, role, content, payload.get("metadata", {}),
                )

                # Broadcast to session subscribers
                await self._broadcast_to_session(session_id, "session.message.new", {
                    "session_id": session_id,
                    "message_id": f"msg_{int(message.timestamp.timestamp() * 1000)}",
                    "role": role,
                    "content": content,
                    "timestamp": message.timestamp.isoformat(),
                    "metadata": getattr(message, "metadata", None),
                    "seq": getattr(message, "seq", 0),
                })
                if titled:
                    await self._broadcast_event("session.updated", {
                        "session_id": session_id,
                        "changes": {"title": session.title},
                    })

                await ws.send_json({
                    "id": msg_id,
                    "type": "session.message.response",
                    "status": "ok",
                    "payload": {
                        "message_id": f"msg_{int(message.timestamp.timestamp() * 1000)}",
                        "session_id": session_id,
                        "role": role,
                        "content": content,
                        "timestamp": message.timestamp.isoformat(),
                    },
                })

            elif msg_type == "session.message.update":
                session_id = payload.get("session_id")
                message_id = payload.get("message_id")
                content = (payload.get("content") or "").strip()

                if not session_id or not message_id or not content:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.message.update.response",
                        "status": "error",
                        "payload": {"code": "MISSING_PARAMETER",
                                    "message": "session_id, message_id and content are required"},
                    })
                    return

                session = await self._session_manager.get_session(session_id)
                if not session:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.message.update.response",
                        "status": "error",
                        "payload": {"code": "SESSION_NOT_FOUND", "message": f"Session {session_id} not found"},
                    })
                    return

                # Truncating history while a run streams invalidates the
                # executor's message indices (mis-directed writes / IndexError).
                try:
                    from boarai.core.session.run_manager import get_run_manager

                    if get_run_manager().get_active_run_for_session(session_id):
                        await ws.send_json({
                            "id": msg_id,
                            "type": "session.message.update.response",
                            "status": "error",
                            "payload": {
                                "code": "RUN_ACTIVE",
                                "message": "Cannot edit messages while a run is executing",
                            },
                        })
                        return
                except Exception:
                    pass

                # Only the latest user message can be edited
                last_user = next((m for m in reversed(session.messages) if m.role == "user"), None)
                if last_user is None or last_user.message_id != message_id:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.message.update.response",
                        "status": "error",
                        "payload": {"code": "EDIT_NOT_LATEST",
                                    "message": "Only the latest user message can be edited"},
                    })
                    return

                first_user = next((m for m in session.messages if m.role == "user"), None)
                is_first = first_user is not None and first_user.message_id == message_id

                kept = session.truncate_messages(message_id)
                if kept is None:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.message.update.response",
                        "status": "error",
                        "payload": {"code": "MESSAGE_NOT_FOUND", "message": f"Message {message_id} not found"},
                    })
                    return

                last_user.content = content

                titled = False
                if is_first:
                    text = session._user_text_for_title(content)
                    new_title = session.generate_title(text) if text else ""
                    if new_title and new_title.strip() not in session._DEFAULT_TITLES:
                        session.title = new_title
                        titled = True

                await self._session_manager.save_session(session_id)

                # Session subscribers drop the truncated tail
                await self._broadcast_to_session(session_id, "session.messages_truncated", {
                    "session_id": session_id,
                    "keep_message_id": message_id,
                })
                if titled:
                    await self._broadcast_event("session.updated", {
                        "session_id": session_id,
                        "changes": {"title": session.title},
                    })

                await ws.send_json({
                    "id": msg_id,
                    "type": "session.message.update.response",
                    "status": "ok",
                    "payload": {
                        "session_id": session_id,
                        "message_id": message_id,
                        "content": content,
                        "message_count": session.message_count,
                        "title": session.title if titled else None,
                    },
                })

            elif msg_type == "session.run.create":
                session_id = payload.get("session_id", "")
                content = payload.get("content", "")
                model = payload.get("model")

                if not session_id:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.run.create.response",
                        "status": "error",
                        "payload": {"code": "MISSING_PARAMETER", "message": "session_id is required"},
                    })
                    return
                # Allow a run with no text when attachments are present.
                if not content and not payload.get("attachments"):
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.run.create.response",
                        "status": "error",
                        "payload": {"code": "MISSING_PARAMETER", "message": "content is required"},
                    })
                    return

                session = await self._session_manager.get_session(session_id)
                if not session:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.run.create.response",
                        "status": "error",
                        "payload": {"code": "SESSION_NOT_FOUND", "message": f"Session {session_id} not found"},
                    })
                    return

                # Validate attachment references (uploaded earlier via files API)
                attachments = None
                if payload.get("attachments"):
                    from boarai.core.session.attachments import validate_attachments
                    try:
                        attachments = await validate_attachments(session_id, payload.get("attachments"))
                    except ValueError as e:
                        await ws.send_json({
                            "id": msg_id,
                            "type": "session.run.create.response",
                            "status": "error",
                            "payload": {"code": "INVALID_ATTACHMENTS", "message": str(e)},
                        })
                        return

                # Save user message — unless reusing the last one (edit / retry re-run)
                reuse_last_user = bool(payload.get("reuse_last_user", False))
                if reuse_last_user:
                    # Skip [BoarAi_System] injects — the reuse boundary must be
                    # the last REAL user turn, not a runtime-injected message.
                    msg = next(
                        (
                            m for m in reversed(session.messages)
                            if m.role == "user" and not session.is_system_inject(m)
                        ),
                        None,
                    )
                    if msg is None:
                        await ws.send_json({
                            "id": msg_id,
                            "type": "session.run.create.response",
                            "status": "error",
                            "payload": {"code": "NO_USER_MESSAGE", "message": "No user message to reuse"},
                        })
                        return
                    titled = False
                else:
                    metadata: Optional[Dict[str, Any]] = {}
                    if attachments:
                        metadata["attachments"] = attachments
                    # --- V2 hook: session.on_message (blockable) ---
                    # The main chat path: WebUI sends session.run.create with
                    # the user's message — hook it like every other entry.
                    # Command interception happens AFTER this hook so an
                    # extension can still block/rewrite a "/cmd" turn; a
                    # rewrite that stops matching simply flows on as text.
                    blocked, content, hook_reason = await self._run_on_message_hook(
                        session_id, "user", content
                    )
                    if blocked:
                        await ws.send_json({
                            "id": msg_id,
                            "type": "session.run.create.response",
                            "status": "error",
                            "payload": {"code": "MESSAGE_BLOCKED",
                                        "message": hook_reason or "Message blocked by extension hook"},
                        })
                        return
                    # --- Chat-scope command interception (BEFORE any LLM run) ---
                    # A "/cmd" message that resolves to a registered chat
                    # command never reaches the model: handler commands reply
                    # in place (both turns persisted like a normal exchange),
                    # prompt commands expand into the content that flows
                    # through the normal run path. Attachment-carrying turns
                    # are plain messages (commands are text turns).
                    command_meta = None
                    if content and not attachments:
                        from boarai.core.command import (
                            expand_prompt,
                            match_chat_command,
                        )

                        matched = match_chat_command(content)
                        if matched:
                            cmd_info, cmd_args = matched
                            if cmd_info.kind == "prompt":
                                command_meta = {
                                    "name": cmd_info.name,
                                    "kind": "prompt",
                                    "raw": content,
                                }
                                content = expand_prompt(cmd_info.template, cmd_args)
                            else:
                                raw_text = content
                                from boarai.core.command import run_command_turn

                                turn = await run_command_turn(
                                    self._session_manager, session, session_id,
                                    cmd_info, cmd_args, raw_text=raw_text,
                                )
                                await self.publish_command_turn(
                                    session_id, turn, raw_text, session.title,
                                )
                                await ws.send_json({
                                    "id": msg_id,
                                    "type": "session.run.create.response",
                                    "status": "ok",
                                    "payload": {
                                        "session_id": session_id,
                                        "status": "completed",
                                        "command": {
                                            "name": cmd_info.name,
                                            "ok": turn.ok,
                                            "output": turn.reply_text,
                                        },
                                    },
                                })
                                return
                    if command_meta:
                        # Prompt-command expansion: the journal stores what
                        # the LLM sees; the raw "/cmd args" text rides along
                        # in metadata for display.
                        metadata["chat_command"] = command_meta
                    metadata = metadata or None
                    # The session-opening inject must hit the journal BEFORE
                    # the first user turn (append-only: later inserts only
                    # tail-append).
                    session.ensure_session_capability_inject()
                    msg = session.add_message("user", content, metadata=metadata)
                    # First user message -> auto title from content (backend authority)
                    titled = session.maybe_auto_title_from_user(content)
                    # --- EventBus broadcast (SESSION_MESSAGE) ---
                    await self._publish_session_message(
                        session_id, msg, "user", content, metadata,
                    )

                await self._session_manager.save_session(session_id)
                if titled:
                    await self._broadcast_event("session.updated", {
                        "session_id": session_id,
                        "changes": {"title": session.title},
                    })
                else:
                    # Still notify list sort/time without implying title change
                    await self._broadcast_event("session.updated", {
                        "session_id": session_id,
                        "changes": {},
                    })

                # Determine agent from session or payload
                agent_name = payload.get("agent_name", getattr(session, 'agent_name', 'default'))

                # Resolve model: client override → session default → None (let LLMManager pick)
                model_changed = bool(model and model != session.model)
                if model_changed:
                    session.model = model  # persist client override
                elif not model and session.model:
                    model = session.model  # restore from last session

                # Persist thinking_mode to session
                thinking_mode = payload.get("thinking_mode")
                tm_changed = bool(thinking_mode and thinking_mode != session.thinking_mode)
                if tm_changed:
                    from boarai.core.session.thinking.config import normalize_thinking_mode

                    session.thinking_mode = normalize_thinking_mode(thinking_mode)
                if model_changed or tm_changed:
                    await self._session_manager.save_session(session_id)

                # Dual-path vision routing happens in the executor (it decides
                # from the resolved main model whether image attachments must be
                # described by the vision sidecar). The main model is NEVER
                # switched here — session.model stays the user's preference.
                # Subscribe before starting so a fast provider failure cannot be lost.
                self._ws_clients.setdefault(session_id, set()).add(ws)
                run_id = await self._run_manager.create_run(
                    session_id,
                    model,
                    agent_name=agent_name,
                    start_immediately=False,
                )

                # The browser must receive run_id before the background task can
                # emit its first event.
                await ws.send_json({
                    "id": msg_id,
                    "type": "session.run.create.response",
                    "status": "ok",
                    "payload": {"run_id": run_id, "session_id": session_id, "status": "running"},
                })
                self._run_manager.start_run(run_id)

            elif msg_type == "session.run.get":
                run_id = payload.get("run_id", "")
                if not run_id:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.run.get.response",
                        "status": "error",
                        "payload": {"code": "MISSING_PARAMETER", "message": "run_id is required"},
                    })
                    return

                run = self._run_manager.get_run(run_id)
                if not run:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.run.get.response",
                        "status": "error",
                        "payload": {"code": "NOT_FOUND", "message": f"Run {run_id} not found"},
                    })
                    return

                await ws.send_json({
                    "id": msg_id,
                    "type": "session.run.get.response",
                    "status": "ok",
                    "payload": run,
                })

            elif msg_type == "session.run.cancel":
                run_id = payload.get("run_id", "")
                if not run_id:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.run.cancel.response",
                        "status": "error",
                        "payload": {"code": "MISSING_PARAMETER", "message": "run_id is required"},
                    })
                    return

                cancelled = await self._run_manager.cancel_run(run_id)
                await ws.send_json({
                    "id": msg_id,
                    "type": "session.run.cancel.response",
                    "status": "ok" if cancelled else "error",
                    "payload": {"run_id": run_id, "cancelled": cancelled},
                })

            elif msg_type == "session.run.subscribe":
                session_id = payload.get("session_id", "")
                if not session_id:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.run.subscribe.response",
                        "status": "error",
                        "payload": {"code": "MISSING_PARAMETER", "message": "session_id is required"},
                    })
                    return

                active_run = self._run_manager.get_active_run_for_session(session_id)
                if not active_run:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "session.run.subscribe.response",
                        "status": "ok",
                        "payload": {"run_id": None, "resume": None},
                    })
                    return

                run_id = active_run["run_id"]
                # Per-turn live text only. The run-cumulative full_response
                # must NEVER go here: the client splats resume onto the
                # in-progress bubble, and a whole-run transcript would repeat
                # every earlier turn inside it (mid-run view-switch dup bug).
                resume_content = active_run.get("current_turn_text", "")

                # session.get/session.run.create already subscribe this socket to
                # the session. RunManager broadcasts to that subscription, so a
                # second per-run callback would duplicate every stream event.
                await ws.send_json({
                    "id": msg_id,
                    "type": "session.run.subscribe.response",
                    "status": "ok",
                    "payload": {
                        "run_id": run_id,
                        "resume": resume_content,
                    },
                })

            elif msg_type == "session.typing":
                session_id = payload.get("session_id")
                role = payload.get("role", "assistant")
                if session_id:
                    await self._broadcast_to_session(session_id, "session.typing", {
                        "session_id": session_id,
                        "role": role,
                    })

            elif msg_type == "tool.confirm":
                run_id = payload.get("run_id", "")
                approved = payload.get("approved", False)
                if not run_id:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "tool.confirm.response",
                        "status": "error",
                        "payload": {"code": "MISSING_PARAMETER", "message": "run_id is required"},
                    })
                    return
                # Only clients subscribed to the run's session may approve
                # its tools; otherwise any WS connection could authorize
                # arbitrary tool execution.
                run = self._run_manager.get_run(run_id)
                if run is None:
                    await ws.send_json({
                        "id": msg_id,
                        "type": "tool.confirm.response",
                        "status": "error",
                        "payload": {"code": "RUN_NOT_FOUND", "message": f"Run {run_id} not found"},
                    })
                    return
                run_session_id = run.get("session_id", "") if isinstance(run, dict) else ""
                if run_session_id and ws not in self._ws_clients.get(run_session_id, set()):
                    await ws.send_json({
                        "id": msg_id,
                        "type": "tool.confirm.response",
                        "status": "error",
                        "payload": {"code": "FORBIDDEN", "message": "Not subscribed to this run's session"},
                    })
                    return
                confirmation_id = payload.get("confirmation_id", "") or ""
                grant_scope = payload.get("grant_scope", "once") or "once"
                if grant_scope not in ("once", "session", "folder"):
                    grant_scope = "once"
                resolved = self._run_manager.confirm_tool(
                    run_id, confirmation_id, approved, grant_scope=grant_scope
                )
                await ws.send_json({
                    "id": msg_id,
                    "type": "tool.confirm.response",
                    "status": "ok" if resolved else "not_found",
                    "payload": {
                        "run_id": run_id,
                        "confirmation_id": confirmation_id,
                        "approved": approved,
                        "resolved": resolved,
                    },
                })

            else:
                await ws.send_json({
                    "id": msg_id,
                    "type": f"{msg_type}.response",
                    "status": "error",
                    "payload": {"code": "UNKNOWN_TYPE", "message": f"Unknown message type: {msg_type}"},
                })

        except json.JSONDecodeError:
            await ws.send_json({
                "id": msg_id if 'msg_id' in locals() else None,
                "status": "error",
                "payload": {"code": "INVALID_JSON", "message": "Invalid JSON"},
            })
        except Exception as e:
            logger.error(f"WebSocket message processing error: {e}")
            await ws.send_json({
                "id": msg_id if 'msg_id' in locals() else None,
                "status": "error",
                "payload": {"code": "INTERNAL_ERROR", "message": str(e)},
            })

    async def subscribe_extension_failure_bridge(self) -> None:
        """Bus→WS bridge for extension.load_failed (idempotent, server start)."""
        if getattr(self, "_ext_failure_bridge_on", False):
            return
        self._ext_failure_bridge_on = True

        async def _bridge(event) -> None:
            data = dict(getattr(event, "data", None) or {})
            await self._safe_broadcast("extension.load_failed", data)

        await self._event_bus.subscribe(EventType.EXTENSION_LOAD_FAILED, _bridge)

    async def _broadcast_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Broadcast event to all connected WebSocket clients."""
        message = {
            "type": event_type,
            "payload": payload,
            "timestamp": datetime.now().isoformat(),
        }

        disconnected = set()
        for ws in self._all_ws_clients:
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=2.0)
            except Exception:
                disconnected.add(ws)

        # Clean up disconnected clients
        for ws in disconnected:
            self._discard_ws_client(ws)

    async def _safe_broadcast(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Broadcast with exception safety — never blocks the caller."""
        try:
            await self._broadcast_event(event_type, payload)
        except Exception as e:
            logger.debug(f"Broadcast {event_type} completed with: {e}")

    async def _broadcast_to_session(self, session_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        """Broadcast event to clients subscribed to a specific session."""
        if session_id not in self._ws_clients:
            return

        message = {
            "type": event_type,
            "payload": payload,
            "timestamp": datetime.now().isoformat(),
        }

        disconnected = set()
        for ws in self._ws_clients[session_id]:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.add(ws)

        # Clean up disconnected clients
        for ws in disconnected:
            self._discard_ws_client(ws)

    async def _safe_broadcast_to_session(self, session_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        """Broadcast to session subscribers with exception safety — never blocks the caller."""
        try:
            await self._broadcast_to_session(session_id, event_type, payload)
        except Exception as e:
            logger.debug(f"Broadcast {event_type} to session {session_id} completed with: {e}")

    # ==================== Helper Methods ====================

    def _success_response(self, data: Any, message: str = None, status: int = 200) -> web.Response:
        """Create a success response."""
        response = {
            "success": True,
            "data": data,
        }
        if message:
            response["message"] = message
        return web.json_response(response, status=status)

    def _error_response(self, status: int, code: str, message: str) -> web.Response:
        """Create an error response."""
        return web.json_response(
            {
                "success": False,
                "error": {
                    "code": code,
                    "message": message,
                },
            },
            status=status,
        )


# Global instance
_session_api_handler: Optional[SessionAPIHandler] = None


def get_session_api_handler() -> SessionAPIHandler:
    """Get the global session API handler instance."""
    global _session_api_handler
    if _session_api_handler is None:
        _session_api_handler = SessionAPIHandler()
    return _session_api_handler
