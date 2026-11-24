"""
File operation tools — communicates via ctx (no core imports).
Self-registers with ToolRegistry on import.
Relative paths resolve against the session workspace (not project root).
"""

import asyncio
import difflib
import fnmatch
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from boarai.core.extension.tool import (
    CompressStrategy,
    SecurityProfile,
    SuggestedCategory,
    ToolParameter,
    tool,
)

logger = logging.getLogger(__name__)

# ---- Constants ----

# File size limit: refuse to read/write files larger than this
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_GREP_FILES = 5000
MAX_GREP_DIRS = 10000
MAX_GREP_TOTAL_BYTES = 64 * 1024 * 1024
MAX_GREP_LINE_CHARS = 2000
MAX_COPY_FILES = 5000
MAX_COPY_DIRS = 10000
MAX_COPY_TOTAL_BYTES = 512 * 1024 * 1024

# Cap per-file unified diff so a huge write cannot bloat events / storage.
MAX_DIFF_CHARS = 100_000
# difflib is super-linear; skip the diff body for very large before/after
# content (still record path + change type) so a big write cannot stall a tool.
MAX_DIFF_INPUT_CHARS = 400_000

# Directories excluded from recursive search
EXCLUDED_DIRS: Set[str] = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".tox", ".eggs", "build", "dist", ".next", ".nuxt",
    ".cache", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
}

# Binary file extensions + common binary suffixes
BINARY_EXTENSIONS: Set[str] = {
    ".pyc", ".pyo", ".so", ".o", ".a", ".lib", ".dll", ".exe", ".bin",
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".ico", ".bmp", ".webp",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav", ".flac",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".db", ".sqlite", ".sqlite3",
}


# ---- Path Helpers ----

def _workspace_root(ctx) -> Optional[Path]:
    """Session workspace if set, else project_root (install root) with a warning."""
    ws = getattr(ctx, "workspace", None)
    if ws is not None:
        try:
            return Path(ws)
        except (TypeError, ValueError):
            pass
    root = getattr(ctx, "project_root", None)
    if root:
        logger.warning(
            "Session workspace not resolved; falling back to project_root: %s", root
        )
    return Path(root) if root else None


def _safe_path(ctx, target: str) -> Tuple[Optional[Path], Optional[str]]:
    """Resolve path under session workspace; return ``(resolved_path, warning)``."""
    root = _workspace_root(ctx)
    if not root:
        return None, None
    p = Path(target)
    is_relative = not p.is_absolute()
    if is_relative:
        p = root / p
    try:
        p = p.resolve()
        # Prefer session workspace as primary sandbox for relative paths
        try:
            p.relative_to(root.resolve())
            return p, None
        except ValueError:
            pass
        # Absolute path: also allow if under BoarAi project_root (compat)
        proj = getattr(ctx, "project_root", None)
        if proj:
            try:
                p.relative_to(Path(proj).resolve())
                warn = None
                if is_relative:
                    warn = (
                        f"Relative path '{target}' resolved outside the session "
                        f"workspace to '{p}' (under project root)."
                    )
                return p, warn
            except ValueError:
                pass
        # Outside: still return resolved path; PathGuard/security decides write/read
        return p, None
    except (ValueError, OSError):
        return None, None


def _nested_boarai_error(ctx, resolved: Path) -> Optional[str]:
    """Reject writes into a ``.boarai`` dir nested inside the session workspace."""
    ws = getattr(ctx, "workspace", None)
    if ws is None:
        return None
    try:
        ws_res = Path(ws).resolve()
    except (TypeError, ValueError, OSError):
        return None
    proj = getattr(ctx, "project_root", None)
    if proj:
        try:
            if ws_res == Path(proj).resolve():
                return None
        except (TypeError, ValueError, OSError):
            pass
    try:
        rel = resolved.relative_to(ws_res)
    except (ValueError, OSError):
        return None
    if not rel.parts or rel.parts[0] != ".boarai":
        return None
    try:
        from boarai.core.runtime import get_data_dir

        base = str(get_data_dir())
    except Exception:  # noqa: BLE001 - keep the guard usable without runtime
        base = f"{Path(proj).as_posix()}/.boarai" if proj else "<data_dir>"
    return (
        f"Refusing to write into '.boarai' inside the session workspace "
        f"('{resolved}'): relative paths resolve against the workspace, but "
        f"runtime data lives ONLY in the data dir. Use the "
        f"ABSOLUTE path instead — skills: {base}/extensions/skills/<name>/"
        f"SKILL.md; temp files: {base}/temp/."
    )


def _is_link_or_junction(path: Path) -> bool:
    """Treat Windows directory junctions like symlinks for traversal safety."""
    try:
        if path.is_symlink():
            return True
        isjunction = getattr(os.path, "isjunction", None)
        return bool(isjunction and isjunction(path))
    except OSError:
        return True


def _has_symlink_below(root: Path, target: Path) -> bool:
    """Check unresolved path components under an approved root."""
    try:
        base = Path(root).absolute()
        relative = Path(target).absolute().relative_to(base)
    except (OSError, ValueError):
        return False
    current = base
    for component in relative.parts:
        current = current / component
        try:
            if _is_link_or_junction(current):
                return True
        except OSError:
            return True
    return False


def _lexical_boundary(ctx, target: Path) -> Optional[Path]:
    """Find the workspace/project root containing the unresolved path."""
    for candidate in (_workspace_root(ctx), getattr(ctx, "project_root", None)):
        if candidate is None:
            continue
        try:
            Path(target).absolute().relative_to(Path(candidate).absolute())
            return Path(candidate)
        except (OSError, ValueError):
            continue
    return None


def _rel_path(ctx, path: Path) -> str:
    """Convert absolute path back to workspace-relative string using forward slashes."""
    root = _workspace_root(ctx)
    if root:
        try:
            rel = path.relative_to(Path(root).resolve())
            return rel.as_posix()
        except ValueError:
            pass
    proj = getattr(ctx, "project_root", None)
    if proj:
        try:
            rel = path.relative_to(Path(proj).resolve())
            return rel.as_posix()
        except ValueError:
            pass
    return path.as_posix()


def _is_binary_path(filepath: Path) -> bool:
    """Check if a file path indicates binary content by extension."""
    return filepath.suffix.lower() in BINARY_EXTENSIONS


# ---- Vision-aware image reading (2026-11-11 multimodal overhaul P2) ----


def _read_head(filepath: Path, n: int = 12) -> bytes:
    """First bytes of a file for magic-byte sniffing (empty on OSError)."""
    try:
        with filepath.open("rb") as fh:
            return fh.read(n)
    except OSError:
        return b""


def _image_mime(filepath: Path) -> Optional[str]:
    """Magic-byte image detection; extension alone is never trusted."""
    from boarai.core.session.attachments import detect_mime_type

    head = _read_head(filepath)
    if not head:
        return None
    mime = detect_mime_type(filepath.name, head)
    return mime if mime.startswith("image/") else None


#: Cap on wire-only pixel messages riding a request (same intent as hermes'
#: max_calls_per_image — a tool loop reading images must not balloon every
#: later turn of the run).
MAX_WIRE_IMAGES = 3
#: Run-level cap on question-driven vision-model calls (cost guard: distinct
#: questions are cache misses, so a looping model could otherwise burn one
#: sidecar call per question forever).
MAX_QUESTION_CALLS_PER_RUN = 24


