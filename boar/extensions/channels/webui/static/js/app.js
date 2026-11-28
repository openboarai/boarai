/**
 * BoarAi WebUI Main Application
 * 
 * Main Vue application file that initializes the WebUI.
 * Uses TailwindCSS for styling.
 */

// ROOT-CAUSE Lucide patch (2026-07-31):
// NEVER let lucide REPLACE Vue-managed <i data-lucide> elements. lucide's
// replaceElement() does element.parentNode.replaceChild(svg, <i>), which
// detaches the <i> that Vue still references (vnode.el). A later Vue patch
// (v-if/v-else icon toggle, v-for reorder, nested lists) then inserts into a
// detached parent → "Cannot read properties of null (reading 'insertBefore')".
//
// Instead we keep the <i> element identity stable and render the <svg> INSIDE
// it. Vue can then always patch / remove / move the same element it owns, and
// the whole class of insertBefore-null crashes is eliminated everywhere
// (pages, navbar, statusbar, modals, notifications), regardless of where
// createIcons() is called (mounted, updated, watchers, timers).
(function() {
  if (!window.lucide || typeof window.lucide.createElement !== 'function') return;
  const lucide = window.lucide;
  const iconStore = lucide.icons || {};

  // Mirrors lucide's internal name normalization (toPascalCase) so kebab-case
  // data-lucide values (e.g. "send", "layout-dashboard") resolve to the
  // PascalCase keys of lucide.icons (e.g. "Send", "LayoutDashboard").
  function toPascalCase(name) {
    const camel = name.replace(/^([A-Z])|[\s-_]+(\w)/g, (m, p1, p2) => (p2 ? p2.toUpperCase() : p1.toLowerCase()));
    return camel.charAt(0).toUpperCase() + camel.slice(1);
  }

  function hydrateElement(el, nameAttr) {
    if (!el || !el.getAttribute || !el.querySelector || !el.appendChild) return false;
    const name = el.getAttribute(nameAttr);
    if (!name) return false;
    const iconNode = iconStore[name] || iconStore[toPascalCase(name)];
    if (!iconNode) return false;
    // If the same node was hydrated before and Vue re-set the attr (dynamic
    // icon), drop the stale svg so the icon can update in place.
    const existing = el.querySelector('svg');
    if (existing) existing.remove();
    // Copy presentation attributes from the <i> (class / width / height / ...).
    // Do NOT copy inline `style` — the wrapper <i> carries display:contents
    // (set below); copying it onto the svg would make the icon itself
    // display:contents and vanish.
    const attrs = {};
    for (let i = 0; i < el.attributes.length; i++) {
      const a = el.attributes[i];
      if (a.name === nameAttr || a.name === 'style') continue;
      attrs[a.name] = a.value;
    }
    const svg = lucide.createElement(iconNode, attrs);
    // The svg is built once, but Vue keeps patching the wrapper <i> — dynamic
    // :class colors (e.g. approval mode red/amber/green) would stay frozen at
    // the hydration-time value, because the copied class list is a snapshot.
    // Inheriting color from the wrapper makes the svg follow every Vue patch
    // (inline style beats the copied text-* utility class).
    if (svg.style) svg.style.color = 'inherit';
    // Keep the <i> node: put the svg inside so Vue's element reference stays valid.
    el.appendChild(svg);
    el.removeAttribute(nameAttr);
    // The wrapper must not affect layout/typography (the svg carries the size).
    if (el.style) el.style.display = 'contents';
    return true;
  }

  let pendingFrame = null;
  let pendingRoot = null;
  let pendingNameAttr = 'data-lucide';

  function hydrateRoot(root, nameAttr) {
    if (!root || typeof root.querySelectorAll !== 'function') return;
    const elements = Array.from(root.querySelectorAll('[' + nameAttr + ']'));
    for (let i = 0; i < elements.length; i++) {
      const el = elements[i];
      // Vue may re-write the static data-lucide attr back onto an already
      // hydrated <i> (it already has an <svg>). Replacing it again would
      // recreate the svg each patch, so just strip the stale attr.
      if (el && el.querySelector && el.querySelector('svg')) {
        el.removeAttribute(nameAttr);
        continue;
      }
      hydrateElement(el, nameAttr);
    }
  }

  lucide.createIcons = function(options) {
    const opts = options || {};
    const root = opts.root || document;
    const nameAttr = opts.nameAttr || 'data-lucide';
    if (!root || typeof root.querySelectorAll !== 'function') return;
    if (opts.immediate) {
      hydrateRoot(root, nameAttr);
      return;
    }
    // Coalesce repeated mounted/updated/watch calls into one frame so icon
    // hydration does not repeatedly interleave with Vue's DOM patch cycle.
    pendingRoot = pendingRoot && pendingRoot !== root ? document : root;
    pendingNameAttr = nameAttr;
    if (pendingFrame !== null) return;
    const raf = window.requestAnimationFrame || ((fn) => window.setTimeout(fn, 0));
    pendingFrame = raf(() => {
      const targetRoot = pendingRoot || document;
      const targetNameAttr = pendingNameAttr || 'data-lucide';
      pendingFrame = null;
      pendingRoot = null;
      pendingNameAttr = 'data-lucide';
      hydrateRoot(targetRoot, targetNameAttr);
    });
  };

  // Safety net for icons injected asynchronously after the initial load
  // (toasts, live-updated lists, dialogs). Reuses the same coalescing so it
  // never fights Vue's patch cycle.
  function scheduleMutationHydrate() {
    lucide.createIcons({ root: document });
  }
  if (typeof window.MutationObserver === 'function') {
    const observer = new MutationObserver((mutations) => {
      let needsHydrate = false;
      for (let i = 0; i < mutations.length; i++) {
        const m = mutations[i];
        // Dynamic :data-lucide swap (e.g. copy→check, chevron-toggle). Vue only
        // changes the attribute, so no childList mutation fires — hydrate the
        // element directly and let hydrateElement rebuild the svg in place.
        if (m.type === 'attributes' && m.attributeName === 'data-lucide') {
          const el = m.target;
          if (el && el.nodeType === 1 && el.getAttribute('data-lucide') !== m.oldValue) {
            hydrateElement(el, 'data-lucide');
          }
          continue;
        }
        const added = m.addedNodes;
        if (!added || !added.length) continue;
        for (let j = 0; j < added.length; j++) {
          const node = added[j];
          if (node.nodeType === 1 && (node.hasAttribute && node.hasAttribute('data-lucide') || (node.querySelectorAll && node.querySelectorAll('[data-lucide]').length > 0))) {
            needsHydrate = true;
            break;
          }
        }
        if (needsHydrate) break;
      }
      if (needsHydrate) scheduleMutationHydrate();
    });
    // attributes:true so dynamic :data-lucide swaps hydrate automatically; no
    // manual lucide.createIcons() calls are needed anywhere in the app.
    const observeOptions = { childList: true, subtree: true, attributes: true, attributeFilter: ['data-lucide'], attributeOldValue: true };
    if (document.body) {
      observer.observe(document.body, observeOptions);
    } else {
      document.addEventListener('DOMContentLoaded', () => {
        observer.observe(document.body, observeOptions);
      }, { once: true });
    }
  }
})();

