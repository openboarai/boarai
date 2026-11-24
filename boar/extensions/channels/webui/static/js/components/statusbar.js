/**
 * Status Bar Component for BoarAi WebUI
 * 
 * Bottom status bar showing connection status, system info, and uptime.
 * Uses TailwindCSS for styling.
 * Uses Lucide Icons for all icons.
 * Fetches real server metrics from /api/status.
 */

const StatusbarComponent = {
  name: 'Statusbar',
  
  template: `
    <footer class="fixed bottom-0 left-0 right-0 h-statusbar bg-white dark:bg-gray-900 border-t border-gray-200 dark:border-gray-700 flex items-center px-3 md:px-4 text-xs text-gray-500 dark:text-gray-500 z-[1030]">
      <!-- Connection status. On phones the bar is only 28px tall and keeps a
           single readout (latency) plus this colour dot, so the "WS /
           CONNECTED" wording is desktop-only. -->
      <div class="flex items-center gap-1.5 px-1 sm:px-2 py-0.5 sm:py-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800 cursor-default transition-colors duration-150">
        <span class="relative flex h-2 w-2 shrink-0">
          <span class="absolute inline-flex h-full w-full rounded-full opacity-75"
                :class="{
                  'bg-success animate-ping': connectionStatus === 'connected',
                  'bg-error': connectionStatus === 'disconnected',
                  'bg-warning animate-pulse': connectionStatus === 'connecting'
                }"></span>
          <span class="relative inline-flex rounded-full h-2 w-2"
                :class="{
                  'bg-success': connectionStatus === 'connected',
                  'bg-error': connectionStatus === 'disconnected',
                  'bg-warning': connectionStatus === 'connecting'
                }"></span>
        </span>
        <span class="text-gray-500 dark:text-gray-500 hidden sm:inline">{{ t('statusbar.ws') }}</span>
        <span class="font-medium whitespace-nowrap hidden sm:inline"
              :class="{
                'text-success': connectionStatus === 'connected',
                'text-error': connectionStatus === 'disconnected',
                'text-warning': connectionStatus === 'connecting'
              }">
          {{ connectionStatusText }}
        </span>
      </div>
      
      <!-- Separator -->
      <div class="w-px h-4 bg-gray-200 dark:bg-gray-700 mx-1 hidden sm:block"></div>
      
      <!-- System Environment (hidden on phones: too wide for one line) -->
      <div class="hidden md:flex items-center gap-1.5 px-2 py-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800 cursor-default transition-colors duration-150">
        <i data-lucide="monitor" class="w-3.5 h-3.5"></i>
        <span class="text-gray-500 dark:text-gray-500 hidden md:inline">{{ t('statusbar.kernel') }}</span>
        <span class="font-medium text-gray-700 dark:text-gray-300">{{ systemEnv }}</span>
      </div>
      
      <!-- Separator -->
      <div class="w-px h-4 bg-gray-200 dark:bg-gray-700 mx-1 hidden md:block"></div>
      
      <!-- Latency: HTTP / WS -->
      <div class="flex items-center gap-1.5 px-2 py-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800 cursor-default transition-colors duration-150">
        <i data-lucide="zap" class="w-3.5 h-3.5"></i>
        <span class="text-gray-500 dark:text-gray-500 hidden lg:inline">{{ t('statusbar.latency') }}</span>
        <span class="font-medium text-gray-700 dark:text-gray-300 whitespace-nowrap">
          <span :class="{ 'text-success': httpLatency < 50, 'text-warning': httpLatency >= 50 && httpLatency < 200, 'text-error': httpLatency >= 200 }">{{ httpLatency > 0 ? httpLatency + 'ms' : '--' }}</span>
          <span class="text-gray-500"> / </span>
          <span>{{ wsLatency > 0 ? wsLatency + 'ms' : '--' }}</span>
        </span>
      </div>
      
      <!-- Right Section (pushed to right). Uptime and connection count are
           desktop/tablet only: the phone bar keeps latency as the single
           metric (see the decision to shrink it to 28px). -->
      <div class="ml-auto flex items-center">
        <!-- Server Uptime -->
        <div class="hidden md:flex items-center gap-1.5 px-2 py-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800 cursor-default transition-colors duration-150">
          <i data-lucide="clock" class="w-3.5 h-3.5"></i>
          <span class="text-gray-500 dark:text-gray-500 hidden lg:inline">{{ t('statusbar.uptime') }}</span>
          <span class="font-mono font-medium text-gray-700 dark:text-gray-300 whitespace-nowrap">{{ serverUptime }}</span>
        </div>
        
        <!-- Separator -->
        <div class="w-px h-4 bg-gray-200 dark:bg-gray-700 mx-1 hidden lg:block"></div>
        
        <!-- Memory Usage (hidden on phones: keeps the bar single-line) -->
        <div class="hidden md:flex items-center gap-1.5 px-2 py-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800 cursor-default transition-colors duration-150">
          <i data-lucide="hard-drive" class="w-3.5 h-3.5"></i>
          <span class="text-gray-500 dark:text-gray-500 hidden xl:inline">{{ t('statusbar.memory') }}</span>
          <span class="font-medium text-gray-700 dark:text-gray-300 whitespace-nowrap">{{ serverMemory }}</span>
        </div>
        
        <!-- Separator -->
        <div class="w-px h-4 bg-gray-200 dark:bg-gray-700 mx-1 hidden md:block"></div>
        
        <!-- WS Connections -->
        <div class="hidden md:flex items-center gap-1.5 px-2 py-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800 cursor-default transition-colors duration-150">
          <i data-lucide="users" class="w-3.5 h-3.5"></i>
          <span class="text-gray-500 dark:text-gray-500 hidden xl:inline">{{ t('statusbar.connections') }}</span>
          <span class="font-medium text-gray-700 dark:text-gray-300 whitespace-nowrap">{{ wsConnections }}</span>
        </div>
      </div>
    </footer>
  `,
  
  data() {
    return {
      connectionStatus: 'disconnected',
      systemEnv: '--',
      httpLatency: 0,
      wsLatency: 0,
      serverUptime: '--',
      serverMemory: '--',
      wsConnections: 0,
      statsInterval: null,
      uptimeInterval: null,
      _serverStartSec: 0,
    };
  },
  
  computed: {
    connectionStatusText() {
      const statusMap = {
        'connected': this.t('statusbar.connected'),
        'disconnected': this.t('statusbar.disconnected'),
        'connecting': this.t('statusbar.connecting')
      };
      return statusMap[this.connectionStatus] || 'UNKNOWN';
    }
  },
  
  mounted() {
    this.initialize();
    this.startUptimeTimer();
    this.startStatsTimer();
    // language-changed listener + currentLanguage provided by global I18nMixin
    // Reactively track WS connection status
    if (window.BoarAiStore && window.BoarAiStore.state) {
      this._onConnectionChangeStore = (status) => {
        this.connectionStatus = status || 'disconnected';
      };
      window.BoarAiStore.state._onConnectionChange = this._onConnectionChangeStore;
    }
  },
  
  // Icons hydrate automatically via app.js MutationObserver. This component
  // re-renders every second (uptime tick) — no full-page icon scans needed.
  updated() {},

  beforeUnmount() {
    this.stopUptimeTimer();
    this.stopStatsTimer();
    const state = window.BoarAiStore && window.BoarAiStore.state;
    if (state && state._onConnectionChange === this._onConnectionChangeStore) {
      state._onConnectionChange = null;
    }
  },
  
  methods: {
    initialize() {
      this.syncConnectionStatus();
      this.fetchSystemStatus();
    },
    
    syncConnectionStatus() {
      if (window.BoarAiStore && window.BoarAiStore.state) {
        this.connectionStatus = window.BoarAiStore.state.connectionStatus || 'disconnected';
      }
    },
    
    startUptimeTimer() {
      this.uptimeInterval = setInterval(() => {
        this.tickUptime();
      }, 1000);
    },
    
    stopUptimeTimer() {
      if (this.uptimeInterval) {
        clearInterval(this.uptimeInterval);
        this.uptimeInterval = null;
      }
    },
    
    tickUptime() {
      if (this._serverStartSec <= 0) return;
      const elapsed = Math.floor(Date.now() / 1000) - this._serverStartSec;
      const hours = Math.floor(elapsed / 3600);
      const minutes = Math.floor((elapsed % 3600) / 60);
      const seconds = elapsed % 60;
      if (hours > 0) {
        this.serverUptime = [hours.toString().padStart(2, '0'), minutes.toString().padStart(2, '0'), seconds.toString().padStart(2, '0')].join(':');
      } else {
        this.serverUptime = [minutes.toString().padStart(2, '0'), seconds.toString().padStart(2, '0')].join(':');
      }
    },
    
    startStatsTimer() {
      this.statsInterval = setInterval(() => {
        this.updateStats();
      }, 30000);
    },
    
    stopStatsTimer() {
      if (this.statsInterval) {
        clearInterval(this.statsInterval);
        this.statsInterval = null;
      }
    },
    
    async updateStats() {
      this.syncConnectionStatus();
      await this.fetchSystemStatus();
    },
    
    async fetchSystemStatus() {
      const start = performance.now();
      try {
        const resp = await fetch('/api/status');
        const data = await resp.json();
        
        // HTTP latency
        this.httpLatency = Math.round(performance.now() - start);
        
        // WS latency: same server, use HTTP latency as estimate
        if (this.connectionStatus === 'connected') {
          this.wsLatency = this.httpLatency;
        } else {
          this.wsLatency = 0;
        }
        
        // System environment
        if (data.system) {
          const sys = data.system;
          this.systemEnv = `${sys.os} ${sys.os_release} · Py ${sys.python}`;
        }
        
        // Server metrics
        if (data.server) {
          // Store server start time for local uptime ticking
          this._serverStartSec = Math.floor(Date.now() / 1000) - (data.server.uptime_sec || 0);
          this.serverMemory = data.server.memory_mb > 0
            ? this.formatMemory(data.server.memory_mb)
            : '--';
          this.wsConnections = data.server.ws_clients || 0;
        }
      } catch (e) {
        this.httpLatency = -1;
      }
    },
    
    formatMemory(mb) {
      if (mb >= 1024) {
        return (mb / 1024).toFixed(1) + ' GB';
      }
      if (mb < 1) return '--';
      return Math.round(mb) + ' MB';
    },
    
    setConnectionStatus(status) {
      this.connectionStatus = status;
    },
    
    setLatency(value) {
      this.httpLatency = value;
    },
    
    setMemoryUsage(value) {
      this.serverMemory = value;
    },
    
    setActiveConnections(value) {
      this.wsConnections = value;
    }
  }
};

if (typeof module !== 'undefined' && module.exports) {
  module.exports = StatusbarComponent;
}