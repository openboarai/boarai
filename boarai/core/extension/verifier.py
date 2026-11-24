"""Static extension verifier (dry-run — never executes extension code).

The loader enforces its contract by *raising at load time*, which for a
user-layer extension means the trust gate
(``BOARAI_ALLOW_INPROCESS_USER_EXTENSIONS``) fires first and the author
never learns what is actually wrong. The verifier closes that feedback
loop: it answers "is this extension loadable, and if not, exactly why"
WITHOUT importing or running any extension code, so it works on untrusted
user-layer directories too.

Checks (mirrors of loader/discovery/route-registry semantics):

* manifest presence + YAML parse + mapping shape
* schema: unknown top-level / contributes / per-unit keys, required fields,
  semver format, extension_type enum
* entry point presence (same resolution order as the loader)
* ``"code.py#func"`` handler references: file exists and the symbol is a
  top-level def/class/assignment (AST parse — no execution)
* syntax: every ``.py`` under the directory must ``ast.parse``
* routes: reserved prefixes (``/api/``, ``/ws``, ``/static/``) may not be
  claimed with ``use_prefix: false`` by non-system extensions
* pip_dependencies shape (format only — nothing is installed)
* layer facts: user-layer trust-gate notice (info), same-name override
  across layers (warning)
* SKILL.md bundles delegate to ``core.extension.skill.validate``

Output is a flat list of :class:`Issue` records — each individually
addressable (``code`` + ``file`` + ``hint``) so the CLI and the HTTP API
render the same stream (design borrowed from openclacky's
ExtensionVerifier: "whole-program checks an AI author needs to close the
feedback loop").
"""

from __future__ import annotations

import ast
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"
LEVEL_INFO = "info"

# --- Schema knowledge (kept in sync with manifest.ExtensionManifest / _parse_contributes) ---

KNOWN_TOP_KEYS = frozenset({
    "name", "version", "description", "extension_type", "author", "enabled",
    "dependencies", "pip_dependencies", "min_boarai_version", "provides",
    "permissions", "source_id", "source_display", "contributes",
    "config_defaults",
})

# Removed manifest keys: warned about specifically (with migration hints)
# before the generic unknown-key warning fires.
DEPRECATED_TOP_KEYS = frozenset({"config_schema"})

KNOWN_CONTRIBUTES = frozenset({
    "tools", "routes", "hooks", "hook_points", "services", "mcp_servers", "ui",
})

# contributes.ui is a MAPPING (panel + actions), not a list of units, so it is
# deliberately absent from UNIT_KEYS/UNIT_REQUIRED and checked by _check_ui.
UI_KEYS = frozenset({"panel", "actions"})
UI_PANEL_KEYS = frozenset({
    "title", "title_i18n", "icon", "kind", "custom_url", "custom_height",
})
UI_ACTION_KEYS = frozenset({
    "id", "handler", "label", "label_i18n", "icon", "style", "confirm",
    "timeout",
})
UI_PANEL_KINDS = frozenset({"custom"})
UI_ACTION_STYLES = frozenset({"primary", "default", "danger"})

UNIT_KEYS: Dict[str, frozenset] = {
    "tools": frozenset({
        "id", "handler", "description", "schema", "security_profile",
        "path_param", "shell_param", "suggested_category",
        "compress_strategy", "timeout",
    }),
    "routes": frozenset({
        "method", "path", "handler", "description", "use_prefix",
    }),
    "hooks": frozenset({"event", "handler", "order"}),
    "hook_points": frozenset({"name", "semantics", "description", "visibility"}),
    "services": frozenset({"methods"}),
    "mcp_servers": frozenset({
        "name", "command", "args", "env", "cwd", "timeout", "tool_timeout",
    }),
}

# Required per-unit fields — the manifest parser silently DISCARDS items
# missing these, so the verifier reports them as errors (a discarded unit is
# a capability that silently never mounts). An empty handler also fails at
# mount time ("file.py#func" form required), so it is required here too.
UNIT_REQUIRED: Dict[str, tuple] = {
    "tools": ("id", "handler"),
    "routes": ("method", "path", "handler"),
    "hooks": ("event", "handler"),
    "hook_points": ("name",),
    "mcp_servers": ("command",),
}

VALID_EXTENSION_TYPES = frozenset({"channel", "plugin", "tool", "skill", "mcp"})

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+].*)?$")

