> **English** | [简体中文](config_zh.md)

# Configuration System (core/config)

Parsing, schema and corrupt-input fallback for the main config file `<data>/config/config.yaml`.

## Modules

| File | Responsibility |
|------|----------------|
| `schema.py` | `BoarAiConfig` and per-section dataclasses (system / llm / plugin / session / logging / security / thinking / run); sensitive-file pattern list |
| `parser.py` | `ConfigParser`: load, format detection, validation, save |

## Behaviour contract

- **A missing file is the NORMAL first-run state**: no error (INFO level), built-in defaults are adopted, and the file is written lazily on the first settings change; defaults template files are never created eagerly.
- **Corruption falls back to defaults, never bricks startup**: read failure / non-mapping / YAML with legacy python-tags all log `logger.error` + adopt defaults, hinting to re-save from the UI to rewrite a clean file.
- Fields failing validation fall back to that field's default; the whole file is never rejected.
- Non-schema top-level keys (e.g. `bootstrap`) are preserved across `save()` — saving must not eat configuration keys it does not own.
- Saves go through atomic writes (`write_text_atomic_async`); a crash never leaves a half config.yaml.
- `cp._config` must be populated even on the missing-file path: callers gate their write path on it (otherwise every save becomes a silent no-op and the file can never be created).

## Boundaries

- LLM keys and Base URLs do NOT live in config.yaml; they live in `<data>/config/llm.yaml` (owned by the LLM subsystem) and are on the redaction pattern list (`**/auth.json`, `.boarai/config/llm.yaml`).
- Extension configuration is not here either: it goes through the extension facade's `get_config/set_config`, persisted in the extension's private data dir.
- Config changes publish `config.changed` / `config.reloaded` events (registered in the event catalogue; published by the consumers).