def _active_run(ctx):
    """The in-memory Run object for this tool call (None when unavailable)."""
    run_id = getattr(ctx, "run_id", None)
    if not run_id:
        return None
    try:
        from boarai.core.session.run_manager import get_run_manager

        return get_run_manager().get_run_object(run_id)
    except Exception:  # noqa: BLE001 - run lookup is best-effort
        return None


def _tool_image_route(ctx) -> str:
    """ImageRoute value for tool-side image handling."""
    from boarai.core.session.run.model_resolver import route_for_model

    run = _active_run(ctx)
    if run is not None and getattr(run, "_vision_pixels_refused", False):
        return "text_only"

    model = None
    if run is not None:
        model = getattr(run, "model", None) or None
    if not model:
        session_id = getattr(ctx, "session_id", None)
        if session_id:
            try:
                from boarai.core.session.manager import get_session_manager

                sess = get_session_manager()._sessions.get(session_id)
                model = getattr(sess, "model", None)
            except Exception:  # noqa: BLE001 - undeclared capability reading
                model = None
    if not model:
        try:
            from boarai.core.llm import get_llm_manager

            model = get_llm_manager().get_stats().get("default_model")
        except Exception:  # noqa: BLE001 - undeclared stays undeclared
            model = None
    return route_for_model(model).value


async def _queue_wire_image(ctx, filepath: Path, mime: str) -> bool:
    """Queue a wire-only pixel message on the active run (memory-only)."""
    import base64

    run = _active_run(ctx)
    if run is None:
        return False
    try:
        from boarai.core.session.attachments import (
            MAX_IMAGE_BYTES,
            image_exceeds_limits,
            parse_image_dimensions,
        )

        size = filepath.stat().st_size
        if size <= 0 or size > MAX_IMAGE_BYTES:
            return False
        data = await asyncio.to_thread(filepath.read_bytes)
        if image_exceeds_limits(parse_image_dimensions(data[:4096])):
            return False
        queue = run._pending_wire_images
        src = str(filepath)
        # ``_src`` is stripped by the executor before the message hits the
        # wire (underscore keys never travel — same convention as tool
        # result internals).
        queue[:] = [q for q in queue if q.get("_src") != src]
        queue.append({
            "role": "user",
            "_src": src,
            "content": [
                {"type": "text",
                 "text": f"[Image read by file_read: {filepath.name}]"},
                {"type": "image_url", "image_url": {
                    "url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}",
                }},
            ],
        })
        if len(queue) > MAX_WIRE_IMAGES:
            del queue[:len(queue) - MAX_WIRE_IMAGES]
        return True
    except Exception as e:  # noqa: BLE001 - degrade to a text-only note
        logger.warning("file_read wire image queue failed: %s", e)
        return False


async def _sidecar_describe(ctx, filepath: Path, question: Optional[str]):
    """Cached vision-slot description; ``(text, slot_configured)``."""
    from boarai.core.session.run.model_resolver import get_vision_capability
    from boarai.core.session.run.vision_sidecar import describe_image

    cap = get_vision_capability()
    if cap is None:
        return None, False
    text = await describe_image(filepath, f"{cap[0]}/{cap[1]}", prompt=question)
    return text, True


async def _read_image(
    ctx, safe: Path, mime: str, question: Optional[str]
) -> Dict:
    """Vision-aware read of an image file (three-state routing)."""
    rel = _rel_path(ctx, safe)
    size = safe.stat().st_size
    head_note = f"Read image file [{mime}, {size} bytes]: {rel}"

    route = _tool_image_route(ctx)

    if question:
        # Route-first: a vision-capable main model answers from raw pixels
        # itself (highest fidelity — the NATIVE route never detours through
        # the slot); the sidecar serves only when pixels cannot ride.
        if route == "native" and await _queue_wire_image(ctx, safe, mime):
            return {
                "content": (
                    f"{head_note}\nPixels are attached after this tool "
                    f"result — answer the question yourself: {question}"
                ),
                "path": rel, "kind": "image",
            }
        # Cost guard: distinct questions are cache misses, so cap the
        # question-driven vision calls of one run (repeats stay free via
        # the disk cache but still count — the cap only bites on loops).
        run = _active_run(ctx)
        if run is not None:
            asked = getattr(run, "_vision_question_count", 0)
            if asked >= MAX_QUESTION_CALLS_PER_RUN:
                return {
                    "content": (
                        f"{head_note}\nQuestion refused: this run already "
                        f"made {asked} vision-model calls for image "
                        f"questions. Answer from the results you have; the "
                        f"image stays readable at {safe}."
                    ),
                    "path": rel, "kind": "image",
                }
            run._vision_question_count = asked + 1
        desc, has_slot = await _sidecar_describe(ctx, safe, question)
        if desc:
            return {
                "content": f"[Image: {rel}]\nQ: {question}\nA: {desc}\n[/Image]",
                "path": rel, "kind": "image",
            }
        if has_slot:
            return {
                "content": (
                    f"{head_note}\nThe vision model could not answer the "
                    f"question; the image stays readable at {safe}."
                ),
                "path": rel, "kind": "image",
            }
        return {
            "content": (
                f"{head_note}\nThe question could not be answered from "
                f"pixels (no working vision path — no vision capability "
                f"is configured and the pixels could not be attached); "
                f"the file stays readable at {safe}."
            ),
            "path": rel, "kind": "image",
        }

    if route == "sidecar":
        desc, _ = await _sidecar_describe(ctx, safe, None)
        if desc:
            return {
                "content": f"[Image: {rel}]\n{desc}\n[/Image]",
                "path": rel, "kind": "image",
            }
        return {
            "content": (
                f"{head_note}\nThe vision model could not describe it; "
                f"readable at {safe}."
            ),
            "path": rel, "kind": "image",
        }
    if route == "native":
        if await _queue_wire_image(ctx, safe, mime):
            return {
                "content": f"{head_note}\nPixels are attached after this tool result.",
                "path": rel, "kind": "image",
            }
        # Gated (oversize/dimension) or run unavailable: fall back to the
        # slot description when one is configured, else the path note.
        desc, has_slot = await _sidecar_describe(ctx, safe, None)
        if desc:
            return {
                "content": (
                    f"{head_note}\nPixels could not be attached (over the "
                    f"inline limits); vision description:\n{desc}"
                ),
                "path": rel, "kind": "image",
            }
        return {
            "content": (
                f"{head_note}\nImage pixels could not be attached (too "
                f"large, over dimension limits, or run unavailable); "
                f"readable at {safe}."
            ),
            "path": rel, "kind": "image",
        }
    return {
        "content": (
            f"{head_note}\nOmitted: the active model does not support "
            f"vision and no vision capability is configured; readable at "
            f"{safe}."
        ),
        "path": rel, "kind": "image",
    }


def _grep_decoded_file(
    raw: bytes,
    relative_path: str,
    regex: re.Pattern,
    remaining: int,
    context_lines: int,
) -> List[Dict[str, Any]]:
    """Decode and scan one bounded file off the event loop."""
    lines = raw.decode("utf-8", errors="replace").split("\n")
    found: List[Dict[str, Any]] = []
    for index, line in enumerate(lines):
        if len(found) >= remaining:
            break
        if regex.search(line):
            content = line[:MAX_GREP_LINE_CHARS]
            if len(line) > MAX_GREP_LINE_CHARS:
                content += "…(line truncated)"
            entry: Dict[str, Any] = {
                "file": relative_path,
                "line": index + 1,
                "content": content,
            }
            if context_lines > 0:
                start = max(0, index - context_lines)
                end = min(len(lines), index + context_lines + 1)
                entry["context"] = "\n".join(
                    f"{line_no + 1:>4}| {lines[line_no][:MAX_GREP_LINE_CHARS]}"
                    for line_no in range(start, end)
                )
            found.append(entry)
    return found


