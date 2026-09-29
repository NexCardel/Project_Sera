// Production debug gate — set to true only during local development
const SERA_DEBUG = false;
// Scoped debug gate for diagnosing the SMTI (Manual Assist) push path only - flip back to
// false once the connection is confirmed working end to end. Logs to the service worker's own
// console: chrome://extensions -> Project Sera Companion -> "service worker" (Inspect views).
const SMTI_DEBUG = false;

// ---------------- WebSocket bridge to the desktop app (ui/ws_bridge.py) ----------------
// 2026-09-22: replaces Chrome Native Messaging (a registry key + host manifest per browser
// that routinely failed to register on a fresh PC) and the direct HTTP fallback to port 49152
// (which sat inside Windows' own dynamic port range, so another program could already be using
// it at random, and stalled every request ~2s). One WebSocket, tried on a short list of fixed
// ports below that range - nothing to install, nothing to register. See
// docs/app-extension-communication-report.md for the full comparison.
const WS_PORTS = [48765, 48766, 48767, 48768];
let ws = null;
let wsConnecting = false;
let wsPortIndex = 0;
let wsReconnectDelay = 1000;
const WS_RECONNECT_MAX_MS = 15000;
const _pendingWsRequests = new Map(); // "_id" -> {resolve, timer}

// ---------------- SCA (Sera Clipboard Assist) - protocol v2 ----------------
// All SCA logic is in sca/sca_coordinator.js, shared with the Firefox build. Arms carry no
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
      _liveAssistTabs().then(assistTabs => resolve({
        scaEnabled: d.scaEnabled,
        scaMode: d.scaMode,
        allowedDomains: d.allowedDomains || [],
        assistTabs,
      }));
    });
  }),
  executeScript: (details) => chrome.scripting.executeScript(details),
  sessionStore: (chrome.storage && chrome.storage.session) || null,
});

// Per-tab assist lock (B4): tabId -> {kind: 'smti'|'mecp', expiresAt} for each open SMTI/MECP
// card. SCA stays quiet in that tab only. Set on inject, cleared on dismiss/timeout/tab close;
// kept in _passwordStore (memory) so a service-worker restart does not drop it.
const ASSIST_LOCK_MS = 5 * 60 * 1000;
let _assistTabs = null;
function _withAssistTabs(change) {
  const apply = () => {
    const before = JSON.stringify(_assistTabs);
    const t = Date.now();
    Object.keys(_assistTabs).forEach(id => { if (!(_assistTabs[id].expiresAt > t)) delete _assistTabs[id]; });
    if (change) change(_assistTabs);
    if (JSON.stringify(_assistTabs) !== before) _passwordStore.set({ assistTabs: _assistTabs });
    return _assistTabs;
  };
  if (_assistTabs) return Promise.resolve(apply());
  return new Promise(resolve => _passwordStore.get(['assistTabs'], d => {
    if (!_assistTabs) _assistTabs = Object.assign({}, d && d.assistTabs);
    resolve(apply());
  }));
}
function _lockAssistTab(tabId, kind) {
  if (typeof tabId !== 'number') return;
  _withAssistTabs(tabs => { tabs[tabId] = { kind, expiresAt: Date.now() + ASSIST_LOCK_MS }; });
}
// kind given: only that assist's lock (an SMTI dismiss must not unlock a MECP card).
// tabId missing: every lock of that kind.
function _unlockAssistTab(tabId, kind) {
  _withAssistTabs(tabs => {
    Object.keys(tabs).forEach(id => {
      if ((typeof tabId !== 'number' || Number(id) === tabId) && (!kind || tabs[id].kind === kind)) delete tabs[id];
    });
  });
}
function _liveAssistTabs() {
  return _withAssistTabs().then(tabs => {
    const out = {};
    Object.keys(tabs).forEach(id => { out[id] = tabs[id].kind; });
    return out;
  });
}
chrome.tabs.onRemoved.addListener(tabId => _unlockAssistTab(tabId));

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
  if (SMTI_DEBUG) console.log(`[SMTI DEBUG] connectWS: attempting ws://127.0.0.1:${port}/`);
  let socket;
  try {
    socket = new WebSocket(`ws://127.0.0.1:${port}/`);
  } catch (e) {
    if (SMTI_DEBUG) console.warn(`[SMTI DEBUG] connectWS: new WebSocket() threw on port ${port}:`, e);
    wsConnecting = false;
    scheduleReconnect();
    return;
  }

  const connectTimeout = setTimeout(() => {
    if (socket.readyState !== WebSocket.OPEN) {
      if (SMTI_DEBUG) console.warn(`[SMTI DEBUG] connectWS: port ${port} did not open within 2.5s, closing.`);
      try { socket.close(); } catch (_) {}
    }
  }, 2500);

  socket.onopen = () => {
    clearTimeout(connectTimeout);
    ws = socket;
    wsConnecting = false;
    wsReconnectDelay = 1000;
    if (SERA_DEBUG) console.log(`Sera: WebSocket bridge connected on port ${port}`);
    if (SMTI_DEBUG) console.log(`[SMTI DEBUG] connectWS: OPEN on port ${port}`);
    // Notify any callers awaiting connection
    _wsOpenCallbacks.forEach(cb => { try { cb(); } catch (_) {} });
    _wsOpenCallbacks.length = 0;
    syncSettingsFromDesktop();
  };
  socket.onmessage = (event) => {
    let message;
    try { message = JSON.parse(event.data); } catch (_) {
      if (SMTI_DEBUG) console.warn('[SMTI DEBUG] onmessage: non-JSON payload:', event.data);
      return;
    }
    if (SMTI_DEBUG) console.log('[SMTI DEBUG] onmessage: received', message.type, message.mode ? `(mode=${message.mode})` : '');
    handleDesktopMessage(message);
  };
  socket.onerror = (event) => {
    if (SMTI_DEBUG) console.warn(`[SMTI DEBUG] connectWS: socket error on port ${port}:`, event);
  };
  socket.onclose = (event) => {
    clearTimeout(connectTimeout);
    wsConnecting = false;
    if (ws === socket) ws = null;
    if (SMTI_DEBUG) console.warn(`[SMTI DEBUG] connectWS: CLOSED port ${port} (code=${event && event.code}, reason=${event && event.reason}), retrying in ${wsReconnectDelay}ms`);
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
 * Callers that need to send a message but may be running while the bridge is reconnecting
 * should await this before sending - it prevents silent message loss during service-worker
 * wake-up and port cycling.
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
  // Never log the message itself: autofill / SMTI / MECP messages carry a plain-text password.
  if (SERA_DEBUG) console.log("Received from Sera desktop:", message.type);

  // A reply to a message this background page itself sent (a generic ack, or a settings_response).
  if ((message.type === '_ack' || message.type === 'settings_response') && message._id && _pendingWsRequests.has(message._id)) {
    const pending = _pendingWsRequests.get(message._id);
    clearTimeout(pending.timer);
    _pendingWsRequests.delete(message._id);
    pending.resolve(message.type === 'settings_response' ? message : true);
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
    if (Number(message.clipboard_clear_seconds) > 0) {
      storageObj.clipboardClearSeconds = Number(message.clipboard_clear_seconds);
    }
    if (allowedDomains && allowedDomains.length > 0) {
      storageObj.allowedDomains = allowedDomains;
    }
    if (message.registered_pans && Array.isArray(message.registered_pans)) {
      storageObj.registeredPans = message.registered_pans;
    }
    if (message.scc_settings && typeof message.scc_settings === 'object') {
      storageObj.sccSettings = message.scc_settings;
      if (message.scc_settings.enabled !== undefined) {
        storageObj.sccEnabled = !!message.scc_settings.enabled;
      }
    }
    chrome.storage.local.set(storageObj);
  }
}

