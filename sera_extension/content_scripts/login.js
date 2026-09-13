const SERA_DEBUG = false; // production: silence all console output

let _lastScaTriggerUid = "";
let _lastScaTriggerTime = 0;
let _scaPasswordInjected = false;
try {
  if (sessionStorage.getItem('sera_sca_filled') === 'true' || window.__sera_sca_filled) {
    _scaPasswordInjected = true;
  }
} catch (_) {}

window.addEventListener('message', (event) => {
  try {
    if (event.source === window && event.data && event.data.source === 'sera_sca' && event.data.type === 'filled') {
      _scaPasswordInjected = true;
      try { sessionStorage.setItem('sera_sca_filled', 'true'); } catch (_) {}
      try { window.__sera_sca_filled = true; } catch (_) {}
      chrome.runtime.sendMessage({ type: 'sca_fill_completed' });
    }
  } catch (_) {}
});

function checkAndTriggerSCA(candidateText) {
  if (_scaPasswordInjected) return;
  try {
    if (sessionStorage.getItem('sera_sca_filled') === 'true' || window.__sera_sca_filled) {
      _scaPasswordInjected = true;
      return;
    }
  } catch (_) {}
  if (!candidateText || !window.normalizeUid) return;
  const clean = window.normalizeUid(candidateText);
  if (clean.length < 3 || clean.length > 100) return;

  const now = Date.now();
  if (_lastScaTriggerUid === clean && (now - _lastScaTriggerTime) < 800) {
    return;
  }

  chrome.storage.local.get(['armedSCAPayload', 'scaEnabled'], (data) => {
    if (data.scaEnabled === false) return;
    const armed = data.armedSCAPayload;
    if (armed && armed.expires_at > Date.now()) {
      const candidates = (armed.candidate_uids || [])
        .map(u => window.normalizeUid(u))
        .filter(Boolean);

      // Phase 2: Exact matching only!
      const isMatch = candidates.includes(clean);

      if (isMatch) {
        _lastScaTriggerUid = clean;
        _lastScaTriggerTime = Date.now();
        
        const adapter = window.getAdapterForUrl(window.location.href);
        const adapterName = adapter ? adapter.name : "generic";
        
        if (SERA_DEBUG) console.log(`Sera SCA: Exact UID match [${clean}] via adapter [${adapterName}]`);
        
        try {
          chrome.runtime.sendMessage({
            type: "SCA_MATCH_CANDIDATE",
            matched_uid: clean,
            arm_id: armed.arm_id,
            adapter: adapterName,
            portal: window.location.hostname
          });
        } catch (_) {}
      }
    }
  });
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
document.addEventListener('focusin', scanActiveInput, true);
document.addEventListener('keyup', (e) => {
  if (['Enter', 'Tab', 'ArrowRight', 'ArrowDown'].includes(e.key)) {
    scanActiveInput();
  }
}, true);
window.addEventListener('focus', scanActiveInput);

// Listen to direct messages from background
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "SHOW_SCA_WIDGET") {
    renderScaWidgetFromContent(message);
  } else if (message.type === "autofill" && message.userid) {
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

function renderScaWidgetFromContent(params) {
  if (_scaPasswordInjected) return;
  try {
    if (sessionStorage.getItem('sera_sca_filled') === 'true' || window.__sera_sca_filled) {
      _scaPasswordInjected = true;
      return;
    }
  } catch (_) {}
  const pwd = params.password || "";
  const pwdSel = params.password_selector || "";
  const bizName = params.business_name || "";
  const ownName = params.owner_name || "";
  const portalName = params.portal_name || "Portal";
  const matchedUid = params.matched_uid || "";

  function isVis(el) {
    if (!el || el.disabled || el.type === "hidden" || el.getAttribute("tabindex") === "-1") return false;
    try {
      const style = window.getComputedStyle(el);
      return style.display !== "none" && style.visibility !== "hidden";
    } catch (_) { return true; }
  }

  function simType(el, val) {
    if (!el) return;
    try { el.focus(); } catch (_) {}
    try {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(el, val);
    } catch (_) { el.value = val; }
    try {
      el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: val }));
    } catch (_) {}
    try { el.dispatchEvent(new Event('input', { bubbles: true })); } catch (_) {}
    try {
      el.dispatchEvent(new Event('change', { bubbles: true }));
    } catch (_) {}
    try {
      const len = (val || "").length;
      if (typeof el.setSelectionRange === "function") {
        el.setSelectionRange(len, len);
      }
    } catch (_) {}
    try { el.dispatchEvent(new Event('blur', { bubbles: true })); } catch (_) {}
  }

  const fallbacks = [
    pwdSel,
    "#user_pass",
    "input[id='user_pass']",
    "input[name='user_pass']",
    "input[id*='user_pass']",
    "input[name*='user_pass']",
    "input[id*='psw']",
    "input[name*='psw']",
    "input[id$='psw']",
    "input[name$='psw']",
    "input[name='psw']",
    "#psw",
    "input[name='Passwd']",
    "input[type='password']",
    "input[id*='password']",
    "input[name*='password']",
    "#password",
    "#passwordInput",
    "input[name='password']",
    "input[name='pass']"
  ].filter(Boolean);

  function findPassField() {
    for (const sel of fallbacks) {
      try {
        const els = document.querySelectorAll(sel);
        for (const el of els) {
          if (isVis(el)) return el;
        }
      } catch (_) {}
    }
    return null;
  }

  try {
    if (window.self !== window.top) return;
  } catch (_) {
    return;
  }

  const hostId = "sera-sca-widget-host";
  const old = document.getElementById(hostId);
  if (old) return;

  const host = document.createElement("div");
  host.id = hostId;
  const shadow = host.attachShadow({ mode: "closed" });

  const style = document.createElement("style");
  style.textContent = `
    .card {
      position: fixed; top: 20px; right: 24px; z-index: 2147483647;
      width: 320px; padding: 14px 16px;
      background: linear-gradient(145deg, #111814, #0B130E);
      border: 1.5px solid #2E9B5F;
      border-radius: 12px;
      box-shadow: none;
      color: #FFFFFF;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      transform: translateX(120%);
      opacity: 0;
      transition: transform 0.4s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.35s ease;
      box-sizing: border-box;
    }
    .header {
      display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px;
    }
    .badge {
      font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.6px;
      color: #4CF9B7; background: rgba(46, 155, 95, 0.22);
      border: 1px solid rgba(76, 249, 183, 0.35); padding: 3px 7px; border-radius: 6px;
      display: flex; align-items: center; gap: 4px;
    }
    .close-btn {
      background: transparent; border: none; cursor: pointer; font-size: 14px;
      color: #889988; line-height: 1; padding: 2px 4px; border-radius: 4px;
    }
    .close-btn:hover { color: #FFFFFF; }
    .title {
      font-size: 14px; font-weight: 700; color: #FFFFFF; line-height: 1.3;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 2px;
    }
    .subtitle {
      font-size: 12px; color: #9FB3A8; line-height: 1.2; margin-bottom: 10px;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .btn-inject {
      display: flex; align-items: center; justify-content: center; gap: 6px;
      width: 100%; padding: 9px 12px; font-size: 13px; font-weight: 700;
      color: #FFFFFF; background: #2E9B5F; border: 1px solid #34B76D;
      border-radius: 8px; cursor: pointer; transition: all 0.15s ease;
      box-shadow: 0 4px 12px rgba(46, 155, 95, 0.3);
      box-sizing: border-box;
    }
    .btn-inject:hover {
      background: #34B76D; box-shadow: 0 6px 16px rgba(52, 183, 109, 0.45);
    }
    .btn-inject:active {
      transform: scale(0.98);
    }
    .btn-inject.done {
      background: #102B1E; border-color: #2E9B5F; color: #4CF9B7;
    }
    .timer-container {
      margin-top: 10px; height: 3px; background: rgba(255, 255, 255, 0.08);
      border-radius: 2px; overflow: hidden;
    }
    .timer-bar {
      height: 100%; width: 100%; background: #2E9B5F; transform-origin: left;
      transition: transform 30s linear;
    }
  `;
  shadow.appendChild(style);

  const card = document.createElement("div");
  card.className = "card";

  const header = document.createElement("div");
  header.className = "header";

  const badge = document.createElement("div");
  badge.className = "badge";
  badge.textContent = "⚡ SCA Widget";

  const closeBtn = document.createElement("button");
  closeBtn.className = "close-btn";
  closeBtn.textContent = "✕";

  header.append(badge, closeBtn);

  const title = document.createElement("div");
  title.className = "title";
  title.textContent = bizName || ownName || matchedUid;

  const subtitle = document.createElement("div");
  subtitle.className = "subtitle";
  subtitle.textContent = `${portalName} • ${matchedUid}`;

  const injectBtn = document.createElement("button");
  injectBtn.className = "btn-inject";
  injectBtn.innerHTML = "🔑  Inject Password";

  const timerContainer = document.createElement("div");
  timerContainer.className = "timer-container";
  const timerBar = document.createElement("div");
  timerBar.className = "timer-bar";
  timerContainer.appendChild(timerBar);

  card.append(header, title, subtitle, injectBtn, timerContainer);
  shadow.appendChild(card);
  document.documentElement.appendChild(host);

  setTimeout(() => {
    card.style.transform = "translateX(0)";
    card.style.opacity = "1";
    timerBar.style.transform = "scaleX(0)";
  }, 40);

  function dismiss() {
    card.style.transform = "translateX(120%)";
    card.style.opacity = "0";
    setTimeout(() => { if (host.isConnected) host.remove(); }, 380);
  }

  closeBtn.onclick = dismiss;
  const autoTimer = setTimeout(dismiss, 30000);

  injectBtn.onclick = () => {
    const currentField = findPassField();
    if (currentField) {
      simType(currentField, pwd);
      _scaPasswordInjected = true;
      try { sessionStorage.setItem('sera_sca_filled', 'true'); } catch (_) {}
      try { window.__sera_sca_filled = true; } catch (_) {}
      window.postMessage({ source: "sera_sca", type: "filled" }, "*");
      clearTimeout(autoTimer);
      dismiss();
    } else {
      injectBtn.innerHTML = "⚠️ Password field not visible";
      setTimeout(() => {
        injectBtn.innerHTML = "🔑  Inject Password";
      }, 1500);
    }
  };
}