def _validate_grep_pattern(pattern: str) -> Optional[str]:
    """Reject regex features with unbounded/backtracking execution cost."""
    index = 0
    in_class = False
    last_closed_group = False
    variable_repetitions = 0
    exact_repetition_work = 0

    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            if index + 1 < len(pattern):
                escaped = pattern[index + 1]
                if escaped.isdigit() or escaped in ("g", "k"):
                    return "Backreferences are not supported by bounded grep"
                index += 2
            else:
                index += 1
            last_closed_group = False
            continue
        if in_class:
            if char == "]":
                in_class = False
            index += 1
            last_closed_group = False
            continue
        if char == "[":
            in_class = True
            index += 1
            last_closed_group = False
            continue
        if char == "(":
            if pattern.startswith("(?", index):
                if not pattern.startswith("(?:", index):
                    return "Lookaround and special regex groups are not supported by bounded grep"
                index += 3
            else:
                index += 1
            last_closed_group = False
            continue
        if char == ")":
            index += 1
            last_closed_group = True
            continue

        quantifier_end = index
        variable = False
        exact_work = 0
        if char in "*+?":
            variable = True
            quantifier_end = index + 1
        elif char == "{" and index + 1 < len(pattern) and pattern[index + 1].isdigit():
            close = pattern.find("}", index + 1)
            if close >= 0:
                spec = pattern[index + 1:close]
                if "," not in spec:
                    try:
                        exact_work = int(spec)
                    except ValueError:
                        exact_work = 4097
                    variable = False
                    quantifier_end = close + 1
                else:
                    lower, _, upper = spec.partition(",")
                    if not upper:
                        variable = True
                    else:
                        try:
                            lower_n, upper_n = int(lower or "0"), int(upper)
                        except ValueError:
                            lower_n, upper_n = 0, 4097
                        variable = lower_n != upper_n
                        exact_work = upper_n
                    quantifier_end = close + 1

        if quantifier_end != index:
            if last_closed_group:
                return "Quantified groups are not supported by bounded grep"
            if exact_work > 4096:
                return "Regex repetitions are limited to 4096 characters"
            if variable:
                variable_repetitions += 1
                if variable_repetitions > 1:
                    return "Use at most one variable repetition in a grep regex"
            else:
                exact_repetition_work += max(0, exact_work)
                if exact_repetition_work > 8192:
                    return "Combined exact regex repetitions exceed 8192 characters"
            index = quantifier_end
            # Consume lazy ("?") or possessive ("+") modifiers as part of
            # the quantifier instead of counting them as another repetition.
            if index < len(pattern) and pattern[index] in "?+":
                index += 1
            last_closed_group = False
            continue

        index += 1
        last_closed_group = False

    return None


def _is_within(parent: Path, child: Path) -> bool:
    """Check whether ``child`` is inside ``parent`` (inclusive)."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_same_path(a: Path, b: Path) -> bool:
    """Check whether two resolved paths point to the same location."""
    try:
        return a.resolve() == b.resolve()
    except (ValueError, OSError):
        return a == b


def _detect_line_ending(content: str) -> str:
    """Detect the dominant line ending in the content."""
    crlf = content.count("\r\n")
    lf = content.count("\n") - crlf  # solo LFs (not part of CRLF)
    return "\r\n" if crlf > lf else "\n"


def _read_file_text(filepath: Path, preserve_encoding: bool = False) -> Tuple[str, str]:
    """Read a text file, trying UTF-8 first then falling back to common encodings."""
    raw = filepath.read_bytes()

    if len(raw) == 0:
        return ("", "utf-8")

    # Binary detection: check for null bytes in the first 8 KB
    if b"\x00" in raw[:8192]:
        raise ValueError(f"File appears to be binary (contains null bytes)")

    encodings = ["utf-8", "utf-8-sig", "latin-1", "cp1252", "gbk"]
    if not preserve_encoding:
        encodings = encodings[:1]  # Only try UTF-8

    for enc in encodings:
        try:
            content = raw.decode(enc)
            return (content, enc)
        except (UnicodeDecodeError, LookupError):
            continue

    # Last resort: replace errors
    content = raw.decode("utf-8", errors="replace")
    return (content, "utf-8(replace)")


# ---- Change diff (run-level file-change traceability) ----

def _count_diff_lines(diff_text: str) -> Tuple[int, int]:
    """Count (+additions, -deletions) from a unified diff body."""
    adds = dels = 0
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            adds += 1
        elif line.startswith("-") and not line.startswith("---"):
            dels += 1
    return adds, dels


def _build_change(rel: str, old: Optional[str], new: Optional[str],
                  change_type: str) -> Dict[str, Any]:
    """Build the internal file-change record carried on a tool result."""
    try:
        old_text = old or ""
        new_text = new or ""
        # Guard against pathological difflib cost / huge payloads on very large
        # files: record the change (path + type) but omit the diff body.
        if len(old_text) + len(new_text) > MAX_DIFF_INPUT_CHARS:
            return {"_path": rel, "_change_type": change_type}
        diff = "".join(difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        ))
        if len(diff) > MAX_DIFF_CHARS:
            diff = diff[:MAX_DIFF_CHARS] + "\n…(diff truncated)"
        adds, dels = _count_diff_lines(diff)
        return {
            "_path": rel,
            "_change_type": change_type,
            "_diff": diff,
            "_additions": adds,
            "_deletions": dels,
        }
    except Exception:  # noqa: BLE001 — diffing must never break a file op
        return {"_path": rel, "_change_type": change_type}


def _should_exclude_dir(parts: Tuple[str, ...]) -> bool:
    """Check if any path component matches an excluded directory name."""
    return bool(EXCLUDED_DIRS.intersection(parts))


# ---- v2 hook trigger: ext.file_tools.pre_write / post_write ----

async def _trigger_fs_hook(hook_name: str, payload: Dict[str, Any],
                           timeout: float = 2.0) -> Dict[str, Any]:
    """Trigger a file_tools hook point (V2 fix: make declared hooks live)."""
    try:
        from boarai.core.extension import get_hook_registry
        reg = get_hook_registry()
        if reg.is_declared(f"ext.file_tools.{hook_name}"):
            result = await reg.trigger(
                f"ext.file_tools.{hook_name}", dict(payload), timeout=timeout,
            )
            if result.blocked:
                return {"blocked": True,
                        "reason": result.reason or f"blocked by {hook_name} hook"}
            if isinstance(result.data, dict):
                return result.data
        return payload
    except Exception as e:  # noqa: BLE001 — hooks must never break file tools
        logger.warning(f"fs hook '{hook_name}' failed: {e}")
        return payload


# ---- Snapshot (best-effort undo) ----

async def _snapshot_file(path: Path) -> Optional[Path]:
    """Snapshot a file before modification (for undo/revert)."""
    try:
        from boarai.core.trash import get_trash_manager
        tm = get_trash_manager()
        if tm:
            try:
                return await asyncio.wait_for(tm.snapshot_file(path), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(f"Snapshot timed out for: {path}")
    except Exception:
        pass  # Best-effort
    return None


# ---- Fuzzy Matching for file_edit ----

def _strip_trailing_ws(text: str) -> str:
    """Strip trailing whitespace from each line."""
    return "\n".join(line.rstrip() for line in text.split("\n"))


def _normalize_line_endings(text: str) -> str:
    """Normalize all line endings to LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _normalize_indent(text: str) -> str:
    """Normalize indentation: convert tabs to 4 spaces, strip common leading ws."""
    lines = text.split("\n")
    lines = [line.replace("\t", "    ") for line in lines]
    non_empty = [line for line in lines if line.strip()]
    if not non_empty:
        return text
    min_indent = min(len(line) - len(line.lstrip()) for line in non_empty)
    if min_indent > 0:
        lines = [line[min_indent:] if line.strip() else line for line in lines]
    return "\n".join(lines)


