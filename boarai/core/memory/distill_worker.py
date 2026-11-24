"""
DistillWorker — background idle-time distillation for trashed sessions/agents.

Scans the trash for entries marked distill_mode="pending",
loads their data, and runs LLM distillation via ReincarnationEngine.

Only runs when the system is idle (no active AI runs).
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..event import EventType, get_event_bus, publish_event

logger = logging.getLogger(__name__)

SCAN_INTERVAL = 300  # 5 minutes
MAX_RETRIES = 3


class DistillWorker:
    """Background worker for idle-time memory distillation."""

    def __init__(self, memory_manager=None):
        self._manager = memory_manager
        self._llm = None
        self._trash = None
        self._task: Optional[asyncio.Task] = None
        self._retry_counts: Dict[str, int] = {}

    def set_llm(self, llm_service) -> None:
        self._llm = llm_service

    async def start(self) -> None:
        """Start the background distillation loop."""
        from boarai.core.trash import get_trash_manager
        self._trash = get_trash_manager()
        self._task = asyncio.create_task(self._loop())
        logger.info("DistillWorker started (scan_interval=%ds)", SCAN_INTERVAL)

    async def stop(self) -> None:
        """Stop the background loop."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("DistillWorker stopped")

    async def _loop(self) -> None:
        """Main loop: periodically scan for pending distillation."""
        while True:
            try:
                await asyncio.sleep(SCAN_INTERVAL)
                if not self._is_idle():
                    continue
                await self._process_pending()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"DistillWorker loop error: {e}")

    def _is_idle(self) -> bool:
        """Check if the system is idle (no active AI runs)."""
        try:
            from boarai.core.session.run_manager import RunStatus, get_run_manager
            rm = get_run_manager()
            for run in rm.all_runs():
                if run.status == RunStatus.RUNNING:
                    return False
            return True
        except Exception:
            return True  # Assume idle if can't check

    async def _process_pending(self) -> None:
        """Process all pending distillation entries."""
        if not self._trash:
            return

        pending = self._trash.get_pending_distillation()
        if not pending:
            return

        logger.info(f"DistillWorker found {len(pending)} pending entries")
        for entry in pending:
            if not self._is_idle():
                break  # System got busy, stop

            try:
                await self._distill_entry(entry)
            except Exception as e:
                logger.error(f"Distillation failed for {entry.trash_id}: {e}")
                self._handle_failure(entry.trash_id)

    async def _distill_entry(self, entry) -> None:
        """Distill a single trash entry."""
        trash_path = Path(entry.trash_path)
        if not trash_path.exists():
            logger.warning(f"Trash path not found: {trash_path}")
            await self._trash.mark_distill_failed(entry.trash_id)
            return

        # Build entries from session/agent data
        entries = None
        if entry.type == "session":
            entries = await self._load_session_entries(trash_path, entry)
        elif entry.type == "agent":
            entries = await self._load_agent_entries(trash_path, entry)

        if not entries:
            logger.debug(f"No distillable data for {entry.trash_id}")
            await self._trash.mark_distilled(entry.trash_id)
            return

        # Run LLM distillation
        try:
            from .reincarnation import ReincarnationEngine
            engine = ReincarnationEngine(self._manager)
            epitaph, tags = await engine.distill_to_epitaph(entries, self._llm)

            if epitaph:
                await self._save_epitaph(entry, epitaph)

            # Mark as distilled
            await self._trash.mark_distilled(entry.trash_id)

            # Index tags through the live tag_index on the pod_store: a snapshot
            # taken in the constructor predates manager.start() building the
            # index, so self._tag_index would still be None here.
            tag_index = (
                self._manager.pod_store.tag_index if self._manager else None
            )
            if tags and tag_index:
                await tag_index.add(
                    f"distill_{entry.trash_id}", tags,
                )
                await tag_index.flush()

            logger.info(
                f"Distillation complete: {entry.type} {entry.trash_id}"
                + (f", {len(tags)} tags" if tags else "")
            )

        except Exception as e:
            raise

        # Reset retry count on success
        self._retry_counts.pop(entry.trash_id, None)

    async def _load_session_entries(self, trash_path: Path, entry) -> Optional[List]:
        """Load session messages from trash and build MemoryEntry list."""
        from .types import MemoryEntry, EntryType

        # Find session JSON (named {session_id}.json, not session.json)
        session_json = None
        for f in trash_path.glob("*.json"):
            # Skip non-session JSON files
            if f.name in ("memory.json", "prompt_cache.json", "task_state.json"):
                continue
            session_json = f
            break
        if not session_json:
            # Search one level deeper
            for f in trash_path.rglob("*.json"):
                if f.name in ("memory.json", "prompt_cache.json", "task_state.json"):
                    continue
                session_json = f
                break
        if not session_json or not session_json.exists():
            return None

        try:
            content = await asyncio.to_thread(
                session_json.read_text, encoding="utf-8",
            )
            data = json.loads(content)
        except (json.JSONDecodeError, FileNotFoundError):
            return None

        messages = data.get("messages", [])
        compressed = data.get("compressed_summary", "")
        title = data.get("title", "")

        if not messages and not compressed:
            return None

        entries = []

        # Add compressed summary as a single entry (if available)
        if compressed:
            entries.append(MemoryEntry(
                pod_uri=f"pod://session/{entry.metadata.get('session_id', '')}",
                topic=f"Session Summary: {title or 'untitled'}",
                content=compressed[:2000],
                entry_type=EntryType.SUMMARY.value,
                tags=["distilled", "compressed"],
                importance=0.7,
                source_session=entry.metadata.get("session_id", ""),
                metadata={"distilled_from_trash": True},
            ))

        # Add individual messages as entries (max 50 messages)
        for msg in messages[-50:]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if not content or role == "system":
                continue
            etype = EntryType.FACT.value
            if role == "user":
                etype = EntryType.FACT.value
            elif role == "assistant":
                etype = EntryType.DECISION.value

            entries.append(MemoryEntry(
                pod_uri=f"pod://session/{entry.metadata.get('session_id', '')}",
                topic=content[:120] if role == "user" else f"Response: {content[:60]}",
                content=content[:1000],
                entry_type=etype,
                tags=["distilled"],
                importance=0.4,
                source_session=entry.metadata.get("session_id", ""),
                metadata={"distilled_from_trash": True},
            ))

        return entries if entries else None

    async def _load_agent_entries(self, trash_path: Path, entry) -> Optional[List]:
        """Load agent config from trash and build MemoryEntry list."""
        from .types import MemoryEntry, EntryType

        # Find soul.md
        soul_md = trash_path / "soul.md"
        if not soul_md.exists():
            for f in trash_path.rglob("soul.md"):
                soul_md = f
                break
        if not soul_md.exists():
            return None

        # Find agent.yaml
        agent_yaml = trash_path / "agent.yaml"
        agent_data: Dict[str, Any] = {}
        if agent_yaml.exists():
            try:
                import yaml
                agent_content = await asyncio.to_thread(
                    agent_yaml.read_text, encoding="utf-8",
                )
                agent_data = yaml.safe_load(agent_content)
            except Exception:
                pass

        try:
            soul_content = await asyncio.to_thread(
                soul_md.read_text, encoding="utf-8",
            )
        except Exception:
            return None

        entries = [
            MemoryEntry(
                pod_uri=entry.metadata.get("original_path", ""),
                topic=f"Agent: {agent_data.get('name', 'unknown')}",
                content=f"Role: {agent_data.get('description', '')}\\nSoul:\\n{soul_content[:2000]}",
                entry_type=EntryType.FACT.value,
                tags=["distilled", "agent_identity"],
                importance=0.8,
                metadata={"distilled_from_trash": True, "agent_config": agent_data},
            )
        ]
        return entries

    async def _save_epitaph(self, entry, epitaph: str) -> None:
        """Save distillation epitaph to graveyard."""
        if not self._manager:
            return

        boarai_dir = self._manager.pod_store.boarai_dir
        if entry.type == "session":
            graveyard_dir = boarai_dir / "memory" / "graveyard" / "session"
        elif entry.type == "agent":
            graveyard_dir = boarai_dir / "memory" / "graveyard" / "agent"
        else:
            return

        graveyard_dir.mkdir(parents=True, exist_ok=True)
        pod_id = Path(entry.original_path).name
        filepath = graveyard_dir / f"{pod_id}.epitaph"

        epitaph_data = {
            "pod_uri": entry.original_path,
            "trash_id": entry.trash_id,
            "epitaph": epitaph,
            "created_at": datetime.now().isoformat(),
            "distilled_by": "DistillWorker",
        }
        await asyncio.to_thread(
            lambda: filepath.write_text(
                json.dumps(epitaph_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            ),
        )
        logger.info(f"Epitaph saved: {filepath}")

    def _handle_failure(self, trash_id: str) -> None:
        """Handle distillation failure with retry tracking."""
        self._retry_counts[trash_id] = self._retry_counts.get(trash_id, 0) + 1
        if self._retry_counts[trash_id] >= MAX_RETRIES:
            if self._trash:
                asyncio.ensure_future(self._trash.mark_distill_failed(trash_id))
            self._retry_counts.pop(trash_id, None)
            logger.warning(f"Distillation permanently failed after {MAX_RETRIES} retries: {trash_id}")