const { createApp, ref, reactive, computed, onMounted, onUnmounted } = Vue;
// onUnmounted used by theme listener cleanup
const { createRouter, createWebHashHistory } = VueRouter;

// Define routes using separated page components
const routes = [
  { 
    path: '/', 
    redirect: { name: 'dashboard' }
  },
  { 
    path: '/dashboard', 
    name: 'dashboard',
    component: DashboardPage
  },
  { 
    path: '/sessions', 
    name: 'sessions',
    component: SessionsPage
  },
  { 
    path: '/sessions/:id', 
    name: 'sessions-detail',
    component: SessionsPage
  },
  { 
    path: '/tasks', 
    name: 'tasks',
    component: TasksPage
  },
  { 
    path: '/extensions', 
    name: 'extensions',
    component: ExtensionsPage
  },
  { 
    path: '/data', 
    name: 'data',
    component: DataPage
  },
  { 
    path: '/settings', 
    name: 'settings',
    component: SettingsPage
  },
  {
    // Access-gate entry. `bare` renders it without the app chrome (no navbar,
    // no status bar): there is nothing to navigate to until it succeeds.
    path: '/unlock',
    name: 'unlock',
    component: UnlockPage,
    meta: { bare: true, public: true }
  },
  {
    // First-run setup: create the access key (loopback only).
    path: '/setup',
    name: 'setup',
    component: SetupPage,
    meta: { bare: true, public: true }
  }
];

// Create router
const router = createRouter({
  history: createWebHashHistory(),
  routes
});