def _find_match(content: str, old_string: str) -> Optional[Tuple[str, int]]:
    """Find old_string in content using layered fuzzy matching."""
    # Strategy 0: Exact match (always preferred)
    exact_count = content.count(old_string)
    if exact_count > 0:
        return (old_string, exact_count)

    fuzzy_strategies = [
        ("strip trailing ws", _strip_trailing_ws),
        ("normalize line endings", _normalize_line_endings),
        ("normalize indentation", _normalize_indent),
    ]

    for strategy_name, transform in fuzzy_strategies:
        transformed_old = transform(old_string)
        transformed_content = transform(content)
        pos_t = transformed_content.find(transformed_old)
        if pos_t < 0:
            continue

        logger.debug(f"file_edit fuzzy match via '{strategy_name}' at pos {pos_t}")

        # Map back to original content.
        # For shrink-only transforms, pos in original >= pos in transformed.
        # Take a generous window around the match position and search for
        # old_string within it (using the same transform for sub-matching).
        window_start = max(0, pos_t - 100)
        window_end = min(len(content), pos_t + len(transformed_old) + 500)
        window = content[window_start:window_end]

        # 1st attempt: exact old_string in window
        idx = window.find(old_string)
        if idx >= 0:
            actual = window[idx:idx + len(old_string)]
            return (actual, content.count(actual))

        # 2nd attempt: apply same transform to window and find match, then map back
        window_xf = transform(window)
        idx_xf = window_xf.find(transformed_old)
        if idx_xf >= 0:
            # Take the corresponding region from the original window.
            # Because the transform is shrink-only, the actual substring
            # starts at idx_xf or earlier, and is at least as long as old_string.
            margin = len(old_string) * 2
            actual_start = max(0, idx_xf - 10)
            actual_end = min(len(window), idx_xf + len(old_string) + margin)
            candidate = window[actual_start:actual_end]

            # Now try to find the exact old_string inside candidate
            cidx = candidate.find(old_string)
            if cidx >= 0:
                actual = candidate[cidx:cidx + len(old_string)]
                actual_count = content.count(actual)
                if actual_count > 0:
                    return (actual, actual_count)

            # Last resort: try line-based sliding window over original content
            old_lines = old_string.split("\n")
            content_lines = content.split("\n")
            n = len(old_lines)
            for start in range(len(content_lines) - n + 1):
                window_lines = content_lines[start:start + n]
                window_str = "\n".join(window_lines)
                if transform(window_str) == transformed_old:
                    actual = window_str
                    return (actual, content.count(actual))

        # 3rd attempt: line-sliding on the full original
        # (only for strats that operate line-wise)
        if strategy_name != "normalize line endings":
            continue

        old_lines = old_string.split("\n")
        content_lines = content.split("\n")
        n = len(old_lines)
        for start in range(len(content_lines) - n + 1):
            window_str = "\n".join(content_lines[start:start + n])
            if transform(window_str) == transformed_old:
                actual = window_str
                return (actual, content.count(actual))

    return None


def _build_not_found_error(content: str, old_string: str, filepath: str) -> str:
    """Build a helpful error when old_string is not found in the file."""
    old_lines = old_string.split("\n")
    first_line = old_lines[0].strip() if old_lines else ""

    if first_line:
        content_lines = content.split("\n")
        similar: List[Tuple[int, str]] = []
        for idx, line in enumerate(content_lines):
            if line.strip() == first_line:
                start = max(0, idx - 2)
                end = min(len(content_lines), idx + len(old_lines) + 2)
                ctx_block = "\n".join(
                    f"  {i + 1:>4}| {content_lines[i]}" for i in range(start, end)
                )
                similar.append((idx + 1, ctx_block))
                if len(similar) >= 2:  # Early exit after 2 matches
                    break

        if similar:
            details = "\n\n".join(
                f"First line matches near line {num}:\n{ctx}" for num, ctx in similar
            )
            return (
                f"String to replace not found in '{filepath}'. "
                f"The first line exists but the full multi-line string doesn't match. "
                f"This is often caused by whitespace differences (tabs vs spaces).\n\n"
                f"{details}\n\n"
                f"TIP: Use file_read to view the exact content, then retry the edit."
            )

    return (
        f"String to replace not found in '{filepath}'. "
        f"Ensure old_string matches exactly (including whitespace and indentation). "
        f"TIP: Use file_read to view the exact content first, then retry."
    )


# ========== TOOL DEFINITIONS ==========


@tool(name="file_read",
      description="Read the content of a file with line numbers. "
                  "Refuses to read binary files. "
                  "Image files (png/jpg/gif/webp/bmp) are read through the "
                  "vision path instead: described by the configured vision "
                  "model, or attached as pixels when the active model "
                  "supports images. "
                  "Use offset/limit to read large files in chunks.",
      security_profile=SecurityProfile.FILE_READ,
      path_param="path",
      compress_strategy=CompressStrategy.FILE,
      suggested_category=SuggestedCategory.READONLY,
      parameters=[
          ToolParameter(name="path", type="string", required=True,
                        description="File path (workspace-relative ok)"),
          ToolParameter(name="offset", type="integer", required=False, default=0,
                        description="Line number to start reading from (0-based)"),
          ToolParameter(name="limit", type="integer", required=False, default=500,
                        description="Max number of lines to read"),
          ToolParameter(name="question", type="string", required=False, default="",
                        description="For image files only: a specific question "
                                    "about the image, answered by the vision "
                                    "model (cached per image+question)"),
      ])
async def file_read(ctx, path: str, offset: int = 0, limit: int = 500,
                    question: str = "") -> Dict:
    safe, _warn = _safe_path(ctx, path)
    if not safe:
        return {"error": f"Path '{path}' is outside session workspace"}
    if not safe.exists():
        return {"error": f"File not found: {path}"}
    if not safe.is_file():
        return {"error": f"Not a file: {path}"}

    # Size check
    file_size = safe.stat().st_size
    if file_size > MAX_FILE_SIZE:
        return {"error": f"File too large ({file_size} bytes, max {MAX_FILE_SIZE} bytes)"}

    # Vision-aware image read (before the binary refusal): magic bytes win.
    image_mime = _image_mime(safe)
    if image_mime:
        return await _read_image(
            ctx, safe, image_mime, (question or "").strip() or None
        )

    # Binary detection
    if _is_binary_path(safe):
        return {"error": f"File appears to be binary ({safe.suffix}): {path}"}

    try:
        raw = safe.read_bytes()
        if b"\x00" in raw[:8192]:
            return {"error": f"File appears to be binary (contains null bytes): {path}"}

        content, encoding = _read_file_text(safe)
        lines = content.split("\n")
        total_lines = len(lines)

        if offset >= total_lines:
            return {
                "content": "", "path": _rel_path(ctx, safe),
                "total_lines": total_lines, "lines_read": 0,
                "note": f"offset {offset} exceeds total lines {total_lines}",
            }

        sliced = lines[offset:offset + limit]
        numbered = [f"{offset + i + 1:>6}|{line}" for i, line in enumerate(sliced)]
        result = "\n".join(numbered)

        return {
            "content": result,
            "path": _rel_path(ctx, safe),
            "total_lines": total_lines,
            "lines_read": len(sliced),
            "encoding": encoding,
        }
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Failed to read file: {e}"}


