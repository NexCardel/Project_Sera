/**
 * traces_protocol.js — SDC TRACES (TDS) Portal Protocol
 * =============================================================
 * Crosshairs for the TDS CPC portal (tdscpc.gov.in / TRACES 2.0).
 * Built entirely on SUDR (SDC.emit) — no legacy 'filing_result' shape,
 * since nothing downstream depended on TRACES before this existed.
 * See sdcClaude.md §2 for TRACES-vs-ITR-vs-GST similarity mapping this
 * crosshair map is derived from.
 *
 * Crosshair Map:
 * ┌──────────────────────────────────────────────────────────────────────────────────────┐
 * │ ID                     │ Route Pattern / Scope                    │ event.type        │
 * ├──────────────────────────────────────────────────────────────────────────────────────┤
 * │ traces_login_logout    │ /login, /logout, session-expire routes   │ LOGIN_SUCCESS /   │
 * │                        │                                          │ LOGOUT            │
 * │ traces_dashboard       │ deductor dashboard / landing             │ PORTAL_VIEW       │
 * │ traces_profile         │ deductor profile page (TAN + Name)       │ FORM_VIEW         │
 * │ traces_statement_upload│ 24Q/26Q/27Q upload / FVU-validated       │ FILING_SUBMITTED  │
 * │ traces_statement_status│ processed/rejected/defaulted status view │ FILING_VERIFIED / │
 * │                        │                                          │ ERROR             │
 * │ traces_form16          │ Form 16/16A request & download           │ FILING_VERIFIED   │
 * └──────────────────────────────────────────────────────────────────────────────────────┘
 *
 * Identity note (see sdcClaude.md §4): TRACES is two-sided — the logged-in
 * Deductor (TAN) and, on statement/Form-16 rows, a Deductee (PAN) whose tax
 * was deducted. Handlers below scope `pan` to the *deductor's own* PAN
 * where scraped from the profile page; per-row deductee PANs stay in
 * `fields` (namespaced `traces.deductee_pan`) rather than the shared
 * `identity.pan` slot, since they don't identify the session's own user.
 */

