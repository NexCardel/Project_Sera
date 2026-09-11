// Production debug gate — set to true only during local development
const SERA_DEBUG = false;

let nativePort = null;
const sdcInjectedTabs = new Set();

function connectToNativeHost() {
  if (nativePort !== null) return;
  const hostName = "com.amanassociates.sera";
  try {
    nativePort = chrome.runtime.connectNative(hostName);
    if (SERA_DEBUG) console.log('Sera native host connection established');
    nativePort.onMessage.addListener((message) => {
      if (SERA_DEBUG) console.log("Received from Sera desktop:", message);
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
        const sdc = message.sdc_enabled !== undefined ? (message.sdc_enabled !== false && message.tracker_enabled !== false) : fst;
        const sad = message.sad_enabled !== false && message.tracker_enabled !== false;
        const sadNotif = message.sad_browser_notif_enabled !== false;
        const sca = message.sca_enabled !== false;
        const scaMode = message.sca_mode || "autofill";
        const allowedDomains = message.allowed_domains || [];
        const overallTracker = sdc || fst || sad;
        const storageObj = {
          trackerEnabled: overallTracker,
          sdcEnabled: sdc,
          fstEnabled: fst,
          sadEnabled: sad,
          sadBrowserNotifEnabled: sadNotif,
          scaEnabled: sca,
          scaMode: scaMode
        };
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
        if (SERA_DEBUG) console.log("Sera desktop host status:", err.message || "Native host inactive");
      } else {
        if (SERA_DEBUG) console.log("Disconnected from Sera desktop app");
      }
      nativePort = null;
      setTimeout(ensureConnected, 5000);
    });

  } catch (e) {
    if (SERA_DEBUG) console.error("Failed to connect to native host:", e);
    nativePort = null;
  }
}

async function syncSettingsFromDesktop() {
  try {
    const resp = await fetch('http://127.0.0.1:49152', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'request_settings' })
    });
    if (!resp.ok) return null;
    const data = await resp.json();
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
    if (data.sca_mode) {
      storageObj.scaMode = data.sca_mode;
    }
    if (data.sca_enabled !== undefined) {
      storageObj.scaEnabled = !!data.sca_enabled;
    }
    if (data.sdc_enabled !== undefined) {
      storageObj.sdcEnabled = !!data.sdc_enabled;
    } else {
      storageObj.sdcEnabled = true;
    }
    if (data.fst_enabled !== undefined) {
      storageObj.fstEnabled = !!data.fst_enabled;
    } else {
      storageObj.fstEnabled = true;
    }
    if (data.sad_enabled !== undefined) {
      storageObj.sadEnabled = !!data.sad_enabled;
    }
    if (data.tracker_enabled !== undefined) {
      storageObj.trackerEnabled = !!data.tracker_enabled;
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

function ensureConnected() {
  if (!nativePort) connectToNativeHost();
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

async function sendToDesktop(msg, requireHttpAck = false) {
  let sent = false;
  if (!requireHttpAck && !nativePort) {
    ensureConnected();
  }
  if (!requireHttpAck && nativePort) {
    try {
      nativePort.postMessage(msg);
      sent = true;
    } catch (e) {
      if (SERA_DEBUG) console.warn("Sera background: native postMessage failed, falling back to HTTP:", e);
    }
  }
  if (!sent) {
    try {
      const resp = await fetch('http://127.0.0.1:49152', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(msg)
      });
      sent = resp.ok;
      if (sent) {
        if (SERA_DEBUG) console.log("Sera background: successfully delivered to desktop via HTTP port 49152");
      }
    } catch (err) {
      if (SERA_DEBUG) console.warn("Sera background: direct HTTP delivery failed:", err);
    }
  }
  return sent;
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
  chrome.storage.local.get(['trackerEnabled', 'sadEnabled', 'fstEnabled', 'sdcEnabled'], (data) => {
    const update = {};
    if (data.trackerEnabled === undefined) update.trackerEnabled = true;
    if (data.sadEnabled === undefined) update.sadEnabled = true;
    if (data.fstEnabled === undefined) update.fstEnabled = true;
    if (data.sdcEnabled === undefined) update.sdcEnabled = true;
    if (Object.keys(update).length > 0) {
      chrome.storage.local.set(update);
    }
  });
});