async function requestSettingsOverWS() {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    const connected = await waitForConnection(5000);
    if (!connected) return null;
  }
  return new Promise((resolve) => {
    const id = _wsMessageId();
    try {
      ws.send(JSON.stringify({ type: 'request_settings', _id: id }));
    } catch (_) {
      resolve(null);
      return;
    }
    const timer = setTimeout(() => { _pendingWsRequests.delete(id); resolve(null); }, 4000);
    _pendingWsRequests.set(id, { resolve, timer });
  });
}


async function syncSettingsFromDesktop() {
  try {
    const data = await requestSettingsOverWS();
    if (!data || data.status !== 'ok') return null;

    const storageObj = {};
    if (data.registered_pans && Array.isArray(data.registered_pans)) {
      storageObj.registeredPans = data.registered_pans;
    }
    if (data.scc_settings && typeof data.scc_settings === 'object') {
      storageObj.sccSettings = data.scc_settings;
      if (data.scc_settings.enabled !== undefined) {
        storageObj.sccEnabled = !!data.scc_settings.enabled;
      }
    }
    if (data.allowed_services && Array.isArray(data.allowed_services)) {
      storageObj.allowedServices = data.allowed_services;
    }
    if (Array.isArray(data.allowed_domains) && data.allowed_domains.length > 0) {
      storageObj.allowedDomains = data.allowed_domains;
    }
    if (data.sca_mode) {
      storageObj.scaMode = data.sca_mode;
    }
    if (data.sca_enabled !== undefined) {
      storageObj.scaEnabled = !!data.sca_enabled;
    }
    if (Number(data.clipboard_clear_seconds) > 0) {
      storageObj.clipboardClearSeconds = Number(data.clipboard_clear_seconds);
    }
    if (Object.keys(storageObj).length > 0) {
      await chrome.storage.local.set(storageObj);
      if (SERA_DEBUG) console.log("⚡ Sera background: settings synced from desktop:", storageObj);
    }
    return storageObj;
  } catch (err) {
    if (SERA_DEBUG) console.warn("Sera background: syncSettingsFromDesktop failed:", err);
    return null;
  }
}

// Only syncs settings when the socket is already open. While it is closed this must not call
// syncSettingsFromDesktop(): that awaits waitForConnection(), which calls back into here, and
// every step runs synchronously up to its first await - so it recursed until the stack
// overflowed, leaving one queued on-open callback per level (~4,000). When the app's socket
// then opened, each fired its own request_settings, and the app built thousands of settings
// payloads back to back on its GUI thread (frozen window, high CPU). socket.onopen syncs once
// the connection is actually up.
function ensureConnected() {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    connectWS();
    return;
  }
  syncSettingsFromDesktop();
}

// Ensure settings are synced on service worker boot, browser startup, or extension reload
syncSettingsFromDesktop();
if (chrome.runtime && chrome.runtime.onStartup) {
  chrome.runtime.onStartup.addListener(() => {
    syncSettingsFromDesktop();
  });
}
if (chrome.runtime && chrome.runtime.onInstalled) {
  chrome.runtime.onInstalled.addListener(() => {
    syncSettingsFromDesktop();
  });
}

// Sends one message over the bridge. With waitForAck=true, resolves only once the app has
// confirmed receipt (a generic "_ack" reply) - the same guarantee an HTTP 200 used to give.
// If the socket is not yet OPEN (e.g. service worker just woke up mid-reconnect), this waits
// up to 5 s for the connection to establish rather than silently dropping the message.
async function sendToDesktop(msg, waitForAck = false) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    const connected = await waitForConnection(5000);
    if (!connected) {
      if (SMTI_DEBUG) console.warn('[SMTI DEBUG] sendToDesktop: gave up waiting for connection, dropping', msg.type);
      return false;
    }
  }
  const id = _wsMessageId();
  try {
    ws.send(JSON.stringify({ ...msg, _id: id }));
  } catch (e) {
    if (SERA_DEBUG) console.warn("Sera background: WebSocket send failed:", e);
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

// SCA scope (decision D7): login.js + sca_adapters.js (paste / typing watching) run only on the
// approved portal hosts, registered here instead of in the manifest's content_scripts. Nothing on
// other websites watches typing. host_permissions stay broad on purpose: Autofill, SMTI and MECP
// inject on demand into custom services' pages and need them.
const SCA_SCRIPT_ID = 'sera-sca-login';
const SCA_SCRIPT_FILES = ['content_scripts/sca_adapters.js', 'content_scripts/login.js'];
const SCA_BASE_DOMAINS = ['incometax.gov.in', 'incometaxindiaefiling.gov.in', 'gst.gov.in', 'tdscpc.gov.in', 'mca.gov.in'];
let _scaScopeChain = Promise.resolve();
let _scaScopeKey = null;
let _scaLegacyHandle = null;

function scaScopeMatches(domains) {
  const hosts = new Set(SCA_BASE_DOMAINS);
  for (const d of domains || []) {
    const h = String(d || '').trim().toLowerCase();
    if (/^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$/.test(h)) hosts.add(h);
  }
  const matches = [];
  for (const h of hosts) matches.push(`https://${h}/*`, `https://*.${h}/*`);
  return matches;
}

async function registerScaScripts(domains) {
  const matches = scaScopeMatches(domains);
  const key = matches.join('|');
  if (key === _scaScopeKey) return;
  const scripting = chrome.scripting;
  if (scripting && scripting.registerContentScripts) {
    // Registrations persist across service-worker restarts: keep an identical one rather than
    // leaving a moment with no SCA script on every wake-up.
    if (_scaScopeKey === null && scripting.getRegisteredContentScripts) {
      try {
        const [cur] = await scripting.getRegisteredContentScripts({ ids: [SCA_SCRIPT_ID] });
        if (cur && (cur.matches || []).join('|') === key && (cur.js || []).join('|') === SCA_SCRIPT_FILES.join('|')) {
          _scaScopeKey = key;
          return;
        }
      } catch (_) {}
    }
    try { await scripting.unregisterContentScripts({ ids: [SCA_SCRIPT_ID] }); } catch (_) {}
    await scripting.registerContentScripts([{
      id: SCA_SCRIPT_ID, matches, js: SCA_SCRIPT_FILES, runAt: 'document_end', allFrames: true,
    }]);
  } else if (typeof browser !== 'undefined' && browser.contentScripts && browser.contentScripts.register) {
    if (_scaLegacyHandle) { try { await _scaLegacyHandle.unregister(); } catch (_) {} _scaLegacyHandle = null; }
    _scaLegacyHandle = await browser.contentScripts.register({
      matches, js: SCA_SCRIPT_FILES.map(file => ({ file })), runAt: 'document_end', allFrames: true,
    });
  } else {
    return;
  }
  _scaScopeKey = key;
}

function applyScaScope() {
  _scaScopeChain = _scaScopeChain
    .then(() => chrome.storage.local.get(['allowedDomains']))
    .then(d => registerScaScripts(d && d.allowedDomains))
    .catch(err => console.warn('Sera background: SCA scope registration failed:', err));
}

applyScaScope();
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes.allowedDomains) applyScaScope();
});

