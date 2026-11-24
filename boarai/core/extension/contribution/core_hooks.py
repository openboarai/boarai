"""
Core hook points for BoarAi extension system (v2).

These are the core-pre-embedded hook points that extensions can bind handlers
to. They must be declared at startup so the HookRegistry knows their semantics
(observe / blockable / transform) and visibility.

Semantics legend:
- observe   : notify-only; extension handlers may inspect the payload
- blockable : a handler returning {"blocked": True, "reason": ...} stops the chain
- transform : payload flows through handlers; each may modify it (data flows on)

The transform point ``core.llm.transform_output`` is declared but only
consumed on the hot path when a binding exists (see executor).
"""

import logging

from .hook_registry import get_hook_registry

logger = logging.getLogger(__name__)

# (short_name, semantics, description, visibility)
CORE_HOOK_POINTS: list = [
    # --- Run lifecycle ---
    ("run.on_start", "observe", "Run started (payload: run/session/model/agent)"),
    ("run.on_complete", "observe", "Run completed normally"),
    ("run.on_error", "observe", "Run failed (payload: run/session/error)"),
    # --- Tools ---
    ("tool.pre_execute", "blockable", "Before tool execution; block by returning "
                                      "{'blocked': True, 'reason': ...}"),
    ("tool.post_execute", "observe", "After tool execution (payload: success/error)"),
    ("tool.on_error", "observe", "Tool execution raised"),
    # --- LLM ---
    ("llm.pre_request", "transform", "Before LLM request; may inject extra_context "
                                     "(payload: run/session/model/extra_context)"),
    ("llm.post_response", "observe", "After LLM response"),
    ("llm.transform_output", "transform", "Transform assistant text chunks "
                                          "(opt-in; only runs when bound)"),
    # --- Session ---
    ("session.start", "observe", "Session created/opened"),
    ("session.end", "observe", "Session destroyed"),
    ("session.on_message", "blockable", "Before a message enters the session; "
                                       "veto with {'blocked': True} or rewrite "
                                       "via {'data': {'content': ...}}"),
    # --- Approval ---
    ("approval.pre_request", "blockable", "Before an approval request; block to veto"),
    # --- Filesystem ---
    ("fs.pre_write", "blockable", "Before a file write; block to veto", "public"),
    ("fs.post_write", "observe", "After a file write", "public"),
]


def declare_core_hook_points() -> None:
    """Declare all core hook points. Idempotent; safe to call at startup."""
    registry = get_hook_registry()
    for entry in CORE_HOOK_POINTS:
        if len(entry) == 4:
            short_name, semantics, description, visibility = entry
        else:
            short_name, semantics, description = entry
            visibility = "public"
        try:
            registry.declare(
                f"core.{short_name}",
                semantics,
                owner="core",
                description=description,
                visibility=visibility,
            )
        except ValueError:
            pass  # already declared (idempotent)
    logger.info(
        f"Declared {len(CORE_HOOK_POINTS)} core hook points"
    )
