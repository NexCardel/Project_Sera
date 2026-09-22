# Sera FST — File Submission Tracker & Lifecycle Intelligence Engine

**Sera FST (File Submission Tracker)** is the complete file submission tracking, verification, audit, and lifecycle analysis subsystem in Project Sera. It automatically intercepts, captures, validates, resolves taxpayer identities, and logs tax return filings, statutory forms, e-verifications, Ack/ARN numbers, JSON response payloads, and submission timestamps into the vault's `tracker_dump` (SQLite / `rawPayload.db`).

In addition to live browser-level interception, Sera FST includes the **FST Classifier Engine (`FST_Classifier_1`)**, an automated analytical module that performs cross-entry lifecycle correlation, multi-session deduplication, and generates formatted audit spreadsheets (`payload_report.xlsx`).

---

## 1. System Architecture & Data Flow

```text
┌────────────────────────────────────────────────────────────────────────┐
│                         Government Web Portals                         │
│            (Income Tax 2.0, GST Portal, TRACES, State Tax)             │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                  ┌─────────────────┴─────────────────┐
                  │ (Browser Layer)                   │ (OS Native Layer)
                  ▼                                   ▼
      ┌───────────────────────┐           ┌───────────────────────┐
      │  Sera SDC Crosshairs  │           │  Sera VSDC / VSDC-X   │
      │  • gst_protocol.js    │           │  • vsdc_uia_text.py   │
      │  • itr_protocol.js    │           │  • vsdc_router.py     │
      │  • sdc_core.js        │           │  • vsdc_assembler.py  │
      └───────────┬───────────┘           └───────────┬───────────┘
                  │                                   │
                  │ background.js's WebSocket Bridge  │ Direct In-Process
                  │ (ports 48765-48768)               │ Qt Signal
                  ▼                                   ▼
      ┌───────────────────────┐           ┌───────────────────────┐
      │    ui/ws_bridge.py    │           │ main.py Desktop Core  │
      │ (QWebSocketServer)    │──────────►│ • Identity Resolution │
      └───────────────────────┘           │ • Window Session Latch│
                                          │ • tracker_dump Insert │
                                          │ • VsdcHudPill Overlay │
                                          └───────────┬───────────┘
                                                      │
                            ┌─────────────────────────┴─────────────────────────┐
                            │                                                   │
                            ▼                                                   ▼
                ┌───────────────────────────┐                       ┌───────────────────────────┐
                │  Tracker Dump Workspace   │                       │   FST Classifier Engine   │
                │  (TrackerDumpWindow UI)   │                       │    (FST_Classifier_1)     │
                │  • Material Status Badges │                       │  • Temporal Identity Map  │
                │  • Cell Darkened Badges   │◄─────────────────────►│  • 7-Category Correlation │
                │  • Token Meter & JSON     │                       │  • Formatted Excel Report │
                └───────────────────────────┘                       └───────────────────────────┘
```

> [!NOTE]
> **Permanent Retirement of Sera SAD & SDS**:
> - **Sera SAD** (Network API Interceptor / `net_interceptor.js`) has been permanently retired and completely removed from the workspace. All network response hooking has been eliminated in favor of passive DOM crosshairs (SDC) and OS-level optical/accessibility reading (VSDC / VSDC-X).
> - **Sera SDS** (Dataset Scanner / `sds_core.js`) has also been permanently retired.

---

## 2. Detection Tiers & Interception Engines

Sera FST employs a resilient, zero-footprint multi-tier detection architecture across government tax portals:

### Tier 1: Sera SDC — Smart DOM Crosshairs (Browser Extension Layer)
`sera_extension/sdc/` runs in the isolated content script world. SDC sleeps completely on non-target routes and wakes exclusively when the URL matches a registered tax portal crosshair:
* **Session Assembler (`sdc_core.js`)**: Buffers all multi-step filing fragments throughout a taxpayer session into an isolated, portal-scoped storage space (`__SDC_SESSION_ITR__`, `__SDC_SESSION_GST__`).
* **WebSocket Bridge Delivery**: Hands atomic master session payloads to `background.js` (`chrome.runtime.sendMessage`), which forwards them over its WebSocket connection to the desktop app (`ui/ws_bridge.py`) and waits for an acknowledgement before clearing the durable outbox.
* **Multi-Dataset Collection**: Normalizes datasets by `GSTIN/PAN + form + period`. A single taxpayer session filing both GSTR-1 and GSTR-3B records distinct rows without overwriting prior filings.
* **Protocols**:
  - `itr_protocol.js`: Full 7-crosshair mapping across Income Tax 2.0 (landing, form select, personal info, view filed returns, submitted pending, filed & verified).
  - `gst_protocol.js`: Full mapping across GST Portal (welcome calendar, form details table, filing success, modal popups).

