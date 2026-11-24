/**
 * Extensions Page Component
 * 
 * Extensions management view with secondary navigation.
 * Uses TailwindCSS for styling.
 * Supports internationalization (i18n).
 * Uses Lucide Icons for all icons.
 * Integrates with BoarAiStore for state management.
 */

const ExtensionsPage = {
  name: 'ExtensionsPage',
  
  template: `
    <div class="max-w-7xl mx-auto px-4 py-6 animate-fade-in">
      <!-- Page Header -->
      <!-- Secondary Navigation -->
      <div class="mb-6">
        <tab-bar :model-value="activeTab" :tabs="tabs"
                 @update:model-value="activeTab = $event"></tab-bar>
      </div>

      <!-- Loading State -->
      <div v-if="extensionsLoading" class="flex items-center justify-center py-12">
        <loading-component size="lg" :text="t('common.loading')"></loading-component>
      </div>

      <!-- Tab Content -->
      <div v-else class="space-y-4">
        
        <!-- Extensions List -->
        <div v-for="tab in tabs" :key="'content-' + tab.id">
          <div v-if="activeTab === tab.id" class="space-y-4">
            
            <!-- Empty State -->
            <div v-if="getExtensionsByType(tab.id).length === 0" 
                 class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
              <empty-state-component 
                :icon="getTabIcon(tab.id)"
                :title="t('extensions.noExtensions')"
                :description="t('extensions.noExtensionsDesc')">
              </empty-state-component>
            </div>
            
            <!-- Extension Cards -->
            <div v-else class="grid grid-cols-1 md:grid-cols-2 gap-4 items-stretch">
              <div v-for="ext in getExtensionsByType(tab.id)" :key="ext.id"
                   class="flex flex-col bg-white dark:bg-gray-800 rounded-xl border overflow-hidden hover:shadow-md transition-shadow duration-200"
                   :class="ext.status === 'failed'
                     ? 'border-red-300 dark:border-red-800'
                     : 'border-gray-200 dark:border-gray-700'">
                <div class="flex flex-col flex-1 p-4">
                  <div class="flex items-start justify-between pb-3">
                    <!-- Extension Info -->
                    <div class="flex items-start gap-3 flex-1 min-w-0">
                      <div class="flex-shrink-0 w-10 h-10 rounded-lg flex items-center justify-center"
                           :class="ext.status === 'failed'
                             ? 'bg-red-100 dark:bg-red-900/40 text-red-600 dark:text-red-400'
                             : (ext.enabled
                             ? 'bg-primary/10 text-primary'
                             : 'bg-gray-100 dark:bg-gray-700 text-gray-500')">
                        <i :data-lucide="ext.status === 'failed' ? 'alert-triangle' : getTabIcon(tab.id)" class="w-5 h-5"></i>
                      </div>
                      <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2">
                          <h3 class="text-sm font-semibold text-gray-900 dark:text-white truncate">{{ ext.name }}</h3>
                          <span v-if="ext.status === 'failed'"
                                class="inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300">
                            {{ t('extensions.loadFailed') }}
                          </span>
                        </div>
                        <p class="text-xs text-gray-500 dark:text-gray-500 mt-1 line-clamp-2">{{ ext.description }}</p>
                        <div class="flex items-center gap-2 mt-2">
                          <span v-if="ext.version" class="text-xs text-gray-500 dark:text-gray-500">v{{ ext.version }}</span>
                          <span v-if="ext.source === 'system'" class="inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded bg-purple-100 dark:bg-purple-900/40 text-purple-700 dark:text-purple-300">{{ t('extensions.sourceSystem') }}</span>
                          <span v-else-if="ext.source === 'agent'" class="inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300">{{ t('extensions.sourceLocal') }}</span>
                          <span v-else-if="ext.source === 'local'" class="inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300">{{ t('extensions.sourceLocal') }}</span>
                          <span v-else-if="ext.source === 'user'" class="inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300">{{ t('extensions.sourceUser') }}</span>
                          <span v-if="ext.trusted && ext.source !== 'system'" :title="t('extensions.trustedHint')"
                                class="inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300">
                            <i data-lucide="shield-check" class="w-3 h-3 mr-0.5"></i>{{ t('extensions.trustedBadge') }}
                          </span>
                        </div>
                        <!-- v2: contributes capability badges -->
                        <div v-if="hasContributions(ext)" class="flex flex-wrap gap-1 mt-2">
                          <span v-for="(c, ci) in contributionBadges(ext)" :key="ci"
                                :title="c.title"
                                class="inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">
                            {{ c.label }}
                          </span>
                        </div>
                        <!-- Load failure detail: error + actionable hint (the
                             answer to "code or declaration?" must not require
                             reading server logs) -->
                        <div v-if="ext.status === 'failed'"
                             class="mt-2 text-xs rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 p-2 space-y-1">
                          <p class="text-red-700 dark:text-red-300 break-words line-clamp-3">{{ ext.error }}</p>
                          <p v-if="ext.hint" class="text-red-600/90 dark:text-red-400/90 break-words">{{ ext.hint }}</p>
                          <p class="text-[10px] text-red-500 dark:text-red-400 font-mono">
                            {{ ext.failure_code }}<template v-if="ext.attempts"> · {{ t('extensions.retryCount', { count: ext.attempts }) }}</template>
                          </p>
                        </div>
                      </div>
                    </div>

                    <!-- Toggle Switch / Retry -->
                    <div class="flex-shrink-0 ml-4">
                      <button v-if="ext.status === 'failed'" @click="toggleExtension(tab.id, ext.id, true)"
                              :title="t('extensions.retryLoad')"
                              class="inline-flex items-center justify-center w-9 h-9 rounded-lg text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors">
                        <i data-lucide="refresh-cw" class="w-4 h-4"></i>
                      </button>
                      <toggle-switch v-else :model-value="ext.enabled" size="lg"
                                     @update:model-value="toggleExtension(tab.id, ext.id, $event)"></toggle-switch>
                    </div>
                  </div>

                  <!-- Actions -->
                  <div class="flex items-center justify-end gap-2 mt-auto pt-3 border-t border-gray-100 dark:border-gray-700">
                    <template v-if="ext.status === 'failed'">
                      <button v-if="ext.failure_code === 'gate.inprocess_untrusted'"
                              @click="trustExtension(tab.id, ext.id)"
                              class="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-amber-700 dark:text-amber-300 rounded-lg bg-amber-50 dark:bg-amber-900/20 hover:bg-amber-100 dark:hover:bg-amber-900/40 border border-amber-200 dark:border-amber-800 transition-colors">
                        <i data-lucide="shield-check" class="w-3.5 h-3.5"></i>
                        {{ t('extensions.trustAndLoad') }}
                      </button>
                      <button @click="openSettings(ext)"
                              class="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-600 dark:text-gray-500 hover:text-gray-900 dark:hover:text-white rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors">
                        <i data-lucide="settings" class="w-3.5 h-3.5"></i>
                        {{ t('extensions.settings') }}
                      </button>
                      <button @click="stopFailedRetries(tab.id, ext.id)"
                              class="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors">
                        <i data-lucide="ban" class="w-3.5 h-3.5"></i>
                        {{ t('extensions.stopRetries') }}
                      </button>
                    </template>
                    <template v-else>
                      <button v-if="ext.source !== 'system' && ext.trusted" @click="untrustExtension(tab.id, ext.id)"
                              class="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-amber-700 dark:text-amber-300 hover:text-amber-800 dark:hover:text-amber-200 rounded-lg hover:bg-amber-50 dark:hover:bg-amber-900/20 transition-colors">
                        <i data-lucide="shield-off" class="w-3.5 h-3.5"></i>
                        {{ t('extensions.revokeTrust') }}
                      </button>
                      <!-- Hidden when the extension declares nothing to
                           configure: an empty dialog is worse than no button. -->
                      <button v-if="ext.has_settings" @click="openSettings(ext)"
                              class="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-600 dark:text-gray-500 hover:text-gray-900 dark:hover:text-white rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors">
                        <i data-lucide="settings" class="w-3.5 h-3.5"></i>
                        {{ t('extensions.settings') }}
                      </button>
                      <button v-if="ext.source !== 'system'" @click="uninstallExtension(tab.id, ext.id)"
                              class="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors">
                        <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
                        {{ t('extensions.uninstall') }}
                      </button>
                    </template>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Settings dialog: a custom iframe panel + declared action buttons -->
      <extension-settings-modal v-model="settingsOpen"
                                :extension-id="settingsId"
                                :extension-name="settingsName"></extension-settings-modal>
    </div>
  `,

  components: {
    // Registered locally so app.js needs no change; index.html loads the
    // component script before the pages.
    'extension-settings-modal': window.ExtensionSettingsModal,
  },

  data() {
    return {
      activeTab: 'adapters',
      tabs: [],
      settingsOpen: false,
      settingsId: '',
      settingsName: ''
    };
  },
  
  computed: {
    extensionsLoading() {
      return window.BoarAiStore ? window.BoarAiStore.state.extensionsLoading : false;
    },
    
    extensions() {
      return window.BoarAiStore ? window.BoarAiStore.state.extensions : { adapters: [], tools: [], plugins: [], skills: [], mcp: [], failed: [] };
    },
    
    enabledCount() {
      const all = [
        ...this.extensions.adapters,
        ...this.extensions.tools,
        ...this.extensions.plugins,
        ...this.extensions.skills,
        ...this.extensions.mcp
      ];
      return all.filter(e => e.enabled).length;
    }
  },
  
  mounted() {
    this.updateTabLabels();
    this.loadExtensions();
  },
  
  // Icons hydrate automatically via app.js MutationObserver.
  updated() {},

  watch: {
    // deep: store may mutate entries in place (toggle en/disable) without
    // replacing the reference — a shallow watch would miss count updates.
    extensions: {
      deep: true,
      handler() {
        this.updateTabLabels();
      },
    },
  },

  methods: {
    // handleLanguageChange overrides global I18nMixin: update currentLanguage
    // (kept by mixin listener) and also refresh tab labels.
    handleLanguageChange() {
      this.currentLanguage = window.i18n ? window.i18n.getCurrentLanguage() : 'en';
      this.updateTabLabels();
    },
    
    updateTabLabels() {
      this.tabs = [
        { id: 'adapters', label: this.t('extensions.adapters') || 'Adapters', count: this.getTabCount('adapters') },
        { id: 'tools', label: this.t('extensions.tools') || 'Tools', count: this.getTabCount('tools') },
        { id: 'plugins', label: this.t('extensions.plugins') || 'Plugins', count: this.getTabCount('plugins') },
        { id: 'skills', label: this.t('extensions.skills') || 'Skills', count: this.getTabCount('skills') },
        { id: 'mcp', label: this.t('extensions.mcp') || 'MCP', count: this.getTabCount('mcp') }
      ];
    },
    
    async loadExtensions() {
      if (window.BoarAiStore) {
        await window.BoarAiStore.actions.loadExtensions();
      }
    },

    // The dialog fetches the descriptor itself (GET /api/extensions/{id}), so
    // this only has to say which extension to show.
    openSettings(ext) {
      this.settingsId = ext.id;
      this.settingsName = ext.name || ext.id;
      this.settingsOpen = true;
    },
    
    getExtensionsByType(type) {
      const loaded = this.extensions[type] || [];
      // Discovered-but-failed loads render as cards in their manifest type's
      // tab (red border, error + hint, retry / stop-retries actions).
      const typeMap = { channel: 'adapters', tool: 'tools', plugin: 'plugins', skill: 'skills', mcp: 'mcp' };
      const failed = (this.extensions.failed || [])
        .filter((f) => (typeMap[f.extension_type] || 'plugins') === type)
        .map((f) => ({
          id: f.name,
          name: f.name,
          description: f.description || '',
          version: f.version || '',
          enabled: false,
          status: 'failed',
          source: f.source || 'user',
          error: f.error || '',
          hint: f.hint || '',
          failure_code: f.code || '',
          attempts: f.attempts || 0,
          trusted: !!f.trusted,
          contributes: {},
        }));
      return [...loaded, ...failed];
    },

    async stopFailedRetries(type, id) {
      if (!window.BoarAiStore) return;
      const confirmed = await window.BoarAiStore.actions.confirm({
        title: this.t('extensions.stopRetriesConfirmTitle'),
        message: this.t('extensions.stopRetriesConfirmMessage'),
        confirmText: this.t('extensions.stopRetries'),
        cancelText: this.t('common.cancel'),
        type: 'danger'
      });
      if (!confirmed) return;
      // Disable persists (state.json) → hot discovery skips it → retries stop.
      await window.BoarAiStore.actions.toggleExtension(type, id, false);
    },

    async trustExtension(type, id) {
      if (!window.BoarAiStore) return;
      const confirmed = await window.BoarAiStore.actions.confirm({
        title: this.t('extensions.trustConfirmTitle'),
        message: this.t('extensions.trustConfirmMessage'),
        confirmText: this.t('extensions.trustAndLoad'),
        cancelText: this.t('common.cancel'),
        type: 'warning'
      });
      if (!confirmed) return;
      // Per-extension operator grant: persisted in state.json, audited
      // server-side at WARNING, revocable from the loaded card.
      await window.BoarAiStore.actions.trustExtension(id);
    },

    async untrustExtension(type, id) {
      if (!window.BoarAiStore) return;
      const confirmed = await window.BoarAiStore.actions.confirm({
        title: this.t('extensions.revokeConfirmTitle'),
        message: this.t('extensions.revokeConfirmMessage'),
        confirmText: this.t('extensions.revokeTrust'),
        cancelText: this.t('common.cancel'),
        type: 'danger'
      });
      if (!confirmed) return;
      await window.BoarAiStore.actions.untrustExtension(id);
    },
    
    hasContributions(ext) {
      return !!(ext.contributes && (ext.contributes.tools?.length || ext.contributes.routes?.length || ext.contributes.hooks?.length || ext.contributes.hook_points?.length || ext.contributes.services?.length));
    },

    // Capability chips. Each chip is {label, title}: the label is localized,
    // the title lists the underlying names so "hook points:2" is actionable.
    contributionBadges(ext) {
      const c = ext.contributes || {};
      const badges = [];
      const add = (i18nKey, items) => {
        if (!items?.length) return;
        badges.push({
          label: `${this.t(i18nKey)} ${items.length}`,
          title: items.map((i) => (Array.isArray(i) ? i.join(' / ') : i)).join(', '),
        });
      };
      add('extensions.contributesTools', c.tools);
      add('extensions.contributesRoutes', c.routes);
      add('extensions.contributesHooks', c.hooks);
      add('extensions.contributesHookPoints', c.hook_points);
      add('extensions.contributesServices', c.services);
      return badges;
    },

    getTabCount(tabId) {
      return this.getExtensionsByType(tabId).length;
    },
    
    getTabIcon(tabId) {
      const icons = {
        adapters: 'plug',
        tools: 'wrench',
        plugins: 'puzzle',
        skills: 'sparkles',
        mcp: 'network'
      };
      return icons[tabId] || 'box';
    },
    
    async toggleExtension(type, id, enabled) {
      if (window.BoarAiStore) {
        await window.BoarAiStore.actions.toggleExtension(type, id, enabled);
      }
    },
    
    async uninstallExtension(type, id) {
      if (window.BoarAiStore) {
        const confirmed = await window.BoarAiStore.actions.confirm({
          title: this.t('extensions.uninstallConfirmTitle'),
          message: this.t('extensions.uninstallConfirmMessage'),
          confirmText: this.t('extensions.uninstall'),
          cancelText: this.t('common.cancel'),
          type: 'danger'
        });
        
        if (confirmed) {
          const result = await window.BoarAiStore.actions.uninstallExtension(type, id);
          if (result) {
            window.BoarAiStore.actions.success(this.t('extensions.uninstallSuccess'));
            await this.loadExtensions();
          }
        }
      }
    }
  }
};
