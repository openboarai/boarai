"""
SubAgent system for BoarAi.

Provides pre-configured sub-agent types with restricted tool access
that run independently within a parent session. Each sub-agent has
a well-defined scope and produces structured output.

Architecture:
    Parent LLM → calls `subagent` tool → creates SubAgentSession
      → runs ThinkingEngine with restricted tools
      → returns structured result (scope/result/key_files/issues)

6 SubAgent presets:
    explorer  — Read-only codebase investigation
    planner   — Pure analysis + planning (read only)
    verifier  — Verify outputs (read + test commands)
    general   — Full subtask delegation (all tools)
    coder     — Code changes (read/write/search + syntax checks)
    debugger  — Debugging (read/search + test/debug commands)
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from boarai.core.event import EventType, publish_event
from boarai.core.extension import get_extension_manager, get_tool_registry
from boarai.core.llm import get_llm_service
from boarai.core.prompt import get_prompt_assembler

from .config import ThinkingMode, load_thinking_settings, resolve_thinking_config
from .engine import ThinkingEngine, ThinkingChannel

logger = logging.getLogger(__name__)


class SubAgentPreset(str, Enum):
    """Pre-configured sub-agent types."""
    EXPLORER = "explorer"
    PLANNER = "planner"
    VERIFIER = "verifier"
    GENERAL = "general"
    CODER = "coder"
    DEBUGGER = "debugger"


@dataclass
class SubAgentProfile:
    """Profile definition for a sub-agent preset."""
    display_name: str
    description: str
    allowed_tools: List[str]
    """Tool names this sub-agent may use."""
    tool_denylist: List[str] = field(default_factory=list)
    """Tools explicitly denied even if in allowed_tools."""
    shell_commands: List[str] = field(default_factory=list)
    """Allowed shell command patterns (regex). Empty = no shell."""


# ===== Preset definitions (loaded from data file) =====

def _load_presets() -> Dict[SubAgentPreset, SubAgentProfile]:
    """Load sub-agent presets from presets.json."""
    preset_path = Path(__file__).parent / "presets.json"
    try:
        with open(preset_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to load subagent presets from {preset_path}: {e}")
        return {}

    presets: Dict[SubAgentPreset, SubAgentProfile] = {}
    for key, preset_data in data.items():
        try:
            preset_enum = SubAgentPreset(key)
            presets[preset_enum] = SubAgentProfile(
                display_name=preset_data["display_name"],
                description=preset_data["description"],
                allowed_tools=preset_data["allowed_tools"],
                tool_denylist=preset_data.get("tool_denylist", []),
                shell_commands=preset_data.get("shell_commands", []),
            )
        except (ValueError, KeyError) as e:
            logger.warning(f"Skipping invalid subagent preset '{key}': {e}")
    return presets


SUBAGENT_PRESETS: Dict[SubAgentPreset, SubAgentProfile] = _load_presets()


def get_preset(preset_name: str) -> SubAgentProfile:
    """Get a sub-agent profile by name (case-insensitive)."""
    for preset in SubAgentPreset:
        if preset.value == preset_name.lower():
            return SUBAGENT_PRESETS[preset]
    raise ValueError(
        f"Unknown sub-agent preset: {preset_name}. "
        f"Available: {[p.value for p in SubAgentPreset]}"
    )


# ===== Structured output schema =====

SUBAGENT_CONTEXT_PROMPT = """\
You are acting as a sub-agent with restricted capabilities.
Complete the assigned task and return a structured report.

Your output must include these sections (in Markdown):
<subagent_result>
## Scope
<what was investigated>

## Result
<findings, decisions, or generated content>

## Key Files
<relevant file paths, one per line>

