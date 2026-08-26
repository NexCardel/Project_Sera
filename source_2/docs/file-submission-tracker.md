# Sera FST — File Submission Tracker & Lifecycle Intelligence Engine

**Sera FST (File Submission Tracker)** is the complete file submission tracking, verification, audit, and lifecycle analysis subsystem in Project Sera. It automatically intercepts, captures, validates, resolves taxpayer identities, and logs tax return filings, statutory forms, e-verifications, Ack/ARN numbers, JSON response payloads, and submission timestamps into the vault's `tracker_dump` (SQLite / `rawPayload.db`).

In addition to live browser-level interception, Sera FST includes the **FST Classifier Engine (`FST_Classifier_1`)**, an automated analytical module that performs cross-entry lifecycle correlation, multi-session deduplication, and generates formatted audit spreadsheets (`payload_report.xlsx`).

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
            ┌───────────────────────┴───────────────────────┐
            │                                               │
            ▼                                               ▼
┌───────────────────────────┐                 ┌───────────────────────────┐
│  Tracker Dump Workspace   │                 │   FST Classifier Engine   │
│  (TrackerDumpWindow UI)   │                 │    (FST_Classifier_1)     │
│  • SRPF Containers        │                 │  • Temporal Identity Map  │
│  • Monospace Timeline     │                 │  • 7-Category Correlation │
│  • Floating Preferences   │◄───────────────►│  • Live File Watcher      │
│    (QMenu / Tools Panel)  │                 │  • Formatted Excel Report │
└───────────────────────────┘                 └───────────────────────────┘
```

---

## 2. Detection Tiers & Interception Engines

Sera FST employs a multi-tier detection architecture to ensure 100% filing capture reliability across all government tax portals:

### Tier 1: Sera SAD — API Detector (Network Layer — v2.7.4)
`net_interceptor.js` is injected into the web page's `MAIN` execution world at `document_start`. It passively intercepts `window.fetch()` and `XMLHttpRequest` JSON responses without modifying or delaying page network traffic:

* **Asynchronous `Blob` & `ArrayBuffer` Stream Decoding**: Directly unpacks `responseType: "blob"` and `responseType: "arraybuffer"` payloads using `blob.text()` and `TextDecoder('utf-8')`. Prevents browser `DOMException` errors on Angular SPAs and captures file downloads in-flight.
* **Monolithic Computational Document Guard**: Detects complete tax return schema objects (e.g. `/returns/downloadfile`, `ITR`, `ScheduleBP`, `Form_ITR4`, `CreationInfo`) and preserves the entire 100% root computational dataset (Turnover u/s 44AD, Cash, Bank balance, Debtors, Inventory, Deductions, and Tax computations) as a single un-truncated capture without splitting internal sub-arrays.
* **Income Tax Department (ITD 2.0)**:
  - **Live Returns & E-Verification**: Intercepts `/iec/itrweb/auth/v0.1/returns/submit/wzrd`, `/iec/verificationservices/auth/validateOTP`, `/iec/itrweb/auth/v0.1/returns/downloadfile`, and `/iec/servicesapi/auth/getEntity`.
  - **ITD Key Normalization**:
    - `"assmentYear": "2026"` $\longrightarrow$ Automatically formatted as **`AY 2026-27`**.
    - `"formTypeCd": "4S"` / `"formTypeCd": "4"` $\longrightarrow$ Formatted as **`ITR-4S`** / **`ITR-4`**.
    - `"submitUserId": "AHJPR0846B"` $\longrightarrow$ Extracted as **`PAN: AHJPR0846B`**.
    - `"statusDesc"` $\longrightarrow$ Captured as **`ITR processed no demand no refund`** / **`Filing Submitted`**.
* **GST Portal (`services.gst.gov.in`)**:
  - Detects `status_cd: "1"`, `error_cd: null`, extracting `arn`, `rtn_type` (GSTR-1, GSTR-3B, CMP-08, GSTR-9), `ret_period`, `gstin`, and `auth_name`.
* **TRACES Portal (`tdscpc.gov.in`)**:
  - Detects `status: "SUCCESS"`, extracting `requestNo` / `ticketNo` / `tokenNo` for Conso files, Justification reports, and Form 16/16A requests.
* **Universal Array Discovery (`findReturnArrays`)**:
  - When staff view **"View Filed Returns"** or the **Return Dashboard**, SAD recursively searches the JSON response for arrays of return objects, automatically parsing and logging all past assessment years in one pass.
* **Service List Scoped Execution**:
  - **Zero Overhead on Non-Portal Domains**: SAD checks `window.location.hostname` against the configured compliance service catalog. On non-portal websites, SAD stays completely idle.
* **Strict Validation Filter (`isValidArnOrAck`)**:
  - Automatically rejects placeholder dummy strings (`_ARN`) and internal tokens, ensuring only genuine 10–15 digit Ack numbers and 15-character GST ARNs are stored.

---

### Tier 2: Sera DOM — DOM Detector (Visual Layer)
`tracker.js` runs in the content script world as a visual fallback:
* Watches rendered HTML trees via `MutationObserver` for on-screen confirmation banners (*"Submitted successfully"*, *"Acknowledgement Number: ..."*).
* Activates when legacy portals or server-rendered HTML pages render confirmation screens without background JSON APIs.

---

## 3. External Module: FST Classifier Engine (`FST_Classifier_1`)

The **FST Classifier Engine** ([`FST_Classifier_1/fst_classifier.py`](file:///c:/Users/Nex/Downloads/Project%20Sera/APP/FST_Classifier_1/fst_classifier.py)) is an advanced analysis subsystem designed to process raw payload dump streams (`seraRawPayloadDump.txt`) or database tables into an executive Excel audit report (`payload_report.xlsx`).

### A. The 7 Lifecycle Classification Categories

| Category Code | Category Label | Highlight Color | Definition & Qualification Rules |
| :--- | :--- | :--- | :--- |
| **Cat 1** | **1. File Submitted (NOT E-Verified)** | 🟡 `FFF2CC` (Yellow) | The ITR wizard submitted successfully (`/returns/submit/wzrd` with `httpStatus: "ACCEPTED"` and `successFlag: true`), but has **no** matching OTP validation in the dump (`evc: null`). |
| **Cat 2** | **2. File Submitted & E-Verified (ITR)** | 🟢 `E2EFDA` (Green) | The return filing has completed full statutory e-verification (`/validateOTP` returned `"status": "SUCCESS"` with `"moduleCode": "ITR"` and message `"OTP VALIDATED"`). Correlates with submit events or standalone e-verification sessions. |
| **Cat 3** | **3. Bank Account E-Verified (NO Return Submitted)** | 🔵 `DDEBF7` (Blue) | Bank account validation form (`FO-091-EVERI`) was authenticated via Aadhaar OTP (`moduleCode: "NON-ITR"`), but no income tax return was submitted during the session. |
| **Cat 4** | **4. GST Return Filed & E-Verified** | 🟢 `E2EFDA` (Green) | GST return (e.g., GSTR-1, GSTR-3B) was successfully submitted and authenticated via EVC on the GST Portal (`status: "FIL"`, `evc_chk: "E"`). |
| **Cat 5** | **5. Bank Status & Pre-Validation Matrix** | Dynamic (Green/Yellow/Red/Gray) | Evaluates all linked bank accounts into 4 accurate sub-states: <br>• **Validated (Valid & Open, Nominated for Refund)** (Green)<br>• **Validated with Warning (Name Mismatch / <50L Cap)** (Yellow)<br>• **Inactive / Legacy Account (Merged/Closed Bank, ActiveFlag: D)** (Gray)<br>• **Revalidation Required (NPCI Rejected, No Such Account)** (Red) |
| **Cat 6** | **6. Visited But No Return Submission** | ⚪ `EDEDED` (Gray) | Identifies taxpayers who logged in, checked past records, or synced profiles, but performed **zero** ITR or GST submissions during the session. |
| **Cat 7** | **7. Visited Site (All Visits Enumerated)** | ⚪ `F2F2F2` (Light Gray) | Full chronological interaction footprint enumerating every API step, timestamp, and sub-service endpoint accessed by each taxpayer. |

---

### B. Mathematical Identity Resolution (Bypassing Unstable Client IDs)
Because modern portal frontends rotate tokens and fragment single human logins into multiple temporary session IDs, the Classifier does **not** rely on `Client ID`. Instead, it uses a two-pass algorithm:

1. **Pass 1 — Absolute Anchor Extraction**:
   Scans all payloads for explicit `PAN`s and `Acknowledgement Numbers`. Builds an immutable lookup table:
   $$\text{AckMap}: \text{AckNumber} \longrightarrow \text{PAN}$$
2. **Pass 2 — Retroactive & Temporal Linking**:
   When encountering anonymous submission events (like `submit/wzrd` where `PAN` is blank):
   * *Step A (Retroactive Link)*: If the generated Ack appears in $\text{AckMap}$ (e.g. from an e-verification or status fetch), it locks to that PAN.
   * *Step B (Temporal Context Window)*: If unreferenced, it binds to the active chronological PAN stream currently being operated on the interceptor channel.
3. **Deep Assessee Name Extraction**:
   Extracts legal names from deep nested objects in ITR JSON payloads (`rp["ITR"]["ITR4"]["PersonalInfo"]["AssesseeName"]` and `["Verification"]["Declaration"]["AssesseeVerName"]`), ensuring full names like *MOHAMMAD KAMARUJJAMAN MOLLA* are accurately populated.

---

### C. Live Watcher & Desktop App Integration

* **Continuous Live Tracking (`--watch`)**:
  Monitors `seraRawPayloadDump.txt` via non-blocking file polling. Automatically rebuilds `payload_report.xlsx` whenever the interceptor appends new entries.
* **In-App Preferences Integration**:
  The desktop **Tracker Dump Workspace** exposes a direct trigger button inside the **`Preferences`** menu (`mdi.file-excel`), compiling and opening the report instantly in Microsoft Excel.

---

## 4. Tracker Dump Workspace (`TrackerDumpWindow`)

All captured filings and API dumps are logged directly to SQLite table `tracker_dump` (in `rawPayload.db`) and presented in the desktop **Tracker Dump** workspace:

- **Zero-Interrupt Background Logging**: Eliminates intrusive modal prompts; captures save silently.
- **Desktop Toast Alerts**: Non-intrusive 5-second green toasts alert staff upon capture (`Captured Income Tax (ITR-4) — ARN: 125873710140314`).
- **SRPF Containerization**: Aggregates all fragmented submissions, profile lookups, bank validations, and wizard interactions belonging to the same entity into a single unified client container.
- **Floating Preferences Menu Widget**:
  The header card consolidates all utilities into a floating **`Preferences`** menu (`mdi.cog-outline`):
  - 📄 **Open Dump (TXT)** (`mdi.file-document-outline`)
  - 🔄 **Rebuild TXT Dump** (`mdi.file-sync-outline`)
  - 🗄️ **Re-Resolve Identities (SRPF)** (`mdi.database-sync`)
  - 📊 **FST Classifier (Excel Report)** (`mdi.file-excel`)
  - 📤 **Export Captures (CSV)** (`mdi.file-export`)
  - 🧹 **Clear All Captures** (`mdi.delete-sweep`)
- **Session Audit Timeline Decoder (Inspector Tab 3)**:
  - Translates raw API payload sequences into an interactive, plain-English chronological narrative.
  - Partitions multi-visit histories into distinct **Session Blocks** with independent `T+00s` offset baselines.
  - Automatically collapses repetitive operations with clickable expanders (`[ ▶ Expand 7 occurrences ]`).
  - Strict classification distinguishing profile state saves (`PROFILE-*`) from genuine return e-verifications.

---

## 5. Supported Submission & Transaction Types Matrix

| Portal | Action / Transaction | Captured Identifier | Output Format | Classifier Category |
| :--- | :--- | :--- | :--- | :--- |
| **Income Tax** | ITR-1 to ITR-7 Return Filing (Unverified) | 15-digit Ack Number | `Income Tax (ITR-4)` | **Cat 1: Submitted Unverified** |
| **Income Tax** | ITR E-Verification (Aadhaar OTP / EVC) | 15-digit Ack Number | `Income Tax (ITR-4)` | **Cat 2: Submitted & Verified** |
| **Income Tax** | Bank Account Re-Validation (`FO-091-EVERI`) | Bank Ack Number | `Income Tax (FO-091)` | **Cat 3: Bank Verified No Return** |
| **Income Tax** | Bank Account Status & Pre-Validation | `BANK-<PAN>-<ID>` | `Income Tax (Bank)` | **Cat 5: Bank Status Matrix** |
| **Income Tax** | Taxpayer Profile & Contact Sync | `PROFILE-<PAN>` | `Income Tax (Profile)` | **Cat 6 / Cat 7: Visited Site** |
| **Income Tax** | Statutory Forms (10-IEA, 10BA, 29B, 15CA/CB) | `acknowledgementNumber` | `Income Tax (Form 10-IEA)` | **Cat 2 / Cat 4** |
| **Income Tax** | Rectification Request (Sec 154) | `rectificationReferenceNo` | `Income Tax (Rectification)` | **Cat 2** |
| **GST Portal** | Monthly/Quarterly Returns (GSTR-1, 3B, CMP-08, 9) | 15-character ARN | `GST Portal (GSTR-1)` | **Cat 4: GST Filed & E-Verified** |
| **GST Portal** | Payment Challan (PMT-06) | 14-digit CPIN | `GST Portal (PMT-06)` | **Cat 4** |
| **TRACES** | Conso File / Justification Report | `requestNo` | `TRACES Portal` | **Cat 2** |

---

## 6. Execution & Operational Commands

### A. Run FST Classifier in Live Watcher Mode
```cmd
cd "C:\Users\Nex\Downloads\Project Sera\APP\FST_Classifier_1"
python fst_classifier.py "..\seraRawPayloadDump.txt" "payload_report.xlsx" --watch
```
*(Or simply double-click `FST_Classifier_1\run.bat`)*

### B. Launch from Sera Desktop UI
Open **Tracker Dump Workspace** ➔ Click **`Preferences`** ➔ Select **`FST Classifier (Excel Report)`**.

### C. Live Browser Interceptor Test (`F12` Console)
```javascript
fetch('data:application/json,' + encodeURIComponent(JSON.stringify({
    status: "SUCCESS",
    acknowledgementNumber: "982348123456789",
    formName: "ITR-4",
    assessmentYear: "2026-27",
    pan: "AHJPR0846B"
})));
```
