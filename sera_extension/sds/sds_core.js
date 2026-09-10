/**
 * sds_core.js — Sera Dataset Scanner (SDS)
 * =====================================================
 * Resilient schema-driven dataset capture engine for Project Sera.
 * Designed to survive portal UI redesigns by focusing on target datasets
 * rather than brittle CSS selectors.
 *
 * Core Architecture:
 * 1. Session Boundary: Harmonized with SDC's battle-tested session management
 *    (cross-tab persistence, 15m TTL, client context switch on PAN change).
 * 2. Password Page Exemption: Strictly NO scanning of password/login screens.
 *    Only link navigation is registered like a normal browser.
 * 3. Ephemeral Memory: Full page text is analyzed in local function scope
 *    and immediately discarded — zero raw text bloat in memory or payloads.
 * 4. Ultra-Compact Payload: Emits only normalized compliance keys via the SDC/SUDR
 *    assembler envelope architecture.
 * 5. Compressed Flowchart: Maintains a lean link navigation flowchart recording
 *    only clean route hops.
 * 6. Idempotent Gating: Once the required dataset is acquired on a registered page,
 *    the scanner sleeps until the next registered route transition.
 */

(function () {
  'use strict';

  // ─── SDS Engine Paused ──────────────────────────────────────────────────────
  return;

  // ─── 0. Guard: Prevent double injection ─────────────────────────────────────
  if (window.__SERA_SDS_ACTIVE__) return;
  window.__SERA_SDS_ACTIVE__ = true;

  const SDS_VERSION = '1.0.0';
  const SDS_DEBUG = false;

  if (SDS_DEBUG) console.log(`⚡ Sera SDS (Dataset Scanner v${SDS_VERSION}): initialized.`);

  // ─── 1. Compliance Regex Standards (Government Portals) ─────────────────────
  const COMPLIANCE_PATTERNS = {
    // 1. PAN: 5 uppercase letters, 4 digits, 1 letter. 4th char is entity type.
    pan: /\b([A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z])\b/,

    // 2. GSTIN: 2 digits + PAN + 1 char entity count + 'Z' + 1 checksum
    gstin: /\b(\d{2}[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b/,

    // 3. TAN: 4 letters, 5 digits, 1 letter
    tan: /\b([A-Z]{4}\d{5}[A-Z])\b/,

    // 4. Filing & Acknowledgment Numbers
    arn_gst: /\b(AA\d{13}|[A-Z]{2}\d{13})\b/,
    ack_itr: /\b(\d{15})\b/,

    // 5. Form Types (Closed vocabulary across ITR / GST / TDS)
    formType: /\b(ITR-[1-7]|ITR-U|ITR-V|FORM\s*(?:10IEA?|15CA|15CB|29B|35|36|67|26AS|AIS|TIS)|GSTR-?(?:1|2A|2B|3B|4|5|6|7|8|9|9C|CMP-?08|ITC-?01|ITC-?04|REG-?01|PMT-?06|DRC-?03)|(?:FORM\s*)?(?:24Q|26Q|27Q|27EQ|16A?))\b/i,

    // 6. Filing Period & Assessment / Financial Years
    ay: /\b(?:AY|A\.Y\.|Assessment\s*Year)[\s:-]*((?:20\d{2}[-–/]\d{2,4})|20\d{2})\b/i,
    fy: /\b(?:FY|F\.Y\.|Financial\s*Year)[\s:-]*((?:20\d{2}[-–/]\d{2,4})|20\d{2})\b/i,
    monthPeriod: /\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember))[\s,-]+(20\d{2})\b/i,
    quarterPeriod: /\b(Q[1-4])[\s,-]+(?:FY[\s-]*)?(20\d{2}(?:-\d{2})?)\b/i,

    // 7. Filing & Submission Status
    status: /\b(FILED|SUBMITTED|VERIFIED|E-VERIFIED|PENDING|PROCESSED|PROCESSING|REJECTED|SUCCESS|FAILED|IN[- ]PROGRESS|DRAFT)\b/i,

    // 8. Semantic Label Proximity for Names
    nameLabel: /(?:Trade\s*Name|Legal\s*Name(?:\s*of\s*Business)?|Taxpayer(?:\s*Name)?|Client\s*Name|Assessee\s*Name|Dealer\s*Name|Deductor\s*Name)\s*[:\-]\s*([^\n\r<|]{3,60})/i
  };

  // UI Stopwords to prevent button labels or headers from being identified as names
  const UI_STOPWORDS = new Set([
    'DASHBOARD', 'LOGOUT', 'SUBMIT', 'INCOME TAX', 'GST PORTAL', 'GOVERNMENT OF INDIA',
    'E-FILING', 'DOWNLOAD', 'CLICK HERE', 'HOME', 'MY ACCOUNT', 'SERVICES', 'HELP',
    'FEEDBACK', 'CONTACT US', 'FILE NOW', 'PROCEED', 'CANCEL', 'BACK', 'CONTINUE'
  ]);

  // Trade name entity keywords
  const TRADE_ENTITY_REGEX = /\b(PVT|LTD|LIMITED|LLP|ENTERPRISES|ENTERPRISE|TRADERS|TRADING|ASSOCIATES|INDUSTRIES|SOLUTIONS|SERVICES|CORP|COMPANY|CO|BROTHERS|AGENCY|AGENCIES|AGRO|LOGISTICS)\b/i;

  // ─── 2. Password Page Strict Exemption ──────────────────────────────────────
  /**
   * isPasswordOrAuthPage()
   * Identifies login screens, password fields, or session termination pages.
   * On these pages, NO DOM scanning is allowed. Only route tracking occurs.
   */
  function isPasswordOrAuthPage() {
    const urlL = (window.location.href || '').toLowerCase();
    const isAuthUrl = urlL.includes('/login') || urlL.includes('/signin') ||
                      urlL.includes('/auth') || urlL.includes('/sessionexpire') ||
                      urlL.includes('/logout') || urlL.includes('/password') ||
                      urlL.includes('session-expired') || urlL.includes('timeout');

    if (isAuthUrl) return true;

    // Check for password inputs
    try {
      const hasPassInput = document.querySelector('input[type="password"], input[name*="pass" i], input[id*="pass" i], #passwordInput, #user_pass');
      if (hasPassInput) return true;
    } catch (_) {}

    return false;
  }

  // ─── 3. Compressed Navigation Flowchart Manager ─────────────────────────────
  /**
   * Cleans a URL into a compact route slug (strips query parameters, hashes tokens)
   */
  function cleanRouteSlug(rawUrl) {
    if (!rawUrl) return '/';
    try {
      const u = new URL(rawUrl);
      const hashPart = u.hash ? u.hash.split('?')[0] : '';
      const pathPart = u.pathname;
      const combined = (pathPart + (hashPart ? '#' + hashPart : '')).replace(/\/+/g, '/');
      return combined.length > 55 ? combined.substring(0, 52) + '...' : combined;
    } catch (_) {
      return (rawUrl.split('?')[0] || '/').slice(-40);
    }
  }

  // ─── 4. SDS Scanner Engine ──────────────────────────────────────────────────
  class SDSScanner {
    constructor() {
      this.capturedRoutes = new Set();
      this.knownClients = [];
      this.navFlow = []; // Compressed route navigation flowchart
      this.isScanning = false;
      this.activeUrl = '';
      this._pollTimer = null;
      this._pollCount = 0;
      this.lastCapturedData = null;

      this._init();
    }

    async _init() {
      await this._loadKnownClients();
      this._bindRouteListeners();
      this.onRouteChanged(window.location.href);
    }

    async _loadKnownClients() {
      return new Promise(resolve => {
        try {
          if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
            chrome.storage.local.get(['knownClients', 'clientCache'], (data) => {
              const clients = data.knownClients || data.clientCache || [];
              if (Array.isArray(clients)) {
                this.knownClients = clients.map(c => (typeof c === 'string' ? c : (c.trade_name || c.name || '')).toUpperCase().trim()).filter(Boolean);

                // Register known client trade names into Compromise NLP engine
                if (window.nlp && typeof window.nlp.plugin === 'function') {
                  const words = {};
                  this.knownClients.forEach(name => {
                    if (name.length >= 3) {
                      words[name.toLowerCase()] = ['Organization', 'TradeName'];
                    }
                  });
                  try {
                    window.nlp.plugin({ words });
                    if (SDS_DEBUG) console.log(`⚡ Sera SDS: Registered ${Object.keys(words).length} known trade name(s) into Compromise.`);
                  } catch (_) {}
                }
              }
              resolve();
            });
          } else {
            resolve();
          }
        } catch (_) {
          resolve();
        }
      });
    }

    _bindRouteListeners() {
      // 1. URL change polling (for SPA transitions)
      let prevUrl = window.location.href;
      setInterval(() => {
        const curUrl = window.location.href;
        if (curUrl !== prevUrl) {
          prevUrl = curUrl;
          this.onRouteChanged(curUrl);
        }
      }, 350);

      // 2. Hash & popstate
      window.addEventListener('hashchange', () => this.onRouteChanged(window.location.href));
      window.addEventListener('popstate', () => this.onRouteChanged(window.location.href));

      // 3. pushState / replaceState hooks
      ['pushState', 'replaceState'].forEach(method => {
        try {
          const original = history[method];
          history[method] = (...args) => {
            const res = original.apply(history, args);
            setTimeout(() => this.onRouteChanged(window.location.href), 50);
            return res;
          };
        } catch (_) {}
      });
    }

    /**
     * onRouteChanged(url)
     * Handles route transitions. Respects password page exclusion and manages
     * navigation flowchart compression.
     */
    onRouteChanged(url) {
      this.activeUrl = url;
      const slug = cleanRouteSlug(url);

      // Update compressed navigation flowchart (keep last 12 hops max)
      if (this.navFlow.length === 0 || this.navFlow[this.navFlow.length - 1] !== slug) {
        this.navFlow.push(slug);
        if (this.navFlow.length > 12) this.navFlow.shift();
      }

      // Record step in shared SDC session if active
      if (window.__SERA_SDC__ && window.__SERA_SDC__.session) {
        window.__SERA_SDC__.session.load().then(() => {
          window.__SERA_SDC__.session.recordStep(url);
        }).catch(() => {});
      }

      // ─── STRICT GUARD: Password & Login Screens ───
      if (isPasswordOrAuthPage()) {
        if (SDS_DEBUG) console.log(`⚡ Sera SDS: Password/Auth page detected (${slug}). DOM scanning strictly bypassed.`);
        this._stopPolling();
        return;
      }

      // If already captured for this exact route, remain idle (idempotent sleep)
      if (this.capturedRoutes.has(url)) {
        return;
      }

      // Start bounded micro-poll for async SPA data load
      this._startMicroPoll();
    }

    _startMicroPoll() {
      this._stopPolling();
      this._pollCount = 0;

      this._pollTimer = setInterval(() => {
        this._pollCount++;

        // Stop after 12 attempts (~3.6s) if required dataset did not appear
        if (this._pollCount > 12) {
          this._stopPolling();
          return;
        }

        const acquired = this._executeScan();
        if (acquired) {
          this.capturedRoutes.add(this.activeUrl);
          this._stopPolling(); // Dataset acquired: halt scanning immediately!
        }
      }, 300);
    }

    _stopPolling() {
      if (this._pollTimer) {
        clearInterval(this._pollTimer);
        this._pollTimer = null;
      }
    }

    /**
     * _executeScan()
     * Ephemeral execution: grabs document text, extracts compliance dataset,
     * immediately discards raw text, and emits compact payload.
     */
    _executeScan() {
      if (!document.body) return false;

      // 1. Ephemeral read — strictly local variable scope
      let pageText = document.body.innerText || '';
      if (!pageText || pageText.length < 20) {
        pageText = null;
        return false;
      }

      // 2. Extract Government Identifiers
      let pan = this._matchFirst(pageText, COMPLIANCE_PATTERNS.pan);
      const gstin = this._matchFirst(pageText, COMPLIANCE_PATTERNS.gstin);
      const tan = this._matchFirst(pageText, COMPLIANCE_PATTERNS.tan);

      // If GSTIN found, automatically extract embedded PAN
      if (gstin && !pan && gstin.length >= 12) {
        pan = gstin.substring(2, 12);
      }

      // If no valid identifier exists on page yet, wait for next poll
      if (!pan && !gstin && !tan) {
        pageText = null;
        return false;
      }

      // 3. Extract Form Type, Period, Status, ARN
      const formType = this._matchFirst(pageText, COMPLIANCE_PATTERNS.formType);
      const period = this._matchFirst(pageText, COMPLIANCE_PATTERNS.ay) ||
                     this._matchFirst(pageText, COMPLIANCE_PATTERNS.fy) ||
                     this._matchFirst(pageText, COMPLIANCE_PATTERNS.monthPeriod) ||
                     this._matchFirst(pageText, COMPLIANCE_PATTERNS.quarterPeriod);
      const status = this._matchFirst(pageText, COMPLIANCE_PATTERNS.status) || 'PROCESSED';
      const arn = this._matchFirst(pageText, COMPLIANCE_PATTERNS.arn_gst) ||
                  this._matchFirst(pageText, COMPLIANCE_PATTERNS.ack_itr) || 'N/A';

      // 4. Extract Names (Compromise NLP + Semantic Proximity + PAN 4th Character Pivot)
      let clientName = '';
      let tradeName = '';

      const candidateName = this._matchFirst(pageText, COMPLIANCE_PATTERNS.nameLabel);

      if (candidateName && this._isValidNameString(candidateName)) {
        // If Compromise NLP is available, perform grammatical & entity extraction
        if (typeof window.nlp === 'function') {
          try {
            const titleCased = candidateName.replace(/\w\S*/g, (w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase());
            const doc = window.nlp(titleCased);

            const detectedOrgs = doc.organizations().out('array');
            const detectedPeople = doc.people().out('array');

            if (detectedOrgs.length > 0) {
              tradeName = candidateName;
            } else if (detectedPeople.length > 0) {
              clientName = candidateName;
            } else if (pan && pan[3] === 'P') {
              clientName = candidateName;
            } else {
              tradeName = candidateName;
            }
          } catch (_) {
            if (pan && pan[3] === 'P') clientName = candidateName; else tradeName = candidateName;
          }
        } else {
          if (pan && pan[3] === 'P') {
            clientName = candidateName;
          } else {
            tradeName = candidateName;
          }
        }
      }

      // Check known client trade names (e.g. "Dream Girl")
      const pageUpper = pageText.toUpperCase();
      for (const known of this.knownClients) {
        if (known.length >= 3 && pageUpper.includes(known)) {
          tradeName = known;
          break;
        }
      }

      // Check for business suffix
      if (!tradeName && candidateName && TRADE_ENTITY_REGEX.test(candidateName)) {
        tradeName = candidateName;
      }

      // 5. Explicitly dereference ephemeral text for immediate garbage collection
      pageText = null;

      // 6. Assemble compact dataset
      const dataset = {
        pan: pan || '',
        gstin: gstin || '',
        tan: tan || '',
        client_name: clientName || '',
        trade_name: tradeName || '',
        form_type: formType || '',
        period: period || '',
        status: status.toUpperCase(),
        arn: arn,
        nav_flow: [...this.navFlow] // Compressed link navigation flowchart
      };

      // Ensure minimum dataset threshold
      if (!dataset.pan && !dataset.gstin) return false;

      this.lastCapturedData = dataset;
      this._emitDataset(dataset);
      return true;
    }

    _matchFirst(text, regex) {
      if (!text) return null;
      const m = text.match(regex);
      return (m && m[1]) ? m[1].trim().replace(/\s{2,}/g, ' ') : null;
    }

    _isValidNameString(str) {
      if (!str || str.length < 3 || str.length > 70) return false;
      const upper = str.toUpperCase().trim();
      if (UI_STOPWORDS.has(upper)) return false;
      if (!/[A-Za-z]/.test(str) || /^\d+$/.test(str)) return false;
      return true;
    }

    /**
     * _emitDataset(data)
     * Emits ultra-compact JSON payload using the SDC Assembler / SUDR envelope.
     */
    _emitDataset(data) {
      if (SDS_DEBUG) console.log(`⚡ Sera SDS: 🎯 Dataset acquired:`, data);

      const portal = this._detectPortal();
      const eventType = data.status.includes('FILED') || data.status.includes('VERIFIED')
        ? 'FILING_VERIFIED'
        : (data.status.includes('SUBMIT') ? 'FILING_SUBMITTED' : 'FORM_VIEW');

      // 1. If SDC is loaded, emit via SDC canonical assembler emitter
      if (window.__SERA_SDC__ && typeof window.__SERA_SDC__.emit === 'function') {
        const identity = {
          pan: data.pan,
          gstin: data.gstin,
          tan: data.tan,
          legal_name: data.client_name || data.trade_name
        };

        const fields = {
          trade_name: data.trade_name,
          form_type: data.form_type,
          period_label: data.period,
          status: data.status,
          arn: data.arn,
          capture_method: 'SDS_Scanner',
          nav_flow: data.nav_flow
        };

        window.__SERA_SDC__.emit(portal, 'sds_resilient_capture', eventType, identity, fields);

        // Also record in SDC session memory
        if (window.__SERA_SDC__.session) {
          window.__SERA_SDC__.session.data.pan = data.pan || window.__SERA_SDC__.session.data.pan;
          if (data.client_name) window.__SERA_SDC__.session.data.name = data.client_name;
          if (data.trade_name) window.__SERA_SDC__.session.data.company_name = data.trade_name;
          window.__SERA_SDC__.session.save();
        }
      } else {
        // Standalone emission fallback (canonical SUDR envelope shape)
        const envelope = {
          type: 'sudr_capture',
          schema_version: '1.0',
          capture_id: (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : `sds-${Date.now()}`,
          captured_at: new Date().toISOString(),
          source: { protocol: portal, crosshair_id: 'sds_resilient_capture', extension_version: SDS_VERSION },
          session_id: `SDS-${Date.now().toString(36)}`,
          event: { type: eventType, status: data.status.toLowerCase() },
          identity: {
            pan: data.pan,
            gstin: data.gstin,
            tan: data.tan,
            legal_name: data.client_name || data.trade_name,
            confidence: (data.pan || data.gstin) ? 'high' : 'medium'
          },
          fields: {
            trade_name: data.trade_name,
            form_type: data.form_type,
            period_label: data.period,
            status: data.status,
            arn: data.arn,
            capture_method: 'SDS_Scanner',
            nav_flow: data.nav_flow
          },
          evidence: { url: window.location.href, page_title: document.title }
        };

        window.dispatchEvent(new CustomEvent('SeraSDCCapture', { detail: envelope }));
        window.dispatchEvent(new CustomEvent('__se_dc', { detail: envelope }));
      }

      // Show in-browser toast if available
      if (window.SDCToast) {
        window.SDCToast.show({
          type: 'capture',
          badge: 'SDS CAPTURED',
          title: data.form_type || 'Compliance Dataset',
          message: `${data.status} • ${data.period || 'Active Period'}`,
          chips: [
            { label: 'Entity', value: data.trade_name || data.client_name || 'Client' },
            { label: 'PAN', value: data.pan || '', isPan: true }
          ],
          duration: 1800
        });
      }
    }

    _detectPortal() {
      const host = (window.location.hostname || '').toLowerCase();
      if (host.includes('incometax') || host.includes('efiling')) return 'Income Tax';
      if (host.includes('gst.gov.in')) return 'GST Portal';
      if (host.includes('tdscpc') || host.includes('traces')) return 'TRACES Portal';
      if (host.includes('mca.gov.in')) return 'MCA Portal';
      return 'Compliance Portal';
    }
  }

  // ─── 5. Initialize SDS ──────────────────────────────────────────────────────
  window.__SERA_SDS__ = new SDSScanner();
})();
