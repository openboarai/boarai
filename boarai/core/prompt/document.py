"""
PromptDocument — Provider-neutral prompt representation (AST).

The assembler produces a PromptDocument (list of sections), not raw messages.
Each provider renderer converts the document into its native format.

This decouples prompt semantics from provider-specific message formatting,
enabling Anthropic cache_control, OpenAI auto-caching, and future providers
to share the same assembly logic.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


@dataclass
class PromptSection:
    """A single section of the prompt document."""
    name: str
    content: Union[str, List[Dict[str, Any]]]
    role: str = "system"
    stability: int = 1
    breakpoint_after: bool = False
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        # A section is empty only if it has no text content AND carries no
        # tool metadata. An assistant message with tool_calls (empty text)
        # or a tool message with tool_call_id must NOT be dropped.
        # Multimodal content (list of blocks) counts as present.
        if isinstance(self.content, str):
            if self.content.strip():
                return False
        elif self.content:
            return False
        return self.tool_calls is None and self.tool_call_id is None


@dataclass
class PromptDocument:
    """Provider-neutral prompt document."""
    sections: List[PromptSection] = field(default_factory=list)
    tools: Optional[List[Dict[str, Any]]] = None
    token_estimates: Dict[str, int] = field(default_factory=dict)

    @property
    def messages(self) -> List[Dict[str, str]]:
        """Convert sections to flat message list (for backward compatibility)."""
        result: List[Dict[str, Any]] = []
        for s in self.sections:
            if s.is_empty:
                continue
            msg: Dict[str, Any] = {"role": s.role, "content": s.content}
            if s.tool_call_id is not None:
                msg["tool_call_id"] = s.tool_call_id
            if s.tool_calls is not None:
                msg["tool_calls"] = s.tool_calls
            result.append(msg)
        return result

    @property
    def non_empty_sections(self) -> List[PromptSection]:
        """Sections with content, in order."""
        return [s for s in self.sections if not s.is_empty]

    @property
    def breakpoint_message_indices(self) -> List[int]:
        """Message indices where cache breakpoints should be placed."""
        indices = []
        msg_idx = 0
        for section in self.sections:
            if section.is_empty:
                continue
            if section.breakpoint_after:
                indices.append(msg_idx)
            msg_idx += 1
        return indices

    @property
    def system_content(self) -> str:
        """All system-role sections merged into one string."""
        parts = [s.content for s in self.sections if s.role == "system" and not s.is_empty]
        return "\n\n".join(parts)

    @property
    def non_system_messages(self) -> List[Dict[str, str]]:
        """Messages with non-system roles (user, assistant, tool)."""
        result: List[Dict[str, Any]] = []
        for s in self.sections:
            if s.role == "system" or s.is_empty:
                continue
            msg: Dict[str, Any] = {"role": s.role, "content": s.content}
            if s.tool_call_id is not None:
                msg["tool_call_id"] = s.tool_call_id
            if s.tool_calls is not None:
                msg["tool_calls"] = s.tool_calls
            result.append(msg)
        return result

    def get_section(self, name: str) -> Optional[PromptSection]:
        """Get a section by name."""
        for s in self.sections:
            if s.name == name:
                return s
        return None

    def add_section(self, section: PromptSection) -> None:
        """Append a section."""
        self.sections.append(section)