# Loader entry-point resolution order (loader.py step 5).
ENTRY_POINTS = ("extension.py", "__init__.py", "tools.py")

# Reserved core path prefixes (route_registry.RESERVED_PREFIXES) — untrusted
# extensions may not mount these at root level (use_prefix: false).
RESERVED_PREFIXES = ("/api/", "/ws", "/static/")

_SKIP_DIRS = {"__pycache__", ".git", "node_modules"}

# A name that reaches verify_by_name must be a bare directory name: anything
# with a separator or a ".." component would traverse out of the extension
# layers (base / type_dir / "../../.." resolves anywhere on disk).
_UNSAFE_NAME_RE = re.compile(r"[\\/]|^\.\.|\.\.$")


def is_safe_ext_name(name: str) -> bool:
    """Bare directory name — no path separators, no '..' components."""
    n = str(name or "").strip()
    return bool(n) and ".." not in Path(n).parts and not _UNSAFE_NAME_RE.search(n)


def resolve_within_roots(raw_path: str) -> Optional[Path]:
    """Resolve a client-supplied path, confined to project root / data dir."""
    try:
        p = Path(str(raw_path)).expanduser().resolve()
    except Exception:  # noqa: BLE001
        return None
    roots: List[Path] = []
    try:
        from boarai.core.runtime import get_data_dir, get_project_root

        for getter in (get_project_root, get_data_dir):
            try:
                r = getter()
                if r:
                    roots.append(Path(r).resolve())
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    for root in roots:
        if p == root or root in p.parents:
            return p
    return None


@dataclass
class Issue:
    """One structured finding. Flat and individually addressable."""

    ext: str
    level: str          # error | warning | info
    code: str           # machine-readable, e.g. "schema.unknown_key"
    message: str
    unit: str = ""      # contributes unit id ("" for whole-extension issues)
    file: str = ""      # best-effort file location
    hint: str = ""      # actionable next step

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ext": self.ext, "level": self.level, "code": self.code,
            "message": self.message, "unit": self.unit,
            "file": self.file, "hint": self.hint,
        }


def has_errors(issues: List[Issue]) -> bool:
    return any(i.level == LEVEL_ERROR for i in issues)


def _infer_source(ext_path: Path) -> str:
    """system/user layer for a path (delegates to the loader's own logic)."""
    try:
        from boarai.core.extension.loader import ExtensionLoader

        return ExtensionLoader._infer_source(Path(ext_path))
    except Exception:  # noqa: BLE001
        return ""


def _resolve_manifest_file(ext_path: Path) -> Path:
    """Same resolution as the loader: manifest.yaml, extension.yaml alias."""
    for name in ("manifest.yaml", "extension.yaml"):
        candidate = ext_path / name
        if candidate.exists():
            return candidate
    return ext_path / "manifest.yaml"


def _top_level_symbols(tree: ast.AST) -> set:
    names = set()
    for node in getattr(tree, "body", []) or []:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _iter_python_files(ext_path: Path):
    for p in sorted(ext_path.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in p.relative_to(ext_path).parts):
            continue
        yield p


def _check_syntax_and_symbols(
    ext: str, ext_path: Path, issues: List[Issue]
) -> Dict[str, set]:
    """AST-parse every .py file: syntax errors + top-level symbol table."""
    symbols: Dict[str, set] = {}
    for py in _iter_python_files(ext_path):
        rel = py.relative_to(ext_path).as_posix()
        try:
            source = py.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            issues.append(Issue(
                ext=ext, level=LEVEL_WARNING, code="syntax.unreadable",
                message=f"cannot read {rel}: {e}", file=str(py),
            ))
            continue
        try:
            tree = ast.parse(source, filename=str(py))
        except SyntaxError as e:
            issues.append(Issue(
                ext=ext, level=LEVEL_ERROR, code="syntax.parse_error",
                message=f"{rel}: {e.msg} (line {e.lineno})",
                file=str(py),
                hint="Fix the syntax error; the loader would fail to import this file.",
            ))
            continue
        symbols[rel] = _top_level_symbols(tree)
    return symbols


