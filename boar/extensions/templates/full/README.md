# {{name}}

Kitchen-sink extension: tools, routes, hooks, hook_points, services and a
settings panel, all declared in `manifest.yaml` and implemented in `code.py`.

The settings page is a **custom panel**: `static/settings.html` runs in a
same-origin iframe and talks to the core through `/static/js/boar-sdk.js`
(`boar.getConfig` / `boar.saveConfig` / `boar.getData` / `boar.saveData` /
`boar.callAction`). Defaults seed from `config_defaults` in the manifest;
saved values persist to `.boarai/extensions/data/{{name}}/config.json` and
live-apply via `on_config_changed` — see `extension.py`.

Full contract: `boar/prompts/extension/plugins.md` (+ `zh/`).
