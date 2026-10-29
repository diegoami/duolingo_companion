// ==UserScript==
// @name         Duolingo companion: discovery
// @namespace    duolingo-companion
// @version      0.1.0
// @description  Milestone 1. Records Duolingo's JSON API calls (read-only) and uploads them to your companion backend.
// @match        https://www.duolingo.com/*
// @run-at       document-start
// @inject-into  page
// @sandbox      raw
// @noframes
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_deleteValue
// @grant        GM_listValues
// @grant        GM_registerMenuCommand
// @connect      *
// ==/UserScript==

// Runs in the page context (@inject-into page / @sandbox raw) so it can wrap the page's own
// fetch and XMLHttpRequest. It only observes responses: it never sends requests to Duolingo.

(function () {
  'use strict';

  const VERSION = '0.1.0';
  const MAX_BODY_CHARS = 5 * 1024 * 1024;
  const JWT_RE = /eyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]{5,}/g;

  // ---------------------------------------------------------------- capture (pure, testable)

  function redact(text) {
    return typeof text === 'string' ? text.replace(JWT_RE, '[REDACTED_JWT]') : text;
  }

  function isDuolingoUrl(url, base) {
    try {
      return /(^|\.)duolingo\.com$/i.test(new URL(url, base).hostname);
    } catch (_) {
      return false;
    }
  }

  function bodyToText(body) {
    if (body == null) return null;
    if (typeof body === 'string') return body;
    if (typeof URLSearchParams !== 'undefined' && body instanceof URLSearchParams) return body.toString();
    const name = (body && body.constructor && body.constructor.name) || typeof body;
    return `[${name} body not captured]`;
  }

  function looksLikeJson(text) {
    return typeof text === 'string' && /^\s*[[{]/.test(text);
  }

  // JSON responses, plus any write whose request body is JSON (e.g. a session completion).
  function shouldCapture(method, contentType, requestBody) {
    if (/json/i.test(contentType || '')) return true;
    return method !== 'GET' && method !== 'HEAD' && looksLikeJson(requestBody);
  }

  function buildEntry(via, method, url, requestBody, status, contentType, responseText, startedAt) {
    const text = typeof responseText === 'string' ? responseText : '';
    return {
      via,
      method,
      url: redact(url),
      status,
      response_content_type: contentType || null,
      request_body: redact(requestBody),
      response_body: redact(text.length > MAX_BODY_CHARS ? text.slice(0, MAX_BODY_CHARS) : text),
      response_truncated: text.length > MAX_BODY_CHARS,
      started_at: new Date(startedAt).toISOString(),
      duration_ms: Date.now() - startedAt,
    };
  }

  function installHooks(win, onCapture) {
    const base = () => (win.location && win.location.href) || 'https://www.duolingo.com/';
    const emit = (entry) => {
      try {
        onCapture(entry);
      } catch (_) {
        /* never break the page */
      }
    };

    const origFetch = win.fetch;
    if (typeof origFetch === 'function') {
      win.fetch = function (input, init) {
        let capture = null;
        try {
          const isRequest = typeof win.Request === 'function' && input instanceof win.Request;
          const url = isRequest ? input.url : String(input);
          if (isDuolingoUrl(url, base())) {
            const method = String((init && init.method) || (isRequest && input.method) || 'GET').toUpperCase();
            let requestBody = Promise.resolve(null);
            if (init && init.body != null) requestBody = Promise.resolve(bodyToText(init.body));
            else if (isRequest && method !== 'GET' && method !== 'HEAD') {
              // Clone before fetch() marks the Request body as used.
              requestBody = input.clone().text().catch(() => null);
            }
            capture = { url, method, requestBody, startedAt: Date.now() };
          }
        } catch (_) {
          capture = null;
        }

        const promise = origFetch.apply(win, arguments);
        if (!capture) return promise;
        try {
          const { url, method, requestBody, startedAt } = capture;
          promise.then((res) => {
            const contentType = res.headers.get('content-type') || '';
            // Clone synchronously, before the page reads the body.
            const copy = /json/i.test(contentType) || method !== 'GET' ? res.clone() : null;
            requestBody.then((reqText) => {
              if (!copy || !shouldCapture(method, contentType, reqText)) return;
              return copy.text().then((text) =>
                emit(buildEntry('fetch', method, res.url || url, reqText, res.status, contentType, text, startedAt)));
            }).catch(() => {});
          }, () => {});
        } catch (_) {
          /* never break the page */
        }
        return promise;
      };
    }

    const XHR = win.XMLHttpRequest;
    if (XHR && XHR.prototype) {
      const meta = new WeakMap();
      const origOpen = XHR.prototype.open;
      const origSend = XHR.prototype.send;
      XHR.prototype.open = function (method, url) {
        meta.set(this, { method: String(method || 'GET').toUpperCase(), url: String(url) });
        return origOpen.apply(this, arguments);
      };
      XHR.prototype.send = function (body) {
        try {
          const m = meta.get(this);
          if (m && isDuolingoUrl(m.url, base())) {
            const startedAt = Date.now();
            const requestBody = bodyToText(body);
            const xhr = this;
            xhr.addEventListener('loadend', () => {
              try {
                const contentType = xhr.getResponseHeader('content-type') || '';
                if (!shouldCapture(m.method, contentType, requestBody)) return;
                let text;
                if (xhr.responseType === '' || xhr.responseType === 'text') text = xhr.responseText;
                else if (xhr.responseType === 'json') text = JSON.stringify(xhr.response);
                else text = `[${xhr.responseType} response not captured]`;
                emit(buildEntry('xhr', m.method, xhr.responseURL || m.url, requestBody, xhr.status, contentType, text, startedAt));
              } catch (_) {
                /* never break the page */
              }
            });
          }
        } catch (_) {
          /* never break the page */
        }
        return origSend.apply(this, arguments);
      };
    }
  }

  if (typeof module === 'object' && module.exports) {
    module.exports = { installHooks, shouldCapture, redact, isDuolingoUrl, bodyToText, buildEntry };
    return;
  }

  // ---------------------------------------------------------------- queue + upload (userscript only)

  const QUEUE_PREFIX = 'q:'; // q:<kind>:<client_id>, deleted only after the backend confirms
  const DEAD_PREFIX = 'dead:'; // payloads the backend rejected as malformed; kept, never dropped
  const MAX_BACKOFF_MS = 5 * 60 * 1000;

  const state = { captured: 0, sent: 0, error: null, flushing: false, backoffMs: 0, retryTimer: null, flushTimer: null };

  function config() {
    const endpoint = String(GM_getValue('cfg:endpoint', '') || '').replace(/\/+$/, '');
    const secret = String(GM_getValue('cfg:secret', '') || '');
    return { endpoint, secret, ok: Boolean(endpoint && secret) };
  }

  function configure() {
    const current = config();
    const endpoint = prompt('Companion backend URL (e.g. https://mybox.tailnet.ts.net)', current.endpoint);
    if (endpoint === null) return;
    const secret = prompt('Ingest secret (DUO_INGEST_SECRET)', current.secret);
    if (secret === null) return;
    GM_setValue('cfg:endpoint', endpoint.trim());
    GM_setValue('cfg:secret', secret.trim());
    state.error = null;
    flushSoon(0);
  }

  function keysWith(prefix) {
    return GM_listValues().filter((k) => k.startsWith(prefix)).sort();
  }

  function enqueue(kind, data) {
    const clientId = crypto.randomUUID();
    const envelope = {
      client_id: clientId,
      kind,
      captured_at: new Date().toISOString(),
      page_url: location.href,
      script_version: VERSION,
      user_agent: navigator.userAgent,
      data,
    };
    GM_setValue(`${QUEUE_PREFIX}${kind}:${clientId}`, JSON.stringify(envelope));
    state.captured += 1;
    render();
    flushSoon(2000);
  }

  function post(cfg, kind, body) {
    return new Promise((resolve) => {
      GM_xmlhttpRequest({
        method: 'POST',
        url: `${cfg.endpoint}/ingest/${kind}`,
        headers: { 'Content-Type': 'application/json', 'X-Ingest-Secret': cfg.secret },
        data: body,
        timeout: 30000,
        onload: (r) => resolve(r.status),
        onerror: () => resolve(0),
        ontimeout: () => resolve(0),
      });
    });
  }

  function flushSoon(delayMs) {
    clearTimeout(state.flushTimer);
    state.flushTimer = setTimeout(flush, delayMs);
  }

  async function flush() {
    const cfg = config();
    if (state.flushing || !cfg.ok) return render();
    state.flushing = true;
    clearTimeout(state.retryTimer);
    let failed = false;
    try {
      for (const key of keysWith(QUEUE_PREFIX)) {
        const body = GM_getValue(key);
        const kind = key.split(':')[1];
        const status = typeof body === 'string' ? await post(cfg, kind, body) : 400;
        if (status >= 200 && status < 300) {
          GM_deleteValue(key);
          state.sent += 1;
          state.error = null;
        } else if (status === 400 || status === 404 || status === 413 || status === 422) {
          // Malformed for the server: park it so it can't block the queue.
          GM_setValue(DEAD_PREFIX + key, typeof body === 'string' ? body : JSON.stringify(body));
          GM_deleteValue(key);
          state.error = `server rejected a capture (${status})`;
        } else {
          state.error = status === 401 ? 'secret rejected' : status ? `server error ${status}` : 'offline';
          failed = true;
          break;
        }
        render();
      }
    } finally {
      state.flushing = false;
    }
    if (failed && state.error !== 'secret rejected') {
      state.backoffMs = Math.min(Math.max(state.backoffMs * 2, 5000), MAX_BACKOFF_MS);
      state.retryTimer = setTimeout(flush, state.backoffMs);
    } else {
      state.backoffMs = 0;
    }
    render();
  }

  function snapshotDom() {
    const root = document.querySelector('[data-test*="challenge"]') || document.querySelector('main') || document.body;
    const html = root ? root.outerHTML : '';
    enqueue('discovery', {
      via: 'dom',
      url: location.href,
      html: html.length > MAX_BODY_CHARS ? html.slice(0, MAX_BODY_CHARS) : html,
    });
  }

  // ---------------------------------------------------------------- badge

  let badge = null;

  function render() {
    if (!document.body) return;
    if (!badge) {
      badge = document.createElement('div');
      badge.id = 'duo-companion-badge';
      badge.style.cssText = [
        'position:fixed', 'left:6px', 'bottom:6px', 'z-index:2147483647',
        'font:11px/1.4 system-ui,sans-serif', 'padding:2px 7px', 'border-radius:9px',
        'color:#fff', 'opacity:.75', 'cursor:pointer', 'user-select:none', 'pointer-events:auto',
      ].join(';');
      badge.title = 'Duolingo companion (discovery). Tap: upload now / set up';
      badge.addEventListener('click', () => (config().ok ? flushSoon(0) : configure()));
    }
    if (!badge.isConnected) document.body.appendChild(badge);

    const queued = keysWith(QUEUE_PREFIX).length;
    const dead = keysWith(DEAD_PREFIX).length;
    let text;
    let color;
    if (!config().ok) {
      text = 'DC: tap to set up';
      color = '#c0392b';
    } else if (state.error === 'secret rejected') {
      text = 'DC: secret rejected';
      color = '#c0392b';
    } else if (queued) {
      text = `DC ${state.captured} · queued ${queued}${state.error ? ` (${state.error})` : ''}`;
      color = '#d68910';
    } else {
      text = `DC captured ✓ ${state.captured}`;
      color = '#1e8449';
    }
    if (dead) text += ` · ${dead} rejected`;
    badge.textContent = text;
    badge.style.background = color;
  }

  // ---------------------------------------------------------------- main

  installHooks(window, (entry) => enqueue('discovery', entry));

  GM_registerMenuCommand('Set backend URL and secret', configure);
  GM_registerMenuCommand('Upload queued captures now', () => flushSoon(0));
  GM_registerMenuCommand('Upload DOM snapshot of this screen', snapshotDom);

  window.addEventListener('online', () => flushSoon(0));
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') flushSoon(1000);
  });
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { render(); flushSoon(3000); });
  } else {
    render();
    flushSoon(3000);
  }
})();
