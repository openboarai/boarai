"""
Session domain service — the single implementation behind both channels.

HTTP (``core/api/session.py`` handlers) and WebSocket (``session.*`` messages)
are two views of the same domain operations. They used to re-implement each
case by hand, including the ``session.updated`` field set — which is exactly
how the two views drift apart: one adds a field, the other keeps broadcasting
the old shape.

Rules for this module:
  * Return plain domain results (``Session`` objects / bools / dicts) — no
    ``{success, data}`` envelope and no ``send_json``. Each channel adapts.
  * The broadcast field set lives in :data:`SESSION_UPDATE_FIELDS`; both
    channels build their payload through :func:`update_changes`.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

SESSION_UPDATE_FIELDS = (
    "title",
    "pinned",
    "archived",
    "metadata",
    "agent_name",
    "thinking_mode",
    "model",
    "workspace_path",
    "approval_mode",
)
"""Mutable session fields a client may patch, in broadcast order.

Adding a field here updates the HTTP response, the HTTP broadcast, the WS
response and the WS broadcast at once — that is the point of the constant.
"""

_APPROVAL_MODE_OVERRIDES = {
    "deny": "deny",
    "allow": "off",
}
"""``session.default_approval_mode`` (UI wording) → the override spelling that
``ToolGuard.evaluate_tool`` reads from ``security_overrides.approval_mode``.

``ask`` is deliberately absent: "ask" means "follow the global fallback", so
no override is stamped — flipping the global ``tool_guard.approval_mode``
later keeps affecting such sessions (this is what v1.15 intended and why the
global mode did not stop being the fallback). Only deny/allow are explicit
user intent strong enough to freeze a session.
"""


def update_changes(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Project a client payload onto the mutable session fields."""
    return {name: payload.get(name) for name in SESSION_UPDATE_FIELDS}


def stamp_default_approval_mode(session) -> None:
    """Snapshot ``session.default_approval_mode`` into the session overrides."""
    try:
        from boarai.core.config import get_config_parser

        cp = get_config_parser()
        session_cfg = getattr(getattr(cp, "_config", None), "session", None)
        raw = str(
            getattr(session_cfg, "default_approval_mode", "ask") or "ask"
        ).strip().lower()
        stamped = _APPROVAL_MODE_OVERRIDES.get(raw)
        if stamped is None:
            return
        overrides = dict(session.security_overrides or {})
        overrides["approval_mode"] = stamped
        session.security_overrides = overrides
    except Exception as e:  # noqa: BLE001 — config unavailable → global fallback
        logger.debug("Could not apply default approval mode: %s", e)


def apply_session_defaults(session) -> None:
    """Write global defaults (model / agent / workspace / approval mode) into a new session."""
    logger = logging.getLogger(__name__)

    # 1. Default model — from LLMManager (configured in llm.yaml)
    try:
        from boarai.core.llm import get_llm_manager

        llm_mgr = get_llm_manager()
        if llm_mgr.default_model:
            session.model = llm_mgr.default_model
    except Exception as e:  # noqa: BLE001 — no LLM configured yet
        logger.debug("Could not read default model: %s", e)

    # 2. Default agent — from AgentRegistry (is_default=True)
    try:
        from boarai.core.prompt.agent import get_agent_registry

        registry = get_agent_registry()
        default_agent = registry.get_default() if registry else None
        if default_agent and default_agent.name:
            # Thinking level is a session concern only — a new session starts
            # at "auto" and follows the global thinking.default_mode.
            session.agent_name = default_agent.name
    except Exception as e:  # noqa: BLE001
        logger.debug("Could not read default agent: %s", e)

    # 3. Default workspace — single implementation in session.workspace
    try:
        from boarai.core.session.workspace import get_default_workspace_for_session

        ws = get_default_workspace_for_session(session.session_id)
        if ws:
            session.workspace_path = ws
            logger.info(
                "Session %s default workspace: %s", session.session_id, ws
            )
        else:
            logger.debug(
                "Session %s: no default workspace (mode=default or unavailable)",
                session.session_id,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not apply default workspace: %s", e)

    # 4. Default approval mode — snapshot the configured "new session default"
    #    into this session's own overrides (deny/allow only; "ask" means
    #    follow the global fallback, so nothing is stamped). The stamp is
    #    explicit user intent, deliberately frozen per session, and wins over
    #    tool_guard.auto_approve — deployments relying on auto-approve should
    #    set this to "allow".
    stamp_default_approval_mode(session)


class SessionService:
    """Domain operations shared by the HTTP and WebSocket channels."""

    def __init__(
        self,
        manager,
        apply_defaults: Optional[Callable[[Any], None]] = None,
    ) -> None:
        self._manager = manager
        self._apply_defaults = apply_defaults or apply_session_defaults

    async def create_session(
        self,
        *,
        title: str = "New Session",
        agent_name: str = "default",
        source: str = "api",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Create, decorate and persist a session. Returns the Session."""
        session = await self._manager.create_session(agent_name=agent_name)
        session.title = title
        session.metadata = metadata or {}
        session.source = source
        # Global model / agent / workspace defaults, applied before the first
        # save so the client receives a fully-formed session.
        self._apply_defaults(session)
        await self._manager.save_session(session.session_id)
        return session

    async def update_session(self, session_id: str, payload: Dict[str, Any]):
        """Patch the mutable fields, persist, and return the Session."""
        session = await self._manager.update_session_fields(
            session_id=session_id, **update_changes(payload)
        )
        if session is None:
            return None
        await self._manager.save_session(session_id)
        return session

    async def delete_session(self, session_id: str, *, distill: bool = False) -> bool:
        """Delete (or trash) a session; False when it does not exist."""
        return bool(
            await self._manager.delete_session(session_id, distill=bool(distill))
        )

