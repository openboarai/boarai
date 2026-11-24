"""Model catalog: the vendor facts the core cannot infer from a request.

One read-only file, ``boar/config/models.yaml``, shipped with the code. It is
deliberately NOT under ``boar/defaults/``, which bootstrap copies into the user
data dir once and never refreshes — a catalog there would freeze at the version
a user first booted.

A model resolves through exact id, then aliases, then the family regexes, in
that order. A model nothing matches is unknown: callers must send no thinking
field and report no context window. That is the deliberate default — inventing a
request field an endpoint may reject turns a cosmetic setting into a 400.

Per-install corrections belong in ``llm.yaml``: ``context_windows`` for a window
the relay serves differently, and a provider's ``options.thinking`` /
``model_info`` for a thinking shape it does. Both are read on top of this file
(``core.prompt.context_detector``, ``core.llm.thinking.wire``), so the shipped
data stays the one home for what a model is.
"""

from __future__ import annotations

import copy
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from boarai.core.runtime import get_project_root

logger = logging.getLogger(__name__)

# Keys a catalog record may carry. Unknown keys are kept (they are data for
# future consumers) but listed here so validate() can flag typos in the keys
# that code actually reads.
KNOWN_RECORD_KEYS = frozenset({
    "id",
    "aliases",
    "vendor",
    "context_window",
    "max_output_tokens",
    "input_modalities",
    "output_modalities",
    "echo_reasoning_content",
    "reasoning_field",
    "forbids_sampling",
    "pricing",
    "thinking",
    "match",
})

KNOWN_THINKING_KEYS = frozenset({
    "format",
    "levels",
    "level_map",
    "can_disable",
    "supports_effort",
    "disable_by_omission",
    "adaptive",
    "effort_field",
    "toggle_field",
    "enable_field",
    "enable_value",
    "disable_value",
    "budget_map",
    "extra_fields",
    "style",
    "effort_map",
})


def shipped_catalog_path() -> Path:
    """Path of the catalog that ships with the code."""
    module_root = Path(__file__).resolve().parents[3]
    candidates = [
        module_root / "boar" / "config" / "models.yaml",
    ]
    project_root = get_project_root()
    if project_root is not None:
        candidates.append(Path(project_root) / "boar" / "config" / "models.yaml")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


@dataclass
class _Catalog:
    """The parsed catalog file, indexed for lookup."""

    path: Path
    models: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    aliases: Dict[str, str] = field(default_factory=dict)
    families: List[Tuple[re.Pattern, Dict[str, Any]]] = field(default_factory=list)
    fingerprint: Tuple[Any, ...] = ()

    def find(self, name: str) -> Optional[Dict[str, Any]]:
        """Resolve one normalized model name."""
        record = self.models.get(name)
        if record is not None:
            return record
        model_id = self.aliases.get(name)
        if model_id is not None:
            return self.models.get(model_id)
        for pattern, family in self.families:
            if pattern.search(name):
                return family
        return None


def _normalized_names(model: str) -> List[str]:
    """Lookup keys for a model, most specific first."""
    raw = str(model or "").strip().lower()
    if not raw:
        return []
    names = [raw]
    if "/" in raw:
        short = raw.split("/", 1)[1].strip()
        if short and short not in names:
            names.append(short)
    return names


def _fingerprint(path: Path) -> Tuple[Any, ...]:
    try:
        stat = path.stat()
    except OSError:
        return (None,)
    return (stat.st_mtime_ns, stat.st_size)


def _build_catalog(path: Path) -> _Catalog:
    """Parse the catalog file into an index."""
    catalog = _Catalog(path=path, fingerprint=_fingerprint(path))
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return catalog
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        logger.warning("Model catalog %s is not valid YAML, ignoring: %s", path, exc)
        return catalog
    if not isinstance(data, dict):
        return catalog

    for entry in data.get("models") or []:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("id") or "").strip()
        if not model_id:
            logger.warning("Model catalog %s has an entry without an id", path)
            continue
        record = {k: v for k, v in entry.items() if k != "id"}
        key = model_id.lower()
        catalog.models[key] = record
        if "/" in key:
            # A qualified id (``relay/model``) must also answer to its bare
            # model name, which is what an upstream request carries. First
            # registration wins; use the qualified id to disambiguate.
            catalog.aliases.setdefault(key.rsplit("/", 1)[-1], key)
        for alias in record.get("aliases") or []:
            alias_key = str(alias).strip().lower()
            if alias_key:
                catalog.aliases.setdefault(alias_key, key)

    for entry in data.get("families") or []:
        if not isinstance(entry, dict):
            continue
        pattern_text = str(entry.get("match") or "").strip()
        if not pattern_text:
            continue
        try:
            pattern = re.compile(pattern_text, re.IGNORECASE)
        except re.error as exc:
            logger.warning("Model catalog %s has a bad family pattern %r: %s", path, pattern_text, exc)
            continue
        catalog.families.append((pattern, {k: v for k, v in entry.items() if k != "match"}))

    return catalog


