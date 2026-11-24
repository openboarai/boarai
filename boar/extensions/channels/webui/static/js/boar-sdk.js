/**
 * boar-sdk.js — the tiny client library for an extension's custom settings
 * panel (contributes.ui.panel.kind: custom).
 *
 * Your settings page runs in a same-origin iframe at /static/<your-id>/….
 * Load this first:
 *
 *   <script src="/static/js/boar-sdk.js"></script>
 *
 * then use the global `boar` object:
 *
 *   const cfg  = await boar.getConfig();           // your config.json values
 *   await boar.saveConfig({ key: value });          // merge + live-apply
 *   const data = await boar.getData("cache");       // private data map value
 *   await boar.saveData("cache", {...});            // merge into data.json
 *   const r    = await boar.callAction("id", {});   // run a settings action
 *
 * Your extension id is taken from the iframe URL (?ext=<id>, added by the
 * settings dialog). API calls ride the same-origin session cookie the WebUI
 * itself uses — the access key is never exposed to page JavaScript, so there
 * is nothing to bootstrap here.
 */
(function (global) {
  'use strict';

  const nativeFetch = global.fetch.bind(global);

  function extensionId() {
    const params = new URLSearchParams(global.location.search);
    return params.get('ext') || '';
  }

  async function api(method, path, body) {
    const resp = await nativeFetch(path, {
      method,
      cache: 'no-store',
      // Same-origin iframe: the HttpOnly session cookie is sent automatically.
      credentials: 'same-origin',
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const json = await resp.json().catch(() => null);
    if (!resp.ok || !json || json.success !== true) {
      const msg = (json && json.error && json.error.message)
        || ('HTTP ' + resp.status);
      throw new Error(msg);
    }
    return json.data;
  }

  const enc = encodeURIComponent;

  global.boar = {
    /** The extension id this panel belongs to (from the iframe URL). */
    extensionId,

    /** Full config.json of this extension ({} when nothing persisted). */
    getConfig() {
      return api('GET', '/api/extensions/' + enc(extensionId()))
        .then((d) => (d && d.settings && d.settings.values) || {});
    },

    /** Merge keys into config.json; the running extension applies them live. */
    saveConfig(values) {
      return api('PUT', '/api/extensions/' + enc(extensionId()) + '/settings',
        { values });
    },

    /** One value from this extension's private data map. */
    async getData(key) {
      const d = await api('GET', '/api/extensions/' + enc(extensionId()) + '/data');
      return (d && d.values) ? d.values[key] : undefined;
    },

    /** Merge keys into this extension's private data map (data.json). */
    saveData(values) {
      return api('PUT', '/api/extensions/' + enc(extensionId()) + '/data',
        { values });
    },

    /** Run one of this extension's declared settings actions; returns the
     * result envelope {ok, message, view, poll, reload}. */
    callAction(actionId, params) {
      return api('POST',
        '/api/extensions/' + enc(extensionId()) + '/actions/' + enc(actionId),
        { params: params || {} });
    },
  };
})(window);
