"""
Prompt assembler for BoarAi — v5 Stateful Prompt Engine.

7-Layer architecture with stability-ordered sections:

    Layer 1: System Prompt      (stability 5, never changes)     BP#1
    Layer 2: Tool Schema         (stability 5, rarely changes)    tool_last
    Layer 3: Pinned Context      (stability 4, session-level)     BP#2
    Layer 4: Long Memory         (stability 3, 266s idle update)  BP#3
    Layer 5: Task State          (stability 2, every N turns)     BP#4
    Layer 6: Recent Exchange     (stability 1, every turn)        dynamic
    Layer 7: Current Turn        (stability 0, every turn)        dynamic

State-driven (not message-driven): prompt size stays bounded
regardless of conversation length. Cache hit rate ~93%.

Produces PromptDocument (provider-neutral), rendered by Provider
renderers (Anthropic / OpenAI).

All prompt content loaded from boar/prompts/ by the loader module.
Zero prompt strings in this file — only assembly logic.
"""

import logging
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import platform
from datetime import datetime

from .agent import AgentConfig, get_agent_registry
from .assistant_compress import get_assistant_compressor
from .cache import CachedPrefix, SessionPromptCache, get_session_prompt_cache
from boarai.core.runtime import BoundedMap
from .document import PromptDocument, PromptSection
from .fragments import (
    FragmentRegistry,
    MemoryFragment,
    UserProfileFragment,
    SkillIndexFragment,
)
from .loader import SYSTEM_CORE_PROMPT
from .token_counter import count_tokens

logger = logging.getLogger(__name__)


# ==================== Legacy Data Structure (backward compat) ====================


@dataclass
class AssembledPrompt:
    """Output of prompt assembly — backward compatible."""
    messages: List[Dict[str, str]]
    tools: Optional[List[Dict[str, Any]]] = None
    breakpoints: Dict[str, Any] = field(default_factory=dict)
    token_estimates: Dict[str, int] = field(default_factory=dict)
    document: Optional[PromptDocument] = None


# ==================== Helpers ====================


def _wrap_xml(tag: str, content: str) -> str:
    """Wrap content in XML tags."""
    if not content:
        return ""
    return f"<{tag}>\n{content}\n</{tag}>"


