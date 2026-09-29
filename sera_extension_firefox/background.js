// ---------------- WebSocket bridge to the desktop app (ui/ws_bridge.py) ----------------
// 2026-09-22: replaces Firefox Native Messaging (a registry key + host manifest that routinely
// failed to register on a fresh PC) and the direct HTTP fallback to port 49152 (which sat
// inside Windows' own dynamic port range, so another program could already be using it at
// random, and stalled every request ~2s). One WebSocket, tried on a short list of fixed ports
// below that range - nothing to install, nothing to register. See
// docs/app-extension-communication-report.md for the full comparison.
const WS_PORTS = [48765, 48766, 48767, 48768];
let ws = null;
let wsConnecting = false;
let wsPortIndex = 0;
let wsReconnectDelay = 1000;
const WS_RECONNECT_MAX_MS = 15000;
const _pendingWsRequests = new Map(); // "_id" -> {resolve, timer}

// ---------------- SCA (Sera Clipboard Assist) - protocol v2 ----------------
// All SCA logic is in sca/sca_coordinator.js, shared with the Chrome build. Arms carry no
// passwords; one is requested from the desktop only after the client's id is entered on that
// client's portal (see sca_protocol.py).
if (typeof self.SeraSCA === "undefined" && typeof importScripts === "function") {
  importScripts("sca/sca_coordinator.js");
}
// SCA v1 kept every armed client password in chrome.storage.local (written to disk). Remove
// anything it left behind.
try { chrome.storage.local.remove(["armedSCAPayload"]); } catch (_) {}

// ---------------- Session-only storage for password payloads (finding #2) ----------------
// manualAssistPayload / mecpPayload / sccActiveAttempt carry a plaintext password and must never
// touch disk. chrome.storage.session is memory-only, cleared when the browser closes. On a
// Firefox build old enough to lack it (< 115), fall back to a plain in-memory store of the same
// shape - these are only ever read back from this same background context.
const _passwordStore = (chrome.storage && chrome.storage.session) ? chrome.storage.session : (() => {
  const mem = {};
  const keysOf = (k) => Array.isArray(k) ? k : [k];
  return {
    get: (keys, cb) => {
      const out = {};
      keysOf(keys).forEach(k => { if (mem[k] !== undefined) out[k] = mem[k]; });
      if (cb) { cb(out); return; }
      return Promise.resolve(out);
    },
    set: (obj, cb) => {
      Object.assign(mem, obj);
      if (cb) { cb(); return; }
      return Promise.resolve();
    },
    remove: (keys, cb) => {
      keysOf(keys).forEach(k => { delete mem[k]; });
      if (cb) { cb(); return; }
      return Promise.resolve();
    }
  };
})();

// Older installs kept these in chrome.storage.local (on disk); clear any leftovers from before
// the upgrade, plus the tracking payload Part A removed.
try {
  chrome.storage.local.remove(['manualAssistPayload', 'mecpPayload', 'sccActiveAttempt', 'activeAutofillPayload']);
} catch (_) {}
const scaCoordinator = self.SeraSCA.createCoordinator({
  postNative: (msg) => wsSendNow(msg),
  postDesktop: (msg) => { sendToDesktop(msg); },
  getSettings: () => new Promise((resolve) => {
    chrome.storage.local.get(["scaEnabled", "scaMode", "allowedDomains"], (d) => {
      _passwordStore.get(["manualAssistPayload"], (pd) => {
        const ma = pd.manualAssistPayload;
        resolve({
          scaEnabled: d.scaEnabled,
          scaMode: d.scaMode,
          allowedDomains: d.allowedDomains || [],
          manualAssistActive: !!(ma && ma.expiresAt && ma.expiresAt > Date.now()),
        });
      });
    });
  }),
  executeScript: (details) => chrome.scripting.executeScript(details),
  sessionStore: (chrome.storage && chrome.storage.session) || null,
});

function _wsMessageId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

// Fire-and-forget send on the open socket only - used where the caller just needs a quick,
// synchronous "did this go out" answer (e.g. an SCA_ACK), the same role a raw
// nativePort.postMessage() used to play.
function wsSendNow(msg) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    ensureConnected();
    return false;
  }
  try {
    ws.send(JSON.stringify({ ...msg, _id: _wsMessageId() }));
    return true;
  } catch (_) {
    return false;
  }
}

function connectWS() {
  if (ws || wsConnecting) return;
  wsConnecting = true;
  const port = WS_PORTS[wsPortIndex % WS_PORTS.length];
  let socket;
  try {
    socket = new WebSocket(`ws://127.0.0.1:${port}/`);
  } catch (e) {
    wsConnecting = false;
    scheduleReconnect();
    return;
  }

  const connectTimeout = setTimeout(() => {
    if (socket.readyState !== WebSocket.OPEN) {
      try { socket.close(); } catch (_) {}
    }
  }, 2500);

  socket.onopen = () => {
    clearTimeout(connectTimeout);
    ws = socket;
    wsConnecting = false;
    wsReconnectDelay = 1000;
    console.log(`Sera: WebSocket bridge connected on port ${port}`);
    // Notify any callers awaiting connection
    _wsOpenCallbacks.forEach(cb => { try { cb(); } catch (_) {} });
    _wsOpenCallbacks.length = 0;
  };
  socket.onmessage = (event) => {
    let message;
    try { message = JSON.parse(event.data); } catch (_) { return; }
    handleDesktopMessage(message);
  };
  socket.onerror = () => {};
  socket.onclose = () => {
    clearTimeout(connectTimeout);
    wsConnecting = false;
    if (ws === socket) ws = null;
    wsPortIndex++; // try the next candidate port next time
    scheduleReconnect();
  };
}

function scheduleReconnect() {
  setTimeout(() => { if (!ws) connectWS(); }, wsReconnectDelay);
  wsReconnectDelay = Math.min(wsReconnectDelay * 2, WS_RECONNECT_MAX_MS);
}

// Callbacks waiting for the WebSocket to open
const _wsOpenCallbacks = [];

/**
 * Resolves with true when the WebSocket is (or becomes) OPEN, or false after timeoutMs.
 */
