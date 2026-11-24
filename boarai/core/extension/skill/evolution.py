"""Skill self-evolution: decide WHETHER to distil, then drive one authoring run.

The decision is the only thing this module owns. What a skill should contain is
the authoring model's call, informed by three layers of documentation:

1. ``_CREATE_INSTRUCTION`` below — who you are in this turn, whether to write
   at all, and which existing skill to prefer over a new one;
2. ``authoring.SKILL_AUTHORING_STANDARDS`` — how to write one well, with worked
   good/bad pairs;
3. ``boar/prompts/extension/skills.md`` — the full spec, read on demand with
   file_read (never injected, so it costs no prefix tokens).

There is no review pass, no staging area and no repair run. Quality is handled
at the write gate (``core.extension.skill.manage``): its refusals and warnings
come back to the model inside the same authoring run, so it fixes its own
output while the evidence is still in context. A separate reviewer would only
add an LLM round-trip with less information than the author already has.

Why the trigger is shaped the way it is
---------------------------------------
The previous gate fired on LLM turn count alone (``turns >= 8``). Turn count
measures how HARD one task was, not whether that CLASS of task will recur, so a
single "read this blocked link for me" run — eleven turns of retrying a 403 —
distilled a skill nobody asked for. Difficulty is now only a *floor*. Firing
additionally requires one signal that speaks to recurrence or intent:

* the user asked for it (strong intent wording),
* the same task shape showed up in another session (recurrence ledger),
* or the run hit real pitfalls and then worked (hard-won knowledge worth
  carrying forward).

Mutual exclusion with reflection is kept: a run that USED a skill may improve
it, but never creates a new one — distilling on top of an existing skill is how
a library fills up with near-duplicates.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from boarai.core.runtime import spawn

from . import evidence
from .authoring import SKILL_AUTHORING_STANDARDS

logger = logging.getLogger(__name__)

# --- Defaults (overridable via .boarai/config run.skill_evolution) ------------

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    # Floor on run size. Necessary, never sufficient — see the module docstring.
    "auto_create_threshold": 12,     # LLM turns in this run
    "min_tool_calls": 6,
    # Signals: at least one must hold.
    "min_tool_failures": 3,          # hard-won: pitfalls actually hit
    "recurrence_min_sessions": 2,    # same task shape in N DISTINCT sessions
    "weak_intent_min_tool_calls": 3,  # "remember this" needs real work behind it
    # Throttles.
    "cooldown_seconds": 600,         # between two attempts in one session
    "daily_create_cap": 3,           # global per day
    "authoring_timeout": 600,
    "authoring_context_chars": 12000,  # trajectory budget handed to the author
    "reflect_enabled": True,
    "signal_ledger_max": 300,
}

# In-process state: a restart only resets the cooldown and the daily counter.
_last_attempt: Dict[str, float] = {}
_daily_counter: Dict[str, Any] = {"date": "", "count": 0}

# Presence of one of these in a run means "a skill was used".
SKILL_TOOL_NAMES = ("skill_view", "skill_read_resource")

# --- Intent wording ----------------------------------------------------------
#
# Strong: the user asked to codify a procedure. Fires on its own, and also
# lifts the size floor — "from now on do X" after a three-turn task is a better
# signal than eleven turns of nobody asking for anything.
_STRONG_INTENT_RE = re.compile(
    r"(做成(?:一个)?技能|存成技能|保存为技能|写成技能|固化(?:下来|成技能)"
    r"|以后(?:都|每次|一律|遇到|再)[^。！？\n]{0,16}(?:就|这么|这样|按|用|走)"
    r"|下次(?:遇到|再|碰到)[^。！？\n]{0,16}(?:就|直接|这么|这样)"
    r"|每次都(?:这么|这样|按)"
    r"|make (?:this|that|it) a skill"
    r"|save (?:this|that) as a skill"
    r"|turn this into a skill"
    r"|from now on\b"
    r"|(?:always|every time) do it (?:this|that) way)",
    re.IGNORECASE,
)

# Weak: a correction or a "remember this". These usually belong to MEMORY, not
# to a skill ("remember I meant file A"), so they only count alongside real
# tool work and never lift the size floor on their own.
_WEAK_INTENT_RE = re.compile(
    r"(记住|记一下|别再|不要再|别老是|我说过|下次(?:就|记得|别)"
    r"|remember (?:this|that|to)\b"
    r"|(?:always|never) (?:do|use|write|say)\b"
    r"|stop (?:doing|using)\b)",
    re.IGNORECASE,
)

# --- Signal ledger -----------------------------------------------------------
#
# A tiny recency window of task shapes, so "this class of work keeps coming
# back" is answerable without an LLM. Lives outside the skills root: the loader
# scans every child directory there.
_SIGNALS_REL = "state/skill_signals.json"

# Signature vocabulary filter. Function words carry no task identity, and a
# signature built from them would merge every "can you look at this" together.
_STOPWORDS = frozenset("""
的 了 是 在 和 与 及 或 我 你 他 她 它 我们 你们 他们 这 那 这个 那个 这些 那些
就 都 也 还 又 再 请 帮 帮我 看看 看一下 一下 一个 有些 什么 怎么 如何 为什么
可以 能不能 能否 吗 呢 吧 啊 呀 把 被 让 给 对 为 以 而 但 如果 因为 所以 然后
现在 今天 昨天 这里 那里 上面 下面 里面 外面 已经 正在 需要 想要 觉得 知道
a an the and or of to in on for with is are was were be been being do does did
this that these those it its as at by from into onto over under again then than
there here where when why how what which who whom whose all any both each few
more most other some such no nor not only own same so too very can could will
would should shall may might must please help me my your our their i you we they
he she him her us them now just also about using use used get got go going want
need like think know see look find give take make made one two three first
""".split())


def _config() -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    try:
        from boarai.core.session.run.route import load_run_config

        raw = load_run_config().get("skill_evolution")
        if isinstance(raw, dict):
            cfg.update(raw)
    except Exception:  # noqa: BLE001 — defaults are always usable
        pass
    return cfg


# --- Run-shape readers (pure, no IO) -----------------------------------------

def extract_viewed_skills(messages: List[Any]) -> List[str]:
    """Skills viewed via skill_view in this run's history (deduped, in order)."""
    names: List[str] = []
    for m in messages:
        for tc in getattr(m, "tool_calls", None) or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            if fn.get("name", "") != "skill_view":
                continue
            args = fn.get("arguments", "")
            try:
                args = json.loads(args) if isinstance(args, str) else args
            except json.JSONDecodeError:
                args = {}
            name = str((args or {}).get("name") or "").strip()
            if name and name not in names:
                names.append(name)
    return names