class _CatalogCache:
    """The catalog plus the mtime check that keeps it fresh."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cached: Optional[_Catalog] = None

    def get(self) -> _Catalog:
        path = shipped_catalog_path()
        fingerprint = _fingerprint(path)
        with self._lock:
            if self._cached is None or self._cached.fingerprint != fingerprint:
                self._cached = _build_catalog(path)
            return self._cached

    def invalidate(self) -> None:
        """Drop the cached catalog (used by tests after editing the file)."""
        with self._lock:
            self._cached = None


_cache = _CatalogCache()


def reload_catalog() -> None:
    """Force the next lookup to re-read the catalog file."""
    _cache.invalidate()


def resolve_record(model: str) -> Optional[Dict[str, Any]]:
    """Resolve one model to its catalog record, or None when unknown."""
    names = _normalized_names(model)
    if not names:
        return None
    catalog = _cache.get()
    for name in names:
        record = catalog.find(name)
        if record is not None:
            return copy.deepcopy(record)
    return None


def get_model_record(model: str) -> Optional[Dict[str, Any]]:
    """Public alias of :func:`resolve_record`."""
    return resolve_record(model)


def get_context_window(model: str) -> Optional[int]:
    """Catalog context window for a model, or None when unknown."""
    record = resolve_record(model)
    if not record:
        return None
    raw = record.get("context_window")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def get_max_output_tokens(model: str) -> Optional[int]:
    """Catalog output ceiling for a model, or None when unknown."""
    record = resolve_record(model)
    if not record:
        return None
    raw = record.get("max_output_tokens")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def echoes_reasoning_content(model: str) -> bool:
    """True when the endpoint requires assistant reasoning echoed back."""
    record = resolve_record(model)
    return bool(record and record.get("echo_reasoning_content"))


def get_reasoning_field(model: str) -> Optional[str]:
    """Catalog-pinned stream field carrying reasoning, or None to auto-detect."""
    record = resolve_record(model)
    if not record:
        return None
    value = record.get("reasoning_field")
    return str(value) if value else None


def forbids_sampling(model: str) -> bool:
    """True when the endpoint rejects a non-default temperature/top_p."""
    record = resolve_record(model)
    return bool(record and record.get("forbids_sampling"))


def validate() -> List[str]:
    """Schema problems in the catalog, for the catalog contract test."""
    from boarai.core.llm.thinking.formats import KNOWN_FORMATS
    from boarai.core.llm.thinking.wire import LEVELS

    problems: List[str] = []

    def check_thinking(where: str, thinking: Any) -> None:
        if not isinstance(thinking, dict):
            problems.append(f"{where}: thinking must be an object")
            return
        # A record may key thinking by endpoint protocol instead of declaring
        # one shape; see wire._select_protocol.
        if thinking and set(thinking) <= {"openai", "anthropic"}:
            for protocol, variant in thinking.items():
                check_thinking(f"{where} [{protocol}]", variant)
            return
        for key in thinking:
            if key not in KNOWN_THINKING_KEYS:
                problems.append(f"{where}: unknown thinking key {key!r}")
        fmt = thinking.get("format")
        if fmt not in KNOWN_FORMATS:
            problems.append(f"{where}: unknown thinking.format {fmt!r}")
        levels = thinking.get("levels")
        level_set = set()
        if levels is not None:
            if not isinstance(levels, list) or len(set(levels)) != len(levels):
                problems.append(f"{where}: levels must be a list without duplicates")
            else:
                unknown_levels = [level for level in levels if level not in LEVELS]
                if unknown_levels:
                    problems.append(f"{where}: unknown level(s) {unknown_levels}")
                level_set = set(levels)
        level_map = thinking.get("level_map")
        if level_map is not None:
            if not isinstance(level_map, dict):
                problems.append(f"{where}: level_map must be an object")
            else:
                for source, target in level_map.items():
                    if not isinstance(source, str) or source not in LEVELS:
                        # An unquoted ``off`` parses as the boolean False.
                        problems.append(
                            f"{where}: level_map key {source!r} is not a requested level "
                            f"(quote \"off\" — YAML 1.1 reads it as False)"
                        )
                        continue
                    if not isinstance(target, str):
                        problems.append(f"{where}: level_map value for {source!r} must be a string")
                        continue
                    if source == "off" or not level_set:
                        continue
                    if target not in level_set:
                        problems.append(
                            f"{where}: level_map value {target!r} for {source!r} is not in levels"
                        )

    catalog = _cache.get()
    for key, record in catalog.models.items():
        where = f"{catalog.path.name} model {key}"
        for field_name in record:
            if field_name not in KNOWN_RECORD_KEYS:
                problems.append(f"{where}: unknown key {field_name!r}")
        window = record.get("context_window")
        if window is not None and (not isinstance(window, int) or isinstance(window, bool) or window <= 0):
            problems.append(f"{where}: context_window must be a positive int")
        ceiling = record.get("max_output_tokens")
        if ceiling is not None and (not isinstance(ceiling, int) or isinstance(ceiling, bool) or ceiling <= 0):
            problems.append(f"{where}: max_output_tokens must be a positive int")
        if record.get("thinking") is not None:
            check_thinking(where, record["thinking"])
    for pattern, record in catalog.families:
        where = f"{catalog.path.name} family {pattern.pattern}"
        for field_name in record:
            if field_name not in KNOWN_RECORD_KEYS:
                problems.append(f"{where}: unknown key {field_name!r}")
        if record.get("thinking") is not None:
            check_thinking(where, record["thinking"])
    return problems


__all__ = [
    "KNOWN_RECORD_KEYS",
    "KNOWN_THINKING_KEYS",
    "echoes_reasoning_content",
    "forbids_sampling",
    "get_context_window",
    "get_max_output_tokens",
    "get_model_record",
    "get_reasoning_field",
    "reload_catalog",
    "resolve_record",
    "shipped_catalog_path",
    "validate",
]
