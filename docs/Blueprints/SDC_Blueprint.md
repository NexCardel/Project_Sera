# Sera DOM Crosshair (SDC) — Blueprint & Design Reference

## Concept

**SDC** (Sera DOM Crosshair) is a lightweight, route-gated DOM scanning engine for the Sera browser extension.

Unlike `tracker.js` which runs a continuous MutationObserver across the entire DOM (causing minor lag on heavy SPA portals), SDC:

1. **Sleeps completely** while on non-target pages (zero CPU overhead)
2. **Wakes only** when the current URL/hash matches a registered **crosshair pattern**
3. **Delegates immediately** to the matched protocol's handler (ITR, GST, etc.)
4. **Returns to sleep** after the capture

SDC coexists alongside `tracker.js`. Both run when FST is enabled. SDC is the future path toward deprecating the heavy `tracker.js` once all portals are covered.

---

## Architecture

```
sera_extension/
└── sdc/
    ├── sdc_core.js              ← Route listener + dispatcher (SPA-safe)
    └── protocols/
        ├── itr_protocol.js      ← Income Tax portal — ACTIVE (v1.0)
        ├── gst_protocol.js      ← GST portal — Stub (planned)
        ├── traces_protocol.js   ← TRACES TDS portal — Stub (planned)
        └── mca_protocol.js      ← MCA V3 portal — Stub (planned)
```

---

## SDC Core (`sdc_core.js`)

- Intercepts `pushState`, `replaceState`, `hashchange`, `popstate` to detect SPA route changes.
- On a route change, iterates registered protocols to find a host match, then crosshair pattern match.
- On crosshair match, calls the protocol handler and emits `SeraFSTApiCapture` event (consumed by existing `filing_detector.js` pipeline).
- Uses a 400ms debounce so rapid navigations don't fire redundant scans.
- Provides a `SDC.utils` helper library to all protocols.

### Protocol Registration API

```js
window.__SERA_SDC__.register({
  name: 'ITR Portal',
  hostMatch: /incometax\.gov\.in/,
  crosshairs: [
    {
      id: 'itr_filed_verified',
      pattern: /fo-e-verify-now-success/i,
      handler: async (url) => { /* returns SDCCapture or null */ }
    }
  ]
});
```

### SDCCapture Shape

```js
{
  portal: 'income tax',       // string
  pan: 'ABCDE1234F',          // 10-char PAN (primary key)
  client_name: 'JOHN SMITH',  // full legal name
  name: 'JOHN SMITH',
  taxpayer_name: 'JOHN SMITH',
  filing_type: 'ITR-4',       // form type
  period_label: 'AY 2026-27', // assessment / tax period
  arn: '123456789012345',     // 15-digit ACK or 'N/A'
  status: 'Filed & Verified', // see statuses below
  dom_breadcrumbs: '...',
  confirmation_message: '...'
}
```

---

## ITR Protocol Crosshairs

| Crosshair ID | Route Pattern | Status Captured | Priority |
| :--- | :--- | :--- | :--- |
| `itr_filed_verified` | `fo-e-verify-now-success`, `fo-return-success` | **Filed & Verified** | 1st |
| `itr_submitted_pending` | `fo-e-verify-later`, `complete-verification` | **Submitted (Pending e-Verification)** | 2nd |
| `itr_personal_info` | `personal_information`, `profile`, `parta_gen` | **PAN + Name Identity Lock** | 3rd |
| `itr_form_select` | `fo-select-itr-form`, `fo-lets-get-started` | **Form + AY Context Lock** | 4th |

### Status Values

| Status | Description |
| :--- | :--- |
| `Filed & Verified` | Banner: "You have successfully filed and verified your return!" |
| `Submitted (Pending e-Verification)` | Banner: "…submitted…e-Verify within 30 days…Download ITR-V" |
| `Form Selected` | ITR form type and AY identified from route/page |
| `Draft / Personal Info` | PAN + Name captured from profile page |

---

## Name Extraction Strategy (3-Tier)

| Tier | Source | Notes |
| :--- | :--- | :--- |
| T1 | DOM form inputs (`FirstName`, `MiddleName`, `SurName/LastName`) | Most precise — Part A General page |
| T2 | Header profile badge (`#loginUsername`, `.header-user-name`) | Available on all portal pages; strip trailing `...` |
| T3 | Page text composite regex (name near PAN or near "Individual") | Fallback for dynamic rendering |

**Consultation Note**: For person name regex (T3), we deliberately avoid strict regex (too many edge cases — initials, compound names, foreign names). Instead, we anchor name extraction to a PAN or "Individual" label being nearby on the page, which makes it accurate without needing a name-pattern regex.

---

## Non-ITR EVC Disambiguation

SDC **will not** fire on EVC flows unrelated to ITR (e.g., Form 10IEA, refund EVC, generic challan EVC). The `_isItrContext()` guard validates:

1. Route/hash contains ITR-specific segments (`foreturns-ay`, `fo-itr`, `fo-e-verify`, `fo-select-itr`, etc.)
2. **OR** breadcrumb confirms `Income Tax Return > Submit Level Validation`

If neither condition is met, the crosshair handler returns `null` (no capture).

---

## Injection Flow

```
background.js injectSAD(tabId)
  │
  ├─ content_scripts/filing_detector.js  (always: toast notifier + filing_result router)
  ├─ tracker.js                          (if fstEnabled: heavy full-DOM observer)
  │
  └─ After filing_detector resolves:
      ├─ sdc/sdc_core.js                 (if sdcEnabled: route listener + dispatcher)
      ├─ sdc/protocols/itr_protocol.js
      ├─ sdc/protocols/gst_protocol.js
      ├─ sdc/protocols/traces_protocol.js
      └─ sdc/protocols/mca_protocol.js
```

---

## Future Roadmap

- [ ] `gst_protocol.js` — GSTIN + Legal Name + GSTR-1/3B/9/CMP-08 ARN capture
- [ ] `traces_protocol.js` — TAN + 24Q/26Q TDS statement PRN/Token capture
- [ ] `mca_protocol.js` — CIN + SRN ROC filing capture
- [ ] Popup toggle: **"SDC Only" mode** (disables tracker.js, keeps SDC for lighter footprint)
- [ ] Session carry-forward: PAN/Name from `itr_personal_info` auto-propagates to subsequent crosshairs within same session