function waitForConnection(timeoutMs = 5000) {
  if (ws && ws.readyState === WebSocket.OPEN) return Promise.resolve(true);
  ensureConnected();
  return new Promise(resolve => {
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      const idx = _wsOpenCallbacks.indexOf(cb);
      if (idx !== -1) _wsOpenCallbacks.splice(idx, 1);
      resolve(false);
    }, timeoutMs);
    const cb = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(true);
    };
    _wsOpenCallbacks.push(cb);
  });
}

function handleDesktopMessage(message) {
  console.log("Received from Sera desktop:", message);

  // A reply to a message this background page itself sent (a generic ack).
  if (message.type === '_ack' && message._id && _pendingWsRequests.has(message._id)) {
    const pending = _pendingWsRequests.get(message._id);
    clearTimeout(pending.timer);
    _pendingWsRequests.delete(message._id);
    pending.resolve(true);
    return;
  }

  if (message.type && message.type.startsWith("SCA_")) {
    scaCoordinator.handleDesktopMessage(message);
    return;
  }
  if (message.type === "autofill" && message.url) {
    if (message.mode === "mecp" || message.mode === "manual_copy") handleMECPTab(message);
    else if (message.mode === "manual_assist") handleManualAssistTab(message);
    else handleAutofillTab(message);
  } else if (message.type === "update_settings") {
    const sca = message.sca_enabled !== false;
    const scaMode = message.sca_mode || "autofill";
    const allowedDomains = message.allowed_domains || [];
    const storageObj = {
      scaEnabled: sca,
      scaMode: scaMode
    };
    if (allowedDomains && allowedDomains.length > 0) {
      storageObj.allowedDomains = allowedDomains;
    }
    chrome.storage.local.set(storageObj);
  }
}

function ensureConnected() {
  if (!ws || ws.readyState !== WebSocket.OPEN) connectWS();
}

// Sends one message over the bridge. With waitForAck=true, resolves only once the app has
// confirmed receipt (a generic "_ack" reply) - the same guarantee an HTTP 200 used to give.
// Awaits the connection for up to 5s if the bridge is reconnecting.
async function sendToDesktop(msg, waitForAck = false) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    const connected = await waitForConnection(5000);
    if (!connected) return false;
  }
  const id = _wsMessageId();
  try {
    ws.send(JSON.stringify({ ...msg, _id: id }));
  } catch (e) {
    console.warn("Sera background: WebSocket send failed:", e);
    return false;
  }
  if (!waitForAck) return true;
  return new Promise(resolve => {
    const timer = setTimeout(() => {
      _pendingWsRequests.delete(id);
      resolve(false);
    }, 4000);
    _pendingWsRequests.set(id, { resolve, timer });
  });
}


try {
  chrome.alarms.create("sera_keep_alive", { periodInMinutes: 0.5 });
  chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === "sera_keep_alive") ensureConnected();
  });
} catch (e) {}

chrome.runtime.onStartup.addListener(ensureConnected);
chrome.runtime.onInstalled.addListener(() => {
  ensureConnected();
});

ensureConnected();

console.log('Sera: background.js module loaded, registering listeners.');

