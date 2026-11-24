"""Turn tool phase: batch scheduling (parallel/serial), execution, result
normalisation and termination decision."""
from __future__ import annotations

import logging
import asyncio

from typing import (
    Any,
    Dict,
    List,
    Optional,
)
from boarai.core.event import EventType
from boarai.core.session.run_models import Run
from .loop_config import (
    LoopStep,
    RunLoopState,
    TurnFrame,
    TurnRequest,
)
from .messages import TOOL_REJECTED
from .termination import (
    TerminateKind,
    decide_after_denied,
    decide_after_text,
    decide_after_tools,
    record_denied_batch,
    record_text,
    record_tool_batch,
)

logger = logging.getLogger(__name__)

# Cap the per-file diff embedded in tool-message metadata / WS payloads so a
# huge write cannot bloat session history or a websocket frame.
MAX_UI_FILE_DIFF_CHARS = 20_000


class TurnToolRoundMixin:
    """Tool-round phase of RunExecutor (mixed in via multiple inheritance)."""

    async def _execute_single_tool(
        self,
        session: Any,
        tool_call: Dict[str, Any],
        tool_registry: Any = None,
        agent_name: Optional[str] = None,
        agent_config: Optional[Any] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        # --- Emit tool.executing per-tool (EventBus only, too granular for WS) ---
        func = tool_call.get("function", {}) or {}
        tool_name = func.get("name", "") or tool_call.get("name", "")
        tool_id = tool_call.get("id", "")
        raw_args = func.get("arguments", tool_call.get("arguments", ""))
        try:
            if isinstance(raw_args, str) and raw_args:
                import json as _json

                tool_args = _json.loads(raw_args)
            elif isinstance(raw_args, dict):
                tool_args = raw_args
            else:
                tool_args = {}
        except Exception:
            tool_args = {}

        session_id = getattr(session, "session_id", None)

        # --- Execution-side skill_authoring whitelist (defense in depth) ---
        # turn_preflight filters the WIRE schema for authoring runs, but a
        # deviated or prompt-injected tool_call can still name an unlisted
        # tool here (the registry executes by name). Reject at execution so
        # the isolation contract does not rest on model compliance alone.
        # Fail-open on lookup errors: the gate must never break normal runs.
        if run_id:
            try:
                from boarai.core.session.run_manager import get_run_manager

                _run_dict = get_run_manager().get_run(run_id)
                if _run_dict and _run_dict.get("kind") == "skill_authoring":
                    from .turn_preflight import SKILL_AUTHORING_TOOL_WHITELIST

                    if tool_name not in SKILL_AUTHORING_TOOL_WHITELIST:
                        reason = (
                            f"tool '{tool_name}' is outside the skill-authoring "
                            "whitelist (file/skill tools only)"
                        )
                        logger.info(
                            "Authoring run %s blocked tool %s (whitelist)",
                            run_id, tool_name,
                        )
                        return {
                            "tool_call_id": tool_id,
                            "name": tool_name,
                            "role": "tool",
                            "content": f"[Error] {reason}",
                            "is_error": True,
                            "error_kind": "policy",
                            "error_reason": reason,
                        }
            except Exception:  # noqa: BLE001
                pass

        # --- V2 hook: tool.pre_execute (blockable) ---
        hook_result = await self._cfg.trigger_hook(
            "tool.pre_execute",
            {
                "tool_call_id": tool_id,
                "name": tool_name,
                "arguments": tool_args,
                "run_id": run_id,
                "session_id": session_id,
                "agent_name": agent_name,
            },
            timeout=2.0,
        )
        if hook_result and hook_result.blocked:
            reason = hook_result.reason or "blocked by extension hook"
            logger.info(
                "Tool %s blocked by hook (run=%s): %s", tool_name, run_id, reason
            )
            return {
                "tool_call_id": tool_id,
                "name": tool_name,
                "role": "tool",
                "content": f"[Error] {reason}",
                "is_error": True,
                "error_kind": "hook_blocked",
                "error_reason": reason,
                "blocked_by_hook": True,
                "reason": reason,
            }

        await self._cfg.publish_event(
            EventType.TOOL_EXECUTING,
            {
                "tool_call_id": tool_id,
                "name": tool_name,
                "arguments": tool_args,
                "run_id": run_id,
                "session_id": session_id,
                "agent_name": agent_name,
            },
            "run_manager",
        )

        result = await self._cfg.execute_tool(
            session,
            tool_call,
            tool_registry=tool_registry,
            agent_name=agent_name,
            agent_config=agent_config,
            run_id=run_id or getattr(session, "session_id", None),
            approval_granted=True,
        )

        # --- V2 hook: tool.post_execute (observe) ---
        try:
            result_dict = result if isinstance(result, dict) else {}
            await self._cfg.trigger_hook(
                "tool.post_execute",
                {
                    "tool_call_id": tool_id,
                    "name": tool_name,
                    "run_id": run_id,
                    "session_id": session_id,
                    "success": not bool(result_dict.get("error")),
                    "error": result_dict.get("error"),
                },
                timeout=2.0,
            )
        except Exception:  # noqa: BLE001
            pass
        return result

    async def _execute_tool_batch(
        self,
        session: Any,
        tool_calls: List[Dict[str, Any]],
        *,
        agent_name: Optional[str] = None,
        agent_config: Optional[Any] = None,
        run_id: Optional[str] = None,
        tool_reg: Any = None,
    ) -> List[Any]:
        """Execute a batch of tool_calls, returning results in **exact call order**."""
        if not tool_calls:
            return []

        async def one(tc: Dict[str, Any]) -> Any:
            try:
                return await self._execute_single_tool(
                    session,
                    tc,
                    tool_registry=tool_reg,
                    agent_name=agent_name,
                    agent_config=agent_config,
                    run_id=run_id,
                )
            except Exception as exc:  # noqa: BLE001
                return exc

        if len(tool_calls) == 1 or self._batch_is_sequential(tool_calls, tool_reg):
            return [await one(tc) for tc in tool_calls]
        return list(await asyncio.gather(*(one(tc) for tc in tool_calls)))

    @staticmethod
    def _batch_is_sequential(
        tool_calls: List[Dict[str, Any]],
        tool_reg: Any,
    ) -> bool:
        """Whether the batch contains a tool that must run sequentially."""
        from boarai.core.extension.tool import ExecutionMode, resolve_execution_mode

        for tc in tool_calls:
            func = tc.get("function") or {}
            name = func.get("name", "") or tc.get("name", "")
            definition = None
            try:
                registered = tool_reg.get_tool(name) if tool_reg and name else None
                definition = getattr(registered, "definition", None)
            except Exception:  # noqa: BLE001
                definition = None
            if definition is None:
                return True
            if resolve_execution_mode(definition) == ExecutionMode.SEQUENTIAL.value:
                return True
        return False

    async def _fail_truncated_tool_calls(
        self,
        run: Run,
        session: Any,
        tool_calls: List[Dict[str, Any]],
        message_id: str = "",
        seq: int = 0,
    ) -> None:
        """Refuse every tool call from a message that hit the output token cap."""
        results = []
        for tc in tool_calls:
            tc_id = tc.get("id", "") or ""
            if not tc_id:
                continue
            tc_func = tc.get("function", {}) or {}
            tc_name = tc_func.get("name", "") or tc.get("name", "")
            content = (
                f'Tool call "{tc_name}" was not executed: the response hit the '
                "output token limit, so its arguments may be truncated. "
                "Re-issue the call with complete arguments."
            )
            meta = {
                "tool_call_id": tc_id,
                "tool_name": tc_name,
                "is_error": True,
                "error_kind": "truncated",
                "error_reason": "finish_reason=length",
            }
            session.add_message(role="tool", content=content, metadata=meta)
            session.messages[-1].tool_call_id = tc_id
            results.append({
                "content": content,
                "tool_call_id": tc_id,
                "name": tc_name,
                "is_error": True,
                "error_kind": "truncated",
                "error_reason": "finish_reason=length",
            })

        if not results:
            return
        await self._cfg.save_session(run.session_id)
        await self._broadcaster.broadcast_tool_event(
            run,
            "tool.finished",
            tool_calls,
            tool_results=results,
            message_id=message_id, seq=seq,
        )

    async def _run_tool_round(self, st: RunLoopState, req: TurnRequest, tf: TurnFrame) -> LoopStep:
        """Tool round: truncation refusal, approval gate, execution, normalised persistence, termination policy."""
        agent_config = st.agent_config; agent_name = st.agent_name; diff_tracker = st.diff_tracker
        metrics = st.metrics; readonly_auto = st.readonly_auto; run = st.run
        session = st.session; term_cfg = st.term_cfg; tool_reg = st.tool_reg
        turn_state = st.turn_state
        logger.info(
            "Run %s turn %s: finish_reason=%s, tool_calls=%s (%s)",
            run.run_id,
            st.turn,
            tf.finish_reason,
            len(st.pending_tool_calls),
            [
                tc.get("function", {}).get("name", "?")
                for tc in st.pending_tool_calls
            ],
        )

        # A "length" stop means the provider cut the response at
        # max_tokens, so any tool call in it may carry truncated
        # arguments. Fail the whole batch instead of executing it.
        if tf.finish_reason == "length":
            await self._fail_truncated_tool_calls(
                run, session, st.pending_tool_calls,
                message_id=tf.cur_msg_id, seq=tf.cur_msg_seq,
            )
            metrics.finish_reason = "length"
            logger.warning(
                "Run %s turn %s: max_tokens truncated %s tool call(s); "
                "none executed",
                run.run_id, st.turn, len(st.pending_tool_calls),
            )
            return LoopStep.BREAK

        try:
            max_tools = max(0, int(getattr(term_cfg, "max_tools", 0) or 0))
        except (TypeError, ValueError):
            max_tools = 0
        remaining_tools = (
            max(0, max_tools - turn_state.tool_call_total)
            if max_tools else len(st.pending_tool_calls)
        )
        executable_calls = st.pending_tool_calls[:remaining_tools]
        budget_rejected_calls = st.pending_tool_calls[remaining_tools:]

        def _budget_error_result(tc: Dict[str, Any]) -> Dict[str, Any]:
            function = tc.get("function") or {}
            return {
                "tool_call_id": tc.get("id", "") or "",
                "name": function.get("name") or tc.get("name", ""),
                "role": "tool",
                "content": "[Error] Maximum tool-call budget reached; this call was not executed.",
                "is_error": True,
                "error_kind": "budget",
                "error_reason": "max_tools",
            }

        # Approval decision is delegated entirely to ToolConfirmGate,
        # which consults ToolGuard.evaluate_batch (authoritative):
        # the agent's tool allowlist, allowed_categories and
        # can_auto_approve plus the global auto_approve / approval_mode are
        # all handled there — never duplicated here.
        rejection = {}
        approved = True
        if executable_calls:
            approved = await self._confirm.wait_for_confirmation(
                run,
                executable_calls,
                agent_config=agent_config,
                readonly_auto_approve=readonly_auto,
                session_overrides=(
                    getattr(session, "security_overrides", None) or None
                ),
                out_reasons=rejection,
            )
        if not approved:
            kind = rejection.get("kind", "rejected")
            logger.info(
                "Tools blocked for run %s (kind=%s)",
                run.run_id,
                kind,
            )
            rejected_results = []
            policy_reasons = rejection.get("tools") or {}
            policy_first = (
                (rejection.get("reasons") or ["Blocked by security policy"])[
                    0
                ]
                if rejection.get("reasons")
                else "Blocked by security policy"
            )
            for call_index, tc in enumerate(st.pending_tool_calls):
                if call_index >= len(executable_calls):
                    budget_result = _budget_error_result(tc)
                    session.add_message(
                        role="tool",
                        content=budget_result["content"],
                        metadata={
                            "tool_call_id": budget_result["tool_call_id"],
                            "tool_name": budget_result["name"],
                            "is_error": True,
                            "error_kind": "budget",
                            "error_reason": "max_tools",
                        },
                    )
                    session.messages[-1].tool_call_id = budget_result["tool_call_id"]
                    rejected_results.append(budget_result)
                    continue
                tc_id = tc.get("id", "") or ""
                if not tc_id:
                    continue
                tc_func = tc.get("function", {}) or {}
                tc_name = tc_func.get("name", "") or tc.get("name", "")
                if kind == "policy":
                    reason = policy_reasons.get(tc_name) or policy_first
                    # v1.12 (C): structured denial marker with a
                    # recovery path (deepseek-style), so the model
                    # can self-correct instead of dead-ending.
                    # is_error stays True — a bare "rejected"
                    # invites retry loops in headless runs.
                    content = (
                        f"[security: blocked] {reason}\n"
                        "[security: hint] This command hit the "
                        "irreversible-operation deny floor. Do "
                        "NOT retry the same command unchanged. "
                        "To proceed: (a) rewrite it to a "
                        "reversible form (e.g. delete specific "
                        "files instead of recursive wipes), "
                        "(b) continue without this step, or "
                        "(c) ask the user to run it manually "
                        "or adjust shell_safety.deny_patterns "
                        "in the security config."
                    )
                    is_error = True
                    error_kind = "policy"
                    error_reason = reason
                elif kind == "timeout":
                    content = (
                        "[Tool approval timed out — do not retry "
                        "automatically; the user will approve or "
                        "reject in the UI.]"
                    )
                    is_error = True
                    error_kind = "timeout"
                    error_reason = ""
                elif kind == "unattended_deny":
                    # H5: nobody could approve. Tell the model the
                    # denial is structural — a plain "rejected"
                    # would invite retry loops in headless runs.
                    content = (
                        "[Tool blocked: no approval audience is "
                        "available (unattended_policy=deny). Do "
                        "not retry this tool call; finish without "
                        "it or end the turn.]"
                    )
                    is_error = True
                    error_kind = "unattended_deny"
                    error_reason = ""
                else:
                    # User declined the approval prompt.
                    content = TOOL_REJECTED
                    is_error = False
                    error_kind = "rejected"
                    error_reason = ""
                meta = {
                    "tool_call_id": tc_id,
                    "tool_name": tc_name,
                    "is_error": is_error,
                    "error_kind": error_kind,
                    "error_reason": error_reason,
                }
                session.add_message(
                    role="tool",
                    content=content,
                    metadata=meta,
                )
                session.messages[-1].tool_call_id = tc_id
                rejected_results.append(
                    {
                        "content": content,
                        "tool_call_id": tc_id,
                        "name": tc_name,
                        "is_error": is_error,
                        "error_kind": error_kind,
                        "error_reason": error_reason,
                    }
                )
            await self._cfg.save_session(run.session_id)
            # Broadcast the rejection so live UI cards leave "running"
            # immediately instead of waiting for the next reload.
            await self._broadcaster.broadcast_tool_event(
                run,
                "tool.finished",
                st.pending_tool_calls,
                tool_results=rejected_results,
                message_id=tf.cur_msg_id, seq=tf.cur_msg_seq,
            )
            # Breaker: a model re-issuing the same denied batch must not spin
            # paid turns until max_turns. Recorded here because rejected calls
            # never reach record_tool_batch.
            record_denied_batch(turn_state, executable_calls)
            denied_action = decide_after_denied(turn_state, term_cfg)
            if denied_action.kind == TerminateKind.ABORT:
                logger.warning(
                    "Run %s: aborted after repeated denied batches (%s)",
                    run.run_id,
                    denied_action.reason,
                )
                metrics.finish_reason = denied_action.reason
                return LoopStep.BREAK
            st.turn += 1
            return LoopStep.NEXT

        await self._broadcaster.broadcast_tool_event(
            run,
            "tool.start",
            executable_calls,
            message_id=tf.cur_msg_id, seq=tf.cur_msg_seq,
        )

        run._in_tool_execution = True
        try:
            tool_results = await self._execute_tool_batch(
                session,
                executable_calls,
                agent_name=agent_name,
                agent_config=agent_config,
                run_id=run.run_id,
                tool_reg=tool_reg,
            )
            tool_results.extend(
                _budget_error_result(tc) for tc in budget_rejected_calls
            )
        finally:
            run._in_tool_execution = False

        tool_results_data = []
        for idx, result in enumerate(tool_results):
            tc = (
                st.pending_tool_calls[idx]
                if idx < len(st.pending_tool_calls)
                else {}
            )
            tc_id = tc.get("id", "")
            tc_func = tc.get("function", {}) or {}
            tc_name = tc_func.get("name", "") or tc.get("name", "")
            tc_args_raw = tc_func.get(
                "arguments", tc.get("arguments", "")
            )
            try:
                if isinstance(tc_args_raw, str) and tc_args_raw:
                    import json as _json

                    tc_args = _json.loads(tc_args_raw)
                elif isinstance(tc_args_raw, dict):
                    tc_args = tc_args_raw
                else:
                    tc_args = {}
            except Exception:
                tc_args = {}

            if isinstance(result, Exception):
                logger.error("Tool execution failed: %s", result)
                metrics.tool_failures += 1
                err_content = f"Error: {result}"
                meta = {
                    "tool_call_id": tc_id,
                    "tool_name": tc_name,
                    "is_error": True,
                    "error_kind": "execution",
                    "error_reason": str(result),
                }
                if isinstance(tc_args, dict) and tc_args.get("command"):
                    meta["command"] = tc_args.get("command")
                session.add_message(
                    role="tool",
                    content=err_content,
                    metadata=meta,
                )
                session.messages[-1].tool_call_id = tc_id
                tool_results_data.append(
                    {
                        "content": err_content,
                        "tool_call_id": tc_id,
                        "name": tc_name,
                        "arguments": tc_args,
                        "command": meta.get("command"),
                        "is_error": True,
                        "error_kind": "execution",
                        "error_reason": str(result),
                    }
                )
            else:
                # Aggregate file-change traceability (diff) for the
                # run; no-op for non-file tools. Internal keys were
                # already stripped from content/result by run_tools.
                diff_tracker.add(result.get("file_change"))
                raw_content = result.get("content", "")
                from boarai.core.prompt.tool_result_trimmer import (
                    trim as trim_tool_result,
                )

                # Pass the tool's declared CompressStrategy so
                # list-shaped results get structure-aware slimming
                # (enumerations survive instead of being cut).
                trim_strategy = None
                try:
                    _td = (
                        tool_reg.get_tool(tc_name)
                        if tool_reg and tc_name
                        else None
                    )
                    if _td and getattr(_td, "definition", None):
                        _cs = getattr(
                            _td.definition, "compress_strategy", None
                        )
                        trim_strategy = getattr(_cs, "value", _cs)
                except Exception:  # noqa: BLE001
                    trim_strategy = None

                trimmed_content = trim_tool_result(
                    raw_content, strategy=trim_strategy
                )
                result_tc_id = result.get("tool_call_id", tc_id) or tc_id
                tool_name = result.get("name") or tc_name
                tool_args = (
                    result.get("arguments")
                    if isinstance(result.get("arguments"), dict)
                    else tc_args
                )
                command = result.get("command")
                if command is None and isinstance(tool_args, dict):
                    command = tool_args.get("command")
                is_error = bool(result.get("is_error", False))
                if is_error:
                    metrics.tool_failures += 1
                # error_kind MUST be empty for successful tools —
                # the old fallback assigned "execution" even when
                # is_error was False, so the WebUI rendered every
                # successful tool card as failed.
                error_kind = result.get("error_kind") or ""
                if is_error and not error_kind:
                    error_kind = (
                        "permission"
                        if (result.get("result") or {}).get("error")
                        and str(
                            (result.get("result") or {}).get("error")
                        ).startswith("Security:")
                        else "execution"
                    )
                error_reason = result.get("error_reason") or ""
                # UI-only per-file change record (path/type/±/diff)
                # so the WebUI renders an inline "file changed" card
                # with an expandable diff. Never sent to the LLM.
                ui_fc = None
                _fc = result.get("file_change")
                if isinstance(_fc, dict) and _fc.get("path"):
                    ui_fc = dict(_fc)
                    _d = ui_fc.get("diff")
                    if isinstance(_d, str) and len(_d) > MAX_UI_FILE_DIFF_CHARS:
                        ui_fc["diff"] = (
                            _d[:MAX_UI_FILE_DIFF_CHARS]
                            + "\n…(diff truncated)"
                        )
                meta = {
                    "tool_call_id": result_tc_id,
                    "tool_name": tool_name,
                    "is_error": is_error,
                }
                if error_kind:
                    meta["error_kind"] = error_kind
                if error_reason:
                    meta["error_reason"] = error_reason
                if command is not None:
                    meta["command"] = command
                if isinstance(result.get("result"), dict):
                    meta["tool_result"] = result.get("result")
                if ui_fc is not None:
                    meta["file_change"] = ui_fc
                session.add_message(
                    role="tool",
                    content=trimmed_content,
                    metadata=meta,
                )
                session.messages[-1].tool_call_id = result_tc_id
                tool_results_data.append(
                    {
                        "content": trimmed_content,
                        "tool_call_id": result_tc_id,
                        "name": tool_name,
                        "arguments": tool_args,
                        "command": command,
                        "result": result.get("result"),
                        "is_error": is_error,
                        "error_kind": error_kind,
                        "error_reason": error_reason,
                        "file_change": ui_fc,
                    }
                )

        await self._cfg.save_session(run.session_id)

        await self._broadcaster.broadcast_tool_event(
            run,
            "tool.finished",
            st.pending_tool_calls,
            tool_results=tool_results_data,
            message_id=tf.cur_msg_id, seq=tf.cur_msg_seq,
        )

        record_tool_batch(turn_state, st.pending_tool_calls)
        metrics.tool_calls = turn_state.tool_call_total
        action = decide_after_tools(turn_state, term_cfg)
        metrics.stagnation_hits = turn_state.stagnation_hits
        if action.kind == TerminateKind.FORCE_FINAL_TEXT:
            logger.warning(
                "Run %s: termination force_final reason=%s",
                run.run_id,
                action.reason,
            )
            # Keep the hint transient (request-only): persisting it as
            # a user message would permanently fragment the prefix
            # cache and pollute the user-visible history.
            st.pending_inject = action.inject_message or ""
            # Next turn: keep tool schemas on the wire (prefix-cache
            # stability) but never execute tools — model must answer.
            st.tools_enabled = False
            st.wire_tools = True
            # Guarantee one more iteration for final text even on last budgeted turn
            if st.turn + 1 >= st.turn_budget:
                st.turn_budget = st.turn + 2
            st.turn += 1
            return LoopStep.NEXT
        if action.kind == TerminateKind.ABORT:
            logger.warning(
                "Run %s: termination abort reason=%s",
                run.run_id,
                action.reason,
            )
            metrics.finish_reason = action.reason
            return LoopStep.BREAK

        st.turn += 1
        return LoopStep.NEXT

    async def _decide_text_stop(self, st: RunLoopState, tf: TurnFrame) -> LoopStep:
        """With no tool calls, record the stop reason from finish_reason (all three branches end the loop)."""
        metrics = st.metrics; run = st.run; term_cfg = st.term_cfg
        turn_state = st.turn_state
        if tf.finish_reason in ("stop", "end_turn", None):
            logger.info(
                "Run %s completed in %s turns: %s chunks, %s chars "
                "channel=%s",
                run.run_id,
                st.turn + 1,
                st.chunk_count,
                len(st.full_content),
                metrics.channel,
            )
            # Fingerprint this turn's visible text, not cumulative full_content
            record_text(turn_state, tf.text_buffer)
            action = decide_after_text(turn_state, term_cfg, had_tools=False)
            metrics.stagnation_hits = turn_state.stagnation_hits
            metrics.finish_reason = tf.finish_reason or "stop"
            if action.kind == TerminateKind.ABORT:
                logger.warning(
                    "Run %s: text stagnation abort reason=%s",
                    run.run_id,
                    action.reason,
                )
                metrics.finish_reason = action.reason
            return LoopStep.BREAK

        elif tf.finish_reason == "length":
            logger.warning(
                "Run %s hit max_tokens on turn %s", run.run_id, st.turn
            )
            metrics.finish_reason = "length"
            return LoopStep.BREAK

        else:
            logger.warning(
                "Run %s unknown finish_reason=%s",
                run.run_id,
                tf.finish_reason,
            )
            metrics.finish_reason = str(tf.finish_reason or "unknown")
            return LoopStep.BREAK
