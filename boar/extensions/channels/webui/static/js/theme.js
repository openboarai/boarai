/**
 * BoarAi WebUI Theme Manager
 *
 * Single source of truth for light / dark / system.
 * - Preference stored in localStorage (`boarai-theme`)
 * - Tailwind uses class strategy: `html.dark`
 * - `data-theme` = preference; `data-theme-resolved` = effective light|dark
 */
(function () {
  const STORAGE_KEY = 'boarai-theme';
  const COLOR_KEY = 'boarai-color';
  let mediaListener = null;

  function getPreference() {
    const raw = localStorage.getItem(STORAGE_KEY) || 'light';
    return raw === 'dark' || raw === 'light' || raw === 'system' ? raw : 'light';
  }

  function resolve(pref) {
    if (pref === 'dark' || pref === 'light') return pref;
    try {
      return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    } catch (_) {
      return 'light';
    }
  }

  function apply(pref) {
    const preference = pref === 'dark' || pref === 'light' || pref === 'system' ? pref : 'light';
    const resolved = resolve(preference);
    const root = document.documentElement;

    // Tailwind darkMode:'class' — must be on <html>
    if (resolved === 'dark') {
      root.classList.add('dark');
    } else {
      root.classList.remove('dark');
    }
    root.setAttribute('data-theme', preference);
    root.setAttribute('data-theme-resolved', resolved);
    root.style.colorScheme = resolved;

    // Keep body class in sync for any non-Tailwind CSS hooks
    if (document.body) {
      if (resolved === 'dark') {
        document.body.classList.add('dark');
      } else {
        document.body.classList.remove('dark');
      }
    }

    _bindSystemListener(preference);
    return { preference, resolved };
  }

  function set(pref) {
    const preference = pref === 'dark' || pref === 'light' || pref === 'system' ? pref : 'light';
    localStorage.setItem(STORAGE_KEY, preference);
    const result = apply(preference);
    window.dispatchEvent(
      new CustomEvent('theme-changed', {
        detail: { theme: result.preference, resolved: result.resolved },
      })
    );
    return result;
  }

  function toggle() {
    // Toggle only between light/dark (system → opposite of resolved)
    const pref = getPreference();
    const current = resolve(pref);
    const next = current === 'dark' ? 'light' : 'dark';
    return set(next);
  }

  function getResolved() {
    return resolve(getPreference());
  }

  function getColor() {
    return 'cobalt';
  }

  function setColor() {
    localStorage.setItem(COLOR_KEY, 'cobalt');
    applyColor();
    window.dispatchEvent(new CustomEvent('color-changed', { detail: { color: 'cobalt' } }));
    return 'cobalt';
  }

  function applyColor() {
    document.documentElement.setAttribute('data-color', 'cobalt');
  }

  function _bindSystemListener(pref) {
    const mql = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
    if (!mql) return;

    if (mediaListener) {
      try {
        mql.removeEventListener('change', mediaListener);
      } catch (_) {
        try {
          mql.removeListener(mediaListener);
        } catch (__) {}
      }
      mediaListener = null;
    }

    if (pref !== 'system') return;

    mediaListener = () => {
      apply('system');
      window.dispatchEvent(
        new CustomEvent('theme-changed', {
          detail: { theme: 'system', resolved: resolve('system') },
        })
      );
    };

    try {
      mql.addEventListener('change', mediaListener);
    } catch (_) {
      try {
        mql.addListener(mediaListener);
      } catch (__) {}
    }
  }

  // Apply as early as possible (before Vue mount) to avoid flash
  apply(getPreference());
  applyColor(getColor());

  window.BoarTheme = {
    getPreference,
    getResolved,
    resolve,
    apply,
    set,
    toggle,
    getColor,
    setColor,
    applyColor,
  };
})();