@tool(name="file_write",
      description="Write content to a file. Creates parent directories automatically. "
                  "Preserves existing line endings; uses LF for new files.",
      security_profile=SecurityProfile.FILE_WRITE,
      path_param="path",
      compress_strategy=CompressStrategy.DEFAULT,
      suggested_category=SuggestedCategory.SAFE,
      parameters=[
          ToolParameter(name="path", type="string", required=True,
                        description="File path (workspace-relative ok)"),
          ToolParameter(name="content", type="string", required=True,
                        description="Content to write"),
      ])
async def file_write(ctx, path: str, content: str) -> Dict:
    safe, warn = _safe_path(ctx, path)
    if not safe:
        return {"error": f"Path '{path}' is outside session workspace"}
    nested_err = _nested_boarai_error(ctx, safe)
    if nested_err:
        return {"error": nested_err}

    # Content size sanity check
    if len(content) > MAX_FILE_SIZE:
        return {"error": f"Content too large ({len(content)} bytes, max {MAX_FILE_SIZE})"}

    # v2 hook: pre_write (blockable) — other extensions may veto or transform
    hook_res = await _trigger_fs_hook("pre_write", {"path": str(safe), "content": content})
    if hook_res.get("blocked"):
        return {"error": f"Write blocked by extension hook: {hook_res.get('reason', 'vetoed')}"}
    content = hook_res.get("content", content)

    try:
        safe.parent.mkdir(parents=True, exist_ok=True)

        existed = safe.exists()
        # Snapshot existing file before overwriting (revert source)
        snap = await _snapshot_file(safe) if existed else None

        # Preserve existing line endings, default to LF for new files.
        # Capture the prior content too, so we can emit a change diff.
        newline = None
        old_text = ""
        if existed:
            try:
                old_text = safe.read_text(encoding="utf-8", errors="replace")
                newline = _detect_line_ending(old_text)
            except Exception:
                old_text = ""
        if newline is None:
            newline = "\n"

        safe.write_text(content, encoding="utf-8", newline=newline)
        rel = _rel_path(ctx, safe)
        logger.info(f"Wrote {len(content)} chars to {rel}")
        # v2 hook: post_write (observe)
        await _trigger_fs_hook("post_write", {"path": str(safe), "size": len(content)})
        result = {"path": rel, "size": len(content), "status": "written"}
        if warn:
            result["warning"] = warn
        result.update(_build_change(
            rel, old_text if existed else None, content,
            "modified" if existed else "added",
        ))
        result["_existed"] = existed
        if snap is not None:
            result["_snapshot"] = str(snap)
        return result
    except Exception as e:
        return {"error": f"Failed to write file: {e}"}


@tool(name="file_delete",
      description="Delete a file or empty directory. Moved to trash for recovery when possible.",
      security_profile=SecurityProfile.FILE_DELETE,
      path_param="path",
      compress_strategy=CompressStrategy.DEFAULT,
      suggested_category=SuggestedCategory.DANGEROUS,
      parameters=[
          ToolParameter(name="path", type="string", required=True,
                        description="File or directory path (workspace-relative ok)"),
      ])
async def file_delete(ctx, path: str) -> Dict:
    safe, _warn = _safe_path(ctx, path)
    if not safe:
        return {"error": f"Path '{path}' is outside session workspace"}
    if not safe.exists():
        return {"error": f"Path not found: {path}"}

    is_dir = safe.is_dir()
    if is_dir:
        # Only allow deleting empty directories
        try:
            contents = list(safe.iterdir())
            if contents:
                return {
                    "error": f"Directory not empty ({len(contents)} items). "
                             f"Delete files individually or use shell_execute to remove recursively.",
                }
        except PermissionError:
            return {"error": f"Permission denied reading directory: {path}"}

    rel = _rel_path(ctx, safe)
    # Capture prior content (text files only) so the change diff can show what
    # was removed. Directories / binary / oversized files yield no diff body.
    old_text: Optional[str] = None
    if not is_dir and not _is_binary_path(safe):
        try:
            if safe.stat().st_size <= MAX_FILE_SIZE:
                old_text, _enc = _read_file_text(safe)
        except Exception:
            old_text = None

    try:
        from boarai.core.trash import get_trash_manager
        tm = get_trash_manager()
        trash_id = None
        if tm:
            trash_id = await tm.trash_file(safe)
        if trash_id:
            logger.info(f"Moved to trash: {rel} (trash_id={trash_id})")
            result = {"path": rel, "status": "trashed", "trash_id": trash_id,
                      "type": "directory" if is_dir else "file"}
        else:
            # Fallback: hard delete
            if is_dir:
                safe.rmdir()
            else:
                safe.unlink()
            logger.warning(f"Hard deleted (trash unavailable): {rel}")
            result = {"path": rel, "status": "deleted",
                      "type": "directory" if is_dir else "file"}
        result.update(_build_change(rel, old_text, None, "deleted"))
        result["_existed"] = True
        if trash_id:
            result["_trash_id"] = trash_id
        return result
    except Exception as e:
        return {"error": f"Failed to delete: {e}"}


# ---- file_glob: filename pattern search ----

@tool(name="file_glob",
      description="Find files by glob pattern ('**/*.py', 'src/**/*.ts'); newest first. "
                  "Skips .git/node_modules/__pycache__ and binary files.",
      security_profile=SecurityProfile.FILE_READ,
      path_param="path",
      compress_strategy=CompressStrategy.LIST,
      suggested_category=SuggestedCategory.READONLY,
      parameters=[
          ToolParameter(name="pattern", type="string", required=True,
                        description="Glob pattern (relative to the session workspace)"),
          ToolParameter(name="path", type="string", required=False, default=".",
                        description="Subdirectory to search in"),
          ToolParameter(name="max_results", type="integer", required=False, default=50,
                        description="Max number of results to return"),
      ])
async def file_glob(ctx, pattern: str, path: str = ".", max_results: int = 50) -> Dict:
    # Default search root = session workspace (fallback project_root), matching
    # file_read / file_write / shell_execute. The old code hardcoded
    # project_root, so a workspace-scoped session's `file_glob "*"` leaked the
    # entire install root and contradicted `dir /b` (empty workspace).
    root = _workspace_root(ctx)
    if not root:
        return {"error": "Session workspace not set"}
    search_dir = root if path == "." else (root / path)
    if not search_dir.exists() or not search_dir.is_dir():
        return {"error": f"Directory not found: {path}"}

    results: List[Dict[str, Any]] = []
    try:
        for f in search_dir.rglob(pattern):
            if len(results) >= max_results:
                break
            # Skip excluded directories
            parts = f.relative_to(search_dir).parts
            if _should_exclude_dir(parts):
                continue
            if f.is_file() and not _is_binary_path(f):
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    mtime = 0
                results.append({"path": _rel_path(ctx, f), "mtime": mtime})

        if not results and "*" in pattern:
            # Simple pattern like "*.py" → auto-expand to recursive
            expanded = f"**/{pattern}"
            for f in search_dir.rglob(expanded):
                if len(results) >= max_results:
                    break
                parts = f.relative_to(search_dir).parts
                if _should_exclude_dir(parts):
                    continue
                if f.is_file() and not _is_binary_path(f):
                    try:
                        mtime = f.stat().st_mtime
                    except OSError:
                        mtime = 0
                    results.append({"path": _rel_path(ctx, f), "mtime": mtime})

        # Sort by mtime descending
        results.sort(key=lambda r: r["mtime"], reverse=True)

        # Strip mtime from output (just keep paths sorted)
        return {
            "results": [{"path": r["path"]} for r in results],
            "count": len(results),
        }
    except Exception as e:
        return {"error": f"Glob failed: {e}"}