def skill_used_this_run(messages: List[Any]) -> bool:
    for m in messages:
        for tc in getattr(m, "tool_calls", None) or []:
            if isinstance(tc, dict) and tc.get("function", {}).get("name", "") in SKILL_TOOL_NAMES:
                return True
    return False


def _last_assistant_text(messages: List[Any]) -> str:
    """Final non-empty assistant text of the run (stripped), or ''."""
    for m in reversed(messages):
        if getattr(m, "role", "") != "assistant":
            continue
        content = getattr(m, "content", "") or ""
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def _turn_unfinished(last_assistant: str) -> bool:
    """A reply ending in a question mark is an in-flight conversation."""
    return last_assistant.endswith(("?", "？"))


# Deferred-to-user wording. Conservative zh+en set; the structural pending
# approval check below is authoritative and this regex is the second net.
_AWAITING_USER_RE = re.compile(
    r"(需要你?(来)?(决定|拍板|确认|选择|批准|同意)"
    r"|等你(决定|拍板|确认|回复|选择|批准)"
    r"|由你(决定|拍板|确认|选择)"
    r"|请你?(决定|拍板|确认|选择|批准)"
    r"|请确认是否"
    r"|待你(决定|拍板|确认)"
    r"|听你(安排|决定)"
    r"|awaiting your (?:decision|confirmation|approval|reply)"
    r"|need(?:s)? your (?:decision|confirmation|approval|input)"
    r"|your call\b"
    r"|you decide\b"
    r"|let me know (?:if|whether)\b)",
    re.IGNORECASE,
)


