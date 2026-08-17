function checkAndTriggerSCA(candidateText) {
  if (!candidateText) return;
  const clean = candidateText.trim().toUpperCase();
  if (clean.length < 3 || clean.length > 35) return;

  chrome.storage.local.get(['armedSCAPayload', 'scaEnabled'], (data) => {
    if (data.scaEnabled === false) return;
    const armed = data.armedSCAPayload;
    if (armed && armed.matched_uid && armed.expiresAt > Date.now()) {
      if (clean === armed.matched_uid.toUpperCase() || clean.includes(armed.matched_uid.toUpperCase())) {
        console.log("Sera SCA: Detected matching UID entry:", clean);
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
    var userField = document.querySelector("#panAdhaarUserId") ||
                    document.querySelector("#username") ||
                    document.querySelector("input[name='pan']");
    if (userField && message.userid) {
      userField.focus();
      userField.value = message.userid;
      userField.dispatchEvent(new Event('input', { bubbles: true }));
    }

    var passField = document.querySelector("#passwordInput") ||
                    document.querySelector("input[type='password']") ||
                    document.querySelector("#user_pass");
    if (passField && message.password) {
      passField.focus();
      passField.value = message.password;
      passField.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
});