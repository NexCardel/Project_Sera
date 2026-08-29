/**
 * sdc_core.js — Sera DOM Crosshair (SDC) Core Router
 * =====================================================
 * Ultra-lightweight route-gated DOM scanning engine.
 * Replaces the broad MutationObserver approach of tracker.js with a
 * zero-idle crosshair pattern: SDC wakes ONLY when the current URL/hash
 * matches a registered protocol crosshair, delegates to that protocol's
 * scan function, then sleeps again.
 *
 * Architecture:
 *   sdc_core.js        ← This file: route listener + protocol dispatcher
 *   protocols/
 *     itr_protocol.js  ← Income Tax Portal (ITR) rules
 *     gst_protocol.js  ← GST Portal rules (stub)
 *     traces_protocol.js ← TRACES rules (stub)
 *     mca_protocol.js  ← MCA rules (stub)
 *
 * Dispatch Event:
 *   Protocols fire window.dispatchEvent(new CustomEvent('SeraSDCCapture', { detail: {...} }))
 *   filing_detector.js (already loaded) listens on 'SeraFSTApiCapture'.
 *   SDC fires on 'SeraSDCCapture', which sdc_core.js re-dispatches as 'SeraFSTApiCapture'
 *   so the existing pipeline (filing_detector → background.js → native host) works unchanged.
 */