// Create main application
const app = createApp({
  setup() {
    const state = reactive({
      error: null,
      theme: 'light'
    });
    
    const api = {
      async getStatus() {
        const r = await fetch('/api/status');
        if (!r.ok) throw new Error(`Status ${r.status}`);
        return r.json();
      },
      
      async getMessages(sessionId, limit = 50, offset = 0) {
        const r = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/messages?limit=${limit}&offset=${offset}`);
        if (!r.ok) throw new Error(`Messages ${r.status}`);
        const body = await r.json();
        return body.data || { messages: [], total: 0 };
      },
      
      async sendMessage(sessionId, content, type = 'text') {
        const r = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/messages`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content, type }),
        });
        if (!r.ok) throw new Error(`Send message ${r.status}`);
        const body = await r.json();
        return body.data || body;
      },
      
      async getExtensions() {
        const r = await fetch('/api/extensions');
        if (!r.ok) throw new Error(`Extensions ${r.status}`);
        const body = await r.json();
        return body.data || { extensions: [] };
      },
      
      async toggleExtension(name, enabled) {
        const path = enabled ? 'enable' : 'disable';
        const r = await fetch(`/api/extensions/${encodeURIComponent(name)}/${path}`, { method: 'POST' });
        if (!r.ok) throw new Error(`Toggle extension ${r.status}`);
        const body = await r.json();
        return body.data || body;
      },
      
      async getConfig() {
        const r = await fetch('/api/owner');
        if (!r.ok) throw new Error(`Config ${r.status}`);
        const body = await r.json();
        return (body.data && body.data.settings) || {};
      },
      
      async updateConfig(config) {
        const r = await fetch('/api/owner', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(config),
        });
        if (!r.ok) throw new Error(`Update config ${r.status}`);
        const body = await r.json();
        return body.data || body;
      }
    };
    
    const syncThemeState = () => {
      if (window.BoarTheme) {
        state.theme = window.BoarTheme.getPreference();
      } else {
        state.theme = localStorage.getItem('boarai-theme') || 'light';
      }
    };

    const setTheme = (theme) => {
      if (window.BoarTheme) {
        window.BoarTheme.set(theme);
      } else {
        localStorage.setItem('boarai-theme', theme);
        document.documentElement.classList.toggle('dark', theme === 'dark');
        document.documentElement.setAttribute('data-theme', theme);
      }
      state.theme = theme;
    };

    const toggleTheme = () => {
      if (window.BoarTheme) {
        const result = window.BoarTheme.toggle();
        state.theme = result.preference;
        return;
      }
      setTheme(state.theme === 'dark' ? 'light' : 'dark');
    };

    const onThemeChanged = (event) => {
      if (event?.detail?.theme) {
        state.theme = event.detail.theme;
      } else {
        syncThemeState();
      }
    };

    const initialize = async () => {
      try {
        // Re-apply from BoarTheme (already applied early in theme.js)
        if (window.BoarTheme) {
          window.BoarTheme.apply(window.BoarTheme.getPreference());
          window.BoarTheme.applyColor(window.BoarTheme.getColor());
        }
        syncThemeState();

        // Redirect to configured homepage only on root path (not when route hash is set)
        const homepage = localStorage.getItem('boarai-homepage') || 'dashboard';
        if (homepage !== 'dashboard' && router.currentRoute.value.path === '/') {
          router.push({ name: homepage });
        }
      } catch (error) {
        state.error = error.message;
        console.error('Failed to initialize application:', error);
      }
    };

    onMounted(() => {
      initialize();
      window.addEventListener('theme-changed', onThemeChanged);
    });

    onUnmounted(() => {
      window.removeEventListener('theme-changed', onThemeChanged);
    });
    
    return {
      state,
      api,
      setTheme,
      toggleTheme
    };
  },
  
  computed: {
    /** Full-screen routes (`meta.bare`, i.e. the unlock page) drop the chrome. */
    isBare() {
      const meta = this.$route && this.$route.meta;
      return !!(meta && meta.bare);
    },
  },

  template: `
    <div class="min-h-screen bg-white dark:bg-gray-950 transition-colors duration-200">
      <!-- Navbar (hidden on full-screen routes) -->
      <navbar-component v-if="!isBare"></navbar-component>

      <!-- Main Content -->
      <main :class="isBare ? 'min-h-screen' : 'pt-navbar pb-statusbar min-h-screen'">
        <router-view></router-view>
      </main>

      <template v-if="!isBare">
        <!-- Status Bar -->
        <statusbar-component></statusbar-component>

        <!-- Bottom Navigation (Mobile) -->
        <bottom-nav-component></bottom-nav-component>
      </template>

      <!-- Global Components -->
      <notification-component></notification-component>
      <loading-overlay-component></loading-overlay-component>
      <confirm-dialog-component></confirm-dialog-component>
    </div>
  `
});

/**
 * Global i18n mixin — single source for currentLanguage / t() /
 * handleLanguageChange + the language-changed listener. Every component
 * inherits these; components that need extra work on language change simply
 * override handleLanguageChange() (which the listener still calls).
 */
