/**
 * File Explorer components for the Sessions page.
 *
 * FileTreePanel  — lazy-loading directory tree pinned under the session list
 *                  (left sidebar). Data comes from the existing FS browse API
 *                  (store.api.fsBrowse); the root follows the session workspace.
 * FileViewerPane — IDE-style viewer/editor placed left of the chat column.
 *                  Tabs + line numbers + syntax highlighting (reuses the
 *                  zero-dependency mdHighlight tokenizer from sessions.js) and
 *                  a draft/edit mode whose save path calls store.api.fsWrite
 *                  (backend endpoint lands in phase 2).
 *
 * Both are plain options objects exposed as global lexical bindings so
 * pages/sessions.js can register them in `components`.
 */

/* ext -> badge chip (text + tailwind classes). Mirrors the colored file-type
   chips of IDE explorers; tailwind play CDN generates classes on DOM insert. */
const FM_BADGES = {
  py: ['PY', 'bg-indigo-500/15 text-indigo-600 dark:text-indigo-300'],
  js: ['JS', 'bg-yellow-500/15 text-yellow-600 dark:text-yellow-300'],
  mjs: ['JS', 'bg-yellow-500/15 text-yellow-600 dark:text-yellow-300'],
  cjs: ['JS', 'bg-yellow-500/15 text-yellow-600 dark:text-yellow-300'],
  ts: ['TS', 'bg-blue-500/15 text-blue-600 dark:text-blue-300'],
  tsx: ['TS', 'bg-blue-500/15 text-blue-600 dark:text-blue-300'],
  jsx: ['JS', 'bg-yellow-500/15 text-yellow-600 dark:text-yellow-300'],
  json: ['{}', 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-300'],
  md: ['MD', 'bg-sky-500/15 text-sky-600 dark:text-sky-300'],
  html: ['<>', 'bg-orange-500/15 text-orange-600 dark:text-orange-300'],
  htm: ['<>', 'bg-orange-500/15 text-orange-600 dark:text-orange-300'],
  vue: ['VU', 'bg-green-500/15 text-green-600 dark:text-green-300'],
  css: ['#', 'bg-purple-500/15 text-purple-600 dark:text-purple-300'],
  scss: ['#', 'bg-purple-500/15 text-purple-600 dark:text-purple-300'],
  less: ['#', 'bg-purple-500/15 text-purple-600 dark:text-purple-300'],
  yml: ['Y', 'bg-fuchsia-500/15 text-fuchsia-600 dark:text-fuchsia-300'],
  yaml: ['Y', 'bg-fuchsia-500/15 text-fuchsia-600 dark:text-fuchsia-300'],
  sh: ['$', 'bg-lime-500/15 text-lime-600 dark:text-lime-300'],
  bash: ['$', 'bg-lime-500/15 text-lime-600 dark:text-lime-300'],
  sql: ['SQ', 'bg-cyan-500/15 text-cyan-600 dark:text-cyan-300'],
  c: ['C', 'bg-slate-500/15 text-slate-600 dark:text-slate-300'],
  h: ['H', 'bg-slate-500/15 text-slate-600 dark:text-slate-300'],
  cpp: ['C+', 'bg-pink-500/15 text-pink-600 dark:text-pink-300'],
  hpp: ['C+', 'bg-pink-500/15 text-pink-600 dark:text-pink-300'],
  go: ['GO', 'bg-cyan-500/15 text-cyan-600 dark:text-cyan-300'],
  rs: ['RS', 'bg-orange-500/15 text-orange-600 dark:text-orange-300'],
  java: ['JV', 'bg-red-500/15 text-red-600 dark:text-red-300'],
  png: ['IMG', 'bg-violet-500/15 text-violet-600 dark:text-violet-300'],
  jpg: ['IMG', 'bg-violet-500/15 text-violet-600 dark:text-violet-300'],
  jpeg: ['IMG', 'bg-violet-500/15 text-violet-600 dark:text-violet-300'],
  gif: ['IMG', 'bg-violet-500/15 text-violet-600 dark:text-violet-300'],
  webp: ['IMG', 'bg-violet-500/15 text-violet-600 dark:text-violet-300'],
  svg: ['IMG', 'bg-violet-500/15 text-violet-600 dark:text-violet-300'],
  zip: ['ZIP', 'bg-amber-500/15 text-amber-600 dark:text-amber-300'],
  pdf: ['PDF', 'bg-red-500/15 text-red-600 dark:text-red-300'],
  txt: ['TX', 'bg-gray-500/15 text-gray-600 dark:text-gray-300'],
};

const FM_LANGS = {
  py: 'python',
  js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'javascript',
  ts: 'typescript', tsx: 'typescript',
  json: 'json',
  sh: 'bash', bash: 'bash',
  html: 'html', htm: 'html', vue: 'html',
  css: 'css', scss: 'css', less: 'css',
  yml: 'yaml', yaml: 'yaml',
  sql: 'sql',
  c: 'c', h: 'c',
  cpp: 'cpp', hpp: 'cpp',
  java: 'java',
  cs: 'csharp',
  go: 'go',
  rs: 'rust',
};

function fmExt(name) {
  const i = String(name || '').lastIndexOf('.');
  return i > 0 ? String(name).slice(i + 1).toLowerCase() : '';
}

function fmBadge(name) {
  const hit = FM_BADGES[fmExt(name)];
  if (hit) return { text: hit[0], cls: hit[1] };
  const ext = fmExt(name);
  return { text: ext ? ext.slice(0, 2).toUpperCase() : '·', cls: 'bg-gray-500/15 text-gray-500 dark:text-gray-400' };
}

function fmLang(name) {
  return FM_LANGS[fmExt(name)] || '';
}

const FM_IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico']);

function fmIsImage(name) {
  return FM_IMAGE_EXTS.has(fmExt(name));
}

function fmFormatBytes(size) {
  const n = Number(size);
  if (!isFinite(n) || n < 0) return '';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1024 / 1024).toFixed(1) + ' MB';
}

