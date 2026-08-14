// Project Sera: Lightweight content script.
// Autofill is now handled by direct injection from background.js.
// This script only listens for explicit messages as a fallback.

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