def _digest(text: Any) -> str:
    """Content address for the token-estimate cache (collision-immune enough)."""
    if not isinstance(text, str):
        import json

        try:
            text = json.dumps(text, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            text = repr(text)
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


# Tools-param encode cache: the schema list is stable across turns, so its
# tiktoken encode (the dominant pre-request CPU cost) runs once per distinct
# schema set per process, not once per turn.
_TOOLS_TOKEN_CACHE: BoundedMap = BoundedMap(maxsize=64)


def _count_tools_tokens(tools: Any) -> int:
    """Count tokens of the tools param as the JSON actually sent to the API."""
    if not tools:
        return 0
    try:
        import json
        dumped = json.dumps(tools, ensure_ascii=False)
    except Exception:  # noqa: BLE001 — estimation must never break assembly
        return count_tokens(str(tools))
    key = _digest(dumped)
    cached = _TOOLS_TOKEN_CACHE.get(key)
    if cached is None:
        cached = count_tokens(dumped)
        _TOOLS_TOKEN_CACHE[key] = cached
    return cached


# ==================== Stateful Assembler ====================


class PromptAssembler:
    """v6 Stateful Prompt Engine with simplified architecture."""

    def __init__(self):
        # Bounded: a prefix entry holds the whole system message plus every tool
        # schema, and a fragment snapshot holds fragment bytes. Keyed by
        # session_id with no eviction these grew for the life of the process, so
        # a long-running server accumulated one heavy entry per session ever run.
        self._prefix_cache: BoundedMap[str, CachedPrefix] = BoundedMap()
        # Content-addressed token-estimate cache (C5): (zone name, text
        # digest) → tiktoken result. Unchanged sections — the whole stable
        # prefix and every already-counted history message — reuse their
        # encode, so a turn only pays tiktoken for text new to this process.
        # Eviction or a cold cache merely re-encodes; estimates stay exact.
        self._section_tokens: BoundedMap = BoundedMap(maxsize=4096)
        # Context fragments: composable system-section sources (user info, memory)
        self._fragment_registry = FragmentRegistry()
        self._fragment_snapshots: BoundedMap[str, Dict[str, Optional[bytes]]] = BoundedMap()
        # Last stable prefix per session, for drift detection: the cache key
        # (signature) is unchanged but the bytes are not, which silently costs
        # a cache hit on every later turn.
        self._prefix_snapshots: BoundedMap[str, tuple] = BoundedMap()
        # Register built-in fragments
        self._fragment_registry.register(MemoryFragment())
        self._fragment_registry.register(UserProfileFragment())
        self._fragment_registry.register(SkillIndexFragment())

    def register_fragment(self, fragment: Any) -> None:
        """Extension point: register a custom ContextFragment."""
        from .fragments import ContextFragment
        if not isinstance(fragment, ContextFragment):
            logger.warning(
                "register_fragment called with non-ContextFragment: %s",
                type(fragment).__name__,
            )
            return
        self._fragment_registry.register(fragment)

    # ----- Layer Builders -----

    def _build_layer1_system(
        self,
        agent_name: str = "default",
    ) -> str:
        """Layer 1: System prompt (system_core + agent identity + rules)."""
        parts: List[str] = []

        if SYSTEM_CORE_PROMPT:
            parts.append(_wrap_xml("system-core", SYSTEM_CORE_PROMPT))

        registry = get_agent_registry()
        agent = registry.get(agent_name)
        if agent:
            identity = agent.resolve_identity(registry)
            if identity:
                parts.append(_wrap_xml("agent-identity", identity))
            rules = agent.resolve_rules(registry)
            if rules:
                parts.append(_wrap_xml("agent-rules", rules))

        return "\n\n".join(parts)

    @staticmethod
    def _build_layer6_recent(
        last_user_input: str = "",
        last_assistant_response: str = "",
        compress_assistant: bool = True,
    ) -> List[Dict[str, str]]:
        """Layer 6: Recent exchange (last 1 turn)."""
        messages: List[Dict[str, str]] = []

        if last_user_input:
            messages.append({"role": "user", "content": last_user_input})

        if last_assistant_response:
            if compress_assistant:
                compressor = get_assistant_compressor()
                compressed = compressor.compress(last_assistant_response)
                messages.append({"role": "assistant", "content": compressed})
            else:
                messages.append({"role": "assistant", "content": last_assistant_response})

        return messages

    @staticmethod
    def _build_layer7_current(
        user_input: str = "",
        tool_loop_messages: Optional[List[Dict[str, str]]] = None,
        task_instruction: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Layer 7: Current turn (user input + tool loop)."""
        messages: List[Dict[str, str]] = []

        # Tool loop messages (within Run, tool-call results)
        if tool_loop_messages:
            messages.extend(tool_loop_messages)

        # SubAgent task instruction
        if task_instruction:
            messages.append({
                "role": "user",
                "content": f"[Task Instruction]: {task_instruction}",
            })

        # Current user input
        if user_input:
            messages.append({"role": "user", "content": user_input})

        return messages

    # ----- Prefix Cache -----

    def _get_or_build_prefix(
        self,
        session_id: str,
        agent_name: str,
        extension_manager: Optional[Any],
        tool_registry: Optional[Any],
        agent_config: Optional[AgentConfig] = None,
        cache: Optional[SessionPromptCache] = None,
    ) -> tuple:
        """Get or build cached Layer 1 prefix."""
        if cache:
            signature = cache.compute_signature(
                agent_name=agent_name,
                agent_config=agent_config,
                extension_manager=extension_manager,
                tool_registry=tool_registry,
            )
        else:
            signature = {}

        # Check in-memory cache
        if session_id in self._prefix_cache:
            cached = self._prefix_cache[session_id]
            if cached.signature == signature:
                logger.debug(f"Prefix cache HIT (memory) for session {session_id}")
                return cached.zone1_message, cached.tools

        # Check disk cache
        if cache:
            disk_cached = cache.load(session_id, signature)
            if disk_cached:
                self._prefix_cache[session_id] = disk_cached
                return disk_cached.zone1_message, disk_cached.tools

        # Build from scratch
        logger.debug(f"Building prefix for session {session_id}")
        zone1_content = self._build_layer1_system(agent_name=agent_name)

        zone1_message: Dict[str, str] = {"role": "system", "content": zone1_content}

        # Extract tools
        tools_list: List[Dict[str, Any]] = []
        if tool_registry:
            try:
                tools_list = tool_registry.get_tool_definitions()
                if agent_config:
                    tools_list = agent_config.filter_tools(tools_list)
            except Exception as e:
                logger.warning(f"Failed to get tool definitions: {e}")

        self._check_prefix_drift(session_id, zone1_content, tools_list, signature)

        # Save to caches
        cached_prefix = CachedPrefix(
            signature=signature,
            zone1_message=zone1_message,
            tools=tools_list,
        )
        self._prefix_cache[session_id] = cached_prefix

        if cache:
            cache.save(
                session_id=session_id,
                signature=signature,
                zone1_message=zone1_message,
                tools=tools_list,
            )

        return zone1_message, tools_list

    def _estimate_layer_tokens(
        self,
        sections: List[PromptSection],
    ) -> Dict[str, int]:
        """Per-zone token estimates reusing cached encodes for unchanged text."""
        layer_tokens: Dict[str, int] = {}
        for s in sections:
            key = (s.name, _digest(s.content))
            tokens = self._section_tokens.get(key)
            if tokens is None:
                tokens = count_tokens(s.content)
                self._section_tokens[key] = tokens
            layer_tokens[s.name] = layer_tokens.get(s.name, 0) + tokens
        return layer_tokens

    @staticmethod
    def _drift_enabled() -> bool:
        """Drift checks run in debug/test, or when explicitly enabled."""
        import os

        return (
            os.getenv("BOARAI_PROMPT_DRIFT_CHECK", "") == "1"
            or logger.isEnabledFor(logging.DEBUG)
        )

    def _check_prefix_drift(
        self,
        session_id: str,
        content: str,
        tools: Any,
        signature: Any,
    ) -> Any:
        """Report a stable-prefix rewrite that the cache signature missed."""
        names: List[str] = []
        for item in tools or []:
            if not isinstance(item, dict):
                continue
            fn = item.get("function")
            name = fn.get("name") if isinstance(fn, dict) else item.get("name")
            if name:
                names.append(str(name))
        snapshot = (content, tuple(names), repr(signature))
        previous = self._prefix_snapshots.get(session_id)
        self._prefix_snapshots[session_id] = snapshot
        if previous is None or not self._drift_enabled():
            return None

        prev_content, prev_tools, prev_sig = previous
        if prev_sig != snapshot[2] or prev_tools != snapshot[1]:
            return None
        if prev_content == content:
            return None

        offset = next(
            (
                i
                for i, (a, b) in enumerate(zip(prev_content, content))
                if a != b
            ),
            min(len(prev_content), len(content)),
        )
        try:
            import asyncio

            from boarai.core.event import EventType, publish_event

            payload = {
                "session_id": session_id,
                "offset": offset,
                "before_len": len(prev_content),
                "after_len": len(content),
            }
            loop = asyncio.get_running_loop()
            loop.create_task(
                publish_event(
                    EventType.PROMPT_PREFIX_DRIFT, payload, "prompt_assembler"
                )
            )
        except Exception as e:  # noqa: BLE001 — observability only
            logger.debug("Prefix drift publish skipped: %s", e)
        logger.warning(
            "Prompt prefix drifted with an unchanged signature "
            "(session=%s, first diff at %s, %s -> %s chars)",
            session_id, offset, len(prev_content), len(content),
        )
        return offset

    def invalidate_prefix_cache(self, session_id: str) -> None:
        """Invalidate prefix cache + fragment snapshots for a session."""
        self._prefix_cache.pop(session_id, None)
        self._fragment_snapshots.pop(session_id, None)
        # An explicit invalidation is intentional: forget the baseline so the
        # rebuild is not reported as drift.
        self._prefix_snapshots.pop(session_id, None)
        try:
            cache = get_session_prompt_cache()
            cache.invalidate(session_id)
        except Exception:
            pass

    @staticmethod
    def _build_session_capability(
        project_root: Optional[Path] = None,
        session_id: str = "",
        model: str = "",
        workspace: Optional[Path] = None,
    ) -> Dict[str, str]:
        """Build session context as a user message injected on first turn."""
        today = datetime.now().strftime("%Y-%m-%d %A")
        os_name = platform.system()
        py_ver = platform.python_version()

        if workspace is not None:
            cwd = str(Path(workspace).resolve())
        elif project_root:
            cwd = str(project_root.resolve())
        else:
            cwd = str(Path.cwd())

        # ProjectRoot gives the model an absolute anchor for runtime-data
        # writes (skills, temp) — relative paths resolve against Workspace,
        # which silently misplaces `.boarai/...` writes into the session dir.
        root_part = ""
        if project_root:
            root_str = str(Path(project_root).resolve())
            if root_str != cwd:
                root_part = f"ProjectRoot: {root_str} | "

        sid = session_id or "new"
        content = (
            f"[BoarAi_System] Session: {sid} | "
            f"Workspace: {cwd} | "
            f"{root_part}"
            f"Date: {today} | "
            f"Platform: {os_name} | "
            f"Python: {py_ver}"
        )

        return {"role": "user", "content": content}

    # ----- Main Assembly -----

    def assemble_messages(
        self,
        user_input: str,
        session_messages: Optional[List[Dict[str, str]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        agent_name: str = "default",
        task_instruction: Optional[str] = None,
        extra_context: Optional[Dict[str, Any]] = None,
        extension_manager: Optional[Any] = None,
        tool_registry: Optional[Any] = None,
        session_id: str = "",
        cache: Optional[SessionPromptCache] = None,
        last_user_input: str = "",
        last_assistant_response: str = "",
        memory_horizon: str = "",
        proactive_memory: str = "",
    ) -> AssembledPrompt:
        """Assemble a full prompt using the simplified architecture."""
        ctx = dict(extra_context or {})

        agent_config = get_agent_registry().get(agent_name)

        if cache is None and session_id:
            try:
                cache = get_session_prompt_cache()
            except Exception:
                pass

        # --- Layer 1: Cached prefix ---
        zone1_msg, registry_tools = self._get_or_build_prefix(
            session_id=session_id or "_no_session",
            agent_name=agent_name,
            extension_manager=extension_manager,
            tool_registry=tool_registry,
            agent_config=agent_config,
            cache=cache,
        )

        if tools is None and registry_tools:
            tools = registry_tools

        # --- System context fragments (user info + frozen memory) ---
        try:
            project_root = Path(__file__).resolve().parent.parent.parent.parent
        except Exception:
            project_root = Path.cwd()

        fragments = self._fragment_registry.render_all(
            assembler=self,
            memory_horizon=memory_horizon,
            proactive_memory=proactive_memory,
            project_root=project_root,
            session_id=session_id,
        )

        # All remaining fragments are system sections (user info + memory).
        # user_profile (priority 40) renders before memory (priority 41).
        system_frag_parts: List[str] = [frag.text for frag in fragments]
        frag_full = "\n\n".join(system_frag_parts)

        if session_id:
            self._fragment_snapshots[session_id] = (
                self._fragment_registry.compute_snapshots(
                    assembler=self,
                    memory_horizon=memory_horizon,
                    proactive_memory=proactive_memory,
                    project_root=project_root,
                    session_id=session_id,
                )
            )

        # --- Layer 6: Recent Exchange ---
        if session_messages:
            recent_messages = []
        else:
            recent_messages = self._build_layer6_recent(
                last_user_input=last_user_input,
                last_assistant_response=last_assistant_response,
                compress_assistant=True,
            )

        # --- Layer 7: Current Turn ---
        # --- Normalize tool calls before Layer 7 ---
        tool_loop = None
        if session_messages:
            if any(
                m.get("tool_calls") or m.get("role") == "tool"
                for m in session_messages
            ):
                # Lazy import to break circular dependency (session.run ↔ core.prompt)
                from ..session.run.messages import normalize_messages
                norm_result = normalize_messages(
                    list(session_messages),
                    synth_missing=True,
                    remove_orphans=True,
                )
                tool_loop = norm_result.normalized
            else:
                tool_loop = session_messages

        current_messages = self._build_layer7_current(
            user_input=user_input,
            tool_loop_messages=tool_loop,
            task_instruction=task_instruction,
        )

        # --- Assemble PromptDocument ---
        sections: List[PromptSection] = []

        # Layer 1: System (system_core + agent identity/rules) — BP#1
        sections.append(PromptSection(
            name="system",
            content=zone1_msg["content"],
            role="system",
            stability=5,
            breakpoint_after=True,
        ))

        # User info + frozen memory (user_profile priority 40, memory 41)
        # Both are system sections; memory stays frozen for the session.
        if frag_full:
            sections.append(PromptSection(
                name="memory",
                content=frag_full,
                role="system",
                stability=3,
                breakpoint_after=True,
            ))

        # Recent Exchange (dynamic)
        for msg in recent_messages:
            sections.append(PromptSection(
                name="recent",
                content=msg.get("content", ""),
                role=msg.get("role", "user"),
                stability=1,
                breakpoint_after=False,
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
            ))

        # Layer 7: Current Turn (dynamic)
        for msg in current_messages:
            sections.append(PromptSection(
                name="current",
                content=msg.get("content", ""),
                role=msg.get("role", "user"),
                stability=0,
                breakpoint_after=False,
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
            ))

        doc = PromptDocument(sections=sections, tools=tools)

        # --- Compute flat messages and breakpoints (backward compat) ---
        all_messages = doc.messages

        bp_indices = doc.breakpoint_message_indices

        breakpoints: Dict[str, Any] = {}
        anthropic_bps: Dict[str, Any] = {"message_indices": bp_indices}
        if tools:
            anthropic_bps["tool_last"] = True
        if bp_indices or anthropic_bps.get("tool_last"):
            breakpoints["anthropic"] = anthropic_bps

        # --- Token estimates (content-addressed encode cache) ---
        layer_tokens = self._estimate_layer_tokens(sections)
        tools_tokens = _count_tools_tokens(tools) if tools else 0

        cached_tokens = sum(
            layer_tokens.get(name, 0)
            for name in ["system", "memory"]
        ) + tools_tokens

        dynamic_tokens = sum(
            layer_tokens.get(name, 0)
            for name in ["recent", "current"]
        )

        total = cached_tokens + dynamic_tokens
        token_estimates = {
            **layer_tokens,
            "tools_param": tools_tokens,
            "total_cached": cached_tokens,
            "total_dynamic": dynamic_tokens,
            "total": total,
            "estimated_hit_rate_pct": round(
                (cached_tokens / max(total, 1)) * 100, 1
            ),
        }

        if cached_tokens < 2048:
            logger.warning(
                f"Cached prefix = {cached_tokens} tokens, "
                f"below Anthropic 2048 minimum."
            )

        return AssembledPrompt(
            messages=all_messages,
            tools=tools,
            breakpoints=breakpoints,
            token_estimates=token_estimates,
            document=doc,
        )


# ==================== Global Instance ====================

_prompt_assembler: Optional[PromptAssembler] = None


def get_prompt_assembler(project_root: Optional[Path] = None) -> PromptAssembler:
    """Get the global prompt assembler instance."""
    global _prompt_assembler
    if _prompt_assembler is None:
        _prompt_assembler = PromptAssembler()
    return _prompt_assembler


class PromptError(Exception):
    """Prompt related errors."""
    pass