if (SERA_DEBUG) console.log('Sera: background.js module loaded, registering listeners.');

let sccActiveAttempt = null;
_passwordStore.get(['sccActiveAttempt'], d => {
  if (d && d.sccActiveAttempt) sccActiveAttempt = d.sccActiveAttempt;
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (!tab.url || tab.url.startsWith('chrome://') || tab.url.startsWith('about:') || tab.url.startsWith('chrome-extension://')) return;
  if (changeInfo.status === 'complete') {
    // Show Manual Assist again after a reload (e.g. invalid password) in the tab SMTI opened only.
    maybeReinjectManualAssist(tabId);
  }

  // ── SCC Webpage Link Mutation Observer (Income Tax / ITR Only) ───────────
  _passwordStore.get(['sccActiveAttempt'], (data) => {
    const attempt = data.sccActiveAttempt || sccActiveAttempt;
    if (!attempt || !attempt.password) return;

    const now = Date.now();
    if (now - (attempt.timestamp || 0) > 10 * 60 * 1000) {
      sccActiveAttempt = null;
      _passwordStore.remove(['sccActiveAttempt']);
      return;
    }

    if (attempt.tabId && attempt.tabId !== tabId) return;

    const curUrl = changeInfo.url || (tab && tab.url) || '';
    if (!curUrl) return;

    const initUrl = attempt.initial_url || '';
    const isItrDomain = curUrl.includes('incometax.gov.in') || (initUrl && initUrl.includes('incometax.gov.in'));
    if (!isItrDomain) return;

    const isLoginUrl = curUrl.toLowerCase().includes('/login') || curUrl.toLowerCase().includes('/auth');
    const urlChanged = initUrl ? (curUrl !== initUrl) : true;
    const isPostLoginRoute = urlChanged && !isLoginUrl;

    if (isPostLoginRoute) {
      if (SERA_DEBUG) console.log(`⚡ Sera SCC: Link mutation observed away from login (${initUrl} -> ${curUrl})`);
      sccActiveAttempt = null;
      _passwordStore.remove(['sccActiveAttempt', 'mecpPayload']);

      try {
        chrome.scripting.executeScript({
          target: { tabId },
          func: () => {
            const el = document.getElementById("sera-mecp-host");
            if (el) el.remove();
          }
        }).catch(() => {});
      } catch (_) {}

      sendToDesktop({
        type: "scc_password_verified",
        client_id: attempt.client_id,
        service_id: attempt.service_id,
        userid: attempt.userid || attempt.pan || "",
        pan: attempt.pan || attempt.userid || "",
        password: attempt.password,
        combo_label: attempt.combo_label,
        portal: "Income Tax",
        destination_url: curUrl,
        timestamp: new Date().toISOString()
      }, false);
    }
  });
});

