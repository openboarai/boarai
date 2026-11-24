"""
Vision sidecar: describe image attachments with the configured vision model and
inject the *text description* into the main model's request (dual-path vision
routing, aligned with openclacky's OCR sidecar and hermes' ``vision_analyze``).

The main (default / user-selected) model never receives ``image_url`` blocks in
sidecar mode — it stays on the user's preferred strong text model, so text
quality is never "dumbed down" by a smaller vision model. The vision model only
reads the raw image bytes and returns a Markdown description.

Reliability:
- Disk cache keyed by ``sha256(image bytes) + prompt + model`` so repeated
  turns (history images are re-injected on every run) are served from
  ``.boarai/cache/vision_sidecar/`` without extra LLM calls.
- Four-state outcome like openclacky: ok → description text; empty → the model
  returned nothing (treated as ok-but-empty); call_failed → None (caller
  injects a fallback note); bad_image → None.
- Images over ``MAX_IMAGE_BYTES`` cannot be sent inline to the vision model —
  they degrade to a path reference note (same as inline-image handling).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Sidecar prompt — extract all text and describe layout/state (openclacky-style).
VISION_SIDECAR_PROMPT = (
    "You are an image analysis tool. Extract ALL text visible in the image "
    "verbatim, and describe the layout, UI state, and any notable visual "
    "elements concisely. If this looks like a screenshot, a chart, or a "
    "document page, present the text as structured Markdown and preserve "
    "table layout. If the image is unclear or unreadable, say so explicitly. "
    "Output in the language of the surrounding conversation when "
    "determinable; otherwise use English."
)

#: Cache version — bump to invalidate all cached descriptions.
CACHE_VERSION = "v1"
#: Cache TTL in seconds (descriptions for the same image rarely change).
CACHE_TTL_SECONDS = 30 * 24 * 3600
#: Inline image cap (mirrors core/session/attachments.py MAX_IMAGE_BYTES).
MAX_IMAGE_BYTES = 5 * 1024 * 1024
#: Sidecar completion token cap. Thinking-style vision models can burn a
#: small budget on reasoning and return empty content (openclacky uses
#: 8192 for the same reason).
MAX_TOKENS = 8192


def _cache_dir() -> Path:
    from boarai.core.runtime import get_data_dir

    return get_data_dir() / "cache" / "vision_sidecar"


def _cache_key(image_bytes: bytes, model: str, prompt: str) -> str:
    h = hashlib.sha256(image_bytes).hexdigest()
    return hashlib.sha256(
        f"{CACHE_VERSION}|{h}|{model}|{prompt}".encode("utf-8")
    ).hexdigest()


def _read_cache(image_bytes: bytes, model: str, prompt: str) -> Optional[str]:
    try:
        path = _cache_dir() / f"{_cache_key(image_bytes, model, prompt)}.json"
        if not path.is_file():
            return None
        if time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
            return None
        data = json.loads(path.read_text("utf-8"))
        return data.get("text") or None
    except Exception as e:  # noqa: BLE001 - cache is best-effort
        logger.debug("Vision sidecar cache read failed: %s", e)
        return None


def _write_cache(image_bytes: bytes, model: str, prompt: str, text: str) -> None:
    try:
        d = _cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".tmp-{_cache_key(image_bytes, model, prompt)}"
        tmp.write_text(
            json.dumps({"text": text, "model": model}, ensure_ascii=False),
            "utf-8",
        )
        tmp.replace(d / f"{_cache_key(image_bytes, model, prompt)}.json")
    except Exception as e:  # noqa: BLE001 - cache is best-effort
        logger.debug("Vision sidecar cache write failed: %s", e)


async def describe_image(
    path: Path, model_spec: str, prompt: Optional[str] = None
) -> Optional[str]:
    """Describe one image file via the vision model (with disk cache)."""
    effective_prompt = (prompt or "").strip() or VISION_SIDECAR_PROMPT
    try:
        size = path.stat().st_size
        if size <= 0 or size > MAX_IMAGE_BYTES:
            return None
        image_bytes = await asyncio.to_thread(path.read_bytes)
    except (OSError, ValueError) as e:  # noqa: BLE001
        logger.warning("Vision sidecar read failed %s: %s", path, e)
        return None
    if not image_bytes:
        return None

    cached = await asyncio.to_thread(
        _read_cache, image_bytes, model_spec, effective_prompt
    )
    if cached is not None:
        return cached

    import base64
    import mimetypes

    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": effective_prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]
    try:
        from boarai.core.llm.service import get_llm_service

        svc = get_llm_service()
        # Stream + aggregate. Some OpenAI-compatible relays 502 on
        # NON-streaming requests that carry image_url blocks while
        # streaming works (probe against the 星云 relay, 2026-11-11:
        # stream=False → 502 "请求失败", stream=True → OK). The run's
        # native path already streams; the sidecar must match.
        parts: List[str] = []
        async for chunk in svc.chat_stream(
            messages,
            model=model_spec,
            temperature=0.2,
            max_tokens=MAX_TOKENS,
        ):
            piece = getattr(chunk, "content", None)
            if piece:
                parts.append(piece)
        text = "".join(parts)
    except Exception as e:  # noqa: BLE001 - degrade to fallback note
        logger.warning(
            "Vision sidecar call failed (model=%s, image=%s): %s",
            model_spec,
            path.name,
            e,
        )
        return None

    text = str(text).strip()
    if not text:
        logger.warning("Vision sidecar returned empty description for %s", path.name)
        return None

    await asyncio.to_thread(
        _write_cache, image_bytes, model_spec, effective_prompt, text
    )
    return text


async def build_sidecar_notes(
    session_id: str,
    attachments: List[Dict[str, Any]],
) -> str:
    """Describe all image attachments in one Markdown note block."""
    from boarai.core.session import attachments as _att

    cap = None
    try:
        from boarai.core.session.run.model_resolver import get_vision_capability

        cap = get_vision_capability()
    except Exception:  # noqa: BLE001
        cap = None

    notes: List[str] = []
    for att in attachments or []:
        if not isinstance(att, dict):
            continue
        name = att.get("name") or att.get("stored_name") or "attachment"
        kind = att.get("kind") or _att.detect_kind(name, att.get("mime_type"))
        stored_name = _att.sanitize_filename(att.get("stored_name") or "")
        if not stored_name:
            continue
        path = _att.resolve_attachment_path(session_id, stored_name)

        if kind == "image" and path is not None and cap is not None:
            desc = await describe_image(path, f"{cap[0]}/{cap[1]}")
            if desc:
                notes.append(f"[Image: {name}]\n{desc}\n[/Image]")
                continue
            # Fallback: model failed / image too large — keep a path reference.
            if path is not None:
                notes.append(
                    f"[Image: {name} — could not be described by the vision "
                    f"model; readable at {path}]"
                )
                continue
        if path is not None:
            notes.append(f"[Attachment: {name} — saved to session files: {path}]")
        else:
            notes.append(
                f"[Attachment: {name} — file not found, ask the user to re-upload]"
            )

    if not notes:
        return ""
    return "\n\n" + "\n\n".join(notes)
