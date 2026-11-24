# {{name}}

Minimal extension container. `GET /api/ext/{{name}}/ping` returns a health
check once the extension is trusted and loaded.

Add capabilities in `manifest.yaml` under `contributes:` and implement the
referenced handlers in `code.py`. For a settings page, declare
`contributes.ui.panel` (kind: custom) and write your own page under
`static/` — see `boar/prompts/extension/plugins.md` and the full template.
