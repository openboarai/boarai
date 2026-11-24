/**
 * Navbar Component for BoarAi WebUI
 * 
 * Top navigation bar with logo, tabs, and user info.
 * Uses TailwindCSS for styling + CSS variables for theming.
 * Supports internationalization (i18n).
 * Uses Lucide Icons for all icons.
 */

const NavbarComponent = {
  name: 'Navbar',
  
  template: `
    <nav class="fixed top-0 left-0 right-0 h-navbar bg-white/95 dark:bg-gray-900/95 backdrop-blur-md border-b border-gray-200 dark:border-gray-700 flex items-center px-4 md:px-6 z-[1030] shadow-sm transition-all duration-300"
         :class="{ 'shadow-md bg-white/98 dark:bg-gray-900/98': isScrolled }">
      
      <!-- Logo (matches favicon: indigo/violet rounded square + white boar-head silhouette) -->
      <div class="flex items-center gap-2 cursor-pointer select-none group" @click="goToHome">
        <svg class="w-7 h-7 text-primary group-hover:scale-110 transition-transform duration-200"
             viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
          <rect width="100" height="100" rx="22" fill="currentColor"/>
          <path d="M32 46c0-10 8-18 18-18s18 8 18 18v22l-6-4-6 4-6-4-6 4-6-4-6 4z"
                fill="none" stroke="white" stroke-width="5" stroke-linejoin="round"/>
          <circle cx="43" cy="47" r="3" fill="white"/>
          <circle cx="57" cy="47" r="3" fill="white"/>
          <circle cx="43" cy="47" r="1.5" fill="currentColor"/>
          <circle cx="57" cy="47" r="1.5" fill="currentColor"/>
          <ellipse cx="36" cy="43" rx="4" ry="2" fill="white" transform="rotate(-25 36 43)"/>
          <ellipse cx="64" cy="43" rx="4" ry="2" fill="white" transform="rotate(25 64 43)"/>
        </svg>
        <span class="text-lg font-bold text-primary tracking-tight hidden sm:inline">BoarAi</span>
        <span class="text-gray-500 dark:text-gray-500 text-sm hidden sm:inline">|</span>
      </div>
      
      <!-- Navigation Tabs (Desktop) -->
      <div class="hidden md:flex items-center justify-center gap-1 flex-1">
        <router-link
          v-for="tab in tabs"
          :key="tab.name"
          :to="tab.path"
          class="relative flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium transition-all duration-200 whitespace-nowrap"
          :class="currentTab === tab.name 
            ? 'bg-primary text-white shadow-sm shadow-primary/30 hover:bg-primary-dark' 
            : 'text-gray-600 dark:text-gray-500 hover:text-gray-900 dark:hover:text-white hover:bg-primary/10 dark:hover:bg-primary/20'"
          @click="handleTabClick(tab)"
        >
          <i :data-lucide="tab.icon" class="w-4 h-4"></i>
          <span>{{ tab.label }}</span>
        </router-link>
      </div>
      
      <!-- Spacer for mobile -->
      <div class="flex-1 md:hidden"></div>
      
      <!-- Right Controls -->
      <div class="flex items-center gap-2">
        <!-- Pending approval badge (security gate). Surfaced globally so the
             user notices a tool waiting for approval even on other pages. -->
        <button v-if="pendingApprovalCount > 0"
                type="button"
                @click="goToPendingApproval"
                class="relative flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm font-medium text-yellow-800 dark:text-yellow-200 bg-yellow-100 dark:bg-yellow-900/40 hover:bg-yellow-200 dark:hover:bg-yellow-900/60 transition-colors"
                :title="t('common.toolConfirm') || 'Tool Execution Required'">
          <i data-lucide="shield-alert" class="w-4 h-4"></i>
          <span class="hidden sm:inline">{{ pendingApprovalCount }}</span>
          <span class="absolute -top-1 -right-1 min-w-4 h-4 px-1 rounded-full bg-yellow-500 text-white text-[10px] font-bold flex items-center justify-center leading-none">
            {{ pendingApprovalCount }}
          </span>
        </button>

        <!-- Language Switcher (@click.stop avoids document outside-handler closing on open) -->
        <div class="relative language-switcher" @click.stop>
          <button type="button"
                  @click.stop="toggleLanguageMenu"
                  class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-all duration-200 hover:bg-gray-100 dark:hover:bg-gray-800"
                  :class="showLanguageMenu ? 'bg-gray-100 dark:bg-gray-800' : ''"
                  :aria-expanded="showLanguageMenu ? 'true' : 'false'">
            <i data-lucide="globe" class="w-4 h-4"></i>
            <span class="hidden sm:inline">{{ currentLanguageLabel }}</span>
            <i data-lucide="chevron-down" class="w-3 h-3"></i>
          </button>
          
          <!-- Language Dropdown -->
          <div v-if="showLanguageMenu" 
               class="absolute top-full right-0 mt-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-xl min-w-[120px] z-[1000] overflow-hidden"
               @click.stop>
            <button type="button"
                    v-for="lang in languages" 
                    :key="lang.code"
                    @click.stop="setLanguage(lang.code)"
                    class="w-full flex items-center gap-3 px-4 py-3 text-left text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer transition-colors duration-150"
                    :class="currentLanguage === lang.code ? 'bg-primary/10 text-primary' : ''">
              <i :data-lucide="lang.icon" class="w-4 h-4"></i>
              <span class="text-sm">{{ lang.label }}</span>
            </button>
          </div>
        </div>
        
        <!-- Theme Switcher -->
        <button @click="toggleTheme" 
                class="flex items-center justify-center w-9 h-9 rounded-lg transition-all duration-200 hover:bg-gray-100 dark:hover:bg-gray-800"
                :title="themeLabel">
          <svg v-if="resolvedTheme === 'dark'" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M20.985 12.486a9 9 0 1 1-9.473-9.472c.405-.022.617.46.402.803a6 6 0 0 0 8.268 8.268c.344-.215.825-.004.803.401"></path>
          </svg>
          <svg v-else xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="4"></circle>
            <path d="M12 2v2"></path>
            <path d="M12 20v2"></path>
            <path d="m4.93 4.93 1.41 1.41"></path>
            <path d="m17.66 17.66 1.41 1.41"></path>
            <path d="M2 12h2"></path>
            <path d="M20 12h2"></path>
            <path d="m6.34 17.66-1.41 1.41"></path>
            <path d="m19.07 4.93-1.41 1.41"></path>
          </svg>
        </button>
      </div>
    </nav>
  `,
  
  data() {
    return {
      isScrolled: false,
      showLanguageMenu: false,
      currentTab: 'dashboard',
      theme: localStorage.getItem('boarai-theme') || 'light',
      resolvedThemeState: window.BoarTheme ? window.BoarTheme.getResolved() : (localStorage.getItem('boarai-theme') === 'dark' ? 'dark' : 'light'),
      colorTheme: localStorage.getItem('boarai-color') || 'cyan',
      languages: [
        { code: 'en', label: 'English', icon: 'languages' },
        { code: 'zh', label: '中文', icon: 'languages' }
      ],
      colorThemes: [
        { code: 'cyan', name: this.t('settings.colorCyan'), hex: '#6366f1' },
        { code: 'purple', name: this.t('settings.colorPurple'), hex: '#8b5cf6' }
      ]
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
      // Depend on currentLanguage to trigger re-computation on language change
      const lang = this.currentLanguage;
      return [
        { 
          name: 'dashboard', 
          label: this.t('nav.home'), 
          icon: 'layout-dashboard', 
          path: '/dashboard'
        },
        { 
          name: 'sessions', 
          label: this.t('nav.chat'), 
          icon: 'message-square', 
          path: '/sessions'
        },
        { 
          name: 'tasks', 
          label: this.t('nav.tasks'), 
          icon: 'list-checks', 
          path: '/tasks'
        },
        { 
          name: 'extensions', 
          label: this.t('nav.extensions'), 
          icon: 'puzzle', 
          path: '/extensions'
        },
        { 
          name: 'data', 
          label: this.t('nav.data'), 
          icon: 'database', 
          path: '/data'
        },
        { 
          name: 'settings', 
          label: this.t('nav.settings'), 
          icon: 'settings', 
          path: '/settings'
        }
      ];
    },
    
    currentLanguageLabel() {
      const lang = this.languages.find(l => l.code === this.currentLanguage);
      return lang ? lang.label : 'English';
    },
    
    resolvedTheme() {
      return this.resolvedThemeState;
    },

    themeIcon() {
      // Icon reflects effective appearance (system preference may resolve either way)
      return this.resolvedTheme === 'dark' ? 'moon' : 'sun';
    },
    
    themeLabel() {
      if (this.theme === 'system') return this.t('common.system');
      return this.resolvedTheme === 'dark' ? this.t('common.dark') : this.t('common.light');
    },
    
    colorThemeHex() {
      const ct = this.colorThemes.find(c => c.code === this.colorTheme);
      return ct ? ct.hex : '#6366f1';
    },
    
    colorThemeLabel() {
      const ct = this.colorThemes.find(c => c.code === this.colorTheme);
      return ct ? ct.name : 'Cyan';
    },

    pendingApprovalCount() {
      const pa = window.BoarAiStore ? window.BoarAiStore.state.pendingApprovals : {};
      return Object.keys(pa).length;
    }
  },
  
  watch: {
    '$route'(to) {
      this.currentTab = this.activeTab;
    }
  },
  
  mounted() {
    this.currentTab = this.activeTab;
    
    // Theme is owned by BoarTheme (applied early); just sync local state
    if (window.BoarTheme) {
      this.theme = window.BoarTheme.getPreference();
      window.BoarTheme.apply(this.theme);
    } else {
      const savedTheme = localStorage.getItem('boarai-theme') || 'light';
      this.theme = savedTheme;
      document.documentElement.classList.toggle('dark', savedTheme === 'dark');
      document.documentElement.setAttribute('data-theme', savedTheme);
    }
    
    window.addEventListener('scroll', this.handleScroll);
    document.addEventListener('click', this.handleClickOutside);
    window.addEventListener('theme-changed', this.handleThemeChange);
    // language-changed listener + currentLanguage provided by global I18nMixin
  },
  
  // Icons hydrate automatically via app.js MutationObserver.
  updated() {},

  beforeUnmount() {
    window.removeEventListener('scroll', this.handleScroll);
    document.removeEventListener('click', this.handleClickOutside);
    window.removeEventListener('theme-changed', this.handleThemeChange);
  },
  
  methods: {
    handleThemeChange(event) {
      const detail = event.detail || {};
      this.theme = detail.theme || (window.BoarTheme ? window.BoarTheme.getPreference() : this.theme);
      this.resolvedThemeState = detail.resolved || (window.BoarTheme ? window.BoarTheme.getResolved() : (this.theme === 'dark' ? 'dark' : 'light'));
    },
    
    handleScroll() {
      this.isScrolled = window.scrollY > 10;
    },
    
    handleClickOutside(event) {
      // Close language menu when clicking outside (ignore while just opened)
      if (this._langMenuIgnoreOutside) return;
      const languageMenu = this.$el.querySelector('.language-switcher');
      if (languageMenu && !languageMenu.contains(event.target)) {
        this.showLanguageMenu = false;
      }
    },
    
    goToHome() {
      const homepage = localStorage.getItem('boarai-homepage') || 'dashboard';
      this.$router.push({ name: homepage });
      this.currentTab = homepage;
    },

    goToPendingApproval() {
      const pa = window.BoarAiStore ? window.BoarAiStore.state.pendingApprovals : {};
      const firstId = Object.keys(pa)[0];
      if (firstId) {
        this.currentTab = 'sessions';
        this.$router.push({ name: 'sessions-detail', params: { id: firstId } });
      }
    },
    
    handleTabClick(tab) {
      this.currentTab = tab.name;
      this.showLanguageMenu = false;
    },
    
    toggleLanguageMenu() {
      const next = !this.showLanguageMenu;
      this.showLanguageMenu = next;
      // Opening: ignore the same-tick document click that opened the menu
      if (next) {
        this._langMenuIgnoreOutside = true;
        this.$nextTick(() => {
          setTimeout(() => { this._langMenuIgnoreOutside = false; }, 0);
        });
      }
    },
    
    async setLanguage(langCode) {
      this.showLanguageMenu = false;
      if (!window.i18n) return;
      try {
        await window.i18n.setLanguage(langCode);
        // currentLanguage updates via global I18nMixin listener; icons hydrate
        // automatically via app.js MutationObserver.
      } catch (error) {
        console.error('Failed to set language:', error);
      }
    },
    
    toggleTheme() {
      let preference = this.theme;
      let resolved = preference === 'dark' ? 'dark' : 'light';

      if (window.BoarTheme) {
        const result = window.BoarTheme.toggle();
        preference = result.preference;
        resolved = result.resolved;
        this.theme = preference;
      } else {
        const newTheme = this.theme === 'dark' ? 'light' : 'dark';
        localStorage.setItem('boarai-theme', newTheme);
        document.documentElement.classList.toggle('dark', newTheme === 'dark');
        document.documentElement.setAttribute('data-theme', newTheme);
        document.documentElement.setAttribute('data-theme-resolved', newTheme);
        this.theme = newTheme;
        preference = newTheme;
        resolved = newTheme;
        window.dispatchEvent(new CustomEvent('theme-changed', {
          detail: { theme: newTheme, resolved: newTheme },
        }));
      }

      // Defensive re-apply in case another listener raced
      if (window.BoarTheme) {
        window.BoarTheme.apply(preference);
      }
    },
    
    toggleColorTheme() {
      // Cycle through color themes: cyan -> purple -> cyan
      const currentIndex = this.colorThemes.findIndex(c => c.code === this.colorTheme);
      const nextIndex = (currentIndex + 1) % this.colorThemes.length;
      this.colorTheme = this.colorThemes[nextIndex].code;
      if (window.BoarTheme) {
        window.BoarTheme.setColor(this.colorTheme);
      } else {
        localStorage.setItem('boarai-color', this.colorTheme);
        document.documentElement.setAttribute('data-color', this.colorTheme);
        window.dispatchEvent(new CustomEvent('color-changed', { detail: { color: this.colorTheme } }));
      }
    }
  }
};

if (typeof module !== 'undefined' && module.exports) {
  module.exports = NavbarComponent;
}