def _check_handler_refs(
    ext: str,
    ext_path: Path,
    contributes: Dict[str, Any],
    symbols: Dict[str, set],
    issues: List[Issue],
) -> None:
    """Validate every "code.py#func" reference in contributes (routes/tools/hooks)."""
    units: List[tuple] = []
    for kind in ("tools", "routes", "hooks"):
        for item in contributes.get(kind) or []:
            if not isinstance(item, dict):
                continue
            unit_id = str(item.get("id") or item.get("path") or item.get("event") or "")
            handler = item.get("handler")
            # Empty/missing handlers are already errors via UNIT_REQUIRED
            # (ref.missing_field) — skip here to avoid double-reporting.
            if handler:
                units.append((kind, unit_id, str(handler)))
    # Settings-UI action buttons carry handlers too (contributes.ui.actions).
    ui = contributes.get("ui")
    if isinstance(ui, dict):
        for item in ui.get("actions") or []:
            if not isinstance(item, dict):
                continue
            handler = item.get("handler")
            if handler:
                units.append((
                    "ui.actions", str(item.get("id") or ""), str(handler),
                ))
    # services methods may be "code.py#func" references (a bare name resolves
    # from the entry class instead and needs no file check).
    for item in contributes.get("services") or []:
        if not isinstance(item, dict):
            continue
        for method in item.get("methods") or []:
            if isinstance(method, str) and "#" in method:
                func = method.split("#", 1)[1]
                units.append(("services", func, method))

    for kind, unit_id, handler in units:
        if "#" not in handler:
            issues.append(Issue(
                ext=ext, level=LEVEL_ERROR, code="ref.handler_format",
                message=f"contributes.{kind} '{unit_id}' handler {handler!r} "
                        "is not in \"file.py#func\" form",
                unit=unit_id,
                hint="The loader resolves handlers only as \"code.py#func\".",
            ))
            continue
        file_part, _, func_part = handler.partition("#")
        target = ext_path / file_part
        rel = file_part
        if not target.is_file():
            issues.append(Issue(
                ext=ext, level=LEVEL_ERROR, code="ref.handler_missing_file",
                message=f"contributes.{kind} '{unit_id}' handler file "
                        f"{file_part!r} does not exist",
                unit=unit_id, file=str(target),
            ))
            continue
        known = symbols.get(Path(rel).as_posix())
        if known is None:
            # File exists but did not parse (syntax error already reported).
            continue
        if func_part and func_part not in known:
            issues.append(Issue(
                ext=ext, level=LEVEL_ERROR, code="ref.handler_missing_func",
                message=f"contributes.{kind} '{unit_id}': function "
                        f"{func_part!r} is not a top-level symbol in {file_part}",
                unit=unit_id, file=str(target),
                hint="Top-level def/class/assignment only; nested helpers are "
                     "not resolvable.",
            ))


