# Sera DOM Crosshair (SDC) — Blueprint & Design Reference

## Concept

**SDC** (Sera DOM Crosshair) is a lightweight, route-gated DOM scanning and session assembly engine for the Sera browser extension.

Unlike continuous DOM observers that cause lag on heavy SPA portals, SDC:

1. **Sleeps completely** while on non-target pages (zero CPU overhead)
2. **Wakes only** when the current URL/hash matches a registered **crosshair pattern**
3. **Delegates immediately** to the matched protocol's handler (ITR, GST, etc.)
4. **Buffers 100% of captured session data** into the in-memory **`sdc_assembler`** module
5. **Flushes one single atomic master payload** directly to the Project Sera desktop app when the taxpayer session terminates (via logout, client switch, or 15-min inactivity TTL)

---

## Architecture

```
sera_extension/
└── sdc/
    ├── sdc_core.js              ← Route listener + SDC Assembler + HTTP Dispatcher
    └── protocols/
        ├── itr_protocol.js      ← Income Tax portal — ACTIVE (v2.9.6)
        ├── gst_protocol.js      ← GST portal — Active
        ├── traces_protocol.js   ← TRACES TDS portal — Stub (planned)
        └── mca_protocol.js      ← MCA V3 portal — Stub (planned)
```

---

## SDC Assembler (`sdc_assembler`)

The **SDC Assembler** aggregates all multi-step filing fragments throughout a taxpayer's active portal session into an isolated, portal-scoped local storage space:

- **Portal-Scoped Storage Isolation**:
  - `__SDC_SESSION_ITR__` for Income Tax Portal (`incometax.gov.in`)
  - `__SDC_SESSION_GST__` for GST Portal (`gst.gov.in`)
  - `__SDC_SESSION_TRACES__` for TRACES Portal (`tdscpc.gov.in`)
  - `__SDC_SESSION_MCA__` for MCA Portal (`mca.gov.in`)
  - `__SDC_SESSION_DEFAULT__` for generic compliance sites
- **15-Minute Inactivity TTL**: Sessions automatically finalize and flush after 15 minutes of inactivity.
- **Double-Flush Guard (`_assembler_flushed`)**: Prevents duplicate emissions when multiple termination triggers (e.g., explicit logout + login boundary guard) fire in rapid succession.
- **Browse-Only Filter**: If a session contains navigation steps but zero crosshair captures, `filing_result` emission is suppressed, recording only the audit trail in `sdc_session_timelines`.
- **Client Context Switch Guard**: Detects when a new PAN is encountered mid-session, cleanly sealing and flushing the prior client's session before initializing the new client session.

---

## Primary Dispatch Pipeline: Direct HTTP Loopback

SDC delivers unified payloads to the desktop application via a resilient two-tier pipeline:

1. **Primary Route — Direct Local HTTP (`http://127.0.0.1:49152`)**:
   - Dispatches directly from the active tab via `fetch()`.
   - Immune to Manifest V3 background service worker idle/sleep cycles.
   - Ultra-low latency and zero dependency on native messaging process pipes.
2. **Fail-Safe Route — Chrome Runtime Service Worker**:
   - If the direct HTTP fetch fails (e.g., desktop app temporarily closed), it falls back to `chrome.runtime.sendMessage()` to route via `background.js` and the Native Messaging host.

## Multi-Dataset Assembler Contract

SDC does not create a tracker row for every page visit. It buffers captures during the active portal session and emits one final `filing_result` envelope when the session terminates. The authoritative multi-dataset collection is:

```json
{
  "raw_payload": {
    "assembler_captures": [
      {
        "dataset_key": "GSTIN|GSTR-1|JUNE (FY 2026-27)",
        "filing_type": "GSTR-1",
        "period_label": "June (FY 2026-27)"
      },
      {
        "dataset_key": "GSTIN|GSTR-3B|JUNE (FY 2026-27)",
        "filing_type": "GSTR-3B",
        "period_label": "June (FY 2026-27)"
      }
    ]
  }
}
```