(function () {
  'use strict';

  let _tracesSession = {
    tan: '',
    deductor_name: '',
    client_temp_name: ''
  };

  function _resetTracesSession() {
    _tracesSession = { tan: '', deductor_name: '', client_temp_name: '' };
  }

  function _cleanText(el) {
    if (!el) return '';
    return (el.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function _queryFirst(selectors) {
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el && _cleanText(el)) return el;
    }
    return null;
  }

  // ─── Header badge fallback (Deductor name shown once logged in) ────────────
  function _extractHeaderName() {
    const el = _queryFirst(['.deductor-name', '#deductorName', '.user-name', '.header-user', '[id*=deductor]']);
    return el ? _cleanText(el) : '';
  }

  function _extractTanFromText() {
    const bodyText = document.body ? document.body.innerText : '';
    const m = bodyText.match(/\b([A-Z]{4}[0-9]{5}[A-Z])\b/);
    return m ? m[1] : '';
  }

  // ─── Crosshair Handlers ──────────────────────────────────────────────────

  async function _handleLoginLogout(url) {
    const isLogout = /logout|sign-?out|session.?expire|session-?timeout/i.test(url);

    if (isLogout) {
      _resetTracesSession();
      window.__SERA_SDC__.emit('TRACES Portal', 'traces_login_logout', 'LOGOUT',
        { tan: null, legal_name: null }, {});
      return null;
    }

    const tan = _extractTanFromText();
    if (tan) _tracesSession.tan = tan;

    window.__SERA_SDC__.emit('TRACES Portal', 'traces_login_logout', 'LOGIN_SUCCESS',
      { tan: _tracesSession.tan || null, legal_name: _tracesSession.deductor_name || null }, {});
    return null;
  }

  async function _handleDashboard() {
    const name = _extractHeaderName();
    if (name) _tracesSession.deductor_name = name;

    window.__SERA_SDC__.emit('TRACES Portal', 'traces_dashboard', 'PORTAL_VIEW',
      { tan: _tracesSession.tan || null, legal_name: _tracesSession.deductor_name || null }, {});
    return null;
  }

  async function _handleProfile() {
    const tanEl = _queryFirst(['#tan', '[name=tan]', '.profile-tan']);
    const nameEl = _queryFirst(['#deductorName', '.profile-name', '.deductor-legal-name']);

    const tan = tanEl ? _cleanText(tanEl) : (_extractTanFromText() || _tracesSession.tan);
    const name = nameEl ? _cleanText(nameEl) : _tracesSession.deductor_name;

    if (tan) _tracesSession.tan = tan;
    if (name) _tracesSession.deductor_name = name;

    window.__SERA_SDC__.emit('TRACES Portal', 'traces_profile', 'FORM_VIEW',
      { tan: _tracesSession.tan || null, legal_name: _tracesSession.deductor_name || null },
      {});
    return null;
  }

  async function _handleStatementUpload(url) {
    const formMatch = url.match(/24q|26q|27q|27eq/i);
    const formType = formMatch ? formMatch[0].toUpperCase() : '';

    const fyEl = _queryFirst(['.financial-year', '#fy', '[name=fy]']);
    const qtrEl = _queryFirst(['.quarter', '#quarter', '[name=quarter]']);
    const tokenEl = _queryFirst(['.token-number', '#token', '.prn']);

    window.__SERA_SDC__.emit('TRACES Portal', 'traces_statement_upload', 'FILING_SUBMITTED',
      { tan: _tracesSession.tan || null, legal_name: _tracesSession.deductor_name || null },
      {
        'traces.form_type': formType,
        'traces.financial_year': fyEl ? _cleanText(fyEl) : '',
        'traces.quarter': qtrEl ? _cleanText(qtrEl) : '',
        'traces.token_number': tokenEl ? _cleanText(tokenEl) : ''
      });
    return null;
  }

  async function _handleStatementStatus() {
    const statusEl = _queryFirst(['.statement-status', '#statementStatus', '.status-cell']);
    const statusText = statusEl ? _cleanText(statusEl) : '';
    const lower = statusText.toLowerCase();

    // Real TRACES status vocabulary (see sdcClaude.md §2):
    //   Processed without Default / Processed for 26AS -> FILING_VERIFIED
    //   Processed with Default / Rejected               -> ERROR
    //   Pending for Processing                           -> FILING_SUBMITTED
    let eventType = 'FILING_SUBMITTED';
    if (/without default|processed for 26as/.test(lower)) eventType = 'FILING_VERIFIED';
    else if (/with default|rejected/.test(lower)) eventType = 'ERROR';

    window.__SERA_SDC__.emit('TRACES Portal', 'traces_statement_status', eventType,
      { tan: _tracesSession.tan || null, legal_name: _tracesSession.deductor_name || null },
      { 'traces.raw_status': statusText });
    return null;
  }

  async function _handleForm16() {
    const requestStatusEl = _queryFirst(['.request-status', '#requestStatus']);
    const statusText = requestStatusEl ? _cleanText(requestStatusEl) : '';
    // 'Available' means the form is downloadable -> verified/complete.
    const eventType = /available/i.test(statusText) ? 'FILING_VERIFIED' : 'FILING_SUBMITTED';

    window.__SERA_SDC__.emit('TRACES Portal', 'traces_form16', eventType,
      { tan: _tracesSession.tan || null, legal_name: _tracesSession.deductor_name || null },
      { 'traces.form16_request_status': statusText });
    return null;
  }

  // ─── Register ────────────────────────────────────────────────────────────
  function _register() {
    const SDC = window.__SERA_SDC__;
    if (!SDC) { setTimeout(_register, 100); return; }

    SDC.onSessionClear(_resetTracesSession);

    SDC.register({
      name: 'TRACES Portal',
      hostMatch: /(?:tdscpc\.gov\.in|traces\.nsdl\.com|localhost|127\.0\.0\.1)/i,
      crosshairs: [
        {
          id: 'traces_statement_status',
          pattern: /(?:statement.?status|view.?statement.?status)/i,
          handler: _handleStatementStatus
        },
        {
          id: 'traces_form16',
          pattern: /(?:form16|form-16|requested.?downloads|download.*form.?16)/i,
          handler: _handleForm16
        },
        {
          id: 'traces_statement_upload',
          pattern: /(?:24q|26q|27q|27eq|upload.?statement|statement.?upload)/i,
          handler: _handleStatementUpload
        },
        {
          id: 'traces_profile',
          pattern: /(?:profile|deductor.?details|my.?profile)/i,
          handler: _handleProfile
        },
        {
          id: 'traces_dashboard',
          pattern: /(?:dashboard|home|landing|welcome)/i,
          handler: _handleDashboard
        },
        {
          id: 'traces_login_logout',
          pattern: /[/#](?:login|logout|sign-?in|sign-?out|session.?expire|session-?timeout)(?:[?/#]|$)/i,
          handler: _handleLoginLogout
        }
      ]
    });

    console.log('⚡ Sera SDC: TRACES Portal Protocol registered (SUDR-native, ' +
      '6 crosshairs — login, dashboard, profile, statement upload, statement status, Form 16).');
  }

  _register();
})();