def _check_ui(ext: str, contributes: Dict[str, Any], issues: List[Issue]) -> None:
    """Validate ``contributes.ui`` (settings panel + action buttons)."""
    ui = contributes.get("ui")
    if ui is None:
        return
    if not isinstance(ui, dict):
        issues.append(Issue(
            ext=ext, level=LEVEL_ERROR, code="schema.bad_ui",
            message=f"contributes.ui must be a mapping, got {type(ui).__name__}",
        ))
        return

    unknown = set(ui.keys()) - UI_KEYS
    if unknown:
        issues.append(Issue(
            ext=ext, level=LEVEL_WARNING, code="schema.unknown_field",
            message=f"contributes.ui has unknown key(s): "
                    f"{', '.join(sorted(map(str, unknown)))}",
            hint=f"Allowed: {', '.join(sorted(UI_KEYS))}",
        ))

    panel = ui.get("panel")
    if panel is not None:
        if not isinstance(panel, dict):
            issues.append(Issue(
                ext=ext, level=LEVEL_ERROR, code="schema.bad_ui_panel",
                message="contributes.ui.panel must be a mapping "
                        f"(got {type(panel).__name__})",
            ))
        else:
            bad = set(panel.keys()) - UI_PANEL_KEYS
            if bad:
                issues.append(Issue(
                    ext=ext, level=LEVEL_WARNING, code="schema.unknown_field",
                    message=f"contributes.ui.panel has unknown key(s): "
                            f"{', '.join(sorted(map(str, bad)))}",
                    hint=f"Allowed: {', '.join(sorted(UI_PANEL_KEYS))}",
                ))
            kind = str(panel.get("kind") or "custom")
            if kind not in UI_PANEL_KINDS:
                issues.append(Issue(
                    ext=ext, level=LEVEL_ERROR, code="ui.bad_panel_kind",
                    message=f"contributes.ui.panel.kind {kind!r} is not one of "
                            f"{sorted(UI_PANEL_KINDS)}",
                ))
            elif kind == "custom" and not str(panel.get("custom_url") or "").strip():
                issues.append(Issue(
                    ext=ext, level=LEVEL_ERROR, code="ui.missing_custom_url",
                    message="contributes.ui.panel.kind is 'custom' but "
                            "custom_url is empty",
                    hint="Point it at your own asset, e.g. "
                         "/static/<your extension id>/settings.html",
                ))

    actions = ui.get("actions")
    if actions is None:
        return
    if not isinstance(actions, list):
        issues.append(Issue(
            ext=ext, level=LEVEL_ERROR, code="schema.bad_ui_actions",
            message="contributes.ui.actions must be a list of mappings",
        ))
        return
    for item in actions:
        if not isinstance(item, dict):
            issues.append(Issue(
                ext=ext, level=LEVEL_ERROR, code="schema.bad_unit",
                message=f"contributes.ui.actions entry is not a mapping: {item!r}",
            ))
            continue
        aid = str(item.get("id") or "")
        missing = [f for f in ("id", "handler") if not item.get(f)]
        if missing:
            issues.append(Issue(
                ext=ext, level=LEVEL_ERROR, code="ref.missing_field",
                message=f"contributes.ui.actions '{aid}' is missing required "
                        f"field(s): {', '.join(missing)} (the button is "
                        "DISCARDED silently at load time)",
                unit=aid,
            ))
        bad = set(item.keys()) - UI_ACTION_KEYS
        if bad:
            issues.append(Issue(
                ext=ext, level=LEVEL_WARNING, code="schema.unknown_field",
                message=f"contributes.ui.actions '{aid}' has unknown key(s): "
                        f"{', '.join(sorted(map(str, bad)))}",
                unit=aid,
                hint=f"Allowed: {', '.join(sorted(UI_ACTION_KEYS))}",
            ))
        style = str(item.get("style") or "default")
        if style not in UI_ACTION_STYLES:
            issues.append(Issue(
                ext=ext, level=LEVEL_WARNING, code="ui.bad_action_style",
                message=f"contributes.ui.actions '{aid}' style {style!r} is "
                        f"not one of {sorted(UI_ACTION_STYLES)}; the WebUI "
                        "renders it as 'default'",
                unit=aid,
            ))


def _check_routes(
    ext: str, contributes: Dict[str, Any], source: str, issues: List[Issue]
) -> None:
    for item in contributes.get("routes") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        use_prefix = bool(item.get("use_prefix", True))
        unit_id = f"{str(item.get('method', 'GET')).upper()} {path}"
        if not path.startswith("/"):
            issues.append(Issue(
                ext=ext, level=LEVEL_ERROR, code="route.bad_path",
                message=f"route {unit_id}: path must start with '/'",
                unit=unit_id,
            ))
        if (
            not use_prefix
            and source != "system"
            and path.startswith(RESERVED_PREFIXES)
        ):
            issues.append(Issue(
                ext=ext, level=LEVEL_ERROR, code="route.reserved_prefix",
                message=f"route {unit_id}: untrusted (user-layer) extensions "
                        "may not mount reserved core prefixes at root level",
                unit=unit_id,
                hint="Set use_prefix: true (mounts at /api/ext/<name>/...) or "
                     "move the extension to the system layer.",
            ))


def _check_skill_bundle(ext: str, ext_path: Path, issues: List[Issue]) -> None:
    """SKILL.md directories delegate to the skill validator (multi-error)."""
    try:
        from boarai.core.extension.skill.validate import validate_skill_bundle

        errors = validate_skill_bundle(ext_path.name, ext_path)
    except Exception as e:  # noqa: BLE001
        issues.append(Issue(
            ext=ext, level=LEVEL_WARNING, code="skill.validate_unavailable",
            message=f"skill validation could not run: {e}",
        ))
        return
    for err in errors or []:
        issues.append(Issue(
            ext=ext, level=LEVEL_ERROR, code="skill.invalid",
            message=str(err), file=str(ext_path / "SKILL.md"),
        ))


