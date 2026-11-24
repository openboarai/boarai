"""
Monitoring API for BoarAi — token & cache statistics.

Endpoints:
    GET /api/monitoring/stats  —  aggregated token/cache statistics

Data is sourced from the global append-only usage ledger
(``<data_dir>/logs/usage.jsonl``) so token history survives session
deletion; legacy pre-ledger entries are merged from Session.cache_requests.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List

from aiohttp import web

from boarai.core.session import get_session_manager
from boarai.core.session.usage_ledger import get_usage_ledger

logger = logging.getLogger(__name__)

# Approximate cost per token (Anthropic Claude 3.5 Sonnet pricing)
# Cache write: $3.75 / 1M tokens, Cache read: $0.30 / 1M tokens
CACHE_WRITE_COST_PER_TOKEN = 3.75 / 1_000_000
CACHE_READ_COST_PER_TOKEN = 0.30 / 1_000_000


def _parse_ts_local(ts_raw: Any):
    """Parse an ISO timestamp to an AWARE datetime in server-local time."""
    if not ts_raw or not isinstance(ts_raw, str):
        return None
    try:
        ts = datetime.fromisoformat(ts_raw)
    except (ValueError, TypeError):
        return None
    if ts.tzinfo is None:
        # Legacy naive stamp: written by datetime.now() → already local.
        return ts.astimezone()
    return ts.astimezone()


# Short-TTL stats cache: aggregating reads the whole JSONL ledger on every
# call, and the WebUI stats page polls. A few seconds of staleness is fine
# for a stats view and keeps repeated range switches cheap.
_STATS_CACHE: Dict[str, Any] = {}
_STATS_CACHE_TTL = 3.0

# Ledger read cache keyed by file fingerprint (mtime+size across all dated
# ledger files) so back-to-back aggregations skip re-reading/parsing them.
_LEDGER_ENTRIES_CACHE: Dict[str, Any] = {"fp": None, "entries": []}


def invalidate_stats_cache() -> None:
    """Drop all cached stats/ledger data (called after writes/clears)."""
    _STATS_CACHE.clear()
    _LEDGER_ENTRIES_CACHE["fp"] = None
    _LEDGER_ENTRIES_CACHE["entries"] = []


async def _compute_stats(range_key: str = "all") -> Dict[str, Any]:
    """Aggregate cache/token statistics from the global usage ledger."""
    # Short-TTL whole-result cache (keyed by range window).
    import time as _time

    cached = _STATS_CACHE.get(range_key)
    if cached and (_time.monotonic() - cached[0]) < _STATS_CACHE_TTL:
        return cached[1]

    sm = get_session_manager()
    session_ids = list(sm._sessions.keys())

    total_input = 0
    total_completion = 0
    total_cache_read = 0
    total_cache_created = 0
    total_compressions = 0
    today_input = 0
    today_cache_read = 0

    agent_stats: Dict[str, Dict] = {}
    all_requests: List[Dict] = []
    # Local-time windows: "today" must mean the USER's today, not UTC's.
    today_start = datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    month_start = today_start.replace(day=1)
    today_active_sessions = set()

    # Range filter for breakdowns (per-model/provider/agent/session)
    from datetime import timedelta
    if range_key == "today":
        cutoff = today_start
    elif range_key == "7d":
        cutoff = today_start - timedelta(days=6)
    elif range_key == "30d":
        cutoff = today_start - timedelta(days=29)
    else:
        cutoff = None

    # Extended aggregations: today / month / per-model / per-provider /
    # per-session / 30-day & monthly trends & today-hourly trend
    month_input = 0
    month_cache_read = 0
    today_completion = 0
    today_requests = 0
    model_stats: Dict[str, Dict] = {}
    session_stats: Dict[str, Dict] = {}
    daily: Dict[str, int] = {}
    daily_read: Dict[str, int] = {}
    daily_created: Dict[str, int] = {}
    daily_requests: Dict[str, int] = {}
    monthly: Dict[str, Dict] = {}
    hourly: List[int] = [0] * 24
    hourly_read: List[int] = [0] * 24
    # Range-window aggregates (drive the KPI cards + hit-rate trend)
    r_input = 0
    r_completion = 0
    r_cache_read = 0
    r_cache_created = 0
    r_requests = 0

    # model -> provider map (best effort; ledger entries carry model only)
    model_provider: Dict[str, str] = {}
    try:
        from boarai.core.llm import get_llm_service
        for m in get_llm_service().list_models():
            prov = m.get("provider", "")
            name = m.get("name", "")
            if name and prov:
                model_provider[name] = prov
            short = m.get("short_name", "")
            if short and prov and short not in model_provider:
                model_provider[short] = prov
    except Exception:  # noqa: BLE001
        pass

    ledger = get_usage_ledger()
    # Idempotent: copies pre-ledger session entries (no run_id) into the
    # ledger so token history survives deletion of old sessions too.
    await ledger.migrate_legacy_entries(await sm.legacy_usage_entries())
    # Ledger read with file-fingerprint caching (the JSONL files can grow
    # large; re-parsing them on every stats poll is wasted work when nothing
    # changed).
    fp = ledger.fingerprint()
    if fp is not None and fp == _LEDGER_ENTRIES_CACHE.get("fp"):
        entries = list(_LEDGER_ENTRIES_CACHE["entries"])
    else:
        entries = await ledger.read_all()
        _LEDGER_ENTRIES_CACHE["fp"] = fp
        _LEDGER_ENTRIES_CACHE["entries"] = list(entries)
    ledger_ids = {e.get("run_id") for e in entries if e.get("run_id")}
    # Merge session entries not yet represented in the ledger. Two cases:
    # - legacy entries (no run_id) that migration has not copied yet
    # - entries whose ledger append FAILED (record() never raises) — the
    #   run_id check used to skip exactly these, silently losing the usage
    #   forever. Now only run_ids CONFIRMED present in the ledger skip.
    for sid in session_ids:
        session = sm._sessions.get(sid)
        if not session:
            continue
        total_compressions += session.compression_count
        for idx, req in enumerate(session.cache_requests):
            rid = req.get("run_id")
            if rid:
                if rid in ledger_ids:
                    continue
            elif f"legacy:{sid}:{idx}" in ledger_ids:
                continue
            entries.append({
                "ts": req.get("ts", ""),
                "run_id": rid or "",
                "session_id": sid,
                "session_title": getattr(session, "title", "") or "",
                "agent_name": session.metadata.get("agent_name", "default"),
                "model": req.get("model", "unknown"),
                "input_tokens": req.get("input_tokens", 0),
                "completion_tokens": req.get("completion_tokens", 0),
                "cache_read_tokens": req.get("cache_read_tokens", 0),
                "cache_creation_tokens": req.get("cache_creation_tokens", 0),
            })

    for req in entries:
        inp = req.get("input_tokens", 0)
        cr = req.get("cache_read_tokens", 0)
        cc = req.get("cache_creation_tokens", 0)
        comp = req.get("completion_tokens", 0)
        model = req.get("model", "unknown")
        sid = req.get("session_id", "")
        ts_raw = req.get("ts", "")

        total_input += inp
        total_completion += comp
        total_cache_read += cr
        total_cache_created += cc

        # Parse timestamp once (drives today/month/hourly + range filter).
        # Always converted to LOCAL aware — see _parse_ts_local.
        ts = _parse_ts_local(ts_raw)
        in_range = cutoff is None or (ts is not None and ts >= cutoff)
        if in_range:
            r_input += inp
            r_completion += comp
            r_cache_read += cr
            r_cache_created += cc
            r_requests += 1

        # Per-agent tracking (ledger carries the producing agent)
        agent = req.get("agent_name") or "default"
        if in_range:
            if agent not in agent_stats:
                agent_stats[agent] = {
                    "requests": 0, "input": 0, "completion": 0,
                    "cache_read": 0,
                }
            agent_stats[agent]["requests"] += 1
            agent_stats[agent]["input"] += inp
            agent_stats[agent]["completion"] += comp
            agent_stats[agent]["cache_read"] += cr

        # Per-model / per-session (range-filtered) + per-day / per-month
        # trends (always full-window)
        if in_range:
            provider = model_provider.get(model) or (
                model.split("/", 1)[0] if "/" in model else "unknown"
            )
            mst = model_stats.setdefault(model, {
                "provider": provider, "requests": 0, "input": 0,
                "completion": 0, "cache_read": 0,
            })
            mst["requests"] += 1
            mst["input"] += inp
            mst["completion"] += comp
            mst["cache_read"] += cr
            if sid:
                sst = session_stats.setdefault(sid, {
                    "title": "", "requests": 0, "input": 0, "completion": 0,
                    "cache_read": 0,
                })
                # Prefer the CURRENT session title (renames don't reach the
                # append-only ledger snapshot).
                live = sm._sessions.get(sid)
                live_title = getattr(live, "title", "") if live else ""
                if live_title:
                    sst["title"] = live_title
                elif not sst["title"]:
                    sst["title"] = req.get("session_title", "") or ""
                sst["requests"] += 1
                sst["input"] += inp
                sst["completion"] += comp
                sst["cache_read"] += cr
        # Day/month buckets come from the parsed LOCAL timestamp — slicing
        # the raw string would bucket by UTC date for new aware entries.
        if ts is not None:
            day_key = ts.strftime("%Y-%m-%d")
            daily[day_key] = daily.get(day_key, 0) + inp
            daily_read[day_key] = daily_read.get(day_key, 0) + cr
            daily_created[day_key] = daily_created.get(day_key, 0) + cc
            daily_requests[day_key] = daily_requests.get(day_key, 0) + 1
            month_key = ts.strftime("%Y-%m")
            mo = monthly.setdefault(month_key, {"input": 0, "requests": 0})
            mo["input"] += inp
            mo["requests"] += 1

        # Today / month / hourly tracking
        if ts is not None:
            if ts >= today_start:
                today_input += inp
                today_cache_read += cr
                today_completion += comp
                today_requests += 1
                hourly[ts.hour] += inp
                hourly_read[ts.hour] += cr
                if sid:
                    today_active_sessions.add(sid)
            if ts >= month_start:
                month_input += inp
                month_cache_read += cr

        all_requests.append({
            "ts": req.get("ts", ""),
            "day": ts.strftime("%Y-%m-%d") if ts is not None else "",
            "model": model,
            "input_tokens": inp,
            "completion_tokens": req.get("completion_tokens", 0),
            "cache_read_tokens": cr,
        })

    # Compute derived metrics
    hit_rate = (
        round((total_cache_read / total_input) * 100, 1)
        if total_input > 0 else 0.0
    )
    today_hit_rate = (
        round((today_cache_read / today_input) * 100, 1)
        if today_input > 0 else 0.0
    )

    # Cost savings (avoid double-counting: total_cache_created is already in total_input)
    cost_without_cache = total_input * CACHE_WRITE_COST_PER_TOKEN
    cost_with_cache = (
        total_cache_created * CACHE_WRITE_COST_PER_TOKEN
        + max(0, total_cache_read - total_cache_created) * CACHE_READ_COST_PER_TOKEN
    )
    savings = round(cost_without_cache - cost_with_cache, 4)

    # Per-model & per-provider breakdowns (sorted by input tokens desc)
    per_model = sorted(
        (
            {
                "model": name,
                "provider": st["provider"],
                "requests": st["requests"],
                "input": st["input"],
                "completion": st["completion"],
                "cache_read": st["cache_read"],
                "hit_rate": (
                    round(st["cache_read"] / st["input"] * 100, 1)
                    if st["input"] > 0 else 0.0
                ),
            }
            for name, st in model_stats.items()
        ),
        key=lambda x: x["input"],
        reverse=True,
    )
    prov_agg: Dict[str, Dict] = {}
    for item in per_model:
        p = prov_agg.setdefault(item["provider"], {
            "requests": 0, "input": 0, "completion": 0, "cache_read": 0,
        })
        p["requests"] += item["requests"]
        p["input"] += item["input"]
        p["completion"] += item["completion"]
        p["cache_read"] += item["cache_read"]
    per_provider = sorted(
        (
            {
                "provider": name,
                "requests": st["requests"],
                "input": st["input"],
                "completion": st["completion"],
                "cache_read": st["cache_read"],
                "hit_rate": (
                    round(st["cache_read"] / st["input"] * 100, 1)
                    if st["input"] > 0 else 0.0
                ),
            }
            for name, st in prov_agg.items()
        ),
        key=lambda x: x["input"],
        reverse=True,
    )

    # Per-session breakdown (range-filtered, sorted by input desc)
    per_session = sorted(
        (
            {
                "session_id": sid,
                "title": st["title"],
                "requests": st["requests"],
                "input": st["input"],
                "completion": st["completion"],
                "cache_read": st["cache_read"],
                "hit_rate": (
                    round(st["cache_read"] / st["input"] * 100, 1)
                    if st["input"] > 0 else 0.0
                ),
            }
            for sid, st in session_stats.items()
        ),
        key=lambda x: x["input"],
        reverse=True,
    )

    # 30-day daily trend & last-6-months trend (local-time anchored)
    now = datetime.now().astimezone()
    trend_30d = [
        daily.get((now - timedelta(days=i)).strftime("%Y-%m-%d"), 0)
        for i in range(29, -1, -1)
    ]
    monthly_list = []
    for i in range(5, -1, -1):
        mm = now.month - i
        yy = now.year
        while mm <= 0:
            mm += 12
            yy -= 1
        key = f"{yy:04d}-{mm:02d}"
        st = monthly.get(key, {"input": 0, "requests": 0})
        monthly_list.append({
            "key": key,
            "label": key,
            "input": st["input"],
            "requests": st["requests"],
        })

    # Range-window derived metrics (KPI cards follow the selected range)
    range_hit_rate = (
        round((r_cache_read / r_input) * 100, 1) if r_input > 0 else 0.0
    )
    range_cost_without = r_input * CACHE_WRITE_COST_PER_TOKEN
    range_cost_with = (
        r_cache_created * CACHE_WRITE_COST_PER_TOKEN
        + max(0, r_cache_read - r_cache_created) * CACHE_READ_COST_PER_TOKEN
    )
    range_savings = round(range_cost_without - range_cost_with, 4)

    # 30-day daily hit-rate trend & today hourly hit-rate trend
    cache_trend_30d = []
    for i in range(29, -1, -1):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        din = daily.get(day, 0)
        drd = daily_read.get(day, 0)
        cache_trend_30d.append(round((drd / din * 100) if din > 0 else 0, 1))
    trend_today_rate = [
        round((hourly_read[h] / hourly[h] * 100) if hourly[h] > 0 else 0, 1)
        for h in range(24)
    ]

    # Daily activity heatmap series (last 8 weeks, calendar-anchored)
    daily_activity = []
    for i in range(55, -1, -1):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        d_in = daily.get(day, 0)
        d_read = daily_read.get(day, 0)
        d_created = daily_created.get(day, 0)
        cost = (
            d_created * CACHE_WRITE_COST_PER_TOKEN
            + max(0, d_read - d_created) * CACHE_READ_COST_PER_TOKEN
        )
        daily_activity.append({
            "date": day,
            "input": d_in,
            "requests": daily_requests.get(day, 0),
            "cost_usd": round(cost, 4),
        })

    # Per-agent breakdown (sorted by input tokens desc)
    per_agent = []
    for agent, st in sorted(
        agent_stats.items(), key=lambda kv: kv[1]["input"], reverse=True
    ):
        agent_hit = (
            round((st["cache_read"] / st["input"]) * 100, 1)
            if st["input"] > 0 else 0.0
        )
        per_agent.append({
            "agent": agent,
            "requests": st["requests"],
            "input": st["input"],
            "completion": st["completion"],
            "cache_read": st["cache_read"],
            "avg_hit_rate": agent_hit,
            "hit_rate": agent_hit,
        })

    # Recent requests (last 20)
    recent = sorted(all_requests, key=lambda r: r.get("ts", ""), reverse=True)[:20]

    month_hit_rate = (
        round((month_cache_read / month_input) * 100, 1)
        if month_input > 0 else 0.0
    )

    result = {
        "tokens": {
            "total": total_input,
            "today": today_input,
            "hit_rate": hit_rate,
            "today_hit_rate": today_hit_rate,
            "trend": _compute_trend(all_requests),
            # Extended metrics (additive; old clients ignore them)
            "month": month_input,
            "month_hit_rate": month_hit_rate,
            "completion_total": total_completion,
            "today_completion": today_completion,
            "today_requests": today_requests,
            "requests": len(entries),
        },
        "trend_30d": trend_30d,
        "trend_today": hourly,
        "trend_today_rate": trend_today_rate,
        "cache_trend_30d": cache_trend_30d,
        "range": {
            "key": range_key,
            "input": r_input,
            "completion": r_completion,
            "cache_read": r_cache_read,
            "cache_created": r_cache_created,
            "requests": r_requests,
            "hit_rate": range_hit_rate,
            "savings_usd": range_savings,
        },
        "monthly": monthly_list,
        "per_model": per_model,
        "per_provider": per_provider,
        "per_session": per_session,
        "daily_activity": daily_activity,
        "cache": {
            "total_read": total_cache_read,
            "total_created": total_cache_created,
            "hit_rate": hit_rate,
            "trend": _compute_cache_trend(all_requests),
        },
        "sessions": {
            "total": len(session_ids),
            "active": len([s for s in session_ids
                          if sm._sessions.get(s) and not sm._sessions[s].archived]),
            "today": len(today_active_sessions),
        },
        "compression": {
            "total_count": total_compressions,
        },
        "savings": {
            "estimated_usd": savings,
            "tokens_saved": total_cache_read,
        },
        "per_agent": per_agent,
        "recent_requests": recent,
    }
    import time as _time

    _STATS_CACHE[range_key] = (_time.monotonic(), result)
    return result


def _compute_trend(requests: List[Dict]) -> List[int]:
    """Compute token usage trend (last 7 LOCAL days, daily totals)."""
    from collections import defaultdict
    daily = defaultdict(int)
    for r in requests:
        try:
            # Pre-computed local day key (falls back to string slice for
            # legacy callers that only pass raw timestamps).
            day = r.get("day") or r.get("ts", "")[:10]
            if day:
                daily[day] += r.get("input_tokens", 0)
        except Exception:
            pass

    from datetime import timedelta
    today = datetime.now().astimezone()
    trend = []
    for i in range(6, -1, -1):
        day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        trend.append(daily.get(day, 0))
    return trend


def _compute_cache_trend(requests: List[Dict]) -> List[float]:
    """Compute cache hit rate trend (last 7 LOCAL daily averages)."""
    from collections import defaultdict
    daily_input = defaultdict(int)
    daily_read = defaultdict(int)
    for r in requests:
        try:
            day = r.get("day") or r.get("ts", "")[:10]
            if day:
                daily_input[day] += r.get("input_tokens", 0)
                daily_read[day] += r.get("cache_read_tokens", 0)
        except Exception:
            pass

    from datetime import timedelta
    today = datetime.now().astimezone()
    trend = []
    for i in range(6, -1, -1):
        day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        inp = daily_input.get(day, 0)
        rd = daily_read.get(day, 0)
        trend.append(round((rd / inp * 100) if inp > 0 else 0, 1))
    return trend


# ==================== API Handlers ====================


async def _clear_usage_data() -> Dict[str, Any]:
    """Wipe the global usage ledger and per-session cache-request history."""
    ledger = get_usage_ledger()
    await ledger.clear()
    invalidate_stats_cache()

    sm = get_session_manager()
    cleared = 0
    try:
        ids = await sm._storage.list_sessions()
    except Exception:  # noqa: BLE001
        ids = list(sm._sessions.keys())
    for sid in ids:
        try:
            session = sm._sessions.get(sid)
            loaded = session is not None
            if not session:
                session = await sm._storage.load_session(sid)
            if not session or not getattr(session, "cache_requests", None):
                continue
            session.cache_requests = []
            cleared += 1
            if loaded:
                await sm.save_session(sid)
            else:
                await sm._storage.save_session(session)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to clear usage for session %s: %s", sid, e)
    return {"sessions_cleared": cleared}


async def handle_monitoring_clear(request: web.Request) -> web.Response:
    """POST /api/monitoring/stats/clear — wipe token/cache usage history."""
    try:
        result = await _clear_usage_data()
        return web.json_response({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Failed to clear monitoring stats: {e}")
        return web.json_response(
            {"success": False, "error": {"message": str(e)}},
            status=500,
        )


async def handle_monitoring_stats(request: web.Request) -> web.Response:
    """GET /api/monitoring/stats — aggregated token & cache statistics."""
    try:
        range_key = request.query.get("range", "all")
        if range_key not in ("today", "7d", "30d", "all"):
            range_key = "all"
        stats = await _compute_stats(range_key)
        return web.json_response({"success": True, "data": stats})
    except Exception as e:
        logger.error(f"Failed to compute monitoring stats: {e}")
        return web.json_response(
            {"success": False, "error": {"message": str(e)}},
            status=500,
        )