const I18nMixin = {
  data() {
    return {
      currentLanguage: window.i18n ? window.i18n.getCurrentLanguage() : 'en',
    };
  },
  mounted() {
    window.addEventListener('language-changed', this.handleLanguageChange);
  },
  beforeUnmount() {
    window.removeEventListener('language-changed', this.handleLanguageChange);
  },
  methods: {
    t(key, params) {
      const lang = this.currentLanguage; // reactive dependency for re-render
      if (window.i18n && typeof window.i18n.t === 'function') return window.i18n.t(key, params);
      return key || '';
    },
    handleLanguageChange() {
      this.currentLanguage = window.i18n ? window.i18n.getCurrentLanguage() : 'en';
      // Pages with language-dependent derived state (tab labels, filter
      // options) implement onLanguageChanged() instead of re-overriding the
      // whole listener handler.
      if (typeof this.onLanguageChanged === 'function') this.onLanguageChanged();
    },
  },
};
app.mixin(I18nMixin);

// Register components
// Register unified UI component library (BaseModal/BaseButton/TabBar/...)
if (window.registerUiComponents) window.registerUiComponents(app);

app.component('navbar-component', NavbarComponent);
app.component('statusbar-component', StatusbarComponent);

// Register new global components
app.component('notification-component', NotificationComponent);
app.component('loading-component', LoadingComponent);
app.component('loading-overlay-component', LoadingOverlayComponent);
app.component('empty-state-component', EmptyStateComponent);
app.component('confirm-dialog-component', ConfirmDialogComponent);
app.component('line-chart', LineChartComponent);
app.component('bar-chart', BarChartComponent);
app.component('donut-chart', DonutChartComponent);
app.component('sparkline', SparklineComponent);

// Create bottom navigation component
const BottomNavComponent = {
  name: 'BottomNav',
  
  template: `
    <nav class="md:hidden fixed bottom-statusbar left-0 right-0 bg-white dark:bg-gray-900 border-t border-gray-200 dark:border-gray-700 z-[1030]">
      <!-- Six tabs must fit a 320-360px phone without clipping: every item is
           an equal flex child (flex-1 + min-w-0) with a 10px label that can
           truncate, instead of fixed px-3 paddings that summed to 377px. -->
      <div class="flex justify-around items-stretch h-14 px-1">
        <router-link
          v-for="tab in tabs"
          :key="tab.name"
          :to="tab.path"
          class="relative flex flex-1 min-w-0 flex-col items-center justify-center gap-0.5 px-0.5 py-1 rounded-lg transition-all duration-200"
          :class="currentTab === tab.name 
            ? 'text-primary' 
            : 'text-gray-500 dark:text-gray-400'"
          @click="handleTabClick(tab)"
        >
          <i :data-lucide="tab.icon" class="w-[18px] h-[18px] shrink-0"></i>
          <span class="w-full truncate text-center text-[10px] font-medium leading-tight">{{ tab.label }}</span>
        </router-link>
      </div>
    </nav>
  `,
  
  data() {
    return {
      currentTab: 'dashboard',
    };
  },
  
  computed: {
    activeTab() {
      const route = this.$route;
      if (route) {
        const name = route.name || '';
        if (name === 'sessions' || name === 'sessions-detail') return 'sessions';
        const tab = this.tabs.find(t => t.name === name);
        return tab ? tab.name : 'dashboard';
      }
      return 'dashboard';
    },
    
    tabs() {
      // Depend on currentLanguage (from global I18nMixin) so labels recompute
      const _lang = this.currentLanguage;
      return [
        { name: 'dashboard', label: this.t('nav.home'), icon: 'layout-dashboard', path: '/dashboard' },
        { name: 'sessions', label: this.t('nav.chat'), icon: 'message-square', path: '/sessions' },
        { name: 'tasks', label: this.t('nav.tasks'), icon: 'list-checks', path: '/tasks' },
        { name: 'extensions', label: this.t('nav.extensions'), icon: 'puzzle', path: '/extensions' },
        { name: 'data', label: this.t('nav.data'), icon: 'database', path: '/data' },
        { name: 'settings', label: this.t('nav.settings'), icon: 'settings', path: '/settings' }
      ];
    }
  },
  
  watch: {
    '$route'() {
      this.currentTab = this.activeTab;
    }
  },
  
  mounted() {
    this.currentTab = this.activeTab;
  },

  // Icons hydrate automatically via app.js MutationObserver (childList +
  // data-lucide attribute changes). No manual createIcons calls needed.

  methods: {
    handleTabClick(tab) {
      this.currentTab = tab.name;
    }
  }
};