def _awaiting_user_decision(session_id: str, last_assistant: str) -> bool:
    """Whether the turn handed the decision back to the user."""
    if session_id:
        try:
            from boarai.core.session import get_run_manager

            gate = getattr(get_run_manager(), "_confirm", None)
            if gate is not None:
                for entry in gate.list_pending():
                    if entry.get("session_id") == session_id:
                        return True
        except Exception:  # noqa: BLE001 — best-effort structural check
            pass
    return bool(last_assistant) and bool(_AWAITING_USER_RE.search(last_assistant))


# --- Task signature + recurrence ledger --------------------------------------

# Single Chinese characters that carry no task identity. Stripped before the
# CJK run is reduced to a character set, so "帮我看看这个知乎链接的内容" and
# "看看这个 知乎 链接 的内容" land on the same signature.
_CJK_STOPCHARS = frozenset(
    "的了是在和我你他她它们这那就都也还再请帮把被让给对为而以但吗呢吧啊呀"
    "看上下个之其此该能可以要会得着过很太更最只每个些什么怎么如何为何"
    "现在今天昨天已经正在需要想要觉得知道一二三四五六十百千万"
)


def _content_words(text: str, limit: int = 8) -> List[str]:
    """Content tokens, sorted so word order never changes the hash."""
    lowered = str(text or "").lower()
    kept: List[str] = []

    for tok in re.findall(r"[a-z][a-z0-9_]+", lowered):
        if tok not in _STOPWORDS and tok not in kept:
            kept.append(tok)

    for run in re.findall(r"[\u4e00-\u9fff]+", lowered):
        for ch in run:
            if ch not in _CJK_STOPCHARS and ch not in kept:
                kept.append(ch)

    return sorted(kept)[:limit]


def task_signature(user_text: str, tool_names: List[str]) -> str:
    """Heuristic identity of a task CLASS: content words + the tool set."""
    payload = " ".join(_content_words(user_text)) + "|" + ",".join(sorted(set(tool_names)))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _ledger_path() -> Path:
    from boarai.core.runtime.data_paths import resolve_data_path

    return resolve_data_path(_SIGNALS_REL)


def _load_ledger() -> Dict[str, Any]:
    try:
        data = json.loads(_ledger_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    sigs = data.get("sigs") if isinstance(data, dict) else None
    return {k: v for k, v in sigs.items() if isinstance(v, dict)} if isinstance(sigs, dict) else {}


def _save_ledger(sigs: Dict[str, Any], max_entries: int) -> None:
    if len(sigs) > max(1, int(max_entries)):
        # The ledger is a recency window, not a log: drop the least recent.
        # Sorted numerically — an ISO string would order "9" after "10".
        stale = sorted(sigs, key=lambda k: float(sigs[k].get("last") or 0.0))
        for key in stale[: len(sigs) - max(1, int(max_entries))]:
            sigs.pop(key, None)
    path = _ledger_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"sigs": sigs}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(path)
    except OSError as exc:
        logger.debug("Skill signal ledger save failed: %s", exc)


def record_signal(
    user_text: str, tool_names: List[str], session_id: str, max_entries: int
) -> int:
    """Note this run's task shape; return the distinct-session count after it."""
    signature = task_signature(user_text, tool_names)
    sigs = _load_ledger()
    entry = sigs.get(signature) or {}
    sessions = [s for s in (entry.get("sessions") or []) if isinstance(s, str)]
    if session_id and session_id not in sessions:
        sessions.append(session_id)
    sigs[signature] = {
        "n": int(entry.get("n") or 0) + 1,
        "sessions": sessions[-8:],
        # Epoch seconds: the cap in _save_ledger sorts on this numerically.
        "last": time.time(),
    }
    _save_ledger(sigs, max_entries)
    return len(sigs[signature]["sessions"])


# --- Throttles ---------------------------------------------------------------

def _under_cooldown(session_id: str, cooldown: float) -> bool:
    return (time.time() - _last_attempt.get(session_id, 0.0)) < cooldown


def _daily_exceeded(cap: int) -> bool:
    today = time.strftime("%Y-%m-%d")
    if _daily_counter["date"] != today:
        _daily_counter["date"] = today
        _daily_counter["count"] = 0
    return _daily_counter["count"] >= cap


def _bump_daily() -> None:
    today = time.strftime("%Y-%m-%d")
    if _daily_counter["date"] != today:
        _daily_counter["date"] = today
        _daily_counter["count"] = 0
    _daily_counter["count"] += 1


