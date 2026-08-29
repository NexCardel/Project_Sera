let nativePort = null;

function connectToNativeHost() {
  if (nativePort !== null) return;
  const hostName = "com.amanassociates.sera";
  try {
    nativePort = chrome.runtime.connectNative(hostName);
    console.log('Sera native host connection established');
    nativePort.onMessage.addListener((message) => {
      console.log("Received from Sera desktop:", message);
      if (message.type && message.type.startsWith("SCA_")) {
        if (message.command_id) {
          try {
            nativePort.postMessage({ type: "SCA_ACK", command_id: message.command_id });
          } catch(e) {}
          if (!self.seenScaCommands) self.seenScaCommands = new Set();
          if (self.seenScaCommands.has(message.command_id)) return;
          self.seenScaCommands.add(message.command_id);
        }
        handleScaCommand(message, {id: "nativeHost"}, () => {});
        return;
      }
      if (message.type === "autofill" && message.url) {
        if (message.mode === "mecp" || message.mode === "manual_copy") handleMECPTab(message);
        else if (message.mode === "manual_assist") handleManualAssistTab(message);
        else handleAutofillTab(message);
      } else if (message.type === "SCA_ARM") {
        handleScaArm(message);
      } else if (message.type === "update_settings") {
        const fst = message.fst_enabled !== false && message.tracker_enabled !== false;
        const sad = message.sad_enabled !== false && message.tracker_enabled !== false;
        const sadNotif = message.sad_browser_notif_enabled !== false;
        const sca = message.sca_enabled !== false;
        const scaMode = message.sca_mode || "autofill";
        const allowedDomains = message.allowed_domains || [];
        const overallTracker = fst || sad;
        const storageObj = {
          trackerEnabled: overallTracker,
          fstEnabled: fst,
          sadEnabled: sad,
          sadBrowserNotifEnabled: sadNotif,
          scaEnabled: sca,
          scaMode: scaMode
        };
        if (allowedDomains && allowedDomains.length > 0) {
          storageObj.allowedDomains = allowedDomains;
        }
        if (!overallTracker) {
          storageObj.activeAutofillPayload = null;
        }
        chrome.storage.local.set(storageObj, () => {
          if (overallTracker) {
            injectAllOpenTabs('desktop-settings-enabled');
          } else {
            broadcastTrackerState(false);
          }
        });
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

// Reopen the last Manual Assist widget from the browser toolbar if clicked directly
if (chrome.action && chrome.action.onClicked) {
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
}

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
  chrome.storage.local.get(['trackerEnabled', 'sadEnabled', 'fstEnabled'], (data) => {
    const update = {};
    if (data.trackerEnabled === undefined) update.trackerEnabled = true;
    if (data.sadEnabled === undefined) update.sadEnabled = true;
    if (data.fstEnabled === undefined) update.fstEnabled = true;
    if (Object.keys(update).length > 0) {
      chrome.storage.local.set(update);
    }
  });
});

// Broadcast changes to open tabs whenever settings change in storage
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes.sadEnabled || changes.trackerEnabled || changes.fstEnabled || changes.sdcEnabled) {
    chrome.storage.local.get(['trackerEnabled', 'sadEnabled', 'fstEnabled', 'sdcEnabled'], (data) => {
      const trackerEnabled = data.trackerEnabled !== false;
      const sadEnabled = data.sadEnabled !== false && trackerEnabled;
      const fstEnabled = data.fstEnabled !== false && trackerEnabled;
      broadcastTrackerState(trackerEnabled, sadEnabled, fstEnabled);
    });
  }
});

ensureConnected();

console.log('Sera SAD: background.js module loaded, registering listeners.');