// Fill function injected into the page
function fillCredentialsInPage(userid, password, usernameSelector, passwordSelector, extensionFlow) {
  // Runs in the page: the background's SERA_DEBUG does not exist here (login.js, which also
  // declares one, runs only on the SCA portals since D7).
  const SERA_DEBUG = false;
  if (window.__seraFillActive) return; // prevent duplicate runs
  // sera_dom.js (shared visibility rule) is injected just before this function; without it, fill nothing.
  if (!window.__seraDom) return;
  const isVisible = window.__seraDom.isVisible;
  window.__seraFillActive = true;
  if (SERA_DEBUG) console.log("Sera: fillCredentialsInPage started, flow:", extensionFlow);

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

    // Position cursor cleanly at the end without leaving text selected
    try {
      const len = (value || "").length;
      if (typeof el.setSelectionRange === "function") {
        el.setSelectionRange(len, len);
      }
    } catch (_) {}
  }

  let panDone = false;
  let passDone = false;

  function checkDone() {
    if (panDone && passDone) {
      window.__seraFillActive = false;
      if (SERA_DEBUG) console.log("Sera: Autofill finished");
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
          if (SERA_DEBUG) console.log("Sera: Username/Email filled");
        } else {
          if (SERA_DEBUG) console.log("Sera: Username/Email already filled");
        }
        clearInterval(panInterval);
        panDone = true;
        if (callback) callback();
        checkDone();
      } else if (panAttempts >= 60) {
        clearInterval(panInterval);
        if (SERA_DEBUG) console.warn("Sera: Username/Email field not found after timeout");
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
        if (SERA_DEBUG) console.log("Sera: Password filled");

        clearInterval(passInterval);
        passDone = true;
        checkDone();
      } else if (passAttempts >= 90) { // 45 seconds poll for 2-step logins
        clearInterval(passInterval);
        if (SERA_DEBUG) console.warn("Sera: Password field not found after timeout");
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
    // Match on the tab's own host (or a subdomain of it), never a substring of its URL: a search
    // result or redirect link that merely mentions the portal is not a portal tab.
    const onHost = (h, base) => h === base || h.endsWith('.' + base);
    const candidates = (tabs || []).filter(t => {
      let h;
      try { h = new URL(t.url).hostname; } catch (_) { return false; }
      if (onHost(h, hostname)) return true;
      return onHost(hostname, 'tdscpc.gov.in') && onHost(h, 'tdscpc.gov.in');
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

function manualAssistWidget(userid, password, usernameSelector, passwordSelector, clientName, expiresMs, clearSeconds) {
  try {
    if (window.self !== window.top) return;
  } catch (_) {
    return;
  }

  // sera_dom.js (shared visibility + field rules) is injected just before this function.
  const seraDom = window.__seraDom;
  if (!seraDom) return;

  const hostId = "sera-manual-assist-host";
  const old = document.getElementById(hostId);
  if (old) {
    const curCid = old.getAttribute("data-client-id");
    if (curCid === String(userid)) {
      // Widget is already active and displayed on this page
      return;
    }
    old.remove();
  }
  const mecpOld = document.getElementById("sera-mecp-host");
  if (mecpOld) mecpOld.remove();

  const duration = expiresMs || 30000;
  const host = document.createElement("div");
  host.id = hostId;
  host.setAttribute("data-client-id", String(userid));
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

  let dismiss = () => {
    if (timerTimeout) clearTimeout(timerTimeout);
    try {
      if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.session) {
        chrome.storage.session.remove(['manualAssistPayload']);
      }
      if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
        chrome.runtime.sendMessage({ type: "MANUAL_ASSIST_CLEAR" });
      }
    } catch (_) {}
    card.style.transform = "translateX(120%)";
    card.style.opacity = "0";
    setTimeout(() => { if (host.isConnected) host.remove(); }, 380);
  };

  function setBtn(btn, state, text) {
    if (!btn) return;
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

  // Countdown timer bar
  const timerContainer = document.createElement("div");
  timerContainer.className = "timer-container";
  const timerBar = document.createElement("div");
  timerBar.className = "timer-bar";
  timerContainer.appendChild(timerBar);

  let timerTimeout = null;
  let timerStartTime = 0;
  let remainingMs = duration;
  let isTimerPaused = false;

  function startCountdown() {
    if (timerTimeout) clearTimeout(timerTimeout);
    timerStartTime = Date.now();
    isTimerPaused = false;
    timerBar.style.transition = `transform ${remainingMs}ms linear`;
    timerBar.style.transform = "scaleX(0)";
    timerTimeout = setTimeout(() => {
      if (host.isConnected) dismiss();
    }, remainingMs);
  }

  function pauseTimer() {
    if (isTimerPaused || !timerTimeout) return;
    isTimerPaused = true;
    clearTimeout(timerTimeout);
    timerTimeout = null;
    const elapsed = Date.now() - timerStartTime;
    remainingMs = Math.max(0, remainingMs - elapsed);
    try {
      const computed = window.getComputedStyle(timerBar);
      const curMatrix = computed.transform;
      timerBar.style.transition = "none";
      timerBar.style.transform = curMatrix;
    } catch (_) {}
  }

  function resumeTimer() {
    if (!isTimerPaused) return;
    if (remainingMs <= 500) {
      dismiss();
      return;
    }
    startCountdown();
  }

  function resetTimer() {
    if (timerTimeout) clearTimeout(timerTimeout);
    remainingMs = duration;
    isTimerPaused = false;
    timerBar.style.transition = "none";
    timerBar.style.transform = "scaleX(1)";
    void timerBar.offsetWidth; // force reflow
    startCountdown();
  }

  card.addEventListener("mouseenter", pauseTimer);
  card.addEventListener("mouseleave", resumeTimer);

  const closeBtn = document.createElement("button");
  closeBtn.className = "close-btn";
  closeBtn.innerHTML = "✕";
  closeBtn.title = "Dismiss";
  closeBtn.onclick = dismiss;

  header.append(badge, closeBtn);

  // Client Title
  const title = document.createElement("div");
  title.className = "client-title";
  title.textContent = clientName || "Client Profile";

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

  // Action Buttons
  const actions = document.createElement("div");
  actions.className = "actions";

  const uidBtn = document.createElement("button");
  uidBtn.className = "btn primary";
  uidBtn.innerHTML = "👤  Username";
  uidBtn.onclick = () => {
    resetTimer();
    const result = smartFill(userid, "user", usernameSelector, userFallbacks, passwordSelector);
    if (result === "filled") {
      setBtn(uidBtn, "done", "✓  Username Injected");
      setTimeout(() => setBtn(uidBtn, "", "👤  Username"), 2000);
    } else {
      setBtn(uidBtn, "done", "📋  Copied Username (Ctrl+V)");
      setTimeout(() => setBtn(uidBtn, "", "👤  Username"), 2500);
    }
  };

  const passBtn = document.createElement("button");
  passBtn.className = "btn primary";
  passBtn.innerHTML = "🔑  Password";
  passBtn.onclick = () => {
    resetTimer();
    const result = smartFill(password, "pass", passwordSelector, passFallbacks);
    if (result === "filled") {
      dismiss();
    } else {
      setBtn(passBtn, "done", "📋  Copied Password (Ctrl+V)");
      setTimeout(dismiss, 1200);
    }
  };

  actions.append(uidBtn, passBtn);

  card.append(header, title, actions, timerContainer);
  shadow.appendChild(card);
  document.documentElement.appendChild(host);

  // Animate in
  setTimeout(() => {
    card.style.transform = "translateX(0)";
    card.style.opacity = "1";
    startCountdown();
  }, 30);

  // kind is "user" or "pass"; otherSelector is the other field's configured selector.
  function findField(kind, selector, fallbacks, otherSelector) {
    return seraDom.findField(document, kind, selector, fallbacks, otherSelector);
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

  function fill(el, value, refind) {
    if (!el || !value) return false;

    const applyValue = (targetEl, val) => {
      if (!targetEl) return false;
      try {
        if (targetEl.disabled) { targetEl.removeAttribute("disabled"); targetEl.disabled = false; }
        if (targetEl.readOnly) { targetEl.removeAttribute("readonly"); targetEl.readOnly = false; }
        targetEl.focus();
      } catch (_) {}

      let replaced = false;
      // 1. Native browser replacement via execCommand: cleanly replaces any existing combo
      try {
        if (typeof targetEl.select === "function") {
          targetEl.select();
        }
        replaced = document.execCommand("insertText", false, val);
      } catch (_) {}

      // 2. Direct descriptor setter fallback if execCommand was not supported or didn't update value
      if (!replaced || targetEl.value !== val) {
        try {
          const proto = window.HTMLInputElement ? window.HTMLInputElement.prototype : Object.getPrototypeOf(targetEl);
          const desc = Object.getOwnPropertyDescriptor(proto, "value");
          if (desc && desc.set) {
            desc.set.call(targetEl, val);
          } else {
            targetEl.value = val;
          }
        } catch (_) {
          targetEl.value = val;
        }

        try {
          targetEl.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: val }));
          targetEl.dispatchEvent(new Event("input", { bubbles: true }));
          targetEl.dispatchEvent(new Event("change", { bubbles: true }));
        } catch (_) {}
      }

      // 3. Keep cursor cleanly at the end without leaving text highlighted
      try {
        const len = (val || "").length;
        if (typeof targetEl.setSelectionRange === "function") {
          targetEl.setSelectionRange(len, len);
        }
      } catch (_) {}

      return true;
    };

    applyValue(el, value);

    // Asynchronous re-sync: guards against Angular change detection resets
    // (e.g. when mat-checkbox is clicked or an invalid attempt is dismissed and Angular enables the control on the next tick)
    setTimeout(() => {
      try {
        const freshEl = (refind && refind()) || el;
        if (freshEl && freshEl.value !== value) {
          applyValue(freshEl, value);
        }
      } catch (_) {}
    }, 60);

    setTimeout(() => {
      try {
        const freshEl = (refind && refind()) || el;
        if (freshEl && freshEl.value !== value) {
          applyValue(freshEl, value);
        }
      } catch (_) {}
    }, 180);

    return true;
  }

  function copyText(text, secret) {
    if (!text) return;
    if (secret) seraDom.scheduleClipboardClear(text, clearSeconds);
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

  // A Username with no visible non-password input is copied, never typed into the password box.
  function smartFill(value, kind, selector, fallbacks, otherSelector) {
    const refind = selector ? () => findField(kind, selector, fallbacks, otherSelector) : null;
    const el = findField(kind, selector, fallbacks, otherSelector);
    if (el && fill(el, value, refind)) return "filled";

    const fltEl = getFlutterActiveInput();
    if (fltEl) {
      if (execInsert(fltEl, value)) return "filled";
      if (fill(fltEl, value, refind)) return "filled";
    }

    const secret = kind === "pass";
    if (isFlutterPage()) {
      copyText(value, secret);
      return "flutter_no_focus";
    }

    copyText(value, secret);
    return "copied";
  }

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
          try {
            if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.session) {
              chrome.storage.session.remove(['manualAssistPayload']);
            }
            if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
              chrome.runtime.sendMessage({ type: "MANUAL_ASSIST_CLEAR" });
            }
          } catch (_) {}
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
}