# ---- file_grep: content search with context ----

@tool(name="file_grep",
      description="Search file contents with regex; returns file, line number, and "
                  "matching lines (optional context). Uses bounded regex features "
                  "to keep searches responsive. Skips binaries and .git/node_modules.",
      security_profile=SecurityProfile.FILE_READ,
      path_param="path",
      compress_strategy=CompressStrategy.GREP,
      suggested_category=SuggestedCategory.READONLY,
      parameters=[
          ToolParameter(name="pattern", type="string", required=True,
                        description="Regex pattern (bounded repetition; no backreferences/lookaround)"),
          ToolParameter(name="path", type="string", required=False, default=".",
                        description="Subdirectory or file to search in"),
          ToolParameter(name="glob", type="string", required=False, default="*",
                        description="File glob pattern to filter (e.g., '*.py', '*.{js,ts}')"),
          ToolParameter(name="max_results", type="integer", required=False, default=30,
                        description="Max number of matching lines to return"),
          ToolParameter(name="context_lines", type="integer", required=False, default=0,
                        description="Lines of context to show before and after each match"),
          ToolParameter(name="case_sensitive", type="boolean", required=False, default=False,
                        description="If true, match case-sensitively"),
      ])
async def file_grep(ctx, pattern: str, path: str = ".", glob: str = "*",
                    max_results: int = 30, context_lines: int = 0,
                    case_sensitive: bool = False) -> Dict:
    # Default search root = session workspace (fallback project_root), matching
    # file_glob / file_read. Prevents workspace-scoped content search from
    # silently sweeping the whole install root.
    root = _workspace_root(ctx)
    if not root:
        return {"error": "Session workspace not set"}

    raw_search_path = Path(path) if Path(path).is_absolute() else (root / path)
    boundary = _lexical_boundary(ctx, raw_search_path)
    if boundary and _has_symlink_below(boundary, raw_search_path):
        return {"error": "Refusing to search through a symbolic link"}
    search_path, _warning = _safe_path(ctx, path)
    if search_path is None:
        return {"error": f"Path is outside the allowed workspace: {path}"}
    if not search_path.exists():
        return {"error": f"Path not found: {path}"}

    try:
        max_results = min(max(int(max_results), 1), 100)
        context_lines = min(max(int(context_lines), 0), 5)
    except (TypeError, ValueError):
        max_results, context_lines = 30, 0

    flags = 0 if case_sensitive else re.IGNORECASE
    if len(str(glob)) > 256:
        return {"error": "File glob exceeds the 256-character limit"}
    if len(str(pattern)) > 512:
        return {"error": "Regex pattern exceeds the 512-character limit"}
    pattern_error = _validate_grep_pattern(str(pattern))
    if pattern_error:
        return {"error": pattern_error}
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return {"error": f"Invalid regex pattern: {e}"}

    results: List[Dict[str, Any]] = []

    try:
        scanned_files = 0
        scanned_bytes = 0
        scanned_dirs = 0
        scan_limited = False

        def _iter_files():
            nonlocal scanned_dirs, scan_limited
            if search_path.is_file():
                yield search_path
                return
            for directory, dirnames, filenames in os.walk(
                search_path, followlinks=False
            ):
                scanned_dirs += 1
                if scanned_dirs > MAX_GREP_DIRS:
                    scan_limited = True
                    return
                dirnames[:] = [
                    name for name in dirnames
                    if name not in EXCLUDED_DIRS
                    and not _is_link_or_junction(Path(directory) / name)
                ]
                for filename in filenames:
                    candidate = Path(directory) / filename
                    if fnmatch.fnmatch(candidate.relative_to(search_path).as_posix(), glob):
                        yield candidate

        for f in _iter_files():
            if len(results) >= max_results:
                scan_limited = True
                break
            if scanned_files >= MAX_GREP_FILES or scanned_bytes >= MAX_GREP_TOTAL_BYTES:
                scan_limited = True
                break
            try:
                scanned_files += 1
                if _is_link_or_junction(f):
                    continue
                parts = f.relative_to(search_path).parts
                if _should_exclude_dir(parts) or _is_binary_path(f) or not f.is_file():
                    continue
                if _has_symlink_below(search_path, f):
                    continue
                size = f.stat().st_size
                if size > MAX_FILE_SIZE or scanned_bytes + size > MAX_GREP_TOTAL_BYTES:
                    continue
                def _read_bounded(candidate: Path = f) -> bytes:
                    with candidate.open("rb") as handle:
                        return handle.read(MAX_FILE_SIZE + 1)
                raw = await asyncio.to_thread(_read_bounded)
                scanned_bytes += len(raw)
                if len(raw) > MAX_FILE_SIZE:
                    continue
                if b"\x00" in raw[:8192]:
                    continue  # Skip binary
            except Exception:
                continue
            matches = await asyncio.to_thread(
                _grep_decoded_file,
                raw,
                _rel_path(ctx, f),
                regex,
                max_results - len(results),
                context_lines,
            )
            results.extend(matches)

        return {"results": results, "count": len(results),
                "truncated": scan_limited or len(results) >= max_results}
    except Exception as e:
        return {"error": f"Grep failed: {e}"}


# ---- file_edit: precise string-replacement editing ----

@tool(name="file_edit",
      description="Edit a file by replacing old_string with new_string — preferred over "
                  "rewriting via file_write. Read the file first so old_string matches "
                  "exactly (fuzzy fallback handles minor whitespace). old_string must be "
                  "unique; if it repeats, add surrounding context or set replace_all.",
      security_profile=SecurityProfile.FILE_WRITE,
      path_param="path",
      compress_strategy=CompressStrategy.DEFAULT,
      suggested_category=SuggestedCategory.SAFE,
      parameters=[
          ToolParameter(name="path", type="string", required=True,
                        description="File path (workspace-relative ok)"),
          ToolParameter(name="old_string", type="string", required=True,
                        description="The exact string to find and replace"),
          ToolParameter(name="new_string", type="string", required=True,
                        description="The new string to replace the old string with"),
          ToolParameter(name="replace_all", type="boolean", required=False, default=False,
                        description="If true, replace all occurrences. Default: replace only first"),
      ])
