// tracker.js - Sera DOM Detector (Passive & Active DOM Confirmation Tracker)
// Monitors portal DOM confirmation screens, banners, modals, and tables for Ack/ARN numbers.

(function() {
  console.log("⚡ Sera DOM (Visual Layer Tracker): tracker.js loaded.");

  // Base portal domains to monitor
  const BASE_SERVICE_PORTALS = [
    "incometax.gov.in",
    "incometaxindiaefiling.gov.in",
    "gst.gov.in",
    "tdscpc.gov.in",
    "mca.gov.in",
    "localhost",
    "127.0.0.1"
  ];

  const currentHost = (window.location.hostname || "").toLowerCase();

  // Page Route & Normalization Helper
  function getNormalizedPageKey(url) {
    try {
      const u = new URL(url || window.location.href);
      let path = u.pathname || "";
      let hashRoute = "";
      if (u.hash) {
        hashRoute = u.hash.split('?')[0];
      }
      let key = (u.origin + path + (hashRoute ? hashRoute : "")).toLowerCase();
      return key.replace(/\/+$/, '');
    } catch (e) {
      return (url || window.location.href || "").toLowerCase().split('?')[0].replace(/\/+$/, '');
    }
  }

  // Page visit & re-entry tracker for DOM capture replacement
  const seraPageTracker = window.__SERA_DOM_PAGE_TRACKER__ || {
    activePageKey: getNormalizedPageKey(window.location.href),
    pageDataHashes: {}
  };
  window.__SERA_DOM_PAGE_TRACKER__ = seraPageTracker;

  // Route / Site Link History Tracker
  const seraNav = window.__SERA_NAV_TRACKER__ || {
    history: [],
    lastUrl: window.location.href,
    lastTime: performance.now(),
    step: 1
  };
  window.__SERA_NAV_TRACKER__ = seraNav;

  function getShortPath(url) {
    try {
      const u = new URL(url);
      let p = u.hash ? u.hash.replace(/^#\/?/, '') : u.pathname;
      p = p.split('?')[0].split('/').filter(Boolean).pop() || 'home';
      return p;
    } catch (e) {
      return "unknown";
    }
  }

  function recordNavigation(url) {
    const newPageKey = getNormalizedPageKey(url);
    const prevPageKey = seraPageTracker.activePageKey;
    if (newPageKey !== prevPageKey) {
      seraPageTracker.activePageKey = newPageKey;
      // Reset capture hash for the new active page upon transition so returning to it captures a fresh snapshot
      delete seraPageTracker.pageDataHashes[newPageKey];
    }

    if (seraNav.history.length === 0) {
      seraNav.history.push(`[#${seraNav.step} @ 0.000000] - ${getShortPath(url)}`);
    } else {
      const now = performance.now();
      const diff = ((now - seraNav.lastTime) / 1000).toFixed(6);
      seraNav.history.push(`[#${seraNav.step} @ ${diff}] - ${getShortPath(url)}`);
      seraNav.lastTime = now;
      if (seraNav.history.length > 25) {
        seraNav.history.shift();
      }
    }
    seraNav.step++;
    seraNav.lastUrl = url;
  }

  // Initialize first route
  if (seraNav.history.length === 0) recordNavigation(window.location.href);

  // Deduplication set for legacy/cross-session guards
  window.__SERA_DOM_CAPTURED_SET__ = window.__SERA_DOM_CAPTURED_SET__ || new Set();

  function isAllowedDomain(allowedList) {
    if (window.location.protocol === "file:") return true;
    if (!currentHost) return true;
    if (BASE_SERVICE_PORTALS.some(d => currentHost.includes(d) || d.includes(currentHost))) {
      return true;
    }
    if (Array.isArray(allowedList) && allowedList.length > 0) {
      if (allowedList.some(d => d && (currentHost.includes(d.toLowerCase()) || d.toLowerCase().includes(currentHost)))) {
        return true;
      }
    }
    return false;
  }

  // Check extension settings and context from chrome.storage.local
  chrome.storage.local.get([
    'activeAutofillPayload',
    'manualAssistPayload',
    'mecpPayload',
    'trackerEnabled',
    'fstEnabled',
    'allowedDomains'
  ], (data) => {
    // 1. If tracker or FST is explicitly disabled globally in preferences, skip
    if (data && (data.trackerEnabled === false || data.fstEnabled === false)) {
      console.log("Sera DOM: Tracker/FST is explicitly disabled in settings. Skipping.");
      return;
    }

    // 2. Domain Scoping: only run on designated compliance portals or custom allowed domains
    const allowed = data && data.allowedDomains;
    if (!isAllowedDomain(allowed)) {
      // Idle on non-portal domains
      return;
    }

    // 3. Resolve context if autofill, manual assist, or MECP is stored (STRICT DOMAIN SCOPING)
    let matchedContext = null;
    const payloads = [data && data.activeAutofillPayload, data && data.manualAssistPayload, data && data.mecpPayload].filter(Boolean);
    for (const p of payloads) {
      if (p.url && p.url.toLowerCase().includes(currentHost)) {
        matchedContext = p;
        break;
      }
      if (p.portal) {
        const pLower = p.portal.toLowerCase();
        if ((currentHost.includes("incometax") || currentHost.includes("efiling")) && (pLower.includes("income") || pLower.includes("itd"))) {
          matchedContext = p;
          break;
        }
        if (currentHost.includes("gst.gov.in") && pLower.includes("gst")) {
          matchedContext = p;
          break;
        }
      }
    }
    // NEVER fall back to payloads[0] if it belongs to a completely different portal!

    console.log("⚡ Sera DOM: Monitoring active on portal [" + currentHost + "].");
    startDomMonitoring(matchedContext);
  });

  function startDomMonitoring(initialContext) {
    let context = initialContext || {};
    let debounceTimer = null;

    // Listen for storage changes in case context is set after page load
    try {
      chrome.storage.onChanged.addListener((changes, area) => {
        if (area === 'local') {
          if (changes.activeAutofillPayload && changes.activeAutofillPayload.newValue) {
            context = Object.assign({}, context, changes.activeAutofillPayload.newValue);
          }
          if (changes.manualAssistPayload && changes.manualAssistPayload.newValue) {
            context = Object.assign({}, context, changes.manualAssistPayload.newValue);
          }
        }
      });
    } catch (_) {}

    function scheduleScan() {
      if (window.location.href !== seraNav.lastUrl) {
        recordNavigation(window.location.href);
      }
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        scanDomForFiling(context);
      }, 350);
    }

    // Initial scans
    scheduleScan();
    setTimeout(scheduleScan, 1000);
    setTimeout(scheduleScan, 2500);

    // MutationObserver to catch dynamically rendered SPAs (Angular / React)
    const targetNode = document.body || document.documentElement;
    if (targetNode) {
      const observer = new MutationObserver(() => {
        scheduleScan();
      });
      observer.observe(targetNode, {
        childList: true,
        subtree: true,
        characterData: true
      });
    }
  }

  // Helper: extract all visible text from an element without children restriction
  function getCleanText(el) {
    if (!el) return "";
    return (el.innerText || el.textContent || "").replace(/\s+/g, ' ').trim();
  }

  // Determine portal name from hostname or context (HOSTNAME ALWAYS TAKES PRECEDENCE)
  function getPortalName(context) {
    if (currentHost.includes("incometax") || currentHost.includes("efiling")) return "income tax";
    if (currentHost.includes("gst.gov.in")) return "gst";
    if (currentHost.includes("tdscpc.gov.in")) return "traces";
    if (currentHost.includes("mca.gov.in")) return "mca";
    if (context && context.portal) return context.portal;
    return "Compliance Portal";
  }

  function isValidArn(arn) {
    if (!arn || typeof arn !== 'string') return false;
    const s = arn.trim();
    if (s.length < 7 || s.length > 30) return false;

    // Rule 1: An authentic government ARN / Ack number MUST contain at least 2 digits
    const digitCount = (s.match(/\d/g) || []).length;
    if (digitCount < 2) return false;

    // Rule 2: Cannot be common English labels, word fragments, or page noise
    const lower = s.toLowerCase();
    const junkWords = [
      "feedback", "website", "policies", "terms", "undefined", "null",
      "receipt", "download", "dashboard", "select", "summary", "validation",
      "contact", "support", "logout", "login", "acknowledgement", "acknowledgment",
      "wledgement", "nowledgement"
    ];
    if (junkWords.some(j => lower.includes(j))) return false;

    return /^[A-Za-z0-9\/-]{7,30}$/.test(s);
  }

  function scanDomForFiling(context) {
    try {
      const currentUrl = window.location.href.toLowerCase();
      let urlForm = "";
      let urlPeriod = "";

      // 1. Universal URL & Route Form and Period Pre-Extraction
      const itrMatch = currentUrl.match(/(?:fo-)?itr[-_]?([1-7][a-z]?)(?:-ay(\d{2,4}))?/i);
      if (itrMatch) {
        urlForm = "ITR-" + itrMatch[1].toUpperCase();
        if (itrMatch[2]) {
          const y = itrMatch[2];
          urlPeriod = y.length === 4 ? `AY ${y}-${parseInt(y.slice(2)) + 1}` : `AY 20${y}-${parseInt(y) + 1}`;
        }
      } else {
        const gstMatch = currentUrl.match(/(?:returns\/auth\/gstr|returns\/auth\/|gstr[-_]?)(1|3b|4|9|9c|cmp08|cmp-08)/i);
        if (gstMatch) {
          const gType = gstMatch[1].toUpperCase().replace("-", "");
          urlForm = gType === "CMP08" ? "CMP-08" : `GSTR-${gType}`;
        } else {
          const ayMatch = currentUrl.match(/foreturns-ay(\d{2})/);
          if (ayMatch) {
            urlPeriod = "AY 20" + ayMatch[1] + "-" + (parseInt(ayMatch[1]) + 1);
          }
        }
      }

      // Check for Filing Completion / Status / Active Return Endpoints
      const isFilingUrl = (
        currentUrl.includes("fo-e-verify-later") ||
        currentUrl.includes("e-verify-return/success") ||
        currentUrl.includes("success") ||
        currentUrl.includes("acknowledgement") ||
        currentUrl.includes("fo-filing-success") ||
        currentUrl.includes("submission-success") ||
        currentUrl.includes("gstr1") ||
        currentUrl.includes("gstr3b") ||
        currentUrl.includes("cmp08") ||
        currentUrl.includes("gstr9") ||
        currentUrl.includes("returns/auth") ||
        currentUrl.includes("view-filed-returns")
      );

      // 2. First check explicit selectors if provided by context
      if (context && context.success_selector) {
        const successEl = document.querySelector(context.success_selector);
        if (successEl) {
          let arn = "N/A";
          if (context.arn_selector) {
            const arnEl = document.querySelector(context.arn_selector);
            if (arnEl) arn = getCleanText(arnEl);
          }
          dispatchFilingResult(context, arn, "Explicit_Selector");
          return;
        }
      }

      // Positive confirmation & active tracking cues
      const CONFIRMATION_PHRASES = [
        "submitted successfully",
        "successfully submitted",
        "successfully filed",
        "successfully filed and verified",
        "successfully submitted your return",
        "successfully filed your return",
        "you have successfully submitted",
        "you have successfully filed",
        "return successfully e-verified",
        "successfully e-verified",
        "e-verification successful",
        "verification successful",
        "filing successful",
        "return filed successfully",
        "filed successfully",
        "has been successfully filed",
        "application reference number",
        "acknowledgement number",
        "acknowledgment reference number",
        "itr-v acknowledgement",
        "e-filing acknowledgement",
        "request has been submitted successfully",
        "token number is generated",
        "srn is generated",
        "challan generated successfully",
        "payment successful",
        "form submitted successfully",
        "e-verify within 30 days",
        "status - filed",
        "status : filed",
        "status: filed",
        "status - submitted",
        "status : submitted",
        "status: submitted",
        "return status : filed",
        "details of outward supplies",
        "view filed returns",
        "filed returns",
        "personal_information",
        "gross total income",
        "total deductions",
        "taxes paid",
        "tax liability"
      ];

      // Negative cues to prevent false alarms
      const NEGATIVE_PHRASES = [
        "failed to submit",
        "submission failed",
        "error occurred",
        "not submitted",
        "unable to submit",
        "how to file",
        "guidelines for filing",
        "sample acknowledgement"
      ];

      // Check entire document body text for confirmation signals
      const pageFullText = (document.body ? (document.body.innerText || document.body.textContent || "") : "").replace(/\s+/g, ' ');
      const lowerFullText = pageFullText.toLowerCase();

      let hasPositiveCue = false;
      for (const phrase of CONFIRMATION_PHRASES) {
        if (lowerFullText.includes(phrase)) {
          hasPositiveCue = true;
          break;
        }
      }

      for (const neg of NEGATIVE_PHRASES) {
        // If error message is prominent and no real confirmation exists
        if (lowerFullText.includes(neg) && !lowerFullText.includes("submitted successfully") && !lowerFullText.includes("filed successfully") && !lowerFullText.includes("status - filed")) {
          return;
        }
      }

      // 3. Extract Live Identity (Name & PAN/GSTIN) via Multi-Vector Scanning (ALWAYS RUNS ON EVERY PAGE/STEP)
      const identity = extractIdentityFromPage(context, pageFullText);

      // Clean Identity Isolation: If live DOM shows a different PAN/GSTIN than in-memory session, reset memory
      if (identity.pan && window.__SERA_SESSION_IDENTITY__.pan && identity.pan !== window.__SERA_SESSION_IDENTITY__.pan) {
        window.__SERA_SESSION_IDENTITY__ = { pan: "", client_name: "", form: "", period: "", gstin: "" };
      }
      if (identity.pan) window.__SERA_SESSION_IDENTITY__.pan = identity.pan;
      if (identity.client_name) window.__SERA_SESSION_IDENTITY__.client_name = identity.client_name;
      if (identity.gstin) window.__SERA_SESSION_IDENTITY__.gstin = identity.gstin;

      const finalPan = identity.pan || window.__SERA_SESSION_IDENTITY__.pan || (context && context.pan) || "";
      const finalClientName = identity.client_name || window.__SERA_SESSION_IDENTITY__.client_name || (context && (context.client_name || context.name)) || "";
      const finalGstin = identity.gstin || window.__SERA_SESSION_IDENTITY__.gstin || "";

      // Gate: Must have at least a client identity or positive filing cue to record
      if (!finalPan && !finalClientName && !hasPositiveCue && !isFilingUrl) {
        return;
      }

      // 4. Extract ARN / Ack Number
      let extractedArn = null;

      // A. Labeled Ack/ARN Patterns (Explicitly ordered from most specific to general)
      const ackMatch = pageFullText.match(/\b(?:acknowledgement\s*number|acknowledgment\s*number|acknowledgement\s*no\.?|acknowledgment\s*no\.?|itr-v\s*acknowledgement|application\s*reference\s*number|acknowledgment\s*reference\s*number|reference\s*number|reference\s*no\.?|token\s*number|token\s*no\.?|transaction\s*id|ack\s*no\.?|ack\s*number|srn)\b\s*(?:is|:|-|#|\s)\s*([A-Za-z0-9\/-]{7,25})/i);
      if (ackMatch && ackMatch[1] && isValidArn(ackMatch[1])) {
        extractedArn = ackMatch[1].trim();
      }

      // B. GST ARN Pattern (Handles "AB190726085446K" with exact GST format: 2 state digits + 13 alphanum)
      if (!extractedArn || extractedArn === "N/A") {
        const gstArnMatch = pageFullText.match(/\b([0-9]{2}[A-Z0-9]{13})\b/);
        if (gstArnMatch && gstArnMatch[1] && isValidArn(gstArnMatch[1])) {
          extractedArn = gstArnMatch[1].trim();
        }
      }

      // C. Standalone ITD 15-digit Ack Number
      if (!extractedArn || extractedArn === "N/A") {
        const standalone15 = pageFullText.match(/\b(\d{15})\b/);
        if (standalone15 && standalone15[1] && isValidArn(standalone15[1])) {
          extractedArn = standalone15[1].trim();
        }
      }

      // D. TRACES / MCA Tokens
      if (!extractedArn || extractedArn === "N/A") {
        const tracesMatch = pageFullText.match(/\b([A-Z][0-9]{8})\b/); // MCA SRN
        if (tracesMatch && tracesMatch[1] && isValidArn(tracesMatch[1])) {
          extractedArn = tracesMatch[1].trim();
        }
      }

      // 5. Extract Assessment Year / Period (Handles ITD AY and GST FY + Tax Period)
      let extractedPeriod = urlPeriod || "";
      const taxPeriodMatch = pageFullText.match(/\b(?:Tax\s*Period|Return\s*Period)\s*[-:]\s*([A-Za-z0-9()]+)/i);
      const fyMatch = pageFullText.match(/\b(?:FY|F\.Y\.|Financial\s*Year)\s*[-:]?\s*(\d{4}[-–]\d{2,4})\b/i);
      if (taxPeriodMatch && fyMatch) {
        extractedPeriod = `${taxPeriodMatch[1].trim()} (FY ${fyMatch[1].trim()})`;
      } else if (taxPeriodMatch) {
        extractedPeriod = taxPeriodMatch[1].trim();
      } else if (fyMatch) {
        extractedPeriod = `FY ${fyMatch[1].trim()}`;
      } else {
        const ayMatch = pageFullText.match(/\b(?:AY|A\.Y\.|Assessment\s*Year)\s*[:#-]?\s*(\d{4}[-–]\d{2,4})\b/i);
        if (ayMatch && ayMatch[1]) {
          let y = ayMatch[1].replace('–', '-');
          extractedPeriod = `AY ${y}`;
        }
      }
      if (!extractedPeriod) {
        extractedPeriod = window.__SERA_SESSION_IDENTITY__.period || (context && context.period_label) || "";
      }
      if (extractedPeriod) window.__SERA_SESSION_IDENTITY__.period = extractedPeriod;

      // 6. Extract Filing / Form Type (Handles all ITD ITR-1..7 and GST GSTR-1..9)
      let extractedForm = urlForm || "";
      if (!extractedForm || extractedForm === "Filing Confirmation" || extractedForm === "Dashboard / Profile") {
        const formMatch = pageFullText.match(/\b(GSTR-1\/IFF|GSTR-1|GSTR-3B|CMP-08|GSTR-9C|GSTR-9|GSTR-2B|GSTR-4|ITR-[1-7][A-Z]?|Form\s*16[A]?|Form\s*24Q|Form\s*26Q|Form\s*27Q|Form\s*27EQ)\b/i);
        if (formMatch && formMatch[1]) {
          extractedForm = formMatch[1].toUpperCase().replace(/\s+/, '-');
        } else {
          extractedForm = window.__SERA_SESSION_IDENTITY__.form || (context && context.filing_type) || (hasPositiveCue ? "Filing Confirmation" : "Dashboard / Profile");
        }
      }
      if (extractedForm) window.__SERA_SESSION_IDENTITY__.form = extractedForm;

      // 7. Extract Status from Summary Card or Confirmation Banners (e.g. "Status - Filed", "Status - Submitted")
      let extractedStatus = "";
      const statusCardMatch = pageFullText.match(/\bStatus\s*[-:]\s*([A-Za-z]+)/i);
      if (statusCardMatch) {
        extractedStatus = statusCardMatch[1].trim();
      } else if (lowerFullText.includes("filed successfully") || lowerFullText.includes("successfully filed")) {
        extractedStatus = "Filed";
      } else if (lowerFullText.includes("submitted successfully") || lowerFullText.includes("successfully submitted")) {
        extractedStatus = "Submitted";
      }

      // 8. Extract Exact Breadcrumbs from DOM
      let domBreadcrumbs = "";
      try {
        const bcEl = document.querySelector('nav[aria-label*="breadcrumb" i], .breadcrumb, .breadcrumbs, app-breadcrumb, [class*="breadcrumb" i], [class*="routing" i]');
        if (bcEl) {
          domBreadcrumbs = getCleanText(bcEl);
        } else {
          // Look for text containing 'Dashboard >'
          const bcMatch = pageFullText.match(/(?:Dashboard\s*>\s*[A-Za-z0-9\s.>–\/-]+)/i);
          if (bcMatch) domBreadcrumbs = bcMatch[0].trim();
        }
      } catch (_) {}

      // 9. Extract Full Confirmation / Status Message from DOM
      let confirmationMessage = "";
      const msgMatch = pageFullText.match(/(?:You\s*have\s*successfully\s*(?:submitted|filed)[^.]*?\.\s*(?:you\s*still\s*need\s*to\s*e-Verify[^.]*?\.)?|[A-Za-z0-9\s-]*?has\s*been\s*successfully\s*filed[^.]*?\.|\bStatus\s*[-:]\s*Filed\b|\bStatus\s*[-:]\s*Submitted\b)/i);
      if (msgMatch) {
        confirmationMessage = msgMatch[0].trim();
      }

      // 10. Harvest Complete DOM Attribute Snapshot as scraped_data
      const scrapedData = harvestDomSnapshot(pageFullText);

      const finalArn = extractedArn || "N/A";

      // Dispatch capture result
      dispatchFilingResult(context, finalArn, "DOM_Detector", {
        client_name: finalClientName,
        pan: finalPan,
        gstin: finalGstin,
        period_label: extractedPeriod,
        filing_type: extractedForm,
        status: extractedStatus || (hasPositiveCue ? "Filed" : "Draft"),
        site_link_history: seraNav.history.join("\n"),
        dom_breadcrumbs: domBreadcrumbs,
        confirmation_message: confirmationMessage,
        scraped_data: scrapedData
      });

    } catch (err) {
      console.warn("Sera DOM: Error in scanDomForFiling:", err);
    }
  }

  // Persistent in-memory session identity storage
  window.__SERA_SESSION_IDENTITY__ = window.__SERA_SESSION_IDENTITY__ || { pan: "", client_name: "", form: "", period: "" };

  // Multi-Vector Identity Scanner: Browser Storage, Form Inputs, Profile Badges, and Full Text
  function extractIdentityFromPage(context, pageFullText) {
    // ALWAYS start clean so all live DOM and storage vectors run fresh
    let result = {
      client_name: "",
      pan: ""
    };

    const invalidTokens = [
      "income tax", "department", "government", "dashboard", "logout", "login",
      "profile", "submit", "select", "assessment", "e-filing", "gst portal",
      "home", "services", "downloads", "help", "contact", "search", "view",
      "acknowledgement", "verification", "return", "status", "welcome",
      "user", "password", "continue", "skip", "instructions", "file",
      "first name", "middle name", "last name", "surname", "org name",
      "assessee name", "taxpayer name", "legal name", "trade name",
      "part a", "general information", "schedule"
    ];

    function cleanExtractedName(str) {
      if (!str) return "";
      let s = str.replace(/\s+/g, ' ').trim();
      s = s.replace(/^(?:[A-Z]?\d{1,3}\.|\d+[\.\s-])\s*/i, ''); // Strip section numbering e.g. "A1. " or "1. "
      s = s.replace(/\s*[\(|-]?\s*[A-Z]{5}[0-9]{4}[A-Z]\s*[\)]?$/i, ''); // Strip trailing PAN with or without () / - / |
      s = s.replace(/\s*[\(|-]?\s*[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]\s*[\)]?$/i, ''); // Strip trailing GSTIN
      s = s.replace(/(?:\s*[|•–—]\s*.*$)/, ''); // Remove anything after separator
      s = s.replace(/\s*(?:Individual|HUF|Company|Firm|AOP|BOI|Taxpayer)$/i, ''); // Remove status suffix
      s = s.replace(/\s*(?:Logout|Profile|Update|Settings|Dashboard|Login|Home|My Account)$/i, '');
      return s.trim();
    }

    function isValidClientName(str) {
      if (!str || typeof str !== 'string') return false;
      const raw = str.trim();
      const s = cleanExtractedName(raw);
      if (s.length < 3 || s.length > 80) return false;
      if (/@|www\.|\.gov|\.in|http/i.test(s)) return false;
      if (/^[A-Z]{5}[0-9]{4}[A-Z]$/.test(s)) return false; // Raw PAN
      if (/^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/.test(s)) return false; // Raw GSTIN
      if (/^\d+$/.test(s)) return false;

      // Reject form section headings and field labels (e.g. "A1. First Name", "Part A", "Last Name")
      if (/^(?:first\s*name|middle\s*name|last\s*name|surname|org\s*name|assessee\s*name|taxpayer\s*name|legal\s*name|trade\s*name)$/i.test(s)) {
        return false;
      }
      if (/^(?:[A-Z]?\d{1,3}\.|\d+[\.\s-])\s*(?:First|Middle|Last|Sur|Org|Assessee|Taxpayer|Legal|Trade|Father|Mother|Address|Status|Part|General)/i.test(raw)) {
        return false;
      }

      const lower = s.toLowerCase();
      if (invalidTokens.some(tok => lower === tok || lower.startsWith(tok + " ") || lower.endsWith(" " + tok))) return false;
      return true;
    }

    function isValidPan(p) {
      if (!p || typeof p !== 'string') return false;
      const s = p.trim().toUpperCase();
      if (!/^[A-Z]{5}[0-9]{4}[A-Z]$/.test(s)) return false;
      return !["AAAAA0000A", "ABCDE1234F", "XXXXX0000X"].includes(s);
    }

    const text = pageFullText || (document.body ? (document.body.innerText || document.body.textContent || "") : "");

    // VECTOR 0: Dedicated GST Portal Extraction (Header Profile & Summary Cards)
    if (currentHost.includes("gst.gov.in") || text.includes("Goods and Services Tax") || text.includes("GSTIN")) {
      // 1. Summary Card: GSTIN - ..., Legal Name - ..., Trade Name - ...
      const gstinCard = text.match(/\bGSTIN\s*[-:]\s*([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b/i);
      if (gstinCard) {
        result.gstin = gstinCard[1].toUpperCase();
        result.pan = gstinCard[1].slice(2, 12).toUpperCase();
      }

      const legalNameCard = text.match(/\bLegal\s*Name\s*[-:]\s*([A-Za-z0-9\s.'&-]{2,80}?)(?=\s+(?:Trade|Tax|Status|FY|Due|\*|$))/i);
      if (legalNameCard && isValidClientName(legalNameCard[1])) {
        result.client_name = cleanExtractedName(legalNameCard[1]);
      }

      if (!result.client_name) {
        const tradeNameCard = text.match(/\bTrade\s*Name\s*[-:]\s*([A-Za-z0-9\s.'&-]{2,80}?)(?=\s+(?:Status|FY|Tax|Due|\*|$))/i);
        if (tradeNameCard && isValidClientName(tradeNameCard[1])) {
          result.client_name = cleanExtractedName(tradeNameCard[1]);
        }
      }

      // 2. Top Header Profile Badge (e.g. "ARUN BAIDYA \n 19ATYPB6533F2ZX")
      const gstHeaderBadge = text.match(/([A-Za-z\s.]{3,60})\s+([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b/);
      if (gstHeaderBadge) {
        if (!result.gstin) {
          result.gstin = gstHeaderBadge[2].toUpperCase();
          result.pan = gstHeaderBadge[2].slice(2, 12).toUpperCase();
        }
        if (!result.client_name && isValidClientName(gstHeaderBadge[1])) {
          result.client_name = cleanExtractedName(gstHeaderBadge[1]);
        }
      }
    }

    // VECTOR 1: Browser Storage (sessionStorage & localStorage)
    try {
      const storages = [window.sessionStorage, window.localStorage];
      for (const st of storages) {
        if (!st) continue;
        for (let i = 0; i < st.length; i++) {
          const key = st.key(i);
          if (!key) continue;
          const val = st.getItem(key);
          if (!val || typeof val !== 'string') continue;

          // Check if value is JSON
          if (val.startsWith("{") || val.startsWith("[")) {
            try {
              const parsed = JSON.parse(val);
              if (parsed && typeof parsed === 'object') {
                if (!result.pan) {
                  const pCandidate = parsed.pan || parsed.userPan || parsed.panNumber || parsed.user_pan || parsed.PAN || parsed.panNo || parsed.userId || parsed.username;
                  if (isValidPan(pCandidate)) result.pan = pCandidate.toUpperCase();
                }
                if (!result.client_name) {
                  const nCandidate = parsed.name || parsed.taxpayerName || parsed.assesseeName || parsed.userName || parsed.userFullName || parsed.legalName || parsed.user_name || parsed.client_name || parsed.fullName || parsed.entityName;
                  if (isValidClientName(nCandidate)) result.client_name = cleanExtractedName(nCandidate);
                }
              }
            } catch (_) {}
          } else {
            // Raw string storage values
            if (!result.pan && isValidPan(val)) {
              result.pan = val.toUpperCase();
            }
          }
        }
      }
    } catch (_) {}

    // VECTOR 2: Tax Return Form Fields (ITR-1..7, PartA_GEN, GSTR, etc.)
    try {
      let fName = "", mName = "", lName = "";
      const nameInputs = document.querySelectorAll(
        'input[name*="FirstName" i], input[id*="FirstName" i], output[name*="FirstName" i], output[id*="FirstName" i], ' +
        'input[name*="SurName" i], input[id*="SurName" i], output[name*="SurName" i], output[id*="SurName" i], ' +
        'input[name*="LastName" i], input[id*="LastName" i], output[name*="LastName" i], output[id*="LastName" i], ' +
        'input[name*="MiddleName" i], input[id*="MiddleName" i], output[name*="MiddleName" i], output[id*="MiddleName" i], ' +
        'input[name*="AssesseeName" i], output[name*="AssesseeName" i], [data-testid*="first-name" i], [data-testid*="last-name" i]'
      );

      for (const el of nameInputs) {
        const key = ((el.getAttribute('name') || el.id || '') + '').toLowerCase();
        const val = (el.value || el.innerText || el.textContent || '').trim();
        if (!val || val.length > 60 || !isValidClientName(val)) continue;

        if (key.includes('firstname') || key.includes('first_name')) fName = val;
        else if (key.includes('middlename') || key.includes('middle_name')) mName = val;
        else if (key.includes('surname') || key.includes('lastname') || key.includes('last_name') || key.includes('orgname')) lName = val;
        else if (!fName && !lName && (key.includes('assesseename') || key.includes('taxpayername'))) {
          fName = val;
        }
      }

      if (fName || lName) {
        const combined = cleanExtractedName([fName, mName, lName].filter(Boolean).join(" "));
        if (isValidClientName(combined)) {
          result.client_name = combined;
        }
      }
    } catch (_) {}

    // VECTOR 3: Form Inputs, Outputs & Element Attributes
    try {
      if (!result.pan) {
        const panEls = document.querySelectorAll('input[id*="pan" i], input[name*="pan" i], [data-pan], [title*="PAN" i], output[id*="pan" i], output[name*="pan" i]');
        for (const el of panEls) {
          const val = (el.value || el.innerText || el.textContent || el.getAttribute('title') || "").trim();
          const match = val.match(/\b([A-Z]{5}[0-9]{4}[A-Z])\b/);
          if (match && isValidPan(match[1])) {
            result.pan = match[1].toUpperCase();
            break;
          }
        }
      }
    } catch (_) {}

    // VECTOR 4: Header & Profile Badges (ITD 2.0, GST, TRACES, MCA)
    const profileSelectors = [
      '#loginUsername', 'button[id*="loginUsername" i]', 'button[id*="loginUsername" i] *',
      '.user-profile-name', '.user-name', '.username', 'span.header-username', 'span.user-name',
      '.user-details', '.user-details *', 'div[class*="user-name" i]', 'div[class*="profile-name" i]',
      'span[class*="profile-name" i]', 'div.login-user-name', 'span.welcome-user', '.userInfoName',
      'app-header .user-name', 'app-header .profile-name', 'app-header button', 'header button',
      'header .dropdown-toggle', 'nav .dropdown-toggle', '.navbar-nav .dropdown-toggle',
      '[data-testid*="assessee-name" i]', '[data-testid*="taxpayer-name" i]',
      '[id*="assesseeName" i]', '[id*="taxpayerName" i]',
      '.assessee-name', '.taxpayer-name', '.legal-name', '.trade-name', 'app-user-profile .name'
    ];

    for (const sel of profileSelectors) {
      try {
        const els = document.querySelectorAll(sel);
        for (const el of els) {
          const rawTxt = getCleanText(el);
          if (!rawTxt || rawTxt.length < 3) continue;

          // Check if element contains both NAME and PAN
          const comboMatch = rawTxt.match(/([A-Za-z\s.]{3,60})[\s(|–—•-]+([A-Z]{5}[0-9]{4}[A-Z])[\)]?/);
          if (comboMatch) {
            const candName = cleanExtractedName(comboMatch[1]);
            if (!result.client_name && isValidClientName(candName)) {
              result.client_name = candName;
            }
            if (!result.pan && isValidPan(comboMatch[2])) {
              result.pan = comboMatch[2].toUpperCase();
            }
          }

          let txt = rawTxt.replace(/^(?:Welcome|Hi|Hello)[,\s:]+/i, '').trim();
          txt = cleanExtractedName(txt);
          if (!result.client_name && isValidClientName(txt)) {
            result.client_name = txt;
          }
        }
      } catch (_) {}
    }

    // VECTOR 5: Full Page Text Matching (Fallback)
    const text = pageFullText || (document.body ? (document.body.innerText || document.body.textContent || "") : "");

    // GSTIN from page text (e.g. 19AFAPM7143K1Z7) -> auto-derive PAN (AFAPM7143K)
    const gstinMatch = text.match(/\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b/);
    if (gstinMatch) {
      if (!result.gstin) result.gstin = gstinMatch[1].toUpperCase();
      if (!result.pan) {
        const derivedPan = gstinMatch[1].slice(2, 12).toUpperCase();
        if (isValidPan(derivedPan)) result.pan = derivedPan;
      }
    }

    // PAN from page text
    if (!result.pan) {
      const panMatch = text.match(/\b([A-Z]{5}[0-9]{4}[A-Z])\b/);
      if (panMatch && isValidPan(panMatch[1])) {
        result.pan = panMatch[1].toUpperCase();
      }
    }

    // Labeled fields from page text
    if (!result.client_name) {
      const nameMatch = text.match(/(?:Legal\s*Name\s*(?:of\s*Registered\s*Person)?|Trade\s*Name|Assessee\s*Name|Name\s*of\s*(?:the\s*)?Assessee|Taxpayer\s*Name|Name\s*of\s*(?:the\s*)?Taxpayer|Name\s*of\s*Deductor|Company\s*Name|LLP\s*Name)\s*[:#-]\s*([A-Za-z0-9\s.'&-]{3,70})/i);
      if (nameMatch && nameMatch[1]) {
        const candidate = cleanExtractedName(nameMatch[1]);
        if (isValidClientName(candidate)) result.client_name = candidate;
      }
    }

    // Greeting from page text
    if (!result.client_name) {
      const greetingMatch = text.match(/(?:Welcome|Hi)[,\s]+([A-Za-z][A-Za-z \t.'-]{2,60})/i);
      if (greetingMatch && greetingMatch[1]) {
        const candidate = cleanExtractedName(greetingMatch[1]);
        if (isValidClientName(candidate)) result.client_name = candidate;
      }
    }

    return result;
  }

  // Harvest Complete Structured DOM Snapshot for Local PC Python Parser
  function harvestDomSnapshot(pageFullText) {
    const snapshot = {
      title_attributes: [],
      ng_binds: [],
      summary_labels: {},
      form_fields: {},
      header_badges: [],
      breadcrumbs: "",
      session_storage: {},
      confirmation_text: ""
    };

    try {
      // 1. Title attributes (e.g. <a title="MOHAMMED RAFIQUE GAZI">)
      const titleEls = document.querySelectorAll('[title]');
      for (const el of titleEls) {
        const t = el.getAttribute('title');
        if (t && t.trim().length > 2 && t.trim().length < 100) {
          snapshot.title_attributes.push({
            tag: el.tagName.toLowerCase(),
            class: el.className || "",
            id: el.id || "",
            title: t.trim()
          });
        }
      }

      // 2. Angular data-ng-bind and ng-bind elements
      const ngEls = document.querySelectorAll('[data-ng-bind], [ng-bind]');
      for (const el of ngEls) {
        const expr = el.getAttribute('data-ng-bind') || el.getAttribute('ng-bind');
        const val = (el.innerText || el.textContent || "").trim();
        if (expr && val) {
          snapshot.ng_binds.push({
            directive: el.hasAttribute('data-ng-bind') ? 'data-ng-bind' : 'ng-bind',
            expr: expr.trim(),
            value: val
          });
        }
      }

      // 3. Summary labels from key-value cards / tables (e.g. GSTIN - ..., Legal Name - ..., Status - ...)
      const text = pageFullText || (document.body ? (document.body.innerText || document.body.textContent || "") : "");
      const labelRegex = /\b(GSTIN|Legal\s*Name|Trade\s*Name|FY|Tax\s*Period|Return\s*Period|Status|Due\s*Date|Acknowledgement\s*Number|Ack\s*No\.?|ARN|Form|Period|Assessment\s*Year)\s*[-:]\s*([A-Za-z0-9\s.'&()/-]{2,80})/gi;
      let match;
      while ((match = labelRegex.exec(text)) !== null) {
        const k = match[1].trim();
        const v = match[2].trim().replace(/\s+/g, ' ');
        snapshot.summary_labels[k] = v;
      }

      // 4. Form inputs, outputs, selects, textareas
      const fieldEls = document.querySelectorAll('input, output, select, textarea, [data-pan]');
      for (const el of fieldEls) {
        const key = el.name || el.id || el.getAttribute('data-pan') || el.getAttribute('aria-label');
        const val = (el.value || el.innerText || el.textContent || "").trim();
        if (key && val && val.length < 100) {
          snapshot.form_fields[key] = val;
        }
      }

      // 5. Header / Profile badges
      const badgeEls = document.querySelectorAll('#loginUsername, .user-profile-name, .header-username, app-header .user-name, [class*="profile-name" i]');
      for (const el of badgeEls) {
        const bText = (el.innerText || el.textContent || "").replace(/\s+/g, ' ').trim();
        if (bText && !snapshot.header_badges.includes(bText)) {
          snapshot.header_badges.push(bText);
        }
      }

      // 6. Breadcrumbs
      const bcEl = document.querySelector('nav[aria-label*="breadcrumb" i], .breadcrumb, .breadcrumbs, app-breadcrumb, [class*="breadcrumb" i], [class*="routing" i]');
      if (bcEl) {
        snapshot.breadcrumbs = (bcEl.innerText || bcEl.textContent || "").replace(/\s+/g, ' ').trim();
      } else {
        const bcMatch = text.match(/(?:Dashboard\s*>\s*[A-Za-z0-9\s.>–\/-]+)/i);
        if (bcMatch) snapshot.breadcrumbs = bcMatch[0].trim();
      }

      // 7. Session storage snapshot
      if (window.sessionStorage) {
        for (let i = 0; i < window.sessionStorage.length; i++) {
          const k = window.sessionStorage.key(i);
          if (k) {
            const v = window.sessionStorage.getItem(k);
            if (v && v.length < 500) {
              snapshot.session_storage[k] = v;
            }
          }
        }
      }

      // 8. Confirmation / Status Text
      const confMatch = text.match(/(?:You\s*have\s*successfully\s*(?:submitted|filed)[^.]*?\.\s*(?:you\s*still\s*need\s*to\s*e-Verify[^.]*?\.)?|[A-Za-z0-9\s-]*?has\s*been\s*successfully\s*filed[^.]*?\.|\bStatus\s*[-:]\s*Filed\b|\bStatus\s*[-:]\s*Submitted\b)/i);
      if (confMatch) {
        snapshot.confirmation_text = confMatch[0].replace(/\s+/g, ' ').trim();
      }

    } catch (err) {
      console.warn("Sera DOM: Error harvesting scraped_data:", err);
    }

    return snapshot;
  }

  function dispatchFilingResult(ctx, arn, captureType, meta = {}) {
    const portalName = getPortalName(ctx);

    // Cross-Portal & Cross-Client Context Sanitizer: prevent stale GST context from polluting ITD, or vice versa
    let safeCtx = ctx;
    if (safeCtx) {
      if (safeCtx.portal) {
        const p = safeCtx.portal.toLowerCase();
        if (portalName === "income tax" && p.includes("gst")) safeCtx = null;
        if (portalName === "gst" && (p.includes("income") || p.includes("itd"))) safeCtx = null;
      }
      // If live PAN/GSTIN was extracted on page and safeCtx has a different PAN, discard safeCtx to prevent embedding wrong client data!
      if (meta && meta.pan && safeCtx && safeCtx.pan && meta.pan.toUpperCase() !== safeCtx.pan.toUpperCase()) {
        safeCtx = null;
      }
    }

    const clientName = (meta && meta.client_name) || (safeCtx && safeCtx.client_name) || (safeCtx && safeCtx.name) || "";
    const pan = (meta && meta.pan) || (safeCtx && safeCtx.pan) || "";
    const gstin = (meta && meta.gstin) || (meta && meta.scraped_data && meta.scraped_data.summary_labels && meta.scraped_data.summary_labels.GSTIN) || "";
    const period = (meta && meta.period_label) || (safeCtx && safeCtx.period_label) || "";
    const filingType = (meta && meta.filing_type) || (safeCtx && safeCtx.filing_type) || "Filing Confirmation";
    const status = (meta && meta.status) || (meta && meta.scraped_data && meta.scraped_data.summary_labels && meta.scraped_data.summary_labels.Status) || "Submitted";
    const clientId = (safeCtx && safeCtx.client_id) || null;

    const currentPageKey = getNormalizedPageKey(window.location.href);
    const formFieldsSample = (meta && meta.scraped_data && meta.scraped_data.form_fields) ? JSON.stringify(meta.scraped_data.form_fields) : "";
    const dataHash = `${portalName}|${arn}|${pan}|${period}|${clientName}|${status}|${(meta && meta.confirmation_message) || ''}|${formFieldsSample}`;

    // Constraint Rule: Avoid continuous re-firing while on the same page visit with unchanged data,
    // but ALWAYS allow fresh captures when navigating away and coming back to the page link.
    if (seraPageTracker.pageDataHashes[currentPageKey] === dataHash) {
      return;
    }
    seraPageTracker.pageDataHashes[currentPageKey] = dataHash;

    console.log(`⚡ Sera DOM Captured [${captureType}]: Portal=${portalName}, Name=${clientName}, ARN=${arn}, PAN=${pan}, GSTIN=${gstin}, Period=${period}, Status=${status}, PageKey=${currentPageKey}`);

    const payload = {
      type: "filing_result",
      client_id: clientId,
      client_name: clientName,
      name: clientName,
      taxpayer_name: clientName,
      portal: portalName,
      arn: arn,
      capture_method: "DOM_Tracker",
      period_label: period,
      filing_type: filingType,
      status: status,
      pan: pan,
      gstin: gstin,
      url: window.location.href,
      page_key: currentPageKey,
      is_page_update: true,
      site_link_history: (meta && meta.site_link_history) || "",
      dom_breadcrumbs: (meta && meta.dom_breadcrumbs) || "",
      confirmation_message: (meta && meta.confirmation_message) || "",
      scraped_data: (meta && meta.scraped_data) || null,
      raw_payload: {
        source: "Sera_DOM_Visual_Tracker",
        detection_type: captureType,
        client_name: clientName,
        name: clientName,
        taxpayer_name: clientName,
        portal: portalName,
        arn: arn,
        pan: pan,
        gstin: gstin,
        period: period,
        filing_type: filingType,
        status: status,
        url: window.location.href,
        page_key: currentPageKey,
        is_page_update: true,
        timestamp: new Date().toISOString(),
        site_link_history: (meta && meta.site_link_history) || "",
        dom_breadcrumbs: (meta && meta.dom_breadcrumbs) || "",
        confirmation_message: (meta && meta.confirmation_message) || "",
        scraped_data: (meta && meta.scraped_data) || null
      },
      session_id: (safeCtx && safeCtx.session_id) || ""
    };

    // 1. Send result to background script
    try {
      if (chrome.runtime && chrome.runtime.sendMessage) {
        chrome.runtime.sendMessage(payload);
      }
    } catch (e) {
      console.warn("Sera DOM: Failed to send filing_result message:", e);
    }

    // 2. Show sleek left-side in-browser FST toast notification
    try {
      if (window.__SERA_TOAST_NOTIFIER__ && typeof window.__SERA_TOAST_NOTIFIER__.notify === 'function') {
        window.__SERA_TOAST_NOTIFIER__.notify({
          portal: portalName,
          client_name: clientName,
          name: clientName,
          arn: arn,
          filing_type: filingType,
          period_label: period,
          pan: pan,
          capture_method: "DOM_Tracker"
        });
      }
    } catch (toastErr) {
      console.warn("Sera DOM: Toast notification error:", toastErr);
    }
  }
})();
