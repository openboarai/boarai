"""
Task State manager — structured conversation state.

Instead of sending raw conversation history or a natural-language summary,
this module maintains a structured task state that the LLM can reference:

    goal: What the user is trying to achieve
    current_step: What's being done right now
    working_files: Files being edited
    decisions: Confirmed decisions
    open_questions: Unresolved items
    completed: Done items
    pending: Todo items

The state updates lazily (every N turns or on task change), NOT every turn.
This keeps the cached prefix stable for many turns, maximizing cache hits.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# Update task state every N turns (not every turn)
UPDATE_INTERVAL_TURNS = 5


@dataclass
class TaskState:
    """Structured task state for prompt injection."""
    goal: str = ""
    current_step: str = ""
    working_files: List[str] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)
    completed: List[str] = field(default_factory=list)
    pending: List[str] = field(default_factory=list)

    # Tracking
    version: int = 0
    updated_at: str = ""
    turns_since_update: int = 0
    _last_delta: str = field(default="", repr=False)
    """Incremental [BoarAi_System] inject produced by the last update."""

    @property
    def last_delta(self) -> str:
        """The incremental task-state delta from the most recent update."""
        return getattr(self, "_last_delta", "")

    def should_update(self) -> bool:
        """Check if task state should be refreshed."""
        if self.version == 0:
            return True
        return self.turns_since_update >= UPDATE_INTERVAL_TURNS

    def mark_updated(self) -> None:
        """Mark that the state was just updated."""
        self.version += 1
        self.updated_at = datetime.now().isoformat()
        self.turns_since_update = 0

    def increment_turn(self) -> None:
        """Called after each conversation turn."""
        self.turns_since_update += 1

    def to_yaml(self) -> str:
        """Render as YAML for prompt injection."""
        data: Dict[str, Any] = {}
        if self.goal:
            data["goal"] = self.goal
        if self.current_step:
            data["current_step"] = self.current_step
        if self.working_files:
            data["working_files"] = self.working_files
        if self.decisions:
            data["decisions"] = self.decisions
        if self.open_questions:
            data["open_questions"] = self.open_questions
        if self.completed:
            data["completed"] = self.completed
        if self.pending:
            data["pending"] = self.pending

        if not data:
            return ""

        return yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for persistence."""
        return {
            "goal": self.goal,
            "current_step": self.current_step,
            "working_files": list(self.working_files),
            "decisions": list(self.decisions),
            "open_questions": list(self.open_questions),
            "completed": list(self.completed),
            "pending": list(self.pending),
            "version": self.version,
            "updated_at": self.updated_at,
            "turns_since_update": self.turns_since_update,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskState":
        """Deserialize from dictionary."""
        return cls(
            goal=data.get("goal", ""),
            current_step=data.get("current_step", ""),
            working_files=data.get("working_files", []),
            decisions=data.get("decisions", []),
            open_questions=data.get("open_questions", []),
            completed=data.get("completed", []),
            pending=data.get("pending", []),
            version=data.get("version", 0),
            updated_at=data.get("updated_at", ""),
            turns_since_update=data.get("turns_since_update", 0),
        )


class TaskStateManager:
    """Per-session task state manager."""

    def __init__(self, sessions_dir: Path):
        self._sessions_dir = Path(sessions_dir)

    def _get_state_path(self, session_id: str) -> Path:
        safe_id = session_id
        for char in ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]:
            safe_id = safe_id.replace(char, "_")
        return self._sessions_dir / safe_id / "task_state.json"

    def load(self, session_id: str) -> TaskState:
        """Load task state for a session."""
        import json

        path = self._get_state_path(session_id)
        if not path.exists():
            return TaskState()

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return TaskState.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load task state for {session_id}: {e}")
            return TaskState()

    def save(self, session_id: str, state: TaskState) -> None:
        """Save task state for a session."""
        import json

        path = self._get_state_path(session_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Failed to save task state for {session_id}: {e}")

    def update_from_llm(
        self,
        session_id: str,
        state: TaskState,
        user_input: str,
        assistant_response: str,
        llm_service: Any = None,
    ) -> TaskState:
        """Update task state using LLM extraction."""
        if not state.should_update():
            state.increment_turn()
            return state

        # Snapshot BEFORE update so the caller can diff changed fields.
        before = state.to_dict()

        # Try LLM extraction (async, but we do sync best-effort)
        # For now, use heuristic extraction
        self._heuristic_update(state, user_input, assistant_response)

        state.mark_updated()
        state._last_delta = self.compute_delta(before, state.to_dict())
        self.save(session_id, state)
        return state

    @staticmethod
    def compute_delta(before: Dict[str, Any], after: Dict[str, Any]) -> str:
        """Render changed task-state fields as a [BoarAi_System] inject text."""
        def _norm(v: Any) -> Any:
            if isinstance(v, list):
                return sorted(str(x) for x in v)
            return str(v).strip()

        changed = {}
        for key in ("goal", "current_step", "working_files", "decisions",
                    "open_questions", "completed", "pending"):
            old_v = _norm(before.get(key))
            new_v = _norm(after.get(key))
            if old_v != new_v and new_v not in ("", "[]"):
                changed[key] = new_v

        if not changed:
            return ""

        lines: List[str] = []
        if "goal" in changed:
            lines.append(f"{changed['goal']}")
            del changed["goal"]
        for key, val in changed.items():
            lines.append(f"{key}: {val}")

        return "[BoarAi_System] Goal: " + " | ".join(lines)

    @staticmethod
    def _heuristic_update(
        state: TaskState,
        user_input: str,
        assistant_response: str,
    ) -> None:
        """Heuristic task state extraction — populates all 7 fields."""
        import re

        # --- File paths ---
        file_pattern = r'[\w/\\]+\.\w{1,5}'
        files = set(re.findall(file_pattern, user_input + assistant_response))
        for f in files:
            if f not in state.working_files and len(f) > 3:
                state.working_files.append(f)
                if len(state.working_files) > 10:
                    state.working_files = state.working_files[-10:]

        # --- Trivial filter ---
        trivial_patterns = [
            "你好", "hello", "hi", "hey", "在吗", "在不在",
            "测试", "test", "谢谢", "thanks", "ok", "好的", "继续",
        ]
        is_trivial = (
            len(user_input) < 10
            or any(user_input.lower().strip().startswith(t) for t in trivial_patterns)
        )

        # --- Goal (first meaningful task) ---
        if not state.goal and not is_trivial and len(user_input) > 10:
            state.goal = user_input[:200]

        # --- Current step (latest non-trivial request) ---
        if not is_trivial and user_input:
            state.current_step = user_input[:200]

        # --- Decisions (extract from assistant: "use X", "prefer Y") ---
        decision_markers = [
            r'(?:使用|用|采用|选择|建议)\s*[:：]?\s*([A-Za-z\u4e00-\u9fff\s\w]+?)(?:\s*[，,。.]|$)',
            r'(?:use|using|choose|prefer)\s+([a-zA-Z0-9\s\-\_\.]+?)(?:[.,]|$)',
        ]
        for marker in decision_markers:
            for match in re.finditer(marker, assistant_response, re.IGNORECASE):
                decision = match.group(1).strip()
                if len(decision) > 3 and len(decision) < 80:
                    entry = f"Use {decision}" if not decision.startswith(("Use", "采用", "使用", "选择")) else decision
                    if entry not in state.decisions:
                        state.decisions.append(entry)
        if len(state.decisions) > 8:
            state.decisions = state.decisions[-8:]

        # --- Open questions (user asks something without answer confirmed) ---
        question_markers = [
            r'(?:怎么|如何|为什么|能不能|可以|要不要|是不是|有没有)\S*[\?？]?',
        ]
        for marker in question_markers:
            for match in re.finditer(marker, user_input):
                q = match.group().strip()
                if len(q) > 4 and q not in state.open_questions:
                    state.open_questions.append(q)
        if len(state.open_questions) > 5:
            state.open_questions = state.open_questions[-5:]

        # --- Completed (extract from assistant: "finished", "done") ---
        completed_markers = [
            r'(?:已完成|已创建|已实现|已修复|已添加|已更新|已删除)\s*[:：]?\s*([^，,。.\n]{3,60})',
            r'(?:done|created|implemented|fixed|added|updated)\s+([a-zA-Z0-9\s\-\_\.]{3,60})(?:[.,]|$)',
        ]
        for marker in completed_markers:
            for match in re.finditer(marker, assistant_response, re.IGNORECASE):
                item = match.group(1).strip()
                if item not in state.completed:
                    state.completed.append(item)
        # Also detect success statements
        for phrase in ["pass", "完成后", "测试通过", "successfully"]:
            if phrase.lower() in assistant_response.lower() and phrase not in state.completed:
                state.completed.append(phrase)
        if len(state.completed) > 10:
            state.completed = state.completed[-10:]

        # --- Pending (user asks for something not yet confirmed done) ---
        # A task is "pending" if user requested it AND it's not in completed list.
        # Extract actionable requests from user_input.
        request_markers = [
            r'(?:帮我|请|写一下|实现|创建|添加|修复|修改|测试|运行|检查|提交|总结)\s*[:：]?\s*([^，,。.\n]{3,80})',
        ]
        newly_requested = []
        for marker in request_markers:
            for match in re.finditer(marker, user_input):
                item = match.group(1).strip()
                if item and item not in state.completed and item not in state.pending:
                    newly_requested.append(item)

        # Only mark as pending if it wasn't just completed by assistant
        for item in newly_requested:
            already_done = any(
                item.lower() in c.lower() or c.lower() in item.lower()
                for c in state.completed[-3:]
            )
            if not already_done:
                state.pending.append(item)

        # Remove items from pending if they just got completed
        state.pending = [
            p for p in state.pending
            if not any(
                p.lower() in c.lower() or c.lower() in p.lower()
                for c in state.completed[-3:]
            )
        ]
        if len(state.pending) > 10:
            state.pending = state.pending[-10:]


# Global instance
_task_state_manager: Optional[TaskStateManager] = None


def get_task_state_manager(sessions_dir: Optional[Path] = None) -> TaskStateManager:
    """Get the global task state manager instance."""
    global _task_state_manager
    if _task_state_manager is None:
        if sessions_dir is None:
            try:
                from boarai.core.runtime import get_data_dir
                sessions_dir = get_data_dir() / "sessions"
            except Exception:
                raise RuntimeError(
                    "TaskStateManager not initialized. "
                    "Call get_task_state_manager(sessions_dir) first."
                )
        _task_state_manager = TaskStateManager(sessions_dir)
    return _task_state_manager
