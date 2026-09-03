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

  // ─── SUDR: Sera Unified Dialect Recognition ─────────────────────────────────
  // Single source of truth for the canonical event vocabulary lives in
  // event_types.json (web-accessible resource). Loaded once, cached, with a
  // small built-in fallback so a fetch failure can't take crosshairs down.
  const SUDR_FALLBACK_EVENT_TYPES = {
    LOGIN_SUCCESS: { status: 'success' }, PORTAL_VIEW: { status: 'success' },
    FORM_VIEW: { status: 'pending' }, FILING_SUBMITTED: { status: 'pending' },
    FILING_VERIFIED: { status: 'success' }, RETURNS_LIST_VIEW: { status: 'success' },
    LOGOUT: { status: 'success' }, ERROR: { status: 'failed' }
  };
  let _sudrEventTypes = SUDR_FALLBACK_EVENT_TYPES;
  let _sudrLoaded = false;

  async function _loadSudrEventTypes() {
    try {
      const url = (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.getURL)
        ? chrome.runtime.getURL('event_types.json')
        : null;
      if (!url) return;
      const res = await fetch(url);
      const data = await res.json();
      if (data && data.event_types) {
        _sudrEventTypes = data.event_types;
        _sudrLoaded = true;
        console.log(`⚡ Sera SDC: SUDR vocabulary loaded (schema v${data.schema_version}, ${Object.keys(data.event_types).length} event types).`);
      }
    } catch (err) {
      console.warn('⚡ Sera SDC: SUDR event_types.json failed to load, using built-in fallback vocabulary.', err);
    }
  }
  _loadSudrEventTypes();

  /**
   * deriveStatus(eventType)
   * Status is NEVER chosen by a protocol — it's mechanically derived from
   * event.type via the SUDR registry, so no two protocols can disagree on
   * what "pending" vs "success" means for the same kind of event.
   */
  function deriveStatus(eventType) {
    const entry = _sudrEventTypes[eventType];
    return (entry && entry.status) || 'pending';
  }

  /**
   * normalizeIdentity(raw)
   * Validates PAN/GSTIN/TAN shape once, at the source, instead of letting
   * downstream Python guess whether a scraped string is trustworthy.
   */
  function normalizeIdentity(raw) {
    raw = raw || {};
    const pan   = /^[A-Z]{5}[0-9]{4}[A-Z]$/.test(raw.pan || '')   ? raw.pan   : null;
    const gstin = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$/.test(raw.gstin || '') ? raw.gstin : null;
    const tan   = /^[A-Z]{4}[0-9]{5}[A-Z]$/.test(raw.tan || '')   ? raw.tan   : null;
    const cin   = raw.cin || null; // CIN pattern not yet validated — MCA still stub

    let confidence = 'low';
    if (pan || gstin || tan) confidence = 'high';
    else if (raw.legal_name) confidence = 'medium';

    return { pan, gstin, tan, cin, legal_name: raw.legal_name || null, confidence };
  }


  // ─── Registered Protocol Registry ───────────────────────────────────────────
  // Each protocol registers itself via SDC.register(protocol).
  // A protocol is: { name, hostMatch, crosshairs: [{ pattern: RegExp, handler: fn }] }
  const _protocols = [];

  const SDC = window.__SERA_SDC__ = {
    version: SDC_VERSION,

    /**
     * SDC Session & Timeline Manager
     * Syncs memory to chrome.storage.local for cross-tab persistence with 15m TTL.
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
          status: 'active',
          start_time: new Date().toISOString(),
          end_time: null,
          timeline: [],
          assembler_captures: [],  // Buffered per-capture payloads (flushed atomically at session end)
          _assembler_flushed: false, // Guard: prevents double-flush of the same session
          _lastStartedPan: '',
          _ts: Date.now()
        };
      },

      _getStorageKey() {
        const host = (window.location.hostname || '').toLowerCase();
        if (host.includes('incometax') || host.includes('efiling')) return '__SDC_SESSION_ITR__';
        if (host.includes('gst.gov.in')) return '__SDC_SESSION_GST__';
        if (host.includes('tdscpc.gov.in') || host.includes('traces')) return '__SDC_SESSION_TRACES__';
        if (host.includes('mca.gov.in')) return '__SDC_SESSION_MCA__';
        return '__SDC_SESSION_DEFAULT__';
      },

      async load() {
        const storageKey = this._getStorageKey();
        return new Promise(resolve => {
          const onLoaded = (sess) => {
            const now = Date.now();
            if (!sess || !sess.session_id || (now - (sess._ts || 0) > 15 * 60 * 1000)) {
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
              chrome.storage.local.get([storageKey], (res) => {
                onLoaded(res ? res[storageKey] : null);
              });
            } else if (typeof localStorage !== 'undefined') {
              const raw = localStorage.getItem(storageKey);
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
        const storageKey = this._getStorageKey();
        return new Promise(resolve => {
          this.data._ts = Date.now();
          try {
            if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
              const payload = {};
              payload[storageKey] = this.data;
              chrome.storage.local.set(payload, resolve);
            } else if (typeof localStorage !== 'undefined') {
              localStorage.setItem(storageKey, JSON.stringify(this.data));
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

        // Dispatch finalized unified payload to Desktop App via Assembler
        this._flushAssembler(staleSession);
      },

      /**
       * Assembles the final, comprehensive session packet (all captures + full timeline)
       * and dispatches a single atomic transfer to the backend app.
       */
      async _flushAssembler(targetSession) {
        const sData = targetSession || this.data;
        if (!sData || !sData.session_id) return;
        if ((sData.timeline || []).length === 0) return; // Skip empty artifacts

        // ─── Double-Flush Guard ───────────────────────────────────
        // Prevents the same session from being flushed more than once,
        // regardless of how many code paths converge on session termination.
        if (sData._assembler_flushed) {
          console.log(`⚡ Sera SDC Assembler: ⚠️ Session [${sData.session_id}] already flushed — ignoring duplicate flush call.`);
          return;
        }
        sData._assembler_flushed = true;
        // MUST save the flushed state immediately to prevent race conditions or cross-tab double flushes
        await this.save();
        // ─────────────────────────────────────────────────────────

        console.log(`⚡ Sera SDC Assembler: 📦 Flushing atomic unified payload to backend for session [${sData.session_id}].`);

        // 1. Always emit the timeline sync (for sdc_session_timelines DB — pure audit trail)
        _emitDual({
          type: 'sdc_session_timeline',
          session_id: sData.session_id,
          pan: sData.pan || "",
          client_name: sData.name || "",
          portal: sData.portal || "income tax",
          status: sData.status || "active",
          start_time: sData.start_time,
          end_time: sData.end_time || null,
          total_steps: (sData.timeline || []).length,
          timeline: sData.timeline || [],
          timestamp: new Date().toISOString()
        });

        // 2. Only emit filing_result if there are actual captured payloads.
        //    Pure navigation sessions (no crosshair hits) must NOT create tracker_dump entries.
        const captures = sData.assembler_captures || [];
        if (captures.length === 0) {
          console.log(`⚡ Sera SDC Assembler: ℹ️ Session [${sData.session_id}] had no captures — skipping filing_result emission.`);
          return;
        }

        // Compute dominant parameters across any portal (ITR, GST, TRACES, MCA)
        const portal = sData.portal || (captures.length > 0 && captures[0].portal) || "Income Tax";
        const arn = captures.map(c => c.arn).find(a => a && a !== "N/A") || "N/A";
        const period = captures.map(c => c.period_label).find(p => p) || sData.ay || "";
        const filingType = captures.map(c => c.filing_type).find(f => f) || sData.form || "";
        const gstin = sData.gstin || captures.map(c => c.gstin).find(g => g) || "";
        const pan = sData.pan || (gstin && gstin.length >= 12 ? gstin.substring(2, 12) : "") || captures.map(c => c.pan).find(p => p) || "";
        const clientName = sData.name || captures.map(c => c.client_name || c.name || c.taxpayer_name).find(n => n) || "";
        const companyName = captures.map(c => c.company_name || c.trade_name).find(c => c) || "";
        const proprietorName = captures.map(c => c.proprietor_name || c.legal_name).find(p => p) || "";
        
        // CRITICAL: Strip the globally duplicated timeline from individual captures
        // to prevent exceeding the browser's strict 64KB limit for keepalive fetch requests
        const strippedCaptures = captures.map(c => {
          // GST calendar/form captures only need the dataset fields below.
          // Avoid repeating the complete timeline and nested raw capture in
          // every period while preserving the assembler's per-period model.
          if (/gst/i.test(c.portal || portal)) {
            return {
              gstin: c.gstin || '',
              pan: c.pan || '',
              client_name: c.client_name || c.name || c.taxpayer_name || '',
              company_name: c.company_name || '',
              proprietor_name: c.proprietor_name || '',
              filing_type: c.filing_type || '',
              filing_preference: c.filing_preference || c.scraped_data?.filing_preference || '',
              period_label: c.period_label || '',
              status: c.status || '',
              due_date: c.due_date || '',
              arn: c.arn || 'N/A',
              capture_method: c.capture_method || 'SDC_GST',
              capture_origin: c.capture_origin || 'form_view',
              submitted_in_session: Boolean(c.submitted_in_session),
              submission_arn: c.submission_arn || '',
              submission_timestamp: c.submission_timestamp || '',
              last_viewed_at: c.last_viewed_at || c.updated_at || c.timestamp || '',
              first_captured_at: c.first_captured_at || c.timestamp || '',
              updated_at: c.updated_at || c.timestamp || '',
              dataset_key: c.dataset_key || '',
              scraped_data: c.scraped_data || null
            };
          }
          const clone = { ...c };
          delete clone.session_timeline;
          if (clone.raw_payload && typeof clone.raw_payload === 'object') {
            clone.raw_payload = { ...clone.raw_payload };
            delete clone.raw_payload.session_timeline;
          }
          return clone;
        });

        // ─── Derivation of Tracker Dump Candidates (Blueprint Section 1 & 5) ───
        // 1. Every dataset with a confirmed submission during the session
        const submittedDatasets = strippedCaptures.filter(c => Boolean(c.submitted_in_session));

        // 2. The single last dataset viewed during the session (with greatest last_viewed_at or updated_at)
        let lastViewedDataset = null;
        if (strippedCaptures.length > 0) {
          const sortedByView = [...strippedCaptures].sort((a, b) => {
            const timeA = new Date(a.last_viewed_at || a.updated_at || a.first_captured_at || a.timestamp || 0).getTime();
            const timeB = new Date(b.last_viewed_at || b.updated_at || b.first_captured_at || b.timestamp || 0).getTime();
            return timeB - timeA;
          });
          lastViewedDataset = sortedByView[0];
        }

        // 3. tracker_dump_captures = submitted_datasets + last_viewed_dataset (deduplicated by dataset_key)
        const trackerDumpMap = new Map();
        for (const sub of submittedDatasets) {
          if (sub.dataset_key) trackerDumpMap.set(sub.dataset_key, sub);
        }
        if (lastViewedDataset && lastViewedDataset.dataset_key) {
          if (!trackerDumpMap.has(lastViewedDataset.dataset_key)) {
            trackerDumpMap.set(lastViewedDataset.dataset_key, lastViewedDataset);
          }
        }
        const trackerDumpCaptures = Array.from(trackerDumpMap.values());
        console.log(`⚡ Sera SDC Assembler: Derived ${trackerDumpCaptures.length} Tracker Dump candidate(s) (${submittedDatasets.length} submitted, ${lastViewedDataset ? 1 : 0} last-viewed) from ${strippedCaptures.length} total assembled datasets.`);
        
        const masterPayload = {
          type: "filing_result",
          session_id: sData.session_id,
          client_id: sData.client_id || null,
          client_name: clientName,
          taxpayer_name: clientName,
          company_name: companyName,
          proprietor_name: proprietorName,
          pan: pan,
          gstin: gstin,
          portal: portal,
          status: sData.status || "completed",
          arn: arn,
          capture_method: "SDC_Assembler",
          period_label: period,
          filing_type: filingType,
          timestamp: new Date().toISOString(),
          session_timeline: sData.timeline || [],
          raw_payload: {
            source: "Sera_SDC_Assembler",
            session_id: sData.session_id,
            detection_type: "SDC_Assembler_Unified",
            client_name: clientName,
            company_name: companyName,
            proprietor_name: proprietorName,
            pan: pan,
            gstin: gstin,
            portal: portal,
            status: sData.status || "completed",
            arn: arn,
            period_label: period,
            filing_type: filingType,
            tracker_dump_captures: trackerDumpCaptures, // Selective routing
            assembler_captures: strippedCaptures,       // Full set for LTT/timeline
            last_viewed_dataset_key: lastViewedDataset ? lastViewedDataset.dataset_key : '',
            session_timeline: sData.timeline || []
          }
        };

        // Compress the complete assembler payload before it enters the
        // durable outbox. Gzip is lossless and keeps the desktop contract
        // unchanged because the desktop listener restores this object before
        // handing it to the tracker-dump pipeline.
        const transportPayload = await _buildCompressedPayload(masterPayload);
        
        // Logout may unload the page immediately. Prefer the extension
        // background-worker hop for the final atomic payload so it reaches
        // the desktop host even if the page's HTTP keepalive is cancelled.
        await _queueReliableFlush(transportPayload || masterPayload);
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
                company_name: capture.company_name || capture.trade_name || "",
                proprietor_name: capture.proprietor_name || capture.legal_name || "",
                dob: capture.dob || this.data.dob || "",
                form: capture.filing_type || this.data.form || "",
                ay: capture.period_label || this.data.ay || "",
                arn: capture.arn || "N/A",
                status: capture.status || "Captured",
                due_date: capture.due_date || "",
                gstin: capture.gstin || ""
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
            company_name: capture.company_name || capture.trade_name || "",
            proprietor_name: capture.proprietor_name || capture.legal_name || "",
            dob: capture.dob || this.data.dob || "",
            form: capture.filing_type || this.data.form || "",
            ay: capture.period_label || this.data.ay || "",
            arn: capture.arn || "N/A",
            status: capture.status || "Captured",
            due_date: capture.due_date || "",
            gstin: capture.gstin || ""
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
        
        // Complete the atomic assembler flush before the GST/ITR protocol clears
        // the shared session state on logout.
        await this._flushAssembler(this.data);
        await this.save();
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
        // Obsolete: SDC Assembler now buffers and flushes atomically at session end.
      },

      async clear() {
        const storageKey = this._getStorageKey();
        return new Promise(resolve => {
          this._initCleanSession();
          try {
            if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
              chrome.storage.local.remove([storageKey], resolve);
            } else if (typeof localStorage !== 'undefined') {
              localStorage.removeItem(storageKey);
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
      // background.js may reinject the SDC bundle on both tab completion and
      // SPA URL updates. Do not register or scan the same protocol repeatedly.
      if (_protocols.some(existing => existing.name === protocol.name)) {
        console.log(`⚡ Sera SDC: Protocol "${protocol.name}" already registered — skipping duplicate.`);
        return false;
      }
      _protocols.push(protocol);
      console.log(`⚡ Sera SDC: Registered protocol "${protocol.name}" with ${protocol.crosshairs.length} crosshair(s).`);
      return true;
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
    async clearAllSessions(options = {}) {
      console.log('⚡ Sera SDC: 🔄 New login detected — clearing all protocol session caches.');
      _lastScannedUrl = ''; // force re-scan on the next route
      await this.session.clear();
      for (const fn of _sessionClearCallbacks) {
        try { fn(); } catch (_) {}
      }
      if (options.reason === 'login') {
        const now = Date.now();
        if (now - _lastSessionRestartToastAt > 1500) {
          _lastSessionRestartToastAt = now;
          if (window.SDCToast) {
            window.SDCToast.show({
              type: 'start',
              badge: 'NEW SESSION',
              title: 'New Session',
              message: 'Login screen detected — assembler restarted.',
              duration: 2200
            });
          }
        }
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

    /** Retry a previously queued final payload after extension/page startup. */
    async retryPendingFlush() {
      if (typeof chrome === 'undefined' || !chrome.storage || !chrome.storage.local || _pendingFlushInFlight) return;
      chrome.storage.local.get([PENDING_FLUSH_KEY], result => {
        const pending = result && result[PENDING_FLUSH_KEY];
        if (!pending || !pending.session_id) return;
        _pendingFlushInFlight = true;
        _sendReliableFlush(pending).finally(() => { _pendingFlushInFlight = false; });
      });
    },

    /** Public capture emitter for protocols */
    emitCapture(capture, protocolName, crosshairId) {
      _emitCapture(capture, protocolName, crosshairId);
    },

    /**
     * emit(protocolName, crosshairId, eventType, identity, fields)
     * SUDR enforcement point. This is the ONLY way a protocol may send a
     * capture in the canonical envelope shape — protocols hand over
     * ingredients (what happened, who it's about, extra scraped fields),
     * this function builds the envelope. A protocol can never construct or
     * dispatch a malformed/divergent payload because it never touches the
     * envelope itself.
     */
    emit(protocolName, crosshairId, eventType, identity, fields) {
      if (!_sudrEventTypes[eventType]) {
        console.warn(`⚡ Sera SDC: SUDR.emit() called with unknown event.type "${eventType}" — check event_types.json.`);
      }
      const envelope = {
        type: 'sudr_capture', // top-level routing key for extension_listener.py
        schema_version: '1.0',
        capture_id: (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : `sudr-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        captured_at: new Date().toISOString(),
        source: { protocol: protocolName, crosshair_id: crosshairId, extension_version: SDC_VERSION },
        session_id: (SDC.session && SDC.session.data && SDC.session.data.session_id) || '',
        event: { type: eventType, status: deriveStatus(eventType) },
        identity: normalizeIdentity(identity),
        fields: fields || {},
        evidence: { url: window.location.href, page_title: document.title }
      };

      console.log(`⚡ Sera SDC (SUDR) CAPTURE [${crosshairId} → ${eventType}]:`, JSON.stringify(envelope).substring(0, 300));
      _emitDual(envelope);
    }
  };

  // Session-clear callback registry (populated by protocols via SDC.onSessionClear)
  const _sessionClearCallbacks = [];

  // ─── Route Change Detection (SPA-safe with Loop Protection) ───────────────
  let _lastScannedUrl = '';
  let _lastSessionRestartToastAt = 0;
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
    
    // Fast path for hard termination boundaries (Logout / Login)
    const urlL = (url || '').toLowerCase();
    const isTermination = urlL.includes('logout') || urlL.includes('signout') || urlL.includes('sessionexpire') || urlL.includes('timeout') || urlL.includes('login');
    
    if (isTermination) {
      _dispatch(url, 0); // Execute instantly (0ms debounce)
    } else {
      _debounceTimer = setTimeout(() => _dispatch(url, 0), 200);
    }
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

  // 4. Instant Zero-Delay Logout Click Interceptor (Bypasses server API wait)
  document.addEventListener('click', (e) => {
    const el = e.target.closest('a, button, [role="button"], li');
    if (!el) return;
    const txt = (el.textContent || '').toLowerCase();
    const href = (el.getAttribute('href') || '').toLowerCase();
    const isLogoutClick = txt.includes('log out') || txt.includes('sign out') || href.includes('logout') || href.includes('signout');
    
    if (isLogoutClick && SDC.session.data.session_id) {
      console.log('⚡ Sera SDC: 🖱️ Instant Logout Click intercepted! Flushing immediately before navigation.');
      // Execute instantly while the browser waits for the server response
      SDC.session.finalizeLogout(window.location.href);
      // NOTE: We don't wipe storage here in case the server fails and they are still logged in, 
      // but the payload is already gone. The regular URL-based boundary will wipe it later.
    }
  }, true); // use capture phase to guarantee we intercept it

  // 5. pagehide / tab close instant flush fallback
  window.addEventListener('pagehide', () => {
    // If there is an active un-flushed session with captures when the tab is closed, flush it!
    if (SDC.session.data.session_id) {
      const hasCaptures = (SDC.session.data.assembler_captures || []).length > 0;
      if (!SDC.session.data._assembler_flushed && hasCaptures) {
        console.log('⚡ Sera SDC: 🚪 Tab closed! Instant flush triggered via keepalive.');
        SDC.session.data.status = 'completed';
        SDC.session.data.end_time = new Date().toISOString();
        const tl = SDC.session.data.timeline || [];
        tl.push({
            step: tl.length + 1,
            title: "Tab Closed / Navigated Away",
            url: window.location.href,
            route: "TAB_CLOSED",
            timestamp: SDC.session.data.end_time,
            is_termination: true
        });
        // Queue the final payload, then remove persisted session memory so a
        // later tab cannot resurrect the abruptly closed session.
        SDC.session._flushAssembler(SDC.session.data)
          .finally(() => SDC.session.clear())
          .catch(() => {});
      } else {
        // No filing payload exists, but any persisted identity/timeline state
        // must still be discarded when the tab terminates abruptly.
        SDC.session.clear().catch(() => {});
      }
    }
  });

  // ─── Human-Readable Route Title Formatter ──────────────────────────────────
  function _formatRouteTitle(url, crosshairId) {
    if (!url) return "Page Navigation";
    const lower = url.toLowerCase();

    // Prefer semantic crosshair IDs over URL fragments. GST routes commonly
    // contain "/auth/" even when the page is a return form, not a login page.
    if (crosshairId === 'gst_form_details') return "GST Return Form Details";
    if (crosshairId === 'gst_returns_dashboard') return "GST Returns Dashboard";
    if (crosshairId === 'gst_welcome_calendar') return "GST Returns Calendar";
    if (crosshairId === 'gst_iff_submission_success' ||
        crosshairId === 'gst_gstr1_submission_success' ||
        crosshairId === 'gst_gstr3b_submission_success' ||
        crosshairId === 'gst_filing_success') {
      return "GST Filing Submission Success";
    }
    if (crosshairId === 'gst_login_logout') {
      if (lower.includes('logout') || lower.includes('signout') || lower.includes('sign-out') ||
          lower.includes('sessionexpire') || lower.includes('session-expire') ||
          lower.includes('sessionexpired') || lower.includes('session-expired') || lower.includes('timeout')) {
        return "GST Session Ended";
      }
      return "GST Login Screen";
    }

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
    if (crosshairId === 'itr_login' || lower.includes('login') || lower.includes('sessionexpire') || lower.includes('session-expire') || lower.includes('sessionexpired') || lower.includes('session-expired')) {
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
        if (crosshair.enabled === false) continue;
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

        // ─── CLIENT CONTEXT SWITCH & SESSION BOUNDARY PROTECTION ───
        // 1. PAN Mismatch: If capture has a new PAN that differs from the active session PAN
        if (capture && capture.pan && SDC.session.data.pan && capture.pan !== SDC.session.data.pan) {
            console.log(`⚡ Sera SDC: 🔄 PAN Mismatch Detected (${SDC.session.data.pan} -> ${capture.pan})! Finalizing stale session.`);
            if (SDC.session.data.timeline && SDC.session.data.timeline.length > 0) {
                SDC.session.data.status = 'completed';
                SDC.session.data.end_time = new Date().toISOString();
                const tl = SDC.session.data.timeline;
                tl.push({
                    step: tl.length + 1,
                    title: "Client Context Switch",
                    url: url,
                    route: "SWITCH_EVENT",
                    timestamp: SDC.session.data.end_time,
                    is_termination: true,
                    note: `Session finalized automatically due to new PAN (${capture.pan}) detection.`
                });
                await SDC.session.save();
                SDC.session._flushAssembler();
            }
            await SDC.clearAllSessions();
            // Re-load the new clean session initialized by clearAllSessions
            await SDC.session.load();
        }

        // 2. Login / Logout Boundary Guard
        // Only fire for crosshairs explicitly designated as login/logout handlers.
        // Do NOT use url.includes('login') — it over-matches pages like 'pre-login', 'link-login', etc.
        const isLoginCrosshair = matchedCrosshair.id === 'itr_login' || matchedCrosshair.id === 'gst_login_logout';
        if (isLoginCrosshair) {
            const urlL = (url || '').toLowerCase();
            const isLogout = urlL.includes('logout') || urlL.includes('signout') || urlL.includes('sign-out') || 
                             urlL.includes('sessionexpire') || urlL.includes('session-expire') || 
                             urlL.includes('sessionexpired') || urlL.includes('session-expired') || urlL.includes('timeout');
            
            // Login is not a termination boundary. Staff may return to the
            // login screen to authenticate the next client; keep current SDC
            // memory intact. Termination is handled by logout, timeout,
            // session expiry, or abrupt tab close.
            if (!isLogout) {
              console.log('⚡ Sera SDC: Login screen detected — preserving current session memory.');
              return;
            }

            if (isLogout && !capture) {
                return; // Auth / logout route: session was finalized & wiped by handler — skip save
            }
            
            // If NOT a logout route, but we arrived at a login screen with an existing mature session
            if (!isLogout && (SDC.session.data.pan || (SDC.session.data.timeline && SDC.session.data.timeline.length > 0))) {
                if (!capture || (capture.pan !== SDC.session.data.pan)) {
                    console.log(`⚡ Sera SDC: 🔄 Login Route Detected with active session! Finalizing previous session.`);
                    SDC.session.data.status = 'completed';
                    SDC.session.data.end_time = new Date().toISOString();
                    const tl = SDC.session.data.timeline;
                    tl.push({
                        step: tl.length + 1,
                        title: "Returned to Login",
                        url: url,
                        route: "LOGIN_BOUNDARY",
                        timestamp: SDC.session.data.end_time,
                        is_termination: true,
                        note: "User navigated to Login screen. Session finalized."
                    });
                    await SDC.session.save();
                    SDC.session._flushAssembler();
                    await SDC.clearAllSessions();
                    await SDC.session.load();
                }
            }
        }
        // ──────────────────────────────────────────────────────────

        // Record timeline step with capture
        SDC.session.data.portal = matchedProtocol.name;
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
          await _emitCapture(capture, matchedProtocol.name, matchedCrosshair.id);
          return;
        } else if (retryCount < 2 && !isLoginCrosshair) {
          // Schedule up to 2 retries (at +700ms and +1400ms) for Angular rendering.
          // Skipped for Login/Logout boundaries as termination happens instantly.
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

  // ─── Canonical Dataset Key Generator ────────────────────────────────────────
  function computeDatasetKey(portal, identifier, formType, periodLabel) {
    const pStr = String(portal || '');
    let pCanon = 'PORTAL';
    if (/gst/i.test(pStr)) pCanon = 'GST';
    else if (/itr|income/i.test(pStr)) pCanon = 'ITR';
    else pCanon = pStr.replace(/[^A-Z0-9]/gi, '').toUpperCase() || 'PORTAL';

    const idCanon = String(identifier || '').replace(/[^A-Z0-9]/gi, '').toUpperCase() || 'UNKNOWN';

    let fStr = String(formType || '');
    if (!fStr && pStr.includes('(') && pStr.includes(')')) {
      fStr = pStr.split('(').pop().split(')')[0].trim();
    }
    let fCanon = 'FORM';
    if (/gstr[-_ ]*1a/i.test(fStr)) fCanon = 'GSTR1A';
    else if (/gstr[-_ ]*1|iff/i.test(fStr)) fCanon = 'GSTR1';
    else if (/gstr[-_ ]*2a/i.test(fStr)) fCanon = 'GSTR2A';
    else if (/gstr[-_ ]*2b/i.test(fStr)) fCanon = 'GSTR2B';
    else if (/gstr[-_ ]*3b/i.test(fStr)) fCanon = 'GSTR3B';
    else if (/cmp[-_ ]*08/i.test(fStr)) fCanon = 'CMP08';
    else if (/gstr[-_ ]*4/i.test(fStr)) fCanon = 'GSTR4';
    else if (/gstr[-_ ]*9c/i.test(fStr)) fCanon = 'GSTR9C';
    else if (/gstr[-_ ]*9/i.test(fStr)) fCanon = 'GSTR9';
    else if (/gstr[-_ ]*7/i.test(fStr)) fCanon = 'GSTR7';
    else if (/gstr[-_ ]*8/i.test(fStr)) fCanon = 'GSTR8';
    else {
      const mItr = fStr.match(/itr[-_ ]*([1-7])/i);
      if (mItr) fCanon = 'ITR' + mItr[1];
      else fCanon = fStr.replace(/[^A-Z0-9]/gi, '').toUpperCase() || 'FORM';
    }

    // Canonical period: clean trailing text, status lines, due dates, newlines
    const rawPerStr = String(periodLabel || '').trim();
    const perClean = rawPerStr.split(/[\r\n]|(?:\b(?:Due date|Option|Filed|Pending|NA)\b)/i)[0].trim();

    // Assessment Year canonical normalization: (e.g. "AY 2026-27", "2026-27", "AY: 2026-27") -> AY_2026_27
    const mAy = perClean.match(/\b(?:AY|A\.Y\.)?\s*(20\d{2})[-_](\d{2})\b/i);

    let perCanon = 'CURRENT';
    if (mAy) {
      perCanon = `AY_${mAy[1]}_${mAy[2]}`;
    } else {
      const mMon = perClean.match(/\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b/i);
      const mQtr = perClean.match(/\b(Apr[- ]*Jun|Jul[- ]*Sep|Oct[- ]*Dec|Jan[- ]*Mar|Q[1-4])\b/i);
      const mYr = rawPerStr.match(/\b(20\d{2})\b/);

      if (mQtr && mYr) {
        perCanon = mQtr[1].replace(/[^A-Z0-9]+/gi, '_').toUpperCase() + '_' + mYr[1];
      } else if (mMon && mYr) {
        perCanon = mMon[1].slice(0, 3).toUpperCase() + '_' + mYr[1];
      } else if (mMon) {
        perCanon = mMon[1].slice(0, 3).toUpperCase();
      } else {
        perCanon = perClean.replace(/[^A-Z0-9]+/gi, '_').replace(/^_+|_+$/g, '').toUpperCase() || 'CURRENT';
      }
    }

    return `${pCanon}:${idCanon}:${fCanon}:${perCanon}`;
  }

  // ─── Capture Emit ────────────────────────────────────────────────────────────
  // Formats full filing payload and sends directly to background native host pipeline
  async function _emitCapture(capture, protocolName, crosshairId) {
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
    const status = capture.status || "submitted";
    const timestamp = new Date().toISOString();

    const datasetKey = computeDatasetKey(
      portalName,
      capture.gstin || pan || "",
      filingType,
      period
    );

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
      due_date: capture.due_date || "",
      company_name: capture.company_name || capture.trade_name || "",
      proprietor_name: capture.proprietor_name || capture.legal_name || "",
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
      dataset_key: datasetKey,
      capture_origin: capture.capture_origin || (crosshairId.includes('success') ? 'submission_success' : crosshairId.includes('calendar') ? 'calendar_view' : 'form_view'),
      submitted_in_session: Boolean(capture.submitted_in_session || crosshairId.includes('success')),
      submission_arn: capture.submission_arn || (crosshairId.includes('success') && arn !== 'N/A' ? arn : ''),
      submission_timestamp: capture.submission_timestamp || (crosshairId.includes('success') ? timestamp : ''),
      last_viewed_at: timestamp,
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
        dataset_key: datasetKey,
        capture_origin: capture.capture_origin || (crosshairId.includes('success') ? 'submission_success' : crosshairId.includes('calendar') ? 'calendar_view' : 'form_view'),
        submitted_in_session: Boolean(capture.submitted_in_session || crosshairId.includes('success')),
        submission_arn: capture.submission_arn || (crosshairId.includes('success') && arn !== 'N/A' ? arn : ''),
        submission_timestamp: capture.submission_timestamp || (crosshairId.includes('success') ? timestamp : ''),
        last_viewed_at: timestamp,
        session_timeline: SDC.session.data.timeline || [],
        dom_breadcrumbs: capture.dom_breadcrumbs || SDC.utils.getBreadcrumbs(),
        confirmation_message: capture.confirmation_message || ""
      }
    };

    console.log(`⚡ Sera SDC Assembler: 📥 Buffered capture [${crosshairId}] in memory. Key: ${datasetKey}`);
    // ─── Assembler Buffering ───
    SDC.session.data.assembler_captures = SDC.session.data.assembler_captures || [];
    // GST can revisit the same form for a status update, or move to a new
    // filing period during one login session. Keep one dataset per
    // GSTIN + form + period: same-period revisits update status; new periods
    // remain separate datasets inside the atomic master payload.
    const isGstCapture = /gst/i.test(portalName) || Boolean(detail.gstin);
    if (isGstCapture || datasetKey) {
      const existingIndex = SDC.session.data.assembler_captures.findIndex(item => item.dataset_key === datasetKey);
      if (existingIndex >= 0) {
        const previous = SDC.session.data.assembler_captures[existingIndex];
        // Sticky submission flags: once submitted_in_session is true, it MUST NOT be downgraded by a calendar or view revisit!
        const wasSubmitted = Boolean(previous.submitted_in_session || detail.submitted_in_session);
        const retainedArn = (wasSubmitted && previous.submission_arn) ? previous.submission_arn : (detail.submission_arn || (detail.arn !== 'N/A' ? detail.arn : previous.arn || 'N/A'));
        const retainedTimestamp = (wasSubmitted && previous.submission_timestamp) ? previous.submission_timestamp : (detail.submission_timestamp || '');

        SDC.session.data.assembler_captures[existingIndex] = {
          ...previous,
          ...detail,
          submitted_in_session: wasSubmitted,
          submission_arn: retainedArn,
          submission_timestamp: retainedTimestamp,
          arn: (retainedArn && retainedArn !== 'N/A') ? retainedArn : (detail.arn && detail.arn !== 'N/A' ? detail.arn : previous.arn || 'N/A'),
          status: wasSubmitted ? 'Filed & Confirmed' : detail.status,
          first_captured_at: previous.first_captured_at || previous.timestamp,
          updated_at: timestamp,
          last_viewed_at: timestamp,
          capture_origin: detail.capture_origin || previous.capture_origin
        };
      } else {
        detail.first_captured_at = timestamp;
        detail.last_viewed_at = timestamp;
        SDC.session.data.assembler_captures.push(detail);
      }
    } else {
      detail.first_captured_at = timestamp;
      detail.last_viewed_at = timestamp;
      SDC.session.data.assembler_captures.push(detail);
    }
    
    // Transport routing (Blueprint Section 6):
    // Only confirmed submissions or explicit success events should immediately emit a live filing_result
    // Routine calendar/form views update the assembler buffer and session timeline, but wait for session finalization
    // so they do not flood tracker_dump with intermediate views.
    if (detail.submitted_in_session || detail.capture_origin === 'submission_success') {
      console.log(`⚡ Sera SDC: 🚀 Confirmed submission for ${datasetKey} — dispatching real-time to desktop tracker dump.`);
      _emitDual(detail);
    } else {
      console.log(`⚡ Sera SDC: 👁️ View capture buffered for ${datasetKey} (origin: ${detail.capture_origin}) — deferred to session finalization.`);
    }

    // Save the new buffer state to chrome.storage.local immediately so it survives page unloads/refreshes
    await SDC.session.save().catch(e => console.warn('⚡ Sera SDC: Failed to save capture buffer:', e));

    // ─── Trigger In-Browser Toast Notification ────────────────────────────────
    try {
      if (window.SDCToast && !capture.silent && !capture.skip_toast) {
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

  // ─── Durable final-payload outbox ───────────────────────────────────────
  // Keep the immutable assembler result until the desktop acknowledges it.
  // This survives logout navigation, page unloads, and MV3 worker restarts.
  const PENDING_FLUSH_KEY = '__SERA_SDC_PENDING_FLUSH__';
  let _pendingFlushInFlight = false;

  async function _buildCompressedPayload(payload) {
    if (typeof CompressionStream === 'undefined' || typeof TextEncoder === 'undefined') return null;
    try {
      const json = JSON.stringify(payload);
      const input = new TextEncoder().encode(json);
      const stream = new Blob([input]).stream().pipeThrough(new CompressionStream('gzip'));
      const compressed = new Uint8Array(await new Response(stream).arrayBuffer());
      let binary = '';
      const chunkSize = 0x8000;
      for (let i = 0; i < compressed.length; i += chunkSize) {
        binary += String.fromCharCode(...compressed.subarray(i, i + chunkSize));
      }
      return {
        type: 'filing_result_compressed',
        schema_version: '2.0',
        encoding: 'gzip+base64',
        original_type: 'filing_result',
        session_id: payload.session_id || '',
        original_size: input.byteLength,
        compressed_size: compressed.byteLength,
        payload: btoa(binary)
      };
    } catch (err) {
      console.warn('⚡ Sera SDC: Compression failed; using uncompressed payload.', err);
      return null;
    }
  }

  function _clearPendingFlush(sessionId) {
    return new Promise(resolve => {
      if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
        chrome.storage.local.get([PENDING_FLUSH_KEY], result => {
          const pending = result && result[PENDING_FLUSH_KEY];
          if (!pending || pending.session_id !== sessionId) { resolve(); return; }
          chrome.storage.local.remove([PENDING_FLUSH_KEY], resolve);
        });
      } else resolve();
    });
  }

  function _sendReliableFlush(payload) {
    return new Promise(resolve => {
      let settled = false;
      const finish = (ok) => {
        if (settled) return;
        settled = true;
        if (ok) _clearPendingFlush(payload.session_id).finally(() => resolve(true));
        else resolve(false);
      };

      // Use one background-worker hop for the final flush. The worker owns
      // delivery after the page begins unloading on logout.
      if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
        try {
          chrome.runtime.sendMessage(payload, response => {
            if (!chrome.runtime.lastError && response && response.status === 'accepted') finish(true);
            else finish(false);
          });
          setTimeout(() => finish(false), 2500);
          return;
        } catch (_) {}
      }
      finish(false);
    });
  }

  function _queueReliableFlush(payload) {
    return new Promise(resolve => {
      const dispatch = () => {
        if (_pendingFlushInFlight) { resolve(false); return; }
        _pendingFlushInFlight = true;
        _sendReliableFlush(payload).then(ok => {
          _pendingFlushInFlight = false;
          resolve(ok);
        });
      };
      if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
        chrome.storage.local.set({ [PENDING_FLUSH_KEY]: payload }, dispatch);
      } else dispatch();
    });
  }

  // ─── Dispatch Pipeline ──────────────────────────────────────────────────
  function _emitDual(payload, preferRuntime = false) {
    // 1. Dispatch events for page-level test harness & filing detector listeners
    try {
      window.dispatchEvent(new CustomEvent('SeraSUDRCapture', { detail: payload }));
      window.dispatchEvent(new CustomEvent('SeraSDCApiCapture', { detail: payload }));
      window.dispatchEvent(new CustomEvent('SeraSDCCapture', { detail: payload }));
      window.dispatchEvent(new CustomEvent('SeraFSTApiCapture', { detail: payload }));
    } catch (_) {}

    const _chromeRuntimeFallback = (p) => {
      try {
        if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
          chrome.runtime.sendMessage(p, () => {
            if (chrome.runtime && chrome.runtime.lastError) {
              console.warn('⚡ Sera SDC: Background worker fallback failed.', chrome.runtime.lastError);
              _directHttpDispatch(p);
            } else {
              console.log('⚡ Sera SDC: Successfully delivered payload via Chrome Runtime fallback.');
            }
          });
        }
      } catch (err) {
        console.warn('⚡ Sera SDC: sendMessage threw error during fallback.', err);
        _directHttpDispatch(p);
      }
    };

    const _directHttpDispatch = (p, callback) => {
      if (typeof fetch !== 'function') return;
      fetch('http://127.0.0.1:49152', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(p),
        mode: 'cors',
        credentials: 'omit',
        keepalive: true
      }).then(response => {
        if (!response.ok) {
          console.warn(`⚡ Sera SDC: Direct HTTP fallback failed with status ${response.status}.`);
          if (callback) callback(false);
        } else {
          console.log('⚡ Sera SDC: Successfully delivered payload via direct HTTP fallback.');
          if (callback) callback(true);
        }
      }).catch(err => {
        console.warn('⚡ Sera SDC: Direct HTTP fallback error.', err);
        if (callback) callback(false);
      });
    };

    if (preferRuntime && typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
      _chromeRuntimeFallback(payload);
      return;
    }

    // 2. Direct HTTP Dispatch (Primary) - Fast, stateless, reliable
    if (typeof fetch === 'function') {
      fetch('http://127.0.0.1:49152', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        mode: 'cors',
        credentials: 'omit',
        keepalive: true
      })
      .then(response => {
        if (!response.ok) {
          console.warn(`⚡ Sera SDC: Direct HTTP failed with status ${response.status}. Falling back to Service Worker.`);
          _chromeRuntimeFallback(payload);
        } else {
          console.log('⚡ Sera SDC: Successfully delivered payload via Direct HTTP.');
        }
      })
      .catch(err => {
        console.warn('⚡ Sera SDC: Direct HTTP fetch error. Desktop app might be closed or port blocked. Falling back to Service Worker.', err);
        _chromeRuntimeFallback(payload);
      });
    } else {
      _chromeRuntimeFallback(payload);
    }
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

    /** Extract Assessment Year from a URL, DOM, or page text */
    extractAY(url, pageText) {
      if (url) {
        const m = url.match(/(?:foreturns-ay(\d{2})|ay(\d{4}))/i);
        if (m) {
          if (m[1]) return `AY 20${m[1]}-${parseInt(m[1]) + 1}`;
          if (m[2]) return `AY ${m[2]}-${parseInt(m[2].slice(2)) + 1}`;
        }
      }
      try {
        if (typeof document !== 'undefined') {
          const ayEls = document.querySelectorAll(
            'mat-select[formcontrolname*="ay" i], mat-select[formcontrolname*="assessment" i], mat-select[id*="ay" i], mat-select[name*="ay" i],' +
            'select[name*="ay" i], select[id*="ay" i], [aria-label*="assessment year" i], .mat-select-value'
          );
          for (const el of ayEls) {
            const txt = (el.innerText || el.textContent || el.value || '').trim();
            const m = txt.match(/\b(20\d{2}[-_]\d{2})\b/);
            if (m) return `AY ${m[1].replace('_', '-')}`;
          }
        }
      } catch (_) {}

      const txt = pageText || (typeof document !== 'undefined' && document.body ? document.body.innerText : '');
      if (txt) {
        const m = txt.match(/(?:Assessment\s*Year|AY|A\.Y\.)\s*[:#-]?\s*(20\d{2}[-_]\d{2})/i) || txt.match(/\b(20\d{2}-\d{2})\b/);
        if (m) return `AY ${m[1].replace('_', '-')}`;
      }
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