# --- Layer 1: the trigger instruction ----------------------------------------

def _existing_skills_brief(store: Any, limit: int = 30) -> str:
    try:
        skills = store.list_enabled()
    except Exception:  # noqa: BLE001
        return "(unknown)"
    parts = [f"{s.name} — {s.description}" for s in skills[:limit]]
    return "\n".join(parts) or "(none)"


_CREATE_INSTRUCTION = """\
[BoarAi_System] skill-authoring

This is an automated post-task turn (kind=skill_authoring), not a user reply.
The user has NOT responded to anything and is not waiting on you: do not read
this as their answer, their approval, or permission to continue earlier work.
When you are done — including deciding to do nothing — stop and yield.

The run you are distilled from is reproduced below as a trajectory: the task,
every tool call, and what came back, with the failures lifted to the top.

## Decide first, write second

Prefer the EARLIEST option that fits:

1. A skill you loaded in that run, which covers this learning → update it.
2. An existing class-level skill that covers it (see the list below) → update
   it: add a subsection, a pitfall, or broaden its trigger.
3. A new class-level skill → only when nothing above covers the territory.

Updating beats creating. A library of narrow one-run skills is the failure
shape, not the goal.

## Do NOT capture

These become persistent self-imposed constraints that bite later:

- Environment failures: missing binary, fresh-install error, "command not
  found", unconfigured credentials, a path that moved. The user can fix those;
  they are not durable procedure.
- Negative claims about tools or features ("X does not work", "cannot use Y").
  They harden into refusals you will cite for months after the real problem was
  fixed. If a tool failed because of setup state, capture the FIX under an
  existing setup or troubleshooting skill instead.
- Transient errors that a retry solved. The lesson is the retry pattern, not
  the original failure.
- One-off narratives. "Summarize today's news" or "analyze this PR" is not a
  class of work.
- Unresolved failure. If the run ended without a method that actually worked,
  there is no skill here. Writing up the dead ends as a "recommended workflow"
  teaches every future run to repeat them.

If nothing qualifies, reply exactly "No skill warranted." and stop. That is a
correct outcome, not a failure.

## How to write

`skill_manage` is the only write path — never file_write for a skill.

    skill_manage(action="create", name=..., description=..., body=...,
                 files=[{"path": "scripts/x.py", "content": "..."}])

Ship every script or reference the body names in the SAME call. The result
carries `warnings` (advisory — the write already succeeded) and `index_line`
(how your description will actually appear in the prompt index). Read them, fix
what matters, and do not loop on cosmetics.

Existing skills (do not duplicate; prefer updating one):
__EXISTING__

__STANDARDS__
"""

_IMPROVE_INSTRUCTION = """\
[BoarAi_System] skill-authoring

This is an automated skill-improvement turn (kind=skill_authoring), not a user
reply. The user has NOT responded to anything and is not waiting on you: do not
read this as their answer, their approval, or permission to continue earlier
work. When you are done — including deciding to change nothing — stop and yield.

The skill "{name}" has been used {uses} time(s). The run you are looking at
reproduces one of those uses as a trajectory: the task, every tool call, and
what came back, with the failures lifted to the top.

Read that trajectory for friction you hit WHILE APPLYING the skill:

- a step you had to reinterpret because it was ambiguous or wrong;
- an edge case or pitfall you ran into that the skill does not mention;
- a command, path, flag or endpoint in the skill that did not work as written;
- steps that could be collapsed or reordered.

If you find a CONCRETE improvement, apply it with:

    skill_manage(action="update", name="{name}", description=..., body=...)

Call skill_view("{name}") first — an unattended run is refused otherwise, and
you should be editing what is there, not what you remember. Omit a field to
keep its current value, so patching one section never means re-typing the rest.
Fix the sentence that misled; do not append "UPDATE: actually ..." underneath
it. Tighten the description only if it no longer matches what the skill does.

Never degrade a working skill to rephrase it. If it served well as written,
reply exactly "No improvement needed." and stop.

__STANDARDS__
"""


# --- Trace card --------------------------------------------------------------
#
# One ``[BoarAi_System]`` message in the origin session covers three needs at
# once: visible in the next turn's context (dedupe), rendered in the WebUI, and
# auditable in the journal.