// Fill function injected into the page
function fillCredentialsInPage(userid, password, usernameSelector, passwordSelector, extensionFlow) {
  if (window.__seraFillActive) return; // prevent duplicate runs
  window.__seraFillActive = true;
  console.log("Sera: fillCredentialsInPage started, flow:", extensionFlow);

  function cleanSelector(sel) {
    if (!sel) return "";
    return sel.trim().replace(/\s+\[/g, '[').replace(/input\s+/g, 'input');
  }

  function queryFirst(selectorStr) {
    if (!selectorStr) return null;
    const parts = selectorStr.split(',').map(s => s.trim()).filter(Boolean);
    for (const p of parts) {
      try {
        const els = document.querySelectorAll(p);
        for (const el of els) {
          if (isVisible(el)) return el;
        }
      } catch (e) {}
    }
    return null;
  }

  function isVisible(el) {
    if (!el) return false;
    if (el.name === 'hiddenPassword' || el.getAttribute('tabindex') === '-1' || el.getAttribute('aria-hidden') === 'true' || el.closest('[aria-hidden="true"]')) return false;
    if (el.type === 'hidden') return false;
    try {
      const style = window.getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') === 0) return false;
      const rect = el.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    } catch (e) {
      return true;
    }
  }


  function simulateType(el, value) {
    if (!el) return;
    try { el.focus(); } catch (e) {}

    // Use native property descriptor setter
    try {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(el, value);
    } catch (e) { el.value = value; }

    el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  // Auto-click Continue/Login button after password fill
  function autoClickContinue() {
    const btnSelectors = [
      "button[type='submit']",
      "button.mat-primary",
      "button.mat-raised-button",
      "button.btn-primary",
      "#loginButton",
      "button:not([disabled])"
    ];
    for (const sel of btnSelectors) {
      try {
        const btns = document.querySelectorAll(sel);
        for (const btn of btns) {
          const text = (btn.textContent || '').trim().toLowerCase();
          if (isVisible(btn) && (text.includes('continue') || text.includes('login') || text.includes('sign in') || text.includes('submit'))) {
            console.log("Sera: Auto-clicking Continue/Login button:", text);
            setTimeout(() => btn.click(), 300);
            return true;
          }
        }
      } catch (e) {}
    }
    return false;
  }

  let panDone = false;
  let passDone = false;

  function checkDone() {
    if (panDone && passDone) {
      window.__seraFillActive = false;
      console.log("Sera: Autofill finished");
    }
  }

  // ---------- User ID / Email / PAN ----------
  function startPanPoll(callback) {
    let panAttempts = 0;
    const cleanUserSel = cleanSelector(usernameSelector);
    const userFallbacks = [
      "input[id*='userId']",
      "input[name*='userId']",
      "input[id$='userId']",
      "input[name$='userId']",
      "#userId",
      "input[name='userId']",
      "input[id*='txtUserId']",
      "input[name*='txtUserId']",
      "input[id*='USER_ID']",
      "#identifierId",
      "input[type='email']",
      "input[name='identifier']",
      "#panAdhaarUserId",
      "#username",
      "#userName",
      "input[name='pan']",
      "input[id*='pan']",
      "input[name='username']",
      "input[name='user']"
    ];

    const panInterval = setInterval(() => {
      panAttempts++;
      let userField = null;

      if (cleanUserSel) {
        userField = queryFirst(cleanUserSel);
      }

      if (!userField) {
        for (let f of userFallbacks) {
          try {
            const els = document.querySelectorAll(f);
            for (let el of els) {
              if (isVisible(el)) { userField = el; break; }
            }
            if (userField) break;
          } catch (e) {}
        }
      }
      
      if (userField && userid) {
        if (userField.value !== userid) {
          simulateType(userField, userid);
          console.log("Sera: Username/Email filled");
        } else {
          console.log("Sera: Username/Email already filled");
        }
        clearInterval(panInterval);
        panDone = true;
        if (callback) callback();
        checkDone();
      } else if (panAttempts >= 60) {
        clearInterval(panInterval);
        console.warn("Sera: Username/Email field not found after timeout");
        panDone = true;
        if (callback) callback();
        checkDone();
      }
    }, 500);
  }

  // ---------- Password ----------
  function startPasswordPoll() {
    let passAttempts = 0;
    const cleanPassSel = cleanSelector(passwordSelector);
    const passFallbacks = [
      "input[id*='psw']",
      "input[name*='psw']",
      "input[id$='psw']",
      "input[name$='psw']",
      "input[name='psw']",
      "#psw",
      "input[type='password']",
      "input[id*='password']",
      "input[name*='password']",
      "input[name='Passwd']",
      "#passwordInput",
      "#user_pass",
      "#password",
      "input[name='passwd']"
    ];

    const passInterval = setInterval(() => {
      passAttempts++;
      let passField = null;

      if (cleanPassSel) {
        passField = queryFirst(cleanPassSel);
      }

      if (!passField) {
        for (let f of passFallbacks) {
          try {
            const els = document.querySelectorAll(f);
            for (let el of els) {
              if (isVisible(el)) { passField = el; break; }
            }
            if (passField) break;
          } catch (e) {}
        }
      }
                        
      if (passField && password) {
        if (passField.disabled) { passField.removeAttribute('disabled'); passField.disabled = false; }
        simulateType(passField, password);
        console.log("Sera: Password filled");

        // Auto-click Continue/Login after a delay for Angular to process
        setTimeout(() => {
          autoClickContinue();
        }, 600);

        clearInterval(passInterval);
        passDone = true;
        checkDone();
      } else if (passAttempts >= 90) { // 45 seconds poll for 2-step logins
        clearInterval(passInterval);
        console.warn("Sera: Password field not found after timeout");
        passDone = true;
        checkDone();
      }
    }, 500);
  }

  if (extensionFlow === "single") {
    startPanPoll();
    startPasswordPoll();
  } else {
    // Double / sequential
    startPanPoll(() => {
      startPasswordPoll();
    });
  }
}

// Read-only probe run inside a tab: is a password box visible? (never reads a value)
function _seesPasswordBox() {
  return Array.from(document.querySelectorAll('input[type="password"]')).some(el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  });
}

function _loginUrlKey(u) {
  try {
    const p = new URL(u);
    return (p.origin + p.pathname).replace(/\/+$/, '').toLowerCase();
  } catch (_) { return ''; }
}

// Calls done(tab) with the first candidate tab that shows the login page, else done(null).
function _findLoginTab(candidates, url, done) {
  const wanted = _loginUrlKey(url);
  const byUrl = candidates.find(t => _loginUrlKey(t.url) === wanted);
  if (byUrl) { done(byUrl); return; }
  const rest = candidates.filter(t => t.id !== undefined && /^https?:/i.test(t.url || ''));
  const next = (i) => {
    if (i >= rest.length) { done(null); return; }
    let answered = false;
    const answer = (yes) => {
      if (answered) return;
      answered = true;
      if (yes) done(rest[i]); else next(i + 1);
    };
    try {
      chrome.scripting.executeScript({ target: { tabId: rest[i].id, allFrames: true }, func: _seesPasswordBox }, results => {
        if (chrome.runtime.lastError) { answer(false); return; }
        answer(Array.isArray(results) && results.some(r => r && r.result === true));
      });
    } catch (_) { answer(false); }
  };
  next(0);
}

// Opens the portal's login page and calls onReady(tabId) exactly once, after it has loaded.
// Reuses an open tab of the portal only when it shows the login page (decision D5); otherwise
// a new tab is opened, so work in other tabs of that portal is never navigated away.
function openPortalTab(url, onReady) {
  let hostname;
  try { hostname = new URL(url).hostname; } catch (_) { return; }
  const wantedUrl = String(url).split('#')[0].toLowerCase();

  const run = (tab, isNew) => {
    if (!tab || tab.id === undefined) return;
    chrome.windows.update(tab.windowId, { focused: true }, () => { if (chrome.runtime.lastError) {} });
    const alreadyOnUrl = !isNew && tab.url && tab.url.split('#')[0].toLowerCase() === wantedUrl;
    if (alreadyOnUrl && tab.status === 'complete') {
      chrome.tabs.update(tab.id, { active: true }, () => { if (chrome.runtime.lastError) {} });
      onReady(tab.id);
      return;
    }

    let done = false;
    // 'complete' only counts after this tab has started loading, so a stale 'complete'
    // from the page being replaced cannot trigger an early injection.
    let started = isNew || alreadyOnUrl;
    let timer = null;
    const cleanup = () => {
      done = true;
      clearTimeout(timer);
      try { chrome.tabs.onUpdated.removeListener(listener); } catch (_) {}
    };
    const listener = (tabId, info) => {
      if (tabId !== tab.id || done) return;
      if (info.status === 'loading') { started = true; return; }
      if (info.status === 'complete' && started) { cleanup(); onReady(tab.id); }
    };
    chrome.tabs.onUpdated.addListener(listener);
    timer = setTimeout(cleanup, 30000);

    if (isNew || alreadyOnUrl) return;
    chrome.tabs.update(tab.id, { url, active: true }, () => { if (chrome.runtime.lastError) {} });
  };

  chrome.tabs.query({}, tabs => {
    const candidates = (tabs || []).filter(t => {
      if (!t.url) return false;
      if (t.url.includes(hostname)) return true;
      return hostname.includes('tdscpc.gov.in') && t.url.includes('tdscpc.gov.in');
    });
    _findLoginTab(candidates, url, existing => {
      if (existing) { run(existing, false); return; }
      chrome.tabs.create({ url }, newTab => {
        if (chrome.runtime.lastError || !newTab) return;
        run(newTab, true);
      });
    });
  });
}

function handleAutofillTab(message) {
  openPortalTab(message.url, tabId => {
    injectFillScript(tabId, message.userid, message.password, message.username_selector, message.password_selector, message.extension_flow);
  });
}

function manualAssistWidget(userid, password, usernameSelector, passwordSelector, clientName, expiresMs) {
  const hostId = "sera-manual-assist-host";
  const old = document.getElementById(hostId);
  if (old) old.remove();
  const mecpOld = document.getElementById("sera-mecp-host");
  if (mecpOld) mecpOld.remove();

  const duration = expiresMs || 30000;
  const host = document.createElement("div");
  host.id = hostId;
  host.style.cssText = "position: fixed; top: 18px; right: 24px; z-index: 2147483647; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; pointer-events: auto;";

  const shadow = host.attachShadow({ mode: "closed" });
  const style = document.createElement("style");
  style.textContent = `
    :host { all: initial; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    .card {
      min-width: 320px;
      max-width: 420px;
      padding: 16px 18px;
      background: linear-gradient(145deg, #121815, #0B120E);
      border: 1.5px solid #2E9B5F;
      border-radius: 14px;
      box-shadow: none;
      color: #FFFFFF;
      transform: translateX(120%);
      opacity: 0;
      transition: transform 0.38s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.32s ease;
    }
    .header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 8px;
    }
    .badge {
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.7px;
      color: #4CF9B7;
      background: rgba(46, 155, 95, 0.22);
      border: 1px solid rgba(76, 249, 183, 0.35);
      padding: 3.5px 9px;
      border-radius: 6px;
      display: flex;
      align-items: center;
      gap: 5px;
    }
    .close-btn {
      background: transparent;
      border: none;
      color: #7E9388;
      font-size: 16px;
      cursor: pointer;
      line-height: 1;
      padding: 2px 6px;
      border-radius: 4px;
      transition: color 0.15s ease, background 0.15s ease;
    }
    .close-btn:hover {
      color: #FFFFFF;
      background: rgba(255, 255, 255, 0.1);
    }
    .client-title {
      font-size: 15.5px;
      font-weight: 800;
      color: #FFFFFF;
      line-height: 1.35;
      margin-bottom: 14px;
      word-break: break-word;
      letter-spacing: 0.2px;
    }
    .actions {
      display: flex;
      flex-direction: column;
      gap: 9px;
    }
    .btn {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      width: 100%;
      padding: 10.5px 14px;
      border: 1px solid #1E4D34;
      border-radius: 9px;
      background: #14281E;
      color: #E2F8EE;
      font: 700 13px -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      cursor: pointer;
      transition: all 0.18s ease;
      outline: none;
      user-select: none;
    }
    .btn:hover {
      background: #1B3D2B;
      border-color: #2E9B5F;
      color: #FFFFFF;
      box-shadow: 0 0 12px rgba(46, 155, 95, 0.3);
    }
    .btn:active {
      background: #23794A;
      transform: scale(0.985);
    }
    .btn.primary {
      background: #2E9B5F;
      border-color: #34B76D;
      color: #FFFFFF;
    }
    .btn.primary:hover {
      background: #34B76D;
    }
    .btn.done {
      background: #102B1E;
      border-color: #2E9B5F;
      color: #4CF9B7;
    }
    .timer-container {
      margin-top: 12px;
      height: 3.5px;
      background: rgba(255, 255, 255, 0.08);
      border-radius: 2px;
      overflow: hidden;
    }
    .timer-bar {
      height: 100%;
      width: 100%;
      background: #2E9B5F;
      transform-origin: left;
      transition: transform linear;
    }
    .flutter-guide {
      display: flex;
      flex-direction: column;
      gap: 9px;
      margin-top: 2px;
      margin-bottom: 2px;
    }
    .guide-step {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 11px 13px;
      background: rgba(255, 255, 255, 0.03);
      border: 1.5px solid rgba(255, 255, 255, 0.08);
      border-radius: 10px;
      transition: all 0.25s ease;
    }
    .guide-step.active {
      background: rgba(46, 155, 95, 0.16);
      border-color: #2E9B5F;
      box-shadow: 0 0 16px rgba(46, 155, 95, 0.3);
    }
    .guide-step.done {
      background: rgba(16, 43, 30, 0.55);
      border-color: #1E7E48;
      opacity: 0.88;
    }
    .step-num {
      width: 28px;
      height: 28px;
      border-radius: 50%;
      background: #18241E;
      border: 1.5px solid #334D3E;
      color: #8EB7A0;
      font-size: 13px;
      font-weight: 800;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      transition: all 0.2s ease;
    }
    .guide-step.active .step-num {
      background: #2E9B5F;
      border-color: #4CF9B7;
      color: #FFFFFF;
      box-shadow: 0 0 10px rgba(76, 249, 183, 0.45);
    }
    .guide-step.done .step-num {
      background: #102B1E;
      border-color: #4CF9B7;
      color: #4CF9B7;
      font-weight: 900;
    }
    .step-text {
      display: flex;
      flex-direction: column;
      gap: 2.5px;
    }
    .step-title {
      font-size: 13.5px;
      font-weight: 700;
      color: #FFFFFF;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .step-desc {
      font-size: 11.5px;
      color: #8BA295;
      line-height: 1.35;
    }
    .guide-step.active .step-desc {
      color: #BDEFD6;
    }
    .guide-step.done .step-desc {
      color: #5D806E;
    }
  `;

  shadow.appendChild(style);
  const card = document.createElement("div");
  card.className = "card";

  // Header
  const header = document.createElement("div");
  header.className = "header";

  const badge = document.createElement("div");
  badge.className = "badge";
  badge.innerHTML = "⚡ Sera Assist";

  const closeBtn = document.createElement("button");
  closeBtn.className = "close-btn";
  closeBtn.innerHTML = "✕";
  closeBtn.title = "Dismiss";

  header.append(badge, closeBtn);

  // Client Title
  const title = document.createElement("div");
  title.className = "client-title";
  title.textContent = clientName || "Client Profile";

  // Action Buttons
  const actions = document.createElement("div");
  actions.className = "actions";

  const uidBtn = document.createElement("button");
  uidBtn.className = "btn primary";
  uidBtn.innerHTML = "👤  Username";

  const passBtn = document.createElement("button");
  passBtn.className = "btn primary";
  passBtn.innerHTML = "🔑  Password";

  actions.append(uidBtn, passBtn);

  // Countdown timer bar
  const timerContainer = document.createElement("div");
  timerContainer.className = "timer-container";
  const timerBar = document.createElement("div");
  timerBar.className = "timer-bar";
  timerContainer.appendChild(timerBar);

  card.append(header, title, actions, timerContainer);
  shadow.appendChild(card);
  document.documentElement.appendChild(host);

  // Animate in
  setTimeout(() => {
    card.style.transform = "translateX(0)";
    card.style.opacity = "1";
    timerBar.style.transitionDuration = `${duration}ms`;
    timerBar.style.transform = "scaleX(0)";
  }, 30);

  function clean(sel) {
    return (sel || "").trim().replace(/\s+\[/g, "[").replace(/input\s+/g, "input");
  }

  function visible(el) {
    if (!el || el.type === "hidden") return false;
    try {
      const style = window.getComputedStyle(el);
      if (style.display === "none" || style.visibility === "hidden") return false;
      return true;
    } catch (_) {
      return true;
    }
  }

  function queryAll(selectorStr) {
    if (!selectorStr) return [];
    const results = [];
    const parts = selectorStr.split(',').map(s => s.trim()).filter(Boolean);
    for (const p of parts) {
      try {
        const els = document.querySelectorAll(p);
        for (const el of els) {
          if (visible(el) && !results.includes(el)) results.push(el);
        }
      } catch (_) {}
    }
    return results;
  }

  function findField(selector, fallbacks) {
    if (selector) {
      const matches = queryAll(clean(selector));
      if (matches.length > 0) return matches[0];
    }
    const fbs = Array.isArray(fallbacks) ? fallbacks : [];
    for (const sel of fbs) {
      const matches = queryAll(sel);
      if (matches.length > 0) return matches[0];
    }
    try {
      const active = document.activeElement;
      if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA") && visible(active)) {
        return active;
      }
    } catch (_) {}
    return null;
  }

  function isFlutterPage() {
    return !!(document.querySelector("flt-glass-pane") ||
              document.querySelector("flt-text-editing-host") ||
              document.querySelector("flutter-view") ||
              document.querySelector("[flt-renderer]") ||
              window.location.hostname.includes("tdscpc.gov.in") ||
              window.location.hostname.includes("traces"));
  }

  function getFlutterActiveInput() {
    try {
      const host = document.querySelector("flt-text-editing-host");
      if (host) {
        const inp = host.querySelector("input, textarea");
        if (inp) return inp;
      }
      const pane = document.querySelector("flt-glass-pane");
      if (pane && pane.shadowRoot) {
        const inp = pane.shadowRoot.querySelector("flt-text-editing-host input, flt-text-editing-host textarea");
        if (inp) return inp;
      }
    } catch (_) {}
    return null;
  }

  function execInsert(el, value) {
    try {
      el.focus();
      el.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, key: "a", code: "KeyA", ctrlKey: true }));
      document.execCommand("selectAll");
      const ok = document.execCommand("insertText", false, value);
      if (ok) return true;
    } catch (_) {}
    return false;
  }

  function fill(el, value) {
    if (!el || !value) return false;
    try {
      if (el.disabled) { el.removeAttribute("disabled"); el.disabled = false; }
      if (el.readOnly) { el.removeAttribute("readonly"); el.readOnly = false; }
      el.focus();
    } catch (_) {}
    try {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(el, value);
    } catch (_) {
      el.value = value;
    }
    el.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: value.slice(-1) }));
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true, key: value.slice(-1) }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
    return true;
  }

  function copyText(text) {
    if (!text) return;
    try {
      navigator.clipboard.writeText(text);
    } catch (_) {
      try {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
      } catch (_) {}
    }
  }

  function smartFill(value, selector, fallbacks) {
    const el = findField(selector, fallbacks);
    if (el && fill(el, value)) return "filled";

    const fltEl = getFlutterActiveInput();
    if (fltEl) {
      if (execInsert(fltEl, value)) return "filled";
      if (fill(fltEl, value)) return "filled";
    }

    if (isFlutterPage()) {
      copyText(value);
      return "flutter_no_focus";
    }

    copyText(value);
    return "copied";
  }

  const userFallbacks = [
    "input[id*='userId']", "input[name*='userId']", "input[id$='userId']", "input[name$='userId']",
    "#userId", "input[name='userId']", "input[id*='txtUserId']", "input[name*='txtUserId']", "input[id*='USER_ID']",
    "#identifierId", "input[type='email']", "#panAdhaarUserId", "#username", "#userName",
    "input[name='pan']", "input[id*='pan']", "input[name='tan']", "input[id*='tan']",
    "input[name='username']", "input[name='user']"
  ];

  const passFallbacks = [
    "input[id*='psw']", "input[name*='psw']", "input[id$='psw']", "input[name$='psw']",
    "input[name='psw']", "#psw", "input[type='password']", "input[id*='password']", "input[name*='password']",
    "input[name='Passwd']", "#password", "#passwordInput", "#user_pass", "input[name='passwd']"
  ];

  const isFlutter = isFlutterPage();

  // ── Flutter auto-fill via MutationObserver ─────────────────────────────
  let flutterObserver = null;
  let flutterFillStep = 0; // 0 = waiting for userID, 1 = waiting for password, 2 = done

  function fillFlutterInput(el, value) {
    try {
      el.focus();
      document.execCommand("selectAll");
      const ok = document.execCommand("insertText", false, value);
      if (ok) return true;
    } catch (_) {}
    try {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(el, value);
    } catch (_) { el.value = value; }
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return el.value === value;
  }

  function tabToNextField(el) {
    el.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, key: "Tab", code: "Tab", keyCode: 9 }));
    el.dispatchEvent(new KeyboardEvent("keyup",  { bubbles: true, cancelable: true, key: "Tab", code: "Tab", keyCode: 9 }));
  }

  let step1El = null, step2El = null, stepNum1 = null, stepNum2 = null, stepTitle1 = null, stepTitle2 = null, stepDesc2 = null;

  function updateFlutterUI(step) {
    if (!step1El || !step2El) return;
    if (step === 1) {
      step1El.className = "guide-step done";
      if (stepNum1) stepNum1.innerHTML = "✓";
      if (stepTitle1) stepTitle1.innerHTML = "✓  Username Typed";
      step2El.className = "guide-step active";
      if (stepTitle2) stepTitle2.innerHTML = "👉 Step 2: Click the Password box";
      if (stepDesc2) stepDesc2.innerHTML = "Now tap or click inside the Password box on the page!";
    } else if (step === 2) {
      step2El.className = "guide-step done";
      if (stepNum2) stepNum2.innerHTML = "✓";
      if (stepTitle2) stepTitle2.innerHTML = "✓  Password Typed";
    }
  }

  function startFlutterObserver() {
    if (flutterObserver) return;
    flutterFillStep = 0;

    const roots = [document];
    try {
      const pane = document.querySelector("flt-glass-pane");
      if (pane && pane.shadowRoot) roots.push(pane.shadowRoot);
    } catch (_) {}

    const onInput = (el) => {
      if (flutterFillStep === 0) {
        // Fill User ID
        const filled = fillFlutterInput(el, userid);
        if (filled) {
          flutterFillStep = 1;
          updateFlutterUI(1);
          setTimeout(() => tabToNextField(el), 80);
        }
      } else if (flutterFillStep === 1) {
        // Fill Password
        const filled = fillFlutterInput(el, password);
        if (filled) {
          flutterFillStep = 2;
          updateFlutterUI(2);
          stopFlutterObserver();
          setTimeout(dismiss, 400);
        }
      }
    };

    const observe = (root) => {
      const mo = new MutationObserver((mutations) => {
        for (const m of mutations) {
          for (const node of m.addedNodes) {
            if (node.nodeType !== 1) continue;
            if (node.tagName && (node.tagName === "INPUT" || node.tagName === "TEXTAREA")) {
              onInput(node);
            }
            const inp = node.querySelector && node.querySelector("input, textarea");
            if (inp) onInput(inp);
          }
        }
      });
      const host = root.querySelector ? root.querySelector("flt-text-editing-host") : null;
      if (host) {
        mo.observe(host, { childList: true, subtree: true });
        const existing = host.querySelector("input, textarea");
        if (existing) onInput(existing);
      } else {
        mo.observe(root.body || root, { childList: true, subtree: true });
      }
      return mo;
    };

    const observers = roots.map(observe);
    flutterObserver = { disconnect: () => observers.forEach(o => o.disconnect()) };
  }

  function stopFlutterObserver() {
    if (flutterObserver) { flutterObserver.disconnect(); flutterObserver = null; }
  }

  let dismiss = () => {
    card.style.transform = "translateX(120%)";
    card.style.opacity = "0";
    setTimeout(() => { if (host.isConnected) host.remove(); }, 380);
  };

  if (isFlutter) {
    // Hide buttons on Flutter sites as requested — replace with child-friendly step cards
    actions.style.display = "none";

    const guideContainer = document.createElement("div");
    guideContainer.className = "flutter-guide";

    // Step 1: Username
    step1El = document.createElement("div");
    step1El.className = "guide-step active";
    stepNum1 = document.createElement("div");
    stepNum1.className = "step-num";
    stepNum1.textContent = "1";
    const textWrap1 = document.createElement("div");
    textWrap1.className = "step-text";
    stepTitle1 = document.createElement("div");
    stepTitle1.className = "step-title";
    stepTitle1.textContent = "👉 Step 1: Click the Username box";
    const stepDesc1 = document.createElement("div");
    stepDesc1.className = "step-desc";
    stepDesc1.textContent = "Click or tap inside the User ID box on the page. Sera will type it automatically!";
    textWrap1.append(stepTitle1, stepDesc1);
    step1El.append(stepNum1, textWrap1);

    // Step 2: Password
    step2El = document.createElement("div");
    step2El.className = "guide-step";
    stepNum2 = document.createElement("div");
    stepNum2.className = "step-num";
    stepNum2.textContent = "2";
    const textWrap2 = document.createElement("div");
    textWrap2.className = "step-text";
    stepTitle2 = document.createElement("div");
    stepTitle2.className = "step-title";
    stepTitle2.textContent = "Step 2: Click the Password box";
    stepDesc2 = document.createElement("div");
    stepDesc2.className = "step-desc";
    stepDesc2.textContent = "Next, click inside the Password box on the page. Sera will type it for you!";
    textWrap2.append(stepTitle2, stepDesc2);
    step2El.append(stepNum2, textWrap2);

    guideContainer.append(step1El, step2El);
    card.insertBefore(guideContainer, timerContainer);

    // Start watching immediately
    startFlutterObserver();

    // Wrap dismiss to clean up observer
    const baseDismiss = dismiss;
    dismiss = () => { stopFlutterObserver(); baseDismiss(); };
  }

  function setBtn(btn, state, text) {
    if (state === "done") {
      btn.className = "btn done";
      btn.style.removeProperty("border-color");
      btn.style.removeProperty("color");
    } else if (state === "warn") {
      btn.className = "btn";
      btn.style.borderColor = "#E8A040";
      btn.style.color = "#F5C97A";
    } else {
      btn.className = "btn primary";
      btn.style.removeProperty("border-color");
      btn.style.removeProperty("color");
    }
    btn.innerHTML = text;
  }

  uidBtn.onclick = () => {
    const result = smartFill(userid, usernameSelector, userFallbacks);
    if (result === "filled") {
      setBtn(uidBtn, "done", "✓  Username Injected");
      setTimeout(() => setBtn(uidBtn, "", "👤  Username"), 2000);
    } else {
      setBtn(uidBtn, "done", "📋  Copied Username (Ctrl+V)");
      setTimeout(() => setBtn(uidBtn, "", "👤  Username"), 2500);
    }
  };

  passBtn.onclick = () => {
    const result = smartFill(password, passwordSelector, passFallbacks);
    if (result === "filled") {
      setBtn(passBtn, "done", "✓  Password Injected");
      setTimeout(dismiss, 400);
    } else {
      setBtn(passBtn, "done", "📋  Copied Password (Ctrl+V)");
      setTimeout(dismiss, 1200);
    }
  };

  closeBtn.onclick = dismiss;
  setTimeout(() => { if (host.isConnected) dismiss(); }, duration);
}

