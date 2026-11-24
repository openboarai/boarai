"""Idle compression gate: threshold, compress, create_run race guard."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set

from boarai.core.extension import get_extension_manager, get_tool_registry
from boarai.core.prompt import InsertThenCompress, get_idle_timer, get_prompt_assembler
from boarai.core.session.thinking import get_fallback_machine, safe_compress

logger = logging.getLogger(__name__)


class CompressionGate:
    """Owns idle timer arm/cancel and compress-vs-create race guard."""

    def __init__(
        self,
        active_run_predicate: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self._compressor = InsertThenCompress()
        self._idle_timer = get_idle_timer()
        self._compressing_sessions: Set[str] = set()
        # Strong refs to compression tasks so they are not GC'd and survive
        # timer cancellation (the timer task only SPAWNS them now).
        self._compress_tasks: Dict[str, "asyncio.Task"] = {}
        self._sm: Optional[Any] = None
        self._llm_svc: Optional[Any] = None
        # RunManager injects its registry lookup here so this module never
        # imports run_manager back (that cycle used to be broken with a lazy
        # import inside _has_active_run).
        self._active_run_predicate = active_run_predicate

    def _has_active_run(self, session_id: str) -> bool:
        """Whether a QUEUED/RUNNING run exists for this session."""
        if self._active_run_predicate is not None:
            try:
                return bool(self._active_run_predicate(session_id))
            except Exception as e:  # noqa: BLE001
                # Failing safe here means "skip compression". Silent, that
                # lets a session grow until the provider overflows with no
                # trace of why the gate never fired.
                logger.warning(
                    "Active-run probe for session %s raised (%s); treating the "
                    "session as busy and skipping compression. A repeat of this "
                    "means the session grows until the provider rejects it — "
                    "check the injected registry predicate.",
                    session_id,
                    e,
                )
                return True
        try:
            from boarai.core.session.run_manager import get_run_manager

            return (
                get_run_manager().get_active_run_for_session(session_id)
                is not None
            )
        except Exception:  # noqa: BLE001
            return False

    @property
    def _session_manager(self) -> Any:
        if self._sm is None:
            from boarai.core.session.manager import get_session_manager

            self._sm = get_session_manager()
        return self._sm

    @property
    def _llm(self) -> Any:
        if self._llm_svc is None:
            from boarai.core.llm import get_llm_service

            self._llm_svc = get_llm_service()
        return self._llm_svc

    def _chunks_dir(self, session_id: str) -> Any:
        """Path to session chunk archive dir (.boarai/sessions/{id}/chunks)."""
        from pathlib import Path

        base = Path(getattr(self._session_manager, "_sessions_dir", None))
        if not base:
            from boarai.core.runtime import get_data_dir
            base = get_data_dir() / "sessions"
        return base / session_id / "chunks"

    async def _archive_chunk(self, session_id: str,
                             messages: List[Dict[str, Any]]) -> Optional[str]:
        """Archive raw messages into a chunk .md file (v1.5, Clacky-style)."""
        import asyncio
        from datetime import datetime

        if not messages:
            return None
        chunks_dir = self._chunks_dir(session_id)
        await asyncio.to_thread(chunks_dir.mkdir, parents=True, exist_ok=True)

        # Find next chunk index (max existing + 1 — len-based naming would
        # overwrite chunks if any were ever removed).
        existing = []
        try:
            existing = [p for p in chunks_dir.iterdir()
                        if p.suffix == ".md" and p.name.startswith("chunk-")]
        except Exception:
            existing = []
        idx = 0
        for p in existing:
            try:
                idx = max(idx, int(p.stem.split("-")[1]))
            except (IndexError, ValueError):
                continue
        idx += 1
        fname = f"chunk-{idx:03d}.md"
        path = chunks_dir / fname

        # Build markdown with topics front matter + messages
        topics = []
        lines = [f"# Chunk {idx} — archived on "
                 f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                 ""]
        non_sys = [m for m in messages if m.get("role") != "system"]
        for m in non_sys[:50]:  # cap 50 messages per chunk
            role = m.get("role", "")
            content = str(m.get("content", ""))[:2000]
            if role == "system":
                continue
            if content:
                lines.append(f"## {role}")
                lines.append(content)
                lines.append("")
            if m.get("tool_calls"):
                for tc in m.get("tool_calls", []):
                    fn = tc.get("function", {}).get("name", "")
                    args = str(tc.get("function", {}).get("arguments", ""))[:500]
                    if fn:
                        topics.append(fn)
                        lines.append(f"### tool_call: {fn}")
                        if args:
                            lines.append(f"```\n{args}\n```")
                            lines.append("")

        body = "\n".join(lines)
        try:
            await asyncio.to_thread(
                path.write_text, body, encoding="utf-8"
            )
        except Exception as e:
            logger.warning("Failed to write chunk %s: %s", path, e)
            return None
        def _mark_chunk() -> None:
            from boarai.core.runtime.artifact_mark import fingerprint_path, write_mark

            # Marking fsyncs: keep it off the event loop.
            write_mark(
                "chunk",
                str(path),
                run_id=session_id,
                output_fp=fingerprint_path(path),
            )

        try:
            await asyncio.to_thread(_mark_chunk)
        except Exception:  # noqa: BLE001 — bookkeeping only
            pass
        return fname

    def _build_chunk_index(self, session_id: str) -> str:
        """Build a Previous-chunks index block for the summary (v1.5)."""
        chunks_dir = self._chunks_dir(session_id)
        try:
            files = sorted(
                (p for p in chunks_dir.iterdir()
                 if p.suffix == ".md" and p.name.startswith("chunk-")),
                key=lambda p: p.name,
                reverse=True,
            )
        except Exception:
            files = []
        if not files:
            return ""
        lines = ["", "Previous chunks (newest first):"]
        for p in files[:10]:
            lines.append(f"- `{p.name}`")
        if len(files) > 10:
            lines.append(f"- ...and {len(files) - 10} older chunks")
        lines.append("Use file_read on a chunk to recall details.")
        return "\n".join(lines)

    def cancel_timer(self, session_id: str) -> None:
        self._idle_timer.cancel(session_id)

    def arm_after_run(self, session_id: str) -> None:
        self._idle_timer.start(session_id, self.on_idle_compress)

    async def wait_ready(self, session_id: str, timeout: float = 30.0) -> None:
        if session_id not in self._compressing_sessions:
            return
        logger.info(
            "Session %s is being compressed, waiting before creating run",
            session_id,
        )
        deadline = time.time() + timeout
        while session_id in self._compressing_sessions:
            if time.time() > deadline:
                logger.warning(
                    "Timeout waiting for compression on session %s", session_id
                )
                break
            await asyncio.sleep(0.2)

    async def on_idle_compress(self, session_id: str) -> None:
        """Timer callback — spawn compression as an INDEPENDENT task."""
        if session_id in self._compressing_sessions:
            return
        if self._has_active_run(session_id):
            logger.debug("Compress skipped for %s: run active", session_id)
            return
        if not await self._should_compress_by_threshold(session_id):
            return

        self._compressing_sessions.add(session_id)
        self._compress_tasks[session_id] = asyncio.create_task(
            self._run_compress(session_id)
        )

    async def _run_compress(self, session_id: str) -> None:
        try:
            try:
                await self._do_compress(session_id)
            finally:
                self._compressing_sessions.discard(session_id)
                self._compress_tasks.pop(session_id, None)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            # Reaching this point means even the deterministic fallback did not
            # run (session I/O or rendering itself broke). An uncompressed session
            # runs until the provider overflows, so leave a traceable stack
            # instead of a single message line.
            logger.error(
                "Idle compress failed for session %s: %s",
                session_id,
                e,
                exc_info=True,
            )

    async def _should_compress_by_threshold(self, session_id: str) -> bool:
        session = await self._session_manager.get_session(session_id)
        if not session:
            logger.warning("Threshold check: session %s not found", session_id)
            return False

        usage = 0
        if session.cache_requests:
            last_req = session.cache_requests[-1]
            # Context footprint = last turn's prompt size (context_tokens);
            # legacy entries only have input_tokens (same meaning back then).
            usage = (
                last_req.get("context_tokens")
                or last_req.get("input_tokens", 0)
                or last_req.get("prompt_tokens", 0)
            )

        if usage <= 0:
            logger.debug(
                "Threshold check: session %s has 0 context usage, skip", session_id
            )
            return False

        context_window = 128000
        try:
            from boarai.core.prompt.context_detector import detect_context_window

            context_window = detect_context_window(
                model=session.model or "",
                provider="",
            )
        except Exception as e:
            logger.debug("Context window detection failed for threshold check: %s", e)

        ratio = usage / max(context_window, 1)

        threshold = 0.70
        try:
            from boarai.core.config import get_config_parser

            cp = get_config_parser()
            if cp and cp._config and hasattr(cp._config, "session"):
                threshold = cp._config.session.compression_threshold
        except Exception as e:
            logger.debug("Failed to read compression_threshold from config: %s", e)

        if ratio < threshold:
            logger.info(
                "Compress skipped for %s: %s/%s (%.0f%%) < threshold (%.0f%%)",
                session_id,
                usage,
                context_window,
                ratio * 100,
                threshold * 100,
            )
            return False

        logger.info(
            "Compress triggered for %s: %s/%s (%.0f%%) >= threshold (%.0f%%)",
            session_id,
            usage,
            context_window,
            ratio * 100,
            threshold * 100,
        )
        return True

    # --- Three-level compression fallback ---
    #
    # Compression is the only defence against context overflow. Failing
    # silently means the session runs until the provider errors out, with no
    # clue for the user or the logs. So three levels degrade in order, and each
    # one leaves a trace:
    #   1. LLM summary (safe_compress, two retries on the same model)
    #   2. one more attempt on a different model from the fallback chain
    #   3. deterministic summary with no LLM (tool_result_trimmer pruning +
    #      render_summary)

    async def _compress_llm_levels(
        self,
        session_id: str,
        session_raw: List[Dict[str, Any]],
        zone1_zone2: List[Dict[str, str]],
        memory_context: Dict[str, str],
        task_context: Dict[str, Any],
        session_model: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Levels 1 and 2: LLM summary, then one retry on a fallback model."""
        result = await safe_compress(
            compressor=self._compressor,
            session_messages=session_raw,
            zone1_zone2=zone1_zone2,
            llm_service=self._llm,
            memory_context=memory_context,
            task_context=task_context,
        )
        if result and result.get("summary"):
            return result

        tried = {session_model or "", (result or {}).get("_model") or ""}
        alt = next(
            (m for m in get_fallback_machine().fallback_models() if m and m not in tried),
            None,
        )
        if alt is None:
            logger.error(
                "Compress for %s: LLM summary failed and the fallback chain "
                "offers no different model", session_id,
            )
            return None

        logger.warning(
            "Compress for %s: LLM summary failed, retrying once on fallback "
            "model %s", session_id, alt,
        )
        result = await safe_compress(
            compressor=self._compressor,
            session_messages=session_raw,
            zone1_zone2=zone1_zone2,
            llm_service=self._llm,
            model=alt,
            memory_context=memory_context,
            task_context=task_context,
            max_retries=0,
        )
        if result and result.get("summary"):
            return result
        logger.error(
            "Compress for %s: fallback model %s also failed", session_id, alt,
        )
        return None

    def _deterministic_summary_text(
        self,
        session_raw: List[Dict[str, Any]],
        task_context: Dict[str, Any],
    ) -> str:
        """Level 3: deterministic summary with no LLM involved."""
        from boarai.core.prompt.tool_result_trimmer import trim

        max_items = 40
        max_item_chars = 400

        lines: List[str] = []
        tool_names: List[str] = []
        for m in session_raw:
            for tc in m.get("tool_calls") or []:
                fn = (tc.get("function") or {}).get("name", "") if isinstance(tc, dict) else ""
                if fn and fn not in tool_names:
                    tool_names.append(fn)
            role = m.get("role", "")
            if role not in ("user", "assistant"):
                continue
            content = str(m.get("content", "") or "").strip()
            if not content or len(lines) >= max_items:
                continue
            label = "User" if role == "user" else "Assistant"
            lines.append(f"- {label}: {trim(content)[:max_item_chars]}")

        parts: List[str] = []
        if tool_names:
            parts.append("Tools used: " + ", ".join(tool_names[:20]))
        parts.extend(lines)
        digest = "\n".join(parts) or "(no textual content to summarize)"
        background = (
            "[deterministic fallback — LLM summarization was unavailable; this "
            "digest lists only what was literally present in the transcript, so "
            "a topic missing here is not evidence it did not happen]\n" + digest
        )

        ts_goal = str(task_context.get("goal", "") or "").strip()
        ts_step = str(task_context.get("current_step", "") or "").strip()
        ts_files = list(task_context.get("working_files", []) or [])
        data: Dict[str, Any] = {
            "new_background": background,
            "new_decisions": "",
            "new_preferences": "",
            "referenced_files": "",
            "updated_task": (
                {
                    "goal": ts_goal,
                    "current_step": ts_step,
                    "working_files": ts_files,
                    "decisions": [],
                    "completed": [],
                    "is_blocked": bool(task_context.get("is_blocked", False)),
                }
                if (ts_goal or ts_step or ts_files)
                else None
            ),
        }
        return self._compressor.render_summary(data)

    async def _project_attachments_for_compress(
        self, session_id: str, attachments: List[Dict[str, Any]]
    ) -> str:
        """Text projection of attachments for the compression selection."""
        from boarai.core.session import attachments as _att

        notes: List[str] = []
        for att in attachments or []:
            if not isinstance(att, dict):
                continue
            name = att.get("name") or att.get("stored_name") or "attachment"
            stored = _att.sanitize_filename(att.get("stored_name") or "")
            path = _att.resolve_attachment_path(session_id, stored) if stored else None
            if (att.get("kind") or "") == "image":
                desc = await _att.sidecar_describe(path) if path is not None else None
                if desc:
                    notes.append(f"[Image: {name}]\n{desc}\n[/Image]")
                elif path is not None:
                    notes.append(f"[Image: {name} — readable at {path}]")
            elif path is not None:
                notes.append(f"[Attachment: {name} — readable at {path}]")
        if not notes:
            return ""
        return "\n\n" + "\n\n".join(notes)

    async def _do_compress(self, session_id: str) -> None:
        session = await self._session_manager.get_session(session_id)
        if not session:
            logger.warning("Compress: session %s not found", session_id)
            return
        if len(session.messages) < 4:
            logger.warning(
                "Compress: session %s has only %s messages (< 4)",
                session_id,
                len(session.messages),
            )
            return

        assembler = get_prompt_assembler()
        ext_mgr = get_extension_manager()
        tool_reg = get_tool_registry()
        assembled = assembler.assemble_messages(
            user_input="",
            session_messages=[],
            session_id=session_id,
            extension_manager=ext_mgr,
            tool_registry=tool_reg,
        )
        zone1_zone2 = list(assembled.messages)

        session_raw = []
        projected_source = []
        for m in session.messages:
            if m.metadata and m.metadata.get("type") == "archived_background":
                continue
            entry = {"role": m.role, "content": m.content}
            if m.tool_calls:
                entry["tool_calls"] = m.tool_calls
            if m.role == "tool":
                entry["tool_call_id"] = m.tool_call_id or ""
            elif m.tool_call_id:
                entry["tool_call_id"] = m.tool_call_id
            session_raw.append(entry)
            projected_source.append(m)

        # Compressibility gate BEFORE archiving (and before any attachment
        # projection): a skipped compression must not leave a duplicate
        # chunk file behind on every timer fire, nor pay vision-model calls
        # for a summary that never happens.
        non_sys = sum(1 for m in session_raw if m.get("role") != "system")
        if not self._compressor.should_compress(session_raw):
            logger.warning(
                "Compress: session %s skipped — %s total, %s non-system, "
                "need > %s non-system msgs",
                session_id,
                len(session_raw),
                non_sys,
                self._compressor._keep_raw_turns * 2,
            )
            return

        # v1.5 chunk archive: persist the FULL raw history before trimming so
        # details survive in .md chunks (recallable via file_read) while the
        # live prompt stays compact. The archive stays VERBATIM — attachment
        # projections ride only the summary input below.
        await self._archive_chunk(session_id, session_raw)

        # Attachment projection (multimodal overhaul P4c): without it the
        # summary (and the trim that follows) would silently forget every
        # attachment once the owning messages leave history. Runs after the
        # gate so a skipped compression costs no vision calls.
        summary_raw = []
        for entry, m in zip(session_raw, projected_source):
            atts = (
                (m.metadata or {}).get("attachments") if m.role == "user" else None
            )
            if atts:
                projection = await self._project_attachments_for_compress(
                    session_id, atts
                )
                if projection:
                    entry = {
                        **entry,
                        "content": (
                            (entry.get("content") or "") + projection
                            if entry.get("content")
                            else projection.strip()
                        ),
                    }
            summary_raw.append(entry)

        logger.info(
            "Idle compress: session %s (%s msgs, %s non-system)",
            session_id,
            len(session_raw),
            non_sys,
        )

        memory_context = {}
        try:
            from boarai.core.memory import get_memory_manager
            from boarai.core.memory.types import MemoryQuery

            mm = get_memory_manager()
            if mm:
                for field, etype in [
                    ("decisions", "decision"),
                    ("preferences", "preference"),
                    ("environment", "fact"),
                ]:
                    results = await mm.recall(
                        MemoryQuery(
                            entry_type=etype,
                            limit=5,
                            min_importance=0.5,
                        ),
                        pod_uris=["pod://global"],
                    )
                    memory_context[field] = self._fmt_memory_list(results)
        except Exception as e:
            logger.debug("Memory context load skipped: %s", e)

        task_context = {}
        try:
            from boarai.core.prompt.task_state import get_task_state_manager

            tsm = get_task_state_manager()
            ts = await tsm.load(session_id)
            task_context = {
                "goal": ts.goal,
                "current_step": ts.current_step,
                "working_files": ts.working_files,
                "is_blocked": ts.is_blocked,
            }
        except Exception as e:
            logger.debug("TaskState load skipped: %s", e)

        result = await self._compress_llm_levels(
            session_id,
            summary_raw,
            zone1_zone2,
            memory_context,
            task_context,
            session_model=session.model or "",
        )

        summary_text = (result or {}).get("summary", "") or ""
        if summary_text:
            # Ledger the compression call itself — its input (Z1+Z2+full history)
            # is one of the largest single token consumers in the system.
            try:
                from boarai.core.session.usage_ledger import get_usage_ledger

                await get_usage_ledger().record_side_usage(
                    result.get("_usage") or {},
                    kind="compression",
                    model=result.get("_model") or "unknown",
                    session_id=session_id,
                    session_title=getattr(session, "title", "") or "",
                    agent_name=getattr(session, "agent_name", "") or "default",
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("Compression usage ledger skipped: %s", e)
        else:
            # Level 3: deterministic fallback. Compression never fails silently —
            # the context must be cut down, or the session runs until the provider
            # overflows and the user is left without a visible cause.
            summary_text = self._deterministic_summary_text(
                summary_raw, task_context
            )
            logger.error(
                "Compress for %s: LLM summary and its fallback model both "
                "failed; used deterministic digest of %s messages",
                session_id,
                len(summary_raw),
            )

        # v1.5: append a Previous-chunks index so the summary knows where the
        # archived raw history lives (Clacky-style, keeps prompt non-bloated).
        chunk_index = self._build_chunk_index(session_id)
        if chunk_index:
            summary_text = summary_text + "\n" + chunk_index

        # A run may have started while the summary LLM call was in flight —
        # rewriting session.messages now would invalidate the executor's
        # message indices. Abort BEFORE any destructive mutation (the summary
        # work is wasted, but session state stays consistent).
        if self._has_active_run(session_id):
            logger.info(
                "Compress aborted for %s: run became active during summary",
                session_id,
            )
            return

        # v2: summary becomes a [BoarAi_System] USER message (not a system
        # section) placed right after the session-context inject. This keeps
        # the system prefix immutable. The legacy field is retained for
        # distill_worker / API / UI backward compat.
        session.compressed_summary = summary_text

        # Anchor-based trimming: drop everything BEFORE the last goal inject,
        # keep the active task (goal onward). Without a goal, fall back to the
        # classic keep-N-turns window.
        goal_inject = session.find_last_goal_inject()
        if goal_inject is not None:
            self._trim_after_goal(session, goal_inject)
        else:
            session.trim_history(self._compressor._keep_raw_turns)

        session.messages = self._ensure_tool_pairs(session.messages)
        session.insert_or_replace_summary(summary_text)

        ts_data = (result or {}).get("updated_task")
        if ts_data:
            try:
                from boarai.core.prompt.task_state import get_task_state_manager

                tsm = get_task_state_manager()
                existing = await tsm.load(session_id)
                existing.goal = ts_data.get("goal") or existing.goal
                existing.current_step = (
                    ts_data.get("current_step") or existing.current_step
                )
                existing.working_files = list(
                    set(existing.working_files + ts_data.get("working_files", []))
                )
                existing.decisions = list(
                    set(existing.decisions + ts_data.get("decisions", []))
                )
                existing.completed = list(
                    set(existing.completed + ts_data.get("completed", []))
                )
                await tsm.save(existing)
            except Exception as e:
                logger.debug("TaskState merge skipped: %s", e)

        try:
            from boarai.core.memory import get_memory_manager
            from boarai.core.memory.types import MemoryEntry

            mm = get_memory_manager()
            # The deterministic fallback (result is None) has no LLM-extracted
            # decisions/preferences, so this block only has content to write when
            # an LLM result is available.
            if mm and result:
                pod_uri = f"pod://session/{session_id}"
                for field, importance, entry_type in [
                    ("new_decisions", 0.9, "decision"),
                    ("new_preferences", 0.8, "preference"),
                    ("new_background", 0.5, "fact"),
                ]:
                    val = result.get(field, "")
                    if val and val not in (
                        "none",
                        "none confirmed",
                        "none noted",
                        "",
                        "未改变",
                    ):
                        if not await self._memory_has_similar(mm, pod_uri, val):
                            entry = MemoryEntry(
                                pod_uri=pod_uri,
                                topic=f"{entry_type} from session {session_id[:8]}",
                                content=val,
                                entry_type=entry_type,
                                tags=[
                                    "compressed",
                                    entry_type,
                                    session.agent_name or "default",
                                ],
                                importance=importance,
                                metadata={
                                    "channel": getattr(session, "source", "system"),
                                },
                                source_session=session_id,
                                source_agent=session.agent_name or "default",
                            )
                            await mm.remember(pod_uri, entry, publish=False)
        except Exception as e:
            logger.debug("Memory write skipped: %s", e)

        await self._session_manager.save_session(session_id)
        logger.info(
            "Idle compress complete: %s, %s -> %s msgs",
            session_id,
            len(session_raw),
            len(session.messages),
        )

    @staticmethod
    def _trim_after_goal(session, goal_inject) -> None:
        """Keep only the active task: content from the latest goal inject onward."""
        goal_idx = None
        for i, m in enumerate(session.messages):
            if m is goal_inject or m.message_id == goal_inject.message_id:
                goal_idx = i
                break
        if goal_idx is None:
            session.trim_history(10)
            return

        # Keep system messages + [BoarAi_System] injects before goal (session
        # context / older summaries) and everything from the goal onward.
        kept = []
        for i, m in enumerate(session.messages):
            if i < goal_idx:
                if m.role == "system" or session.is_system_inject(m):
                    kept.append(m)
            else:
                kept.append(m)

        session.messages = kept
        session.message_count = len(session.messages)
        session.compression_count += 1
        from datetime import datetime as _dt

        session.updated_at = _dt.now()

    @staticmethod
    def _fmt_memory_list(items: List) -> str:
        if not items:
            return "none"
        parts = []
        for i in items[:5]:
            content = getattr(i, "content", None)
            if content is None and isinstance(i, dict):
                content = i.get("content", str(i))
            parts.append(str(content)[:80])
        return ", ".join(parts)

    @staticmethod
    async def _memory_has_similar(mm, pod_uri: str, content: str) -> bool:
        try:
            from boarai.core.memory.types import MemoryQuery
            existing = await mm.recall(
                MemoryQuery(text=content[:100], limit=5),
                pod_uris=[pod_uri],
            )
            return bool(existing)
        except Exception:
            return False

    @staticmethod
    def _ensure_tool_pairs(messages: List) -> List:
        if not messages:
            return messages
        tool_call_ids = set()
        tool_result_ids = set()
        for m in messages:
            tc = getattr(m, "tool_calls", None) or []
            if tc:
                for tc_item in tc:
                    if isinstance(tc_item, dict):
                        tool_call_ids.add(tc_item.get("id", ""))
            tid = getattr(m, "tool_call_id", None)
            if tid:
                tool_result_ids.add(tid)
        orphan_call_ids = tool_call_ids - tool_result_ids
        orphan_result_ids = tool_result_ids - tool_call_ids
        if not orphan_call_ids and not orphan_result_ids:
            return messages
        filtered = []
        for m in messages:
            tc = getattr(m, "tool_calls", None) or []
            tid = getattr(m, "tool_call_id", None)
            if tc and any(
                item.get("id", "") in orphan_call_ids
                for item in tc
                if isinstance(item, dict)
            ):
                continue
            if tid and tid in orphan_result_ids:
                continue
            filtered.append(m)
        return filtered

    # (_ensure_last_user_anchor removed: it was a no-op. Requests that would
    # end on a bare assistant message (reuse-last-user retries) are handled at
    # the executor level with a transient RETRY_NUDGE user message instead —
    # persisted history must not be reshaped to satisfy one provider call.)