(function () {
  'use strict';

  // ─── Guard: prevent double injection ────────────────────────────────────────
  if (window.__SERA_SDC_ACTIVE__) return;
  window.__SERA_SDC_ACTIVE__ = true;

  const SDC_VERSION = '1.0.0';
  console.log(`⚡ Sera SDC (DOM Crosshair v${SDC_VERSION}): core loaded.`);

  // ─── Registered Protocol Registry ───────────────────────────────────────────
  // Each protocol registers itself via SDC.register(protocol).
  // A protocol is: { name, hostMatch, crosshairs: [{ pattern: RegExp, handler: fn }] }
  const _protocols = [];

  const SDC = window.__SERA_SDC__ = {
    version: SDC_VERSION,

    /**
     * SDC Session Manager
     * Syncs memory to chrome.storage.local for cross-tab persistence with 30m TTL.
     */
    session: {
      data: {},
      async load() {
        return new Promise(resolve => {
          try {
            chrome.storage.local.get(['__SDC_SESSION__'], (res) => {
              const sess = res.__SDC_SESSION__ || { _ts: 0 };
              // 30 minute TTL expiry
              if (Date.now() - sess._ts > 30 * 60 * 1000) {
                this.data = {};
              } else {
                this.data = sess;
              }
              resolve();
            });
          } catch (e) {
            resolve();
          }
        });
      },
      async save() {
        return new Promise(resolve => {
          this.data._ts = Date.now();
          try {
            chrome.storage.local.set({ __SDC_SESSION__: this.data }, resolve);
          } catch (e) {
            resolve();
          }
        });
      },
      async clear() {
        return new Promise(resolve => {
          this.data = {};
          try {
            chrome.storage.local.remove(['__SDC_SESSION__'], resolve);
          } catch (e) {
            resolve();
          }
        });
      }
    },

    /**
     * Emits a session_start ping to the desktop app
     */
    emitSessionStart(portalName, pan, name) {
      if (!pan || !name) return;
      console.log(`⚡ Sera SDC: 🟢 Session Start Ping [${portalName}] - ${pan} - ${name}`);
      const payload = {
        type: 'session_start',
        portal: portalName,
        pan: pan,
        client_name: name,
        timestamp: new Date().toISOString()
      };
      // Send via both pipelines (HTTP + runtime)
      _emitDual(payload);
    },

    /**
     * register(protocol)
     * protocol = {
     *   name:       string           (e.g. "ITR Portal")
     *   hostMatch:  RegExp           (e.g. /incometax\.gov\.in/)
     *   crosshairs: Array<{
     *     id:       string           (unique crosshair ID, e.g. "itr_personal_info")
     *     pattern:  RegExp           (matches full URL or hash route)
     *     handler:  fn(url, ctx)     (scan function, returns Promise<SDCCapture|null>)
     *   }>
     * }
     */
    register(protocol) {
      _protocols.push(protocol);
      console.log(`⚡ Sera SDC: Registered protocol "${protocol.name}" with ${protocol.crosshairs.length} crosshair(s).`);
    },

    /**
     * onSessionClear(fn)
     * Protocols call this to register a cleanup handler.
     * All handlers are called when SDC detects a login/logout route.
     */
    onSessionClear(fn) {
      if (typeof fn === 'function') _sessionClearCallbacks.push(fn);
    },

    /**
     * clearAllSessions()
     * Wipes all protocol session caches and resets the last-scanned URL so
     * the next page gets a fresh scan. Called automatically on login/logout.
     */
    async clearAllSessions() {
      console.log('⚡ Sera SDC: 🔄 New login detected — clearing all protocol session caches.');
      _lastScannedUrl = ''; // force re-scan on the next route
      await this.session.clear();
      for (const fn of _sessionClearCallbacks) {
        try { fn(); } catch (_) {}
      }
    },

    /** Manually trigger a scan for the current URL (used for testing or forced re-scans). */
    scanNow() {
      _onUrlChange(window.location.href);
    }
  };

  // Session-clear callback registry (populated by protocols via SDC.onSessionClear)
  const _sessionClearCallbacks = [];

  // ─── Route Change Detection (SPA-safe with Loop Protection) ───────────────
  let _lastScannedUrl = '';
  let _lastObservedUrl = window.location.href;
  let _debounceTimer = null;
  let _pendingRetryTimers = [];

  function _clearPendingRetries() {
    if (_debounceTimer) {
      clearTimeout(_debounceTimer);
      _debounceTimer = null;
    }
    for (const t of _pendingRetryTimers) {
      clearTimeout(t);
    }
    _pendingRetryTimers = [];
  }

  function _onUrlChange(url) {
    if (url === _lastScannedUrl) return;
    _clearPendingRetries();
    _lastScannedUrl = url; // Lock URL immediately to prevent duplicate runs
    _debounceTimer = setTimeout(() => _dispatch(url, 0), 200);
  }

  // 1. Lightweight 300ms URL change poller (catches Angular router transitions)
  setInterval(() => {
    const currentHref = window.location.href;
    if (currentHref !== _lastObservedUrl) {
      _lastObservedUrl = currentHref;
      _onUrlChange(currentHref);
    }
  }, 300);

  // 2. Intercept pushState / replaceState for SPA navigation
  ['pushState', 'replaceState'].forEach(method => {
    try {
      const original = history[method];
      history[method] = function (...args) {
        const result = original.apply(this, args);
        setTimeout(() => {
          const currentHref = window.location.href;
          if (currentHref !== _lastObservedUrl) {
            _lastObservedUrl = currentHref;
            _onUrlChange(currentHref);
          }
        }, 0);
        return result;
      };
    } catch (_) {}
  });

  // 3. hashchange & popstate listeners
  window.addEventListener('hashchange', () => {
    _lastObservedUrl = window.location.href;
    _onUrlChange(window.location.href);
  });
  window.addEventListener('popstate', () => {
    _lastObservedUrl = window.location.href;
    _onUrlChange(window.location.href);
  });

  // ─── Dispatch: Match URL → Protocol → Crosshair → Handler ──────────────────
  async function _dispatch(url, retryCount = 0) {
    // Abort if the user has navigated away while a retry was pending
    if (window.location.href !== url) return;

    const host = (window.location.hostname || '').toLowerCase();

    for (const protocol of _protocols) {
      if (!protocol.hostMatch.test(host)) continue;

      for (const crosshair of protocol.crosshairs) {
        if (!crosshair.pattern.test(url)) continue;

        console.log(`⚡ Sera SDC: Crosshair matched → [${protocol.name}] "${crosshair.id}" (attempt #${retryCount + 1})`);

        try {
          // 1. Load shared cross-tab session data
          await SDC.session.load();

          // 2. Execute protocol handler synchronously against loaded memory
          const capture = await crosshair.handler(url);

          if (crosshair.id === 'itr_login') {
            // Auth route: session was already wiped by handler — skip save to avoid recreating stale session
            return;
          }

          // 3. Save any memory changes back to shared storage
          await SDC.session.save();

          // 4. Session Start Trigger: fires whenever a valid PAN is identified for a new/switched client
          const activePan = SDC.session.data.pan;
          const activeName = SDC.session.data.name;
          if (activePan && activePan !== SDC.session.data._lastStartedPan) {
            SDC.session.data._lastStartedPan = activePan;
            await SDC.session.save();
            SDC.emitSessionStart(protocol.name, activePan, activeName || 'Taxpayer');
          }

          if (capture) {
            _clearPendingRetries();
            _emitCapture(capture, protocol.name, crosshair.id);
            return;
          } else if (crosshair.id !== 'itr_login' && retryCount < 2) {
            // Schedule up to 2 retries (at +700ms and +1400ms) for Angular rendering
            const delay = (retryCount + 1) * 700;
            const timer = setTimeout(() => {
              if (window.location.href === url) {
                _dispatch(url, retryCount + 1);
              }
            }, delay);
            _pendingRetryTimers.push(timer);
          }
        } catch (err) {
          console.warn(`⚡ Sera SDC: Handler error in crosshair "${crosshair.id}":`, err);
        }

        // First matching crosshair handled
        break;
      }
    }
  }

  // ─── Capture Emit ────────────────────────────────────────────────────────────
  // Formats full filing payload and sends directly to background native host pipeline
  function _emitCapture(capture, protocolName, crosshairId) {
    const captureMethod = `SDC_${crosshairId}`;
    const portalName = capture.portal || protocolName || "income tax";
    const clientName = capture.client_name || capture.name || capture.taxpayer_name || "";
    const pan = capture.pan || "";
    const arn = capture.arn || "N/A";
    const period = capture.period_label || "";
    const filingType = capture.filing_type || "ITR";
    const status = capture.status || "Submitted";
    const timestamp = new Date().toISOString();

    const detail = {
      type: "filing_result",
      client_id: capture.client_id || null,
      client_name: clientName,
      name: clientName,
      taxpayer_name: clientName,
      portal: portalName,
      arn: arn,
      capture_method: captureMethod,
      period_label: period,
      filing_type: filingType,
      status: status,
      pan: pan,
      gstin: capture.gstin || "",
      url: window.location.href,
      page_key: window.location.href.split('?')[0].replace(/\/+$/, '').toLowerCase(),
      is_page_update: true,
      site_link_history: capture.site_link_history || "",
      dom_breadcrumbs: capture.dom_breadcrumbs || SDC.utils.getBreadcrumbs(),
      confirmation_message: capture.confirmation_message || "",
      scraped_data: capture.scraped_data || null,
      raw_payload: {
        source: "Sera_SDC",
        detection_type: captureMethod,
        client_name: clientName,
        name: clientName,
        taxpayer_name: clientName,
        portal: portalName,
        arn: arn,
        pan: pan,
        gstin: capture.gstin || "",
        period: period,
        filing_type: filingType,
        status: status,
        url: window.location.href,
        timestamp: timestamp,
        dom_breadcrumbs: capture.dom_breadcrumbs || SDC.utils.getBreadcrumbs(),
        confirmation_message: capture.confirmation_message || ""
      }
    };

    console.log(`⚡ Sera SDC CAPTURE [${crosshairId}]:`, JSON.stringify(detail).substring(0, 300));
    _emitDual(detail);
  }

  // ─── Dual Dispatch Pipeline ──────────────────────────────────────────────────
  function _emitDual(payload) {
    // 1. Chrome Extension Runtime Dispatch (via Service Worker -> Native Host)
    try {
      if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
        chrome.runtime.sendMessage(payload, () => {
          if (chrome.runtime && chrome.runtime.lastError) {
            // Ignored - background worker might be handling it
          }
        });
      }
    } catch (err) {
      // Ignored
    }

    // 2. Direct Local IPC Dispatch (connects directly to Project Sera Desktop App on port 49152)
    try {
      if (typeof fetch === 'function') {
        fetch('http://127.0.0.1:49152', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
          mode: 'cors',
          credentials: 'omit'
        }).catch(() => {});
      }
    } catch (err) {}

    // 3. Dispatch event for page-level test harness
    try {
      window.dispatchEvent(new CustomEvent('SeraSDCApiCapture', { detail: payload }));
    } catch (_) {}
  }

  // ─── Shared Helper Utilities (available to all protocols) ───────────────────
  SDC.utils = {
    /**
     * PAN Regex: exactly 5 uppercase letters, 4 digits, 1 uppercase letter
     * e.g. ABCDE1234F
     */
    PAN_REGEX: /\b([A-Z]{5}[0-9]{4}[A-Z])\b/g,

    /**
     * 15-digit ITD Acknowledgement Number
     */
    ACK15_REGEX: /\b(\d{15})\b/g,

    /**
     * GST ARN Pattern
     */
    GST_ARN_REGEX: /\b((?:AA|AD|AN|AP|AR|AS|BR|CG|DD|DL|DN|GA|GJ|HP|HR|JH|JK|KA|KL|LA|LD|MH|ML|MN|MP|MZ|NL|OD|PB|PY|RJ|SK|TG|TN|TR|TS|UK|UP|UT|WB)\d{12}[A-Z])\b/,

    /** Get clean visible text from an element */
    getText(el) {
      if (!el) return '';
      return (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    },

    /** Query element safely */
    $q(selector) {
      try { return document.querySelector(selector); } catch (_) { return null; }
    },

    /** Query all elements safely */
    $qa(selector) {
      try { return Array.from(document.querySelectorAll(selector)); } catch (_) { return []; }
    },

    /** Extract first PAN from a string */
    extractPan(str) {
      const m = str.toUpperCase().match(/\b([A-Z]{5}[0-9]{4}[A-Z])\b/);
      return m ? m[1] : '';
    },

    /** Extract first 15-digit ITD ack from a string */
    extractAck15(str) {
      const m = str.match(/\b(\d{15})\b/);
      // Reject if it's clearly part of a phone/account (heuristic: no label context needed here, caller decides)
      return m ? m[1] : '';
    },

    /** Validate PAN strictly */
    isValidPan(str) {
      if (!str || str.length !== 10) return false;
      if (/^[A-Z]{5}[0-9]{4}[A-Z]$/.test(str.toUpperCase())) {
        // 4th char of PAN indicates entity type - basic sanity
        const fourth = str[3].toUpperCase();
        return 'PCHABGJLFTE'.includes(fourth);
      }
      return false;
    },

    /** Get current URL hash route last segment */
    getRouteKey(url) {
      try {
        const u = new URL(url);
        const hash = u.hash || '';
        const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean);
        return parts[parts.length - 1] || '';
      } catch (_) { return ''; }
    },

    /** Extract Assessment Year from a URL like foreturns-ay26 or fo-itr4-ay2026 */
    extractAY(url) {
      const m = url.match(/(?:foreturns-ay(\d{2})|ay(\d{4}))/i);
      if (!m) return '';
      if (m[1]) return `AY 20${m[1]}-${parseInt(m[1]) + 1}`;
      if (m[2]) return `AY ${m[2]}-${parseInt(m[2].slice(2)) + 1}`;
      return '';
    },

    /** Extract ITR Form type from URL or page text */
    extractItrForm(url, pageText) {
      // URL first: fo-itr4-ay2026, fo-itr-shared, fo-itr1, etc.
      const urlMatch = url.match(/fo-itr([1-7][a-z]?)/i);
      if (urlMatch) return `ITR-${urlMatch[1].toUpperCase()}`;
      // Page text
      if (pageText) {
        const txtMatch = pageText.match(/\b(ITR-[1-7][A-Z]?)\b/i);
        if (txtMatch) return txtMatch[1].toUpperCase();
      }
      return '';
    },

    /** Get full page text safely */
    getPageText() {
      return document.body ? (document.body.innerText || document.body.textContent || '') : '';
    },

    /** Breadcrumbs */
    getBreadcrumbs() {
      try {
        const el = document.querySelector(
          'nav[aria-label*="breadcrumb" i], .breadcrumb, .breadcrumbs, app-breadcrumb, [class*="breadcrumb" i]'
        );
        if (el) return (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
        const m = (document.body ? document.body.innerText : '').match(/Dashboard\s*>\s*[A-Za-z0-9\s.>–\/-]+/i);
        if (m) return m[0].trim();
      } catch (_) {}
      return '';
    }
  };

  // ─── Trigger initial scan on load ───────────────────────────────────────────
  // Protocols may not be registered yet, so defer to next tick to let them load first.
  setTimeout(() => _onUrlChange(window.location.href), 0);

})();
