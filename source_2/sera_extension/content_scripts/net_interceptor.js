/**
 * net_interceptor.js — Sera SAD (API Detector)
 * ---------------------------------------------
 * Runs in the page's MAIN execution world. Passively intercepts fetch() and
 * XMLHttpRequest (XHR) network API responses to detect filing confirmations,
 * e-verifications, statutory forms, challans, and multi-year filed return histories
 * without modifying or delaying page network traffic.
 */

(function() {
    const SAD_VERSION = "2.6.6";
    if (window.__SERA_SAD_VERSION__ === SAD_VERSION) return;
    window.__SERA_SAD_VERSION__ = SAD_VERSION;
    window.__SERA_SAD_INTERCEPTOR_LOADED__ = true;

    // Base service portal domains from configured compliance catalog
    const BASE_SERVICE_PORTALS = [
        "incometax.gov.in",
        "incometaxindiaefiling.gov.in",
        "gst.gov.in",
        "tdscpc.gov.in",
        "mca.gov.in"
    ];

    function isAllowedPortalHost(hostname) {
        if (!hostname) return false;
        const h = hostname.toLowerCase();
        if (BASE_SERVICE_PORTALS.some(d => h.includes(d) || d.includes(h))) {
            return true;
        }
        const customPortals = window.__SERA_ALLOWED_PORTAL_DOMAINS__;
        if (Array.isArray(customPortals)) {
            if (customPortals.some(d => d && (h.includes(d.toLowerCase()) || d.toLowerCase().includes(h)))) {
                return true;
            }
        }
        return false;
    }

    const currentHost = window.location.hostname.toLowerCase();
    if (!isAllowedPortalHost(currentHost)) {
        // Scoped to service list only: Do not hook network APIs on unconfigured websites
        return;
    }

    console.log(`⚡ Sera SAD (API Detector v${SAD_VERSION}): Scoped interceptor active for portal domain [${currentHost}].`);

    // Session cache to prevent duplicate events for the same Ack/ARN within a session
    const capturedSet = window.__SERA_CAPTURED_SET__ || new Set();
    window.__SERA_CAPTURED_SET__ = capturedSet;

    // Helper: Deep search object for key matches up to maxDepth
    function findValueDeep(obj, targetKeys, maxDepth = 4) {
        if (!obj || typeof obj !== 'object' || maxDepth <= 0) return null;
        for (const key of Object.keys(obj)) {
            if (targetKeys.some(tk => key.toLowerCase() === tk.toLowerCase())) {
                const val = obj[key];
                if (val !== null && val !== undefined && String(val).trim().length > 0 && typeof val !== 'object') {
                    return String(val).trim();
                }
            }
        }
        for (const key of Object.keys(obj)) {
            if (typeof obj[key] === 'object' && obj[key] !== null) {
                const res = findValueDeep(obj[key], targetKeys, maxDepth - 1);
                if (res) return res;
            }
        }
        return null;
    }

    // Helper: Find all arrays of objects anywhere in a JSON response
    function findReturnArrays(obj, maxDepth = 4) {
        if (!obj || typeof obj !== 'object' || maxDepth <= 0) return [];
        if (Array.isArray(obj)) {
            if (obj.length > 0 && typeof obj[0] === 'object' && obj[0] !== null) return [obj];
            return [];
        }
        let arrays = [];
        for (const key of Object.keys(obj)) {
            if (typeof obj[key] === 'object' && obj[key] !== null) {
                arrays.push(...findReturnArrays(obj[key], maxDepth - 1));
            }
        }
        return arrays;
    }

    // Strict validation of Ack / ARN number to reject garbage/placeholder tokens
    function isValidArnOrAck(arn) {
        if (!arn || typeof arn !== 'string') return false;
        const clean = arn.trim();
        if (clean.length < 5 || clean.length > 35) return false;
        if (clean.startsWith('_') || clean.toUpperCase().startsWith('FOS') || clean.toUpperCase().includes('NULL') || clean.toUpperCase().includes('UNDEFINED') || clean.toUpperCase() === 'N/A') {
            return false;
        }
        // Profile captures (e.g. PROFILE-BXIPM0155L, PROFILE-DDUPB2716N, PROFILE-27AAACG0000A1Z5)
        if (clean.toUpperCase().startsWith('PROFILE-') || clean.toUpperCase().startsWith('PROF-')) return true;
        // Income Tax Ack: 10 to 15 digits
        if (/^\d{10,15}$/.test(clean)) return true;
        // GST ARN: 15 alphanumeric characters
        if (/^[A-Za-z0-9]{15}$/.test(clean) && /[A-Za-z]/.test(clean) && /\d/.test(clean)) return true;
        // TRACES Request ID / Challan CRN: 7 to 20 alphanumeric characters
        if (/^[A-Za-z0-9]{7,20}$/.test(clean) && !clean.includes(' ')) return true;
        return false;
    }

    // Helper: Dispatch single capture event with deduplication
    function emitCapture(detail) {
        if (!detail || !isValidArnOrAck(detail.arn)) return;
        const dedupKey = detail.arn.startsWith('PROFILE-')
            ? `${detail.portal}_${detail.arn}_${detail.raw_payload ? JSON.stringify(detail.raw_payload).slice(0, 100) : ''}`
            : `${detail.portal}_${detail.arn}_${detail.period_label || ''}`;
        if (capturedSet.has(dedupKey)) return;
        capturedSet.add(dedupKey);

        console.log(`⚡ Sera SAD [API Detector] Captured Filing: ${detail.portal} | ${detail.filing_type || 'Return'} | ARN: ${detail.arn} | Period: ${detail.period_label || 'N/A'}`);
        window.dispatchEvent(new CustomEvent('SeraFSTApiCapture', {
            detail: {
                portal: detail.portal,
                arn: String(detail.arn).trim(),
                period_label: detail.period_label || "",
                filing_type: detail.filing_type || "",
                pan: detail.pan || "",
                status: detail.status || "submitted",
                capture_method: 'SAD_API_Interceptor',
                raw_payload: detail.raw_payload || {},
                url: detail.url || "",
                timestamp: new Date().toISOString()
            }
        }));
    }

    // Helper: Inspect individual record object (strictly scoped to configured service portals)
    function inspectSingleRecord(url, host, record, globalPan = "") {
        if (!record || typeof record !== 'object') return false;

        let portal = null;
        let capturedArn = null;
        let periodLabel = "";
        let filingType = "";
        let pan = globalPan || "";
        let status = "submitted";

        // 1. GST Portal Detection
        if (host.includes("gst.gov.in") || url.includes("/returns/") || url.includes("/gstr") || url.includes("/evc/") || url.includes("/file") || url.includes("/challan") || url.includes("/taxpayer")) {
            portal = "GST Portal";
            const gstKeys = ["arn", "ack_num", "ack_no", "ackNo", "cpin", "ref_id", "referenceId", "trn", "trans_id"];
            capturedArn = findValueDeep(record, gstKeys);
            const rawPeriod = findValueDeep(record, ["ret_period", "ret_prd", "period"]);
            if (rawPeriod) periodLabel = String(rawPeriod);
            const rawType = findValueDeep(record, ["rtn_type", "form_type", "form", "form_name"]);
            if (rawType) filingType = String(rawType);
            const rawGstin = findValueDeep(record, ["gstin", "pan"]);
            if (rawGstin) pan = String(rawGstin);
            const rawStatus = findValueDeep(record, ["status", "status_cd", "filing_status"]);
            if (rawStatus) status = String(rawStatus);

            // 1.2 GST Profile & Entity Information
            if (!capturedArn) {
                const profileGstin = findValueDeep(record, ["gstin", "pan"]) || pan || globalPan;
                const isGstProfile = url.includes("/profile") || url.includes("/taxpayer") || url.includes("/auth/");
                const hasGstFields = Boolean(record.legalName || record.tradeName || record.authSignatory || record.pradr || record.taxpayerType);
                if (profileGstin && (isGstProfile || hasGstFields)) {
                    capturedArn = `PROFILE-${profileGstin}`;
                    filingType = "GST Taxpayer Profile";
                    periodLabel = "Profile Info";
                    pan = profileGstin;
                    status = record.status || "active";
                }
            }
        }

        // 2. Income Tax Portal Detection
        if (!capturedArn && (host.includes("incometax.gov.in") || host.includes("incometaxindiaefiling.gov.in") || url.includes("/foservices/") || url.includes("/iec/") || url.includes("/e-filing-ws/") || url.includes("/servicesapi/"))) {
            portal = "Income Tax Portal";
            const itAckKeys = [
                "acknowledgementNumber", "acknowledgmentNumber", "acknowledgementNo", "acknowledgmentNo",
                "ackNum", "ackNumber", "ackNo", "itrAckNo", "itrAckNum", "itrAcknowledgementNo", "itrAcknowledgmentNo",
                "receiptNo", "receiptNumber", "rectificationReferenceNo", "responseReferenceNo", "submissionId",
                "grievanceNumber", "crn", "cin", "transId", "transactionNo", "transactionId", "tokenNo", "tokenNumber", "refNo", "referenceNumber", "arn",
                "ack_num", "ack_no", "ackNo", "ack_number", "ackDtls", "acknowledgementDetails", "ack_id", "token"
            ];
            capturedArn = findValueDeep(record, itAckKeys);
            
            // Extract Assessment Year (handles ITD's 'assmentYear', 'asmtYear', etc.)
            const rawAy = findValueDeep(record, ["assessmentYear", "assessment_year", "asmtYear", "assmentYear", "asmtYr", "ay", "ayDesc", "ayYr", "assessmentYr", "financialYear", "finYr", "fy", "period"]);
            if (rawAy) {
                const ayStr = String(rawAy).trim();
                if (/^\d{4}$/.test(ayStr)) {
                    const nextYr = (parseInt(ayStr, 10) + 1).toString().slice(-2);
                    periodLabel = `AY ${ayStr}-${nextYr}`;
                } else {
                    periodLabel = ayStr.toUpperCase().startsWith("AY") ? ayStr : `AY ${ayStr}`;
                }
            }

            // Extract Form Name (handles ITD's 'formTypeCd', 'formName', etc.)
            const rawForm = findValueDeep(record, ["formName", "form_name", "formType", "formTypeCd", "form", "itrForm", "formDesc", "itrFormName", "formNumber", "form_num", "formCode", "form_cd", "formTitle", "formId", "rtn_type"]);
            if (rawForm) {
                const fStr = String(rawForm).trim().toUpperCase();
                if (/^[1-7][A-Z]?$/.test(fStr)) {
                    filingType = `ITR-${fStr}`;
                } else if (!fStr.startsWith("ITR") && /^[1-7]/.test(fStr)) {
                    filingType = `ITR-${fStr}`;
                } else {
                    filingType = fStr;
                }
            }

            // Extract PAN / User ID (handles ITD's 'entityNum', 'submitUserId', 'pan', etc.)
            const rawPan = findValueDeep(record, [
                "entityNum", "entity_num", "entityId", "entity_id", "submitUserId", "userPan",
                "taxpayerId", "clientPan", "pan", "panNumber", "panNo", "userId", "createdByUser", "updatedByUser"
            ]);
            if (rawPan) {
                const panCandidate = String(rawPan).trim().toUpperCase();
                if (/^[A-Z]{5}[0-9]{4}[A-Z]$/.test(panCandidate)) {
                    pan = panCandidate;
                } else if (!pan) {
                    pan = panCandidate;
                }
            }
            // Pattern Matcher Fallback for PAN if still empty
            if (!pan || !/^[A-Z]{5}[0-9]{4}[A-Z]$/.test(pan)) {
                for (const k of Object.keys(record)) {
                    const v = String(record[k] || "").trim().toUpperCase();
                    if (/^[A-Z]{5}[0-9]{4}[A-Z]$/.test(v)) {
                        pan = v;
                        break;
                    }
                }
            }

            const rawStatus = findValueDeep(record, ["statusDesc", "filingStatus", "status", "itrStatus"]);
            if (rawStatus) status = String(rawStatus);

            // Pattern Matcher Fallback for AY & Form
            if (!periodLabel) {
                for (const k of Object.keys(record)) {
                    const v = String(record[k] || "").trim();
                    const m = v.match(/\b(20\d{2}[-/]\d{2,4}|20\d{2})\b/);
                    if (m && !v.includes(":") && v.length <= 9) {
                        periodLabel = `AY ${m[1]}`;
                        break;
                    }
                }
            }
            if (!filingType) {
                for (const k of Object.keys(record)) {
                    const v = String(record[k] || "").trim();
                    const m = v.match(/\b(ITR[- ]?[1-7]|FORM[- ]?10[A-Z0-9]*|GSTR[- ]?[1-9][A-Z]?)\b/i);
                    if (m) {
                        filingType = m[1].toUpperCase();
                        break;
                    }
                }
            }

            // 2.2 Income Tax Profile, Contact Details & Taxpayer Entity Detection
            if (!capturedArn) {
                const profilePan = findValueDeep(record, ["userId", "pan", "panNumber", "submitUserId", "createdByUser", "updatedByUser"]) || pan || globalPan;
                const isProfileEndpoint = url.includes("/saveEntity") || url.includes("/getEntity") || url.includes("/profile") || url.includes("/contact") || url.includes("/user") || url.includes("/taxpayer");
                const hasProfileFields = Boolean(record.priMobileNum || record.priEmailId || record.aadhaarNum || record.firstName || record.lastName || record.addrLine1Txt || record.dob || record.panStatus);

                if (profilePan && (isProfileEndpoint || hasProfileFields)) {
                    capturedArn = `PROFILE-${profilePan}`;
                    filingType = "Profile / Contact Details";
                    periodLabel = "Profile Info";
                    pan = profilePan;
                    status = record.status || "active";
                }
            }
        }

        // 3. TRACES Portal Detection
        if (!capturedArn && (host.includes("tdscpc.gov.in") || url.includes("/usr/") || url.includes("reqCorrection"))) {
            portal = "TRACES Portal";
            capturedArn = findValueDeep(record, ["requestNo", "ticketNo", "tokenNo", "reqNo"]);
            const rawForm = findValueDeep(record, ["form", "formType"]);
            if (rawForm) filingType = String(rawForm);
            const rawQuarter = findValueDeep(record, ["quarter", "qtr"]);
            const rawFy = findValueDeep(record, ["financialYear", "fy"]);
            if (rawQuarter || rawFy) periodLabel = `${rawQuarter || ''} ${rawFy || ''}`.trim();
        }

        // 4. MCA V3 Portal Detection
        if (!capturedArn && (host.includes("mca.gov.in") || url.includes("/mca/v3/"))) {
            portal = "MCA Portal";
            capturedArn = findValueDeep(record, ["srn", "serviceRequestNumber", "requestNo", "srnNumber", "ref_id"]);
            const rawForm = findValueDeep(record, ["formName", "form_name", "formType", "eformName"]);
            if (rawForm) filingType = String(rawForm);
        }

        // 5. Configured Custom Service Portal Detection
        if (!capturedArn && isAllowedPortalHost(host)) {
            portal = "Service Portal";
            capturedArn = findValueDeep(record, [
                "arn", "ack_num", "ack_no", "ackNo", "ackNum", "ackNumber", "acknowledgementNumber",
                "srn", "requestNo", "cpin", "cin", "crn", "receiptNo", "ref_id", "transactionNo"
            ]);
            const rawType = findValueDeep(record, ["formName", "form_name", "formType", "rtn_type", "type"]);
            if (rawType) filingType = String(rawType);
        }

        if (portal && capturedArn) {
            emitCapture({
                portal: portal,
                arn: capturedArn,
                period_label: periodLabel,
                filing_type: filingType,
                pan: pan,
                status: status,
                raw_payload: record,
                url: url
            });
            return true;
        }
        return false;
    }

    // Helper: Safely inspect JSON object or array for filing confirmations
    function inspectPayload(url, jsonObj) {
        if (!jsonObj || typeof jsonObj !== 'object') return;

        try {
            const host = window.location.hostname.toLowerCase();
            const globalPan = findValueDeep(jsonObj, ["pan", "panNumber", "panNo", "userId", "gstin"]) || "";

            console.log("🔍 Sera SAD Inspecting API Response:", url, "Keys:", Object.keys(jsonObj));

            // Automatically find ANY arrays of objects anywhere in the JSON response
            const allArrays = findReturnArrays(jsonObj);
            let totalCaptured = 0;

            for (const arr of allArrays) {
                for (const item of arr) {
                    if (inspectSingleRecord(url, host, item, globalPan)) {
                        totalCaptured++;
                    }
                }
            }

            // Also inspect root object as single transaction receipt if no array items matched
            if (totalCaptured === 0) {
                inspectSingleRecord(url, host, jsonObj, globalPan);
            } else {
                console.log(`⚡ Sera SAD: Successfully processed and captured ${totalCaptured} record(s) from return history array.`);
            }

        } catch (err) {
            // Silent catch — zero interference with portal page script
        }
    }

    // --- Intercept fetch() API calls ---
    if (!window.__SERA_ORIGINAL_FETCH__) {
        window.__SERA_ORIGINAL_FETCH__ = window.fetch;
    }
    const nativeFetch = window.__SERA_ORIGINAL_FETCH__;
    if (typeof nativeFetch === 'function') {
        window.fetch = async function(...args) {
            const response = await nativeFetch.apply(this, args);
            try {
                const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || "";
                const resClone = response.clone();
                resClone.text().then(text => {
                    if (text && typeof text === 'string') {
                        const trimmed = text.trim();
                        if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
                            try {
                                const jsonObj = JSON.parse(trimmed);
                                inspectPayload(url, jsonObj);
                            } catch (_) {}
                        }
                    }
                }).catch(() => {});
            } catch (e) {
                // Passive catch
            }
            return response;
        };
    }

    // --- Intercept XMLHttpRequest (XHR) API calls ---
    if (!window.__SERA_ORIGINAL_XHR_OPEN__) {
        window.__SERA_ORIGINAL_XHR_OPEN__ = XMLHttpRequest.prototype.open;
    }
    const originalXhrOpen = window.__SERA_ORIGINAL_XHR_OPEN__;

    if (!window.__SERA_ORIGINAL_XHR_SEND__) {
        window.__SERA_ORIGINAL_XHR_SEND__ = XMLHttpRequest.prototype.send;
    }
    const originalXhrSend = window.__SERA_ORIGINAL_XHR_SEND__;

    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
        this._seraUrl = typeof url === 'string' ? url : "";
        return originalXhrOpen.apply(this, [method, url, ...rest]);
    };

    XMLHttpRequest.prototype.send = function(...args) {
        this.addEventListener('load', function() {
            try {
                if (this.readyState === 4 && this.status >= 200 && this.status < 300) {
                    let jsonObj = null;
                    if (this.responseType === 'json' && this.response) {
                        jsonObj = this.response;
                    } else if (this.responseType === '' || this.responseType === 'text') {
                        const raw = this.responseText;
                        if (raw && typeof raw === 'string') {
                            const trimmed = raw.trim();
                            if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
                                try { jsonObj = JSON.parse(trimmed); } catch (_) {}
                            }
                        }
                    } else if (this.response) {
                        if (typeof this.response === 'object') {
                            jsonObj = this.response;
                        } else if (typeof this.response === 'string') {
                            const trimmed = this.response.trim();
                            if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
                                try { jsonObj = JSON.parse(trimmed); } catch (_) {}
                            }
                        }
                    }
                    if (jsonObj) {
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