The dataset key is normalized from `GSTIN/PAN + filing type + period`. Revisiting the same form and period updates that dataset; a different form or period remains a separate capture. The desktop listener decompresses the optional `filing_result_compressed` envelope, and `main.py` materializes each `assembler_captures` item as its own `tracker_dump` row.

## Known Issue — Desktop Delivery / Multi-Dataset Visibility

**Recorded:** 2026-09-03  
**Symptom:** The tracker dump shows no separate rows for multiple GST forms or periods.  
**Observed cause:** The desktop listener was not listening on `127.0.0.1:49152`; additionally, the compressed-payload decoder previously referenced `base64` without importing it. In either case the final assembler envelope could be dropped before reaching the dataset-expansion code.  
**Expected behavior:** The desktop app must be running with the updated `ui/extension_listener.py`, the extension must be reloaded, and port `49152` must be listening before the final logout/timeout flush.  
**Verification:** Confirm the browser console shows a final assembler dispatch, confirm the desktop listener accepts the payload, then inspect `raw_payload.assembler_captures` and the resulting tracker rows.  
**Status:** Source fix applied; packaged builds must be rebuilt/restarted before production testing.

---

## ITR Protocol Crosshairs (7-Crosshair Active Map)

| # | Crosshair ID | Route Pattern | Captured Scope & Status | Priority |
| :-: | :--- | :--- | :--- | :-: |
| **1** | `itr_filed_verified` | `fo-e-verify-now-success`, `fo-return-success`, `e-verify.*success` | **`Filed & Verified`**: Captures 15-digit ACK, AY, Form, Date | 1st |
| **2** | `itr_submitted_pending` | `fo-e-verify-later`, `complete-verification`, `fo-verify-later` | **`Submitted (Pending e-Verification)`**: Captures Ack Number, AY, Form | 2nd |
| **3** | `itr_view_filed_returns` | `view-filed-returns`, `itr-status`, `fo-view-filed-returns` | **Ledger Extractor**: Scans return card/table, evaluates card milestone timeline via `_resolveCardFilingStatus()` | 3rd |
| **4** | `itr_personal_info` | `personal_information`, `myProfile`, `profileDetail`, `parta_gen` | **Authoritative Identity**: Extracts legal full name (`FirstName + MiddleName + SurName`), DOB, PAN | 4th |
| **5** | `itr_form_select` | `fo-select-itr-form`, `fo-lets-get-started` | **Form Intent**: Captures ITR Form (ITR 1–7), AY, Section (139(1), 139(5)) | 5th |
| **6** | `itr_landing` | `fileincometaxreturn`, `filereturn`, `dashboard`, `welcome` | **Landing Badge**: Captures PAN, AY, Filing Mode, Header Name Badge | 6th |
| **7** | `itr_login` | `#/login`, `#/logout`, `#/sign-in`, `#/password`, `#/session-expired` | **Session Boundary Guard**: Pre-login PAN extraction & session boundary seal | 7th |

### View Filed Returns Dynamic Milestone Resolver
On `view-filed-returns`, the protocol analyzes the visual milestone markers inside each card to prevent false-positive verifications:
- `"Processed with no demand/refund"` / `"Processed"` $\rightarrow$ **`Filed & Verified (Processed)`**
- `"Successfully e-verified"` $\rightarrow$ **`Filed & Verified`**
- `"Pending for e-verification"` / `"e-Verify Later"` (without verified milestone) $\rightarrow$ **`Submitted (Pending e-Verification)`**
- `"Defective"` $\rightarrow$ **`Defective Notice Issued`**

---

## Name Extraction Strategy (3-Tier)

| Tier | Source | Notes |
| :--- | :--- | :--- |
| **T0** | Portal Session Storage (`sessionStorage`) | Fast JSON profile cache inspection |
| **T1** | DOM Form Controls (`FirstName`, `MiddleName`, `SurName/LastName`) | Authoritative un-truncated legal name |
| **T2** | Header Profile Badge (`#loginUsername`, aria-label, role node siblings) | Instant temporary name badge (`client_temp_name`) |
| **T3** | Page Text Composite Regex | Pattern match anchored near PAN or taxpayer tags |