const SERA_DOM_FILE = 'content_scripts/sera_dom.js';

// Injects the shared DOM rules, then the function, into the same target.
function injectWithDom(target, func, args) {
  return chrome.scripting.executeScript({ target, files: [SERA_DOM_FILE] })
    .then(() => chrome.scripting.executeScript({ target, func, args }));
}

// Seconds after which a copied password is wiped from the clipboard (the desktop's setting; 30 until it syncs).
function clipboardClearSeconds() {
  return new Promise(resolve => {
    try {
      chrome.storage.local.get(['clipboardClearSeconds'], d => {
        const n = Number(d && d.clipboardClearSeconds);
        resolve(n > 0 ? n : 30);
      });
    } catch (_) { resolve(30); }
  });
}

// The one tab SMTI opened (or the staff member pressed it in). Only that tab is ever re-injected.
function _rememberSmtiTab(tabId) {
  _passwordStore.set({ smtiTabIds: [tabId] });
}

// After a page load in SMTI's tab: show the widget again only while the payload is live and
// the page shows a login form (a password box or the configured username field).
function maybeReinjectManualAssist(tabId) {
  _passwordStore.get(['manualAssistPayload', 'smtiTabIds'], data => {
    const p = data.manualAssistPayload;
    if (!p || !(p.expiresAt > Date.now())) return;
    if (!Array.isArray(data.smtiTabIds) || !data.smtiTabIds.includes(tabId)) return;
    setTimeout(() => {
      injectWithDom({ tabId }, (sel) => window.__seraDom.hasLoginForm(document, sel), [p.username_selector])
        .then(results => {
          if (results && results.some(r => r && r.result === true)) injectManualAssist(tabId, p);
        })
        .catch(() => {});
    }, 700);
  });
}

function handleManualAssistTab(message) {
  try { new URL(message.url); } catch (_) { return; }
  // A previous client's SMTI tab must not be re-injected with this client's payload.
  _passwordStore.remove(['mecpPayload', 'smtiTabIds']);
  _passwordStore.set({
    manualAssistPayload: { ...message, expiresAt: Date.now() + (5 * 60 * 1000) }
  });

  // Flutter web apps (e.g. TRACES) fire status="complete" when the HTML shell loads,
  // but Flutter itself bootstraps asynchronously after that. Give it time to render.
  const isFlutterUrl = /tdscpc\.gov\.in|traces\.gov\.in|flutter/i.test(message.url || "");
  const injectDelay = isFlutterUrl ? 3000 : 0;

  openPortalTab(message.url, tabId => {
    _rememberSmtiTab(tabId);
    setTimeout(() => injectManualAssist(tabId, message, true), injectDelay);
  });
}

const _lastManualAssistInject = {};
function injectManualAssist(tabId, message, force = false) {
  if (!tabId) {
    if (SMTI_DEBUG) console.warn('[SMTI DEBUG] injectManualAssist: no tabId, aborting');
    return;
  }
  const now = Date.now();
  if (!force && _lastManualAssistInject[tabId] && (now - _lastManualAssistInject[tabId]) < 1000) {
    if (SMTI_DEBUG) console.log(`[SMTI DEBUG] injectManualAssist: skipped, tab ${tabId} was injected <1s ago`);
    return;
  }
  _lastManualAssistInject[tabId] = now;
  _lockAssistTab(tabId, 'smti');

  if (SMTI_DEBUG) console.log(`[SMTI DEBUG] injectManualAssist: injecting into tab ${tabId}`, {
    hasUserid: !!message.userid, hasPassword: !!message.password,
    username_selector: message.username_selector, password_selector: message.password_selector
  });

  clipboardClearSeconds()
    .then(clearSecs => injectWithDom({ tabId }, manualAssistWidget,
      [message.userid, message.password, message.username_selector, message.password_selector,
        message.client_name || message.portal, 30000, clearSecs]))
    .then(() => { if (SMTI_DEBUG) console.log(`[SMTI DEBUG] injectManualAssist: widget injected OK into tab ${tabId}`); })
    .catch(err => console.error(`[SMTI DEBUG] injectManualAssist: executeScript FAILED on tab ${tabId} (page may block scripting, e.g. chrome:// or a PDF viewer):`, err));
}

