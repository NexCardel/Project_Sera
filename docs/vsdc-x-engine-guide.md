# Sera VSDC-X — UI Automation Exact-Text Capture Engine

**Subsystem:** OS-Level Zero-Footprint Sensor & Capture Pipeline (`core/vsdc/`)  
**Target Application:** Project Sera Desktop Core (`APP`)  
**Status:** Active (Production across GST and ITR Portals)  
**Version:** v2.9.9  
**Last Updated:** September 2026  

---

## 1. Executive Overview

**Sera VSDC-X** is an advanced OS-level sensor integrated into the **Visual Screen Data Capture (VSDC)** pipeline of Project Sera. While legacy VSDC reconstructs on-screen text probabilistically by capturing display pixels and running hardware-accelerated DirectML optical character recognition (`Windows.Media.Ocr`), **VSDC-X reads the Chromium accessibility tree directly** via native Windows UI Automation (`UIAutomationCore.dll`).

### Why VSDC-X Was Built
1. **Zero Optical Noise**: OCR inherently suffers from character confusion (e.g., `l`/`I` $\rightarrow$ `1`, `O` $\rightarrow$ `0`, kerning ligatures, anti-aliased font blurring). VSDC-X captures exact, character-perfect string values directly from browser accessibility nodes.
2. **Elimination of Multi-Pass Heuristics**: Complex heuristic cascades—such as the 7-tier legal name reconstruction cascade in `vsdc_name_parser.py`—are bypassed because names, GSTINs, PANs, and form labels arrive exactly as rendered by the browser.
3. **Modal & Stepper Transparency**: Reads seamlessly through Chromium modal popups (e.g., OTP dialogs, filing confirmation overlays) and Angular wizard steppers without requiring custom viewport coordinate cropping.
4. **Readonly Form Field Access**: Leverages the UIA `ValuePattern` interface to read text values from readonly `<input>` elements (such as full taxpayer legal names, dates of birth, and registration details) whose accessible `Name` attribute only exposes the field label.

---

## 2. Legal, Risk & Passive Observation Posture

> [!IMPORTANT]
> **Strict Passive Observation Risk Boundary**  
> VSDC-X operates in the **exact same legal and compliance risk tier** as legacy VSDC's DirectML OCR:
> - **Zero Process Injection**: Never enters the browser process space or DLL boundaries.
> - **Zero DOM / JavaScript Hooks**: Does not inject scripts, mutation observers, or prototypes into web pages.
> - **Zero Network Footprint**: Never intercepts, crafts, delays, or transmits HTTP/WebSocket requests.
> - **Zero Automated Interaction**: Never clicks buttons, fills inputs, focuses fields, or dismisses dialogs. All actions are performed by the human operator.
> 
> From the viewpoint of government portal anti-bot mechanisms and Terms of Service (ToS), Project Sera is completely invisible. VSDC-X is strictly a high-fidelity **read-only accessibility sensor**.

---

## 3. Architecture & Threading Model

