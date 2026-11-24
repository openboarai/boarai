"""
Session attachment helpers: storage layout, filename safety, and LLM
downscaling injection.

Attachments live in each session folder::

    .boarai/sessions/{session_id}/files/{stored_name}

Disk history only stores *reference metadata* (name / stored_name / size /
mime_type / kind) inside ``SessionMessage.metadata["attachments"]`` — raw
bytes are never embedded in the session JSON. When a run is assembled, the
executor calls :func:`build_attachment_messages` to downscale attachments into
LLM-consumable form in memory (image → data URL block, text → embedded
content, other → path note). The injected form is never written back to disk,
so prompt-cache stability and session file size stay intact.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# --- Limits ---

MAX_FILE_BYTES = 32 * 1024 * 1024          # 32 MB per uploaded file
MAX_IMAGE_BYTES = 5 * 1024 * 1024          # 5 MB inline vision cap
MAX_IMAGE_EDGE = 8000                      # long-edge cap (Anthropic hard limit)
MAX_IMAGE_PIXELS = 20_000_000              # total pixel cap (gateway-safe)
MAX_TEXT_EMBED_BYTES = 64 * 1024           # 64 KB text inline embed cap
TEXT_EXTENSIONS = frozenset({
    ".md", ".markdown", ".txt", ".log", ".csv", ".json", ".yaml", ".yml",
    ".py", ".js", ".ts", ".tsx", ".rb", ".go", ".rs", ".java", ".c", ".cpp",
    ".h", ".hpp", ".cs", ".php", ".sh", ".bat", ".ps1", ".toml", ".ini",
    ".cfg", ".conf", ".env", ".sql", ".html", ".css", ".xml", ".svg",
    ".ini", ".properties",
})
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})

# Characters unsafe on common filesystems: / \ : * ? " < > | NUL
_UNSAFE_NAME_CHARS = re.compile(r'[\/\\:\*?"<>|\x00-\x1f]')

# --- Public API ---


def sanitize_filename(name: str) -> str:
    """Sanitize an uploaded filename for safe storage on any filesystem."""
    base = _UNSAFE_NAME_CHARS.sub("_", str(name or "")).strip().strip(".")
    if not base or base.strip("_-. ") == "":
        return "upload"
    return base


def detect_kind(name: str, mime_type: Optional[str] = None) -> str:
    """Classify an attachment for LLM downscaling: image / text / other."""
    ext = Path(name or "").suffix.lower()
    if ext in IMAGE_EXTENSIONS or (mime_type or "").startswith("image/"):
        return "image"
    if ext in TEXT_EXTENSIONS or (mime_type or "").startswith("text/"):
        return "text"
    return "other"


def detect_mime_type(name: str, head: bytes) -> str:
    """Detect MIME type from magic bytes (images) then extension fallback."""
    if head:
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if head.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
            return "image/gif"
        if head.startswith(b"RIFF") and len(head) >= 12 and head[8:12] == b"WEBP":
            return "image/webp"
        if head.startswith(b"BM"):
            return "image/bmp"
    guessed = mimetypes.guess_type(name or "")[0]
    return guessed or "application/octet-stream"


def parse_image_dimensions(head: bytes) -> Optional[Tuple[int, int]]:
    """``(width, height)`` from image header magic; None when unparseable."""
    if len(head) >= 24 and head.startswith(b"\x89PNG\r\n\x1a\n"):
        width = int.from_bytes(head[16:20], "big")
        height = int.from_bytes(head[20:24], "big")
        return (width, height) if width and height else None
    if len(head) >= 10 and head[:6] in (b"GIF87a", b"GIF89a"):
        width = int.from_bytes(head[6:8], "little")
        height = int.from_bytes(head[8:10], "little")
        return (width, height) if width and height else None
    if len(head) >= 30 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        fourcc = head[12:16]
        if fourcc == b"VP8X":
            width = 1 + int.from_bytes(head[24:27], "little")
            height = 1 + int.from_bytes(head[27:30], "little")
            return (width, height) if width and height else None
        if fourcc == b"VP8L":
            bits = int.from_bytes(head[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if fourcc == b"VP8 " and head[23:26] == b"\x9d\x01\x2a":
            width = int.from_bytes(head[26:28], "little") & 0x3FFF
            height = int.from_bytes(head[28:30], "little") & 0x3FFF
            return (width, height) if width and height else None
        return None
    if len(head) >= 26 and head[:2] == b"BM":
        # BMP info header: width/height are int32 LE at 18/22; height is
        # negative for top-down bitmaps (row order, not a size).
        width = int.from_bytes(head[18:22], "little", signed=True)
        height = int.from_bytes(head[22:26], "little", signed=True)
        width, height = abs(width), abs(height)
        return (width, height) if width and height else None
    if len(head) >= 4 and head[:2] == b"\xff\xd8":
        i = 2
        while i + 9 <= len(head):
            if head[i] != 0xFF:
                i += 1
                continue
            marker = head[i + 1]
            if marker == 0xFF:
                # Fill-byte padding before the real marker.
                i += 1
                continue
            if marker == 0xD8 or 0xD0 <= marker <= 0xD7 or marker == 0x01:
                i += 2
                continue
            length = int.from_bytes(head[i + 2:i + 4], "big")
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height = int.from_bytes(head[i + 5:i + 7], "big")
                width = int.from_bytes(head[i + 7:i + 9], "big")
                return (width, height) if width and height else None
            if length < 2:
                return None
            i += 2 + length
        return None
    return None


def image_exceeds_limits(dimensions: Optional[Tuple[int, int]]) -> bool:
    """True when parsed dimensions break the inline-vision gate."""
    if not dimensions:
        return False
    width, height = dimensions
    return max(width, height) > MAX_IMAGE_EDGE or width * height > MAX_IMAGE_PIXELS


def get_files_dir(session_id: str) -> Path:
    """Return the attachment directory for a session (created on demand)."""
    from boarai.core.session.manager import get_session_manager

    sm = get_session_manager()
    session_dir = sm._storage.get_session_dir(session_id)  # noqa: SLF001
    files_dir = session_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    return files_dir


def resolve_attachment_path(session_id: str, stored_name: str) -> Optional[Path]:
    """Resolve a stored attachment path with traversal protection."""
    from boarai.core.session.manager import get_session_manager

    sm = get_session_manager()
    session_dir = sm._storage.get_session_dir(session_id)  # noqa: SLF001
    if not session_dir.is_dir():
        return None
    files_dir = session_dir / "files"
    safe = sanitize_filename(stored_name)
    candidate = (files_dir / safe).resolve()
    try:
        candidate.relative_to(files_dir.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def allocate_stored_name(files_dir: Path, name: str) -> str:
    """Pick a non-colliding stored filename (name, name_1, name_2, ...)."""
    safe = sanitize_filename(name)
    stem = Path(safe).stem
    suffix = Path(safe).suffix
    candidate = safe
    counter = 1
    while (files_dir / candidate).exists():
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


async def validate_attachments(session_id: str, attachments: Any) -> List[Dict[str, Any]]:
    """Validate and normalize incoming attachment references."""
    if not attachments:
        return []
    if not isinstance(attachments, list):
        raise ValueError("attachments must be a list")

    normalized: List[Dict[str, Any]] = []
    for idx, att in enumerate(attachments):
        if not isinstance(att, dict):
            raise ValueError(f"attachment #{idx + 1} must be an object")
        stored_name = att.get("stored_name") or ""
        name = att.get("name") or stored_name
        if not stored_name or not sanitize_filename(stored_name):
            raise ValueError(f"attachment #{idx + 1} is missing stored_name")
        path = resolve_attachment_path(session_id, stored_name)
        if path is None:
            raise ValueError(f"attachment file not found: {name}")
        size = int(att.get("size") or 0) or path.stat().st_size
        mime_type = att.get("mime_type") or detect_mime_type(name, b"")
        normalized.append({
            "name": name,
            "stored_name": stored_name,
            "size": size,
            "mime_type": mime_type,
            "kind": att.get("kind") or detect_kind(name, mime_type),
        })
    return normalized


async def build_attachment_messages(
    content: str,
    attachments: List[Dict[str, Any]],
    files_dir: Optional[Path] = None,
    image_disposition: str = "inline",
    inline_images: Optional[Set[str]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Downscale attachment references into LLM-consumable form (in memory)."""
    if not attachments:
        return content, []

    notes: List[str] = []
    extra_messages: List[Dict[str, Any]] = []

    for att in attachments:
        stored_name = sanitize_filename(att.get("stored_name") or "")
        name = att.get("name") or stored_name
        if not stored_name:
            continue
        path = None
        if files_dir is not None:
            candidate = (files_dir / stored_name).resolve()
            try:
                candidate.relative_to(files_dir.resolve())
                if candidate.is_file():
                    path = candidate
            except ValueError:
                path = None
        if path is None:
            notes.append(
                f"[Attachment: {name} — file not found, ask the user to re-upload]"
            )
            continue

        size = att.get("size") or path.stat().st_size
        kind = att.get("kind") or detect_kind(name, att.get("mime_type"))

        if kind == "image":
            if image_disposition == "omit":
                notes.append(
                    f"[Image: {name} ({_fmt_size(size)}) — omitted: the active "
                    f"model does not support vision; readable at {path}]"
                )
                continue
            if inline_images is not None and stored_name not in inline_images:
                # Outside the replay window / byte budget: degrade to the
                # cached sidecar description (near-free) or a path note.
                desc = await sidecar_describe(path)
                if desc:
                    notes.append(
                        f"[Image: {name} ({_fmt_size(size)}) — outside the "
                        f"replay window; vision description:\n{desc}\n[/Image]"
                    )
                else:
                    notes.append(
                        f"[Image: {name} ({_fmt_size(size)}) — outside the "
                        f"replay window; readable at {path}]"
                    )
                continue
            data_url, reason = await _image_to_data_url(path, name, size)
            if data_url is None and reason == "oversize":
                desc = await sidecar_describe(path)
                if desc:
                    notes.append(
                        f"[Image: {name} ({_fmt_size(size)}) — over inline "
                        f"vision limits; vision description:\n{desc}\n[/Image]"
                    )
                    continue
            if data_url is None:
                notes.append(
                    f"[Attachment: {name} ({_fmt_size(size)}) — image too large "
                    f"for inline vision; readable at {path}]"
                )
            else:
                extra_messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"[Image attachment: {name}]"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                })
            continue

        if kind == "text" and size <= MAX_TEXT_EMBED_BYTES:
            try:
                text = await asyncio.to_thread(_read_text, path, MAX_TEXT_EMBED_BYTES)
                notes.append(
                    f"[Attachment: {name} ({_fmt_size(size)})]\n{text}\n[/Attachment]"
                )
            except (OSError, UnicodeDecodeError) as e:
                logger.warning("Attachment text read failed %s: %s", path, e)
                notes.append(
                    f"[Attachment: {name} ({_fmt_size(size)}) — readable at {path}]"
                )
            continue

        notes.append(
            f"[Attachment: {name} ({_fmt_size(size)}) — saved to session file "
            f"directory; you may read it with your file tools: {path}]"
        )

    new_content = content
    if notes:
        suffix = "\n\n" + "\n\n".join(notes)
        new_content = content + suffix if content else suffix.strip()
    return new_content, extra_messages


