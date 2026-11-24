/**
 * Access-gate client for the WebUI.
 *
 * The credential never touches JavaScript:
 *   - the access key is typed once on the unlock page and traded for an
 *     HttpOnly + SameSite=Strict cookie (`POST /api/auth/session`);
 *   - every later request just carries that cookie (same-origin fetch);
 *   - the WebSocket upgrade trades the cookie for a 30-second single-use
 *     ticket, because a browser cannot set headers on an upgrade and putting
 *     a long-lived key in the URL would leak it into history and logs.
 *
 * Two window events are the contract with the rest of the app:
 *   `boarai-auth-required`  no usable session — the router shows /unlock
 *   (nothing else is needed: the shell reloads state after unlocking)
 */
(function () {
  'use strict';

  const STATUS_PATH = '/api/auth/status';
  const SESSION_PATH = '/api/auth/session';
  const TICKET_PATH = '/api/auth/ws-ticket';
  const nativeFetch = window.fetch.bind(window);

  /** Last known status; `null` means "ask the server again". */
  let status = null;
  let statusPromise = null;

  function emitAuthRequired(detail) {
    window.dispatchEvent(new CustomEvent('boarai-auth-required', { detail: detail || {} }));
  }

  async function readJson(response) {
    try {
      return await response.json();
    } catch (_) {
      return null;
    }
  }

  function toError(payload, httpStatus) {
    const error = payload && payload.error ? payload.error : {};
    const err = new Error(error.message || ('HTTP ' + httpStatus));
    err.code = error.code || ('http_' + httpStatus);
    err.status = httpStatus;
    err.retryAfter = error.retry_after || 0;
    return err;
  }

  /**
   * Same-origin request against the gated API.
   * @param {string} path
   * @param {object} [options] fetch init overrides
   * @returns {Promise<any>} the `data` payload
   */
  async function request(path, options) {
    const response = await nativeFetch(
      path,
      Object.assign({ credentials: 'same-origin', cache: 'no-store' }, options || {}),
    );
    const payload = await readJson(response);
    if (!response.ok || !payload || payload.success !== true) {
      const error = toError(payload, response.status);
      // `auth_setup_required` means the instance became unclaimed (fresh
      // install, or access.yaml removed) — the router must show /setup, not
      // /unlock, because there is no key to type.
      if (error.code === 'auth_required' || error.code === 'auth_setup_required') {
        status = null;
        emitAuthRequired({ code: error.code });
      }
      throw error;
    }
    return payload.data;
  }

  const Auth = {
    /**
     * Current gate state for this browser (cached until something changes).
     * @param {boolean} [force] re-read from the server
     */
    async status(force) {
      if (!force && status) return status;
      if (statusPromise && !force) return statusPromise;

      statusPromise = request(STATUS_PATH)
        .then((data) => {
          status = data;
          statusPromise = null;
          if (data && data.must_setup) {
            emitAuthRequired({ code: 'auth_setup_required' });
          } else if (data && data.must_unlock) {
            emitAuthRequired({ code: 'auth_required' });
          }
          return data;
        })
        .catch((error) => {
          statusPromise = null;
          status = null;
          throw error;
        });
      return statusPromise;
    },

    /** Last status without a round-trip (may be null). */
    cached() {
      return status;
    },

    /**
     * Trade the access key for a device session.
     * @param {string} key
     * @param {{ttlSeconds?: number, remember?: boolean}} [options]
     */
    async unlock(key, options) {
      const opts = options || {};
      const data = await request(SESSION_PATH, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          key: key,
          ttl_seconds: opts.ttlSeconds,
          remember: opts.remember !== false,
        }),
      });
      status = null;
      return data;
    },

    /** Drop this device's session. */
    async logout() {
      try {
        await request(SESSION_PATH, { method: 'DELETE' });
      } catch (_) {
        /* already gone */
      }
      status = null;
      emitAuthRequired({ code: 'logged_out' });
    },

    /**
     * First-run setup: claim the access key and get a session immediately.
     * @param {string} key
     * @param {{ttlSeconds?: number, remember?: boolean}} [options]
     */
    async setup(key, options) {
      const opts = options || {};
      const data = await request('/api/auth/setup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          key: key,
          ttl_seconds: opts.ttlSeconds,
          remember: opts.remember !== false,
        }),
      });
      status = null;
      return data;
    },

    /** Single-use credential for the WebSocket upgrade. */
    async getTicket() {
      const data = await request(TICKET_PATH, { method: 'POST' });
      return data.ticket;
    },

    async sessions() {
      const data = await request('/api/auth/sessions');
      return (data && data.sessions) || [];
    },

    async revokeSession(id) {
      return request('/api/auth/sessions/' + encodeURIComponent(id), { method: 'DELETE' });
    },

    /**
     * Prepare a new key. NOTHING is applied yet — the current key keeps working
     * and no device is signed out, so abandoning this is harmless.
     * @returns {Promise<{key: string, expires_in: number}>}
     */
    async rotateKey() {
      return request('/api/auth/key/rotate', { method: 'POST' });
    },

    /** Apply the prepared key. Destructive: every device is signed out. */
    async confirmRotation() {
      const data = await request('/api/auth/key/rotate/confirm', { method: 'POST' });
      status = null;
      return data;
    },

    /** Discard a prepared key without changing anything. */
    async cancelRotation() {
      try {
        return await request('/api/auth/key/rotate', { method: 'DELETE' });
      } catch (_) {
        return null; // best-effort: the candidate expires on its own anyway
      }
    },

    async settings() {
      return request('/api/auth/settings');
    },

    async saveSettings(patch) {
      return request('/api/auth/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
    },
  };

  window.BoarAiAuth = Auth;
})();
