let nativePort = null;

function connectToNativeHost() {
  if (nativePort !== null) return;
  const hostName = "com.amanassociates.sera";
  try {
    nativePort = chrome.runtime.connectNative(hostName);
    console.log('Sera native host connection established');
    nativePort.onMessage.addListener((message) => {
      console.log("Received from Sera desktop:", message);
      if (message.type === "autofill" && message.url) {
        if (message.mode === "mecp" || message.mode === "manual_copy") handleMECPTab(message);
        else if (message.mode === "manual_assist") handleManualAssistTab(message);
        else handleAutofillTab(message);
      } else if (message.type === "update_settings") {
        const fst = message.fst_enabled !== false && message.tracker_enabled !== false;
        const sad = message.sad_enabled !== false && message.tracker_enabled !== false;
        const overallTracker = fst || sad;
        if (!overallTracker) {
          chrome.storage.local.set({ trackerEnabled: false, fstEnabled: false, sadEnabled: false, activeAutofillPayload: null });
        } else {
          chrome.storage.local.set({ trackerEnabled: true, fstEnabled: fst, sadEnabled: sad });
        }
      }
    });
    nativePort.onDisconnect.addListener(() => {
      const err = chrome.runtime.lastError;
      if (err) {
        console.log("Sera desktop host status:", err.message || "Native host inactive");
      } else {
        console.log("Disconnected from Sera desktop app");
      }
      nativePort = null;
      setTimeout(ensureConnected, 5000);
    });

  } catch (e) {
    console.error("Failed to connect to native host:", e);
    nativePort = null;
  }
}

function ensureConnected() {
  if (!nativePort) connectToNativeHost();
}

// Reopen the last Manual Assist widget from the browser toolbar. The visible
// widget expires quickly, while the encrypted/local extension session remains
// available for a limited time so staff can bring it back when needed.
chrome.action.onClicked.addListener((tab) => {
  chrome.storage.local.get(['manualAssistPayload', 'mecpPayload'], data => {
    const mecp = data.mecpPayload;
    if (mecp && mecp.expiresAt && mecp.expiresAt >= Date.now()) {
      let targetHost = '';
      try { targetHost = new URL(mecp.url).hostname; } catch (_) {}
      if (tab.url && targetHost && tab.url.includes(targetHost)) {
        injectMECP(tab.id, mecp);
        return;
      }
    }
    const payload = data.manualAssistPayload;
    if (!payload || !payload.expiresAt || payload.expiresAt < Date.now()) {
      chrome.storage.local.remove(['manualAssistPayload', 'mecpPayload']);
      return;
    }
    let targetHost = '';
    try { targetHost = new URL(payload.url).hostname; } catch (_) { return; }
    if (!tab.url || !tab.url.includes(targetHost)) return;
    injectManualAssist(tab.id, payload);
  });
});

try {
  chrome.alarms.create("sera_keep_alive", { periodInMinutes: 0.5 });
  chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === "sera_keep_alive") ensureConnected();
  });
} catch (e) {}

chrome.runtime.onStartup.addListener(ensureConnected);
chrome.runtime.onInstalled.addListener(() => {
  // Ensure native connection
  ensureConnected();
  // Enable tracker by default the first time the extension is installed
  chrome.storage.local.get(['trackerEnabled'], (data) => {
    if (data.trackerEnabled === undefined) {
      chrome.storage.local.set({ trackerEnabled: true });
    }
  });
});

ensureConnected();

console.log('Sera SAD: background.js module loaded, registering listeners.');

// SAD: Inject net_interceptor.js into a tab's MAIN world.
// chrome.scripting.executeScript with world:'MAIN' bypasses page CSP entirely.
function injectSAD(tabId, reason) {
  console.log('Sera SAD: injectSAD called for tab', tabId, '| reason:', reason);
  chrome.scripting.executeScript({
    target: { tabId: tabId },
    files: ['content_scripts/net_interceptor.js'],
    world: 'MAIN'
  }).then(() => {
    console.log('Sera SAD: ✅ injected into tab', tabId);
  }).catch(err => {
    console.log('Sera SAD: ❌ inject failed for tab', tabId, ':', err.message);
  });
}

