"""
AuditLog — Structured security event logging for BoarAi.

Writes JSON-line audit records to a configurable log file.
Thread-safe via appending writes (no locking needed for most OS).
"""

import io
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class AuditLog:
    """Security audit log writer."""

    def __init__(self, config, project_root: Path):
        self._enabled = config.enabled if hasattr(config, 'enabled') else True
        log_path = getattr(config, 'log_path', '.boarai/logs/security.log')
        resolved = Path(log_path)
        if not resolved.is_absolute():
            from boarai.core.runtime import resolve_data_path
            resolved = resolve_data_path(str(resolved))
        if not resolved.is_absolute():
            resolved = project_root / resolved
        # Dated layout: records go to <base>/<YYYY-MM-DD>/<name>. The
        # resolved path itself (pre-dated flat file) is kept as the legacy
        # source for queries so old records stay readable.
        self._base_dir = resolved.parent
        self._filename = resolved.name
        self._legacy_path = resolved
        self._events_filter = set(
            getattr(config, 'events', None) or
            ["tool_execute", "tool_deny", "path_block", "approval"]
        )
        # Serialize concurrent appends so large JSON lines never interleave.
        self._write_lock = threading.Lock()
        logger.info(f"Audit log: {self._base_dir}/<date>/{self._filename} (enabled={self._enabled})")

    def _current_path(self) -> Path:
        """Today's audit file path (directory created lazily on write)."""
        from boarai.core.logging.handlers import dated_path
        return dated_path(self._base_dir, self._filename)

    def set_enabled(self, enabled: bool) -> None:
        """Turn the writer on/off at runtime (Settings → Security switch)."""
        self._enabled = bool(enabled)

    def set_events(self, events) -> None:
        """Replace the recorded event-type allowlist at runtime."""
        self._events_filter = set(events or [])

    def record(self, event: str, data: Dict[str, Any]) -> None:
        """Record a security event."""
        if not self._enabled:
            return
        if self._events_filter and event not in self._events_filter:
            return

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **data,
        }
        try:
            path = self._current_path()
            with self._write_lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            logger.warning(f"Audit write failed: {e}")

    def query(self, limit: int = 100, event_type: Optional[str] = None) -> list[Dict[str, Any]]:
        """Read recent audit records (for API / UI display)."""
        from boarai.core.logging.handlers import iter_date_dirs

        # Sources newest-first: today's dated file, then earlier date dirs,
        # then the pre-dated flat file.
        sources: list[Path] = [
            d / self._filename for d in reversed(iter_date_dirs(self._base_dir))
        ]
        if self._legacy_path.is_file():
            sources.append(self._legacy_path)

        results = []
        for path in sources:
            if len(results) >= limit:
                break
            if not path.is_file():
                continue
            try:
                # Read only the tail (limit*2) instead of the whole file, so
                # query memory stays bounded for large audit logs.
                lines = self._tail_lines(path, limit * 2)
            except OSError:
                continue
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event_type and record.get("event") != event_type:
                    continue
                results.append(record)
                if len(results) >= limit:
                    break
        return results

    @staticmethod
    def _tail_lines(path: Path, n: int) -> list[str]:
        """Read the last ``n`` lines of a file without loading it fully."""
        with open(path, "rb") as f:
            f.seek(0, io.SEEK_END)
            size = f.tell()
            block = 8192
            data = b""
            pos = size
            while pos > 0 and data.count(b"\n") <= n:
                read_size = min(block, pos)
                pos -= read_size
                f.seek(pos)
                data = f.read(read_size) + data
        return data.decode("utf-8", errors="replace").splitlines()