# --- Private helpers ---


def _fmt_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


async def _image_to_data_url(
    path: Path, name: str, size: int
) -> Tuple[Optional[str], str]:
    """Read an image and return ``(data_url, reason)``."""
    if size > MAX_IMAGE_BYTES:
        return None, "oversize"
    try:
        data = await asyncio.to_thread(path.read_bytes)
    except OSError as e:
        logger.warning("Attachment image read failed %s: %s", path, e)
        return None, "read_failed"
    if len(data) > MAX_IMAGE_BYTES:
        return None, "oversize"
    mime = detect_mime_type(name, data[:12])
    if not mime.startswith("image/"):
        return None, "not_image"
    if image_exceeds_limits(parse_image_dimensions(data[:4096])):
        return None, "oversize"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}", ""


def plan_inline_images(
    attachment_lists: List[List[Dict[str, Any]]],
    window: int,
    max_bytes: int,
) -> Set[str]:
    """stored_names that receive pixel re-injection on this run."""
    ordered: List[Tuple[str, int]] = []
    for atts in attachment_lists or []:
        for att in atts or []:
            if not isinstance(att, dict):
                continue
            if (att.get("kind") or "") != "image":
                continue
            stored = att.get("stored_name") or ""
            if stored:
                ordered.append((stored, int(att.get("size") or 0)))
    selected: Set[str] = set()
    if window <= 0 or max_bytes <= 0:
        return selected
    total = 0
    for stored, size in reversed(ordered):
        if len(selected) >= window:
            break
        if size > max_bytes - total:
            continue
        selected.add(stored)
        total += size
    return selected


async def sidecar_describe(path: Path) -> Optional[str]:
    """Cached vision-slot description for an image the inline gate refused."""
    try:
        from boarai.core.session.run.model_resolver import get_vision_capability
        from boarai.core.session.run.vision_sidecar import describe_image

        cap = get_vision_capability()
        if cap is None:
            return None
        return await describe_image(path, f"{cap[0]}/{cap[1]}")
    except Exception as e:  # noqa: BLE001 - degrade to the path note
        logger.warning("Oversize image sidecar description failed %s: %s", path, e)
        return None


def _read_text(path: Path, limit: int) -> str:
    """Read text bytes with UTF-8 fallback to latin-1, truncated to limit."""
    data = path.read_bytes()
    if len(data) > limit:
        data = data[:limit]
    try:
        return data.decode("utf-8", errors="replace")
    except UnicodeDecodeError:  # pragma: no cover - decode errors="replace" won't raise
        return data.decode("latin-1", errors="replace")
