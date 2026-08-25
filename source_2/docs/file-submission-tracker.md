# Sera FST — File Submission Tracker

**Sera FST (File Submission Tracker)** is the complete file submission tracking, verification, and audit subsystem in Project Sera. It automatically captures, validates, and logs tax return filings, statutory forms, e-verifications, Ack/ARN numbers, JSON response payloads, and submission timestamps into the vault's `tracker_dump` and `filing_status` database tables.

---

## 1. System Architecture & Data Flow

```text
┌────────────────────────────────────────────────────────────────────────┐
│                         Government Web Portals                         │
│       (Income Tax 2.0, GST Portal, TRACES, State Tax Systems)          │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                  ┌─────────────────┴─────────────────┐
                  │                                   │
                  ▼                                   ▼
      ┌───────────────────────┐           ┌───────────────────────┐
      │  Sera SAD (API Layer) │           │  Sera DOM (DOM Layer) │
      │  net_interceptor.js   │           │  tracker.js           │
      │  (MAIN Execution      │           │  (MutationObserver    │
      │   World Hook)         │           │   Visual Fallback)    │
      └───────────┬───────────┘           └───────────┬───────────┘
                  │                                   │
                  │ CustomEvent (SeraFSTApiCapture)   │ chrome.runtime.sendMessage
                  ▼                                   │
      ┌───────────────────────┐                       │
      │  filing_detector.js   │                       │
      │  (ISOLATED Content    │                       │
      │   Script Bridge)      │                       │
      └───────────┬───────────┘                       │
                  │                                   │
                  └─────────────────┬─────────────────┘
                                    │
                                    ▼
                      ┌───────────────────────────┐
                      │   background.js (Worker)  │
                      └─────────────┬─────────────┘
                                    │ Native Messaging (stdio)
                                    ▼
                      ┌───────────────────────────┐
                      │   native_host/host.py     │
                      └─────────────┬─────────────┘
                                    │ TCP Loopback Socket (Port 49152)
                                    ▼
                      ┌───────────────────────────┐
                      │  ui/extension_listener.py │
                      │  (PyQt6 QThread Server)   │
                      └─────────────┬─────────────┘
                                    │ Qt Signal (filing_result_received)
                                    ▼
                      ┌───────────────────────────┐
                      │   main.py Desktop Core    │
                      │  • PAN & Token Resolution │
                      │  • DB tracker_dump Insert │
                      │  • Non-intrusive Toast    │
                      └─────────────┬─────────────┘
                                    │
                                    ▼
                      ┌───────────────────────────┐
                      │  Tracker Dump Workspace   │
                      │  (TrackerDumpWindow UI)   │
                      └───────────────────────────┘
```

---

## 2. Detection Tiers & Engines

Sera FST employs a two-tier detection architecture to ensure 100% filing capture reliability across all government tax portals:

### Tier 1: Sera SAD — API Detector (Network Layer)
`net_interceptor.js` is injected into the web page's `MAIN` execution world at `document_start`. It passively intercepts `window.fetch()` and `XMLHttpRequest` JSON responses without modifying or delaying page network traffic:

* **Angular `responseType: "json"` Compatibility**: Directly inspects `xhr.response` parsed objects when `xhr.responseType === "json"`, preventing browser `DOMException` errors on Angular SPAs.
* **Income Tax Department (ITD 2.0)**:
  - **Live Returns & E-Verification**: Intercepts `/iec/foservices/api/e-verify/submit`, `/iec/foservices/api/itr/everify`, and `/iec/servicesapi/auth/getEntity`.
  - **ITD Key Normalization**:
    - `"assmentYear": "2024"` $\longrightarrow$ Automatically formatted as **`AY 2024-25`**.
    - `"formTypeCd": "4S"` / `"formTypeCd": "3"` $\longrightarrow$ Automatically formatted as **`ITR-4S`** / **`ITR-3`**.
    - `"submitUserId": "BKAPM7233A"` $\longrightarrow$ Automatically extracted as **`PAN: BKAPM7233A`**.
    - `"statusDesc"` $\longrightarrow$ Captured as **`ITR processed no demand no refund`** / **`Filing Submitted`**.
* **GST Portal (`services.gst.gov.in`)**:
  - Detects `status_cd: "1"`, `error_cd: null`, extracting `arn`, `rtn_type` (GSTR-1, GSTR-3B, CMP-08, GSTR-9), `ret_period`, and `gstin`.
* **TRACES Portal (`tdscpc.gov.in`)**:
  - Detects `status: "SUCCESS"`, extracting `requestNo` / `ticketNo` / `tokenNo` for Conso files, Justification reports, and Form 16/16A requests.
* **Universal Array Discovery (`findReturnArrays`)**:
  - When staff view **"View Filed Returns"** or the **Return Dashboard**, SAD recursively searches the entire JSON tree for arrays of return objects, automatically parsing and logging all past assessment years in one pass.
