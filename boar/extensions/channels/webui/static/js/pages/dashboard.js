/**
 * Dashboard Page Component
 *
 * Real data dashboard showing token usage, sessions, extensions, LLM status
 * and recent requests — all from live API endpoints.
 * Uses TailwindCSS for styling. Supports i18n. Uses Lucide Icons.
 */

const DashboardPage = {
  name: 'DashboardPage',

  template: `
    <div class="max-w-7xl mx-auto px-4 py-6 animate-fade-in">

      <!-- Status Bar -->
      <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-3 mb-6">
        <!-- The chip row used to be a single non-wrapping line: at 390px it
             overflowed the card by 65px and made the whole page pan. -->
        <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-sm text-gray-500 dark:text-gray-500">
          <div class="flex items-center gap-2 shrink-0">
            <span class="w-2 h-2 rounded-full bg-green-500"></span>
            <span class="font-medium text-gray-900 dark:text-white">{{ t('dashboard.running') }}</span>
          </div>
          <div class="flex flex-wrap items-center gap-x-4 gap-y-1 min-w-0">
            <span class="flex items-center gap-1">
              <i data-lucide="cpu" class="w-3.5 h-3.5"></i>
              {{ llmStatus.default_model || '-' }}
            </span>
            <span class="flex items-center gap-1">
              <i data-lucide="plug" class="w-3.5 h-3.5"></i>
              {{ llmStatus.total_providers || 0 }} {{ t('dashboard.providers') }}
            </span>
            <span class="flex items-center gap-1">
              <i data-lucide="boxes" class="w-3.5 h-3.5"></i>
              {{ extensions.running || 0 }}/{{ extensions.total || 0 }} {{ t('dashboard.extensions') }}
            </span>
            <span class="flex items-center gap-1">
              <i data-lucide="message-square" class="w-3.5 h-3.5"></i>
              {{ sessions.total || 0 }} {{ t('dashboard.sessions') }}
            </span>
          </div>
        </div>
      </div>

      <!-- 4 Stat Cards -->
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">

        <!-- Tokens Card -->
        <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4">
          <div class="flex items-center justify-between mb-3">
            <span class="text-sm font-medium text-gray-500 dark:text-gray-500">{{ t('dashboard.tokenUsage') }}</span>
            <div class="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center">
              <i data-lucide="coins" class="w-4 h-4 text-primary"></i>
            </div>
          </div>
          <div class="text-2xl font-bold text-gray-900 dark:text-white mb-1">{{ formatNumber(tokens.total) }}</div>
          <div class="text-xs text-gray-500 dark:text-gray-500 mb-3">{{ t('dashboard.totalTokens') }}</div>
          <div class="flex items-center justify-between text-xs mb-2">
            <span class="text-gray-500 dark:text-gray-500">{{ t('dashboard.todayTokens') }}</span>
            <span class="font-medium text-gray-900 dark:text-white tabular-nums">{{ formatNumber(tokens.today) }}</span>
          </div>
          <div class="flex items-center justify-between text-xs">
            <span class="text-gray-500 dark:text-gray-500">{{ t('dashboard.hitRate') }}</span>
            <div class="flex items-center gap-2">
              <div class="w-14 h-1.5 bg-gray-200 dark:bg-gray-600 rounded-full overflow-hidden">
                <div class="h-full rounded-full transition-all"
                     :class="tokens.hit_rate > 70 ? 'bg-emerald-500' : 'bg-amber-500'"
                     :style="{ width: (tokens.hit_rate || 0) + '%' }"></div>
              </div>
              <span class="font-medium text-gray-900 dark:text-white tabular-nums w-9 text-right">{{ (tokens.hit_rate || 0).toFixed(0) }}%</span>
            </div>
          </div>
        </div>

        <!-- Sessions Card -->
        <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4">
          <div class="flex items-center justify-between mb-3">
            <span class="text-sm font-medium text-gray-500 dark:text-gray-500">{{ t('dashboard.sessions') }}</span>
            <div class="w-8 h-8 rounded-lg bg-blue-500/10 flex items-center justify-center">
              <i data-lucide="message-square" class="w-4 h-4 text-blue-500"></i>
            </div>
          </div>
          <div class="text-2xl font-bold text-gray-900 dark:text-white mb-1">{{ sessions.total || 0 }}</div>
          <div class="text-xs text-gray-500 dark:text-gray-500 mb-3">{{ t('dashboard.totalSessions') }}</div>
          <div class="flex items-center justify-between text-xs mb-2">
            <span class="text-gray-500 dark:text-gray-500">{{ t('dashboard.active') }}</span>
            <span class="font-medium text-green-600 dark:text-green-400 tabular-nums">{{ sessions.active || 0 }}</span>
          </div>
          <div class="flex items-center justify-between text-xs">
            <span class="text-gray-500 dark:text-gray-500">{{ t('dashboard.today') }}</span>
            <span class="font-medium text-gray-900 dark:text-white tabular-nums">{{ sessions.today || 0 }}</span>
          </div>
        </div>

        <!-- Extensions Card -->
        <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4">
          <div class="flex items-center justify-between mb-3">
            <span class="text-sm font-medium text-gray-500 dark:text-gray-500">{{ t('dashboard.extensions') }}</span>
            <div class="w-8 h-8 rounded-lg bg-amber-500/10 flex items-center justify-center">
              <i data-lucide="puzzle" class="w-4 h-4 text-amber-500"></i>
            </div>
          </div>
          <div class="text-2xl font-bold text-gray-900 dark:text-white mb-1">{{ extensions.total || 0 }}</div>
          <div class="text-xs text-gray-500 dark:text-gray-500 mb-3">{{ t('dashboard.totalExtensions') }}</div>
          <div class="flex justify-between text-xs">
            <span class="flex items-center gap-1">
              <span class="w-1.5 h-1.5 rounded-full bg-green-500"></span>
              <span class="text-gray-500 dark:text-gray-500">{{ t('dashboard.running') }}</span>
              <span class="font-medium text-gray-900 dark:text-white tabular-nums">{{ extensions.running || 0 }}</span>
            </span>
            <span class="flex items-center gap-1">
              <span class="w-1.5 h-1.5 rounded-full bg-red-500"></span>
              <span class="text-gray-500 dark:text-gray-500">{{ t('dashboard.error') }}</span>
              <span class="font-medium text-gray-900 dark:text-white tabular-nums">{{ extensions.error || 0 }}</span>
            </span>
            <span class="flex items-center gap-1">
              <span class="w-1.5 h-1.5 rounded-full bg-gray-300 dark:bg-gray-600"></span>
              <span class="text-gray-500 dark:text-gray-500">{{ t('dashboard.disabled') }}</span>
              <span class="font-medium text-gray-900 dark:text-white tabular-nums">{{ extensions.disabled || 0 }}</span>
            </span>
          </div>
        </div>

        <!-- LLM Card -->
        <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4">
          <div class="flex items-center justify-between mb-3">
            <span class="text-sm font-medium text-gray-500 dark:text-gray-500">{{ t('dashboard.llm') }}</span>
            <div class="w-8 h-8 rounded-lg bg-purple-500/10 flex items-center justify-center">
              <i data-lucide="brain" class="w-4 h-4 text-purple-500"></i>
            </div>
          </div>
          <div class="text-2xl font-bold text-gray-900 dark:text-white mb-1">{{ llmStatus.total_providers || 0 }}</div>
          <div class="text-xs text-gray-500 dark:text-gray-500 mb-3">{{ t('dashboard.providers') }}</div>
          <div class="flex items-center justify-between text-xs mb-2">
            <span class="text-gray-500 dark:text-gray-500">{{ t('dashboard.models') }}</span>
            <span class="font-medium text-gray-900 dark:text-white tabular-nums">{{ (llmStatus.available_models || []).length }}</span>
          </div>
          <div class="flex items-center justify-between text-xs">
            <span class="text-gray-500 dark:text-gray-500">{{ t('dashboard.defaultModel') }}</span>
            <span class="font-medium text-gray-900 dark:text-white text-xs truncate max-w-[140px] text-right">{{ llmStatus.default_model || '-' }}</span>
          </div>
        </div>
      </div>

      <!-- Bottom Row -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">

        <!-- Recent Requests -->
        <div class="lg:col-span-3 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4">
          <h3 class="text-sm font-semibold text-gray-900 dark:text-white mb-3 flex items-center gap-2">
            <i data-lucide="activity" class="w-4 h-4 text-gray-500"></i>
            {{ t('dashboard.recentRequests') }}
          </h3>
          <div v-if="recentRequests.length === 0" class="text-sm text-gray-500 dark:text-gray-500 text-center py-8">
            {{ t('dashboard.noRequests') }}
          </div>
          <div v-else class="overflow-x-auto">
            <table class="w-full text-xs">
              <thead>
                <tr class="text-gray-500 dark:text-gray-500 border-b border-gray-100 dark:border-gray-700">
                  <th class="text-left font-medium pb-2 pr-3">{{ t('dashboard.agent') }}</th>
                  <th class="text-left font-medium pb-2 pr-3">{{ t('dashboard.modelShort') }}</th>
                  <th class="text-right font-medium pb-2 pr-3">{{ t('dashboard.hitRate') }}</th>
                  <th class="text-right font-medium pb-2">{{ t('dashboard.tokens') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(r, i) in recentRequests" :key="i"
                    class="border-b border-gray-50 dark:border-gray-700/50 hover:bg-gray-50 dark:hover:bg-gray-700/30 transition-colors">
                  <td class="py-2 pr-3 text-gray-700 dark:text-gray-300">{{ r.agent || '-' }}</td>
                  <td class="py-2 pr-3 text-gray-500 dark:text-gray-500 font-mono">{{ r.model ? r.model.split('/').pop() : '-' }}</td>
                  <td class="py-2 pr-3 text-right">
                    <span class="inline-flex items-center gap-1"
                          :class="r.hit_rate > 70 ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'">
                      <i :data-lucide="r.hit_rate > 70 ? 'check-circle' : 'alert-circle'" class="w-3 h-3"></i>
                      {{ (r.hit_rate || 0).toFixed(0) }}%
                    </span>
                  </td>
                  <td class="py-2 text-right font-mono tabular-nums text-gray-700 dark:text-gray-300">{{ formatNumber(r.input_tokens) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  `,

  data() {
    return {
      tokens: { total: 0, today: 0, hit_rate: 0 },
      sessions: { total: 0, active: 0, today: 0 },
      extensions: { total: 0, running: 0, error: 0, disabled: 0 },
      llmStatus: { total_providers: 0, default_model: '', available_models: [] },
      recentRequests: [],
    };
  },

  mounted() {
    this.loadAll();
    this._refreshTimer = setInterval(() => this.loadAll(), 30000);
  },

  // Icons hydrate automatically via app.js MutationObserver.
  updated() {},

  beforeUnmount() {
    if (this._refreshTimer) clearInterval(this._refreshTimer);
  },

  methods: {
    formatNumber(num) {
      if (!num) return '0';
      if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
      if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
      return String(num);
    },

    async loadAll() {
      await Promise.all([
        this.loadMonitoringStats(),
        this.loadLLMStatus(),
        this.loadExtensions(),
      ]);
    },

    async loadMonitoringStats() {
      try {
        const res = await fetch('/api/monitoring/stats');
        const json = await res.json();
        if (!json.success) return;
        const d = json.data;
        this.tokens = d.tokens || { total: 0, today: 0, hit_rate: 0 };
        this.sessions = { ...(d.sessions || {}), today: (d.sessions && d.sessions.today) || d.sessions?.active || 0 };

        // Build recent requests with hit_rate computed
        const raw = (d.recent_requests || []).slice(0, 20);
        this.recentRequests = raw.map(r => ({
          ...r,
          hit_rate: r.input_tokens > 0
            ? ((r.cache_read_tokens || 0) / r.input_tokens) * 100
            : 0,
        }));
      } catch (e) {
        console.warn('Failed to load monitoring stats:', e);
      }
    },

    async loadLLMStatus() {
      try {
        const res = await fetch('/api/llm/status');
        const json = await res.json();
        if (json.success) this.llmStatus = json.data;
      } catch (e) {
        console.warn('Failed to load LLM status:', e);
      }
    },

    async loadExtensions() {
      try {
        const res = await fetch('/api/extensions/stats');
        const json = await res.json();
        if (!json.success) return;
        const d = json.data;
        this.extensions = {
          total: d.total_extensions || 0,
          running: d.status_counts?.running || 0,
          error: d.status_counts?.error || 0,
          disabled: d.status_counts?.disabled || 0,
        };
      } catch (e) {
        console.warn('Failed to load extensions:', e);
      }
    },
  },
};