// Helper to broadcast tracker & SAD state changes to open tabs
function broadcastTrackerState(trackerEnabled, sadEnabled, fstEnabled) {
  const tOn = trackerEnabled !== false;
  const sOn = (sadEnabled !== undefined ? (sadEnabled !== false) : tOn) && tOn;
  const fOn = (fstEnabled !== undefined ? (fstEnabled !== false) : tOn) && tOn;
  chrome.tabs.query({}, (tabs) => {
    for (const tab of tabs) {
      if (!tab.url || tab.url.startsWith('chrome://') || tab.url.startsWith('about:') || tab.url.startsWith('chrome-extension://')) continue;
      try {
        chrome.tabs.sendMessage(tab.id, {
          type: "SERA_TRACKER_STATE_CHANGED",
          trackerEnabled: tOn,
          sadEnabled: sOn,
          fstEnabled: fOn
        }).catch(() => {});
        chrome.tabs.sendMessage(tab.id, {
          type: "SERA_SAD_STATE_CHANGED",
          sadEnabled: sOn,
          trackerEnabled: tOn
        }).catch(() => {});
      } catch (_) {}
    }
  });
}

// SDC (Sera DOM Crosshair): Inject scripts with zero network tampering
function injectSAD(tabId, reason) {
  chrome.storage.local.get(['trackerEnabled', 'fstEnabled', 'sdcEnabled'], (data) => {
    const trackerEnabled = data.trackerEnabled !== false;
    const fstEnabled = data.fstEnabled !== false && trackerEnabled;
    const sdcEnabled = (data.sdcEnabled !== false) && fstEnabled;

    if (!trackerEnabled || !sdcEnabled) {
      return; // All visual and DOM scanning disabled
    }

    console.log(`⚡ Sera SDC: Injecting pure isolated DOM Crosshair engine into tab ${tabId} | reason: ${reason}`);

    // Pure isolated-world crosshair scripts (NO network hooking, NO main world injection)
    const sdcFiles = [
      'sdc/sdc_toast.js',
      'sdc/sdc_core.js',
      'sdc/protocols/itr_protocol.js',
      'sdc/protocols/gst_protocol.js',
      'sdc/protocols/traces_protocol.js',
      'sdc/protocols/mca_protocol.js'
    ];

    chrome.scripting.executeScript({
      target: { tabId: tabId, allFrames: false }, // top frame only for SDC
      files: sdcFiles
    }).catch(err => {
      // Ignored for non-matching or restricted URLs
    });
  });
}

// Inject into ALL open tabs
function injectAllOpenTabs(reason) {
  chrome.tabs.query({}, (tabs) => {
    console.log('Sera SAD: tab scan for injection, found', tabs.length, 'tabs | reason:', reason);
    for (const tab of tabs) {
      if (!tab.url || tab.url.startsWith('chrome://') || tab.url.startsWith('about:') || tab.url.startsWith('chrome-extension://')) continue;
      if (tab.status === 'complete') injectSAD(tab.id, reason || 'startup-scan');
    }
  });
}

// Inject into every tab that finishes loading or updates its SPA URL
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (!tab.url || tab.url.startsWith('chrome://') || tab.url.startsWith('about:') || tab.url.startsWith('chrome-extension://')) return;
  if (changeInfo.status === 'complete' || changeInfo.url) {
    injectSAD(tabId, changeInfo.url ? 'onUpdated-spa-url' : 'onUpdated-complete');
  }
});