### Tier 2: Sera VSDC & VSDC-X (OS-Level Zero-Footprint Sensor Layer)
`core/vsdc/` operates purely from the desktop layer without entering the browser process, injecting scripts, or touching web network traffic:
* **VSDC-X Direct UI Automation Sensor (`vsdc_uia_text.py`)**: Reads the Chromium accessibility tree directly via native Windows UI Automation (`UIAutomationCore.dll`). Eliminates character confusion and OCR noise on complex web fonts.
* **DirectML Visual OCR (`vsdc_ocr.py`)**: Hardware-accelerated offline fallback running on DirectML (`Windows.Media.Ocr`) for legacy or non-standard visual layouts.
* **ITR Heading Resolution & Statutory Filing Type**: Extracts statutory form type from page headings ahead of wizard URLs, and extracts statutory filing reasons (`Original`, `Revised`, `Belated`, and `Updated u/s 139(8A)`) into `filing_preference`.
* **Personal Info & Readonly Input Capture**: Uses UIA `ValuePattern` to extract authoritative un-truncated legal names, Date of Birth (DOB), primary phone, and primary email directly into database columns with strict privacy safeguards.
* **Terminal Submission Gating**: Strictly gates 15-digit ITR Ack extraction to terminal submission crosshairs, completely preventing intermediate wizard pages (like schedules or questionnaires) from minting premature false submission records.
* **Window-Isolated Session Tracking (`_WindowSession`)**: Prevents multi-window and cross-portal state contamination.
* **Dynamic HUD Pill Overlay (`VsdcHudPill`)**: Desktop overlay stays hidden while polling, pulses on capture (250ms expansion), and tags engine attribution (`[VSDC-X]` vs `[VSDC]`).
* **Zero-Leakage PAN Beeper & Gemini AI Enricher**: Zero-leakage audible feedback on PAN detection; optional Gemini Flash 1.5/2.0 structured compliance enricher with live in-app Token Meter.

### Tier 3: Sera DOM Tracker — Legacy Visual Fallback
`tracker.js` runs as a MutationObserver fallback for legacy or server-rendered HTML government portals that do not support modern crosshair navigation.

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

### C. Direct SQLite & Desktop App Integration

* **Direct SQLite Ingestion (`rawPayload.db`)**:
  Processes real-time entries directly from the local `rawPayload.db` SQLite database, eliminating unnecessary intermediate disk text dumps (`seraRawPayloadDump.txt`).
* **In-App Preferences Integration**:
  The desktop **Tracker Dump Workspace** exposes a direct trigger button inside the **`Preferences`** menu (`mdi.file-excel`), compiling and opening the report instantly in Microsoft Excel.

---

## 4. Tracker Dump Workspace (`TrackerDumpWindow`)

All captured filings and API dumps are logged directly to SQLite table `tracker_dump` (in `rawPayload.db`) and presented in the desktop **Tracker Dump** workspace:

- **Zero-Interrupt Background Logging**: Eliminates intrusive modal prompts; captures save silently to encrypted SQLite.
- **Desktop Toast Alerts**: Non-intrusive 5-second green toasts alert staff upon capture (`Captured Income Tax (ITR-4) — ARN: 125873710140314`).
- **SRPF Containerization**: Aggregates all fragmented submissions, profile lookups, bank validations, and wizard interactions belonging to the same entity into a single unified client container.
- **Color-Coded Status Badges & Pills**:
  The table renders filing statuses as stylized, color-coded Google Material pill badges (`mdi.*`) with strict monotonic status protection:
  - 🟢 **Verified / Processed**: `mdi.check-decagram` (Emerald Green `#00E676` / `#052e16`) for `"Filed & Verified (Processed)"`, `"Filed & Verified"`, `"Filed"`, `"e-Verified"`.
  - 🟡 **Pending e-Verification**: `mdi.clock-alert-outline` (Amber Yellow `#FFD600` / `#2e2305`) for `"Submitted (e-verification pending)"`, `"Submitted (Pending e-Verification)"`, `"Submitted"`.
  - 🔵 **e-Verification Action**: `mdi.shield-check-outline` (Blue `#40C4FF` / `#082f49`) for `"e-Verification completed"`, `"Bank Account e-Verified"`.
  - 🟣 **Bank Validated**: `mdi.bank-check` (Purple `#E040FB` / `#2e1065`) for `"Validated (Active & Nominated for Refund)"`.
  - 🟠 **In Progress / Draft**: `mdi.progress-clock` (Orange `#FF9100` / `#381a05`) for `"In Progress"`, `"Draft"`, `"Processing"`.
  - 🔴 **Failed / Rejected**: `mdi.alert-octagon-outline` (Red `#FF5252` / `#450a0a`) for `"Failed"`, `"Rejected"`, `"Defective"`.
  - ⚪ **Default / Visited**: `mdi.information-outline` (Neutral Slate `#94A3B8` / `#1e293b`).
- **Floating Preferences Menu Widget**:
  The header card consolidates utilities into a floating **`Preferences`** menu (`mdi.cog-outline`):
  - 🗄️ **Re-Resolve Identities (SRPF)** (`mdi.database-sync`)
  - 📊 **FST Classifier (Excel Report)** (`mdi.file-excel`)
  - 📑 **DOM Parser 1 (Excel Report)** (`mdi.file-table-outline`)
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
 
### A. Run FST Classifier Against SQLite Database
```cmd
cd "C:\Users\Nex\Downloads\Project Sera\APP\FST_Classifier_1"
python fst_classifier.py "..\rawPayload.db" "payload_report.xlsx"
```

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