```text
┌──────────────────────────────────────────────────────────────────────────────────┐
│                      Foreground Web Browser (Chrome / Edge)                      │
│            Displaying Government Portal (Income Tax 2.0 / GST Portal)            │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │ Windows UI Automation Tree
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                           vsdc_uia_text.py Subsystem                             │
│                                                                                  │
│   • Persistent Dedicated Worker Thread (ThreadPoolExecutor(max_workers=1))       │
│   • COM Apartment Initialization: comtypes.CoInitialize()                        │
│   • Timeout Guard: Hard 0.25s execution ceiling (prevents thread deadlocks)     │
│   • ValuePattern & Name Inspection: Extracts visible text & input values         │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │ Exact Text & Ordered Lines
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                           vsdc_router.py Dual Routing                            │
│                                                                                  │
│   1. Route Gater: Inspects active URL & Window Title (<0.5ms)                   │
│   2. Dual Capture:                                                               │
│      - Primary Sensor: vsdc_uia_text.read_page_text(hwnd) [Exact]               │
│      - Fallback Sensor: Windows.Media.Ocr DirectML Scan [Visual]                 │
│   3. Consensus Engine: Merges fields; highest confidence rank wins              │
│   4. Session Guard: Window-isolated session tracker (_WindowSession)            │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │ Assembled Dataset & Identity
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                          vsdc_assembler.py Aggregator                            │
│                                                                                  │
│   • Stores Form, AY, Filing Type (Original/Revised/Belated/Updated), Ack/ARN     │
│   • Captures Legal Name, DOB, Primary Phone & Email (direct to DB columns)       │
│   • Emits Canonical JSON Dataset with method: "VSDC-X_<route>"                   │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │ Emits Signal
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                     Desktop Application & HUD Overlay                            │
│                                                                                  │
│   • VSDCHudPill: Pulse-on-capture toast with "[VSDC-X]" source tag              │
│   • TrackerDumpWindow: Color-coded Material pill badges & SQLite record          │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### Threading & COM Apartment Isolation
Windows UI Automation COM objects (`IUIAutomation`, `IUIAutomationElement`) are apartment-bound. Uncoordinated cross-thread calls cause silent COM marshalling failures or indefinite hangs on complex web pages.

To ensure zero instability in the main PyQt6 GUI thread:
1. **Persistent Worker Pool**: `core/vsdc/vsdc_uia_text.py` uses a private single-worker `ThreadPoolExecutor` with `initializer=comtypes.CoInitialize`.
2. **Deterministic Timeouts**: Every page-read call executes with a hard timeout (`DEFAULT_TIMEOUT_SEC = 0.25s`). If Chromium takes longer to respond, the call aborts cleanly and the router falls back to OCR without blocking the frame polling loop.
3. **ValuePattern Extraction**: Inspects both `CurrentName` and the `ValuePattern` (`UIA_ValuePatternId = 10002`). This unlocks readonly HTML form fields where `CurrentName` is only the label (e.g. `"First Name"`) and the actual taxpayer text resides inside the value pattern.

---

## 4. GST Route Coverage & Protocol Mapping

VSDC-X is fully integrated across all GST portal workflows (`services.gst.gov.in`):

| Crosshair / Page | Extracted Metadata | Extraction Mechanism |
| :--- | :--- | :--- |
| **`gst_welcome_calendar`**<br>(Dashboard & Welcome Banner) | • Taxpayer Legal / Trade Name<br>• GSTIN / PAN<br>• Filing Preference (Quarterly / Monthly) | Reads welcome card and return preference toggle. Uses a 60-poll patience latch to allow dynamic preference widgets to finish rendering. |
| **`gst_form_details`**<br>(Form Preparation & Filing Table) | • Legal Name & Trade Name<br>• Financial Year & Tax Period<br>• Return Filing Status<br>• Statutory Form Type (GSTR-1, GSTR-3B, CMP-08) | UIA document-order elements provide adjacent `"Label"` / `"Value"` line pairs, matching `extract_gst_form_table()` seamlessly. |
| **`gst_filing_success`** &<br>**`gst_filing_file_success`** | • 15-character statutory GST ARN<br>• Submission Status (`Filed & Verified`)<br>• Submission Date & Timestamp | Reads inline success banners and Chromium modal popups. ARN and status are validated with cross-source consensus (`get_status_rank`). |

---

## 5. Income Tax (ITD 2.0) Route Coverage

VSDC-X provides complete coverage across the Income Tax e-Filing 2.0 portal (`eportal.incometax.gov.in`):

### 5.1 Page Heading Form Resolution
In the ITR filing wizard, URLs like `.../fo-itr-shared/fo-lets-get-started` do not include a form slug in the path. Furthermore, eligibility body text often mentions other forms (e.g. *"Assessees with business income should file ITR-3"*), which caused older regexes to mis-key ITR-4 filings.
- **`extract_itr_form_heading()`**: Reads the exact page header line (e.g., `"ITR 4 - (Income Tax Return 4)"`).
- **Priority Resolution**:
  $$\text{Form Type} = \text{extract\_itr\_form\_heading}() \;\succ\; \text{resolve\_itr\_form\_type\_from\_url}() \;\succ\; \text{loose text scan}$$

### 5.2 Statutory Filing Type Capture & u/s 139(8A) Flow
Statutory tax returns differentiate between **Form Type** (ITR-1 through ITR-7) and **Filing Type** (why the return is filed). Rather than schema-altering database migrations, VSDC-X stores the statutory filing type in `filing_preference` (sharing this column with GST Monthly/Quarterly):

- **Supported Types**:
  - `Original` (u/s 139(1))
  - `Revised` (u/s 139(5))
  - `Belated` (u/s 139(4))
  - `Updated` (u/s 139(8A))
- **`extract_itr_filing_type()`**: Prioritizes statutory section anchors (`"139(8A) - Updated Return"`) before inspecting explicit `"Filing Type"` label blocks. Prevents label confusion such as `"Acknowledgement Number of Original Return"` being misclassified as an `Original` filing during revised submissions.
- **Dedicated Crosshairs**:
  - `itr_offline_json_upload`: Non-terminal route (`.../offlineJsonSubmission`). End-anchored to prevent premature ack capture.
  - `itr_offline_json_submit`: Captures the terminal acknowledgement and mandatory verification following offline upload.

### 5.3 Personal Info & Authoritative Identity Capture
On `personal_information` and profile pages:
- Extracts authoritative, untruncated legal name, Date of Birth (DOB), primary mobile, and primary email via `ValuePattern`.
- Enforces a 60-poll patience latch allowing asynchronous profile sub-cards to paint before route locking.
- Truncated header pill names (e.g. `"INDRAJIT CHATTE..."`) yield to full profile names.
- Contact info flows exclusively into database columns (`clients.phone`, `clients.email`) and is **strictly barred from logs, HUD toasts, and telemetry**.

### 5.4 False Submission Protection on Filing Wizards
Prior to hardening, intermediate wizard pages (such as `fo-schedules-summary` and `fo-filing-status-questionnaire` during revised returns) caused premature captures because previous returns' acknowledgment numbers appeared on-screen.
- **Terminal Submission Gating**: 15-digit ITR Ack extraction is strictly restricted to `is_terminal_submission` crosshair routes (`itr_filed_verified`, `itr_submitted_pending`, `itr_offline_json_submit`).
- **View Filed Returns Scoping**: Only extracts the topmost/latest return card on `view-filed-returns`, preventing historical `"Processed"` cards from overriding a newly submitted `"Pending for e-verification"` status.

---

## 6. Window-Isolated Session Architecture (`_WindowSession`)

To support multi-monitor, multi-window workflows where tax practitioners navigate GST on one window and ITR on another:
- **`_WindowSession`**: State is tracked per native window handle (`hwnd`).
- **Zero Cross-Window Bleeding**: Identity, form context, route latching, and capture buffers remain strictly encapsulated per window.
- **Time-Based Static Polling Cutoff**: Replaced fragile poll-count limits with a 30-second wall-clock timeout (`_STATIC_SCREEN_TIMEOUT_SEC = 30.0s`). Polling automatically resets when the page signals loading completion or visual changes occur.

---

## 7. Ambient Desktop Feedback & Visual HUD Pill

VSDC-X updates the desktop overlay pill (`VsdcHudPill`):
- **Hidden While Polling**: Zero visual distraction during passive background watching.
- **Pulse-on-Capture**: On any confirmed identity or filing capture, the pill fades in, expands with a 250ms pulse, displays client details, and auto-dismisses.
- **Source Attribution Tag**: Clearly displays whether data was captured via **`[VSDC-X]`** (Exact UIA) or **`[VSDC]`** (DirectML Visual OCR).
- **Persistent Attribution**: Persists `capture_method` in SQLite as `VSDC-X_<crosshair_id>` and highlights rows with dedicated cyan badges in Tracker Dump.

---

## 8. Diagnostic Tooling & Test Harness

| Tool Script | Location | Purpose |
| :--- | :--- | :--- |
| `vsdc_uia_probe.py` | `tools/` | Inspects and prints the entire accessible UI Automation tree of the active browser window. |
| `vsdc_console_watch.py` | `tools/` | Runs the standalone VSDC-X capture pipeline in the console with live event output without launching the full GUI. |
| `vsdc_x_modal_test.py` | `tools/` | Simulates Chromium modal dialogs and validates UIA tree traversal through modals. |
| `vsdc_x_router_gap_test.py` | `tools/` | Executes end-to-end routing simulations against simulated GST submission pages. |
| `vsdc_x_itr_router_gap_test.py` | `tools/` | Tests ITR wizard form heading detection, filing type extraction, and terminal submission gating. |
| `VSDC_UIA_ONLY=1` | Environment Variable | Diagnostic flag forcing the router to discard OCR text and run 100% on UIA text. |
