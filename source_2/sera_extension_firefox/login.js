let _lastScaTriggerUid = "";
let _lastScaTriggerTime = 0;

window.addEventListener('message', (event) => {
  try {
    if (event.source === window && event.data && event.data.source === 'sera_sca' && event.data.type === 'filled') {
      chrome.runtime.sendMessage({ type: 'sca_fill_completed' });
    }
  } catch (_) {}
});

function checkAndTriggerSCA(candidateText) {
  if (!candidateText) return;
  const clean = candidateText.trim().toUpperCase();
  if (clean.length < 3 || clean.length > 100) return;

  const now = Date.now();
  if (_lastScaTriggerUid === clean && (now - _lastScaTriggerTime) < 1200) {
    return;
  }

  chrome.storage.local.get(['armedSCAPayload', 'scaEnabled'], (data) => {
    if (data.scaEnabled === false) return;
    const armed = data.armedSCAPayload;
    if (armed && armed.expiresAt > Date.now()) {
      const targetUid = (armed.matched_uid || '').toUpperCase();
      const candidates = (armed.candidate_uids && Array.isArray(armed.candidate_uids) ? armed.candidate_uids : [targetUid])
        .map(u => String(u || '').trim().toUpperCase())
        .filter(Boolean);

      const isMatch = candidates.some(uid => (
        clean === uid ||
        clean.includes(uid) ||
        (clean.length >= 5 && uid.includes(clean))
      ));

      if (isMatch) {
        _lastScaTriggerUid = clean;
        _lastScaTriggerTime = Date.now();
        console.log("Sera SCA: Detected matching UID entry:", clean, "on portal", window.location.hostname);
        chrome.runtime.sendMessage({
          type: "sca_paste_matched",
          matched_uid: clean,
          portal: window.location.hostname
        });
      }
    }
  });
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

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "autofill" && message.userid) {
    console.log("Sera content script: received fallback autofill message.");
    // Simple one-shot fill, no observers
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