function injectFillScript(tabId, userid, password, usernameSelector, passwordSelector, extensionFlow) {
  injectWithDom({ tabId: tabId, allFrames: true }, fillCredentialsInPage,
    [userid, password, usernameSelector, passwordSelector, extensionFlow]
  ).then(() => console.log("Sera: fill script injected"))
    .catch(err => console.error("Sera: inject failed", err));
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (SERA_DEBUG) console.log("Sera background: received runtime message:", msg.type);
  if (msg.type === "MANUAL_ASSIST_CLEAR" || msg.type === "MANUAL_ASSIST_DONE" || msg.type === "MANUAL_ASSIST_DISMISSED") {
    _passwordStore.remove(['manualAssistPayload', 'smtiTabIds']);
    _unlockAssistTab(sender && sender.tab ? sender.tab.id : null, 'smti');
    sendResponse({ ok: true });
    return true;
  }
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
          if (SMTI_DEBUG) console.log('[SMTI DEBUG] TRIGGER_MANUAL_ASSIST_FOR_TAB: found active mecpPayload, injecting MECP');
          injectMECP(msg.tabId, mecp);
          return;
        }
        const payload = data.manualAssistPayload;
        if (payload && payload.expiresAt && payload.expiresAt >= Date.now()) {
          if (SMTI_DEBUG) console.log('[SMTI DEBUG] TRIGGER_MANUAL_ASSIST_FOR_TAB: found active manualAssistPayload, injecting');
          _rememberSmtiTab(msg.tabId);
          injectManualAssist(msg.tabId, payload);
        } else if (SMTI_DEBUG) {
          console.warn('[SMTI DEBUG] TRIGGER_MANUAL_ASSIST_FOR_TAB: no active manualAssistPayload/mecpPayload in storage for this tab');
        }
      });
    }
    sendResponse({ status: "ok" });
    return true;
  }
  if ((msg.type === "SCC_PASSWORD_INJECTED" || msg.type === "SCC_PASSWORD_COPIED") && msg.payload) {
    const tId = (sender && sender.tab) ? sender.tab.id : null;
    const tUrl = (sender && sender.tab) ? sender.tab.url : "";
    sccActiveAttempt = {
      ...msg.payload,
      tabId: tId,
      initial_url: tUrl,
      timestamp: Date.now()
    };
    _passwordStore.set({ sccActiveAttempt });
    sendResponse({ status: "ok" });
    return true;
  }
  if (msg.type === "MECP_DISMISSED" || msg.type === "MECP_CLOSED") {
    _passwordStore.remove(['mecpPayload']);
    _unlockAssistTab(sender && sender.tab ? sender.tab.id : null, 'mecp');
    if (msg.type === "MECP_DISMISSED") {
      _passwordStore.remove(['sccActiveAttempt']);
      sccActiveAttempt = null;
    }
    sendResponse({ status: "ok" });
    return true;
  }
  if (msg.type === "SCC_LOGIN_DETECTED" && msg.attempt) {
    const attempt = msg.attempt;
    sccActiveAttempt = null;
    _passwordStore.remove(['sccActiveAttempt']);
    if (SERA_DEBUG) console.log(`⚡ Sera SCC: In-page DOM login detected for ${attempt.userid} (${attempt.combo_label})`);
    sendToDesktop({
      type: "scc_password_verified",
      client_id: attempt.client_id,
      service_id: attempt.service_id,
      userid: attempt.userid || attempt.pan || "",
      pan: attempt.pan || attempt.userid || "",
      password: attempt.password,
      combo_label: attempt.combo_label,
      portal: "Income Tax",
      destination_url: msg.destination_url || "",
      timestamp: new Date().toISOString()
    }, false);
    sendResponse({ status: "ok" });
    return true;
  }
  if (msg.type === "TRIGGER_UNREGISTERED_SCC_MECP" && msg.pan) {
    const pan = String(msg.pan).trim().toUpperCase();
    const tabId = (sender && sender.tab) ? sender.tab.id : null;
    if (!tabId) {
      sendResponse({ status: "no_tab" });
      return true;
    }
    chrome.storage.local.get(['registeredPans', 'sccSettings', 'sccEnabled'], async (data) => {
      let curData = data || {};
      // If sccSettings or registeredPans is missing or lacks opt3_fixed_str, pull fresh from desktop!
      if (!curData.registeredPans || !curData.sccSettings || curData.sccSettings.opt3_fixed_str === undefined) {
        const fresh = await syncSettingsFromDesktop();
        if (fresh) {
          curData = await new Promise(resolve => chrome.storage.local.get(['registeredPans', 'sccSettings', 'sccEnabled'], resolve));
        }
      }
      const regList = (curData.registeredPans || []).map(p => String(p).trim().toUpperCase());
      // Strictly do NOT pop up for registered clients
      if (regList.includes(pan)) {
        if (SERA_DEBUG) console.log(`⚡ Sera SCC: Suppressing unregistered pop-in for registered PAN ${pan}`);
        sendResponse({ status: "registered" });
        return;
      }
      if (curData.sccEnabled === false) {
        sendResponse({ status: "disabled" });
        return;
      }
      const combos = generateSccCombos(pan, curData.sccSettings || {});
      injectMECP(tabId, {
        scc_mode: true,
        scc_combos: combos,
        userid: "",
        client_name: `PAN: ${pan} (Unregistered)`,
        client_id: null,
        portal: msg.portal || "Income Tax",
        unregistered_pan: pan
      });
      sendResponse({ status: "injected" });
    });
    return true;
  }
});

// ---------------- MECP (Manual Extension Copy/Paste) Widget ----------------