* **Service List Scoped Execution**:
  - **Zero Overhead on Non-Portal Domains**: SAD checks `window.location.hostname` against the configured compliance service catalog. On unconfigured websites (general browsing, search engines, banking, etc.), SAD stays completely idle and does not hook network APIs.
  - **Dynamic Portal Synchronization**: When services are created, edited, or deleted in Sera's **Service Manager**, the allowed portal domains list is broadcast to the extension in real-time.
* **Strict Validation Filter (`isValidArnOrAck`)**:
  - Automatically rejects placeholder dummy strings (`_ARN`) and internal session tokens (`FOS009956...`), ensuring only genuine 10–15 digit Ack numbers and 15-char GST ARNs are stored.
* **Session Deduplication**:
  - Maintains an in-memory session cache (`Set`) to prevent repeated page requests or tab navigations from duplicating identical entries.

---

### Tier 2: Sera DOM — DOM Detector (Visual Layer)
`tracker.js` runs in the content script world as a visual fallback:
* Watches rendered HTML trees via `MutationObserver` for on-screen confirmation banners (*"Submitted successfully"*, *"Acknowledgement Number: ..."*).
* Activates when legacy portals or server-rendered HTML pages render full HTML pages without background JSON APIs.

---

## 3. Supported Submission & Transaction Types

| Portal | Action / Transaction | Captured Identifier | Output Format |
| :--- | :--- | :--- | :--- |
| **Income Tax** | ITR-1 to ITR-7 Return Filing | 15-digit Ack Number | `Income Tax (ITR-4)` |
| **Income Tax** | ITR E-Verification (OTP / EVC) | 15-digit Ack Number | `Income Tax (ITR-4)` |
| **Income Tax** | Statutory Forms (10-IEA, 10BA, 29B, 15CA/CB, 35) | `acknowledgementNumber` | `Income Tax (Form 10-IEA)` |
| **Income Tax** | Rectification Request (Sec 154) | `rectificationReferenceNo` | `Income Tax (Rectification)` |
| **Income Tax** | Response to Outstanding Demand | `responseReferenceNo` | `Income Tax (Demand Reply)` |
| **Income Tax** | E-Proceedings Notice Submission | `submissionId` | `Income Tax (Notice Reply)` |
| **Income Tax** | e-Pay Tax Advance/Self-Assessment Tax | `CRN` / Bank `CIN` | `Income Tax (Challan)` |
| **GST** | Monthly/Quarterly Returns (GSTR-1, 3B, CMP-08, 9) | 15-character ARN | `GST Portal (GSTR-3B)` |
| **GST** | Payment Challan (PMT-06) | 14-digit CPIN | `GST Portal (PMT-06)` |
| **GST** | Revocation (REG-21) / Amendment (REG-14) | `arn` / `ref_id` | `GST Portal (REG-21)` |
| **GST** | Refund Application (RFD-01) | `arn` / `ack_num` | `GST Portal (RFD-01)` |
| **TRACES** | Conso File / Justification Report | `requestNo` | `TRACES Portal` |
| **TRACES** | Form 16 / 16A Bulk Request | `requestNo` | `TRACES Portal` |

---

## 4. Tracker Dump Workspace (`TrackerDumpWindow`)

All captured filings and API dumps are logged directly to SQLite table `tracker_dump` and presented in the desktop **Tracker Dump** workspace:

- **Zero-Interrupt Background Logging**: Eliminates intrusive modal prompts (`FilingConfirmationDialog` unhooked); captures save silently in the background.
- **Desktop Toast Alerts**: Non-intrusive 5-second green desktop toasts alert staff upon capture (`Captured Income Tax (ITR-4) (Sera SAD (API Detector)) — ARN: 125873710140314`).
- **Real-Time Search & Multi-Field Filters**: Search across Client Name, PAN, GSTIN, ARN, Period, or Portal, with filters for capture method (`SAD_API_Interceptor`, `DOM_Tracker`, `Manual_Fallback`) and status.
- **Raw JSON Payload Inspector Drawer**: Click **View Payload** on any row to inspect complete API response headers, timestamps, and nested data trees.
- **Data Management**: Delete individual rows or use **Clear All** for one-click workspace purging, plus **Export CSV** for firm audit records.
- **Universal Client Resolution**: Automatically matches client primary keys, `client_id_token` (`CLI-00370`), MCL Serial Numbers (`No. 370`), or extracted PAN (`GZEPM6367M`).

---

## 5. Verification & Testing

### A. Live Browser Console Verification (`F12`)
To test the active network interceptor on any browser tab without filing taxes:
```javascript
fetch('data:application/json,' + encodeURIComponent(JSON.stringify({
    status: "SUCCESS",
    acknowledgementNumber: "982348123456789",
    formName: "ITR-4",
    assessmentYear: "2024-25",
    pan: "ABCDE1234F"
})));
```

### B. Direct TCP Socket Injector (Python)
To test the desktop receiver, database insertion, and toast alert directly:
```bash
python tests/test_dump_injection.py 370 "Income Tax Portal"
```