## Issues
<blockers or concerns, or "None">
</subagent_result>
"""


def parse_subagent_output(content: str) -> Dict[str, str]:
    """Parse structured sub-agent output from LLM response."""
    result = {
        "scope": "",
        "result": "",
        "key_files": "",
        "issues": "",
    }

    # Try to extract from <subagent_result> tags
    import re
    tag_match = re.search(
        r'<subagent_result>(.*?)</subagent_result>',
        content, re.DOTALL
    )
    if tag_match:
        inner = tag_match.group(1)
    else:
        inner = content

    # Extract sections
    sections = re.split(r'##\s+', inner)
    for section in sections:
        section = section.strip()
        if not section:
            continue
        for key in ("Scope", "Result", "Key Files", "Issues"):
            if section.startswith(key):
                result[key.lower().replace(" ", "_")] = section[len(key):].strip()
                break

    # Fallback: use whole content as result
    if not result["result"]:
        result["result"] = content[:2000]

    return result


# ===== SubAgentSession =====

class SubAgentSession:
    """Executes a single sub-agent task with restricted tools."""

    DEFAULT_MAX_TURNS = 10
    DEFAULT_TIMEOUT = 120  # seconds

    def __init__(
        self,
        session_id: str,
        preset_name: str,
        instruction: str,
        max_turns: int = DEFAULT_MAX_TURNS,
        timeout: float = DEFAULT_TIMEOUT,
        parent_agent_name: str = "default",
    ) -> None:
        """Initialize sub-agent session."""
        self.session_id = session_id
        self.preset_name = preset_name
        self.instruction = instruction
        self.max_turns = max_turns
        self.timeout = timeout
        self.parent_agent_name = parent_agent_name
        self.subagent_id = f"sub_{uuid.uuid4().hex[:8]}"

        self._profile = get_preset(preset_name)
        self._started_at: Optional[float] = None
        self._turn_count = 0

    async def run(self) -> Dict[str, Any]:
        """Execute the sub-agent task with real tool execution loop."""
        self._started_at = time.time()
        logger.info(
            f"SubAgent[{self.subagent_id}] started: preset={self.preset_name}, "
            f"instruction={self.instruction[:80]}"
        )

        try:
            # 1. Build initial messages
            messages = self._build_messages()

            # 2. Resolve tools from preset
            tools = self._resolve_tools()

            # 3. Tool execution loop via ThinkingEngine
            engine = ThinkingEngine()
            # Resolve the model once so thinking config and the usage ledger
            # know what the wire call actually uses (empty = provider default).
            model = ""
            try:
                from boarai.core.llm import get_llm_manager

                model = get_llm_manager().default_model or ""
            except Exception:  # noqa: BLE001 — no LLM configured yet
                model = ""
            self._model = model
            total_turns = 0
            final_content = ""
            all_failures: List[str] = []
            all_successes: List[str] = []
            _, thinking_enabled = load_thinking_settings()
            subagent_thinking = (
                ThinkingMode.LIGHT if thinking_enabled else ThinkingMode.AUTO
            )

            for iteration in range(self.max_turns):
                result = await engine.run(
                    llm_call=self._make_llm_call(),
                    user_input=self.instruction if iteration == 0 else "",
                    session_messages=messages,
                    model=model,
                    tools=tools,
                    thinking_mode=subagent_thinking,
                    max_turns=self.max_turns - iteration,
                )
                total_turns += result.turns

                if result.finish_reason == "tool_calls" and result.tool_calls:
                    # Execute tools and feed results back
                    tool_results = await self._execute_tool_calls(
                        result.tool_calls, messages
                    )
                    for tr in tool_results:
                        if not tr.get("success", True):
                            all_failures.append(
                                f"{tr.get('name', 'tool')}: {tr.get('error', 'unknown')}"
                            )
                        else:
                            all_successes.append(tr.get("name", "tool"))
                    continue  # continue loop after tool execution
                else:
                    # Text response → done
                    final_content = result.content
                    break
            else:
                # Max iterations hit without text response
                logger.warning(
                    f"SubAgent[{self.subagent_id}] hit max turns ({self.max_turns})"
                )
                final_content = "Sub-agent reached maximum turns without completing."

            # Don't hardcode success — check tool execution results
            success = len(all_failures) == 0 and len(final_content) > 0

            # 4. Parse structured output
            parsed = parse_subagent_output(final_content)
            parsed["turns"] = total_turns
            parsed["success"] = success
            parsed["tool_errors"] = all_failures
            parsed["subagent_id"] = self.subagent_id

            logger.info(
                f"SubAgent[{self.subagent_id}] completed: "
                f"{total_turns} turns, {success=}, "
                f"errors={len(all_failures)}, {len(final_content)} chars"
            )
            await self._ledger_usage()
            return parsed

        except asyncio.TimeoutError:
            logger.warning(f"SubAgent[{self.subagent_id}] timed out after {self.timeout}s")
            await self._ledger_usage()
            return {
                "scope": "",
                "result": "Sub-agent timed out.",
                "key_files": "",
                "issues": f"Exceeded {self.timeout}s timeout",
                "turns": self._turn_count,
                "success": False,
                "subagent_id": self.subagent_id,
            }
        except Exception as e:
            logger.error(f"SubAgent[{self.subagent_id}] failed: {e}")
            await self._ledger_usage()
            return {
                "scope": "",
                "result": f"Sub-agent error: {e}",
                "key_files": "",
                "issues": str(e),
                "turns": self._turn_count,
                "success": False,
                "subagent_id": self.subagent_id,
            }

    async def _execute_tool_calls(
        self,
        tool_calls: List[Dict[str, Any]],
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Execute tool calls and append results to messages."""
        from boarai.core.extension import get_tool_registry

        registry = get_tool_registry()
        results: List[Dict[str, Any]] = []

        for tc in tool_calls:
            func = tc.get("function", {})
            name = func.get("name", "unknown")
            call_id = tc.get("id", "")

            try:
                args = json.loads(func.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                args = {}

            # Add tool_calls assistant message
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [tc],
            })

            # Execute via registry (with security checks)
            try:
                result = await registry.execute_tool(
                    tool_name=name,
                    arguments=args,
                    session_id=self.session_id,
                    agent_name=self.parent_agent_name,
                )
                result_text = json.dumps(result, ensure_ascii=False)
                success = True
            except Exception as e:
                result_text = json.dumps({"error": str(e)})
                success = False
                results.append({
                    "name": name,
                    "success": False,
                    "error": str(e),
                })

            # Add tool result message
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": result_text,
            })

            if success:
                results.append({"name": name, "success": True})

        return results

    def _build_messages(self) -> List[Dict[str, Any]]:
        """Build initial messages for sub-agent."""
        profile = self._profile
        tool_desc = ", ".join(profile.allowed_tools)

        system_msg = (
            f"You are a {profile.display_name} sub-agent.\n"
            f"{profile.description}\n\n"
            f"Your available tools: {tool_desc}\n\n"
            f"{SUBAGENT_CONTEXT_PROMPT}"
        )

        return [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": self.instruction},
        ]

    def _resolve_tools(self) -> Optional[List[Dict[str, Any]]]:
        """Resolve tools available to this sub-agent from global registry."""
        profile = self._profile
        tool_reg = get_tool_registry()

        if "*" in profile.allowed_tools:
            return None  # All tools

        available = []
        for name in profile.allowed_tools:
            # ToolRegistry stores tools internally; try to get schema
            try:
                tool_def = tool_reg.get_tool(name)
                if tool_def:
                    available.append(tool_def.to_dict())
            except Exception:
                logger.debug(f"SubAgent: tool '{name}' not found in registry")
                continue

        return available if available else None

    def _make_llm_call(self):
        """Create the LLM call function for ThinkingEngine."""
        llm = get_llm_service()

        async def _call(messages, model, tools, cache_breakpoints=None, **kwargs):
            async for chunk in llm.chat_stream(
                messages=messages,
                model=model,
                tools=tools,
                cache_breakpoints=cache_breakpoints,
                **kwargs,
            ):
                # Intercept usage chunks so the sub-agent's token consumption
                # can be ledgered (it bypasses the run executor entirely).
                if getattr(chunk, "usage", None):
                    agg = getattr(self, "_usage_agg", None) or {
                        "prompt_tokens": 0, "completion_tokens": 0,
                        "cache_read": 0, "cache_creation": 0,
                    }
                    u = chunk.usage
                    agg["prompt_tokens"] += int(u.get("prompt_tokens", 0) or 0)
                    agg["completion_tokens"] += int(
                        u.get("completion_tokens", 0) or 0
                    )
                    agg["cache_read"] += int(
                        u.get("cached_tokens", 0)
                        or u.get("cache_hit_tokens", 0)
                        or u.get("cache_read_input_tokens", 0)
                        or 0
                    )
                    agg["cache_creation"] += int(
                        u.get("cache_creation_input_tokens", 0) or 0
                    )
                    self._usage_agg = agg
                yield chunk

        return _call

    async def _ledger_usage(self) -> None:
        """Record this sub-agent's total LLM usage to the global ledger."""
        agg = getattr(self, "_usage_agg", None)
        if not agg:
            return
        try:
            from boarai.core.session.usage_ledger import get_usage_ledger

            await get_usage_ledger().record_side_usage(
                {
                    "prompt_tokens": agg["prompt_tokens"],
                    "completion_tokens": agg["completion_tokens"],
                    "cache_read_input_tokens": agg["cache_read"],
                    "cache_creation_input_tokens": agg["cache_creation"],
                },
                kind="subagent",
                model=getattr(self, "_model", "") or "unknown",
                session_id=self.session_id,
                agent_name=f"subagent:{self.preset_name}",
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("SubAgent usage ledger skipped: %s", e)


# ===== Tool registration =====

# The subagent tool is registered at module import time so it's
# available to parent agents immediately.

SUBAGENT_TOOL_SCHEMA = {
    "name": "subagent",
    "description": (
        "Delegate a task to a specialized sub-agent with restricted tools. "
        "The sub-agent runs independently and returns a structured report "
        "with scope, result, key files, and issues. "
        "Use this for tasks that benefit from focused investigation "
        "or parallel execution."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "preset": {
                "type": "string",
                "enum": [p.value for p in SubAgentPreset],
                "description": (
                    "Sub-agent type:\n"
                    "- explorer: Read-only codebase investigation\n"
                    "- planner: Pure analysis and planning\n"
                    "- verifier: Verify outputs with tests\n"
                    "- general: Full subtask delegation\n"
                    "- coder: Focused code modifications\n"
                    "- debugger: Debug and troubleshoot"
                ),
            },
            "instruction": {
                "type": "string",
                "description": "Clear task description for the sub-agent",
            },
            "max_turns": {
                "type": "integer",
                "description": "Maximum execution turns (default: 10)",
                "default": 10,
            },
            "background": {
                "type": "boolean",
                "description": (
                    "Run in the background and return a delegation_id "
                    "immediately instead of waiting. Collect the outcome later "
                    "with get_subagent_result. Use for long tasks or when "
                    "several sub-agents should run concurrently."
                ),
                "default": False,
            },
        },
        "required": ["preset", "instruction"],
    },
}


