/**
 * BoarAi WebUI Internationalization (i18n) Module
 * 
 * Supports Chinese and English languages.
 * Uses localStorage to persist language preference.
 * Dynamically loads language files from JSON.
 */

// Language messages cache
const messages = {
  en: null,
  zh: null
};

/**
 * Resolve the initial language: an explicit stored choice wins, otherwise
 * follow the browser. Hardcoding 'en' here meant a zh-CN browser could never
 * see Chinese on first run — and the setup/unlock pages have no navbar, so
 * there was no switcher to fix it with either.
 * @returns {string} 'zh' or 'en'
 */
function detectLanguage() {
  let stored = null;
  try {
    stored = localStorage.getItem('boarai-language');
  } catch (_) {
    // Storage blocked (private mode): fall through to browser detection.
  }
  if (stored === 'en' || stored === 'zh') return stored;
  const preferred =
    (navigator.languages && navigator.languages[0]) || navigator.language || 'en';
  return /^zh\b/i.test(String(preferred)) ? 'zh' : 'en';
}

// Current language
let currentLanguage = detectLanguage();

// Fallback messages (English) for immediate use
const fallbackMessages = {
  nav: {
    home: 'Dashboard',
    chat: 'Sessions',
    tasks: 'Tasks',
    extensions: 'Extensions',
    data: 'Data',
    settings: 'Settings'
  },
  dashboard: {
    status: 'Status',
    running: 'Running',
    allSystemsOperational: 'All systems operational',
    sessions: 'Sessions',
    activeConversations: '{count} active conversations',
    extensions: 'Extensions',
    enabledDisabled: '{enabled} enabled, {disabled} disabled',
    memory: 'Memory',
    memoryUsage: '{used} of {total} allocated',
    recentActivity: 'Recent Activity',
    webuiStarted: 'WebUI extension started',
    newSessionCreated: 'New session created: {id}',
    highMemoryUsage: 'High memory usage detected'
  },
  sessions: {
    newSession: '+ New Session',
    noSessions: 'No Sessions Yet',
    welcomeTitle: 'Start a conversation',
    welcomeHint: 'Type a message to start chatting with your AI agent.',
    welcomeBackTitle: 'Type to start a new chat',
    welcomeBackHint: 'Or pick an existing session from the sidebar.',
    interrupting: 'Interrupting...',
    interrupted: 'Response interrupted',
    interruptedByNewMessage: 'Response interrupted to process the new message'
  },
  extensions: {
    browseStore: 'Browse Store',
    extensionManager: 'Extension Manager',
    manageExtensions: 'Manage and configure your extensions.',
    active: 'Active',
    settings: 'Settings',
    disable: 'Disable'
  },
  data: {
    import: 'Import',
    export: 'Export',
    noData: 'No Data Available',
    importData: 'Import data or start a session to see data here.'
  },
  settings: {
    appearance: 'Appearance',
    theme: 'Theme',
    chooseTheme: 'Choose your preferred theme',
    language: 'Language',
    selectLanguage: 'Select your preferred language',
    connection: 'Connection',
    serverUrl: 'Server URL',
    autoReconnect: 'Auto Reconnect',
    autoReconnectDesc: 'Automatically reconnect on disconnect',
    about: 'About',
    version: 'v{version}',
    homepage: 'Start Page',
    homepageDesc: 'Choose the default page when opening the WebUI'
  },
  statusbar: {
    ws: 'WS:',
    kernel: 'Kernel:',
    latency: 'Latency:',
    uptime: 'Uptime:',
    memory: 'Memory:',
    connections: 'Connections:',
    connected: 'CONNECTED',
    disconnected: 'DISCONNECTED',
    connecting: 'CONNECTING...',
    wsConnected: 'WebSocket connected',
    wsDisconnected: 'WebSocket disconnected'
  },
  common: {
    light: 'Light',
    dark: 'Dark',
    system: 'System',
    english: 'English',
    chinese: 'ä¸­æ–‡',
    loading: 'Loading BoarAi WebUI...',
    minAgo: '{minutes} min ago',
    justNow: 'Just now'
  },
  tasks: {
    title: 'Task Manager',
    subtitle: 'Create and manage automated tasks',
    createTask: 'Create Task',
    createNewTask: 'Create New Task',
    totalTasks: 'Total',
    pending: 'Pending',
    running: 'Running',
    completed: 'Completed',
    failed: 'Failed',
    all: 'All',
    noTasks: 'No tasks yet',
    task: 'Task',
    taskTitle: 'Title',
    description: 'Description',
    taskType: 'Type',
    prompt: 'Prompt',
    promptPlaceholder: 'Enter your prompt for the AI agent...',
    scriptPath: 'Script Path',
    scriptArgs: 'Script Args',
    sessionId: 'Session',
    noSession: 'No session (auto-create)',
    newSession: 'New Session',
    sessionCreated: 'Session created',
    schedule: 'Schedule',
    scheduleType: 'Schedule Type',
    noSchedule: 'No Schedule',
    scheduleOnce: 'Once',
    scheduleInterval: 'Interval',
    scheduleCron: 'Cron',
    runAt: 'Run At',
    expression: 'Expression',
    intervalExamples: 'Examples: 30m, 2h, 7d',
    cronHint: 'Standard cron: minute hour day month weekday',
    cronExamples: 'Quick Presets',
    cronExample1: 'Every 9am daily',
    cronExample2: 'Weekday 6pm',
    cronExample3: 'Every 5 min',
    cronExample4: 'Monday 9am',
    useExample: 'Use',
    timezone: 'Timezone',
    schedulePreview: 'Preview',
    willRunAt: 'Will run at',
    create: 'Create',
    createAndRun: 'Save & Run',
    maxRetries: 'Max Retries',
    timeoutSeconds: 'Timeout (s)',
    model: 'Model',
    taskId: 'Task ID',
    kind: 'Type',
    subTasks: 'Sub-tasks',
    steps: 'Steps',
    startedAt: 'Started',
    completedAt: 'Completed',
    resultOutput: 'Output',
    runHistory: 'Run History',
    run: 'Run',
    cancel: 'Cancel',
    pause: 'Pause',
    resume: 'Resume',
    progress: 'Progress',
    sub: 'SUB',
    searchPlaceholder: 'Search tasks...',
    aiTaskPromptRequired: 'Prompt is required for AI tasks',
    scriptTaskPathRequired: 'Script path is required for script tasks',
    selectRunTime: 'Please select a run time',
    intervalFormatError: 'Invalid interval format (e.g. 30m, 2h, 7d)',
    cronFormatError: 'Invalid cron format (e.g. 0 9 * * 1-5)',
    queued: 'Queued',
    paused: 'Paused',
    cancelled: 'Cancelled',
    skipped: 'Skipped',
  },
  // Access gate. Duplicated here (not only in the JSON) because the unlock
  // page renders before the language file finishes loading.
  unlock: {
    title: 'Unlock BoarAi',
    subtitle: 'This device needs the access key printed by the server.',
    keyLabel: 'Access key',
    keyHint: 'Starts with boarai_. Ask the person running the server.',
    keyPlaceholder: 'boarai_…',
    show: 'Show',
    hide: 'Hide',
    durationLabel: 'Stay signed in for',
    durationHint: 'After this the key is asked again.',
    remember: 'Remember this device',
    rememberHint: 'Off = signed out when the browser closes.',
    submit: 'Unlock',
    throttled: 'Too many attempts — retry in {seconds}s.',
    throttledError: 'Too many failed attempts.',
    invalid: 'That access key is not correct.',
    failed: 'Unlock failed.',
    notConfigured: 'No access key exists yet — this looks like a first run.',
    goSetup: 'Create the access key',
    footer: 'The key is only stored as a hash on the server and never leaves it.',
    serverUnreachable: 'Cannot reach the BoarAI server.',
    duration: { minutes: ' min', hours: ' h', days: ' d' },
  },
  // Setup page (first-run). Same fallback strategy as unlock.
  setup: {
    title: 'Create your access key',
    subtitle: 'This BoarAI instance has no access key yet. Create one to finish the first-run setup.',
    keyLabel: 'Access key',
    keyHint: 'Generated keys are strong. You may also type your own.',
    keyPlaceholder: 'boarai_…',
    generate: 'Generate new key',
    submitting: 'Saving…',
    submit: 'Save and enter',
    copy: 'Copy',
    copied: 'Copied',
    copyManual: 'Clipboard blocked — reveal the key and copy it manually.',
    codeLabel: 'Setup code',
    codeHint: 'Printed in the server startup log (docker logs / journalctl). Not needed on the server machine.',
    codePlaceholder: 'XXXX-XXXX-XXXX',
    codeRequired: 'Remote setup needs the setup code from the server startup log.',
    codeInvalid: 'That setup code is not correct.',
    remoteNote: 'You are setting up from another machine: enter the one-time setup code printed in the server startup log; on the server itself no code is needed.',
    generateFailed: 'This browser cannot generate a key. Type a strong one instead.',
    failed: 'Setup failed.',
    weakTooShort: 'Too short',
    weakLowEntropy: 'Too few distinct characters',
    weakNoSymbols: 'Must include numbers or symbols',
    strong: 'Strong',
  },
};

