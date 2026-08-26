// tracker.js - Injected into all pages to monitor for filing success

(function() {
  console.log("Sera FST: tracker.js injected and running...");
  let context = null;
  let hasFired = false;

  // net_interceptor.js is injected into the MAIN world by background.js
  // via chrome.scripting.executeScript on every tab load — no need to do
  // anything here for the SAD network interception path.

  // Check if DOM-based tracker (FST) is enabled before starting DOM monitoring
  chrome.storage.local.get(['activeAutofillPayload', 'trackerEnabled', 'fstEnabled'], (data) => {
    // 1. If tracker or FST is explicitly disabled globally, exit immediately
    if (data.trackerEnabled === false || data.fstEnabled === false) {
      console.log("Sera FST: Tracker/FST is disabled in settings. Skipping.");
      return;
    }

    // 2. Must have an active payload with tracker_enabled === true and fst_enabled !== false
    const payload = data.activeAutofillPayload;
    if (!payload || payload.tracker_enabled !== true || payload.fst_enabled === false) {
      console.log("Sera FST: FST is not active for this payload. Skipping.");
      return;
    }

    context = payload;
    startMonitoring();
  });

  function getElementTextContent(el) {
    return el.textContent || el.innerText || "";
  }


  function startMonitoring() {
    // Also track any submit button clicks as an early warning
    document.body.addEventListener('click', (e) => {
      const text = getElementTextContent(e.target).trim().toLowerCase();
      if (text.includes('submit') || text.includes('file return') || text.includes('confirm')) {
        console.log("Sera FST: Detected click on likely submit button:", e.target);
      }
    });

    const observer = new MutationObserver(() => {
      checkSuccess();
    });

    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    checkSuccess();
  }

  function checkSuccess() {
    if (hasFired || !context) return;

    let arn = null;
    let successFound = false;

    const allElements = document.querySelectorAll('*');

    // 1. Check for success message
    if (context.success_selector) {
      if (document.querySelector(context.success_selector)) successFound = true;
    } else {
      for (const el of allElements) {
        if (el.children.length === 0 && el.textContent.toLowerCase().includes('submitted successfully')) {
          successFound = true;
          break;
        }
      }
    }

    // 2. Check for ARN/Transaction ID
    if (context.arn_selector) {
      const arnEl = document.querySelector(context.arn_selector);
      if (arnEl) arn = getElementTextContent(arnEl).trim();
    } else {
      // Fallback: look for generic Transaction ID text
      for (const el of allElements) {
        if (el.children.length === 0) {
          const txt = el.textContent.trim();
          if (txt.includes('Transaction ID') || txt.includes('ARN')) {
            console.log("Sera FST: Found potential ARN element:", el);
            const parts = txt.split(':');
            if (parts.length > 1 && parts[1].trim().length > 0) {
              arn = parts[1].trim();
            } else {
              // Might be in the next sibling element
              const next = el.nextElementSibling;
              if (next) arn = getElementTextContent(next).trim();
            }
            if (arn) {
              console.log("Sera FST: Extracted ARN:", arn);
              break;
            }
          }
        }
      }
    }

    if (successFound || arn) {
      hasFired = true;
      console.log("Sera FST: Filing Successful! ARN:", arn);
      
      // Send result to background
      chrome.runtime.sendMessage({
        type: "filing_result",
        client_id: context.client_id,
        portal: context.portal,
        arn: arn || "N/A"
      });

      // Show sleek browser popup
      showBrowserSuccessPopup(context, arn);
    }
  }

  function showBrowserSuccessPopup(ctx, arn, method = "DOM_Tracker") {
    const detail = {
      portal: (ctx && ctx.portal) || "Portal",
      arn: arn || "N/A",
      filing_type: (ctx && ctx.filing_type) || "Filing Confirmation",
      period_label: (ctx && ctx.period_label) || "",
      pan: (ctx && ctx.pan) || "",
      capture_method: method
    };

    if (window.__SERA_TOAST_NOTIFIER__ && typeof window.__SERA_TOAST_NOTIFIER__.notify === 'function') {
      window.__SERA_TOAST_NOTIFIER__.notify(detail);
    }
  }
})();
