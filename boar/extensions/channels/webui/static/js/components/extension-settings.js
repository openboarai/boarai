/**
 * ExtensionSettingsModal — host frame for an extension's custom settings page.
 *
 * Settings are ALWAYS a custom panel: an iframe pointing at the extension's
 * own /static/<id>/ page (declared via contributes.ui.panel). The extension
 * page talks to the core through /static/js/boar-sdk.js; this component only
 * provides the dialog chrome and renders declared action-button results
 * (POST .../actions/{id} envelope).
 *
 * It never loads extension code itself, and it cannot: the WebUI is itself an
 * extension and extensions may not import each other.
 *
 * Registered locally by pages/extensions.js (window.ExtensionSettingsModal),
 * so app.js needs no change.
 */
(function (global) {
  'use strict';

  const ExtensionSettingsModal = {
    name: 'ExtensionSettingsModal',
    template: `
      <base-modal :model-value="modelValue"
                  @update:model-value="$emit('update:modelValue', $event)"
                  :title="panelTitle" :icon="panel.icon || 'settings'"
                  width-class="max-w-2xl" body-class="p-5"
                  @close="handleClose">
        <div v-if="loading" class="py-10 flex justify-center">
          <loading-component :text="t('common.loading')"></loading-component>
        </div>

        <div v-else-if="error" class="text-sm text-red-600 dark:text-red-400 break-words">
          {{ error }}
        </div>

        <template v-else>
          <!-- The extension's own page, same origin. -->
          <iframe v-if="iframeSrc"
                  :src="iframeSrc" :title="panelTitle"
                  :style="{ height: panelHeight + 'px' }"
                  class="w-full border-0 rounded-lg bg-white dark:bg-gray-900"></iframe>

          <p v-else class="text-sm text-red-600 dark:text-red-400">
            {{ t('extensions.settingsNoPanel') }}
          </p>

          <!-- Action result (generic primitives: image / kv / markdown / text) -->
          <div v-if="result" class="mt-4 rounded-lg border p-3 space-y-2"
               :class="result.ok === false
                 ? 'border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20'
                 : 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-700/40'">
            <p v-if="result.message" class="text-xs break-words"
               :class="result.ok === false ? 'text-red-700 dark:text-red-300' : 'text-gray-700 dark:text-gray-300'">
              {{ result.message }}
            </p>
            <template v-for="(v, i) in (result.view || [])" :key="i">
              <img v-if="v.type === 'image'" :src="v.src" :alt="v.alt || ''"
                   class="max-w-full rounded-lg border border-gray-200 dark:border-gray-600">
              <dl v-else-if="v.type === 'kv'" class="text-xs space-y-1">
                <div v-for="(row, ri) in (v.rows || [])" :key="ri" class="flex gap-2">
                  <dt class="text-gray-500 dark:text-gray-400 shrink-0">{{ row.label }}</dt>
                  <dd class="text-gray-800 dark:text-gray-200 break-words">{{ row.value }}</dd>
                </div>
              </dl>
              <div v-else-if="v.type === 'markdown'" class="text-xs prose dark:prose-invert max-w-none"
                   v-html="renderMd(v.text)"></div>
              <pre v-else class="text-xs whitespace-pre-wrap break-words text-gray-700 dark:text-gray-300">{{ v.text }}</pre>
            </template>
            <p v-if="polling" class="text-[11px] text-gray-500 dark:text-gray-400 flex items-center gap-1.5">
              <i data-lucide="loader-2" class="w-3 h-3 animate-spin"></i>{{ t('extensions.settingsPolling') }}
            </p>
          </div>

          <!-- Action buttons (a not-loaded extension's handlers are dead code
               — its routes and registrations are unmounted, so hide them
               instead of offering a button that 404s) -->
          <div v-if="actions.length && loaded" class="mt-4 pt-3 border-t border-gray-100 dark:border-gray-700 flex flex-wrap gap-2">
            <button v-for="a in actions" :key="a.id" type="button"
                    :disabled="busy === a.id" :class="actionClass(a)"
                    @click="runAction(a)">
              <i v-if="busy === a.id" data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i>
              <i v-else-if="a.icon" :data-lucide="a.icon" class="w-3.5 h-3.5"></i>
              {{ pick(a.label_i18n, a.label) || a.id }}
            </button>
          </div>
        </template>

        <template #footer>
          <div class="flex items-center justify-end gap-2">
            <base-button variant="ghost" size="sm" @click="handleClose">{{ t('common.cancel') }}</base-button>
          </div>
        </template>
      </base-modal>
    `,
    props: {
      modelValue: { type: Boolean, default: false },
      extensionId: { type: String, default: '' },
      extensionName: { type: String, default: '' },
    },
    emits: ['update:modelValue'],
    data() {
      return {
        loading: false,
        error: '',
        loaded: true,
        panel: {},
        actions: [],
        result: null,
        busy: '',
        polling: false,
        _pollTimer: null,
      };
    },
    computed: {
      panelTitle() {
        return this.pick(this.panel.title_i18n, this.panel.title)
          || this.extensionName || this.extensionId;
      },
      iframeSrc() {
        const url = this.panel.custom_url || '';
        if (!url) return '';
        const joiner = url.indexOf('?') >= 0 ? '&' : '?';
        // ext=<id> is how boar-sdk.js knows which extension it serves; lang=
        // lets the page pick its own i18n bundle (it ships its own strings).
        const lang = (global.i18n && global.i18n.getCurrentLanguage)
          ? global.i18n.getCurrentLanguage() : 'en';
        return url + joiner + 'embed=1&ext=' + encodeURIComponent(this.extensionId)
          + '&lang=' + encodeURIComponent(lang);
      },
      // Defends against a runtime-registered custom_height that is not a
      // number (manifest values are cast in the loader; runtime values are
      // passed through verbatim).
      panelHeight() {
        const h = Number(this.panel.custom_height);
        return Number.isFinite(h) && h > 0 ? h : 480;
      },
    },
    watch: {
      modelValue: {
        immediate: true,
        handler(open) {
          if (open && this.extensionId) this.load();
          if (!open) { this.stopPoll(); this.result = null; }
        },
      },
    },
    beforeUnmount() {
      this.stopPoll();
    },
    methods: {
      // --- i18n for extension-provided labels ---
      pick(map, fallback) {
        if (map && typeof map === 'object') {
          const lang = (global.i18n && global.i18n.getCurrentLanguage)
            ? global.i18n.getCurrentLanguage() : 'en';
          if (map[lang]) return map[lang];
          const short = String(lang).split('-')[0];
          if (map[short]) return map[short];
        }
        return fallback || '';
      },

      actionClass(a) {
        const base = 'inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors disabled:opacity-50 disabled:cursor-not-allowed';
        if (a.style === 'primary') {
          return base + ' text-white bg-primary border-primary hover:bg-primary-dark';
        }
        if (a.style === 'danger') {
          return base + ' text-red-600 dark:text-red-400 border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-900/20';
        }
        return base + ' text-gray-600 dark:text-gray-300 border-gray-200 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700';
      },

      renderMd(text) {
        try {
          if (global.marked && typeof global.marked.parse === 'function') {
            return global.marked.parse(String(text || ''));
          }
        } catch (e) { /* fall through to escaped plain text */ }
        return String(text || '').replace(/[&<>]/g, (c) => (
          { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]
        ));
      },

      // --- load / actions ---
      async load() {
        this.loading = true;
        this.error = '';
        try {
          const resp = await fetch(`/api/extensions/${encodeURIComponent(this.extensionId)}`);
          const json = await resp.json().catch(() => null);
          if (!resp.ok || !json || !json.success) {
            throw new Error((json && json.error && json.error.message) || `HTTP ${resp.status}`);
          }
          const s = (json.data && json.data.settings) || {};
          this.panel = s.panel || {};
          this.actions = Array.isArray(s.actions) ? s.actions : [];
          this.loaded = !!s.loaded;
          this.result = null;
        } catch (e) {
          this.error = String((e && e.message) || e);
        } finally {
          this.loading = false;
        }
      },

      async runAction(a) {
        if (a.confirm) {
          const store = global.BoarAiStore;
          if (store && store.actions.confirm) {
            const ok = await store.actions.confirm({
              title: this.pick(a.label_i18n, a.label) || a.id,
              message: a.confirm,
              confirmText: this.t('extensions.settingsRun'),
              cancelText: this.t('common.cancel'),
              type: a.style === 'danger' ? 'danger' : 'warning',
            });
            if (!ok) return;
          }
        }
        await this.invokeAction(a.id, {});
      },

      async invokeAction(id, params) {
        this.busy = id;
        const store = global.BoarAiStore;
        try {
          const resp = await fetch(
            `/api/extensions/${encodeURIComponent(this.extensionId)}/actions/${encodeURIComponent(id)}`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ params: params || {} }),
            }
          );
          const json = await resp.json().catch(() => null);
          if (!resp.ok || !json || !json.success) {
            throw new Error((json && json.error && json.error.message) || `HTTP ${resp.status}`);
          }
          const data = json.data || {};
          this.result = data;
          if (data.ok === false) {
            if (store) store.actions.error(data.message || this.t('extensions.settingsActionFailed'));
          } else if (data.message && !(data.view && data.view.length)) {
            // A message with no inline view is worth a toast as well.
            if (store) store.actions.success(data.message);
          }
          if (data.reload) await this.load();
          if (data.poll && data.poll.action) this.startPoll(data.poll);
          else this.stopPoll();
          return data;
        } catch (e) {
          this.result = {
            ok: false, code: 'action.failed',
            message: String((e && e.message) || e),
          };
          if (store) store.actions.error(this.result.message);
          this.stopPoll();
          return null;
        } finally {
          this.busy = '';
        }
      },

      startPoll(poll) {
        this.stopPoll();
        const interval = Math.max(500, poll.interval_ms || 2000);
        const deadline = Date.now() + Math.max(1000, poll.max_ms || 120000);
        this.polling = true;
        this._pollTimer = setInterval(async () => {
          if (Date.now() > deadline || !this.modelValue) { this.stopPoll(); return; }
          const data = await this.invokeAction(poll.action, {});
          // The polled action decides when it is done by omitting `poll`.
          if (!data || !data.poll) this.stopPoll();
        }, interval);
      },

      stopPoll() {
        if (this._pollTimer) { clearInterval(this._pollTimer); this._pollTimer = null; }
        this.polling = false;
      },

      handleClose() {
        this.stopPoll();
        this.$emit('update:modelValue', false);
      },
    },
  };

  global.ExtensionSettingsModal = ExtensionSettingsModal;
})(window);
