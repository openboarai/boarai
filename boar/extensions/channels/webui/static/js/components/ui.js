/**
 * BoarAi WebUI — Unified UI Component Library (single file)
 *
 * Centralized primitives replacing hand-written duplicates across pages:
 *   BaseModal / BaseButton / IconButton / ToggleSwitch / TabBar / CardSection
 *   FormField / StatusBadge / RadioGroup / SegmentedControl / BaseSelect
 *   DirectoryPickerModal (business component, used by sessions + settings)
 *
 * All components rely on the global I18nMixin (t()) and the app.js
 * MutationObserver for lucide hydration — no manual createIcons() here.
 *
 * Registration: app.js calls registerUiComponents(app).
 */
(function (global) {
  'use strict';

  // ---------------------------------------------------------------------------
  // BaseButton — variant / size / loading / icon
  // ---------------------------------------------------------------------------
  const UiButtonVariants = {
    primary: 'bg-primary text-white hover:bg-primary-dark shadow-sm',
    secondary: 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600',
    ghost: 'text-gray-600 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700',
    danger: 'bg-red-500 text-white hover:bg-red-600 shadow-sm',
    success: 'bg-emerald-500 text-white hover:bg-emerald-600 shadow-sm',
    warning: 'bg-amber-500 text-white hover:bg-amber-600 shadow-sm',
  };
  const UiButtonSizes = {
    xs: 'px-2 py-1 text-xs rounded',
    sm: 'px-2.5 py-1.5 text-sm rounded-lg',
    md: 'px-4 py-2 text-sm rounded-lg',
    lg: 'px-5 py-2.5 text-base rounded-lg',
  };

  const BaseButton = {
    name: 'BaseButton',
    template: `
      <button :type="type" :disabled="disabled || loading" @click="$emit('click', $event)"
              :class="[
                'inline-flex items-center justify-center gap-1.5 font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-primary/50 disabled:opacity-50 disabled:cursor-not-allowed',
                variants[variant], sizes[size], block ? 'w-full' : ''
              ]">
        <svg v-if="loading" class="animate-spin" :class="iconSize" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
        <i v-else-if="icon" :data-lucide="icon" :class="iconSize"></i>
        <slot></slot>
      </button>
    `,
    props: {
      type: { type: String, default: 'button' },
      variant: { type: String, default: 'primary' },
      size: { type: String, default: 'md' },
      icon: { type: String, default: '' },
      loading: { type: Boolean, default: false },
      disabled: { type: Boolean, default: false },
      block: { type: Boolean, default: false },
    },
    computed: {
      variants() { return UiButtonVariants; },
      sizes() { return UiButtonSizes; },
      iconSize() {
        return this.size === 'xs' ? 'w-3 h-3' : this.size === 'lg' ? 'w-5 h-5' : 'w-4 h-4';
      },
    },
    emits: ['click'],
  };

  // ---------------------------------------------------------------------------
  // IconButton — compact icon-only button
  // ---------------------------------------------------------------------------
  const IconButton = {
    name: 'IconButton',
    template: `
      <button :type="type" :disabled="disabled" :title="title" @click="$emit('click', $event)"
              :class="[
                'p-1.5 rounded transition-colors focus:outline-none disabled:opacity-40 disabled:cursor-not-allowed',
                colorClass
              ]">
        <i :data-lucide="icon" :class="sizeClass"></i>
      </button>
    `,
    props: {
      icon: { type: String, required: true },
      title: { type: String, default: '' },
      type: { type: String, default: 'button' },
      size: { type: [String, Number], default: 4 },
      disabled: { type: Boolean, default: false },
      color: { type: String, default: 'default' },
    },
    computed: {
      sizeClass() {
        const s = String(this.size);
        // Only allow Tailwind pixel sizes that actually exist.
        const allowed = ['3', '3.5', '4', '5', '6'];
        return allowed.includes(s) ? `w-${s} h-${s}` : 'w-4 h-4';
      },
    },
    computed: {
      colorClass() {
        const map = {
          default: 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 hover:text-gray-600 dark:hover:text-gray-300',
          green: 'text-gray-500 hover:bg-green-100 dark:hover:bg-green-900/30 hover:text-green-500',
          blue: 'text-gray-500 hover:bg-blue-100 dark:hover:bg-blue-900/30 hover:text-blue-500',
          red: 'text-gray-500 hover:bg-red-100 dark:hover:bg-red-900/30 hover:text-red-500',
          amber: 'text-gray-500 hover:bg-amber-100 dark:hover:bg-amber-900/30 hover:text-amber-500',
        };
        return map[this.color] || map.default;
      },
    },
    emits: ['click'],
  };

  // ---------------------------------------------------------------------------
  // BaseModal — Teleport + backdrop + esc + title bar + footer slot
  // ---------------------------------------------------------------------------
  const BaseModal = {
    name: 'BaseModal',
    template: `
      <teleport to="body">
        <transition name="ui-modal">
          <div v-if="modelValue" class="fixed inset-0 z-[1040] flex items-center justify-center p-4">
            <div class="absolute inset-0 bg-black/50 backdrop-blur-sm" @click="handleBackdrop"></div>
            <div :class="['relative bg-white dark:bg-gray-800 rounded-xl shadow-2xl border border-gray-200 dark:border-gray-700 w-full flex flex-col', widthClass, maxHClass]">
              <!-- Header -->
              <div v-if="showHeader" class="flex items-center justify-between px-5 py-4 border-b border-gray-200 dark:border-gray-700">
                <h3 class="font-semibold text-gray-900 dark:text-white text-base flex items-center gap-2 min-w-0">
                  <i v-if="icon" :data-lucide="icon" class="w-4 h-4 text-primary shrink-0"></i>
                  <span class="truncate">{{ title }}</span>
                </h3>
                <icon-button icon="x" color="default" size="4" @click="handleClose"></icon-button>
              </div>
              <!-- Body -->
              <div :class="['flex-1 overflow-y-auto', bodyClass]">
                <slot></slot>
              </div>
              <!-- Footer -->
              <div v-if="$slots.footer" class="px-5 py-4 border-t border-gray-200 dark:border-gray-700">
                <slot name="footer"></slot>
              </div>
            </div>
          </div>
        </transition>
      </teleport>
    `,
    props: {
      modelValue: { type: Boolean, default: false },
      title: { type: String, default: '' },
      icon: { type: String, default: '' },
      widthClass: { type: String, default: 'max-w-lg' },
      maxHClass: { type: String, default: 'max-h-[90vh]' },
      bodyClass: { type: String, default: 'p-5' },
      showHeader: { type: Boolean, default: true },
      closeOnBackdrop: { type: Boolean, default: true },
      closeOnEsc: { type: Boolean, default: true },
    },
    methods: {
      handleClose() {
        this.$emit('update:modelValue', false);
        this.$emit('close');
      },
      handleBackdrop() {
        if (this.closeOnBackdrop) this.handleClose();
      },
    },
    watch: {
      // immediate: handle modals that mount already-open; single document-level
      // esc listener (no per-element @keydown, avoiding double close events).
      modelValue: {
        immediate: true,
        handler(v) {
          if (v && this.closeOnEsc) {
            this.$_escHandler = (e) => { if (e.key === 'Escape') this.handleClose(); };
            document.addEventListener('keydown', this.$_escHandler);
          } else if (!v && this.$_escHandler) {
            document.removeEventListener('keydown', this.$_escHandler);
            this.$_escHandler = null;
          }
        },
      },
    },
    beforeUnmount() {
      if (this.$_escHandler) document.removeEventListener('keydown', this.$_escHandler);
    },
    emits: ['update:modelValue', 'close'],
    components: { IconButton },
  };

  // ---------------------------------------------------------------------------
  // ToggleSwitch — unified v-model switch
  // ---------------------------------------------------------------------------
  const ToggleSwitch = {
    name: 'ToggleSwitch',
    template: `
      <button type="button" role="switch" :aria-checked="modelValue" :disabled="disabled"
              @click="$emit('update:modelValue', !modelValue)"
              :class="[
                'relative inline-flex items-center rounded-full transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-primary/50 disabled:opacity-40 disabled:cursor-not-allowed',
                modelValue ? 'bg-primary' : 'bg-gray-200 dark:bg-gray-600',
                size === 'lg' ? 'h-6 w-11' : 'h-5 w-9'
              ]">
        <span :class="[
          'inline-block rounded-full bg-white shadow transform transition-transform duration-200',
          size === 'lg' ? 'h-4 w-4' : 'h-3.5 w-3.5',
          modelValue ? (size === 'lg' ? 'translate-x-6' : 'translate-x-5') : 'translate-x-0.5'
        ]"></span>
      </button>
    `,
    props: {
      modelValue: { type: Boolean, default: false },
      disabled: { type: Boolean, default: false },
      size: { type: String, default: 'sm' },
    },
    emits: ['update:modelValue'],
  };

  // ---------------------------------------------------------------------------
  // TabBar — secondary navigation (tabs = [{id, label, icon?, count?}])
  // ---------------------------------------------------------------------------
  const TabBar = {
    name: 'TabBar',
    template: `
      <!-- max-w-full + overflow-x-auto: page tab sets (7 on Settings, 4 on
           Data) exceed the 390px viewport and used to be clipped off-screen
           with no way to reach the last tabs. Desktop content still fits, so
           w-fit + mx-auto keeps the centred pill look there. -->
      <nav class="flex items-center justify-start md:justify-center gap-1 p-1 bg-gray-100 dark:bg-gray-800 rounded-xl w-fit max-w-full mx-auto overflow-x-auto">
        <button v-for="tab in tabs" :key="tab.id" type="button"
                @click="$emit('update:modelValue', tab.id)"
                class="shrink-0 whitespace-nowrap px-3 py-1.5 sm:px-4 sm:py-2 rounded-lg text-sm font-medium transition-colors flex items-center gap-1.5"
                :class="modelValue === tab.id
                  ? 'bg-white dark:bg-gray-700 text-primary shadow-sm'
                  : 'text-gray-500 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-200'">
          <i v-if="tab.icon" :data-lucide="tab.icon" class="w-4 h-4"></i>
          {{ tab.label }}
          <span v-if="tab.count !== undefined"
                class="px-1.5 py-0.5 text-xs rounded-full"
                :class="modelValue === tab.id ? 'bg-primary/10 text-primary' : 'bg-gray-200 dark:bg-gray-600 text-gray-500 dark:text-gray-500'">
            {{ tab.count }}
          </span>
        </button>
      </nav>
    `,
    props: {
      modelValue: { type: String, default: '' },
      tabs: { type: Array, default: () => [] },
    },
    emits: ['update:modelValue'],
  };

  // ---------------------------------------------------------------------------
  // CardSection — card container + header (icon + title + optional actions)
  // ---------------------------------------------------------------------------
  const CardSection = {
    name: 'CardSection',
    template: `
      <section class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
        <div class="px-4 py-3 bg-gray-50 dark:bg-gray-750 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between gap-3">
          <h3 class="font-medium text-sm text-gray-800 dark:text-gray-200 flex items-center gap-2 min-w-0">
            <i v-if="icon" :data-lucide="icon" class="w-4 h-4 text-primary shrink-0"></i>
            <span class="truncate"><slot name="title">{{ title }}</slot></span>
          </h3>
          <div class="flex items-center gap-2 shrink-0">
            <slot name="header-actions"></slot>
          </div>
        </div>
        <div :class="bodyClass">
          <slot></slot>
        </div>
      </section>
    `,
    props: {
      title: { type: String, default: '' },
      icon: { type: String, default: '' },
      bodyClass: { type: String, default: 'p-4' },
    },
  };

  // ---------------------------------------------------------------------------
  // FormField — label + input slot + hint/error
  // ---------------------------------------------------------------------------
  const FormField = {
    name: 'FormField',
    template: `
      <div>
        <label v-if="label" class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
          {{ label }}<span v-if="required" class="text-red-500 ml-0.5">*</span>
        </label>
        <slot></slot>
        <p v-if="hint" class="mt-1 text-xs text-gray-500 dark:text-gray-500">{{ hint }}</p>
        <p v-if="error" class="mt-1 text-xs text-red-500">{{ error }}</p>
      </div>
    `,
    props: {
      label: { type: String, default: '' },
      hint: { type: String, default: '' },
      error: { type: String, default: '' },
      required: { type: Boolean, default: false },
    },
  };

  // ---------------------------------------------------------------------------
  // StatusBadge — colored pill for status labels
  // ---------------------------------------------------------------------------
  const StatusBadgeColors = {
    gray: 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300',
    green: 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300',
    red: 'bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300',
    amber: 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300',
    blue: 'bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300',
    purple: 'bg-purple-100 dark:bg-purple-900/40 text-purple-700 dark:text-purple-300',
  };

  const StatusBadge = {
    name: 'StatusBadge',
    template: `
      <span :class="['inline-flex items-center px-2 py-0.5 rounded text-xs font-medium', colorClass]">
        <i v-if="icon" :data-lucide="icon" class="w-3 h-3 mr-1"></i>
        <slot></slot>
      </span>
    `,
    props: {
      color: { type: String, default: 'gray' },
      icon: { type: String, default: '' },
    },
    computed: {
      colorClass() { return StatusBadgeColors[this.color] || StatusBadgeColors.gray; },
    },
  };

  // ---------------------------------------------------------------------------
  // RadioGroup — options = [{value, label, icon?, desc?}], layout: segment/card/inline
  // ---------------------------------------------------------------------------
  const RadioGroup = {
    name: 'RadioGroup',
    template: `
      <div :class="layoutClass">
        <button v-for="opt in options" :key="opt.value" type="button"
                @click="$emit('update:modelValue', opt.value)"
                :class="radioClass(opt)">
          <span v-if="opt.icon" class="w-7 h-7 rounded-lg flex items-center justify-center shrink-0"
                :class="modelValue === opt.value ? 'bg-primary/15 text-primary' : 'bg-gray-100 dark:bg-gray-700 text-gray-500'">
            <i :data-lucide="opt.icon" class="w-3.5 h-3.5"></i>
          </span>
          <span class="min-w-0">
            <span class="block text-sm font-medium"
                  :class="modelValue === opt.value ? 'text-primary' : 'text-gray-800 dark:text-gray-200'">
              {{ opt.label }}
            </span>
            <span v-if="opt.desc" class="block text-xs text-gray-500 dark:text-gray-500 leading-relaxed mt-0.5">
              {{ opt.desc }}
            </span>
          </span>
        </button>
      </div>
    `,
    props: {
      modelValue: { type: [String, Number], default: '' },
      options: { type: Array, default: () => [] },
      layout: { type: String, default: 'inline' },
    },
    computed: {
      layoutClass() {
        if (this.layout === 'segment') return 'flex gap-2';
        if (this.layout === 'card') return 'grid grid-cols-1 sm:grid-cols-3 gap-3';
        return 'flex flex-wrap gap-2';
      },
    },
    methods: {
      radioClass(opt) {
        if (this.layout === 'segment') {
          return [
            'px-3 py-1.5 border-2 rounded-lg text-sm font-medium transition-colors flex items-center gap-1.5',
            this.modelValue === opt.value
              ? 'border-primary bg-primary/5 ring-1 ring-primary/30 text-primary'
              : 'border-gray-200 dark:border-gray-600 text-gray-500 dark:text-gray-500 hover:border-primary/50',
          ].join(' ');
        }
        if (this.layout === 'card') {
          return [
            'text-left rounded-xl border p-3.5 transition-all flex items-start gap-2',
            this.modelValue === opt.value
              ? 'border-primary bg-primary/5 ring-1 ring-primary/30 dark:bg-primary/10'
              : 'border-gray-200 dark:border-gray-600 hover:border-gray-300 dark:hover:border-gray-500 hover:bg-gray-50 dark:hover:bg-gray-700/40',
          ].join(' ');
        }
        return [
          'px-3 py-1.5 rounded-lg text-sm font-medium transition-colors flex items-center gap-1.5',
          this.modelValue === opt.value
            ? 'border-primary bg-primary/5 ring-1 ring-primary/30 text-primary'
            : 'border-gray-200 dark:border-gray-600 text-gray-500 dark:text-gray-500 hover:border-primary/50',
        ].join(' ');
      },
    },
    emits: ['update:modelValue'],
  };

  // SegmentedControl = RadioGroup with segment layout (kept as alias for clarity)
  const SegmentedControl = {
    name: 'SegmentedControl',
    template: `<radio-group :model-value="modelValue" :options="options" layout="segment" @update:model-value="$emit('update:modelValue', $event)"></radio-group>`,
    props: {
      modelValue: { type: [String, Number], default: '' },
      options: { type: Array, default: () => [] },
    },
    emits: ['update:modelValue'],
    components: { RadioGroup },
  };

  // ---------------------------------------------------------------------------
  // BaseSelect — styled native select (options = [{value,label}] or plain)
  // ---------------------------------------------------------------------------
  const BaseSelect = {
    name: 'BaseSelect',
    template: `
      <div class="relative w-full">
        <select :value="modelValue" :disabled="disabled" @change="$emit('update:modelValue', $event.target.value)"
                :class="[
                  'w-full px-3 py-2 text-sm bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 disabled:opacity-50',
                  showChevron ? 'appearance-none pr-8' : ''
                ]">
          <option v-if="placeholder" value="">{{ placeholder }}</option>
          <slot></slot>
          <option v-for="opt in normalizedOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
        </select>
        <i v-if="showChevron" data-lucide="chevron-down"
           class="w-4 h-4 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-gray-500"></i>
      </div>
    `,
    props: {
      modelValue: { type: [String, Number], default: '' },
      options: { type: Array, default: () => [] },
      disabled: { type: Boolean, default: false },
      showChevron: { type: Boolean, default: true },
      placeholder: { type: String, default: '' },
    },
    computed: {
      normalizedOptions() {
        return this.options.map((o) => (typeof o === 'object' && o !== null && 'value' in o) ? o : { value: o, label: o });
      },
    },
    emits: ['update:modelValue'],
  };

  // ---------------------------------------------------------------------------
  // DirectoryPickerModal — shared by sessions.js + settings.js
  // Emits: update:modelValue, browse(path), goUp, select(path), confirm
  // Props mirror page dirPicker state: roots/path/entries/loading/error/selected
  // ---------------------------------------------------------------------------
  const DirectoryPickerModal = {
    name: 'DirectoryPickerModal',
    template: `
      <teleport to="body">
        <div v-if="modelValue" class="fixed inset-0 z-[1040] flex items-center justify-center p-4">
          <div class="absolute inset-0 bg-black/40" @click="close"></div>
          <div class="relative w-full max-w-2xl h-[600px] flex flex-col rounded-xl shadow-2xl bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700">
            <!-- Header -->
            <div class="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
              <div class="flex items-center gap-2 min-w-0">
                <i data-lucide="folder-open" class="w-4 h-4 text-primary shrink-0"></i>
                <span class="font-medium text-sm text-gray-900 dark:text-white truncate">{{ title }}</span>
              </div>
              <icon-button icon="x" color="default" size="4" @click="close"></icon-button>
            </div>
            <!-- Roots -->
            <div class="px-3 pt-2 pb-1 flex flex-wrap gap-1 border-b border-gray-100 dark:border-gray-700/60">
              <button v-for="r in roots" :key="r.path + r.kind" type="button"
                      @click="$emit('browse', r.path)"
                      class="px-2 py-0.5 rounded text-xs font-medium bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-primary/10 hover:text-primary transition-colors">
                {{ r.label }}
              </button>
            </div>
            <!-- Path bar -->
            <div class="px-3 py-2 flex items-center gap-2 border-b border-gray-100 dark:border-gray-700/60">
              <icon-button icon="arrow-up" color="default" size="3.5" :disabled="!parentPath"
                           :title="t('common.parent') || 'Parent'"
                           @click="$emit('goUp')"></icon-button>
              <input :value="pathInput" @input="$emit('update:pathInput', $event.target.value)"
                     @keydown.enter.prevent="$emit('browse', pathInput)"
                     class="flex-1 min-w-0 px-2 py-1 text-xs rounded border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-800 dark:text-gray-200 font-mono"
                     :placeholder="t('common.absolutePath') || 'Absolute path'" />
              <base-button variant="secondary" size="xs" @click="$emit('browse', pathInput)">
                {{ t('common.go') || 'Go' }}
              </base-button>
            </div>
            <!-- Entries -->
            <div class="flex-1 overflow-y-auto px-1 py-1">
              <div v-if="loading" class="p-6 text-center text-xs text-gray-500">
                {{ t('common.loading') || 'Loading...' }}
              </div>
              <div v-else-if="error" class="p-6 text-center text-xs text-red-500">{{ error }}</div>
              <div v-else-if="!filteredEntries.length" class="p-6 text-center text-xs text-gray-500">
                {{ effectiveEmptyText }}
              </div>
              <div v-else class="space-y-0.5">
                <button v-for="e in filteredEntries" :key="e.path" type="button"
                        @click="handleEntryClick(e)" @dblclick="handleEntryDblClick(e)"
                        :class="[
                          'w-full px-2 py-1.5 rounded text-left text-xs flex items-center gap-2 transition-colors',
                          e.is_dir ? 'hover:bg-gray-100 dark:hover:bg-gray-700 cursor-pointer' : 'hover:bg-gray-100 dark:hover:bg-gray-700 cursor-pointer',
                          selected === e.path ? 'bg-primary/10 text-primary' : 'text-gray-700 dark:text-gray-300'
                        ]">
                  <i :data-lucide="e.is_dir ? 'folder' : 'file'"
                     :class="['w-3.5 h-3.5 shrink-0', e.is_dir ? 'text-amber-500' : '']"></i>
                  <span class="truncate flex-1">{{ e.name }}</span>
                </button>
              </div>
            </div>
            <!-- Footer -->
            <div class="px-4 py-3 border-t border-gray-200 dark:border-gray-700 flex items-center justify-between gap-3">
              <slot name="footer-left">
                <div class="text-xs text-gray-500 dark:text-gray-500 font-mono truncate flex-1">
                  {{ selected || path || '' }}
                </div>
              </slot>
              <div class="flex gap-2 shrink-0">
                <base-button variant="secondary" size="sm" @click="close">
                  {{ t('common.cancel') || 'Cancel' }}
                </base-button>
                <base-button variant="primary" size="sm" @click="$emit('confirm')">
                  {{ effectiveConfirmLabel }}
                </base-button>
              </div>
            </div>
          </div>
        </div>
      </teleport>
    `,
    props: {
      modelValue: { type: Boolean, default: false },
      title: { type: String, default: '' },
      roots: { type: Array, default: () => [] },
      path: { type: String, default: '' },
      pathInput: { type: String, default: '' },
      parentPath: { type: String, default: '' },
      entries: { type: Array, default: () => [] },
      loading: { type: Boolean, default: false },
      error: { type: String, default: '' },
      selected: { type: String, default: '' },
      showFiles: { type: Boolean, default: true },
      emptyText: { type: String, default: '' },
      confirmLabel: { type: String, default: '' },
      // When true: single click selects, double click browses into dir
      // (sessions.js workspace picker); when false: click browses into dir
      // (settings.js workspace picker).
      doubleClickBrowse: { type: Boolean, default: false },
    },
    computed: {
      filteredEntries() {
        if (this.showFiles) return this.entries;
        return (this.entries || []).filter((e) => e.is_dir);
      },
      effectiveEmptyText() {
        return this.emptyText || this.t('common.emptyDirectory') || 'Empty directory';
      },
      effectiveConfirmLabel() {
        return this.confirmLabel || this.t('common.confirm') || 'Confirm';
      },
    },
    methods: {
      close() {
        this.$emit('update:modelValue', false);
      },
      handleEntryClick(e) {
        if (this.doubleClickBrowse) {
          // select on single click; parent may pass dirs only
          this.$emit('select', e.path);
          return;
        }
        this.$emit(e.is_dir ? 'browse' : 'select', e.path);
      },
      handleEntryDblClick(e) {
        if (this.doubleClickBrowse && e.is_dir) {
          this.$emit('browse', e.path);
        }
      },
    },
    watch: {
      modelValue: {
        immediate: true,
        handler(v) {
          if (v) {
            this.$_dirEscHandler = (e) => { if (e.key === 'Escape') this.close(); };
            document.addEventListener('keydown', this.$_dirEscHandler);
          } else if (this.$_dirEscHandler) {
            document.removeEventListener('keydown', this.$_dirEscHandler);
            this.$_dirEscHandler = null;
          }
        },
      },
    },
    beforeUnmount() {
      if (this.$_dirEscHandler) document.removeEventListener('keydown', this.$_dirEscHandler);
    },
    emits: ['update:modelValue', 'browse', 'goUp', 'select', 'confirm', 'update:pathInput'],
    components: { IconButton, BaseButton },
  };

  // ---------------------------------------------------------------------------
  // LanguageSwitch — compact endonym toggle for chrome-less pages
  // ---------------------------------------------------------------------------
  // The navbar owns the full dropdown, but `meta.bare` routes (setup / unlock)
  // render without a navbar, so a first-run user had no way to reach Chinese at
  // all. This is the minimal standalone equivalent: two buttons, no dropdown,
  // no outside-click bookkeeping, works at 320px.
  const LanguageSwitch = {
    name: 'LanguageSwitch',
    template: `
      <div class="inline-flex items-center gap-0.5 p-0.5 rounded-lg bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700"
           role="group" :aria-label="t('settings.language') || 'Language'">
        <button v-for="lang in languages" :key="lang.code" type="button"
                @click="choose(lang.code)"
                :aria-pressed="currentLanguage === lang.code ? 'true' : 'false'"
                class="px-2.5 py-1 text-xs font-medium rounded-md transition-colors focus:outline-none focus:ring-2 focus:ring-primary/50"
                :class="currentLanguage === lang.code
                  ? 'bg-white dark:bg-gray-700 text-primary shadow-sm'
                  : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'">
          {{ lang.label }}
        </button>
      </div>
    `,
    computed: {
      // Labels come from i18n: both locales ship each language's endonym, so
      // no non-ASCII literal lives in this source file. Depends on
      // currentLanguage via t(), so it re-renders on switch.
      languages() {
        return [
          { code: 'en', label: this.t('common.english') || 'English' },
          { code: 'zh', label: this.t('common.chinese') || 'Chinese' },
        ];
      },
    },
    methods: {
      async choose(code) {
        if (!window.i18n || code === this.currentLanguage) return;
        try {
          await window.i18n.setLanguage(code);
        } catch (error) {
          console.error('Failed to set language:', error);
        }
      },
    },
  };

  // ---------------------------------------------------------------------------
  // Register everything
  // ---------------------------------------------------------------------------
  global.registerUiComponents = function registerUiComponents(app) {
    if (!app) return;
    const registry = {
      'base-button': BaseButton,
      'language-switch': LanguageSwitch,
      'icon-button': IconButton,
      'base-modal': BaseModal,
      'toggle-switch': ToggleSwitch,
      'tab-bar': TabBar,
      'card-section': CardSection,
      'form-field': FormField,
      'status-badge': StatusBadge,
      'radio-group': RadioGroup,
      'segmented-control': SegmentedControl,
      'base-select': BaseSelect,
      'directory-picker-modal': DirectoryPickerModal,
    };
    for (const name of Object.keys(registry)) {
      if (app.component(name)) continue; // avoid double registration
      app.component(name, registry[name]);
    }
  };

  // Expose class maps + shared utils for pages
  global.UiKit = {
    buttonVariants: UiButtonVariants,
    buttonSizes: UiButtonSizes,
    statusBadgeColors: StatusBadgeColors,

    /** Relativize an ISO timestamp ("just now", "5m ago", "2h ago", "3d ago"),
     *  falling back to locale date string for dates older than 7 days. */
    formatTime(iso, i18n) {
      if (!iso) return '';
      try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return String(iso);
        const diff = Date.now() - d.getTime();
        const min = 60000, hour = 3600000, day = 86400000, week = 604800000;
        if (diff < min && diff > -min) return (i18n && i18n.t) ? i18n.t('common.justNow') || 'just now' : 'just now';
        if (diff < hour) return Math.floor(diff / min) + 'm ago';
        if (diff < day) return Math.floor(diff / hour) + 'h ago';
        if (diff < week) return Math.floor(diff / day) + 'd ago';
        return d.toLocaleDateString();
      } catch (e) {
        return String(iso);
      }
    },

    /** Pretty-print byte count (B/KB/MB/GB). */
    formatBytes(bytes, decimals = 1) {
      if (!bytes && bytes !== 0) return '-';
      if (bytes === 0) return '0 B';
      const k = 1024;
      const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
      const i = Math.floor(Math.log(bytes) / Math.log(k));
      return parseFloat((bytes / Math.pow(k, i)).toFixed(decimals)) + ' ' + sizes[i];
    },

    /** Trigger a client-side file download for JSON data. */
    downloadJSON(filename, data) {
      try {
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (e) {
        console.warn('downloadJSON failed:', e);
      }
    },

    /** Resolve the active primary color as a hex string (follows theme). */
    primaryHex() {
      try {
        const rgb = getComputedStyle(document.documentElement).getPropertyValue('--primary-rgb').trim();
        // Variable is a space-separated triplet: "99 102 241"
        const parts = (rgb || '').split(/\s+/).map((s) => parseInt(s, 10));
        if (parts.length === 3 && parts.every((n) => !isNaN(n))) {
          return '#' + parts.map((n) => n.toString(16).padStart(2, '0')).join('');
        }
      } catch (e) { /* fall through */ }
      return '#6366f1'; // indigo-500 default
    },

    /** Copy text to clipboard with legacy fallback. */
    async copyToClipboard(text) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (e) {
        try {
          const ta = document.createElement('textarea');
          ta.value = text;
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
          return true;
        } catch (e2) {
          return false;
        }
      }
    },
  };
})(window);