GET_SUBAGENT_RESULT_TOOL_SCHEMA = {
    "name": "get_subagent_result",
    "description": (
        "Collect the outcome of a background sub-agent started with "
        "subagent(background=true). Pass wait=true to block until it finishes, "
        "or false to poll. Omit delegation_id to list every delegation of this "
        "session with its status."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "delegation_id": {
                "type": "string",
                "description": "Id returned by subagent(background=true)",
            },
            "wait": {
                "type": "boolean",
                "description": "Block until finished instead of polling",
                "default": True,
            },
            "timeout": {
                "type": "number",
                "description": "Max seconds to wait (default 120)",
                "default": 120,
            },
        },
        "required": [],
    },
}


def register_subagent_tool() -> None:
    """Register the subagent tool into the global ToolRegistry."""
    from boarai.core.extension.tool import ToolDefinition, ToolParameter

    # Convert JSON Schema parameters to ToolParameter list
    props = SUBAGENT_TOOL_SCHEMA["parameters"]["properties"]
    required = SUBAGENT_TOOL_SCHEMA["parameters"].get("required", [])

    parameters = []
    for pname, pinfo in props.items():
        parameters.append(ToolParameter(
            name=pname,
            type=pinfo.get("type", "string"),
            description=pinfo.get("description", ""),
            required=pname in required,
            default=pinfo.get("default"),
        ))

    definition = ToolDefinition(
        name=SUBAGENT_TOOL_SCHEMA["name"],
        description=SUBAGENT_TOOL_SCHEMA["description"],
        parameters=parameters,
    )

    async def _subagent_handler(
        ctx: Any,
        preset: str,
        instruction: str,
        max_turns: int = 10,
        background: bool = False,
    ) -> Dict[str, Any]:
        """Handle subagent tool call."""
        session_id = getattr(ctx, "session_id", "") or ""
        agent_name = getattr(ctx, "agent_name", "default")
        capped_turns = min(max_turns, 100)

        if background:
            # Hand the work to the delegation pool and return immediately, so
            # the parent keeps its turn instead of blocking on the child.
            from .delegation import get_delegation_pool

            delegation = get_delegation_pool().submit(
                session_id,
                preset,
                instruction,
                max_turns=capped_turns,
                parent_agent_name=agent_name,
            )
            payload = delegation.to_dict()
            payload["note"] = (
                "Started in the background. Call get_subagent_result with "
                "this delegation_id to collect the outcome."
            )
            return {
                "content": json.dumps(payload, ensure_ascii=False, indent=2),
                "metadata": {
                    "type": "subagent_delegated",
                    "delegation_id": delegation.delegation_id,
                    "status": delegation.status.value,
                    "background": True,
                },
            }


        session = SubAgentSession(
            session_id=session_id,
            preset_name=preset,
            instruction=instruction,
            max_turns=capped_turns,  # Cap at 100 (was 20)
            parent_agent_name=agent_name,
        )

        # Broadcast subagent start event for monitoring/auditing
        await publish_event(
            EventType.AGENT_SUBAGENT_START,
            {
                "session_id": session_id,
                "subagent_id": session.subagent_id,
                "preset": preset,
                "instruction": instruction[:200],
                "parent_agent_name": agent_name,
            },
            "subagent"
        )

        result = await session.run()

        return {
            "content": json.dumps(result, ensure_ascii=False, indent=2),
            "metadata": {
                "type": "subagent_result",
                "subagent_id": result.get("subagent_id", ""),
                "turns": result.get("turns", 0),
                "success": result.get("success", False),
            },
        }

    try:
        registry = get_tool_registry()
        registry.register_tool_sync(definition, _subagent_handler)
        logger.info("SubAgent tool registered")
    except Exception as e:
        logger.warning(f"Failed to register SubAgent tool: {e}")



