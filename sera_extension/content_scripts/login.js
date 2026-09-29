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
