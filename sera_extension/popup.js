// popup.js - Project Sera Extension Companion Toolbar UI Logic

document.addEventListener('DOMContentLoaded', () => {
  const toggleFst = document.getElementById('toggle-fst');
  const toggleVsdc = document.getElementById('toggle-vsdc');
  const vsdcBadge = document.getElementById('vsdc-badge');
  const toggleSds = document.getElementById('toggle-sds');
  const toggleToast = document.getElementById('toggle-toast');
  const toggleSca = document.getElementById('toggle-sca');
  const statusDot = document.getElementById('status-dot');
  const statusLabel = document.getElementById('status-label');
  const statusDetail = document.getElementById('status-detail');
  const btnReconnect = document.getElementById('btn-reconnect');
  const btnManualAssist = document.getElementById('btn-manual-assist');
  const syncStatus = document.getElementById('sync-status');
  const versionBadge = document.getElementById('version-badge');

  // Load manifest version
  try {
    const manifest = chrome.runtime.getManifest();
    if (manifest && manifest.version) {
      versionBadge.textContent = `v${manifest.version}`;
    }
  } catch (_) {}

  function updateVsdcBadge(active) {
    if (!vsdcBadge) return;
    if (active) {
      vsdcBadge.textContent = 'Active';
      vsdcBadge.className = 'pill pill-green';
      vsdcBadge.style.background = '';
      vsdcBadge.style.color = '';
      vsdcBadge.style.border = '';
    } else {
      vsdcBadge.textContent = 'Off';
      vsdcBadge.className = 'pill';
      vsdcBadge.style.background = 'rgba(107, 114, 128, 0.15)';
      vsdcBadge.style.color = '#9ca3af';
      vsdcBadge.style.border = '1px solid rgba(107, 114, 128, 0.3)';
    }
  }

  // 1. Load current extension settings from storage
  function loadSettings() {
    chrome.storage.local.get([
      'trackerEnabled',
      'fstEnabled',
      'sdcEnabled',
      'vsdcEnabled',
      'sdsEnabled',
      'sdcToastEnabled',
      'scaEnabled',
      'manualAssistPayload',
      'mecpPayload'
    ], (data) => {
      const tracker = data.trackerEnabled !== false;
      const sdc = (data.sdcEnabled !== false) && tracker;
      const vsdc = data.vsdcEnabled !== false;
      const sds = false; // Paused
      const toast = data.sdcToastEnabled !== false;
      const sca = data.scaEnabled !== false;

      if (toggleFst) toggleFst.checked = sdc;
      if (toggleVsdc) toggleVsdc.checked = vsdc;
      updateVsdcBadge(vsdc);
      if (toggleSds) toggleSds.checked = false;
      if (toggleToast) toggleToast.checked = toast;
      if (toggleSca) toggleSca.checked = sca;

      // Update manual assist button status
      const hasPendingAssist = (data.manualAssistPayload && data.manualAssistPayload.expiresAt > Date.now()) ||
                               (data.mecpPayload && data.mecpPayload.expiresAt > Date.now());
      if (btnManualAssist) {
        if (hasPendingAssist) {
          btnManualAssist.style.opacity = '1.0';
          btnManualAssist.title = 'Active credential assistance ready for current portal tab';
        } else {
          btnManualAssist.style.opacity = '0.75';
          btnManualAssist.title = 'No active assistant queue. Trigger manual login assist.';
        }
      }
    });
  }

  // 2. Check Host Connection
  function checkHostConnection() {
    statusDot.className = 'status-dot';
    statusLabel.textContent = 'Checking host...';
    statusDetail.textContent = 'Connecting to 127.0.0.1';

    chrome.runtime.sendMessage({ type: 'CHECK_NATIVE_STATUS' }, (resp) => {
      if (chrome.runtime.lastError || !resp || !resp.connected) {
        statusDot.className = 'status-dot disconnected';
        statusLabel.textContent = 'Desktop App Offline';
        statusDetail.textContent = 'Launch Project Sera Desktop';
      } else {
        statusDot.className = 'status-dot connected';
        statusLabel.textContent = 'Connected to Sera Host';
        statusDetail.textContent = resp.mode === 'native' ? 'Active Sync: Native Host' : 'Active Sync: Local Bridge (Port 49152)';
      }
    });
  }

  // 3. Save Settings on Toggle
  function saveSettings(changedKey) {
    syncStatus.textContent = 'Saving...';
    syncStatus.style.color = '#f59e0b';

    const sdcVal = toggleFst ? toggleFst.checked : true;
    const vsdcVal = toggleVsdc ? toggleVsdc.checked : true;
    const sdsVal = toggleSds ? toggleSds.checked : true;
    const toastVal = toggleToast ? toggleToast.checked : true;
    const scaVal = toggleSca ? toggleSca.checked : true;

    updateVsdcBadge(vsdcVal);

    const storageUpdate = {
      sadEnabled: false, // Permanently purged
      fstEnabled: sdcVal,
      sdcEnabled: sdcVal,
      vsdcEnabled: vsdcVal,
      sdsEnabled: sdsVal,
      sdcToastEnabled: toastVal,
      trackerEnabled: sdcVal || sdsVal || vsdcVal,
      sadBrowserNotifEnabled: false,
      scaEnabled: scaVal
    };

    chrome.storage.local.set(storageUpdate, () => {
      syncStatus.textContent = 'Synced';
      syncStatus.style.color = '#10b981';

      // Notify background service worker to update open tabs and sync to native host
      chrome.runtime.sendMessage({
        type: 'SETTINGS_CHANGED_FROM_POPUP',
        settings: storageUpdate
      });

      setTimeout(() => {
        syncStatus.textContent = 'Saved';
        syncStatus.style.color = '#6b7280';
      }, 1500);
    });
  }

  // Attach toggle change listeners
  if (toggleFst) toggleFst.addEventListener('change', () => saveSettings('fstEnabled'));
  if (toggleVsdc) toggleVsdc.addEventListener('change', () => saveSettings('vsdcEnabled'));
  if (toggleSds) toggleSds.addEventListener('change', () => saveSettings('sdsEnabled'));
  if (toggleToast) toggleToast.addEventListener('change', () => saveSettings('sdcToastEnabled'));
  if (toggleSca) toggleSca.addEventListener('change', () => saveSettings('scaEnabled'));

  // Reconnect button
  btnReconnect.addEventListener('click', () => {
    chrome.runtime.sendMessage({ type: 'RECONNECT_NATIVE_HOST' }, () => {
      setTimeout(checkHostConnection, 400);
    });
  });

  // Manual Assist Button Trigger
  btnManualAssist.addEventListener('click', () => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs && tabs[0]) {
        chrome.runtime.sendMessage({
          type: 'TRIGGER_MANUAL_ASSIST_FOR_TAB',
          tabId: tabs[0].id,
          url: tabs[0].url
        });
      }
      window.close();
    });
  });

  // Listen for storage changes while popup is open
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'local') {
      loadSettings();
    }
  });

  // Initialize
  loadSettings();
  checkHostConnection();
});
