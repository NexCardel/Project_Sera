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
      } else if (message.type === "SCA_ARM") {
        handleScaArm(message);
      } else if (message.type === "update_settings") {
        const fst = message.fst_enabled !== false && message.tracker_enabled !== false;
        const sad = message.sad_enabled !== false && message.tracker_enabled !== false;
        const sca = message.sca_enabled !== false;
        const scaMode = message.sca_mode || "autofill";
        const overallTracker = fst || sad;
        if (!overallTracker) {
          chrome.storage.local.set({ trackerEnabled: false, fstEnabled: false, sadEnabled: false, scaEnabled: sca, scaMode: scaMode, activeAutofillPayload: null });
        } else {
          chrome.storage.local.set({ trackerEnabled: true, fstEnabled: fst, sadEnabled: sad, scaEnabled: sca, scaMode: scaMode });
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
      "#identifierId",
      "input[type='email']",
      "input[name='identifier']",
      "#panAdhaarUserId",
      "#userId",
      "input[name='userId']",
      "#txtUserId",
      "#identifierId",
      "input[type='email']",
      "input[name='identifier']",
      "#panAdhaarUserId",
      "#username",
      "#userName",
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
      "input[name='psw']",
      "#psw",
      "input[type='password']",
      "input[name='Passwd']",
      "input[name='password']",
      "#passwordInput",
      "#user_pass",
      "#password",
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
        // Handle IT portal secure access message checkbox specifically (never click "Show password" checkboxes)
        const cb = document.querySelector("mat-checkbox#agreeTermAndCondition, mat-checkbox.login-terms, app-login mat-checkbox");
        if (cb && !cb.classList.contains("mat-checkbox-checked") && !cb.classList.contains("mat-mdc-checkbox-checked")) {
          const cbText = (cb.textContent || "").toLowerCase();
          if (!cbText.includes("show") && !cbText.includes("reveal")) {
            cb.click();
          }
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
      box-shadow: 0 16px 44px rgba(0,0,0,0.75), 0 0 20px rgba(46, 155, 95, 0.25);
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
  uidBtn.innerHTML = "👤  Inject User ID";

  const passBtn = document.createElement("button");
  passBtn.className = "btn primary";
  passBtn.innerHTML = "🔑  Inject Password";

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
    if (!el || el.disabled || el.type === "hidden" || el.getAttribute("tabindex") === "-1") return false;
    try {
      const style = window.getComputedStyle(el);
      if (style.display === "none" || style.visibility === "hidden") return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    } catch (_) {
      return true;
    }
  }

  function find(selector, fallbacks) {
    try {
      const el = selector && document.querySelector(clean(selector));
      if (visible(el)) return el;
    } catch (_) {}
    for (const sel of fallbacks) {
      try {
        const el = document.querySelector(sel);
        if (visible(el)) return el;
      } catch (_) {}
    }
    return null;
  }

  function fill(el, value) {
    if (!el) return false;
    try { el.focus(); } catch (_) {}
    try {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(el, value);
    } catch (_) {
      el.value = value;
    }
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  function dismiss() {
    card.style.transform = "translateX(120%)";
    card.style.opacity = "0";
    setTimeout(() => { if (host.isConnected) host.remove(); }, 380);
  }

  uidBtn.onclick = () => {
    const el = find(usernameSelector, [
      "#userId", "input[name='userId']", "#txtUserId",
      "#identifierId", "input[type='email']", "#panAdhaarUserId", "#username", "#userName",
      "input[name='username']", "input[name='user']", "input[name='pan']"
    ]);
    if (fill(el, userid)) {
      uidBtn.className = "btn done";
      uidBtn.innerHTML = "✓  User ID Injected";
      setTimeout(() => {
        uidBtn.className = "btn primary";
        uidBtn.innerHTML = "👤  Inject User ID";
      }, 2000);
    }
  };

  passBtn.onclick = () => {
    const el = find(passwordSelector, [
      "input[name='psw']", "#psw", "input[type='password']", "input[name='Passwd']",
      "#password", "#passwordInput", "#user_pass", "input[name='password']", "input[name='pass']"
    ]);
    if (fill(el, password)) {
      passBtn.className = "btn done";
      passBtn.innerHTML = "✓  Password Injected";
      setTimeout(() => {
        dismiss();
      }, 400);
    }
  };

  closeBtn.onclick = dismiss;
  setTimeout(() => { if (host.isConnected) dismiss(); }, duration);
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

// ---------------- SCA (Sera Clipboard Assist) ----------------
let armedSCAPayload = null;
let armedSCATimer = null;

function handleScaArm(message) {
  chrome.storage.local.get(['scaEnabled'], (data) => {
    if (data.scaEnabled === false) {
      console.log("Sera SCA: SCA is disabled in settings. Skipping arm.");
      return;
    }
    console.log("Sera SCA: Silently arming password for client", message.client_id_token || message.client_id);
    if (armedSCATimer) {
      clearTimeout(armedSCATimer);
      armedSCATimer = null;
    }
    const ttl = message.ttl_ms || 45000;
    armedSCAPayload = {
      ...message,
      expiresAt: Date.now() + ttl
    };
    armedSCATimer = setTimeout(() => {
      console.log("Sera SCA: Armed state expired.");
      armedSCAPayload = null;
      armedSCATimer = null;
    }, ttl);

    // Broadcast armed payload to active tabs for instantaneous paste readiness
    chrome.storage.local.set({ armedSCAPayload: armedSCAPayload });
  });
}

// Global runtime message listener from content scripts (e.g. paste triggered)
chrome.runtime.onMessage.addListener((req, sender, sendResponse) => {
  if (req.type === "sca_paste_matched") {
    console.log("Sera SCA: UID paste detected on portal", req.portal, "tab", sender.tab ? sender.tab.id : "unknown");
    if (!sender.tab || !sender.tab.id) return;

    chrome.storage.local.get(['armedSCAPayload', 'scaEnabled', 'scaMode'], (data) => {
      if (data.scaEnabled === false) return;
      const payload = data.armedSCAPayload || armedSCAPayload;
      if (!payload || !payload.expiresAt || payload.expiresAt < Date.now()) {
        console.log("Sera SCA: No active armed payload found for paste event.");
        return;
      }

      const matchedService = (payload.services || []).find(s => {
        try {
          const uHost = new URL(s.url).hostname.toLowerCase();
          const targetPortal = (req.portal || '').toLowerCase();
          let tHost = '';
          if (sender.tab && sender.tab.url) {
            try { tHost = new URL(sender.tab.url).hostname.toLowerCase(); } catch (_) {}
          }
          return (tHost && (tHost.includes(uHost) || uHost.includes(tHost))) ||
                 (targetPortal && (targetPortal.includes(uHost) || uHost.includes(targetPortal)));
        } catch (_) {
          return true;
        }
      }) || (payload.services && payload.services[0]);

      if (matchedService && matchedService.password) {
        const isWidgetMode = (payload.sca_mode === "widget" || payload.sca_mode === "assist") || (data.scaMode === "widget" || data.scaMode === "assist");

        if (isWidgetMode) {
          // Trigger interactive SCA Widget on this tab
          chrome.scripting.executeScript({
            target: { tabId: sender.tab.id },
            func: (pwd, pwdSel, bizName, ownName, portalName, matchedUid, clientId, clientToken) => {
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
                el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: val }));
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
              }

              const fallbacks = [
                pwdSel,
                "input[name='psw']",
                "#psw",
                "input[name='Passwd']",
                "input[type='password']",
                "#password",
                "#passwordInput",
                "#user_pass",
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

              function renderAndShowWidget(targetField) {
                const hostId = "sera-sca-widget-host";
                const old = document.getElementById(hostId);
                if (old) old.remove();
                const assistOld = document.getElementById("sera-sca-assist-host");
                if (assistOld) assistOld.remove();
                const toastOld = document.getElementById("sera-sca-toast-host");
                if (toastOld) toastOld.remove();

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
                    box-shadow: 0 12px 36px rgba(0,0,0,0.65), 0 0 16px rgba(46, 155, 95, 0.25);
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
                title.textContent = bizName || "Client Profile";

                const subtitle = document.createElement("div");
                subtitle.className = "subtitle";
                subtitle.textContent = ownName ? `👤 ${ownName} • ${portalName}` : `${portalName}`;

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
                document.body.appendChild(host);

                // Animate in
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
                  const currentField = targetField && isVis(targetField) ? targetField : findPassField();
                  if (currentField) {
                    simType(currentField, pwd);
                    clearTimeout(autoTimer);
                    injectBtn.className = "btn-inject done";
                    injectBtn.innerHTML = "✓  Password Injected";
                    setTimeout(dismiss, 500);
                  } else {
                    injectBtn.innerHTML = "⚠️ Password field not visible";
                    setTimeout(() => {
                      injectBtn.innerHTML = "🔑  Inject Password";
                    }, 1500);
                  }
                };
              }

              // Check if password field is already visible (single-page login)
              const initialField = findPassField();
              if (initialField) {
                renderAndShowWidget(initialField);
              } else {
                // Two-page login: wait up to 45s for user to click Next and password field to appear
                let attempts = 0;
                const waitInterval = setInterval(() => {
                  attempts++;
                  const pf = findPassField();
                  if (pf) {
                    clearInterval(waitInterval);
                    renderAndShowWidget(pf);
                  } else if (attempts >= 300) {
                    clearInterval(waitInterval);
                  }
                }, 150);
              }
            },
            args: [
              matchedService.password,
              matchedService.password_selector,
              payload.business_name || "",
              payload.owner_name || "",
              matchedService.name || "Portal",
              payload.matched_uid || "",
              payload.client_id || 0,
              payload.client_id_token || ""
            ]
          }).then(() => {
            if (!nativePort) ensureConnected();
            if (nativePort) {
              try {
                nativePort.postMessage({
                  type: "audit_event",
                  action: "SCA widget armed",
                  client_id: payload.client_id,
                  detail: `SCA widget armed — client ${payload.client_id_token || payload.client_id} — portal ${matchedService.name || 'Portal'}`
                });
              } catch (_) {}
            }
          }).catch(err => console.error("Sera SCA: Widget injection error", err));
        } else {
          // Trigger ambient silent password fill on this tab
          chrome.scripting.executeScript({
            target: { tabId: sender.tab.id },
            func: (pwd, pwdSel, flow, bizName, ownName, portalName) => {
              function isVis(el) {
                if (!el) return false;
                if (el.type === 'hidden' || el.getAttribute('tabindex') === '-1') return false;
                try {
                  const style = window.getComputedStyle(el);
                  return style.display !== 'none' && style.visibility !== 'hidden';
                } catch (_) { return true; }
              }
              function simType(el, val) {
                if (!el) return;
                try { el.focus(); } catch (_) {}
                try {
                  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
                  setter.call(el, val);
                } catch (_) { el.value = val; }
                el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: val }));
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
              }

              function showScaToast() {
                const existing = document.getElementById('sera-sca-toast-host');
                if (existing) existing.remove();

                const host = document.createElement('div');
                host.id = 'sera-sca-toast-host';
                host.style.cssText = 'position: fixed; top: 20px; right: 24px; z-index: 2147483647; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; pointer-events: auto;';

                const shadow = host.attachShadow({ mode: 'closed' });
                const container = document.createElement('div');
                container.style.cssText = `
                  display: flex;
                  flex-direction: column;
                  gap: 6px;
                  min-width: 290px;
                  max-width: 380px;
                  padding: 14px 16px;
                  background: linear-gradient(145deg, #111814, #0B130E);
                  border: 1.5px solid #2E9B5F;
                  border-radius: 12px;
                  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.65), 0 0 16px rgba(46, 155, 95, 0.25);
                  color: #FFFFFF;
                  transform: translateX(120%);
                  opacity: 0;
                  transition: transform 0.4s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.35s ease;
                `;

                const headerRow = document.createElement('div');
                headerRow.style.cssText = 'display: flex; align-items: center; justify-content: space-between; margin-bottom: 2px;';

                const badge = document.createElement('span');
                badge.style.cssText = 'font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.6px; color: #4CF9B7; background: rgba(46, 155, 95, 0.22); border: 1px solid rgba(76, 249, 183, 0.35); padding: 3px 7px; border-radius: 6px; display: flex; align-items: center; gap: 4px;';
                badge.innerHTML = '⚡ Sera Clipboard Assist';

                const closeBtn = document.createElement('span');
                closeBtn.style.cssText = 'cursor: pointer; font-size: 14px; color: #889988; line-height: 1; padding: 2px 4px; border-radius: 4px;';
                closeBtn.textContent = '✕';
                closeBtn.onclick = () => {
                  container.style.transform = 'translateX(120%)';
                  container.style.opacity = '0';
                  setTimeout(() => host.remove(), 400);
                };

                headerRow.appendChild(badge);
                headerRow.appendChild(closeBtn);

                const title = document.createElement('div');
                title.style.cssText = 'font-size: 14px; font-weight: 700; color: #FFFFFF; line-height: 1.3; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;';
                title.textContent = bizName || 'Client Profile';

                let ownerDiv = null;
                if (ownName) {
                  ownerDiv = document.createElement('div');
                  ownerDiv.style.cssText = 'font-size: 12px; color: #9FB3A8; line-height: 1.2;';
                  ownerDiv.textContent = `👤 ${ownName}`;
                }

                const statusDiv = document.createElement('div');
                statusDiv.style.cssText = 'display: flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600; color: #34D399; margin-top: 4px; padding-top: 6px; border-top: 1px solid rgba(255, 255, 255, 0.08);';
                statusDiv.innerHTML = `<span>✓</span> <span>Password was autofilled for ${portalName || 'Portal'}</span>`;

                container.appendChild(headerRow);
                container.appendChild(title);
                if (ownerDiv) container.appendChild(ownerDiv);
                container.appendChild(statusDiv);
                shadow.appendChild(container);
                document.body.appendChild(host);

                // Slide in
                setTimeout(() => {
                  container.style.transform = 'translateX(0)';
                  container.style.opacity = '1';
                }, 40);

                // Auto-dismiss after 6.5 seconds
                setTimeout(() => {
                  container.style.transform = 'translateX(120%)';
                  container.style.opacity = '0';
                  setTimeout(() => host.remove(), 400);
                }, 6500);
              }

              // Find password field (includes TRACES and Google's Passwd field)
              const fallbacks = [
                pwdSel,
                "input[name='psw']",
                "#psw",
                "input[name='Passwd']",
                "input[type='password']",
                "#password",
                "#passwordInput",
                "#user_pass",
                "input[name='password']",
                "input[name='pass']"
              ].filter(Boolean);

              let attempts = 0;
              // Poll for up to 30 seconds waiting for password field to appear when user advances to step 2
              const interval = setInterval(() => {
                attempts++;
                let passField = null;
                for (const sel of fallbacks) {
                  try {
                    const els = document.querySelectorAll(sel);
                    for (const el of els) {
                      if (isVis(el)) { passField = el; break; }
                    }
                    if (passField) break;
                  } catch (_) {}
                }

                if (passField) {
                  clearInterval(interval);
                  setTimeout(() => {
                    simType(passField, pwd);
                    showScaToast();
                    console.log("Sera SCA: Password filled safely & notification banner displayed.");
                  }, 100);
                } else if (attempts >= 200) {
                  clearInterval(interval);
                }
              }, 150);
            },
            args: [
              matchedService.password,
              matchedService.password_selector,
              matchedService.extension_flow || "double",
              payload.business_name || "",
              payload.owner_name || "",
              matchedService.name || "Portal"
            ]
          }).then(() => {
            // Send audit trail notification back to desktop app
            if (!nativePort) ensureConnected();
            if (nativePort) {
              try {
                nativePort.postMessage({
                  type: "audit_event",
                  action: "SCA autofill triggered",
                  client_id: payload.client_id,
                  detail: `SCA ambient autofill — client ${payload.client_id_token || payload.client_id} — portal ${matchedService.name || 'Portal'}`
                });
              } catch (_) {}
            }
          }).catch(err => console.error("Sera SCA: Injection error", err));
        }
      }
    });
  }
});
