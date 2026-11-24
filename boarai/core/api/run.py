"""
Run API endpoints for BoarAi server.

Flat API structure - all IDs and actions in request body/query params.
"""

import json
import logging
from typing import Any, Optional

from aiohttp import web

from boarai.core.session import Session, get_run_manager, get_session_manager

logger = logging.getLogger(__name__)


class RunAPIHandler:
    """Handles run API requests (flat API, IDs in body)."""
    
    def __init__(self):
        self._run_manager = get_run_manager()
        self._session_manager = get_session_manager()
    
    async def handle_create_run(self, request: web.Request) -> web.Response:
        """POST /api/chat - Send user message & start background run."""
        try:
            data = await request.json()
            session_id = data.get("session_id", "")
            content = data.get("content", "")
            
            if not session_id:
                return self._run_error(400, "INVALID_REQUEST", "session_id is required")
            # Allow a run with no text when attachments are present.
            if not content and not data.get("attachments"):
                return self._run_error(400, "INVALID_REQUEST", "content is required")
            
            model = data.get("model")
            
            session = await self._session_manager.get_session(session_id)
            if not session:
                return self._run_error(404, "NOT_FOUND", f"Session {session_id} not found")

            # Validate attachment references (uploaded earlier via files API)
            attachments = None
            if data.get("attachments"):
                from boarai.core.session.attachments import validate_attachments
                try:
                    attachments = await validate_attachments(session_id, data.get("attachments"))
                except ValueError as e:
                    return self._run_error(400, "INVALID_ATTACHMENTS", str(e))

            # Save user message — unless reusing the last one (edit / retry re-run)
            reuse_last_user = bool(data.get("reuse_last_user", False))
            if reuse_last_user:
                msg = next((m for m in reversed(session.messages) if m.role == "user"), None)
                if msg is None:
                    return self._run_error(400, "NO_USER_MESSAGE", "No user message to reuse")
            else:
                metadata: dict = {}
                if attachments:
                    metadata["attachments"] = attachments
                # --- Chat command interception (BEFORE any LLM run) ---
                # Same single source as the WS session.run.create path
                # (core.command.run_command_turn + publish_command_turn),
                # so both channels of the run-creation domain behave
                # identically: handler commands reply in place with a
                # `command` payload instead of a run_id; prompt commands
                # expand into the content that flows through normally.
                command_meta = None
                if content and not attachments:
                    from boarai.core.command import (
                        expand_prompt,
                        match_chat_command,
                        run_command_turn,
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
                            from boarai.core.api import get_session_api_handler

                            session_api = get_session_api_handler()
                            turn = await run_command_turn(
                                self._session_manager, session, session_id,
                                cmd_info, cmd_args, raw_text=content,
                            )
                            await session_api.publish_command_turn(
                                session_id, turn, content, session.title,
                            )
                            return web.json_response({
                                "success": True,
                                "data": {
                                    "session_id": session_id,
                                    "status": "completed",
                                    "command": {
                                        "name": cmd_info.name,
                                        "ok": turn.ok,
                                        "output": turn.reply_text,
                                    },
                                },
                            })
                if command_meta:
                    metadata["chat_command"] = command_meta
                metadata = metadata or None
                # The session-opening inject must hit the journal BEFORE the
                # first user turn: an append-only journal only tail-appends,
                # so a later insert can never reclaim position #0 on disk.
                session.ensure_session_capability_inject()
                msg = session.add_message("user", content, metadata=metadata)
                # First user message → title (ignore leading system injects)
                session.maybe_auto_title_from_user(content)

            await self._session_manager.save_session(session_id)

            # Resolve model: client override → session default → None (let LLMManager pick)
            model_changed = bool(model and model != session.model)
            if model_changed:
                session.model = model  # persist client override
            elif not model and session.model:
                model = session.model  # restore from last session

            # Persist thinking_mode to session
            thinking_mode = data.get("thinking_mode")
            tm_changed = bool(thinking_mode and thinking_mode != session.thinking_mode)
            if tm_changed:
                from boarai.core.session.thinking.config import normalize_thinking_mode

                session.thinking_mode = normalize_thinking_mode(thinking_mode)
            if model_changed or tm_changed:
                await self._session_manager.save_session(session_id)

            # Resolve agent_name: client → session default
            agent_name = data.get("agent_name") or getattr(session, 'agent_name', 'default')

            # Dual-path vision routing happens in the executor (it decides from
            # the resolved main model whether image attachments must be
            # described by the vision sidecar). The main model is NEVER switched
            # here — session.model stays the user's preference.
            run_id = await self._run_manager.create_run(
                session_id,
                model,
                agent_name=agent_name,
            )
            
            return web.json_response({
                "success": True,
                "data": {
                    "run_id": run_id,
                    "session_id": session_id,
                    "status": "running",
                },
            })
        
        except json.JSONDecodeError:
            return self._run_error(400, "INVALID_JSON", "Request body must be valid JSON")
        except Exception as e:
            logger.error(f"Error creating run: {e}")
            return self._run_error(500, "INTERNAL_ERROR", str(e))
    
    async def handle_run_action(self, request: web.Request) -> web.Response:
        """POST /api/runs - Run operations. Action in body."""
        try:
            data = await request.json()
            action = data.get("action", "")
            run_id = data.get("run_id", "")
            session_id = data.get("session_id", "")
            
            if action == "get":
                if not run_id:
                    return self._run_error(400, "INVALID_REQUEST", "run_id is required")
                run = self._run_manager.get_run(run_id)
                if not run:
                    return self._run_error(404, "NOT_FOUND", f"Run {run_id} not found")
                return self._run_success(run)
            
            elif action == "cancel":
                if not run_id:
                    return self._run_error(400, "INVALID_REQUEST", "run_id is required")
                cancelled = await self._run_manager.cancel_run(run_id)
                if not cancelled:
                    return self._run_error(404, "NOT_FOUND", f"Run {run_id} not found")
                return self._run_success({"run_id": run_id, "status": "cancelled"})
            
            elif action == "list":
                if not session_id:
                    return self._run_error(400, "INVALID_REQUEST", "session_id is required")
                runs = self._run_manager.get_session_runs(session_id)
                return self._run_success({"runs": runs, "session_id": session_id})
            
            else:
                return self._run_error(400, "INVALID_ACTION", f"Unknown action: {action}")
        
        except json.JSONDecodeError:
            return self._run_error(400, "INVALID_JSON", "Request body must be valid JSON")
        except Exception as e:
            logger.error(f"Error handling run action: {e}")
            return self._run_error(500, "INTERNAL_ERROR", str(e))
    
    def _run_success(self, data: Any, status: int = 200) -> web.Response:
        return web.json_response({"success": True, "data": data}, status=status)
    
    def _run_error(self, status: int, code: str, message: str) -> web.Response:
        return web.json_response(
            {"success": False, "error": {"code": code, "message": message}},
            status=status,
        )


_run_handler: Optional[RunAPIHandler] = None


def get_run_api_handler() -> RunAPIHandler:
    global _run_handler
    if _run_handler is None:
        _run_handler = RunAPIHandler()
    return _run_handler
