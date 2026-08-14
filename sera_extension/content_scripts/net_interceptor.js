/**
 * net_interceptor.js — Sera API Detection (SAD)
 * ---------------------------------------------
 * Runs in the page's MAIN execution world. Passively intercepts fetch() and
 * XMLHttpRequest (XHR) network API responses to detect filing confirmations
 * (GST ARNs, Income Tax Ack Numbers, TRACES Request IDs) without modifying
 * or delaying page network traffic.
 */

(function() {
    if (window.__SERA_SAD_INTERCEPTOR_LOADED__) return;
    window.__SERA_SAD_INTERCEPTOR_LOADED__ = true;

    console.log("Sera SAD: Network Response Interceptor active in MAIN world.");

    // Helper: Safely inspect JSON object for filing success markers
    function inspectPayload(url, jsonObj) {
        if (!jsonObj || typeof jsonObj !== 'object') return;

        try {
            const host = window.location.hostname.toLowerCase();
            let portal = "Tax Portal";
            let capturedArn = null;
            let isSuccess = false;

            // 1. GST Portal Detection (services.gst.gov.in)
            if (host.includes("gst.gov.in") || url.includes("/returns/") || url.includes("/gstr") || url.includes("/evc/")) {
                portal = "GST Portal";
                const statusCd = String(jsonObj.status_cd || jsonObj.status || "");
                if (statusCd === "1" || statusCd.toUpperCase() === "SUCCESS" || jsonObj.error_cd === null) {
                    isSuccess = true;
                    // ARN extraction
                    capturedArn = jsonObj.arn || jsonObj.ack_num || (jsonObj.data && (jsonObj.data.arn || jsonObj.data.ack_num));
                }
            }

            // 2. Income Tax Portal Detection (eportal.incometax.gov.in)
            if (!capturedArn && (host.includes("incometax.gov.in") || url.includes("/foservices/api/"))) {
                portal = "Income Tax Portal";
                const status = String(jsonObj.status || jsonObj.statusCode || "").toUpperCase();
                if (status === "SUCCESS" || status === "200" || jsonObj.success === true) {
                    isSuccess = true;
                    capturedArn = jsonObj.acknowledgementNumber || jsonObj.itrAckNo || jsonObj.ackNo || jsonObj.ackNumber;
                }
            }

            // 3. TRACES Portal Detection (tdscpc.gov.in)
            if (!capturedArn && (host.includes("tdscpc.gov.in") || url.includes("/usr/") || url.includes("reqCorrection"))) {
                portal = "TRACES Portal";
                const status = String(jsonObj.status || "").toUpperCase();
                if (status === "SUCCESS" || jsonObj.requestNo || jsonObj.ticketNo) {
                    isSuccess = true;
                    capturedArn = jsonObj.requestNo || jsonObj.ticketNo || jsonObj.tokenNo;
                }
            }

            // 4. Universal Fallback Heuristic
            if (!capturedArn) {
                const strData = JSON.stringify(jsonObj);
                // Look for GST ARN pattern (15 chars starting with state code, e.g. AA27...)
                const arnMatch = strData.match(/"(?:arn|ack_num|acknowledgementNumber|requestNo)"\s*:\s*"([A-Za-z0-9]{10,20})"/i);
                if (arnMatch && arnMatch[1]) {
                    capturedArn = arnMatch[1];
                    isSuccess = true;
                }
            }

            if (isSuccess && capturedArn) {
                console.log(`Sera SAD Captured Filing! Portal: ${portal}, ARN: ${capturedArn}`);
                window.dispatchEvent(new CustomEvent('SeraFSTApiCapture', {
                    detail: {
                        portal: portal,
                        arn: String(capturedArn).trim(),
                        capture_method: 'SAD_API_Interceptor',
                        raw_payload: jsonObj,
                        url: url,
                        timestamp: new Date().toISOString()
                    }
                }));
            }
        } catch (err) {
            // Silent catch — zero interference with portal page script
        }
    }

    // --- Intercept fetch() API calls ---
    const nativeFetch = window.fetch;
    if (typeof nativeFetch === 'function') {
        window.fetch = async function(...args) {
            const response = await nativeFetch.apply(this, args);
            try {
                const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || "";
                // Clone response to passively inspect without disturbing original stream
                const resClone = response.clone();
                resClone.json().then(jsonObj => {
                    inspectPayload(url, jsonObj);
                }).catch(() => {});
            } catch (e) {
                // Passive catch
            }
            return response;
        };
    }

    // --- Intercept XMLHttpRequest (XHR) API calls ---
    const originalXhrOpen = XMLHttpRequest.prototype.open;
    const originalXhrSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
        this._seraUrl = typeof url === 'string' ? url : "";
        return originalXhrOpen.apply(this, [method, url, ...rest]);
    };

    XMLHttpRequest.prototype.send = function(...args) {
        this.addEventListener('load', function() {
            try {
                if (this.readyState === 4 && this.status >= 200 && this.status < 300) {
                    const responseText = this.responseText;
                    if (responseText && responseText.trim().startsWith('{')) {
                        const jsonObj = JSON.parse(responseText);
                        inspectPayload(this._seraUrl || "", jsonObj);
                    }
                }
            } catch (e) {
                // Passive catch
            }
        });
        return originalXhrSend.apply(this, args);
    };

})();