// Phase 2: Handle fill command from background coordinator
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "SCA_FILL_COMMAND") {
    const adapter = window.getAdapterForUrl ? window.getAdapterForUrl(window.location.href) : null;
    if (!adapter) {
      if (SERA_DEBUG) console.log("Sera SCA: No adapter found for fill command.");
      return;
    }
    
    const password = message.password;
    if (!password) return;

    let attempts = 0;
    const interval = setInterval(() => {
      attempts++;
      try {
        const pwFields = adapter.findPasswordFields(document);
        
        if (pwFields.length > 0) {
          clearInterval(interval);
          setTimeout(() => {
            try {
              const success = adapter.fillPassword(pwFields[0], password);
              if (success) {
                if (SERA_DEBUG) console.log("Sera SCA: Password filled via adapter.");
                _scaPasswordInjected = true;
                try { sessionStorage.setItem('sera_sca_filled', 'true'); } catch (_) {}
                try { window.__sera_sca_filled = true; } catch (_) {}
                chrome.runtime.sendMessage({ type: "SCA_FILL_RESULT", result: "success", detail: `Password filled on ${message.adapter}` });
                chrome.runtime.sendMessage({ type: "sca_fill_completed" });
              } else {
                chrome.runtime.sendMessage({ type: "SCA_ERROR", detail: `Adapter ${message.adapter} failed to fill password field.` });
              }
            } catch (fillErr) {
              chrome.runtime.sendMessage({ type: "SCA_ERROR", detail: `Exception during ${message.adapter} fill: ${fillErr.message}` });
            }
          }, 100);
        } else if (attempts >= 200) {
          clearInterval(interval);
          chrome.runtime.sendMessage({ type: "SCA_ERROR", detail: `Timeout waiting for password field on ${message.adapter}` });
        }
      } catch (err) {
        clearInterval(interval);
        chrome.runtime.sendMessage({ type: "SCA_ERROR", detail: `Exception detecting fields on ${message.adapter}: ${err.message}` });
      }
    }, 150);
  }
});

function showScaToast(bizName, ownName, portalName) {
  // Suppressed permanently: prevents obscuring top-right taxpayer badge/profile header required by VSDC visual name capture
  return;
}

// ── In-Page SCC Verification Detector (Income Tax Only) ─────────────────
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
