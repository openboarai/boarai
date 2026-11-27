/**
 * Unlock page — the only first-run screen the access gate needs.
 *
 * One field (the access key printed by the server), one policy choice (how
 * long this device stays signed in), one button. Nothing else is gated: once
 * the cookie exists the rest of the product behaves exactly as before.
 *
 * Route: /unlock (meta.bare — no navbar, no status bar)
 * Query: ?next=<hash path> restored after a successful unlock
 */
const UnlockPage = {
  name: 'UnlockPage',

  template: `
    <div class="min-h-screen flex items-center justify-center px-4 py-10 bg-gray-50 dark:bg-gray-950">
      <div class="w-full max-w-md">
        <!-- No navbar on this route, so the language switch has to live here. -->
        <div class="flex justify-end mb-3">
          <language-switch></language-switch>
        </div>

        <!-- Brand -->
        <div class="text-center mb-6">
          <div class="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-primary/10 dark:bg-primary/20 mb-4">
            <i data-lucide="shield-check" class="w-7 h-7 text-primary"></i>
          </div>
          <h1 class="text-xl font-semibold text-gray-900 dark:text-white">
            {{ t('unlock.title') || 'Unlock BoarAi' }}
          </h1>
          <p class="mt-1.5 text-sm text-gray-500 dark:text-gray-400">
            {{ t('unlock.subtitle') || 'This device needs the access key printed by the server.' }}
          </p>
        </div>

        <form @submit.prevent="submit"
              class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm p-5 space-y-4">
          <!-- Key -->
          <form-field :label="t('unlock.keyLabel') || 'Access key'"
                      :hint="t('unlock.keyHint') || 'Starts with boarai_. Ask the person running the server.'"
                      :error="error">
            <div class="relative">
              <input :type="reveal ? 'text' : 'password'"
                     v-model="key"
                     ref="keyInput"
                     autocomplete="current-password"
                     spellcheck="false"
                     autocapitalize="off"
                     :disabled="busy || !keyConfigured"
                     :placeholder="t('unlock.keyPlaceholder') || 'boarai_…'"
                     class="w-full px-3 py-2 pr-10 text-sm font-mono bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary/50 disabled:opacity-60" />
              <button type="button" @click="reveal = !reveal"
                      :title="reveal ? (t('unlock.hide') || 'Hide') : (t('unlock.show') || 'Show')"
                      class="absolute right-1.5 top-1/2 -translate-y-1/2 p-1.5 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors">
                <i :data-lucide="reveal ? 'eye-off' : 'eye'" class="w-4 h-4"></i>
              </button>
            </div>
          </form-field>

          <!-- Session lifetime -->
          <form-field :label="t('unlock.durationLabel') || 'Stay signed in for'"
                      :hint="t('unlock.durationHint') || 'After this the key is asked again.'">
            <base-select v-model="ttl" :options="ttlOptions" :disabled="busy || !keyConfigured"></base-select>
          </form-field>

          <label class="flex items-start gap-2 text-sm text-gray-600 dark:text-gray-300 select-none">
            <input type="checkbox" v-model="remember" :disabled="busy || !keyConfigured"
                   class="mt-0.5 rounded border-gray-300 dark:border-gray-600 text-primary focus:ring-primary/50" />
            <span>
              {{ t('unlock.remember') || 'Remember this device' }}
              <span class="block text-xs text-gray-400 dark:text-gray-500">
                {{ t('unlock.rememberHint') || 'Off = signed out when the browser closes.' }}
              </span>
            </span>
          </label>

          <base-button type="submit" block icon="log-in"
                       :loading="busy" :disabled="busy || !keyConfigured || !key.trim()">
            {{ t('unlock.submit') || 'Unlock' }}
          </base-button>

          <p v-if="retryAfter > 0" class="text-xs text-amber-600 dark:text-amber-400 text-center">
            {{ (t('unlock.throttled') || 'Too many attempts — retry in {seconds}s.').replace('{seconds}', retryAfter) }}
          </p>
        </form>

        <!-- Not configured yet: first run creates the key on the setup page -->
        <div v-if="statusLoaded && !keyConfigured"
             class="mt-4 rounded-lg border border-amber-200 dark:border-amber-900/60 bg-amber-50 dark:bg-amber-950/40 p-3">
          <p class="text-xs text-amber-800 dark:text-amber-200">
            {{ t('unlock.notConfigured') || 'No access key exists yet — this looks like a first run.' }}
          </p>
          <base-button size="sm" class="mt-2" icon="key-round"
                       @click="$router.replace({ name: 'setup' })">
            {{ t('unlock.goSetup') || 'Create the access key' }}
          </base-button>
        </div>

        <p class="mt-4 text-center text-xs text-gray-400 dark:text-gray-500">
          {{ t('unlock.footer') || 'The key is only stored as a hash on the server and never leaves it.' }}
        </p>
      </div>
    </div>
  `,

  data() {
    return {
      key: '',
      ttl: '',
      remember: true,
      reveal: false,
      busy: false,
      error: '',
      retryAfter: 0,
      statusLoaded: false,
      keyConfigured: true,
      // Raw seconds; labels come from a computed so they follow the language
      // switch instead of freezing at whichever locale loaded first.
      ttlSeconds: [],
    };
  },

  computed: {
    ttlOptions() {
      return this.ttlSeconds.map((seconds) => ({
        value: String(seconds),
        label: this.durationLabel(seconds),
      }));
    },
  },

  async mounted() {
    await this.loadPolicy();
    this.$nextTick(() => {
      const input = this.$refs.keyInput;
      if (input && !input.disabled) input.focus();
    });
  },

  beforeUnmount() {
    this._clearCountdown();
  },

  methods: {
    async loadPolicy() {
      try {
        const status = await window.BoarAiAuth.status(true);
        // Already unlocked (e.g. a bookmarked #/unlock, or loopback bypass):
        // there is nothing to ask for, so go straight to the app.
        if (status && status.must_unlock === false) {
          this.$router.replace(this.safeNext() || { name: 'dashboard' });
          return;
        }
        this.keyConfigured = status.key_configured !== false;
        this.ttlSeconds = (status.ttl_options || []).map(Number);
        const preferred = String(status.ttl_seconds || '');
        this.ttl = this.ttlSeconds.some((s) => String(s) === preferred)
          ? preferred
          : (this.ttlSeconds.length ? String(this.ttlSeconds[0]) : '');
      } catch (error) {
        this.statusLoaded = false;
        this.error = error && error.message
          ? error.message
          : (this.t('unlock.serverUnreachable') || 'Cannot reach the BoarAI server.');
        return;
      }
      this.statusLoaded = true;
    },

    durationLabel(seconds) {
      const value = Number(seconds) || 0;
      if (value < 3600) {
        return Math.max(1, Math.round(value / 60)) + (this.t('unlock.duration.minutes') || ' min');
      }
      if (value < 86400) {
        return Math.round(value / 3600) + (this.t('unlock.duration.hours') || ' h');
      }
      return Math.round(value / 86400) + (this.t('unlock.duration.days') || ' d');
    },

    async submit() {
      const key = (this.key || '').trim();
      if (!key || this.busy) return;
      this.busy = true;
      this.error = '';
      try {
        await window.BoarAiAuth.unlock(key, {
          ttlSeconds: this.ttl ? Number(this.ttl) : undefined,
          remember: this.remember,
        });
        this.key = '';
        // Full reload, not a router push: while locked, every boot-time fetch
        // (models, sessions, extensions) 401'd and the WebSocket never opened
        // — its only connect points are app install and the onclose timer.
        // Re-running the shell is what actually brings the app back to life.
        const next = this.safeNext() || '/dashboard';
        window.location.replace(
          window.location.pathname + window.location.search + '#' + next,
        );
        window.location.reload();
      } catch (error) {
        const code = (error && error.code) || '';
        // Only a real 429 is "too many attempts". A wrong key answers 401 with
        // an advisory retry_after, and treating that as throttling would show
        // a lockout message on the very first typo.
        if (code === 'auth_throttled' || (error && error.status === 429)) {
          this.error = this.t('unlock.throttledError') || 'Too many failed attempts.';
          this.startCountdown(Number(error.retryAfter) || 30);
        } else if (code === 'auth_invalid') {
          this.error = this.t('unlock.invalid') || 'That access key is not correct.';
        } else if (code === 'auth_not_configured') {
          this.keyConfigured = false;
        } else if (code === 'origin_not_allowed') {
          this.error = this.t('unlock.originBlocked') || error.message || 'Origin not allowed.';
        } else {
          this.error = (error && error.message) || (this.t('unlock.failed') || 'Unlock failed.');
        }
      } finally {
        this.busy = false;
      }
    },

    /** Only same-app hash paths are accepted as a post-unlock destination. */
    safeNext() {
      const next = this.$route && this.$route.query ? this.$route.query.next : '';
      if (typeof next !== 'string') return null;
      if (!next.startsWith('/') || next.startsWith('//')) return null;
      if (next.startsWith('/unlock')) return null;
      return next;
    },

    startCountdown(seconds) {
      this._clearCountdown();
      this.retryAfter = Math.max(1, Math.ceil(seconds));
      this._timer = setInterval(() => {
        this.retryAfter -= 1;
        if (this.retryAfter <= 0) this._clearCountdown();
      }, 1000);
    },

    _clearCountdown() {
      if (this._timer) {
        clearInterval(this._timer);
        this._timer = null;
      }
      this.retryAfter = 0;
    },
  },
};

window.UnlockPage = UnlockPage;