function generateSccCombos(pan, sccSettings) {
  const cleanPan = String(pan || "").trim().toUpperCase();
  if (!cleanPan || cleanPan.length < 9) return [];
  const chars = cleanPan.substring(0, 4).toLowerCase();
  const digits = cleanPan.length === 10 ? cleanPan.substring(5, 9) : cleanPan.substring(4, 8);
  const cfg = sccSettings || {};
  const results = [];
  for (let i = 1; i <= 4; i++) {
    const lbl = cfg[`opt${i}_label`] || `Combo ${i}`;
    let fixedStr = cfg[`opt${i}_fixed_str`];
    if (fixedStr === undefined || fixedStr === null) {
      if (i === 1) fixedStr = "@";
      else if (i === 2) fixedStr = "Link@";
      else if (i === 3) fixedStr = "Income@2014";
      else if (i === 4) fixedStr = "income@2014";
      else fixedStr = "";
    }
    let val = "";
    if (i === 1) {
      val = `${chars}${fixedStr || "@"}${digits}`;
    } else if (i === 2) {
      val = `${fixedStr}${digits}`;
    } else if (i === 3 || i === 4) {
      val = `${fixedStr}`;
    }
    results.push({
      id: i,
      label: lbl,
      value: val
    });
  }
  return results;
}

