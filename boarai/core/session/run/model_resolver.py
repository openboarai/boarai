"""
Capability-driven three-state vision routing (multimodal overhaul P1,
2026-11-11; supersedes the 2026-08-14 slot-only dual-path rule).

The executor re-injects historical image attachments on each turn (within
the replay window / byte budget). How they reach the model depends on the
main model's declared ``capabilities.image`` flag (llm.yaml, written by the
WebUI supplier page) crossed with the ``capability_models["vision"]`` slot:

- declared True  → NATIVE: raw ``image_url`` blocks for the main model; a
  configured slot does NOT steal the turn (highest fidelity).
- declared False → SIDECAR when a vision slot is configured (the slot
  describes each image, text is injected; the main model still handles ALL
  text — never "dumbed down"), else TEXT_ONLY (pre-flight omission notes
  with readable-path handles — no wasted gateway 400 round-trip).
- undeclared     → legacy behaviour: SIDECAR when a slot is configured and
  the main model is not itself the slot model, else NATIVE. The executor's
  strip-and-retry stays the fuse for a lying capability declaration.

``run.model`` stays the user's preferred main model — plain-text turns never
switch models, so prompt-cache prefixes stay stable.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

#: Capability key in llm.yaml ``capability_models`` for vision.
VISION_CAPABILITY = "vision"


class ImageRoute(str, Enum):
    """How image attachments reach the model on this run."""

    NATIVE = "native"
    SIDECAR = "sidecar"
    TEXT_ONLY = "text_only"


def has_image_attachments(session: Any) -> bool:
    """Return True when any user message carries an image attachment."""
    if session is None:
        return False
    messages = getattr(session, "messages", None) or []
    for m in messages:
        if getattr(m, "role", "") != "user":
            continue
        metadata = getattr(m, "metadata", None) or {}
        attachments = metadata.get("attachments") or []
        for att in attachments:
            if not isinstance(att, dict):
                continue
            kind = att.get("kind") or ""
            if kind == "image":
                return True
            # Old disk history may lack "kind" — infer from name/mime.
            if not kind:
                name = att.get("name") or att.get("stored_name") or ""
                mime = att.get("mime_type") or ""
                try:
                    from boarai.core.session import attachments as _att

                    if _att.detect_kind(name, mime) == "image":
                        return True
                except Exception:  # noqa: BLE001
                    continue
    return False


def get_vision_capability() -> Optional[Tuple[str, str]]:
    """Return ``(provider, model)`` for the configured vision capability."""
    try:
        from boarai.core.llm import get_llm_manager

        cap = get_llm_manager().get_capability_model(VISION_CAPABILITY)
    except Exception as e:  # noqa: BLE001 - degrade to inline images
        logger.debug("Vision capability lookup failed: %s", e)
        return None
    if not cap:
        return None
    provider = str(cap.get("provider") or "").strip()
    model = str(cap.get("model") or "").strip()
    if not provider or not model:
        return None
    return provider, model


def _split_spec(requested_model: Optional[str]) -> Tuple[Optional[str], str]:
    """Split ``"provider/model"`` into ``(provider, model)``."""
    spec = str(requested_model or "").strip()
    if "/" in spec:
        provider, model = spec.split("/", 1)
        return (provider or None), model
    return None, spec


def model_image_capability(requested_model: Optional[str]) -> Optional[bool]:
    """The main model's declared ``capabilities.image`` flag."""
    try:
        from boarai.core.llm import get_llm_manager

        provider, model = _split_spec(requested_model)
        if not model:
            return None
        caps = get_llm_manager().get_model_capabilities(provider, model)
    except Exception as e:  # noqa: BLE001 - undeclared is the safe reading
        logger.debug("Model capability lookup failed for %s: %s", requested_model, e)
        return None
    if "image" not in caps:
        return None
    return bool(caps["image"])


def route_for_model(requested_model: Optional[str]) -> ImageRoute:
    """Capability-driven route for image content aimed at ``requested_model``."""
    cap = model_image_capability(requested_model)
    slot = get_vision_capability()
    if cap is True:
        return ImageRoute.NATIVE
    if cap is False:
        return ImageRoute.SIDECAR if slot is not None else ImageRoute.TEXT_ONLY
    # Undeclared capability: preserve the pre-2026-11-11 behaviour exactly.
    if slot is not None:
        _, vision_model = slot
        if not (requested_model and _normalize_spec(requested_model) == vision_model):
            return ImageRoute.SIDECAR
    return ImageRoute.NATIVE


def resolve_image_route(
    session: Any,
    requested_model: Optional[str],
) -> ImageRoute:
    """Three-state routing for this session's image attachments."""
    if not has_image_attachments(session):
        return ImageRoute.NATIVE
    return route_for_model(requested_model)


def vision_sidecar_required(
    session: Any,
    requested_model: Optional[str],
) -> bool:
    """Thin compat wrapper: True when :func:`resolve_image_route` says SIDECAR."""
    return resolve_image_route(session, requested_model) == ImageRoute.SIDECAR


def _normalize_spec(model: str) -> str:
    """Strip a leading ``provider/`` prefix for equality checks."""
    m = str(model or "")
    return m.split("/", 1)[-1] if "/" in m else m
