"""
Write-Once Tool Results (WOTR) — deterministic inline tiered retention.

Tool results are trimmed ONCE at insertion time and NEVER modified afterward.
The trim is fully deterministic: same input always produces same output,
so prefix cache remains intact across tool-loop iterations.

Tiers (by estimated token count):
    ≤ 2000 tokens  — kept in full (covers ~90% of typical tool results)
    ≤ 8000 tokens  — head 500 + tail 500 + [N tokens omitted] marker
    > 8000 tokens  — head 500 + tail 500 + structural preview + marker

The marker "[N tokens omitted]" includes the exact token count N,
making it deterministic (same result → same marker).
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---- Thresholds (in estimated tokens, ~4 chars per token) ----

FULL_RETAIN_MAX_TOKENS = 2000        # Keep full if ≤ 2000 tokens
HEAD_TAIL_MAX_TOKENS = 8000          # Head+tail only if ≤ 8000 tokens
HEAD_CHARS = 2000                    # ~500 tokens
TAIL_CHARS = 2000                    # ~500 tokens
STRUCTURAL_PREVIEW_LINES = 5         # Lines of structural preview for oversized

from .token_counter import count_tokens

def _deterministic_marker(omitted_tokens: int) -> str:
    """Generate a deterministic marker for omitted content."""
    return f"\n\n[{omitted_tokens} tokens omitted]"

def _structural_preview(content: str, lines: int = STRUCTURAL_PREVIEW_LINES) -> str:
    """Extract a structural preview of the content."""
    stripped = content.strip()
    if not stripped:
        return ""

    # JSON / dict
    if stripped.startswith("{") or stripped.startswith("["):
        preview_lines = stripped.split("\n")[:lines]
        return "\n".join(preview_lines)

    # Plain text / code: first N lines
    text_lines = stripped.split("\n")
    result = []
    count = 0
    for line in text_lines:
        if count >= lines:
            break
        stripped_line = line.rstrip()
        if stripped_line:
            result.append(stripped_line)
            count += 1
    return "\n".join(result)

def _compress_list_json(content: str) -> Optional[str]:
    """Deterministically slim a JSON result carrying an "entries" list."""
    stripped = content.strip()
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return None
    try:
        obj = json.loads(stripped)
    except (ValueError, TypeError):
        return None

    keep_short = {
        "entry_id", "id", "name", "topic", "title", "type", "entry_type",
        "pod", "pod_uri", "importance", "status", "count", "total",
        "offset", "has_more", "query", "path", "kind",
    }

    def slim_entry(e):
        if not isinstance(e, dict):
            return e
        out = {}
        for k, v in e.items():
            if k in keep_short:
                out[k] = v
            elif isinstance(v, str):
                out[k] = v[:80] + ("…" if len(v) > 80 else "")
            elif isinstance(v, (int, float, bool)) or v is None:
                out[k] = v
            # nested lists/dicts inside entries are dropped (rare)
        return out

    if isinstance(obj, dict) and isinstance(obj.get("entries"), list):
        out = {k: v for k, v in obj.items() if k != "entries"}
        out["entries"] = [slim_entry(e) for e in obj["entries"]]
        return json.dumps(out, ensure_ascii=False, indent=2)
    if isinstance(obj, list):
        return json.dumps([slim_entry(e) for e in obj], ensure_ascii=False, indent=2)
    return None


def _render_list_page(obj: dict, kept: list, omitted: int) -> str:
    """Serialize a paged entries list with a deterministic truncation note."""
    out = {k: v for k, v in obj.items() if k != "entries"}
    out["count"] = len(kept)
    if omitted:
        out["entries_truncated"] = omitted
        out["note"] = (
            f"{omitted} more entries omitted — repeat the call with "
            "offset/limit pagination to see the rest"
        )
    out["entries"] = kept
    return json.dumps(out, ensure_ascii=False, indent=2)


def _page_list_json(content: str, max_tokens: int) -> Optional[str]:
    """Keep the largest entry prefix that fits max_tokens (deterministic)."""
    try:
        obj = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not (isinstance(obj, dict) and isinstance(obj.get("entries"), list)):
        return None
    entries = obj["entries"]
    total = len(entries)
    lo, hi = 0, total
    while lo < hi:
        mid = (lo + hi + 1) // 2
        cand = _render_list_page(obj, entries[:mid], total - mid)
        if count_tokens(cand) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return _render_list_page(obj, entries[:lo], total - lo)


def trim(content: str, strategy: Optional[str] = None) -> str:
    """Trim tool result content once, deterministically."""
    if not content:
        return content

    if strategy == "list":
        slimmed = _compress_list_json(content)
        if slimmed is not None:
            if count_tokens(slimmed) <= FULL_RETAIN_MAX_TOKENS:
                return slimmed
            # Still too large: drop whole ENTRIES from the tail (keep the
            # enumeration head + a deterministic paging hint) instead of
            # slicing characters — a head/tail cut would lose the middle
            # entries entirely, defeating the point of a list.
            paged = _page_list_json(slimmed, FULL_RETAIN_MAX_TOKENS)
            if paged is not None:
                return paged
            content = slimmed  # non-entries JSON: fall through to tiers

    total_tokens = count_tokens(content)

    # Tier 1: small enough to keep in full
    if total_tokens <= FULL_RETAIN_MAX_TOKENS:
        return content

    # Tier 2: head + tail with deterministic marker
    if total_tokens <= HEAD_TAIL_MAX_TOKENS:
        head = content[:HEAD_CHARS]
        tail = content[-TAIL_CHARS:] if len(content) > TAIL_CHARS else ""
        omitted = total_tokens - count_tokens(head) - count_tokens(tail)
        return head + _deterministic_marker(max(omitted, 1)) + "\n" + tail

    # Tier 3: structural preview + head + tail + marker
    preview = _structural_preview(content)
    head = content[:HEAD_CHARS]
    tail = content[-TAIL_CHARS:] if len(content) > TAIL_CHARS else ""
    omitted = total_tokens - count_tokens(preview) - count_tokens(head) - count_tokens(tail)

    parts = [preview]
    if preview:
        # Honest label: what follows is only the HEAD — the middle is omitted.
        # The old "full content below" wording misled the model into thinking
        # nothing was missing.
        parts.append("\n[head of content below — middle omitted — tail at end]")
    parts.append(head)
    parts.append(_deterministic_marker(max(omitted, 1)))
    if tail:
        parts.append(tail)

    return "\n".join(parts)
