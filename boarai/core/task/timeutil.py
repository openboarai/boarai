"""Timezone helpers for task scheduling (naive datetime in target TZ wall-clock)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

def resolve_tz(name: Optional[str]):
    """Return tzinfo for IANA name, or None for local naive."""
    if not name or name in ("local", "LOCAL"):
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        try:
            import pytz  # type: ignore
            return pytz.timezone(name)
        except Exception:
            return None

def now_for_schedule(timezone: Optional[str] = None) -> datetime:
    """Current wall-clock time for schedule comparisons."""
    tz = resolve_tz(timezone)
    if tz is None:
        return datetime.now()
    aware = datetime.now(tz)
    return aware.replace(tzinfo=None)

def parse_iso_datetime(
    raw: Optional[str],
    timezone: Optional[str] = None,
) -> Optional[datetime]:
    """Parse ISO datetime into naive wall-clock in the target schedule timezone."""
    if not raw:
        return None
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None

    if dt.tzinfo is None:
        return dt

    target = resolve_tz(timezone)
    if target is None:
        # Fall back to system local wall-clock
        return dt.astimezone().replace(tzinfo=None)

    # Convert aware → target TZ wall-clock naive
    try:
        return dt.astimezone(target).replace(tzinfo=None)
    except Exception:
        return dt.astimezone().replace(tzinfo=None)