function handleManualAssistTab(message) {
  try { new URL(message.url); } catch (_) { return; }
  _passwordStore.remove(['mecpPayload']);
  _passwordStore.set({
    manualAssistPayload: { ...message, expiresAt: Date.now() + (5 * 60 * 1000) }
  });

  // Flutter web apps (e.g. TRACES) fire status="complete" when the HTML shell loads,
  // but Flutter itself bootstraps asynchronously after that. Give it time to render.
  const isFlutterUrl = /tdscpc\.gov\.in|traces\.gov\.in|flutter/i.test(message.url || "");
  const injectDelay = isFlutterUrl ? 3000 : 0;

  openPortalTab(message.url, tabId => setTimeout(() => injectManualAssist(tabId, message), injectDelay));
}

function injectManualAssist(tabId, message) {
  // Disarm SCA so it doesn't trigger on the same tab simultaneously as SMTI
  scaCoordinator.disarm("manual assist started");
  chrome.scripting.executeScript({ target:{tabId, allFrames: true}, func:manualAssistWidget,
    args:[message.userid, message.password, message.username_selector, message.password_selector,
      message.client_name || message.portal, 30000] })
    .then(() => console.log("Sera: Manual Assist widget injected"))
    .catch(err => console.error("Sera: Manual Assist injection failed", err));
}

