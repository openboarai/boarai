/**
 * BoarAi WebUI State Management
 * 
 * Simple reactive state store using Vue 3's reactivity system.
 * Provides global state management without Vuex/Pinia dependency.
 */

const BoarAiStore = {
  // Application state
  state: Vue.reactive({
    // UI State
    isLoading: false,
    loadingText: '',
    connectionStatus: 'disconnected',  // connected / disconnected / connecting
    
    // Notification queue
    notifications: [],
    notificationId: 0,

    // Pending tool approvals (security gate). Keyed by session_id so the
    // navbar can surface a persistent badge even when the user is on another
    // page. Each entry: { runId, tools: [], count }.
    pendingApprovals: {},
    
    // Confirm dialog state
    confirmDialog: {
      show: false,
      title: '',
      message: '',
      confirmText: 'Confirm',
      cancelText: 'Cancel',
      type: 'warning', // warning, danger, info
      onConfirm: null,
      onCancel: null
    },
    
    // Extensions state
    extensions: {
      adapters: [],
      tools: [],
      plugins: [],
      skills: [],
      mcp: [],
      failed: []
    },
    extensionsLoading: false,
    
    // Sessions state
    sessions: [],
    activeSession: null,
    sessionsLoading: false,
    
    // Tasks state
    tasks: [],
    tasksLoading: false,
    
    // Stream callbacks (set by sessions.js, called directly from WS handler)
    onStreamChunk: null,
    onStreamDone: null,
    onStreamError: null,
    onStreamCancelled: null,
    onStreamThinking: null,
    onStreamRetry: null,
    onRunUsage: null,
    onToolPending: null,
    onToolStart: null,
    onToolFinished: null,
    onToolOutput: null,
    
    // Session broadcast event callbacks
    onSessionDeleted: null,
    // Change log dropped (kept) or consumed (revert-all); panel must refresh
    onChangesCleared: null,
    
    // Connection status callback (statusbar etc.)
    _onConnectionChange: null,
    
    // Data state
    dataStats: {
      tokens: { total: 0, today: 0, avgPerSession: 0 },
      sessions: { total: 0, active: 0 },
      tasks: { total: 0, completed: 0, active: 0 },
      skills: { installed: 0, active: 0 }
    },
    
    // System state
    systemInfo: {
      version: '0.1.0',
      uptime: 0,
      cpuUsage: 0,
      memoryUsage: 0,
      activeConnections: 0
    },
    
    // (streaming handled via direct callbacks onStreamChunk/onStreamDone/onStreamError)
  }),
  
  // WebSocket Connection Manager
  _ws: null,
  _wsConnected: false,
  _wsReconnectTimer: null,
  _msgIdCounter: 0,
  _pendingRequests: {},
  _runCallbacks: {},  // run_id -> { onChunk, onDone, onError }
  _onTaskEvent: null,   // (eventType, payload) => void — registered by tasks page
  // Sessions that should receive live run.* events (session.get / run.create auto-subscribe on server)
  _subscribedSessionIds: new Set(),
  // Called after WS (re)open so pages can re-bind session stream + reconcile stuck UI
  onWsReconnected: null,
  
  _trackSessionSubscription(sessionId) {
    if (sessionId) this._subscribedSessionIds.add(sessionId);
  },

  _setPendingApproval(sessionId, runId, tools, confirmationId) {
    if (!sessionId) return;
    const list = tools || [];
    this.state.pendingApprovals[sessionId] = {
      runId,
      confirmationId: confirmationId || '',
      tools: list.map(t => ({
        name: t.name || t.id || 'tool',
        grantable: t.grantable,
        // Directory a "allow this folder" answer would cover (null when the
        // call names none). Kept so the third button survives a page switch.
        folder_grant: t.folder_grant || null,
      })),
      count: list.length || 1,
    };
  },

  _clearPendingApproval(sessionId) {
    if (sessionId && this.state.pendingApprovals[sessionId]) {
      delete this.state.pendingApprovals[sessionId];
    }
  },

  async _resubscribeSessionsAfterReconnect() {
    const ids = [...this._subscribedSessionIds];
    for (const sessionId of ids) {
      try {
        // session.get re-adds this socket to handler._ws_clients[session_id]
        await this._sendWsMessage('session.get', {
          session_id: sessionId,
          include_messages: false,
        }, 5000);
      } catch (e) {
        console.warn('WS re-subscribe session failed:', sessionId, e);
      }
    }
    try {
      if (typeof this.onWsReconnected === 'function') {
        this.onWsReconnected({ sessionIds: ids });
      }
    } catch (e) {
      console.warn('onWsReconnected handler error:', e);
    }
  },
  
  async _connectWebSocket() {
    if (this._ws && (this._ws.readyState === WebSocket.OPEN || this._ws.readyState === WebSocket.CONNECTING)) return;
    if (this._wsAuthPending) return;
    this._wsAuthPending = true;
    this._setConnectionStatus('connecting');
    // A browser cannot set headers on a WebSocket upgrade, and putting the
    // access key in the URL would leak it into history/logs — so the cookie is
    // traded for a 30-second single-use ticket first.
    let ticket;
    try {
      ticket = await window.BoarAiAuth.getTicket();
    } catch (error) {
      this._wsAuthPending = false;
      this._setConnectionStatus('disconnected');
      // `auth_required` already emitted `boarai-auth-required` in auth.js.
      console.warn('WebSocket ticket unavailable:', error && error.message);
      return;
    }
    this._wsAuthPending = false;
    if (this._ws && (this._ws.readyState === WebSocket.OPEN || this._ws.readyState === WebSocket.CONNECTING)) return;

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws`;
    this._ws = new WebSocket(wsUrl, ['boarai.v1', `boarai-ticket.${ticket}`]);
    const ws = this._ws;
    
    ws.onopen = () => {
      this._wsConnected = true;
      this._setConnectionStatus('connected');
      console.log('WebSocket connected:', wsUrl);
      this.actions.success(window.i18n.t('statusbar.wsConnected'), 2000);
      // Long tools: idle proxies may drop WS; re-join session fan-out after reconnect
      this._resubscribeSessionsAfterReconnect();
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        
        // Handle streaming run events (no id, pushed from server)
        if (msg.type && msg.type.startsWith('run.') && !msg.id) {
          const runId = msg.payload?.run_id;
          const sessionId = msg.payload?.session_id;
          
          // Dispatch to session page callbacks (direct, no reactive delay)
          // message_id lets the UI route each chunk/thinking to the correct
          // per-turn assistant bubble (one run may stream several turns).
          if (msg.type === 'run.chunk' && sessionId) {
            if (this.onStreamChunk) this.onStreamChunk(sessionId, msg.payload.content || '', runId, msg.payload.message_id || '', msg.payload.seq ?? null);
          } else if (msg.type === 'run.thinking' && sessionId) {
            if (this.onStreamThinking) this.onStreamThinking(sessionId, msg.payload.content || '', runId, msg.payload.message_id || '', msg.payload.seq ?? null);
          } else if (msg.type === 'run.done' && sessionId) {
            this._clearPendingApproval(sessionId);
            if (this.onStreamDone) this.onStreamDone(sessionId, runId);
          } else if (msg.type === 'run.error' && sessionId) {
            this._clearPendingApproval(sessionId);
            if (this.onStreamError) this.onStreamError(sessionId, msg.payload.error || 'Run error', runId);
          } else if (msg.type === 'run.cancelled' && sessionId) {
            this._clearPendingApproval(sessionId);
            if (this.onStreamCancelled) this.onStreamCancelled(sessionId, msg.payload.reason, runId);
          } else if (msg.type === 'run.created' && sessionId) {
            if (this.onRunCreated) {
              this.onRunCreated(sessionId, {
                runId,
                model: msg.payload.model || '',
                model_source: msg.payload.model_source || 'default',
              });
            }
          } else if (msg.type === 'run.vision_downgrade' && sessionId) {
            if (this.onVisionDowngrade) {
              this.onVisionDowngrade(sessionId, {
                runId,
                model: msg.payload.model || '',
                reason: msg.payload.reason || '',
              });
            }
          } else if (msg.type === 'run.retry' && sessionId) {
            if (this.onStreamRetry) {
              this.onStreamRetry(sessionId, {
                attempt: msg.payload.attempt,
                max_attempts: msg.payload.max_attempts,
                error: msg.payload.error || '',
                delay_seconds: msg.payload.delay_seconds,
                runId,
              });
            }
          } else if (msg.type === 'run.usage' && sessionId) {
            // Live cumulative per-run LLM usage (pushed as each LLM turn
            // completes) — drives real-time sidebar/toolbar counters.
            if (this.onRunUsage) {
              this.onRunUsage(sessionId, {
                runId,
                turns: msg.payload.turns || 0,
                input: msg.payload.input || 0,
                completion: msg.payload.completion || 0,
                cache_read: msg.payload.cache_read || 0,
                cache_creation: msg.payload.cache_creation || 0,
              });
            }
          } else if (msg.type === 'run.diff' && sessionId) {
            // Aggregated per-run file changes (added/modified/deleted + diff).
            if (this.onRunDiff) {
              this.onRunDiff(sessionId, {
                runId,
                files_changed: msg.payload.files_changed || [],
                unified_diff: msg.payload.unified_diff || '',
                stats: msg.payload.stats || {},
              });
            }
          } else if (msg.type === 'run.tool.pending' && sessionId) {
            const tools = msg.payload.tools || [];
            this._setPendingApproval(sessionId, runId, tools, msg.payload.confirmation_id || '');
            if (this.onToolPending) this.onToolPending(
              sessionId,
              runId,
              tools,
              msg.payload.confirmation_id || '',
            );
            // Global notice so approval is surfaced on ANY page (not just the
            // session view). Toast auto-dismisses; the navbar badge persists.
            const toolNames = tools.map(t => t.name || t.id || 'tool').join(', ');
            this.actions.warning(
              window.i18n.t('notifications.toolApprovalRequired', { tools: toolNames }),
              8000
            );
          } else if ((msg.type === 'run.tool.start' || msg.type === 'run.start') && sessionId) {
            // Tool execution started — approval resolved (approved).
            this._clearPendingApproval(sessionId);
            // Prefer run.tool.start (fixed broadcaster); keep run.start as legacy alias.
            if (this.onToolStart) this.onToolStart(sessionId, msg.payload.tools || [], runId, msg.payload.message_id || '', msg.payload.seq ?? null);
          } else if ((msg.type === 'run.tool.finished' || msg.type === 'run.finished') && sessionId) {
            // Tool execution completed (with tool results). Approval resolved
            // (either approved then finished, or rejected).
            this._clearPendingApproval(sessionId);
            if (this.onToolFinished) this.onToolFinished(
              sessionId,
              msg.payload.tools || [],
              msg.payload.tool_results || [],
              runId,
              msg.payload.message_id || '',
              msg.payload.seq ?? null,
            );
          } else if (msg.type === 'run.tool.output' && sessionId) {
            // Incremental output from a RUNNING tool (e.g. shell stdout lines).
            // tool_call_id pairs the delta with the executing card; the
            // authoritative result still arrives with tool.finished.
            if (this.onToolOutput) this.onToolOutput(sessionId, {
              tool_call_id: msg.payload.tool_call_id || '',
              name: msg.payload.name || '',
              stream: msg.payload.stream || 'stdout',
              delta: msg.payload.delta || '',
            }, runId);
          }
          
          // Also dispatch to per-run callbacks for backward compat
          if (runId && this._runCallbacks[runId]) {
            const cb = this._runCallbacks[runId];
            const eventType = msg.type.replace('run.', '');
            if (eventType === 'chunk') {
              cb.onChunk(msg.payload.content || '');
            } else if (eventType === 'done') {
              cb.onDone(msg.payload.content || '');
              delete this._runCallbacks[runId];
            } else if (eventType === 'error') {
              cb.onError(msg.payload.error || 'Run error');
              delete this._runCallbacks[runId];
            }
          }
          return;
        }
        
        // Handle broadcast events (no id, pushed from server, not run events)
        if (!msg.id && msg.type && !msg.type.startsWith('run.')) {
          if (msg.type === 'session.deleted' && this.onSessionDeleted) {
            this.onSessionDeleted(msg.payload?.session_id);
          }
          // The change log was dropped (keep) or consumed (revert-all)
          if (msg.type === 'session.changes.cleared' && this.onChangesCleared) {
            this.onChangesCleared(msg.payload?.session_id, msg.payload?.reason);
          }
          // Another client edited the latest user message (tail truncated)
          if (msg.type === 'session.messages_truncated' && this.onMessagesTruncated) {
            this.onMessagesTruncated(msg.payload?.session_id, msg.payload?.keep_message_id);
          }
          // File manager: a file was saved through /api/fs/write (any client).
          if (msg.type === 'fs.changed' && this.onFsChanged) {
            this.onFsChanged(msg.payload?.path, msg.payload?.mtime ?? null);
          }
          // Access gate: the key was rotated or this device was revoked. The
          // cookie is already dead server-side; drop the socket and let the
          // router show /unlock rather than trickling 401s page by page.
          if (msg.type === 'auth.revoked') {
            const reason = (msg.payload?.data || msg.payload || {}).reason || 'revoked';
            try {
              if (this._ws) this._ws.close();
            } catch (_) { /* already closed */ }
            this._wsConnected = false;
            this._setConnectionStatus('disconnected');
            window.dispatchEvent(new CustomEvent('boarai-auth-required', {
              detail: { code: 'auth_required', reason },
            }));
          }
          // Online update (core.update): worker phases/terminals, forwarded
          // from the bus (payload wrapped as {event, data} by serve.py).
          if (msg.type && msg.type.startsWith('update.')) {
            const updatePayload = msg.payload?.data || msg.payload || {};
            if (this.onUpdateEvent) this.onUpdateEvent(msg.type, updatePayload);
            if (msg.type === 'update.available' && updatePayload.latest && !this.onUpdateEvent) {
              // No page is listening (settings closed): surface a toast so the
              // finding is never silent.
              this.actions.info(
                window.i18n.t('notifications.updateAvailable', { version: updatePayload.latest }),
                8000
              );
            }
          }
          // Session events: forward to registered callbacks
          if (msg.type === 'session.created' && this._onSessionCreated) {
            this._onSessionCreated(msg.payload?.data || msg.payload);
          }
          if (msg.type === 'session.updated' && this._onSessionUpdated) {
            this._onSessionUpdated(msg.payload?.data || msg.payload);
          }
          if (msg.type === 'session.run.status') {
            const payload = msg.payload || {};
            const sessionId = payload.session_id;
            const runId = payload.run_id;

            if (this._onSessionRunStatus) this._onSessionRunStatus(payload);

            // Recover a terminal event if its session-scoped stream frame was
            // missed during reconnect or a late subscription.
            if (sessionId && payload.status === 'completed' && this.onStreamDone) {
              this.onStreamDone(sessionId, runId);
            } else if (sessionId && (payload.status === 'failed' || payload.status === 'error') && this.onStreamError) {
              this.onStreamError(sessionId, payload.error || 'Run error', runId);
            } else if (sessionId && payload.status === 'cancelled' && this.onStreamCancelled) {
              this.onStreamCancelled(sessionId, payload.reason, runId);
            }
          }
          // Task events: forward to registered callback
          if (msg.type.startsWith('task.') && this._onTaskEvent) {
            this._onTaskEvent(msg.type, msg.payload);
          }
          // Extension load failures: durable toast + live list refresh.
          // WS half of the dual-channel contract (HTTP half: GET
          // /api/extensions `failed`). The server backs off retries, so this
          // cannot spam: at most one event per backoff window.
          if (msg.type === 'extension.load_failed') {
            const failData = msg.payload?.data || msg.payload || {};
            this.actions.error(
              window.i18n.t('notifications.extensionLoadFailed', { name: failData.name || '' }),
              6000
            );
            this.actions.loadExtensions();
          }
          // Skill events: non-blocking notice when the agent auto-writes a skill
          if (msg.type === 'skill.created' || msg.type === 'skill.updated' || msg.type === 'skill.archived') {
            const skillData = msg.payload?.data || msg.payload || {};
            const skillName = skillData.name || '';
            let skillText;
            if (msg.type === 'skill.created') {
              skillText = window.i18n.t('notifications.skillCreated', { name: skillName });
            } else if (msg.type === 'skill.updated') {
              skillText = window.i18n.t('notifications.skillUpdated', { name: skillName });
            } else {
              skillText = window.i18n.t('notifications.skillArchived', { name: skillName });
            }
            this.actions.info(skillText, 4000);
          }
          return;
        }
        
        // Handle request/response matching
        if (msg.id && this._pendingRequests[msg.id]) {
          const { resolve, reject } = this._pendingRequests[msg.id];
          delete this._pendingRequests[msg.id];
          if (msg.status === 'ok') {
            resolve(msg.payload);
          } else {
            reject(new Error(msg.payload?.message || msg.payload?.code || 'Request failed'));
          }
        }
      } catch (e) {
        console.warn('WS message parse error:', e);
      }
    };
    
    ws.onclose = () => {
      // A superseded socket must not clear the live one: close() fires this
      // asynchronously, so a reconnect started in between would otherwise be
      // dropped from the store while its socket is still open.
      if (this._ws !== ws) return;
      const wasConnected = this._wsConnected;
      this._wsConnected = false;
      this._setConnectionStatus('disconnected');
      this._ws = null;
      // Only notify on unexpected disconnects (not initial close)
      if (wasConnected) {
        this.actions.warning(window.i18n.t('statusbar.wsDisconnected'), 3000);
      }
      // Auto-reconnect after 3s
      this._wsReconnectTimer = setTimeout(() => this._connectWebSocket(), 3000);
    };
    
    ws.onerror = () => {
      this._setConnectionStatus('disconnected');
      ws.close();
    };
  },
  
  _setConnectionStatus(status) {
    this.state.connectionStatus = status;
    if (typeof this.state._onConnectionChange === 'function') {
      this.state._onConnectionChange(status);
    }
  },
  
  async _sendWsMessage(type, payload, timeout = 30000) {
    // Ensure WS is connected
    if (!this._ws || this._ws.readyState !== WebSocket.OPEN) {
      await this._waitForWsConnection();
    }
    
    return new Promise((resolve, reject) => {
      const id = ++this._msgIdCounter;
      this._pendingRequests[id] = { resolve, reject };
      
      const timer = setTimeout(() => {
        delete this._pendingRequests[id];
        reject(new Error(`Request timeout: ${type}`));
      }, timeout);
      
      // Wrap resolve/reject to clear timer
      this._pendingRequests[id] = {
        resolve: (v) => { clearTimeout(timer); resolve(v); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      };
      
      try {
        this._ws.send(JSON.stringify({ id, type, payload }));
      } catch (e) {
        clearTimeout(timer);
        delete this._pendingRequests[id];
        reject(e);
      }
    });
  },
  
  _waitForWsConnection(timeout = 5000) {
    if (this._wsConnected) return Promise.resolve();
    this._connectWebSocket();
    return new Promise((resolve, reject) => {
      const deadline = Date.now() + timeout;
      const check = () => {
        if (this._wsConnected) {
          resolve();
        } else if (Date.now() >= deadline) {
          reject(new Error('WebSocket connection timeout'));
        } else {
          setTimeout(check, 100);
        }
      };
      check();
    });
  },
  
  // API Methods (WS-first, HTTP fallback)
  api: {
    // ==================== Sessions API (via WebSocket) ====================
    async getSessions(params = {}) {
      const mapSession = (session) => {
        const id = session.session_id || session.id;
        if (!id) return null;
        return {
          id,
          title: session.title || 'Untitled',
          agent_name: session.agent_name || 'default',
          time: this._formatTime(session.updated_at),
          isPinned: !!session.pinned,
          isArchived: !!session.archived,
          messageCount: session.message_count || 0,
          preview: session.preview,
          source: session.source,
          status: session.status || 'active',
          hasError: session.status === 'error',
          model: session.model || null,
          thinking_mode: session.thinking_mode || 'none',
          workspace_path: session.workspace_path || '',
        };
      };

      const queryParams = new URLSearchParams({
        limit: String(params.limit || 50),
        offset: String(params.offset || 0),
        sort: params.sort || 'updated_at',
        order: params.order || 'desc',
        ...(params.status && { status: params.status }),
        ...(params.search && { search: params.search }),
        ...(params.source && { source: params.source }),
      });

      const stashSources = (sources) => {
        // Channel id → display name from the backend SourceRegistry; consumed
        // by the session-list source badge. Absent payload → keep previous.
        if (sources && typeof sources === 'object') {
          BoarAiStore.state.sourceNames = sources;
        }
      };

      // HTTP is authoritative for the list (avoids early-WS empty races).
      // WS remains the primary channel for create/update/stream.
      const fetchViaHttp = async () => {
        const response = await fetch(`/api/sessions?${queryParams}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const result = await response.json();
        if (!result.success) throw new Error(result.error?.message || 'Failed to get sessions');
        stashSources(result.data?.sources);
        return (result.data?.sessions || []).map(mapSession).filter(Boolean);
      };

      try {
        return await fetchViaHttp();
      } catch (httpError) {
        console.warn('HTTP getSessions failed, trying WS:', httpError);
        try {
          const payload = {
            limit: params.limit || 50,
            offset: params.offset || 0,
            sort: params.sort || 'updated_at',
            order: params.order || 'desc',
            ...(params.status && { status: params.status }),
            ...(params.search && { search: params.search }),
            ...(params.source && { source: params.source }),
          };
          const result = await BoarAiStore._sendWsMessage('session.list', payload);
          stashSources(result?.sources);
          return (result?.sessions || []).map(mapSession).filter(Boolean);
        } catch (wsError) {
          console.error('WS getSessions also failed:', wsError);
          throw httpError;
        }
      }
    },
    
    async createSession(title = 'New Session', metadata = {}, agentName = 'default') {
      try {
        const result = await BoarAiStore._sendWsMessage('session.create', {
          title, metadata, source: 'webui', agent_name: agentName,
        });
        return {
          id: result.session_id,
          title: result.title,
          agent_name: result.agent_name || agentName,
          time: this._formatTime(result.updated_at),
          isPinned: result.pinned,
          isArchived: false,
          messageCount: result.message_count,
          preview: null,
          source: 'webui',
        };
      } catch (error) {
        console.error('WS createSession failed, falling back to HTTP:', error);
        const response = await fetch('/api/sessions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title, metadata, source: 'webui', agent_name: agentName }),
        });
        const result = await response.json();
        if (!result.success) throw new Error(result.error?.message || 'Failed to create session');
        return {
          id: result.data.session_id,
          title: result.data.title,
          agent_name: result.data.agent_name || agentName,
          time: this._formatTime(result.data.updated_at),
          isPinned: result.data.pinned,
          isArchived: false,
          messageCount: result.data.message_count,
          preview: null,
          source: 'webui',
        };
      }
    },
    
    async getSession(sessionId, includeMessages = false) {
      try {
        BoarAiStore._trackSessionSubscription(sessionId);
        return await BoarAiStore._sendWsMessage('session.get', {
          session_id: sessionId, include_messages: includeMessages,
        });
      } catch (error) {
        console.error('WS getSession failed, falling back to HTTP:', error);
        const response = await fetch(`/api/sessions/${sessionId}?include_messages=${includeMessages}`);
        const result = await response.json();
        if (!result.success) throw new Error(result.error?.message || 'Failed to get session');
        return result.data;
      }
    },
    
    async renameSession(id, newTitle) {
      try {
        return await BoarAiStore._sendWsMessage('session.update', {
          session_id: id, title: newTitle,
        });
      } catch (error) {
        console.error('WS renameSession failed, falling back to HTTP:', error);
        const response = await fetch(`/api/sessions/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: newTitle }),
        });
        const result = await response.json();
        if (!result.success) throw new Error(result.error?.message || 'Failed to rename session');
        return { success: true };
      }
    },

    async updateMessage(sessionId, messageId, content) {
      // Edit latest user message: rewrite content + truncate the tail (branch re-run).
      try {
        return await BoarAiStore._sendWsMessage('session.message.update', {
          session_id: sessionId, message_id: messageId, content,
        });
      } catch (error) {
        console.error('WS updateMessage failed, falling back to HTTP:', error);
        const response = await fetch(`/api/sessions/${sessionId}/messages/${messageId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content }),
        });
        const result = await response.json();
        if (!result.success) throw new Error(result.error?.message || 'Failed to update message');
        return result.data;
      }
    },
    
    async updateSessionThinkingMode(id, thinkingMode) {
      const mode = (thinkingMode === undefined || thinkingMode === null || thinkingMode === '')
        ? 'none'
        : String(thinkingMode).toLowerCase();
      try {
        return await BoarAiStore._sendWsMessage('session.update', {
          session_id: id, thinking_mode: mode,
        });
      } catch (error) {
        console.error('WS updateSessionThinkingMode failed, falling back to HTTP:', error);
        const response = await fetch(`/api/sessions/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ thinking_mode: mode }),
        });
        const result = await response.json();
        if (!result.success) throw new Error(result.error?.message || 'Failed to update thinking mode');
        return { success: true, data: result.data };
      }
    },
    
    /** Session tool-approval mode: 'deny' | 'on' (ask) | 'off' (allow).
     *  Persisted into session.security_overrides.approval_mode on the server. */
    async updateSessionApprovalMode(id, approvalMode) {
      const mode = String(approvalMode || 'on').toLowerCase();
      try {
        return await BoarAiStore._sendWsMessage('session.update', {
          session_id: id, approval_mode: mode,
        });
      } catch (error) {
        console.error('WS updateSessionApprovalMode failed, falling back to HTTP:', error);
        const response = await fetch(`/api/sessions/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ approval_mode: mode }),
        });
        const result = await response.json();
        if (!result.success) throw new Error(result.error?.message || 'Failed to update approval mode');
        return { success: true, data: result.data };
      }
    },
    
    async updateSessionModel(id, model) {
      try {
        return await BoarAiStore._sendWsMessage('session.update', {
          session_id: id, model: model,
        });
      } catch (error) {
        console.error('WS updateSessionModel failed, falling back to HTTP:', error);
        const response = await fetch(`/api/sessions/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ model: model }),
        });
        const result = await response.json();
        if (!result.success) throw new Error(result.error?.message || 'Failed to update model');
        return { success: true };
      }
    },

    /** Set or clear session working directory (server-side path). path null/"" clears. */
    async updateSessionWorkspace(id, path) {
      const response = await fetch(`/api/sessions/${id}/workspace`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: path == null ? '' : path }),
      });
      const result = await response.json();
      if (!result.success) {
        throw new Error(result.error?.message || 'Failed to update workspace');
      }
      return result.data || result;
    },

    async fsRoots(sessionId) {
      const params = new URLSearchParams();
      if (sessionId) params.set('session_id', sessionId);
      const response = await fetch(`/api/fs/roots?${params.toString()}`);
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to list roots');
      return result.data || result;
    },

    async fsDefaultWorkspace(sessionId) {
      if (!sessionId) throw new Error('session_id is required');
      const params = new URLSearchParams({ session_id: sessionId });
      const response = await fetch(`/api/fs/default-workspace?${params.toString()}`);
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to get default workspace');
      return result.data || result;
    },

    async fsBrowse(path, showHidden = false) {
      const q = new URLSearchParams();
      if (path) q.set('path', path);
      if (showHidden) q.set('show_hidden', '1');
      const response = await fetch(`/api/fs/browse?${q.toString()}`);
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to browse');
      return result.data || result;
    },

    async fsValidate(path, allowOutside = true) {
      const response = await fetch('/api/fs/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, allow_outside_sandbox: allowOutside }),
      });
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Validate failed');
      return result.data || result;
    },

    // File viewer pane (phase-2 backend contract, core/api/fs.py):
    //   GET /api/fs/read?path=&session_id=
    //     -> {path, content, size, encoding, binary, truncated, mtime?}
    //   PUT /api/fs/write  {path, content, session_id}
    //     -> {path, size, mtime}
    // Both must pass PathGuard (read floor / write floor) server-side.
    async fsRead(path, sessionId) {
      const q = new URLSearchParams({ path });
      if (sessionId) q.set('session_id', sessionId);
      const response = await fetch(`/api/fs/read?${q.toString()}`);
      if (!response.ok) {
        const errBody = await response.json().catch(() => null);
        throw new Error(errBody?.error?.message || `Read failed (HTTP ${response.status})`);
      }
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to read file');
      return result.data || result;
    },

    // baseMtime enables the server-side optimistic lock: a mismatch yields
    // HTTP 409 whose message surfaces through the thrown Error.
    async fsWrite(path, content, sessionId, baseMtime) {
      const response = await fetch('/api/fs/write', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path, content,
          session_id: sessionId || undefined,
          base_mtime: baseMtime ?? undefined,
        }),
      });
      if (!response.ok) {
        const errBody = await response.json().catch(() => null);
        const err = new Error(errBody?.error?.message || `Write failed (HTTP ${response.status})`);
        err.status = response.status;
        throw err;
      }
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to write file');
      return result.data || result;
    },
    
    async togglePinSession(id, isPinned) {
      try {
        return await BoarAiStore._sendWsMessage('session.update', {
          session_id: id, pinned: isPinned,
        });
      } catch (error) {
        console.error('WS togglePinSession failed, falling back to HTTP:', error);
        const response = await fetch(`/api/sessions/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pinned: isPinned }),
        });
        const result = await response.json();
        if (!result.success) throw new Error(result.error?.message || 'Failed to toggle pin');
        return { success: true };
      }
    },
    
    async deleteSession(id) {
      try {
        return await BoarAiStore._sendWsMessage('session.delete', { session_id: id });
      } catch (error) {
        console.error('WS deleteSession failed, falling back to HTTP:', error);
        const response = await fetch(`/api/sessions/${id}`, { method: 'DELETE' });
        const result = await response.json();
        if (!result.success) throw new Error(result.error?.message || 'Failed to delete session');
        return { success: true };
      }
    },
    
    async archiveSession(id) {
      try {
        return await BoarAiStore._sendWsMessage('session.update', {
          session_id: id, archived: true,
        });
      } catch (error) {
        console.error('WS archiveSession failed, falling back to HTTP:', error);
        const response = await fetch(`/api/sessions/${id}/archive`, { method: 'POST' });
        const result = await response.json();
        if (!result.success) throw new Error(result.error?.message || 'Failed to archive session');
        return { success: true };
      }
    },
    
    // ==================== Messages API (via WebSocket) ====================
    async getMessages(sessionId, params = {}) {
      try {
        BoarAiStore._trackSessionSubscription(sessionId);
        const result = await BoarAiStore._sendWsMessage('session.get', {
          session_id: sessionId,
          include_messages: true,
          message_limit: params.limit || 50,
          ...(params.offset != null && { offset: params.offset }),
          ...(params.since_seq != null && { since_seq: params.since_seq }),
          ...(params.before_seq != null && { before_seq: params.before_seq }),
        });
        return {
            session_id: sessionId,
            messages: (result.messages || []).map((m, index) => ({
                message_id: m.message_id || m.id || m.tool_call_id || `msg_${sessionId}_${index}_${m.timestamp || m.role}`,
                role: m.role,
                content: m.content,
                timestamp: m.timestamp,
                // seq<=0 means "legacy/no seq" — normalize to null (consistent
                // with the `> 0` guards in _earliestSeq/_maxSeqOf/seqMatch).
                seq: (m.seq > 0 ? m.seq : null),
                tool_calls: m.tool_calls || null,
                tool_call_id: m.tool_call_id || null,
                thinking: m.thinking || null,
                metadata: m.metadata || {},
                tool_name: m.metadata?.tool_name || null,
                command: m.metadata?.command || null,
                chat_command: m.metadata?.chat_command || null,
                is_error: !!(m.metadata?.is_error),
                tool_result: m.metadata?.tool_result || null,
                error_kind: m.metadata?.error_kind || null,
                error_reason: m.metadata?.error_reason || null,
            })),
            total: result.total ?? (result.messages || []).length,
          limit: params.limit || 50,
          offset: params.offset || 0,
          has_more: !!result.has_more,
          watermark_seq: result.watermark_seq ?? null,
        };
      } catch (error) {
        console.error('WS getMessages failed, falling back to HTTP:', error);
        const queryParams = new URLSearchParams({
          limit: params.limit || 50,
          offset: params.offset || 0,
          ...(params.before && { before: params.before }),
          ...(params.since_seq != null && { since_seq: params.since_seq }),
          ...(params.before_seq != null && { before_seq: params.before_seq }),
        });
        const response = await fetch(`/api/sessions/${sessionId}/messages?${queryParams}`);
        const result = await response.json();
        if (!result.success) throw new Error(result.error?.message || 'Failed to get messages');
        return result.data;
      }
    },
    
    async sendMessage(sessionId, content, role = 'user', metadata = {}) {
      try {
        return await BoarAiStore._sendWsMessage('session.message', {
          session_id: sessionId, content, role, metadata,
        });
      } catch (error) {
        console.error('WS sendMessage failed, falling back to HTTP:', error);
        const response = await fetch(`/api/sessions/${sessionId}/messages`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content, role, metadata }),
        });
        const result = await response.json();
        if (!result.success) throw new Error(result.error?.message || 'Failed to send message');
        return result.data;
      }
    },
    
    async clearMessages(sessionId) {
      try {
        // No WS equivalent for clear, use HTTP
        const response = await fetch(`/api/sessions/${sessionId}/clear`, { method: 'POST' });
        const result = await response.json();
        if (!result.success) throw new Error(result.error?.message || 'Failed to clear messages');
        return { success: true };
      } catch (error) {
        console.error('Failed to clear messages:', error);
        throw error;
      }
    },

    // Per-run file-change log (added/modified/deleted + unified diff), newest
    // first. Live updates arrive via the run.diff WS event; this backs reload.
    async getChanges(sessionId, limit = 50) {
      try {
        const response = await fetch(
          `/api/sessions/${encodeURIComponent(sessionId)}/changes?limit=${limit}`
        );
        const result = await response.json();
        if (!result.success) throw new Error(result.error?.message || 'Failed to get changes');
        return result.data?.changes || [];
      } catch (error) {
        console.error('Failed to get session changes:', error);
        return [];
      }
    },

    // Revert files the session touched, to before the conversation. Pass
    // `path` for per-row Undo (single file, same before-session semantics).
    async revertAllChanges(sessionId, path) {
      const response = await fetch(
        `/api/sessions/${encodeURIComponent(sessionId)}/changes/revert-all`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(path ? { path } : {}),
        }
      );
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Revert failed');
      return result.data;
    },

    // Accept ("keep") the session's file changes: files stay on disk, the
    // server drops the change log and releases the revert snapshots. Pass
    // `path` for per-row Keep (single file).
    async acceptChanges(sessionId, path) {
      const response = await fetch(
        `/api/sessions/${encodeURIComponent(sessionId)}/changes/accept`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(path ? { path } : {}),
        }
      );
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Accept failed');
      return result.data;
    },

    // ==================== Chat / Run API (via WebSocket with streaming) ====================
    async sendChatMessage(sessionId, content, model, agentName, extraParams, attachments) {
      const wsPayload = {
        session_id: sessionId, content, model,
        ...(agentName && { agent_name: agentName }),
        ...(extraParams || {}),
      };
      // Attachment references (uploaded via POST /api/sessions/{id}/files)
      if (attachments && attachments.length) wsPayload.attachments = attachments;
      try {
        BoarAiStore._trackSessionSubscription(sessionId);
        const result = await BoarAiStore._sendWsMessage('session.run.create', wsPayload, 10000);
        // A chat-command turn completes in place (no run is created): the
        // backend answers with `command` instead of `run_id`.
        return {
          run_id: result.run_id,
          session_id: sessionId,
          status: result.status,
          command: result.command || null,
        };
      } catch (error) {
        // The SSE fallback (GET /api/runs/events) was removed: it silently dropped
        // thinking/retry/tool events and duplicated the streaming channel. Run
        // streaming is WS-only; callers surface this error so the UI can retry.
        console.error('WS sendChatMessage failed (run streaming is WS-only):', error);
        throw error;
      }
    },
    
    // ==================== Chat commands (slash menu) ====================
    // The command system is chat-only: commands are intercepted by the
    // server BEFORE any LLM run. The catalog feeds the composer's "/" popup.
    async getChatCommands() {
      try {
        const response = await fetch('/api/commands');
        const result = await response.json();
        return result.commands || [];
      } catch (error) {
        console.warn('Failed to load chat commands:', error);
        return [];
      }
    },

    async getRun(runId) {
      try {
        return await BoarAiStore._sendWsMessage('session.run.get', { run_id: runId });
      } catch (error) {
        console.error('WS getRun failed, falling back to HTTP:', error);
        const response = await fetch('/api/runs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'get', run_id: runId }),
        });
        const result = await response.json();
        if (!result.success) throw new Error(result.error?.message || 'Failed to get run');
        return result.data;
      }
    },
    
    // ==================== Session attachment upload ====================
    // Upload via multipart HTTP (binary channel); only reference metadata is
    // later passed through the WS/JSON chat protocol.
    async uploadAttachment(sessionId, file) {
      const form = new FormData();
      form.append('file', file);
      const response = await fetch(`/api/sessions/${sessionId}/files`, {
        method: 'POST',
        body: form, // do NOT set Content-Type — browser adds the boundary
      });
      const result = await response.json();
      if (!result.success) {
        throw new Error(result.error?.message || 'Upload failed');
      }
      return result.data;
    },

    attachmentUrl(sessionId, storedName) {
      return `/api/sessions/${sessionId}/files/${encodeURIComponent(storedName)}`;
    },

    // Resume streaming after page reload for an ongoing run
    // Streaming handled via direct callbacks onStreamChunk/onStreamDone/onStreamError
    async subscribeToActiveRun(sessionId) {
      try {
        return await BoarAiStore._sendWsMessage('session.run.subscribe', {
          session_id: sessionId,
        }, 5000);
      } catch (error) {
        console.warn('subscribeToActiveRun failed:', error);
        return { run_id: null };
      }
    },

    // Cancel an active run (for interrupt/resend)
    async cancelRun(runId) {
      if (!runId) return { success: false, error: 'No run_id' };
      try {
        return await BoarAiStore._sendWsMessage('session.run.cancel', {
          run_id: runId,
        }, 3000);
      } catch (error) {
        console.warn('WS cancelRun failed, falling back to HTTP:', error);
        const response = await fetch('/api/runs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'cancel', run_id: runId }),
        });
        const result = await response.json();
        if (!result.success) throw new Error(result.error?.message || 'Failed to cancel run');
        return result.data;
      }
    },
    
    // ==================== Extensions API (HTTP) ====================
    async getExtensions() {
      const resp = await fetch('/api/extensions');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const json = await resp.json();
      if (!json.success) throw new Error(json.error?.message || 'Failed to load extensions');
      return json.data;
    },

    async toggleExtension(type, id, enabled) {
      const endpoint = enabled ? 'enable' : 'disable';
      const resp = await fetch(`/api/extensions/${encodeURIComponent(id)}/${endpoint}`, {
        method: 'POST',
      });
      let json = null;
      try { json = await resp.json(); } catch (e) { /* non-JSON body */ }
      if (!resp.ok) {
        // 409 carries the structured load failure (code/message/hint) —
        // surface it instead of a bare "HTTP 409".
        const err = new Error(json?.error?.message || `HTTP ${resp.status}`);
        err.code = json?.error?.code || '';
        err.hint = json?.error?.hint || '';
        throw err;
      }
      if (!json?.success) throw new Error(json?.error?.message || 'Failed to toggle extension');
      return json.data;
    },

    async trustExtension(id) {
      const resp = await fetch(`/api/extensions/${encodeURIComponent(id)}/trust`, {
        method: 'POST',
      });
      let json = null;
      try { json = await resp.json(); } catch (e) { /* non-JSON body */ }
      if (!resp.ok) {
        // 409 carries the structured load failure (code/message/hint).
        const err = new Error(json?.error?.message || `HTTP ${resp.status}`);
        err.code = json?.error?.code || '';
        err.hint = json?.error?.hint || '';
        throw err;
      }
      if (!json?.success) throw new Error(json?.error?.message || 'Failed to trust extension');
      return json.data;
    },

    async untrustExtension(id) {
      const resp = await fetch(`/api/extensions/${encodeURIComponent(id)}/untrust`, {
        method: 'POST',
      });
      let json = null;
      try { json = await resp.json(); } catch (e) { /* non-JSON body */ }
      if (!resp.ok) {
        const err = new Error(json?.error?.message || `HTTP ${resp.status}`);
        err.code = json?.error?.code || '';
        throw err;
      }
      if (!json?.success) throw new Error(json?.error?.message || 'Failed to revoke trust');
      return json.data;
    },

    async installExtension(type, id) {
      const resp = await fetch('/api/extensions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, id }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const json = await resp.json();
      if (!json.success) throw new Error(json.error?.message || 'Failed to install extension');
      return json.data;
    },

    async uninstallExtension(type, id) {
      const resp = await fetch(`/api/extensions/${encodeURIComponent(id)}`, {
        method: 'DELETE',
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const json = await resp.json();
      if (!json.success) throw new Error(json.error?.message || 'Failed to uninstall extension');
      return json.data;
    },
    
    // ==================== Tasks API (HTTP, real backend) ====================
    async getTasks(params = {}) {
      const query = new URLSearchParams();
      if (params.session_id) query.set('session_id', params.session_id);
      if (params.status) query.set('status', params.status);
      if (params.kind) query.set('kind', params.kind);
      query.set('limit', params.limit || 100);
      query.set('offset', params.offset || 0);
      const response = await fetch(`/api/tasks?${query.toString()}`);
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to load tasks');
      return result.data.tasks;
    },
    
    async createTask(task) {
      const response = await fetch('/api/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(task),
      });
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to create task');
      return result.data;
    },
    
    async getTask(taskId) {
      const response = await fetch(`/api/tasks/${taskId}`);
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to load task');
      return result.data;
    },
    
    async updateTask(taskId, updates) {
      const response = await fetch(`/api/tasks/${taskId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      });
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to update task');
      return result.data;
    },
    
    async deleteTask(taskId) {
      const response = await fetch(`/api/tasks/${taskId}`, { method: 'DELETE' });
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to delete task');
      return result.data;
    },
    
    async executeTask(taskId) {
      const response = await fetch(`/api/tasks/${taskId}/execute`, { method: 'POST' });
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to execute task');
      return result.data;
    },
    
    async cancelTask(taskId) {
      const response = await fetch(`/api/tasks/${taskId}/cancel`, { method: 'POST' });
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to cancel task');
      return result.data;
    },
    
    async pauseTask(taskId) {
      const response = await fetch(`/api/tasks/${taskId}/pause`, { method: 'POST' });
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to pause task');
      return result.data;
    },
    
    async resumeTask(taskId) {
      const response = await fetch(`/api/tasks/${taskId}/resume`, { method: 'POST' });
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to resume task');
      return result.data;
    },
    
    async getTaskTree(taskId) {
      const response = await fetch(`/api/tasks/${taskId}/tree`);
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to load task tree');
      return result.data;
    },

    async getTaskHistory(taskId) {
      const response = await fetch(`/api/tasks/${taskId}/history`);
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to load task history');
      return result.data;
    },
    
    // ==================== LLM Chat API (HTTP, direct) ====================
    async sendDirectChatMessage(messages, model) {
      const response = await fetch('/api/llm/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages, model }),
      });
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'LLM chat failed');
      return result.data;
    },
    
    // ==================== System API (HTTP) ====================
    async getSystemInfo() {
      // Real data from /api/status (the previous random placeholder was fake).
      try {
        const resp = await fetch('/api/status');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        const server = data.server || {};
        return {
          version: data.version || '0.1.0',
          uptime: server.uptime_sec || 0,
          cpuUsage: 0, // /api/status does not expose CPU; kept for shape parity
          memoryUsage: server.memory_mb || 0, // MB
          activeConnections: server.ws_clients || 0,
          os: data.system ? `${data.system.os} ${data.system.os_release}` : '',
          python: data.system ? data.system.python : '',
        };
      } catch (e) {
        console.error('getSystemInfo failed:', e);
        return { version: '0.1.0', uptime: 0, cpuUsage: 0, memoryUsage: 0, activeConnections: 0 };
      }
    },
    
    async getStats() {
      try {
        const response = await fetch('/api/monitoring/stats');
        const result = await response.json();
        if (!result.success) throw new Error(result.error?.message || 'Failed to get stats');
        return result.data;
      } catch (error) {
        console.error('Failed to load stats:', error);
        // Fallback to empty data
        return {
          tokens: { total: 0, today: 0, hit_rate: 0, trend: [0,0,0,0,0,0,0] },
          cache: { total_read: 0, total_created: 0, hit_rate: 0, trend: [0,0,0,0,0,0,0] },
          sessions: { total: 0, active: 0 },
          compression: { total_count: 0 },
          savings: { estimated_usd: 0, tokens_saved: 0 },
          per_agent: [],
          recent_requests: [],
        };
      }
    },
    
    // ==================== Models API (HTTP) ====================
    async getModels() {
      const response = await fetch('/api/llm/models');
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to get models');
      return result.data;
    },
    
    // ==================== Providers API (HTTP) ====================
    async getProviders() {
      const response = await fetch('/api/llm/providers');
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to get providers');
      return result.data;
    },
    
    async addProvider(config) {
      const response = await fetch('/api/llm/providers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to add provider');
      return result.data;
    },
    
    async updateProvider(name, updates) {
      const response = await fetch(`/api/llm/providers/${encodeURIComponent(name)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      });
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to update provider');
      return result.data;
    },
    
    async deleteProvider(name) {
      const response = await fetch(`/api/llm/providers/${encodeURIComponent(name)}`, {
        method: 'DELETE',
      });
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to delete provider');
      return result.data;
    },
    
    // Global chat default is set via capability card (not per-provider "default" button).
    async setDefaultModel(providerName, modelName) {
      const response = await fetch(`/api/llm/providers/${encodeURIComponent(providerName)}/default`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ default_model: modelName }),
      });
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to set default model');
      return result.data;
    },
    
    async setCapabilityModel(capability, provider, model) {
      const response = await fetch('/api/llm/capability-models', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ capability, provider, model }),
      });
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to set capability model');
      return result.data;
    },
    
    async getCapabilityModels() {
      const response = await fetch('/api/llm/capability-models');
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to get capability models');
      return result.data.capability_models || {};
    },
    
    async validateProvider(name) {
      const response = await fetch(`/api/llm/providers/${encodeURIComponent(name)}/validate`, {
        method: 'POST',
      });
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to validate provider');
      return result.data;
    },

    async fetchProviderModels(config) {
      const response = await fetch('/api/llm/providers/fetch-models', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });
      const result = await response.json();
      if (!result.success) throw new Error(result.error?.message || 'Failed to fetch models');
      return result.data;
    },
    
    // ==================== Helper ====================
    _formatTime(isoString) {
      if (!isoString) return 'Unknown';
      try {
        const date = new Date(isoString);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);
        const diffDays = Math.floor(diffMs / 86400000);
        if (diffMins < 1) return 'Just now';
        if (diffMins < 60) return `${diffMins} min ago`;
        if (diffHours < 24) return `${diffHours} hour${diffHours > 1 ? 's' : ''} ago`;
        if (diffDays < 7) return `${diffDays} day${diffDays > 1 ? 's' : ''} ago`;
        return date.toLocaleDateString();
      } catch (error) {
        return 'Unknown';
      }
    },

    // ==================== Trash API ====================
    async getTrash(type, limit = 50) {
      const params = new URLSearchParams();
      if (type) params.append('type', type);
      params.append('limit', String(limit));
      const response = await fetch(`/api/trash?${params.toString()}`);
      const result = await response.json();
      return result.success ? result.data : { entries: [], stats: { total: 0 } };
    },

    async restoreTrash(trashId) {
      const response = await fetch(`/api/trash/${trashId}/restore`, { method: 'POST' });
      return await response.json();
    },

    async purgeTrashItem(trashId) {
      const response = await fetch(`/api/trash/${trashId}`, { method: 'DELETE' });
      return await response.json();
    },

    async purgeAllTrash() {
      const response = await fetch('/api/trash', { method: 'DELETE' });
      return await response.json();
    },

    async distillTrashItem(trashId) {
      const response = await fetch(`/api/trash/${trashId}/distill`, { method: 'POST' });
      return await response.json();
    },

    async getTrashSnapshots(limit = 50) {
      const response = await fetch(`/api/trash/snapshots?limit=${limit}`);
      const result = await response.json();
      return result.success ? result.data : { snapshots: [] };
    },

    // ==================== Memory API ====================
    async getMemoryStats() {
      const response = await fetch('/api/memory/stats');
      const result = await response.json();
      return result.success ? result.data : {};
    },

    async getMemoryEntries(query) {
      const params = new URLSearchParams();
      if (query?.pod) params.append('pod', query.pod);
      if (query?.text) params.append('text', query.text);
      if (query?.entry_type) params.append('entry_type', query.entry_type);
      if (query?.limit) params.append('limit', String(query.limit));
      const response = await fetch(`/api/memory/entries?${params.toString()}`);
      const result = await response.json();
      return result.success ? result.data : { entries: [] };
    },

    async deleteMemoryEntry(entryId, pod) {
      const params = pod ? `?pod=${encodeURIComponent(pod)}` : '';
      const response = await fetch(`/api/memory/entries/${encodeURIComponent(entryId)}${params}`, { method: 'DELETE' });
      return await response.json();
    },

    async updateMemoryEntry(entryId, fields) {
      const response = await fetch(`/api/memory/entries/${encodeURIComponent(entryId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fields || {}),
      });
      return await response.json();
    },

    async getMemoryStaging() {
      const response = await fetch('/api/memory/staging');
      const result = await response.json();
      return result.success ? result.data : { insights: [] };
    },

    async confirmStaging(id) {
      const response = await fetch(`/api/memory/staging/${id}/confirm`, { method: 'POST' });
      return await response.json();
    },

    async rejectStaging(id) {
      const response = await fetch(`/api/memory/staging/${id}/reject`, { method: 'POST' });
      return await response.json();
    },

    async consolidateMemory() {
      const response = await fetch('/api/memory/consolidate', { method: 'POST' });
      return await response.json();
    },
  },
  
  // Actions
  actions: {
    // Loading state management
    showLoading(text = '') {
      BoarAiStore.state.isLoading = true;
      BoarAiStore.state.loadingText = text;
    },
    
    hideLoading() {
      BoarAiStore.state.isLoading = false;
      BoarAiStore.state.loadingText = '';
    },
    
    // Notification management
    notify(message, type = 'info', duration = 3000) {
      const id = ++BoarAiStore.state.notificationId;
      const notification = { id, message, type, show: true };
      
      BoarAiStore.state.notifications.push(notification);
      
      if (duration > 0) {
        setTimeout(() => {
          BoarAiStore.actions.dismissNotification(id);
        }, duration);
      }
      
      return id;
    },
    
    success(message, duration = 3000) {
      return BoarAiStore.actions.notify(message, 'success', duration);
    },
    
    error(message, duration = 5000) {
      return BoarAiStore.actions.notify(message, 'error', duration);
    },
    
    warning(message, duration = 4000) {
      return BoarAiStore.actions.notify(message, 'warning', duration);
    },
    
    info(message, duration = 3000) {
      return BoarAiStore.actions.notify(message, 'info', duration);
    },
    
    dismissNotification(id) {
      const index = BoarAiStore.state.notifications.findIndex(n => n.id === id);
      if (index > -1) {
        BoarAiStore.state.notifications[index].show = false;
        setTimeout(() => {
          BoarAiStore.state.notifications.splice(index, 1);
        }, 300);
      }
    },
    
    // Confirm dialog management
    confirm(options) {
      return new Promise((resolve) => {
        BoarAiStore.state.confirmDialog = {
          show: true,
          title: options.title || 'Confirm',
          message: options.message || 'Are you sure?',
          confirmText: options.confirmText || 'Confirm',
          cancelText: options.cancelText || 'Cancel',
          type: options.type || 'warning',
          onConfirm: () => {
            BoarAiStore.state.confirmDialog.show = false;
            resolve(true);
          },
          onCancel: () => {
            BoarAiStore.state.confirmDialog.show = false;
            resolve(false);
          }
        };
      });
    },
    
    // Extensions actions
    async loadExtensions() {
      BoarAiStore.state.extensionsLoading = true;
      try {
        const extensions = await BoarAiStore.api.getExtensions();
        BoarAiStore.state.extensions = extensions;
      } catch (error) {
        BoarAiStore.actions.error(window.i18n.t('notifications.loadExtensionsFailed'));
        console.error('Failed to load extensions:', error);
      } finally {
        BoarAiStore.state.extensionsLoading = false;
      }
    },
    
    async toggleExtension(type, id, enabled) {
      try {
        await BoarAiStore.api.toggleExtension(type, id, enabled);
        const list = BoarAiStore.state.extensions[type];
        if (list) {
          const ext = list.find(e => e.id === id);
          if (ext) {
            ext.enabled = enabled;
          }
        }
        BoarAiStore.actions.success(enabled ? window.i18n.t('notifications.extensionEnabled') : window.i18n.t('notifications.extensionDisabled'));
        // A disable of a FAILED extension removes its card; refresh so the
        // list and the failure registry stay in sync.
        if (!enabled) BoarAiStore.actions.loadExtensions();
      } catch (error) {
        const detail = error?.message && !error.message.startsWith('HTTP ') ? error.message : '';
        const hint = error?.hint || '';
        BoarAiStore.actions.error(
          detail ? `${detail}${hint ? ' — ' + hint : ''}` : window.i18n.t('notifications.toggleExtensionFailed'),
          detail ? 8000 : undefined
        );
        BoarAiStore.actions.loadExtensions();
        console.error('Failed to toggle extension:', error);
      }
    },

    async trustExtension(id) {
      try {
        await BoarAiStore.api.trustExtension(id);
        BoarAiStore.actions.success(window.i18n.t('notifications.extensionTrusted', { name: id }));
      } catch (error) {
        const detail = error?.message && !error.message.startsWith('HTTP ') ? error.message : '';
        const hint = error?.hint || '';
        BoarAiStore.actions.error(
          detail ? `${detail}${hint ? ' — ' + hint : ''}` : window.i18n.t('notifications.trustExtensionFailed'),
          detail ? 10000 : undefined
        );
        console.error('Failed to trust extension:', error);
      } finally {
        // Trust grants load+enable, or leaves a fresh failure record —
        // either way the list and the failure registry must be re-read.
        BoarAiStore.actions.loadExtensions();
      }
    },

    async untrustExtension(id) {
      try {
        await BoarAiStore.api.untrustExtension(id);
        BoarAiStore.actions.success(window.i18n.t('notifications.extensionUntrusted', { name: id }));
      } catch (error) {
        const detail = error?.message && !error.message.startsWith('HTTP ') ? error.message : '';
        BoarAiStore.actions.error(
          detail || window.i18n.t('notifications.trustExtensionFailed')
        );
        console.error('Failed to revoke trust:', error);
      } finally {
        BoarAiStore.actions.loadExtensions();
      }
    },

    async uninstallExtension(type, id) {
      return await BoarAiStore.api.uninstallExtension(type, id);
    },
    
    // Sessions actions
    async loadSessions() {
      BoarAiStore.state.sessionsLoading = true;
      try {
        const sessions = await BoarAiStore.api.getSessions();
        BoarAiStore.state.sessions = sessions;
      } catch (error) {
        BoarAiStore.actions.error(window.i18n.t('notifications.loadSessionsFailed'));
        console.error('Failed to load sessions:', error);
      } finally {
        BoarAiStore.state.sessionsLoading = false;
      }
    },
    
    // Tasks actions
    async loadTasks() {
      BoarAiStore.state.tasksLoading = true;
      try {
        const tasks = await BoarAiStore.api.getTasks();
        BoarAiStore.state.tasks = tasks;
      } catch (error) {
        BoarAiStore.actions.error(window.i18n.t('notifications.loadTasksFailed'));
        console.error('Failed to load tasks:', error);
      } finally {
        BoarAiStore.state.tasksLoading = false;
      }
    },
    
    // System actions
    async loadSystemInfo() {
      try {
        const info = await BoarAiStore.api.getSystemInfo();
        BoarAiStore.state.systemInfo = info;
      } catch (error) {
        console.error('Failed to load system info:', error);
      }
    },
    
    async loadStats() {
      try {
        const stats = await BoarAiStore.api.getStats();
        BoarAiStore.state.dataStats = stats;
      } catch (error) {
        console.error('Failed to load stats:', error);
      }
    }
  },
  
  // Helper to inject store into Vue app
  install(app) {
    app.provide('store', BoarAiStore);
    app.config.globalProperties.$store = BoarAiStore;
  }
};

// Initialize WebSocket connection
BoarAiStore._connectWebSocket();

// Export to global scope
window.BoarAiStore = BoarAiStore;
