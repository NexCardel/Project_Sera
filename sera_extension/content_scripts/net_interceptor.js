/**
 * net_interceptor.js — Sera SAD (API Detector)
 * ---------------------------------------------
 * Runs in the page's MAIN execution world. Passively intercepts fetch() and
 * XMLHttpRequest (XHR) network API responses to detect filing confirmations,
 * e-verifications, statutory forms, challans, and multi-year filed return histories
 * without modifying or delaying page network traffic.
 */

(function() {
    const SAD_VERSION = "2.8.5.4";
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
    function findValueDeep(obj, targetKeys, maxDepth = 6) {
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

    // Build a small, explicit evidence envelope for simpleParser. The raw
    // response is still retained below; this prevents the parser from having
    // to guess which of several similarly named fields was intended.
    function collectEvidenceValues(obj, path = "", depth = 0, output = [], seen = new Set()) {
        if (obj === null || obj === undefined || depth > 8) return output;
        if (typeof obj === "string") {
            const trimmed = obj.trim();
            if ((trimmed.startsWith("{") || trimmed.startsWith("[")) && trimmed.length < 200000) {
                try { return collectEvidenceValues(JSON.parse(trimmed), path, depth + 1, output, seen); } catch (_) {}
            }
            if (path) output.push({ path, key: path.split(".").pop().toLowerCase(), value: trimmed });
            return output;
        }
        if (typeof obj !== "object") {
            if (path) output.push({ path, key: path.split(".").pop().toLowerCase(), value: String(obj) });
            return output;
        }
        if (seen.has(obj)) return output;
        seen.add(obj);
        for (const key of Object.keys(obj)) {
            const nextPath = path ? `${path}.${key}` : key;
            collectEvidenceValues(obj[key], nextPath, depth + 1, output, seen);
        }
        return output;
    }

    function buildSimpleParserEvidence(url, record, portal) {
        const values = collectEvidenceValues(record);
        const pans = new Set();
        const gstins = new Set();
        const names = [];
        const emails = [];
        const phones = [];
        const dobs = [];
        const transactions = [];
        const addUnique = (list, item) => {
            if (item && !list.some(existing => existing.value === item.value && existing.source === item.source)) list.push(item);
        };
        const decodeKnownField = (key, raw) => {
            const text = String(raw || "").trim();
            const keyLower = key.toLowerCase();
            const known = keyLower.includes("email") || keyLower.includes("mobile") || keyLower.includes("phone") || ["fullname", "firstname", "middlename", "lastname", "legalname", "tradename", "bn", "ln", "tn"].includes(keyLower);
            if (!known || !/^[A-Za-z0-9+/]+={0,2}$/.test(text) || text.length % 4) return text;
            try {
                const bytes = Uint8Array.from(atob(text), char => char.charCodeAt(0));
                const decoded = new TextDecoder().decode(bytes).trim();
                if (keyLower.includes("email")) return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(decoded) ? decoded : text;
                if (keyLower.includes("mobile") || keyLower.includes("phone")) return /^\+?[0-9][0-9 .-]{6,18}$/.test(decoded) ? decoded : text;
                return /^[A-Za-z][A-Za-z .,'-]{1,120}$/.test(decoded) ? decoded : text;
            } catch (_) { return text; }
        };
        const validDate = raw => {
            const text = String(raw || "").trim();
            if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text;
            if (/^\d{2}[/-]\d{2}[/-]\d{4}$/.test(text)) {
                const parts = text.split(/[/-]/);
                return `${parts[2]}-${parts[1]}-${parts[0]}`;
            }
            const number = Number(text);
            if (Number.isFinite(number) && number >= 10000000000 && number <= 10000000000000) {
                const date = new Date(number);
                if (!Number.isNaN(date.getTime())) return date.toISOString().slice(0, 10);
            }
            return null;
        };
        const normalizeForm = raw => {
            const text = String(raw || "").trim().toUpperCase();
            if (!text || ["NONE", "NULL", "N/A", "NA"].includes(text)) return null;
            if (/^[1-7][A-Z]?$/.test(text)) return `ITR-${text}`;
            if (/^ITR[- ]?[1-7][A-Z]?$/.test(text)) return text.replace(" ", "-");
            return text;
        };
        const identityKeys = new Set(["pan", "pannumber", "panno", "entitynum", "userpan", "taxpayerid", "clientpan", "gstin", "gstinno"]);
        const nameKeys = new Set(["legalname", "tradename", "authsignatory", "nameasperbank", "fullname", "firstname", "middlename", "midname", "lastname", "surnameororgname", "assesseename", "taxpayername", "name", "entityname", "clientname", "username", "contactperson", "signatoryname", "orgname", "businessname", "tradenm", "lgnm", "bn", "ln", "tn"]);
        const emailKeys = new Set(["email", "emailid", "priemailid", "primaryemail", "clientemail", "emailaddress"]);
        const dobKeys = new Set(["dob", "dateofbirth"]);
        const transactionKeys = new Set(["transactionno", "transactionid", "transid", "acknum", "acknumber", "ackno", "arn", "arnnumber", "ack_num", "ack_no", "referenceid", "ref_id", "submissionid", "tokenno", "token", "receiptno", "receiptnumber"]);

        for (const entry of values) {
            const key = entry.key;
            const text = decodeKnownField(key, entry.value);
            const upper = text.toUpperCase();
            if (identityKeys.has(key)) {
                if (/^[0-9A-Z]{15}$/.test(upper) && /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z][Z][0-9A-Z]$/.test(upper)) {
                    gstins.add(upper); pans.add(upper.slice(2, 12));
                } else if (/^[A-Z]{5}[0-9]{4}[A-Z]$/.test(upper)) {
                    pans.add(upper);
                }
            }
            if (nameKeys.has(key) && text && !/^(N\/A|NA|NONE|NULL)$/i.test(text)) {
                const role = ["bn", "tn", "tradename"].includes(key) ? "business_name" : (["ln", "legalname"].includes(key) ? "legal_name" : "client_name");
                addUnique(names, { value: text, source: entry.path, role });
            }
            if (emailKeys.has(key) && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(text)) {
                addUnique(emails, { value: text, source: entry.path, role: key === "priemailid" ? "primary_email" : "portal_email" });
            }
            if ((key.includes("mobile") || key.includes("phone")) && /^\+?[0-9][0-9 .-]{6,18}$/.test(text)) {
                addUnique(phones, { value: text, source: entry.path, role: key.includes("pri") ? "primary_mobile" : "portal_mobile" });
            }
            if (dobKeys.has(key)) {
                const parsed = validDate(text);
                if (parsed) addUnique(dobs, { value: parsed, source: entry.path });
            }
            if (transactionKeys.has(key) && text && !/^(N\/A|NA|NONE|NULL|-)$/i.test(text)) {
                const role = upper.startsWith("EVERIFY") ? "everification_transaction" : ((key.includes("ack") || key.includes("arn")) ? "acknowledgement" : "portal_transaction");
                addUnique(transactions, { value: text, source: entry.path, role });
            }
        }

        // Synthesize full client name if individual components (first, middle, last) exist
        const fn = values.find(e => ["firstname", "first_name", "fname"].includes(e.key));
        const mn = values.find(e => ["middlename", "midname", "middle_name", "mname"].includes(e.key));
        const ln = values.find(e => ["lastname", "last_name", "lname", "surnameororgname", "sur_name", "surname"].includes(e.key));
        if (fn || mn || ln) {
            const parts = [fn && fn.value, mn && mn.value, ln && ln.value].filter(Boolean).map(s => String(s).trim()).filter(Boolean);
            const compoundName = parts.join(" ").trim();
            if (compoundName && compoundName.length >= 3 && !/^(N\/A|NA|NONE|NULL)$/i.test(compoundName)) {
                addUnique(names, { value: compoundName, source: "composed.name_components", role: "client_name" });
            }
        }

        const query = new URL(url, window.location.href).searchParams;
        let period = null;
        let periodSource = null;
        const rtnPrd = query.get("rtn_prd");
        if (/^(0[1-9]|1[0-2])\d{4}$/.test(rtnPrd || "")) {
            period = `${rtnPrd.slice(2)}-${rtnPrd.slice(0, 2)}`;
            periodSource = "url.rtn_prd";
        }
        if (!period) {
            const ay = values.find(entry => ["assessmentyear", "assessment_year", "asmtyear", "assmentyear"].includes(entry.key) && /^\d{4}$/.test(String(entry.value)));
            if (ay) { period = `AY ${ay.value}-${String(Number(ay.value) + 1).slice(-2)}`; periodSource = ay.path; }
        }
        let returnType = null;
        let returnTypeSource = null;
        const rtnTyp = query.get("rtn_typ");
        if (rtnTyp) { returnType = normalizeForm(rtnTyp); returnTypeSource = "url.rtn_typ"; }
        if (!returnType) {
            const form = values.find(entry => ["formname", "formtype", "formtypecd", "rtn_type", "form"].includes(entry.key) && normalizeForm(entry.value));
            if (form) { returnType = normalizeForm(form.value); returnTypeSource = form.path; }
        }
        const rawText = JSON.stringify(record).toLowerCase();
        const lowerUrl = url.toLowerCase();
        const everification = lowerUrl.includes("validateotp") && String(record.moduleCode || "").toUpperCase() === "ITR" && String(record.status || "").toUpperCase() === "SUCCESS" && rawText.includes("otp validated");
        const submission = (lowerUrl.includes("/returns/submit/wzrd") && String(record.httpStatus || "").toUpperCase() === "ACCEPTED" && record.successFlag === true) || (lowerUrl.includes("formdetails") && rawText.includes('"status":"fil"'));
        return {
            version: 1,
            portal,
            identities: { pans: Array.from(pans), gstins: Array.from(gstins) },
            names, emails, phones, dobs, transactions,
            return_type: returnType ? { value: returnType, source: returnTypeSource } : null,
            period: period ? { value: period, source: periodSource } : null,
            lifecycle: { submission, everification, other_evc: !everification && lowerUrl.includes("validateotp") && String(record.status || "").toUpperCase() === "SUCCESS", pending_everification: rawText.includes("pending for e-verification") }
        };
    }

    // Helper: Find all arrays of objects anywhere in a JSON response
    function findReturnArrays(obj, maxDepth = 6) {
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
        // Profile & Synthetic captures (e.g. PROFILE-*, BANK-*, ITR-STATUS-*, ITR-DOWNLOAD-*)
        if (clean.toUpperCase().startsWith('PROFILE-') || clean.toUpperCase().startsWith('PROF-') || clean.toUpperCase().startsWith('BANK-') || clean.toUpperCase().startsWith('ITR-')) return true;
        // Income Tax Ack: 10 to 15 digits
        if (/^\d{10,15}$/.test(clean)) return true;
        // GST ARN: 15 alphanumeric characters
        if (/^[A-Za-z0-9]{15}$/.test(clean) && /[A-Za-z]/.test(clean) && /\d/.test(clean)) return true;
        // TRACES Request ID / Challan CRN: 7 to 20 alphanumeric characters
        if (/^[A-Za-z0-9]{7,20}$/.test(clean) && !clean.includes(' ')) return true;
        return false;
    }

    // Session Navigation Resets
    function checkResetSession() {
        const p = window.location.pathname.toLowerCase() + window.location.hash.toLowerCase();
        if (p.includes('login') || p.includes('logout')) {
            if (window.sessionStorage.getItem('__SERA_SESSION_ID__')) {
                window.sessionStorage.removeItem('__SERA_SESSION_ID__');
                window.sessionStorage.removeItem('__SERA_SESSION_PAN__');
                console.log("⚡ Sera SAD: Session cleared due to login/logout navigation.");
            }
        }
    }
    
    window.addEventListener('hashchange', checkResetSession);
    window.addEventListener('popstate', checkResetSession);
    
    const origPush = history.pushState;
    history.pushState = function() {
        origPush.apply(this, arguments);
        checkResetSession();
    };
    const origReplace = history.replaceState;
    history.replaceState = function() {
        origReplace.apply(this, arguments);
        checkResetSession();
    };
    checkResetSession(); // initial check

    // Helper: Dispatch single capture event with deduplication
    function emitCapture(detail) {
        if (!detail || !isValidArnOrAck(detail.arn)) return;
        const dedupKey = detail.arn.startsWith('PROFILE-')
            ? `${detail.portal}_${detail.arn}_${detail.raw_payload ? JSON.stringify(detail.raw_payload).slice(0, 100) : ''}`
            : `${detail.portal}_${detail.arn}_${detail.period_label || ''}`;
        if (capturedSet.has(dedupKey)) return;
        capturedSet.add(dedupKey);

        let sessionId = window.sessionStorage.getItem('__SERA_SESSION_ID__');
        let sessionPan = window.sessionStorage.getItem('__SERA_SESSION_PAN__');

        if (detail.pan) {
            const currentPan = String(detail.pan).trim().toUpperCase();
            if (sessionPan && sessionPan !== currentPan) {
                console.log(`⚡ Sera SAD: PAN change detected (${sessionPan} -> ${currentPan}). Resetting session.`);
                sessionId = null; 
            }
            window.sessionStorage.setItem('__SERA_SESSION_PAN__', currentPan);
        }

        if (!sessionId) {
            sessionId = 'ses_' + Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15);
            window.sessionStorage.setItem('__SERA_SESSION_ID__', sessionId);
        }

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
                simple_parser_evidence: detail.simple_parser_evidence || null,
                url: detail.url || "",
                timestamp: new Date().toISOString(),
                session_id: sessionId
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

        // 1. Host-Specific Domain Disambiguation
        const isIncomeTax = host.includes("incometax.gov.in") || host.includes("incometaxindiaefiling.gov.in") || url.includes("/iec/") || url.includes("/foservices/") || url.includes("/servicesapi/") || url.includes("/itrweb/");
        const isGst = !isIncomeTax && (host.includes("gst.gov.in") || url.includes("services.gst.gov.in") || url.includes("/gstr") || url.includes("/evc/") || url.includes("/challan"));

        // 1.1 GST Portal Detection
        if (isGst) {
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
                if (profileGstin && hasGstFields) {
                    capturedArn = `PROFILE-${profileGstin}`;
                    filingType = "GST Taxpayer Profile";
                    periodLabel = "Profile Info";
                    pan = profileGstin;
                    status = record.status || "active";
                }
            }
        }

        // 1.2 Income Tax Portal Detection
        if (!capturedArn && isIncomeTax) {
            portal = "Income Tax Portal";

            // Keep NON-ITR OTP/EVC responses as evidence-only events. They are
            // not treated as return submissions, but simpleParser must still be
            // able to report that an EVC happened without a return submission.
            const isNonItrOtp = record.moduleCode && String(record.moduleCode).trim().toUpperCase() === "NON-ITR";
            if (isNonItrOtp && String(record.status || "").toUpperCase() === "SUCCESS") {
                const evcReference = findValueDeep(record, ["transactionNo", "transactionId", "transId", "ref_id", "referenceId", "tokenNo"]);
                if (evcReference) {
                    capturedArn = `EVC-${evcReference}`;
                    filingType = "EVC Event";
                    periodLabel = "EVC Activity";
                    status = "evc accepted";
                }
            }

            const itAckKeys = [
                "arnNumber", "arn_number", "acknowledgementNumber", "acknowledgmentNumber", "acknowledgementNo", "acknowledgmentNo",
                "ackNum", "ackNumber", "ackNo", "itrAckNo", "itrAckNum", "itrAcknowledgementNo", "itrAcknowledgmentNo",
                "ack_num", "ack_no", "ack_number", "ackDtls", "acknowledgementDetails", "ack_id",
                "receiptNo", "receiptNumber", "rectificationReferenceNo", "responseReferenceNo", "submissionId",
                "grievanceNumber", "crn", "cin", "arn",
                "transId", "transactionNo", "transactionId", "tokenNo", "tokenNumber", "refNo", "referenceNumber", "token"
            ];
            if (!isNonItrOtp) {
                // Strict priority: If an explicit 15-digit numeric Government ARN is present, use it immediately
                const directArn = findValueDeep(record, ["arnNumber", "arn_number", "ackNum", "ackNumber", "acknowledgementNumber", "acknowledgementNo"]);
                if (directArn && /^\d{15}$/.test(String(directArn).trim())) {
                    capturedArn = String(directArn).trim();
                } else {
                    capturedArn = findValueDeep(record, itAckKeys);
                }
            }
            
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
            if (!pan && globalPan) {
                pan = globalPan;
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

            // 2.2 Income Tax Downloaded Returns / Full Computational ITR Schema Interception
            if (!capturedArn) {
                const isDownloadEndpoint = url.includes("downloadfile") || url.includes("/download") || url.includes("/returns/") || url.includes("/view-filed-returns") || url.includes("getFiledReturns");
                const isItrStructure = Boolean(record.ITR || record.ITR1 || record.ITR4 || record.ITR2 || record.ITR3 || record.Form_ITR4 || record.Form_ITR1 || record.ScheduleBP || record.IncomeDeductions || record.CreationInfo);

                if (isDownloadEndpoint || isItrStructure) {
                    const itrPan = findValueDeep(record, ["PAN", "pan", "userId", "entityNum", "AssesseeVerPAN"], 8) || pan || globalPan;
                    const extractedAck = findValueDeep(record, ["ackNum", "ackNumber", "ackNo", "acknowledgementNumber", "transactionNo", "arnNumber"], 8);
                    const formDesc = findValueDeep(record, ["FormName", "formName", "formType", "formTypeCd", "form"], 8);
                    const rawAyVal = findValueDeep(record, ["AssessmentYear", "assessmentYear", "asmtYear", "assmentYear"], 8);

                    let ayLabel = periodLabel;
                    if (!ayLabel && rawAyVal) {
                        const ayStr = String(rawAyVal).trim();
                        if (/^\d{4}$/.test(ayStr)) {
                            const nextYr = (parseInt(ayStr, 10) + 1).toString().slice(-2);
                            ayLabel = `AY ${ayStr}-${nextYr}`;
                        } else {
                            ayLabel = ayStr.toUpperCase().startsWith("AY") ? ayStr : `AY ${ayStr}`;
                        }
                    }
                    if (!ayLabel) ayLabel = "FILED";

                    if (itrPan) {
                        capturedArn = extractedAck ? String(extractedAck) : `ITR-DOWNLOAD-${itrPan}-${ayLabel.replace(/[\s\/]+/g, '_')}`;
                        filingType = formDesc ? (formDesc.startsWith("ITR") ? formDesc : `ITR-${formDesc}`) : (filingType || "ITR Return Download");
                        periodLabel = ayLabel;
                        pan = itrPan;
                        status = "downloaded";
                    }
                }
            }

            // 2.3 Income Tax Profile, Contact Details & Taxpayer Entity Detection
            if (!capturedArn) {
                // Reject generic dashboard menu items or toast status messages from creating fake profile records
                const isGenericMenu = Boolean(record.actionName && record.moduleCd);
                const isGenericToast = Boolean(record.code && record.desc && (record.type === "INFO" || record.type === "SUCCESS" || String(record.desc).includes("successfully")));

                if (!isGenericMenu && !isGenericToast) {
                    const profilePan = findValueDeep(record, ["userId", "pan", "PAN", "panNumber", "submitUserId", "createdByUser", "updatedByUser"], 6) || pan || globalPan;
                    const hasProfileField = findValueDeep(record, ["priMobileNum", "priEmailId", "aadhaarNum", "firstName", "lastName", "addrLine1Txt", "dob", "panStatus", "mobileNo", "emailId"], 6);
                    const isExplicitProfileUrl = url.includes("/saveEntity") || url.includes("/profile") || url.includes("/contact") || url.includes("/taxpayerDetails");

                    if (profilePan && (hasProfileField || (isExplicitProfileUrl && (record.firstName || record.lastName || record.priEmailId || record.priMobileNum)))) {
                        capturedArn = `PROFILE-${profilePan}`;
                        filingType = "Profile / Contact Details";
                        periodLabel = "Profile Info";
                        pan = profilePan;
                        status = record.status || "active";
                    }
                }
            }

            // 2.4 Bank Account Validation & Nomination Interception
            if (!capturedArn) {
                const isBank = Boolean(record.bankAcctNum || record.bankName || record.ifscCd || record.bankPanLinkingId || record.accountStatus);
                if (isBank) {
                    const bankPan = findValueDeep(record, ["entityNum", "pan", "userId"], 6) || pan || globalPan;
                    const bankId = record.bankPanLinkingId || record.transactionNo || (record.ifscCd ? `${record.ifscCd}` : "ACCT");
                    capturedArn = `BANK-${bankPan || 'ACCT'}-${bankId}`;
                    filingType = "Bank Validation";
                    periodLabel = record.bankName || "Bank Details";
                    pan = bankPan;
                    status = record.accountStatus || (record.accValidity === 'V' ? 'Validated' : 'Pending');
                }
            }

            // 2.5 ITR Draft & Return Filing Status Interception
            if (!capturedArn) {
                const isReturnStatus = url.includes("/return/details") || url.includes("/returns/details") || (record.isDraftPresent !== undefined && record.isReturnFiled !== undefined);
                if (isReturnStatus) {
                    const retPan = pan || globalPan;
                    const draftForm = record.draftFormCode || filingType || "ITR";
                    capturedArn = `ITR-STATUS-${retPan || 'CLIENT'}-${draftForm}`;
                    filingType = `${draftForm} Status`;
                    periodLabel = periodLabel || "Filing Status";
                    status = record.isReturnFiled === 'Y' ? 'Filed' : (record.isDraftPresent === 'Y' ? 'Draft Present' : 'Not Filed');
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

        const simpleParserEvidence = buildSimpleParserEvidence(url, record, portal);
        // The dashboard response often carries the PAN in the envelope rather
        // than the inner response object. Preserve that identity in the
        // evidence ledger so context captures can join the client timeline.
        if (pan && /^[A-Z]{5}[0-9]{4}[A-Z]$/i.test(String(pan)) && !simpleParserEvidence.identities.pans.includes(String(pan).toUpperCase())) {
            simpleParserEvidence.identities.pans.push(String(pan).toUpperCase());
        }
        if (simpleParserEvidence.identities.pans.length > 0) {
            pan = simpleParserEvidence.identities.pans[0];
        } else if (!/^[A-Z]{5}[0-9]{4}[A-Z]$/.test(String(pan || "").toUpperCase())) {
            pan = "";
        }
        if (!periodLabel && simpleParserEvidence.period) periodLabel = simpleParserEvidence.period.value;
        if (!filingType && simpleParserEvidence.return_type) filingType = simpleParserEvidence.return_type.value;

        // ITR dashboard APIs frequently return useful filing data without an
        // ACK/ARN. Keep those responses as linked context instead of dropping
        // them; the hash makes repeated identical polls deduplicable upstream.
        if (!capturedArn && isIncomeTax && url.includes("/itrweb/")) {
            const contextKeys = [
                "isDraftPresent", "isReturnFiled", "isOnDemandLoading", "assessmentYear",
                "assessment_year", "asmtYear", "assmentYear", "formTypeCd", "returnStatus",
                "filingStatus", "efileStatus", "itrPanDetlList", "returnDetails", "filingDate",
                "dueDate", "ackNum", "ackNumber", "returnType", "data"
            ];
            const hasDashboardContext = contextKeys.some(key => findValueDeep(record, [key], 8) !== undefined);
            const contextPan = pan || globalPan || findValueDeep(record, ["pan", "userId", "entityNum", "loggedInUserId"], 8) || "CLIENT";
            if (hasDashboardContext) {
                let contextText = "";
                try { contextText = JSON.stringify(record); } catch (_) {}
                let hash = 0;
                for (let i = 0; i < contextText.length; i++) hash = ((hash << 5) - hash + contextText.charCodeAt(i)) | 0;
                const contextHash = Math.abs(hash).toString(36);
                capturedArn = `ITR-CONTEXT-${String(contextPan).toUpperCase()}-${contextHash}`;
                filingType = filingType || "ITR Dashboard Context";
                periodLabel = periodLabel || "Dashboard Context";
                status = "context";
                pan = contextPan;
            }
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
                url: url,
                simple_parser_evidence: simpleParserEvidence
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
            let globalPan = findValueDeep(jsonObj, [
                "entityNum", "entity_num", "entityId", "entity_id", "loggedInUserId", "userId",
                "submitUserId", "userPan", "taxpayerId", "clientPan", "pan", "panNumber", "panNo",
                "loginId", "createdByUser", "updatedByUser", "gstin"
            ]) || "";

            if (!globalPan) {
                try {
                    const jsonStr = typeof jsonObj === 'string' ? jsonObj : JSON.stringify(jsonObj);
                    const m = jsonStr.match(/"(?:entityNum|pan|userId|loggedInUserId|gstin)":\s*"([A-Za-z0-9]+)"/i);
                    if (m && m[1]) globalPan = m[1].toUpperCase();
                } catch (_) {}
            }

            if (!globalPan) {
                try {
                    const docText = document.body ? document.body.innerText.slice(0, 5000) : "";
                    const m = docText.match(/\b([A-Z]{5}[0-9]{4}[A-Z])\b/);
                    if (m) globalPan = m[1];
                } catch (_) {}
            }

            console.log("🔍 Sera SAD Inspecting API Response:", url, "Keys:", Object.keys(jsonObj), "Global PAN:", globalPan);

            // If response is a single monolithic computational ITR schema or file download, inspect root object directly
            const isMonolithicItr = Boolean(
                jsonObj.ITR || jsonObj.ITR1 || jsonObj.ITR2 || jsonObj.ITR3 || jsonObj.ITR4 || jsonObj.ITR5 || jsonObj.ITR6 || jsonObj.ITR7 ||
                jsonObj.Form_ITR4 || jsonObj.Form_ITR1 || jsonObj.PersonalInfo || jsonObj.CreationInfo || jsonObj.ScheduleBP || jsonObj.IncomeDeductions ||
                url.includes("downloadfile") || url.includes("/download")
            );

            if (isMonolithicItr) {
                inspectSingleRecord(url, host, jsonObj, globalPan);
                return;
            }

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

            // Also inspect root object as single transaction receipt or profile if needed
            if (totalCaptured === 0 || url.includes("/saveEntity") || url.includes("/getEntity") || url.includes("/profile") || url.includes("/contact")) {
                inspectSingleRecord(url, host, jsonObj, globalPan);
            }
            if (totalCaptured > 0) {
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
                    const reqUrl = this._seraUrl || "";

                    if (this.responseType === 'json' && this.response) {
                        inspectPayload(reqUrl, this.response);
                    } else if (this.responseType === 'blob' && this.response instanceof Blob) {
                        this.response.text().then(text => {
                            if (text && typeof text === 'string') {
                                const trimmed = text.trim();
                                if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
                                    try {
                                        const jsonObj = JSON.parse(trimmed);
                                        inspectPayload(reqUrl, jsonObj);
                                    } catch (_) {}
                                }
                            }
                        }).catch(() => {});
                    } else if (this.responseType === 'arraybuffer' && this.response instanceof ArrayBuffer) {
                        try {
                            const text = new TextDecoder('utf-8').decode(this.response);
                            if (text && typeof text === 'string') {
                                const trimmed = text.trim();
                                if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
                                    const jsonObj = JSON.parse(trimmed);
                                    inspectPayload(reqUrl, jsonObj);
                                }
                            }
                        } catch (_) {}
                    } else if (this.responseType === '' || this.responseType === 'text') {
                        try {
                            const raw = this.responseText;
                            if (raw && typeof raw === 'string') {
                                const trimmed = raw.trim();
                                if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
                                    const jsonObj = JSON.parse(trimmed);
                                    inspectPayload(reqUrl, jsonObj);
                                }
                            }
                        } catch (_) {}
                    } else if (this.response) {
                        if (typeof this.response === 'object' && !(this.response instanceof Blob) && !(this.response instanceof ArrayBuffer)) {
                            inspectPayload(reqUrl, this.response);
                        } else if (typeof this.response === 'string') {
                            const trimmed = this.response.trim();
                            if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
                                try {
                                    const jsonObj = JSON.parse(trimmed);
                                    inspectPayload(reqUrl, jsonObj);
                                } catch (_) {}
                            }
                        }
                    }
                }
            } catch (e) {
                // Passive catch
            }
        });
        return originalXhrSend.apply(this, args);
    };

})();
