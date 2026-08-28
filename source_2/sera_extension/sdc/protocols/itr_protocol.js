/**
 * itr_protocol.js — SDC Income Tax Return (ITR) Portal Protocol
 * ==============================================================
 * Registers crosshairs for the Income Tax e-Filing portal
 * (eportal.incometax.gov.in / incometaxindiaefiling.gov.in).
 *
 * Crosshair Map (from SDC blueprint + site history analysis):
 * ┌──────────────────────────────────────────────────────────────────────────┐
 * │  ID                    │ Trigger Route Pattern                           │
 * ├──────────────────────────────────────────────────────────────────────────┤
 * │  itr_personal_info     │ …/personal_information  (PAN + Name extraction) │
 * │  itr_form_select       │ …/fo-select-itr-form    (Form type + AY)        │
 * │  itr_filed_verified    │ …/fo-e-verify-now-success OR fo-return-success  │
 * │  itr_submitted_pending │ …/fo-e-verify-later     (Pending e-verification)│
 * └──────────────────────────────────────────────────────────────────────────┘
 *
 * Name Extraction Strategy (3-Tier):
 *   T1: DOM form inputs (FirstName, MiddleName, SurName/LastName)
 *   T2: Header profile badge (#loginUsername, .user-name, button[id*=loginUsername])
 *   T3: Full page text regex composite (First + optional Middle + Last)
 *
 * Non-ITR EVC Disambiguation:
 *   Only classify as ITR filing if breadcrumb/route confirms
 *   "Income Tax Return > Submit Level Validation" hierarchy
 *   OR route contains fo-itr-shared / foreturns-ay pattern.
 */

