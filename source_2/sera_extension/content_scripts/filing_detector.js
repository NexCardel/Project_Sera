// filing_detector.js - Listens for SAD API captures and handles filing results
(function() {
    console.log("Project Sera: Filing detector active.");

    window.addEventListener('SeraFSTApiCapture', (event) => {
        if (!event || !event.detail) return;
        const detail = event.detail;

        console.log("Sera Filing Detector: Received SAD API Capture event", detail);

        if (!chrome.runtime || !chrome.runtime.id) {
            console.log("Sera Filing Detector: Extension was reloaded. Please refresh this tab if needed.");
            return;
        }

        try {
            chrome.storage.local.get(['activeAutofillPayload', 'manualAssistPayload', 'mecpPayload', 'sadEnabled', 'trackerEnabled', 'allowedDomains'], (data) => {
                if (chrome.runtime.lastError || !chrome.runtime || !chrome.runtime.id) return;
                if (data && (data.trackerEnabled === false || data.sadEnabled === false)) {
                    console.log("Sera Filing Detector: SAD interception is disabled in settings. Skipping.");
                    return;
                }
                // Verify host is in allowed service domains if configured
                const currentHost = window.location.hostname.toLowerCase();
                const allowed = data && data.allowedDomains;
                if (Array.isArray(allowed) && allowed.length > 0) {
                    const isMatch = allowed.some(d => d && (currentHost.includes(d.toLowerCase()) || d.toLowerCase().includes(currentHost)));
                    if (!isMatch) {
                        console.log("Sera Filing Detector: Domain not in allowed service portal list. Skipping event.");
                        return;
                    }
                }
                const payload = (data && (data.activeAutofillPayload || data.manualAssistPayload || data.mecpPayload)) || {};
                if (payload.sad_enabled === false) {
                    console.log("Sera Filing Detector: SAD is disabled for this payload. Skipping.");
                    return;
                }
                // Prioritize explicit detail.client_id; for SAD captures with extracted PAN/ARN, decouple from stale session storage
                const effectiveClientId = detail.client_id || (detail.pan || detail.arn ? null : (payload.client_id || null));

                chrome.runtime.sendMessage({
                    type: "filing_result",
                    client_id: effectiveClientId,
                    portal: detail.portal || payload.portal || "Portal",
                    arn: detail.arn || "N/A",
                    capture_method: detail.capture_method || "SAD_API_Interceptor",
                    period_label: detail.period_label || payload.period_label || "",
                    filing_type: detail.filing_type || payload.filing_type || "",
                    pan: detail.pan || payload.pan || "",
                    url: detail.url || "",
                    raw_payload: detail.raw_payload || {}
                });
            });
        } catch (e) {
            console.warn("Sera Filing Detector: Extension context error, please refresh tab:", e);
        }
    });
})();