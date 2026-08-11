let nativePort = null;

function connectToNativeHost() {
  if (nativePort !== null) return;
  const hostName = "com.amanassociates.sera";
  try {
    nativePort = chrome.runtime.connectNative(hostName);
    nativePort.onMessage.addListener((message) => {
      console.log("Received from Sera desktop:", message);
      if (message.type === "autofill" && message.url) {
        if (message.mode === "manual_assist") handleManualAssistTab(message);
        else handleAutofillTab(message);
      } else if (message.type === "update_settings") {
        if (message.tracker_enabled === false) {
          chrome.storage.local.set({ trackerEnabled: false, activeAutofillPayload: null });
        } else {
          chrome.storage.local.set({ trackerEnabled: true });
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
  chrome.storage.local.get(['manualAssistPayload'], data => {
    const payload = data.manualAssistPayload;
    if (!payload || !payload.expiresAt || payload.expiresAt < Date.now()) {
      chrome.storage.local.remove(['manualAssistPayload']);
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
chrome.runtime.onInstalled.addListener(ensureConnected);

ensureConnected();

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
  chrome.storage.local.set({ 
    activeAutofillPayload: { ...message, tracker_enabled: isTrackerEnabled, ts: Date.now() },
    trackerEnabled: isTrackerEnabled
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

function injectManualAssist(tabId, message) {
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
  chrome.scripting.executeScript({
    target: { tabId },
    func: fillCredentialsInPage,
    args: [userid, password, usernameSelector, passwordSelector, extensionFlow]
  }).then(() => console.log("Sera: fill script injected"))
    .catch(err => console.error("Sera: inject failed", err));
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "filing_result") {
    if (nativePort) nativePort.postMessage(msg);
    else {
      chrome.storage.local.get({ pendingResults: [] }, data => {
        chrome.storage.local.set({ pendingResults: [...data.pendingResults, msg] });
      });
    }
    // Clear tracking state so we don't fire uncertain_result when tab closes
    chrome.storage.local.remove(['trackingTabId', 'activeAutofillPayload']);
  }
});
