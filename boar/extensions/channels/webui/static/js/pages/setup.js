/**
 * Setup page — first-run flow where the operator creates the access key.
 *
 * One step: generate or type a key, choose session lifetime, claim it.
 * Route: /setup (meta.bare — no navbar, no status bar)
 * Query: ?next=<hash path> restored after a successful setup
 */
const SetupPage = {
  name: 'SetupPage',

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
            <i data-lucide="key-round" class="w-7 h-7 text-primary"></i>
          </div>
          <h1 class="text-xl font-semibold text-gray-900 dark:text-white">
            {{ t('setup.title') || 'Create your access key' }}
          </h1>
          <p class="mt-1.5 text-sm text-gray-500 dark:text-gray-400">
            {{ t('setup.subtitle') || 'Your WebUI needs an access key before it can be used.' }}
          </p>
        </div>

        <form @submit.prevent="submit"
              class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm p-5 space-y-4">
          <!-- Key generation -->
          <form-field :label="t('setup.keyLabel') || 'Access key'"
                      :hint="t('setup.keyHint') || 'Generated keys are strong. You may also type your own.'"
                      :error="error">
            <div class="relative">
              <input :type="reveal ? 'text' : 'password'"
                     v-model="key"
                     ref="keyInput"
                     autocomplete="new-password"
                     spellcheck="false"
                     autocapitalize="off"
                     :disabled="busy"
                     :placeholder="t('setup.keyPlaceholder') || 'boarai_…'"
                     class="w-full px-3 py-2 pr-16 text-sm font-mono bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary/50 disabled:opacity-60" />
              <button type="button" @click="reveal = !reveal"
                      :title="reveal ? (t('unlock.hide') || 'Hide') : (t('unlock.show') || 'Show')"
                      class="absolute right-8 top-1/2 -translate-y-1/2 p-1.5 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors">
                <i :data-lucide="reveal ? 'eye-off' : 'eye'" class="w-4 h-4"></i>
              </button>
              <button type="button" @click="copyKey"
                      :title="copied ? (t('setup.copied') || 'Copied') : (t('setup.copy') || 'Copy')"
                      class="absolute right-1.5 top-1/2 -translate-y-1/2 p-1.5 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors">
                <i :data-lucide="copied ? 'check' : 'copy'" class="w-4 h-4"></i>
              </button>
            </div>
            <div class="flex items-center gap-2 mt-2">
              <base-button size="sm" @click="generateKey" icon="refresh-cw" :loading="generating">
                {{ t('setup.generate') || 'Generate new key' }}
              </base-button>
              <span v-if="strengthMessage" class="text-xs"
                    :class="strengthColor">{{ strengthMessage }}</span>
            </div>
          </form-field>

          <!-- Remote first-run: prove console access with the printed code -->
          <form-field v-if="setupCodeRequired"
                      :label="t('setup.codeLabel') || 'Setup code'"
                      :hint="t('setup.codeHint') || 'Printed in the server startup log (docker logs / journalctl). Not needed on the server machine.'">
            <input type="text"
                   v-model="setupCode"
                   autocomplete="off"
                   spellcheck="false"
                   autocapitalize="off"
                   :disabled="busy"
                   :placeholder="t('setup.codePlaceholder') || 'XXXX-XXXX-XXXX'"
                   class="w-full px-3 py-2 text-sm font-mono bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary/50 disabled:opacity-60" />
          </form-field>

          <!-- Session lifetime -->
          <form-field :label="t('unlock.durationLabel') || 'Stay signed in for'"
                      :hint="t('unlock.durationHint') || 'After this the key is asked again.'">
            <base-select v-model="ttl" :options="ttlOptions" :disabled="busy"></base-select>
          </form-field>

          <label class="flex items-start gap-2 text-sm text-gray-600 dark:text-gray-300 select-none">
            <input type="checkbox" v-model="remember" :disabled="busy"
                   class="mt-0.5 rounded border-gray-300 dark:border-gray-600 text-primary focus:ring-primary/50" />
            <span>
              {{ t('unlock.remember') || 'Remember this device' }}
              <span class="block text-xs text-gray-400 dark:text-gray-500">
                {{ t('unlock.rememberHint') || 'Off = signed out when the browser closes.' }}
              </span>
            </span>
          </label>

          <base-button type="submit" block icon="shield-check"
                       :loading="busy" :disabled="busy || !key.trim()">
            {{ busy ? t('setup.submitting') || 'Claiming...' : t('setup.submit') || 'Claim and continue' }}
          </base-button>

          <p v-if="retryAfter > 0" class="text-xs text-amber-600 dark:text-amber-400 text-center">
            {{ (t('unlock.throttled') || 'Too many attempts — retry in {seconds}s.').replace('{seconds}', retryAfter) }}
          </p>
        </form>

        <!-- Remote first-run: point at the console-printed one-shot code -->
        <div v-if="setupCodeRequired"
             class="mt-4 rounded-lg border border-amber-200 dark:border-amber-900/60 bg-amber-50 dark:bg-amber-950/40 p-3">
          <p class="text-xs text-amber-800 dark:text-amber-200">
            {{ t('setup.remoteNote') || 'You are setting up from another machine. Enter the one-time setup code printed in the server startup log; on the server itself no code is needed.' }}
          </p>
        </div>

        <!-- Loopback first-run: show the code here too — a detached start may
             never print the banner on a terminal. Loopback visitors could
             claim directly anyway, so displaying it grants no new power. -->
        <div v-else-if="localSetupCode"
             class="mt-4 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-3">
          <p class="text-xs font-medium text-gray-700 dark:text-gray-300">
            {{ t('setup.localCodeTitle') || 'Claiming from another machine? Use this one-time code:' }}
          </p>
          <div class="mt-2 flex items-center gap-2">
            <code class="flex-1 text-sm font-mono tracking-wider text-gray-900 dark:text-gray-100 select-all">{{ localSetupCode }}</code>
            <button type="button" @click="copyLocalCode"
                    class="shrink-0 px-2 py-1 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors flex items-center gap-1">
              <i :data-lucide="localCodeCopied ? 'check' : 'copy'" class="w-3.5 h-3.5"></i>
              {{ localCodeCopied ? (t('setup.copied') || 'Copied') : (t('setup.copy') || 'Copy') }}
            </button>
            <button type="button" @click="loadLocalCode"
                    :title="t('setup.refresh') || 'Refresh'"
                    class="shrink-0 px-2 py-1 text-xs rounded-lg border border-gray-300 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors">
              <i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i>
            </button>
          </div>
          <p class="mt-1.5 text-xs text-gray-400 dark:text-gray-500">
            {{ t('setup.localCodeHint') || 'Visible only on the server itself; re-minted on restart.' }}
          </p>
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
      generating: false,
      error: '',
      retryAfter: 0,
      setupCode: '',
      setupCodeRequired: false,
      copied: false,
      localSetupCode: '',
      localCodeCopied: false,
      // Raw seconds only. Labels are derived in a computed so they follow the
      // language switch — baking them into data froze them at the first locale.
      ttlSeconds: [],
      strengthMessage: '',
      strengthColor: '',
      minKeyLength: 32,
    };
  },

  watch: {
    key() {
      this.checkStrength();
    },
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

  methods: {
    async loadPolicy() {
      try {
        const status = await window.BoarAiAuth.status(true);
        // Already configured: redirect to dashboard (shouldn't happen via route guard)
        if (status && status.must_setup === false) {
          this.$router.replace(this.safeNext() || { name: 'dashboard' });
          return;
        }
        this.setupCodeRequired = !!status.setup_code_required;
        if (!this.setupCodeRequired) await this.loadLocalCode();
        this.minKeyLength = Number(status.min_key_length) || 32;
        this.ttlSeconds = (status.ttl_options || []).map(Number);
        const preferred = String(status.ttl_seconds || '');
        this.ttl = this.ttlSeconds.some((s) => String(s) === preferred)
          ? preferred
          : (this.ttlSeconds.length ? String(this.ttlSeconds[0]) : '');
      } catch (error) {
        this.error = error && error.message
          ? error.message
          : (this.t('unlock.serverUnreachable') || 'Cannot reach the BoarAI server.');
      }
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

    generateKey() {
      this.generating = true;
      this.error = '';
      this.strengthMessage = '';
      this.strengthColor = '';
      try {
        // getRandomValues works on plain HTTP too (unlike crypto.subtle), so
        // LAN access over http:// still gets a cryptographically strong key.
        const bytes = new Uint8Array(32);
        window.crypto.getRandomValues(bytes);
        let binary = '';
        for (let i = 0; i < bytes.length; i += 1) {
          binary += String.fromCharCode(bytes[i]);
        }
        const b64 = btoa(binary)
          .replace(/\+/g, '-')
          .replace(/\//g, '_')
          .replace(/=+$/, '');
        this.key = `boarai_${b64}`;
        this.reveal = true; // the operator must be able to copy it
      } catch (e) {
        this.error = this.t('setup.generateFailed')
          || 'This browser cannot generate a key. Type a strong one instead.';
      } finally {
        this.generating = false;
      }
    },

    checkStrength() {
      const key = this.key || '';
      if (!key) {
        this.strengthMessage = '';
        this.strengthColor = '';
        return;
      }
      if (key.length < this.minKeyLength) {
        this.strengthMessage = `${this.t('setup.weakTooShort') || 'Too short'} (${key.length}/${this.minKeyLength})`;
        this.strengthColor = 'text-red-600 dark:text-red-400';
        return;
      }
      const unique = new Set(key).size;
      if (unique < 12) {
        this.strengthMessage = this.t('setup.weakLowEntropy') || 'Too few distinct characters';
        this.strengthColor = 'text-red-600 dark:text-red-400';
        return;
      }
      if (/^[a-z]+$/.test(key.toLowerCase())) {
        this.strengthMessage = this.t('setup.weakNoSymbols') || 'Must include numbers or symbols';
        this.strengthColor = 'text-red-600 dark:text-red-400';
        return;
      }
      this.strengthMessage = this.t('setup.strong') || 'Strong';
      this.strengthColor = 'text-green-600 dark:text-green-400';
    },

    async loadLocalCode() {
      // Loopback-only endpoint; refresh simply re-reads the current code.
      try {
        const data = await window.BoarAiAuth.setupCode();
        this.localSetupCode = (data && data.setup_code) || '';
      } catch (_) {
        this.localSetupCode = '';
      }
    },

    async copyLocalCode() {
      if (!this.localSetupCode) return;
      const ok = await window.UiKit.copyToClipboard(this.localSetupCode);
      if (ok) {
        this.localCodeCopied = true;
        setTimeout(() => { this.localCodeCopied = false; }, 2000);
      } else {
        this.error = this.t('setup.copyManual')
          || 'Clipboard blocked — select the code and copy it manually.';
      }
    },

    async copyKey() {
      if (!this.key || this.busy) return;
      const ok = await window.UiKit.copyToClipboard(this.key);
      if (ok) {
        this.copied = true;
        setTimeout(() => { this.copied = false; }, 2000);
      } else {
        // Clipboard blocked (insecure context / permissions): the input can
        // be revealed and selected, so say what to do instead of failing.
        this.error = this.t('setup.copyManual')
          || 'Clipboard blocked — reveal the key and copy it manually.';
      }
    },

    async submit() {
      const key = (this.key || '').trim();
      if (!key || this.busy) return;
      this.busy = true;
      this.error = '';
      try {
        await window.BoarAiAuth.setup(key, {
          ttlSeconds: this.ttl ? Number(this.ttl) : undefined,
          remember: this.remember,
          setupCode: this.setupCodeRequired ? this.setupCode.trim() : undefined,
        });
        this.key = '';
        const next = this.safeNext() || '/dashboard';
        window.location.replace(
          window.location.pathname + window.location.search + '#' + next,
        );
        window.location.reload();
      } catch (error) {
        const code = (error && error.code) || '';
        if (code === 'auth_throttled' || (error && error.status === 429)) {
          this.error = this.t('unlock.throttledError') || 'Too many failed attempts.';
          this.startCountdown(Number(error.retryAfter) || 30);
        } else if (code === 'auth_setup_code_required') {
          this.error = this.t('setup.codeRequired')
            || 'Remote setup needs the setup code from the server startup log.';
        } else if (code === 'auth_setup_code_invalid') {
          this.error = this.t('setup.codeInvalid') || 'That setup code is not correct.';
        } else if (code === 'auth_key_weak') {
          this.error = error.message || 'Key does not meet strength requirements.';
        } else if (code === 'auth_already_configured') {
          // Lost the race with another tab: go to unlock instead
          this.$router.replace({ name: 'unlock', query: this.safeNext() ? { next: this.safeNext() } : {} });
        } else {
          this.error = (error && error.message) || (this.t('setup.failed') || 'Setup failed.');
        }
      } finally {
        this.busy = false;
      }
    },

    safeNext() {
      const next = this.$route && this.$route.query ? this.$route.query.next : '';
      if (typeof next !== 'string') return null;
      if (!next.startsWith('/') || next.startsWith('//')) return null;
      if (next.startsWith('/setup')) return null;
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

window.SetupPage = SetupPage;
