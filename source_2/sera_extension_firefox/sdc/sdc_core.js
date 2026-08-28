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

    /** Manually trigger a scan for the current URL (used for testing or forced re-scans). */
    scanNow() {
      _onUrlChange(window.location.href);
    }
  };

  // ─── Route Change Detection (SPA-safe) ──────────────────────────────────────
  let _lastScannedUrl = '';
  let _debounceTimer = null;

  function _onUrlChange(url) {
    if (_debounceTimer) clearTimeout(_debounceTimer);
    _debounceTimer = setTimeout(() => _dispatch(url), 400);
  }

  // Intercept pushState / replaceState for SPA navigation
  ['pushState', 'replaceState'].forEach(method => {
    const original = history[method];
    history[method] = function (...args) {
      const result = original.apply(this, args);
      setTimeout(() => _onUrlChange(window.location.href), 0);
      return result;
    };
  });

  // hashchange for hash-routed apps (ITD portal uses Angular with hash routing)
  window.addEventListener('hashchange', () => _onUrlChange(window.location.href));

  // popstate for browser back/forward
  window.addEventListener('popstate', () => _onUrlChange(window.location.href));

  // ─── Dispatch: Match URL → Protocol → Crosshair → Handler ──────────────────
  async function _dispatch(url) {
    if (url === _lastScannedUrl) return;

    const host = (window.location.hostname || '').toLowerCase();

    for (const protocol of _protocols) {
      if (!protocol.hostMatch.test(host)) continue;

      for (const crosshair of protocol.crosshairs) {
        if (!crosshair.pattern.test(url)) continue;

        console.log(`⚡ Sera SDC: Crosshair matched → [${protocol.name}] "${crosshair.id}" for URL: ${url.substring(0, 120)}`);

        try {
          const capture = await crosshair.handler(url);
          if (capture) {
            _lastScannedUrl = url;
            _emitCapture(capture, protocol.name, crosshair.id);
          }
        } catch (err) {
          console.warn(`⚡ Sera SDC: Handler error in crosshair "${crosshair.id}":`, err);
        }

        // Only one crosshair fires per URL change per protocol (first match wins)
        break;
      }
    }
  }

  // ─── Capture Emit ────────────────────────────────────────────────────────────
  // Re-dispatches as 'SeraFSTApiCapture' so filing_detector.js picks it up unchanged.
  function _emitCapture(capture, protocolName, crosshairId) {
    const detail = {
      ...capture,
      capture_method: `SDC_${crosshairId}`,
      portal: capture.portal || protocolName,
      url: window.location.href,
      timestamp: new Date().toISOString()
    };

    console.log(`⚡ Sera SDC CAPTURE [${crosshairId}]:`, JSON.stringify(detail).substring(0, 300));

    // Fire on SeraFSTApiCapture → picked up by filing_detector.js
    window.dispatchEvent(new CustomEvent('SeraFSTApiCapture', { detail }));

    // Also show toast via the existing notifier if available
    try {
      if (window.__SERA_TOAST_NOTIFIER__ && typeof window.__SERA_TOAST_NOTIFIER__.notify === 'function') {
        window.__SERA_TOAST_NOTIFIER__.notify(detail);
      }
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
