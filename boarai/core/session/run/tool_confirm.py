"""Tool execution confirmation gate."""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from boarai.core.event import EventType, publish_event

if TYPE_CHECKING:
    from boarai.core.session.run.broadcaster import RunBroadcaster
    from boarai.core.session.run_models import Run

logger = logging.getLogger(__name__)


class ToolConfirmGate:
    """Wait for user/security approval before tool batches execute."""

    def __init__(self, broadcaster: "RunBroadcaster") -> None:
        self._broadcaster = broadcaster
        # confirmation_id -> entry {run_id, event, approved, resolved, tool_info}
        self._pending_confirmations: Dict[str, Dict[str, Any]] = {}
        self._tool_auto_execute = False
        # session_id -> set of tool names approved "always allow" this session
        self._session_grants: Dict[str, set] = {}
        # session_id -> set of ``<tool>:dir:<folder>`` keys approved at folder
        # scope ("always allow this directory"). File-op approvals are
        # non-grantable by tool name (the signature is too coarse to pin a
        # path), so this is the only scope that can remember them.
        self._folder_grants: Dict[str, set] = {}

    def set_tool_auto_execute(self, auto: bool) -> None:
        self._tool_auto_execute = auto
        logger.info("Tool auto-execute set to: %s", auto)

    def clear_session_grants(self, session_id: str) -> None:
        """Drop all session-scope grants when a session ends (memory hygiene)."""
        self._session_grants.pop(session_id, None)
        self._folder_grants.pop(session_id, None)

    def confirm_tool(
        self,
        run_id: str,
        confirmation_id: str,
        approved: bool,
        grant_scope: str = "once",
    ) -> bool:
        """Resolve a pending confirmation (idempotent, race-safe)."""
        entry = self._pending_confirmations.get(confirmation_id)
        if not entry or entry.get("run_id") != run_id:
            logger.warning(
                "Confirm ignored: no pending confirmation %s for run %s",
                confirmation_id,
                run_id,
            )
            return False
        if entry.get("resolved"):
            logger.info("Confirm already resolved: %s", confirmation_id)
            return False

        entry["approved"] = bool(approved)
        entry["resolved"] = True
        event = entry.get("event")
        if event and not event.is_set():
            event.set()

        # Session-scope grant: remember per-tool *signatures* so future batches
        # skip re-prompting only for the same tool+command headword (P0-2 fix).
        # A shell grant is ``shell_execute:curl``, NOT ``shell_execute`` — so
        # approving one curl command never waives approval for arbitrary future
        # shell commands.
        if approved and grant_scope == "session":
            session_id = entry.get("session_id")
            sigs = set(entry.get("signatures") or [])
            if session_id and sigs:
                self._session_grants.setdefault(session_id, set()).update(sigs)
                logger.info(
                    "Session grant for %s: %s", session_id, ", ".join(sorted(sigs))
                )

        # Folder-scope grant: the approved file operations are remembered per
        # (tool, directory). Only signatures that actually pin a directory are
        # in ``folder_keys``; a batch with no file path grants nothing.
        if approved and grant_scope == "folder":
            session_id = entry.get("session_id")
            folder_keys = set(entry.get("folder_keys") or ())
            if session_id and folder_keys:
                self._folder_grants.setdefault(session_id, set()).update(
                    folder_keys
                )
                logger.info(
                    "Folder grant for %s: %s",
                    session_id, ", ".join(sorted(folder_keys)),
                )

        self._audit(
            "approve" if approved else "deny",
            run_id=run_id,
            confirmation_id=confirmation_id,
            tools=entry.get("tool_info", []),
            grant_scope=grant_scope,
        )
        logger.info(
            "Tool confirmation for run %s (%s): %s",
            run_id,
            confirmation_id,
            "approved" if approved else "rejected",
        )
        return True

    def cleanup_run(self, run_id: str) -> None:
        """Drop + resolve all pending confirmations for a run (cancellation)."""
        for cid, entry in list(self._pending_confirmations.items()):
            if entry.get("run_id") == run_id:
                event = entry.get("event")
                if event and not event.is_set():
                    event.set()  # release any waiter
                del self._pending_confirmations[cid]
        if run_id in self._pending_confirmations:
            self._pending_confirmations.pop(run_id, None)

    async def wait_for_confirmation(
        self,
        run: "Run",
        tool_calls: List[Dict[str, Any]],
        *,
        agent_config: Any = None,
        readonly_auto_approve: bool = False,
        session_overrides: Optional[Dict[str, Any]] = None,
        out_reasons: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Decide + (if needed) wait for user approval of a tool batch."""
        decisions = self._evaluate(run, tool_calls, agent_config, session_overrides)

        # F4: the auto-execute toggle skips the interactive prompt ONLY — it
        # never skips policy denials (forbidden lists, session deny mode,
        # shell hard floors). Evaluate first; a denied batch is rejected even
        # with auto-execute on.
        if self._tool_auto_execute:
            denied = [d for d in decisions if d.verdict == "denied"]
            tool_info_auto = self._build_tool_info(
            tool_calls, decisions, session_id=run.session_id
        )
            if denied:
                self._audit(
                    "denied_by_policy",
                    run_id=run.run_id,
                    session_id=run.session_id,
                    tools=tool_info_auto,
                    reasons=[d.reason for d in denied],
                )
                if out_reasons is not None:
                    out_reasons["kind"] = "policy"
                    out_reasons["reasons"] = [d.reason for d in denied]
                    out_reasons["tools"] = {
                        d.tool_name: d.reason for d in denied if d.tool_name
                    }
                logger.info(
                    "Run %s: auto-execute blocked by policy (%s denied)",
                    run.run_id,
                    len(denied),
                )
                return False
            self._audit(
                "auto_execute",
                run_id=run.run_id,
                session_id=run.session_id,
                tools=tool_info_auto,
            )
            return True

        # Compute per-call grant signatures (tool + shell command prefix).
        signatures = self._compute_signatures(tool_calls, decisions)
        # Only grantable signatures may be remembered by a session grant —
        # non-grantable (chained / content-sensitive) approvals can never be
        # grant-skipped, so persisting them would be dead weight and look
        # like "always allow silently ignored" on the next prompt.
        grantable_signatures = [
            sig for d, sig in zip(decisions, signatures)
            if getattr(d, "grantable", True)
        ]

        # Session-scope grants only (v1.15: permanent/global grants removed):
        # previously-approved signatures skip re-prompting within this session
        # (fail-open, defense-in-depth only — they never override a `denied`
        # verdict, and never a content-sensitive non-grantable approval).
        granted = self._session_grants.get(run.session_id, set())
        if granted:
            for d, sig in zip(decisions, signatures):
                if not (d.verdict == "need_approval" and d.grantable):
                    continue
                if sig in granted:
                    d.verdict = "auto_allow"
                    d.reason = "Previously approved for this session"

        # Folder-scope grants: a file operation whose directory was approved
        # for this tool runs without prompting. Applies to ``need_approval``
        # only — the absolute write floor is ``denied`` and is never waived
        # by a grant (protect-the-protector).
        folder_keys = self._folder_keys_for(tool_calls, run.session_id)
        folder_granted = self._folder_grants.get(run.session_id, set())
        if folder_granted:
            for d, key in zip(decisions, folder_keys):
                if d.verdict != "need_approval" or not key:
                    continue
                if key in folder_granted:
                    d.verdict = "auto_allow"
                    d.reason = "Folder approved for this session"

        tool_info = self._build_tool_info(
            tool_calls, decisions, folder_keys, session_id=run.session_id
        )

        # --- V2 hook: approval.pre_request (blockable) ---
        # Fires before any approval request is surfaced to the user (WS
        # broadcast). A handler returning {"blocked": True, ...} vetoes the
        # entire batch without prompting.
        if any(d.verdict == "need_approval" for d in decisions):
            try:
                from boarai.core.extension import get_hook_registry

                _pre = await get_hook_registry().trigger(
                    "core.approval.pre_request",
                    {
                        "run_id": run.run_id,
                        "session_id": run.session_id,
                        "tools": tool_info,
                    },
                    timeout=2.0,
                )
                if _pre is not None and _pre.blocked:
                    # Audit is best-effort and must NEVER swallow the veto
                    # (an audit failure would fall through to the approval UI).
                    try:
                        self._audit(
                            "denied_by_hook",
                            run_id=run.run_id,
                            session_id=run.session_id,
                            tools=tool_info,
                            reason=_pre.reason or "blocked by approval hook",
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    if out_reasons is not None:
                        out_reasons["kind"] = "hook"
                        out_reasons["reasons"] = [_pre.reason or "blocked by approval hook"]
                        out_reasons["tools"] = {}
                    logger.info(
                        "Run %s: approval request vetoed by hook: %s",
                        run.run_id, _pre.reason,
                    )
                    return False
            except Exception:  # noqa: BLE001 — hooks must never break approval
                pass

        # 1. Any tool denied by policy → reject the whole batch.
        if any(d.verdict == "denied" for d in decisions):
            denied = [d for d in decisions if d.verdict == "denied"]
            self._audit(
                "denied_by_policy",
                run_id=run.run_id,
                session_id=run.session_id,
                tools=tool_info,
                reasons=[d.reason for d in denied],
            )
            logger.info(
                "Run %s: tool batch rejected by policy (%s denied)",
                run.run_id,
                len(denied),
            )
            if out_reasons is not None:
                out_reasons["kind"] = "policy"
                out_reasons["reasons"] = [d.reason for d in denied]
                out_reasons["tools"] = {
                    d.tool_name: d.reason for d in denied if d.tool_name
                }
            return False

        # 2. All tools auto-allow → run without asking. No broadcast, so the
        #    frontend never sees a phantom approval prompt.
        if all(d.verdict == "auto_allow" for d in decisions):
            self._audit(
                "auto_allow",
                run_id=run.run_id,
                session_id=run.session_id,
                tools=tool_info,
            )
            logger.debug(
                "Run %s: tool batch auto-allowed (%s tools)",
                run.run_id,
                len(tool_info),
            )
            return True

        # H5: unattended policy — when nobody can answer (no WS callback for
        # this run and no client subscribed to its session), resolve the
        # gated batch immediately per tool_guard.unattended_policy instead of
        # hanging until pending_timeout ("ask" keeps the legacy wait).
        policy = self._unattended_policy()
        if policy != "ask":
            try:
                attended = self._broadcaster.has_audience(
                    run.run_id, run.session_id
                )
            except Exception:  # noqa: BLE001 — fail-safe: assume attended
                attended = True
            if not attended:
                self._audit(
                    f"unattended_{policy}",
                    run_id=run.run_id,
                    session_id=run.session_id,
                    tools=tool_info,
                )
                if out_reasons is not None:
                    out_reasons["kind"] = f"unattended_{policy}"
                    if policy == "deny":
                        out_reasons["reasons"] = [
                            "No approval audience; unattended_policy=deny"
                        ]
                logger.info(
                    "Run %s: unattended policy '%s' resolved gated batch",
                    run.run_id,
                    policy,
                )
                return policy == "approve"

        # 3. Some tools need approval → ask the user.
        confirmation_id = str(uuid.uuid4())
        event = asyncio.Event()
        self._pending_confirmations[confirmation_id] = {
            "run_id": run.run_id,
            "session_id": run.session_id,
            "event": event,
            "approved": False,
            "resolved": False,
            "tool_info": tool_info,
            "signatures": grantable_signatures,
            # Only the first folder that can actually be granted, and only
            # one: the card names exactly one directory, so remembering every
            # directory in the batch would grant folders the operator never
            # saw. Shell calls contribute nothing (their grant is the command
            # headword, not a directory).
            "folder_keys": [key for key in folder_keys if key][:1],
        }

        await publish_event(
            EventType.TOOL_PENDING,
            {
                "run_id": run.run_id,
                "session_id": run.session_id,
                "confirmation_id": confirmation_id,
                "tools": tool_info,
            },
            "run_manager",
        )
        await self._broadcaster.broadcast_to_ws(
            run.run_id,
            {
                "type": EventType.TOOL_PENDING.value,
                "confirmation_id": confirmation_id,
                "tools": tool_info,
            },
        )

        timeout = self._approval_timeout()
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Tool confirmation timeout (%ss) for run %s (%s)",
                timeout,
                run.run_id,
                confirmation_id,
            )

        entry = self._pending_confirmations.pop(confirmation_id, None) or {}
        approved = bool(entry.get("approved", False))

        if not entry.get("resolved"):
            self._audit(
                "timeout",
                run_id=run.run_id,
                session_id=run.session_id,
                confirmation_id=confirmation_id,
                tools=entry.get("tool_info", tool_info),
            )
            if out_reasons is not None:
                out_reasons["kind"] = "timeout"
        elif not approved and out_reasons is not None:
            out_reasons["kind"] = "rejected"
        return approved

    # ---- Internal helpers ----

    @staticmethod
    def _shell_command_for(tc: Dict[str, Any], name: str) -> Optional[str]:
        """Extract the shell command string for a tool call (None if not shell)."""
        try:
            from boarai.core.security import get_security_manager

            sec = get_security_manager()
            guard = sec.tool_guard if sec and sec.is_initialized else None
            if guard is not None and name:
                cmd, _ = guard.extract_shell_command(tc, name)
                return cmd
        except Exception:  # noqa: BLE001
            pass
        return None

    def list_pending(self) -> List[Dict[str, Any]]:
        """Snapshot of unresolved confirmations (for the approvals API)."""
        out = []
        # snapshot: another run may insert entries while we iterate
        for cid, entry in list(self._pending_confirmations.items()):
            if entry.get("resolved"):
                continue
            out.append(
                {
                    "confirmation_id": cid,
                    "run_id": entry.get("run_id", ""),
                    "session_id": entry.get("session_id", ""),
                    "tools": entry.get("tool_info", []),
                }
            )
        return out

    @staticmethod
    def _compute_signatures(
        tool_calls: List[Dict[str, Any]], decisions: List,
    ) -> List[str]:
        """Per-call grant signatures (tool name + shell command headword)."""
        try:
            from boarai.core.security import get_security_manager
            from boarai.core.security.tool_guard import ToolGuard

            sec = get_security_manager()
            guard = sec.tool_guard if sec and sec.is_initialized else None
        except Exception:  # noqa: BLE001
            guard = None

        sigs: List[str] = []
        for tc, d in zip(tool_calls or [], decisions):
            name = getattr(d, "tool_name", "") or ""
            cmd = None
            if guard is not None and name:
                cmd, _ = guard.extract_shell_command(tc, name)
            sigs.append(ToolGuard.shell_command_signature(name, cmd))
        return sigs

    def _evaluate(
        self,
        run: "Run",
        tool_calls: List[Dict[str, Any]],
        agent_config: Any,
        session_overrides: Optional[Dict[str, Any]] = None,
    ) -> List:
        """Authoritative approval verdicts via ToolGuard (fail-safe → ask)."""
        try:
            from boarai.core.security import get_security_manager
            from boarai.core.security.tool_guard import ApprovalDecision

            sec = get_security_manager()
            if sec.is_initialized:
                return sec.evaluate_batch(
                    tool_calls, agent_config,
                    session_overrides=session_overrides,
                    session_id=getattr(run, "session_id", None),
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("Approval evaluation failed, fallback to ask: %s", e)
        from boarai.core.security.tool_guard import ApprovalDecision

        return [
            ApprovalDecision(
                (tc.get("function", {}) or {}).get("name", "") or tc.get("name", ""),
                "need_approval",
                reason="Security evaluator unavailable",
            )
            for tc in tool_calls or []
        ]

    @staticmethod
    def _security_guard():
        """Initialized ToolGuard or None (all helpers below fail safe)."""
        try:
            from boarai.core.security import get_security_manager

            sec = get_security_manager()
            if sec.is_initialized:
                return sec.tool_guard
        except Exception:  # noqa: BLE001
            pass
        return None

    def _effective_approval_mode(
        self, session_overrides: Optional[Dict[str, Any]]
    ) -> str:
        """Session-override-aware effective mode (on/off/deny)."""
        guard = self._security_guard()
        if guard is None:
            return "on"
        try:
            return guard.effective_approval_mode(session_overrides)
        except Exception:  # noqa: BLE001
            return "on"

    def _unattended_policy(self) -> str:
        """tool_guard.unattended_policy, normalized (fail-safe: "ask")."""
        guard = self._security_guard()
        if guard is None:
            return "ask"
        return guard.unattended_policy_value

    def _folder_keys_for(
        self, tool_calls: List[Dict[str, Any]], session_id: Optional[str] = None
    ) -> List[Optional[str]]:
        """Per-call folder grant key (``<tool>:dir:<folder>``), or None."""
        guard = self._security_guard()
        if guard is None:
            return [None] * len(tool_calls or [])
        keys: List[Optional[str]] = []
        for tc in tool_calls or []:
            func = tc.get("function", {}) or {}
            name = func.get("name", "") or tc.get("name", "")
            if not name:
                keys.append(None)
                continue
            try:
                folder = guard.folder_grant_for(tc, name, session_id=session_id)
            except Exception:  # noqa: BLE001 - a malformed call grants nothing
                folder = None
            keys.append(
                guard.folder_grant_key(name, folder) if folder else None
            )
        return keys

    def _build_tool_info(
        self,
        tool_calls: List[Dict[str, Any]],
        decisions: List,
        folder_keys: Optional[List[Optional[str]]] = None,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Pair each tool call with its category/reason for the UI + audit."""
        # decisions and tool_calls are positionally aligned (evaluate_batch
        # preserves order); same-name calls with different commands can have
        # different grantable verdicts.
        keys = list(folder_keys or [])
        guard = self._security_guard()
        out = []
        for idx, tc in enumerate(tool_calls or []):
            func = tc.get("function", {}) or {}
            name = func.get("name", "") or tc.get("name", "")
            dec = decisions[idx] if idx < len(decisions) else None
            folder = None
            if guard is not None and name:
                try:
                    folder = guard.folder_grant_for(
                        tc, name, session_id=session_id
                    )
                except Exception:  # noqa: BLE001
                    folder = None
            out.append(
                {
                    "id": tc.get("id", ""),
                    "name": name,
                    "arguments": func.get("arguments", ""),
                    "category": dec.category if dec else "unknown",
                    "reason": dec.reason if dec else "",
                    "grantable": bool(dec.grantable) if dec else False,
                    # Purely presentational; the key itself is what the
                    # confirm path stores (``folder_keys``).
                    "folder_grant": folder,
                    "folder_key": keys[idx] if idx < len(keys) else None,
                }
            )
        return out

    def _approval_timeout(self) -> float:
        """How long a run may stay suspended awaiting a user decision."""
        guard = self._security_guard()
        if guard is None:
            return 600.0
        return guard.pending_timeout_seconds

    @staticmethod
    def _audit(event: str, **data: Any) -> None:
        """Write an approval audit record (best-effort, never blocks the run)."""
        try:
            from boarai.core.security import get_security_manager
            from boarai.core.security.redact import redact_deep

            sec = get_security_manager()
            if sec and getattr(sec, "_initialized", False):
                payload = redact_deep({"decision": event, **data})
                sec.audit_record("approval", payload)
        except Exception as e:  # noqa: BLE001
            logger.debug("Approval audit skipped: %s", e)