_CARD_TEXT = {
    "created": "已自动提炼技能 {name}",
    "improved": "已自动改进技能 {name}",
    "deleted": "已删除技能 {name}",
}


async def _persist_skill_card(
    session: Any,
    session_manager: Any,
    *,
    kind: str,
    name: str,
    detail: str = "",
    authoring_session_id: str = "",
) -> None:
    text = _CARD_TEXT.get(kind, "技能 {name}: {kind}").format(name=name, kind=kind)
    if detail:
        text = f"{text}：{detail}"
    card: Dict[str, Any] = {"kind": kind, "name": name, "detail": detail, "ts": time.time()}
    if authoring_session_id:
        # Lineage link: the WebUI can open the hidden background session that
        # produced this change.
        card["authoring_session_id"] = authoring_session_id
    try:
        session.add_message("user", f"[BoarAi_System] {text}", metadata={"skill_card": card})
        await session_manager.save_session(session.session_id)
    except Exception as exc:  # noqa: BLE001 — bookkeeping only
        logger.debug("Skill card persist skipped: %s", exc)


async def _publish(kind: str, payload: Dict[str, Any]) -> None:
    try:
        from boarai.core.event import publish_event

        await publish_event(f"skill.{kind}", payload)
    except Exception as exc:  # noqa: BLE001
        logger.debug("skill.%s publish skipped: %s", kind, exc)


# --- Completion fingerprint --------------------------------------------------

def _mark_skill(skill: Any, session: Any, run_id: str) -> None:
    """Record a distilled skill as a complete artifact of this run."""
    try:
        from boarai.core.runtime.artifact_mark import (
            config_fingerprint,
            fingerprint_path,
            write_mark,
        )

        path = getattr(skill, "path", None)
        if not path:
            return
        skill_dir = str(Path(path).parent)
        write_mark(
            "skill",
            skill_dir,
            run_id=run_id or getattr(session, "session_id", ""),
            config_fp=config_fingerprint(
                model=getattr(session, "model", "") or "",
                agent_name=getattr(session, "agent_name", "") or "default",
            ),
            output_fp=fingerprint_path(Path(skill_dir)),
        )
    except Exception as exc:  # noqa: BLE001 — bookkeeping only
        logger.debug("Skill mark write skipped: %s", exc)


def _clear_skill_mark(store: Any, name: str) -> None:
    """Drop a stale fingerprint when a skill disappears from the index."""
    try:
        from boarai.core.runtime.artifact_mark import clear_mark

        clear_mark("skill", str(Path(store.agent_dir) / name))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Skill mark clear skipped for %s: %s", name, exc)


# --- The authoring run -------------------------------------------------------