function mecpWidget(userid, password, clientName, expiresMs, sccMode, sccCombos, clientId, portal, unregisteredPan, clearSeconds) {
  // sera_dom.js (shared clipboard clearing) is injected just before this function.
  const seraDom = window.__seraDom;
  if (!seraDom) return;

  const hostId = "sera-mecp-host";
  const old = document.getElementById(hostId);
  if (old) old.remove();
  const smtiOld = document.getElementById("sera-manual-assist-host");
  if (smtiOld) smtiOld.remove();

  const host = document.createElement("div");
  host.id = hostId;
  const shadow = host.attachShadow({ mode: "closed" });

  const isSCC = !!(sccMode && sccCombos && sccCombos.length > 0);

  const style = document.createElement("style");
  style.textContent = `
    .box {
      position: fixed; top: 18px; right: 18px; z-index: 2147483647;
      width: ${isSCC ? "360px" : "320px"}; padding: 14px 16px; background: #161B22; border: 1.5px solid ${isSCC ? "#2E9B5F" : "#30363D"};
      border-radius: 10px; color: #F0F6FC; box-shadow: 0 10px 32px rgba(0,0,0,.5);
      font: 13px -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    }
    .header {
      display: flex; align-items: center; justify-content: space-between; gap: 8px;
      margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid #30363D;
    }
    .badge-wrap {
      display: flex; flex-direction: column; gap: 2px;
    }
    .badge-tag {
      font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.6px;
      color: #4CF9B7;
    }
    .client-title {
      font-weight: 700; color: #F0F6FC; font-size: 13px; word-break: break-word; line-height: 1.3;
    }
    .close-btn {
      background: transparent; border: none; color: #8B949E; font-size: 18px;
      cursor: pointer; padding: 0 4px; line-height: 1; border-radius: 4px;
    }
    .close-btn:hover { color: #F0F6FC; background: #21262D; }
    .section-title {
      font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.6px;
      color: #8B949E; margin: 10px 0 6px 2px;
    }
    .field-row {
      display: flex; align-items: center; justify-content: space-between; gap: 10px;
      margin-bottom: 8px; background: #0D1117; padding: 8px 10px; border-radius: 6px;
      border: 1px solid #21262D;
    }
    .field-left {
      display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1;
    }
    .field-label {
      font-size: 10px; color: #8B949E; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
    }
    .field-value {
      font-family: Consolas, SFMono-Regular, Menlo, monospace; font-size: 13px; color: #E6EDF3;
      word-break: break-all; font-weight: 600; line-height: 1.3;
    }
    .copy-btn {
      display: flex; align-items: center; justify-content: center; gap: 4px;
      background: #238636; color: #FFFFFF; border: none; border-radius: 5px;
      padding: 6px 12px; font: 600 12px -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      cursor: pointer; transition: background 0.15s ease; flex-shrink: 0; white-space: nowrap;
    }
    .copy-btn:hover { background: #2EA043; }
    .copy-btn.copied { background: #1F6FEB; }
    .timer-container {
      margin-top: 12px; height: 3.5px; background: rgba(255, 255, 255, 0.08);
      border-radius: 2px; overflow: hidden;
    }
    .timer-bar {
      height: 100%; width: 100%; background: #2E9B5F; transform-origin: left;
    }
    .toast {
      display: none; position: absolute; bottom: 6px; left: 16px; right: 16px;
      background: #238636; color: #FFF; padding: 5px 10px; border-radius: 4px;
      font-size: 11px; text-align: center; font-weight: 600; z-index: 10;
    }
  `;

  shadow.appendChild(style);
  const box = document.createElement("div");
  box.className = "box";

  // Header
  const header = document.createElement("div");
  header.className = "header";

  const badgeWrap = document.createElement("div");
  badgeWrap.className = "badge-wrap";
  if (isSCC) {
    const bTag = document.createElement("div");
    bTag.className = "badge-tag";
    bTag.textContent = "⚡ Sera Assist (SCC)";
    badgeWrap.appendChild(bTag);
  }
  const title = document.createElement("div");
  title.className = "client-title";
  title.textContent = clientName || (isSCC ? "Verify Password" : "Client Credentials");
  badgeWrap.appendChild(title);

  const close = document.createElement("button");
  close.className = "close-btn";
  close.textContent = "×";
  close.title = "Dismiss";
  // The x, the timeout and "both copied" all end here; the background clears mecpPayload.
  // Only the x also ends an SCC attempt (MECP_DISMISSED): a timeout must not stop SCC verifying.
  let timerTimeout = null;
  function closeCard(messageType) {
    if (timerTimeout) clearTimeout(timerTimeout);
    timerTimeout = null;
    try { chrome.runtime.sendMessage({ type: messageType }); } catch (_) {}
    if (host.isConnected) host.remove();
  }
  close.onclick = () => closeCard("MECP_DISMISSED");
  header.append(badgeWrap, close);

  // Toast banner
  const toast = document.createElement("div");
  toast.className = "toast";

  function showToast(msg) {
    toast.textContent = msg;
    toast.style.display = "block";
    setTimeout(() => { toast.style.display = "none"; }, 2500);
  }

  function copyCredential(val, label) {
    if (!val) return;
    try {
      navigator.clipboard.writeText(val);
      showToast(`${label} copied!`);
    } catch (_) {
      try {
        const ta = document.createElement("textarea");
        ta.value = val;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        showToast(`${label} copied!`);
      } catch (_) {}
    }
  }

  // D6: a plain card closes once both the User ID (when shown) and the password are copied.
  // The SCC card has its own copy rules (Part G) and is left alone.
  const hasUserId = !!(userid && String(userid).trim());
  let userIdCopied = false;
  let passwordCopied = false;
  let finishing = false;
  function closeWhenBothCopied() {
    if (finishing || isSCC || !passwordCopied || (hasUserId && !userIdCopied)) return;
    finishing = true;
    if (timerTimeout) clearTimeout(timerTimeout);
    timerTimeout = null;
    setTimeout(() => closeCard("MECP_CLOSED"), 1200);
  }

  // User ID Row (rendered only if userid is provided)
  if (hasUserId) {
    const uidRow = document.createElement("div");
    uidRow.className = "field-row";
    const uidLeft = document.createElement("div");
    uidLeft.className = "field-left";
    const uidLbl = document.createElement("div");
    uidLbl.className = "field-label";
    uidLbl.textContent = "User ID (PAN)";
    const uidVal = document.createElement("div");
    uidVal.className = "field-value";
    uidVal.textContent = userid || "";
    uidLeft.append(uidLbl, uidVal);

    const uidCopy = document.createElement("button");
    uidCopy.className = "copy-btn";
    uidCopy.innerHTML = "📋 Copy";
    uidCopy.onclick = () => {
      copyCredential(userid, "User ID");
      userIdCopied = true;
      closeWhenBothCopied();
      uidCopy.classList.add("copied");
      uidCopy.innerHTML = "✓ Copied";
      setTimeout(() => {
        uidCopy.classList.remove("copied");
        uidCopy.innerHTML = "📋 Copy";
      }, 2000);
    };
    uidRow.append(uidLeft, uidCopy);
    box.append(header, uidRow);
  } else {
    box.append(header);
  }

  if (isSCC) {
    // Password Combinations Section
    const secTitle = document.createElement("div");
    secTitle.className = "section-title";
    secTitle.textContent = "Password Combinations (Unverified)";
    box.appendChild(secTitle);

    sccCombos.forEach((combo) => {
      const cRow = document.createElement("div");
      cRow.className = "field-row";

      const cLeft = document.createElement("div");
      cLeft.className = "field-left";
      const cLbl = document.createElement("div");
      cLbl.className = "field-label";
      cLbl.textContent = combo.label || `Combo ${combo.id}`;
      const cVal = document.createElement("div");
      cVal.className = "field-value";
      cVal.textContent = combo.value || "";
      cLeft.append(cLbl, cVal);

      const cCopy = document.createElement("button");
      cCopy.className = "copy-btn";
      cCopy.innerHTML = "📋 Copy";
      cCopy.onclick = () => {
        copyCredential(combo.value, combo.label || "Password");
        cCopy.classList.add("copied");
        cCopy.innerHTML = "✓ Copied";
        setTimeout(() => {
          cCopy.classList.remove("copied");
          cCopy.innerHTML = "📋 Copy";
        }, 2000);

        try {
          chrome.runtime.sendMessage({
            type: "SCC_PASSWORD_COPIED",
            payload: {
              client_id: clientId,
              userid: userid || unregisteredPan || "",
              pan: unregisteredPan || userid || "",
              password: combo.value,
              combo_label: combo.label || `Combo ${combo.id}`,
              portal: portal || "Income Tax"
            }
          });
        } catch (_) {}
      };

      cRow.append(cLeft, cCopy);
      box.appendChild(cRow);
    });
  } else {
    // Single Password Row (cleartext)
    const passRow = document.createElement("div");
    passRow.className = "field-row";
    const passLeft = document.createElement("div");
    passLeft.className = "field-left";
    const passLbl = document.createElement("div");
    passLbl.className = "field-label";
    passLbl.textContent = "Password";
    const passVal = document.createElement("div");
    passVal.className = "field-value";
    passVal.textContent = password || "";
    passLeft.append(passLbl, passVal);

    const passCopy = document.createElement("button");
    passCopy.className = "copy-btn";
    passCopy.innerHTML = "📋 Copy";
    passCopy.onclick = () => {
      copyCredential(password, "Password");
      seraDom.scheduleClipboardClear(password, clearSeconds);
      passwordCopied = true;
      closeWhenBothCopied();
      passCopy.classList.add("copied");
      passCopy.innerHTML = "✓ Copied";
      setTimeout(() => {
        passCopy.classList.remove("copied");
        passCopy.innerHTML = "📋 Copy";
      }, 2000);
    };

    passRow.append(passLeft, passCopy);
    box.appendChild(passRow);
  }

  // Countdown bar: pauses while the pointer is over the card, like SMTI's.
  const timerContainer = document.createElement("div");
  timerContainer.className = "timer-container";
  const timerBar = document.createElement("div");
  timerBar.className = "timer-bar";
  timerContainer.appendChild(timerBar);
  box.appendChild(timerContainer);

  let timerStartTime = 0;
  let remainingMs = expiresMs || 90000;
  let isTimerPaused = false;

  function startCountdown() {
    if (timerTimeout) clearTimeout(timerTimeout);
    timerStartTime = Date.now();
    isTimerPaused = false;
    timerBar.style.transition = `transform ${remainingMs}ms linear`;
    timerBar.style.transform = "scaleX(0)";
    timerTimeout = setTimeout(() => closeCard("MECP_CLOSED"), remainingMs);
  }

  function pauseTimer() {
    if (finishing || isTimerPaused || !timerTimeout) return;
    isTimerPaused = true;
    clearTimeout(timerTimeout);
    timerTimeout = null;
    remainingMs = Math.max(0, remainingMs - (Date.now() - timerStartTime));
    try {
      const cur = window.getComputedStyle(timerBar).transform;
      timerBar.style.transition = "none";
      timerBar.style.transform = cur;
    } catch (_) {}
  }

  function resumeTimer() {
    if (finishing || !isTimerPaused) return;
    if (remainingMs <= 500) { closeCard("MECP_CLOSED"); return; }
    startCountdown();
  }

  box.addEventListener("mouseenter", pauseTimer);
  box.addEventListener("mouseleave", resumeTimer);

  box.appendChild(toast);
  shadow.appendChild(box);
  document.documentElement.appendChild(host);
  startCountdown();
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
  _lockAssistTab(tabId, 'mecp');
  clipboardClearSeconds().then(clearSecs => injectWithDom({ tabId }, mecpWidget, [
    message.userid || "",
    message.password || "",
    message.client_name || message.portal,
    90000,
    message.scc_mode === true,
    message.scc_combos || [],
    message.client_id || null,
    message.portal || "Income Tax",
    message.unregistered_pan || "",
    clearSecs
  ])).then(() => console.log("Sera: MECP widget injected"))
    .catch(err => console.error("Sera: MECP injection failed", err));
}

// ---------------- SCA page events -> coordinator ----------------
chrome.runtime.onMessage.addListener((req, sender) => {
  scaCoordinator.handleRuntimeMessage(req, sender);
});