function injectFillScript(tabId, userid, password, usernameSelector, passwordSelector, extensionFlow) {
  chrome.scripting.executeScript({
    target: { tabId: tabId, allFrames: true },
    func: fillCredentialsInPage,
    args: [userid, password, usernameSelector, passwordSelector, extensionFlow]
  }).then(() => console.log("Sera: fill script injected"))
    .catch(err => console.error("Sera: inject failed", err));
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  console.log("Sera background: received runtime message:", msg);
  if (msg.type === "CHECK_NATIVE_STATUS") {
    sendResponse({ connected: !!(ws && ws.readyState === WebSocket.OPEN), mode: "ws" });
    return true;
  }
  if (msg.type === "RECONNECT_NATIVE_HOST") {
    ensureConnected();
    sendResponse({ connected: !!(ws && ws.readyState === WebSocket.OPEN), mode: "ws" });
    return true;
  }
  if (msg.type === "SETTINGS_CHANGED_FROM_POPUP") {
    const s = msg.settings || {};
    sendToDesktop({
      type: "extension_settings_updated",
      sca_enabled: s.scaEnabled
    });
    sendResponse({ status: "ok" });
    return true;
  }
  if (msg.type === "TRIGGER_MANUAL_ASSIST_FOR_TAB") {
    if (msg.tabId) {
      _passwordStore.get(['manualAssistPayload', 'mecpPayload'], data => {
        const mecp = data.mecpPayload;
        if (mecp && mecp.expiresAt && mecp.expiresAt >= Date.now()) {
          injectMECP(msg.tabId, mecp);
          return;
        }
        const payload = data.manualAssistPayload;
        if (payload && payload.expiresAt && payload.expiresAt >= Date.now()) {
          injectManualAssist(msg.tabId, payload);
        }
      });
    }
    sendResponse({ status: "ok" });
    return true;
  }
});

