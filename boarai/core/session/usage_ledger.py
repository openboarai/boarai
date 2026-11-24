"""Append-only global LLM usage ledger.

Token usage must survive session deletion: every terminal run path
(completed / cancelled / failed) appends one JSONL entry here, independent
of session storage. The monitoring API aggregates from this ledger so
historical totals never disappear when a session is removed.

Storage (dated layout): ``<data_dir>/logs/<YYYY-MM-DD>/usage.jsonl`` — one
JSON object per line, one file per day. A pre-dated flat file at
``<data_dir>/logs/usage.jsonl`` is still read/deleted so old entries remain
counted after the upgrade.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_LEDGER_REL = "logs/usage.jsonl"
_LEDGER_NAME = "usage.jsonl"


def _ledger_base_dir() -> Path:
    from boarai.core.runtime.data_paths import resolve_data_path

    return resolve_data_path(_LEDGER_REL).parent


class UsageLedger:
    """Append-only JSONL usage store (process-wide singleton)."""

    def __init__(self, path: Optional[Path] = None) -> None:
        if path is not None:
            self._base_dir = path.parent
            self._legacy_path = path
        else:
            self._base_dir = _ledger_base_dir()
            from boarai.core.runtime.data_paths import resolve_data_path
            self._legacy_path = resolve_data_path(_LEDGER_REL)
        self._lock = asyncio.Lock()
        # Terminal run ids already written by THIS process, so a duplicated
        # terminal path (e.g. completed then finalize running twice) cannot
        # inflate the token totals. Bounded: a long-lived server would
        # otherwise grow one id per run forever.
        self._recorded_runs: set = set()
        self._recorded_cap = 10_000

    @property
    def path(self) -> Path:
        """Today's ledger file (may not exist yet)."""
        return self._base_dir / datetime.now().strftime("%Y-%m-%d") / _LEDGER_NAME

    @property
    def files(self) -> List[Path]:
        """Every ledger file (oldest first): dated files + legacy flat file."""
        out: List[Path] = []
        try:
            for entry in sorted(self._base_dir.iterdir(), key=lambda p: p.name):
                if entry.is_dir() and len(entry.name) == 10 and entry.name[4] == "-":
                    f = entry / _LEDGER_NAME
                    if f.is_file():
                        out.append(f)
        except OSError:
            pass
        if self._legacy_path.is_file():
            out.append(self._legacy_path)
        return out

    def fingerprint(self) -> Optional[tuple]:
        """Cheap change detector across all ledger files (mtime+size)."""
        parts = []
        for f in self.files:
            try:
                st = f.stat()
                parts.append((str(f), st.st_mtime_ns, st.st_size))
            except OSError:
                continue
        return tuple(parts) if parts else None

    async def record(self, entry: Dict[str, Any]) -> None:
        """Append one usage entry. Never raises — stats must not break runs."""
        run_id = str(entry.get("run_id") or "")
        if run_id and entry.get("status"):
            if run_id in self._recorded_runs:
                logger.warning(
                    "Usage ledger: duplicate terminal entry for run %s (%s); dropped",
                    run_id, entry.get("status"),
                )
                return
            if len(self._recorded_runs) >= self._recorded_cap:
                self._recorded_runs.clear()
            self._recorded_runs.add(run_id)
        line = json.dumps(entry, ensure_ascii=False)
        async with self._lock:
            try:
                await asyncio.to_thread(self._append_sync, line)
            except Exception as e:  # noqa: BLE001
                logger.warning("Usage ledger append failed: %s", e)

    def _append_sync(self, line: str) -> None:
        path = self._base_dir / datetime.now().strftime("%Y-%m-%d") / _LEDGER_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    async def record_side_usage(
        self,
        usage: Dict[str, Any],
        *,
        kind: str,
        model: str = "unknown",
        session_id: str = "",
        session_title: str = "",
        agent_name: str = "default",
    ) -> None:
        """Record usage from auxiliary LLM calls (compression, subagent, distillation) that bypass the run executor."""
        if not usage:
            return
        prompt = int(usage.get("prompt_tokens", 0) or 0)
        completion = int(usage.get("completion_tokens", 0) or 0)
        if prompt <= 0 and completion <= 0:
            return
        cache_read = int(
            usage.get("cached_tokens", 0)
            or usage.get("cache_hit_tokens", 0)
            or usage.get("cache_read_input_tokens", 0)
            or 0
        )
        from datetime import datetime, timezone

        await self.record({
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": f"{kind}:{uuid.uuid4()}",
            "session_id": session_id,
            "session_title": session_title,
            "agent_name": agent_name,
            "model": model or "unknown",
            "input_tokens": prompt,
            "completion_tokens": completion,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": int(
                usage.get("cache_creation_input_tokens", 0) or 0
            ),
            "context_tokens": 0,
            "status": kind,
        })

    async def clear(self) -> None:
        """Delete every ledger file (full stats reset). Never raises."""
        async with self._lock:
            try:
                await asyncio.to_thread(self._clear_sync)
            except Exception as e:  # noqa: BLE001
                logger.warning("Usage ledger clear failed: %s", e)

    def _clear_sync(self) -> None:
        for f in self.files:
            try:
                f.unlink()
            except OSError:
                pass

    async def read_all(self) -> List[Dict[str, Any]]:
        """Read every ledger entry (skips corrupt lines)."""
        try:
            return await asyncio.to_thread(self._read_sync)
        except Exception as e:  # noqa: BLE001
            logger.warning("Usage ledger read failed: %s", e)
            return []

    def _read_sync(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for path in self.files:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            out.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue
        return out


    async def migrate_legacy_entries(
        self, entries: List[Dict[str, Any]]
    ) -> None:
        """Copy pre-ledger session usage entries into the ledger."""
        existing = {e.get("run_id") for e in await self.read_all()}
        for req in entries or []:
            sid = req.get("session_id", "")
            if not sid:
                continue
            legacy_id = f"legacy:{sid}:{req.get('index', 0)}"
            if legacy_id in existing:
                continue
            await self.record({
                "ts": req.get("ts", ""),
                "run_id": legacy_id,
                "session_id": sid,
                "session_title": req.get("session_title", "") or "",
                "agent_name": req.get("agent_name", "default"),
                "model": req.get("model", "unknown"),
                "input_tokens": req.get("input_tokens", 0),
                "completion_tokens": req.get("completion_tokens", 0),
                "cache_read_tokens": req.get("cache_read_tokens", 0),
                "cache_creation_tokens": req.get("cache_creation_tokens", 0),
                "status": "legacy",
            })
            existing.add(legacy_id)


_ledger: Optional[UsageLedger] = None


def get_usage_ledger() -> UsageLedger:
    """Process-wide ledger singleton."""
    global _ledger
    if _ledger is None:
        _ledger = UsageLedger()
    return _ledger