(function () {
  'use strict';

  // Wait for SDC core to be ready
  function _register() {
    const SDC = window.__SERA_SDC__;
    if (!SDC) {
      console.warn('Sera SDC ITR Protocol: sdc_core not loaded. Retrying...');
      setTimeout(_register, 100);
      return;
    }

    const u = SDC.utils;

    // ─── Session Identity Cache (per page-load) ──────────────────────────────
    // Persists PAN + Name discovered on personal_info so later crosshairs can use them.
    const session = window.__SDC_ITR_SESSION__ = window.__SDC_ITR_SESSION__ || {
      pan: '',
      name: '',
      form: '',
      ay: ''
    };

    // ─── Shared: Name Extraction (3-Tier) ───────────────────────────────────

    function _extractName() {
      // TIER 1: Form inputs on personal_information page
      let fName = '', mName = '', lName = '';
      try {
        const inputs = u.$qa(
          'input[name*="FirstName" i], input[id*="FirstName" i], output[name*="FirstName" i], output[id*="FirstName" i],' +
          'input[name*="MiddleName" i], input[id*="MiddleName" i], output[name*="MiddleName" i], output[id*="MiddleName" i],' +
          'input[name*="SurName" i], input[id*="SurName" i], output[name*="SurName" i], output[id*="SurName" i],' +
          'input[name*="LastName" i], input[id*="LastName" i], output[name*="LastName" i], output[id*="LastName" i]'
        );
        for (const el of inputs) {
          const key = (el.getAttribute('name') || el.id || '').toLowerCase();
          const val = (el.value || el.innerText || el.textContent || '').trim();
          if (!val || val.length > 60) continue;
          if (key.includes('firstname') || key.includes('first_name')) fName = val;
          else if (key.includes('middlename') || key.includes('middle_name')) mName = val;
          else if (key.includes('surname') || key.includes('lastname') || key.includes('last_name')) lName = val;
        }
        if (fName || lName) {
          return [fName, mName, lName].filter(Boolean).join(' ').trim().toUpperCase();
        }
      } catch (_) {}

      // TIER 2: Header profile badge
      const badgeSelectors = [
        '#loginUsername',
        'button[id*="loginUsername" i]',
        'span[id*="loginUsername" i]',
        '.header-user-name',
        '.user-profile-name',
        'span.header-username',
        'app-header .user-name',
      ];
      for (const sel of badgeSelectors) {
        try {
          const el = u.$q(sel);
          if (!el) continue;
          const val = u.getText(el).replace(/\.\.\.$/, '').trim();  // strip trailing ellipsis
          if (val && val.length >= 3 && val.length <= 80 && /^[A-Za-z\s.'-]+$/.test(val)) {
            return val.toUpperCase();
          }
        } catch (_) {}
      }

      // TIER 3: Page-level composite text regex
      // Matches patterns like: "MANZUR ALI MOLLA" in profile area
      // We look near "Individual" or near a valid PAN to anchor the name
      try {
        const pageText = u.getPageText();
        // Look for NAME appearing near "Individual" or near a PAN block
        const nameNearPan = pageText.match(
          /([A-Z][A-Z\s.'-]{2,59})\s+(?:[A-Z]{5}[0-9]{4}[A-Z])/
        );
        if (nameNearPan) {
          const candidate = nameNearPan[1].trim();
          if (candidate.split(' ').length >= 2) return candidate;
        }
        // Breadcrumb text sometimes contains name
        const bc = u.getBreadcrumbs();
        if (bc) {
          const bcName = bc.match(/(?:Welcome[,\s]+|Hello[,\s]+)([A-Z][A-Z\s.'-]{2,59})/i);
          if (bcName) return bcName[1].trim().toUpperCase();
        }
      } catch (_) {}

      return session.name || '';
    }

    // ─── Shared: PAN Extraction ──────────────────────────────────────────────

    function _extractPan() {
      // 1. Dedicated PAN input fields
      try {
        const panInputs = u.$qa(
          'input[id*="pan" i], input[name*="pan" i], output[id*="pan" i], output[name*="pan" i], [data-pan]'
        );
        for (const el of panInputs) {
          const val = (el.value || el.innerText || el.textContent || el.getAttribute('data-pan') || '').trim();
          const pan = u.extractPan(val);
          if (pan && u.isValidPan(pan)) return pan;
        }
      } catch (_) {}

      // 2. Full page text scan — first valid PAN hit
      const pageText = u.getPageText().toUpperCase();
      const matches = [...pageText.matchAll(/\b([A-Z]{5}[0-9]{4}[A-Z])\b/g)];
      for (const m of matches) {
        if (u.isValidPan(m[1])) return m[1];
      }

      return session.pan || '';
    }

    // ─── Shared: AY + Form from Context ─────────────────────────────────────

    function _extractAyAndForm(url) {
      const ay = u.extractAY(url) || session.ay || '';
      const form = u.extractItrForm(url, u.getPageText()) || session.form || '';
      return { ay, form };
    }

    // ─── Shared: Breadcrumb ITR context validator ────────────────────────────
    // Prevents non-ITR EVC pages from being classified as ITR filings.

    function _isItrContext(url) {
      const hashRoute = (new URL(url).hash || '').toLowerCase();
      // Must be in ITR filing flow
      const ITR_ROUTE_SIGNALS = [
        'foreturns-ay', 'fo-itr', 'fo-e-verify', 'fo-return-success',
        'fo-e-verify-later', 'fo-e-verify-now-success', 'fo-lets-get-started',
        'fo-select-itr', 'fo-schedules', 'fo-filing', 'fo-select-status',
        'personal_information', 'fo-itr-shared', 'fo-filing-status'
      ];
      if (ITR_ROUTE_SIGNALS.some(s => hashRoute.includes(s))) return true;

      // Breadcrumb check
      const bc = u.getBreadcrumbs().toLowerCase();
      if (bc.includes('income tax return') || bc.includes('e-file') || bc.includes('filing returns')) return true;

      return false;
    }

    // ─── One-shot form-value watcher (for Angular lazy-render) ───────────────
    // Called when T1 returns nothing on personal_information because the API
    // hasn't pre-filled the form fields yet. Watches for value to appear,
    // updates session.name, then self-destructs.

    function _watchForFormValues(url) {
      // Only run once per page visit
      if (window.__SDC_ITR_FORM_WATCHER__) return;
      window.__SDC_ITR_FORM_WATCHER__ = true;

      const FORM_SELECTOR =
        'input[name*="FirstName" i], input[id*="FirstName" i], ' +
        'output[name*="FirstName" i], output[id*="FirstName" i]';

      let attempts = 0;
      const MAX_ATTEMPTS = 20; // 20 × 500ms = 10s max wait

      const poll = setInterval(() => {
        attempts++;
        const el = u.$q(FORM_SELECTOR);
        const val = el ? (el.value || el.innerText || el.textContent || '').trim() : '';

        if (val && val.length >= 2) {
          clearInterval(poll);
          window.__SDC_ITR_FORM_WATCHER__ = false;

          // Re-run full name extraction now that fields are populated
          const fullName = _extractName();
          const pan = session.pan || _extractPan();

          if (fullName) {
            session.name = fullName;
            console.log(`⚡ Sera SDC [itr_personal_info]: ✅ Lazy-render name resolved → "${fullName}"`);
          }
          if (pan && !session.pan) session.pan = pan;
          return;
        }

        if (attempts >= MAX_ATTEMPTS) {
          clearInterval(poll);
          window.__SDC_ITR_FORM_WATCHER__ = false;
          console.log('⚡ Sera SDC [itr_personal_info]: Form fields not populated after 10s — using badge/T3 fallback.');
        }
      }, 500);
    }

    // ─── CROSSHAIR 1: Personal Info / Profile ────────────────────────────────
    // Target: .../personal_information OR .../profile pages
    // Purpose: Capture PAN + Full Legal Name + AY + Form type
    // Extracted: PAN (primary key), Legal Name (3-tier), Form type, AY
    //
    // Name extraction flow:
    //   T1 (form inputs: FirstName + MiddleName + SurName) — most accurate
    //     └─ If empty (Angular hasn't rendered yet): starts _watchForFormValues()
    //   T2 (header profile badge) — available immediately but may be truncated
    //   T3 (page text near PAN) — last resort

    function _handlePersonalInfo(url) {
      if (!_isItrContext(url)) return null;

      // Reset watcher guard on every fresh navigation to this page
      window.__SDC_ITR_FORM_WATCHER__ = false;

      const pan = _extractPan();
      const name = _extractName(); // tries T1 first
      const { ay, form } = _extractAyAndForm(url);

      // If T1 gave us nothing (Angular lazy), fire background watcher
      const t1InputEl = u.$q(
        'input[name*="FirstName" i], input[id*="FirstName" i], ' +
        'output[name*="FirstName" i], output[id*="FirstName" i]'
      );
      const t1HasValue = t1InputEl && (t1InputEl.value || t1InputEl.innerText || '').trim().length > 0;
      if (!t1HasValue) {
        _watchForFormValues(url);
        console.log('⚡ Sera SDC [itr_personal_info]: T1 empty — waiting for Angular to populate form fields.');
      }

      // Update session cache with whatever we have now (watcher will upgrade name later)
      if (pan) session.pan = pan;
      if (name) session.name = name;
      if (ay) session.ay = ay;
      if (form) session.form = form;

      if (!pan && !name) {
        console.log('Sera SDC [itr_personal_info]: No PAN or name found yet. Skipping dispatch (watcher active).');
        return null;
      }

      return {
        portal: 'income tax',
        pan,
        client_name: name,
        name,
        taxpayer_name: name,
        filing_type: form || 'ITR (Form Pending)',
        period_label: ay,
        arn: 'N/A',
        status: 'Draft / Personal Info',
        dom_breadcrumbs: u.getBreadcrumbs(),
        confirmation_message: ''
      };
    }


    // ─── CROSSHAIR 2: ITR Form Selection Page ────────────────────────────────
    // Target: .../fo-select-itr-form
    // Purpose: Lock in ITR Form Type (ITR-1, 2, 3, 4) + AY

    function _handleFormSelect(url) {
      if (!_isItrContext(url)) return null;

      const { ay, form } = _extractAyAndForm(url);
      if (form) session.form = form;
      if (ay) session.ay = ay;

      const pan = session.pan || _extractPan();
      const name = session.name || _extractName();

      if (!form) return null; // Nothing interesting yet

      return {
        portal: 'income tax',
        pan,
        client_name: name,
        name,
        taxpayer_name: name,
        filing_type: form,
        period_label: ay,
        arn: 'N/A',
        status: 'Form Selected',
        dom_breadcrumbs: u.getBreadcrumbs(),
        confirmation_message: ''
      };
    }

    // ─── CROSSHAIR 3: Filed & Verified ───────────────────────────────────────
    // Target: .../fo-e-verify-now-success OR .../fo-return-success
    // Trigger signals:
    //   • Banner: "You have successfully filed and verified your return!"
    //   • Buttons: [Go To Dashboard], [Download Receipt]
    //
    // NON-ITR EVC guard: only fires if breadcrumb / route confirms ITR flow.

    function _handleFiledVerified(url) {
      if (!_isItrContext(url)) return null;

      const pageText = u.getPageText();
      const lower = pageText.toLowerCase();

      // Must see the verified confirmation banner
      const VERIFIED_PHRASES = [
        'successfully filed and verified your return',
        'you have successfully filed and verified',
        'filed and verified',
        'return has been verified'
      ];
      const hasVerified = VERIFIED_PHRASES.some(p => lower.includes(p));
      if (!hasVerified) return null;

      // Extract 15-digit ACK
      const ack = u.extractAck15(pageText) || 'N/A';

      const pan = session.pan || _extractPan();
      const name = session.name || _extractName();
      const { ay, form } = _extractAyAndForm(url);

      // Update session
      if (pan) session.pan = pan;
      if (name) session.name = name;

      const msg = _extractBannerText(
        'successfully filed and verified',
        'you have successfully filed and verified your return'
      );

      return {
        portal: 'income tax',
        pan,
        client_name: name,
        name,
        taxpayer_name: name,
        filing_type: form || 'ITR',
        period_label: ay,
        arn: ack,
        status: 'Filed & Verified',
        dom_breadcrumbs: u.getBreadcrumbs(),
        confirmation_message: msg
      };
    }

    // ─── CROSSHAIR 4: Submitted (Pending e-Verification) ─────────────────────
    // Target: .../fo-e-verify-later OR .../complete-verification
    // Trigger signals:
    //   • Banner: "You have successfully submitted your return!"
    //   • Subtext: "you still need to e-Verify within 30 days"
    //   • Button: [Download ITR-V]
    //
    // NON-ITR EVC guard enforced.

    function _handleSubmittedPending(url) {
      if (!_isItrContext(url)) return null;

      const pageText = u.getPageText();
      const lower = pageText.toLowerCase();

      const SUBMITTED_PHRASES = [
        'successfully submitted your return',
        'you have successfully submitted',
        'e-verify within 30 days',
        'download itr-v',
        'download itrv'
      ];
      const hasSubmitted = SUBMITTED_PHRASES.some(p => lower.includes(p));
      if (!hasSubmitted) return null;

      // Reject if this is a verified page (already caught by crosshair 3)
      if (lower.includes('successfully filed and verified')) return null;

      const ack = u.extractAck15(pageText) || 'N/A';
      const pan = session.pan || _extractPan();
      const name = session.name || _extractName();
      const { ay, form } = _extractAyAndForm(url);

      if (pan) session.pan = pan;
      if (name) session.name = name;

      const msg = _extractBannerText(
        'successfully submitted your return',
        'you have successfully submitted your return'
      );

      return {
        portal: 'income tax',
        pan,
        client_name: name,
        name,
        taxpayer_name: name,
        filing_type: form || 'ITR',
        period_label: ay,
        arn: ack,
        status: 'Submitted (Pending e-Verification)',
        dom_breadcrumbs: u.getBreadcrumbs(),
        confirmation_message: msg
      };
    }

    // ─── Helper: Extract Banner Text from DOM ────────────────────────────────

    function _extractBannerText(...keywords) {
      try {
        const cards = u.$qa(
          '.alert, .success-card, .success-banner, .confirmation-message, ' +
          '[class*="success" i], [class*="alert" i], [class*="banner" i], ' +
          '[class*="confirm" i], [class*="message" i], p, h3, h4, span'
        );
        for (const el of cards) {
          const text = u.getText(el).toLowerCase();
          if (keywords.some(k => text.includes(k))) {
            return u.getText(el);
          }
        }
      } catch (_) {}
      return '';
    }

    // ─── Session Reset: Clear all ITR client data ────────────────────────────
    // Called on every login/logout route detection to ensure stale PAN/Name
    // from a previous client does not bleed into the next session.

    function _resetItrSession() {
      session.pan  = '';
      session.name = '';
      session.form = '';
      session.ay   = '';
      // Also clear the global window object so no stale carry-forward exists
      window.__SDC_ITR_SESSION__ = session;
      console.log('⚡ Sera SDC [ITR]: 🧹 Session cleared — ready for new client.');
    }

    // ─── CROSSHAIR 0 (Priority): Login / Logout Route Guard ─────────────────
    // Target: /login, /logout, /password (ITD portal login funnel)
    // Site history order: logout → login → password → fileIncomeTaxReturn → ...
    //
    // On ANY of these routes: clear all cached session data immediately.
    // Returns null (no capture to dispatch — this is a session hygiene crosshair).

    function _handleLoginLogout(url) {
      _resetItrSession();
      SDC.clearAllSessions(); // notify all protocols (extensible for GST, etc.)
      return null; // no capture to emit — just a session wipe
    }

    // ─── Register SDC.onSessionClear for ITR ────────────────────────────────
    // So that if clearAllSessions() is triggered from any OTHER protocol's
    // login detection (e.g., in future GST logout), ITR cache also gets wiped.
    SDC.onSessionClear(_resetItrSession);

    // ─── Register Protocol ───────────────────────────────────────────────────

    SDC.register({
      name: 'ITR Portal',
      hostMatch: /(?:incometax\.gov\.in|incometaxindiaefiling\.gov\.in)/,
      crosshairs: [
        {
          // PRIORITY 0: Login/logout detection — must run before any capture crosshair
          id: 'itr_login',
          pattern: /(?:^[^#]*$|[/#](?:login|logout|password|user-login|sign-in|signout|sign-out)(?:[?/]|$))/i,
          handler: _handleLoginLogout
        },
        {
          id: 'itr_filed_verified',
          // Highest capture priority: check success routes first
          pattern: /(?:fo-e-verify-now-success|fo-return-success|e-verify.*success)/i,
          handler: _handleFiledVerified
        },
        {
          id: 'itr_submitted_pending',
          pattern: /(?:fo-e-verify-later|complete-verification|fo-verify-later)/i,
          handler: _handleSubmittedPending
        },
        {
          id: 'itr_personal_info',
          pattern: /(?:personal.?information|personal.?info|profile|partA_gen|parta.?gen)/i,
          handler: _handlePersonalInfo
        },
        {
          id: 'itr_form_select',
          pattern: /(?:fo-select-itr-form|select.?itr.?form|fo-lets-get-started)/i,
          handler: _handleFormSelect
        }
      ]
    });

    console.log('⚡ Sera SDC: ITR Protocol registered successfully.');
  }

  _register();

})();