/**
 * Get current language
 * @returns {string} Current language code
 */
function getCurrentLanguage() {
  return currentLanguage;
}

/**
 * Set current language
 * @param {string} lang - Language code ('en' or 'zh')
 * @returns {Promise<void>}
 */
async function setLanguage(lang) {
  // Load language file if not cached
  if (!messages[lang]) {
    await loadLanguageFile(lang);
  }
  
  // Always update language - even if file load failed, we use fallback
  currentLanguage = lang;
  try {
    localStorage.setItem('boarai-language', lang);
  } catch (_) {
    // Storage blocked: the choice still applies for this session.
  }
  
  // Update document lang attribute
  document.documentElement.setAttribute('lang', lang);
  
  // Dispatch event for components to react
  window.dispatchEvent(new CustomEvent('language-changed', { detail: { language: lang } }));
}

/**
 * Get message by key
 * @param {string} key - Message key (e.g., 'nav.home')
 * @param {Object} params - Parameters to replace in message
 * @returns {string} Translated message
 */
function t(key, params = {}) {
  const lookup = (source) => {
    if (!source || typeof source !== 'object') return null;
    const keys = key.split('.');
    let message = source;
    for (const k of keys) {
      if (message && typeof message === 'object' && k in message) {
        message = message[k];
      } else {
        return null;
      }
    }
    return typeof message === 'string' ? message : null;
  };

  // Loaded locale ?built-in fallback ?English file ?raw key
  let message =
    lookup(messages[currentLanguage]) ||
    lookup(fallbackMessages) ||
    (currentLanguage !== 'en' ? lookup(messages.en) : null) ||
    key;

  for (const [paramKey, paramValue] of Object.entries(params)) {
    message = message.replace(new RegExp(`\\{${paramKey}\\}`, 'g'), paramValue);
  }

  return message;
}

