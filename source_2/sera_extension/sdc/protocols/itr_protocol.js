/**
 * itr_protocol.js — SDC Income Tax Return (ITR) Portal Protocol
 * ==============================================================
 * Registers crosshairs for the Income Tax e-Filing portal
 * (eportal.incometax.gov.in / incometaxindiaefiling.gov.in).
 *
 * Crosshair Map (from SDC blueprint + site history analysis):
 * ┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
 * │  ID                    │ Trigger Route Pattern                                                   │
 * ├──────────────────────────────────────────────────────────────────────────────────────────────────┤
 * │  itr_landing           │ …/fileIncomeTaxReturn OR …/dashboard  (Landing & Header Name extractor) │
 * │  itr_personal_info     │ …/personal_information OR …/myProfile/profileDetail (Full Name, PAN, DOB)│
 * │  itr_form_select       │ …/fo-select-itr-form                 (Form type + AY)                   │
 * │  itr_filed_verified    │ …/fo-e-verify-now-success OR fo-return-success                          │
 * │  itr_submitted_pending │ …/fo-e-verify-later                  (Pending e-verification)           │
 * │  itr_view_filed_returns│ …/view-filed-returns                 (15-digit ACK return history)      │
 * └──────────────────────────────────────────────────────────────────────────────────────────────────┘
 *
 * Name Extraction Strategy (Additive 3-Tier + Header Fallback):
 *   T1: DOM form inputs (FirstName, MiddleName, SurName/LastName, FullName on personal info / profile)
 *   T2: Header profile badge (#loginUsername, .user-name, button[id*=loginUsername] -> client_temp_name)
 *   T3: Full page text regex composite (First + optional Middle + Last near PAN / Individual)
 *   Fallback: client_temp_name automatically used if employee does not visit profile pages.
 *
 * DOB Extraction Strategy:
 *   DOM inputs/outputs (dateOfBirth, dob, birthDate) + labeled text patterns.
 */