def register_subagent_result_tool() -> None:
    """Register ``get_subagent_result`` so a parent can collect background work."""
    from boarai.core.extension.tool import ToolDefinition, ToolParameter

    from .delegation import get_delegation_pool

    schema = GET_SUBAGENT_RESULT_TOOL_SCHEMA
    props = schema["parameters"]["properties"]
    required = schema["parameters"].get("required", [])
    parameters = [
        ToolParameter(
            name=name,
            type=info.get("type", "string"),
            description=info.get("description", ""),
            required=name in required,
            default=info.get("default"),
        )
        for name, info in props.items()
    ]
    definition = ToolDefinition(
        name=schema["name"],
        description=schema["description"],
        parameters=parameters,
    )

    def _not_found(delegation_id: str) -> Dict[str, Any]:
        return {
            "content": json.dumps(
                {"error": f"unknown delegation_id: {delegation_id}"},
                ensure_ascii=False,
            ),
            "is_error": True,
            "metadata": {"type": "subagent_result"},
        }

    async def _result_handler(
        ctx: Any,
        delegation_id: str = "",
        wait: bool = True,
        timeout: float = 120.0,
    ) -> Dict[str, Any]:
        pool = get_delegation_pool()
        session_id = getattr(ctx, "session_id", "") or ""

        # No id: a status listing, so the parent can see what is outstanding.
        if not delegation_id:
            delegations = pool.list_for_session(session_id)
            payload = {
                "delegations": [d.to_dict() for d in delegations],
                "pending": pool.pending_for_session(session_id),
                "max_concurrent_children": pool.max_concurrent_children,
            }
            return {
                "content": json.dumps(payload, ensure_ascii=False, indent=2),
                "metadata": {"type": "subagent_list", "count": len(delegations)},
            }

        if not wait:
            delegation = pool.get(delegation_id)
            if delegation is None:
                return _not_found(delegation_id)
            payload = delegation.to_dict(include_result=delegation.is_terminal)
            if delegation.is_terminal:
                delegation.consumed = True
            return {
                "content": json.dumps(payload, ensure_ascii=False, indent=2),
                "metadata": {
                    "type": "subagent_result",
                    "delegation_id": delegation_id,
                    "status": delegation.status.value,
                },
            }

        try:
            delegation = await pool.await_result(
                delegation_id, timeout=max(1.0, float(timeout or 120.0)),
            )
        except KeyError:
            return _not_found(delegation_id)
        except asyncio.TimeoutError:
            current = pool.get(delegation_id)
            payload = {
                "delegation_id": delegation_id,
                "status": current.status.value if current else "unknown",
                "timed_out": True,
                "note": "Still running; call get_subagent_result again later.",
            }
            return {
                "content": json.dumps(payload, ensure_ascii=False, indent=2),
                "metadata": {"type": "subagent_result", "timed_out": True},
            }

        payload = delegation.to_dict(include_result=True)
        return {
            "content": json.dumps(payload, ensure_ascii=False, indent=2),
            "metadata": {
                "type": "subagent_result",
                "delegation_id": delegation_id,
                "status": delegation.status.value,
                "success": delegation.status.value == "completed",
            },
        }

    try:
        registry = get_tool_registry()
        registry.register_tool_sync(definition, _result_handler)
        logger.info("get_subagent_result tool registered")
    except Exception as e:
        logger.warning(f"Failed to register get_subagent_result tool: {e}")
