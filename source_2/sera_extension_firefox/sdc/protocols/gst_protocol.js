/**
 * gst_protocol.js — SDC GST Portal Protocol
 * ==========================================
 * Registers SDC Crosshairs for the GST Common Portal (services.gst.gov.in / return.gst.gov.in / gst.gov.in).
 * 
 * Crosshair Map:
 * ┌────────────────────────────────────────────────────────────────────────────────────────┐
 * │ ID                      │ Route Pattern / Scope                                        │
 * ├────────────────────────────────────────────────────────────────────────────────────────┤
 * │ gst_welcome_calendar    │ …/services/auth/fowelcome OR …/dashboard                     │
 * │                         │ (Captures GSTIN, PAN, Legal Name, Filing Preference &       │
 * │                         │  parses Returns Calendar to log all historical filing states)│
 * │ gst_form_details        │ …/returns/auth/gstr1, …/gstr3b, …/cmp08, …/iff               │
 * │                         │ (Captures GSTIN, Legal Name, Trade Name, FY, Tax Period,     │
 * │                         │  Status, and Due Date)                                       │
 * │ gst_returns_dashboard   │ …/services/auth/returns OR …/quicklinks/returns              │
 * │ gst_filing_success      │ …/gstr1/success, …/gstr3b/success, …/filing/success          │
 * │ gst_login_logout        │ …/login, …/logout, …/session-expired                         │
 * └────────────────────────────────────────────────────────────────────────────────────────┘
 */