// Inject into every tab that finishes loading
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  console.log('Sera SAD: tabs.onUpdated fired', tabId, changeInfo.status, tab && tab.url);
  if (changeInfo.status !== 'complete') return;
  if (!tab.url || tab.url.startsWith('chrome://') || tab.url.startsWith('about:')) return;
  injectSAD(tabId, 'onUpdated');
});

// Also inject into ALL already-open tabs when the service worker starts up
// (handles the case where the extension is reloaded while tabs are already open)
chrome.tabs.query({}, (tabs) => {
  console.log('Sera SAD: startup tab scan, found', tabs.length, 'tabs');
  for (const tab of tabs) {
    if (!tab.url || tab.url.startsWith('chrome://') || tab.url.startsWith('about:')) continue;
    if (tab.status === 'complete') injectSAD(tab.id, 'startup-scan');
  }
});



// Fill function injected into the page
function fillCredentialsInPage(userid, password, usernameSelector, passwordSelector, extensionFlow) {
  if (window.__seraFillActive) return; // prevent duplicate runs
  window.__seraFillActive = true;
  console.log("Sera: fillCredentialsInPage started, flow:", extensionFlow);

  function cleanSelector(sel) {
    if (!sel) return "";
    return sel.trim().replace(/\s+\[/g, '[').replace(/input\s+/g, 'input');
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
    el.dispatchEvent(new Event('focus', { bubbles: true }));

    // Use Angular-compatible native setter
    try {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(el, value);
    } catch (e) { el.value = value; }

    // Dispatch compositionstart to signal framework that input is starting
    el.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));

    // Dispatch proper InputEvent (Angular's DefaultValueAccessor listens for this)
    el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));

    // Dispatch compositionend to finalize
    el.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: value }));

    el.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Unidentified' }));
    el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Unidentified' }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    try { el.blur(); } catch (e) {}
    el.dispatchEvent(new Event('blur', { bubbles: true }));
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
      "#identifierId",
      "input[type='email']",
      "input[name='identifier']",
      "#panAdhaarUserId",
      "#username",
      "input[name='pan']",
      "input[name='username']",
      "input[name='user']"
    ];

    const panInterval = setInterval(() => {
      panAttempts++;
      let userField = null;

      if (cleanUserSel) {
        try {
          const cand = document.querySelector(cleanUserSel);
          if (cand && isVisible(cand)) userField = cand;
        } catch (e) {}
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
      "input[type='password']",
      "input[name='Passwd']",
      "input[name='password']",
      "#passwordInput",
      "#user_pass",
      "input[name='passwd']"
    ];

    const passInterval = setInterval(() => {
      passAttempts++;
      let passField = null;

      if (cleanPassSel) {
        try {
          const cand = document.querySelector(cleanPassSel);
          if (cand && isVisible(cand)) passField = cand;
        } catch (e) {}
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
        // Handle secure access checkbox (Angular Material v14+ uses mat-mdc-checkbox-checked)
        const cb = document.querySelector("mat-checkbox");
        if (cb && !cb.classList.contains("mat-checkbox-checked") && !cb.classList.contains("mat-mdc-checkbox-checked")) {
          cb.click();
        }
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

function handleAutofillTab(message) {
  let targetHostname;
  try { targetHostname = new URL(message.url).hostname; } catch (e) { console.error("Invalid URL", message.url); return; }

  // Store payload  // Keep the active payload around for the content scripts
  const isTrackerEnabled = message.tracker_enabled === true;
  const isFstEnabled = message.fst_enabled !== false && isTrackerEnabled;
  const isSadEnabled = message.sad_enabled !== false && isTrackerEnabled;
  chrome.storage.local.set({ 
    activeAutofillPayload: { ...message, tracker_enabled: isTrackerEnabled, fst_enabled: isFstEnabled, sad_enabled: isSadEnabled, ts: Date.now() },
    trackerEnabled: isTrackerEnabled,
    fstEnabled: isFstEnabled,
    sadEnabled: isSadEnabled
  });

  chrome.tabs.query({}, (tabs) => {
    const existing = tabs.find(t => t.url && t.url.includes(targetHostname));
    
    if (existing) {
      chrome.windows.update(existing.windowId, { focused: true }, () => {
        if (chrome.runtime.lastError) {}
        chrome.storage.local.set({ trackingTabId: existing.id });
        chrome.tabs.update(existing.id, { url: message.url, active: true }, () => {
          if (chrome.runtime.lastError) {}
          chrome.tabs.onUpdated.addListener(function listener(tabId, info) {
            if (tabId === existing.id && info.status === 'complete') {
              chrome.tabs.onUpdated.removeListener(listener);
              injectFillScript(existing.id, message.userid, message.password, message.username_selector, message.password_selector, message.extension_flow);
            }
          });
        });
      });
    } else {
      chrome.tabs.create({ url: message.url }, (newTab) => {
        if (chrome.runtime.lastError || !newTab) return;
        chrome.storage.local.set({ trackingTabId: newTab.id });
        chrome.tabs.onUpdated.addListener(function listener(tabId, info) {
          if (tabId === newTab.id && info.status === 'complete') {
            chrome.tabs.onUpdated.removeListener(listener);
            injectFillScript(newTab.id, message.userid, message.password, message.username_selector, message.password_selector, message.extension_flow);
          }
        });
      });
    }

  });
}

function manualAssistWidget(userid, password, usernameSelector, passwordSelector, clientName, expiresMs) {
  const hostId = "sera-manual-assist-host";
  const old = document.getElementById(hostId);
  if (old) old.remove();
  const mecpOld = document.getElementById("sera-mecp-host");
  if (mecpOld) mecpOld.remove();

  const host = document.createElement("div");
  host.id = hostId;
  const shadow = host.attachShadow({ mode: "closed" });
  const style = document.createElement("style");
  style.textContent = `
    :host { all: initial; }
    .box { position: fixed; z-index: 2147483647; top: 18px; right: 18px; min-width: 210px; max-width: 310px; width: max-content;
      padding: 12px; border-radius: 10px; background: #241f1b; color: #fff;
      box-shadow: 0 8px 28px rgba(0,0,0,.28); font: 13px Segoe UI,Arial,sans-serif;
      transform: scale(1.3); transform-origin: top right; }
    .title { display:flex; align-items:flex-start; justify-content:space-between; gap:10px;
      margin-bottom: 9px; font-weight: 700; }
    .client { color:#bfe3d7; word-break: break-word; white-space: normal; line-height: 1.35; font-size: 13px; }
    button { display:block; width:100%; margin-top:7px; padding:7px 8px; border:0; border-radius:6px;
      background:#4cf9b7; color:#165c55; font:600 12px Segoe UI,Arial,sans-serif; cursor:pointer; }
    button:hover { background:#80ffca; } .close { background:transparent; color:#fff; width:auto;
      margin:0; padding:0 2px; font-size:16px; flex-shrink:0; }
  `;

  shadow.appendChild(style);
  const box = document.createElement("div"); box.className = "box";
  const title = document.createElement("div"); title.className = "title";
  const name = document.createElement("span"); name.className = "client"; name.textContent = clientName || "Client";
  const close = document.createElement("button"); close.className = "close"; close.textContent = "×";
  title.append(name, close);
  const uidBtn = document.createElement("button"); uidBtn.textContent = "User ID";
  const passBtn = document.createElement("button"); passBtn.textContent = "Password";
  box.append(title, uidBtn, passBtn); shadow.appendChild(box); document.documentElement.appendChild(host);

  function clean(sel) { return (sel || "").trim().replace(/\s+\[/g, "[").replace(/input\s+/g, "input"); }
  function visible(el) { if (!el || el.disabled || el.type === "hidden") return false;
    const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }
  function find(selector, fallbacks) {
    try { const el = selector && document.querySelector(clean(selector)); if (visible(el)) return el; } catch (_) {}
    for (const sel of fallbacks) { try { const el = document.querySelector(sel); if (visible(el)) return el; } catch (_) {} }
    return null;
  }
  function fill(el, value) {
    if (!el) return;
    el.focus();
    try { Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set.call(el, value); }
    catch (_) { el.value = value; }
    el.dispatchEvent(new CompositionEvent("compositionstart", { bubbles:true }));
    el.dispatchEvent(new InputEvent("input", { bubbles:true, inputType:"insertText", data:value }));
    el.dispatchEvent(new CompositionEvent("compositionend", { bubbles:true, data:value }));
    el.dispatchEvent(new Event("change", { bubbles:true })); el.blur();
  }
  uidBtn.onclick = () => fill(find(usernameSelector, ["#identifierId", "input[type='email']", "#panAdhaarUserId", "#username", "input[name='username']"]), userid);
  passBtn.onclick = () => {
    fill(find(passwordSelector, ["input[type='password']", "#passwordInput", "input[name='password']"]), password);
    setTimeout(() => { if (host.isConnected) host.remove(); }, 350);
  };
  close.onclick = () => host.remove();
  setTimeout(() => { if (host.isConnected) host.remove(); }, expiresMs || 30000);

}

function handleManualAssistTab(message) {
  let hostname;
  try { hostname = new URL(message.url).hostname; } catch (_) { return; }
  chrome.storage.local.remove(['mecpPayload']);
  chrome.storage.local.set({
    manualAssistPayload: { ...message, expiresAt: Date.now() + (5 * 60 * 1000) }
  });
  chrome.tabs.query({}, tabs => {
    const existing = tabs.find(t => t.url && t.url.includes(hostname));
    const open = tab => {
      if (!tab) return;
      chrome.windows.update(tab.windowId, { focused: true }, () => { if (chrome.runtime.lastError) {} });
      chrome.tabs.onUpdated.addListener(function listener(tabId, info) {
        if (tabId === tab.id && info.status === "complete") {
          chrome.tabs.onUpdated.removeListener(listener);
          injectManualAssist(tab.id, message);
        }
      });
      chrome.tabs.update(tab.id, { url: message.url, active: true }, () => { if (chrome.runtime.lastError) {} });
    };
    if (existing) open(existing); else chrome.tabs.create({ url: message.url }, open);

  });
}

function recordInjectionAndClearCookiesIfNeeded() {
  chrome.storage.local.get({ injectionCount: 0 }, (data) => {
    let newCount = (data.injectionCount || 0) + 1;
    console.log(`Sera: Extension injection count = ${newCount}/5`);
    
    if (newCount >= 5) {
      console.log("Sera: Reached 5 extension injections. Clearing browser cookies...");
      clearBrowserCookies(() => {
        console.log("Sera: Browser cookies cleared successfully after 5 injections.");
      });
      chrome.storage.local.set({ injectionCount: 0 });
    } else {
      chrome.storage.local.set({ injectionCount: newCount });
    }
  });
}

function clearBrowserCookies(callback) {
  let done = false;
  const finish = () => {
    if (!done) {
      done = true;
      if (callback) callback();
    }
  };

  if (chrome.browsingData && chrome.browsingData.removeCookies) {
    chrome.browsingData.removeCookies({ "since": 0 }, () => {
      if (chrome.runtime.lastError) {
        console.warn("Sera: browsingData.removeCookies warning:", chrome.runtime.lastError);
      }
      finish();
    });
  } else if (chrome.browsingData && chrome.browsingData.remove) {
    chrome.browsingData.remove({ "since": 0 }, { "cookies": true }, () => {
      finish();
    });
  } else if (chrome.cookies) {
    chrome.cookies.getAll({}, (cookies) => {
      if (!cookies || cookies.length === 0) {
        finish();
        return;
      }
      let pending = cookies.length;
      cookies.forEach((cookie) => {
        const protocol = cookie.secure ? "https:" : "http:";
        const url = `${protocol}//${cookie.domain.replace(/^\./, "")}${cookie.path}`;
        chrome.cookies.remove({ url: url, name: cookie.name }, () => {
          pending--;
          if (pending <= 0) finish();
        });
      });
    });
  } else {
    finish();
  }
}

function injectManualAssist(tabId, message) {
  recordInjectionAndClearCookiesIfNeeded();
  chrome.scripting.executeScript({ target:{tabId}, func:manualAssistWidget,
    args:[message.userid, message.password, message.username_selector, message.password_selector,
      message.client_name || message.portal, 30000] })
    .then(() => console.log("Sera: Manual Assist widget injected"))
    .catch(err => console.error("Sera: Manual Assist injection failed", err));
}

// Track tab closure for Tier 2 fallback
chrome.tabs.onRemoved.addListener((tabId, removeInfo) => {
  chrome.storage.local.get(['trackingTabId', 'activeAutofillPayload'], (data) => {
    if (data.trackingTabId === tabId && data.activeAutofillPayload) {
      // The tracked tab was closed. Send uncertain_result to desktop app
      if (nativePort) {
        nativePort.postMessage({
          type: "uncertain_result",
          client_id: data.activeAutofillPayload.client_id,
          portal: data.activeAutofillPayload.portal
        });
      }
      // Clear tracking state
      chrome.storage.local.remove(['trackingTabId', 'activeAutofillPayload']);
    }
  });
});

function injectFillScript(tabId, userid, password, usernameSelector, passwordSelector, extensionFlow) {
  recordInjectionAndClearCookiesIfNeeded();
  chrome.scripting.executeScript({
    target: { tabId },
    func: fillCredentialsInPage,
    args: [userid, password, usernameSelector, passwordSelector, extensionFlow]
  }).then(() => console.log("Sera: fill script injected"))
    .catch(err => console.error("Sera: inject failed", err));
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  console.log("Sera background: received runtime message:", msg);
  if (msg.type === "filing_result") {
    console.log("Sera background: handling filing_result, nativePort is", nativePort ? "connected" : "null");
    if (nativePort) {
      try {
        nativePort.postMessage(msg);
        console.log("Sera background: successfully posted filing_result to native host");
      } catch (err) {
        console.error("Sera background: failed to postMessage to nativePort:", err);
      }
    } else {
      console.warn("Sera background: nativePort is null, reconnecting and caching...");
      ensureConnected();
      chrome.storage.local.get({ pendingResults: [] }, data => {
        chrome.storage.local.set({ pendingResults: [...data.pendingResults, msg] });
      });
    }
    // Clear tracking state so we don't fire uncertain_result when tab closes
    chrome.storage.local.remove(['trackingTabId', 'activeAutofillPayload']);
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
  let hostname;
  try { hostname = new URL(message.url).hostname; } catch (_) { return; }
  chrome.storage.local.remove(['manualAssistPayload']);
  chrome.storage.local.set({
    mecpPayload: { ...message, expiresAt: Date.now() + (5 * 60 * 1000) }
  });
  chrome.tabs.query({}, tabs => {
    const existing = tabs.find(t => t.url && t.url.includes(hostname));
    if (existing) {
      chrome.windows.update(existing.windowId, { focused: true }, () => { if (chrome.runtime.lastError) {} });
      chrome.tabs.update(existing.id, { url: message.url, active: true }, () => { if (chrome.runtime.lastError) {} });
      
      let injected = false;
      if (existing.status === "complete") {
        injected = true;
        injectMECP(existing.id, message);
      }
      
      chrome.tabs.onUpdated.addListener(function listener(tabId, info) {
        if (tabId === existing.id && info.status === "complete" && !injected) {
          injected = true;
          chrome.tabs.onUpdated.removeListener(listener);
          injectMECP(existing.id, message);
        }
      });
    } else {
      chrome.tabs.create({ url: message.url }, (newTab) => {
        if (chrome.runtime.lastError || !newTab) return;
        let injected = false;
        chrome.tabs.onUpdated.addListener(function listener(tabId, info) {
          if (tabId === newTab.id && info.status === "complete" && !injected) {
            injected = true;
            chrome.tabs.onUpdated.removeListener(listener);
            injectMECP(newTab.id, message);
          }
        });
      });
    }
  });
}

function injectMECP(tabId, message) {
  recordInjectionAndClearCookiesIfNeeded();
  chrome.scripting.executeScript({
    target: { tabId },
    func: mecpWidget,
    args: [message.userid, message.password, message.client_name || message.portal, 60000]
  }).then(() => console.log("Sera: MECP widget injected"))
    .catch(err => console.error("Sera: MECP injection failed", err));
}