// Broadcast changes to open tabs whenever settings change in storage
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes.sadEnabled || changes.trackerEnabled || changes.fstEnabled || changes.sdcEnabled || changes.sdsEnabled) {
    chrome.storage.local.get(['trackerEnabled', 'sadEnabled', 'fstEnabled', 'sdcEnabled', 'sdsEnabled'], (data) => {
      const trackerEnabled = data.trackerEnabled !== false;
      const sadEnabled = data.sadEnabled !== false && trackerEnabled;
      const fstEnabled = data.fstEnabled !== false && trackerEnabled;
      broadcastTrackerState(trackerEnabled, sadEnabled, fstEnabled);
    });
  }
});

ensureConnected();

if (SERA_DEBUG) console.log('Sera SAD: background.js module loaded, registering listeners.');

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
  if (sdcInjectedTabs.has(tabId)) {
    if (SERA_DEBUG) console.log(`⚡ Sera SDC: Tab ${tabId} already injected — skipping duplicate injection.`);
    return;
  }
  // Reserve the tab before the asynchronous settings lookup to prevent two
  // concurrent injection requests from both passing the guard.
  sdcInjectedTabs.add(tabId);
  chrome.storage.local.get(['trackerEnabled', 'fstEnabled', 'sdcEnabled', 'sdsEnabled'], (data) => {
    const trackerEnabled = data.trackerEnabled !== false;
    const sdcEnabled = (data.sdcEnabled !== false) && trackerEnabled;
    const fstEnabled = (data.fstEnabled !== false) && trackerEnabled;
    const sdsEnabled = false; // SDS Paused

    if (!trackerEnabled || (!sdcEnabled && !fstEnabled)) {
      sdcInjectedTabs.delete(tabId);
      return; // All visual and DOM scanning disabled
    }

    if (SERA_DEBUG) console.log(`⚡ Sera SDC: Injecting pure isolated DOM Crosshair engine into tab ${tabId} | reason: ${reason}`);

    // Pure isolated-world scripts (NO network hooking, NO main world injection)
    const sdcFiles = [
      'sdc/sdc_toast.js',
      'sdc/sdc_core.js'
    ];

    if (sdcEnabled) {
      sdcFiles.push(
        'sdc/protocols/itr_protocol.js',
        'sdc/protocols/gst_protocol.js',
        'sdc/protocols/traces_protocol.js',
        'sdc/protocols/mca_protocol.js'
      );
    }

    chrome.scripting.executeScript({
      target: { tabId: tabId, allFrames: false }, // top frame only
      files: sdcFiles
    }).catch(err => {
      // Ignored for non-matching or restricted URLs
    });
  });
}

// Inject into ALL open tabs
function injectAllOpenTabs(reason) {
  chrome.tabs.query({}, (tabs) => {
    if (SERA_DEBUG) console.log('Sera SAD: tab scan for injection, found', tabs.length, 'tabs | reason:', reason);
    for (const tab of tabs) {
      if (!tab.url || tab.url.startsWith('chrome://') || tab.url.startsWith('about:') || tab.url.startsWith('chrome-extension://')) continue;
      if (tab.status === 'complete') injectSAD(tab.id, reason || 'startup-scan');
    }
  });
}

let sccActiveAttempt = null;
chrome.storage.local.get(['sccActiveAttempt'], d => {
  if (d && d.sccActiveAttempt) sccActiveAttempt = d.sccActiveAttempt;
});

