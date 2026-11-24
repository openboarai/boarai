"""
Tool execution helpers for conversation runs.

Keeps RunManager thinner: argument parsing, security kwargs, and registry call.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from boarai.core.extension import get_tool_registry

logger = logging.getLogger(__name__)


def _strip_internal_keys(raw: Any) -> Any:
    """Drop underscore-prefixed internal keys (e.g. file-change diffs)."""
    if isinstance(raw, dict):
        return {k: v for k, v in raw.items() if not str(k).startswith("_")}
    return raw


def _extract_file_change(raw: Any) -> Optional[Dict[str, Any]]:
    """Pull the file-change record from a tool result, if present."""
    if not isinstance(raw, dict):
        return None
    if "_path" not in raw and "_change_type" not in raw:
        return None
    change: Dict[str, Any] = {
        "path": raw.get("_path", ""),
        "change_type": raw.get("_change_type", "modified"),
    }
    diff = raw.get("_diff")
    if isinstance(diff, str) and diff:
        change["diff"] = diff
    for src, dst in (("_additions", "additions"), ("_deletions", "deletions")):
        val = raw.get(src)
        if isinstance(val, int):
            change[dst] = val
    # Revert metadata: before-run snapshot (modify/overwrite), prior existence
    # (added vs existed), and trash id (delete recovery).
    if "_existed" in raw:
        change["existed_before"] = bool(raw.get("_existed"))
    snap = raw.get("_snapshot")
    if isinstance(snap, str) and snap:
        change["snapshot"] = snap
    trash_id = raw.get("_trash_id")
    if isinstance(trash_id, str) and trash_id:
        change["trash_id"] = trash_id
    return change


def _format_tool_content(raw: Any) -> str:
    """Serialize tool result for session/LLM/UI consumption."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    try:
        return json.dumps(
            _strip_internal_keys(raw), ensure_ascii=False, indent=2, default=str
        )
    except Exception:
        return str(raw)


def _tool_payload_is_error(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    if raw.get("error"):
        return True
    if raw.get("success") is False:
        return True
    exit_code = raw.get("exit_code")
    if exit_code is not None and exit_code != 0:
        return True
    return False


async def execute_single_tool(
    session: Any,
    tool_call: Dict[str, Any],
    *,
    tool_registry: Any = None,
    agent_name: Optional[str] = None,
    agent_config: Optional[Any] = None,
    run_id: Optional[str] = None,
    approval_granted: bool = True,
) -> Dict[str, Any]:
    """Execute one OpenAI-format tool_call via ToolRegistry."""
    tool_id = tool_call.get("id", "")
    func = tool_call.get("function", {})
    tool_name = func.get("name", "")
    raw_args = func.get("arguments", "{}")

    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError:
        args = {}

    if not isinstance(args, dict):
        args = {"value": args}

    logger.info("Executing tool: %s (id=%s) args=%s", tool_name, tool_id, args)

    error_kind = ""
    error_reason = ""
    file_change: Optional[Dict[str, Any]] = None

    tool_reg = tool_registry if tool_registry is not None else get_tool_registry()
    session_id = getattr(session, "session_id", None)
    command = args.get("command") if isinstance(args.get("command"), str) else None

    try:
        exec_kwargs = dict(args)
        if agent_name:
            exec_kwargs["agent_name"] = agent_name
        if session_id:
            exec_kwargs["session_id"] = session_id
        if agent_config:
            exec_kwargs["_agent_tools"] = agent_config.zone2_tool_names
            exec_kwargs["_agent_shell"] = agent_config.shell_allowlist
            exec_kwargs["_agent_paths"] = agent_config.allowed_paths
            exec_kwargs["_agent_allowed_categories"] = agent_config.allowed_categories
            exec_kwargs["_agent_can_auto_approve"] = agent_config.can_auto_approve
        exec_kwargs["_approval_granted"] = approval_granted
        exec_kwargs["_run_id"] = run_id or session_id
        # Pairs live tool.output fan-out with the UI's tool card.
        if tool_id:
            exec_kwargs["_tool_call_id"] = tool_id

        exec_result = await tool_reg.execute_tool(tool_name, **exec_kwargs)
        if exec_result.success:
            raw = exec_result.result
            # Pull traceability data (diff) out, then strip internal keys so
            # they never reach the LLM-facing content or the persisted blob.
            file_change = _extract_file_change(raw)
            clean_raw = _strip_internal_keys(raw)
            content = _format_tool_content(clean_raw)
            is_error = _tool_payload_is_error(clean_raw)
            # Prefer structured command from shell tool payload when present.
            if isinstance(clean_raw, dict) and isinstance(clean_raw.get("command"), str):
                command = clean_raw.get("command") or command
            result_payload = clean_raw if isinstance(clean_raw, dict) else None
            if is_error and isinstance(clean_raw, dict):
                error_kind = "tool"
                error_reason = str(clean_raw.get("error") or "")
        else:
            content = f"Tool error: {exec_result.error or 'Unknown error'}"
            is_error = True
            result_payload = {
                "success": False,
                "error": exec_result.error or "Unknown error",
                "command": command,
            }
            # Security blocks (PathGuard / ToolGuard / ShellGuard) carry the
            # exact policy reason; surface it as a first-class error kind.
            meta = exec_result.metadata or {}
            error_kind = "permission" if meta.get("security_block") else "execution"
            error_reason = str(meta.get("reason") or exec_result.error or "")
    except Exception as e:
        logger.error("Tool %s execution error: %s", tool_name, e)
        content = f"Error executing {tool_name}: {e}"
        is_error = True
        result_payload = {
            "success": False,
            "error": str(e),
            "command": command,
        }
        error_kind = "execution"
        error_reason = str(e)

    out: Dict[str, Any] = {
        "tool_call_id": tool_id,
        "name": tool_name,
        "arguments": args,
        "content": content,
        "is_error": is_error,
    }
    if error_kind:
        out["error_kind"] = error_kind
    if error_reason:
        out["error_reason"] = error_reason
    if command is not None:
        out["command"] = command
    if result_payload is not None:
        out["result"] = result_payload
    if file_change is not None:
        out["file_change"] = file_change
    return out


def extract_summary_section(summary: str, header: str) -> Optional[str]:
    """Extract content of a ``══ HEADER ══`` delimited summary section."""
    import re

    pattern = re.escape(header) + r"[^\n]*\n(.*?)(?=\n══ |\Z)"
    match = re.search(pattern, summary, re.DOTALL)
    if not match:
        return None
    value = match.group(1).strip()
    if value and value not in ("none", "none confirmed", "none noted", ""):
        return value
    return None
