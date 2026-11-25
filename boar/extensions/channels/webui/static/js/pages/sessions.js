/**
 * Sessions Page Component
 * 
 * Chat sessions management view with sidebar session list.
 * Uses TailwindCSS for styling.
 * Supports internationalization (i18n).
 * Uses Lucide Icons for all icons.
 */

function sanitizeMarkdownHtml(html) {
  const documentFragment = new DOMParser().parseFromString(String(html), 'text/html');
  const allowedTags = new Set([
    'A', 'B', 'BLOCKQUOTE', 'BR', 'CODE', 'DEL', 'EM', 'H1', 'H2', 'H3',
    'H4', 'H5', 'H6', 'HR', 'I', 'IMG', 'KBD', 'LI', 'OL', 'P', 'PRE', 'S',
    'STRONG', 'TABLE', 'TBODY', 'TD', 'TH', 'THEAD', 'TR', 'UL'
  ]);
  const contentTagsToRemove = new Set([
    'IFRAME', 'OBJECT', 'EMBED', 'SCRIPT', 'STYLE', 'SVG', 'MATH',
    'TEMPLATE', 'FORM', 'BUTTON', 'VIDEO', 'AUDIO'
  ]);
  const elements = Array.from(documentFragment.body.querySelectorAll('*'));

  for (const element of elements) {
    if (!element.isConnected) continue;
    // GFM task-list markers are the only <input> markdown may carry: keep them
    // as render-only checkboxes (forced disabled, no handlers, no extra attrs).
    if (element.tagName === 'INPUT') {
      if ((element.getAttribute('type') || '').toLowerCase() !== 'checkbox') {
        element.remove();
        continue;
      }
      for (const attribute of Array.from(element.attributes)) {
        if (!['type', 'checked', 'disabled'].includes(attribute.name.toLowerCase())) {
          element.removeAttribute(attribute.name);
        }
      }
      element.setAttribute('disabled', '');
      continue;
    }
    if (contentTagsToRemove.has(element.tagName)) {
      element.remove();
      continue;
    }
    if (!allowedTags.has(element.tagName)) {
      element.replaceWith(...Array.from(element.childNodes));
      continue;
    }

    for (const attribute of Array.from(element.attributes)) {
      const name = attribute.name.toLowerCase();
      let keep = name === 'title';
      if (element.tagName === 'A' && name === 'href') keep = true;
      if (element.tagName === 'IMG' && ['src', 'alt', 'width', 'height'].includes(name)) keep = true;
      if ((element.tagName === 'TD' || element.tagName === 'TH') &&
          ['colspan', 'rowspan', 'align'].includes(name)) keep = true;
      if (['CODE', 'PRE', 'UL', 'LI'].includes(element.tagName) && name === 'class') {
        keep = attribute.value.split(/\s+/).every((value) =>
          value === 'contains-task-list' || value === 'task-list-item' ||
          /^language-[a-zA-Z0-9_+-]+$/.test(value)
        );
      }
      if (keep && ['href', 'src'].includes(name)) {
        try {
          const parsed = new URL(attribute.value, window.location.href);
          const protocols = element.tagName === 'A'
            ? ['http:', 'https:', 'mailto:', 'tel:']
            : ['http:', 'https:'];
          keep = protocols.includes(parsed.protocol);
        } catch (_) {
          keep = false;
        }
      }
      if (!keep || name.startsWith('on')) element.removeAttribute(attribute.name);
    }
  }
  return documentFragment.body.innerHTML;
}

// ── Markdown code blocks: IDE-style chrome + lightweight highlighting ─────
// marked emits bare <pre><code class="language-x">; the sanitizer runs FIRST,
// so the wrapper/header/token spans injected here bypass the allowlist by
// design (they are renderer-generated, never raw markdown HTML). The regex
// highlighter is deliberately dependency-free: an ordered sticky-regex rule
// list per language, first match wins at each position.
const MD_LANG_ALIAS = {
  py: 'python', python3: 'python',
  js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript', node: 'javascript',
  ts: 'typescript', tsx: 'typescript',
  sh: 'bash', shell: 'bash', zsh: 'bash', console: 'bash', shellsession: 'bash', dockerfile: 'bash',
  yml: 'yaml', htm: 'html', vue: 'html', xml: 'html', svg: 'html',
  rs: 'rust', golang: 'go', 'c++': 'cpp', cc: 'cpp', hh: 'cpp', hpp: 'cpp', h: 'c',
  cs: 'csharp',
};

const MD_KEYWORDS = {
  python: ['and', 'as', 'assert', 'async', 'await', 'break', 'case', 'class', 'continue', 'def', 'del', 'elif', 'else', 'except', 'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is', 'lambda', 'match', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while', 'with', 'yield'],
  js: ['abstract', 'as', 'async', 'await', 'break', 'case', 'catch', 'class', 'const', 'continue', 'debugger', 'declare', 'default', 'delete', 'do', 'else', 'enum', 'export', 'extends', 'finally', 'for', 'from', 'function', 'get', 'if', 'implements', 'import', 'in', 'infer', 'instanceof', 'interface', 'keyof', 'let', 'namespace', 'new', 'of', 'readonly', 'return', 'satisfies', 'set', 'static', 'super', 'switch', 'throw', 'try', 'type', 'typeof', 'var', 'void', 'while', 'with', 'yield'],
  bash: ['if', 'then', 'else', 'elif', 'fi', 'for', 'while', 'until', 'do', 'done', 'case', 'esac', 'function', 'in', 'time', 'return', 'break', 'continue', 'exit', 'export', 'local', 'readonly', 'declare', 'source', 'shift', 'eval', 'exec', 'trap'],
  bashCmd: ['cd', 'cat', 'chmod', 'chown', 'cp', 'curl', 'docker', 'echo', 'git', 'grep', 'head', 'kubectl', 'ls', 'mkdir', 'mv', 'node', 'npm', 'pip', 'python', 'python3', 'py', 'rm', 'sed', 'awk', 'tail', 'touch', 'wget'],
  c: ['auto', 'break', 'case', 'char', 'const', 'continue', 'default', 'do', 'double', 'else', 'enum', 'extern', 'float', 'for', 'goto', 'if', 'inline', 'int', 'long', 'register', 'restrict', 'return', 'short', 'signed', 'sizeof', 'static', 'struct', 'switch', 'typedef', 'union', 'unsigned', 'void', 'volatile', 'while'],
  cpp: ['auto', 'break', 'case', 'catch', 'char', 'class', 'const', 'constexpr', 'continue', 'default', 'delete', 'do', 'double', 'else', 'enum', 'explicit', 'extern', 'final', 'float', 'for', 'friend', 'goto', 'if', 'inline', 'int', 'long', 'namespace', 'new', 'noexcept', 'operator', 'override', 'private', 'protected', 'public', 'return', 'short', 'signed', 'sizeof', 'static', 'struct', 'switch', 'template', 'this', 'throw', 'try', 'typedef', 'typename', 'union', 'unsigned', 'using', 'virtual', 'void', 'volatile', 'while'],
  java: ['abstract', 'assert', 'boolean', 'break', 'byte', 'case', 'catch', 'char', 'class', 'const', 'continue', 'default', 'do', 'double', 'else', 'enum', 'extends', 'final', 'finally', 'float', 'for', 'goto', 'if', 'implements', 'import', 'instanceof', 'int', 'interface', 'long', 'native', 'new', 'package', 'private', 'protected', 'public', 'record', 'return', 'sealed', 'short', 'static', 'super', 'switch', 'synchronized', 'this', 'throw', 'throws', 'transient', 'try', 'var', 'void', 'volatile', 'while', 'yield'],
  csharp: ['as', 'async', 'await', 'base', 'break', 'case', 'catch', 'checked', 'class', 'const', 'continue', 'default', 'delegate', 'do', 'else', 'enum', 'event', 'explicit', 'extern', 'finally', 'fixed', 'for', 'foreach', 'goto', 'if', 'implicit', 'in', 'init', 'interface', 'is', 'lock', 'namespace', 'new', 'operator', 'out', 'override', 'params', 'private', 'protected', 'public', 'readonly', 'record', 'ref', 'return', 'sealed', 'sizeof', 'stackalloc', 'static', 'struct', 'switch', 'this', 'throw', 'try', 'typeof', 'unchecked', 'unsafe', 'using', 'var', 'virtual', 'volatile', 'while'],
  go: ['break', 'case', 'chan', 'const', 'continue', 'default', 'defer', 'else', 'fallthrough', 'for', 'func', 'go', 'goto', 'if', 'import', 'interface', 'map', 'package', 'range', 'return', 'select', 'struct', 'switch', 'type', 'var'],
  rust: ['as', 'async', 'await', 'break', 'const', 'continue', 'crate', 'dyn', 'else', 'enum', 'extern', 'fn', 'for', 'if', 'impl', 'in', 'let', 'loop', 'match', 'mod', 'move', 'mut', 'pub', 'ref', 'return', 'self', 'static', 'struct', 'super', 'trait', 'type', 'unsafe', 'use', 'where', 'while'],
  sql: ['select', 'from', 'where', 'insert', 'into', 'values', 'update', 'set', 'delete', 'create', 'table', 'drop', 'alter', 'add', 'index', 'view', 'join', 'inner', 'left', 'right', 'outer', 'full', 'on', 'group', 'by', 'order', 'having', 'limit', 'offset', 'union', 'all', 'as', 'distinct', 'between', 'like', 'in', 'is', 'not', 'null', 'and', 'or', 'asc', 'desc', 'primary', 'key', 'foreign', 'references', 'constraint', 'default', 'unique', 'check', 'case', 'when', 'then', 'else', 'end', 'exists', 'with', 'begin', 'commit', 'rollback', 'transaction', 'truncate', 'cascade'],
  generic: ['function', 'return', 'if', 'else', 'elif', 'for', 'while', 'import', 'from', 'export', 'const', 'let', 'var', 'class', 'def', 'end', 'begin', 'do', 'done', 'then', 'fi', 'case', 'in', 'not', 'and', 'or', 'true', 'false', 'null', 'None', 'True', 'False', 'try', 'catch', 'except', 'finally', 'throw', 'raise', 'with', 'as', 'pass', 'break', 'continue'],
};

const MD_RULE_CACHE = Object.create(null);

function mdRulesFor(lang) {
  const key = lang || '';
  if (MD_RULE_CACHE[key]) return MD_RULE_CACHE[key];
  const rules = [];
  const push = (src, cls, flags) => rules.push([new RegExp(src, (flags || '') + 'y'), cls]);
  const words = (list, cls, flags) => push('\\b(?:' + list.join('|') + ')\\b', cls, flags);
  const strings = () => {
    push('"""[\\s\\S]*?(?:"""|$)', 'str');
    push("'''[\\s\\S]*?(?:'''|$)", 'str');
    push('"(?:\\\\.|[^"\\\\\\n])*"?', 'str');
    push("'(?:\\\\.|[^'\\\\\\n])*'?", 'str');
    push('`(?:\\\\.|[^`\\\\])*`?', 'str');
  };
  const num = () => push('\\b0[xXbBoO][0-9a-fA-F_]+\\b|\\b\\d[\\d_]*(?:\\.\\d+)?(?:[eE][+-]?\\d+)?(?:px|em|rem|%|vh|vw|s|ms|deg|fr)?\\b', 'num');
  const fn = () => push('[A-Za-z_$][\\w$]*(?=\\s*\\()', 'fn');
  const type = () => push('\\b[A-Z][A-Za-z0-9_]*\\b', 'type');
  const cComments = () => {
    push('//[^\\n]*', 'comment');
    push('/\\*[\\s\\S]*?(?:\\*/|$)', 'comment');
  };

  switch (key) {
    case 'python':
      push('#[^\\n]*', 'comment');
      strings();
      push('@[A-Za-z_][\\w.]*', 'dec');
      num();
      words(MD_KEYWORDS.python, 'kw');
      words(['True', 'False', 'None', 'self', 'cls'], 'lit');
      fn(); type();
      break;
    case 'javascript':
    case 'typescript':
      cComments();
      strings();
      num();
      words(MD_KEYWORDS.js, 'kw');
      words(['true', 'false', 'null', 'undefined', 'NaN', 'Infinity', 'this', 'super'], 'lit');
      fn(); type();
      break;
    case 'json':
      push('"(?:\\\\.|[^"\\\\])*"(?=\\s*:)', 'key');
      strings();
      num();
      words(['true', 'false', 'null'], 'lit');
      break;
    case 'bash':
      push('#[^\\n]*', 'comment');
      strings();
      push('\\$\\{?[0-9@*#?$!A-Za-z_][\\w]*\\}?', 'var');
      push('(?:^|\\s)-{1,2}[A-Za-z][A-Za-z0-9-]*', 'attr');
      num();
      words(MD_KEYWORDS.bash, 'kw');
      words(MD_KEYWORDS.bashCmd, 'fn');
      break;
    case 'html':
      push('<!--[\\s\\S]*?(?:-->|$)', 'comment');
      push('<![A-Za-z][^>]*>', 'comment');
      strings();
      push('</?[A-Za-z][A-Za-z0-9:-]*|/?>', 'tag');
      push('[A-Za-z_][A-Za-z0-9:_.-]*(?=\\s*=)', 'attr');
      break;
    case 'css':
      push('/\\*[\\s\\S]*?(?:\\*/|$)', 'comment');
      strings();
      push('@[A-Za-z-]+', 'kw');
      push('!important\\b', 'kw');
      push('#[0-9a-fA-F]{3,8}\\b', 'num');
      push('[a-z-]+(?=\\s*:[^;{]*[;}])', 'attr');
      num();
      break;
    case 'yaml':
      push('#[^\\n]*', 'comment');
      strings();
      push('[A-Za-z0-9_.$/-]+(?=\\s*:(?:\\s|$))', 'attr');
      num();
      words(['true', 'false', 'null', 'yes', 'no', 'on', 'off'], 'lit');
      break;
    case 'sql':
      push('--[^\\n]*', 'comment');
      push('/\\*[\\s\\S]*?(?:\\*/|$)', 'comment');
      push("'(?:''|[^'\\n])*'?", 'str');
      num();
      words(MD_KEYWORDS.sql, 'kw', 'i');
      break;
    case 'c':
    case 'cpp':
    case 'java':
    case 'csharp':
    case 'go':
    case 'rust':
      cComments();
      if (key === 'c' || key === 'cpp') push('^[ \\t]*#[a-z_]+', 'dec', 'm');
      strings();
      if (key === 'rust') push('\\b[a-z_]+!(?=\\s*[({\\[])', 'fn');
      num();
      words(MD_KEYWORDS[key], 'kw');
      if (key === 'go') words(['true', 'false', 'nil', 'iota'], 'lit');
      else if (key === 'rust') words(['true', 'false', 'Some', 'None', 'Ok', 'Err', 'Self'], 'lit');
      else words(['true', 'false', 'null', 'NULL'], 'lit');
      fn(); type();
      break;
    default:
      cComments();
      push('#[^\\n]*', 'comment');
      push('<!--[\\s\\S]*?(?:-->|$)', 'comment');
      strings();
      num();
      words(MD_KEYWORDS.generic, 'kw');
      fn();
      break;
  }
  MD_RULE_CACHE[key] = rules;
  return rules;
}

function mdEscapeHtml(text) {
  return String(text).replace(/[&<>]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[char]));
}

function mdDecodeEntities(html) {
  return String(html)
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, '&');
}

function mdHighlight(code, lang) {
  const text = String(code ?? '');
  if (!text) return '';
  // Very large blocks stay plain: the per-position rule loop is O(n·rules).
  if (text.length > 50000) return mdEscapeHtml(text);
  const rules = mdRulesFor(MD_LANG_ALIAS[lang] || lang || '');
  let out = '';
  let pos = 0;
  while (pos < text.length) {
    let hitText = null;
    let hitClass = null;
    for (let i = 0; i < rules.length; i++) {
      const re = rules[i][0];
      re.lastIndex = pos;
      const match = re.exec(text);
      if (match && match[0]) {
        hitText = match[0];
        hitClass = rules[i][1];
        break;
      }
    }
    if (hitText) {
      out += hitClass
        ? '<span class="tok-' + hitClass + '">' + mdEscapeHtml(hitText) + '</span>'
        : mdEscapeHtml(hitText);
      pos += hitText.length;
    } else {
      out += mdEscapeHtml(text[pos]);
      pos += 1;
    }
  }
  return out;
}

// Split one pipe-table row into cells, honouring escaped pipes and optional
// leading/trailing pipe.
function mdSplitTableRow(line) {
  let src = String(line).trim();
  if (src.startsWith('|')) src = src.slice(1);
  if (src.endsWith('|') && !src.endsWith('\\|')) src = src.slice(0, -1);
  const cells = [];
  let current = '';
  for (let i = 0; i < src.length; i++) {
    const ch = src[i];
    if (ch === '\\' && src[i + 1] === '|') {
      current += '\\|';
      i++;
      continue;
    }
    if (ch === '|') {
      cells.push(current);
      current = '';
      continue;
    }
    current += ch;
  }
  cells.push(current);
  return cells;
}

function mdIsTableDelimLine(line) {
  const trimmed = String(line).trim();
  if (!trimmed.includes('-') || !trimmed.includes('|')) return false;
  const cells = mdSplitTableRow(trimmed);
  return cells.length > 0 && cells.every((cell) => /^:?-{1,}:?$/.test(cell.trim()));
}

// GFM only recognises a table when header and delimiter rows carry the same
// cell count; LLM-authored markdown often ships a short/long delimiter row and
// the whole table then degrades into literal pipe text. Pad/truncate the
// delimiter row to the header count before parsing. Fenced code is untouched.
function mdNormalizeTableDelimiters(text) {
  const lines = String(text).split('\n');
  let inFence = false;
  for (let i = 0; i < lines.length; i++) {
    if (/^\s*(```+|~~~+)/.test(lines[i])) {
      inFence = !inFence;
      continue;
    }
    if (inFence || !mdIsTableDelimLine(lines[i])) continue;
    const header = lines[i - 1];
    const body = lines[i + 1];
    if (!header || !header.includes('|') || mdIsTableDelimLine(header)) continue;
    if (!body || !body.includes('|')) continue;
    const headerCount = mdSplitTableRow(header).length;
    const cells = mdSplitTableRow(lines[i]).map((cell) => cell.trim() || '---');
    if (cells.length === headerCount) continue;
    while (cells.length < headerCount) cells.push('---');
    cells.length = headerCount;
    const piped = String(lines[i]).trimStart().startsWith('|');
    lines[i] = (piped ? '| ' : '') + cells.join(' | ') + (piped ? ' |' : '');
  }
  return lines.join('\n');
}

const MD_COPY_SVG = '<svg class="md-ic md-ic-copy" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>';
const MD_CHECK_SVG = '<svg class="md-ic md-ic-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';

function mdCopyLabel() {
  const label = typeof t === 'function' ? t('common.copy') : null;
  return mdEscapeHtml(label || 'Copy');
}

// Wrap every <pre><code> in an IDE-style frame: language tab + copy button.
// Runs AFTER sanitizeMarkdownHtml so the injected chrome needs no allowlist.
function mdEnhanceCodeBlocks(html) {
  return String(html).replace(
    /<pre><code(?:\s+class="([^"]*)")?>([\s\S]*?)<\/code><\/pre>/g,
    (match, cls, inner) => {
      const langMatch = /language-([\w+-]+)/.exec(cls || '');
      const lang = langMatch ? langMatch[1].toLowerCase() : '';
      const label = lang || 'text';
      const copyLabel = mdCopyLabel();
      return '<div class="md-codeblock">'
        + '<div class="md-codeblock-head">'
        + '<span class="md-codeblock-lang">' + mdEscapeHtml(label) + '</span>'
        + '<button type="button" class="md-codeblock-copy" title="' + copyLabel + '">'
        + MD_COPY_SVG + MD_CHECK_SVG + '<span>' + copyLabel + '</span></button>'
        + '</div>'
        + '<pre><code>' + mdHighlight(mdDecodeEntities(inner), lang) + '</code></pre>'
        + '</div>';
    }
  );
}

function mdCopyPlainText(text) {
  const legacyCopy = () => {
    let ok = false;
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '0';
    ta.style.left = '0';
    ta.style.width = '2em';
    ta.style.height = '2em';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try {
      ta.setSelectionRange(0, ta.value.length);
      ok = document.execCommand('copy');
    } catch (e) {
      ok = false;
    }
    document.body.removeChild(ta);
    return ok;
  };
  if (legacyCopy()) return true;
  try {
    if (navigator.clipboard) navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    return false;
  }
}

// One delegated listener covers every code block, including ones streamed in
// later: v-html content cannot bind Vue handlers, so clicks bubble here.
if (typeof document !== 'undefined' && !document.__mdCodeCopyBound) {
  document.__mdCodeCopyBound = true;
  document.addEventListener('click', (event) => {
    const target = event.target;
    const button = target && target.closest ? target.closest('.md-codeblock-copy') : null;
    if (!button) return;
    const block = button.closest('.md-codeblock');
    const code = block ? block.querySelector('pre code') : null;
    if (!code) return;
    event.preventDefault();
    if (!mdCopyPlainText(code.innerText || code.textContent || '')) return;
    const label = button.querySelector('span');
    if (!label) return;
    if (!button.dataset.mdLabel) button.dataset.mdLabel = label.textContent;
    const done = (typeof t === 'function' ? t('common.copied') : '') || 'Copied';
    button.classList.add('is-copied');
    label.textContent = done;
    clearTimeout(Number(button.dataset.mdTimer || 0));
    button.dataset.mdTimer = String(setTimeout(() => {
      button.classList.remove('is-copied');
      label.textContent = button.dataset.mdLabel || '';
    }, 1600));
  });
}

// ── Transcript display modes ──────────────────────────────────────────────
// Three-tier view filter over the PROCESS information (thinking + tool calls)
// of completed rounds. Invariants shared by every tier: result information
// (reply text, error stamps) is never hidden; live rounds and rounds carrying
// a run-level error stay flat; folded segments remain reachable through their
// fold row and the per-round override. Renderers read ONLY the boolean policy
// fields below — never the mode name — so adding a tier is one table row
// (pattern borrowed from deepseek-harness presentation-policy.ts).
// minimal hides thinking implicitly: showFoldRow=false removes the fold row
// and its segments never render process blocks. If a future tier wants the
// openclacky N>=2 rule (lone tool calls keep their plain card row), reintroduce
// a foldSingleToolRun field and restore the segWorthFolding filter.
// quietLive: whether the LIVE trailing round renders flat. detailed keeps it
// flat (full monitoring); concise and minimal also gate the live round — the
// streaming text shows, the process folds into its summary row (user request:
// 简洁模式在运行中也要收缩), the run-activity row carries the "working"
// signal, and the approval bar is a separate overlay. The round-header eye
// button still forces a quiet live round flat on demand.
const DISPLAY_POLICIES = {
  detailed: { foldProcessRuns: false, thinkingPreview: true, showFoldRow: true, quietLive: false },
  concise: { foldProcessRuns: true, thinkingPreview: true, showFoldRow: true, quietLive: true },
};
const DEFAULT_DISPLAY_MODE = 'concise';
const DISPLAY_MODE_STORAGE_KEY = 'boarai_display_mode';
const DISPLAY_MODE_LABEL_KEYS = {
  detailed: 'sessions.displayDetailed',
  concise: 'sessions.displayConcise',
};
// Dead boolean toggle this feature replaces; migrated once, then removed.
const LEGACY_REASONING_STORAGE_KEY = 'boarai_show_reasoning';

// Neutral entry for messages outside any folded segment (or while the whole
// plan is flat): everything renders exactly as before the feature existed.
const FLAT_PLAN_ENTRY = Object.freeze({
  flat: true, showProcess: true, foldRow: false, segOpen: false,
  seg: null, segId: null, round: null,
});

/**
 * Pure O(n) planner for one round [startIdx, endIdx) — port of omp's
 * chat-transcript-plan.ts: assistant reply text is NEVER folded; each
 * consecutive run of thinking / tool calls between two visible texts folds
 * into one activity segment. Tool messages paired onto an assistant
 * tool_calls card render inside that card and are skipped here; standalone
 * tool messages join the segment as their own row.
 * helpers: { isPairedTool, toolFailed, attachedFailed, hasVisibleText,
 *            thinkingPreview, markTs } — injected so this stays testable.
 * @returns {{anchorIdx:number, idxs:number[], toolCount:number, thinkCount:number,
 *            failCount:number, seconds:number|null, preview:string}[]}
 */
function planRoundSegments(messages, startIdx, endIdx, helpers) {
  const segments = [];
  let seg = null;
  const ensure = (i) => {
    if (!seg) {
      seg = {
        anchorIdx: i, idxs: [], toolCount: 0, thinkCount: 0, failCount: 0,
        firstTs: null, lastTs: null, preview: '', firstToolName: '',
      };
      segments.push(seg);
    }
    seg.idxs.push(i);
  };
  for (let i = startIdx; i < endIdx; i++) {
    const m = messages[i];
    if (!m || m.role === 'user') continue;
    if (m.role === 'tool') {
      if (helpers.isPairedTool(m)) continue;
      ensure(i);
      seg.toolCount += 1;
      if (!seg.firstToolName) seg.firstToolName = helpers.toolName(m);
      if (helpers.toolFailed(m)) seg.failCount += 1;
      helpers.markTs(seg, m);
      continue;
    }
    // assistant
    const tcs = Array.isArray(m.tool_calls) ? m.tool_calls : [];
    if (m.thinking || tcs.length) {
      ensure(i);
      if (m.thinking) {
        seg.thinkCount += 1;
        if (!seg.preview) seg.preview = helpers.thinkingPreview(m.thinking);
      }
      seg.toolCount += tcs.length;
      for (const tc of tcs) {
        if (!seg.firstToolName) seg.firstToolName = helpers.attachedName(tc);
        if (helpers.attachedFailed(tc)) seg.failCount += 1;
      }
      helpers.markTs(seg, m);
    }
    // Visible reply text closes the activity run (omp: text is never hidden).
    if (helpers.hasVisibleText(m)) seg = null;
  }
  for (const s of segments) {
    s.seconds = (s.firstTs !== null && s.lastTs !== null && s.lastTs > s.firstTs)
      ? (s.lastTs - s.firstTs) / 1000 : null;
    delete s.firstTs;
    delete s.lastTs;
  }
  return segments;
}

/**
 * ToolCallCard Component
 * Collapsible card for tool execution display with status-based styling.
 */
const ToolCallCard = {
  name: 'ToolCallCard',
  props: {
    toolName: { type: String, default: '' },
    toolArgs: { type: [String, Object], default: '' },
    result: { type: String, default: '' },
    status: { type: String, default: 'success' }, // 'running', 'success', 'error', 'rejected', 'permission', 'policy', 'timeout', 'execution'
    errorReason: { type: String, default: '' },
    // Live stdout/stderr tail while the tool is running (streamed via
    // tool.output WS frames); replaced by the final result on finish.
    liveOutput: { type: String, default: '' },
  },
  data() {
    // _userCollapsed lets auto-expanded failure/rejected/running cards be
    // collapsed by the user (first click) without fighting the auto-expand rule.
    // Failure reasons live in the expanded body only, so a collapsed error card
    // is exactly one header row tall — same as a successful one.
    return { isOpen: false, _userCollapsed: false };
  },
  updated() {
    // Pin the live-output view to the tail while the tool runs.
    const pre = this.$refs.liveOut;
    if (pre && this.resolvedStatus === 'running') pre.scrollTop = pre.scrollHeight;
  },
  computed: {
    resolvedStatus() {
      if (this.status === 'running') return 'running';
      if (this.status === 'rejected') return 'rejected';
      if (this.status === 'permission' || this.status === 'policy'
          || this.status === 'timeout' || this.status === 'execution') return 'error';
      if (this.status === 'error') return 'error';
      if (this.result && typeof this.result === 'string') {
        if (this.result.includes('rejected by user')) return 'rejected';
        if (this.result.includes('Error:') || this.result.includes('rror')) return 'error';
      }
      return 'success';
    },
    // Running cards stay COLLAPSED: the header already shows a one-line
    // args/live preview, and auto-expanding the full params block made the
    // message list height jump on every tool batch arrival/completion
    // (recorded as full-viewport reflow flashes). Users click to expand.
    autoExpanded() {
      return false;
    },
    bodyVisible() {
      if (this.isOpen) return true;
      return this.autoExpanded && !this._userCollapsed;
    },
    // Granular failure kind so the UI can explain WHY the tool failed.
    errorKind() {
      const s = this.status;
      if (['permission', 'policy', 'timeout', 'execution', 'rejected'].includes(s)) return s;
      if (this.resolvedStatus === 'rejected') return 'rejected';
      if (this.result && typeof this.result === 'string' && this.result.includes('rejected by user')) return 'rejected';
      return 'error';
    },
    reasonText() {
      if (this.errorReason && String(this.errorReason).trim()) return String(this.errorReason).trim();
      const fallback = {
        permission: this.t('sessions.toolPermissionDenied') || 'Permission denied',
        policy: this.t('sessions.toolBlockedByPolicy') || 'Blocked by security policy',
        timeout: this.t('sessions.toolTimedOut') || 'Approval timed out',
        rejected: this.t('sessions.toolRejectedByUser') || 'Rejected by user',
        execution: this.t('sessions.toolExecutionFailed') || 'Tool execution failed',
        error: this.t('sessions.toolExecutionFailed') || 'Tool execution failed',
      }[this.errorKind] || this.t('sessions.toolExecutionFailed') || 'Tool execution failed';
      if (this.errorKind === 'error' && this.result) {
        let r = String(this.result).trim();
        r = r.replace(/^\[Error\]\s*/i, '')
          .replace(/^Tool error:\s*/i, '')
          .replace(/^Error:\s*/i, '')
          .replace(/^\[Tool execution\s*/i, '')
          .replace(/\]\s*$/i, '')
          .trim();
        if (r) return r;
      }
      return fallback;
    },
    shortArgs() {
      if (!this.toolArgs) return '';
      let str = typeof this.toolArgs === 'string' ? this.toolArgs : JSON.stringify(this.toolArgs);
      try {
        const parsed = JSON.parse(str);
        // Prefer human-visible command/path fields while running
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          const preferred = ['command', 'cmd', 'path', 'query', 'pattern', 'file', 'filepath', 'glob'];
          for (const k of preferred) {
            if (parsed[k] != null && String(parsed[k]).trim()) {
              str = String(parsed[k]);
              break;
            }
          }
          if (str === (typeof this.toolArgs === 'string' ? this.toolArgs : JSON.stringify(this.toolArgs))) {
            const vals = Object.values(parsed);
            if (vals.length === 1) str = String(vals[0]);
          }
        }
      } catch (e) { /* use raw string */ }
      // Running: show more of the command; done: keep compact
      const maxLen = this.resolvedStatus === 'running' ? 120 : 50;
      return str.length > maxLen ? str.substring(0, maxLen) + '...' : str;
    },
  },
  methods: {
    // t() provided by global I18nMixin
    toggleOpen() {
      // Auto-expanded (failure/rejected/running) cards: first click collapses.
      if (!this.isOpen && this.autoExpanded && !this._userCollapsed) {
        this._userCollapsed = true;
        return;
      }
      this.isOpen = !this.isOpen;
    },
    formatArgs() {
      if (!this.toolArgs) return '';
      let str = typeof this.toolArgs === 'string' ? this.toolArgs : JSON.stringify(this.toolArgs, null, 2);
      try { return JSON.stringify(JSON.parse(str), null, 2); } catch (e) { return str; }
    },
    // Empty payloads ({}, [], null) must not render a full-width dark slab.
    isEmptyArgs() {
      let s = '';
      try { s = typeof this.toolArgs === 'string' ? this.toolArgs : JSON.stringify(this.toolArgs); } catch (e) { s = ''; }
      s = String(s || '').trim();
      return !s || s === '{}' || s === 'null' || s === '[]';
    },
  },
  template: `
    <div class="my-0.5 rounded-r-md border-l-2 text-xs font-mono transition-all"
         :class="{
           'border-neutral-400 bg-neutral-50/60 dark:bg-neutral-800/40': resolvedStatus === 'rejected',
           'border-amber-500 bg-amber-50/50 dark:bg-amber-950/20': resolvedStatus === 'error',
           'border-blue-500 bg-blue-50/30 dark:bg-blue-950/20': resolvedStatus === 'running',
           'border-emerald-500 bg-gray-50 dark:bg-gray-900/60': resolvedStatus === 'success',
         }">
      <!-- Header -->
      <div @click="toggleOpen"
           class="flex items-center justify-between px-3 py-1.5 cursor-pointer select-none rounded-r-md hover:bg-black/[0.03] dark:hover:bg-white/[0.03]">
        <div class="flex items-center gap-2 min-w-0 flex-1">
          <i v-if="resolvedStatus === 'rejected'" data-lucide="ban" class="w-3.5 h-3.5 text-neutral-500 shrink-0"></i>
          <i v-else-if="resolvedStatus === 'error'" data-lucide="alert-triangle" class="w-3.5 h-3.5 text-amber-500 shrink-0"></i>
          <i v-else-if="resolvedStatus === 'running'" data-lucide="loader" class="w-3.5 h-3.5 text-blue-500 animate-spin shrink-0"></i>
          <i v-else data-lucide="check-circle" class="w-3.5 h-3.5 text-emerald-500 shrink-0"></i>
          <span class="font-semibold text-gray-700 dark:text-gray-300 shrink-0">{{ toolName }}</span>
          <!-- Show command/args while running so user sees what is executing -->
          <span v-if="toolArgs"
                class="text-gray-500 dark:text-gray-500 truncate min-w-0"
                :class="resolvedStatus === 'running' ? 'max-w-[min(420px,55vw)]' : 'max-w-[200px]'"
                :title="shortArgs">{{ shortArgs }}</span>
        </div>
        <div class="flex items-center gap-2 shrink-0 ml-2">
          <i :data-lucide="bodyVisible ? 'chevron-down' : 'chevron-right'" class="w-3 h-3 text-gray-500"></i>
        </div>
      </div>
      <!-- Body: collapsed by default (running included); click header to expand
           params / reason / live output / result. The header icon alone carries
           the failure status while collapsed, keeping every card one row tall. -->
      <div v-if="bodyVisible"
           class="border-t border-gray-200/50 dark:border-gray-700/50 text-gray-600 dark:text-gray-500">
        <!-- Failure reason (permission denied / policy / timeout / execution) -->
        <div v-if="resolvedStatus === 'error' || resolvedStatus === 'rejected'"
             class="px-3 pt-2">
          <div class="text-xs font-sans mb-0.5"
               :class="resolvedStatus === 'rejected'
                 ? 'text-neutral-500 dark:text-neutral-400'
                 : 'text-amber-600 dark:text-amber-400'">
            <i :data-lucide="resolvedStatus === 'rejected' ? 'ban' : 'shield-alert'"
               class="w-3 h-3 inline mr-1"></i>
            {{ t('sessions.toolReason') || 'Reason' }}:
            <span class="font-semibold">{{ reasonText }}</span>
          </div>
        </div>
        <div v-if="toolArgs && !isEmptyArgs" class="px-3 pt-2 pb-1.5">
          <div class="text-xs text-gray-500 mb-0.5 font-sans">{{ t('sessions.parameters') }}</div>
          <pre class="inline-block align-top max-w-full p-1.5 rounded bg-black/5 dark:bg-black/30 overflow-x-auto whitespace-pre-wrap max-h-28 overflow-y-auto">{{ formatArgs() }}</pre>
        </div>
        <div v-if="liveOutput && resolvedStatus === 'running'" class="px-3 pt-1.5 pb-2">
          <div class="text-xs text-gray-500 mb-0.5 font-sans">{{ t('sessions.toolLiveOutput') || 'Live output' }}</div>
          <pre ref="liveOut" class="p-1.5 rounded bg-black/5 dark:bg-black/30 max-h-40 overflow-y-auto whitespace-pre-wrap">{{ liveOutput }}</pre>
        </div>
        <div v-if="result" class="px-3 pt-1.5 pb-2">
          <div class="text-xs text-gray-500 mb-0.5 font-sans">{{ t('sessions.toolOutput') }}</div>
          <pre class="p-1.5 rounded bg-black/5 dark:bg-black/30 max-h-40 overflow-y-auto whitespace-pre-wrap">{{ result }}</pre>
        </div>
      </div>
    </div>
  `,
};

/**
 * FileChangeCard Component
 * Compact inline row for a single file mutation (add/modify/delete) with an
 * expandable colored unified diff — mirrors IDE-style "file changed" chips.
 */
const FileChangeCard = {
  name: 'FileChangeCard',
  props: {
    change: { type: Object, default: null },
    // Session workspace root, used to render a full absolute path.
    base: { type: String, default: '' },
  },
  data() {
    return { open: false };
  },
  computed: {
    path() { return (this.change && this.change.path) || ''; },
    // Absolute path = session workspace root + workspace-relative path;
    // already-absolute stored paths pass through unchanged.
    fullPath() {
      const p = String(this.path || '');
      if (/^([a-zA-Z]:[\\/]|[\\/])/.test(p)) return p;
      const b = String(this.base || '').replace(/[\\/]+$/, '');
      return b ? b + '/' + p : p;
    },
    ctype() { return (this.change && this.change.change_type) || 'modified'; },
    additions() { return (this.change && this.change.additions) || 0; },
    deletions() { return (this.change && this.change.deletions) || 0; },
    hasDiff() { return !!(this.change && this.change.diff); },
    icon() {
      return { added: 'file-plus', deleted: 'file-x', modified: 'file-pen' }[this.ctype] || 'file';
    },
    typeLabel() {
      return {
        added: this.t('sessions.changeAdded') || 'Added',
        deleted: this.t('sessions.changeDeleted') || 'Deleted',
        modified: this.t('sessions.changeModified') || 'Modified',
      }[this.ctype] || this.ctype;
    },
    // Parse the unified diff into rows carrying REAL old/new file line numbers
    // (from @@ hunk headers) so the gutter aligns with the editor, instead of
    // sequentially numbering the diff text (which mis-counted ---/+++/@@ rows).
    diffRows() {
      let lines = String((this.change && this.change.diff) || '').split('\n');
      let truncated = false;
      if (lines.length > 600) { lines = lines.slice(0, 600); truncated = true; }
      const rows = [];
      let oldNo = 0;
      let newNo = 0;
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (line === '') {
          // Trailing artifact of splitting on a final newline: no numbers.
          rows.push({ text: line, oldNo: '', newNo: '' });
          continue;
        }
        if (line.startsWith('@@')) {
          const m = line.match(/^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@/);
          if (m) { oldNo = parseInt(m[1], 10); newNo = parseInt(m[2], 10); }
          rows.push({ text: line, oldNo: '', newNo: '' });
          continue;
        }
        if (line.startsWith('---') || line.startsWith('+++')) {
          rows.push({ text: line, oldNo: '', newNo: '' });
          continue;
        }
        if (line.startsWith('+')) {
          rows.push({ text: line, oldNo: '', newNo: newNo });
          newNo += 1;
          continue;
        }
        if (line.startsWith('-')) {
          rows.push({ text: line, oldNo: oldNo, newNo: '' });
          oldNo += 1;
          continue;
        }
        // Context line (leading space): present in both old and new.
        rows.push({ text: line, oldNo: oldNo, newNo: newNo });
        oldNo += 1;
        newNo += 1;
      }
      if (truncated) rows.push({ text: '… (diff preview truncated to 600 lines)', oldNo: '', newNo: '' });
      return rows;
    },
  },
  methods: {
    lineClass(line) {
      // The diff <pre> container is ALWAYS dark (bg-gray-900/dark:bg-black),
      // so line colors must NOT follow the page theme — light-mode pastels
      // on the black canvas looked broken. Fixed dark palette everywhere.
      if (!line) return 'text-gray-500';
      if (line.startsWith('+++') || line.startsWith('---')) return 'text-gray-500 font-semibold';
      if (line.startsWith('+')) return 'text-emerald-300 bg-emerald-500/15';
      if (line.startsWith('-')) return 'text-red-300 bg-red-500/15';
      if (line.startsWith('@@')) return 'text-sky-400';
      return 'text-gray-300';
    },
  },
  template: `
    <div class="my-0.5 rounded-md border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/60 text-xs font-mono overflow-hidden">
      <div class="flex items-center gap-2 px-3 py-1.5">
        <i :data-lucide="icon" class="w-3.5 h-3.5 text-gray-500 dark:text-gray-400 shrink-0"></i>
        <span class="truncate text-gray-700 dark:text-gray-300 min-w-0" :title="fullPath">{{ path }}</span>
        <span class="shrink-0 px-1.5 py-0.5 rounded leading-none font-semibold"
              :class="{
                'bg-emerald-100 dark:bg-emerald-900/50 text-emerald-700 dark:text-emerald-300': ctype === 'added',
                'bg-red-100 dark:bg-red-900/50 text-red-700 dark:text-red-300': ctype === 'deleted',
                'bg-amber-100 dark:bg-amber-900/50 text-amber-700 dark:text-amber-300': ctype === 'modified',
              }">{{ typeLabel }}</span>
        <span class="shrink-0 text-emerald-600 dark:text-emerald-400">+{{ additions }}</span>
        <span class="shrink-0 text-red-600 dark:text-red-400">-{{ deletions }}</span>
        <button v-if="hasDiff" type="button" @click="open = !open"
                class="ml-auto shrink-0 inline-flex items-center gap-1 px-2 py-0.5 rounded text-primary hover:bg-primary/10 transition-colors">
          <span class="font-sans">{{ t('sessions.viewChanges') || 'View changes' }}</span>
          <i :data-lucide="open ? 'chevron-up' : 'chevron-down'" class="w-3 h-3"></i>
        </button>
      </div>
      <div v-if="open && hasDiff" class="border-t border-gray-200/60 dark:border-gray-700/60">
        <!-- Full path header at the top of the diff code block -->
        <div class="px-3 py-1 text-[11px] font-mono text-gray-300 dark:text-gray-400 bg-[#161b22] break-all select-all" :title="fullPath">{{ fullPath }}</div>
        <!-- Rows are w-max + min-w-full: long lines must GROW the scroll
             width (horizontal scrollbar) instead of being squeezed by
             flex-1/min-w-0, which clipped the right edge with no way to
             read it. min-w-full keeps the add/del tint spanning the row.
             The gutter is sticky so line numbers stay readable while
             scrolling a long line sideways.
             Padding lives on the wrapper, NOT on the scroll box: padding
             inside an overflow box paints scrolled content, which sliced the
             16th row in half. 270px = 15 rows x 18px, so a view is whole. -->
        <div class="bg-[#0d1117] py-2">
          <pre class="diff-scroll text-[11px] leading-[18px] overflow-auto max-h-[270px] font-mono"><span v-for="(row, i) in diffRows" :key="i" :class="lineClass(row.text)" class="flex w-max min-w-full whitespace-pre"><span class="sticky left-0 z-10 flex shrink-0 select-none bg-[#0d1117]"><span class="w-10 pl-3 pr-2 text-right text-[10px] text-gray-600">{{ row.oldNo }}</span><span class="w-10 pr-3 text-right text-[10px] text-gray-500">{{ row.newNo }}</span></span><span class="shrink-0 whitespace-pre pr-4">{{ row.text }}</span></span></pre>
        </div>
      </div>
    </div>
  `,
};

// Shared session-toolbar items: bottom toolbar (active session) + welcome pane
// (pre-session). Interpolated twice into the template, same component scope.
// Session-scoped items (status/tokens/context/workspace) are v-if="activeSession";
// createSession() persists the welcome-pane picks onto the new session.
const SESSION_TOOLBAR_SNIPPET = `
         <!-- 1. Status. The dot keeps its colour meaning on phones, where the
              text label is dropped (see .session-toolbar-label in polish.css). -->
         <div v-if="activeSession" class="session-toolbar-pill flex items-center gap-1.5 px-2 py-0.5 rounded text-xs bg-gray-50 dark:bg-gray-700/50 text-gray-500 dark:text-gray-500 whitespace-nowrap shrink-0"
              :title="statusLabel" :aria-label="statusLabel">
           <span class="w-1.5 h-1.5 rounded-full" :class="statusDotClass"></span>
           <span class="session-toolbar-label">{{ statusLabel }}</span>
         </div>
         <!-- 2. Thinking Mode (click-to-expand popover; compact trigger) -->
         <div class="relative shrink-0">
           <button @click.stop="closeOtherToolbarPopovers('thinking'); showThinkingPopover = !showThinkingPopover"
                   class="session-toolbar-pill flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-gray-100 dark:bg-gray-700/50 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600/50 transition-colors whitespace-nowrap"
                   :title="thinkingModeLabel"
                   :aria-label="(t('sessions.thinkingMode') || 'Thinking mode') + ': ' + thinkingModeLabel"
                   :aria-expanded="showThinkingPopover ? 'true' : 'false'">
             <i data-lucide="brain" class="w-3 h-3 shrink-0"></i>
             <span class="session-toolbar-label">{{ thinkingModeLabel }}</span>
             <i data-lucide="chevron-up" class="w-3 h-3 shrink-0 opacity-50 session-toolbar-caret"></i>
           </button>
           <div v-if="showThinkingPopover"
                          class="session-popover session-popover-thinking absolute left-0 bottom-full mb-1 bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg shadow-lg z-50 py-1 min-w-[150px]"
                          @click.stop>
             <button v-for="opt in thinkingModeOptions" :key="opt.v"
                     @click="selectThinkingMode(opt.v)"
                     :title="opt.tip || ''"
                     :disabled="!!opt.disabled"
                     class="w-full px-3 py-1.5 text-left text-xs hover:bg-gray-100 dark:hover:bg-gray-600 transition-colors truncate flex items-center gap-1.5 disabled:hover:bg-transparent"
                     :class="[thinkingMode === opt.v ? 'text-primary font-semibold' : 'text-gray-700 dark:text-gray-300', opt.disabled ? 'opacity-40 cursor-not-allowed' : '']">
               <i v-if="thinkingMode === opt.v" data-lucide="check" class="w-3 h-3 shrink-0"></i>
               <span v-else class="w-3 shrink-0"></span>
               {{ opt.l }}
               </button>
               </div>
               </div>
               <!-- 2a. Transcript display mode (detailed / concise / minimal): a
               browser-local view filter over process info; same pill+popover
               pattern as the thinking-mode selector, icon-only on phones via
               .session-toolbar-label. -->
               <div class="relative shrink-0">
               <button @click.stop="closeOtherToolbarPopovers('display'); showDisplayPopover = !showDisplayPopover"
                   class="session-toolbar-pill flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-gray-100 dark:bg-gray-700/50 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600/50 transition-colors whitespace-nowrap"
                   :title="(t('sessions.displayMode') || 'Display mode') + ': ' + displayModeLabel"
                   :aria-label="(t('sessions.displayMode') || 'Display mode') + ': ' + displayModeLabel"
                   :aria-expanded="showDisplayPopover ? 'true' : 'false'">
               <i data-lucide="layers" class="w-3 h-3 shrink-0"></i>
               <span class="session-toolbar-label">{{ displayModeLabel }}</span>
               <i data-lucide="chevron-up" class="w-3 h-3 shrink-0 opacity-50 session-toolbar-caret"></i>
               </button>
               <div v-if="showDisplayPopover"
                class="session-popover session-popover-display absolute left-0 bottom-full mb-1 bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg shadow-lg z-50 py-1 min-w-[210px] max-w-[min(320px,85vw)]"
                @click.stop>
               <button v-for="opt in displayModeOptions" :key="opt.v"
                     @click="selectDisplayMode(opt.v)"
                     class="w-full px-3 py-1.5 text-left text-xs hover:bg-gray-100 dark:hover:bg-gray-600 transition-colors flex items-start gap-1.5"
                     :class="displayMode === opt.v ? 'text-primary font-semibold' : 'text-gray-700 dark:text-gray-300'">
               <i v-if="displayMode === opt.v" data-lucide="check" class="w-3 h-3 shrink-0 mt-0.5"></i>
               <span v-else class="w-3 shrink-0"></span>
               <span class="min-w-0">
                 <span class="block">{{ opt.l }}</span>
                 <span v-if="opt.tip" class="block opacity-60 font-normal mt-0.5 leading-snug whitespace-normal">{{ opt.tip }}</span>
               </span>
               </button>
               </div>
               </div>
               <!-- 2b. Session Approval Mode (click-to-expand popover; compact trigger) -->
         <div class="relative shrink-0">
           <button @click.stop="closeOtherToolbarPopovers('approval'); showApprovalPopover = !showApprovalPopover"
                   class="session-toolbar-pill flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-gray-100 dark:bg-gray-700/50 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600/50 transition-colors whitespace-nowrap"
                   :title="(t('sessions.approvalModeTip') || 'Tool approval mode for this session') + ': ' + approvalModeLabel"
                   :aria-label="(t('sessions.approvalModeTip') || 'Tool approval mode') + ': ' + approvalModeLabel"
                   :aria-expanded="showApprovalPopover ? 'true' : 'false'">
             <!-- Icon-only on phones: the shield colour is the only cue left, so
                  red/amber/emerald must stay untouched. -->
             <i data-lucide="shield" class="w-3 h-3 shrink-0"
                :class="approvalMode === 'deny' ? 'text-red-500' : approvalMode === 'allow' ? 'text-emerald-500' : 'text-amber-500'"></i>
             <span class="session-toolbar-label" :class="approvalModeColor">{{ approvalModeLabel }}</span>
             <i data-lucide="chevron-up" class="w-3 h-3 shrink-0 opacity-50 session-toolbar-caret"></i>
           </button>
           <div v-if="showApprovalPopover"
                          class="session-popover session-popover-approval absolute left-0 bottom-full mb-1 bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg shadow-lg z-50 py-1 min-w-[96px]"
                          @click.stop>
             <button v-for="opt in approvalModeOptions" :key="opt.v"
                     @click="selectApprovalMode(opt.v)"
                     class="w-full px-3 py-1.5 text-left text-xs hover:bg-gray-100 dark:hover:bg-gray-600 transition-colors truncate flex items-center gap-1.5"
                     :class="approvalMode === opt.v ? opt.c + ' font-semibold' : 'text-gray-700 dark:text-gray-300'">
               <i v-if="approvalMode === opt.v" data-lucide="check" class="w-3 h-3 shrink-0"></i>
               <span v-else class="w-3 shrink-0"></span>
               {{ opt.l }}
             </button>
           </div>
         </div>
         <!-- 3a. Agent Selector (pill + popover) -->
         <div v-if="agentOptions.length > 0" class="relative shrink-0">
           <button @click.stop="closeOtherToolbarPopovers('agent'); showAgentPopover = !showAgentPopover"
                   class="session-toolbar-pill flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-gray-100 dark:bg-gray-700/50 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600/50 transition-colors whitespace-nowrap"
                   :title="currentAgentName"
                   :aria-label="'Agent: ' + currentAgentName"
                   :aria-expanded="showAgentPopover ? 'true' : 'false'">
             <i data-lucide="bot" class="w-3 h-3 shrink-0"></i>
             <span class="truncate max-w-[120px] session-toolbar-label">{{ currentAgentName }}</span>
           </button>
            <div v-if="showAgentPopover"
                            class="session-popover session-popover-agent absolute left-0 bottom-full mb-1 bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg shadow-lg z-50 py-1 min-w-[120px] max-h-[200px] overflow-y-auto">
              <button v-for="a in agentOptions" :key="a.name"
                      @click="currentAgentName = a.name; changeAgent(); showAgentPopover = false"
                      class="w-full px-3 py-1.5 text-left text-xs hover:bg-gray-100 dark:hover:bg-gray-600 transition-colors truncate flex items-center gap-1.5"
                      :class="(currentAgentName === a.name) ? 'text-primary font-semibold' : 'text-gray-700 dark:text-gray-300'">
                <i data-lucide="bot" class="w-3 h-3 shrink-0" :class="a.is_default ? 'text-amber-500' : 'text-gray-500'"></i>
                {{ a.name }}
              </button>
            </div>
          </div>
         <!-- 3b. Model Selector (two-level: provider → models).
              The wrapper is the desktop anchor; on phones polish.css makes it
              static and lets the popover span the whole toolbar instead. -->
         <div class="session-model-anchor relative shrink-0">
           <button @click.stop="toggleModelPopover"
                             class="session-toolbar-pill flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-gray-100 dark:bg-gray-700/50 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600/50 transition-colors whitespace-nowrap"
                             :title="displayModelName"
                             :aria-label="(t('sessions.model') || 'Model') + ': ' + displayModelName"
                             :aria-expanded="showModelPopover ? 'true' : 'false'">
                       <i data-lucide="cpu" class="w-3 h-3 shrink-0"></i>
                       <span class="truncate max-w-[140px] session-toolbar-model-label">{{ displayModelName }}</span>
                     </button>
                     <div v-if="showModelPopover"
                          class="session-popover session-model-popover absolute left-0 bottom-full mb-1 z-50"
                          @click.stop>
                        <!-- Level 1: Providers (phones stack both panes; see polish.css) -->
                        <div class="session-model-providers w-[105px] shrink-0 py-1 max-h-[240px] overflow-y-auto bg-white dark:bg-gray-700 rounded-lg shadow-lg border border-gray-200 dark:border-gray-600">
                          <button v-for="(models, provider) in groupedModels" :key="provider"
                                  @mouseenter="hoveredProvider = provider"
                                  @click="hoveredProvider = provider"
                                  class="w-full px-3 py-1.5 text-left text-xs transition-colors flex items-center justify-between gap-1"
                                  :class="(hoveredProvider === provider || isSelectedProvider(provider))
                                    ? 'bg-primary/10 text-primary dark:bg-primary/20 dark:text-primary-light'
                                    : 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-600/50'">
                            <span class="truncate">{{ provider }}</span>
                            <i data-lucide="chevron-right" class="w-3 h-3 shrink-0 opacity-50 max-sm:hidden"></i>
                          </button>
                        </div>
                        <!-- Level 2: Models (separate floating panel) -->
                        <div v-if="hoveredProvider && groupedModels[hoveredProvider]"
                             class="session-model-models absolute left-[109px] bottom-0 w-[250px] shrink-0 py-1 max-h-[240px] overflow-y-auto bg-white dark:bg-gray-700 rounded-lg shadow-lg border border-gray-200 dark:border-gray-600">
                <div class="px-3 py-1 text-xs text-gray-500 dark:text-gray-500 font-medium uppercase tracking-wide">
                  {{ hoveredProvider }}
                </div>
                <button v-for="m in groupedModels[hoveredProvider]" :key="m.name"
                        @click="selectedModel = m.name; onModelChange(); showModelPopover = false; hoveredProvider = null"
                        :title="m.short_name || m.name"
                        class="w-full px-3 py-1.5 text-left text-xs transition-colors truncate flex items-center gap-1.5"
                        :class="isSelectedModel(m)
                          ? 'bg-primary/10 text-primary font-semibold dark:bg-primary/20 dark:text-primary-light'
                          : 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-600/50'">
                  <span v-if="isSelectedModel(m)" class="w-1.5 h-1.5 rounded-full bg-primary shrink-0"></span>
                  <span v-else class="w-1.5 shrink-0"></span>
                  <span class="flex-1 min-w-0 truncate">{{ m.short_name || m.name }}</span>
                  <!-- Enabled capability icons (image/audio/video/image-gen/video-gen) -->
                  <span v-if="enabledCapDefs(m).length"
                        class="flex items-center gap-0.5 shrink-0 text-primary/80 dark:text-primary-light/90"
                        :title="enabledCapTitle(m)">
                    <i v-for="mc in enabledCapDefs(m)" :key="mc.key" :data-lucide="mc.icon" class="w-3 h-3"></i>
                  </span>
                </button>
              </div>
            </div>
          </div>
          <!-- 4. Token (total, hover for detail). Dropped on phones (the
               session info panel still shows the numbers) so the row fits. -->
          <div v-if="activeSession" class="session-toolbar-pill session-toolbar-optional relative flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-gray-100 dark:bg-gray-700/50 text-gray-500 dark:text-gray-500 cursor-default group shrink-0"
               @click.self.stop
               :title="t('sessions.tokensIn') + ' / ' + t('sessions.tokensOut')">
            <i data-lucide="database" class="w-2.5 h-2.5"></i>
            <span class="tabular-nums">{{ formatTokens(totalTokens) }}</span>
            <!-- Hover tooltip (desktop only: hidden on mobile so its
                 nowrap width never widens the page horizontally) -->
            <div class="hidden md:block absolute bottom-full left-0 mb-1.5 px-2 py-1.5 rounded-md bg-gray-900 dark:bg-gray-600 text-white text-xs whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50 shadow-lg">
              <div class="flex items-center gap-1.5"><i data-lucide="arrow-up" class="w-2.5 h-2.5 text-emerald-400"></i>{{ t('sessions.tokensIn') }}: <span class="font-mono tabular-nums">{{ formatTokens(sessionStats.tokenInput || 0) }}</span></div>
              <div class="flex items-center gap-1.5"><i data-lucide="arrow-down" class="w-2.5 h-2.5 text-blue-400"></i>{{ t('sessions.tokensOut') }}: <span class="font-mono tabular-nums">{{ formatTokens(sessionStats.tokenOutput || 0) }}</span></div>
            </div>
          </div>
          <!-- 5. Context Usage Rate -->
          <div v-if="activeSession" class="session-toolbar-pill relative flex items-center gap-1.5 px-2 py-0.5 rounded text-xs bg-gray-100 dark:bg-gray-700/50 text-gray-500 dark:text-gray-500 cursor-default group shrink-0"
               @click.self.stop>
            <div class="w-10 h-1.5 bg-gray-300 dark:bg-gray-600 rounded-full overflow-hidden session-toolbar-context-bar">
              <div class="h-full rounded-full transition-all duration-300" :style="{ width: contextPercent + '%' }" :class="contextBarColor"></div>
            </div>
            <!-- Desktop shows used / limit; phones only have room for the
                 percentage next to the bar. -->
            <span class="tabular-nums whitespace-nowrap session-toolbar-context-full">{{ formatTokens(sessionStats.contextUsage || 0) }}&nbsp;<span class="opacity-50">/&thinsp;{{ contextLimitFormatted }}</span></span>
            <span class="tabular-nums whitespace-nowrap session-toolbar-context-pct">{{ Math.round(contextPercent) }}%</span>
            <!-- Hover tooltip: detailed layer breakdown (desktop only) -->
            <div class="hidden md:block absolute bottom-full left-0 mb-1.5 px-2.5 py-2 rounded-md bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700/60 text-gray-800 dark:text-gray-100 text-xs whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50 shadow-xl">
              <template v-if="layerBreakdown.length">
                <div v-for="ly in layerBreakdown" :key="ly.key" class="flex items-center gap-2 mb-1 last:mb-0">
                  <div class="flex items-center gap-1.5 w-[72px] shrink-0">
                    <span class="inline-block w-1.5 h-1.5 rounded-sm shrink-0" :style="{ background: ly.color }"></span>
                    <span class="text-xs leading-none truncate">{{ ly.label }}</span>
                  </div>
                  <span class="font-mono tabular-nums w-[36px] text-right text-xs shrink-0" :class="ly.tokenClass">{{ ly.tokensFmt }}</span>
                  <div class="w-10 h-1 bg-gray-200 dark:bg-gray-600 rounded-full overflow-hidden shrink-0">
                    <div class="h-full rounded-full" :style="{ width: ly.pct + '%', background: ly.color }"></div>
                  </div>
                  <span class="font-mono tabular-nums w-[24px] text-right text-xs shrink-0">{{ ly.pct }}%</span>
                </div>
                <div class="border-t border-gray-200 dark:border-gray-600 mt-1.5 pt-1 flex items-center justify-between text-xs opacity-70">
                  <span>{{ t('sessions.contextTotal') }}</span>
                  <span class="font-mono tabular-nums">{{ formatTokens(contextTotalTokens) }}</span>
                </div>
              </template>
              <div v-else class="text-xs opacity-60">{{ t('sessions.contextNoData') }}</div>
            </div>
          </div>
          <!-- 6. Workspace (icon only on phones; the path stays in the title) -->
          <button v-if="activeSession" @click.stop="openDirPicker"
                  class="session-toolbar-pill flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-gray-100 dark:bg-gray-700/50 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600/50 transition-colors whitespace-nowrap max-w-[160px] shrink-0"
                  :title="workspaceDisplayPath"
                  :aria-label="workspaceDisplayPath">
            <i data-lucide="folder-open" class="w-2.5 h-2.5 shrink-0 text-primary"></i>
            <span class="truncate session-toolbar-label">{{ workspaceShortPath }}</span>
          </button>
`;

const SessionsPage = {
  name: 'SessionsPage',
  components: { ToolCallCard, FileChangeCard, FileTreePanel, FileViewerPane },
  
  template: `
    <div class="sessions-viewport flex h-[calc(100vh-var(--navbar-height)-var(--statusbar-height))] animate-fade-in relative">
      <!-- Mobile Sidebar Overlay -->
      <div v-if="isMobile && sidebarExpanded"
           class="session-sidebar-backdrop lg:hidden"
           @click="toggleSidebar">
      </div>
      
      <!-- Session Sidebar -->
      <div class="session-sidebar flex flex-col flex-shrink-0 border-r border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 transition-all duration-300 z-50"
           :class="sidebarClasses">
        
        <!-- Sidebar Header -->
        <div class="flex items-center justify-between px-3 h-12 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 flex-shrink-0">
          <button @click="toggleSidebar" 
                  class="flex items-center justify-center w-6 h-6 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors">
            <i :data-lucide="sidebarExpanded ? 'panel-left-close' : 'panel-left-open'" class="w-3.5 h-3.5 text-gray-500 dark:text-gray-500"></i>
          </button>
          <template v-if="sidebarExpanded">
            <!-- Select mode bar -->
            <div v-if="selectMode" class="flex items-center gap-2">
              <span class="text-xs text-gray-500 dark:text-gray-500">
                {{ selectedCount > 0 ? selectedCount + ' ' + (t('common.selected') || 'selected') : (t('common.selectSessions') || 'Select sessions') }}
              </span>
              <button @click="exitSelectMode"
                      class="px-2 py-1 text-xs text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded transition-colors">
                {{ t('common.cancel') || 'Cancel' }}
              </button>
            </div>
            <div v-else class="flex items-center gap-2">
              <button @click="toggleSelectMode"
                      class="flex items-center gap-1 px-2 py-1 text-xs text-gray-500 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 rounded transition-colors">
                <i data-lucide="check-square" class="w-3.5 h-3.5"></i>
              </button>
              <button @click="createSession"
                      class="flex items-center gap-1.5 px-3 py-1.5 bg-primary text-white rounded-lg hover:bg-primary-dark transition-colors text-xs font-medium">
                <i data-lucide="plus" class="w-3.5 h-3.5"></i>
                {{ t('sessions.newSession') }}
              </button>
            </div>
          </template>
        </div>
        
        <!-- Session List -->
        <div v-if="sidebarExpanded" class="session-list-scroll overflow-y-auto flex-1 min-h-0">
          <div v-if="isLoadingSessions && sessions.length === 0" class="p-4 text-center">
            <i data-lucide="loader" class="w-6 h-6 text-gray-300 dark:text-gray-600 mx-auto mb-2 animate-spin"></i>
            <p class="text-xs text-gray-500 dark:text-gray-500">{{ t('common.loading') || 'Loading...' }}</p>
          </div>
          <div v-else-if="sessions.length === 0" class="p-4 text-center">
            <i data-lucide="message-square" class="w-8 h-8 text-gray-300 dark:text-gray-600 mx-auto mb-2"></i>
            <p class="text-xs text-gray-500 dark:text-gray-500">{{ t('sessions.noSessions') }}</p>
          </div>
          
          <div v-else class="py-2">
            <!-- Batch Action Bar (inside scrollable area) -->
            <div v-if="selectMode"
                 class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 sticky top-0 z-10">
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-1">
                  <button @click="toggleSelectAll"
                          class="px-2 py-1.5 text-xs rounded hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300 transition-colors">
                    {{ allSessionsSelected ? (t('common.deselectAll') || 'Deselect all') : (t('common.selectAll') || 'Select all') }}
                  </button>
                  <button @click="invertSelection"
                          class="px-2 py-1.5 text-xs rounded hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300 transition-colors">
                    {{ t('common.invertSelection') || 'Invert' }}
                  </button>
                </div>
                <div class="flex-shrink-0" :class="{ 'invisible': selectedCount === 0 }">
                  <button @click="batchDelete"
                          class="px-3 py-1.5 text-xs font-medium bg-red-500 text-white rounded-lg hover:bg-red-600 transition-colors">
                    <i data-lucide="trash-2" class="w-3 h-3 inline mr-1"></i>
                    {{ t('delete.deleteSelected') || 'Delete' }}
                  </button>
                </div>
              </div>
            </div>
            <template v-for="group in groupedSessions" :key="group.id">
              <!-- Group Header -->
              <div class="px-3 py-1.5 text-xs font-semibold text-gray-500 dark:text-gray-500 uppercase tracking-wider">
                {{ group.label }}
              </div>
              
              <!-- Session Items -->
              <div v-for="session in group.sessions" 
                   :key="session.id"
                   @click="selectMode ? toggleSelectSession(session) : selectSession(session)"
                   class="group px-3 cursor-pointer transition-colors duration-150 relative h-[52px] overflow-hidden flex items-center gap-2"
                   :class="[
                     session.isRunning ? 'running-session' : '',
                     selectedSessionIds[session.id]
                       ? 'bg-primary/5 dark:bg-primary/10'
                       : activeSession?.id === session.id 
                         ? 'bg-primary/10 text-primary' 
                         : session.hasError
                           ? 'text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-950/30 hover:bg-red-100 dark:hover:bg-red-950/50'
                           : 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                   ]">
                <!-- Checkbox (select mode) -->
                <div v-show="selectMode" class="flex-shrink-0">
                  <div class="w-4 h-4 rounded border-2 flex items-center justify-center transition-colors"
                       :class="selectedSessionIds[session.id] ? 'bg-primary border-primary' : 'border-gray-300 dark:border-gray-500'">
                    <i v-show="selectedSessionIds[session.id]" data-lucide="check" class="w-3 h-3 !text-white"></i>
                  </div>
                </div>
                <!-- Left focus bar: shown only when selected; color reflects state -->
                <span v-show="!selectMode && activeSession?.id === session.id"
                      class="absolute left-1 top-1/2 -translate-y-1/2 w-1.5 h-5 rounded-full z-10 transition-colors"
                      :class="session.hasError ? 'bg-red-500' : (session.isRunning ? 'bg-emerald-500' : 'bg-primary')"></span>
                <!-- Normal View (always rendered for stable height) -->
                <div class="w-full h-full flex flex-col justify-center"
                     :class="{ 'invisible': editingSessionId === session.id || deletingSessionId === session.id }">
                  <div class="flex items-center justify-between">
                    <div class="text-sm font-medium truncate flex-1 flex items-center gap-1.5">
                      <i v-if="session.hasError" data-lucide="alert-circle" class="w-3.5 h-3.5 text-red-500 dark:text-red-400 shrink-0"></i>
                      <span class="truncate" :class="{ 'italic text-gray-400 dark:text-gray-500': sessionTitleDisplay(session) !== session.title }">{{ sessionTitleDisplay(session) }}</span>
                    </div>
                    <!-- Row actions. Desktop reveals them on hover; touch
                         devices have no hover, so they are always visible
                         there with slightly larger hit areas. -->
                    <div class="session-row-actions items-center gap-1 ml-2"
                         :class="selectMode ? '!hidden' : (isTouch ? 'flex' : 'hidden group-hover:flex')">
                      <button @click.stop="togglePin(session)"
                              class="p-1.5 md:p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
                              :class="session.isPinned ? 'text-primary' : 'text-gray-500 dark:text-gray-500'">
                        <i data-lucide="pin" class="w-3.5 h-3.5 md:w-3 md:h-3"></i>
                      </button>
                      <button @click.stop="startRename(session)"
                              class="p-1.5 md:p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors text-gray-500 dark:text-gray-500">
                        <i data-lucide="pencil" class="w-3.5 h-3.5 md:w-3 md:h-3"></i>
                      </button>
                      <button @click.stop="startDelete(session)"
                              class="p-1.5 md:p-1 rounded hover:bg-red-100 dark:hover:bg-red-900/30 transition-colors text-gray-500 dark:text-gray-500 hover:text-red-500 dark:hover:text-red-400">
                        <i data-lucide="trash-2" class="w-3.5 h-3.5 md:w-3 md:h-3"></i>
                      </button>
                    </div>
                  </div>
                  <div class="flex items-center justify-between mt-0.5">
                    <span class="text-xs text-gray-500 dark:text-gray-500">{{ session.time }}</span>
                    <div class="flex items-center gap-1.5 shrink-0">
                      <span v-if="session.isPinned" class="text-xs text-primary/60">{{ t('sessions.pinned') || 'Pinned' }}</span>
                      <span v-if="sourceBadge(session)"
                            class="px-1.5 py-px text-[10px] leading-none rounded-full border border-gray-300 dark:border-gray-600 text-gray-500 dark:text-gray-400"
                            :title="t('sessions.sourceLabel') + ': ' + session.source">
                        {{ sourceBadge(session) }}
                      </span>
                    </div>
                  </div>
                </div>
                
                <!-- Inline Edit Mode (absolute overlay) -->
                <div v-if="editingSessionId === session.id" 
                     @click.stop 
                     class="absolute inset-0 w-full h-full flex items-center px-3 bg-inherit">
                  <div class="flex items-center gap-2 w-full">
                    <input ref="editInput"
                           type="text"
                           v-model="editingTitle"
                           @keyup.enter="confirmRename(session)"
                           @keyup.escape="cancelRename"
                           @blur="confirmRename(session)"
                           class="flex-1 px-2 py-1.5 text-sm bg-white dark:bg-gray-700 border border-primary rounded-md focus:outline-none focus:ring-2 focus:ring-primary/50 text-gray-900 dark:text-white">
                    <button @click.stop="confirmRename(session)"
                            class="p-1.5 rounded hover:bg-green-100 dark:hover:bg-green-900/30 transition-colors text-green-500">
                      <i data-lucide="check" class="w-3.5 h-3.5"></i>
                    </button>
                    <button @click.stop="cancelRename"
                            class="p-1.5 rounded hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors text-gray-500">
                      <i data-lucide="x" class="w-3.5 h-3.5"></i>
                    </button>
                  </div>
                </div>
                
                <!-- Inline Delete Confirmation (absolute overlay) -->
                <div v-if="deletingSessionId === session.id" 
                     @click.stop 
                     class="absolute inset-0 w-full h-full flex flex-col justify-center px-3 bg-inherit">
                  <div class="flex items-center gap-2 w-full">
                    <span class="text-xs text-gray-600 dark:text-gray-500 flex-1 truncate">{{ t('delete.deleteTitle') || 'Delete session?' }}</span>
                    <button @click.stop="confirmDelete(session, false)"
                            class="px-2.5 py-1.5 text-xs font-medium bg-gray-200 dark:bg-gray-600 text-gray-700 dark:text-gray-300 rounded hover:bg-gray-300 dark:hover:bg-gray-500"
                            :title="t('delete.normalDesc')">
                      {{ t('delete.normalOption') || 'Delete' }}
                    </button>
                    <button @click.stop="confirmDelete(session, true)"
                            class="px-2.5 py-1.5 text-xs font-medium bg-primary text-white rounded hover:bg-primary-dark"
                            :title="t('delete.distillDesc')">
                      {{ t('delete.distillOption') || 'Distill' }}
                    </button>
                    <button @click.stop="cancelDelete"
                            class="px-2.5 py-1.5 text-xs font-medium bg-gray-200 dark:bg-gray-600 text-gray-700 dark:text-gray-300 rounded hover:bg-gray-300 dark:hover:bg-gray-500 transition-colors">
                      {{ t('common.cancel') || 'Cancel' }}
                    </button>
                  </div>
                </div>
              </div>
            </template>
          </div>
        </div>
        
        <!-- File manager tree: bottom half of the sidebar. Collapsible via its
             own header chevron; the split bar above it drag-resizes the height
             (persisted in localStorage). Hidden below the lg breakpoint. -->
        <div v-if="sidebarExpanded"
             class="fm-tree-section flex-col flex-shrink-0 border-t border-gray-200 dark:border-gray-700 hidden lg:flex">
          <div v-if="fmTreeOpen" class="fm-split fm-split-v"
               @pointerdown="fmStartDragV"
               :title="t('files.dragV') || 'Drag to resize'"></div>
          <keep-alive>
            <file-tree-panel v-if="fmTreeOpen"
                             ref="fileTree"
                             :session-id="activeSession ? activeSession.id : ''"
                             :root-path="workspaceDisplayPath"
                             :style="{ height: fmTreeH + 'px' }"
                             @open-file="fmOpenFile"
                             @collapse="fmTreeOpen = false; fmPersist()"></file-tree-panel>
          </keep-alive>
          <button v-if="!fmTreeOpen" type="button" class="fm-collapsed-bar"
                  @click="fmTreeOpen = true; fmPersist()"
                  :title="t('files.expand') || 'Expand file tree'">
            <i data-lucide="chevron-up" class="w-3.5 h-3.5 text-gray-400 dark:text-gray-500"></i>
            <i data-lucide="folder-tree" class="w-3.5 h-3.5 text-gray-400 dark:text-gray-500"></i>
            <span class="text-xs text-gray-500 dark:text-gray-500">{{ t('files.title') || 'Files' }}</span>
          </button>
        </div>
      </div>
      
      <!-- Main Chat Area -->
      <div class="flex-1 flex flex-col min-w-0">
        <!-- Chat Header -->
        <div class="session-chat-header flex items-center justify-between px-4 h-12 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
          <div class="flex items-center gap-2 min-w-0">
            <button v-if="isMobile" @click="toggleSidebar" 
                    class="flex items-center justify-center w-8 h-8 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors mr-1 shrink-0">
              <i data-lucide="menu" class="w-4 h-4 text-gray-500 dark:text-gray-500"></i>
            </button>
            <i data-lucide="message-square" class="w-5 h-5 text-primary shrink-0"></i>
            <span class="font-medium text-gray-900 dark:text-white truncate">
              {{ activeSession ? activeSession.title : t('sessions.newSession') }}
            </span>
          </div>
          <div class="flex items-center gap-2 shrink-0">
            <!-- Decorative search affordance: there is no session search yet, so
                 it is hidden on phones where the header needs the width for
                 the title (desktop keeps the original chrome). -->
            <button class="hidden md:flex items-center justify-center w-8 h-8 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors">
              <i data-lucide="search" class="w-4 h-4 text-gray-500 dark:text-gray-500"></i>
            </button>
            <!-- File viewer pane toggle (desktop only; the pane itself is
                 hidden below lg together with the tree). -->
            <button @click="fmToggleViewer"
                    class="hidden lg:flex items-center justify-center w-8 h-8 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                    :class="{ 'bg-primary/10': fmViewerOpen }"
                    :title="t('files.viewerToggle') || 'Toggle file viewer'">
              <i data-lucide="folder-open" class="w-4 h-4"
                 :class="fmViewerOpen ? 'text-primary' : 'text-gray-500 dark:text-gray-500'"></i>
            </button>
            <button @click="toggleRightSidebar" 
                    class="flex items-center justify-center w-8 h-8 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                    :class="{ 'bg-primary/10 text-primary': rightSidebarExpanded }">
              <i data-lucide="panel-right" class="w-4 h-4 text-gray-500 dark:text-gray-500"></i>
            </button>
          </div>
        </div>
        
        <!-- Content row: file viewer pane (left, drag-resizable width) beside
             the chat column (messages + changes panel + toolbar + composer). -->
        <div class="flex-1 flex min-h-0">
          <keep-alive>
            <file-viewer-pane v-if="fmViewerOpen && !isMobile"
                              ref="fileViewer"
                              :session-id="activeSession ? activeSession.id : ''"
                              :style="{ width: fmViewerW + 'px' }"
                              class="flex-shrink-0"
                              @close="fmToggleViewer"></file-viewer-pane>
          </keep-alive>
          <div v-if="fmViewerOpen && !isMobile" class="fm-split fm-split-h"
               @pointerdown="fmStartDragH"
               :title="t('files.dragH') || 'Drag to resize'"></div>
          <div class="flex-1 flex flex-col min-w-0">
        <!-- Messages Area -->
        <div class="relative flex-1 min-h-0 flex flex-col">
        <div class="session-messages flex-1 overflow-y-scroll px-4 pt-4 pb-6 relative" ref="messagesContainer"
             @scroll="onMessagesScroll">
          <!-- Welcome pane (no active session): title + hint + centered input.
               Typing here auto-creates a session and sends (see sendMessage). -->
          <div v-if="!activeSession" class="session-welcome flex flex-col items-center justify-center h-full px-4 pb-20">
            <div class="session-welcome-icon w-16 h-16 rounded-full bg-primary/10 dark:bg-primary/20 flex items-center justify-center mb-4">
              <i data-lucide="bot" class="w-8 h-8 text-primary"></i>
            </div>
            <h3 class="text-lg font-semibold text-gray-700 dark:text-gray-300 mb-2 text-center">
              {{ sessions.length > 0 ? t('sessions.welcomeBackTitle') : t('sessions.welcomeTitle') }}
            </h3>
            <p class="text-sm text-gray-500 dark:text-gray-500 text-center px-4 mb-6">
              {{ sessions.length > 0 ? t('sessions.welcomeBackHint') : t('sessions.welcomeHint') }}
            </p>
            <!-- Pre-session toolbar: same selectors as the bottom toolbar, so
                 thinking / approval / agent / model can be chosen before the first
                 message; createSession() persists them onto the new session. -->
            <div class="session-toolbar w-full max-w-xl flex flex-wrap items-center justify-center gap-x-2 gap-y-1.5 mb-4 text-xs select-none"
                 @click.self="showModelPopover = false; showAgentPopover = false; showThinkingPopover = false; showApprovalPopover = false; showDisplayPopover = false; hoveredProvider = null">
              ${SESSION_TOOLBAR_SNIPPET}
            </div>
            <div class="session-welcome-composer w-full max-w-xl">
              <div class="flex gap-2 items-end">
                <textarea
                       ref="welcomeInput"
                       :placeholder="t('sessions.inputPlaceholder')"
                       class="flex-1 px-3 sm:px-4 py-2.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary resize-none"
                       style="min-height: 2.5rem"
                       rows="1"
                       @input="autoResize"
                       @keydown.enter.exact.prevent="sendMessage"></textarea>
                <button @click="sendMessage"
                        class="flex items-center justify-center w-10 h-10 bg-primary text-white rounded-lg hover:bg-primary-dark transition-colors flex-shrink-0">
                  <i data-lucide="send" class="w-4 h-4"></i>
                </button>
              </div>
            </div>
          </div>
          
          <!-- Round wheel navigator: one dash per user round; the round in
               view is wider + primary colored, others small and faint.
               Hover floats a numbered list panel to the left of the wheel. -->
          <!-- Round wheel: hover/desktop affordance only. On touch it used to
               float 20x6px dashes on top of the message text. -->
          <div v-if="activeSession && userMessages.length >= 1"
               class="group fixed top-1/2 -translate-y-1/2 z-40 hidden lg:flex flex-col items-center gap-2.5 py-2 px-1.5"
               :class="rightSidebarExpanded ? 'right-72' : 'right-1'"
               @mouseenter="onWheelEnter"
               @mouseleave="onWheelLeave">
            <button v-for="(msg, index) in userMessages"
                    :key="msg.id"
                    @click="jumpToRound(index)"
                    @mouseenter="wheelHoverIndex = index"
                    class="rounded-full transition-all duration-200"
                    :class="wheelDotClass(index)"
                    :title="(index + 1) + '. ' + userBubbleContent(msg).substring(0, 40)">
            </button>
            <!-- Hover picker: shows ONLY the round under the cursor (falls back
                 to the in-view round). Mouse wheel rotates; clicking jumps. -->
            <div v-show="wheelHover"
                 @wheel.prevent="rotateWheel"
                 class="session-round-picker absolute right-7 top-1/2 w-[268px] overflow-hidden"
                 :style="wheelPickerStyle()">
              <div class="session-round-picker-head">
                <span>{{ t('sessions.roundsLabel') || 'Rounds' }}</span>
                <span class="tabular-nums">{{ (wheelHoverIndex != null ? wheelHoverIndex : currentRoundIndex) + 1 }} / {{ userMessages.length }}</span>
              </div>
              <div class="session-round-picker-list" v-if="hoveredRoundEntry">
                <button class="session-round-picker-item is-active"
                        @click="jumpToRound(hoveredRoundEntry.index); wheelHover = false">
                  <span class="session-round-picker-index">{{ hoveredRoundEntry.index + 1 }}</span>
                  <span class="session-round-picker-text">{{ userBubbleContent(hoveredRoundEntry.msg).substring(0, 30) }}{{ userBubbleContent(hoveredRoundEntry.msg).length > 30 ? '…' : '' }}</span>
                </button>
              </div>
            </div>
          </div>
          
          <div v-if="activeSession" class="session-message-stack">
            <div v-if="isLoadingMessages && messages.length === 0"
                 class="session-loading-state" role="status" aria-live="polite">
              <div class="session-loading-title">
                <i data-lucide="loader-circle" class="w-4 h-4"></i>
                <span>{{ t('sessions.loadingConversation') }}</span>
              </div>
              <div class="session-loading-skeleton" aria-hidden="true">
                <div class="session-skeleton-row">
                  <span class="session-skeleton-avatar"></span>
                  <div class="session-skeleton-bubble">
                    <span class="session-skeleton-line medium"></span>
                    <span class="session-skeleton-line long"></span>
                    <span class="session-skeleton-line short"></span>
                  </div>
                </div>
                <div class="session-skeleton-row is-user">
                  <div class="session-skeleton-bubble">
                    <span class="session-skeleton-line medium"></span>
                    <span class="session-skeleton-line short"></span>
                  </div>
                </div>
                <div class="session-skeleton-row">
                  <span class="session-skeleton-avatar"></span>
                  <div class="session-skeleton-bubble">
                    <span class="session-skeleton-line long"></span>
                    <span class="session-skeleton-line medium"></span>
                  </div>
                </div>
              </div>
            </div>
            <div v-else-if="!isLoadingMessages && messages.length === 0"
                 class="session-empty-conversation" role="status">
              <div class="session-empty-icon"><i data-lucide="sparkles" class="w-5 h-5"></i></div>
              <h3 class="session-empty-title">{{ t('sessions.emptyConversationTitle') }}</h3>
              <p class="session-empty-hint">{{ t('sessions.emptyConversationHint') }}</p>
            </div>
            <div v-if="isLoadingMessages && messages.length > 0 && !loadingOlder" class="session-load-progress" role="progressbar"
                 :aria-label="t('sessions.loadingConversation')"></div>
            <div v-if="loadingOlder" class="flex justify-center py-2 text-xs text-gray-500" role="status" aria-live="polite">
              <span class="inline-flex items-center gap-2 rounded-full border border-gray-200 dark:border-gray-700 bg-white/90 dark:bg-gray-800/90 px-3 py-1.5 shadow-sm">
                <i data-lucide="loader-circle" class="w-3.5 h-3.5 text-primary animate-spin"></i>
                {{ t('sessions.loadingEarlier') }}
              </span>
            </div>
            <div v-for="(message, index) in messages" :key="messageKey(message, index)" 
                 :data-message-id="message.id"
                 class="session-message-row flex flex-col transition-all duration-300"
                 :class="[message.role === 'user' ? 'items-end gap-1' : 'items-stretch gap-1', msgSpacing(index)]">
              <!-- Tool messages: cards are self-contained and start at the
                   column edge, like assistant replies. -->
              <!-- Injected runtime context ([BoarAi_System] …) is meta, not chat
                   content: centered collapsible line above the user bubble.
                   Skill records / authoring instructions carry their own row
                   below, so skip this one to avoid two stacked toggles. -->
              <div v-if="showUserMetaLine(message)" class="w-full flex justify-center">
                <button @click="message._metaOpen = !message._metaOpen"
                        class="inline-flex items-center gap-1 text-[11px] text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors">
                  <!-- Same lucide-swap caveat as the thinking toggle: rotate
                       a wrapper instead of rebinding the icon name. -->
                  <span class="inline-flex items-center transition-transform duration-150"
                        :class="message._metaOpen ? 'rotate-90' : ''">
                    <i data-lucide="chevron-right" class="w-3 h-3"></i>
                  </span>
                  {{ t('sessions.systemContext') || 'System context' }}
                </button>
              </div>
              <div v-if="message._metaOpen && showUserMetaLine(message)" class="w-full flex justify-center">
                <div class="max-w-[85%] px-2.5 py-1.5 rounded-lg bg-gray-100/80 dark:bg-gray-800/60 text-[11px] font-mono text-gray-400 dark:text-gray-500 whitespace-pre-wrap break-all text-center">{{ userMetaLine(message) }}</div>
              </div>
              <!-- Round header: avatar + name are one unit and own their div.
                   Kept OUTSIDE the message width wrapper (max-w-[75%]) so the
                   label is a sibling of the body, not nested in the same box
                   as thinking / tool cards / reply text. -->
              <div v-if="message.role === 'assistant' && isRoundHeader(index)"
                   class="session-assistant-header">
                <span class="session-assistant-avatar">
                  <i data-lucide="bot" class="w-5 h-5"></i>
                </span>
                <span class="session-assistant-name">{{ messageDisplayName(message) }}</span>
                <!-- Chat-command badge: this turn was answered by a registered
                     command (or a prompt template) without a normal LLM run. -->
                <span v-if="message.chat_command"
                      class="ml-1 inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full bg-primary/10 text-primary text-[10px] font-mono shrink-0"
                      :title="message.chat_command.kind === 'prompt'
                        ? (t('sessions.promptCommandTip') || 'Prompt command — expanded and sent to the model')
                        : (t('sessions.chatCommandTip') || 'Chat command — answered locally, no LLM run')">
                  <i :data-lucide="message.chat_command.kind === 'prompt' ? 'sparkles' : 'terminal-square'" class="w-3 h-3"></i>/{{ message.chat_command.name }}
                </span>
                <!-- Per-round display override: force this round flat (all
                     process visible) or follow the global display mode again.
                     Only offered when the round actually has foldable process. -->
                <button v-if="planAt(index).round && planAt(index).round.hasFold"
                        @click="toggleRoundFlat(index)"
                        class="ml-0.5 inline-flex items-center rounded p-0.5 text-gray-300 dark:text-gray-600 hover:text-gray-500 dark:hover:text-gray-400 hover:bg-black/[0.04] dark:hover:bg-white/[0.06] transition-colors"
                        :title="planAt(index).round.override ? (t('sessions.roundFollowGlobal') || 'Follow global display mode') : (t('sessions.roundShowProcess') || 'Show this round’s process')"
                        :aria-pressed="planAt(index).round.override ? 'true' : 'false'">
                  <i :data-lucide="planAt(index).round.override ? 'eye-off' : 'eye'" class="w-3.5 h-3.5"></i>
                </button>
              </div>
              <!-- Process fold row (concise/minimal): one-line summary of a
                   folded activity segment. Stays mounted while the segment is
                   open so it doubles as the collapse handle; the segment's own
                   message rows re-render their process blocks in place. -->
              <div v-if="planAt(index).foldRow" :class="msgWidthClass(message)">
                <button @click="toggleFoldSeg(index)"
                        class="-ml-1 inline-flex items-center gap-1 px-1 py-0.5 rounded cursor-pointer select-none text-[11px] text-gray-400 dark:text-gray-500 hover:text-gray-500 dark:hover:text-gray-400 hover:bg-black/[0.04] dark:hover:bg-white/[0.06] transition-colors max-w-full"
                        :title="planAt(index).segOpen ? (t('sessions.collapseProcess') || 'Collapse process') : (t('sessions.expandProcess') || 'Expand process')"
                        :aria-expanded="planAt(index).segOpen ? 'true' : 'false'">
                  <!-- Rotate a WRAPPER, not the <i> (lucide swaps it for an svg). -->
                  <span class="inline-flex items-center transition-transform duration-150 shrink-0"
                        :class="planAt(index).segOpen ? 'rotate-90' : ''">
                    <i data-lucide="chevron-right" class="w-3 h-3 opacity-60"></i>
                  </span>
                  <i data-lucide="layers" class="w-3 h-3 opacity-70 shrink-0"></i>
                  <span class="shrink-0">{{ processFoldLabel(planAt(index).seg) }}</span>
                  <span v-if="planAt(index).seg && planAt(index).seg.failCount > 0"
                        class="shrink-0 text-amber-500 dark:text-amber-400">
                    · {{ t('sessions.processFoldFailed', { count: planAt(index).seg.failCount }) }}
                  </span>
                  <span v-if="!planAt(index).segOpen && displayPolicy.thinkingPreview && planAt(index).seg && planAt(index).seg.preview"
                        class="truncate opacity-60 min-w-0">{{ planAt(index).seg.preview }}</span>
                </button>
              </div>
              <div :class="msgWidthClass(message)">
                <!-- User Message -->
                <div v-if="message.role === 'user'">
                  <!-- Skill evolution outcome card (persisted [BoarAi_System]
                       record; NOT a human turn — no bubble, no wheel round) -->
                  <div v-if="message.skill_card" class="w-full flex justify-center py-0.5">
                    <div class="session-skill-card"
                         :class="'session-skill-card--' + (message.skill_card.kind || 'created')">
                      <i :data-lucide="message.skill_card.kind === 'rejected' || message.skill_card.kind === 'restored' ? 'triangle-alert' : 'sparkles'"
                         class="w-3.5 h-3.5 shrink-0"></i>
                      <span class="min-w-0">{{ userMetaLine(message).replace(/^\[BoarAi_System\]\s*/, '') }}</span>
                    </div>
                  </div>
                  <!-- Skill authoring instruction: subdued collapsible task
                       line instead of a user bubble full of English prompt -->
                  <div v-else-if="message.skill_authoring" class="w-full flex flex-col items-center gap-1">
                    <button @click="message._metaOpen = !message._metaOpen"
                            class="inline-flex items-center gap-1 text-[11px] text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors">
                      <i data-lucide="sparkles" class="w-3 h-3"></i>
                      {{ t('sessions.skillAuthoring') || '技能提炼任务' }}
                      <span class="inline-flex items-center transition-transform duration-150"
                            :class="message._metaOpen ? 'rotate-180' : ''">
                        <i data-lucide="chevron-down" class="w-3 h-3"></i>
                      </span>
                    </button>
                    <div v-if="message._metaOpen"
                         class="max-w-[85%] px-2.5 py-1.5 rounded-lg bg-gray-100/80 dark:bg-gray-800/60 text-[11px] font-mono text-gray-400 dark:text-gray-500 whitespace-pre-wrap break-all">{{ userBubbleContent(message) }}</div>
                  </div>
                  <template v-else>
                  <!-- Edit mode: bubble -> editor panel -->
                  <div v-if="editingMessageId === message.id" class="flex flex-col items-end w-full max-w-[85%]">
                    <div class="w-full rounded-xl bg-gray-50 dark:bg-gray-800/60 border border-primary/30 shadow-sm overflow-hidden">
                      <!-- Editor header -->
                      <div class="flex items-center justify-between px-3 py-1.5 bg-primary/5 dark:bg-primary/10 border-b border-primary/20">
                        <span class="inline-flex items-center gap-1.5 text-[11px] font-medium text-primary">
                          <i data-lucide="pencil-line" class="w-3 h-3"></i>
                          {{ t('sessions.editingLabel') || 'Editing message' }}
                        </span>
                        <button @click="cancelEditMessage"
                                class="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
                                :title="t('sessions.cancelEdit') || 'Cancel'">
                          <i data-lucide="x" class="w-3.5 h-3.5"></i>
                        </button>
                      </div>
                      <!-- Editor body -->
                      <textarea
                        v-model="editingContent"
                        ref="editInput"
                        :placeholder="t('sessions.editPlaceholder') || 'Edit your message...'"
                        class="w-full px-3 py-2.5 text-sm bg-transparent text-gray-800 dark:text-gray-200 focus:outline-none resize-none leading-relaxed"
                        style="min-height: 4rem"
                        rows="3"
                        @input="autoResizeEdit"
                        @keydown.enter.exact.prevent="submitEditMessage"
                        @keydown.escape.prevent="cancelEditMessage"></textarea>
                      <!-- Editor footer -->
                      <div class="flex items-center justify-between px-3 py-2 border-t border-gray-200/70 dark:border-gray-700/60 bg-white/60 dark:bg-gray-900/40">
                        <span class="text-[11px] text-gray-400 dark:text-gray-500 hidden sm:inline whitespace-nowrap">
                          {{ t('sessions.editShortcutHint') || 'Enter to send · Esc to cancel' }}
                        </span>
                        <div class="flex items-center gap-2 ml-auto shrink-0">
                          <button @click="cancelEditMessage"
                                  class="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium whitespace-nowrap shrink-0 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg transition-colors">
                            {{ t('sessions.cancelEdit') || 'Cancel' }}
                          </button>
                          <button @click="submitEditMessage"
                                  class="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-xs font-medium whitespace-nowrap shrink-0 bg-primary text-white rounded-lg hover:bg-primary-dark shadow-sm transition-colors">
                            <i data-lucide="send" class="w-3.5 h-3.5"></i>
                            {{ t('sessions.saveMessage') || 'Save' }}
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                  <!-- Normal bubble -->
                  <template v-else>
                    <div v-if="userBubbleContent(message).trim() || (message.attachments && message.attachments.length)" class="flex flex-col items-end gap-1">
                    <div class="session-user-bubble text-sm rounded-lg p-3 break-words inline-block bg-primary/70 text-white selection:bg-white/30">
                      <!-- Attachment previews: images inline, files as download chips -->
                      <div v-if="message.attachments && message.attachments.length"
                           class="mb-2 flex flex-wrap gap-2 justify-end">
                        <template v-for="(att, ai) in message.attachments" :key="ai">
                          <div v-if="att.kind === 'image'"
                               @click.stop="openImagePreview(att)"
                               :title="att.name || ''"
                               class="block rounded-lg overflow-hidden border border-white/20 bg-white/10 cursor-zoom-in hover:opacity-90 transition-opacity">
                            <img :src="attachmentDownloadUrl(att.stored_name)"
                                 class="max-w-[180px] max-h-[160px] object-cover"
                                 :alt="att.name">
                          </div>
                          <a v-else
                             :href="attachmentDownloadUrl(att.stored_name)"
                             class="inline-flex items-center gap-1.5 px-2 py-1 rounded bg-white/15 hover:bg-white/25 transition-colors">
                            <i data-lucide="file-text" class="w-3.5 h-3.5 shrink-0"></i>
                            <span class="max-w-[140px] truncate">{{ att.name }}</span>
                            <span class="opacity-70 shrink-0">{{ formatBytes(att.size) }}</span>
                          </a>
                        </template>
                      </div>
                      {{ userBubbleContent(message) }}
                    </div>
                    <!-- Actions: always visible, directly under the bubble.
                         Ghost icon buttons: no fill, border or shadow, so the
                         row reads as part of the message rather than a card. -->
                    <div class="flex items-center gap-0.5">
                      <!-- Send time sits left of the copy button; the same
                           row keeps edit right after it. -->
                      <span v-if="messageTimeLabel(message)"
                            class="px-1 text-[11px] tabular-nums text-gray-400/90 dark:text-gray-500 select-none"
                            :title="messageTimeTitle(message)">{{ messageTimeLabel(message) }}</span>
                      <button @click="copyMessage(message)" class="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors" :title="t('common.copy') || 'Copy'">
                        <i :data-lucide="message._copied ? 'check' : 'copy'" class="w-3 h-3"></i>
                      </button>
                      <button v-if="isLatestUserMessage(message)" @click="startEditMessage(message)"
                              class="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
                              :title="t('sessions.editMessage') || 'Edit'">
                        <i data-lucide="pencil" class="w-3 h-3"></i>
                      </button>
                    </div>
                    </div>
                  </template>
                  </template>
                </div>
                <!-- Tool Result Message → as ToolCallCard with result -->
                <!-- Prefer pairing onto assistant tool_calls cards when possible -->
                <div v-else-if="message.role === 'tool' && !isToolMsgPairedToAssistant(message) && planAt(index).showProcess"
                     class="min-w-0 session-tool-standalone">
                  <file-change-card
                    v-if="getFileChange(message)"
                    :change="getFileChange(message)"
                    :base="workspaceDisplayPath">
                  </file-change-card>
                  <tool-call-card
                    v-else
                    :tool-name="getToolNameForToolMsg(message)"
                    :tool-args="getToolArgsForToolMsg(message)"
                    :result="message.content || ''"
                    :status="getToolResultStatus(message)"
                    :error-reason="getToolErrorReason(message)">
                  </tool-call-card>
                </div>
                <div v-else-if="message.role === 'tool'" class="hidden"></div>
                <!-- Assistant Message: bg/padding only if it has text content or error -->
                <!-- Folded process-only assistant messages skip the body div
                     entirely: an empty padded box per hidden message would
                     stack up as visible whitespace under the fold row. -->
                <div v-else-if="message.role === 'assistant' && (planAt(index).showProcess || message.content || message._error)"
                     class="session-assistant-message"
                     :class="(message.content || message._error)
                       ? 'text-sm py-1 break-words text-gray-700 dark:text-gray-300 markdown-body'
                       : 'text-sm pt-1 break-words min-w-0'">
                  <!-- Thinking Panel (message-level, collapsible). Deliberately
                       low-key: no border/box, just a muted inline toggle line so
                       the reply body stays the visual focus. -->
                  <div v-if="message.thinking && planAt(index).showProcess" class="overflow-hidden">
                    <div @click="message._thinkOpen = !message._thinkOpen"
                         class="-ml-1 inline-flex items-center gap-1 px-1 py-0.5 rounded cursor-pointer select-none text-[11px] text-gray-400 dark:text-gray-500 hover:text-gray-500 dark:hover:text-gray-400 hover:bg-black/[0.04] dark:hover:bg-white/[0.06] transition-colors">
                      <i data-lucide="brain" class="w-3 h-3 opacity-70 shrink-0"></i>
                      <span>{{ t('sessions.thinking') }}</span>
                      <span v-if="message._thinkingComplete" class="opacity-70">
                        · {{ t('sessions.thinkingComplete', { time: (message._thinkingTime || 0).toFixed(1) }) }}
                      </span>
                      <span v-else class="opacity-70">
                        · {{ t('sessions.thinkingInProgress') }}
                      </span>
                      <!-- Rotate a WRAPPER, not the <i>: lucide swaps the
                           <i> for an <svg>, so a :class on the icon itself
                           patches a detached node and the chevron freezes. -->
                      <span class="inline-flex items-center transition-transform duration-150"
                            :class="message._thinkOpen ? 'rotate-180' : ''">
                        <i data-lucide="chevron-down" class="w-3 h-3 opacity-60 shrink-0"></i>
                      </span>
                    </div>
                    <!-- Fold wrapper: animated max-height/opacity so completion
                         fold is a 150ms transition, not a hard layout jump. -->
                    <div class="overflow-hidden transition-all duration-150 ease-out"
                         :style="{ maxHeight: message._thinkOpen ? '320px' : '0px', opacity: message._thinkOpen ? 1 : 0 }">
                      <div data-thinking-body
                           @scroll="onThinkingBodyScroll($event, message)"
                           class="ml-0.5 mt-1 pl-2.5 pb-1 text-[11px] leading-relaxed text-gray-400 dark:text-gray-500 font-mono whitespace-pre-wrap max-h-[300px] max-w-[72ch] overflow-y-auto border-l border-gray-200/80 dark:border-gray-700/60">
                        {{ message.thinking }}
                        <span v-if="!message._thinkingComplete" class="inline-block w-1.5 h-3 bg-primary/40 ml-0.5 animate-pulse"></span>
                      </div>
                    </div>
                  </div>

                  <!-- Tool Calls as collapsible cards (if any) -->
                  <div v-if="planAt(index).showProcess && message.tool_calls && message.tool_calls.length > 0">
                    <!-- File mutations render as an inline change chip with a
                         viewable diff instead of the generic tool card -->
                    <div v-for="tc in message.tool_calls" :key="tc.id">
                      <file-change-card
                        v-if="getAttachedFileChange(tc)"
                        :change="getAttachedFileChange(tc)"
                        :base="workspaceDisplayPath">
                      </file-change-card>
                      <tool-call-card
                        v-else
                        :tool-name="getToolName(tc)"
                        :tool-args="getToolArgs(tc)"
                        :result="getAttachedToolResult(tc)"
                        :status="getAttachedToolStatus(tc)"
                        :error-reason="getAttachedToolErrorReason(tc)"
                        :live-output="getAttachedLiveOutput(tc)">
                      </tool-call-card>
                    </div>
                  </div>
                  <!-- Provider auto-retry progress on the live assistant bubble -->
                  <div v-if="message._retryAttempt && !message._error && !message.content"
                       class="rounded-lg border border-amber-200 dark:border-amber-800/50 bg-amber-50/80 dark:bg-amber-950/25 px-3 py-2 mb-1">
                    <div class="flex items-center gap-2 text-xs text-amber-700 dark:text-amber-300">
                      <i data-lucide="refresh-cw" class="w-3.5 h-3.5 animate-spin shrink-0"></i>
                      <span>
                        {{ t('sessions.retryingHint', { attempt: message._retryAttempt, max: message._retryMax || '?' })
                           || ('Retrying ' + message._retryAttempt + '/' + (message._retryMax || '?')) }}
                      </span>
                    </div>
                    <div v-if="message._retryError"
                         class="mt-1 text-xs text-amber-600/80 dark:text-amber-400/70 whitespace-pre-wrap break-words line-clamp-3">
                      {{ message._retryError }}
                    </div>
                  </div>
                  <!-- Text: normal reply, or partial stream before failure (not the [Error] stamp) -->
                  <div v-if="message.content && !message._error" class="assistant-message-content">
                    <div v-html="renderMarkdown(message.content)"></div>
                    <span v-if="isAssistantStreaming(message, index)" class="assistant-stream-caret" aria-hidden="true"></span>
                  </div>
                  <div v-else-if="message.content && message._error && !isPersistedErrorStamp(message)"
                       class="mb-2 opacity-70" v-html="renderMarkdown(message.content)"></div>
                  <!-- Error in reply slot + retry (LLM / run failures) -->
                  <div v-if="message._error"
                       class="rounded-lg border border-red-200 dark:border-red-800/60 bg-red-50 dark:bg-red-950/30 px-3 py-2.5">
                    <div class="flex items-start gap-2">
                      <i data-lucide="alert-circle" class="w-4 h-4 text-red-500 dark:text-red-400 shrink-0 mt-0.5"></i>
                      <div class="min-w-0 flex-1">
                        <div class="text-xs font-medium text-red-600 dark:text-red-400 mb-1">
                          {{ t('sessions.statusError') || 'Error' }}
                        </div>
                        <div class="text-sm text-red-700 dark:text-red-300 whitespace-pre-wrap break-words">
                          {{ displayErrorText(message) }}
                        </div>
                        <button type="button"
                                @click="retryFailedMessage(message)"
                                :disabled="!canRetryError(message)"
                                class="mt-2 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium
                                       bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300
                                       hover:bg-red-200 dark:hover:bg-red-900/60
                                       disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                                :title="canRetryError(message) ? (t('common.retry') || 'Retry') : (t('sessions.retryUnavailable') || 'Retry only available for the latest live failure')">
                          <i data-lucide="refresh-cw" class="w-3 h-3"></i>
                          {{ t('common.retry') || 'Retry' }}
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
                <!-- System / Divider message (errors, interrupts, notifications) -->
                <div v-else-if="message.role === 'system'"
                     class="flex justify-center my-2">
                  <div class="px-4 py-2 rounded-lg text-xs font-medium max-w-md text-center"
                       :class="message._error
                         ? 'bg-red-50 text-red-600 border border-red-200'
                         : message._interrupt
                           ? 'bg-amber-50 text-amber-700 border border-amber-200'
                           : 'bg-gray-100 text-gray-500 border border-gray-200'">
                    {{ message.content }}
                  </div>
                </div>
                <!-- Folded process-only assistant row (display-mode plan hid
                     its blocks and it carries no text): render nothing, like
                     the paired-tool hidden branch. Without this catch-all the
                     message falls into the debug fallback below. -->
                <div v-else-if="message.role === 'assistant'" class="hidden"></div>
                <!-- Fallback (shouldn't happen) -->
                <div v-else 
                     class="text-sm rounded-lg p-3 break-words text-gray-500 dark:text-gray-500 bg-gray-50 dark:bg-gray-800 italic">
                  [{{ message.role }}] {{ message.content || '(empty)' }}
                </div>
              <!-- Round footer: completion chip + copy + consumption pills.
                   Rendered at ROW level on the round's last message so rounds
                   that end on a tool card, an error stamp or an interrupt
                   banner (no final text reply) still show finished time and
                   token stats — previously it lived inside the assistant
                   branch and vanished for interrupted rounds. -->
              <!-- While a run is in flight the CURRENT round has no finished
                   stamp yet — suppress its footer entirely so a lone copy
                   button does not float under a half-streamed reply. Older
                   rounds (with a persisted finished time) keep their footer. -->
              <div v-if="isRoundEnd(index) && (!isSessionRunning || !!roundFinishedLabel(index))"
                   class="session-round-footer flex justify-start items-center gap-1.5 mt-0.5 opacity-75 hover:opacity-100 transition-opacity">
                <!-- Run completion time, left of the round copy button.
                       Hidden while the round's run is still in flight. -->
                  <span v-if="roundFinishedLabel(index)"
                        class="px-1 text-[11px] tabular-nums text-gray-400 dark:text-gray-500 select-none"
                        :title="roundFinishedTitle(index)">{{ roundFinishedLabel(index) }}</span>
                  <button @click="copyRound(index)" class="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors" :title="t('sessions.copyRound') || 'Copy round'">
                    <i :data-lucide="message._copied ? 'check' : 'copy'" class="w-3 h-3"></i>
                  </button>
                  <!-- Per-round consumption pills (tokens + wall-clock),
                       styled for both themes (visible on dark canvas) -->
                  <span v-if="!isSessionRunning && roundStats(index) && roundStats(index).hasTokens"
                        class="relative group inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full
                               text-[11px] text-gray-400 dark:text-gray-500
                               select-none cursor-default">
                    <i data-lucide="database" class="w-3 h-3 opacity-70"></i>
                    <span>Tokens:</span>
                    <span class="font-medium tabular-nums text-gray-500 dark:text-gray-400">{{ (roundStats(index).input + roundStats(index).output).toLocaleString() }}</span>
                    <i data-lucide="info" class="w-3 h-3 opacity-40"></i>
                    <!-- Hover breakdown panel (desktop only) -->
                    <div class="hidden md:block absolute bottom-full left-0 mb-1.5 w-64 rounded-lg
                                bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700/60
                                text-gray-800 dark:text-gray-100 text-xs p-3 opacity-0 group-hover:opacity-100
                                transition-opacity pointer-events-none z-50 shadow-xl">
                      <div class="flex items-center justify-between mb-2">
                        <span class="font-semibold">{{ t('sessions.tokenBreakdown') || 'Token breakdown' }}</span>
                        <span class="opacity-70">{{ t('sessions.totalLabel') || 'Total' }}
                          <span class="font-semibold text-gray-900 dark:text-white tabular-nums ml-1">{{ (roundStats(index).input + roundStats(index).output).toLocaleString() }}</span>
                        </span>
                      </div>
                      <div class="flex items-center justify-between py-0.5">
                        <span class="flex items-center gap-1.5"><span class="inline-block w-2 h-2 rounded-[2px] bg-blue-500"></span>{{ t('sessions.tokensIn') }}</span>
                        <span class="font-semibold tabular-nums">{{ roundStats(index).input.toLocaleString() }}</span>
                      </div>
                      <div class="flex items-center justify-between py-0.5 pl-4 opacity-90">
                        <span class="flex items-center gap-1.5"><span class="inline-block w-2 h-2 rounded-[2px] bg-emerald-500"></span>{{ t('sessions.cacheHit') || 'Cache hit' }}</span>
                        <span class="tabular-nums">{{ roundStats(index).cacheRead.toLocaleString() }}</span>
                      </div>
                      <div class="flex items-center justify-between py-0.5 pl-4 opacity-90">
                        <span class="flex items-center gap-1.5"><span class="inline-block w-2 h-2 rounded-[2px] bg-red-400"></span>{{ t('sessions.cacheMiss') || 'Cache miss' }}</span>
                        <span class="tabular-nums">{{ roundStats(index).cacheMiss.toLocaleString() }}</span>
                      </div>
                      <div class="flex items-center justify-between py-0.5 pl-4 opacity-90">
                        <span class="flex items-center gap-1.5"><span class="inline-block w-2 h-2 rounded-[2px] bg-yellow-400"></span>{{ t('sessions.cacheWrite') || 'Cache write' }}</span>
                        <span class="tabular-nums">{{ roundStats(index).cacheCreation.toLocaleString() }}</span>
                      </div>
                      <div class="border-t border-gray-200 dark:border-gray-700/60 my-1.5"></div>
                      <div class="flex items-center justify-between py-0.5">
                        <span class="flex items-center gap-1.5"><span class="inline-block w-2 h-2 rounded-[2px] bg-purple-500"></span>{{ t('sessions.tokensOut') }}</span>
                        <span class="font-semibold tabular-nums">{{ roundStats(index).output.toLocaleString() }}</span>
                      </div>
                      <div class="flex items-center justify-between py-0.5 pl-4 opacity-90">
                        <span>{{ t('sessions.thinkingPart') || 'Thinking' }}</span>
                        <span class="tabular-nums">{{ roundStats(index).reasoning.toLocaleString() }}</span>
                      </div>
                      <div class="flex items-center justify-between py-0.5 pl-4 opacity-90">
                        <span>{{ t('sessions.replyPart') || 'Reply' }}</span>
                        <span class="tabular-nums">{{ Math.max(0, roundStats(index).output - roundStats(index).reasoning).toLocaleString() }}</span>
                      </div>
                      <div class="border-t border-gray-200 dark:border-gray-700/60 my-1.5"></div>
                      <div class="flex items-center justify-between py-0.5">
                        <span class="flex items-center gap-1.5"><i data-lucide="zap" class="w-3 h-3 text-yellow-500 dark:text-yellow-400"></i>{{ t('sessions.hitRate') }}</span>
                        <span class="font-semibold tabular-nums" :class="roundHitRate(roundStats(index)) > 50 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500 dark:text-red-400'">{{ roundHitRate(roundStats(index)) }}%</span>
                      </div>
                      <div class="flex h-1.5 rounded-full overflow-hidden bg-gray-200 dark:bg-gray-700/60 mt-1">
                        <div class="h-full bg-emerald-500" :style="{ width: roundHitRate(roundStats(index)) + '%' }"></div>
                        <div class="h-full bg-yellow-400" :style="{ width: roundWriteRate(roundStats(index)) + '%' }"></div>
                        <div class="h-full bg-red-400 flex-1"></div>
                      </div>
                      <div class="flex items-center gap-3 mt-1.5 opacity-80">
                        <span class="flex items-center gap-1"><span class="inline-block w-2 h-2 rounded-[2px] bg-emerald-500"></span>{{ t('sessions.legendHit') || 'Hit' }}</span>
                        <span class="flex items-center gap-1"><span class="inline-block w-2 h-2 rounded-[2px] bg-yellow-400"></span>{{ t('sessions.legendWrite') || 'Write' }}</span>
                        <span class="flex items-center gap-1"><span class="inline-block w-2 h-2 rounded-[2px] bg-red-400"></span>{{ t('sessions.legendMiss') || 'Miss' }}</span>
                      </div>
                    </div>
                  </span>
                  <span v-if="!isSessionRunning && roundStats(index)"
                        class="hidden sm:inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full
                               text-[11px] text-gray-400 dark:text-gray-500
                               select-none cursor-default"
                        :title="t('sessions.roundStatsTitle') || 'Tokens and wall-clock time for this round'">
                    <i data-lucide="clock" class="w-3 h-3 opacity-70"></i>
                    <span>{{ t('sessions.durationLabel') || 'Duration:' }}</span>
                    <span class="font-medium tabular-nums text-gray-500 dark:text-gray-400">{{ formatDuration(roundStats(index).seconds) }}</span>
                  </span>
                
              </div>
              </div>
            </div>
            <div v-if="showRunActivity()" class="session-run-activity" role="status" aria-live="polite">
              <span class="session-run-activity-copy">
                <span class="session-run-activity-label">
                  <transition name="activity-fade" mode="out-in">
                    <span :key="activityLabelIdx">{{ activityLabel }}</span>
                  </transition>
                </span>
                <span class="session-run-dots" aria-hidden="true"><span></span><span></span><span></span></span>
              </span>
            </div>

            <!-- Running tools render only on assistant.tool_calls cards (no duplicate strip). -->

            <!-- Tail pill only for retry/waiting states: normal streaming is
                 already shown by the toolbar status dot + per-card badges. -->
            <div v-if="liveRetryInfo && !interrupting" class="flex justify-center py-2 animate-fade-in">
              <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium
                           shadow-sm backdrop-blur-sm"
                    :class="liveRetryInfo
                      ? 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300 border border-amber-200/60 dark:border-amber-800/40'
                      : 'bg-primary/10 text-primary dark:bg-primary/20 dark:text-primary-light'">
                <span class="flex items-center gap-0.5">
                  <span class="w-1 h-1 rounded-full animate-bounce [animation-delay:0ms]"
                        :class="liveRetryInfo ? 'bg-amber-500' : 'bg-primary'"></span>
                  <span class="w-1 h-1 rounded-full animate-bounce [animation-delay:150ms]"
                        :class="liveRetryInfo ? 'bg-amber-500' : 'bg-primary'"></span>
                  <span class="w-1 h-1 rounded-full animate-bounce [animation-delay:300ms]"
                        :class="liveRetryInfo ? 'bg-amber-500' : 'bg-primary'"></span>
                </span>
                <span>{{ liveRetryLabel || t('sessions.runPillRunning') }}</span>
              </span>
            </div>
          </div>

        </div>

        <!-- Floating "scroll to latest" button: absolutely centered over the
             messages wrapper (NOT fixed to the viewport) so it never overlaps
             the toolbar or drifts when the file viewer pane is open. -->
        <button v-if="activeSession && (showScrollBtn || unreadCount > 0)"
                @click="scrollToLatest"
                :title="t('sessions.scrollToLatest')"
                class="absolute z-40 bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-1.5 pl-3 pr-3.5 h-10 rounded-full shadow-lg border bg-white/95 dark:bg-gray-800/95 backdrop-blur-sm transition-all hover:scale-105 hover:shadow-xl"
                :class="unreadCount > 0
                  ? 'border-primary/40 text-primary dark:text-primary-light'
                  : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400'">
          <i data-lucide="arrow-down" class="w-4 h-4 shrink-0"></i>
          <span v-if="unreadCount > 0" class="text-xs font-semibold whitespace-nowrap">
            {{ unreadCount }} {{ t('sessions.newMessages') }}
          </span>
          <span v-if="unreadCount > 0"
                class="absolute -top-1.5 -right-1.5 min-w-5 h-5 px-1 rounded-full bg-red-500 text-white text-[11px] font-semibold flex items-center justify-center">
            {{ unreadCount }}
          </span>
        </button>
        </div>

        <!-- Tool Confirmation Overlay -->
        <div v-if="pendingTools && pendingTools.length > 0"
             class="border-t border-gray-200 dark:border-gray-700 px-4 py-3 bg-yellow-50 dark:bg-yellow-900/20">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2 text-sm">
              <i data-lucide="shield-alert" class="w-4 h-4 text-yellow-600 dark:text-yellow-400"></i>
              <span class="text-yellow-800 dark:text-yellow-200 font-medium">{{ t('common.toolConfirm') || 'Tool Execution Required' }}</span>
            </div>
            <div class="flex items-center gap-2">
              <button @click="rejectTools"
                      class="px-3 py-1.5 text-xs font-medium bg-gray-200 dark:bg-gray-600 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-300 dark:hover:bg-gray-500 transition-colors">
                {{ t('common.reject') || 'Reject' }}
              </button>
              <button @click="approveTools"
                      class="px-3 py-1.5 text-xs font-medium bg-yellow-500 text-white rounded-lg hover:bg-yellow-600 transition-colors">
                {{ t('common.approve') || 'Allow Once' }}
              </button>
              <button v-if="pendingGrantable"
                      @click="approveToolsAlways"
                      class="px-3 py-1.5 text-xs font-medium bg-yellow-600 text-white rounded-lg hover:bg-yellow-700 transition-colors"
                      :title="t('common.approveAlwaysTip') || 'Allow for this session'">
                {{ t('common.approveAlways') || 'Allow for Session' }}
              </button>
              <!-- Folder grant (v1.16): file-op approvals are non-grantable by
                   tool name, so this is the only "always allow" that can
                   stick. Only offered when the call names a directory. -->
              <button v-if="pendingFolderGrant"
                      @click="approveToolsFolder"
                      class="px-3 py-1.5 text-xs font-medium bg-yellow-700 text-white rounded-lg hover:bg-yellow-800 transition-colors"
                      :title="(t('common.approveFolderTip') || 'Allow every operation under this folder for the session') + ': ' + pendingFolderGrant">
                {{ t('common.approveFolder') || 'Allow Folder' }}
              </button>
            </div>
          </div>
          <div class="mt-1.5 text-xs text-yellow-700 dark:text-yellow-300">
            <span v-for="(t, i) in pendingTools" :key="t.id">
              {{ i > 0 ? ', ' : '' }}{{ t.name || t.id }}
            </span>
          </div>
        </div>

        <!-- File changes traceability panel: sits ABOVE the session toolbar and
             the composer input. Hidden entirely while the session has no file
             mutations; the header toggles collapse/expand and the list shows
             up to 10 uniform 30px rows (max-height 302px incl. borders),
             scrolling beyond. -->
        <div v-if="activeSession && flatChangedFiles.length"
             class="session-changes border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
          <div class="flex items-center gap-2 pl-4 pr-3 py-1.5">
            <button type="button" @click="changesPanelOpen = !changesPanelOpen"
                    class="flex-1 min-w-0 flex items-center gap-1.5 text-xs font-medium text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white transition-colors select-none"
                    :title="changesPanelOpen ? (t('sessions.collapseChanges') || 'Collapse file changes') : (t('sessions.expandChanges') || 'Expand file changes')">
              <i :data-lucide="changesPanelOpen ? 'chevron-down' : 'chevron-right'" class="w-3.5 h-3.5 shrink-0"></i>
              <i data-lucide="git-compare-arrows" class="w-3.5 h-3.5 shrink-0 text-gray-400 dark:text-gray-500"></i>
              <span class="tabular-nums">{{ flatChangedFiles.length }}</span>
              <span>{{ t('sessions.filesChanged') || 'files' }}</span>
            </button>
            <!-- Batch actions: Keep (accept, drop log + snapshots) / Undo
                 (revert everything to before the conversation). Both allowed
                 while a run is active (real-time product decision); Undo also
                 needs at least one revertible entry. -->
            <div class="flex items-center gap-1.5 shrink-0">
              <button type="button" @click="acceptChanges"
                      :disabled="changesBusy"
                      class="px-2.5 py-1 rounded-md text-xs font-medium bg-primary text-white hover:bg-primary-dark disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                      :title="t('sessions.keepChangesTip') || 'Keep the changes (no longer revertible)'">
                {{ t('sessions.keepChanges') || 'Keep' }}
              </button>
              <button type="button" @click="revertAllChanges"
                      :disabled="changesBusy || !hasRevertibleChanges"
                      class="px-2.5 py-1 rounded-md text-xs font-medium border border-red-300 dark:border-red-800 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                      :title="t('sessions.undoChangesTip') || 'Revert everything to before the conversation'">
                {{ t('sessions.undoChanges') || 'Undo' }}
              </button>
            </div>
          </div>
          <div v-show="changesPanelOpen" class="px-3 pb-2">
            <!-- 302px = 10 rows x 30px + 2 x 1px border (box-sizing: border-box),
                 so exactly 10 whole rows are visible before scrolling. -->
            <div class="overflow-y-auto rounded-lg border border-gray-200/60 dark:border-gray-600/60 bg-gray-50 dark:bg-gray-700/30"
                 style="max-height: 302px">
              <div v-for="f in flatChangedFiles" :key="f.run_id + ':' + f.path"
                   class="flex items-center gap-1.5 h-[30px] px-2 text-xs border-b border-gray-200/40 dark:border-gray-600/40 last:border-b-0">
                <span class="px-1 py-0.5 rounded text-[10px] font-medium flex-shrink-0" :class="changeBadgeClass(f.change_type)">
                  {{ changeTypeLabel(f.change_type) }}
                </span>
                <span class="flex-1 min-w-0 truncate text-gray-700 dark:text-gray-300" :title="absFilePath(f.path)">{{ f.path }}</span>
                <span class="flex-shrink-0 tabular-nums whitespace-nowrap">
                  <span class="text-emerald-600 dark:text-emerald-400">+{{ f.additions || 0 }}</span>
                  <span class="text-red-500 dark:text-red-400 ml-0.5">-{{ f.deletions || 0 }}</span>
                </span>
                <!-- Per-row Keep (accept just this file) / Undo (revert just
                     this file to before the conversation). Keep always
                     available; Undo needs a revert source. -->
                <button type="button" @click="keepOne(f.path)" :disabled="changesBusy"
                        class="p-0.5 rounded text-gray-400 hover:text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-900/20 transition-colors flex-shrink-0"
                        :title="t('sessions.keepFileBtn') || 'Keep this file'">
                  <i data-lucide="check" class="w-3 h-3"></i>
                </button>
                <button v-if="f.revertible" type="button" @click="undoOne(f.path)" :disabled="changesBusy"
                        class="p-0.5 rounded text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors flex-shrink-0"
                        :title="t('sessions.revertFileBtn') || 'Revert this file'">
                  <i data-lucide="rotate-ccw" class="w-3 h-3"></i>
                </button>
                <span v-else class="w-4 flex-shrink-0" :title="t('sessions.revertUnavailable') || 'Not revertible (snapshot expired)'"></span>
              </div>
            </div>
          </div>
        </div>

        <!-- Toolbar: status | thinking pills | model | token | context | stop -->
        <div v-if="activeSession" ref="sessionToolbar"
             class="session-toolbar flex flex-wrap items-center justify-start gap-x-2 gap-y-1.5 px-4 py-2 bg-white dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700 text-xs select-none"
             :class="{ 'fm-compact': toolbarCompact }"
             @click.self="showModelPopover = false; showAgentPopover = false; showThinkingPopover = false; showApprovalPopover = false; showDisplayPopover = false; hoveredProvider = null">
${SESSION_TOOLBAR_SNIPPET}
        </div>

        <!-- Input Area (only with an active session; the welcome pane owns the input otherwise) -->
        <div v-if="activeSession" class="session-composer relative border-t border-gray-200 dark:border-gray-700 p-3 sm:p-4 bg-white dark:bg-gray-800"
             @dragover.prevent @drop.prevent="handleDrop">
          <!-- Slash-command popup: chat-scope commands are intercepted by the
               server BEFORE any LLM run. Catalog: GET /api/commands?scope=chat -->
          <div v-if="slashPopupVisible && slashFiltered.length"
               class="absolute bottom-full left-2 right-2 sm:left-4 sm:right-4 mb-1 rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 shadow-lg overflow-hidden"
               style="z-index: 60">
            <div class="max-h-56 overflow-y-auto py-1">
              <div v-for="(cmd, ci) in slashFiltered" :key="cmd.name"
                   @mousedown.prevent="confirmSlashCommand(cmd)"
                   @mouseenter="slashSelectedIndex = ci"
                   class="flex items-center gap-2 px-3 py-1.5 cursor-pointer text-xs"
                   :class="ci === slashSelectedIndex ? 'bg-primary/10' : 'hover:bg-gray-100 dark:hover:bg-gray-700/60'">
                <span class="font-mono text-primary shrink-0">/{{ cmd.name }}</span>
                <span v-if="cmd.kind === 'prompt'"
                      class="shrink-0 px-1 rounded bg-amber-100 dark:bg-amber-900/40 text-amber-600 dark:text-amber-400 text-[10px]">{{ t('sessions.promptCommandBadge') || 'prompt' }}</span>
                <span class="truncate text-gray-500 dark:text-gray-400 min-w-0">{{ cmd.description }}</span>
              </div>
            </div>
            <div class="px-3 py-1 border-t border-gray-100 dark:border-gray-700 text-[10px] text-gray-400 dark:text-gray-500">
              {{ t('sessions.slashPopupHint') || 'Enter/Tab to complete · Esc to dismiss' }}
            </div>
          </div>
          <!-- Image routing badge (three-state capability routing) -->
          <div v-if="visionRoute"
               class="flex items-center gap-1.5 mb-2 px-2.5 py-1 rounded-full text-[11px] w-max border"
               :class="visionRoute === 'native'
                 ? 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800'
                 : visionRoute === 'vision_sidecar'
                   ? 'bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300 border-indigo-200 dark:border-indigo-800'
                   : 'bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 border-amber-200 dark:border-amber-800'">
            <i :data-lucide="visionRoute === 'native' ? 'image' : visionRoute === 'vision_sidecar' ? 'eye' : 'eye-off'"
               class="w-3 h-3 shrink-0"></i>
            <span>{{ visionRouteLabel }}</span>
          </div>
          <!-- Vision model / downgrade notice (input-driven capability routing) -->
          <div v-if="visionNotice"
               class="flex items-start gap-2 mb-2 px-3 py-2 rounded-lg text-xs bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800">
            <i data-lucide="eye" class="w-3.5 h-3.5 mt-0.5 shrink-0"></i>
            <span class="flex-1">{{ visionNotice.message }}</span>
            <button @click="visionNotice = null" class="shrink-0 text-indigo-400 hover:text-indigo-600 dark:hover:text-indigo-200"
                    :title="t('common.close') || 'Close'">
              <i data-lucide="x" class="w-3.5 h-3.5"></i>
            </button>
          </div>
          <!-- Pending attachment chips (uploaded, not yet sent) -->
          <div v-if="pendingAttachments.length || isUploadingAttachment" class="flex flex-wrap gap-2 mb-2">
            <div v-for="(att, i) in pendingAttachments" :key="i"
                 class="inline-flex items-center gap-2 px-2 py-1 rounded-lg bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 text-xs text-gray-700 dark:text-gray-300">
              <img v-if="att.kind === 'image' && att._previewUrl"
                   :src="att._previewUrl"
                   @click.stop="openImagePreview(att)"
                   class="w-8 h-8 rounded object-cover cursor-zoom-in hover:opacity-80 transition-opacity"
                   :title="t('sessions.previewImage') || 'Preview image'">
              <i v-else data-lucide="file-text" class="w-4 h-4 text-primary shrink-0"></i>
              <span class="max-w-[130px] truncate">{{ att.name }}</span>
              <span class="text-gray-400 dark:text-gray-500 shrink-0">{{ formatBytes(att.size) }}</span>
              <button @click="removePendingAttachment(i)"
                      class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 shrink-0"
                      :title="t('common.remove') || 'Remove'">
                <i data-lucide="x" class="w-3.5 h-3.5"></i>
              </button>
            </div>
            <div v-if="isUploadingAttachment" class="inline-flex items-center gap-2 px-2 py-1 rounded-lg bg-gray-100 dark:bg-gray-700 text-xs text-gray-500">
              <span class="inline-block w-3.5 h-3.5 border-2 border-primary border-t-transparent rounded-full animate-spin"></span>
              {{ t('sessions.uploading') || 'Uploading...' }}
            </div>
          </div>
          <div class="flex gap-2 items-end">
            <button @click="toggleSlashPopup"
                    class="flex items-center justify-center w-8 h-8 mb-1 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors flex-shrink-0"
                    :class="{ 'bg-primary/10 text-primary dark:text-primary': slashPopupVisible }"
                    :title="t('sessions.insertCommand') || 'Insert command'">
              <i data-lucide="slash" class="w-4 h-4"></i>
            </button>
            <button @click="$refs.attachInput.click()"
                    class="flex items-center justify-center w-8 h-8 mb-1 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors flex-shrink-0"
                    :title="t('sessions.attachFiles') || 'Attach files'">
              <i data-lucide="paperclip" class="w-4 h-4"></i>
            </button>
            <input ref="attachInput" type="file" multiple class="hidden" @change="handleFileSelect">
            <textarea
                   ref="messageInput"
                   :placeholder="t('sessions.inputPlaceholder')"
                   class="flex-1 px-3 sm:px-4 py-2.5 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary resize-none"
                   style="min-height: 2.5rem"
                   rows="1"
                   @input="onComposerInput"
                   @paste="handlePaste"
                   @keydown="onComposerKeydown"></textarea>
            <button @click="sendMessage"
                    class="flex items-center justify-center w-10 h-10 bg-primary text-white rounded-lg hover:bg-primary-dark transition-colors flex-shrink-0">
              <i data-lucide="send" class="w-4 h-4"></i>
            </button>
            <button v-if="isSessionRunning && !interrupting" @click="cancelCurrentRun"
                    class="flex items-center justify-center w-10 h-10 bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 rounded-lg hover:bg-red-200 dark:hover:bg-red-900/50 transition-colors flex-shrink-0"
                    :title="t('sessions.cancelRun')">
              <i data-lucide="square" class="w-4 h-4"></i>
            </button>
          </div>
        </div>
          </div><!-- /chat column -->
        </div><!-- /content row -->
      </div>
      
      <!-- Right Sidebar: Session Info Panel.
           On phones/tablets polish.css turns .session-info-panel into an
           88%-wide right drawer (fixed + backdrop); it stays an inline
           w-72 column on desktop. -->
      <div v-if="rightSidebarExpanded && isMobile"
           class="session-info-panel-backdrop"
           @click="toggleRightSidebar"></div>
      <div v-if="rightSidebarExpanded"
           class="session-info-panel flex-shrink-0 w-72 border-l border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 transition-all duration-300 z-50">
        <div class="session-info-panel-header flex items-center justify-between px-4 h-12 border-b border-gray-200 dark:border-gray-700">
          <h3 class="font-medium text-gray-900 dark:text-white text-sm">{{ t('sessions.sessionInfo') }}</h3>
          <button @click="toggleRightSidebar"
                  class="flex items-center justify-center w-6 h-6 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors">
            <i data-lucide="x" class="w-4 h-4 text-gray-500 dark:text-gray-500"></i>
          </button>
        </div>
        <div class="p-4 space-y-5 overflow-y-auto h-[calc(100%-56px)] text-xs">

          <!-- Overview -->
          <div>
            <div class="flex items-center gap-1.5 text-gray-500 dark:text-gray-500 mb-2 uppercase tracking-wider text-xs font-semibold">
              <i data-lucide="info" class="w-3 h-3"></i>
              {{ t('sessions.overview') }}
            </div>
            <div class="space-y-2 bg-gray-50 dark:bg-gray-700/30 rounded-lg p-3">
              <div class="flex justify-between">
                <span class="text-gray-500 dark:text-gray-500">{{ t('sessions.rounds') || 'Rounds' }}</span>
                <span class="font-medium text-gray-900 dark:text-white tabular-nums">{{ userMessages.length }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-gray-500 dark:text-gray-500">{{ t('sessions.apiCalls') || 'API Calls' }}</span>
                <span class="font-medium text-gray-900 dark:text-white tabular-nums">{{ sessionStats.apiCalls || 0 }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-gray-500 dark:text-gray-500">{{ t('sessions.toolCalls') || 'Tool Calls' }}</span>
                <span class="font-medium text-gray-900 dark:text-white tabular-nums">
                  {{ sessionStats.toolCallsCount || 0 }}
                  <span v-if="sessionStats.toolErrorsCount > 0" 
                        class="ml-1 text-xs px-1 py-0.5 rounded bg-amber-100 dark:bg-amber-900/50 text-amber-700 dark:text-amber-300">
                    {{ sessionStats.toolErrorsCount }} {{ t('sessions.errors') || 'errors' }}
                  </span>
                </span>
              </div>
              <div class="flex justify-between">
                <span class="text-gray-500 dark:text-gray-500">{{ t('sessions.created') }}</span>
                <span class="text-gray-900 dark:text-white">{{ formatDate(sessionStats.createdAt) }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-gray-500 dark:text-gray-500">{{ t('sessions.updated') }}</span>
                <span class="text-gray-900 dark:text-white">{{ formatDate(sessionStats.updatedAt) }}</span>
              </div>
            </div>
          </div>

          <!-- Token Cache Stats -->
          <div>
            <div class="flex items-center gap-1.5 text-gray-500 dark:text-gray-500 mb-2 uppercase tracking-wider text-xs font-semibold">
              <i data-lucide="database" class="w-3 h-3"></i>
              {{ t('sessions.cacheStats') }}
            </div>
            <div class="space-y-2 bg-gray-50 dark:bg-gray-700/30 rounded-lg p-3">
              <div class="flex justify-between">
                <span class="text-gray-500 dark:text-gray-500">{{ t('sessions.tokensIn') }}</span>
                <span class="font-medium text-gray-900 dark:text-white tabular-nums">{{ formatTokens(sessionStats.tokenInput) }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-gray-500 dark:text-gray-500">{{ t('sessions.tokensOut') }}</span>
                <span class="font-medium text-gray-900 dark:text-white tabular-nums">{{ formatTokens(sessionStats.tokenOutput) }}</span>
              </div>
              <div class="border-t border-gray-200 dark:border-gray-600 my-1.5"></div>
              <div class="flex justify-between">
                <span class="text-emerald-600 dark:text-emerald-400">{{ t('sessions.cacheHitInput') }}</span>
                <span class="font-medium text-emerald-600 dark:text-emerald-400 tabular-nums">{{ formatTokens(sessionStats.cacheHit) }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-amber-600 dark:text-amber-400">{{ t('sessions.cacheMissInput') }}</span>
                <span class="font-medium text-amber-600 dark:text-amber-400 tabular-nums">{{ formatTokens(sessionStats.cacheMiss) }}</span>
              </div>
              <div class="flex justify-between items-center">
                <span class="text-gray-500 dark:text-gray-500">{{ t('sessions.hitRate') }}</span>
                <div class="flex items-center gap-2">
                  <div class="w-14 h-1.5 bg-gray-200 dark:bg-gray-600 rounded-full overflow-hidden">
                    <div class="h-full rounded-full transition-all duration-300"
                         :class="sessionStats.hitRate > 50 ? 'bg-emerald-500' : 'bg-amber-500'"
                         :style="{ width: sessionStats.hitRate + '%' }"></div>
                  </div>
                  <span class="font-medium text-gray-900 dark:text-white tabular-nums w-9 text-right">{{ sessionStats.hitRate.toFixed(0) }}%</span>
                </div>
              </div>
            </div>
          </div>

          <!-- Compression -->
          <div>
            <div class="flex items-center gap-1.5 text-gray-500 dark:text-gray-500 mb-2 uppercase tracking-wider text-xs font-semibold">
              <i data-lucide="layers" class="w-3 h-3"></i>
              {{ t('sessions.compression') }}
            </div>
            <div class="space-y-2 bg-gray-50 dark:bg-gray-700/30 rounded-lg p-3">
              <!-- Compress Now button (always visible) -->
              <button @click="compressSession"
                      :disabled="compressing"
                      class="w-full flex items-center justify-center gap-2 px-3 py-1.5 rounded bg-primary hover:bg-primary-dark disabled:bg-gray-400 disabled:hover:bg-gray-400 text-white text-sm font-medium transition-colors">
                <span v-if="compressing" class="inline-block w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
                <span>{{ compressing ? t('sessions.compressing') : t('sessions.compressNow') }}</span>
              </button>
              <!-- Compression stats (only if compressed) -->
              <div v-if="sessionStats.compressionCount > 0" class="pt-1 border-t border-gray-200 dark:border-gray-600">
                <div class="flex justify-between text-xs">
                  <span class="text-gray-500 dark:text-gray-500">{{ t('sessions.compressedTimes', { count: sessionStats.compressionCount }) }}</span>
                  <span class="font-medium text-gray-900 dark:text-white">{{ sessionStats.compressionCount }}</span>
                </div>
                <button v-if="sessionStats.compressedSummary"
                        @click="showCompressedSummary = !showCompressedSummary"
                        class="w-full mt-1.5 flex items-center justify-between px-2 py-1 rounded bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-600 transition-colors text-xs">
                  <span>{{ t('sessions.viewSummary') }}</span>
                  <i :data-lucide="showCompressedSummary ? 'chevron-up' : 'chevron-down'" class="w-2.5 h-2.5"></i>
                </button>
                <div v-if="showCompressedSummary && sessionStats.compressedSummary"
                     class="mt-1 px-2 py-1.5 text-xs text-gray-500 dark:text-gray-500 bg-white dark:bg-gray-700/50 rounded border border-gray-100 dark:border-gray-700 max-h-32 overflow-y-auto whitespace-pre-wrap">
                  {{ sessionStats.compressedSummary }}
                </div>
              </div>
            </div>
          </div>

          <!-- Export -->
          <div>
            <div class="flex items-center gap-1.5 text-gray-500 dark:text-gray-500 mb-2 uppercase tracking-wider text-xs font-semibold">
              <i data-lucide="download" class="w-3 h-3"></i>
              {{ t('sessions.export') }}
            </div>
            <button @click="exportSessionJSON"
                    class="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors text-xs font-medium">
              <i data-lucide="file-code" class="w-3.5 h-3.5"></i>
              {{ t('sessions.exportJson') }}
            </button>
          </div>
        </div>
      </div>

      <!-- Directory picker modal -->
      <directory-picker-modal :model-value="showDirPicker"
                              :title="t('sessions.selectWorkspace') || 'Select working directory'"
                              :roots="dirPicker.roots.filter(x => x.label)"
                              :path="dirPicker.path"
                              :path-input="dirPicker.pathInput"
                              :parent-path="dirPicker.parent"
                              :entries="dirPickerEntries"
                              :loading="dirPicker.loading"
                              :error="dirPicker.error"
                              :selected="dirPicker.selected"
                              :show-files="false"
                              :empty-text="t('sessions.noSubdirs') || 'No subdirectories'"
                              :confirm-label="t('sessions.useThisDir') || 'Use this folder'"
                              double-click-browse
                              @update:model-value="closeDirPicker"
                              @update:path-input="dirPicker.pathInput = $event"
                              @browse="browseDir($event)"
                              @go-up="goDirUp"
                              @select="dirPicker.selected = $event"
                              @confirm="confirmDirPicker">
        <template #footer-left>
          <button @click="clearWorkspace"
                  class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
            {{ t('sessions.resetWorkspace') || 'Reset to default' }}
          </button>
        </template>
      </directory-picker-modal>

      <!-- Image lightbox: click a thumbnail to preview full-size -->
      <div v-if="previewImage"
           class="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 md:p-8"
           @click.self="closeImagePreview">
        <div class="relative max-w-full max-h-full flex flex-col">
          <img :src="previewImage.src"
               :alt="previewImage.name"
               class="max-w-full max-h-[85vh] object-contain rounded-lg shadow-2xl bg-white dark:bg-gray-900">
          <div class="mt-2 flex items-center justify-between gap-3">
            <span class="text-xs text-gray-300 truncate">{{ previewImage.name }}</span>
            <div class="flex items-center gap-2 shrink-0">
              <a :href="previewImage.src"
                 target="_blank"
                 rel="noopener"
                 class="p-1.5 rounded hover:bg-white/15 text-gray-300 hover:text-white transition-colors"
                 :title="t('common.openInNewTab') || 'Open in new tab'">
                <i data-lucide="external-link" class="w-4 h-4"></i>
              </a>
              <button @click="closeImagePreview"
                      class="p-1.5 rounded hover:bg-white/15 text-gray-300 hover:text-white transition-colors"
                      :title="t('common.close') || 'Close'">
                <i data-lucide="x" class="w-5 h-5"></i>
              </button>
            </div>
          </div>
        </div>
      </div>

    </div>
  `,
  
  data() {
    return {
      // Initialised from the viewport so the phone never paints an open drawer
      // for a frame (checkMobile() used to close it only after mount).
      sidebarExpanded: window.innerWidth >= 1024,
      rightSidebarExpanded: false,
      // File manager panes: pane OPEN STATES are deliberately NOT persisted —
      // tree and viewer both start COLLAPSED on every reload (a half-open
      // panel taking over the sidebar/chat after a refresh is worse than one
      // click to reopen). Only the drag-resized sizes are kept.
      fmTreeOpen: false,
      fmTreeH: Math.max(120, Number(localStorage.getItem('boarui.fm.treeH')) || 260),
      fmViewerOpen: false,
      fmViewerW: Math.max(320, Number(localStorage.getItem('boarui.fm.viewerW')) || 560),
      toolbarCompact: false,
      isMobile: window.innerWidth < 1024,
      // Touch devices cannot hover, so hover-only row actions need a fallback.
      isTouch: !!(window.matchMedia && window.matchMedia('(hover: none)').matches),
      activeSession: null,
      editingSessionId: null,
      editingTitle: '',
      deletingSessionId: null,
      // Edit latest user message: message id in edit mode + draft text
      editingMessageId: null,
      editingContent: '',
      selectMode: false,
      selectedSessionIds: {},
      sessions: [],
      messages: [],
      // IDs currently being deleted locally — skip reload/auto-select races
      _pendingDeleteIds: null,
      // When true, loadSessions must not auto-pick first session (after intentional clear)
      _suppressAutoSelect: false,
      // Monotonic token so stale loadMessages responses are ignored
      _loadMessagesSeq: 0,
      isLoadingSessions: false,
      isLoadingMessages: false,
      loadingOlder: false,
      selectedModel: null,
      availableModels: [],
      // Plain object (not Set) so Vue tracks isRunning without full-list thrash
      runningSessionIds: Object.create(null),
      // Client sticky error (mirrors runningSessionIds). Survives loadSessions list replace
      // until server status or a new successful turn clears it.
      sessionErrorIds: Object.create(null),
      // Live failures in this page session: keep Retry enabled across soft loadMessages
      // (disk reload always maps _errorRetryable=false for historical stamps).
      sessionRetryableErrorIds: Object.create(null),
      // Provider auto-retry progress from WS run.retry (attempt / max / error)
      liveRetryInfo: null,
      // Vision notices: { message } shown above the input when the vision
      // capability auto-overrode the model or image blocks were stripped.
      visionNotice: null,
      // Three-state image routing of the current/last run:
      // 'native' | 'vision_sidecar' | 'text_only' (null = no image run).
      visionRoute: null,
      // Per-run file-change traceability (added/modified/deleted + unified diff).
      // changesBySession: sessionId -> newest-first list of run records.
      changesBySession: Object.create(null),
      // Collapse/expand state of the composer-adjacent file-changes panel.
      changesPanelOpen: true,
      // In-flight guard for the Keep/Undo batch buttons (double-click shield).
      changesBusy: false,
      // Uploaded-but-unsent attachment references (with local preview for images)
      pendingAttachments: [],
      isUploadingAttachment: false,
      // Slash-command popup (chat-scope commands, intercepted server-side
      // before any LLM run; catalog from GET /api/commands?scope=chat)
      slashCmds: [],
      slashCmdsLoaded: false,
      slashPopupVisible: false,
      slashFilter: '',
      slashSelectedIndex: 0,
      // Image lightbox: { src, name } or null. Click a message/pending image
      // thumbnail to preview it full-size in an overlay.
      previewImage: null,
      // Agent selector
      agentOptions: [],
      currentAgentName: 'default',
      // Tool confirmation
      pendingTools: null,
      pendingRunId: null,
      pendingConfirmationId: null,
      // Round wheel navigator: index of the user round currently in view
      currentRoundIndex: 0,
      wheelHover: false,
      // Index of the wheel dash under the cursor; null when not hovering.
      wheelHoverIndex: null,
      // Tool execution visualization
      toolExecuting: false,  // Tools mid-flight (drives running status on assistant cards)
      compressing: false,   // Manual compression in progress
      // Session stats for info bar and sidebar
      sessionStats: { messages: 0, toolCallsCount: 0, toolErrorsCount: 0, tokenInput: 0, tokenOutput: 0, model: '',
        cacheHit: 0, cacheMiss: 0, hitRate: 0,
        compressionCount: 0, compressedSummary: null,
        contextUsage: 0, tokenEstimates: null,
        createdAt: '', updatedAt: '', sessionId: '' },

      showDirPicker: false,
      dirPicker: {
        roots: [],
        path: '',
        pathInput: '',
        parent: null,
        entries: [],
        selected: '',
        loading: false,
        error: '',
      },
      // Thinking system (server authoritative, loaded from session on open)
      thinkingMode: 'none',
      // Effective thinking mapping for the selected model (provider wire shape,
      // supported levels, clamped "off" note). Loaded from /api/llm/thinking.
      thinkingCapabilities: null,
      _thinkingCapsSeq: 0,
      // Session tool-approval mode: 'deny' | 'ask' | 'allow' (server authoritative,
      // persisted as security_overrides.approval_mode = deny/on/off)
      approvalMode: 'ask',
      // Transcript display mode: 'detailed' | 'concise' | 'minimal' — a
      // browser-local view filter over process info (see DISPLAY_POLICIES).
      // Replaces the never-wired boarai_show_reasoning toggle; legacy key is
      // migrated once (any legacy value lands on the new default) and removed.
      displayMode: (() => {
        try {
          const saved = localStorage.getItem(DISPLAY_MODE_STORAGE_KEY);
          if (saved && DISPLAY_POLICIES[saved]) return saved;
          localStorage.setItem(DISPLAY_MODE_STORAGE_KEY, DEFAULT_DISPLAY_MODE);
          localStorage.removeItem(LEGACY_REASONING_STORAGE_KEY);
          return DEFAULT_DISPLAY_MODE;
        } catch (e) { return DEFAULT_DISPLAY_MODE; }
      })(),
      // Expanded process-fold segments (segment anchor message id) and
      // per-round "force flat" overrides (round anchor id -> true). Per-session
      // reading aids, persisted via _saveDisplayState / _loadDisplayState.
      foldOpenSegs: new Set(),
      roundFlatOverrides: {},
      _activeRunId: null,
      _runTimeoutId: null,
      _terminalRunIds: new Set(),
      _sessionVersion: 0,
      interrupting: false,
      _interruptingRunIds: new Set(),
      _creatingLock: false,
      // Prevent double-send / double-retry.
      // Note: Vue 3 does NOT expose data keys starting with _/$ to templates.
      sendGuard: false,
      // After HTTP create: wait for WS session.created to insert list row (avoid double flash)
      _pendingCreateId: null,
      models: [],
      // Capability definitions used to render multimodal icons on model rows
      // (keys match the flags stored per model in llm.yaml).
      modelCaps: [
        { key: 'image', icon: 'image', label: 'Image input' },
        { key: 'audio', icon: 'mic', label: 'Audio input' },
        { key: 'video', icon: 'video', label: 'Video input' },
        { key: 'image_gen', icon: 'image-plus', label: 'Image generation' },
        { key: 'video_gen', icon: 'clapperboard', label: 'Video generation' },
      ],
      channelIndicator: '',
      turnCount: 0,
      showModelPopover: false,
      showAgentPopover: false,
      showThinkingPopover: false,
      showApprovalPopover: false,
      showDisplayPopover: false,
      hoveredProvider: null,
      showCompressedSummary: false,
      // Rotating run-activity label index + its timer (see activityLabel).
      activityLabelIdx: 0,
      _activityLabelTimer: null,
      _pendingWorkspaceChangeMessage: null,
      // --- Message mutation batching ---
      // WS events fire in separate microtasks; each triggers Vue reactivity.
      // Batching all message mutations into one synchronous block prevents
      // Vue DOM diff corruption (insertBefore / emitsOptions on null nodes).
      _messageMutationQueue: [],
      _flushMessageMutationsScheduled: false,
      _messageObjectKeys: new WeakMap(),
      _messageObjectKeyCounter: 0,
      // Track last WS stream event time to avoid watchdog force-replacing
      // messages while streaming is active.
      _lastWsStreamEventTime: 0,
      // Floating "scroll to latest" button + unread new-message indicator
      showScrollBtn: false,
      unreadCount: 0,
      // True once a mutation burst already bumped unreadCount (reset on bottom)
      _unreadPending: false,
      // Sticky-bottom intent: auto-follow must survive content-growth bursts
      // (thinking panel expand, large chunks) that would otherwise look like
      // "user scrolled away" to a post-growth distance check. Only real user
      // scrolling (see onMessagesScroll) detaches; re-attaches at bottom.
      _stickToBottom: true,
      // --- seq-based incremental sync + cursor pagination ---
      // Highest seq already loaded for the active session (incremental watermark).
      _watermarkSeq: 0,
      // Whether older messages exist (drives upward-scroll lazy load via before_seq).
      _hasMore: false,
      // Guard against concurrent upward-scroll "load older" requests.
      _loadingOlder: false,
      // Tool start/finished WS events that arrived while loadMessages was
      // replacing the list. Dropping them left cards stuck on "running"
      // until run-end; queue and replay after the load settles instead.
      _deferredToolEvents: [],
      // Messages fetched per page (cold open + older pagination). 30 was so
      // small that one tool-heavy round filled the whole first page and the
      // latest USER message only appeared after an upward-scroll lazy load.
      _messagePageSize: 100,
    };
  },
  
  computed: {
    // Slash popup rows: name/alias prefix wins, then substring, then
    // description match (a typed query searches; a bare "/" browses all).
    slashFiltered() {
      const q = (this.slashFilter || '').toLowerCase();
      if (!q) return this.slashCmds;
      const rank = (c) => {
        const name = (c.name || '').toLowerCase();
        const aliases = (c.aliases || []).map(a => String(a).toLowerCase());
        if (name.startsWith(q)) return 0;
        if (aliases.some(a => a.startsWith(q))) return 1;
        if (name.includes(q)) return 2;
        if ((c.description || '').toLowerCase().includes(q)) return 3;
        return 9;
      };
      return this.slashCmds
        .map(c => ({ c, r: rank(c) }))
        .filter(x => x.r < 9)
        .sort((a, b) => a.r - b.r || String(a.c.name).localeCompare(String(b.c.name)))
        .map(x => x.c);
    },
    // Label for the three-state image-routing badge (P5 observability).
    visionRouteLabel() {
      const map = {
        native: this.t('sessions.visionRouteNative') || 'Images: native vision',
        vision_sidecar: this.t('sessions.visionRouteSidecar') || 'Images: vision sidecar',
        text_only: this.t('sessions.visionRouteTextOnly') || 'Images: omitted (text-only model)',
      };
      return map[this.visionRoute] || '';
    },
    // File-change records for the active session (newest first).
    activeChanges() {
      if (!this.activeSession) return [];
      return this.changesBySession[this.activeSession.id] || [];
    },
    // Flat, de-duplicated file list (newest occurrence per path wins) so the
    // composer changes panel shows files directly — no per-run collapse needed.
    flatChangedFiles() {
      const seen = new Set();
      const out = [];
      for (const rec of this.activeChanges) {
        for (const f of (rec.files_changed || [])) {
          if (!f || !f.path || seen.has(f.path)) continue;
          seen.add(f.path);
          out.push({ ...f, run_id: rec.run_id });
        }
      }
      return out;
    },
    // "Undo" gating mirrors the server: revert_session restores each path from
    // its EARLIEST recorded entry, so the batch is undoable while any earliest
    // entry keeps a revert source. Judging by the newest occurrence (the flat
    // panel list) can disagree — e.g. a later snapshot timeout on a file that
    // was revertible at first touch would grey out a working Undo.
    hasRevertibleChanges() {
      const earliest = new Map();
      for (const rec of this.activeChanges) {
        for (const f of (rec.files_changed || [])) {
          if (f && f.path) earliest.set(f.path, f); // newest→oldest: last wins
        }
      }
      for (const f of earliest.values()) {
        if (f.revertible) return true;
      }
      return false;
    },
    // "Allow for Session" / "Allow Globally" are only meaningful when the
    // backend says the grant will actually stick (grantable). Chained shell
    // commands re-evaluate their tail on every call, and confirm-tier
    // commands (v1.13.1) are deliberately non-grantable — hide the buttons
    // instead of offering a no-op.
    pendingGrantable() {
      const tools = this.pendingTools || [];
      if (!tools.length) return true;
      return tools.some((t) => t && t.grantable !== false);
    },

    /**
     * Directory a "always allow this folder" answer would cover: the first
     * pending call that names one (the backend derives it from the file
     * tool's path argument). Empty string hides the third button.
     */
    pendingFolderGrant() {
      const tools = this.pendingTools || [];
      for (const tool of tools) {
        if (tool && tool.folder_grant) return String(tool.folder_grant);
      }
      return '';
    },

    dirPickerEntries() {
      return (this.dirPicker.entries || []).map((e) => ({
        path: e.path,
        name: e.name,
        is_dir: e.type === 'dir',
      }));
    },

    sidebarClasses() {
      if (this.isMobile) {
        return [
          'fixed',
          // Start below the 60px top bar: the old inset-y-0 drawer covered the
          // bar and its own header (collapse + "New session") was painted
          // underneath it, invisible and untappable.
          'top-[60px]',
          'bottom-0',
          'left-0',
          'transform',
          this.sidebarExpanded ? 'translate-x-0' : '-translate-x-full',
          'w-72',
          'shadow-xl'
        ];
      }
      return [
        this.sidebarExpanded ? 'w-64' : 'w-12'
      ];
    },
    
    groupedSessions() {
      // Access _sessionVersion to create reactive dependency — when this counter
      // is incremented (e.g. after createSession), this computed is forced to
      // re-evaluate even if Vue's proxy mutation tracking fails.
      void this._sessionVersion;
      const pinned = this.sessions.filter(s => s.isPinned);
      const unpinned = this.sessions.filter(s => !s.isPinned);
      
      const groups = [];
      
      if (pinned.length > 0) {
        groups.push({
          id: 'pinned',
          label: this.t('sessions.pinned') || 'Pinned',
          sessions: pinned
        });
      }
      
      // For demo purposes, we'll just put all unpinned in "Today"
      if (unpinned.length > 0) {
        groups.push({
          id: 'today',
          label: this.t('sessions.today') || 'Today',
          sessions: unpinned
        });
      }
      
      return groups;
    },
    
    userMessages() {
      // A round is a conversation the user actually spoke: meta-only injections
      // ([BoarAi_System] context with an empty bubble) are not rounds and must
      // not occupy a wheel slot.
      return this.messages.filter((msg) => msg.role === 'user'
        && !msg.skill_card && !msg.skill_authoring
        && ((this.userBubbleContent(msg) || '').trim()
          || (msg.attachments && msg.attachments.length)));
    },

    // The single round shown in the hover picker: the dash under the cursor,
    // or the in-view round when the pointer is over the wheel but not on a
    // specific dash yet.
    hoveredRoundEntry() {
      const index = (this.wheelHoverIndex != null) ? this.wheelHoverIndex : this.currentRoundIndex;
      const msg = this.userMessages[index];
      return msg ? { msg, index } : null;
    },

    isSessionRunning() {
      // Depend on _sessionVersion so delete on runningSessionIds always re-renders
      // the "运行中" pill / stop button (plain-object key delete can miss tracking).
      void this._sessionVersion;
      const id = this.activeSession?.id;
      return !!(id && this.runningSessionIds[id]);
    },

    // Active presentation policy: templates read these booleans, never the
    // mode string (deepseek presentation-policy pattern).
    displayPolicy() {
      return DISPLAY_POLICIES[this.displayMode] || DISPLAY_POLICIES[DEFAULT_DISPLAY_MODE];
    },

    displayModeLabel() {
      const key = DISPLAY_MODE_LABEL_KEYS[this.displayMode];
      return (key && this.t(key)) || this.displayMode;
    },

    displayModeOptions() {
      return [
        { v: 'detailed', l: this.t('sessions.displayDetailed') || 'Detailed', tip: this.t('sessions.displayDetailedTip') || '' },
        { v: 'concise', l: this.t('sessions.displayConcise') || 'Concise', tip: this.t('sessions.displayConciseTip') || '' },
      ];
    },

    /**
     * Per-message render directives for the display-mode filter: an array
     * parallel to `messages`. entry.showProcess gates thinking/tool blocks,
     * entry.foldRow marks the segment's one-line summary button, entry.round
     * carries the round-header override button state.
     * Reactivity note: under detailed/concise the LIVE trailing round is
     * forced flat WITHOUT reading its message contents, so streaming chunk
     * mutations never invalidate this plan. Under minimal (quietLive) the
     * live round IS planned and per-chunk recompute is the accepted cost of
     * that tier. Structural changes and mode / override / fold toggles always
     * recompute it.
     */
    transcriptPlan() {
      const msgs = this.messages;
      const n = msgs.length;
      const plan = new Array(n).fill(FLAT_PLAN_ENTRY);
      const policy = this.displayPolicy;
      if (!policy.foldProcessRuns || n === 0) return plan;

      // O(n) pre-pass so the planner never re-scans `messages` per tool call
      // (getAttachedToolStatus does an O(n) find; too slow inside a computed).
      const toolMsgByCallId = new Map();
      const pairedToolIds = new Set();
      for (let i = 0; i < n; i++) {
        const m = msgs[i];
        if (!m) continue;
        if (m.role === 'tool' && m.tool_call_id) toolMsgByCallId.set(m.tool_call_id, m);
        else if (m.role === 'assistant' && Array.isArray(m.tool_calls)) {
          for (const tc of m.tool_calls) if (tc && tc.id) pairedToolIds.add(tc.id);
        }
      }
      const helpers = {
        isPairedTool: (m) => !!(m.tool_call_id && pairedToolIds.has(m.tool_call_id)),
        toolName: (m) => m.tool_name || '',
        // Reuse the card's name resolver; normalize its '?'/'unknown' to ''
        // so a missing name never leaks into the fold-row label.
        attachedName: (tc) => {
          const name = this.getToolName(tc);
          return name === '?' || name === 'unknown' ? '' : name;
        },
        toolFailed: (m) => this.getToolResultStatus(m) !== 'success',
        attachedFailed: (tc) => {
          if (!tc || !tc.id) return false;
          const res = toolMsgByCallId.get(tc.id);
          return res ? this.getToolResultStatus(res) !== 'success' : false;
        },
        // /\S/.test short-circuits at the first non-space char and copies
        // nothing — this runs on every streaming chunk under quietLive.
        hasVisibleText: (m) => !!(m.content && /\S/.test(m.content)) && !this.isPersistedErrorStamp(m),
        // pi getThinkingPreview: first line as a content scent, capped.
        thinkingPreview: (s) => String(s || '').trimStart().split(/\r?\n/, 1)[0].trimEnd().slice(0, 80),
        markTs: (seg, m) => {
          const ts = Date.parse(m.timestamp);
          if (Number.isNaN(ts)) return;
          if (seg.firstTs === null || ts < seg.firstTs) seg.firstTs = ts;
          if (seg.lastTs === null || ts > seg.lastTs) seg.lastTs = ts;
        },
      };

      const running = this.isSessionRunning;
      let i = 0;
      while (i < n) {
        if (msgs[i].role === 'user') { i += 1; continue; }
        let end = i;
        while (end < n && msgs[end].role !== 'user') end += 1;

        // Round header anchor: first assistant of the round (mirrors isRoundHeader).
        let headerIdx = i;
        while (headerIdx < end && msgs[headerIdx].role !== 'assistant') headerIdx += 1;
        if (headerIdx >= end) headerIdx = i;

        // Live tail stays flat (pi/omp isLiveTail): approvals, running cards
        // and streaming progress must remain visible and clickable.
        const isLive = running && end === n;
        if (isLive && !policy.quietLive) { i = end; continue; }

        // Rounds with a run-level error stay flat in every mode so the process
        // remains inspectable for debugging (deepseek turnProcessAlwaysOpen).
        // Skipped for quiet live rounds: a run-level error terminates the run,
        // so a live round cannot carry a terminal error stamp yet.
        let hasRunError = false;
        if (!isLive) {
          for (let j = i; j < end; j++) {
            const m = msgs[j];
            if (m && (m._error || this.isPersistedErrorStamp(m))) { hasRunError = true; break; }
          }
        }
        if (hasRunError) { i = end; continue; }

        const roundId = (msgs[headerIdx] && msgs[headerIdx].id) || `round_${i}`;
        const override = !!this.roundFlatOverrides[roundId];
        const segments = planRoundSegments(msgs, i, end, helpers);

        if (override) {
          // Forced flat by the round-header button, but keep the meta so the
          // button stays visible and can re-follow the global mode.
          plan[headerIdx] = { ...FLAT_PLAN_ENTRY, round: { roundId, hasFold: segments.length > 0, override: true } };
          i = end;
          continue;
        }
        if (!segments.length) {
          plan[headerIdx] = { ...FLAT_PLAN_ENTRY, round: { roundId, hasFold: false, override: false } };
          i = end;
          continue;
        }
        for (const seg of segments) {
          const segId = (msgs[seg.anchorIdx] && msgs[seg.anchorIdx].id) || `seg_${seg.anchorIdx}`;
          const open = this.foldOpenSegs.has(segId);
          for (const idx of seg.idxs) {
            plan[idx] = {
              flat: false,
              showProcess: open,
              // Fold row stays mounted while open: it is the collapse handle.
              foldRow: idx === seg.anchorIdx && policy.showFoldRow,
              segOpen: open,
              seg, segId, round: null,
            };
          }
        }
        plan[headerIdx] = { ...plan[headerIdx], round: { roundId, hasFold: true, override: false } };
        i = end;
      }
      return plan;
    },

    // Mirrors showRunActivity() so the row's appearance can be watched; Vue
    // cannot watch a method call from the template.
    runActivityVisible() {
      return this.showRunActivity();
    },

    // Rotating activity copy (user request: not one static phrase). Cycles
    // while the run is alive; runActivityVisible's watcher drives the timer.
    activityLabel() {
      const keys = [
        'sessions.activityThinking',
        'sessions.activityAnalyzing',
        'sessions.activityWorking',
        'sessions.activityAlmost',
      ];
      const key = keys[this.activityLabelIdx % keys.length];
      return this.t(key) || this.t('sessions.activityWorking');
    },

    liveRetryLabel() {
      const info = this.liveRetryInfo;
      if (!info || !info.attempt) return '';
      const attempt = info.attempt;
      const max = info.max_attempts || info.max || '?';
      return this.t('sessions.statusRetrying', { attempt, max })
        || `Retrying ${attempt}/${max}`;
    },

    workspaceDisplayPath() {
      if (!this.activeSession) return '';
      return this.activeSession.workspace_path || this.activeSession.workspacePath || '';
    },
    workspaceShortPath() {
      const p = this.workspaceDisplayPath;
      if (!p) return '';
      // Cross-platform short display: last 2 segments
      const parts = p.replace(/\\/g, '/').split('/').filter(Boolean);
      if (parts.length <= 2) return p;
      return '…/' + parts.slice(-2).join('/');
    },

    // Error flag for the currently active session.
    // Prefer sticky sessionErrorIds (survives list replace); fall back to row.hasError.
    sessionError() {
      void this._sessionVersion;
      if (!this.activeSession) return false;
      const id = this.activeSession.id;
      if (this.sessionErrorIds[id]) return true;
      const s = this.sessions.find(x => x.id === id);
      return !!(s && s.hasError);
    },

    statusLabel() {
      if (this.sessionError) return this.t('sessions.statusError');
      if (this.liveRetryLabel) return this.liveRetryLabel;
      // Tool approval pending: the run is suspended waiting for the user —
      // surface that instead of a generic "running".
      if (this.pendingTools) return this.t('sessions.statusWaiting');
      if (this.isSessionRunning) return this.t('sessions.statusRunning');
      return this.t('sessions.statusIdle');
    },
    statusDotClass() {
      if (this.sessionError) return 'bg-red-500';
      if (this.liveRetryInfo) return 'bg-amber-500';
      if (this.pendingTools) return 'bg-amber-500 animate-pulse';
      if (this.isSessionRunning) return 'bg-green-500';
      return 'bg-gray-400';
    },
    totalTokens() {
      return (this.sessionStats.tokenInput || 0) + (this.sessionStats.tokenOutput || 0);
    },
    contextPercent() {
      // Use actual context usage (prompt_tokens of latest LLM request) not cumulative tokens
      const usage = this.sessionStats.contextUsage || 0;
      if (usage === 0) return 0;
      const limit = this.contextLimit;
      const pct = Math.round((usage / limit) * 100);
      return Math.min(pct, 100);
    },
    contextLimit() {
      // Get max context window from selected model's metadata, fallback to 128K
      const m = this._findModelByName(this.selectedModel);
      if (m && m.context_window) return m.context_window;
      return 128000;
    },
    contextLimitFormatted() {
      const limit = this.contextLimit;
      if (limit >= 1000000) return (limit / 1000000).toFixed(1) + 'M';
      if (limit >= 1000) return (limit / 1000).toFixed(0) + 'K';
      return String(limit);
    },
    contextBarColor() {
      const pct = this.contextPercent;
      if (pct >= 90) return 'bg-red-500';
      if (pct >= 70) return 'bg-amber-500';
      if (pct >= 40) return 'bg-blue-500';
      return 'bg-emerald-500';
    },
    contextDetail() {
      return `${this.formatTokens(this.sessionStats.contextUsage || 0)} / ${this.contextLimitFormatted} (${this.contextPercent}%)`;
    },
    totalEstimatedTokens() {
      const te = this.sessionStats.tokenEstimates;
      return te && te.total ? te.total : 0;
    },
    // The REAL context footprint (last turn's prompt tokens). Prefer it over
    // the layer-estimate sum so the popover total matches the bar; fall back
    // to the estimate when no run has produced usage yet.
    contextTotalTokens() {
      const actual = this.sessionStats.contextUsage || 0;
      return actual > 0 ? actual : this.totalEstimatedTokens;
    },
    layerBreakdown() {
      const te = this.sessionStats.tokenEstimates;
      if (!te || !te.total) return [];
      const i18n = window.i18n ? window.i18n.t.bind(window.i18n) : (k) => k;
      const fmt = (n) => this.formatTokensSmall(n || 0);
      const layers = [
        { key: 'system',   label: i18n('sessions.contextSystem'),   tokens: te.system || 0,     color: '#a78bfa' }, // purple
        { key: 'tools',    label: i18n('sessions.contextTools'),    tokens: (te.tools || 0) + (te.tools_param || 0), color: '#f59e0b' }, // amber
        { key: 'pinned',   label: i18n('sessions.contextPinned'),   tokens: te.pinned || 0,    color: '#818cf8' }, // indigo
        { key: 'memory',   label: i18n('sessions.contextMemory'),   tokens: te.memory || 0,    color: '#34d399' }, // emerald
        { key: 'task_state',label: i18n('sessions.contextTaskState'),tokens: te.task_state || 0,color: '#22d3ee' }, // cyan
        { key: 'recent',   label: i18n('sessions.contextRecent'),   tokens: te.recent || 0,    color: '#60a5fa' }, // blue
        { key: 'current',  label: i18n('sessions.contextCurrent'),  tokens: te.current || 0,   color: '#f472b6' }, // rose
      ];
      // Reconcile to the ACTUAL total: the gap between the real context usage
      // and the summed layer estimates is surfaced as an "other" row so the
      // breakdown adds up to the number shown in the bar (no misleading total).
      const sum = layers.reduce((a, l) => a + (l.tokens || 0), 0);
      const total = this.contextTotalTokens || 0;
      const residual = Math.max(0, total - sum);
      if (residual > 0) {
        layers.push({ key: 'other', label: i18n('sessions.contextOther') || 'Other', tokens: residual, color: '#9ca3af' });
      }
      const denom = total || 1;
      const pct = (n) => Math.round((n || 0) / denom * 100);
      return layers
        .map(ly => ({ ...ly, pct: pct(ly.tokens), tokensFmt: fmt(ly.tokens), tokenClass: ly.tokens > 0 ? 'text-gray-800 dark:text-white' : 'text-gray-400 dark:text-gray-500' }))
        .filter(ly => ly.tokens > 0);
    },
    groupedModels() {
      const groups = {};
      for (const m of this.availableModels) {
        const provider = m.provider || (m.name ? m.name.split('/')[0] : 'default');
        if (!groups[provider]) groups[provider] = [];
        groups[provider].push(m);
      }
      return groups;
    },
    displayModelName() {
      if (!this.selectedModel) return this.t('sessions.autoModel');
      // Show short name if available, or last segment after /
      const m = this._findModelByName(this.selectedModel);
      return m?.short_name || this.selectedModel.split('/').pop() || this.selectedModel;
    },
    thinkingModeOptions() {
      const caps = this.thinkingCapabilities;
      const effective = (v) => (caps && caps.effective ? caps.effective[v] : null);
      const hasFields = (entry) => !!(entry && entry.fields && Object.keys(entry.fields).length);
      const withLevel = (v, label) => {
        const entry = effective(v);
        if (!entry || !entry.level || !hasFields(entry)) return label;
        // A real disable needs no suffix; only a clamped one does.
        if (entry.level === 'off') return label;
        if (caps && caps.supports_effort === false) return `${label} → on`;
        return `${label} → ${entry.level}`;
      };
      const levelOption = (v, label) => {
        const entry = effective(v);
        const unsupported = !!(caps && entry && !hasFields(entry));
        return {
          v,
          l: withLevel(v, label),
          display: label,
          disabled: unsupported,
          tip: unsupported ? this.t('sessions.thinkingUnsupportedTip') : '',
        };
      };
      // off sends nothing: either the model has no thinking control at all
      // (DeepSeek R1, Haiku — every mode is a no-op, so Off is unusable too), or
      // the wire merely has no "off" spelling while the levels still work.
      const offSendsNothing = !!(caps && !caps.can_disable && !caps.off_clamped);
      const offUnsupported = !!(offSendsNothing && !hasFields(effective('light')));
      let offTip = this.t('sessions.thinkingOffTip');
      if (offUnsupported) {
        offTip = this.t('sessions.thinkingUnsupportedTip');
      } else if (offSendsNothing) {
        offTip = this.t('sessions.thinkingOffNoFieldTip');
      }
      const autoTip = this.t('sessions.thinkingAutoTip');
      return [
        { v: 'none', l: this.t('sessions.thinkingNone'), display: this.t('sessions.thinkingNone'), tip: autoTip },
        {
          v: 'off',
          l: withLevel('off', this.t('sessions.thinkingOff')),
          display: this.t('sessions.thinkingOff'),
          disabled: offUnsupported,
          tip: offTip,
        },
        levelOption('light', this.t('sessions.thinkingLight')),
        levelOption('deep', this.t('sessions.thinkingDeep')),
        levelOption('ultra', this.t('sessions.thinkingUltra')),
      ];
    },
    thinkingModeLabel() {
      const hit = this.thinkingModeOptions.find((o) => o.v === this.thinkingMode);
      return hit ? (hit.display || hit.l) : this.thinkingMode;
    },
    approvalModeOptions() {
      return [
        { v: 'deny', l: this.t('sessions.approvalDeny'), c: 'text-red-600 dark:text-red-400' },
        { v: 'ask', l: this.t('sessions.approvalAsk'), c: 'text-amber-600 dark:text-amber-400' },
        { v: 'allow', l: this.t('sessions.approvalAllow'), c: 'text-emerald-600 dark:text-emerald-400' },
      ];
    },
    approvalModeLabel() {
      const hit = this.approvalModeOptions.find((o) => o.v === this.approvalMode);
      return hit ? hit.l : this.approvalMode;
    },
    approvalModeColor() {
      const hit = this.approvalModeOptions.find((o) => o.v === this.approvalMode);
      return hit ? hit.c : '';
    },
    allSessionsSelected() {
      if (this.sessions.length === 0) return false;
      return this.selectedCount === this.sessions.length;
    },
    selectedCount() {
      return Object.keys(this.selectedSessionIds).length;
    },
  },
  
  mounted() {
    this.checkMobile();
    window.addEventListener('resize', this.checkMobile);
    // Esc closes the toolbar popovers (model / agent selectors); without a
    // keyboard exit the only ways out are the trigger or a toolbar-body click.
    this._closePopoversOnEsc = (e) => {
      if (e.key !== 'Escape') return;
      this.showModelPopover = false;
      this.showAgentPopover = false;
      this.showThinkingPopover = false;
      this.showApprovalPopover = false;
      this.showDisplayPopover = false;
      this.hoveredProvider = null;
    };
    document.addEventListener('keydown', this._closePopoversOnEsc);
    // Close image lightbox with Escape.
    this._onPreviewKeydown = (e) => {
      if (e.key === 'Escape' && this.previewImage) this.closeImagePreview();
    };
    window.addEventListener('keydown', this._onPreviewKeydown);
    // language-changed listener + currentLanguage provided by global I18nMixin
    
    // Normalize input height using the same scrollHeight measurement that
    // autoResize uses, so the empty-state height matches post-input height.
    // Without this, rows="1" may compute a different height than scrollHeight,
    // causing the input area to jump on first keystroke.
    this.$nextTick(() => this.autoResize());
    
    // Toolbar compact mode: the chat column narrows when the file viewer is
    // open (or on small desktops). Watch the toolbar width directly — pane
    // drags change it without any window resize.
    this._toolbarRO = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect?.width ?? 0;
      this.toolbarCompact = width > 0 && width < 640;
    });
    this._watchToolbarWidth();

    // Load sessions, models, and agents from API
    this.loadSessions();
    this.loadModels();
    this.loadAgents();
    // Landing on the welcome screen: preselect the configured new-session
    // default approval mode (same source as Settings → Sessions).
    this._loadDefaultApprovalMode();

    // Listen for stream events via direct callback (avoids $watch deep reactivity issues)
    // Route a stream event to the assistant bubble owned by a server message_id.
    // One run may stream several LLM turns, each persisted as its own assistant
    // message; without routing every turn merged into the first bubble (only a
    // full reload separated them). With message_id we create/update per-turn
    // bubbles so the live view matches the disk structure.
    this._onStreamChunk = (sessionId, content, runId, messageId, seq) => {
      if (!this._isCurrentRunEvent(sessionId, runId)) return;
      if (this.isLoadingMessages) return;
      this._lastWsStreamEventTime = Date.now();
      this._setSessionRunning(sessionId, true);
      this._clearRunTimeout();
      // Successful stream progress clears retry banner
      this._clearRetryProgress(runId);
      this._scheduleMessageMutation(() => {
        const target = this._routeAssistantBubble(runId, messageId, seq);
        if (!target) return;
        // First content chunk = thinking is over for this turn → fold now.
        if (content) this._foldThinking(target);
        target.content = (target.content || '') + content;
        if (target._retryAttempt) {
          target._retryAttempt = null;
          target._retryMax = null;
          target._retryError = null;
        }
      });
    };

    this._onStreamThinking = (sessionId, thinking, runId, messageId, seq) => {
      if (!this._isCurrentRunEvent(sessionId, runId)) return;
      if (this.isLoadingMessages) return;
      this._lastWsStreamEventTime = Date.now();
      this._clearRunTimeout();
      // Thinking deltas are successful stream progress too: a recovered retry
      // that enters a thinking-only phase must drop the retry banner now,
      // not wait for the first content chunk.
      this._clearRetryProgress(runId);
      this._scheduleMessageMutation(() => {
        const target = this._routeAssistantBubble(runId, messageId, seq);
        if (!target) return;
        if (target._retryAttempt) {
          target._retryAttempt = null;
          target._retryMax = null;
          target._retryError = null;
        }
        // Start the timer on the first delta. Key off _thinkingStart (not the
        // text) so a soft reload that already hydrated `thinking` still gets a
        // fresh timer instead of a bogus fallback later.
        if (!target._thinkingStart) {
          target._thinkingStart = Date.now();
          // Fresh turn: re-attach inner thinking follow (user may have
          // detached while reading a previous turn's panel).
          target._thinkDetached = false;
        }
        if (!target.thinking) {
          target.thinking = '';
        }
        // Auto-expand thinking panel while streaming
        target._thinkOpen = true;
        target.thinking = (target.thinking || '') + thinking;
        target._thinkingComplete = false;
      });
    };
    
    this._onStreamDone = (sessionId, runId) => {
      this._clearRunTimeout();
      // A completed run must erase any false-positive client error stamp
      // (stall timeout / watchdog race) left on its bubbles mid-flight.
      this._healRunError(runId);
      if (!this._acceptTerminalRunEvent(sessionId, runId)) return;
      const isActiveRun = this._finalizeTerminalRun(sessionId, runId);
      if (!isActiveRun) {
        this._clearPendingRunStatusMessage(runId);
        return;
      }

      this.liveRetryInfo = null;
      // Terminal run: any leftover retry card must not survive into history.
      this._clearRetryAnnotations(runId);
      this._setSessionError(sessionId, false);
      this._scheduleMessageMutation(() => {
        // Auto-fold thinking panels when stream completes; settle every pending
        // turn so no stale pulsing cursor remains (multi-turn runs).
        const msgs = this.messages;
        for (let i = msgs.length - 1; i >= 0; i--) {
          if (msgs[i].role === 'assistant' && msgs[i].thinking && !msgs[i]._thinkingComplete) {
            this._finalizeThinkingTime(msgs[i]);
            this._lastThinkingMsgId = msgs[i].id;
            // Unconditional fold: the panel was auto-expanded while
            // streaming, so it auto-folds on completion regardless of the
            // display mode (the mode gates v-if visibility via the transcript
            // plan, not the fold state). Users can re-open it with one click.
            msgs[i]._thinkOpen = false;
          }
        }
      });
      this._clearPendingRunStatusMessage(runId);
      this.toolExecuting = false;
      if (this._runningToolIds) this._runningToolIds.clear();
      // Defer loadMessages so pending Vue patches from last chunks settle first.
      // 400 ms gives Vue enough time to finish DOM patching + server to persist.
      clearTimeout(this._streamDoneReloadTimer);
      this._streamDoneReloadTimer = setTimeout(() => {
        if (this.activeSession?.id === sessionId) {
          // Refresh token/cache stats after messages settle: the run's usage
          // is persisted by the backend at completion, so toolbar + sidebar
          // must re-fetch or they keep showing the pre-run zeros. Stats also
          // derive tool-call counts from this.messages → chain after reload.
          // force:true — the run's per-message stamps (run_usage / run_id /
          // run_finished_at) are persisted at completion, but an incremental
          // (since_seq) reload only APPENDS new messages and never patches the
          // already-streamed ones, leaving the per-round "Tokens" chip hidden
          // until a manual F5. A forced full reload re-maps every message.
          this.loadMessages(sessionId, { force: true })
            .catch(() => {})
            .then(() => {
              if (this.activeSession?.id === sessionId) {
                return this.loadSessionStats(sessionId);
              }
            })
            .catch(() => {});
        }
      }, 400);
    };
    
    this._onStreamError = (sessionId, error, runId) => {
      this._clearRunTimeout();
      const errText = error || this.t('sessions.llmError') || 'Run encountered an error';
      const stamp = () => {
        // Terminal failure always ends local running — even if this session is not focused.
        this._forceSessionIdle(sessionId, runId, { error: true });
        if (sessionId === this.activeSession?.id) this.liveRetryInfo = null;
        // Live failure in this page session — keep Retry after soft loadMessages
        if (sessionId) this.sessionRetryableErrorIds[sessionId] = true;
        if (sessionId !== this.activeSession?.id) {
          this.loadSessions();
          return;
        }

        // Show error in the assistant reply slot + retry button
        this._markAssistantError(runId, errText);
        this._clearPendingRunStatusMessage(runId);
        // Soft reload only when still idle (avoid wiping a new send/retry that started
        // in the 500ms window after the previous failure).
        clearTimeout(this._streamErrorReloadTimer);
        this._streamErrorReloadTimer = setTimeout(() => {
          if (this.activeSession?.id !== sessionId) return;
          if (this.runningSessionIds[sessionId] || this._activeRunId) return;
          // Usage up to the failure is persisted too — keep toolbar/sidebar
          // stats in sync after a failed turn, not only after success/cancel.
          // force:true so the persisted per-message run stamps (run_usage)
          // reach the already-streamed messages and the Tokens chip shows.
          this.loadMessages(sessionId, { force: true })
            .catch(() => {})
            .then(() => {
              if (this.activeSession?.id === sessionId) {
                return this.loadSessionStats(sessionId);
              }
            })
            .catch(() => {});
        }, 500);
      };
      // Trust the server over stray/duplicate/late error frames: if the run is
      // actually running or already completed/cancelled, stamping an error
      // would corrupt a healthy conversation — heal instead.
      if (runId && window.BoarAiStore?.api?.getRun) {
        window.BoarAiStore.api.getRun(runId)
          .then((r) => {
            const st = r?.status || r?.data?.status || null;
            if (st === 'running' || st === 'queued') return;
            if (st === 'completed' || st === 'cancelled') {
              this._healRunError(runId);
              this._forceSessionIdle(sessionId, runId, { error: false });
              return;
            }
            stamp();
          })
          .catch(() => stamp());
      } else {
        stamp();
      }
    };

    // Three-state capability routing: run.created carries model_source =
    // native | vision_sidecar | text_only for image-bearing runs. The badge
    // shows the route; sidecar / text_only additionally raise a notice.
    this._onRunCreated = (sessionId, info) => {
      if (!sessionId || sessionId !== this.activeSession?.id) return;
      const source = info?.model_source;
      this.visionRoute =
        (source === 'native' || source === 'vision_sidecar' || source === 'text_only')
          ? source : null;
      if (source === 'vision_sidecar') {
        this.visionNotice = {
          message: this.t('sessions.visionSidecar') ||
            'Image analyzed by the vision model; text is handled by the current model.',
        };
      } else if (source === 'text_only') {
        this.visionNotice = {
          message: this.t('sessions.visionTextOnly') ||
            'Current model is text-only and no vision model is configured; images were replaced by readable-path notes.',
        };
      }
    };

    // Provider rejected image_url blocks; executor stripped them and retried.
    this._onVisionDowngrade = (sessionId, info) => {
      if (!sessionId || sessionId !== this.activeSession?.id) return;
      this.visionNotice = {
        message: this.t('sessions.visionDowngrade') ||
          'Current model does not support images; image blocks were omitted for this request.',
      };
    };

    // Live usage: server pushes the run's CUMULATIVE LLM usage as each turn
    // completes — fold it onto the persisted base so sidebar + toolbar
    // counters (tokens in/out, cache hit, API calls) tick up mid-run instead
    // of only after the terminal usage record lands.
    this._onRunUsage = (sessionId, u) => {
      if (!sessionId || sessionId !== this.activeSession?.id) return;
      this._liveRunUsage = u;
      const b = this._statsBase || {};
      const input = (b.tokenInput || 0) + (u.input || 0);
      const cacheHit = (b.cacheHit || 0) + (u.cache_read || 0);
      this.sessionStats.tokenInput = input;
      this.sessionStats.tokenOutput = (b.tokenOutput || 0) + (u.completion || 0);
      this.sessionStats.cacheHit = cacheHit;
      this.sessionStats.cacheMiss = Math.max(0, input - cacheHit);
      this.sessionStats.hitRate = input > 0 ? (cacheHit / input * 100) : 0;
      this.sessionStats.apiCalls = (b.apiCalls || 0) + (u.turns || 0);
    };

    this._onStreamRetry = (sessionId, info) => {
      if (!sessionId || sessionId !== this.activeSession?.id) return;
      if (this.isLoadingMessages) return;
      const runId = info?.runId || null;
      if (!this._isCurrentRunEvent(sessionId, runId)) return;
      this._setSessionRunning(sessionId, true);
      this._setSessionError(sessionId, false);
      this._clearRunTimeout();
      this._startRunTimeout();
      const attempt = Number(info?.attempt) || 1;
      const maxAttempts = Number(info?.max_attempts) || 0;
      const errText = info?.error || '';
      this.liveRetryInfo = {
        attempt,
        max_attempts: maxAttempts,
        error: errText,
        delay_seconds: info?.delay_seconds || 0,
        runId,
      };
      this._lastWsStreamEventTime = Date.now();
      // Annotate the live assistant bubble so the card can show retry progress
      this._scheduleMessageMutation(() => {
        const msgs = this.messages;
        for (let i = msgs.length - 1; i >= 0; i--) {
          if (msgs[i].role === 'assistant' && (!msgs[i]._runId || !runId || msgs[i]._runId === runId)) {
            if (runId && !msgs[i]._runId) msgs[i]._runId = runId;
            msgs[i]._retryAttempt = attempt;
            msgs[i]._retryMax = maxAttempts;
            msgs[i]._retryError = errText;
            // Clear prior error paint while auto-retrying
            if (msgs[i]._error) {
              msgs[i]._error = false;
              msgs[i]._errorText = undefined;
              msgs[i]._errorRetryable = false;
            }
            break;
          }
        }
      });
      this._sessionVersion++;
      this.scrollToBottom(true);
    };

    this._onStreamCancelled = (sessionId, reason, runId) => {
      this._clearRunTimeout();
      if (!this._acceptTerminalRunEvent(sessionId, runId)) return;
      const isActiveRun = this._finalizeTerminalRun(sessionId, runId);

      this._removeEmptyAssistantMessage(runId);
      this._appendSystemMessage({
        id: this._runSystemMessageId(runId),
        content: this._interruptMessage(reason),
        interrupt: true,
      });
      if (isActiveRun) this.loadSessions();
      // Refresh token/cache stats after cancel — the backend now persists the
      // run's usage on the cancelled path, so reload it into the info bar.
      if (this.activeSession?.id === sessionId) {
        this.loadSessionStats(sessionId).catch(() => {});
      }
    };

    this._onToolPending = (sessionId, runId, tools, confirmationId) => {
      if (sessionId !== this.activeSession?.id) return;
      this.pendingTools = tools;
      this.pendingRunId = runId;
      this.pendingConfirmationId = confirmationId || '';
      // Auto-show confirmation via the tool overlay
    };

    // Tool execution started — attach tool_calls onto last assistant bubble only
    // (do NOT also render a second floating activeTools list — that caused duplicates)
    this._onToolStart = (sessionId, tools, runId, messageId, seq) => {
      if (sessionId !== this.activeSession?.id) return;
      if (this.isLoadingMessages) {
        this._deferredToolEvents.push(
          { kind: 'start', args: [sessionId, tools, runId, messageId, seq] });
        return;
      }
      this._lastWsStreamEventTime = Date.now();
      // Tool activity proves the run is alive — reset the stall timer.
      if (this._activeRunId) this._startRunTimeout();
      // Tool activity also proves a retry recovered (tool-first turn).
      this._clearRetryProgress(runId);
      this.toolExecuting = true;
      if (!this._runningToolIds) this._runningToolIds = new Set();
      const list = (tools || []).map(t => ({
        id: t.id,
        name: t.name,
        arguments: t.arguments || '',
        // keep OpenAI-style shape so getToolName/getToolArgs work
        function: t.function || {
          name: t.name,
          arguments: typeof t.arguments === 'string'
            ? t.arguments
            : JSON.stringify(t.arguments || {}),
        },
      }));
      for (const t of list) {
        if (t.id) this._runningToolIds.add(t.id);
      }
      this._scheduleMessageMutation(() => {
        // Attach tool calls to the exact turn bubble that requested them.
        let target = messageId
          ? this.messages.find(m => m.role === 'assistant' && m._serverMessageId === messageId)
          : null;
        target = target || this._routeAssistantBubble(runId, messageId, seq);
        if (!target) return;
        if (target._retryAttempt) {
          target._retryAttempt = null;
          target._retryMax = null;
          target._retryError = null;
        }
        // Tool calls starting = thinking is over for this turn → fold now.
        this._foldThinking(target);
        if (seq > 0 && target.seq == null) target.seq = seq;
        // Merge by id so re-broadcasts don't wipe partial state
        const prev = target.tool_calls || [];
        const byId = new Map(prev.map(tc => [tc.id, tc]));
        for (const tc of list) {
          if (tc.id) byId.set(tc.id, { ...byId.get(tc.id), ...tc });
          else prev.push(tc);
        }
        target.tool_calls = list.length
          ? (list.every(t => t.id) ? [...byId.values()] : [...prev, ...list.filter(t => !t.id)])
          : prev;
        // Sidebar "工具调用" counter ticks as soon as a batch starts.
        this._recountToolStats();
      });
      this._sessionVersion++;
    };

    // Tool execution finished — insert results; status comes from paired tool msgs
    this._onToolFinished = (sessionId, tools, toolResults, runId, messageId, seq) => {
      if (sessionId !== this.activeSession?.id) return;
      if (this.isLoadingMessages) {
        this._deferredToolEvents.push(
          { kind: 'finished',
            args: [sessionId, tools, toolResults, runId, messageId, seq] });
        return;
      }
      this._lastWsStreamEventTime = Date.now();
      if (this._activeRunId) this._startRunTimeout();
      this.toolExecuting = false;
      // Clear per-tool running markers (ids from tools list and results)
      if (this._runningToolIds) {
        for (const t of (tools || [])) {
          if (t?.id) this._runningToolIds.delete(t.id);
        }
        for (const tr of (toolResults || [])) {
          if (tr?.tool_call_id) this._runningToolIds.delete(tr.tool_call_id);
        }
        // If batch finished with empty lists, drop all mid-flight markers
        if ((!tools || !tools.length) && (!toolResults || !toolResults.length)) {
          this._runningToolIds.clear();
        }
      }
      // Real-time changes panel: ingest each file mutation the moment its
      // tool finishes, instead of waiting for the end-of-run run.diff.
      for (const tr of (toolResults || [])) {
        if (tr && tr.file_change) {
          this._ingestLiveFileChange(sessionId, runId, tr.file_change);
        }
      }
      this._scheduleMessageMutation(() => {
        // Insert tool result messages (run.done will call loadMessages to sync)
        if (toolResults && toolResults.length > 0) {
          for (let resultIndex = 0; resultIndex < toolResults.length; resultIndex++) {
            const tr = toolResults[resultIndex];
            // Still show error-only results so cards leave "running"
            const tcId = tr.tool_call_id || '';
            // Avoid duplicate tool rows if we already have this result (or after reconcile)
            if (tcId && this.messages.some(m => m.role === 'tool' && m.tool_call_id === tcId)) {
              // Update existing row content if we got a fuller payload later
              const existing = this.messages.find(m => m.role === 'tool' && m.tool_call_id === tcId);
              if (existing) {
                if (tr.file_change) existing.file_change = tr.file_change;
                if (tr.content && (!existing.content || existing.content.length < tr.content.length)) {
                  existing.content = tr.content;
                  existing.is_error = !!tr.is_error;
                  if (tr.error_kind) existing.error_kind = tr.error_kind;
                  if (tr.error_reason) existing.error_reason = tr.error_reason;
                }
              }
              continue;
            }
            const args = tr.arguments || (tr.command ? { command: tr.command } : null);
            this.messages.push({
              id: tcId || `tool_result_${this._activeRunId || 'unknown'}_${resultIndex}`,
              role: 'tool',
              content: tr.content || (tr.is_error ? (tr.error || 'Tool error') : ''),
              timestamp: new Date().toISOString(),
              tool_call_id: tcId,
              tool_name: tr.name || '',
              command: tr.command || null,
              arguments: args,
              is_error: !!tr.is_error,
              tool_result: tr.result || null,
              error_kind: tr.error_kind || null,
              error_reason: tr.error_reason || null,
              file_change: tr.file_change || null,
            });
          }
        }
        // Mark thinking complete on the turn that executed these tools so
        // "正在推理" does not stick; fall back to the last assistant.
        const msgs = this.messages;
        let done = false;
        if (messageId) {
          const t = msgs.find(m => m.role === 'assistant' && m._serverMessageId === messageId);
          if (t && t.thinking && !t._thinkingComplete) {
            this._finalizeThinkingTime(t);
            done = true;
          }
        }
        if (!done) {
          for (let i = msgs.length - 1; i >= 0; i--) {
            if (msgs[i].role === 'assistant' && msgs[i].thinking && !msgs[i]._thinkingComplete) {
              this._finalizeThinkingTime(msgs[i]);
              break;
            }
          }
        }
        // Sidebar "工具调用" counter ticks live with each finished batch.
        this._recountToolStats();
      });
      this._syncToolExecutingFlag();
      // Soft disk sync only if pairing still incomplete — avoid full list replace thrash
      if (sessionId && this._openToolCallIds().length > 0) {
        clearTimeout(this._toolFinishReloadTimer);
        this._toolFinishReloadTimer = setTimeout(() => {
          if (this.activeSession?.id !== sessionId) return;
          // Skip if still mid-stream (next LLM turn may have started)
          if (this.isSessionRunning && this.toolExecuting) return;
          this.loadMessages(sessionId).then(() => {
            this._syncToolExecutingFlag();
            if (this._runningToolIds) {
              for (const id of [...this._runningToolIds]) {
                if (this.messages.some(m => m.role === 'tool' && m.tool_call_id === id)) {
                  this._runningToolIds.delete(id);
                }
              }
            }
          }).catch(() => {});
        }, 800);
      }
    };

    // Live tool output — stream running-tool deltas (shell stdout/stderr
    // batches) into the executing card; the final result arrives with
    // tool.finished and loadMessages replaces the tail wholesale.
    this._onToolOutput = (sessionId, info, runId) => {
      if (sessionId !== this.activeSession?.id) return;
      if (this.isLoadingMessages || !info?.tool_call_id || !info.delta) return;
      this._lastWsStreamEventTime = Date.now();
      if (this._activeRunId) this._startRunTimeout();
      this._scheduleMessageMutation(() => {
        for (const m of this.messages) {
          if (m.role !== 'assistant' || !Array.isArray(m.tool_calls)) continue;
          const tc = m.tool_calls.find(t => t && t.id === info.tool_call_id);
          if (!tc) continue;
          const merged = (tc._liveOutput || '') + info.delta;
          // Keep only the visible tail bounded; the authoritative result is
          // still broadcast/persisted on tool.finished.
          tc._liveOutput = merged.length > 20000 ? merged.slice(-20000) : merged;
          break;
        }
      });
      this._sessionVersion++;
    };

    // Session deleted (local batch/single or another client)
    this._onSessionDeleted = (sessionId) => {
      // Local delete path already updated UI; ignore echo broadcasts to avoid
      // loadSessions → auto-select → getMessages 404 races mid-batch.
      if (this._pendingDeleteIds && this._pendingDeleteIds.has(sessionId)) {
        this.sessions = this.sessions.filter(s => s.id !== sessionId);
        return;
      }
      this.sessions = this.sessions.filter(s => s.id !== sessionId);
      if (this.activeSession?.id === sessionId) {
        this.activeSession = null;
        this.messages = [];
        this._suppressAutoSelect = true;
        this.$router.push({ name: 'sessions' });
      }
      this.loadSessions().catch(() => {});
    };

    // Session created — single source of truth for list insert (WS broadcast).
    // createSession() must NOT optimistically prepend; that double-flashes with this handler.
    this._onSessionCreated = (payload) => {
      const raw = payload?.data || payload || {};
      const session = this._mapServerSession(raw);
      if (!session?.id) {
        this.loadSessions();
        return;
      }
      const exists = this.sessions.some(s => s.id === session.id);
      if (!exists) {
        this.sessions = [session, ...this.sessions];
        this._sessionVersion++;
      } else {
        // Merge fresher fields without reordering flash
        const row = this.sessions.find(s => s.id === session.id);
        if (row) Object.assign(row, session);
      }

      const pending = this._pendingCreateId;
      if (pending && pending === session.id) {
        this._pendingCreateId = null;
        // Already selected after HTTP create — keep selection, sync fields
        if (this.activeSession?.id === session.id) {
          Object.assign(this.activeSession, session);
          if (session.model) this.selectedModel = session.model;
          this._applySessionThinkingMode(session);
          this._applySessionApprovalMode(session);
        } else {
          this.selectSession(session, true);
        }
      } else if (!this.activeSession) {
        // Created from another tab/channel
        this.selectSession(session, true);
      } else if (this.activeSession.id === session.id) {
        Object.assign(this.activeSession, session);
      }
      // Do not call loadSessions() here — replaces list and causes a second flash
    };

    // Session updated — apply title/fields in-place (do not rely only on full list reload)
    this._onSessionUpdated = (payload) => {
      const data = payload?.data || payload || {};
      const sid = data.session_id || data.id;
      const changes = data.changes || data;
      if (!sid) {
        this.loadSessions();
        return;
      }
      const newTitle = changes.title ?? data.title;
      if (newTitle != null && String(newTitle).trim()) {
        this._applySessionTitle(sid, String(newTitle).trim());
      }
      // Keep list in sync for other fields (message_count etc.)
      this.loadSessions();
    };

    // Run status changed — update session run badge + safety idle.
    // Store also fans out completed/failed/cancelled → stream handlers; if those
    // no-op (accept race), still clear sticky "运行中" here.
    this._onSessionRunStatus = (payload) => {
      const sid = payload?.session_id;
      const status = payload?.status;
      const runId = payload?.run_id;
      if (!sid) return;
      const s = this.sessions.find(x => x.id === sid);
      if (s) {
        s._runStatus = status;
        if (status === 'completed' || status === 'failed' || status === 'cancelled') {
          setTimeout(() => { if (s._runStatus === status) s._runStatus = null; }, 5000);
        }
      }
      if (status === 'failed' || status === 'error') {
        this._forceSessionIdle(sid, runId, { error: true });
      } else if (status === 'completed' || status === 'cancelled') {
        // Belt-and-suspenders: clear running even if onStreamDone already ran
        // or was rejected (duplicate terminal / session switch race).
        // Deferred one tick on purpose: the store calls onStreamDone right
        // after this status callback for the same frame. Forcing idle here
        // first marks the run terminal, so the stream handler rejects the
        // terminal event and skips its post-run forced reload — which is the
        // only path that pulls run_usage / run_finished_at into the round
        // chips after a successful run (tokens missing + "0s" until F5).
        setTimeout(() => {
          if (this.runningSessionIds[sid] || this._activeRunId === runId
              || (!runId && sid === this.activeSession?.id && this._activeRunId)) {
            this._forceSessionIdle(sid, runId || this._activeRunId, { error: false });
          }
          // Forcing idle above makes the stream handler reject the terminal
          // frame and skip ITS post-run forced reload — the only path that
          // pulls run_usage / run_finished_at into the round chips. Without
          // this second reload a cancelled/completed round shows no tokens or
          // duration until a manual refresh (most visible after interrupts).
          setTimeout(() => {
            if (sid === this.activeSession?.id && !this.isSessionRunning) {
              this.loadMessages(sid, { force: true }).catch(() => {});
            }
          }, 400);
        }, 0);
      }
    };

    // Another client edited the latest user message → drop the tail after keep_message_id
    // (idempotent for the editing client, which already truncated locally).
    this._onMessagesTruncated = (sessionId, keepMessageId) => {
      if (!sessionId || this.activeSession?.id !== sessionId) return;
      const idx = this.messages.findIndex(m => m.id === keepMessageId);
      if (idx >= 0) {
        this.messages = this.messages.slice(0, idx + 1);
      }
      if (this.editingMessageId) this.cancelEditMessage();
    };

    // Aggregated per-run file changes (run.diff): prepend a record so the
    // composer changes panel updates live as each run finishes.
    this._onRunDiff = (sessionId, payload) => {
      if (!sessionId || !payload) return;
      const rec = {
        run_id: payload.runId || `run_${Date.now()}`,
        ts: new Date().toISOString(),
        files_changed: payload.files_changed || [],
        unified_diff: payload.unified_diff || '',
        stats: payload.stats || {},
      };
      const prev = this.changesBySession[sessionId] || [];
      // Replace any same-run live record (built from per-tool file_change
      // events) with this authoritative end-of-run record; never duplicate.
      const rest = prev.filter((r) => r.run_id !== rec.run_id);
      // Cap the in-memory live list (the persisted log is capped server-side).
      this.changesBySession[sessionId] = [rec, ...rest].slice(0, 50);
    };

    // Change log dropped (keep) or consumed (revert-all), possibly from
    // another client: reload for the active session so partially-failed
    // reverts keep showing their retryable entries; drop the cache for any
    // other session (selectSession re-fetches on switch).
    this._onChangesCleared = (sessionId) => {
      if (!sessionId) return;
      if (this.activeSession?.id === sessionId) {
        this.loadChanges(sessionId);
      } else {
        delete this.changesBySession[sessionId];
      }
    };

    // File manager: a file was saved through /api/fs/write (this client or
    // another tab). Refresh loaded tree dirs and mark stale viewer tabs.
    this._onFsChanged = (path, mtime) => {
      if (!path) return;
      const tree = this.$refs.fileTree;
      if (tree && tree.onFsChanged) tree.onFsChanged(path);
      const viewer = this.$refs.fileViewer;
      if (viewer && viewer.onFsChanged) viewer.onFsChanged(path, mtime);
    };

    // Subscribe to stream events from store
    if (window.BoarAiStore) {
      window.BoarAiStore.onStreamChunk = this._onStreamChunk;
      window.BoarAiStore.onStreamDone = this._onStreamDone;
      window.BoarAiStore.onStreamError = this._onStreamError;
      window.BoarAiStore.onStreamCancelled = this._onStreamCancelled;
      window.BoarAiStore.onStreamThinking = this._onStreamThinking;
      window.BoarAiStore.onStreamRetry = this._onStreamRetry;
      window.BoarAiStore.onRunUsage = this._onRunUsage;
      window.BoarAiStore.onRunCreated = this._onRunCreated;
      window.BoarAiStore.onVisionDowngrade = this._onVisionDowngrade;
      window.BoarAiStore.onToolPending = this._onToolPending;
      window.BoarAiStore.onToolStart = this._onToolStart;
      window.BoarAiStore.onToolFinished = this._onToolFinished;
      window.BoarAiStore.onToolOutput = this._onToolOutput;
      window.BoarAiStore.onRunDiff = this._onRunDiff;
      window.BoarAiStore.onChangesCleared = this._onChangesCleared;
      window.BoarAiStore.onFsChanged = this._onFsChanged;
      window.BoarAiStore.onSessionDeleted = this._onSessionDeleted;
      window.BoarAiStore.onMessagesTruncated = this._onMessagesTruncated;
      window.BoarAiStore._onSessionCreated = this._onSessionCreated;
      window.BoarAiStore._onSessionUpdated = this._onSessionUpdated;
      window.BoarAiStore._onSessionRunStatus = this._onSessionRunStatus;
      // After WS drop/reconnect: re-bind stream + pull messages so tool cards leave "running"
      this._onWsReconnectedStore = () => {
        this._onWsReconnected();
      };
      window.BoarAiStore.onWsReconnected = this._onWsReconnectedStore;
    }
    
    // Click-outside handler for the toolbar popovers. It runs in the capture
    // phase, so it sees EVERY click before the trigger's own handler — which is
    // why clicks inside the toolbar (e.g. picking a provider on touch, where
    // there is no hover) must be ignored here. Without the guard the model
    // popover closed on the very tap that should reveal the model list.
    this._onDocumentClick = (e) => {
      const target = e.target;
      if (target && typeof target.closest === 'function' && target.closest('.session-toolbar')) return;
      this.showModelPopover = false;
      this.showAgentPopover = false;
      this.showThinkingPopover = false;
      this.showApprovalPopover = false;
      this.showDisplayPopover = false;
      this.hoveredProvider = null;
    };
    document.addEventListener('click', this._onDocumentClick, true);

    // Long tools: if tool.finished / run.done WS frames are lost, poll and reconcile
    this._runWatchdogTimer = setInterval(() => {
      this._runWatchdogTick().catch(() => {});
    }, 4000);
  },
  
  watch: {
    // Welcome screen (no active session): the pre-session toolbar must show
    // the CONFIGURED new-session default approval mode, not a hardcoded 'ask'
    // — otherwise the pill lies about what the next conversation will use.
    activeSession(v, old) {
      if (!v && old) this._loadDefaultApprovalMode();
    },
    // The run-status row lives after the message loop, so it grows the scroll
    // height after the mutation-driven scroll already ran. Re-pin to the bottom
    // when it appears (respecting a deliberate scroll-up) or the row is left
    // half-clipped under the composer toolbar.
    runActivityVisible(visible) {
      if (visible) {
        this.scrollToBottom(false);
        this._startActivityLabelRotation();
      } else {
        this._stopActivityLabelRotation();
      }
    },
    // The file-changes panel lives above the toolbar/composer, so it SHRINKS
    // the message viewport when run.diff lands (after the last mutation scroll
    // already ran). Re-pin so the newest bubble isn't left hidden behind the
    // new panel — sticky intent still guards deliberate scroll-up.
    'flatChangedFiles.length'(n, old) {
      if (n > (old || 0)) this.$nextTick(() => this.scrollToBottom(false));
    },
    // Keep the wheel highlight truthful right after loads/appends even when the
    // container never emits a scroll event (content fits without scrollbar).
    'messages.length'() {
      this.$nextTick(() => this._updateCurrentRound());
    },
    '$route.params.id': {
      handler(newId) {
        if (!newId) return;
        if (this._pendingDeleteIds && this._pendingDeleteIds.has(newId)) return;
        if (this.activeSession?.id === newId) return;
        if (this.sessions.length > 0) {
          const session = this.sessions.find(s => s.id === newId);
          if (session) {
            this.selectSession(session, false);
          }
        }
      },
      immediate: false
    },
    // The composer input (messageInput) is rendered under v-if="activeSession"
    // and is created after loadSessions() resolves — mounted's $nextTick only
    // normalized whatever input existed at that moment. Without this, the empty
    // composer uses the browser's rows="1" intrinsic height (a couple px taller
    // than the scrollHeight autoResize applies), so it visibly shrinks on the
    // first keystroke. Re-normalize whenever the session changes.
    activeSession() {
      this.$nextTick(() => {
        this.autoResize();
        this._watchToolbarWidth();
      });
    }
  },
  
  // NEVER call lucide.createIcons in updated() — it rewrites Vue-managed <i>
  // nodes mid-patch and causes Symbol(_vei) / nextSibling null crashes during stream.
  updated() {},
  
  beforeUnmount() {
    if (this._toolbarRO) this._toolbarRO.disconnect();
    window.removeEventListener('resize', this.checkMobile);
    document.removeEventListener('keydown', this._closePopoversOnEsc);
    if (this._runWatchdogTimer) {
      clearInterval(this._runWatchdogTimer);
      this._runWatchdogTimer = null;
    }
    this._clearRunTimeout();
    if (this._scrollTimer) {
      clearTimeout(this._scrollTimer);
      this._scrollTimer = null;
    }
    if (this._toolFinishReloadTimer) {
      clearTimeout(this._toolFinishReloadTimer);
      this._toolFinishReloadTimer = null;
    }
    if (this._streamDoneReloadTimer) {
      clearTimeout(this._streamDoneReloadTimer);
      this._streamDoneReloadTimer = null;
    }
    if (this._streamErrorReloadTimer) {
      clearTimeout(this._streamErrorReloadTimer);
      this._streamErrorReloadTimer = null;
    }
    if (window.BoarAiStore) {
      const store = window.BoarAiStore;
      const callbacks = [
        ['onStreamChunk', this._onStreamChunk],
        ['onStreamDone', this._onStreamDone],
        ['onStreamError', this._onStreamError],
        ['onStreamCancelled', this._onStreamCancelled],
        ['onStreamThinking', this._onStreamThinking],
        ['onStreamRetry', this._onStreamRetry],
        ['onRunUsage', this._onRunUsage],
        ['onRunCreated', this._onRunCreated],
        ['onVisionDowngrade', this._onVisionDowngrade],
        ['onToolPending', this._onToolPending],
        ['onToolStart', this._onToolStart],
        ['onToolFinished', this._onToolFinished],
        ['onToolOutput', this._onToolOutput],
        ['onRunDiff', this._onRunDiff],
        ['onChangesCleared', this._onChangesCleared],
        ['onSessionDeleted', this._onSessionDeleted],
        ['onMessagesTruncated', this._onMessagesTruncated],
        ['_onSessionCreated', this._onSessionCreated],
        ['_onSessionUpdated', this._onSessionUpdated],
        ['_onSessionRunStatus', this._onSessionRunStatus],
        ['onWsReconnected', this._onWsReconnectedStore],
      ];
      for (const [name, callback] of callbacks) {
        if (store[name] === callback) store[name] = null;
      }
      // Clear pending message mutations to avoid stale references
      if (this._messageMutationQueue) this._messageMutationQueue.length = 0;
      this._flushMessageMutationsScheduled = false;
    }
    document.removeEventListener('click', this._onDocumentClick, true);
    this._stopActivityLabelRotation();
    if (this._onPreviewKeydown) {
      window.removeEventListener('keydown', this._onPreviewKeydown);
      this._onPreviewKeydown = null;
    }
  },
  
  methods: {
    /** Stable v-for key — avoid Math.random ids so Vue can patch in place */
    messageKey(message, index) {
      if (message && message.id != null && message.id !== '') return String(message.id);
      if (message && message.tool_call_id) return `tool_${message.tool_call_id}`;
      if (message && typeof message === 'object') {
        let key = this._messageObjectKeys.get(message);
        if (!key) {
          this._messageObjectKeyCounter += 1;
          key = `msg_local_${this._messageObjectKeyCounter}`;
          this._messageObjectKeys.set(message, key);
        }
        return key;
      }
      return `msg_primitive_${index}_${String(message)}`;
    },

    /**
     * Schedule a message mutation to be applied in a batched synchronous block.
     * Prevents Vue DOM diff corruption when multiple WS events fire in rapid
     * succession and each triggers reactivity in separate microtasks.
     */
    _scheduleMessageMutation(fn) {
      this._messageMutationQueue.push(fn);
      if (!this._flushMessageMutationsScheduled) {
        this._flushMessageMutationsScheduled = true;
        // Use microtask to collect all mutations from the current event loop tick
        // before Vue's async scheduler processes them.
        Promise.resolve().then(() => this._flushMessageMutations());
      }
    },

    /**
     * Route a stream event to the assistant bubble owned by a server message_id.
     * A single run may stream several LLM turns, each persisted as its own
     * assistant message. With a message_id we update the matching bubble or
     * create a new per-turn bubble (placed after any tool rows); without one
     * (legacy events) we fall back to the last assistant of the run.
     */
    _routeAssistantBubble(runId, messageId, seq) {
      const msgs = this.messages;
      const runMatch = (m) => !m._runId || !runId || m._runId === runId;
      // Seq-based match: lets live events align to a disk bubble even when the
      // message_id differs (e.g. soft reload adopted a fresh id). Monotonic seq
      // is the stable cross-path cursor. seq<=0 means "no seq" (never match).
      const seqMatch = (m) => seq > 0 && m.seq != null && Number(m.seq) === Number(seq);
      if (messageId || seq > 0) {
        // 1) exact server message_id match (already-known turn bubble)
        for (let i = msgs.length - 1; i >= 0; i--) {
          const m = msgs[i];
          if (m.role === 'assistant' && messageId && m._serverMessageId === messageId) return m;
        }
        // 1.5) seq match (disk bubble aligned via monotonic cursor)
        for (let i = msgs.length - 1; i >= 0; i--) {
          const m = msgs[i];
          if (m.role === 'assistant' && seqMatch(m)) return m;
        }
        // 2) adopt the empty temp placeholder of this run (first turn)
        for (let i = msgs.length - 1; i >= 0; i--) {
          const m = msgs[i];
          if (m.role !== 'assistant' || !runMatch(m)) continue;
          const isPlaceholder = String(m.id || '').includes('_temp_ai')
            && !m.content && !m.thinking
            && !(m.tool_calls && m.tool_calls.length) && !m._error;
          if (isPlaceholder) {
            if (messageId) m._serverMessageId = messageId;
            if (seq > 0 && m.seq == null) m.seq = seq;
            return m;
          }
        }
        // 3) adopt the latest unclaimed run bubble (e.g. resume placeholder)
        for (let i = msgs.length - 1; i >= 0; i--) {
          const m = msgs[i];
          if (m.role !== 'assistant' || !runMatch(m)) continue;
          if (!m._serverMessageId) {
            if (messageId) m._serverMessageId = messageId;
            if (seq > 0 && m.seq == null) m.seq = seq;
            return m;
          }
        }
        // 4) new turn: create a fresh bubble (appended after any tool rows)
        const fresh = {
          id: messageId || `msg_${Date.now()}_stream_ai`,
          role: 'assistant',
          content: '',
          timestamp: new Date().toISOString(),
          seq: (seq > 0 ? seq : null),
          _runId: runId,
          _serverMessageId: messageId || null,
        };
        msgs.push(fresh);
        return fresh;
      }
      // legacy path (no message_id): last assistant of this run
      for (let i = msgs.length - 1; i >= 0; i--) {
        const m = msgs[i];
        if (m.role === 'assistant' && runMatch(m)) return m;
      }
      const fresh = {
        id: `msg_${Date.now()}_stream_ai`,
        role: 'assistant',
        content: '',
        timestamp: new Date().toISOString(),
        seq: null,
        _runId: runId,
        _serverMessageId: null,
      };
      msgs.push(fresh);
      return fresh;
    },

    /** Apply all queued message mutations in a single synchronous block. */
    _flushMessageMutations() {
      this._flushMessageMutationsScheduled = false;
      const batch = this._messageMutationQueue.splice(0);
      if (batch.length === 0) return;
      const beforeSig = this._messagesTailSig();
      for (const fn of batch) {
        try {
          fn();
        } catch (e) {
          console.warn('Message mutation error:', e);
        }
      }
      // Only count as "new message" if the visible tail actually changed
      if (beforeSig !== this._messagesTailSig()) {
        this._trackUnreadOnMutation();
      }
      // Single scroll at end of batch
      this._scrollToBottomThrottled();
    },

    /** Signature of the tail messages: new id / longer content / new row. */
    _messagesTailSig() {
      const msgs = this.messages;
      const n = msgs.length;
      if (!n) return '';
      const last = msgs[n - 1];
      return `${n}|${last.id}|${String(last.content || '').length}|${String(last.thinking || '').length}`;
    },

    /** Bump the unread counter only when the user is away from the bottom. */
    _trackUnreadOnMutation() {
      if (this._stickToBottom) {
        this.unreadCount = 0;
        this._unreadPending = false;
      } else {
        this.showScrollBtn = true;
        if (!this._unreadPending) {
          this._unreadPending = true;
          this.unreadCount = (this.unreadCount || 0) + 1;
          // Stamp first appearance so the pill stays visible >=3s (min show
          // time) instead of flashing when stick state flips mid-burst.
          if (!this._unreadShownAt) this._unreadShownAt = Date.now();
        }
      }
    },

    /** Throttle auto-scroll during high-frequency stream chunks */
    _scrollToBottomThrottled() {
      if (this._scrollTimer) return;
      this._scrollTimer = setTimeout(() => {
        this._scrollTimer = null;
        this.scrollToBottom(false);
        this._followThinkingBody();
      }, 80);
    },
    
    // Find model entry in availableModels by any name format:
    // "provider/model", "model", or last segment after "/"
    _findModelByName(modelName) {
      if (!modelName || !this.availableModels.length) return null;
      const short = modelName.includes('/') ? modelName.split('/').pop() : modelName;
      return this.availableModels.find(x =>
        (x.name || x) === modelName ||
        (x.short_name || '') === modelName ||
        (x.short_name || '') === short ||
        (typeof x.name === 'string' && x.name.endsWith('/' + short)) ||
        (typeof x.name === 'string' && x.name.split('/').pop() === short)
      ) || null;
    },

    // Robust equality for model menu highlight (session.model vs list entry)
    isSelectedModel(m) {
      if (!m || !this.selectedModel) return false;
      if (this.selectedModel === m.name || this.selectedModel === m.short_name) return true;
      const found = this._findModelByName(this.selectedModel);
      if (!found) return false;
      return found === m || found.name === m.name || found.short_name === m.short_name;
    },

    isSelectedProvider(provider) {
      if (!provider || !this.selectedModel) return false;
      const found = this._findModelByName(this.selectedModel);
      if (found) {
        const p = found.provider || (found.name ? found.name.split('/')[0] : '');
        return p === provider;
      }
      // Fallback: selectedModel itself may be "provider/model"
      if (this.selectedModel.includes('/')) {
        return this.selectedModel.split('/')[0] === provider;
      }
      return false;
    },

    /**
     * Only one toolbar popover may be open at a time. The document-level
     * click-outside handler ignores clicks inside .session-toolbar (so tapping
     * a provider/model on touch does not close the panel), which means the
     * triggers have to close their siblings themselves.
     */
    closeOtherToolbarPopovers(keep) {
      if (keep !== 'thinking') this.showThinkingPopover = false;
      if (keep !== 'approval') this.showApprovalPopover = false;
      if (keep !== 'display') this.showDisplayPopover = false;
      if (keep !== 'agent') this.showAgentPopover = false;
      if (keep !== 'model') {
        this.showModelPopover = false;
        this.hoveredProvider = null;
      }
    },

    toggleModelPopover() {
      this.closeOtherToolbarPopovers('model');
      this.showModelPopover = !this.showModelPopover;
      if (this.showModelPopover) {
        // Expand the provider that owns the current model so selection is visible
        const found = this._findModelByName(this.selectedModel);
        if (found) {
          this.hoveredProvider = found.provider || (found.name ? found.name.split('/')[0] : null);
        } else if (this.selectedModel && this.selectedModel.includes('/')) {
          this.hoveredProvider = this.selectedModel.split('/')[0];
        } else {
          const keys = Object.keys(this.groupedModels || {});
          this.hoveredProvider = keys[0] || null;
        }
      } else {
        this.hoveredProvider = null;
      }
    },

    renderMarkdown(content) {
      if (window.marked) {
        // Configure marked options
        window.marked.setOptions({
          breaks: true,
          gfm: true
        });
        const normalized = mdNormalizeTableDelimiters(String(content ?? ''));
        return mdEnhanceCodeBlocks(sanitizeMarkdownHtml(window.marked.parse(normalized)));
      }
      // Fallback remains text-only when the Markdown parser is unavailable.
      return String(content ?? '')
        .replace(/[&<>"']/g, (char) => ({
          '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        })[char])
        .replace(/\n/g, '<br>');
    },

    // Copy text reliably across browsers / contexts. Runs synchronously inside
    // the click gesture: the async Clipboard API is fire-and-forget with a
    // rejection fallback, so permissions or insecure contexts (e.g. LAN IP)
    // never silently break copying.
    _copyTextToClipboard(text) {
      if (!text) return false;
      const legacyCopy = () => {
        let ok = false;
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.top = '0';
        ta.style.left = '0';
        ta.style.width = '2em';
        ta.style.height = '2em';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        try {
          ta.setSelectionRange(0, ta.value.length);
          ok = document.execCommand('copy');
        } catch (e) {
          ok = false;
        }
        document.body.removeChild(ta);
        return ok;
      };
      if (navigator.clipboard && window.isSecureContext) {
        try {
          navigator.clipboard.writeText(text).then(
            () => {},
            () => { legacyCopy(); },
          );
          return true;
        } catch (e) {
          return legacyCopy();
        }
      }
      return legacyCopy();
    },

    copyMessage(message) {
      const text = message.content || '';
      this._copyTextToClipboard(text);
      message._copied = true;
      setTimeout(() => { message._copied = false; }, 1500);
    },

    // ── Attachment upload helpers (file picker / drag-drop / paste) ──
    async uploadFiles(fileList) {
      if (!fileList || !fileList.length) return;
      if (!this.activeSession) {
        window.BoarAiStore.actions.error(this.t('sessions.attachNoSession') || 'Start a session first');
        return;
      }
      const sessionId = this.activeSession.id;
      this.isUploadingAttachment = true;
      try {
        for (const file of fileList) {
          try {
            const ref = await window.BoarAiStore.api.uploadAttachment(sessionId, file);
            if (ref.kind === 'image') {
              ref._previewUrl = window.BoarAiStore.api.attachmentUrl(sessionId, ref.stored_name);
            }
            this.pendingAttachments.push(ref);
          } catch (err) {
            console.error('Attachment upload failed:', err);
            window.BoarAiStore.actions.error(err.message || (this.t('sessions.uploadFailed') || 'Upload failed'));
          }
        }
      } finally {
        this.isUploadingAttachment = false;
      }
    },

    handleFileSelect(event) {
      const files = Array.from(event.target.files || []);
      this.uploadFiles(files);
      event.target.value = ''; // allow re-picking the same file
    },

    handleDrop(event) {
      const files = Array.from(event.dataTransfer?.files || []);
      this.uploadFiles(files);
    },

    // Paste an image (or file) straight into the composer
    handlePaste(event) {
      const files = Array.from(event.clipboardData?.files || []);
      if (!files.length) return;
      event.preventDefault();
      this.uploadFiles(files);
    },

    removePendingAttachment(index) {
      this.pendingAttachments.splice(index, 1);
    },

    attachmentDownloadUrl(storedName) {
      if (!this.activeSession) return '#';
      return window.BoarAiStore.api.attachmentUrl(this.activeSession.id, storedName);
    },

    // Open the full-size image lightbox. `storedName` may be undefined for
    // in-flight optimistic user messages — fall back to the raw URL if present.
    openImagePreview(att) {
      if (!att) return;
      const storedName = att.stored_name || att.storedName;
      const src = storedName
        ? this.attachmentDownloadUrl(storedName)
        : (att._previewUrl || att.url || null);
      if (!src || src === '#') return;
      this.previewImage = { src, name: att.name || storedName || '' };
    },

    closeImagePreview() {
      this.previewImage = null;
    },

    formatBytes(size) {
      if (size == null || isNaN(size)) return '';
      if (size >= 1024 * 1024) return (size / (1024 * 1024)).toFixed(1) + ' MB';
      if (size >= 1024) return (size / 1024).toFixed(1) + ' KB';
      return size + ' B';
    },

    // True when this assistant message is the last reply of its round (the
    // following message is a user message or nothing). Rounds render ONE copy
    // button here copying the whole round (user prompt + all replies).
    // True on the round's LAST message (any role except user): the point where
    // the round footer (finished chip / copy / consumption pills) renders.
    // Deliberately role-agnostic — an interrupted or failed round ends on a
    // tool card, an error stamp or an interrupt banner, and those rounds must
    // show their stats too (the old assistant-with-text restriction hid them).
    // Only the IMMEDIATE successor decides: scanning the whole tail made every
    // message of the final round qualify and painted the footer once per row.
    isRoundEnd(index) {
      const msg = this.messages[index];
      if (!msg || msg.role === 'user') return false;
      const next = this.messages[index + 1];
      return !next || next.role === 'user';
    },

    // Aggregate this round's (user prompt → last reply) token consumption and
    // wall-clock span from the per-run metadata stamps persisted by the run
    // executor (run_usage / run_started_at / run_finished_at). A round may
    // contain several runs (failed attempt + retry) → sum distinct run ids.
    // Returns null when nothing is known (legacy rounds without stamps and
    // without usable timestamps) so the chip stays hidden.
    roundStats(index) {
      const msgs = this.messages;
      const last = msgs[index];
      if (!last) return null;
      let start = index;
      while (start > 0 && msgs[start - 1].role !== 'user') start--;
      // userIdx = the prompt that opened this round (fallback timer origin)
      const userIdx = (start > 0 && msgs[start - 1].role === 'user') ? start - 1 : start;
      let input = 0, output = 0, cacheRead = 0, cacheCreation = 0, reasoning = 0;
      let started = null, finished = null;
      const seenRuns = new Set();
      for (let i = start; i <= index; i++) {
        const m = msgs[i];
        const u = m._runUsage;
        if (u) {
          const rid = m._runId || `idx_${i}`;
          if (!seenRuns.has(rid)) {
            seenRuns.add(rid);
            input += Number(u.input) || 0;
            output += Number(u.output) || 0;
            cacheRead += Number(u.cache_read) || 0;
            cacheCreation += Number(u.cache_creation) || 0;
            reasoning += Number(u.reasoning) || 0;
          }
        }
        if (m._runStartedAt && (started === null || m._runStartedAt < started)) {
          started = m._runStartedAt;
        }
        if (m._runFinishedAt && (finished === null || m._runFinishedAt > finished)) {
          finished = m._runFinishedAt;
        }
      }
      // Wall-clock span the user cares about: prompt submission (the user
      // message's persisted timestamp) → the run's terminal stamp. Falls back
      // to run created→finished, then to legacy timestamp approximation.
      let seconds = null;
      const submitEpoch = Date.parse(msgs[userIdx].timestamp) / 1000;
      if (finished && !Number.isNaN(submitEpoch) && finished > submitEpoch) {
        seconds = finished - submitEpoch;
      } else if (started && finished && finished > started) {
        seconds = finished - started;
      }
      if (seconds === null) {
        // Legacy rounds (pre-stamp): approximate send → last reply from the
        // persisted message timestamps (origin = the round's user prompt).
        const t0 = Date.parse(msgs[userIdx].timestamp);
        const t1 = Date.parse(last.timestamp);
        if (!Number.isNaN(t0) && !Number.isNaN(t1) && t1 > t0) {
          seconds = (t1 - t0) / 1000;
        }
      }
      const hasTokens = input > 0 || output > 0;
      const hasDuration = seconds !== null && seconds > 0;
      if (!hasTokens && !hasDuration) return null;
      const cacheMiss = Math.max(0, input - cacheRead);
      return {
        input, output, cacheRead, cacheMiss, cacheCreation, reasoning,
        seconds: seconds || 0, hasTokens,
      };
    },

    roundHitRate(st) {
      if (!st || !st.input) return 0;
      return Math.min(100, Math.round((st.cacheRead / st.input) * 100));
    },

    roundWriteRate(st) {
      if (!st || !st.input) return 0;
      return Math.min(100 - this.roundHitRate(st), Math.round((st.cacheCreation / st.input) * 100));
    },

    formatDuration(sec) {
      const s = Math.max(0, Math.round(Number(sec) || 0));
      if (s < 60) return `${s}s`;
      const m = Math.floor(s / 60);
      if (m < 60) return `${m}m ${s % 60}s`;
      const h = Math.floor(m / 60);
      return `${h}h ${m % 60}s`;
    },

    // Timestamp chip: "HH:MM" today, "MM-DD HH:MM" earlier this year,
    // "YYYY-MM-DD HH:MM" for older sessions.
    timeChipLabel(epoch) {
      if (!Number.isFinite(epoch)) return '';
      const d = new Date(epoch);
      const pad = (n) => String(n).padStart(2, '0');
      const now = new Date();
      const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
      if (d.toDateString() === now.toDateString()) return time;
      const date = d.getFullYear() === now.getFullYear()
        ? `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
        : `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
      return `${date} ${time}`;
    },

    // "Sent at" chip on a user bubble: the persisted message timestamp.
    messageTimeLabel(message) {
      const epoch = Date.parse(message?.timestamp || '');
      return Number.isNaN(epoch) ? '' : this.timeChipLabel(epoch);
    },

    messageTimeTitle(message) {
      const epoch = Date.parse(message?.timestamp || '');
      return Number.isNaN(epoch) ? '' : new Date(epoch).toLocaleString();
    },

    // "Run finished at" chip on the round's last reply. run_finished_at is the
    // terminal stamp the executor writes onto the run's last assistant
    // message; the footer can now sit on a later tool message (interrupted
    // rounds), so scan the whole round for the newest stamp. Legacy rounds
    // fall back to the message timestamp once the session is idle.
    _roundFinishedEpoch(index) {
      const msgs = this.messages;
      let start = index;
      while (start > 0 && msgs[start - 1].role !== 'user') start--;
      let finished = 0;
      for (let i = start; i <= index; i++) {
        const f = Number(msgs[i] && msgs[i]._runFinishedAt);
        if (Number.isFinite(f) && f > finished) finished = f;
      }
      if (finished > 0) return finished * 1000;
      if (this.isSessionRunning) return null;
      const parsed = Date.parse(msgs[index]?.timestamp || '');
      return Number.isNaN(parsed) ? null : parsed;
    },

    roundFinishedLabel(index) {
      const epoch = this._roundFinishedEpoch(index);
      return epoch === null ? '' : this.timeChipLabel(epoch);
    },

    roundFinishedTitle(index) {
      const epoch = this._roundFinishedEpoch(index);
      return epoch === null ? '' : new Date(epoch).toLocaleString();
    },

    // Copy the displayed text of the whole round: the user prompt followed by
    // every assistant reply in that round (thinking / tool cards excluded).
    copyRound(index) {
      const msgs = this.messages;
      let start = index;
      while (start > 0 && msgs[start - 1].role !== 'user') start--;
      const parts = [];
      for (let i = start; i <= index; i++) {
        const m = msgs[i];
        if (m.role === 'user' && m.content) {
          parts.push(m.content);
        } else if (m.role === 'assistant' && m.content) {
          parts.push(m.content);
        }
      }
      const text = parts.join('\n\n');
      this._copyTextToClipboard(text);
      const last = msgs[index];
      if (last) {
        last._copied = true;
        setTimeout(() => { last._copied = false; }, 1500);
      }
    },

    autoResize() {
      // Welcome pane and composer each own one input ref; normalize whichever is live.
      const el = this.$refs.messageInput || this.$refs.welcomeInput;
      if (!el) return;
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 120) + 'px';
    },

    autoResizeEdit() {
      // Edit pane textarea grows with content (bounded).
      const ref = this.$refs.editInput;
      const el = Array.isArray(ref) ? ref[0] : ref;
      if (!el) return;
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 240) + 'px';
    },

    // ==================== Slash-command popup ====================
    // Chat-scope commands are intercepted by the server BEFORE any LLM run
    // (handler kind) or expanded into a prompt (prompt kind). The popup only
    // completes the name; sending stays the normal sendMessage path.

    // NOTE: template-facing methods must NOT start with "_" — Vue 3's
    // runtime-compiled render (`with(_ctx)`) treats reserved-prefix keys as
    // absent and falls through to a global ReferenceError.
    onComposerInput() {
      this.autoResize();
      this._updateSlashPopup();
    },

    _updateSlashPopup() {
      const input = this.$refs.messageInput;
      const value = input ? input.value : '';
      // Only a leading "/token" still being typed (single token, no space).
      const m = /^\/([A-Za-z0-9_-]*)$/.exec(value);
      if (!m) {
        this.slashPopupVisible = false;
        return;
      }
      this.slashFilter = m[1];
      this.slashSelectedIndex = 0;
      this.slashPopupVisible = true;
      if (!this.slashCmdsLoaded) {
        this.slashCmdsLoaded = true;
        window.BoarAiStore.api.getChatCommands().then(cmds => {
          this.slashCmds = Array.isArray(cmds) ? cmds : [];
        });
      }
    },

    onComposerKeydown(e) {
      if (this.slashPopupVisible && this.slashFiltered.length) {
        const n = this.slashFiltered.length;
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          this.slashSelectedIndex = (this.slashSelectedIndex + 1) % n;
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          this.slashSelectedIndex = (this.slashSelectedIndex - 1 + n) % n;
          return;
        }
        if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey)) {
          e.preventDefault();
          this.confirmSlashCommand(this.slashFiltered[this.slashSelectedIndex]);
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          this.slashPopupVisible = false;
          return;
        }
      }
      // Same semantics as the previous @keydown.enter.exact.prevent binding.
      if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        this.sendMessage();
      }
    },

    confirmSlashCommand(cmd) {
      if (!cmd) return;
      const input = this.$refs.messageInput;
      if (input) {
        input.value = `/${cmd.name} `;
        input.focus();
        this.autoResize();
      }
      this.slashPopupVisible = false;
      this.slashFilter = '';
    },

    // Toolbar "/" button: browse the command catalog without typing a slash.
    // Opens the SAME popup the composer's leading "/" typing drives; picking
    // a row lands "/cmd " in the input via confirmSlashCommand.
    toggleSlashPopup() {
      if (this.slashPopupVisible) {
        this.slashPopupVisible = false;
        this.slashFilter = '';
        return;
      }
      this.slashFilter = '';
      this.slashSelectedIndex = 0;
      this.slashPopupVisible = true;
      if (!this.slashCmdsLoaded) {
        this.slashCmdsLoaded = true;
        window.BoarAiStore.api.getChatCommands().then(cmds => {
          this.slashCmds = Array.isArray(cmds) ? cmds : [];
        });
      }
      this.$nextTick(() => {
        const el = this.$refs.messageInput;
        if (el) el.focus();
      });
    },

    // True for the one assistant message that owns the round's avatar + name.
    // The header is a label for the round, so it belongs at the top of the
    // round (the first assistant message after a user prompt), not attached to
    // the reply text that may come several rows later behind thinking and tool
    // cards. One label per round is enough; the rows under it share it.
    isRoundHeader(index) {
      const msgs = this.messages;
      const msg = msgs[index];
      if (!msg || msg.role !== 'assistant') return false;
      for (let i = index - 1; i >= 0; i--) {
        const prev = msgs[i];
        if (prev.role === 'user') return true;
        if (prev.role === 'assistant') return false;
      }
      return true;
    },

    // Display name for an assistant reply: the per-message agent snapshot wins;
    // fall back to the session's current agent, then the generic label.
    messageDisplayName(message) {
      if (message?.agent_name) return message.agent_name;
      if (this.activeSession?.agent_name) return this.activeSession.agent_name;
      return this.t('sessions.assistantName') || 'Assistant';
    },

    // Only the latest user message can be edited (backend enforces the same rule).
    isLatestUserMessage(message) {
      if (!message || message.role !== 'user') return false;
      for (let i = this.messages.length - 1; i >= 0; i--) {
        if (this.messages[i].role === 'user') return this.messages[i].id === message.id;
      }
      return false;
    },

    startEditMessage(message) {
      this.editingMessageId = message.id;
      this.editingContent = message.content || '';
      this.$nextTick(() => {
        const ref = this.$refs.editInput;
        const el = Array.isArray(ref) ? ref[0] : ref;
        if (el) {
          el.focus();
          el.setSelectionRange(el.value.length, el.value.length);
        }
      });
    },

    cancelEditMessage() {
      this.editingMessageId = null;
      this.editingContent = '';
    },

    async submitEditMessage() {
      const content = (this.editingContent || '').trim();
      const messageId = this.editingMessageId;
      if (!content || !messageId) return;
      if (this.sendGuard) return;
      this.sendGuard = true;
      const sessionId = this.activeSession?.id;
      try {
        if (!sessionId) return;

        // Cancel any live run first so the disk reload below is authoritative
        // and the tail being replaced is not still streaming.
        if (this.isSessionRunning && this._activeRunId) {
          await this._interruptCurrentRun();
          this.interrupting = false;
        }

        // Resolve an optimistic temp id (message sent moments ago, not yet reloaded)
        let targetId = messageId;
        if (String(messageId).includes('_temp')) {
          await this.loadMessages(sessionId, { force: true });
          let latest = null;
          for (let i = this.messages.length - 1; i >= 0; i--) {
            if (this.messages[i].role === 'user') { latest = this.messages[i]; break; }
          }
          if (!latest) return;
          targetId = latest.id;
        }

        // Persist: rewrite content + truncate the tail on the backend
        await window.BoarAiStore.api.updateMessage(sessionId, targetId, content);

        // Local optimistic update: rewrite this bubble, drop everything after it
        const idx = this.messages.findIndex(m => m.id === targetId);
        if (idx >= 0) {
          const edited = this.messages.slice(0, idx + 1);
          edited[idx].content = content;
          this.messages = edited;
        }
        this.editingMessageId = null;
        this.editingContent = '';

        // Re-run via the shared send path (edited bubble stays; no new user bubble,
        // and no duplicate user message — reuse the edited one as this run's input).
        const input = this.$refs.messageInput || this.$refs.welcomeInput;
        await this._sendMessageToAPI(input, content, { skipUserBubble: true, reuseLastUser: true });
      } catch (error) {
        console.error('Failed to edit message:', error);
        window.BoarAiStore.actions.error(this.t('notifications.sendMessageFailed') || 'Failed to edit message');
      } finally {
        this.sendGuard = false;
      }
    },

    // Dynamic spacing: group consecutive non-user messages tightly
    msgSpacing(index) {
      if (index >= this.messages.length - 1) return '';
      const msg = this.messages[index];
      const next = this.messages[index + 1];
      if (!next) return 'mb-3';
      // User message starts a new round
      if (msg.role === 'user') return 'mb-4';
      // Consecutive non-user messages (same round) → no gap
      if (next.role === 'assistant' || next.role === 'tool') return 'mb-0';
      // Before a user message → normal gap
      return 'mb-3';
    },

    // Width: user=80%, assistant=75%, tool cards=75% (same width for consistent alignment)
    msgWidthClass(msg) {
      // Skill records have no bubble: they are meta rows like the timestamp
      // line and must span the full row, otherwise the shrink-to-fit right
      // aligned item centers the chip/toggle inside the 80% column instead.
      if (msg.skill_card || msg.skill_authoring) return 'w-full min-w-0';
      // While editing, the editor panel needs a DEFINITE width: the row item is
      // shrink-to-fit, so inner w-full percentages would collapse the panel to
      // the textarea's intrinsic ~180px (cramped footer, vertical button text).
      if (msg.role === 'user' && this.editingMessageId === msg.id) return 'w-full max-w-[85%] min-w-0';
      if (msg.role === 'user') return 'max-w-[80%]';
      if (msg.role === 'tool') return 'max-w-[75%]';
      if (msg.role === 'assistant') return 'max-w-[75%]';
      return 'flex-1 min-w-0';
    },

    isAssistantStreaming(message, index) {
      if (!message || message.role !== 'assistant' || message._error) return false;
      if (!this.isSessionRunning || this.sessionError || this.pendingTools || this.toolExecuting || this.interrupting || this.liveRetryInfo) return false;
      if (message.tool_calls && message.tool_calls.length) return false;
      if (message._runId && this._activeRunId && message._runId !== this._activeRunId) return false;
      for (let i = index + 1; i < this.messages.length; i++) {
        if (this.messages[i].role === 'assistant') return false;
      }
      return true;
    },

    showRunActivity() {
      // The activity row stays visible for the WHOLE run (user request):
      // with process rows folded it is the only live signal on screen.
      // Suppressed only when a dedicated UI owns the moment (approval bar,
      // interrupt/cancel, retry card) or the run is over/failed.
      if (!this.activeSession || !this.isSessionRunning || this.sessionError) return false;
      if (this.pendingTools || this.interrupting || this.liveRetryInfo) return false;
      return true;
    },

    // One stable label for the whole waiting phase. The row stays mounted
    // while the run prepares or executes tools; swapping between "preparing"
    // and "working" made the text (and pill width) jump, which read as a
    // flash. The animated dots already carry the in-progress signal.
    // Label rotation for the run-activity row (see activityLabel computed).
    _startActivityLabelRotation() {
      if (this._activityLabelTimer) return;
      this._activityLabelTimer = setInterval(() => {
        this.activityLabelIdx = (this.activityLabelIdx + 1) % 4;
      }, 4000);
    },
    _stopActivityLabelRotation() {
      if (this._activityLabelTimer) {
        clearInterval(this._activityLabelTimer);
        this._activityLabelTimer = null;
      }
      this.activityLabelIdx = 0;
    },

    // The runtime prepends a "[BoarAi_System] …" context line to user turns.
    // Keep it out of the bubble body: it renders as a collapsible meta line.
    userMetaLine(msg) {
      const c = String((msg && msg.content) || '');
      const m = c.match(/^\s*(\[BoarAi_System\][^\n]*)/);
      return m ? m[1].trim() : '';
    },
    userBubbleContent(msg) {
      const c = String((msg && msg.content) || '');
      return c.replace(/^\s*\[BoarAi_System\][^\n]*\n?/, '');
    },
    // Skill records and authoring instructions ARE [BoarAi_System] injections
    // but already render their own row (outcome chip / collapsible task line).
    // Rendering the generic context toggle too stacked two misaligned rows for
    // one message.
    showUserMetaLine(msg) {
      if (!msg || msg.role !== 'user') return false;
      if (msg.skill_card || msg.skill_authoring) return false;
      return !!this.userMetaLine(msg);
    },

    // Untitled sessions all read "New Session" in the list and are impossible
    // to tell apart; label still-default titles as an empty placeholder.
    sessionTitleDisplay(session) {
      const title = String((session && session.title) || '').trim();
      const defaults = ['New Session', 'new session', 'New session', 'New', 'new',
        '新建会话', '新建', '新会话', 'Untitled', 'untitled', '未命名'];
      if (!title || defaults.includes(title)) {
        return '(' + (this.t('sessions.emptySession') || 'empty') + ')';
      }
      return session.title;
    },

    // Which channel/adapter created this session. `webui` sessions get no
    // badge (the common case); every other source shows the display name the
    // adapter registered in the backend SourceRegistry (stashed into
    // BoarAiStore.state.sourceNames by store.getSessions). Unknown/uninstalled
    // sources fall back to the raw id — the WebUI hardcodes no channel names.
    sourceBadge(session) {
      const source = String((session && session.source) || '').trim();
      if (!source || source === 'webui') return null;
      const names = window.BoarAiStore?.state?.sourceNames || {};
      return names[source] || source;
    },

    checkMobile() {
      const wasMobile = this.isMobile;
      this.isMobile = window.innerWidth < 1024;
      // Only close the drawer when the viewport CROSSES into the mobile
      // breakpoint. Resizing unconditionally used to collapse it every time
      // iOS showed/hid its URL bar.
      if (this.isMobile && !wasMobile) {
        this.sidebarExpanded = false;
        this.fmViewerOpen = false;
      }
    },
    
    toggleSidebar() {
      this.sidebarExpanded = !this.sidebarExpanded;
    },
    
    toggleRightSidebar() {
      this.rightSidebarExpanded = !this.rightSidebarExpanded;
      // Two drawers on top of each other is a trap on a phone: opening the
      // info panel closes the session list (and its backdrop).
      if (this.rightSidebarExpanded && this.isMobile) {
        this.sidebarExpanded = false;
      }
    },

    // ── File manager panes (tree under session list + viewer left of chat) ──
    fmPersist() {
      localStorage.setItem('boarui.fm.treeH', String(this.fmTreeH));
      localStorage.setItem('boarui.fm.viewerW', String(this.fmViewerW));
    },
    fmToggleViewer() {
      this.fmViewerOpen = !this.fmViewerOpen;
      this.fmPersist();
    },
    fmOpenFile(file) {
      if (!this.fmViewerOpen) {
        this.fmViewerOpen = true;
        this.fmPersist();
      }
      this.$nextTick(() => {
        if (this.$refs.fileViewer) this.$refs.fileViewer.openFile(file);
      });
    },
    fmStartDragV(e) {
      e.preventDefault();
      const sidebar = e.target.closest('.session-sidebar');
      if (!sidebar) return;
      const maxY = Math.max(160, sidebar.clientHeight - 48 - 140);
      const startY = e.clientY;
      const startH = this.fmTreeH;
      document.body.classList.add('fm-resizing-v');
      const move = (ev) => {
        this.fmTreeH = Math.min(maxY, Math.max(120, startH + (startY - ev.clientY)));
      };
      const up = () => {
        window.removeEventListener('pointermove', move);
        document.body.classList.remove('fm-resizing-v');
        this.fmPersist();
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up, { once: true });
    },
    fmStartDragH(e) {
      e.preventDefault();
      const row = e.target.parentElement;
      if (!row) return;
      const maxX = Math.max(360, row.clientWidth - 380);
      const startX = e.clientX;
      const startW = this.fmViewerW;
      document.body.classList.add('fm-resizing-h');
      const move = (ev) => {
        this.fmViewerW = Math.min(maxX, Math.max(320, startW + (ev.clientX - startX)));
      };
      const up = () => {
        window.removeEventListener('pointermove', move);
        document.body.classList.remove('fm-resizing-h');
        this.fmPersist();
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up, { once: true });
    },

    _watchToolbarWidth() {
      if (!this._toolbarRO) return;
      const el = this.$refs.sessionToolbar;
      if (el && el !== this._toolbarObservedEl) {
        this._toolbarRO.disconnect();
        this._toolbarRO.observe(el);
        this._toolbarObservedEl = el;
      }
    },
    
    scrollToMessage(messageId) {
      this.$nextTick(() => {
        const messageEl = document.querySelector(`[data-message-id="${messageId}"]`);
        if (messageEl) {
          messageEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
          // Highlight effect
          messageEl.classList.add('ring-2', 'ring-primary', 'ring-offset-2', 'dark:ring-offset-gray-900');
          setTimeout(() => {
            messageEl.classList.remove('ring-2', 'ring-primary', 'ring-offset-2', 'dark:ring-offset-gray-900');
          }, 2000);
        }
      });
    },

    /** Wheel click / hover-panel click: jump to a user round by index. */
    jumpToRound(index) {
      const msg = this.userMessages[index];
      if (!msg) return;
      this.currentRoundIndex = index;
      // Explicit selection wins over scroll-derived recompute until the
      // programmatic scroll settles; short replies otherwise flip the
      // highlight to a neighbouring round mid-scroll.
      this._roundSelectLockUntil = Date.now() + 800;
      this.scrollToMessage(msg.id);
    },

    // Hover enter/leave with a grace period so crossing the gap between the
    // dash column and the floating picker does not flicker it closed.
    onWheelEnter() {
      clearTimeout(this._wheelHoverTimer);
      this.wheelHover = true;
    },
    onWheelLeave() {
      clearTimeout(this._wheelHoverTimer);
      this._wheelHoverTimer = setTimeout(() => {
        this.wheelHover = false;
        this.wheelHoverIndex = null;
      }, 180);
    },

    // Dash emphasis decays with distance from the reference dash (hovered one,
    // or the in-view round when not hovering): ±3 graded steps, everything
    // further out shares the same faint default.
    wheelDotClass(index) {
      const ref = (this.wheelHoverIndex != null) ? this.wheelHoverIndex : this.currentRoundIndex;
      const d = Math.abs(index - ref);
      if (d === 0) return 'w-5 h-1.5 bg-primary shadow-sm group-hover:w-6 group-hover:h-2';
      if (d === 1) return 'w-4 h-1 bg-gray-300/90 dark:bg-gray-500/80 group-hover:w-5 group-hover:h-1.5';
      if (d === 2) return 'w-3.5 h-1 bg-gray-300/80 dark:bg-gray-600/80';
      return 'w-3 h-1 bg-gray-300/60 dark:bg-gray-600/60';
    },

    // Keep the hover card level with the dash under the cursor: the dash
    // column is centered at 50%, each dash row is ~14.5px (4-6px bar + 10px
    // gap), so the card shifts by the hovered dash's offset from the middle.
    wheelPickerStyle() {
      const n = this.userMessages.length;
      if (!n) return { transform: 'translateY(-50%)' };
      const ref = (this.wheelHoverIndex != null) ? this.wheelHoverIndex : this.currentRoundIndex;
      const offset = (ref - (n - 1) / 2) * 14.5;
      return { transform: `translateY(calc(-50% + ${offset}px))` };
    },

    /** Mouse wheel over the picker rotates the selection and scrolls chat. */
    rotateWheel(e) {
      const now = Date.now();
      if (now - (this._lastWheelTs || 0) < 220) return;
      this._lastWheelTs = now;
      const dir = (e.deltaY || 0) > 0 ? 1 : -1;
      // Rotate from the HOVERED dash when there is one so wheel scrolling over
      // the picker walks the selection instead of jumping back to the in-view
      // round first.
      const base = (this.wheelHoverIndex != null) ? this.wheelHoverIndex : this.currentRoundIndex;
      const next = Math.min(
        Math.max(base + dir, 0),
        this.userMessages.length - 1,
      );
      if (next !== base) this.jumpToRound(next);
      this.wheelHoverIndex = next;
    },

    /** Track which user round is in view (drives the wheel highlight). */
    _updateCurrentRound() {
      const container = this.$refs.messagesContainer;
      const rounds = this.userMessages;
      if (!container || !rounds.length) return;
      if (Date.now() < (this._roundSelectLockUntil || 0)) return;
      // At (or near) the bottom the newest round is by definition in view: a
      // short reply never lifts its user row above the 40% probe line, which
      // used to leave the second-to-last round highlighted after picking it.
      if (container.scrollHeight - container.scrollTop - container.clientHeight < 150) {
        this.currentRoundIndex = rounds.length - 1;
        return;
      }
      const line = container.scrollTop + container.clientHeight * 0.4;
      let current = 0;
      for (let i = 0; i < rounds.length; i++) {
        const el = container.querySelector(`[data-message-id="${rounds[i].id}"]`);
        if (el && el.offsetTop <= line) current = i;
      }
      this.currentRoundIndex = current;
    },
    
    async togglePin(session) {
      try {
        await window.BoarAiStore.api.togglePinSession(session.id, !session.isPinned);
        await this.loadSessions();
      } catch (error) {
        console.error('Failed to toggle pin:', error);
        window.BoarAiStore.actions.error(this.t('notifications.togglePinFailed'));
      }
    },
    
    startRename(session) {
      this.editingSessionId = session.id;
      this.editingTitle = session.title;
      this.deletingSessionId = null;
      this.$nextTick(() => {
        if (this.$refs.editInput && this.$refs.editInput[0]) {
          this.$refs.editInput[0].focus();
          this.$refs.editInput[0].select();
        }
      });
    },
    
    async confirmRename(session) {
      const newTitle = this.editingTitle.trim();
      if (newTitle && newTitle !== session.title) {
        try {
          await window.BoarAiStore.api.renameSession(session.id, newTitle);
          await this.loadSessions();
        } catch (error) {
          console.error('Failed to rename session:', error);
          window.BoarAiStore.actions.error(this.t('notifications.renameSessionFailed'));
        }
      }
      this.editingSessionId = null;
      this.editingTitle = '';
    },
    
    cancelRename() {
      this.editingSessionId = null;
      this.editingTitle = '';
    },
    
    startDelete(session) {
      this.deletingSessionId = session.id;
      this.editingSessionId = null;
    },
    
    async confirmDelete(session, distill = false) {
      const pending = this._pendingDeleteIds || new Set();
      pending.add(session.id);
      this._pendingDeleteIds = pending;

      // Remove from list immediately for instant UI response
      this.sessions = this.sessions.filter(s => s.id !== session.id);
      this.deletingSessionId = null;
      
      // If deleted session was active, clear and navigate back
      if (this.activeSession?.id === session.id) {
        this.activeSession = null;
        this.messages = [];
        this._suppressAutoSelect = true;
        this.$router.push({ name: 'sessions' });
      }
      
      const label = distill ? this.t('notifications.distillDelete') : this.t('notifications.sessionDeleted');
      window.BoarAiStore.actions.success(label);
      
      // Delete via HTTP with optional distill flag
      try {
        const url = `/api/sessions/${session.id}${distill ? '?distill=true' : ''}`;
        await fetch(url, { method: 'DELETE' });
      } catch (e) {
        console.error('Delete session error:', e);
      } finally {
        if (this._pendingDeleteIds) {
          this._pendingDeleteIds.delete(session.id);
          if (this._pendingDeleteIds.size === 0) this._pendingDeleteIds = null;
        }
        this._suppressAutoSelect = false;
        this.loadSessions().catch(() => {});
      }
    },
    
    cancelDelete() {
      this.deletingSessionId = null;
    },

    // --- Multi-Select ---
    toggleSelectMode() {
      this.selectMode = !this.selectMode;
      if (!this.selectMode) {
        this.selectedSessionIds = {};
      }
    },
    exitSelectMode() {
      this.selectMode = false;
      this.selectedSessionIds = {};
    },
    toggleSelectSession(session) {
      const obj = { ...this.selectedSessionIds };
      if (obj[session.id]) {
        delete obj[session.id];
      } else {
        obj[session.id] = true;
      }
      this.selectedSessionIds = obj;
    },
    toggleSelectAll() {
      if (this.allSessionsSelected) {
        this.selectedSessionIds = {};
      } else {
        const obj = {};
        this.sessions.forEach(s => { obj[s.id] = true; });
        this.selectedSessionIds = obj;
      }
    },
    invertSelection() {
      const obj = {};
      this.sessions.forEach(s => {
        if (!this.selectedSessionIds[s.id]) {
          obj[s.id] = true;
        }
      });
      this.selectedSessionIds = obj;
    },
    async batchDelete() {
      const ids = Object.keys(this.selectedSessionIds);
      if (ids.length === 0) return;

      const idSet = new Set(ids);
      // Mark all targets pending so session.deleted echoes don't thrash loadSessions
      this._pendingDeleteIds = new Set([...(this._pendingDeleteIds || []), ...ids]);
      
      // Remove from list immediately
      this.sessions = this.sessions.filter(s => !idSet.has(s.id));
      
      // If active session was deleted, clear and navigate back
      if (this.activeSession && idSet.has(this.activeSession.id)) {
        this.activeSession = null;
        this.messages = [];
        this._suppressAutoSelect = true;
        // Invalidate in-flight message/stats loads for the cleared session
        this._loadMessagesSeq += 1;
        this.$router.push({ name: 'sessions' });
      }
      
      this.exitSelectMode();
      window.BoarAiStore.actions.success(
        (window.i18n?.t?.('notifications.sessionsDeleted') || 'Deleted {n} sessions').replace('{n}', ids.length)
      );
      
      // Delete each session via API (broadcasts suppressed via _pendingDeleteIds)
      try {
        for (const id of ids) {
          try {
            await fetch(`/api/sessions/${id}`, { method: 'DELETE' });
          } catch (e) {
            console.error('Batch delete error for', id, e);
          }
        }
      } finally {
        this._pendingDeleteIds = null;
        this._suppressAutoSelect = false;
        this.loadSessions().catch(() => {});
      }
    },
    
    selectSession(session, navigate = true) {
      const prevId = this.activeSession?.id;
      this.activeSession = session;
      this._coldProbeDone = false;
      // Clear pending workspace change message when switching sessions
      this._pendingWorkspaceChangeMessage = null;
      // Leaving another session: drop its local running flag (server may still run)
      if (prevId && prevId !== session.id) {
        if (this.runningSessionIds[prevId]) {
          delete this.runningSessionIds[prevId];
        }
        if (this._activeRunId) {
          // Only clear tracked run if it belonged to previous session
          this._activeRunId = null;
        }
        this.toolExecuting = false;
        this.interrupting = false;
        this.liveRetryInfo = null;
        // Drop previous session bubbles so loadMessages always hydrates from disk
        // (running-guard must not keep an empty pane after switch/refresh).
        this.messages = [];
        this._resetMessagePagination();
      } else if (!prevId) {
        // Cold open / full page refresh: start from empty until disk load finishes
        this.messages = [];
        this._resetMessagePagination();
      }
      // Restore this session's display state (fold rows + round overrides) so
      // expand/collapse choices survive reloads, route changes and switches.
      this._loadDisplayState(session.id);
      // Re-sync the tool-approval overlay for the newly active session. The
      // security gate can fire while the user is on another page/session; the
      // store keeps the pending record so switching here restores the buttons.
      const pa = (window.BoarAiStore && window.BoarAiStore.state) ? window.BoarAiStore.state.pendingApprovals : {};
      const pending = pa ? pa[session.id] : null;
      if (pending) {
        this.pendingTools = pending.tools || [];
        this.pendingRunId = pending.runId || null;
        this.pendingConfirmationId = pending.confirmationId || '';
      } else {
        this.pendingTools = null;
        this.pendingRunId = null;
        this.pendingConfirmationId = null;
      }
      // Set current agent from session
      if (session.agent_name) {
        this.currentAgentName = session.agent_name;
      }
      // Thinking mode is session-scoped: apply from list data immediately.
      // Set the model first so the capability lookup uses the right one.
      if (session.model) {
        this.selectedModel = session.model;
      }
      this._applySessionThinkingMode(session);
      // Approval mode is session-scoped too (security_overrides.approval_mode)
      this._applySessionApprovalMode(session);
      // Load messages + restore stop button if a run is still live (refresh / switch)
      this._resumeRunStreaming(session.id).catch((e) => {
        console.warn('resume after selectSession failed:', e);
        this.loadMessages(session.id);
      });
      // Load persisted file-change history for the composer changes panel (fire-and-forget)
      this.loadChanges(session.id);
      // Update URL to sessions/{id}
      if (navigate) {
        this.$router.push({ name: 'sessions-detail', params: { id: session.id } });
      }
      // Auto-close sidebar on mobile after selection
      if (this.isMobile) {
        this.sidebarExpanded = false;
      }
    },

    /** Apply server auto-title (first user message) to list row + active header */
    _applySessionTitle(sessionId, title) {
      if (!sessionId || !title) return;
      const t = String(title).trim();
      if (!t) return;
      let changed = false;
      const row = this.sessions.find(s => s.id === sessionId);
      if (row && row.title !== t) {
        row.title = t;
        changed = true;
      }
      if (this.activeSession?.id === sessionId && this.activeSession.title !== t) {
        this.activeSession.title = t;
        changed = true;
      }
      if (changed) this._sessionVersion++;
    },
    
    async loadSessions() {
      this.isLoadingSessions = true;
      try {
        const sessions = await window.BoarAiStore.api.getSessions();
        let list = Array.isArray(sessions) ? sessions : [];
        // Hide sessions still being deleted so we never auto-open a dying id
        if (this._pendingDeleteIds && this._pendingDeleteIds.size > 0) {
          list = list.filter(s => !this._pendingDeleteIds.has(s.id));
        }
        // Merge live UI flags: list replace must not wipe running / sticky error.
        this.sessions = list.map((s) => {
          const id = s.id;
          const serverError = s.status === 'error' || !!s.hasError;
          if (serverError) this.sessionErrorIds[id] = true;
          s.isRunning = !!this.runningSessionIds[id];
          s.hasError = !!(serverError || this.sessionErrorIds[id]);
          return s;
        });
        if (window.BoarAiStore?.state) {
          window.BoarAiStore.state.sessions = this.sessions;
        }
        
        // If there's a session ID in route params, auto-select it
        const routeId = this.$route.params.id;
        if (routeId && !(this._pendingDeleteIds && this._pendingDeleteIds.has(routeId))) {
          const session = this.sessions.find(s => s.id === routeId);
          if (session) {
            // Already focused: only refresh list fields — do NOT re-enter selectSession
            // (that re-runs resume/loadMessages and can paint empty assistant as error).
            if (this.activeSession?.id === session.id) {
              Object.assign(this.activeSession, session);
            } else {
              this.selectSession(session, false);
            }
          } else if (this.activeSession?.id === routeId) {
            // Route still points at a session that no longer exists
            this.activeSession = null;
            this.messages = [];
          }
        } else if (!this.activeSession && this.sessions.length > 0 && !this._suppressAutoSelect) {
          // Prefer most recently updated session so empty-pane + API list don't fight.
          // Suppressed after intentional delete-clear so mid-batch reloads don't reopen sessions.
          this.selectSession(this.sessions[0], true);
        }
      } catch (error) {
        console.error('Failed to load sessions:', error);
        // Keep previous list if refresh fails; only show empty on first load
        if (!Array.isArray(this.sessions)) this.sessions = [];
        window.BoarAiStore.actions.error(this.t('notifications.loadSessionsFailed'));
      } finally {
        this.isLoadingSessions = false;
      }
    },
    
    async loadModels() {
      try {
        const result = await window.BoarAiStore.api.getModels();
        this.availableModels = result.models || [];
        // Model selection is driven by session data (loadMessages / createSession),
        // not auto-selected here. If no session is active, selectedModel stays null
        // and the toolbar shows "Auto".
      } catch (error) {
        console.error('Failed to load models:', error);
      }
    },

    /** Capability definitions enabled on a model entry (popover row icons). */
    enabledCapDefs(m) {
      const caps = m && m.capabilities;
      if (!caps) return [];
      return this.modelCaps.filter((mc) => caps[mc.key]);
    },

    enabledCapTitle(m) {
      return this.enabledCapDefs(m)
        .map((mc) => this.t('settings.mcap_' + mc.key) || mc.label)
        .join(' / ');
    },
    
    async loadMessages(sessionId, options = {}) {
      if (!sessionId) return;
      if (this._pendingDeleteIds && this._pendingDeleteIds.has(sessionId)) return;
      const force = !!(options && options.force);
      const older = !!(options && options.older);

      const seq = ++this._loadMessagesSeq;
      this.isLoadingMessages = true;
      try {
        // Protect optimistic UI only when a live run already has local bubbles.
        // After F5 / cold open, messages is [] — must always apply disk snapshot
        // or the pane stays blank while the "thinking" pill still shows.
        // `older` is an explicit user action and must still proceed.
        const sessionIsRunning = !!(this.runningSessionIds[sessionId]) || !!this._activeRunId;
        const hasLocalMessages = Array.isArray(this.messages) && this.messages.length > 0;
        if (!older && sessionIsRunning && hasLocalMessages && !force) {
          // Still heal error flags on existing bubbles if needed, but keep local list
          this._healEmptyAssistantErrorFromSession(sessionId);
          return;
        }

        // Fetch strategy by mode: older (before_seq) / incremental (since_seq) / cold.
        // Incremental append only when the local list is already server-backed
        // (no optimistic temp bubbles) — otherwise a full reload must reconcile
        // temp bubbles with their disk ids (see submitEditMessage's force reload).
        const hasTempBubbles = this._hasTempBubbles();
        let params;
        if (older) {
          params = { limit: this._messagePageSize, before_seq: this._earliestSeq() };
        } else if (this._watermarkSeq > 0 && !force && !hasTempBubbles) {
          params = { since_seq: this._watermarkSeq };
        } else {
          params = { limit: this._messagePageSize };
        }

        const result = await window.BoarAiStore.api.getMessages(sessionId, params);
        // Stale response: user switched session or delete cleared active
        if (seq !== this._loadMessagesSeq) return;
        if (this.activeSession?.id !== sessionId) return;
        if (this._pendingDeleteIds && this._pendingDeleteIds.has(sessionId)) return;

        // IMPORTANT: wait for any in-flight Vue DOM patches from WS stream updates
        // to settle before mutating the messages array — otherwise Vue's virtual
        // DOM diff references orphaned nodes and throws insertBefore / Symbol(_vei).
        await this.$nextTick();
        const mapped = (result.messages || []).map((msg, msgIndex, arr) =>
          this._mapLoadedMessage(sessionId, msg, msgIndex, arr, sessionIsRunning));

        const newWatermark = (typeof result.watermark_seq === 'number')
          ? result.watermark_seq
          : this._maxSeqOf(mapped);

        if (older) {
          await this._prependOlderMessages(mapped);
          this._hasMore = !!result.has_more;
          if (newWatermark > this._watermarkSeq) this._watermarkSeq = newWatermark;
          return;
        }

        if (this._watermarkSeq > 0 && !force && !hasTempBubbles) {
          this._appendIncrementalMessages(mapped);
          if (newWatermark > this._watermarkSeq) this._watermarkSeq = newWatermark;
          return;
        }

        // --- Cold open / forced full reload ---
        // Decide scroll intent BEFORE the DOM mutation: only the first paint
        // of a session (or a reload while the user is still at the bottom)
        // may jump to the latest message. A background force-reload must not
        // yank the viewport away from a user reading earlier history.
        const firstPaint = !hasLocalMessages;
        const _preScrollEl = this.$refs.messagesContainer;
        const wasNearBottom = !_preScrollEl
          || (_preScrollEl.scrollHeight - _preScrollEl.scrollTop - _preScrollEl.clientHeight < 150);
        this.messages = mapped;
        this._watermarkSeq = newWatermark;
        this._hasMore = !!result.has_more;

        // Fresh conversation: reset scroll-to-latest / unread state
        this.showScrollBtn = false;
        this.unreadCount = 0;
        this._unreadPending = false;

        // Session-level error heal for edge cases
        this._healEmptyAssistantErrorFromSession(sessionId);

        // Soft reload / cold open: latest idle error must stay Retry-clickable.
        // loadMessages maps _errorRetryable=false by default; restore for the
        // trailing assistant error (live sticky flag OR any latest disk failure).
        if (!sessionIsRunning) {
          for (let i = this.messages.length - 1; i >= 0; i--) {
            const m = this.messages[i];
            if (m.role === 'assistant' && m._error) {
              m._errorRetryable = true;
              this.sessionRetryableErrorIds[sessionId] = true;
              break;
            }
          }
        } else if (this.sessionRetryableErrorIds[sessionId]) {
          for (let i = this.messages.length - 1; i >= 0; i--) {
            const m = this.messages[i];
            if (m.role === 'assistant' && m._error) {
              m._errorRetryable = true;
              break;
            }
          }
        }

        // Last assistant is an explicit disk error while UI still thinks "running"
        // (missed run.error / race with list reload) — force idle + sticky error.
        // Only the latest assistant counts — older [Error] stamps must not kill a new run.
        let lastAsst = null;
        for (let i = this.messages.length - 1; i >= 0; i--) {
          if (this.messages[i].role === 'assistant') {
            lastAsst = this.messages[i];
            break;
          }
        }
        if (lastAsst) {
          const raw = String(lastAsst.content || '').trim();
          const lastIsDiskError = !!lastAsst.is_error || /^\[Error\]/i.test(raw);
          if (lastIsDiskError) {
            if (this.runningSessionIds[sessionId] || this._activeRunId) {
              this._forceSessionIdle(sessionId, this._activeRunId, { error: true });
            } else {
              this._setSessionError(sessionId, true);
            }
          }
        }

        // Restore thinking time cached from stream completion
        if (this._thinkingTimeCache) {
          for (const m of this.messages) {
            // Server metadata is authoritative; live cache only fills gaps
            // (turns finalized before the last reload measured them locally).
            if (
              this._thinkingTimeCache[m.id] !== undefined
              && (m._thinkingTime === undefined || m._thinkingTime === null)
            ) {
              m._thinkingTime = this._thinkingTimeCache[m.id];
            }
          }
        }
        
        // Update active session with messages
        if (this.activeSession) {
          this.activeSession.messages = this.messages;
        }
        
        // Authoritative session fields (thinking_mode / model / workspace)
        try {
          const sessionData = await window.BoarAiStore.api.getSession(sessionId, false);
          if (seq !== this._loadMessagesSeq || this.activeSession?.id !== sessionId) return;
          if (sessionData) {
            this._applySessionApprovalMode(sessionData);
            if (sessionData.model) {
              this.selectedModel = sessionData.model;
            }
            this._applySessionThinkingMode(sessionData);
            if (sessionData.workspace_path !== undefined && this.activeSession) {
              this.activeSession.workspace_path = sessionData.workspace_path || '';
            }
            // Keep list row in sync for next switch
            const row = this.sessions.find(s => s.id === sessionId);
            if (row) {
              row.thinking_mode = this.thinkingMode;
              if (sessionData.model !== undefined) row.model = sessionData.model || '';
              if (sessionData.security_overrides !== undefined) {
                row.security_overrides = sessionData.security_overrides || {};
              }
            }
          }
        } catch (e) {
          // Non-critical: keep values applied from selectSession list data
        }
        
        // After disk reload, tool cards must leave "running" if results exist
        this._syncToolExecutingFlag();

        // Scroll to bottom after loading messages (wait for layout) — but
        // only on first paint or when the user was already at the bottom;
        // otherwise keep their reading position.
        this.$nextTick(() => {
          requestAnimationFrame(() => {
            if (firstPaint || wasNearBottom) this.scrollToBottom(true);
          });
        });
        // Pull remaining history pages so the round wheel, in-pane search and
        // the message list all see the FULL conversation after a cold open —
        // with only the latest page loaded the wheel showed just the rounds
        // of that page and older user messages stayed invisible until an
        // upward-scroll lazy load happened to reach them.
        if (this._hasMore) {
          this._loadAllOlderPages(sessionId, firstPaint || wasNearBottom).catch(() => {});
        }
        // Load session token stats for info bar
        await this.loadSessionStats(sessionId);
      } catch (error) {
        // Expected during delete races / concurrent clients — do not toast
        const msg = String(error?.message || error || '');
        const isGone = /not found/i.test(msg);
        if (isGone || (this._pendingDeleteIds && this._pendingDeleteIds.has(sessionId))
            || this.activeSession?.id !== sessionId || seq !== this._loadMessagesSeq) {
          if (this.activeSession?.id === sessionId && isGone) {
            this.activeSession = null;
            this.messages = [];
          }
          return;
        }
        console.error('Failed to load messages:', error);
        window.BoarAiStore.actions.error(this.t('notifications.loadMessagesFailed'));
        this.messages = [];
      } finally {
        if (seq === this._loadMessagesSeq) {
          this.isLoadingMessages = false;
          this._flushDeferredToolEvents();
          this._updateCurrentRound();
        }
      }
    },

    /** Replay tool start/finished events that arrived mid-loadMessages. */
    _flushDeferredToolEvents() {
      const deferred = (this._deferredToolEvents || []).splice(0);
      for (const evt of deferred) {
        if (evt.kind === 'start') this._onToolStart(...evt.args);
        else if (evt.kind === 'finished') this._onToolFinished(...evt.args);
      }
    },

    /** Map a raw message dict (WS or HTTP) into the frontend message shape. */
    _mapLoadedMessage(sessionId, msg, msgIndex, arr, sessionIsRunning) {
      const meta = msg.metadata || {};
      const isRunError = !!(msg.is_error ?? meta.is_error);
      const errText = meta.error || msg.error || null;
      const content = msg.content || '';
      const stamped = /^\[Error\]/i.test(String(content).trim());
      // Server-persisted marks are the ONLY error source on reload:
      // metadata.is_error or an explicit [Error] content stamp. An empty
      // assistant bubble is NOT evidence of failure — tool-call turns are
      // legitimately content-less, and core prunes aborted empty tails
      // server-side (_prune_empty_tail_assistant).
      const showError = msg.role === 'assistant' && (isRunError || stamped);
      return {
        id: msg.message_id || msg.id || msg.tool_call_id || `msg_${sessionId}_${msgIndex}_${msg.timestamp || msg.role}`,
        // seq==0 means "legacy/no seq" — treat as null so pagination cursors
        // (earliest/watermark) never mistake 0 for a valid monotonic cursor.
        seq: (msg.seq > 0 ? msg.seq : null),
        role: msg.role,
        content: content,
        timestamp: msg.timestamp,
        // Per-message agent/model snapshots (from message metadata) so
        // history keeps correct attribution after the session role/model changes.
        agent_name: msg.agent_name || meta.agent_name || null,
        model: msg.model || meta.model || null,
        tool_calls: msg.tool_calls || null,
        tool_call_id: msg.tool_call_id || null,
        attachments: meta.attachments || null,
        thinking: msg.thinking || null,
        tool_name: msg.tool_name || meta.tool_name || null,
        command: msg.command || meta.command || null,
        // Chat-command turns (server-intercepted before any LLM run):
        // metadata.chat_command = {name, kind|ok, raw?} — drives the badge.
        chat_command: meta.chat_command || msg.chat_command || null,
        arguments: msg.arguments || meta.arguments || null,
        is_error: isRunError || stamped,
        tool_result: msg.tool_result || meta.tool_result || null,
        // Per-file change record (persisted in message metadata) so inline
        // "file changed" cards survive a reload / F5.
        file_change: msg.file_change || meta.file_change || null,
        error_kind: msg.error_kind || meta.error_kind || null,
        error_reason: msg.error_reason || meta.error_reason || null,
        // Skill evolution records: outcome card / authoring instruction.
        skill_card: meta.skill_card || null,
        skill_authoring: !!meta.skill_authoring,
        // Restored from disk: show error, but do not allow retry (historical)
        _error: showError,
        _errorText: showError
          ? (errText
            || (stamped ? String(content).replace(/^\[Error\]\s*/i, '') : '')
            || this.t('sessions.llmError')
            || 'Run encountered an error')
          : undefined,
        // Live failures set true in _markAssistantError; reload always false
        _errorRetryable: false,
        _runId: meta.run_id || null,
        // Per-run consumption stamp (executor._stamp_run_usage_meta): the
        // WebUI sums these across a round for the "tokens · duration" chip.
        _runUsage: meta.run_usage || null,
        _runStartedAt: Number(meta.run_started_at) || null,
        _runFinishedAt: Number(meta.run_finished_at) || null,
        // Server message id so mid-stream chunks route to the right bubble
        // even after a soft reload adopted disk ids.
        _serverMessageId: msg.role === 'assistant'
          ? (msg.message_id || msg.id || null)
          : null,
        _thinkingComplete: !!msg.thinking,
        // Server-persisted thinking duration survives refresh / reload
        _thinkingTime:
          (msg.metadata && Number(msg.metadata.thinking_seconds)) || undefined,
      };
    },

    /** Max numeric seq across a list of mapped messages (0 when empty/unknown). */
    _maxSeqOf(list) {
      let max = 0;
      for (const m of (list || [])) {
        const s = Number(m && m.seq);
        if (Number.isFinite(s) && s > 0 && s > max) max = s;
      }
      return max;
    },

    /** Earliest valid (>0) seq in the current list (null when none). */
    _earliestSeq() {
      let min = null;
      for (const m of this.messages) {
        const s = Number(m && m.seq);
        if (Number.isFinite(s) && s > 0) {
          if (min === null || s < min) min = s;
        }
      }
      return min;
    },

    /** Prepend older history (before_seq) while keeping scroll position stable. */
    async _prependOlderMessages(mapped) {
      if (!mapped || !mapped.length) return;
      const existing = new Set(this.messages.map(m => m.id));
      const older = mapped.filter(m => !existing.has(m.id));
      if (!older.length) return;
      older.sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));

      // Record anchor (first visible message) offset to restore scroll after prepend.
      const container = this.$refs.messagesContainer;
      let anchorId = null;
      let anchorTop = 0;
      if (container) {
        const first = container.querySelector('[data-message-id]');
        if (first) {
          anchorId = first.getAttribute('data-message-id');
          anchorTop = first.offsetTop;
        }
      }

      this.messages = older.concat(this.messages);
      if (this.activeSession) {
        this.activeSession.messages = this.messages;
      }
      await this.$nextTick();

      if (container && anchorId) {
        const nowEl = container.querySelector(`[data-message-id="${anchorId}"]`);
        if (nowEl) {
          container.scrollTop += (nowEl.offsetTop - anchorTop);
        }
      }
    },

    /** Append incremental (since_seq) messages with id-based dedupe + seq sort. */
    _appendIncrementalMessages(mapped) {
      if (!mapped || !mapped.length) return;
      const existing = new Set(this.messages.map(m => m.id));
      const fresh = mapped.filter(m => !existing.has(m.id));
      if (!fresh.length) return;
      fresh.sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
      this.messages = this.messages.concat(fresh);
      if (this.activeSession) {
        this.activeSession.messages = this.messages;
      }
      // Tool cards must leave "running" if results now exist on disk.
      this._syncToolExecutingFlag();
      // Unread tracking + throttled auto-scroll (same as stream mutations).
      this._trackUnreadOnMutation();
      this._scrollToBottomThrottled();
    },

    /**
     * Sequentially fetch every remaining older page after a cold open, so the
     * full conversation is loaded without requiring upward scrolls. Aborts on
     * session switch or error. Each prepend keeps the viewport anchored on the
     * previously first visible message; when the user started at the bottom
     * (first paint), re-pin to the bottom once everything is in.
     */
    async _loadAllOlderPages(sessionId, pinToBottom) {
      while (this._hasMore && this.activeSession?.id === sessionId) {
        try {
          await this.loadMessages(sessionId, { older: true });
        } catch (_) {
          break;
        }
      }
      if (pinToBottom && this.activeSession?.id === sessionId) {
        this.scrollToBottom(true);
      }
    },

    /** Reset seq pagination state (session switch / clear / cold open). */
    _resetMessagePagination() {
      this._watermarkSeq = 0;
      this._hasMore = false;
      this._loadingOlder = false;
      this.loadingOlder = false;
    },

    /** True when the local list still contains optimistic temp bubbles. */
    _hasTempBubbles() {
      return this.messages.some(m => String(m && m.id || '').includes('_temp'));
    },
    
    getUrlSessionId() {
      // Read session ID from route params
      return this.$route.params.id || null;
    },
    
    async createSession() {
      if (this._creatingLock) return;  // 防连点锁
      this._creatingLock = true;
      try {
        // Capture welcome-pane toolbar selections before create: the server's
        // new-session defaults (applied below) would otherwise overwrite them.
        const preAgent = this.currentAgentName || 'default';
        const preModel = this.selectedModel;
        const preThinking = this.thinkingMode;
        const preApproval = this.approvalMode;
        // Always create with backend default English placeholder so
        // Session.maybe_auto_title_from_user recognizes it.
        const res = await fetch('/api/sessions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: 'New Session',
            source: 'webui',
            agent_name: preAgent,
          }),
        });
        const json = await res.json();
        if (!json.success) throw new Error(json.error?.message || 'Failed');
        const data = json.data;
        // List row is added only via WS session.created (_onSessionCreated).
        // HTTP path only opens the session so the user can type immediately.
        this._pendingCreateId = data.session_id;
        const newSession = this._mapServerSession(data);
        // Persist welcome-pane selections BEFORE opening the session, so the
        // loadMessages fetch inside selectSession already sees them (avoids a
        // GET/PUT race that would revert the UI back to server defaults).
        const prePatch = {};
        if (preThinking && preThinking !== (newSession.thinking_mode || 'none')) prePatch.thinking_mode = preThinking;
        if (preModel && preModel !== (newSession.model || '')) prePatch.model = preModel;
        if (Object.keys(prePatch).length) {
          try {
            await fetch(`/api/sessions/${data.session_id}`, {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(prePatch),
            });
            Object.assign(newSession, prePatch);
          } catch (e) {
            console.warn('Failed to persist preselected session options:', e);
          }
        }
        // A fresh session defaults to 'ask'; only persist an explicit pick.
        if (preApproval && preApproval !== 'ask') {
          const preApprovalBackend = preApproval === 'deny' ? 'deny' : 'off';
          try {
            await window.BoarAiStore.api.updateSessionApprovalMode(data.session_id, preApprovalBackend);
            newSession.security_overrides = { ...(newSession.security_overrides || {}), approval_mode: preApprovalBackend };
          } catch (e) {
            console.warn('Failed to persist preselected approval mode:', e);
          }
        }
        this.selectedModel = newSession.model || null;
        this._applySessionThinkingMode(newSession);
        this.currentAgentName = newSession.agent_name || 'default';
        this.selectSession(newSession);
        // If WS broadcast is late/missing, ensure list still gets the row once
        setTimeout(() => {
          if (this._pendingCreateId !== data.session_id) return;
          this._pendingCreateId = null;
          if (!this.sessions.some(s => s.id === data.session_id)) {
            this.sessions = [newSession, ...this.sessions];
            this._sessionVersion++;
          }
        }, 1500);
        window.BoarAiStore.actions.success(this.t('notifications.sessionCreated'));
      } catch (error) {
        console.error('Failed to create session:', error);
        this._pendingCreateId = null;
        window.BoarAiStore.actions.error(this.t('notifications.createSessionFailed'));
      } finally {
        this._creatingLock = false;
      }
    },

    /** Normalize session.create / session.list / WS payload into list row shape */
    _mapServerSession(data) {
      if (!data) return null;
      const id = data.session_id || data.id;
      if (!id) return null;
      const fmt = window.BoarAiStore?.api?._formatTime;
      return {
        id,
        title: data.title || 'New Session',
        agent_name: data.agent_name || this.currentAgentName || 'default',
        time: fmt ? fmt.call(window.BoarAiStore.api, data.updated_at) : (data.updated_at || ''),
        isPinned: !!(data.pinned ?? data.isPinned),
        isArchived: !!(data.archived ?? data.isArchived),
        messageCount: data.message_count ?? data.messageCount ?? 0,
        preview: data.preview ?? null,
        source: data.source || 'webui',
        status: data.status || 'active',
        hasError: data.status === 'error' || !!data.hasError,
        model: data.model || '',
        thinking_mode: data.thinking_mode || 'none',
        workspace_path: data.workspace_path || '',
        security_overrides: data.security_overrides || {},
      };
    },
    
    async sendMessage(event) {
      const input = this.$refs.messageInput || this.$refs.welcomeInput;
      const content = input?.value?.trim() || '';
      // Allow sending attachments without any text (empty prompt + files).
      if (!content && !this.pendingAttachments.length) return;
      // 防连点/连按 Enter：进行中发送时忽略第二次
      if (this.sendGuard) return;
      this.sendGuard = true;
      // Clear stale vision notices at the start of a fresh turn.
      this.visionNotice = null;
      this.visionRoute = null;

      try {
        if (this.isSessionRunning && this._activeRunId) {
          await this._interruptCurrentRun();
          this.interrupting = false;
        }

        // 无活动会话时先建会话再发，避免 then 竞态与内容丢失
        if (!this.activeSession) {
          await this.createSession();
          if (!this.activeSession) return;
        }

        await this._sendMessageToAPI(input, content);

        // Sent from the welcome pane: hand focus to the composer for follow-ups.
        if (this.$refs.messageInput && this.$refs.messageInput !== input) {
          this.$refs.messageInput.focus();
        }
      } finally {
        this.sendGuard = false;
      }
    },
    
    async cancelCurrentRun() {
      await this._interruptCurrentRun();
    },

    async _interruptCurrentRun() {
      const runId = this._activeRunId;
      if (!runId || this.interrupting) return;

      this.interrupting = true;
      this._interruptingRunIds.add(runId);

      // Mark terminal BEFORE the async cancel call so the WS run.cancelled
      // callback (if it arrives during the await) is skipped via
      // _acceptTerminalRunEvent.  This avoids a splice+push DOM race
      // between _onStreamCancelled and the follow-up _sendMessageToAPI.
      this._markRunTerminal(runId);
      this._clearRunTimeout();

      const systemMessageId = this._runSystemMessageId(runId);
      this._appendSystemMessage({
        id: systemMessageId,
        content: this.t('sessions.interrupting'),
        interrupt: true,
      });

      let cancelled = false;
      try {
        await window.BoarAiStore.api.cancelRun(runId);
        cancelled = true;
      } catch (e) {
        // Cancel API failed — undo terminal mark so the WS callback
        // can still process run.cancelled when it arrives.
        this._terminalRunIds.delete(runId);
        const errorMessage = e.message || this.t('notifications.streamError');
        console.warn('Cancel failed:', e);
        this._interruptingRunIds.delete(runId);
        this.interrupting = false;
        this._appendSystemMessage({
          id: systemMessageId,
          content: errorMessage,
          error: true,
        });
        window.BoarAiStore.actions.error(errorMessage);
        return;
      }
      // Cancel succeeded: clean up immediately (don't wait for WS event).
      // The WS callback was already blocked by _markRunTerminal, so we
      // must do its cleanup here: remove empty assistant bubble + update
      // the system message.
      const sessionId = this.activeSession?.id;
      if (sessionId) {
        this._interruptingRunIds.delete(runId);
        this._setSessionRunning(sessionId, false);
        this._activeRunId = null;
        this.interrupting = false;
        this.toolExecuting = false;
        this.pendingTools = null;
        this.pendingRunId = null;
        this.pendingConfirmationId = null;
        if (this._runningToolIds) this._runningToolIds.clear();

        // Cleanup that _onStreamCancelled would have done
        this._removeEmptyAssistantMessage(runId);
        this._appendSystemMessage({
          id: systemMessageId,
          content: this.t('sessions.interrupted'),
          interrupt: true,
        });
      }
    },

    _runSystemMessageId(runId) {
      return `run_status_${runId || Date.now()}`;
    },

    _clearPendingRunStatusMessage(runId) {
      this._scheduleMessageMutation(() => {
        const id = this._runSystemMessageId(runId);
        const message = this.messages.find(item => item.id === id);
        if (message && message._interrupt && !message._error) {
          this.messages.splice(this.messages.indexOf(message), 1);
        }
      });
    },

    _appendSystemMessage({ id, content, error = false, interrupt = false }) {
      this._scheduleMessageMutation(() => {
        const existing = this.messages.find(message => message.id === id);
        const message = {
          id,
          role: 'system',
          content,
          timestamp: new Date().toISOString(),
          _error: error,
          _interrupt: interrupt,
        };
        if (existing) Object.assign(existing, message);
        else this.messages.push(message);
      });
      this._scrollToBottomThrottled();
    },

    _isCurrentRunEvent(sessionId, runId) {
      if (sessionId !== this.activeSession?.id) return false;
      if (runId && this._terminalRunIds.has(runId)) return false;
      return !runId || !this._activeRunId || runId === this._activeRunId;
    },

    _isActiveRun(runId) {
      if (!runId || runId === this._activeRunId) return true;
      if (this._interruptingRunIds.has(runId)) return false;
      return !this._activeRunId;
    },

    _acceptTerminalRunEvent(sessionId, runId) {
      if (sessionId !== this.activeSession?.id) return false;
      if (runId && this._terminalRunIds.has(runId)) return false;
      return this._isActiveRun(runId) || this._interruptingRunIds.has(runId);
    },

    _finalizeTerminalRun(sessionId, runId) {
      const isActiveRun = this._isActiveRun(runId);
      const matchesTracked =
        !runId || !this._activeRunId || runId === this._activeRunId;
      this._markRunTerminal(runId);
      if (runId) this._interruptingRunIds.delete(runId);
      // Always drop local running when terminal is accepted for this session.
      // Stale isActiveRun=false used to leave "运行中" stuck after missed frames.
      if (isActiveRun || matchesTracked || this.runningSessionIds[sessionId]) {
        this._setSessionRunning(sessionId, false);
      }
      // Clear tracked run whenever this terminal event is for the tracked id
      // (or we have no id) — even if isActiveRun was false due to race.
      if (matchesTracked) {
        this._activeRunId = null;
      }
      if (!isActiveRun && !matchesTracked) return false;

      this.liveRetryInfo = null;
      this.interrupting = false;
      // Reset stream timestamp so watchdog can force-reload after run ends
      this._lastWsStreamEventTime = 0;
      this.pendingTools = null;
      this.pendingRunId = null;
      this.pendingConfirmationId = null;
      this.toolExecuting = false;
      if (this._runningToolIds) this._runningToolIds.clear();
      // Guarantee a stats refresh on the run.done path even if a later
      // duplicate terminal event is rejected by _acceptTerminalRunEvent.
      this._schedulePostRunStatsRefresh(sessionId);
      return true;
    },

    _markRunTerminal(runId) {
      if (!runId) return;
      this._terminalRunIds.add(runId);
      if (this._terminalRunIds.size > 32) {
        this._terminalRunIds.delete(this._terminalRunIds.values().next().value);
      }
    },

    /**
     * Debounced token/cache stats refresh once a run reaches a terminal state.
     * Hooked into BOTH terminal reducers (_finalizeTerminalRun for run.done and
     * _forceSessionIdle for error/cancel/watchdog): whichever fires first marks
     * the run terminal, so the other path's inline refresh is skipped — which
     * left the toolbar / sidebar stats at pre-run zeros until a manual reload.
     */
    _schedulePostRunStatsRefresh(sessionId) {
      if (!sessionId) return;
      if (this._pendingDeleteIds && this._pendingDeleteIds.has(sessionId)) return;
      clearTimeout(this._postRunStatsTimer);
      this._postRunStatsTimer = setTimeout(() => {
        this._postRunStatsTimer = null;
        if (this.activeSession?.id !== sessionId) return;
        this.loadSessionStats(sessionId).catch(() => {});
      }, 600);
    },

    _removeEmptyAssistantMessage(runId) {
      this._scheduleMessageMutation(() => {
        const messageIndex = this._findAssistantIndexForRun(runId, { emptyOnly: true });
        if (messageIndex !== undefined) this.messages.splice(messageIndex, 1);
      });
    },

    /**
     * Locate assistant bubble for a run.
     * runId may arrive after the optimistic bubble is created — also match
     * last assistant without _runId so errors don't leave an empty shell.
     */
    _findAssistantIndexForRun(runId, { emptyOnly = false } = {}) {
      const ranked = [...this.messages]
        .map((message, index) => ({ message, index }))
        .reverse()
        .filter(({ message }) => {
          if (message.role !== 'assistant') return false;
          if (emptyOnly && (message.content || message.thinking || message._error)) return false;
          if (!runId) return true;
          return !message._runId || message._runId === runId;
        });
      if (!ranked.length) return undefined;
      // Prefer exact runId match over unbound optimistic bubble
      if (runId) {
        const exact = ranked.find(({ message }) => message._runId === runId);
        if (exact) return exact.index;
      }
      return ranked[0].index;
    },

    _markAssistantError(runId, errorText) {
      const idx = this._findAssistantIndexForRun(runId, { emptyOnly: false });
      const text = errorText || this.t('sessions.llmError') || 'Run encountered an error';
      this._scheduleMessageMutation(() => {
        if (idx === undefined) {
          this.messages.push({
            id: `msg_err_${runId || Date.now()}`,
            role: 'assistant',
            content: '',
            timestamp: new Date().toISOString(),
            _runId: runId || null,
            _error: true,
            _errorText: text,
            _errorRetryable: true,
            _thinkingComplete: true,
          });
        } else {
          const msg = this.messages[idx];
          if (runId && !msg._runId) msg._runId = runId;
          msg._error = true;
          msg._errorText = text;
          msg._errorRetryable = true;
          msg._thinkingComplete = true;
        }
      });
      // Message error UI must pull session chrome out of "运行中"
      const sid = this.activeSession?.id;
      if (sid) {
        if (this.runningSessionIds[sid]) this._setSessionRunning(sid, false);
        this._setSessionError(sid, true);
        // Sticky live-retry flag survives the post-error loadMessages soft reload
        this.sessionRetryableErrorIds[sid] = true;
        if (runId && this._activeRunId === runId) this._activeRunId = null;
      }
      this._sessionVersion++;
      this.scrollToBottom(true);
    },

    /**
     * A run that reached 'completed' must not leave a stale client-side error
     * stamp (false-positive stall timeout / watchdog race) on its bubbles.
     * Drops _error flags and standalone msg_err_* placeholder messages.
     */
    _healRunError(runId) {
      if (!runId) return;
      const sid = this.activeSession?.id;
      this._scheduleMessageMutation(() => {
        for (let i = this.messages.length - 1; i >= 0; i--) {
          const m = this.messages[i];
          if (!m) continue;
          if (m.id === `msg_err_${runId}`) {
            this.messages.splice(i, 1);
            continue;
          }
          if (m.role === 'assistant' && m._runId === runId && m._error) {
            m._error = false;
            m._errorText = '';
            m._errorRetryable = false;
          }
        }
      });
      if (sid) {
        this._setSessionError(sid, false);
        if (this.sessionRetryableErrorIds[sid]) delete this.sessionRetryableErrorIds[sid];
      }
    },

    /**
     * After loadMessages: keep error UI for empty/[Error] assistants.
     * Historical (reloaded) failures stay visible; retry is disabled.
     * While the session is still running, empty assistants are stream shells — do not heal as error.
     */
    _healEmptyAssistantErrorFromSession(sessionId) {
      const sessionIsRunning = !!(this.runningSessionIds[sessionId]);
      // Only propagate errors that are already authoritative (live _error or
      // a disk [Error] stamp). Never fabricate an error from an empty bubble —
      // that heuristic produced false positives on healthy tool-call turns.
      for (let i = 0; i < this.messages.length; i++) {
        const m = this.messages[i];
        if (m.role !== 'assistant') continue;
        const raw = String(m.content || '').trim();
        const stamped = /^\[Error\]/i.test(raw);
        if (!m._error && !stamped) continue;
        m._error = true;
        if (stamped) {
          m._errorText = raw.replace(/^\[Error\]\s*/i, '') || m._errorText;
        }
        m._errorText = m._errorText
          || this.t('sessions.llmError')
          || 'Run encountered an error';
        // Keep existing true; do not force-disable here (latest idle error
        // is re-enabled below / by canRetryError).
        if (m._errorRetryable !== true) m._errorRetryable = false;
        m._thinkingComplete = true;
      }
      // Trailing assistant error while idle → Retry enabled (incl. disk [Error])
      if (!sessionIsRunning && sessionId) {
        for (let i = this.messages.length - 1; i >= 0; i--) {
          const m = this.messages[i];
          if (m.role === 'assistant' && m._error) {
            m._errorRetryable = true;
            this.sessionRetryableErrorIds[sessionId] = true;
            break;
          }
        }
      }
    },

    isPersistedErrorStamp(message) {
      return /^\[Error\]/i.test(String(message?.content || '').trim());
    },

    displayErrorText(message) {
      if (message?._errorText) return message._errorText;
      const raw = String(message?.content || '').trim();
      if (/^\[Error\]/i.test(raw)) return raw.replace(/^\[Error\]\s*/i, '');
      return this.t('sessions.llmError') || 'Run encountered an error';
    },

    /**
     * Retry is enabled when:
     * 1. Message has an error
     * 2. Session is not running / sending
     * 3. This is the latest assistant error (no newer assistant after it)
     * Latest idle failure is always retryable (live or disk [Error] stamp).
     */
    canRetryError(message) {
      if (!message || !message._error) return false;
      if (this.isSessionRunning || this.sendGuard) return false;
      // Must be the trailing assistant failure (no newer assistant reply)
      const idx = this.messages.findIndex(m => m === message || m.id === message.id);
      if (idx < 0) return false;
      for (let i = idx + 1; i < this.messages.length; i++) {
        if (this.messages[i].role === 'assistant') return false;
      }
      // Latest error while idle: always allow (cold open / soft reload / live)
      if (message._errorRetryable === true) return true;
      const sid = this.activeSession?.id;
      if (sid && this.sessionRetryableErrorIds[sid]) return true;
      // Disk-loaded latest [Error]/empty failure — still allow manual retry
      return true;
    },

    /**
     * Retry: keep the user turn, replace failed assistant with a fresh run.
     * Works for live failures and latest disk [Error] stamps while idle.
     */
    async retryFailedMessage(errorMessage) {
      if (!this.canRetryError(errorMessage)) return;
      if (this.sendGuard) return;

      const idx = this.messages.findIndex(m => m === errorMessage || m.id === errorMessage.id);
      if (idx < 0) return;

      let userContent = null;
      for (let i = idx - 1; i >= 0; i--) {
        if (this.messages[i].role === 'user') {
          userContent = this.messages[i].content;
          break;
        }
      }
      if (!userContent || !String(userContent).trim()) {
        console.warn('retryFailedMessage: no prior user message');
        return;
      }

      const sid = this.activeSession?.id;
      if (!sid) return;

      // Cancel pending error soft-reload so it cannot wipe the new run UI
      this._cancelPendingSoftReloads();
      this.liveRetryInfo = null;
      if (this.sessionRetryableErrorIds[sid]) {
        delete this.sessionRetryableErrorIds[sid];
      }

      // Clear error state before new attempt
      this._setSessionError(sid, false);

      // Create new assistant placeholder
      const assistantMsg = {
        id: `msg_${Date.now()}_temp_ai`,
        role: 'assistant',
        content: '',
        timestamp: new Date().toISOString(),
        agent_name: this.currentAgentName,
      };

      // Atomic replacement: keep messages before the error, add new placeholder.
      // This avoids splice(remove) + push(add) across an await boundary, which
      // triggers Vue patchKeyedChildren DOM races (insertBefore on null /
      // Symbol(_vei) on null) when the API returns quickly (e.g. 4xx error).
      this.messages = [...this.messages.slice(0, idx), assistantMsg];
      await this.$nextTick();

      // Mark running before API call so UI shows "运行中" immediately
      this._setSessionRunning(sid, true);
      this._sessionVersion++;
      this.sendGuard = true;

      try {
        const runResult = await window.BoarAiStore.api.sendChatMessage(
          sid,
          String(userContent),
          this.selectedModel || null,
          this.currentAgentName || 'default',
          // Retry reuses the existing user turn — do not append a duplicate message
          { reuse_last_user: true },
        );
        assistantMsg._runId = runResult?.run_id;
        if (runResult?.run_id) {
          if (this._terminalRunIds.has(runResult.run_id)) {
            this._setSessionRunning(sid, false);
          } else {
            this._activeRunId = runResult.run_id;
            this._setSessionRunning(sid, true);
            this._startRunTimeout();
          }
        }
      } catch (e) {
        console.error('retry send failed:', e);
        this._forceSessionIdle(sid, null, { error: true });
        assistantMsg._error = true;
        assistantMsg._errorText = e?.message || this.t('sessions.llmError') || 'Retry failed';
        assistantMsg._errorRetryable = true;
        assistantMsg._thinkingComplete = true;
        if (sid) this.sessionRetryableErrorIds[sid] = true;
        this.scrollToBottom(true);
      } finally {
        this.sendGuard = false;
      }
    },

    async _resendAfterError(content) {
      if (!this.activeSession) return;
      if (this.sendGuard) return;
      this.sendGuard = true;
      try {
        const input = this.$refs.messageInput || null;
        await this._sendMessageToAPI(input, content, { skipUserBubble: true, reuseLastUser: true });
      } finally {
        this.sendGuard = false;
      }
    },

    _cancelPendingSoftReloads() {
      if (this._streamErrorReloadTimer) {
        clearTimeout(this._streamErrorReloadTimer);
        this._streamErrorReloadTimer = null;
      }
      if (this._streamDoneReloadTimer) {
        clearTimeout(this._streamDoneReloadTimer);
        this._streamDoneReloadTimer = null;
      }
      if (this._toolFinishReloadTimer) {
        clearTimeout(this._toolFinishReloadTimer);
        this._toolFinishReloadTimer = null;
      }
      // Bump seq so any in-flight loadMessages response is ignored
      this._loadMessagesSeq = (this._loadMessagesSeq || 0) + 1;
      // In-flight loadMessages finally will skip clearing when seq mismatches —
      // drop the spinner here so UI is not stuck on "loading".
      this.isLoadingMessages = false;
    },

    _interruptMessage(reason) {
      return reason === 'new_run_created'
        ? this.t('sessions.interruptedByNewMessage')
        : this.t('sessions.interrupted');
    },

    async _sendMessageToAPI(input, content, options = {}) {
      if (!this.activeSession) {
        console.error('No active session');
        return;
      }
      const { skipUserBubble = false, reuseLastUser = false } = options;
      
      const sessionId = this.activeSession.id;

      // New turn must not be wiped by a previous error/done soft-reload
      this._cancelPendingSoftReloads();
      this.liveRetryInfo = null;
      
      // Clear error state on new message
      this._setSessionError(sessionId, false);
      
      // Prepend pending workspace change message if exists
      let actualContent = content;
      if (this._pendingWorkspaceChangeMessage) {
        actualContent = this._pendingWorkspaceChangeMessage + '\n\n' + content;
        this._pendingWorkspaceChangeMessage = null;
      }
      
      // Add user message to UI immediately (optimistic update)
      // skipUserBubble: retry keeps the existing user turn
      let userMessage = null;
      if (!skipUserBubble) {
        userMessage = {
          id: `msg_${Date.now()}_temp`,
          role: 'user',
          content: content,
          timestamp: new Date().toISOString(),
          // Optimistic snapshot of pending attachments (with local previews)
          attachments: this.pendingAttachments.length
            ? this.pendingAttachments.map(a => ({ ...a }))
            : null,
        };
        this._scheduleMessageMutation(() => {
          this.messages.push(userMessage);
        });
      }
      if (input) {
        input.value = '';
        this.autoResize();
      }
      this.slashPopupVisible = false;
      this.slashFilter = '';
      this.scrollToBottom(true);
      
      // Create assistant message container
      const assistantMessage = {
        id: `msg_${Date.now()}_temp_ai`,
        role: 'assistant',
        content: '',
        timestamp: new Date().toISOString(),
        agent_name: this.currentAgentName,
      };
      this._scheduleMessageMutation(() => {
        this.messages.push(assistantMessage);
      });
      this.scrollToBottom(true);
      
      // Mark running BEFORE the WS round-trip so UI shows "运行中" immediately
      // (manual retry / re-send after error must not look idle).
      this._setSessionRunning(sessionId, true);
      this._sessionVersion++;
      
      try {
        // Send chat message via WebSocket (store handles streaming via callbacks)
        // Thinking mode is session-persisted; run path reads session.thinking_mode
        // Attachment references travel with the run; retry reuses the stored
        // user message so no new attachment payload is sent.
        const pendingRefs = !skipUserBubble && this.pendingAttachments.length
          ? this.pendingAttachments.map(({ _previewUrl, ...ref }) => ref)
          : undefined;
        const runResult = await window.BoarAiStore.api.sendChatMessage(
          sessionId,
          actualContent,
          this.selectedModel || null,
          this.currentAgentName || 'default',
          reuseLastUser ? { reuse_last_user: true } : undefined,
          pendingRefs,
        );
        if (!skipUserBubble) this.pendingAttachments = [];
        // Chat-command turn: the server intercepted the message BEFORE any
        // LLM run, persisted both turns and answered in place. Paint the
        // reply into the placeholder bubble and go idle — there is no run
        // to track, no stream to wait for.
        if (runResult?.command) {
          const cmd = runResult.command;
          assistantMessage.id = `msg_cmd_${Date.now()}`;
          assistantMessage.content = cmd.output || '';
          assistantMessage.chat_command = { name: cmd.name, kind: 'handler' };
          assistantMessage._thinkingComplete = true;
          // A failed command's text IS the reply (no error bubble): the
          // failure message was persisted as the assistant turn server-side.
          this._setSessionRunning(sessionId, false);
          this._sessionVersion++;
          this.scrollToBottom(true);
        } else if (runResult?.run_id) {
          assistantMessage._runId = runResult.run_id;
          if (this._terminalRunIds.has(runResult.run_id)) {
            // Terminal event already painted error/cancel on this bubble
            this._setSessionRunning(sessionId, false);
          } else {
            this._activeRunId = runResult.run_id;
            this._setSessionRunning(sessionId, true);
            this._startRunTimeout();
          }
        }
        
      } catch (error) {
        console.error('Failed to send message via WS:', error);
        this._forceSessionIdle(sessionId, null, { error: true });
        // Keep user bubble; put error in assistant reply slot
        assistantMessage._error = true;
        assistantMessage._errorText = error.message || this.t('notifications.sendMessageFailed');
        assistantMessage._errorRetryable = true;
        assistantMessage._thinkingComplete = true;
        this.sessionRetryableErrorIds[sessionId] = true;
        this.scrollToBottom(true);
      }
    },
    
    // Scroll listener on messages container: toggle floating button + clear unread
    // + lazy-load older history when the user scrolls near the top.
    onMessagesScroll() {
      const container = this.$refs.messagesContainer;
      if (!container) return;
      const now = Date.now();
      const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 150;
      // Programmatic scrolls (auto-follow / jump-to-latest) must not re-judge
      // sticky intent nor clear the unread pill: reflow bursts right after a
      // scrollToBottom flipped stick state and made the pill flash for <1s.
      if (now >= (this._programmaticScrollUntil || 0)) {
        this._stickToBottom = nearBottom;
        this.showScrollBtn = !nearBottom;
        if (nearBottom && (!this._unreadShownAt || now - this._unreadShownAt >= 3000)) {
          this.unreadCount = 0;
          this._unreadPending = false;
          this._unreadShownAt = 0;
        }
      }
      this._updateCurrentRound();
      // Upward-scroll lazy load of older history (seq cursor pagination).
      const nearTop = container.scrollTop < 120;
      if (nearTop && this._hasMore && !this._loadingOlder && this.activeSession) {
        const sid = this.activeSession.id;
        const running = !!(this.runningSessionIds[sid]) || !!this._activeRunId;
        if (!running) {
          this._loadingOlder = true;
          this.loadingOlder = true;
          this.loadMessages(sid, { older: true })
            .finally(() => {
              this._loadingOlder = false;
              this.loadingOlder = false;
            });
        }
      }
    },

    // Floating button click: jump to latest and clear unread
    scrollToLatest() {
      this.unreadCount = 0;
      this._unreadPending = false;
      this._unreadShownAt = 0;
      this.showScrollBtn = false;
      this.scrollToBottom(true);
    },

    scrollToBottom(force = false) {
      this.$nextTick(() => {
        const container = this.$refs.messagesContainer;
        if (!container) return;
        // Gate on sticky intent, NOT on post-growth distance: a growth burst
        // (thinking panel expand / big chunk) must not disable auto-follow.
        if (!force && !this._stickToBottom) return;
        if (force) this._stickToBottom = true;
        this.unreadCount = 0;
        this._unreadPending = false;
        this._unreadShownAt = 0;
        this.showScrollBtn = false;
        // Suppress stick/unread re-judgement while the scroll event fired by
        // this assignment lands (reflow bursts otherwise look like user scroll).
        this._programmaticScrollUntil = Date.now() + 300;
        // Absolute bottom of the scrollport. scrollIntoView on the last
        // message would leave trailing nodes (live run-status pill, padding)
        // below the fold while a run is active.
        container.scrollTop = container.scrollHeight;
      });
    },

    /** User scrolled inside a thinking panel: detach/re-attach inner follow. */
    onThinkingBodyScroll(e, message) {
      const el = e.target;
      if (!el || !message) return;
      message._thinkDetached = (el.scrollHeight - el.scrollTop - el.clientHeight) > 24;
    },

    /** Keep the streaming thinking panel's inner scroll pinned to its tail,
     *  unless the user is reading earlier reasoning (detached). */
    _followThinkingBody() {
      const container = this.$refs.messagesContainer;
      if (!container) return;
      const msgs = this.messages;
      let target = null;
      for (let i = msgs.length - 1; i >= 0; i--) {
        const m = msgs[i];
        if (m.role === 'assistant' && m.thinking && m._thinkOpen && !m._thinkingComplete) {
          target = m;
          break;
        }
      }
      if (!target || target._thinkDetached) return;
      const nodes = container.querySelectorAll('[data-message-id]');
      for (let i = nodes.length - 1; i >= 0; i--) {
        if (nodes[i].getAttribute('data-message-id') !== target.id) continue;
        const body = nodes[i].querySelector('[data-thinking-body]');
        if (body) body.scrollTop = body.scrollHeight;
        break;
      }
    },

    // Manual compression trigger
    async compressSession() {
      if (!this.activeSession || this.compressing) return;
      this.compressing = true;
      try {
        BoarAiStore.actions.info(window.i18n.t('sessions.compressing'));
        const resp = await fetch(`/api/sessions/${this.activeSession.id}/compress`, { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
          this.sessionStats.compressionCount = data.data.compression_count || 1;
          this.sessionStats.compressedSummary = data.data.compressed_summary_preview || null;
          BoarAiStore.actions.success(window.i18n.t('sessions.compressSuccess'));
          await this.loadSessionStats(this.activeSession.id);
        } else {
          BoarAiStore.actions.error(data.error?.message || 'Compression failed');
        }
      } catch (e) {
        BoarAiStore.actions.error('Compression request failed');
      } finally {
        this.compressing = false;
      }
    },

    // Fetch session token & cache stats for the info bar and sidebar
    async loadSessionStats(sessionId) {
      if (!sessionId) return;
      if (this._pendingDeleteIds && this._pendingDeleteIds.has(sessionId)) return;
      if (this.activeSession && this.activeSession.id !== sessionId) return;
      try {
        const sessionData = await window.BoarAiStore.api.getSession(sessionId, false);
        if (!sessionData) return;
        if (this.activeSession && this.activeSession.id !== sessionId) return;
        if (this._pendingDeleteIds && this._pendingDeleteIds.has(sessionId)) return;
        const cacheReqs = sessionData.cache_requests || [];
        let tokenInput = 0, tokenOutput = 0, cacheHit = 0, cacheCreation = 0;
        let model = '';
        const totals = sessionData.usage_totals;
        if (totals) {
          // WS session.get summary path: server-side aggregates
          tokenInput = totals.input || 0;
          tokenOutput = totals.output || 0;
          cacheHit = totals.cache_read || 0;
          cacheCreation = totals.cache_creation || 0;
          model = totals.model || '';
        } else {
          for (const r of cacheReqs) {
            tokenInput += r.input_tokens || 0;
            tokenOutput += (r.completion_tokens || 0);
            cacheHit += r.cache_read_tokens || 0;
            cacheCreation += r.cache_creation_tokens || 0;
            if (!model && r.model) model = r.model;
          }
        }
        const cacheMiss = Math.max(0, tokenInput - cacheHit);
        
        // Calculate tool calls count from messages
        let toolCallsCount = 0;
        let toolErrorsCount = 0;
        if (this.messages && this.messages.length > 0) {
          for (const msg of this.messages) {
            // Count tool_calls arrays
            if (msg.tool_calls && Array.isArray(msg.tool_calls)) {
              toolCallsCount += msg.tool_calls.length;
            }
            // Count tool errors
            if (msg.tool_call_id && msg.is_error) {
              toolErrorsCount += 1;
            }
          }
        }
        
        this.sessionStats = {
          messages: sessionData.message_count || this.messages.length,
          // LLM API call count: server aggregates run_turns across runs into
          // usage_totals.api_calls; fall back to the request-record count
          // when the summary path has no totals.
          apiCalls: (totals && typeof totals.api_calls === 'number')
            ? totals.api_calls
            : cacheReqs.length,
          toolCallsCount,
          toolErrorsCount,
          tokenInput, tokenOutput, model,
          cacheHit, cacheMiss,
          hitRate: tokenInput > 0 ? (cacheHit / tokenInput * 100) : 0,
          compressionCount: sessionData.compression_count || 0,
          compressedSummary: sessionData.compressed_summary || null,
          contextUsage: sessionData.context_usage || 0,
          tokenEstimates: sessionData.token_estimates || null,
          createdAt: sessionData.created_at || '',
          updatedAt: sessionData.updated_at || '',
          sessionId: sessionData.session_id || sessionId,
        };
        // Base for live counters: run.usage frames add the CURRENT run's
        // cumulative usage on top of these persisted totals (see _onRunUsage).
        this._statsBase = {
          tokenInput,
          tokenOutput,
          cacheHit,
          apiCalls: this.sessionStats.apiCalls || 0,
        };
        this._liveRunUsage = null;
      } catch (e) {
        const msg = String(e?.message || e || '');
        if (!/not found/i.test(msg)) {
          console.warn('Failed to load session stats:', e);
        }
        if (this.activeSession?.id === sessionId) {
          this.sessionStats.messages = this.messages.length;
        }
      }
    },

    formatTokens(count) {
      if (!count) return '0';
      if (count >= 1000000) return (count / 1000000).toFixed(1) + 'M';
      if (count >= 1000) return (count / 1000).toFixed(1) + 'K';
      return String(count);
    },
    formatTokensSmall(count) {
      if (!count) return '0';
      if (count >= 1000000) return (count / 1000000).toFixed(1) + 'M';
      if (count >= 10000) return Math.round(count / 1000) + 'K';
      if (count >= 1000) return (count / 1000).toFixed(1) + 'K';
      return String(count);
    },

    formatDate(iso) {
      if (!iso) return '-';
      try {
        const d = new Date(iso);
        const month = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        const hour = String(d.getHours()).padStart(2, '0');
        const min = String(d.getMinutes()).padStart(2, '0');
        const sec = String(d.getSeconds()).padStart(2, '0');
        return `${d.getFullYear()}-${month}-${day} ${hour}:${min}:${sec}`;
      } catch (e) { return iso; }
    },

    // ── File-change traceability helpers ──
    changeBadgeClass(type) {
      if (type === 'added') return 'bg-emerald-100 dark:bg-emerald-900/50 text-emerald-700 dark:text-emerald-300';
      if (type === 'deleted') return 'bg-red-100 dark:bg-red-900/50 text-red-700 dark:text-red-300';
      return 'bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300';
    },

    changeTypeLabel(type) {
      if (type === 'added') return this.t('sessions.changeAdded') || 'Added';
      if (type === 'deleted') return this.t('sessions.changeDeleted') || 'Deleted';
      return this.t('sessions.changeModified') || 'Modified';
    },

    // Per-file change record (path/type/±/diff) for a tool message, from live
    // WS payload (msg.file_change) or persisted metadata (msg.metadata).
    // Real-time changes panel: fold one tool's file mutation into the run's
    // live record the moment it finishes. The end-of-run run.diff (same
    // run_id) replaces this record with the authoritative one; loadChanges
    // preserves the in-flight record across mid-run reloads.
    _ingestLiveFileChange(sessionId, runId, change) {
      if (!sessionId || !change || !change.path) return;
      const rid = runId || `live-${sessionId}`;
      const list = this.changesBySession[sessionId] || [];
      let rec = list.find((r) => r.run_id === rid);
      if (!rec) {
        rec = { run_id: rid, ts: new Date().toISOString(), files_changed: [], unified_diff: '', stats: {} };
        this.changesBySession[sessionId] = [rec, ...list].slice(0, 50);
      }
      // Same revertibility rule as the backend: added files revert by
      // trashing; existing files need a before-snapshot or a trash id (both
      // ride on the tool's file_change payload).
      const revertible = change.existed_before === false
        ? true
        : !!(change.snapshot || change.trash_id);
      const entry = {
        path: change.path,
        change_type: change.change_type || 'modified',
        additions: change.additions || 0,
        deletions: change.deletions || 0,
        revertible,
      };
      // Latest state of a path within the run goes first: flatChangedFiles
      // takes the FIRST occurrence per path inside a record.
      rec.files_changed = [entry, ...(rec.files_changed || []).filter((f) => f.path !== entry.path)];
      rec.ts = new Date().toISOString();
    },

    getFileChange(msg) {
      if (!msg) return null;
      if (msg.file_change) return msg.file_change;
      const md = msg.metadata;
      return (md && md.file_change) || null;
    },
    getAttachedFileChange(tc) {
      const id = tc && tc.id;
      if (!id) return null;
      const m = this.messages.find(mm => mm.role === 'tool' && mm.tool_call_id === id);
      return this.getFileChange(m);
    },
    // Stored change paths are workspace-RELATIVE; join with the session
    // workspace root for the hover tooltip. Paths already absolute (file
    // edited outside the workspace) pass through unchanged.
    absFilePath(rel) {
      const p = String(rel || '');
      if (/^([a-zA-Z]:[\\/]|[\\/])/.test(p)) return p;
      const base = String(this.workspaceDisplayPath || '').replace(/[\\/]+$/, '');
      return base ? base + '/' + p : p;
    },

    async loadChanges(sessionId) {
      if (!sessionId) return;
      try {
        const recs = await window.BoarAiStore.api.getChanges(sessionId);
        const serverRecs = recs || [];
        // The in-flight run's live record is persisted only at run end; keep
        // it so a mid-run reload (session switch / WS resync) doesn't blank
        // the real-time panel.
        const prev = this.changesBySession[sessionId] || [];
        const liveRunId = this.activeSession?.id === sessionId ? this._activeRunId : null;
        const live = liveRunId ? prev.find((r) => r.run_id === liveRunId) : null;
        this.changesBySession[sessionId] =
          live && !serverRecs.some((r) => r.run_id === liveRunId)
            ? [live, ...serverRecs]
            : serverRecs;
      } catch (e) {
        // Non-critical: live run.diff events still populate the panel.
        console.warn('loadChanges failed:', e);
      }
    },

    // "Keep": accept the session's file changes — files stay on disk, the
    // server drops the change log and releases revert snapshots. Not itself
    // destructive (no confirm), but the batch becomes non-revertible.
    async acceptChanges() {
      if (!this.activeSession || this.changesBusy) return;
      const sid = this.activeSession.id;
      this.changesBusy = true;
      try {
        await window.BoarAiStore.api.acceptChanges(sid);
        window.BoarAiStore.actions.success(
          this.t('sessions.changesKept') || 'Changes kept'
        );
        await this.loadChanges(sid);
      } catch (e) {
        window.BoarAiStore.actions.error(
          e.message || (this.t('sessions.keepFailed') || 'Failed to keep changes')
        );
      } finally {
        this.changesBusy = false;
      }
    },

    // "Undo": revert every file the session touched back to its before-session
    // state (files created during it move to the Recycle Bin). Partial
    // failures keep their entries for retry (server-side prune is surgical).
    async revertAllChanges() {
      if (!this.activeSession || this.changesBusy) return;
      const count = this.flatChangedFiles.length;
      const ok = await window.BoarAiStore.actions.confirm({
        title: this.t('sessions.revertAllTitle') || 'Revert all changes',
        message: (this.t('sessions.revertAllMessage') ||
          'Restore all {count} changed file(s) to their state before this conversation? Files created during it move to the Recycle Bin.')
          .replace('{count}', count),
        confirmText: this.t('sessions.revert') || 'Revert',
        type: 'danger',
      });
      if (!ok) return;
      const sid = this.activeSession.id;
      this.changesBusy = true;
      try {
        await this._doRevert(() => window.BoarAiStore.api.revertAllChanges(sid), sid);
      } finally {
        this.changesBusy = false;
      }
    },

    // Per-row Keep: accept just this file (drop its log entries + release
    // snapshots). Disk untouched, so no confirm dialog.
    async keepOne(path) {
      if (!this.activeSession || this.changesBusy) return;
      const sid = this.activeSession.id;
      this.changesBusy = true;
      try {
        await window.BoarAiStore.api.acceptChanges(sid, path);
        window.BoarAiStore.actions.success(this.t('sessions.changeKept') || 'File kept');
        await this.loadChanges(sid);
      } catch (e) {
        window.BoarAiStore.actions.error(e.message || (this.t('sessions.keepFailed') || 'Failed to keep changes'));
      } finally {
        this.changesBusy = false;
      }
    },

    // Per-row Undo: revert just this file to its before-conversation state —
    // same earliest-snapshot semantics as batch Undo, scoped to the path.
    async undoOne(path) {
      if (!this.activeSession || this.changesBusy) return;
      const ok = await window.BoarAiStore.actions.confirm({
        title: this.t('sessions.revertFileTitle') || 'Revert file',
        message: (this.t('sessions.revertFileMessage') ||
          'Restore "{path}" to its state before this conversation?').replace('{path}', path),
        confirmText: this.t('sessions.revert') || 'Revert',
        type: 'danger',
      });
      if (!ok) return;
      const sid = this.activeSession.id;
      this.changesBusy = true;
      try {
        await this._doRevert(() => window.BoarAiStore.api.revertAllChanges(sid, path), sid);
      } finally {
        this.changesBusy = false;
      }
    },

    async _doRevert(fn, sid) {
      try {
        const res = await fn();
        const rev = (res && res.reverted) ? res.reverted.length : 0;
        const failed = (res && res.failed) ? res.failed.length : 0;
        if (failed > 0) {
          const reason = (res.failed[0] && res.failed[0].reason) || '';
          window.BoarAiStore.actions.warning(
            (this.t('sessions.revertPartial') || 'Reverted {ok}, {fail} failed')
              .replace('{ok}', rev).replace('{fail}', failed)
            + (reason ? ` (${reason})` : '')
          );
        } else {
          window.BoarAiStore.actions.success(
            (this.t('sessions.revertSuccess') || 'Reverted {ok} file(s)').replace('{ok}', rev)
          );
        }
        // Refresh: revertible flags / file states may have changed on disk.
        await this.loadChanges(sid);
      } catch (e) {
        window.BoarAiStore.actions.error(e.message || (this.t('sessions.revertFailed') || 'Revert failed'));
      }
    },

    async exportSessionJSON() {
      if (!this.activeSession) return;
      try {
        const data = await window.BoarAiStore.api.getSession(this.activeSession.id, true);
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `session-${this.activeSession.id.substring(0, 8)}.json`;
        a.click();
        URL.revokeObjectURL(url);
        window.BoarAiStore.actions.success(this.t('notifications.sessionExported'));
      } catch (e) {
        console.error('Export failed:', e);
        window.BoarAiStore.actions.error(this.t('notifications.exportFailed'));
      }
    },

    /** Normalize + apply thinking_mode from session payload (including "none"). */
    _applySessionThinkingMode(sessionOrData) {
      const raw = sessionOrData?.thinking_mode;
      const mode = (raw === undefined || raw === null || raw === '')
        ? 'none'
        : String(raw).toLowerCase();
      const allowed = new Set(['none', 'off', 'light', 'deep', 'ultra']);
      this.thinkingMode = allowed.has(mode) ? mode : 'none';
      if (this.activeSession) {
        this.activeSession.thinking_mode = this.thinkingMode;
      }
      this.loadThinkingCapabilities();
    },

    /** Load the effective thinking mapping for the selected model. */
    async loadThinkingCapabilities() {
      const seq = (this._thinkingCapsSeq || 0) + 1;
      this._thinkingCapsSeq = seq;
      const model = this.selectedModel || '';
      try {
        const url = '/api/llm/thinking' + (model ? `?model=${encodeURIComponent(model)}` : '');
        const response = await fetch(url);
        const json = await response.json();
        if (seq !== this._thinkingCapsSeq) return;
        this.thinkingCapabilities = (json && json.success) ? json.data : null;
      } catch (e) {
        if (seq !== this._thinkingCapsSeq) return;
        this.thinkingCapabilities = null;
      }
    },

    /** Keep sessions[] row + activeSession in sync after local edit. */
    _syncThinkingModeToSessionList(sessionId, mode) {
      if (this.activeSession?.id === sessionId) {
        this.activeSession.thinking_mode = mode;
      }
      const row = this.sessions.find(s => s.id === sessionId);
      if (row) row.thinking_mode = mode;
    },

    // Thinking mode: session-scoped only (persist + read from session data)
    selectThinkingMode(mode) {
      this.thinkingMode = mode;
      this.showThinkingPopover = false;
      this.onThinkingModeChange();
    },

    selectApprovalMode(mode) {
      this.approvalMode = mode;
      this.showApprovalPopover = false;
      this.onApprovalModeChange();
    },

    onThinkingModeChange() {
      const sessionId = this.activeSession?.id;
      if (!sessionId) return;
      const mode = this.thinkingMode || 'none';
      this._syncThinkingModeToSessionList(sessionId, mode);
      window.BoarAiStore.api.updateSessionThinkingMode(sessionId, mode)
        .then(() => {
          this._syncThinkingModeToSessionList(sessionId, mode);
        })
        .catch((e) => {
          console.warn('Failed to persist thinking_mode:', e);
        });
    },

    /** Normalize + apply approval mode from session payload (security_overrides). */
    _applySessionApprovalMode(sessionOrData) {
      const overrides = sessionOrData?.security_overrides || {};
      const raw = overrides.approval_mode || sessionOrData?.approval_mode || '';
      const mode = String(raw).toLowerCase();
      // deny → deny; off → allow; on/smart → ask; unset → ask (global default gating)
      this.approvalMode = mode === 'deny' ? 'deny' : mode === 'off' ? 'allow' : 'ask';
    },

    /**
     * Welcome screen: preselect the configured new-session default
     * (/api/settings/session → session.default_approval_mode). Only applies
     * while no session is active, so an open session's own mode is never
     * clobbered by a late response.
     */
    async _loadDefaultApprovalMode() {
      try {
        const res = await fetch('/api/settings/session');
        const body = await res.json();
        const mode = String(body?.data?.default_approval_mode || 'ask').toLowerCase();
        if (!this.activeSession && ['deny', 'ask', 'allow'].includes(mode)) {
          this.approvalMode = mode;
        }
      } catch (e) {
        console.warn('Failed to load default approval mode:', e);
      }
    },

    /** Keep sessions[] row + activeSession overrides in sync after local edit. */
    _syncApprovalModeToSessionList(sessionId, uiMode) {
      const backend = uiMode === 'deny' ? 'deny' : uiMode === 'allow' ? 'off' : 'on';
      const patch = (s) => {
        if (!s) return;
        s.security_overrides = { ...(s.security_overrides || {}), approval_mode: backend };
      };
      if (this.activeSession?.id === sessionId) patch(this.activeSession);
      patch(this.sessions.find(s => s.id === sessionId));
    },

    // Approval mode: session-scoped (persist to security_overrides.approval_mode)
    onApprovalModeChange() {
      const sessionId = this.activeSession?.id;
      if (!sessionId) return;
      const uiMode = this.approvalMode || 'ask';
      const backend = uiMode === 'deny' ? 'deny' : uiMode === 'allow' ? 'off' : 'on';
      this._syncApprovalModeToSessionList(sessionId, uiMode);
      window.BoarAiStore.api.updateSessionApprovalMode(sessionId, backend)
        .then(() => {
          this._syncApprovalModeToSessionList(sessionId, uiMode);
        })
        .catch((e) => {
          console.warn('Failed to persist approval_mode:', e);
        });
    },
    onModelChange() {
      // Persist to session JSON on backend (authoritative source)
      if (this.activeSession?.id) {
        window.BoarAiStore.api.updateSessionModel(this.activeSession.id, this.selectedModel)
          .catch(() => {}); // Non-critical
      }
      // Refresh the effective thinking mapping (model capabilities differ).
      this.loadThinkingCapabilities();
    },
    // ── Transcript display mode ──
    // Per-index plan lookup for the template (computed array; O(1)).
    planAt(index) {
      return this.transcriptPlan[index] || FLAT_PLAN_ENTRY;
    },

    // Fold-row click: expand/collapse one activity segment in place. The
    // segment's rows render their process blocks again via showProcess.
    toggleFoldSeg(index) {
      const entry = this.planAt(index);
      if (!entry.segId) return;
      if (entry.segOpen) this.foldOpenSegs.delete(entry.segId);
      else this.foldOpenSegs.add(entry.segId);
      this._saveDisplayState();
    },

    // Round-header eye button: force this round flat (show all process) or
    // follow the global display mode again.
    toggleRoundFlat(index) {
      const round = this.planAt(index).round;
      if (!round || !round.hasFold) return;
      this.roundFlatOverrides[round.roundId] = !round.override;
      this._saveDisplayState();
    },

    // ── Display-state persistence ──
    // Fold/override choices are per-session UI state the user expects to
    // survive reloads and session switches; stored under one small key.
    _displayStateKey(sessionId) {
      return `boarai_display_state:${sessionId}`;
    },
    _loadDisplayState(sessionId) {
      this.foldOpenSegs = new Set();
      this.roundFlatOverrides = {};
      if (!sessionId) return;
      try {
        const raw = localStorage.getItem(this._displayStateKey(sessionId));
        if (!raw) return;
        const saved = JSON.parse(raw);
        if (saved && Array.isArray(saved.o)) {
          this.foldOpenSegs = new Set(saved.o.filter((v) => typeof v === 'string'));
        }
        if (saved && Array.isArray(saved.f)) {
          for (const id of saved.f) if (typeof id === 'string') this.roundFlatOverrides[id] = true;
        }
      } catch (e) { /* corrupt or blocked storage: start fresh */ }
    },
    _saveDisplayState() {
      const sessionId = this.activeSession && this.activeSession.id;
      if (!sessionId) return;
      try {
        const o = [...this.foldOpenSegs].slice(-300);
        const f = Object.keys(this.roundFlatOverrides)
          .filter((k) => this.roundFlatOverrides[k]).slice(-300);
        if (!o.length && !f.length) {
          localStorage.removeItem(this._displayStateKey(sessionId));
          return;
        }
        localStorage.setItem(this._displayStateKey(sessionId), JSON.stringify({ o, f }));
      } catch (e) { /* storage full/blocked: non-critical reading aid */ }
    },

    selectDisplayMode(mode) {
      if (!DISPLAY_POLICIES[mode]) return;
      this.displayMode = mode;
      this.showDisplayPopover = false;
      try { localStorage.setItem(DISPLAY_MODE_STORAGE_KEY, mode); } catch (e) {}
    },

    // Fold-row label: "过程 · 思考 · 3 次工具 · 12s" (interpunct-joined parts).
    processFoldLabel(seg) {
      if (!seg) return '';
      const parts = [this.t('sessions.processFold') || 'Process'];
      if (seg.thinkCount > 0) parts.push(this.t('sessions.processFoldThinking') || 'Thinking');
      // Single-tool runs name the tool (scent the folded card used to give);
      // multi-tool runs show the count.
      if (seg.toolCount === 1 && seg.firstToolName) parts.push(seg.firstToolName);
      else if (seg.toolCount > 0) {
        parts.push(this.t('sessions.processFoldTools', { count: seg.toolCount }) || `${seg.toolCount} tools`);
      }
      if (seg.seconds !== null && seg.seconds >= 1) parts.push(this.formatDuration(seg.seconds));
      return parts.join(' · ');
    },

    _setSessionRunning(sessionId, running) {
      if (!sessionId) return;
      const was = !!this.runningSessionIds[sessionId];
      if (running) {
        if (!was) {
          this.runningSessionIds[sessionId] = true;
        }
        // Entering a new run clears sticky error (same as send path)
        if (this.sessionErrorIds[sessionId]) {
          delete this.sessionErrorIds[sessionId];
        }
      } else {
        if (was) {
          delete this.runningSessionIds[sessionId];
        }
        // Leaving running state must clear tool mid-flight flag (missed tool.finished)
        if (sessionId === this.activeSession?.id) {
          this.toolExecuting = false;
        }
      }
      // Sync to session objects for template reactivity
      for (const s of this.sessions) {
        if (s.id === sessionId) {
          s.isRunning = !!running;
          if (running) s.hasError = false;
          break;
        }
      }
      // Bump on both enter and leave so "运行中" pill / stop button re-render
      if (was !== !!running) this._sessionVersion++;
    },

    /**
     * Any successful stream progress (content / thinking / tool activity)
     * proves the provider recovered from a retryable error: drop the live
     * retry banner immediately instead of waiting for the first content
     * chunk or run.done (thinking-only / tool-first turns stalled the pill).
     */
    _clearRetryProgress(runId) {
      if (this.liveRetryInfo && (!runId || !this.liveRetryInfo.runId || this.liveRetryInfo.runId === runId)) {
        this.liveRetryInfo = null;
      }
      // The retry card is annotated heuristically onto "the last assistant
      // bubble", but a recovered retry streams into a NEW per-turn bubble
      // (server message_id routing). Without a sweep the annotated bubble
      // (typically a tool-call-only turn with empty content) keeps showing
      // the spinning retry card forever.
      this._clearRetryAnnotations(runId);
    },

    /** Drop live-only retry fields from message bubbles (runId-scoped when known). */
    _clearRetryAnnotations(runId) {
      this._scheduleMessageMutation(() => {
        const msgs = this.messages;
        for (let i = msgs.length - 1; i >= 0; i--) {
          const m = msgs[i];
          if (!m || !m._retryAttempt) continue;
          if (runId && m._runId && m._runId !== runId) continue;
          m._retryAttempt = null;
          m._retryMax = null;
          m._retryError = null;
        }
      });
    },

    /**
     * Single reduce for terminal run: always clear running + activeRunId.
     * error:true → sticky hasError; error:false → clear sticky error (completed/cancel).
     */
    _forceSessionIdle(sessionId, runId, { error = false } = {}) {
      if (sessionId === this.activeSession?.id) {
        this.liveRetryInfo = null;
        // Error / cancel / watchdog terminal: drop leftover retry cards too.
        this._clearRetryAnnotations(runId);
      }
      if (!sessionId) return;
      if (runId) this._markRunTerminal(runId);
      if (runId) this._interruptingRunIds.delete(runId);
      this._setSessionRunning(sessionId, false);
      if (sessionId === this.activeSession?.id) {
        // Always drop tracked run on active session idle — server has no live run
        // (or we are forcing terminal). Stale _activeRunId kept the watchdog in a
        // half-live state and the "运行中" pill never left.
        if (!runId || !this._activeRunId || runId === this._activeRunId) {
          this._activeRunId = null;
        } else {
          // Mismatched runId still means "go idle" for UI chrome on this session
          this._activeRunId = null;
        }
        this.interrupting = false;
        this.pendingTools = null;
        this.pendingRunId = null;
        this.pendingConfirmationId = null;
        this.toolExecuting = false;
        if (this._runningToolIds) this._runningToolIds.clear();
      } else if (runId && this._activeRunId === runId) {
        this._activeRunId = null;
      }
      this._setSessionError(sessionId, !!error);
      this._healAllThinkingComplete(sessionId);
      // Error / cancel / watchdog terminal path: refresh token+cache stats so
      // the toolbar & sidebar don't keep pre-run zeros until a manual reload.
      this._schedulePostRunStatsRefresh(sessionId);
    },

    /**
     * Mark all assistant messages' thinking as complete.
     * Ensures no stale pulsing cursor after run termination
     * (cancelled, error, WS reconnect, polling belt-and-suspenders, etc.).
     */
    _healAllThinkingComplete(sessionId) {
      // Only heal messages currently on screen; if another session
      // triggered _forceSessionIdle, do not touch active messages.
      if (sessionId !== this.activeSession?.id) return;
      if (!Array.isArray(this.messages)) return;
      for (let i = this.messages.length - 1; i >= 0; i--) {
        const msg = this.messages[i];
        if (msg.role === 'assistant' && msg.thinking && !msg._thinkingComplete) {
          this._finalizeThinkingTime(msg);
        }
      }
    },

    /**
     * Settle a turn's thinking timer: mark complete and record elapsed seconds.
     * Single source of truth so every completion path (run.done, tool finish,
     * heal on cancel/error) uses identical seconds-unit math.
     */
    _finalizeThinkingTime(msg) {
      msg._thinkingComplete = true;
      const start = msg._thinkingStart || Date.now();
      const elapsed = Math.max(0.1, (Date.now() - start) / 1000);
      msg._thinkingTime = elapsed;
      if (!this._thinkingTimeCache) this._thinkingTimeCache = {};
      this._thinkingTimeCache[msg.id] = elapsed;
    },

    /**
     * Fold a bubble's thinking panel the moment the turn MOVES PAST thinking
     * (first content chunk or tool call start) — not at run end. Multi-turn
     * runs otherwise keep every earlier panel open until the whole run
     * finishes. No-op once folded, so per-chunk calls are cheap.
     */
    _foldThinking(target) {
      if (!target || !target.thinking || !target._thinkOpen) return;
      if (!target._thinkingComplete) this._finalizeThinkingTime(target);
      target._thinkOpen = false;
    },

    _startRunTimeout() {
      this._clearRunTimeout();
      // Bind the armed run/session so a later session switch or a newer run
      // can never let this timer stamp an error onto an unrelated view.
      const armedRunId = this._activeRunId || null;
      const armedSessionId = this.activeSession?.id || null;
      // If no stream event within 45s, assume connection stalled and surface error
      this._runTimeoutId = setTimeout(() => {
        this._runTimeoutId = null;
        if (!this._activeRunId || !this.activeSession) return;
        const stuckRunId = this._activeRunId;
        const sessionId = this.activeSession.id;
        if (armedRunId && stuckRunId !== armedRunId) return;
        if (armedSessionId && sessionId !== armedSessionId) return;
        // Verify against the server before stamping: long tool executions or
        // a slow first token are NOT stalls, and a run that already finished
        // must never leave a false error card behind.
        const settle = (status) => {
          if (this._activeRunId !== stuckRunId) return;
          if (status === 'running' || status === 'queued') {
            this._startRunTimeout();
            return;
          }
          if (status === 'completed' || status === 'cancelled') {
            this._forceSessionIdle(sessionId, stuckRunId, { error: false });
            return;
          }
          console.warn('Run timed out waiting for LLM response:', stuckRunId);
          this._forceSessionIdle(sessionId, stuckRunId, { error: true });
          this._markAssistantError(
            stuckRunId,
            this.t('sessions.llmTimeout') || 'LLM connection timed out — check provider settings and retry',
          );
        };
        if (window.BoarAiStore?.api?.getRun) {
          window.BoarAiStore.api.getRun(stuckRunId)
            .then((r) => settle(r?.status || r?.data?.status || null))
            .catch(() => settle(null));
        } else {
          settle(null);
        }
      }, 45000);
    },

    _clearRunTimeout() {
      if (this._runTimeoutId) {
        clearTimeout(this._runTimeoutId);
        this._runTimeoutId = null;
      }
    },

    _setSessionError(sessionId, hasError) {
      if (!sessionId) return;
      if (hasError) {
        this.sessionErrorIds[sessionId] = true;
      } else {
        if (this.sessionErrorIds[sessionId]) {
          delete this.sessionErrorIds[sessionId];
        }
        // Clearing error also drops live-retry (new turn / success / cancel)
        if (this.sessionRetryableErrorIds[sessionId]) {
          delete this.sessionRetryableErrorIds[sessionId];
        }
      }
      // Sync error flag to the session object in the list for template reactivity.
      for (const s of this.sessions) {
        if (s.id === sessionId) {
          s.hasError = !!hasError;
          break;
        }
      }
      this._sessionVersion++;
    },

    /**
     * Clear toolExecuting when every open tool_call already has a tool-role result.
     * Prevents permanent "执行中" after a lost run.tool.finished frame.
     */
    /**
     * Recompute sidebar tool counters from the live message list. Same rules
     * as loadSessionStats (tool_calls arrays → calls, error tool rows →
     * errors) so mid-run ticks and post-reload totals never disagree.
     */
    _recountToolStats() {
      let calls = 0;
      let errors = 0;
      for (const msg of (this.messages || [])) {
        if (msg.tool_calls && Array.isArray(msg.tool_calls)) {
          calls += msg.tool_calls.length;
        }
        if (msg.tool_call_id && msg.is_error) {
          errors += 1;
        }
      }
      this.sessionStats.toolCallsCount = calls;
      this.sessionStats.toolErrorsCount = errors;
    },

    _syncToolExecutingFlag() {
      const openIds = this._openToolCallIds();
      if (openIds.length === 0) {
        this.toolExecuting = false;
        if (this._runningToolIds) this._runningToolIds.clear();
        return;
      }
      // Still have unpaired tool_calls while session run is live → keep mid-flight
      if (this.isSessionRunning) {
        this.toolExecuting = true;
      }
    },

    _openToolCallIds() {
      const ids = [];
      for (const m of this.messages) {
        if (m.role !== 'assistant' || !m.tool_calls?.length) continue;
        for (const tc of m.tool_calls) {
          if (!tc?.id) continue;
          const hasResult = this.messages.some(
            (x) => x.role === 'tool' && x.tool_call_id === tc.id,
          );
          if (!hasResult) ids.push(tc.id);
        }
      }
      return ids;
    },

    async _onWsReconnected() {
      const sid = this.activeSession?.id;
      if (!sid) return;
      try {
        // session.get already re-subscribed in store; pull messages + run status
        await this._reconcileActiveRun(sid, { forceLoadMessages: true });
      } catch (e) {
        console.warn('post-reconnect reconcile failed:', e);
      }
    },

    /**
     * Watchdog: while UI thinks a run/tool is live, poll server truth.
     * Also: after refresh, if stop button never came back, probe once per tick
     * when session is open and we have no active run id yet (cheap subscribe).
     */
    async _runWatchdogTick() {
      if (this._watchdogBusy) return;
      const sid = this.activeSession?.id;
      if (!sid) return;
      const live =
        this.isSessionRunning
        || this.toolExecuting
        || !!this._activeRunId
        || this._openToolCallIds().length > 0
        || !!this.liveRetryInfo;
      // Cold page after refresh: isSessionRunning false until resume succeeds —
      // still probe occasionally so stop button can appear without full remount.
      const shouldProbeCold =
        !live
        && !this._coldProbeDone
        && !!sid;
      if (!live && !shouldProbeCold) return;
      this._watchdogBusy = true;
      try {
        if (shouldProbeCold) {
          this._coldProbeDone = true;
          await this._resumeRunStreaming(sid);
        } else {
          // forceLoad when only sticky running (no open tools) so disk + idle heal
          const force = this.isSessionRunning && this._openToolCallIds().length === 0;
          await this._reconcileActiveRun(sid, { forceLoadMessages: force });
        }
      } finally {
        this._watchdogBusy = false;
      }
    },

    async _reconcileActiveRun(sessionId, { forceLoadMessages = false } = {}) {
      if (!sessionId || sessionId !== this.activeSession?.id) return;
      if (!window.BoarAiStore?.api) return;

      const openBefore = this._openToolCallIds();
      let runInfo = null;
      try {
        runInfo = await window.BoarAiStore.api.subscribeToActiveRun(sessionId);
      } catch (e) {
        console.warn('subscribeToActiveRun during reconcile failed:', e);
      }

      const activeRunId = runInfo?.run_id || null;
      const tracked = this._activeRunId;
      const uiThinksLive =
        this.isSessionRunning
        || this.toolExecuting
        || !!tracked
        || openBefore.length > 0;

      // No server-side RUNNING/QUEUED run — local UI must go idle.
      // Previously: if tracked was already terminal but _activeRunId lingered,
      // or isSessionRunning with null tracked, the pill stayed on "运行中".
      if (!activeRunId) {
        if (!uiThinksLive && !forceLoadMessages) return;

        let terminalStatus = null;
        let terminalError = null;
        if (tracked) {
          try {
            const r = await window.BoarAiStore.api.getRun(tracked);
            terminalStatus = r?.status || r?.data?.status || null;
            terminalError = r?.error || r?.data?.error || null;
          } catch (_) { /* run may already be GC'd */ }
        }

        await this.loadMessages(sessionId, { force: true });
        this._syncToolExecutingFlag();

        if (terminalStatus === 'failed' || terminalStatus === 'error') {
          this._forceSessionIdle(sessionId, tracked, { error: true });
          this._markAssistantError(
            tracked,
            terminalError || this.t('sessions.llmError') || 'Run failed',
          );
        } else if (terminalStatus === 'cancelled') {
          this._forceSessionIdle(sessionId, tracked, { error: false });
          this._removeEmptyAssistantMessage(tracked);
        } else {
          // completed / unknown / no tracked id — always clear stuck running
          if (terminalStatus === 'completed' && tracked) this._healRunError(tracked);
          this._forceSessionIdle(sessionId, tracked || this._activeRunId, { error: false });
          this._setSessionError(sessionId, false);
        }
        this.liveRetryInfo = null;
        this.toolExecuting = false;
        if (this._runningToolIds) this._runningToolIds.clear();
        this.loadSessions();
        return;
      }

      // Server still has a running run — keep UI in sync
      if (!tracked) {
        this._activeRunId = activeRunId;
      } else if (tracked !== activeRunId) {
        // Server advanced to a newer run; drop stale local id
        this._activeRunId = activeRunId;
      }
      this._setSessionRunning(sessionId, true);

      // Reload messages if tools still open or forced (reconnect).
      // When WS stream is actively delivering events, skip force-replace to avoid
      // wiping in-progress streaming state (insertBefore/emitsOptions null-node
      // errors when Vue DOM patcher meets a replaced array mid-stream).
      const wsStreamActive = this._lastWsStreamEventTime > 0
        && (Date.now() - this._lastWsStreamEventTime) < 10000;
      const shouldForce = forceLoadMessages && !wsStreamActive;
      if (shouldForce || openBefore.length > 0 || this.toolExecuting) {
        await this.loadMessages(sessionId, { force: shouldForce });
        this._syncToolExecutingFlag();
      }
    },
    
    // Resume streaming after page reload / session select for an ongoing run.
    // Must set isSessionRunning so the stop button reappears after refresh.
    async _resumeRunStreaming(sessionId) {
      if (!sessionId) return;
      try {
        // Paint disk history immediately — do NOT wait for WS first.
        // Waiting for subscribe before loadMessages left a blank pane for seconds
        // (or forever when running-guard skipped an empty local list).
        await this.loadMessages(sessionId, { force: true });
        if (sessionId !== this.activeSession?.id) return;

        // Wait for WS so session.run.subscribe is not silently dropped
        if (window.BoarAiStore?._waitForWsConnection) {
          try {
            await window.BoarAiStore._waitForWsConnection(8000);
          } catch (_) { /* fall through; subscribe may still HTTP-fail */ }
        }
        if (sessionId !== this.activeSession?.id) return;

        let info = null;
        // Short retry: first paint after refresh often races WS open
        for (let attempt = 0; attempt < 3; attempt++) {
          info = await window.BoarAiStore.api.subscribeToActiveRun(sessionId);
          if (info?.run_id) break;
          if (attempt < 2) await new Promise((r) => setTimeout(r, 400 * (attempt + 1)));
        }

        if (sessionId !== this.activeSession?.id) return;

        if (info?.run_id) {
          this._activeRunId = info.run_id;
          this._setSessionRunning(sessionId, true);
          // Optional: paint partial assistant text already streamed before refresh
          if (info.resume && typeof info.resume === 'string' && info.resume.length) {
            const msgs = this.messages;
            let found = false;
            for (let i = msgs.length - 1; i >= 0; i--) {
              if (msgs[i].role === 'assistant') {
                // Only the in-progress bubble may be repainted: its disk /
                // checkpoint text (plus chunks received since) is a PREFIX of
                // the server's live turn text. A completed turn's text is not
                // a prefix, so without this guard a stale or over-broad resume
                // payload splats a whole transcript into one bubble (the
                // mid-run view-switch duplication bug).
                const cur = msgs[i].content || '';
                if (cur.length < info.resume.length && info.resume.startsWith(cur)) {
                  msgs[i].content = info.resume;
                  msgs[i]._runId = info.run_id;
                }
                found = true;
                break;
              }
            }
            if (!found) {
              this._scheduleMessageMutation(() => {
                this.messages.push({
                  id: `msg_resume_${info.run_id}`,
                  role: 'assistant',
                  content: info.resume,
                  timestamp: new Date().toISOString(),
                  _runId: info.run_id,
                });
              });
            }
          }
          // Re-check once: run may have finished during subscribe retries
          try {
            const again = await window.BoarAiStore.api.subscribeToActiveRun(sessionId);
            if (sessionId !== this.activeSession?.id) return;
            if (!again?.run_id) {
              this._forceSessionIdle(sessionId, info.run_id, { error: false });
              await this.loadMessages(sessionId, { force: true });
            }
          } catch (_) { /* keep running; watchdog will reconcile */ }
        } else {
          // No live run on server — never leave a sticky "运行中" after F5
          if (this.isSessionRunning || this._activeRunId || this.toolExecuting) {
            this._forceSessionIdle(sessionId, this._activeRunId, { error: false });
          }
          this.liveRetryInfo = null;
          this.toolExecuting = false;
          if (this._runningToolIds) this._runningToolIds.clear();
        }

        this._syncToolExecutingFlag();
      } catch (error) {
        console.warn('Failed to resume run streaming:', error);
        try {
          await this.loadMessages(sessionId, { force: true });
          if (sessionId === this.activeSession?.id) {
            this._forceSessionIdle(sessionId, this._activeRunId, { error: false });
          }
        } catch (_) { /* ignore */ }
      }
    },

    // ==================== Agent Selector ====================

    async loadAgents() {
      try {
        const res = await fetch('/api/agents');
        const result = await res.json();
        if (result.success) {
          this.agentOptions = result.data.agents || [];
          // Set default agent
          const def = this.agentOptions.find(a => a.is_default);
          if (def) this.currentAgentName = def.name;
        }
      } catch (e) {
        console.warn('Failed to load agents:', e);
      }
    },

    async changeAgent() {
      if (!this.activeSession) return;
      try {
        await window.BoarAiStore.api.renameSession(this.activeSession.id, this.activeSession.title);
        // Optional: seed session thinking_mode from agent default when switching agent
        let newThinkingMode = 'none';
        const newAgent = this.agentOptions.find(a => a.name === this.currentAgentName);
        if (newAgent && newAgent.thinking_mode) {
          newThinkingMode = String(newAgent.thinking_mode).toLowerCase();
        }
        const res = await fetch(`/api/sessions/${this.activeSession.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            agent_name: this.currentAgentName,
            thinking_mode: newThinkingMode,
          }),
        });
        const result = await res.json();
        if (result.success) {
          this._applySessionThinkingMode({ thinking_mode: newThinkingMode });
          this._syncThinkingModeToSessionList(this.activeSession.id, this.thinkingMode);
          window.BoarAiStore.actions.success(this.t('notifications.agentChanged', { name: this.currentAgentName }));
        }
      } catch (e) {
        console.error('Failed to change agent:', e);
      }
    },

    // ==================== Tool Confirmation ====================

    approveTools() {
      if (!this.pendingRunId) return;
      this._sendToolConfirm(true, 'once');
    },

    approveToolsAlways() {
      if (!this.pendingRunId) return;
      this._sendToolConfirm(true, 'session');
    },

    approveToolsFolder() {
      if (!this.pendingRunId) return;
      this._sendToolConfirm(true, 'folder');
    },


    rejectTools() {
      if (!this.pendingRunId) return;
      this._sendToolConfirm(false, 'once');
    },

    async _sendToolConfirm(approved, grantScope) {
      const runId = this.pendingRunId;
      const confirmationId = this.pendingConfirmationId || '';
      this.pendingRunId = null;
      this.pendingTools = null;
      this.pendingConfirmationId = null;
      // Clear the global navbar badge immediately (backend echoes tool.start /
      // tool.finished shortly after, but don't wait for the round-trip).
      if (this.activeSession?.id && window.BoarAiStore) {
        window.BoarAiStore._clearPendingApproval(this.activeSession.id);
      }
      try {
        const result = await window.BoarAiStore._sendWsMessage('tool.confirm', {
          run_id: runId,
          confirmation_id: confirmationId,
          approved,
          grant_scope: grantScope || 'once',
        });
      } catch (e) {
        console.error('Failed to send tool confirmation:', e);
      }
    },


    async openDirPicker() {
      if (!this.activeSession?.id) return;
      this.showDirPicker = true;
      this.dirPicker.error = '';
      this.dirPicker.selected = this.workspaceDisplayPath || '';
      try {
        const rootsData = await window.BoarAiStore.api.fsRoots(this.activeSession.id);
        this.dirPicker.roots = rootsData.roots || [];
      } catch (e) {
        this.dirPicker.roots = [];
      }
      // 起始路径：当前 workspace；若无效/不存在则回退到项目根（空 path）
      const start = this.workspaceDisplayPath || '';
      const ok = await this.browseDir(start || null, { fallbackToRoot: true });
      if (!ok && start) {
        // 保留用户原 workspace 显示在输入框，便于手动改
        this.dirPicker.pathInput = start;
        this.dirPicker.selected = start;
      }
    },

    closeDirPicker() {
      this.showDirPicker = false;
    },

    /**
     * @param {string|null} path
     * @param {{fallbackToRoot?: boolean}} opts  路径无效时是否自动回退到默认根目录
     * @returns {Promise<boolean>} 是否浏览成功
     */
    async browseDir(path, opts = {}) {
      const fallbackToRoot = !!opts.fallbackToRoot;
      this.dirPicker.loading = true;
      this.dirPicker.error = '';
      try {
        const data = await window.BoarAiStore.api.fsBrowse(path || '', false);
        this.dirPicker.path = data.path || '';
        this.dirPicker.pathInput = data.path || '';
        this.dirPicker.parent = data.parent || null;
        this.dirPicker.entries = data.entries || [];
        this.dirPicker.selected = data.path || '';
        return true;
      } catch (e) {
        const msg = String(e?.message || e || 'Browse failed');
        // 打开选择器时：无效/不存在路径不阻断 UI，回退到项目根
        if (fallbackToRoot && path) {
          try {
            const data = await window.BoarAiStore.api.fsBrowse('', false);
            this.dirPicker.path = data.path || '';
            this.dirPicker.pathInput = data.path || '';
            this.dirPicker.parent = data.parent || null;
            this.dirPicker.entries = data.entries || [];
            this.dirPicker.selected = data.path || '';
            this.dirPicker.error = '';
            return true;
          } catch (e2) {
            this.dirPicker.error = String(e2?.message || e2 || msg);
            this.dirPicker.entries = [];
            return false;
          }
        }
        this.dirPicker.error = msg;
        this.dirPicker.entries = [];
        return false;
      } finally {
        this.dirPicker.loading = false;
      }
    },

    goDirUp() {
      if (this.dirPicker.parent) this.browseDir(this.dirPicker.parent);
    },

    async confirmDirPicker() {
      if (!this.activeSession?.id) return;
      const path = this.dirPicker.selected || this.dirPicker.path;
      if (!path) {
        window.BoarAiStore.actions.error(this.t('sessions.workspacePickRequired') || 'Select a folder');
        return;
      }
      try {
        const data = await window.BoarAiStore.api.updateSessionWorkspace(this.activeSession.id, path);
        const wp = data.workspace_path || path;
        this.activeSession.workspace_path = wp;
        // keep list entry in sync
        const s = this.sessions.find(x => x.id === this.activeSession.id || x.session_id === this.activeSession.id);
        if (s) s.workspace_path = wp;
        this.showDirPicker = false;
        window.BoarAiStore.actions.success(this.t('sessions.workspaceUpdated') || 'Workspace updated');
        
        // Store for next user message (will be prepended to user content)
        this._pendingWorkspaceChangeMessage = `[BoarAi_System] Workspace: ${wp}`;
        
        // Reload messages to show the system message saved by backend
        await this.loadMessages(this.activeSession.id);
      } catch (e) {
        window.BoarAiStore.actions.error(String(e?.message || e));
      }
    },

    async clearWorkspace() {
      if (!this.activeSession?.id) return;
      try {
        // Get configured default workspace for this session
        const defaultData = await window.BoarAiStore.api.fsDefaultWorkspace(this.activeSession.id);
        const defaultPath = defaultData?.path || '';
        
        await window.BoarAiStore.api.updateSessionWorkspace(this.activeSession.id, defaultPath);
        this.activeSession.workspace_path = defaultPath;
        const s = this.sessions.find(x => x.id === this.activeSession.id || x.session_id === this.activeSession.id);
        if (s) s.workspace_path = defaultPath;
        this.showDirPicker = false;
        
        const msg = defaultPath 
          ? `[BoarAi_System] Workspace: ${defaultPath}`
          : '[BoarAi_System] Workspace: (default)';
        this._pendingWorkspaceChangeMessage = msg;
        
        window.BoarAiStore.actions.success(this.t('sessions.workspaceReset') || 'Workspace reset to default');
        await this.loadMessages(this.activeSession.id);
      } catch (e) {
        window.BoarAiStore.actions.error(String(e?.message || e));
      }
    },

    // ==================== Tool Call Display Helpers ====================

    getToolName(tc) {
      if (!tc) return '?';
      if (typeof tc === 'string') return tc;
      const func = tc.function || {};
      return func.name || tc.name || 'unknown';
    },

    getToolArgs(tc) {
      if (!tc) return '';
      if (typeof tc === 'string') return tc;
      const func = tc.function || {};
      let args = func.arguments || tc.arguments || '';
      if (typeof args === 'object') {
        try { args = JSON.stringify(args); } catch (e) { args = String(args); }
      }
      // Full args for ToolCallCard (it truncates for display); do not pre-cut here
      return args || '';
    },

    // Find the tool name for a tool role message by matching tool_call_id
    // against the previous assistant message's tool_calls array.
    getToolNameForToolMsg(message) {
      if (!message) return 'Tool';
      if (message.tool_name) return message.tool_name;
      if (!message.tool_call_id) return 'Tool';
      const idx = this.messages.indexOf(message);
      // Search backwards for the assistant msg that contained this tool_call_id
      for (let i = idx - 1; i >= 0; i--) {
        const prev = this.messages[i];
        if (prev.role === 'assistant' && prev.tool_calls) {
          for (const tc of prev.tool_calls) {
            if (tc.id === message.tool_call_id) {
              return this.getToolName(tc);
            }
          }
        }
      }
      return message.tool_call_id.substring(0, 12);
    },

    getToolArgsForToolMsg(message) {
      if (!message) return '';
      if (message.command) {
        return JSON.stringify({ command: message.command }, null, 2);
      }
      if (message.arguments) {
        return typeof message.arguments === 'string'
          ? message.arguments
          : JSON.stringify(message.arguments, null, 2);
      }
      if (!message.tool_call_id) return '';
      const idx = this.messages.indexOf(message);
      for (let i = idx - 1; i >= 0; i--) {
        const prev = this.messages[i];
        if (prev.role === 'assistant' && prev.tool_calls) {
          for (const tc of prev.tool_calls) {
            if (tc.id === message.tool_call_id) {
              return this.getToolArgs(tc);
            }
          }
        }
      }
      return '';
    },

    getAttachedToolResult(tc) {
      if (!tc || !tc.id) return '';
      const match = this.messages.find(
        (m) => m.role === 'tool' && m.tool_call_id === tc.id
      );
      return match?.content || '';
    },

    // Live (streaming) output tail for a RUNNING tool call, fed by
    // tool.output WS frames; cleared implicitly when loadMessages rebuilds.
    getAttachedLiveOutput(tc) {
      // Touch reactive version so Set-based live tails re-evaluate cards
      void this._sessionVersion;
      return (tc && tc._liveOutput) || '';
    },

    /**
     * Status for a tool_call card attached to an assistant message.
     * - Has tool result → success / error / rejected from content
     * - No result + this session currently streaming that run → running
     * - No result otherwise (reload after crash, orphan) → rejected (cancelled)
     *   so UI never sticks on "执行中" forever after restart.
     */
    getAttachedToolStatus(tc) {
      // Touch reactive version so Set-based running flags re-evaluate cards
      void this._sessionVersion;
      if (!tc || !tc.id) return 'success';
      const match = this.messages.find(
        (m) => m.role === 'tool' && m.tool_call_id === tc.id
      );
      // Result always wins — even if isSessionRunning (next LLM turn)
      if (match) return this.getToolResultStatus(match);

      // Prefer per-tool live set when present (set on tool.start, cleared on finish)
      if (this._runningToolIds && this._runningToolIds.has(tc.id)) {
        return 'running';
      }

      // Mid-flight batch without per-id tracking yet
      if (this.toolExecuting) return 'running';

      // Session still streaming LLM after tools → do NOT keep tools "running"
      return 'rejected';
    },

    // Hide standalone tool messages when already shown under assistant.tool_calls
    isToolMsgPairedToAssistant(message) {
      if (!message || !message.tool_call_id) return false;
      const idx = this.messages.indexOf(message);
      for (let i = idx - 1; i >= 0; i--) {
        const prev = this.messages[i];
        if (prev.role === 'assistant' && prev.tool_calls) {
          return prev.tool_calls.some((tc) => tc.id === message.tool_call_id);
        }
        if (prev.role === 'user') break;
      }
      return false;
    },

    // Determine tool card status from result content / flags.
    // Prefers the structured error_kind emitted by the backend so failed tools
    // render a precise reason (permission / policy / timeout / execution).
    getToolResultStatus(messageOrContent) {
      if (!messageOrContent) return 'success';
      if (typeof messageOrContent === 'object') {
        if (messageOrContent.error_kind) {
          const k = messageOrContent.error_kind;
          if (k === 'rejected') return 'rejected';
          if (k === 'permission' || k === 'policy' || k === 'timeout' || k === 'execution') return k;
        }
        if (messageOrContent.is_error) return 'error';
        const content = messageOrContent.content || '';
        if (content.includes('rejected by user')) return 'rejected';
        if (content.includes('Error:') || content.includes('Tool error:')) return 'error';
        // shell structured payload may be stringified JSON
        if (/"success"\s*:\s*false/.test(content) || /"exit_code"\s*:\s*(?!0\b)\d+/.test(content)) {
          return 'error';
        }
        return 'success';
      }
      const content = String(messageOrContent);
      if (content.includes('rejected by user')) return 'rejected';
      if (content.includes('Error:') || content.includes('Tool error:') || content.includes('rror')) return 'error';
      if (/"success"\s*:\s*false/.test(content) || /"exit_code"\s*:\s*(?!0\b)\d+/.test(content)) {
        return 'error';
      }
      return 'success';
    },

    // Human-readable failure reason for a tool message (structured field first,
    // then falls back to stripping prefixes from the stored content).
    // rejected/timeout render their i18n label instead of raw content.
    getToolErrorReason(message) {
      if (!message) return '';
      if (message.error_reason && String(message.error_reason).trim()) {
        return String(message.error_reason).trim();
      }
      const kind = message.error_kind;
      if (kind === 'rejected' || kind === 'timeout') return '';
      const content = String(message.content || '').trim();
      if (!content) return '';
      if (content.includes('rejected by user')) return '';
      return content
        .replace(/^\[Error\]\s*/i, '')
        .replace(/^\[Tool execution\s*/i, '')
        .replace(/^Tool error:\s*/i, '')
        .replace(/^Error:\s*/i, '')
        .replace(/\]\s*$/i, '')
        .trim();
    },

    // Failure reason for a tool_call card attached to an assistant message.
    getAttachedToolErrorReason(tc) {
      if (!tc || !tc.id) return '';
      const match = this.messages.find(
        (m) => m.role === 'tool' && m.tool_call_id === tc.id
      );
      return match ? this.getToolErrorReason(match) : '';
    },
  }
};
