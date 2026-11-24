"""Extension scaffold generator (Python API; the CLI face was removed).

Philosophy (openclacky ExtensionScaffold): *the best documentation for an AI
author is a working skeleton it can copy*. Generated containers are complete,
runnable extensions — the hello template answers GET /api/ext/<id>/ping, the
full template exercises every ``contributes`` kind.

Rendering is deliberately dumb: copy the template directory, ``str.replace``
the ``{{name}}`` / ``{{Name}}`` / ``{{type}}`` / ``{{channel_identity}}``
placeholders. No ERB, no code generation logic to drift from the templates.
(``{{...}}`` instead of ``__xxx__``: a ``__name__`` placeholder collided
with Python's own ``__name__`` idiom — ``logging.getLogger(__name__)`` was
rendered to the slug, producing a NameError at load time that AST
verification cannot catch because it is syntactically valid.)

Scaffolding writes to the USER layer (``.boarai/extensions/<typedir>/<id>/``)
— never to the read-only system layer — and immediately runs the static
verifier so the author gets the issue stream without a second command.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# boar/extensions/templates lives next to the boarai package's project root.
TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "boar" / "extensions" / "templates"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")


class ScaffoldError(Exception):
    """Raised for invalid scaffold requests (bad id/type, existing target)."""


def slugify(ext_id: str) -> str:
    """Normalize an extension id to snake_case (manifest name convention)."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(ext_id).strip().lower()).strip("_")
    return slug


def camelize(slug: str) -> str:
    """snake_case -> PascalCase (class-name prefix in the templates)."""
    return "".join(p.capitalize() for p in re.split(r"[_\-]", slug) if p)


def _channel_identity_block(slug: str, ext_type: str) -> str:
    name = camelize(slug)
    if ext_type == "channel":
        return (
            "# Channel identity — auto-registered into the SourceRegistry\n"
            "# by ExtensionManager on enable (never write registration code):\n"
            f'source_id: "{slug}"\n'
            f'source_display: "{name}"'
        )
    return (
        "# Channel identity — only meaningful for extension_type \"channel\"\n"
        "# (auto-registered into the SourceRegistry on enable):\n"
        f'# source_id: "{slug}"\n'
        f'# source_display: "{name}"'
    )


def render_template(
    template_dir: Path,
    target: Path,
    *,
    slug: str,
    ext_type: str,
) -> None:
    """Copy ``template_dir`` into ``target``, substituting placeholders."""
    replacements = {
        "{{channel_identity}}": _channel_identity_block(slug, ext_type),
        "{{Name}}": camelize(slug),
        "{{name}}": slug,
        "{{type}}": ext_type,
    }
    target.mkdir(parents=True, exist_ok=False)
    try:
        for src in sorted(template_dir.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(template_dir)
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            text = src.read_text(encoding="utf-8")
            for placeholder, value in replacements.items():
                text = text.replace(placeholder, value)
            dest.write_text(text, encoding="utf-8", newline="\n")
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def new_container(
    ext_id: str,
    ext_type: str = "plugin",
    *,
    full: bool = False,
    target_root: Optional[Path] = None,
) -> Path:
    """Scaffold a runnable extension into the user layer; returns its path."""
    from boarai.core.extension.discovery import ExtensionDiscovery, ExtensionType

    try:
        ExtensionType(ext_type)
    except ValueError:
        raise ScaffoldError(
            f"invalid extension_type {ext_type!r} "
            "(valid: channel, plugin, tool, mcp — skills are SKILL.md "
            "bundles managed by the skill store, see prompts/extension/skills.md)"
        )
    if ext_type == "skill":
        raise ScaffoldError(
            "skills are SKILL.md bundles (no manifest/entry point); author "
            "them under .boarai/extensions/skills/<name>/SKILL.md per "
            "prompts/extension/skills.md"
        )
    slug = slugify(ext_id)
    if not slug or not _ID_RE.match(slug):
        raise ScaffoldError(
            f"invalid extension id {ext_id!r}: use lowercase letters, digits "
            "and underscores, starting with a letter or digit"
        )

    template_dir = TEMPLATES_DIR / ("full" if full else "hello")
    if not template_dir.is_dir():
        raise ScaffoldError(f"scaffold template missing: {template_dir}")

    if target_root is None:
        from boarai.core.runtime import get_data_dir

        target_root = Path(get_data_dir()) / "extensions"
    type_dir = ExtensionDiscovery.TYPE_DIRS[ExtensionType(ext_type)]
    target = Path(target_root) / type_dir / slug
    if target.exists():
        raise ScaffoldError(f"extension already exists: {target}")

    render_template(template_dir, target, slug=slug, ext_type=ext_type)
    logger.info(
        "Scaffolded %s extension '%s' at %s",
        "full" if full else "hello", slug, target,
    )
    return target


def scaffold_extension(
    ext_id: str,
    ext_type: str = "plugin",
    *,
    full: bool = False,
    target_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """new_container + immediate static verification (one feedback stream)."""
    from boarai.core.extension.verifier import has_errors, verify_extension

    path = new_container(ext_id, ext_type, full=full, target_root=target_root)
    issues = verify_extension(path)
    return {
        "path": str(path),
        "name": path.name,
        "issues": [i.to_dict() for i in issues],
        "ok": not has_errors(issues),
    }