app.component('bottom-nav-component', BottomNavComponent);

// Use router
app.use(router);

// Install store
if (window.BoarAiStore) {
  app.use(window.BoarAiStore);
}

// Provide API
app.provide('api', {
  async getStatus() {
    const r = await fetch('/api/status');
    if (!r.ok) throw new Error(`Status ${r.status}`);
    return r.json();
  },
  
  async getMessages(sessionId, limit = 50, offset = 0) {
    const r = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/messages?limit=${limit}&offset=${offset}`);
    if (!r.ok) throw new Error(`Messages ${r.status}`);
    const body = await r.json();
    return body.data || { messages: [], total: 0 };
  },
  
  async sendMessage(sessionId, content, type = 'text') {
    const r = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, type }),
    });
    if (!r.ok) throw new Error(`Send message ${r.status}`);
    const body = await r.json();
    return body.data || body;
  },
  
  async getExtensions() {
    const r = await fetch('/api/extensions');
    if (!r.ok) throw new Error(`Extensions ${r.status}`);
    const body = await r.json();
    return body.data || { extensions: [] };
  },
  
  async toggleExtension(name, enabled) {
    const path = enabled ? 'enable' : 'disable';
    const r = await fetch(`/api/extensions/${encodeURIComponent(name)}/${path}`, { method: 'POST' });
    if (!r.ok) throw new Error(`Toggle extension ${r.status}`);
    const body = await r.json();
    return body.data || body;
  },
  
  async getConfig() {
    const r = await fetch('/api/owner');
    if (!r.ok) throw new Error(`Config ${r.status}`);
    const body = await r.json();
    return (body.data && body.data.settings) || {};
  },
  
  async updateConfig(config) {
    const r = await fetch('/api/owner', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });
    if (!r.ok) throw new Error(`Update config ${r.status}`);
    const body = await r.json();
    return body.data || body;
  }
});

// =========================================================================
// Access gate
// =========================================================================
// The gate is transport-level (cookies), so the SPA only has to answer one
// question: "is this browser unlocked?" Everything else — 401 shapes, tickets,
// device lists — lives in auth.js / the server.
const UNLOCK_ROUTE = 'unlock';
const SETUP_ROUTE = 'setup';

router.beforeEach(async (to) => {
  if (to.meta && to.meta.public) return true;
  if (!window.BoarAiAuth) return true;

  let gate;
  try {
    gate = await window.BoarAiAuth.status();
  } catch (_) {
    // Server unreachable: render the page and let it show its own error.
    // Bouncing to /unlock or /setup here would hide a real outage behind a
    // credential prompt.
    return true;
  }
  // First-run setup mode: no key exists yet → redirect to setup page.
  if (gate && gate.must_setup) {
    return {
      name: SETUP_ROUTE,
      query: to.fullPath && to.fullPath !== '/' ? { next: to.fullPath } : {},
    };
  }
  // Normal unlock mode.
  if (gate && gate.must_unlock) {
    return {
      name: UNLOCK_ROUTE,
      query: to.fullPath && to.fullPath !== '/' ? { next: to.fullPath } : {},
    };
  }
  return true;
});

// Any 401 (`auth_required` or `auth_setup_required`) — rotated key, revoked
// device, expired cookie, or unclaimed instance — lands on the appropriate page.
window.addEventListener('boarai-auth-required', () => {
  const current = router.currentRoute.value;
  if (!current) return;
  // Already on a credential page: it owns its own mode switching (setup →
  // dashboard once claimed, unlock → setup via its CTA). Re-routing here would
  // just fight the page and risk a redirect loop.
  if (current.name === SETUP_ROUTE || current.name === UNLOCK_ROUTE) return;

  // Determine which page based on whether a key exists.
  window.BoarAiAuth.status().then((gate) => {
    if (gate && gate.must_setup) {
      router.replace({
        name: SETUP_ROUTE,
        query: current.fullPath && current.fullPath !== '/' ? { next: current.fullPath } : {},
      });
    } else {
      router.replace({
        name: UNLOCK_ROUTE,
        query: current.fullPath && current.fullPath !== '/' ? { next: current.fullPath } : {},
      });
    }
  }).catch(() => {
    // Server down: stay on current page.
  });
});

// Initialize i18n and mount application
async function initializeAndMount() {
  // Wait for i18n to initialize
  if (window.i18n && window.i18n.initI18n) {
    await window.i18n.initI18n();
  }
  
  // Mount application
  app.mount('#app');
  
  // Export for external access
  window.BoarAiApp = {
    app,
    router,
    api: app._context.provides.api
  };
}

// Start initialization
initializeAndMount();