def verify_extension(ext_path: Any) -> List[Issue]:
    """Statically verify one extension directory. Never executes its code."""
    ext_path = Path(ext_path)
    ext = ext_path.name
    issues: List[Issue] = []

    if not ext_path.is_dir():
        return [Issue(
            ext=ext, level=LEVEL_ERROR, code="path.not_found",
            message=f"extension directory not found: {ext_path}",
        )]

    # --- SKILL.md bundles are not manifest extensions ---
    if (ext_path / "SKILL.md").is_file() and not _resolve_manifest_file(ext_path).exists():
        _check_skill_bundle(ext, ext_path, issues)
        return issues

    source = _infer_source(ext_path)

    # --- Manifest ---
    manifest_file = _resolve_manifest_file(ext_path)
    if not manifest_file.is_file():
        issues.append(Issue(
            ext=ext, level=LEVEL_ERROR, code="manifest.not_found",
            message="no manifest.yaml (or legacy extension.yaml) in the directory",
            file=str(manifest_file),
            hint="Discovery requires a manifest AND an entry point "
                 "(extension.py / __init__.py / tools.py).",
        ))
        return issues

    try:
        import yaml

        data = yaml.safe_load(manifest_file.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        issues.append(Issue(
            ext=ext, level=LEVEL_ERROR, code="manifest.parse_error",
            message=f"YAML parse failed: {e}", file=str(manifest_file),
        ))
        return issues
    if not isinstance(data, dict):
        issues.append(Issue(
            ext=ext, level=LEVEL_ERROR, code="manifest.bad_shape",
            message="manifest must be a YAML mapping", file=str(manifest_file),
        ))
        return issues

    name = str(data.get("name") or "")
    if not name:
        issues.append(Issue(
            ext=ext, level=LEVEL_ERROR, code="schema.missing_required",
            message="'name' is required", file=str(manifest_file),
        ))
    elif name != ext:
        issues.append(Issue(
            ext=ext, level=LEVEL_WARNING, code="schema.name_dir_mismatch",
            message=f"manifest name {name!r} differs from directory name {ext!r}",
            file=str(manifest_file),
            hint="Enable/disable state and overrides key on the manifest name; "
                 "a mismatch is confusing but not fatal.",
        ))
    ext = name or ext

    version = str(data.get("version") or "")
    if not version:
        issues.append(Issue(
            ext=ext, level=LEVEL_ERROR, code="schema.missing_required",
            message="'version' is required", file=str(manifest_file),
        ))
    elif not VERSION_RE.match(version):
        issues.append(Issue(
            ext=ext, level=LEVEL_ERROR, code="schema.invalid_version",
            message=f"version {version!r} is not semver (x.y.z)",
            file=str(manifest_file),
        ))

    ext_type = str(data.get("extension_type") or "plugin")
    if ext_type not in VALID_EXTENSION_TYPES:
        issues.append(Issue(
            ext=ext, level=LEVEL_ERROR, code="schema.invalid_type",
            message=f"extension_type {ext_type!r} is invalid "
                    f"(valid: {', '.join(sorted(VALID_EXTENSION_TYPES))})",
            file=str(manifest_file),
        ))

    # --- Removed manifest keys (config_schema era) ---
    deprecated = sorted(set(data.keys()) & DEPRECATED_TOP_KEYS)
    if deprecated:
        issues.append(Issue(
            ext=ext, level=LEVEL_WARNING, code="schema.deprecated_key",
            message=f"deprecated top-level key(s): {', '.join(deprecated)}",
            file=str(manifest_file),
            hint="The declarative settings form was removed; declare "
                 "contributes.ui.panel (kind: custom, custom_url) and talk to "
                 "the core from your page via /static/js/boar-sdk.js. The key "
                 "is ignored — existing config.json values still load.",
        ))

    unknown_top = set(data.keys()) - KNOWN_TOP_KEYS - DEPRECATED_TOP_KEYS
    if unknown_top:
        issues.append(Issue(
            ext=ext, level=LEVEL_WARNING, code="schema.unknown_key",
            message=f"unknown top-level key(s): {', '.join(sorted(map(str, unknown_top)))}",
            file=str(manifest_file),
            hint=f"Allowed: {', '.join(sorted(KNOWN_TOP_KEYS))}",
        ))

    # --- Entry point (loader step 5) ---
    if not any((ext_path / candidate).is_file() for candidate in ENTRY_POINTS):
        issues.append(Issue(
            ext=ext, level=LEVEL_ERROR, code="entry.not_found",
            message="no entry point (extension.py / __init__.py / tools.py)",
            hint="Discovery SKIPS directories without an entry point, so the "
                 "extension is invisible until one exists.",
        ))

    # --- pip_dependencies shape ---
    pip_deps = data.get("pip_dependencies")
    if pip_deps is not None and not isinstance(pip_deps, list):
        issues.append(Issue(
            ext=ext, level=LEVEL_ERROR, code="deps.bad_shape",
            message="pip_dependencies must be a list of requirement strings",
            file=str(manifest_file),
        ))
    elif isinstance(pip_deps, list):
        for dep in pip_deps:
            if not isinstance(dep, str) or not dep.strip():
                issues.append(Issue(
                    ext=ext, level=LEVEL_WARNING, code="deps.invalid",
                    message=f"pip_dependencies entry {dep!r} is not a "
                            "non-empty requirement string",
                    file=str(manifest_file),
                ))

    # --- contributes ---
    contributes = data.get("contributes")
    if contributes is not None and not isinstance(contributes, dict):
        issues.append(Issue(
            ext=ext, level=LEVEL_ERROR, code="schema.bad_contributes",
            message="contributes must be a mapping", file=str(manifest_file),
        ))
        contributes = {}
    contributes = contributes or {}

    unknown_contrib = set(contributes.keys()) - KNOWN_CONTRIBUTES
    if unknown_contrib:
        issues.append(Issue(
            ext=ext, level=LEVEL_WARNING, code="schema.unknown_contributes",
            message=f"unknown contributes kind(s): {', '.join(sorted(map(str, unknown_contrib)))} "
                    "(discarded by the loader)",
            file=str(manifest_file),
            hint=f"Allowed: {', '.join(sorted(KNOWN_CONTRIBUTES))}",
        ))

    for kind, allowed in UNIT_KEYS.items():
        for item in contributes.get(kind) or []:
            if not isinstance(item, dict):
                issues.append(Issue(
                    ext=ext, level=LEVEL_ERROR, code="schema.bad_unit",
                    message=f"contributes.{kind} entry is not a mapping: {item!r}",
                    file=str(manifest_file),
                ))
                continue
            unit_id = str(item.get("id") or item.get("name") or item.get("path") or "")
            missing = [f for f in UNIT_REQUIRED.get(kind, ()) if not item.get(f)]
            if missing:
                issues.append(Issue(
                    ext=ext, level=LEVEL_ERROR, code="ref.missing_field",
                    message=f"contributes.{kind} '{unit_id}' is missing required "
                            f"field(s): {', '.join(missing)} (the unit is DISCARDED "
                            "silently at load time)",
                    unit=unit_id, file=str(manifest_file),
                ))
            unknown_unit = set(item.keys()) - allowed
            if unknown_unit:
                issues.append(Issue(
                    ext=ext, level=LEVEL_WARNING, code="schema.unknown_field",
                    message=f"contributes.{kind} '{unit_id}' has unknown key(s): "
                            f"{', '.join(sorted(map(str, unknown_unit)))}",
                    unit=unit_id, file=str(manifest_file),
                    hint=f"Allowed for {kind}: {', '.join(sorted(allowed))}",
                ))
            if kind == "mcp_servers":
                srv_name = str(item.get("name") or "").strip()
                if not srv_name or " " in srv_name:
                    issues.append(Issue(
                        ext=ext, level=LEVEL_ERROR, code="schema.bad_mcp_name",
                        message=f"mcp_servers name {srv_name!r} must be a "
                                "non-empty snake_case tool-name prefix",
                        unit=srv_name, file=str(manifest_file),
                    ))

    # --- Python syntax + handler references (AST only, no execution) ---
    symbols = _check_syntax_and_symbols(ext, ext_path, issues)
    _check_handler_refs(ext, ext_path, contributes, symbols, issues)
    _check_routes(ext, contributes, source, issues)
    _check_ui(ext, contributes, issues)

    # --- Layer facts ---
    if source == "user":
        _check_layer_override(ext, ext_path, issues)
        if os.getenv("BOARAI_ALLOW_INPROCESS_USER_EXTENSIONS", "") != "1":
            issues.append(Issue(
                ext=ext, level=LEVEL_INFO, code="gate.inprocess_untrusted",
                message="user-layer extension: the loader will REFUSE to "
                        "execute it until the operator grants trust",
                hint="Operator grant, per extension: WebUI failure card "
                     "'Trust & load' / POST /api/extensions/<name>/trust "
                     "(revocable). Or globally: set "
                     "BOARAI_ALLOW_INPROCESS_USER_EXTENSIONS=1 (User-level "
                     "env var) + restart FROM A NEW TERMINAL. Static "
                     "verification (this report) needs no trust.",
            ))
    elif source == "system":
        _check_layer_override(ext, ext_path, issues)

    return issues


def _check_layer_override(ext: str, ext_path: Path, issues: List[Issue]) -> None:
    """Warn when the same extension name exists in both layers (one shadows)."""
    try:
        from boarai.core.extension.discovery import ExtensionDiscovery, ExtensionType
        from boarai.core.runtime import get_data_dir

        rel_type_dir = None
        parent = ext_path.parent.name  # channels/tools/plugins/mcp
        for t, d in ExtensionDiscovery.TYPE_DIRS.items():
            if d == parent:
                rel_type_dir = d
                break
        if rel_type_dir is None:
            return
        project_root = Path(__file__).resolve().parents[3]
        system_dir = project_root / "boar" / "extensions" / rel_type_dir / ext
        user_dir = Path(get_data_dir()) / "extensions" / rel_type_dir / ext
        if system_dir.is_dir() and user_dir.is_dir():
            winner = "user" if user_dir.resolve() == ext_path.resolve() else "system"
            issues.append(Issue(
                ext=ext, level=LEVEL_WARNING, code="override",
                message=f"present in BOTH layers; the {winner} layer wins",
                hint="Remove or rename one copy if the override is unintended.",
            ))
    except Exception as e:  # noqa: BLE001
        logger.debug("Override check skipped for %s: %s", ext, e)


def verify_all(project_root: Optional[Path] = None) -> Dict[str, List[Issue]]:
    """Verify every discovered extension; keyed by directory path string."""
    from boarai.core.extension.discovery import ExtensionDiscovery

    discovery = ExtensionDiscovery(project_root)
    out: Dict[str, List[Issue]] = {}

    candidates: List[Path] = []
    for type_dir in set(ExtensionDiscovery.TYPE_DIRS.values()):
        if type_dir == "skills":
            continue  # SKILL.md bundles are SkillStore's domain
        for base in (discovery._system_dir, discovery._user_dir):
            root = base / type_dir
            if not root.is_dir():
                continue
            for item in sorted(root.iterdir()):
                if item.is_dir() and item.name not in _SKIP_DIRS:
                    candidates.append(item)

    for path in candidates:
        # Only report directories that look like extension attempts (manifest
        # OR python files) — skip empty/data dirs silently.
        has_manifest = _resolve_manifest_file(path).is_file()
        has_py = any(True for _ in _iter_python_files(path))
        if not has_manifest and not has_py:
            continue
        out[str(path)] = verify_extension(path)
    return out


def verify_by_name(name: str, project_root: Optional[Path] = None) -> List[Issue]:
    """Verify a single extension by name (user layer first, then system)."""
    if not is_safe_ext_name(name):
        return [Issue(
            ext=str(name)[:80], level=LEVEL_ERROR, code="path.invalid_name",
            message="extension name must be a bare directory name "
                    "(no path separators, no '..')",
            hint="Pass a plain id like 'wechat_ilink', or use the path "
                 "argument confined to the project/data roots.",
        )]
    from boarai.core.extension.discovery import ExtensionDiscovery

    discovery = ExtensionDiscovery(project_root)
    for type_dir in ExtensionDiscovery.TYPE_DIRS.values():
        for base in (discovery._user_dir, discovery._system_dir):
            candidate = base / type_dir / name
            if candidate.is_dir():
                return verify_extension(candidate)
    return [Issue(
        ext=name, level=LEVEL_ERROR, code="path.not_found",
        message=f"no extension directory named {name!r} in any layer",
    )]


def format_issues(issues: List[Issue]) -> str:
    """Human/CLI rendering of an issue stream."""
    if not issues:
        return "OK: no issues found."
    tags = {LEVEL_ERROR: "[ERR] ", LEVEL_WARNING: "[WARN]", LEVEL_INFO: "[INFO]"}
    lines = []
    for i in issues:
        loc = f" [{i.file}]" if i.file else ""
        unit = f" {i.unit}" if i.unit else ""
        # ASCII separator: Windows consoles with legacy codepages mangle
        # em-dashes into mojibake.
        lines.append(f"{tags.get(i.level, '[?]   ')} {i.ext}{unit} ({i.code}) - {i.message}{loc}")
        if i.hint:
            lines.append(f"         hint: {i.hint}")
    return "\n".join(lines)
