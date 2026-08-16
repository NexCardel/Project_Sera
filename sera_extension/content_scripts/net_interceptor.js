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
            let periodLabel = "";
            let filingType = "";
            if (host.includes("gst.gov.in") || url.includes("/returns/") || url.includes("/gstr") || url.includes("/evc/") || url.includes("/file")) {
                portal = "GST Portal";
                const statusCd = String(jsonObj.status_cd || jsonObj.status || jsonObj.statusCode || "");
                if (statusCd === "1" || statusCd.toUpperCase() === "SUCCESS" || jsonObj.error_cd === null || jsonObj.error === null) {
                    isSuccess = true;
                    // Comprehensive GST ARN & Reference keys
                    capturedArn = jsonObj.arn || jsonObj.ack_num || jsonObj.ack_no || jsonObj.ackNo ||
                                  jsonObj.ref_id || jsonObj.referenceId || jsonObj.trans_id ||
                                  (jsonObj.data && (jsonObj.data.arn || jsonObj.data.ack_num || jsonObj.data.ack_no || jsonObj.data.ackNo || jsonObj.data.ref_id || jsonObj.data.trans_id)) ||
                                  (jsonObj.response && (jsonObj.response.arn || jsonObj.response.ack_num)) ||
                                  (jsonObj.result && (jsonObj.result.arn || jsonObj.result.ack_num));

                    // Extract period and return type if present
                    const rawPeriod = jsonObj.ret_period || (jsonObj.data && jsonObj.data.ret_period) || jsonObj.period || "";
                    if (rawPeriod) periodLabel = String(rawPeriod);
                    const rawType = jsonObj.rtn_type || jsonObj.form_type || (jsonObj.data && (jsonObj.data.rtn_type || jsonObj.data.form_type)) || "";
                    if (rawType) filingType = String(rawType);
                }
            }

            // 2. Income Tax Portal Detection (eportal.incometax.gov.in)
            if (!capturedArn && (host.includes("incometax.gov.in") || url.includes("/foservices/api/"))) {
                portal = "Income Tax Portal";
                const status = String(jsonObj.status || jsonObj.statusCode || "").toUpperCase();
                if (status === "SUCCESS" || status === "200" || jsonObj.success === true) {
                    isSuccess = true;
                    capturedArn = jsonObj.acknowledgementNumber || jsonObj.itrAckNo || jsonObj.ackNo || jsonObj.ackNumber ||
                                  (jsonObj.data && (jsonObj.data.acknowledgementNumber || jsonObj.data.itrAckNo || jsonObj.data.ackNo));
                    const rawAy = jsonObj.assessmentYear || (jsonObj.data && jsonObj.data.assessmentYear) || "";
                    if (rawAy) periodLabel = `AY ${rawAy}`;
                    const rawForm = jsonObj.formName || (jsonObj.data && jsonObj.data.formName) || "";
                    if (rawForm) filingType = String(rawForm);
                }
            }

            // 3. TRACES Portal Detection (tdscpc.gov.in)
            if (!capturedArn && (host.includes("tdscpc.gov.in") || url.includes("/usr/") || url.includes("reqCorrection"))) {
                portal = "TRACES Portal";
                const status = String(jsonObj.status || "").toUpperCase();
                if (status === "SUCCESS" || jsonObj.requestNo || jsonObj.ticketNo || (jsonObj.data && (jsonObj.data.requestNo || jsonObj.data.ticketNo))) {
                    isSuccess = true;
                    capturedArn = jsonObj.requestNo || jsonObj.ticketNo || jsonObj.tokenNo ||
                                  (jsonObj.data && (jsonObj.data.requestNo || jsonObj.data.ticketNo || jsonObj.data.tokenNo));
                }
            }

            // 4. Universal Fallback Heuristic
            if (!capturedArn) {
                const strData = JSON.stringify(jsonObj);
                // Look for GST ARN pattern (15 chars starting with state code, e.g. AA27...)
                const arnMatch = strData.match(/"(?:arn|ack_num|ack_no|ackNo|acknowledgementNumber|requestNo|ref_id)"\s*:\s*"([A-Za-z0-9]{10,25})"/i);
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
                        period_label: periodLabel,
                        filing_type: filingType,
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