// ---------------- MECP (Manual Extension Copy/Paste) Widget ----------------

function mecpWidget(userid, password, clientName, expiresMs) {
  const hostId = "sera-mecp-host";
  const old = document.getElementById(hostId);
  if (old) old.remove();
  const smtiOld = document.getElementById("sera-manual-assist-host");
  if (smtiOld) smtiOld.remove();

  const host = document.createElement("div");
  host.id = hostId;
  const shadow = host.attachShadow({ mode: "open" });

  const style = document.createElement("style");
  style.textContent = `
    .box {
      position: fixed; top: 18px; right: 18px; z-index: 2147483647;
      width: 310px; padding: 14px 16px; background: #161B22; border: 1.5px solid #30363D;
      border-radius: 10px; color: #F0F6FC; box-shadow: 0 10px 32px rgba(0,0,0,.5);
      font: 13px Segoe UI, Arial, sans-serif;
    }
    .header {
      display: flex; align-items: center; justify-content: space-between; gap: 8px;
      margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid #30363D;
    }
    .client-title {
      font-weight: 700; color: #7EE787; font-size: 13px; word-break: break-word; line-height: 1.3;
    }
    .close-btn {
      background: transparent; border: none; color: #8B949E; font-size: 18px;
      cursor: pointer; padding: 0 4px; line-height: 1; border-radius: 4px;
    }
    .close-btn:hover { color: #F0F6FC; background: #21262D; }
    .field-row {
      display: flex; align-items: center; justify-content: space-between; gap: 8px;
      margin-bottom: 10px; background: #0D1117; padding: 8px 10px; border-radius: 6px;
      border: 1px solid #21262D;
    }
    .field-label {
      font-size: 11px; color: #8B949E; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;
    }
    .field-value {
      font-family: monospace; font-size: 13px; color: #C9D1D9; letter-spacing: 2px; margin-top: 2px;
    }
    .copy-btn {
      display: flex; align-items: center; justify-content: center; gap: 4px;
      background: #238636; color: #FFFFFF; border: none; border-radius: 5px;
      padding: 6px 12px; font: 600 12px Segoe UI, Arial, sans-serif; cursor: pointer;
      transition: background 0.15s ease; flex-shrink: 0;
    }
    .copy-btn:hover { background: #2EA043; }
    .copy-btn.copied { background: #1F6FEB; }
    .eye-btn {
      background: transparent; border: 1px solid #30363D; color: #C9D1D9; border-radius: 5px;
      padding: 5px 7px; font-size: 13px; cursor: pointer; display: flex; align-items: center;
      justify-content: center; transition: background 0.15s ease, border-color 0.15s ease;
      flex-shrink: 0; min-width: 32px; height: 28px;
    }
    .eye-btn:hover { background: #21262D; border-color: #8B949E; }
    .toast {
      display: none; position: absolute; bottom: 6px; left: 16px; right: 16px;
      background: #238636; color: #FFF; padding: 5px 10px; border-radius: 4px;
      font-size: 11px; text-align: center; font-weight: 600;
    }
  `;

  shadow.appendChild(style);
  const box = document.createElement("div");
  box.className = "box";

  // Header
  const header = document.createElement("div");
  header.className = "header";
  const title = document.createElement("div");
  title.className = "client-title";
  title.textContent = clientName || "MECP - Client Credentials";
  const close = document.createElement("button");
  close.className = "close-btn";
  close.textContent = "×";
  header.append(title, close);

  // Helper function to create masked text
  function maskText(str) {
    if (!str) return "••••••••";
    if (str.length <= 3) return "•".repeat(str.length);
    return str.substring(0, 1) + "•".repeat(Math.max(4, str.length - 2)) + str.substring(str.length - 1);
  }

  // Toast banner
  const toast = document.createElement("div");
  toast.className = "toast";

  function showToast(msg) {
    toast.textContent = msg;
    toast.style.display = "block";
    setTimeout(() => { toast.style.display = "none"; }, 2500);
  }

  function copyCredential(val, label) {
    navigator.clipboard.writeText(val).then(() => {
      showToast(`${label} copied! Clipboard auto-clears in 45s.`);
      setTimeout(() => {
        navigator.clipboard.readText().then(current => {
          if (current === val) {
            navigator.clipboard.writeText("");
          }
        }).catch(() => {});
      }, 45000);
    }).catch(err => {
      const ta = document.createElement("textarea");
      ta.value = val;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      showToast(`${label} copied!`);
    });
  }

  // User ID Row
  const uidRow = document.createElement("div");
  uidRow.className = "field-row";
  const uidLeft = document.createElement("div");
  const uidLbl = document.createElement("div");
  uidLbl.className = "field-label";
  uidLbl.textContent = "User ID";
  const uidVal = document.createElement("div");
  uidVal.className = "field-value";
  uidVal.textContent = maskText(userid);
  uidLeft.append(uidLbl, uidVal);

  const uidCopy = document.createElement("button");
  uidCopy.className = "copy-btn";
  uidCopy.innerHTML = "📋 Copy";
  uidCopy.onclick = () => {
    copyCredential(userid, "User ID");
    uidCopy.classList.add("copied");
    uidCopy.innerHTML = "✓ Copied";
    setTimeout(() => {
      uidCopy.classList.remove("copied");
      uidCopy.innerHTML = "📋 Copy";
    }, 2000);
  };
  uidRow.append(uidLeft, uidCopy);

  // Password Row
  const passRow = document.createElement("div");
  passRow.className = "field-row";
  const passLeft = document.createElement("div");
  const passLbl = document.createElement("div");
  passLbl.className = "field-label";
  passLbl.textContent = "Password";
  const passVal = document.createElement("div");
  passVal.className = "field-value";
  let isPassRevealed = false;
  passVal.textContent = maskText(password);
  passLeft.append(passLbl, passVal);

  const passRight = document.createElement("div");
  passRight.style.display = "flex";
  passRight.style.alignItems = "center";
  passRight.style.gap = "6px";

  const eyeToggleBtn = document.createElement("button");
  eyeToggleBtn.className = "eye-btn";
  eyeToggleBtn.title = "Show / Hide Password";
  eyeToggleBtn.innerHTML = "👁️";
  eyeToggleBtn.onclick = () => {
    isPassRevealed = !isPassRevealed;
    passVal.textContent = isPassRevealed ? (password || "") : maskText(password);
    eyeToggleBtn.innerHTML = isPassRevealed ? "🙈" : "👁️";
  };

  const passCopy = document.createElement("button");
  passCopy.className = "copy-btn";
  passCopy.innerHTML = "📋 Copy";
  passCopy.onclick = () => {
    copyCredential(password, "Password");
    passCopy.classList.add("copied");
    passCopy.innerHTML = "✓ Copied";
    setTimeout(() => {
      passCopy.classList.remove("copied");
      passCopy.innerHTML = "📋 Copy";
    }, 2000);
  };

  passRight.append(eyeToggleBtn, passCopy);
  passRow.append(passLeft, passRight);

  box.append(header, uidRow, passRow, toast);
  shadow.appendChild(box);
  document.documentElement.appendChild(host);

  close.onclick = () => host.remove();
  setTimeout(() => { if (host.isConnected) host.remove(); }, expiresMs || 60000);
}

function handleMECPTab(message) {
  try { new URL(message.url); } catch (_) { return; }
  _passwordStore.remove(['manualAssistPayload']);
  _passwordStore.set({
    mecpPayload: { ...message, expiresAt: Date.now() + (5 * 60 * 1000) }
  });
  openPortalTab(message.url, tabId => injectMECP(tabId, message));
}

function injectMECP(tabId, message) {
  chrome.scripting.executeScript({
    target: { tabId },
    func: mecpWidget,
    args: [message.userid, message.password, message.client_name || message.portal, 60000]
  }).then(() => console.log("Sera: MECP widget injected"))
    .catch(err => console.error("Sera: MECP injection failed", err));
}

// ---------------- SCA page events -> coordinator ----------------
chrome.runtime.onMessage.addListener((req, sender) => {
  scaCoordinator.handleRuntimeMessage(req, sender);
});