// Also scan open tabs on worker startup
injectAllOpenTabs('service-worker-startup');



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
  const isSadNotifEnabled = message.sad_browser_notif_enabled !== false;
  chrome.storage.local.set({ 
    activeAutofillPayload: { ...message, tracker_enabled: isTrackerEnabled, fst_enabled: isFstEnabled, sad_enabled: isSadEnabled, sad_browser_notif_enabled: isSadNotifEnabled, ts: Date.now() },
    trackerEnabled: isTrackerEnabled,
    fstEnabled: isFstEnabled,
    sadEnabled: isSadEnabled,
    sadBrowserNotifEnabled: isSadNotifEnabled
  });

  chrome.tabs.query({}, (tabs) => {
    const existing = tabs.find(t => {
      if (!t.url) return false;
      if (t.url.includes(targetHostname)) return true;
      if (targetHostname.includes('tdscpc.gov.in') && t.url.includes('tdscpc.gov.in')) return true;
      return false;
    });
    
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
    for (const sel of fallbacks) {
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
  let hostname;
  try { hostname = new URL(message.url).hostname; } catch (_) { return; }
  chrome.storage.local.remove(['mecpPayload']);
  chrome.storage.local.set({
    manualAssistPayload: { ...message, expiresAt: Date.now() + (5 * 60 * 1000) }
  });

  // Flutter web apps (e.g. TRACES) fire status="complete" when the HTML shell loads,
  // but Flutter itself bootstraps asynchronously after that. Give it time to render.
  const isFlutterUrl = /tdscpc\.gov\.in|traces\.gov\.in|flutter/i.test(message.url || "");
  const injectDelay = isFlutterUrl ? 3000 : 0;

  chrome.tabs.query({}, tabs => {
    const existing = tabs.find(t => {
      if (!t.url) return false;
      if (t.url.includes(hostname)) return true;
      if (hostname.includes('tdscpc.gov.in') && t.url.includes('tdscpc.gov.in')) return true;
      return false;
    });
    const open = tab => {
      if (!tab) return;
      chrome.windows.update(tab.windowId, { focused: true }, () => { if (chrome.runtime.lastError) {} });
      chrome.tabs.onUpdated.addListener(function listener(tabId, info) {
        if (tabId === tab.id && info.status === "complete") {
          chrome.tabs.onUpdated.removeListener(listener);
          setTimeout(() => injectManualAssist(tab.id, message), injectDelay);
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
        console.warn("Sera: removeCookies error:", chrome.runtime.lastError.message);
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
  // Disarm SCA so it doesn't trigger on the same tab simultaneously as SMTI
  armedSCAPayload = null;
  chrome.storage.local.remove(['armedSCAPayload']);
  chrome.scripting.executeScript({ target:{tabId, allFrames: true}, func:manualAssistWidget,
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
    target: { tabId: tabId, allFrames: true },
    func: fillCredentialsInPage,
    args: [userid, password, usernameSelector, passwordSelector, extensionFlow]
  }).then(() => console.log("Sera: fill script injected"))
    .catch(err => console.error("Sera: inject failed", err));
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  console.log("Sera background: received runtime message:", msg);
  if (msg.type === "CHECK_NATIVE_STATUS") {
    sendResponse({ connected: !!nativePort });
    return true;
  }
  if (msg.type === "RECONNECT_NATIVE_HOST") {
    ensureConnected();
    sendResponse({ connected: !!nativePort });
    return true;
  }
  if (msg.type === "SETTINGS_CHANGED_FROM_POPUP") {
    const s = msg.settings || {};
    if (s.trackerEnabled && (s.sadEnabled || s.fstEnabled)) {
      injectAllOpenTabs('popup-settings-enabled');
    } else {
      broadcastTrackerState(false);
    }
    if (nativePort) {
      try {
        nativePort.postMessage({
          type: "extension_settings_updated",
          sad_enabled: s.sadEnabled,
          fst_enabled: s.fstEnabled,
          tracker_enabled: s.trackerEnabled,
          sad_browser_notif_enabled: s.sadBrowserNotifEnabled,
          sca_enabled: s.scaEnabled
        });
      } catch (_) {}
    }
    sendResponse({ status: "ok" });
    return true;
  }
  if (msg.type === "TRIGGER_MANUAL_ASSIST_FOR_TAB") {
    if (msg.tabId) {
      chrome.storage.local.get(['manualAssistPayload', 'mecpPayload'], data => {
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

// Recover state on service worker restart
chrome.storage.local.get(['armedSCAPayload'], (data) => {
  if (data.armedSCAPayload && data.armedSCAPayload.expiresAt > Date.now()) {
    armedSCAPayload = data.armedSCAPayload;
    const remaining = data.armedSCAPayload.expiresAt - Date.now();
    armedSCATimer = setTimeout(() => {
      clearScaArm();
    }, remaining);
  } else {
    chrome.storage.local.remove(['armedSCAPayload']);
  }
});


function notifyStateChange(state) {
  try {
    if (nativePort && armedSCAPayload) {
      nativePort.postMessage({
        type: "SCA_STATE",
        arm: { ...armedSCAPayload, state: state }
      });
    } else if (nativePort) {
      nativePort.postMessage({
        type: "SCA_STATE",
        arm: { state: state }
      });
    }
  } catch(e) {}
}
function clearScaArm() {
  notifyStateChange("IDLE");
  armedSCAPayload = null;
  if (armedSCATimer) {
    clearTimeout(armedSCATimer);
    armedSCATimer = null;
  }
  chrome.storage.local.remove(['armedSCAPayload']);
}

function handleScaCommand(req, sender, sendResponse) {
  if (req.type === "SCA_PING") {
    return; // Ack was enough
  } else if (req.type === "SCA_STATE_REQUEST") {
    try {
      nativePort.postMessage({
        type: "SCA_STATE",
        arm: armedSCAPayload || { state: "IDLE" }
      });
    } catch(e) {}
  } else if (req.type === "SCA_DISARM_REQUEST") {
    clearScaArm();
  } else if (req.type === "SCA_ARM_REQUEST") {
    chrome.storage.local.get(['scaEnabled'], (data) => {
      if (data.scaEnabled === false) return;
      
      const newArm = req.arm;
      if (!newArm) return;
      
      console.log(`Sera SCA: Coordinator arming for client ${newArm.client_id_token || newArm.client_id}`);
      
      if (armedSCATimer) clearTimeout(armedSCATimer);
      
      armedSCAPayload = newArm;
      
      const remaining = newArm.expires_at - Date.now();
      if (remaining > 0) {
        armedSCATimer = setTimeout(() => clearScaArm(), remaining);
      } else {
        clearScaArm();
        return;
      }
      
      chrome.storage.local.remove(['manualAssistPayload']);
      chrome.storage.local.set({ armedSCAPayload: armedSCAPayload });
      notifyStateChange("ARMED");
    });
  }
}


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
  if (req.type && req.type.startsWith("SCA_")) {
    // Send ACK immediately if it's a request from native host
    if (req.command_id) {
      try {
        nativePort.postMessage({ type: "SCA_ACK", command_id: req.command_id });
      } catch(e) {}
      
      // Check dedup
      if (!self.seenScaCommands) self.seenScaCommands = new Set();
      if (self.seenScaCommands.has(req.command_id)) return;
      self.seenScaCommands.add(req.command_id);
    }
    
    if (req.type === "SCA_ERROR" || req.type === "SCA_FILL_RESULT") {
      try {
        nativePort.postMessage(req);
      } catch(e) {}
      return;
    }
    
    handleScaCommand(req, sender, sendResponse);
  }

  if (req.type === "sca_fill_completed") {
    chrome.storage.local.get(['armedSCAPayload'], (stored) => {
      console.log("Sera SCA: Successful fill reported; consuming one use.");
      const payload = armedSCAPayload || stored.armedSCAPayload;
      if (!payload || payload.fillCompletionHandled) return;
      payload.fillCompletionHandled = true;
      payload.fillInProgress = false;
      payload.remainingUses = Math.max(0, Number(payload.remainingUses || 1) - 1);
      if (payload.remainingUses <= 0) {
        clearScaArm();
      } else {
        payload.fillCompletionHandled = false;
        armedSCAPayload = payload;
        chrome.storage.local.set({ armedSCAPayload: payload });
      }
    });
    return;
  }

  if (req.type === "sca_paste_matched" || req.type === "SCA_MATCH_CANDIDATE") {
    console.log("Sera SCA: UID paste detected on portal", req.portal, "tab", sender.tab ? sender.tab.id : "unknown");
    if (!sender.tab || !sender.tab.id) return;

    chrome.storage.local.get(['armedSCAPayload', 'scaEnabled', 'scaMode', 'manualAssistPayload'], (data) => {
      if (data.scaEnabled === false) return;
      // Don't trigger SCA if SMTI (Manual Assist) widget is currently active
      const smtiActive = data.manualAssistPayload && data.manualAssistPayload.expiresAt && data.manualAssistPayload.expiresAt > Date.now();
      if (smtiActive) {
        console.log("Sera SCA: Skipping — SMTI (Manual Assist) is currently active on this tab.");
        return;
      }
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
            target: { tabId: sender.tab.id, allFrames: true },
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
            target: { tabId: sender.tab.id, allFrames: true },
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
