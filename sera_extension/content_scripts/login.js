const SERA_DEBUG = false; // production: silence all console output

// ---------------- SCA (Sera Clipboard Assist) - page side, protocol v2 ----------------
// This script only notices that something that LOOKS like a client id was pasted or typed and
// tells the extension's background (sca/sca_coordinator.js). The background decides whether
// it matches the armed client AND this site is that client's portal; only then does it ask the
// desktop for the password. This page never sees an arm, a candidate list or a password.
//
// Removed 2026-09-22: the per-tab "sera_sca_filled" sessionStorage flag (never cleared - SCA
// went silent in a tab after its first fill, so the second client logged in there got
// nothing), the page-readable armed payload, and the unused SCA_FILL_COMMAND / widget code.
const SCA_UID_SHAPE = /^(?:[A-Z]{5}[0-9]{4}[A-Z]|[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]|[A-Z0-9][A-Z0-9._@:/-]{2,79})$/;
let _scaLastSent = "";
let _scaLastSentAt = 0;

function checkAndTriggerSCA(candidateText) {
  if (!candidateText || !window.normalizeUid) return;
  const clean = window.normalizeUid(candidateText);
  if (!SCA_UID_SHAPE.test(clean)) return;
  const now = Date.now();
  if (clean === _scaLastSent && now - _scaLastSentAt < 1500) return;   // one paste fires several events
  _scaLastSent = clean;
  _scaLastSentAt = now;
  try {
    chrome.runtime.sendMessage({ type: "SCA_MATCH_CANDIDATE", candidate: clean }, () => {
      void chrome.runtime.lastError;   // no listener / extension reloading - nothing to do
    });
  } catch (_) {}
}

function scanActiveInput() {
  try {
    const el = document.activeElement;
    if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')) {
      const type = (el.type || '').toLowerCase();
      if (type !== 'password' && el.value) {
        checkAndTriggerSCA(el.value);
      }
    }
  } catch (_) {}
}

document.addEventListener('paste', (e) => {
  try {
    const pastedText = (e.clipboardData || window.clipboardData).getData('text');
    checkAndTriggerSCA(pastedText);
  } catch (_) {}
}, true);

document.addEventListener('input', (e) => {
  try {
    const target = e.target;
    if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) {
      const type = (target.type || '').toLowerCase();
      if (type !== 'password' && target.value) {
        checkAndTriggerSCA(target.value);
      }
    }
  } catch (_) {}
}, true);

document.addEventListener('change', scanActiveInput, true);
document.addEventListener('keyup', (e) => {
  if (['Enter', 'Tab'].includes(e.key)) {
    scanActiveInput();
  }
}, true);

// ---------------- Autofill fallback (desktop "Autofill" button, not SCA) ----------------
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "autofill" && message.userid) {
    if (SERA_DEBUG) console.log("Sera content script: received fallback autofill message.");
    var userField = document.querySelector("input[id*='userId']") ||
                    document.querySelector("input[name*='userId']") ||
                    document.querySelector("#userId") ||
                    document.querySelector("input[name='userId']") ||
                    document.querySelector("#panAdhaarUserId") ||
                    document.querySelector("#username") ||
                    document.querySelector("input[name='user_name']") ||
                    document.querySelector("input[name='pan']");
    if (userField && message.userid) {
      userField.focus();
      userField.value = message.userid;
      userField.dispatchEvent(new Event('input', { bubbles: true }));
      userField.dispatchEvent(new Event('change', { bubbles: true }));
    }

    var passField = document.querySelector("input[id*='psw']") ||
                    document.querySelector("input[name*='psw']") ||
                    document.querySelector("#psw") ||
                    document.querySelector("input[name='psw']") ||
                    document.querySelector("#passwordInput") ||
                    document.querySelector("input[type='password']") ||
                    document.querySelector("#user_pass") ||
                    document.querySelector("input[name='user_pass']");
    if (passField && message.password) {
      passField.focus();
      passField.value = message.password;
      passField.dispatchEvent(new Event('input', { bubbles: true }));
      passField.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }
});

function checkSccLoginSuccess() {
  try {
    const href = window.location.href || "";
    if (!href.includes("incometax.gov.in")) return;

    if (typeof chrome === "undefined" || !chrome.storage || !chrome.storage.local) return;

    chrome.storage.local.get(['sccActiveAttempt'], (data) => {
      const attempt = data && data.sccActiveAttempt;
      if (!attempt || !attempt.password) return;

      const lowerHref = href.toLowerCase();
      const isLogin = lowerHref.includes('/login') || lowerHref.includes('/auth') || lowerHref.includes('/foservices');
      const isDashboard = lowerHref.includes('/dashboard') || lowerHref.includes('/home') || lowerHref.includes('/welcome');
      const hasUserHeader = Boolean(
        document.querySelector('#loginUsername, .user-name, button[id*="loginUsername" i], span[id*="loginUsername" i], a[href*="logout"], button:has(i.fa-power-off)')
      );

      if (hasUserHeader || (isDashboard && !lowerHref.includes('/login'))) {
        chrome.storage.local.remove(['sccActiveAttempt']);
        chrome.runtime.sendMessage({
          type: "SCC_LOGIN_DETECTED",
          destination_url: href,
          attempt: attempt
        });
      }
    });
  } catch (_) {}
}

// ── Automated In-Page Unregistered Client SCC-MECP Trigger ────────────────
function checkUnregisteredSccTrigger() {
  try {
    const href = window.location.href || "";
    if (!href.includes("incometax.gov.in")) return;
    const lowerHref = href.toLowerCase();
    const isPasswordPage = lowerHref.includes('login/password') || lowerHref.includes('/password');
    if (!isPasswordPage) return;

    if (typeof chrome === "undefined" || !chrome.storage || !chrome.storage.local) return;

    const bodyText = document.body ? document.body.innerText : '';
    let pan = '';
    const m = bodyText.match(/(?:PAN|User\s*ID)\s*[:\-]?\s*([A-Z]{5}[0-9]{4}[A-Z]{1})\b/i);
    if (m && m[1]) {
      pan = m[1].toUpperCase();
    } else {
      const allMatches = [...bodyText.matchAll(/\b([A-Z]{5}[0-9]{4}[A-Z]{1})\b/g)];
      if (allMatches.length > 0) {
        pan = allMatches[0][1].toUpperCase();
      }
    }
    if (!pan) return;

    if (window.__SERA_LAST_UNREG_SCC_PAN__ === pan) return;

    chrome.storage.local.get(['registeredPans', 'sccEnabled'], (data) => {
      if (data.sccEnabled === false) return;
      const regList = (data.registeredPans || []).map(p => String(p).trim().toUpperCase());
      // Strictly do NOT pop up for registered clients
      if (regList.includes(pan)) return;

      window.__SERA_LAST_UNREG_SCC_PAN__ = pan;
      chrome.runtime.sendMessage({
        type: "TRIGGER_UNREGISTERED_SCC_MECP",
        pan: pan,
        portal: "Income Tax"
      });
    });
  } catch (_) {}
}

if (typeof window !== "undefined" && window.location && (window.location.hostname || "").includes("incometax.gov.in")) {
  window.addEventListener('hashchange', () => {
    checkSccLoginSuccess();
    checkUnregisteredSccTrigger();
  });
  window.addEventListener('popstate', () => {
    checkSccLoginSuccess();
    checkUnregisteredSccTrigger();
  });
  const _sccInterval = setInterval(() => {
    checkSccLoginSuccess();
    checkUnregisteredSccTrigger();
  }, 1500);
  setTimeout(() => clearInterval(_sccInterval), 180000);
}
