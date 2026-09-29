// popup.js - Project Sera Extension Companion Toolbar UI Logic

document.addEventListener('DOMContentLoaded', () => {
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

  // 1. Load current extension settings from storage
  // manualAssistPayload / mecpPayload carry a password and live in chrome.storage.session, not
  // .local (finding #2). No session storage (old Firefox) -> just show the "no queue" state.
  function loadSettings() {
    chrome.storage.local.get(['scaEnabled'], (data) => {
      const sca = data.scaEnabled !== false;
      if (toggleSca) toggleSca.checked = sca;
    });

    const showAssistStatus = (data) => {
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
    };
    if (chrome.storage.session) {
      chrome.storage.session.get(['manualAssistPayload', 'mecpPayload'], showAssistStatus);
    } else {
      showAssistStatus({});
    }
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
        statusDetail.textContent = 'Active Sync: WebSocket Bridge';
      }
    });
  }

  // 3. Save Settings on Toggle
  function saveSettings(changedKey) {
    syncStatus.textContent = 'Saving...';
    syncStatus.style.color = '#f59e0b';

    const scaVal = toggleSca ? toggleSca.checked : true;

    const storageUpdate = {
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