async def _run_authoring_turn(
    *,
    session: Any,
    session_manager: Any,
    instruction: str,
    agent_name: str,
    timeout: float,
    context: str = "",
) -> Optional[Any]:
    """Run the authoring instruction in a DEDICATED background session."""
    from boarai.core.session.run_manager import get_run_manager

    rm = get_run_manager()
    seed = (
        f"Origin-run trajectory:\n\n{context}\n\n---\n\n{instruction}"
        if context
        else instruction
    )

    try:
        authoring = await session_manager.create_session(agent_name=agent_name or "default")
    except Exception as exc:  # noqa: BLE001
        logger.debug("Authoring session creation failed: %s", exc)
        return None
    authoring.source = "skill_authoring"
    origin_title = str(getattr(session, "title", "") or "").strip()
    authoring.title = f"[skill-authoring] {origin_title or session.session_id}"[:120]
    authoring.metadata["origin_session_id"] = getattr(session, "session_id", "")
    authoring.metadata["skill_authoring"] = True
    authoring.add_message("user", seed, metadata={"skill_authoring": True})
    try:
        await session_manager.save_session(authoring.session_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Authoring session save failed: %s", exc)

    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    run_id = await rm.create_run(
        authoring.session_id,
        completion_future=fut,
        agent_name=agent_name or "default",
        kind="skill_authoring",
    )
    logger.info(
        "Skill authoring run %s started in dedicated session %s (origin %s)",
        run_id, authoring.session_id, getattr(session, "session_id", ""),
    )
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("Skill authoring run %s timed out", run_id)
        try:
            await rm.cancel_run(run_id)
        except Exception:  # noqa: BLE001
            pass
        return None


# --- Entry point -------------------------------------------------------------

def schedule_skill_evolution(
    session: Any,
    run: Any,
    ctx: Any,
    metrics: Any,
) -> None:
    """Single entry point after a run completes (fire-and-forget). Never raises."""
    try:
        if not session or not ctx:
            return
        # The authoring run itself must not trigger evolution (recursion guard).
        if getattr(run, "kind", "chat") != "chat":
            return
        if getattr(run, "error", None):
            return
        status = getattr(run, "status", None)
        status_value = getattr(status, "value", status)
        if status_value is not None and str(status_value) != "completed":
            return

        cfg = _config()
        if not cfg.get("enabled", True):
            return

        messages = list(getattr(session, "messages", []) or [])
        session_id = getattr(session, "session_id", "") or ""
        agent_name = getattr(session, "agent_name", "") or "default"
        user_text = str(getattr(ctx, "user_input", "") or "")

        # Hard gates: an in-flight turn has no finished work to capture. These
        # apply to BOTH paths — creating and reflecting.
        last_assistant = _last_assistant_text(messages)
        if _turn_unfinished(last_assistant):
            logger.debug("Skill evolution skipped for %s: turn unfinished", session_id)
            return
        if _awaiting_user_decision(session_id, last_assistant):
            logger.debug("Skill evolution skipped for %s: awaiting user decision", session_id)
            return

        # Mutual exclusion: a run that used a skill may improve it, never create.
        if skill_used_this_run(messages):
            if cfg.get("reflect_enabled", True):
                spawn(_reflect_worker(
                    session_id, agent_name, extract_viewed_skills(messages), cfg,
                ))
            return

        # --- Creation gate: a floor plus at least one recurrence/intent signal ---
        turns = int(getattr(metrics, "turns", 0) or 0)
        tool_calls = int(getattr(metrics, "tool_calls", 0) or 0)
        failures = int(getattr(metrics, "tool_failures", 0) or 0)

        strong_intent = bool(_STRONG_INTENT_RE.search(user_text))
        weak_intent = (
            bool(_WEAK_INTENT_RE.search(user_text))
            and tool_calls >= int(cfg["weak_intent_min_tool_calls"])
        )
        hard_won = failures >= int(cfg["min_tool_failures"])

        gate = (
            turns >= int(cfg["auto_create_threshold"])
            and tool_calls >= int(cfg["min_tool_calls"])
        ) or strong_intent

        cheap_signals = [
            name for name, hit in (
                ("intent", strong_intent),
                ("correction", weak_intent),
                ("hard_won", hard_won),
            ) if hit
        ]
        # Recurrence needs the ledger; it is checked in the worker so a run with
        # no cheap signal still gets evaluated without blocking this path.
        if not gate:
            logger.debug(
                "Skill evolution skipped for %s: below floor (turns=%s tools=%s)",
                session_id, turns, tool_calls,
            )
            return
        # Cooldown and the daily cap gate the ATTEMPT, not the recording; they
        # are applied in the worker AFTER the ledger entry, so a shape blocked
        # by the cooldown still counts toward recurrence on its next sighting.
        spawn(_create_worker(
            session_id, agent_name, user_text, cheap_signals, cfg,
        ))
    except Exception as exc:  # noqa: BLE001 — scheduling must never break a run
        logger.debug("Skill evolution scheduling skipped: %s", exc)


# --- Workers -----------------------------------------------------------------

def _skill_map(store: Any) -> Dict[str, Tuple[str, str]]:
    """{name: (description, body)} over the WHOLE index."""
    return {s.name: (s.description, s.body) for s in store.list_all()}


def _managed_names(messages: List[Any]) -> set:
    """Skill names an authoring run itself touched via skill_manage."""
    names: set = set()
    for m in messages:
        for tc in getattr(m, "tool_calls", None) or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            if fn.get("name", "") != "skill_manage":
                continue
            args = fn.get("arguments", "")
            try:
                args = json.loads(args) if isinstance(args, str) else args
            except json.JSONDecodeError:
                args = {}
            name = str((args or {}).get("name") or "").strip()
            if name:
                names.add(name)
    return names


async def _report_changes(
    session: Any,
    session_manager: Any,
    store: Any,
    before: Dict[str, Tuple[str, str]],
    after: Dict[str, Tuple[str, str]],
    *,
    authoring_session_id: str,
    run_id: str,
    managed: Optional[set] = None,
) -> None:
    """Diff the index around an authoring run and emit one card per change."""
    created = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))
    improved = sorted(n for n in before if n in after and after[n] != before[n])
    if managed is not None:
        foreign = [n for n in (*created, *improved, *deleted) if n not in managed]
        created = [n for n in created if n in managed]
        improved = [n for n in improved if n in managed]
        deleted = [n for n in deleted if n in managed]
        if foreign:
            logger.info(
                "Skill index changed outside the authoring run; not attributed: %s",
                ", ".join(foreign),
            )

    for name in created:
        skill = await asyncio.to_thread(store.get, name)
        description = getattr(skill, "description", "") or ""
        if skill is not None:
            _mark_skill(skill, session, run_id)
        await _persist_skill_card(
            session, session_manager, kind="created", name=name, detail=description,
            authoring_session_id=authoring_session_id,
        )
        await _publish("created", {
            "name": name, "description": description, "auto": True,
            "source": "agent", "session_id": session.session_id,
        })

    for name in improved:
        skill = await asyncio.to_thread(store.get, name)
        description = getattr(skill, "description", "") or ""
        if skill is not None:
            _mark_skill(skill, session, run_id)
        await _persist_skill_card(
            session, session_manager, kind="improved", name=name, detail=description,
            authoring_session_id=authoring_session_id,
        )
        await _publish("updated", {
            "name": name, "description": description, "auto": True,
            "session_id": session.session_id,
        })

    for name in deleted:
        _clear_skill_mark(store, name)
        await _persist_skill_card(
            session, session_manager, kind="deleted", name=name,
            authoring_session_id=authoring_session_id,
        )

    if created or improved or deleted:
        logger.info(
            "Skill authoring changed the index: +%s ~%s -%s",
            created or "-", improved or "-", deleted or "-",
        )
        try:
            await session_manager.save_session(session.session_id)
        except Exception:  # noqa: BLE001
            pass


