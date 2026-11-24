"""Turn preflight phase: run-start broadcast, wire history construction,
request assembly and turn framing."""
from __future__ import annotations

import logging
import time

from typing import (
    Any,
    Dict,
    List,
    Optional,
    Set,
)
from boarai.core.event import EventType
from boarai.core.session.manager import Session
from boarai.core.session.thinking import ReasoningContentManager, StreamingThinkProcessor
from .context_prep import RunContext
from .loop_config import (
    RunLoopState,
    TurnFrame,
    TurnRequest,
)
from .messages import RETRY_NUDGE

logger = logging.getLogger(__name__)

# Schema-level tool whitelist for background skill-authoring runs
# (run.kind == "skill_authoring"). Shell, web, memory and task surfaces are
# out of scope and never offered to the model.
#
# Writes go through skill_manage ONLY — file_write / file_edit are deliberately
# absent. Two consequences, both intended:
#   * a skill (SKILL.md plus its resources) is written atomically through the
#     gate in core.extension.skill.manage, which resolves the absolute path
#     itself, so a relative ".boarai/..." can no longer land in a dead copy
#     inside the session workspace;
#   * the run structurally cannot overwrite or delete some OTHER skill, so no
#     post-run collision snapshot/restore is needed.
# Reading stays available (file_read / file_glob / file_grep) so the author can
# copy real commands out of the project instead of inventing them.
SKILL_AUTHORING_TOOL_WHITELIST = frozenset({
    "file_read", "file_glob", "file_grep",
    "skill_list", "skill_view", "skill_read_resource",
    "skill_manage",
})


