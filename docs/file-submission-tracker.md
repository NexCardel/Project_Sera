# File Submission Tracker (FST) & Sera API Detection (SAD)

The File Submission Tracker (FST) and Sera API Detection (SAD) engine automatically capture and record tax return filings, Ack/ARN numbers, network response payloads, and submission timestamps directly into the vault's `tracker_dump` and `filing_status` tables.

---

## Workflow & Architecture

```text
[Desktop App: main.py] -- Autofill Payload --> [Native Host / TCP 49153] --> [Extension: background.js]
                                                                               |
                                                                               v
                                                                       Injects net_interceptor.js
                                                                               |
                                                                               v
[tracker_dump & filing_status DB] <-- TCP 49152 -- [ExtensionListener] <-- [net_interceptor.js in MAIN World]
        ^                                                                      |
        |                                                                      v
[Tracker Dump Workspace UI] <--- Real-time reload signal <--- CustomEvent (SeraFSTApiCapture)
```

---

## Detection Tiers

### Tier 1: Sera API Detection (SAD) — Passive Network Interceptor
`net_interceptor.js` is injected into the web page's MAIN execution world. It passively intercepts `window.fetch()` and `XMLHttpRequest` responses without modifying or delaying page network traffic:

- **GST Portal (`*.gst.gov.in`)**: Detects `status_cd: "1"` / `error_cd: null` and extracts `arn` / `ack_num` / `data.arn`.
- **Income Tax Portal (`*.incometax.gov.in`)**: Detects `status: "SUCCESS"` / `statusCode: 200` and extracts `acknowledgementNumber` / `itrAckNo` / `ackNo`.
- **TRACES Portal (`*.tdscpc.gov.in`)**: Detects `status: "SUCCESS"` and extracts `requestNo` / `ticketNo` / `tokenNo`.
- **Universal Fallback Engine**: Regex matching for 15-character ARNs (`AA270826...`), numeric Ack numbers, and common response tokens (`receiptNo`, `challanNo`, `submissionId`).

### Tier 2: DOM Observer Fallback (`tracker.js`)
If a portal uses static HTML rendering without JSON APIs, `tracker.js` monitors page DOM mutations for success banners and ARN text node patterns.

---

## Tracker Dump Subsystem

All captured network responses and filing results are logged directly to the `tracker_dump` database table and presented in the **Tracker Dump** workspace:

- **No Modal Interruptions**: Incoming filings are saved silently in the background without modal popups (`FilingConfirmationDialog` unhooked).
- **Toast Notifications**: Non-intrusive 5-second desktop toasts alert staff upon capture (e.g. `Captured GST Portal Filing (SAD API Interceptor) — ARN: AA270826...`).
- **Real-Time Workspace**: `TrackerDumpWindow` updates automatically, featuring multi-field search (Client Name, PAN, GSTIN, ARN, Period, Portal), method filters (`SAD_API_Interceptor`, `DOM_Tracker`, `Manual_Fallback`), raw JSON payload inspection, and CSV export.
- **Universal Client Resolution**: Maps incoming client parameters dynamically via Database Primary Key ID, `client_id_token` (`CLI-00370`), MCL Serial No (`No. 370`), or Name/PAN/GSTIN substring search.

---

## Verification & Testing Guide

1. **Direct Socket Test**:
   ```bash
   python tests/test_dump_injection.py [client_id_or_name] [portal_name]
   ```
2. **Interactive Simulation Bench**:
   Open [`tests/test_sad_interceptor.html`](file:///c:/Users/Nex/Downloads/Project%20Sera/APP/tests/test_sad_interceptor.html) in your browser, enter any target Client ID, Name, or PAN, and trigger simulated GST, Income Tax, or TRACES submissions.
