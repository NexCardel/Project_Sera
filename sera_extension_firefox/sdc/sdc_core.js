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
     * SDC Session & Timeline Manager
     * Syncs memory to chrome.storage.local for cross-tab persistence with 30m TTL.
     * Records an exclusive, unbroken timeline of every visited page on compliance portals.
     */
    session: {
      data: {},

      _generateSessionId() {
        return 'SDC-SESS-' + Date.now().toString(36) + '-' + Math.random().toString(36).substring(2, 7).toUpperCase();
      },

      _initCleanSession(portal = 'income tax') {
        this.data = {
          session_id: this._generateSessionId(),
          pan: '',
          name: '',
          client_temp_name: '',
          dob: '',
          form: '',
          ay: '',
          portal: portal,
          status: 'active', // 'active' | 'completed' | 'terminated_abruptly'
          start_time: new Date().toISOString(),
          end_time: null,
          timeline: [],
          _lastStartedPan: '',
          _ts: Date.now()
        };
      },

      async load() {
        return new Promise(resolve => {
          const onLoaded = (sess) => {
            const now = Date.now();
            if (!sess || !sess.session_id || (now - (sess._ts || 0) > 30 * 60 * 1000)) {
              if (sess && sess.session_id && sess.status === 'active') {
                this._retrospectivelyFinalizeAbrupt(sess);
              }
              this._initCleanSession();
            } else {
              this.data = sess;
              if (!Array.isArray(this.data.timeline)) this.data.timeline = [];
            }
            resolve();
          };

          try {
            if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
              chrome.storage.local.get(['__SDC_SESSION__'], (res) => {
                onLoaded(res ? res.__SDC_SESSION__ : null);
              });
            } else if (typeof localStorage !== 'undefined') {
              const raw = localStorage.getItem('__SDC_SESSION__');
              onLoaded(raw ? JSON.parse(raw) : (this.data && this.data.session_id ? this.data : null));
            } else {
              if (!this.data || !this.data.session_id) this._initCleanSession();
              resolve();
            }
          } catch (e) {
            if (!this.data || !this.data.session_id) this._initCleanSession();
            resolve();
          }
        });
      },

      async save() {
        return new Promise(resolve => {
          this.data._ts = Date.now();
          try {
            if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
              chrome.storage.local.set({ __SDC_SESSION__: this.data }, resolve);
            } else if (typeof localStorage !== 'undefined') {
              localStorage.setItem('__SDC_SESSION__', JSON.stringify(this.data));
              resolve();
            } else {
              resolve();
            }
          } catch (e) {
            resolve();
          }
        });
      },

      // Retrospectively records "Tab terminated abruptly" on an abandoned session
      _retrospectivelyFinalizeAbrupt(staleSession) {
        if (!staleSession || staleSession.status !== 'active') return;
        console.log(`⚡ Sera SDC: ⚠️ Previous session [${staleSession.session_id}] was not logged out. Retrospectively marking "Tab terminated abruptly".`);
        
        staleSession.status = 'terminated_abruptly';
        staleSession.end_time = new Date().toISOString();
        const tl = staleSession.timeline || [];
        const lastUrl = tl.length > 0 ? tl[tl.length - 1].url : '';
        
        tl.push({
          step: tl.length + 1,
          title: "Tab terminated abruptly",
          url: lastUrl,
          route: "TERMINATION_EVENT",
          timestamp: staleSession.end_time,
          is_termination: true,
          note: "Session abandoned, browser closed, or timeout occurred without clicking Log Out."
        });

        // Dispatch finalized timeline to Desktop App
        _emitDual({
          type: 'sdc_session_timeline',
          session_id: staleSession.session_id,
          pan: staleSession.pan || "",
          client_name: staleSession.name || "",
          portal: staleSession.portal || "income tax",
          status: 'terminated_abruptly',
          start_time: staleSession.start_time,
          end_time: staleSession.end_time,
          total_steps: tl.length,
          timeline: tl,
          timestamp: staleSession.end_time
        });
      },

      /**
       * Records a navigation step in the timeline regardless of crosshair matches
       */
      async recordStep(url, crosshairId = null, capture = null) {
        if (!this.data.timeline) this.data.timeline = [];
        const tl = this.data.timeline;

        // Skip recording exact same URL consecutively within 1 second
        if (tl.length > 0) {
          const last = tl[tl.length - 1];
          if (last.url === url && last.crosshair_id === crosshairId) {
            // Update capture if newly resolved
            if (capture && !last.captured_data) {
              last.captured_data = {
                pan: capture.pan || this.data.pan || "",
                client_name: capture.client_name || capture.name || this.data.name || capture.client_temp_name || this.data.client_temp_name || "",
                client_temp_name: capture.client_temp_name || this.data.client_temp_name || "",
                dob: capture.dob || this.data.dob || "",
                form: capture.filing_type || this.data.form || "",
                ay: capture.period_label || this.data.ay || "",
                arn: capture.arn || "N/A",
                status: capture.status || "Captured"
              };
              await this.save();
              this._emitTimelineSync();
            }
            return;
          }
        }

        const stepNumber = tl.length + 1;
        const title = _formatRouteTitle(url, crosshairId);
        const route = (url.split('#')[1] || url.split('?')[0] || '').split('?')[0];

        const node = {
          step: stepNumber,
          title: title,
          url: url,
          route: route,
          timestamp: new Date().toISOString(),
          crosshair_id: crosshairId || null,
          is_crosshair: Boolean(crosshairId),
          captured_data: capture ? {
            pan: capture.pan || this.data.pan || "",
            client_name: capture.client_name || capture.name || this.data.name || capture.client_temp_name || this.data.client_temp_name || "",
            client_temp_name: capture.client_temp_name || this.data.client_temp_name || "",
            dob: capture.dob || this.data.dob || "",
            form: capture.filing_type || this.data.form || "",
            ay: capture.period_label || this.data.ay || "",
            arn: capture.arn || "N/A",
            status: capture.status || "Captured"
          } : null
        };

        tl.push(node);
        await this.save();
        this._emitTimelineSync();
      },

      /**
       * Finalizes session on clean logout
       */
      async finalizeLogout(url) {
        if (!this.data.session_id) return;
        const tl = this.data.timeline || [];
        const logoutTime = new Date().toISOString();

        tl.push({
          step: tl.length + 1,
          title: "Log Out",
          url: url,
          route: "#/logout",
          timestamp: logoutTime,
          is_logout: true
        });

        this.data.status = 'completed';
        this.data.end_time = logoutTime;
        await this.save();

        this._emitTimelineSync();
        console.log(`⚡ Sera SDC: 🏁 Session [${this.data.session_id}] cleanly completed with ${tl.length} step(s).`);

        if (window.SDCToast) {
          window.SDCToast.show({
            type: 'logout',
            badge: 'LOGOUT',
            title: 'Session Finalized',
            message: `Clean logout (${tl.length} steps).`,
            chips: [
              { label: 'Client', value: this.data.name || 'Taxpayer' },
              { label: 'PAN', value: this.data.pan || '', isPan: true }
            ],
            duration: 1100
          });
        }
      },

      _emitTimelineSync() {
        if (!this.data.session_id) return;
        _emitDual({
          type: 'sdc_session_timeline',
          session_id: this.data.session_id,
          pan: this.data.pan || "",
          client_name: this.data.name || "",
          portal: this.data.portal || "income tax",
          status: this.data.status || "active",
          start_time: this.data.start_time,
          end_time: this.data.end_time || null,
          total_steps: (this.data.timeline || []).length,
          timeline: this.data.timeline || [],
          timestamp: new Date().toISOString()
        });
      },

      async clear() {
        return new Promise(resolve => {
          this._initCleanSession();
          try {
            if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
              chrome.storage.local.remove(['__SDC_SESSION__'], resolve);
            } else if (typeof localStorage !== 'undefined') {
              localStorage.removeItem('__SDC_SESSION__');
              resolve();
            } else {
              resolve();
            }
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
      if (!pan) return;
      console.log(`⚡ Sera SDC: 🟢 Session Start Ping [${portalName}] - ${pan} - ${name || 'Client'}`);
      const payload = {
        type: 'session_start',
        session_id: this.session.data.session_id,
        portal: portalName,
        pan: pan,
        client_name: name || "Taxpayer",
        timestamp: new Date().toISOString()
      };
      // Send via both pipelines (HTTP + runtime)
      _emitDual(payload);

      // Show in-browser emerald toast
      if (window.SDCToast) {
        window.SDCToast.show({
          type: 'start',
          badge: 'SDC ACTIVE',
          title: `Live Session Started`,
          message: `Active on ${portalName.toUpperCase()} for ${name || 'Taxpayer'}.`,
          chips: [
            { label: 'Client', value: name || 'Taxpayer' },
            { label: 'PAN', value: pan, isPan: true }
          ],
          duration: 1100
        });
      }
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
    async scanNow(force = true) {
      if (force) {
        _clearPendingRetries();
        _lastScannedUrl = window.location.href;
        return await _dispatch(window.location.href, 0);
      }
      _onUrlChange(window.location.href);
    },

    /** Public capture emitter for protocols */
    emitCapture(capture, protocolName, crosshairId) {
      _emitCapture(capture, protocolName, crosshairId);
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

  // ─── Human-Readable Route Title Formatter ──────────────────────────────────
  function _formatRouteTitle(url, crosshairId) {
    if (!url) return "Page Navigation";
    const lower = url.toLowerCase();

    if (crosshairId === 'itr_filed_verified' || lower.includes('e-verify-now-success') || lower.includes('return-success')) {
      return "Filing Successful & e-Verified";
    }
    if (crosshairId === 'itr_submitted_pending' || lower.includes('e-verify-later') || lower.includes('complete-verification')) {
      return "Return Submitted (Pending e-Verification)";
    }
    if (crosshairId === 'itr_personal_info' || lower.includes('personal_information') || lower.includes('parta_gen')) {
      if (lower.includes('myprofile') || lower.includes('profiledetail') || lower.includes('profile')) {
        return "Profile & Personal Details";
      }
      return "Personal Information";
    }
    if (crosshairId === 'itr_form_select' || lower.includes('select-itr-form') || lower.includes('lets-get-started')) {
      return "Select ITR Form";
    }
    if (crosshairId === 'itr_landing' || lower.includes('fileincometaxreturn') || lower.includes('file-income-tax-return')) {
      return "Landing Page / e-File ITR";
    }
    if (crosshairId === 'itr_dashboard' || lower.includes('dashboard') || lower.includes('home')) {
      return "Dashboard";
    }
    if (crosshairId === 'itr_login' || lower.includes('login') || lower.includes('auth') || lower.includes('sessionexpire') || lower.includes('session-expire') || lower.includes('sessionexpired') || lower.includes('session-expired')) {
      if (lower.includes('sessionexpire') || lower.includes('session-expire') || lower.includes('sessionexpired') || lower.includes('session-expired') || lower.includes('timeout')) return "Session Expired";
      return lower.includes('logout') ? "Log Out" : "Login Screen";
    }
    if (lower.includes('myprofile') || lower.includes('profiledetail') || lower.includes('profile')) return "Profile & Personal Details";
    if (lower.includes('fileincometaxreturn') || lower.includes('file-income-tax-return')) return "Landing Page / e-File ITR";
    if (lower.includes('download')) return "Download Form / Receipt";
    if (lower.includes('challan') || lower.includes('etaxpayment')) return "e-Pay Tax / Challan";
    if (lower.includes('26as') || lower.includes('ais')) return "View AIS / Form 26AS";
    if (lower.includes('view-returns') || lower.includes('view-filed-returns')) return "View Filed Returns";

    // Fallback: extract last hash segment nicely formatted
    try {
      const hashPart = url.split('#')[1] || url.split('?')[0];
      const segments = hashPart.split('/').filter(Boolean);
      if (segments.length > 0) {
        const last = segments[segments.length - 1].replace(/^fo-/, '').replace(/[-_]/g, ' ');
        return last.charAt(0).toUpperCase() + last.slice(1);
      }
    } catch (_) {}
    return "Page Navigation";
  }

  // ─── Dispatch: Match URL → Protocol → Crosshair → Handler ──────────────────
  async function _dispatch(url, retryCount = 0) {
    // Abort if the user has navigated away while a retry was pending
    if (window.location.href !== url) return;

    const host = (window.location.hostname || '').toLowerCase();

    // 1. Load shared cross-tab session data
    await SDC.session.load();

    let matchedProtocol = null;
    let matchedCrosshair = null;

    for (const protocol of _protocols) {
      if (!protocol.hostMatch.test(host)) continue;
      matchedProtocol = protocol;

      for (const crosshair of protocol.crosshairs) {
        if (!crosshair.pattern.test(url)) continue;
        matchedCrosshair = crosshair;
        break;
      }
      if (matchedCrosshair) break;
    }

    if (matchedCrosshair && matchedProtocol) {
      console.log(`⚡ Sera SDC: Crosshair matched → [${matchedProtocol.name}] "${matchedCrosshair.id}" (attempt #${retryCount + 1})`);

      try {
        // Execute protocol handler
        const capture = await matchedCrosshair.handler(url);

        // Handle itr_login explicitly so we don't abort on PAN captures
        if (matchedCrosshair.id === 'itr_login' && !capture) {
            const urlL = (url || '').toLowerCase();
            const isLogout = urlL.includes('logout') || urlL.includes('signout') || urlL.includes('sign-out') || 
                             urlL.includes('sessionexpire') || urlL.includes('session-expire') || 
                             urlL.includes('sessionexpired') || urlL.includes('session-expired') || urlL.includes('timeout');
            if (isLogout) {
                return; // Auth / logout route: session was finalized & wiped by handler — skip save
            }
        }

        // Record timeline step with capture
        await SDC.session.recordStep(url, matchedCrosshair.id, capture);

        // Save memory changes back to shared storage
        await SDC.session.save();

        // Session Start Trigger: fires whenever a valid PAN is identified for a new/switched client
        const activePan = SDC.session.data.pan;
        const activeName = SDC.session.data.name;
        if (activePan && activePan !== SDC.session.data._lastStartedPan) {
          SDC.session.data._lastStartedPan = activePan;
          await SDC.session.save();
          SDC.emitSessionStart(matchedProtocol.name, activePan, activeName || 'Taxpayer');
        }

        if (capture) {
          _clearPendingRetries();
          _emitCapture(capture, matchedProtocol.name, matchedCrosshair.id);
          return;
        } else if (retryCount < 2) {
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
        console.warn(`⚡ Sera SDC: Handler error in crosshair "${matchedCrosshair.id}":`, err);
      }
    } else if (matchedProtocol && retryCount === 0) {
      // Non-crosshair navigation on compliance portal: record timeline step anyway
      try {
        await SDC.session.recordStep(url, null, null);
      } catch (_) {}
    }
  }

  // ─── Capture Emit ────────────────────────────────────────────────────────────
  // Formats full filing payload and sends directly to background native host pipeline
  function _emitCapture(capture, protocolName, crosshairId) {
    const captureMethod = `SDC_${crosshairId}`;
    const portalName = capture.portal || protocolName || "income tax";
    const clientTempName = capture.client_temp_name || SDC.session.data.client_temp_name || "";
    // Priority: capture.client_name > capture.name > session.data.name > capture.taxpayer_name > clientTempName
    // session.data.name is only preferred if it came from a profile/personal-info scan (not a header badge copy).
    // The capture payload's client_name is the most freshly-extracted value for this crosshair.
    const captureClientName = capture.client_name || capture.name || capture.taxpayer_name || "";
    const sessionName = SDC.session.data.name || "";
    // If session name is identical to the header badge, it was a landing-page fallback — prefer captureClientName.
    const sessionNameIsHeaderFallback = sessionName && sessionName === SDC.session.data.client_temp_name;
    const clientName = captureClientName || (!sessionNameIsHeaderFallback ? sessionName : "") || clientTempName || "";
    const pan = capture.pan || SDC.session.data.pan || "";
    const dob = capture.dob || SDC.session.data.dob || "";
    const arn = capture.arn || "N/A";
    const period = capture.period_label || SDC.session.data.ay || "";
    const filingType = capture.filing_type || SDC.session.data.form || "ITR";
    const status = capture.status || "Submitted";
    const timestamp = new Date().toISOString();

    const detail = {
      type: "filing_result",
      session_id: SDC.session.data.session_id || "",
      client_id: capture.client_id || null,
      client_name: clientName,
      client_temp_name: clientTempName,
      name: clientName,
      taxpayer_name: clientName,
      dob: dob,
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
      site_link_history: (SDC.session.data.timeline || []).map(t => t.url).join('\n'),
      session_timeline: SDC.session.data.timeline || [],
      dom_breadcrumbs: capture.dom_breadcrumbs || SDC.utils.getBreadcrumbs(),
      confirmation_message: capture.confirmation_message || "",
      scraped_data: capture.scraped_data || null,
      raw_payload: {
        source: "Sera_SDC",
        session_id: SDC.session.data.session_id || "",
        detection_type: captureMethod,
        client_name: clientName,
        client_temp_name: clientTempName,
        name: clientName,
        taxpayer_name: clientName,
        dob: dob,
        portal: portalName,
        arn: arn,
        pan: pan,
        gstin: capture.gstin || "",
        period: period,
        filing_type: filingType,
        status: status,
        url: window.location.href,
        timestamp: timestamp,
        session_timeline: SDC.session.data.timeline || [],
        dom_breadcrumbs: capture.dom_breadcrumbs || SDC.utils.getBreadcrumbs(),
        confirmation_message: capture.confirmation_message || ""
      }
    };

    console.log(`⚡ Sera SDC CAPTURE [${crosshairId}]:`, JSON.stringify(detail).substring(0, 300));
    _emitDual(detail);

    // ─── Trigger In-Browser Toast Notification ────────────────────────────────
    try {
      if (window.SDCToast) {
        const currentUrlKey = window.location.href.split('?')[0].replace(/\/+$/, '').toLowerCase();
        const historyNodes = SDC.session.data.timeline || [];
        const matchingPriorVisits = historyNodes.filter(node => (node.url || '').split('?')[0].replace(/\/+$/, '').toLowerCase() === currentUrlKey);
        const isRevisitUpdate = matchingPriorVisits.length > 1;

        if (isRevisitUpdate) {
          // Sapphire Blue Toast for in-place updates from previously visited page
          window.SDCToast.show({
            type: 'update',
            badge: 'UPDATED',
            title: `Refreshed: ${filingType || 'Form'}`,
            message: `Updated parameters for ${clientName || 'Taxpayer'}.`,
            chips: [
              { label: 'Client', value: clientName },
              { label: 'PAN', value: pan, isPan: true },
              { label: 'Form', value: filingType },
              { label: 'AY', value: period },
              { label: 'Ack', value: (arn && arn !== 'N/A') ? arn : '', isAck: true }
            ],
            duration: 1100
          });
        } else {
          // Glowing Green / Gold Toast for fresh crosshair captures
          const isAck = arn && arn !== 'N/A';
          window.SDCToast.show({
            type: 'capture',
            badge: isAck ? 'ACKNOWLEDGED' : 'CAPTURED',
            title: `${filingType || 'Filing Data'} Captured`,
            message: isAck ? `Ack captured on portal.` : `Recorded filing parameters.`,
            chips: [
              { label: 'Client', value: clientName },
              { label: 'PAN', value: pan, isPan: true },
              { label: 'Form', value: filingType },
              { label: 'AY', value: period },
              { label: 'Ack', value: isAck ? arn : '', isAck: true }
            ],
            duration: 1100
          });
        }
      }
    } catch (e) {
      console.warn('⚡ Sera SDC Toast Notice:', e);
    }
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

    // 3. Dispatch events for page-level test harness & filing detector listeners
    try {
      window.dispatchEvent(new CustomEvent('SeraSDCApiCapture', { detail: payload }));
      window.dispatchEvent(new CustomEvent('SeraSDCCapture', { detail: payload }));
      window.dispatchEvent(new CustomEvent('SeraFSTApiCapture', { detail: payload }));
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

    /**
     * Extract Date of Birth (DOB) from text or DOM element value
     * Matches DD/MM/YYYY, DD-MM-YYYY, YYYY-MM-DD, DD-Mon-YYYY (e.g. 16-Jun-1985)
     */
    extractDob(str) {
      if (!str) return '';
      const m = str.match(/\b(\d{2}[\/\-\.]\d{2}[\/\-\.]\d{4}|\d{4}[\/\-\.]\d{2}[\/\-\.]\d{2}|\d{2}[\/\-\.](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\/\-\.]\d{4})\b/i);
      return m ? m[1].replace(/\./g, '/') : '';
    },

    /** Validate PAN strictly */
    isValidPan(str) {
      if (!str || str.length !== 10) return false;
      if (/^[A-Z]{5}[0-9]{4}[A-Z]$/.test(str.toUpperCase())) {
        // 4th char of PAN indicates entity type - basic sanity
        const fourth = str[3].toUpperCase();
        return 'PCHABGJLFTED'.includes(fourth);
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