/**
 * Get all messages for current language
 * @returns {Object} Messages object
 */
function getMessages() {
  return messages[currentLanguage] || fallbackMessages;
}

/**
 * Check if current language is Chinese
 * @returns {boolean}
 */
function isChinese() {
  return currentLanguage === 'zh';
}

/**
 * Load language file from JSON
 * @param {string} lang - Language code ('en' or 'zh')
 * @returns {Promise<void>}
 */
async function loadLanguageFile(lang) {
  try {
    const response = await fetch(`/static/js/i18n/${lang}.json`);
    if (response.ok) {
      messages[lang] = await response.json();
      console.log(`Loaded language file: ${lang}.json`);
    } else {
      console.warn(`Failed to load language file: ${lang}.json`);
    }
  } catch (error) {
    console.warn(`Error loading language file ${lang}.json:`, error);
  }
}

/**
 * Initialize i18n module
 * Loads the current language file
 * @returns {Promise<void>}
 */
async function initI18n() {
  // Always warm English so fallback chain works even when UI is zh
  await Promise.all([
    loadLanguageFile(currentLanguage),
    currentLanguage !== 'en' ? loadLanguageFile('en') : Promise.resolve(),
  ]);
  
  // Update document lang attribute
  document.documentElement.setAttribute('lang', currentLanguage);
  
  // Dispatch initial language event
  window.dispatchEvent(new CustomEvent('language-changed', { detail: { language: currentLanguage } }));
}

// Initialize i18n when module loads
initI18n();

// Export for global access
window.i18n = {
  getCurrentLanguage,
  setLanguage,
  t,
  getMessages,
  isChinese,
  messages,
  initI18n
};