(function () {
  'use strict';

  // ─── Local GST Session State Cache ──────────────────────────────────────────
  let _gstSession = {
    gstin: '',
    pan: '',
    legal_name: '',
    trade_name: '',
    client_name: '',
    client_temp_name: '',
    fy: '',
    tax_period: '',
    due_date: '',
    filing_type: '',
    preference: '',
    calendar: []
  };

  function _resetGstSession() {
    _gstSession = {
      gstin: '',
      pan: '',
      legal_name: '',
      trade_name: '',
      client_name: '',
      client_temp_name: '',
      fy: '',
      tax_period: '',
      due_date: '',
      filing_type: '',
      preference: '',
      calendar: []
    };
  }

  // ─── Authoritative GST ARN Validation Helper ────────────────────────────────
  const GST_ARN_REGEX = /\b((?:AA|AD|AN|AP|AR|AS|BR|CG|CH|DD|DL|DN|GA|GJ|HP|HR|JH|JK|KA|KL|LA|LD|MH|ML|MN|MP|MZ|NL|OD|PB|PY|RJ|SK|TG|TN|TR|TS|UK|UP|UT|WB)\d{12}[A-Z0-9])\b/i;

  function _cleanArn(candidate, gstin, pan) {
    if (!candidate) return 'N/A';
    const c = String(candidate).trim().toUpperCase();
    if (gstin && c === gstin.toUpperCase()) return 'N/A';
    if (pan && c === pan.toUpperCase()) return 'N/A';
    // Reject GSTIN format (2 digits + 5 letters + 4 digits...)
    if (/^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/i.test(c)) return 'N/A';
    // Valid state code ARN format
    if (GST_ARN_REGEX.test(c)) return c;
    // Fallback: 15 alphanumeric characters that does NOT start with digits (ARN starts with state code)
    if (/^[A-Z]{2}[0-9]{12}[A-Z0-9]$/i.test(c)) return c;
    return 'N/A';
  }

  // ─── Infer Filing Type from Context ─────────────────────────────────────────
  function _inferFilingType(meta) {
    // 1. If explicit formType was detected in the DOM heading or URL of the page:
    if (meta && meta.form_type && !/^(?:IFF|GSTR\s*Filing|GSTR\s*Return\s*Filing|)$/i.test(meta.form_type)) {
      return meta.form_type;
    }
    // 2. Active session state (saved during form details navigation /gstr1 or /gstr3b)
    if (_gstSession.filing_type && !/^(?:IFF|GSTR\s*Filing|GSTR\s*Return\s*Filing)$/i.test(_gstSession.filing_type)) {
      return _gstSession.filing_type;
    }
    // 3. Body text search for explicit form mentions
    if (document.body) {
      const txt = document.body.innerText || '';
      const m = txt.match(/\b(GSTR[-_ ]*3B|GSTR[-_ ]*1(?:\s*\/\s*IFF)?|CMP[-_ ]*08|GSTR[-_ ]*4|GSTR[-_ ]*9)\b/i);
      if (m) {
        return m[1].replace(/^GSTR[-_ ]*1(?:\s*\/\s*IFF)?$/i, 'GSTR-1/IFF')
                   .replace(/^GSTR[-_ ]*3B$/i, 'GSTR-3B')
                   .replace(/^CMP[-_ ]*08$/i, 'CMP-08');
      }
    }
    // 4. Session timeline backtracking
    const SDC = window.__SERA_SDC__;
    if (SDC && SDC.session && SDC.session.data && Array.isArray(SDC.session.data.timeline)) {
      const tl = SDC.session.data.timeline;
      for (let i = tl.length - 1; i >= 0; i--) {
        const u = (tl[i].url || '').toLowerCase();
        if (u.includes('gstr3b')) return 'GSTR-3B';
        if (u.includes('gstr1') || u.includes('iff')) return 'GSTR-1/IFF';
        if (u.includes('cmp08')) return 'CMP-08';
        if (tl[i].captured_data && tl[i].captured_data.form && !/returns calendar/i.test(tl[i].captured_data.form)) {
          return tl[i].captured_data.form;
        }
      }
    }
    return (meta && meta.form_type) || 'GSTR-1/IFF';
  }

  function _cleanNameString(raw) {
    if (!raw) return '';
    let s = String(raw).trim();
    // Strip common UI clutter and Material/Bootstrap icons
    s = s.replace(/(?:expand_more|keyboard_arrow_down|account_circle|person|user|profile|logout|settings)/gi, ' ');
    s = s.replace(/\s+/g, ' ').trim();
    return s;
  }

  // ─── Extract GSTIN and PAN from Text / Elements ─────────────────────────────
  function _extractGstinAndPan() {
    let gstin = '';
    let pan = '';

    const gstinRegex = /\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})\b/i;
    const panRegex = /\b([A-Z]{5}[0-9]{4}[A-Z]{1})\b/i;

    // 1. Check metadata headers: "GSTIN - 19EJQPS3779M1ZU" or "GSTIN: 19EJQPS3779M1ZU"
    if (document.body) {
      const gstinMatch = document.body.innerText.match(/GSTIN(?:\/UIN)?\s*[-:]\s*([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})/i);
      if (gstinMatch) {
        gstin = gstinMatch[1].toUpperCase();
        pan = gstin.substring(2, 12);
        return { gstin, pan };
      }
    }

    // 2. Check profile info card on the right
    const rightCards = document.querySelectorAll('.card, .profile-card, .dashboard-profile, div[class*="profile"], div[class*="user"]');
    for (const card of rightCards) {
      const match = card.innerText.match(gstinRegex);
      if (match) {
        gstin = match[1].toUpperCase();
        pan = gstin.substring(2, 12);
        break;
      }
    }

    // 3. Full document body text search
    if (!gstin && document.body) {
      const match = document.body.innerText.match(gstinRegex);
      if (match) {
        gstin = match[1].toUpperCase();
        pan = gstin.substring(2, 12);
      }
    }

    // 4. Fallback PAN search
    if (!pan && document.body) {
      const pMatch = document.body.innerText.match(panRegex);
      if (pMatch) {
        pan = pMatch[1].toUpperCase();
      }
    }

    return { gstin, pan };
  }

  // ─── Extract Taxpayer Names (Legal & Trade) ─────────────────────────────────
  function _extractTaxpayerNames() {
    let legalName = '';
    let tradeName = '';
    let tempName = '';

    if (document.body) {
      const bodyText = document.body.innerText;

      // 1. Structured table: "Legal Name - AMEJUDDIN SK" or "Legal Name of Business - ..."
      const legalMatch = bodyText.match(/Legal\s+Name(?:\s+of\s+Business)?\s*[-:]\s*([A-Z0-9\s\.\-_&]+?)(?=\s*(?:Trade\s+Name|GSTIN|FY|Financial\s+Year|Tax\s+Period|Status|Due\s+Date|\n|$))/i);
      if (legalMatch && legalMatch[1]) {
        legalName = _cleanNameString(legalMatch[1]);
      }

      // 2. Structured table: "Trade Name - A. SHABNAM DRESSES" or "Trade Name of Business - ..."
      const tradeMatch = bodyText.match(/Trade\s+Name(?:\s+of\s+Business)?\s*[-:]\s*([A-Z0-9\s\.\-_&]+?)(?=\s*(?:Legal\s+Name|GSTIN|FY|Financial\s+Year|Tax\s+Period|Status|Due\s+Date|Indicates|\n|$))/i);
      if (tradeMatch && tradeMatch[1]) {
        tradeName = _cleanNameString(tradeMatch[1]);
      }

      // 3. Welcome banner: "Welcome <NAME> to GST Common Portal"
      if (!legalName) {
        const welcomeMatch = bodyText.match(/Welcome\s+([A-Z0-9\s\.\-_&]+?)\s+to\s+GST\s+Common\s+Portal/i);
        if (welcomeMatch && welcomeMatch[1]) {
          legalName = _cleanNameString(welcomeMatch[1]);
        }
      }
    }

    // 4. Header user badge (e.g. top right menu)
    const headerBadges = document.querySelectorAll('header, nav, .navbar, .user-name, #user-dropdown, a[class*="user"], span[class*="user"]');
    for (const badge of headerBadges) {
      const text = badge.innerText || '';
      const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
      for (const line of lines) {
        if (/^[A-Z\s\.\-_&]{3,45}$/i.test(line) && !/^(dashboard|services|gst law|downloads|search|help|e-invoice|news|skip|status|due\s+date|fy|financial\s+year|tax\s+period|return)/i.test(line)) {
          tempName = _cleanNameString(line);
          break;
        }
      }
      if (tempName) break;
    }

    const primaryName = legalName || tradeName || tempName || '';

    return {
      legal_name: legalName,
      trade_name: tradeName,
      client_name: primaryName,
      client_temp_name: tempName || primaryName
    };
  }

  // ─── Extract Form Details (FY, Tax Period, Status, Due Date) ────────────────
  function _extractFormMetadata() {
    if (!document.body) return {};

    const bodyText = document.body.innerText;

    // FY: "FY - 2026-27" or "Financial Year - 2026-27"
    let fy = '';
    const fyMatch = bodyText.match(/(?:FY|Financial\s+Year)\s*[-:]\s*([0-9]{4}\s*-\s*[0-9]{2,4})/i);
    if (fyMatch) fy = fyMatch[1].replace(/\s+/g, '');

    // GST uses both "Tax Period" and "Return Period" labels.
    let taxPeriod = '';
    const periodMatch = bodyText.match(/(?:Tax|Return|Filing)\s+Period\s*[-:]\s*([A-Za-z0-9()\s\-_\/]+?)(?=\s*(?:Status|Due\s+Date|FY|Financial\s+Year|Trade|Legal|GSTIN|\n|$))/i);
    if (periodMatch) taxPeriod = periodMatch[1].trim();
    if (!taxPeriod) {
      const queryPeriod = new URLSearchParams(window.location.search).get('rtn_prd');
      if (queryPeriod && /^\d{6}$/.test(queryPeriod)) {
        const month = Number(queryPeriod.slice(0, 2));
        const year = queryPeriod.slice(2);
        if (month >= 1 && month <= 12) {
          taxPeriod = `${['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][month - 1]} ${year}`;
        }
      }
    }

    // Status: "Status - Filed" or "Status - Not Filed"
    let status = '';
    const statusMatch = bodyText.match(/Status\s*[-:]\s*([A-Za-z0-9\s\-_]+?)(?=\s*(?:Due\s+Date|FY|Tax\s+Period|Return\s+Period|Filing\s+Period|Trade|Legal|\n|$))/i);
    if (statusMatch) status = statusMatch[1].trim();

    // Due Date: "Due Date - 13/07/2026" or "Due Date : 13/07/2026"
    let dueDate = '';
    const dueMatch = bodyText.match(/Due\s+Date\s*[-:]\s*([0-9]{2}[\/\-][0-9]{2}[\/\-][0-9]{4})/i);
    if (dueMatch) dueDate = dueMatch[1].trim();

    // Form Title / Type: from Banner / Breadcrumb (e.g. "GSTR-1/IFF", "GSTR-3B")
    let formType = '';
    const formTitleElem = document.querySelector('h1, h2, h3, .page-title, .panel-title, .breadcrumb');
    if (formTitleElem && /(GSTR-[1-9A-Z]+|CMP-[0-9]+|IFF)/i.test(formTitleElem.innerText)) {
      const m = formTitleElem.innerText.match(/(GSTR-[1-9A-Z]+(?:\s*\/\s*IFF)?|CMP-[0-9]+|IFF)/i);
      if (m) formType = m[1].replace(/\s+/g, ' ').toUpperCase();
    }
    if (!formType) {
      const urlM = window.location.href.match(/(gstr1|gstr3b|cmp08|gstr4|gstr9|iff)/i);
      if (urlM) formType = urlM[1].toUpperCase();
    }
    formType = formType.replace(/^GSTR[-_ ]*1(?:\s*\/\s*IFF)?$/i, 'GSTR-1/IFF')
      .replace(/^GSTR[-_ ]*3B$/i, 'GSTR-3B')
      .replace(/^CMP[-_ ]*08$/i, 'CMP-08')
      .replace(/^GSTR[-_ ]*4$/i, 'GSTR-4')
      .replace(/^GSTR[-_ ]*9$/i, 'GSTR-9');

    // Nil filing checkbox
    const nilCheckbox = document.querySelector('input[type="checkbox"][id*="nil" i], input[type="checkbox"][name*="nil" i]');
    const isNil = Boolean(nilCheckbox && nilCheckbox.checked);

    return {
      fy,
      tax_period: taxPeriod,
      status: status || 'Initiated',
      due_date: dueDate,
      form_type: formType || '',
      is_nil: isNil
    };
  }

  // ─── Extract Return Filing Preference ───────────────────────────────────────
  function _extractFilingPreference() {
    if (!document.body) return '';
    const match = document.body.innerText.match(/Return\s+filing\s+preference\s*(?:\([^)]*\))?\s*:\s*([A-Za-z]+)/i);
    return match ? match[1].trim() : '';
  }

  // ─── Utility: Wait for SPA DOM Rendering ─────────────────────────────────────
  async function _waitForReady(testFn, timeoutMs = 15000, intervalMs = 500) {
    const startTime = Date.now();
    return new Promise((resolve) => {
      const check = () => {
        if (testFn()) {
          resolve(true);
          return;
        }
        if (Date.now() - startTime >= timeoutMs) {
          resolve(false); // Timeout, proceed anyway
          return;
        }
        setTimeout(check, intervalMs);
      };
      check();
    });
  }

  // ─── Parse Returns Calendar (Last 5 return periods) ─────────────────────────
  function _parseReturnsCalendar() {
    const calendarEntries = [];

    // Find table containing "Returns Calendar" or table structure
    const tables = document.querySelectorAll('table, .table, div[class*="calendar"], div[class*="return-status"]');
    let targetTable = null;

    for (const tbl of tables) {
      const txt = tbl.innerText || '';
      if (txt.includes('GSTR-1') || txt.includes('GSTR-3B') || txt.includes('Returns Calendar')) {
        targetTable = tbl;
        break;
      }
    }

    if (targetTable) {
      const rows = targetTable.querySelectorAll('tr, div[class*="row"]');
      let periodHeaders = [];

      // Look for the header row containing periods (e.g. "Mar - 2026", "Apr - 2026")
      for (const row of rows) {
        const cells = row.querySelectorAll('th, td, div[class*="col"], div[class*="cell"]');
        const cellTexts = Array.from(cells).map(c => (c.innerText || '').trim());
        const dateMatchCount = cellTexts.filter(t => /[A-Za-z]{3}\s*[-]?\s*\d{2,4}/.test(t)).length;
        if (dateMatchCount >= 2) {
          periodHeaders = cellTexts.filter(t => /[A-Za-z]{3}\s*[-]?\s*\d{2,4}/.test(t));
          break;
        }
      }

      // If headers found, extract data rows
      if (periodHeaders.length > 0) {
        for (const row of rows) {
          const cells = row.querySelectorAll('th, td, div[class*="col"], div[class*="cell"]');
          if (cells.length < 2) continue;

          const rowLabel = (cells[0].innerText || '').trim();
          if (/GSTR-1|GSTR-3B|CMP-08|GSTR-4|GSTR-9/i.test(rowLabel)) {
            const formType = rowLabel.replace(/\s+/g, ' ');
            for (let i = 1; i < cells.length && (i - 1) < periodHeaders.length; i++) {
              const rawHeader = periodHeaders[i - 1] || '';
              const cleanPeriodMatch = rawHeader.match(/([A-Za-z]{3,9}\s*[-]?\s*\d{2,4})/);
              const period = cleanPeriodMatch ? cleanPeriodMatch[1].trim() : rawHeader.split('\n')[0].trim();
              
              let statusClean = statusCell.replace(/\s+/g, ' ');
              if (/not filed/i.test(statusClean)) statusClean = 'Not Filed';
              else if (/filed/i.test(statusClean)) statusClean = 'Filed';
              else if (/pending/i.test(statusClean)) statusClean = 'Pending';
              else if (/expired/i.test(statusClean)) statusClean = 'NA Option expired';
              else if (/^na$/i.test(statusClean)) statusClean = 'NA';

              calendarEntries.push({
                form: formType,
                period: period,
                status: statusClean,
                raw_status: statusCell
              });
            }
          }
        }
      }
    }

    // Fallback: Text-based regex parsing if Angular table markup is deeply nested
    if (calendarEntries.length === 0 && document.body) {
      const bodyText = document.body.innerText;
      const gstr1Section = bodyText.match(/GSTR-1(?:\s*\/\s*IFF)?[\s\S]*?(?=GSTR-3B|CMP-08|$)/i);
      const gstr3bSection = bodyText.match(/GSTR-3B[\s\S]*?(?=You can navigate|CMP-08|$)/i);

      function parseSection(secText, formName) {
        if (!secText) return;
        // Match 3-letter month + year, then the next words indicating status
        const periodRegex = /([A-Za-z]{3}\s*[-]?\s*\d{2,4})[\s\n\r]+((?:Not\s+Filed|To\s+be\s+Filed|Filed|Pending|NA(?:\s+Option\s+expired)?))/gi;
        let match;
        while ((match = periodRegex.exec(secText)) !== null) {
          const period = match[1].trim();
          let st = match[2].trim().replace(/\s+/g, ' ');

          if (/(?:not filed|to be filed)/i.test(st)) st = 'Not Filed';
          else if (/filed/i.test(st)) st = 'Filed';
          else if (/pending/i.test(st)) st = 'Pending';
          else if (/expired/i.test(st)) st = 'NA Option expired';
          else if (/^na$/i.test(st)) st = 'NA';

          calendarEntries.push({
            form: formName,
            period: period,
            status: st,
            raw_status: match[2].trim()
          });
        }
      }

      if (gstr1Section) parseSection(gstr1Section[0], 'GSTR-1 / IFF');
      if (gstr3bSection) parseSection(gstr3bSection[0], 'GSTR-3B');
    }

    // Nested Angular row/cell containers can expose each calendar row twice.
    // Keep one record per form + period + status before handing data to SDC.
    const seen = new Set();
    return calendarEntries.filter(entry => {
      const key = [entry.form, entry.period, entry.status]
        .map(value => String(value || '').replace(/\s+/g, ' ').trim().toUpperCase())
        .join('|');
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  // ─── Handler 1: Welcome Dashboard & Calendar ────────────────────────────────
  async function _handleWelcomeCalendar(url) {
    // Wait for Angular SPA to render the GSTIN and Returns Calendar
    await _waitForReady(() => {
      const txt = document.body ? document.body.innerText : '';
      const hasGstin = /\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})\b/i.test(txt);
      const hasCalendar = /(Returns Calendar|GSTR-1|GSTR-3B)/i.test(txt);
      return hasGstin && hasCalendar;
    }, 12000, 500);

    const { gstin, pan } = _extractGstinAndPan();
    const { legal_name } = _extractTaxpayerNames();
    const preference = _extractFilingPreference();
    const parsedCalendar = _parseReturnsCalendar();

    // Capture ONLY the immediately previous period for each form.
    // The portal calendar is ordered chronologically, so formPeriods[formPeriods.length - 2]
    // represents the predecessor/previous completed period (or formPeriods[0] if only 1 period exists).
    const calendar = [];
    const forms = [...new Set(parsedCalendar.map(entry => entry.form))];
    for (const form of forms) {
      const formPeriods = parsedCalendar.filter(entry => entry.form === form);
      if (formPeriods.length > 1) {
        calendar.push(formPeriods[formPeriods.length - 2]); // Immediately previous period
      } else if (formPeriods.length === 1) {
        calendar.push(formPeriods[0]);
      }
    }

    if (gstin) _gstSession.gstin = gstin;
    if (pan) _gstSession.pan = pan;
    if (legal_name) _gstSession.legal_name = legal_name;
    if (preference) _gstSession.preference = preference;
    _gstSession.calendar = calendar;

    const SDC = window.__SERA_SDC__;

    // Emit previous period calendar filings to SDC Core & Database silently (no multi-toast burst)
    if (calendar.length > 0 && SDC && typeof SDC.emitCapture === 'function') {
      for (const item of calendar) {
        const itemCapture = {
          gstin: gstin || _gstSession.gstin,
          pan: pan || _gstSession.pan,
          legal_name: legal_name || _gstSession.legal_name,
          client_name: legal_name || _gstSession.legal_name,
          name: legal_name || _gstSession.legal_name,
          taxpayer_name: legal_name || _gstSession.legal_name,
          portal: 'GST Portal',
          filing_type: item.form,
          filing_preference: preference,
          period_label: item.period,
          status: item.status,
          arn: 'N/A',
          silent: true,
          skip_toast: true,
          confirmation_message: 'GST Returns Calendar (Previous Period): ' + item.form + ' for ' + item.period + ' is ' + item.status,
          scraped_data: {
            returns_calendar_item: item,
            legal_name: legal_name || _gstSession.legal_name,
            filing_preference: preference,
            taxpayer_name: legal_name || _gstSession.legal_name,
            gstin: gstin || _gstSession.gstin,
            pan: pan || _gstSession.pan,
            scanned_at: new Date().toISOString()
          }
        };

        SDC.emitCapture(itemCapture, 'GST Portal', 'gst_calendar_entry');
      }
    }

    const filedCount = calendar.filter(c => c.status === 'Filed').length;
    const periodSummary = calendar.length > 0 ? (calendar[0].period + (calendar.length > 1 ? (' - ' + calendar[calendar.length - 1].period) : '')) : 'Previous Period';

    // Record individual return filings into session timeline so audit & LTT track each form/period
    if (SDC && SDC.session && typeof SDC.session.recordStep === 'function') {
      for (const item of calendar) {
        await SDC.session.recordStep(url, 'gst_calendar_entry', {
          gstin: gstin || _gstSession.gstin,
          pan: pan || _gstSession.pan,
          legal_name: legal_name || _gstSession.legal_name,
          client_name: legal_name || _gstSession.legal_name,
          filing_preference: preference,
          form: item.form,
          ay: item.period,
          status: item.status,
          arn: 'N/A'
        });
      }
      await SDC.session.save();
    }

    // Single unified toast summarizing the previous scanned period(s)
    if (window.SDCToast && calendar.length > 0) {
      window.SDCToast.show({
        type: 'capture',
        badge: 'SCANNED',
        title: 'GST Returns Calendar',
        message: `${calendar.length} previous period(s) scanned (${filedCount} filed)`,
        chips: [
          { label: 'Client', value: legal_name || 'Taxpayer' },
          { label: 'GSTIN', value: gstin || _gstSession.gstin, isPan: true },
          { label: 'Period', value: periodSummary }
        ],
        duration: 1600
      });
    }

    // Return null so SDC Assembler does NOT emit a dummy "GST Returns Calendar" filing
    return null;
  }

  // ─── Handler 2: Form Preparation & Summary Details (New Crosshair) ──────────
  async function _handleFormDetails(url) {
    // Wait for Form details to populate
    await _waitForReady(() => {
      const txt = document.body ? document.body.innerText : '';
      const hasGstin = /\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})\b/i.test(txt);
      const hasMeta = /(Status|Due Date|Tax Period|FY)/i.test(txt);
      return hasGstin && hasMeta;
    }, 10000, 500);

    const { gstin, pan } = _extractGstinAndPan();
    const { legal_name, trade_name, client_name, client_temp_name } = _extractTaxpayerNames();
    const meta = _extractFormMetadata();

    if (gstin) _gstSession.gstin = gstin;
    if (pan) _gstSession.pan = pan;
    if (legal_name) _gstSession.legal_name = legal_name;
    if (trade_name) _gstSession.trade_name = trade_name;
    if (client_name) _gstSession.client_name = client_name;
    if (client_temp_name) _gstSession.client_temp_name = client_temp_name;
    if (meta.fy) _gstSession.fy = meta.fy;
    if (meta.tax_period) _gstSession.tax_period = meta.tax_period;
    if (meta.due_date) _gstSession.due_date = meta.due_date;
    if (meta.form_type) _gstSession.filing_type = meta.form_type;

    const fullPeriodLabel = meta.tax_period ? (meta.tax_period + (meta.fy ? ' (FY ' + meta.fy + ')' : '')) : (meta.fy || 'Current Period');
    const finalStatus = meta.status || 'Initiated';

    return {
      gstin: gstin || _gstSession.gstin,
      pan: pan || _gstSession.pan,
      client_name: client_name || _gstSession.client_name,
      client_temp_name: client_temp_name || _gstSession.client_temp_name,
      company_name: trade_name || _gstSession.trade_name,
      proprietor_name: legal_name || _gstSession.legal_name,
      filing_type: meta.form_type || _inferFilingType(meta),
      period_label: fullPeriodLabel,
      status: finalStatus,
      due_date: meta.due_date || '',
      arn: 'N/A',
      scraped_data: {
        gstin: gstin || _gstSession.gstin,
        pan: pan || _gstSession.pan,
        legal_name: legal_name || _gstSession.legal_name,
        trade_name: trade_name || _gstSession.trade_name,
        fy: meta.fy || _gstSession.fy,
        tax_period: meta.tax_period || _gstSession.tax_period,
        status: finalStatus,
        due_date: meta.due_date || _gstSession.due_date,
        form_type: meta.form_type,
        is_nil: meta.is_nil,
        captured_at: new Date().toISOString()
      }
    };
  }

  // ─── Handler 3: Returns Filing Dashboard Selection ──────────────────────────
  async function _handleReturnsDashboard(url) {
    await _waitForReady(() => {
      const txt = document.body ? document.body.innerText : '';
      return /\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})\b/i.test(txt);
    }, 8000, 500);

    const { gstin, pan } = _extractGstinAndPan();
    const { legal_name, trade_name, client_name, client_temp_name } = _extractTaxpayerNames();

    return {
      gstin: gstin || _gstSession.gstin,
      pan: pan || _gstSession.pan,
      client_name: client_name || _gstSession.client_name,
      client_temp_name: client_temp_name || _gstSession.client_temp_name,
      company_name: trade_name || _gstSession.trade_name,
      proprietor_name: legal_name || _gstSession.legal_name,
      filing_type: 'GST Return Filing',
      period_label: 'Filing Dashboard',
      status: 'Return Selection',
      arn: 'N/A'
    };
  }

  // ─── Handler 4: Filing Success / Confirmation ───────────────────────────────
  async function _handleIffSubmissionSuccess(url) {
    const successPattern = /(?:successfully\s+(?:submitted|filed)|return\s+(?:submitted|filed)\s+successfully|filing\s+successful|submitted\s+successfully)/i;

    await _waitForReady(() => {
      const text = document.body ? document.body.innerText : '';
      return successPattern.test(text) && GST_ARN_REGEX.test(text);
    }, 12000, 400);

    const text = document.body ? document.body.innerText : '';
    const successMatch = text.match(successPattern);
    const { gstin, pan } = _extractGstinAndPan();
    const currentGstin = gstin || _gstSession.gstin;
    const currentPan = pan || _gstSession.pan;

    // Search explicitly for labeled ARN first, then fall back to state-coded GST ARN regex
    let rawArn = '';
    const labeledMatch = text.match(/(?:ARN\s*(?:Number|No\.?)?\s*[:\-]?\s*)([A-Za-z0-9]{15})/i);
    if (labeledMatch) {
      rawArn = labeledMatch[1];
    } else {
      const arnRegexMatch = text.match(GST_ARN_REGEX);
      if (arnRegexMatch) rawArn = arnRegexMatch[1];
    }

    const cleanArn = _cleanArn(rawArn, currentGstin, currentPan);
    if (!successMatch || cleanArn === 'N/A') return null;

    const { legal_name, trade_name, client_name, client_temp_name } = _extractTaxpayerNames();
    const meta = _extractFormMetadata();
    const filingType = _inferFilingType(meta);

    return {
      gstin: currentGstin,
      pan: currentPan,
      client_name: client_name || _gstSession.client_name,
      client_temp_name: client_temp_name || _gstSession.client_temp_name,
      company_name: trade_name || _gstSession.trade_name,
      proprietor_name: legal_name || _gstSession.legal_name,
      filing_type: filingType,
      period_label: meta.tax_period || _gstSession.tax_period || 'Current Period',
      status: 'Filed & Confirmed',
      due_date: meta.due_date || '',
      arn: cleanArn,
      scraped_data: {
        success_message: successMatch[0].trim(),
        arn: cleanArn,
        url: url,
        captured_at: new Date().toISOString()
      }
    };
  }

  async function _handleFilingSuccess(url) {
    await _waitForReady(() => {
      const txt = document.body ? document.body.innerText : '';
      const hasArn = GST_ARN_REGEX.test(txt) || /ARN\s*[:\-]/i.test(txt);
      return hasArn;
    }, 10000, 500);

    const { gstin, pan } = _extractGstinAndPan();
    const currentGstin = gstin || _gstSession.gstin;
    const currentPan = pan || _gstSession.pan;
    const { legal_name, trade_name, client_name, client_temp_name } = _extractTaxpayerNames();
    const meta = _extractFormMetadata();

    let rawArn = '';
    const arnMatch = document.body ? document.body.innerText.match(/ARN\s*(?:Number|No\.?)?\s*[:\-]?\s*([A-Za-z0-9]{15})/i) : null;
    if (arnMatch) {
      rawArn = arnMatch[1];
    } else if (document.body) {
      const m = document.body.innerText.match(GST_ARN_REGEX);
      if (m) rawArn = m[1];
    }
    const cleanArn = _cleanArn(rawArn, currentGstin, currentPan);
    const filingType = _inferFilingType(meta);

    return {
      gstin: currentGstin,
      pan: currentPan,
      client_name: client_name || _gstSession.client_name,
      client_temp_name: client_temp_name || _gstSession.client_temp_name,
      company_name: trade_name || _gstSession.trade_name,
      proprietor_name: legal_name || _gstSession.legal_name,
      filing_type: filingType,
      period_label: meta.tax_period || 'Current Period',
      status: 'Filed & Confirmed',
      due_date: meta.due_date || '',
      arn: cleanArn
    };
  }

  // ─── Handler 5: Login & Logout ──────────────────────────────────────────────
  async function _handleLoginLogout(url) {
    const lower = (url || '').toLowerCase();
    const isLogout = lower.includes('logout') || lower.includes('signout') || lower.includes('sign-out') || lower.includes('session') || lower.includes('timeout');

    const SDC = window.__SERA_SDC__;
    if (isLogout && SDC) {
      await SDC.session.finalizeLogout(url);
      _resetGstSession();
      await SDC.clearAllSessions();
      return null;
    }

    const { gstin, pan } = _extractGstinAndPan();
    return {
      gstin: gstin || '',
      pan: pan || '',
      client_name: '',
      client_temp_name: '',
      filing_type: '',
      period_label: '',
      status: 'GST Pre-Login',
      arn: 'N/A'
    };
  }

  // ─── Register Protocol with SDC Core ────────────────────────────────────────
  function _register() {
    const SDC = window.__SERA_SDC__;
    if (!SDC) {
      setTimeout(_register, 100);
      return;
    }

    SDC.onSessionClear(_resetGstSession);

    const registered = SDC.register({
      name: 'GST Portal',
      hostMatch: /(?:services\.gst\.gov\.in|return\.gst\.gov\.in|gst\.gov\.in|localhost|127\.0\.0\.1)/i,
      crosshairs: [
        {
          id: 'gst_filing_success',
          pattern: /(?:services\/auth\/gstr.*success|gstr.*submit.*success|returns.*success|filing.*success|ack.*success)/i,
          handler: _handleFilingSuccess
        },
        {
          id: 'gst_form_details',
          // Matches GSTR-1, GSTR-3B, CMP-08, IFF, GSTR-4, GSTR-9 form tables on return.gst.gov.in and services.gst.gov.in
          pattern: /(?:returns\/auth\/(?:gstr1|gstr3b|cmp08|gstr4|gstr9|iff)|services\/auth\/(?:gstr1|gstr3b|cmp08|gstr4|gstr9|iff)|returns\/(?:gstr1|gstr3b|cmp08|gstr4|gstr9|iff))/i,
          handler: _handleFormDetails
        },
        {
          id: 'gst_filing_file_success',
          // The GST confirmation route for IFF, GSTR-1, GSTR-3B
          pattern: /returns\/auth\/file(?:[?#]|$)/i,
          handler: _handleIffSubmissionSuccess
        },
        {
          id: 'gst_welcome_calendar',
          pattern: /(?:services\/auth\/fowelcome|services\/auth\/dashboard|fowelcome|auth\/dashboard$)/i,
          handler: _handleWelcomeCalendar
        },
        {
          id: 'gst_returns_dashboard',
          pattern: /(?:services\/auth\/returns|services\/quicklinks\/returns|returns\/dashboard)/i,
          handler: _handleReturnsDashboard
        },
        {
          id: 'gst_login_logout',
          pattern: /[/#](?:login|logout|sign-?in|sign-?out|session.?expire|session-?timeout)(?:[?/#]|$)/i,
          handler: _handleLoginLogout
        }
      ]
    });

    console.log('⚡ Sera SDC: GST Portal Protocol registered with Form Summary & Returns Calendar crosshairs.');
    // sdc_core performs its first URL scan before protocol files finish
    // loading. Re-scan once GST is registered so an already-open GST page
    // (including a direct /returns/auth/gstr1 visit) is captured immediately.
    if (registered && typeof SDC.scanNow === 'function') {
      SDC.scanNow(true).catch(err => console.warn('⚡ Sera SDC: GST initial scan failed.', err));
    }
    // GST keeps the IFF form on the same /file route after submission and
    // renders the success banner asynchronously. Watch that route so the
    // success crosshair can run when the ARN appears in the DOM.
    if (registered && typeof MutationObserver !== 'undefined') {
      let scanTimer = null;
      const observer = new MutationObserver(() => {
        if (!/returns\/auth\/file(?:[?#]|$)/i.test(window.location.href) || scanTimer) return;
          scanTimer = setTimeout(async () => {
            scanTimer = null;
          const capture = await _handleIffSubmissionSuccess(window.location.href);
          if (capture) {
            const filingType = capture.filing_type || '';
            const crosshairId = /3B/i.test(filingType)
              ? 'gst_gstr3b_submission_success'
              : /GSTR[- ]?1/i.test(filingType)
                ? 'gst_gstr1_submission_success'
                : 'gst_iff_submission_success';
            // /returns/auth/file has no form-details route entry. Record the
            // success page first so the assembler has a non-empty timeline
            // and can flush the ARN payload when the user returns to Login.
            SDC.session.data.portal = 'GST Portal';
            await SDC.session.recordStep(window.location.href, crosshairId, capture);
            await SDC.session.save();
            SDC.emitCapture(capture, 'GST Portal', crosshairId);
          }
        }, 800);
      });
      if (document.body) observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    }
    if (typeof SDC.retryPendingFlush === 'function') SDC.retryPendingFlush();
  }

  _register();

})();