async def file_edit(ctx, path: str, old_string: str, new_string: str,
                    replace_all: bool = False) -> Dict:
    """Precise string replacement edit with fuzzy matching fallback."""
    safe, _warn = _safe_path(ctx, path)
    if not safe:
        return {"error": f"Path '{path}' is outside session workspace"}
    nested_err = _nested_boarai_error(ctx, safe)
    if nested_err:
        return {"error": nested_err}
    if not safe.exists():
        return {"error": f"File not found: {path}"}
    if not safe.is_file():
        return {"error": f"Not a file: {path}"}

    try:
        content, _ = _read_file_text(safe)

        match_result = _find_match(content, old_string)
        if not match_result:
            return {"error": _build_not_found_error(content, old_string, _rel_path(ctx, safe))}

        actual_old, occurrences = match_result

        # Multi-occurrence: require explicit replace_all
        if not replace_all and occurrences > 1:
            return {
                "error": (
                    f"String appears {occurrences} times in the file. "
                    f"Use replace_all: true to replace all occurrences, "
                    f"or provide a more specific string that appears only once."
                ),
            }

        # v2 hook: pre_write (blockable) — other extensions may veto/transform
        hook_res = await _trigger_fs_hook("pre_write", {"path": str(safe), "content": new_string})
        if hook_res.get("blocked"):
            return {"error": f"Edit blocked by extension hook: {hook_res.get('reason', 'vetoed')}"}
        new_string = hook_res.get("content", new_string)

        # Snapshot before modification (revert source)
        snap = await _snapshot_file(safe)

        # Detect and preserve line endings
        newline = _detect_line_ending(content)

        # Perform replacement
        if replace_all:
            new_content = content.replace(actual_old, new_string)
        else:
            new_content = content.replace(actual_old, new_string, 1)

        safe.write_text(new_content, encoding="utf-8", newline=newline)
        rel = _rel_path(ctx, safe)
        replaced_count = occurrences if replace_all else 1
        logger.info(f"Edit {rel}: {replaced_count} replacement(s)")
        # v2 hook: post_write (observe)
        await _trigger_fs_hook("post_write", {"path": str(safe), "size": len(new_content)})
        result = {
            "path": rel,
            "replacements": replaced_count,
            "status": "edited",
        }
        result.update(_build_change(rel, content, new_content, "modified"))
        result["_existed"] = True
        if snap is not None:
            result["_snapshot"] = str(snap)
        return result
    except Exception as e:
        return {"error": f"Failed to edit file: {e}"}


# ---- file_search (backward-compatible, delegates to glob + grep) ----

@tool(name="file_search",
      description="[Legacy] Combined file search: tries glob pattern first, "
                  "falls back to content search. Prefer file_glob or file_grep for clarity.",
      llm_visible=False,
      security_profile=SecurityProfile.FILE_READ,
      path_param="path",
      compress_strategy=CompressStrategy.DEFAULT,
      suggested_category=SuggestedCategory.READONLY,
      parameters=[
          ToolParameter(name="pattern", type="string", required=True,
                        description="Glob or text pattern"),
          ToolParameter(name="path", type="string", required=False, default=".",
                        description="Subdirectory to search"),
          ToolParameter(name="max_results", type="integer", required=False, default=20,
                        description="Max results"),
      ])
async def file_search(ctx, pattern: str, path: str = ".", max_results: int = 20) -> Dict:
    """Backward-compatible combined search. Delegates to glob first, then grep."""
    # Try glob first
    glob_result = await file_glob(ctx, pattern=pattern, path=path, max_results=max_results)
    if not glob_result.get("error") and glob_result.get("count", 0) > 0:
        return {"results": glob_result["results"], "count": glob_result["count"],
                "mode": "glob"}

    # Fall back to content grep
    grep_result = await file_grep(ctx, pattern=pattern, path=path, max_results=max_results)
    if not grep_result.get("error"):
        grep_results = grep_result.get("results", [])
        file_set: List[str] = []
        seen: Set[str] = set()
        for r in grep_results:
            fpath = r["file"]
            if fpath not in seen:
                seen.add(fpath)
                file_set.append({"path": fpath, "type": "file"})
        return {"results": file_set, "count": len(file_set), "mode": "grep"}

    return {"results": [], "count": 0}


# ---- file_move: move / rename files and directories ----

@tool(name="file_move",
      description="Move or rename a file/directory (prefer over shell 'mv': snapshots "
                  "the source first for recovery). Auto-creates destination parents; "
                  "refuses to overwrite an existing destination.",
      security_profile=SecurityProfile.FILE_WRITE,
      path_param="source",
      compress_strategy=CompressStrategy.DEFAULT,
      suggested_category=SuggestedCategory.DANGEROUS,
      parameters=[
          ToolParameter(name="source", type="string", required=True,
                        description="Path to move (workspace-relative ok)"),
          ToolParameter(name="destination", type="string", required=True,
                        description="Destination path"),
      ])
async def file_move(ctx, source: str, destination: str) -> Dict:
    """Move or rename a file/directory."""
    src, _warn_src = _safe_path(ctx, source)
    if not src:
        return {"error": f"Source path '{source}' is outside session workspace"}
    if not src.exists():
        return {"error": f"Source not found: {source}"}

    dst, _warn_dst = _safe_path(ctx, destination)
    if not dst:
        return {"error": f"Destination path '{destination}' is outside session workspace"}
    nested_err = _nested_boarai_error(ctx, dst)
    if nested_err:
        return {"error": nested_err}
    if _is_same_path(src, dst):
        return {"error": f"Source and destination are the same path: {destination}"}
    if dst.exists():
        return {"error": f"Destination already exists: {destination} (refusing to overwrite)"}

    # Refuse to move a directory into its own subtree.
    if src.is_dir() and _is_within(src, dst):
        return {"error": f"Cannot move directory into its own subtree: {source} -> {destination}"}

    is_dir = src.is_dir()
    try:
        # Snapshot source before moving so trash_restore / snapshots can roll back.
        if not is_dir:
            await _snapshot_file(src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            lambda: shutil.move(str(src), str(dst)),
        )
        rel = _rel_path(ctx, dst)
        logger.info(f"Moved {source} -> {destination} ({'directory' if is_dir else 'file'})")
        return {
            "path": rel,
            "from": _rel_path(ctx, src),
            "status": "moved",
            "type": "directory" if is_dir else "file",
        }
    except Exception as e:
        return {"error": f"Failed to move: {e}"}


# ---- file_copy: copy files and directories ----

@tool(name="file_copy",
      description="Copy a file or directory (recursive). Auto-creates destination "
                  "parents; refuses to overwrite an existing destination.",
      security_profile=SecurityProfile.FILE_WRITE,
      path_param="source",
      compress_strategy=CompressStrategy.DEFAULT,
      suggested_category=SuggestedCategory.SAFE,
      parameters=[
          ToolParameter(name="source", type="string", required=True,
                        description="Path to copy (workspace-relative ok)"),
          ToolParameter(name="destination", type="string", required=True,
                        description="Destination path"),
      ])
