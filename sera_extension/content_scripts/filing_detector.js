// filing_detector.js - Listens for SAD API captures and handles filing results
(function() {
    console.log("Project Sera: Filing detector active.");

    window.addEventListener('SeraFSTApiCapture', (event) => {
        if (!event || !event.detail) return;
        const detail = event.detail;

        console.log("Sera Filing Detector: Received SAD API Capture event", detail);

        chrome.storage.local.get(['activeAutofillPayload'], (data) => {
            const payload = data.activeAutofillPayload || {};
            // Prioritize detail.client_id (from test bench / page event) -> active payload client_id -> fallback 1
            const effectiveClientId = detail.client_id || payload.client_id || 1;

            chrome.runtime.sendMessage({
                type: "filing_result",
                client_id: effectiveClientId,
                portal: detail.portal || payload.portal || "Portal",
                arn: detail.arn || "N/A",
                capture_method: detail.capture_method || "SAD_API_Interceptor",
                raw_payload: detail.raw_payload || {}
            });
        });
    });
})();