/* sessions.js declares mdHighlight as a top-level function of a classic
   script, so it lands on window once that file evaluates (always before any
   user interaction). Fall back to plain escaping if it is ever missing. */
function fmHighlight(code, lang) {
  if (typeof window.mdHighlight === 'function') return window.mdHighlight(code, lang);
  return String(code ?? '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}

// ---------------------------------------------------------------------------
// FileTreePanel
// ---------------------------------------------------------------------------
const FileTreePanel = {
  name: 'FileTreePanel',
  props: {
    sessionId: { type: String, default: '' },
    rootPath: { type: String, default: '' },
  },
  emits: ['open-file', 'collapse'],
  data() {
    return {
      nodes: Object.create(null),
      rootLoaded: false,
      rootLoading: false,
      rootError: '',
      search: '',
      activePath: '',
    };
  },
  computed: {
    visibleRows() {
      const rows = [];
      const walk = (path, depth) => {
        const node = this.nodes[path];
        if (!node || !node.children) return;
        for (const childPath of node.children) {
          const child = this.nodes[childPath];
          if (!child) continue;
          rows.push({ path: childPath, name: child.name, type: child.type, size: child.size, depth, expanded: !!child.expanded, loading: !!child.loading, error: child.error || '' });
          if (child.type === 'dir') {
            if (child.expanded && !child.loaded && !child.loading) {
              rows.push({ path: childPath + '\u0000err', name: child.error || 'error', type: 'error', dirPath: childPath, depth: depth + 1 });
            } else if (child.expanded) {
              walk(childPath, depth + 1);
            }
          }
        }
      };
      if (this.rootPath) walk(this.rootPath, 0);
      const q = this.search.trim().toLowerCase();
      if (!q) return rows;
      return rows.filter((r) => r.type !== 'error' && String(r.name).toLowerCase().includes(q));
    },
  },
  watch: {
    rootPath() { this.resetAndLoad(); },
    sessionId() { this.resetAndLoad(); },
  },
  created() { this.resetAndLoad(); },
  methods: {
    badge: fmBadge,
    fmtSize: fmFormatBytes,
    resetAndLoad() {
      this.nodes = Object.create(null);
      this.rootLoaded = false;
      this.rootError = '';
      this.activePath = '';
      if (this.sessionId && this.rootPath) this.loadRoot();
    },
    ensureNode(path, base) {
      // A browsed directory passes {name: path} as fallback — never let the
      // full path become the display name (rows must show the basename).
      const realName = base.name && base.name !== path
        ? base.name
        : String(path).split(/[\\/]/).filter(Boolean).pop() || path;
      let node = this.nodes[path];
      if (!node) {
        node = { name: realName, type: base.type, size: base.size ?? null, children: null, loaded: false, loading: false, expanded: false, error: '' };
        this.nodes[path] = node;
      } else {
        node.name = realName;
        node.type = base.type;
        if (base.size != null) node.size = base.size;
      }
      return node;
    },
    applyDir(path, data) {
      const node = this.ensureNode(path, { name: path, type: 'dir' });
      const entries = (data && data.entries) || [];
      node.children = entries.map((e) => e.path);
      node.loaded = true;
      node.error = '';
      for (const e of entries) this.ensureNode(e.path, e);
    },
    async loadRoot() {
      this.rootLoading = true;
      this.rootError = '';
      try {
        const data = await window.BoarAiStore.api.fsBrowse(this.rootPath, false);
        this.applyDir(this.rootPath, data);
        this.rootLoaded = true;
      } catch (e) {
        this.rootError = String((e && e.message) || e);
      } finally {
        this.rootLoading = false;
      }
    },
    async toggleDir(path) {
      const node = this.nodes[path];
      if (!node) return;
      if (node.loaded) { node.expanded = !node.expanded; return; }
      node.loading = true;
      node.error = '';
      try {
        const data = await window.BoarAiStore.api.fsBrowse(path, false);
        this.applyDir(path, data);
        node.expanded = true;
      } catch (e) {
        node.error = String((e && e.message) || e);
        node.expanded = true;
      } finally {
        node.loading = false;
      }
    },
    async refresh() {
      if (!this.rootPath) return;
      const dirs = [this.rootPath];
      for (const path of Object.keys(this.nodes)) {
        const n = this.nodes[path];
        if (n.type === 'dir' && n.loaded && n.expanded && path !== this.rootPath) dirs.push(path);
      }
      this.rootLoading = true;
      try {
        await Promise.all(dirs.map(async (p) => {
          try {
            const data = await window.BoarAiStore.api.fsBrowse(p, false);
            this.applyDir(p, data);
          } catch (e) {
            if (p === this.rootPath) this.rootError = String((e && e.message) || e);
            else if (this.nodes[p]) this.nodes[p].error = String((e && e.message) || e);
          }
        }));
        this.rootLoaded = true;
      } finally {
        this.rootLoading = false;
      }
    },
    clickRow(row) {
      if (row.type === 'dir') { this.toggleDir(row.path); return; }
      if (row.type === 'error') { this.toggleDir(row.dirPath); return; }
      this.activePath = row.path;
      this.$emit('open-file', { path: row.path, name: row.name, size: row.size });
    },
    // fs.changed (any client saved a file): debounce-refresh loaded dirs so
    // rapid agent writes collapse into one browse round-trip.
    onFsChanged(path) {
      if (!path) return;
      if (this._fmRefreshTimer) clearTimeout(this._fmRefreshTimer);
      this._fmRefreshTimer = setTimeout(() => {
        this._fmRefreshTimer = null;
        this.refresh();
      }, 800);
    },
  },
  template: `
    <div class="fm-tree flex flex-col min-h-0 bg-white dark:bg-gray-800">
      <div class="flex items-center gap-1 px-2 h-8 flex-shrink-0">
        <button type="button" class="fm-icon-btn" @click="$emit('collapse')"
                :title="t('files.collapse') || 'Collapse'">
          <i data-lucide="chevron-down" class="w-3.5 h-3.5 text-gray-400 dark:text-gray-500"></i>
        </button>
        <i data-lucide="folder-tree" class="w-3.5 h-3.5 text-gray-400 dark:text-gray-500"></i>
        <span class="text-xs font-medium text-gray-600 dark:text-gray-300 truncate flex-1" :title="rootPath">
          {{ t('files.title') || 'Files' }}
        </span>
        <button type="button" class="fm-icon-btn" @click="refresh"
                :title="t('files.refresh') || 'Refresh'">
          <i data-lucide="refresh-cw" class="w-3 h-3 text-gray-400 dark:text-gray-500" :class="{ 'animate-spin': rootLoading }"></i>
        </button>
      </div>
      <div class="px-2 pb-1.5 flex-shrink-0">
        <div class="flex items-center gap-1.5 px-2 h-7 rounded-md bg-gray-100 dark:bg-gray-700/60 border border-gray-200 dark:border-gray-600 focus-within:border-primary transition-colors">
          <i data-lucide="search" class="w-3 h-3 text-gray-400 dark:text-gray-500 shrink-0"></i>
          <input v-model="search" type="text"
                 class="flex-1 min-w-0 bg-transparent text-xs outline-none text-gray-700 dark:text-gray-200 placeholder-gray-400 dark:placeholder-gray-500"
                 :placeholder="t('files.searchPlaceholder') || 'Search files'">
        </div>
      </div>
      <div class="fm-tree-body flex-1 min-h-0 overflow-y-auto px-1 pb-2">
        <div v-if="!sessionId || !rootPath" class="p-4 text-center text-xs text-gray-400 dark:text-gray-500">
          {{ t('files.noSession') || 'No session workspace' }}
        </div>
        <div v-else-if="rootLoading && !rootLoaded" class="p-4 text-center">
          <i data-lucide="loader-circle" class="w-4 h-4 animate-spin mx-auto text-gray-300 dark:text-gray-600"></i>
        </div>
        <div v-else-if="rootError && !rootLoaded" class="p-3 text-center">
          <p class="text-xs text-red-500 dark:text-red-400 mb-1 break-words">{{ rootError }}</p>
          <button type="button" class="text-xs text-primary hover:underline" @click="loadRoot">
            {{ t('files.retry') || 'Retry' }}
          </button>
        </div>
        <div v-else-if="!visibleRows.length" class="p-4 text-center text-xs text-gray-400 dark:text-gray-500">
          {{ search ? (t('files.searchNoResult') || 'No matches') : (t('files.empty') || 'Empty directory') }}
        </div>
        <div v-else>
          <button v-for="row in visibleRows" :key="row.path" type="button"
                  class="fm-row w-full flex items-center gap-1.5 h-6 rounded text-left pr-1"
                  :class="activePath === row.path
                    ? 'bg-primary/10 text-primary'
                    : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700/50'"
                  :style="{ paddingLeft: (6 + row.depth * 12) + 'px' }"
                  :title="row.type === 'error' ? row.name : row.path"
                  @click="clickRow(row)">
            <i v-if="row.type === 'dir'"
               :data-lucide="row.expanded ? 'chevron-down' : 'chevron-right'"
               class="w-3 h-3 shrink-0 text-gray-400 dark:text-gray-500"></i>
            <span v-else class="w-3 shrink-0"></span>
            <i v-if="row.type === 'dir'"
               :data-lucide="row.expanded ? 'folder-open' : 'folder'"
               class="w-3.5 h-3.5 shrink-0 text-amber-500/90"></i>
            <i v-else-if="row.type === 'error'" data-lucide="alert-circle"
               class="w-3.5 h-3.5 shrink-0 text-red-400"></i>
            <span v-else class="fm-badge shrink-0" :class="badge(row.name).cls">{{ badge(row.name).text }}</span>
            <span class="truncate flex-1 text-xs">{{ row.name }}</span>
            <i v-if="row.type === 'dir' && row.loading" data-lucide="loader-circle"
               class="w-3 h-3 animate-spin text-gray-300 dark:text-gray-600 shrink-0"></i>
            <span v-else-if="row.type === 'file' && row.size != null"
                  class="text-[10px] text-gray-400 dark:text-gray-500 shrink-0">{{ fmtSize(row.size) }}</span>
          </button>
        </div>
      </div>
    </div>
  `,
};

// ---------------------------------------------------------------------------
// FileViewerPane
// ---------------------------------------------------------------------------
const FileViewerPane = {
  name: 'FileViewerPane',
  props: {
    sessionId: { type: String, default: '' },
  },
  emits: ['close'],
  data() {
    return {
      tabs: [],
      activePath: '',
      wrap: false,
      pendingClose: null,
    };
  },
  computed: {
    activeTab() {
      return this.tabs.find((x) => x.path === this.activePath) || null;
    },
    viewText() {
      const tab = this.activeTab;
      if (!tab) return '';
      return tab.editing ? tab.draft : (tab.content ?? '');
    },
    lineCount() {
      if (!this.activeTab || this.activeTab.content == null || this.activeTab.binary) return 0;
      return this.viewText.split('\n').length;
    },
    gutterHidden() {
      return this.lineCount === 0 || this.lineCount > 20000;
    },
    hlHtml() {
      const tab = this.activeTab;
      if (!tab || tab.content == null || tab.binary || tab.loading) return '';
      return fmHighlight(this.viewText, tab.lang);
    },
    rawSrc() {
      const tab = this.activeTab;
      return tab && tab.image ? (tab.objectUrl || '') : '';
    },
  },
  watch: {
    sessionId() { this.clearTabs(); },
  },
  methods: {
    badge: fmBadge,
    fmtSize: fmFormatBytes,
    clearTabs() {
      for (const tab of this.tabs) this.revokeUrl(tab);
      this.tabs = [];
      this.activePath = '';
      this.pendingClose = null;
    },
    revokeUrl(tab) {
      if (tab && tab.objectUrl) {
        URL.revokeObjectURL(tab.objectUrl);
        tab.objectUrl = '';
      }
    },
    openFile(file) {
      if (!file || !file.path) return;
      this.pendingClose = null;
      const existing = this.tabs.find((x) => x.path === file.path);
      if (existing) {
        this.activePath = file.path;
        return;
      }
      const tab = {
        path: file.path,
        name: file.name || String(file.path).split(/[\\/]/).pop(),
        lang: fmLang(file.name || file.path),
        content: null,
        draft: '',
        dirty: false,
        editing: false,
        loading: false,
        error: '',
        binary: false,
        truncated: false,
        stale: false,
        image: fmIsImage(file.name || file.path),
        size: file.size ?? null,
        mtime: null,
      };
      this.tabs.push(tab);
      // Mutate through the reactive proxy: edits on the raw object would
      // bypass Vue's triggers and leave the pane stuck on the loading state.
      const live = this.tabs[this.tabs.length - 1];
      this.activePath = live.path;
      this.load(live);
    },
    async load(tab) {
      if (!tab) return;
      tab.loading = true;
      tab.error = '';
      if (tab.image) {
        // <img src> cannot carry the Authorization header, so the bytes are
        // fetched (auth.js injects the bearer) and served via objectURL.
        try {
          const q = new URLSearchParams({ path: tab.path });
          if (this.sessionId) q.set('session_id', this.sessionId);
          const resp = await fetch(`/api/fs/raw?${q.toString()}`);
          if (!resp.ok) {
            const body = await resp.json().catch(() => null);
            throw new Error(body?.error?.message || `Read failed (HTTP ${resp.status})`);
          }
          const blob = await resp.blob();
          this.revokeUrl(tab);
          tab.objectUrl = URL.createObjectURL(blob);
          tab.size = blob.size;
          tab.binary = false;
        } catch (e) {
          tab.error = String((e && e.message) || e);
        } finally {
          tab.loading = false;
        }
        return;
      }
      try {
        const data = await window.BoarAiStore.api.fsRead(tab.path, this.sessionId);
        if (data && data.binary) {
          tab.binary = true;
          tab.content = null;
        } else {
          tab.content = (data && data.content) || '';
          tab.draft = tab.content;
          tab.truncated = !!(data && data.truncated);
          if (data && data.size != null) tab.size = data.size;
          if (data && data.mtime != null) tab.mtime = data.mtime;
        }
        tab.stale = false;
        tab.dirty = false;
      } catch (e) {
        tab.error = String((e && e.message) || e);
      } finally {
        tab.loading = false;
      }
    },
    canEdit(tab) {
      return !!tab && tab.content != null && !tab.binary && !tab.truncated && !tab.loading;
    },
    toggleEdit() {
      const tab = this.activeTab;
      if (!this.canEdit(tab)) return;
      tab.editing = !tab.editing;
      if (tab.editing) {
        this.$nextTick(() => {
          const el = this.$refs.editor;
          if (el) el.focus();
        });
      }
    },
    onDraftInput() {
      const tab = this.activeTab;
      if (tab) tab.dirty = true;
    },
    async saveTab(tab) {
      if (!tab || !tab.dirty) return;
      // Client-side pre-check mirrors the server cap (768 KiB) so oversized
      // drafts fail fast with a friendly toast instead of an HTTP 400.
      const bytes = new TextEncoder().encode(tab.draft).length;
      if (bytes > 768 * 1024) {
        window.BoarAiStore.actions.error(this.t('files.tooLargeToSave') || 'File too large to save');
        return;
      }
      try {
        const data = await window.BoarAiStore.api.fsWrite(
          tab.path, tab.draft, this.sessionId, tab.mtime);
        tab.content = tab.draft;
        tab.dirty = false;
        tab.stale = false;
        if (data && data.size != null) tab.size = data.size;
        if (data && data.mtime != null) tab.mtime = data.mtime;
        window.BoarAiStore.actions.success(this.t('files.saved') || 'Saved');
        if (this.pendingClose === tab.path) this.closeTab(tab);
      } catch (e) {
        if (e && e.status === 409) {
          // Optimistic lock tripped: the file moved on disk since load.
          tab.stale = true;
          return;
        }
        window.BoarAiStore.actions.error(
          (this.t('files.saveFailed') || 'Save failed') + ': ' + String((e && e.message) || e));
      }
    },
    // fs.changed from any client: flag the tab stale when the mtime moved.
    onFsChanged(path, mtime) {
      if (!path) return;
      const tab = this.tabs.find((x) => x.path === path);
      if (!tab) return;
      if (mtime != null && tab.mtime != null && String(mtime) !== String(tab.mtime)) {
        tab.stale = true;
      }
    },
    reloadTab(tab) {
      if (!tab) return;
      tab.stale = false;
      this.load(tab);
    },
    keepMine(tab) {
      if (!tab) return;
      // Keep the draft; the next save trips the 409 lock again if the disk
      // copy still differs.
      tab.stale = false;
    },
    discard(tab) {
      if (!tab) return;
      tab.draft = tab.content ?? '';
      tab.dirty = false;
      tab.editing = false;
    },
    requestClose(tab) {
      if (!tab) return;
      if (tab.dirty) {
        this.activePath = tab.path;
        this.pendingClose = tab.path;
        return;
      }
      this.closeTab(tab);
    },
    closeTab(tab) {
      const idx = this.tabs.indexOf(tab);
      if (idx < 0) return;
      this.revokeUrl(tab);
      this.tabs.splice(idx, 1);
      if (this.pendingClose === tab.path) this.pendingClose = null;
      if (this.activePath === tab.path) {
        const next = this.tabs[Math.min(idx, this.tabs.length - 1)];
        this.activePath = next ? next.path : '';
      }
    },
    discardAndClose(path) {
      const tab = this.tabs.find((x) => x.path === path);
      if (!tab) { this.pendingClose = null; return; }
      this.discard(tab);
      this.closeTab(tab);
    },
    async copyActive() {
      const tab = this.activeTab;
      if (!tab || tab.content == null) return;
      try {
        await navigator.clipboard.writeText(tab.editing ? tab.draft : tab.content);
        window.BoarAiStore.actions.success(this.t('common.copied') || 'Copied');
      } catch (e) {
        window.BoarAiStore.actions.error(String((e && e.message) || e));
      }
    },
    syncGutter() {
      const editor = this.$refs.editor;
      const gutter = this.$refs.editGutter;
      if (editor && gutter) gutter.scrollTop = editor.scrollTop;
    },
  },
  template: `
    <div class="fm-viewer flex flex-col min-w-0 bg-white dark:bg-gray-800">
      <!-- Tab strip + pane actions -->
      <div class="flex items-stretch h-9 flex-shrink-0 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40">
        <div class="fm-tabs flex-1 min-w-0 flex items-stretch overflow-x-auto">
          <div v-for="tab in tabs" :key="tab.path"
               class="fm-tab group flex items-center gap-1.5 px-2.5 h-full text-xs border-r border-gray-200 dark:border-gray-700 cursor-pointer select-none shrink-0"
               :class="tab.path === activePath
                 ? 'bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100'
                 : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700/40'"
               :title="tab.path"
               @click="activePath = tab.path; pendingClose = null">
            <span class="fm-badge shrink-0" :class="badge(tab.name).cls">{{ badge(tab.name).text }}</span>
            <span class="truncate max-w-[120px]">{{ tab.name }}</span>
            <span v-if="tab.dirty" class="w-1.5 h-1.5 rounded-full bg-amber-500 shrink-0"></span>
            <i v-if="tab.stale" data-lucide="alert-triangle"
               class="w-3 h-3 text-amber-500 shrink-0"
               :title="t('files.diskChanged') || 'File changed on disk'"></i>
            <button type="button" class="fm-tab-close shrink-0"
                    :title="t('files.closeTab') || 'Close tab'"
                    @click.stop="requestClose(tab)">
              <i data-lucide="x" class="w-3 h-3"></i>
            </button>
          </div>
          <div v-if="!tabs.length" class="px-3 flex items-center text-xs text-gray-400 dark:text-gray-500">
            {{ t('files.viewerEmpty') || 'No file open' }}
          </div>
        </div>
        <div class="flex items-center gap-0.5 px-1.5 flex-shrink-0 border-l border-gray-200 dark:border-gray-700">
          <button v-if="activeTab && activeTab.content != null && !activeTab.binary" type="button"
                  class="fm-icon-btn" :title="t('files.wrap') || 'Wrap'"
                  :class="{ 'text-primary': wrap }" @click="wrap = !wrap">
            <i data-lucide="wrap-text" class="w-3.5 h-3.5" :class="wrap ? 'text-primary' : 'text-gray-400 dark:text-gray-500'"></i>
          </button>
          <button v-if="activeTab && activeTab.content != null && !activeTab.binary" type="button"
                  class="fm-icon-btn" :title="t('files.copy') || 'Copy'" @click="copyActive">
            <i data-lucide="copy" class="w-3.5 h-3.5 text-gray-400 dark:text-gray-500"></i>
          </button>
          <button v-if="canEdit(activeTab)" type="button" class="fm-icon-btn"
                  :title="activeTab.editing ? (t('files.viewMode') || 'View') : (t('files.edit') || 'Edit')"
                  @click="toggleEdit">
            <i :data-lucide="activeTab.editing ? 'eye' : 'pencil'" class="w-3.5 h-3.5"
               :class="activeTab.editing ? 'text-primary' : 'text-gray-400 dark:text-gray-500'"></i>
          </button>
          <button v-if="activeTab && activeTab.editing && activeTab.dirty" type="button"
                  class="fm-icon-btn" :title="(t('files.save') || 'Save') + ' (Ctrl+S)'" @click="saveTab(activeTab)">
            <i data-lucide="save" class="w-3.5 h-3.5 text-primary"></i>
          </button>
          <button v-if="activeTab && activeTab.editing && activeTab.dirty" type="button"
                  class="fm-icon-btn" :title="t('files.discard') || 'Discard'" @click="discard(activeTab)">
            <i data-lucide="undo-2" class="w-3.5 h-3.5 text-gray-400 dark:text-gray-500"></i>
          </button>
          <button type="button" class="fm-icon-btn" :title="t('files.closePane') || 'Collapse panel'" @click="$emit('close')">
            <i data-lucide="panel-left-close" class="w-3.5 h-3.5 text-gray-400 dark:text-gray-500"></i>
          </button>
        </div>
      </div>

      <!-- Unsaved-changes banner (dirty tab close guard) -->
      <div v-if="pendingClose"
           class="flex items-center gap-2 px-3 py-1.5 text-xs bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 border-b border-amber-200 dark:border-amber-800 flex-shrink-0">
        <i data-lucide="alert-triangle" class="w-3.5 h-3.5 shrink-0"></i>
        <span class="flex-1 truncate">{{ t('files.unsaved') || 'Unsaved changes' }}</span>
        <button type="button" class="px-2 py-0.5 rounded bg-primary text-white hover:bg-primary-dark transition-colors"
                @click="saveTab(tabs.find(x => x.path === pendingClose))">
          {{ t('files.save') || 'Save' }}
        </button>
        <button type="button" class="px-2 py-0.5 rounded bg-gray-200 dark:bg-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-300 dark:hover:bg-gray-500 transition-colors"
                @click="discardAndClose(pendingClose)">
          {{ t('files.discard') || 'Discard' }}
        </button>
        <button type="button" class="px-2 py-0.5 rounded hover:bg-amber-100 dark:hover:bg-amber-900/50 transition-colors"
                @click="pendingClose = null">
          {{ t('files.keepOpen') || 'Keep open' }}
        </button>
      </div>

      <!-- Disk-changed banner (fs.changed mtime drift or a 409 save) -->
      <div v-if="activeTab && activeTab.stale"
           class="flex items-center gap-2 px-3 py-1.5 text-xs bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 border-b border-amber-200 dark:border-amber-800 flex-shrink-0">
        <i data-lucide="alert-triangle" class="w-3.5 h-3.5 shrink-0"></i>
        <span class="flex-1 truncate">{{ t('files.diskChanged') || 'File changed on disk' }}</span>
        <button type="button" class="px-2 py-0.5 rounded bg-primary text-white hover:bg-primary-dark transition-colors"
                @click="reloadTab(activeTab)">
          {{ t('files.reload') || 'Reload' }}
        </button>
        <button type="button" class="px-2 py-0.5 rounded bg-gray-200 dark:bg-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-300 dark:hover:bg-gray-500 transition-colors"
                @click="keepMine(activeTab)">
          {{ t('files.keepMine') || 'Keep mine' }}
        </button>
      </div>

      <!-- Body -->
      <div v-if="!activeTab" class="flex-1 min-h-0 flex flex-col items-center justify-center gap-2 text-gray-400 dark:text-gray-500">
        <i data-lucide="file-code-2" class="w-8 h-8 opacity-60"></i>
        <p class="text-xs">{{ t('files.viewerEmptyHint') || 'Click a file in the tree to open it' }}</p>
      </div>
      <div v-else-if="activeTab.loading" class="flex-1 min-h-0 flex items-center justify-center">
        <i data-lucide="loader-circle" class="w-5 h-5 animate-spin text-gray-300 dark:text-gray-600"></i>
      </div>
      <div v-else-if="activeTab.error" class="flex-1 min-h-0 flex flex-col items-center justify-center gap-2 px-4 text-center">
        <i data-lucide="file-x-2" class="w-6 h-6 text-red-400"></i>
        <p class="text-xs text-red-500 dark:text-red-400 break-words">{{ activeTab.error }}</p>
        <button type="button" class="text-xs text-primary hover:underline" @click="load(activeTab)">
          {{ t('files.retry') || 'Retry' }}
        </button>
      </div>
      <!-- Inline image preview (raster only; bytes come from /api/fs/raw) -->
      <div v-else-if="activeTab.image"
           class="flex-1 min-h-0 flex items-center justify-center p-4 overflow-auto fm-img-wrap">
        <img :src="rawSrc" class="max-w-full max-h-full object-contain rounded shadow-sm"
             :alt="activeTab.name" @error="activeTab.error = activeTab.error || 'Image load failed'">
      </div>
      <div v-else-if="activeTab.binary" class="flex-1 min-h-0 flex flex-col items-center justify-center gap-2 text-gray-400 dark:text-gray-500">
        <i data-lucide="file-cog" class="w-8 h-8 opacity-60"></i>
        <p class="text-xs">{{ t('files.binary') || 'Binary file' }}</p>
        <p class="text-[11px] opacity-80">{{ t('files.binaryHint') || 'No text preview available' }}</p>
      </div>
      <template v-else>
        <div v-if="activeTab.truncated"
             class="px-3 py-1.5 text-xs bg-gray-100 dark:bg-gray-700/50 text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-700 flex-shrink-0">
          {{ t('files.tooLarge') || 'File too large — showing the first part only (read-only)' }}
        </div>
        <!-- View mode: single scroll container, sticky gutter -->
        <div v-if="!activeTab.editing" class="flex-1 min-h-0 overflow-auto fm-scroll">
          <div class="flex items-stretch w-max min-w-full">
            <div v-if="!gutterHidden" class="fm-gutter" aria-hidden="true">
              <div v-for="n in lineCount" :key="n" class="fm-ln">{{ n }}</div>
            </div>
            <pre class="fm-code flex-1" :class="{ 'fm-wrap': wrap }" v-html="hlHtml"></pre>
          </div>
        </div>
        <!-- Edit mode: textarea + scroll-synced gutter -->
        <div v-else class="flex-1 min-h-0 flex items-stretch overflow-hidden">
          <div v-if="!gutterHidden" ref="editGutter" class="fm-gutter fm-gutter-edit" aria-hidden="true">
            <div v-for="n in lineCount" :key="n" class="fm-ln">{{ n }}</div>
          </div>
          <textarea ref="editor" v-model="activeTab.draft" :key="wrap ? 'w' : 'n'"
                    class="fm-editor flex-1 min-w-0" :class="{ 'fm-wrap': wrap }"
                    :wrap="wrap ? 'soft' : 'off'" spellcheck="false"
                    @input="onDraftInput" @scroll="syncGutter"
                    @keydown.ctrl.s.prevent="saveTab(activeTab)"
                    @keydown.meta.s.prevent="saveTab(activeTab)"></textarea>
        </div>
      </template>

      <!-- Status bar -->
      <div class="fm-status flex items-center gap-3 px-3 h-6 flex-shrink-0 border-t border-gray-200 dark:border-gray-700 text-[11px] text-gray-400 dark:text-gray-500">
        <span v-if="activeTab && lineCount">{{ lineCount }} {{ t('files.lines') || 'lines' }}</span>
        <span v-if="activeTab && activeTab.size != null">{{ fmtSize(activeTab.size) }}</span>
        <span v-if="activeTab">{{ activeTab.image ? 'image' : (activeTab.lang || 'text') }}</span>
        <span v-if="activeTab && activeTab.dirty" class="text-amber-500 dark:text-amber-400">{{ t('files.unsaved') || 'Unsaved changes' }}</span>
        <span class="flex-1"></span>
        <span v-if="activeTab" class="truncate max-w-[45%]" :title="activeTab.path">{{ activeTab.path }}</span>
      </div>
    </div>
  `,
};