async def _create_worker(
    session_id: str,
    agent_name: str,
    user_text: str,
    cheap_signals: List[str],
    cfg: Dict[str, Any],
) -> None:
    """Confirm the recurrence signal, then run one authoring turn and report."""
    try:
        from boarai.core.session.manager import get_session_manager

        from .store import get_skill_store

        store = get_skill_store()
        session_manager = get_session_manager()
        session = await session_manager.get_session(session_id)
        if session is None:
            return

        messages = list(getattr(session, "messages", []) or [])
        tool_names = evidence.trajectory_tool_names(messages)

        # Record EVERY floor-passing run BEFORE any other gate. The recurrence
        # signal compares DISTINCT SESSIONS, so a shape must be written on its
        # first sighting or the second session finds an empty ledger and the
        # signal can never fire. The old order — record only after a signal was
        # found — was a deadlock: nothing ever wrote the ledger, so
        # "seen >= recurrence_min_sessions" was unreachable in production (the
        # unit test missed it because it prefilled the ledger by hand).
        seen = await asyncio.to_thread(
            record_signal, user_text, tool_names, session_id,
            int(cfg["signal_ledger_max"]),
        )

        # Budgets gate the ATTEMPT, not the recording: a run blocked by the
        # cooldown still counts toward recurrence on its next sighting.
        if _under_cooldown(session_id, float(cfg["cooldown_seconds"])):
            logger.debug("Skill evolution cooled down for %s", session_id)
            return
        if _daily_exceeded(int(cfg["daily_create_cap"])):
            logger.debug("Skill evolution daily cap reached")
            return

        signals = list(cheap_signals)
        if not signals:
            # ``seen`` already includes THIS session: record_signal returns the
            # distinct-session count after the write, so the first sighting of
            # a shape is 1 and the second session's run makes it 2.
            if seen < int(cfg["recurrence_min_sessions"]):
                logger.debug(
                    "Skill evolution skipped for %s: no signal (recurrence=%s/%s)",
                    session_id, seen, cfg["recurrence_min_sessions"],
                )
                return
            signals.append("recurrence")

        _last_attempt[session_id] = time.time()
        _bump_daily()
        logger.info(
            "Skill authoring triggered for %s (signals: %s)",
            session_id, ", ".join(signals),
        )

        before = await asyncio.to_thread(_skill_map, store)
        instruction = (
            _CREATE_INSTRUCTION
            .replace("__EXISTING__", await asyncio.to_thread(_existing_skills_brief, store))
            .replace("__STANDARDS__", SKILL_AUTHORING_STANDARDS)
        )
        context = await asyncio.to_thread(
            evidence.render_trajectory, messages,
            cap=int(cfg["authoring_context_chars"]),
        )
        completed = await _run_authoring_turn(
            session=session,
            session_manager=session_manager,
            instruction=instruction,
            agent_name=agent_name,
            timeout=float(cfg["authoring_timeout"]),
            context=context,
        )
        if completed is None or getattr(completed, "error", None):
            return

        authoring_session_id = str(getattr(completed, "session_id", "") or "")
        authoring_session = await session_manager.get_session(authoring_session_id)
        managed = (
            _managed_names(list(getattr(authoring_session, "messages", []) or []))
            if authoring_session is not None else None
        )

        # Re-read the origin session: concurrent user activity may advance it.
        session = await session_manager.get_session(session_id)
        if session is None:
            return
        after = await asyncio.to_thread(_skill_map, store)
        await _report_changes(
            session, session_manager, store, before, after,
            authoring_session_id=authoring_session_id,
            run_id=str(getattr(completed, "run_id", "") or ""),
            managed=managed,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Skill create worker failed: %s", exc)


async def _reflect_worker(
    session_id: str,
    agent_name: str,
    viewed: List[str],
    cfg: Dict[str, Any],
) -> None:
    """Improve a skill this run actually used (at most one per run)."""
    try:
        from boarai.core.session.manager import get_session_manager

        from .store import get_skill_store

        store = get_skill_store()
        target = None
        for name in viewed:
            skill = await asyncio.to_thread(store.get, name)
            if (
                skill is not None
                and skill.is_writable
                and await asyncio.to_thread(store.should_reflect, name)
            ):
                target = skill
                break
        if target is None:
            return
        # Marked BEFORE the run: one attempt per cadence window, so an LLM
        # failure cannot spin forever.
        await asyncio.to_thread(store.mark_reflected, target.name)

        session_manager = get_session_manager()
        session = await session_manager.get_session(session_id)
        if session is None:
            return

        before = await asyncio.to_thread(_skill_map, store)
        instruction = (
            _IMPROVE_INSTRUCTION
            .replace("{name}", target.name)
            .replace("{uses}", str(getattr(target, "use_count", 0)))
            .replace("__STANDARDS__", SKILL_AUTHORING_STANDARDS)
        )
        context = await asyncio.to_thread(
            evidence.render_trajectory,
            list(getattr(session, "messages", []) or []),
            cap=int(cfg["authoring_context_chars"]),
        )
        completed = await _run_authoring_turn(
            session=session,
            session_manager=session_manager,
            instruction=instruction,
            agent_name=agent_name,
            timeout=float(cfg["authoring_timeout"]),
            context=context,
        )
        if completed is None or getattr(completed, "error", None):
            return

        session = await session_manager.get_session(session_id)
        if session is None:
            return
        authoring_session = await session_manager.get_session(
            str(getattr(completed, "session_id", "") or "")
        )
        managed = (
            _managed_names(list(getattr(authoring_session, "messages", []) or []))
            if authoring_session is not None else None
        )
        current = await asyncio.to_thread(store.get, target.name)
        if current is None:
            return
        if (current.body, current.description) == (target.body, target.description):
            logger.debug("Skill %s unchanged after reflection", target.name)
            return
        await asyncio.to_thread(store.bump_patch, target.name)
        after = await asyncio.to_thread(_skill_map, store)
        await _report_changes(
            session, session_manager, store, before, after,
            authoring_session_id=str(getattr(completed, "session_id", "") or ""),
            run_id=str(getattr(completed, "run_id", "") or ""),
            managed=managed,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Skill reflect worker failed: %s", exc)