(function () {
  'use strict';

  // Wait for SDC core to be ready
  function _register() {
    const SDC = window.__SERA_SDC__;
    if (!SDC) {
      console.warn('Sera SDC ITR Protocol: sdc_core not loaded. Retrying...');
      setTimeout(_register, 100);
      return;
    }

    const u = SDC.utils;

    // ─── Session Identity Cache (cross-tab via sdc_core.js) ──────────────────
    const getSession = () => SDC.session.data;

    // ─── Name Sanitization & Validation Helpers ─────────────────────────────
    const NOISE_WORDS = new Set([
      'INDIVIDUAL', 'TAXPAYER', 'HUF', 'COMPANY', 'REPRESENTATIVE',
      'DIRECTOR', 'PARTNER', 'WELCOME', 'LOGOUT', 'DASHBOARD', 'SELECT',
      'PROFILE', 'DETAILS', 'STATUS', 'RETURN', 'INCOME', 'TAX', 'FILING',
      'CALL US', 'ENGLISH', 'HELP', 'FEEDBACK', 'NOTIFICATIONS', 'HOME'
    ]);

    function _cleanNameString(str) {
      if (!str) return '';
      return str
        .replace(/(?:MAT[_\s]*ICON|EXPAND[_\s]*MORE|EXPANDMORE|EXPAND[_\s]*LESS|EXPANDLESS|KEYBOARD[_\s]*ARROW[_\s]*(?:DOWN|UP|RIGHT|LEFT)|ARROW[_\s]*(?:DOWN|UP|DROP)|MORE[_\s]*VERT|MORE[_\s]*HORIZ|ACCOUNT[_\s]*CIRCLE|PERSON(?=\s|$)|USER(?=\s|$))/gi, ' ') // strip Material Icon ligatures FIRST (no \b — they concatenate directly onto names/role-tags)
        .replace(/\b(?:Individual|Taxpayer|HUFs?|Company|Representative|Director|Partners?|Proprietor)\b/gi, '') // then strip portal role tags (word boundary now works after ligatures are gone)
        .replace(/[\u02C0-\u02FF\u25A0-\u25FF\u2300-\u23FF\uFE00-\uFE0F⌵▼▽˅^<>|•\-_:]+/g, ' ') // strip Unicode dropdown arrows & symbols
        .replace(/\.\.\.$/, '') // strip trailing ellipsis
        .replace(/[^A-Za-z\s.'-]/g, ' ') // keep only letters, spaces, dots, hyphens, apostrophes
        .replace(/\s+/g, ' ')
        .trim()
        .toUpperCase();
    }

    function _isValidName(name) {
      if (!name || name.length < 3 || name.length > 70) return false;
      if (NOISE_WORDS.has(name)) return false;
      // Must contain at least one vowel and contain only legal name chars
      return /^[A-Z\s.'-]{3,70}$/.test(name) && /[AEIOUY]/.test(name);
    }

    // ─── TIER 0: Portal Storage Extraction (Fastest & Untruncated) ─────────
    function _extractFromPortalStorage() {
      try {
        if (typeof sessionStorage !== 'undefined') {
          for (let i = 0; i < sessionStorage.length; i++) {
            const key = sessionStorage.key(i);
            if (/user|profile|login|session|auth/i.test(key)) {
              try {
                const raw = sessionStorage.getItem(key);
                if (!raw) continue;
                const obj = JSON.parse(raw);
                const candidate = obj.fullName || obj.userName || obj.name || (obj.profile && (obj.profile.fullName || obj.profile.name)) || (obj.userProfile && obj.userProfile.name);
                const clean = _cleanNameString(candidate);
                if (_isValidName(clean)) return clean;
              } catch (_) {}
            }
          }
        }
      } catch (_) {}
      return '';
    }

    // ─── TIER 1: Label-Proximity & Form Controls (Profile & Personal Info) ─
    function _extractByLabel(targetLabels) {
      try {
        const allLabels = u.$qa('label, mat-label, span.form-label, span.label, div.label, th, td, p.label, span, div');
        for (const lbl of allLabels) {
          const txt = u.getText(lbl).toLowerCase();
          if (targetLabels.some(l => txt === l.toLowerCase() || txt.startsWith(l.toLowerCase() + ':') || txt.includes(l.toLowerCase()))) {
            const container = (typeof lbl.closest === 'function' ? lbl.closest('mat-form-field, .form-group, .form-field, .row, tr, div') : null) || lbl.parentElement;
            if (!container) continue;

            const input = container.querySelector('input, output, [formcontrolname], .form-control, .value, span:not(.label):not(.form-label)');
            if (input) {
              const val = (input.value || input.innerText || input.textContent || '').trim();
              const clean = _cleanNameString(val);
              if (_isValidName(clean) && !targetLabels.some(l => clean.toLowerCase().includes(l.toLowerCase()))) {
                return clean;
              }
            }
          }
        }
      } catch (_) {}
      return '';
    }

    // ─── TIER 2: Header Badge & Role Node Extraction ───────────────────────
    function _extractHeaderName() {
      // 1. Check storage first
      const storageName = _extractFromPortalStorage();
      if (storageName) return storageName;

      // 2. Scan for elements containing the exact role tag "Individual", "Taxpayer", "HUF", etc.
      try {
        const roleEls = u.$qa('header *, app-header *, mat-toolbar *, nav *, [class*="header" i] *, [class*="user" i] *, [class*="profile" i] *, [class*="nav" i] *');
        for (const el of roleEls) {
          const t = (el.innerText || el.textContent || '').trim();
          if (/^(?:Individual|Taxpayer|HUFs?|Company|Representative|Director|Partners?|Proprietor)$/i.test(t)) {
            // (a) Check previous sibling element
            const prev = el.previousElementSibling;
            if (prev) {
              const cleanPrev = _cleanNameString(u.getText(prev));
              if (_isValidName(cleanPrev)) return cleanPrev;
            }
            // (b) Check parent element's text nodes & children
            const parent = el.parentElement;
            if (parent) {
              if (parent.childNodes) {
                for (const child of Array.from(parent.childNodes)) {
                  if (child && child !== el) {
                    const cleanChild = _cleanNameString(child.textContent || child.innerText || '');
                    if (_isValidName(cleanChild)) return cleanChild;
                  }
                }
              }
              const cleanParent = _cleanNameString(u.getText(parent));
              if (_isValidName(cleanParent)) return cleanParent;

              // (c) Parent's previous sibling
              if (parent.previousElementSibling) {
                const cleanPrevP = _cleanNameString(u.getText(parent.previousElementSibling));
                if (_isValidName(cleanPrevP)) return cleanPrevP;
              }
            }
          }
        }
      } catch (_) {}

      // 3. Known badge selectors with attribute & text-node inspection
      const badgeSelectors = [
        '#loginUsername',
        'button[id*="loginUsername" i]',
        'span[id*="loginUsername" i]',
        '.header-user-name',
        '.user-profile-name',
        'span.header-username',
        'app-header .user-name',
        'app-header .profile-name',
        'app-header .user-details',
        '.user-details .user-name',
        'header .dropdown-toggle',
        'nav .dropdown-toggle',
        'app-header button',
        '.userInfoName',
        '.login-user-name',
        '[class*="profile-name" i]',
        '[class*="user-name" i]',
        '[class*="user-profile" i]',
        '[class*="header-right" i]'
      ];

      for (const sel of badgeSelectors) {
        try {
          const els = u.$qa(sel);
          for (const el of els) {
            // (a) First check title or aria-label attribute (holds full untruncated name)
            if (typeof el.getAttribute === 'function') {
              const attrName = el.getAttribute('title') || el.getAttribute('aria-label') || '';
              const cleanAttr = _cleanNameString(attrName);
              if (_isValidName(cleanAttr)) return cleanAttr;
            }

            // (b) Check child text nodes directly
            if (el.childNodes && el.childNodes.length > 0) {
              for (const child of Array.from(el.childNodes)) {
                if (child && (child.nodeType === 3 || child.nodeType === Node?.TEXT_NODE) && child.textContent && child.textContent.trim()) {
                  const cleanText = _cleanNameString(child.textContent);
                  if (_isValidName(cleanText)) return cleanText;
                }
              }
            }

            // (c) Check first clean child span
            if (typeof el.querySelector === 'function') {
              const firstSpan = el.querySelector('span:not([class*="role"]):not([class*="type"]):not([class*="sub"])');
              if (firstSpan) {
                const cleanSpan = _cleanNameString(firstSpan.innerText || firstSpan.textContent);
                if (_isValidName(cleanSpan)) return cleanSpan;
              }
            }

            // (d) Full element text
            const cleanFull = _cleanNameString(u.getText(el));
            if (_isValidName(cleanFull)) return cleanFull;
          }
        } catch (_) {}
      }

      // 4. Header multiline / regex fallback scanner
      try {
        const headerContainers = u.$qa('header, app-header, mat-toolbar, [class*="header" i], [class*="navbar" i], body');
        for (const h of headerContainers) {
          const rawText = u.getText(h);
          const lines = rawText.split(/\r?\n|\r/);
          for (let i = 0; i < lines.length; i++) {
            const line = lines[i].trim();
            if (/^(?:Individual|Taxpayer|HUFs?|Company|Representative|Director|Partners?|Proprietor)$/i.test(line)) {
              if (i > 0) {
                const prevLineClean = _cleanNameString(lines[i - 1]);
                if (_isValidName(prevLineClean)) return prevLineClean;
              }
            }
          }
          const m = rawText.match(/([A-Za-z\s.'-]{3,60})[\s\u02C0-\u02FF\u25A0-\u25FF\u2300-\u23FF\uFE00-\uFE0F⌵▼▽˅^vV<>|•\-_:\r\n]+(?:Individual|Taxpayer|HUFs?|Company|Representative|Director|Partners?|Proprietor)\b/i);
          if (m && m[1]) {
            const clean = _cleanNameString(m[1]);
            if (_isValidName(clean)) return clean;
          }
        }
      } catch (_) {}

      return getSession().client_temp_name || '';
    }

    // ─── Shared: Date of Birth (DOB) Extraction ─────────────────────────────
    function _extractDob() {
      // 0. Dedicated Profile Page Text Matcher (Matches 06-Aug-1971, 06/08/1971, 1971-08-06)
      try {
        const pageText = u.getPageText();
        const dobMatch = pageText.match(/(?:Date\s*of\s*Birth|DOB|Birth\s*Date)\s*[:#-]?\s*\r?\n?\s*(\d{2}[\/\-\.](?:[A-Za-z]{3}|\d{2})[\/\-\.]\d{4}|\d{4}[\/\-\.]\d{2}[\/\-\.]\d{2})/i);
        if (dobMatch && dobMatch[1]) {
          return dobMatch[1].trim().replace(/\./g, '-');
        }
      } catch (_) {}

      // 1. Dedicated DOB input/output/form-control elements
      try {
        const dobEls = u.$qa(
          'input[name*="dob" i], input[id*="dob" i], output[name*="dob" i], output[id*="dob" i],' +
          '[id*="dateOfBirth" i], [name*="dateOfBirth" i], [id*="birthDate" i], [name*="birthDate" i],' +
          '[formcontrolname*="dob" i], [formcontrolname*="dateOfBirth" i], [data-testid*="dob" i], [data-testid*="date-of-birth" i], span[id*="dob" i], div[id*="dob" i]'
        );
        for (const el of dobEls) {
          const val = (el.value || el.innerText || el.textContent || '').trim();
          const parsed = u.extractDob ? u.extractDob(val) : '';
          if (parsed) return parsed;
        }
      } catch (_) {}

      // 2. Label proximity for Date of Birth / DOB
      try {
        const labels = u.$qa('label, mat-label, span, div, p, th, td');
        for (const lbl of labels) {
          const txt = (lbl.innerText || lbl.textContent || '').trim();
          if (/^(?:Date\s*of\s*Birth|DOB|Birth\s*Date|Date\s*of\s*Incorporation)[:\s]*$/i.test(txt)) {
            const next = lbl.nextElementSibling || (lbl.parentElement ? lbl.parentElement.querySelector('input, span:not(label), div:not(label), output') : null);
            if (next) {
              const val = (next.value || next.innerText || next.textContent || '').trim();
              const parsed = u.extractDob ? u.extractDob(val) : '';
              if (parsed) return parsed;
            }
          }
        }
      } catch (_) {}

      // 3. On Profile pages: scan for standalone dates with realistic birth years (1920 to current - 10)
      try {
        const isProfile = (window.location.hash || '').toLowerCase().includes('profile');
        if (isProfile) {
          const pageText = u.getPageText();
          const dates = [...pageText.matchAll(/\b(\d{2}[\/\-\.]\d{2}[\/\-\.]\d{4}|\d{2}[\/\-\.](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\/\-\.]\d{4})\b/gi)];
          const currentYr = new Date().getFullYear();
          for (const d of dates) {
            const yr = parseInt(d[1].slice(-4));
            if (yr >= 1920 && yr <= (currentYr - 10)) {
              return d[1].replace(/\./g, '-');
            }
          }
        }
      } catch (_) {}

      return getSession().dob || '';
    }

    // ─── Full Name Extraction Coordinator (Tiered: Profile/Personal Info HIGHEST) ─
    function _extractName() {
      // 0. HIGHEST PRIORITY: Profile & Personal Information Page Text Scanner
      try {
        const pageText = u.getPageText();
        const profileNameMatch = pageText.match(/(?:^|\n)\s*(?:Full\s*)?Name\s*[:#-]?\s*\r?\n\s*([A-Za-z\t .'-]{3,60})\s*(?:\r?\n|$)/i);
        if (profileNameMatch) {
          const cleanP = _cleanNameString(profileNameMatch[1]);
          if (_isValidName(cleanP) && !NOISE_WORDS.has(cleanP)) return cleanP;
        }
      } catch (_) {}

      // TIER 1: Form inputs & labels on personal_information / profile page
      let fName = '', mName = '', lName = '';
      try {
        const inputs = u.$qa(
          'input[name*="FirstName" i], input[id*="FirstName" i], output[name*="FirstName" i], output[id*="FirstName" i],' +
          'input[name*="MiddleName" i], input[id*="MiddleName" i], output[name*="MiddleName" i], output[id*="MiddleName" i],' +
          'input[name*="SurName" i], input[id*="SurName" i], output[name*="SurName" i], output[id*="SurName" i],' +
          'input[name*="LastName" i], input[id*="LastName" i], output[name*="LastName" i], output[id*="LastName" i],' +
          '[id*="fullName" i], [name*="fullName" i], [id*="profileName" i], [data-testid*="user-name" i]'
        );
        for (const el of inputs) {
          const key = (el.getAttribute('name') || el.id || '').toLowerCase();
          const val = (el.value || el.innerText || el.textContent || '').trim();
          if (!val || val.length > 60) continue;
          if (key.includes('firstname') || key.includes('first_name')) fName = _cleanNameString(val);
          else if (key.includes('middlename') || key.includes('middle_name')) mName = _cleanNameString(val);
          else if (key.includes('surname') || key.includes('lastname') || key.includes('last_name')) lName = _cleanNameString(val);
          else if (key.includes('fullname') || key.includes('profilename')) {
            const cleanFull = _cleanNameString(val);
            if (_isValidName(cleanFull)) return cleanFull;
          }
        }
        if (fName || lName) {
          const combined = [fName, mName, lName].filter(Boolean).join(' ').trim();
          if (_isValidName(combined)) return combined;
        }
      } catch (_) {}

      // TIER 1.5: Label-proximity check for "Name", "Full Name", "Legal Name", "Name as per PAN"
      const labelName = _extractByLabel(['name', 'full name', 'legal name', 'name of assessee', 'assessee name', 'taxpayer name', 'name as per pan']);
      if (labelName) return labelName;

      // TIER 2: Header profile badge & storage (Fallback when not on personal info/profile)
      const headerName = _extractHeaderName();
      if (headerName) return headerName;

      // TIER 3: Page-level composite text regex
      try {
        const pageText = u.getPageText();
        const nameNearPan = pageText.match(
          /([A-Z][A-Z\s.'-]{2,59})\s+(?:[A-Z]{5}[0-9]{4}[A-Z])/
        );
        if (nameNearPan) {
          const candidate = _cleanNameString(nameNearPan[1]);
          if (_isValidName(candidate) && candidate.split(' ').length >= 2) return candidate;
        }
        const bc = u.getBreadcrumbs();
        if (bc) {
          const bcName = bc.match(/(?:Welcome[,\s]+|Hello[,\s]+)([A-Z][A-Z\s.'-]{2,59})/i);
          if (bcName) {
            const cleanBc = _cleanNameString(bcName[1]);
            if (_isValidName(cleanBc)) return cleanBc;
          }
        }
      } catch (_) {}

      return getSession().name || getSession().client_temp_name || '';
    }

    // ─── Shared: PAN Extraction ──────────────────────────────────────────────
    function _extractPan() {
      // 0. Dedicated Profile Page Text Matcher (Matches PAN APFPC0458J)
      try {
        const pageText = u.getPageText().toUpperCase();
        const panMatch = pageText.match(/(?:^|\n)\s*PAN\s*[:#-]?\s*\r?\n?\s*([A-Z]{5}[0-9]{4}[A-Z])/i);
        if (panMatch && u.isValidPan(panMatch[1])) {
          return panMatch[1];
        }
      } catch (_) {}

      // 1. Dedicated PAN input fields & form controls
      try {
        const panInputs = u.$qa(
          'input[id*="pan" i], input[name*="pan" i], output[id*="pan" i], output[name*="pan" i], [data-pan], [formcontrolname*="pan" i]'
        );
        for (const el of panInputs) {
          const val = (el.value || el.innerText || el.textContent || el.getAttribute('data-pan') || '').trim();
          const pan = u.extractPan(val);
          if (pan && u.isValidPan(pan)) return pan;
        }
      } catch (_) {}

      // 2. Label proximity for PAN
      try {
        const labels = u.$qa('label, mat-label, span, div, p, th, td');
        for (const lbl of labels) {
          const txt = (lbl.innerText || lbl.textContent || '').trim();
          if (/^(?:PAN|Permanent\s*Account\s*Number|PAN\s*Number)[:\s]*$/i.test(txt)) {
            const next = lbl.nextElementSibling || (lbl.parentElement ? lbl.parentElement.querySelector('input, span:not(label), div:not(label), output') : null);
            if (next) {
              const val = (next.value || next.innerText || next.textContent || '').trim();
              const pan = u.extractPan(val);
              if (pan && u.isValidPan(pan)) return pan;
            }
          }
        }
      } catch (_) {}

      // 3. Full page text scan — first valid PAN hit
      const pageText = u.getPageText().toUpperCase();
      const matches = [...pageText.matchAll(/\b([A-Z]{5}[0-9]{4}[A-Z])\b/g)];
      for (const m of matches) {
        if (u.isValidPan(m[1])) return m[1];
      }

      return getSession().pan || '';
    }

    // ─── Shared: AY + Form from Context ─────────────────────────────────────
    function _extractAyAndForm(url) {
      const ay = u.extractAY(url) || getSession().ay || '';
      const form = u.extractItrForm(url, u.getPageText()) || getSession().form || '';
      return { ay, form };
    }

    // ─── Shared: Best Available Client Name ─────────────────────────────────
    // Returns session.name ONLY if it was set from a profile/personal-info extraction,
    // not if it's just a copy of the truncated header badge name.
    // Falls back to _extractName() → header badge in that priority order.
    function _getClientName() {
      const sName = getSession().name || '';
      const hName = getSession().client_temp_name || '';
      // If session.name was polluted from the header badge (same truncated value), don't use it
      if (sName && sName !== hName) return sName;
      // Try a fresh DOM extraction (works on profile, personal info, and some other pages)
      const extracted = _extractName();
      if (extracted && extracted !== hName) return extracted;
      // Last resort: use the header badge (may be truncated, but better than nothing)
      return hName || sName || '';
    }

    // ─── Shared: Breadcrumb ITR context validator ────────────────────────────
    function _isItrContext(url) {
      const hashRoute = (new URL(url).hash || '').toLowerCase();
      // Must be in ITR filing flow, profile, landing, dashboard or view filed returns
      const ITR_ROUTE_SIGNALS = [
        'foreturns-ay', 'fo-itr', 'fo-e-verify', 'fo-return-success',
        'fo-e-verify-later', 'fo-e-verify-now-success', 'fo-lets-get-started',
        'fo-select-itr', 'fo-schedules', 'fo-filing', 'fo-select-status',
        'personal_information', 'fo-itr-shared', 'fo-filing-status',
        'myprofile', 'profiledetail', 'profile', 'fileincometaxreturn',
        'dashboard', 'landing', 'home', 'viewfiledreturns', 'view-filed-returns',
        'viewreturns', 'filedreturns', 'filed-returns', 'itrstatus', 'itr-status'
      ];
      if (ITR_ROUTE_SIGNALS.some(s => hashRoute.includes(s))) return true;

      // Breadcrumb check
      const bc = u.getBreadcrumbs().toLowerCase();
      if (bc.includes('income tax return') || bc.includes('e-file') || bc.includes('filing returns') ||
          bc.includes('my profile') || bc.includes('profile') || bc.includes('dashboard') ||
          bc.includes('view filed returns') || bc.includes('filed returns')) return true;

      return false;
    }

    // ─── Fast Profile / Form Lazy-Render Watcher (Capped strictly <= 2.0s) ────
    function _watchForProfileValues(url) {
      const lowerUrl = (url || '').toLowerCase();
      if (
        lowerUrl.includes('personal_information-contact') ||
        lowerUrl.includes('personal_information_contact') ||
        lowerUrl.includes('contact-details') ||
        lowerUrl.includes('contact_details') ||
        lowerUrl.includes('/contact') ||
        lowerUrl.includes('bank-details') ||
        lowerUrl.includes('bank_details') ||
        lowerUrl.includes('bankaccount')
      ) {
        return;
      }

      if (window.__SDC_ITR_FORM_WATCHER__) return;
      window.__SDC_ITR_FORM_WATCHER__ = true;

      let attempts = 0;
      const MAX_ATTEMPTS = 5; // 5 × 350ms = 1.75s max wait (guaranteed <= 2.0s)

      const poll = setInterval(async () => {
        attempts++;
        const pan = _extractPan();
        const name = _extractName();
        const headerName = _extractHeaderName();
        const dob = _extractDob();
        const isProfile = url.toLowerCase().includes('profile');

        if (pan || (isProfile && dob) || (name && name !== getSession().client_temp_name)) {
          clearInterval(poll);
          window.__SDC_ITR_FORM_WATCHER__ = false;

          if (pan) getSession().pan = pan;
          if (headerName) getSession().client_temp_name = headerName;
          if (name) getSession().name = name;
          if (dob) getSession().dob = dob;

          const activeName = getSession().name || getSession().client_temp_name || '';
          console.log(`⚡ Sera SDC [itr_lazy_watcher]: ✅ Async profile data resolved (within ${attempts * 350}ms) → Name: "${activeName}", PAN: "${pan || getSession().pan}", DOB: "${dob || getSession().dob}"`);

          const capture = {
            portal: 'income tax',
            pan: pan || getSession().pan || '',
            client_name: activeName,
            client_temp_name: headerName || getSession().client_temp_name || '',
            name: activeName,
            taxpayer_name: activeName,
            dob: dob || getSession().dob || '',
            filing_type: isProfile ? 'Profile / Identity' : 'ITR (Form Pending)',
            period_label: getSession().ay || '',
            arn: 'N/A',
            status: isProfile ? 'Profile Details' : 'Draft / Personal Info',
            dom_breadcrumbs: u.getBreadcrumbs(),
            confirmation_message: ''
          };

          await SDC.session.recordStep(url, isProfile ? 'itr_personal_info' : 'itr_personal_info', capture);
          await SDC.session.save();
          if (typeof SDC.emitCapture === 'function') {
            SDC.emitCapture(capture, 'ITR Portal', 'itr_personal_info');
          }
          return;
        }

        if (attempts >= MAX_ATTEMPTS) {
          clearInterval(poll);
          window.__SDC_ITR_FORM_WATCHER__ = false;
        }
      }, 350);
    }

    // ─── CROSSHAIR 1: Personal Info & Profile Details ───────────────────────
    // Target: .../personal_information OR .../dashboard/myProfile/profileDetail
    function _handlePersonalInfo(url) {
      if (!_isItrContext(url)) return null;

      const lowerUrl = (url || '').toLowerCase();
      // EXPLICIT SUPPRESSION: Ignore Contact, Communication, and Bank details subpages
      if (
        lowerUrl.includes('personal_information-contact') ||
        lowerUrl.includes('personal_information_contact') ||
        lowerUrl.includes('contact-details') ||
        lowerUrl.includes('contact_details') ||
        lowerUrl.includes('/contact') ||
        lowerUrl.includes('bank-details') ||
        lowerUrl.includes('bank_details') ||
        lowerUrl.includes('bankaccount')
      ) {
        console.log('⚡ Sera SDC [itr_personal_info]: Suppressed capture on contact/communication/bank details subpage.');
        return null;
      }

      // Reset watcher guard on fresh navigation
      window.__SDC_ITR_FORM_WATCHER__ = false;

      const isProfilePage = url.toLowerCase().includes('profile');
      const pan = _extractPan();
      const name = _extractName();
      const headerName = _extractHeaderName();
      const dob = _extractDob();
      const { ay, form } = _extractAyAndForm(url);

      // Start fast async watcher (<= 1.75s) if PAN, DOB or Name are still loading
      if (!pan || !dob || !name) {
        _watchForProfileValues(url);
      }

      // Update session cache
      if (pan) getSession().pan = pan;
      if (headerName) getSession().client_temp_name = headerName;
      if (name) getSession().name = name;
      else if (headerName && !getSession().name) getSession().name = headerName;
      if (dob) getSession().dob = dob;
      if (ay) getSession().ay = ay;
      if (form) getSession().form = form;

      const activeName = getSession().name || getSession().client_temp_name || '';

      // On Profile pages (Category B), always capture immediately
      if (!pan && !activeName && !dob && !isProfilePage) {
        console.log('Sera SDC [itr_personal_info]: Waiting for async form render (watcher active).');
        return null;
      }

      return {
        portal: 'income tax',
        pan: pan || getSession().pan || '',
        client_name: activeName || 'Taxpayer',
        client_temp_name: headerName || getSession().client_temp_name || '',
        name: activeName || 'Taxpayer',
        taxpayer_name: activeName || 'Taxpayer',
        dob: dob || getSession().dob || '',
        filing_type: form || (isProfilePage ? 'Profile / Identity' : 'ITR (Form Pending)'),
        period_label: ay,
        arn: 'N/A',
        status: isProfilePage ? 'Profile Details' : 'Draft / Personal Info',
        dom_breadcrumbs: u.getBreadcrumbs(),
        confirmation_message: ''
      };
    }

    // ─── CROSSHAIR 2: ITR Form Selection Page ────────────────────────────────
    // Target: .../fo-select-itr-form
    function _handleFormSelect(url) {
      if (!_isItrContext(url)) return null;

      const { ay, form } = _extractAyAndForm(url);
      if (form) getSession().form = form;
      if (ay) getSession().ay = ay;

      const headerName = _extractHeaderName();
      const pan = getSession().pan || _extractPan();
      const name = _getClientName();
      const dob = getSession().dob || _extractDob();

      if (headerName && !getSession().client_temp_name) getSession().client_temp_name = headerName;
      if (name && name !== getSession().client_temp_name && !getSession().name) getSession().name = name;

      if (!form) return null; // Nothing interesting yet

      const activeName = name || headerName || '';

      return {
        portal: 'income tax',
        pan: pan || getSession().pan || '',
        client_name: activeName,
        client_temp_name: headerName || getSession().client_temp_name || '',
        name: activeName,
        taxpayer_name: activeName,
        dob: dob || getSession().dob || '',
        filing_type: form,
        period_label: ay,
        arn: 'N/A',
        status: 'Form Selected',
        dom_breadcrumbs: u.getBreadcrumbs(),
        confirmation_message: ''
      };
    }

    // ─── CROSSHAIR 3: Filed & Verified ───────────────────────────────────────
    function _handleFiledVerified(url) {
      if (!_isItrContext(url)) return null;

      const pageText = u.getPageText();
      const lower = pageText.toLowerCase();

      const VERIFIED_PHRASES = [
        'successfully filed and verified your return',
        'you have successfully filed and verified',
        'filed and verified',
        'return has been verified'
      ];
      const hasVerified = VERIFIED_PHRASES.some(p => lower.includes(p));
      if (!hasVerified) return null;

      const ack = u.extractAck15(pageText) || 'N/A';
      const headerName = _extractHeaderName();
      const pan = getSession().pan || _extractPan();
      const name = _getClientName();
      const dob = getSession().dob || _extractDob();
      const { ay, form } = _extractAyAndForm(url);

      if (pan) getSession().pan = pan;
      if (name && name !== (getSession().client_temp_name || '')) getSession().name = name;
      if (headerName) getSession().client_temp_name = headerName;
      if (dob) getSession().dob = dob;

      const activeName = name || headerName || '';
      const msg = _extractBannerText(
        'successfully filed and verified',
        'you have successfully filed and verified your return'
      );

      return {
        portal: 'income tax',
        pan: pan || getSession().pan || '',
        client_name: activeName,
        client_temp_name: headerName || getSession().client_temp_name || '',
        name: activeName,
        taxpayer_name: activeName,
        dob: dob || getSession().dob || '',
        filing_type: form || 'ITR',
        period_label: ay,
        arn: ack,
        status: 'Filed & Verified',
        dom_breadcrumbs: u.getBreadcrumbs(),
        confirmation_message: msg
      };
    }

    // ─── CROSSHAIR 4: Submitted (Pending e-Verification) ─────────────────────
    function _handleSubmittedPending(url) {
      if (!_isItrContext(url)) return null;

      const pageText = u.getPageText();
      const lower = pageText.toLowerCase();

      const SUBMITTED_PHRASES = [
        'successfully submitted your return',
        'you have successfully submitted',
        'e-verify within 30 days',
        'download itr-v',
        'download itrv'
      ];
      const hasSubmitted = SUBMITTED_PHRASES.some(p => lower.includes(p));
      if (!hasSubmitted) return null;

      if (lower.includes('successfully filed and verified')) return null;

      const ack = u.extractAck15(pageText) || 'N/A';
      const headerName = _extractHeaderName();
      const pan = getSession().pan || _extractPan();
      const name = _getClientName();
      const dob = getSession().dob || _extractDob();
      const { ay, form } = _extractAyAndForm(url);

      if (pan) getSession().pan = pan;
      if (name && name !== (getSession().client_temp_name || '')) getSession().name = name;
      if (headerName) getSession().client_temp_name = headerName;
      if (dob) getSession().dob = dob;

      const activeName = name || headerName || '';
      const msg = _extractBannerText(
        'successfully submitted your return',
        'you have successfully submitted your return'
      );

      return {
        portal: 'income tax',
        pan: pan || getSession().pan || '',
        client_name: activeName,
        client_temp_name: headerName || getSession().client_temp_name || '',
        name: activeName,
        taxpayer_name: activeName,
        dob: dob || getSession().dob || '',
        filing_type: form || 'ITR',
        period_label: ay,
        arn: ack,
        status: 'Submitted (Pending e-Verification)',
        dom_breadcrumbs: u.getBreadcrumbs(),
        confirmation_message: msg
      };
    }

    // ─── CROSSHAIR 5: Landing Page & Dashboard Identity Capture ──────────────
    // Target: #/dashboard/fileIncomeTaxReturn, #/dashboard, #/home, #/welcome
    function _handleLanding(url) {
      if (!_isItrContext(url)) return null;

      const headerName = _extractHeaderName();
      const pan = _extractPan();
      const dob = _extractDob();
      const { ay, form } = _extractAyAndForm(url);

      if (headerName) {
        getSession().client_temp_name = headerName;
        // NOTE: Do NOT set session.name from header badge here — header badge names are truncated
        // (e.g. "INDRAJIT CHATTE...") and would block full name extraction from personal_info/profile.
        // session.name is ONLY set from profile/personal-info crosshair (Crosshair #4).
      }
      if (pan) getSession().pan = pan;
      if (dob) getSession().dob = dob;
      if (ay) getSession().ay = ay;
      if (form) getSession().form = form;

      // At landing, the best name we have is the header badge (may be truncated).
      // session.name is intentionally NOT set here — it's reserved for profile/personal-info pages.
      const activeName = getSession().name || headerName || getSession().client_temp_name || '';

      console.log(`⚡ Sera SDC [itr_landing]: Post-login landing active for ${activeName || 'Client'} (${pan || 'No PAN'}) [Header Name: "${headerName}"]`);

      if (!pan && !activeName) {
        return null;
      }

      return {
        portal: 'income tax',
        pan: pan || getSession().pan || '',
        client_name: activeName,
        client_temp_name: headerName || getSession().client_temp_name || '',
        name: activeName,
        taxpayer_name: activeName,
        dob: dob || getSession().dob || '',
        filing_type: form || 'ITR (Landing / e-File)',
        period_label: ay || '',
        arn: 'N/A',
        status: 'Landing Page Active',
        dom_breadcrumbs: u.getBreadcrumbs(),
        confirmation_message: ''
      };
    }

    // ─── Shared: View Filed Returns Details Extractor (Card & Table Aware) ───
    function _extractViewFiledReturnsDetails() {
      const pageText = u.getPageText();
      
      // 1. Scan DOM cards / table rows / expansion panels
      try {
        const rows = u.$qa('tr, mat-row, mat-expansion-panel, .card, .return-card, .status-card, [class*="return" i], [class*="filing" i], [class*="card" i]');
        const extractedCards = [];
        for (const row of rows) {
          const rText = u.getText(row);
          const ackM = rText.match(/(?:Acknowledgement\s*(?:Number|No\.?)|Ack\s*No\.?|Receipt\s*No\.?|ARN)\s*[:#-]?\s*(\d{15})/i) || rText.match(/\b(\d{15})\b/);
          if (!ackM) continue;
          
          const ayM = rText.match(/(?:Assessment\s*Year|AY|A\.Y\.)\s*[:#-]?\s*(20\d{2}-\d{2}|\d{4}-\d{2})/i) || rText.match(/\b(20\d{2}-\d{2})\b/);
          const formM = rText.match(/\b(ITR-[1-7][A-Za-z]?|ITR\s*[1-7][A-Za-z]?|ITR-V|ITR-U)\b/i);
          
          const ayStr = ayM ? (ayM[1].toUpperCase().startsWith('AY') ? ayM[1].toUpperCase() : `AY ${ayM[1]}`) : '';
          const formStr = formM ? formM[1].replace(/\s+/g, '-').toUpperCase() : 'ITR';
          
          extractedCards.push({
            ack: ackM[1],
            ay: ayStr,
            form: formStr,
            yearVal: ayM ? parseInt(ayM[1].slice(0, 4)) : 0
          });
        }
        
        if (extractedCards.length > 0) {
          // Sort by latest assessment year descending (e.g. 2026 > 2025 > 2024 > 2013)
          extractedCards.sort((a, b) => b.yearVal - a.yearVal);
          return extractedCards[0];
        }
      } catch (_) {}

      // 2. Full page multiline pattern scan across all card blocks
      try {
        const cardRegex = /(?:Assessment\s*Year|AY|A\.Y\.)\s*[:#-]?\s*(20\d{2}-\d{2}|\d{4}-\d{2})[\s\S]*?(?:Acknowledgement\s*(?:Number|No\.?)|Ack\s*No\.?|Receipt\s*No\.?|ARN)\s*[:#-]?\s*(\d{15})/gi;
        let m;
        const textCards = [];
        while ((m = cardRegex.exec(pageText)) !== null) {
          const formM = m[0].match(/\b(ITR-[1-7][A-Za-z]?|ITR\s*[1-7][A-Za-z]?|ITR-V|ITR-U)\b/i);
          textCards.push({
            ay: m[1].toUpperCase().startsWith('AY') ? m[1].toUpperCase() : `AY ${m[1]}`,
            ack: m[2],
            form: formM ? formM[1].replace(/\s+/g, '-').toUpperCase() : 'ITR',
            yearVal: parseInt(m[1].slice(0, 4))
          });
        }
        if (textCards.length > 0) {
          textCards.sort((a, b) => b.yearVal - a.yearVal);
          return textCards[0];
        }
      } catch (_) {}

      // 3. Fallback direct regex matches
      const ackMatch = pageText.match(/(?:Acknowledgement\s*(?:Number|No\.?)|Ack\s*No\.?|Receipt\s*No\.?|ARN)\s*[:#-]?\s*(\d{15})/i) || pageText.match(/\b(\d{15})\b/);
      const ayMatch = pageText.match(/(?:Assessment\s*Year|AY|A\.Y\.)\s*[:#-]?\s*(20\d{2}-\d{2}|\d{4}-\d{2})/i) || pageText.match(/\b(20\d{2}-\d{2})\b/);
      const formMatch = pageText.match(/\b(ITR-[1-7][A-Za-z]?|ITR\s*[1-7][A-Za-z]?|ITR-V|ITR-U)\b/i);

      return {
        ack: ackMatch ? ackMatch[1] : '',
        ay: ayMatch ? (ayMatch[1].toUpperCase().startsWith('AY') ? ayMatch[1].toUpperCase() : `AY ${ayMatch[1]}`) : '',
        form: formMatch ? formMatch[1].replace(/\s+/g, '-').toUpperCase() : 'ITR',
        yearVal: 0
      };
    }

    // ─── Fast View Filed Returns Watcher (Capped strictly <= 2.0s) ───────────
    function _watchForViewFiledReturns(url) {
      if (window.__SDC_ITR_VIEW_WATCHER__) return;
      window.__SDC_ITR_VIEW_WATCHER__ = true;

      let attempts = 0;
      const MAX_ATTEMPTS = 5; // 5 × 350ms = 1.75s max wait (guaranteed <= 2.0s)

      const poll = setInterval(async () => {
        attempts++;
        const cardDetails = _extractViewFiledReturnsDetails();
        const ack = cardDetails.ack;

        if (ack) {
          clearInterval(poll);
          window.__SDC_ITR_VIEW_WATCHER__ = false;

          const headerName = _extractHeaderName();
          const pan = getSession().pan || _extractPan();
          const name = _getClientName();
          const dob = getSession().dob || _extractDob();
          const ay = cardDetails.ay || getSession().ay || '';
          const form = cardDetails.form || getSession().form || 'ITR';

          if (pan) getSession().pan = pan;
          if (name && name !== (getSession().client_temp_name || '')) getSession().name = name;
          if (headerName) getSession().client_temp_name = headerName;
          if (dob) getSession().dob = dob;
          if (ay) getSession().ay = ay;
          if (form) getSession().form = form;
          getSession().arn = ack;

          const activeName = getSession().name || name || headerName || 'Taxpayer';
          console.log(`⚡ Sera SDC [itr_view_filed_returns]: ✅ Async ACK resolved (within ${attempts * 350}ms) → ACK: "${ack}", AY: "${ay}", Form: "${form}"`);

          const capture = {
            portal: 'income tax',
            pan: pan || getSession().pan || '',
            client_name: activeName,
            client_temp_name: headerName || getSession().client_temp_name || '',
            name: activeName,
            taxpayer_name: activeName,
            dob: dob || getSession().dob || '',
            filing_type: form,
            period_label: ay,
            arn: ack,
            status: 'Filed & Verified (Portal Confirmed)',
            dom_breadcrumbs: u.getBreadcrumbs(),
            confirmation_message: 'Official 15-digit Acknowledgement captured from portal return history.'
          };

          await SDC.session.recordStep(url, 'itr_view_filed_returns', capture);
          await SDC.session.save();
          if (typeof SDC.emitCapture === 'function') {
            SDC.emitCapture(capture, 'ITR Portal', 'itr_view_filed_returns');
          }
          return;
        }

        if (attempts >= MAX_ATTEMPTS) {
          clearInterval(poll);
          window.__SDC_ITR_VIEW_WATCHER__ = false;
        }
      }, 350);
    }

    // ─── CROSSHAIR 3: View Filed Returns (15-Digit ACK & AY Extractor) ────────
    function _handleViewFiledReturns(url) {
      if (!_isItrContext(url)) return null;

      window.__SDC_ITR_VIEW_WATCHER__ = false;

      const cardDetails = _extractViewFiledReturnsDetails();
      const headerName = _extractHeaderName();
      const pan = getSession().pan || _extractPan();
      const name = _getClientName();
      const dob = getSession().dob || _extractDob();

      if (pan) getSession().pan = pan;
      if (name && name !== (getSession().client_temp_name || '')) getSession().name = name;
      if (headerName) getSession().client_temp_name = headerName;
      if (dob) getSession().dob = dob;

      const ack = cardDetails.ack;
      const ay = cardDetails.ay || getSession().ay || '';
      const form = cardDetails.form || getSession().form || 'ITR';

      if (!ack) {
        // Start fast async watcher to catch table when Angular finishes rendering
        _watchForViewFiledReturns(url);
        return null;
      }

      if (ay) getSession().ay = ay;
      if (form) getSession().form = form;
      getSession().arn = ack;

      const activeName = getSession().name || headerName || 'Taxpayer';
      console.log(`⚡ Sera SDC [itr_view_filed_returns]: Extracted 15-digit ACK ${ack} for ${ay} (${form})`);

      return {
        portal: 'income tax',
        pan: pan || getSession().pan || '',
        client_name: activeName,
        client_temp_name: headerName || getSession().client_temp_name || '',
        name: activeName,
        taxpayer_name: activeName,
        dob: dob || getSession().dob || '',
        filing_type: form,
        period_label: ay,
        arn: ack,
        status: 'Filed & Verified (Portal Confirmed)',
        dom_breadcrumbs: u.getBreadcrumbs(),
        confirmation_message: 'Official 15-digit Acknowledgement captured from portal return history.'
      };
    }

    // ─── Helper: Extract Banner Text from DOM ────────────────────────────────
    function _extractBannerText(...keywords) {
      try {
        const cards = u.$qa(
          '.alert, .success-card, .success-banner, .confirmation-message, ' +
          '[class*="success" i], [class*="alert" i], [class*="banner" i], ' +
          '[class*="confirm" i], [class*="message" i], p, h3, h4, span'
        );
        for (const el of cards) {
          const text = u.getText(el).toLowerCase();
          if (keywords.some(k => text.includes(k))) {
            return u.getText(el);
          }
        }
      } catch (_) {}
      return '';
    }

    // ─── Session Reset: Clear all ITR client data ────────────────────────────
    function _resetItrSession() {
      getSession().pan  = '';
      getSession().name = '';
      getSession().client_temp_name = '';
      getSession().dob  = '';
      getSession().form = '';
      getSession().ay   = '';
      console.log('⚡ Sera SDC [ITR]: 🧹 Session cleared — ready for new client.');
    }

    // ─── CROSSHAIR 0 (Priority): Login / Logout / Session Expired Route Guard ───
    async function _handleLoginLogout(url) {
      const lower = (url || '').toLowerCase();
      if (lower.includes('logout') || lower.includes('signout') || lower.includes('sign-out') ||
          lower.includes('sessionexpire') || lower.includes('session-expire') || lower.includes('sessionexpired') ||
          lower.includes('session-expired') || lower.includes('timeout')) {
        await SDC.session.finalizeLogout(url);
      }
      _resetItrSession();
      await SDC.clearAllSessions();
      return null;
    }

    // ─── Register SDC.onSessionClear for ITR ────────────────────────────────
    SDC.onSessionClear(_resetItrSession);

    // ─── Register Protocol ───────────────────────────────────────────────────
    SDC.register({
      name: 'ITR Portal',
      hostMatch: /(?:incometax\.gov\.in|incometaxindiaefiling\.gov\.in|localhost|127\.0\.0\.1|^$)/,
      crosshairs: [
        {
          id: 'itr_filed_verified',
          // Highest capture priority: check success routes first
          pattern: /(?:fo-e-verify-now-success|fo-return-success|e-verify.*success|filing-success)/i,
          handler: _handleFiledVerified
        },
        {
          id: 'itr_submitted_pending',
          pattern: /(?:fo-e-verify-later|complete-verification|fo-verify-later|fo-return-submitted)/i,
          handler: _handleSubmittedPending
        },
        {
          id: 'itr_view_filed_returns',
          pattern: /(?:itr.?status|view.?filed.?returns|fo-view-filed-returns|viewreturns|view-returns|filed-returns|filedreturns)/i,
          handler: _handleViewFiledReturns
        },
        {
          id: 'itr_personal_info',
          pattern: /(?:personal.?information|personal.?info|myProfile|profileDetail|profile-detail|my-profile|profile|partA_gen|parta.?gen|part-a-general)/i,
          handler: _handlePersonalInfo
        },
        {
          id: 'itr_form_select',
          pattern: /(?:fo-select-itr-form|select.?itr.?form|fo-lets-get-started)/i,
          handler: _handleFormSelect
        },
        {
          id: 'itr_landing',
          pattern: /(?:fileincometaxreturn|file-income-tax-return|filereturn|landing|home|welcome|dashboard$|dashboard\/file)/i,
          handler: _handleLanding
        },
        {
          // Login/logout/sessionExpire detection: matches all auth/login/logout/sessionExpire subroutes
          id: 'itr_login',
          pattern: /[/#](?:login|logout|sign-?in|sign-?out|password|pre-login|auth|session.?expire|session-?expired|session-?timeout)(?:[?/#]|$)/i,
          handler: _handleLoginLogout
        }
      ]
    });

    console.log('⚡ Sera SDC: ITR Protocol registered successfully.');
  }

  _register();

})();