def filter_authoring_tools(tools: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Apply the skill_authoring whitelist to an assembled tool-schema list."""
    kept: List[Dict[str, Any]] = []
    for item in tools or []:
        if not isinstance(item, dict):
            continue
        func = item.get("function")
        name = func.get("name") if isinstance(func, dict) else item.get("name")
        if name in SKILL_AUTHORING_TOOL_WHITELIST:
            kept.append(item)
    return kept


class TurnPreflightMixin:
    """Turn preflight phase of RunExecutor (mixed in via multiple inheritance)."""

    def _apply_plan_constraint(
        self,
        session_raw: List[Dict[str, Any]],
        ctx: RunContext,
        turn: int,
    ) -> None:
        """Inject PLAN channel constraint into the latest user message (turn 0)."""
        if turn != 0 or not ctx.route or not ctx.route.plan_constraint:
            return
        constraint = ctx.route.plan_constraint
        for i in range(len(session_raw) - 1, -1, -1):
            if session_raw[i].get("role") == "user":
                content = session_raw[i].get("content") or ""
                if constraint not in content:
                    session_raw[i]["content"] = content + constraint
                break

    async def _announce_run_start(self, st: RunLoopState) -> None:
        """Run the run.on_start hook, decide the vision sidecar, then emit the run.created event and the WS broadcast."""
        agent_name = st.agent_name; ctx = st.ctx; max_turns = st.max_turns
        metrics = st.metrics; model = st.model; readonly_auto = st.readonly_auto
        run = st.run; session = st.session; user_input = st.user_input
        # --- V2 hook: run.on_start (observe) ---
        await self._cfg.trigger_hook(
            "run.on_start",
            {
                "run_id": run.run_id,
                "session_id": run.session_id,
                "task_id": run.task_id,
                "model": model,
                "agent_name": agent_name,
                "user_input": user_input,
            },
        )

        # Capability-driven three-state vision routing (see
        # core/session/run/model_resolver.py): NATIVE pixels for a
        # vision-capable main model, SIDECAR description text when a vision
        # slot must see for it, TEXT_ONLY pre-flight omission for a declared
        # text-only model without a slot. `model_source` surfaces the mode to
        # the UI / observability; image-free runs keep "default".
        try:
            from boarai.core.session.run.model_resolver import (
                has_image_attachments,
                ImageRoute,
                resolve_image_route,
            )

            if has_image_attachments(session):
                route = resolve_image_route(session, model)
                run._model_source = {
                    ImageRoute.NATIVE: "native",
                    ImageRoute.SIDECAR: "vision_sidecar",
                    ImageRoute.TEXT_ONLY: "text_only",
                }[route]
        except Exception as sidecar_err:  # noqa: BLE001
            logger.debug("Vision route judgement skipped: %s", sidecar_err)

        # --- Publish run.created so all extensions know the run context ---
        created_payload = {
            "run_id": run.run_id,
            "session_id": run.session_id,
            "task_id": run.task_id,
            "status": "running",
            "model": model,
            "agent_name": agent_name,
            "thinking_mode": (
                getattr(ctx.route, "thinking_mode", "none") if ctx.route else "none"
            ),
            "channel": getattr(metrics, "channel", "") if metrics else "",
            "route_reason": getattr(metrics, "route_reason", "") if metrics else "",
            "tools_enabled": st.tools_enabled,
            "max_turns": max_turns,
            "max_tools": ctx.max_tools,
            "readonly_auto_approve": readonly_auto,
            # Model provenance: "default" | "native" | "vision_sidecar" |
            # "text_only" (image-bearing runs only — see
            # core/session/run/model_resolver.py).
            "model_source": getattr(run, "_model_source", "default"),
        }
        await self._cfg.publish_event(
            EventType.RUN_CREATED, created_payload, "run_manager"
        )
        # WS: broadcast to frontend (run.created → type "created")
        ws_payload = {**created_payload, "type": "created"}
        await self._broadcaster.broadcast_to_ws(run.run_id, ws_payload)

    async def _build_wire_history(self, st: RunLoopState) -> List[Dict[str, Any]]:
        """Convert session.messages into this request's wire history (attachment downsampling mutates the in-memory copy only, never the session)."""
        from boarai.core.session import attachments as _attachments
        run = st.run; session = st.session
        session_raw = []
        # Replay governance (NATIVE route only): decide once per turn which
        # historical images keep their pixels (window + byte budget); the
        # rest degrade to cached sidecar descriptions or readable-path
        # notes. Memory-only — the session history is never rewritten.
        inline_images: Optional[Set[str]] = None
        if getattr(run, "_model_source", "") in ("native", "default"):
            try:
                from boarai.core.llm import get_llm_manager

                _mgr = get_llm_manager()
                inline_images = _attachments.plan_inline_images(
                    [
                        (m.metadata or {}).get("attachments") or []
                        for m in session.messages
                        if m.role == "user" and (m.metadata or {}).get("attachments")
                    ],
                    _mgr.get_vision_replay_window(),
                    _mgr.get_vision_replay_max_bytes(),
                )
            except Exception as gov_err:  # noqa: BLE001 - governance best-effort
                logger.debug("Image replay governance skipped: %s", gov_err)
                inline_images = None
        for m in session.messages:
            entry = {"role": m.role, "content": m.content}
            if m.tool_calls:
                entry["tool_calls"] = m.tool_calls
            if m.role == "tool":
                entry["tool_call_id"] = m.tool_call_id or ""
            elif m.tool_call_id:
                entry["tool_call_id"] = m.tool_call_id
            # Echo thinking text as reasoning_content for multi-turn
            if m.role == "assistant" and getattr(m, "thinking", None):
                entry["reasoning_content"] = m.thinking
            session_raw.append(entry)
            # Downscale attachment references for user messages — in
            # memory only, never written back to session history.
            if (
                m.role == "user"
                and (m.metadata or {}).get("attachments")
            ):
                if getattr(run, "_model_source", "") == "vision_sidecar":
                    # Dual-path vision routing: describe images via the
                    # vision capability and inject TEXT so the main
                    # (text-only) model never sees image_url blocks.
                    from boarai.core.session.run.vision_sidecar import (
                        build_sidecar_notes,
                    )

                    sidecar_notes = await build_sidecar_notes(
                        run.session_id,
                        m.metadata.get("attachments") or [],
                    )
                    entry["content"] = (
                        m.content + sidecar_notes
                        if m.content
                        else sidecar_notes.strip()
                    )
                elif getattr(run, "_model_source", "") == "text_only":
                    # Declared text-only main model with no vision slot:
                    # strip images pre-flight into omission notes that keep
                    # a readable-path handle — no wasted gateway 400
                    # round-trip, and the strip-retry fuse stays armed for
                    # lying capability declarations.
                    new_content, _ = await _attachments.build_attachment_messages(
                        m.content,
                        m.metadata.get("attachments"),
                        st.attach_files_dir,
                        image_disposition="omit",
                    )
                    entry["content"] = new_content
                elif getattr(run, "_vision_pixels_refused", False):
                    # The strip-retry fuse fired earlier this run: the
                    # provider rejected pixels despite their declaration.
                    # Re-plan this turn's attachments as omission notes
                    # instead of repeating the 400 round-trip every turn.
                    new_content, _ = await _attachments.build_attachment_messages(
                        m.content,
                        m.metadata.get("attachments"),
                        st.attach_files_dir,
                        image_disposition="omit",
                    )
                    entry["content"] = new_content
                else:
                    new_content, img_msgs = await _attachments.build_attachment_messages(
                        m.content,
                        m.metadata.get("attachments"),
                        st.attach_files_dir,
                        inline_images=inline_images,
                    )
                    entry["content"] = new_content
                    session_raw.extend(img_msgs)
        # Tool-queued wire-only image messages (e.g. file_read on an image
        # while the main model is vision-capable): appended after the newest
        # history entry, memory-only, re-injected on every later turn of
        # this run (run-scoped, never persisted to session history). The
        # queue is deduplicated + capped by the tool side; underscore keys
        # (bookkeeping like ``_src``) never travel on the wire.
        for wire_img in getattr(run, "_pending_wire_images", None) or []:
            session_raw.append(
                {k: v for k, v in wire_img.items() if not k.startswith("_")}
            )
        return session_raw

    def _log_tool_change(self, st: RunLoopState, llm_tools: Any) -> None:
        """Journal a change in the model-visible tool set."""
        try:
            names = self._wire_tool_names(llm_tools)
            session = st.session
            previous = st.wired_tool_names
            if previous is None and session is not None:
                # A fresh TurnState does not mean a fresh session: the process
                # may have restarted, or this run may be bound to a session that
                # already recorded tool changes. Replay what the session knows
                # so this turn reports a real delta instead of dropping it
                # (force_final) or re-announcing every tool as added (restart).
                previous = self._session_wire_baseline(session)
            st.wired_tool_names = names
            if previous is not None and set(previous) == set(names):
                return
            if session is None:
                return
            if previous is None and not names:
                # Nothing on the wire and nothing logged yet: there is no
                # baseline worth recording, and an empty entry is pure noise.
                return
            previous_names = list(previous or [])
            previous_set = set(previous_names)
            current_set = set(names)
            added = [n for n in names if n not in previous_set]
            removed = [n for n in previous_names if n not in current_set]
            if not added and not removed:
                return
            if previous is None:
                reason = "initial"
            elif not st.wire_tools:
                reason = "force_final"
            else:
                reason = f"turn:{st.turn}"
            session.record_tool_change(added, removed, reason=reason)
        except Exception as e:  # noqa: BLE001
            logger.debug("tool_change journaling skipped: %s", e)

    @staticmethod
    def _session_wire_baseline(session: Any) -> Optional[List[str]]:
        """Replay a session's recorded tool changes into its current wire set."""
        changes = getattr(session, "tool_changes", None)
        if not changes:
            return None
        baseline: set = set()
        for change in changes:
            baseline.update(change.get("toolsAdded") or ())
            baseline.difference_update(change.get("toolsRemoved") or ())
        return sorted(baseline)

    @staticmethod
    def _wire_tool_names(llm_tools: Any) -> List[str]:
        """Tool names a schema list puts on the wire, in order."""
        names: List[str] = []
        for item in llm_tools or []:
            if not isinstance(item, dict):
                continue
            func = item.get("function")
            name = func.get("name") if isinstance(func, dict) else item.get("name")
            if name:
                names.append(str(name))
        return names

    async def _prepare_turn_request(self, st: RunLoopState, session_raw: List[Dict[str, Any]]) -> TurnRequest:
        """Assemble this turn's request: capability injection, model fallback resolution, extra_context, the pre_request hook, transient hints and FAST-channel selection."""
        req = TurnRequest()
        agent_name = st.agent_name; assembler = st.assembler; ctx = st.ctx
        ext_mgr = st.ext_mgr; last_assistant_response = st.last_assistant_response; last_user_input = st.last_user_input
        memory_horizon = st.memory_horizon; model = st.model; proactive_memory = st.proactive_memory
        run = st.run; session = st.session; tool_reg = st.tool_reg
        user_msg_count = sum(1 for m in session.messages if m.role == "user")
        if (
            st.turn == 0
            and user_msg_count == 1
            and session.session_capability_index() is None
        ):
            from boarai.core.prompt.assembler import PromptAssembler

            # Heal missing workspace_path (create path failed / old sessions)
            if not getattr(session, "workspace_path", None):
                try:
                    from boarai.core.session.workspace import get_default_workspace_for_session

                    _default_ws = get_default_workspace_for_session(run.session_id)
                    if _default_ws:
                        session.workspace_path = _default_ws
                        logger.info(
                            "Session %s workspace healed to default: %s",
                            run.session_id,
                            _default_ws,
                        )
                except Exception as heal_err:
                    logger.debug("workspace heal skipped: %s", heal_err)

            try:
                from boarai.core.session.workspace import resolve_session_workspace

                _ws = resolve_session_workspace(session)
            except Exception:
                _ws = None
            ctx_msg = PromptAssembler._build_session_capability(
                session_id=run.session_id,
                workspace=_ws,
            )
            if session_raw and session_raw[0].get("role") == "system":
                session_raw.insert(1, ctx_msg)
                persist_index = 1
            else:
                session_raw.insert(0, ctx_msg)
                persist_index = 0
            # Persist with the SAME role ("user") and the SAME position so
            # later turns load a byte-identical prefix. Previously we wrote
            # role="system" here, which flipped the message to a system
            # message on the next turn and broke the prefix-cache key.
            # The seq-0 / position / renumbering semantics live in
            # Session.insert_capability_message (single write entry point).
            session.insert_capability_message(
                ctx_msg["role"], ctx_msg["content"], persist_index
            )
            await self._cfg.save_session(run.session_id)

        self._apply_plan_constraint(session_raw, ctx, st.turn)
        requested_model = model or ""
        req.fallback_fm = self._cfg.fallback_machine()
        req.provider = (
            requested_model.split("/", 1)[0]
            if "/" in requested_model
            else "default"
        )
        req.resolved_model = req.fallback_fm.get_suggested_model(
            run.session_id,
            req.provider,
            requested_model,
        ) or None

        if st.turn == 0:
            try:
                from boarai.core.prompt.context_detector import detect_context_window

                effective = detect_context_window(
                    model=req.resolved_model or "",
                    provider=req.provider,
                )
                logger.debug(
                    "Context window: %s tokens (model=%s, provider=%s)",
                    effective,
                    req.resolved_model or "default",
                    req.provider,
                )
            except Exception as e:
                logger.debug("Context window detection skipped: %s", e)

        extra_ctx: dict = {}
        if memory_horizon:
            extra_ctx["memory_horizon"] = memory_horizon
        if proactive_memory:
            extra_ctx["proactive_memory"] = proactive_memory

        # --- V2 hook: llm.pre_request (inject context, best-effort) ---
        try:
            from boarai.core.extension import get_hook_registry
            pre = await get_hook_registry().trigger(
                "core.llm.pre_request",
                {
                    "run_id": run.run_id,
                    "session_id": run.session_id,
                    "model": req.resolved_model,
                    "extra_context": extra_ctx,
                },
                timeout=1.0,
            )
            if pre and isinstance(pre.data, dict) and pre.data.get("extra_context"):
                extra_ctx.update(pre.data["extra_context"])
        except Exception:  # noqa: BLE001
            pass

        req.assembled = assembler.assemble_messages(
            user_input="",
            session_messages=session_raw,
            agent_name=agent_name,
            extra_context=extra_ctx,
            extension_manager=ext_mgr,
            tool_registry=tool_reg,
            session_id=run.session_id,
            last_user_input=last_user_input,
            last_assistant_response=last_assistant_response,
            memory_horizon=memory_horizon,
            proactive_memory=proactive_memory,
        )

        # kind=skill_authoring: deterministic schema-level tool whitelist.
        # Applied per request (the shared prefix cache keeps the full list;
        # authoring sessions are dedicated so nothing else observes this).
        if getattr(run, "kind", "chat") == "skill_authoring":
            req.assembled.tools = filter_authoring_tools(
                getattr(req.assembled, "tools", None)
            )

        # Termination hint travels on THIS request only (transient) —
        # not persisted, so subsequent runs keep a clean prefix.
        if st.pending_inject:
            req.assembled.messages.append(
                {"role": "user", "content": st.pending_inject}
            )
            st.pending_inject = ""

        # A request must never end on a bare assistant message —
        # Anthropic-style APIs and strict gateways reject it. This
        # happens on reuse-last-user retries where history ends with
        # the previous answer. Append a transient retry nudge.
        if (
            req.assembled.messages
            and (req.assembled.messages[-1].get("role") or "") == "assistant"
        ):
            req.assembled.messages.append(
                {"role": "user", "content": RETRY_NUDGE}
            )

        # FAST channel: no tools on the wire; termination-forced turns
        # keep tools on the wire (prefix-cache stability) but execution
        # stays gated by tools_enabled.
        req.llm_tools = req.assembled.tools if st.wire_tools else None
        self._log_tool_change(st, req.llm_tools)
        return req

    async def _open_turn(self, st: RunLoopState, req: TurnRequest) -> TurnFrame:
        """Open a turn: turn-0 token estimate report, thinking handler, assistant placeholder and message anchors."""
        tf = TurnFrame()
        agent_name = st.agent_name; ctx = st.ctx; metrics = st.metrics
        model = st.model; run = st.run; session = st.session
        if st.turn == 0:
            te = req.assembled.token_estimates
            st.turn0_token_estimates = te
            if isinstance(te, dict):
                # Explicit semantic label: these estimates are a TURN-0
                # snapshot of the static prefix — they do NOT track
                # history growth during the run. The authoritative
                # context size is cache_requests[].input_tokens.
                te["snapshot"] = "turn0"
            logger.info(
                "Run %s: assembly channel=%s Z1=%s Z2=%s Z3=%s Tools=%s "
                "Cached=%s HitRate=%s%% tools_enabled=%s",
                run.run_id,
                metrics.channel,
                te.get("system", 0),
                te.get("memory", 0),
                te.get("recent", 0) + te.get("current", 0),
                te.get("tools_param", 0) if st.tools_enabled else 0,
                te.get("total_cached", 0),
                te.get("estimated_hit_rate_pct", "?"),
                st.tools_enabled,
            )
            # --- Publish assembly token estimates (EventBus only) ---
            await self._cfg.publish_event(
                EventType.RUN_ASSEMBLY,
                {
                    "run_id": run.run_id,
                    "session_id": run.session_id,
                    "zone1_tokens": te.get("system", 0),
                    "zone2_tokens": te.get("memory", 0),
                    "zone3_tokens": te.get("recent", 0) + te.get("current", 0),
                    "tool_tokens": te.get("tools_param", 0),
                    "cached_tokens": te.get("total_cached", 0),
                    "cache_hit_rate_pct": te.get("estimated_hit_rate_pct", 0),
                },
                "run_manager",
            )

        tf.think_processor = StreamingThinkProcessor()
        tf.reasoning_mgr = ReasoningContentManager()
        tf.thinking_buffer = ""
        tf.text_buffer = ""
        # Wall-clock window of this turn's reasoning stream so the
        # elapsed thinking time can be persisted with the message.
        tf.thinking_started_at = None
        tf.thinking_ended_at = None
        st.current_turn_text = ""
        # Mirror the reset onto the Run record: reconnecting clients resume
        # from run.current_turn_text, so a stale previous-turn value would be
        # splatted onto this turn's fresh empty placeholder bubble.
        run.current_turn_text = ""
        tf.thinking_kwargs = self._thinking_kwargs(ctx, req.resolved_model or model)
        st.pending_tool_calls = None
        tf.finish_reason = None
        tf.usage_counted = False  # one usage chunk counts once per turn
        # Streaming-save throttle state, reset per turn because the
        # turn-end save below is the durability boundary.
        tf.chunks_since_save = 0
        tf.last_stream_save = time.monotonic()

        # Snapshot the producing role + model onto the message so history
        # keeps correct attribution after session agent/model changes.
        session.add_message(
            "assistant",
            "",
            metadata={
                "agent_name": agent_name,
                "model": model or "",
            },
        )
        tf.assistant_msg_idx = len(session.messages) - 1
        # Per-turn message id so WS clients can route stream chunks /
        # thinking / tool events to the correct assistant bubble.
        tf.cur_msg_id = (
            session.messages[tf.assistant_msg_idx].message_id
            if 0 <= tf.assistant_msg_idx < len(session.messages)
            else ""
        )
        # Per-turn seq so live events carry the same cursor as the
        # persisted message, letting clients align stream vs. disk.
        tf.cur_msg_seq = (
            session.messages[tf.assistant_msg_idx].seq
            if 0 <= tf.assistant_msg_idx < len(session.messages)
            else 0
        )

        tf.stream_completed = False
        tf.vision_stripped = False
        return tf