async def file_copy(ctx, source: str, destination: str) -> Dict:
    """Copy a file or directory (recursively)."""
    workspace = _workspace_root(ctx)
    raw_source = (
        Path(source) if Path(source).is_absolute()
        else ((workspace / source) if workspace else Path(source))
    )
    boundary = _lexical_boundary(ctx, raw_source) if workspace else None
    if boundary and _has_symlink_below(boundary, raw_source):
        return {"error": "Refusing to copy through a symbolic-link path"}
    src, _warn_src = _safe_path(ctx, source)
    if not src:
        return {"error": f"Source path '{source}' is outside session workspace"}
    if not src.exists():
        return {"error": f"Source not found: {source}"}

    dst, _warn_dst = _safe_path(ctx, destination)
    if not dst:
        return {"error": f"Destination path '{destination}' is outside session workspace"}
    nested_err = _nested_boarai_error(ctx, dst)
    if nested_err:
        return {"error": nested_err}
    if _is_same_path(src, dst):
        return {"error": f"Source and destination are the same path: {destination}"}
    if dst.exists():
        return {"error": f"Destination already exists: {destination} (refusing to overwrite)"}

    # Refuse to copy a directory into its own subtree.
    if src.is_dir() and _is_within(src, dst):
        return {"error": f"Cannot copy directory into its own subtree: {source} -> {destination}"}

    is_dir = src.is_dir()
    try:
        if is_dir:
            def _validate_copy_tree() -> Optional[str]:
                directories = 0
                files = 0
                total_bytes = 0
                for directory, dirnames, filenames in os.walk(src, followlinks=False):
                    if any(_is_link_or_junction(Path(directory) / name)
                           for name in dirnames + filenames):
                        return "Refusing to copy a directory containing symbolic links or junctions"
                    directories += 1
                    files += len(filenames)
                    if directories > MAX_COPY_DIRS or files > MAX_COPY_FILES:
                        return (
                            f"Directory copy exceeds the {MAX_COPY_DIRS}-directory or "
                            f"{MAX_COPY_FILES}-file limit"
                        )
                    for filename in filenames:
                        try:
                            total_bytes += (Path(directory) / filename).stat().st_size
                        except OSError:
                            return "Could not inspect every file in the source directory"
                        if total_bytes > MAX_COPY_TOTAL_BYTES:
                            return "Directory copy exceeds the 512 MiB size limit"
                return None

            copy_error = await asyncio.to_thread(_validate_copy_tree)
            if copy_error:
                return {"error": copy_error}
        else:
            source_size = await asyncio.to_thread(lambda: src.stat().st_size)
            if source_size > MAX_COPY_TOTAL_BYTES:
                return {"error": "File copy exceeds the 512 MiB size limit"}
        dst.parent.mkdir(parents=True, exist_ok=True)
        if is_dir:
            await asyncio.to_thread(
                lambda: shutil.copytree(str(src), str(dst), symlinks=False),
            )
        else:
            await asyncio.to_thread(
                lambda: shutil.copy2(str(src), str(dst)),
            )
        rel = _rel_path(ctx, dst)
        logger.info(f"Copied {source} -> {destination} ({'directory' if is_dir else 'file'})")
        return {
            "path": rel,
            "from": _rel_path(ctx, src),
            "status": "copied",
            "type": "directory" if is_dir else "file",
        }
    except Exception as e:
        return {"error": f"Failed to copy: {e}"}


# ---- trash_list: list trash entries ----

@tool(name="trash_list",
      description="List entries in the recycle bin (trash). Includes files deleted "
                  "via file_delete, trashed sessions/agents, and soft-deleted memory "
                  "entries. Use with trash_restore to recover items.",
      llm_visible=False,
      security_profile=SecurityProfile.NONE,
      compress_strategy=CompressStrategy.LIST,
      suggested_category=SuggestedCategory.READONLY,
      parameters=[
          ToolParameter(name="item_type", type="string", required=False, default="",
                        description="Filter by type: 'file', 'session', 'agent', "
                                    "'memory', or '' for all"),
          ToolParameter(name="limit", type="integer", required=False, default=30,
                        description="Max number of entries to return (max 200)"),
      ])
async def trash_list(ctx, item_type: str = "", limit: int = 30) -> Dict:
    """List trash entries, newest first."""
    try:
        from boarai.core.trash import get_trash_manager
        tm = get_trash_manager()
        if tm is None:
            return {"error": "Trash manager unavailable"}
        item_type = item_type.strip().lower() or None
        if item_type not in (None, "file", "session", "agent", "memory"):
            return {"error": f"Invalid item_type: {item_type} "
                             f"(expected '', 'file', 'session', 'agent', or 'memory')"}
        entries = tm.list_trash(item_type=item_type, limit=max(1, min(int(limit), 200)))
        return {
            "count": len(entries),
            "entries": entries,
        }
    except Exception as e:
        return {"error": f"Failed to list trash: {e}"}


# ---- trash_restore: restore a trashed item ----

@tool(name="trash_restore",
      description="Restore a previously trashed file, session, agent, or memory "
                  "entry back to its original location. The original path must be "
                  "free (if something else now occupies it, restore fails). "
                  "Find the trash_id via trash_list.",
      llm_visible=False,
      security_profile=SecurityProfile.FILE_WRITE,
      compress_strategy=CompressStrategy.DEFAULT,
      suggested_category=SuggestedCategory.DANGEROUS,
      parameters=[
          ToolParameter(name="trash_id", type="string", required=True,
                        description="The trash entry id (from trash_list)"),
      ])
async def trash_restore(ctx, trash_id: str) -> Dict:
    """Restore a trashed item to its original location."""
    try:
        from boarai.core.trash import get_trash_manager
        tm = get_trash_manager()
        if tm is None:
            return {"error": "Trash manager unavailable"}

        entry = tm.get_trash(trash_id)
        if not entry:
            return {"error": f"Trash entry not found: {trash_id}"}

        if entry.get("type") == "memory":
            # Memory entries are JSON payloads restored by the core memory
            # manager. Extensions must not import core.memory (whitelist
            # boundary) — restore via the trash API or WebUI data page instead.
            return {"error": "Memory entries cannot be restored by this tool "
                             "(whitelist boundary). Use the WebUI data page or "
                             f"POST /api/trash/{trash_id}/restore."}

        # Reset distill mode so restored sessions/agents aren't distilled again.
        await tm.reset_distill_mode(trash_id)
        ok = await tm.restore(trash_id)
        if not ok:
            return {"error": f"Restore failed for {trash_id} "
                             f"(original path may already exist or item may be missing)"}
        return {
            "status": "restored",
            "trash_id": trash_id,
            "type": entry.get("type", ""),
            "original_path": entry.get("original_path", ""),
        }
    except Exception as e:
        return {"error": f"Failed to restore: {e}"}


# ---- trash_manager: aggregate (the ONLY LLM-facing trash tool) ----

@tool(name="trash_manager",
      description=(
          "Recycle bin: list trashed items (files, sessions, agents) or restore "
          "one to its original location (fails if that path is now occupied). "
          "Use action='list' first to find the trash_id."
      ),
      security_profile=SecurityProfile.FILE_WRITE,
      compress_strategy=CompressStrategy.DEFAULT,
      suggested_category=SuggestedCategory.DANGEROUS,
      parameters=[
          ToolParameter(name="action", type="string", required=True,
                        enum=["list", "restore"],
                        description="list entries or restore one"),
          ToolParameter(name="trash_id", type="string", required=False,
                        description="Entry id to restore (from list)"),
          ToolParameter(name="item_type", type="string", required=False, default="",
                        description="Filter: 'file', 'session', 'agent', or '' for all"),
          ToolParameter(name="limit", type="integer", required=False, default=30,
                        description="Max entries to list"),
      ])
async def trash_manager(ctx, **kwargs) -> Dict:
    """Aggregate dispatcher — routes to trash_list / trash_restore."""
    action = str(kwargs.pop("action", "") or "").strip()
    if not action:
        return {"error": "action is required"}
    if action == "list":
        return await trash_list(
            ctx,
            item_type=str(kwargs.get("item_type", "")),
            limit=int(kwargs.get("limit", 30)),
        )
    if action == "restore":
        trash_id = str(kwargs.get("trash_id", "") or "").strip()
        if not trash_id:
            return {"error": "trash_id is required for restore"}
        return await trash_restore(ctx, trash_id)
    return {"error": f"Unknown action: {action}"}