// Inject into every tab that finishes loading or updates its SPA URL
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (!tab.url || tab.url.startsWith('chrome://') || tab.url.startsWith('about:') || tab.url.startsWith('chrome-extension://')) return;
  if (changeInfo.status === 'complete') {
    // A full document load creates a new execution context; reinject once.
    sdcInjectedTabs.delete(tabId);
    injectSAD(tabId, 'onUpdated-complete');

    // Re-inject Manual Assist if tab is on login page and assist is active (e.g. after invalid password page reload)
    chrome.storage.local.get(['manualAssistPayload'], data => {
      const p = data.manualAssistPayload;
      if (p && p.expiresAt && p.expiresAt > Date.now()) {
        const curUrl = (tab.url || '').toLowerCase();
        let targetHost = '';
        try { targetHost = new URL(p.url).hostname.toLowerCase(); } catch (_) {}
        const matchesHost = targetHost ? curUrl.includes(targetHost) : true;
        const isLogin = curUrl.includes('/login') || curUrl.includes('/auth') || curUrl.includes('/signin') || curUrl.includes('unifiedportal') || curUrl.includes('tdscpc');
        const isPostLogin = curUrl.includes('/dashboard') || curUrl.includes('/home') || curUrl.includes('/welcome') || curUrl.includes('/landing') || curUrl.includes('/portal') || curUrl.includes('/main') || curUrl.includes('/profile');
        if (matchesHost && isLogin && !isPostLogin) {
          setTimeout(() => {
            injectManualAssist(tabId, p);
          }, 700);
        } else if (matchesHost && isPostLogin) {
          chrome.storage.local.remove(['manualAssistPayload']);
        }
      }
    });
  } else if (changeInfo.url) {
    // SPA navigation is already handled by sdc_core's URL watcher.
    if (SERA_DEBUG) console.log(`⚡ Sera SDC: SPA URL changed in tab ${tabId} — keeping existing injection.`);
  }

  // ── SCC Webpage Link Mutation Observer (Income Tax / ITR Only) ───────────
  chrome.storage.local.get(['sccActiveAttempt'], (data) => {
    const attempt = data.sccActiveAttempt || sccActiveAttempt;
    if (!attempt || !attempt.password) return;

    const now = Date.now();
    if (now - (attempt.timestamp || 0) > 10 * 60 * 1000) {
      sccActiveAttempt = null;
      chrome.storage.local.remove(['sccActiveAttempt']);
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
      chrome.storage.local.remove(['sccActiveAttempt', 'mecpPayload']);

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

chrome.tabs.onRemoved.addListener((tabId) => {
  sdcInjectedTabs.delete(tabId);
});

// Also scan open tabs on worker startup
injectAllOpenTabs('service-worker-startup');



// Fill function injected into the page
function fillCredentialsInPage(userid, password, usernameSelector, passwordSelector, extensionFlow) {
  if (window.__seraFillActive) return; // prevent duplicate runs
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

    // Position cursor cleanly at the end without leaving text selected
    try {
      const len = (value || "").length;
      if (typeof el.setSelectionRange === "function") {
        el.setSelectionRange(len, len);
      }
    } catch (_) {}
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
            if (SERA_DEBUG) console.log("Sera: Auto-clicking Continue/Login button:", text);
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

        // Auto-click Continue/Login after a delay for Angular to process
        setTimeout(() => {
          autoClickContinue();
        }, 600);

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
  try {
    if (window.self !== window.top) return;
  } catch (_) {
    return;
  }

  // Do not show widget if user is already logged in or page is on post-login dashboard/portal
  try {
    const curPageUrl = (window.location.href || "").toLowerCase();
    const isPostLoginUrl = curPageUrl.includes('/dashboard') || curPageUrl.includes('/home') || curPageUrl.includes('/welcome') || curPageUrl.includes('/landing') || curPageUrl.includes('/portal') || curPageUrl.includes('/main') || curPageUrl.includes('/profile');
    const isExplicitLoginUrl = curPageUrl.includes('/login') || curPageUrl.includes('/auth') || curPageUrl.includes('/signin');
    if (isPostLoginUrl && !isExplicitLoginUrl) {
      if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
        chrome.storage.local.remove(['manualAssistPayload']);
      }
      if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
        chrome.runtime.sendMessage({ type: "MANUAL_ASSIST_CLEAR" });
      }
      return;
    }
    if (document.querySelector("a[href*='logout'], button[id*='logout'], .user-profile, app-dashboard")) {
      if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
        chrome.storage.local.remove(['manualAssistPayload']);
      }
      if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
        chrome.runtime.sendMessage({ type: "MANUAL_ASSIST_CLEAR" });
      }
      return;
    }
  } catch (_) {}

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
      if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
        chrome.storage.local.remove(['manualAssistPayload']);
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

  // Action Buttons
  const actions = document.createElement("div");
  actions.className = "actions";

  const uidBtn = document.createElement("button");
  uidBtn.className = "btn primary";
  uidBtn.innerHTML = "👤  Username";

  const passBtn = document.createElement("button");
  passBtn.className = "btn primary";
  passBtn.innerHTML = "🔑  Password";
  passBtn.onclick = () => {
    resetTimer();
    const result = smartFill(password, passwordSelector, passFallbacks);
    try {
      if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
        chrome.storage.local.remove(['manualAssistPayload']);
      }
      if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
        chrome.runtime.sendMessage({ type: "MANUAL_ASSIST_CLEAR" });
      }
    } catch (_) {}
    if (result === "filled") {
      setBtn(passBtn, "done", "✓  Password Injected");
      setTimeout(dismiss, 400);
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

  function fill(el, value, selector, fallbacks) {
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
        const freshEl = (selector && findField(selector, fallbacks || [])) || el;
        if (freshEl && freshEl.value !== value) {
          applyValue(freshEl, value);
        }
      } catch (_) {}
    }, 60);

    setTimeout(() => {
      try {
        const freshEl = (selector && findField(selector, fallbacks || [])) || el;
        if (freshEl && freshEl.value !== value) {
          applyValue(freshEl, value);
        }
      } catch (_) {}
    }, 180);

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
    let el = findField(selector, fallbacks);
    if (!el && selector && (selector.includes("password") || selector.includes("psw") || selector.includes("pass"))) {
      const passCandidates = document.querySelectorAll("input[type='password'], input[id*='password'], input[name*='password'], input[id*='psw'], input[name*='psw'], #user_pass");
      for (const p of passCandidates) {
        if (visible(p)) { el = p; break; }
      }
    }
    if (!el) {
      const passInputs = document.querySelectorAll("input[type='password']");
      for (const p of passInputs) {
        if (visible(p)) { el = p; break; }
      }
    }
    if (el && fill(el, value, selector, fallbacks)) return "filled";

    const fltEl = getFlutterActiveInput();
    if (fltEl) {
      if (execInsert(fltEl, value)) return "filled";
      if (fill(fltEl, value, selector, fallbacks)) return "filled";
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
          try {
            if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
              chrome.storage.local.remove(['manualAssistPayload']);
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

  uidBtn.onclick = () => {
    resetTimer();
    const result = smartFill(userid, usernameSelector, userFallbacks);
    if (result === "filled") {
      setBtn(uidBtn, "done", "✓  Username Injected");
      setTimeout(() => setBtn(uidBtn, "", "👤  Username"), 2000);
    } else {
      setBtn(uidBtn, "done", "📋  Copied Username (Ctrl+V)");
      setTimeout(() => setBtn(uidBtn, "", "👤  Username"), 2500);
    }
  };
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
      let listenerFired = false;
      const listener = (tabId, info) => {
        if (tabId === tab.id && info.status === "complete" && !listenerFired) {
          listenerFired = true;
          chrome.tabs.onUpdated.removeListener(listener);
          setTimeout(() => injectManualAssist(tab.id, message), injectDelay);
        }
      };
      chrome.tabs.onUpdated.addListener(listener);
      setTimeout(() => {
        try { chrome.tabs.onUpdated.removeListener(listener); } catch (_) {}
      }, 30000);

      if (tab.status === "complete") {
        setTimeout(() => injectManualAssist(tab.id, message), injectDelay);
      }
      chrome.tabs.update(tab.id, { url: message.url, active: true }, () => { if (chrome.runtime.lastError) {} });
    };
    if (existing) open(existing); else chrome.tabs.create({ url: message.url }, open);

  });
}

function recordInjectionAndClearCookiesIfNeeded() {
  chrome.storage.local.get({ injectionCount: 0 }, (data) => {
    let newCount = (data.injectionCount || 0) + 1;
    if (SERA_DEBUG) console.log(`Sera: Extension injection count = ${newCount}/5`);
    
    if (newCount >= 5) {
      if (SERA_DEBUG) console.log("Sera: Reached 5 extension injections. Clearing browser cookies...");
      clearBrowserCookies(() => {
        if (SERA_DEBUG) console.log("Sera: Browser cookies cleared successfully after 5 injections.");
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
        if (SERA_DEBUG) console.warn("Sera: removeCookies error:", chrome.runtime.lastError.message);
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

const _lastManualAssistInject = {};
function injectManualAssist(tabId, message) {
  if (!tabId) return;
  const now = Date.now();
  if (_lastManualAssistInject[tabId] && (now - _lastManualAssistInject[tabId]) < 1000) {
    return;
  }
  _lastManualAssistInject[tabId] = now;

  recordInjectionAndClearCookiesIfNeeded();
  // Disarm SCA so it doesn't trigger on the same tab simultaneously as SMTI
  armedSCAPayload = null;
  chrome.storage.local.remove(['armedSCAPayload']);

  chrome.scripting.executeScript({ target:{ tabId }, func:manualAssistWidget,
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
  if (SERA_DEBUG) console.log("Sera background: received runtime message:", msg);
  if (msg.type === "MANUAL_ASSIST_CLEAR" || msg.type === "MANUAL_ASSIST_DONE" || msg.type === "MANUAL_ASSIST_DISMISSED") {
    chrome.storage.local.remove(['manualAssistPayload']);
    sendResponse({ ok: true });
    return true;
  }
  if (msg.type === "CHECK_NATIVE_STATUS") {
    if (nativePort) {
      sendResponse({ connected: true, mode: "native" });
      return true;
    }
    fetch('http://127.0.0.1:49152', { method: 'OPTIONS' })
      .then(r => sendResponse({ connected: r.ok, mode: "http" }))
      .catch(() => sendResponse({ connected: false }));
    return true;
  }
  if (msg.type === "RECONNECT_NATIVE_HOST") {
    ensureConnected();
    if (nativePort) {
      sendResponse({ connected: true, mode: "native" });
      return true;
    }
    fetch('http://127.0.0.1:49152', { method: 'OPTIONS' })
      .then(r => sendResponse({ connected: r.ok, mode: "http" }))
      .catch(() => sendResponse({ connected: false }));
    return true;
  }
  if (msg.type === "SETTINGS_CHANGED_FROM_POPUP") {
    const s = msg.settings || {};
    if (s.trackerEnabled && (s.sdcEnabled || s.fstEnabled || s.sadEnabled)) {
      injectAllOpenTabs('popup-settings-enabled');
    } else {
      broadcastTrackerState(false);
    }
    sendToDesktop({
      type: "extension_settings_updated",
      sdc_enabled: s.sdcEnabled,
      fst_enabled: s.fstEnabled,
      sad_enabled: s.sadEnabled,
      tracker_enabled: s.trackerEnabled,
      sad_browser_notif_enabled: s.sadBrowserNotifEnabled,
      sca_enabled: s.scaEnabled
    });
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
  if ((msg.type === "SCC_PASSWORD_INJECTED" || msg.type === "SCC_PASSWORD_COPIED") && msg.payload) {
    const tId = (sender && sender.tab) ? sender.tab.id : null;
    const tUrl = (sender && sender.tab) ? sender.tab.url : "";
    sccActiveAttempt = {
      ...msg.payload,
      tabId: tId,
      initial_url: tUrl,
      timestamp: Date.now()
    };
    chrome.storage.local.set({ sccActiveAttempt });
    sendResponse({ status: "ok" });
    return true;
  }
  if (msg.type === "MECP_DISMISSED") {
    chrome.storage.local.remove(['mecpPayload', 'sccActiveAttempt']);
    sccActiveAttempt = null;
    sendResponse({ status: "ok" });
    return true;
  }
  if (msg.type === "SCC_LOGIN_DETECTED" && msg.attempt) {
    const attempt = msg.attempt;
    sccActiveAttempt = null;
    chrome.storage.local.remove(['sccActiveAttempt']);
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
  if (msg.type === "filing_result" || msg.type === "filing_result_compressed") {
    if (SERA_DEBUG) console.log("Sera background: handling filing_result, sending to desktop...");
    // Keep the MV3 service worker alive until the final assembler payload has
    // actually been forwarded to the desktop host.
    // The HTTP listener returns 200 only after the desktop has accepted the
    // payload. Native postMessage has no receipt acknowledgement, so it is
    // not sufficient for clearing the durable assembler outbox.
    sendToDesktop(msg, true).then((sent) => {
      if (sent) chrome.storage.local.remove(['trackingTabId', 'activeAutofillPayload']);
      else console.warn("Sera background: filing_result was not delivered to desktop.");
      sendResponse({ status: sent ? "accepted" : "failed" });
    }).catch((err) => {
      if (SERA_DEBUG) console.warn("Sera background: filing_result delivery error:", err);
      sendResponse({ status: "failed" });
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

function mecpWidget(userid, password, clientName, expiresMs, sccMode, sccCombos, clientId, portal, unregisteredPan) {
  const hostId = "sera-mecp-host";
  const old = document.getElementById(hostId);
  if (old) old.remove();
  const smtiOld = document.getElementById("sera-manual-assist-host");
  if (smtiOld) smtiOld.remove();

  const host = document.createElement("div");
  host.id = hostId;
  const shadow = host.attachShadow({ mode: "open" });

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
  close.onclick = () => {
    try { chrome.storage.local.remove(['sccActiveAttempt', 'mecpPayload']); } catch (_) {}
    try { chrome.runtime.sendMessage({ type: "MECP_DISMISSED" }); } catch (_) {}
    if (host.isConnected) host.remove();
  };
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

  // User ID Row (rendered only if userid is provided)
  if (userid && String(userid).trim()) {
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

  box.appendChild(toast);
  shadow.appendChild(box);
  document.documentElement.appendChild(host);

  setTimeout(() => { if (host.isConnected) host.remove(); }, expiresMs || 90000);
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
    args: [
      message.userid || "",
      message.password || "",
      message.client_name || message.portal,
      90000,
      message.scc_mode === true,
      message.scc_combos || [],
      message.client_id || null,
      message.portal || "Income Tax",
      message.unregistered_pan || ""
    ]
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
      
      if (SERA_DEBUG) console.log(`Sera SCA: Coordinator arming for client ${newArm.client_id_token || newArm.client_id}`);
      
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
      if (SERA_DEBUG) console.log("Sera SCA: SCA is disabled in settings. Skipping arm.");
      return;
    }
    if (SERA_DEBUG) console.log("Sera SCA: Silently arming password for client", message.client_id_token || message.client_id);
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
      if (SERA_DEBUG) console.log("Sera SCA: Armed state expired.");
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
      if (SERA_DEBUG) console.log("Sera SCA: Successful fill reported; consuming one use.");
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
    if (SERA_DEBUG) console.log("Sera SCA: UID paste detected on portal", req.portal, "tab", sender.tab ? sender.tab.id : "unknown");
    if (!sender.tab || !sender.tab.id) return;

    chrome.storage.local.get(['armedSCAPayload', 'scaEnabled', 'scaMode', 'manualAssistPayload'], (data) => {
      if (data.scaEnabled === false) return;
      // Don't trigger SCA if SMTI (Manual Assist) widget is currently active
      const smtiActive = data.manualAssistPayload && data.manualAssistPayload.expiresAt && data.manualAssistPayload.expiresAt > Date.now();
      if (smtiActive) {
        if (SERA_DEBUG) console.log("Sera SCA: Skipping — SMTI (Manual Assist) is currently active on this tab.");
        return;
      }
      const payload = data.armedSCAPayload || armedSCAPayload;
      if (!payload || !payload.expiresAt || payload.expiresAt < Date.now()) {
        if (SERA_DEBUG) console.log("Sera SCA: No active armed payload found for paste event.");
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
                try {
                  const len = (val || "").length;
                  if (typeof el.setSelectionRange === "function") {
                    el.setSelectionRange(len, len);
                  }
                } catch (_) {}
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
                try {
                  if (window.self !== window.top) return;
                } catch (_) {
                  return;
                }
                const hostId = "sera-sca-widget-host";
                const old = document.getElementById(hostId);
                if (old) return;
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
            sendToDesktop({
              type: "audit_event",
              action: "SCA widget armed",
              client_id: payload.client_id,
              detail: `SCA widget armed — client ${payload.client_id_token || payload.client_id} — portal ${matchedService.name || 'Portal'}`
            });
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
                try {
                  const len = (val || "").length;
                  if (typeof el.setSelectionRange === "function") {
                    el.setSelectionRange(len, len);
                  }
                } catch (_) {}
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
                    if (SERA_DEBUG) console.log("Sera SCA: Password filled safely & notification banner displayed.");
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
            sendToDesktop({
              type: "audit_event",
              action: "SCA autofill triggered",
              client_id: payload.client_id,
              detail: `SCA ambient autofill — client ${payload.client_id_token || payload.client_id} — portal ${matchedService.name || 'Portal'}`
            });
          }).catch(err => console.error("Sera SCA: Injection error", err));
        }
      }
    });
  }
});
