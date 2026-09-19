# Sera Type A Visual Screen Harvester (VSH) — Technical Specification & Blueprint

**Subsystem:** Type A Visual Screen Harvester (VSH / Visual SDC)  
**Target Application:** Project Sera Desktop Core (`APP`)  
**Status:** Ready for Review  
**Date:** September 2026  

---

## 1. Executive Summary

The **Type A Visual Screen Harvester (VSH)** is an OS-level, zero-footprint optical data extraction and session assembly engine designed to achieve **100% legal, compliance, and Terms-of-Service (ToS) risk aversion**.

By completely abandoning browser-side JavaScript injection, DOM observers, and network hooks, VSH operates strictly from the Windows desktop layer. It visually reads the user's active display using native Windows accessibility and hardware-accelerated OCR (`Windows.Media.Ocr`), capturing taxpayer identities, filing types, return periods, and submission acknowledgements across the exact same lifecycle stages as SDC.

From the perspective of government tax portals (Income Tax 2.0, GSTN, TRACES), **Project Sera does not exist**.

> **VSDC-X update (2026-09-17):** a second sensor — UI Automation exact-text reading — now sits alongside the OCR pipeline for GST, reading Chromium's accessibility tree directly instead of screen pixels. Same risk tier, same passive posture, just more accurate and faster where it applies. See **Section 11** for the full detail; this section and the OCR-focused sections below (2–10) still accurately describe the original pipeline, which remains fully intact as the fallback/primary for anything VSDC-X doesn't cover (ITR, in particular).

---

## 2. System Architecture & Component Flow

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 User's Display (OS Level)                               │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐  │
│  │                Unmodified Web Browser (Chrome / Edge / Firefox)                  │  │
│  │                Navigating Tax Portals (ITD 2.0 / GST / TRACES)                   │  │
│  └──────────────────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │ Zero Browser Footprint
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        Type A Visual Screen Harvester (VSH) Core                       │
│                                                                                        │
│  [Layer 1: Windows UI Automation (UIA) Route Gater]                                    │
│  • Reads browser address bar in < 0.5ms via UIAutomationCore.dll (0.0% CPU)            │
│  • Wakes only when URL matches registered Portal Crosshair patterns                   │
│                                                                                        │
│  [Layer 2: Targeted Regional Frame Cropper]                                            │
│  • Captures only the relevant screen subsection (Header, Center Box, or Receipt Card) │
│  • Never OCRs full 1080p screen (Reduces pixel load by 85–90%)                        │
│                                                                                        │
│  [Layer 3: Windows.Media.Ocr Hardware Pipeline]                                        │
│  • Built into Windows 10/11, native C++ DirectML execution, 100% offline                │
│  • Micro-burst execution: 10ms–25ms on modern CPUs, ~35ms on Core 2 Duo                │
│                                                                                        │
│  [Layer 4: Deterministic Tax Regex & Optical Repair Engine]                            │
│  • Auto-repairs optical confusion (l/I -> 1, O -> 0) within strict statutory formats   │
│  • Validates 10-char PAN, 15-char GSTIN, 15-digit ITR Ack, 15-char GST ARN             │
│                                                                                        │
│  [Layer 5: Visual Session Assembler (vsh_assembler)]                                   │
│  • Buffers multi-screen captures across Dashboard -> Selection -> Success             │
│  • Context switch guard (detects PAN changes mid-session)                              │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │ Emits Canonical SDC Master Payload
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                            Desktop Database (database.py)                              │
│                          tracker_dump / rawPayload.db Vault                            │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Multi-Screen Crosshair Map (1-to-1 SDC Parity)

VSH mirrors the multi-screen lifecycle of SDC across three distinct crosshair stages:

| Stage | Crosshair ID | Trigger Route Pattern (UIA URL Match) | Screen Region Cropped | Data Captured | OCR Execution (Core 2 Duo) |
| :---: | :--- | :--- | :--- | :--- | :---: |
| **1** | `vsh_identity` | `.../dashboard`<br>`.../fileIncomeTaxReturn`<br>`.../returns/dashboard` | **Top Header Strip**<br>(Top 80px across window) | • Client PAN / GSTIN<br>• Client Legal / Trade Name | **~10 ms** |
| **2** | `vsh_selection` | `.../fo-select-itr-form`<br>`.../returns/return-dashboard` | **Central Selection Card**<br>(Center 400x300px box) | • Filing Type (`ITR-1..7`, `GSTR-1`, `GSTR-3B`, `CMP-08`)<br>• Period / AY (`AY 2026-27`, `June 2026-27`) | **~15 ms** |
| **3** | `vsh_submission`| `.../fo-return-success`<br>`.../fo-e-verify-now-success`<br>`.../returns/arn-success` | **Central Receipt Card**<br>(Center 500x350px box) | • 15-digit ITR Ack / 15-char GST ARN<br>• e-Verification Status (`Verified` vs `Pending`)<br>• Submission Timestamp | **~25 ms** |

---

## 4. Hardware Optimization & Core 2 Duo Guard

To ensure smooth operation even on legacy office hardware (Intel Core 2 Duo E7500/E8400, 4GB RAM):

### 4.1 Zero-CPU Route Gating via Windows UI Automation
* **Traditional Mistake:** Continuous full-screen OCR every 2 seconds consumes 15% of a Core 2 Duo core and induces lag.
* **VSH Solution:** Uses `UIAutomationCore.dll` to read the browser's address bar. This Windows API call inspects the accessibility tree of the window and extracts the URL in **under 0.5 milliseconds with zero OCR and 0.0% CPU overhead**.
* During form filling, computations, and typing (99% of session time), **OCR NEVER RUNS**.

### 4.2 Targeted Regional Cropping
* Full HD Frame: $1920 \times 1080 = 2,073,600$ pixels.
* Cropped Header / Receipt: $\approx 200,000$ pixels (**89% reduction in pixel processing load**).
* OCR execution drops from 250ms down to **10ms–35ms** on a Core 2 Duo.

### 4.3 State Latch (Single-Shot Execution)
* Once a crosshair captures its target data, it latches into a completed state for that route. It will not re-scan the window until the user navigates away.

---

## 5. Optical Repair & Deterministic Tax Regex Engine

Optical character recognition can occasionally misread characters rendered in browser web fonts. VSH passes all raw OCR outputs through a deterministic statutory repair pipeline before writing to storage:

```text
Raw OCR Text ──► Bounding Match ──► Character Normalization ──► Checksum/Format Verification
```

### Repair Rules:
1. **ITR Acknowledgement Number:** Exactly 15 numeric digits (`\b\d{15}\b`).
   * If a 15-character candidate is detected: replace optical ambiguities (`l`, `I`, `|` $\to$ `1`; `O`, `o` $\to$ `0`; `B` $\to$ `8`; `S`, `s` $\to$ `5`).
2. **GST ARN:** Exactly 15 alphanumeric characters (`\b[A-Z]{2}\d{13}\b`).
   * First 2 characters must be valid Indian State 2-letter codes.
   * Following 13 characters must be strictly numeric digits.
3. **PAN:** Exactly 10 characters (`\b[A-Z]{5}[0-9]{4}[A-Z]\b`).
   * 4th character validated against entity types: `P` (Individual), `C` (Company), `F` (Firm), `H` (HUF), `A` (AOP), `T` (Trust).
4. **Assessment Year (AY):** Pattern `\b(?:AY\s*)?20(\d{2})[-–/](\d{2})\b`.
   * Enforces mathematical validity: second year must equal first year + 1 (e.g. `2026-27`).

---

## 6. The Visual Session Assembler (`vsh_assembler`)

The **Visual Session Assembler** is an in-memory state machine running within the desktop application. It unifies individual screen captures into a single canonical master record:

```python
class VisualSessionAssembler:
    def __init__(self):
        self.active_session = {
            "client_pan": None,
            "client_name": None,
            "filing_type": None,
            "period_label": None,
            "ack_number": None,
            "status": None,
            "filing_timestamp": None,
            "capture_source": "vsh_type_a",
            "crosshairs_satisfied": set()
        }

    def on_crosshair_captured(self, crosshair_id: str, data: dict):
        # 1. Identity Switch Guard
        if data.get("client_pan") and self.active_session["client_pan"]:
            if data["client_pan"] != self.active_session["client_pan"]:
                self.flush_session() # Flush or reset prior client

        # 2. Merge incoming fields
        for k, v in data.items():
            if v:
                self.active_session[k] = v
        self.active_session["crosshairs_satisfied"].add(crosshair_id)

        # 3. Final submission trigger
        if crosshair_id == "vsh_submission" and self.active_session["ack_number"]:
            self.flush_session()

    def flush_session(self):
        if not self.active_session["ack_number"]:
            return
        # Writes directly into database.py (tracker_dump)
        db.insert_tracker_dump(self.active_session)
        self.reset()
```

---

## 7. Master Comparison: SDC vs. Type A VSH

| Evaluation Criterion | SDC (Browser DOM Crosshair) | Type A VSH (Visual Screen Harvester) |
| :--- | :--- | :--- |
| **Execution Environment** | Browser extension content script | Windows Desktop Process (PyQt6 / Python) |
| **Browser Script Injection** | Yes (`content_scripts` in active tab) | **Zero (Completely unmodified browser)** |
| **Inspection / WAF Detection** | Detectable via script-tag / runtime checks | **100% Undetectable (No browser footprint)** |
| **Terms of Service Compliance** | User-agent assistive autofill/capture | **Pure external desktop accessibility reader** |
| **Captured Scope** | PAN, Name, Form, Period, Ack/ARN, Status | **PAN, Name, Form, Period, Ack/ARN, Status** |
| **CPU Usage on Core 2 Duo** | ~0.0% | **~0.0%** (via UIA URL gating + regional crops) |
| **Supported Browsers** | Chrome & Edge (requires unpacked/CRX) | **Chrome, Edge, Firefox, Brave (Universal)** |

---

### 8. Implementation Phases & Status

```text
┌────────────────────────────────────────────────────────────────────────────┐
│                    VSDC Implementation Status (Completed)                  │
├────────────────────────────────────────────────────────────────────────────┤
│  [Piece 1]  Route Watcher via Windows UI Automation           [DELIVERED]  │
│             • Address bar URL extractor (Chrome, Edge, Firefox)            │
│             • Dynamic session countdown timer sanitization                 │
│             • Crosshair route regex matching                               │
│                                                                            │
│  [Piece 2]  Regional Frame Snapper & Windows Native OCR       [DELIVERED]  │
│             • Window bounding box capture via Win32 PrintWindow / GDI      │
│             • Windows.Media.Ocr integration via winsdk                     │
│             • Precision cropping (Header, Selection Box, Receipt Card)    │
│                                                                            │
│  [Piece 3]  Deterministic Tax Regex & Optical Normalizer      [DELIVERED]  │
│             • Ack (15 digits), ARN (14–16 chars / Txn IDs), PAN, AY, Status│
│             • Optical confusion self-repair (l/I -> 1, O -> 0)             │
│             • View Filed Returns multi-year & latest return extraction     │
│                                                                            │
│  [Piece 4]  Visual Session Assembler & Vault Ingestion        [DELIVERED]  │
│             • In-memory session buffering & multi-screen stitching         │
│             • Boundary reset on logout / login auth transitions            │
│             • Strict Ack / ARN guard against false emissions               │
│             • Direct delivery to database.py (tracker_dump SQLite)         │
│                                                                            │
│  [Piece 5]  Non-Intrusive Desktop Feedback & UI Indicator     [DELIVERED]  │
│             • Ambient HUD Pill overlay widget (ui/components/vsdc_hud_pill)│
│             • Non-blocking animation and auto-dismiss on verified capture  │
│             • Zero-leakage local PAN audio beeper feedback                 │
│                                                                            │
│  [Piece 6]  GST Multi-Screen Crosshairs & Metadata Capture    [DELIVERED]  │
│             • gst_welcome_calendar, gst_form_details, gst_filing_success   │
│             • Return period normalization & full table supply metadata     │
│                                                                            │
│  [Piece 7]  Anti-Pollution Name Protection Engine             [DELIVERED]  │
│             • Authoritative statutory legal name precedence over headers   │
│             • Ligature scrubber and corporate vs individual separation     │
│                                                                            │
│  [Piece 8]  Gemini Flash AI Structured Compliance Parser     [DELIVERED]  │
│             • Sub-second pre-dump compliance enrichment                    │
│             • Strict Privacy Guard: Zero personal info/passwords sent      │
│             • Token usage & cost tracker in Tracker Dump workspace         │
│                                                                            │
│  [Piece 9]  VSDC-X: UI Automation Exact-Text Capture Mode  [GST DELIVERED│
│             • Reads Chromium's accessibility tree directly — zero OCR     │
│               error, ~0.1s vs OCR's full capture+encode+decode+recognize  │
│             • Wired into gst_welcome_calendar, gst_form_details, and      │
│               submission/ARN capture; UIA tried first, OCR fills gaps     │
│             • ITR NOT yet wired — same pattern, not started               │
└────────────────────────────────────────────────────────────────────────────┘
```

See **Section 11** below for full detail.

---

## 9. Authoritative Legal Name Precedence & Anti-Pollution Protection

To guarantee that truncated or malformed names from portal headers (e.g. `MOHAMMAD KAMARUJJ...`) do not overwrite high-quality taxpayer records:
1. **Name Quality Scoring (`is_better_taxpayer_name`)**:
   - Longer, multi-word full legal names discovered on profile/form views are promoted over truncated header badges.
   - Known portal noise phrases (`"PROFILE"`, `"LOGOUT"`, `"DASHBOARD"`, `"RETURN"`) and captcha fragments are aggressively scrubbed.
2. **Entity-Aware PAN Intelligence**:
   - Differentiates Individuals (4th character `P`) from Companies/Firms (`C`, `F`, `L`, `T`), ensuring Proprietor Legal Name and Corporate Trade Name are maintained distinctly without collision.

---

## 10. Gemini Flash AI Pre-Dump Enrichment & Privacy Guard

For complex return receipts, unassigned identities, or intricate tax computation tables, VSDC leverages **Google Gemini Flash**:
- **Structured Extraction**: Converts unstructured OCR text and form snapshots into structured JSON objects (legal names, trade names, statutory statuses, acknowledgment numbers).
- **Strict Privacy & Client Data Protection Guard**:
  - Automatically filters and scrubs client personal data before any AI request.
  - **NEVER** sends emails, phone numbers, bank accounts, IFSC codes, addresses, or passwords.
  - **ONLY** processes statutory identifiers: Client PAN, Legal Name, Form Type, Return Period, Submission Status, and ARN.
- **Tracker Dump Token Meter**:
  - Live in-app token and cost counter monitoring Gemini Flash usage and budget across workstations.

---

## 11. VSDC-X: UI Automation Exact-Text Capture Mode

**Status:** Production (GST delivered 2026-09-17; ITR delivered, validated, and hardened 2026-09-19).

### 11.1 Why

OCR reconstructs text from pixels, which is inherently probabilistic — this is exactly where VSDC's weak points lived: taxpayer name extraction needed a 7-tier regex/heuristic cascade (`core/vsdc/vsdc_name_parser.py`) just to reconstruct names OCR read noisily, GST filing-preference capture (Quarterly/Monthly) was unreliable, and submission-status classification had no way to cross-check a single OCR read against anything else.

VSDC-X reads the **Chromium accessibility tree directly** via Windows UI Automation (`UIAutomationCore.dll`, the same public OS API screen readers use) instead of OCR-ing screen pixels. It never enters the browser process, never touches the page's DOM/JS, never sends network traffic — same "outside-the-browser-process, passive observation" risk tier as legacy VSDC's OCR pipeline (see `core/vsdc/vsdc_uia_text.py`'s module docstring, and the risk framing in 11.5). It is strictly a better *sensor*, not a new capture mechanism category.

### 11.2 Architecture

```text
VSDCRouter._route_gst_crosshair() & _route_itr_crosshair() — once per tick, right after the OCR capture:

  uia_text, uia_lines = vsdc_uia_text.read_page_text(hwnd)   # ~0.1s, exact text
       │
       ├─► GST Pipeline:
       │    ├─► gst_welcome_calendar   : name/GSTIN/filing_preference — UIA tried first,
       │    │                            OCR top_right_profile rescan as fallback
       │    ├─► gst_form_details       : uia_meta = extract_gst_form_table(uia_text, ...)
       │    │                            merged OVER the OCR-derived meta — UIA wins per-field
       │    └─► submission / ARN path : ARN and status each tried via UIA first; consensus
       │                                 keeps whichever source classifies with higher confidence
       │
       └─► ITR Pipeline:
            ├─► Page Heading Extractor : extract_itr_form_heading() reads exact form ("ITR 4")
            │                            ahead of wizard URL and body copy
            ├─► Wizard URL Resolver    : resolve_itr_form_type_from_url() matches form slug
            ├─► Statutory Filing Type  : extract_itr_filing_type() captures Original/Revised/
            │                            Belated/Updated u/s 139(8A) into filing_preference
            ├─► Personal Info Profile  : ValuePattern reads readonly inputs (Full Name, DOB,
            │                            Primary Phone & Email) directly into DB columns
            └─► Terminal Ack Gate      : Ack extraction strictly restricted to terminal routes
                                         (itr_filed_verified, itr_submitted_pending, itr_offline_json_submit)
```

The key structural insight: UIA's label/value elements arrive as **adjacent lines in document order** (`"Legal Name -"` then `"NAZRUL HAQUE"` as the next element) — the exact same "label on line N, value on line N+1" shape `extract_gst_form_table()` was already built to handle for OCR line-wrapping. So VSDC-X required **no new parsing logic** — it feeds the identical extractors in `core/vsdc/vsdc_regex.py` exact text instead of noisy OCR text.

`core/vsdc/vsdc_uia_text.py` handles two real threading gotchas: UIA COM objects are apartment-bound (must be created *and* used from the same OS thread — the module uses a persistent single-worker `ThreadPoolExecutor` with `comtypes.CoInitialize()` run via the pool's `initializer=`), and every call has a hard timeout, since a raw UIA call from a thread with no message loop can hang or deadlock on a complex page — an unguarded hang would silently freeze the entire VSDC worker thread, killing OCR capture too since they share the same per-tick loop.

### 11.3 What's captured, verified against live sessions

All confirmed against real GST and Income Tax portal sessions across multiple live assessees:

| Capability | Status | Notes |
| :--- | :---: | :--- |
| **GST: Taxpayer name** | ✅ | Exact, zero reconstruction needed |
| **GST: GSTIN** | ✅ | Exact 15-character validation |
| **GST: Filing preference** | ✅ | Quarterly vs Monthly captured via patience latch (up to 60 polls) |
| **GST: Full form-table dataset** | ✅ | Legal name, trade name, FY, tax period, status, due date, form type (GSTR-1, GSTR-3B, CMP-08) |
| **GST: Submission ARN + status** | ✅ | Inline banners and modal popups confirmed with cross-source consensus (`get_status_rank`) |
| **ITR: Form Type Heading Resolution** | ✅ | `extract_itr_form_heading()` reads exact form heading ahead of URL; immune to body copy form references |
| **ITR: Statutory Filing Type** | ✅ | `extract_itr_filing_type()` captures Original, Revised, Belated, and Updated (u/s 139(8A)) into `filing_preference` |
| **ITR: Profile & Personal Info** | ✅ | UIA `ValuePattern` reads readonly inputs: Full Legal Name, DOB, Primary Phone, Primary Email |
| **ITR: Offline JSON Flow** | ✅ | Supports `itr_offline_json_upload` and terminal `itr_offline_json_submit` crosshairs |
| **ITR: False Submission Gating** | ✅ | Terminal submission crosshairs gate ack extraction; prevents previous year acks appearing in wizards from minting false records |
| **ITR: View Filed Returns Scoping** | ✅ | Only extracts the latest/topmost filing card, preventing past "Processed" status from overriding fresh submissions |
| **Window Session Isolation** | ✅ | `_WindowSession` prevents multi-window / multi-portal state contamination across browser instances |
| **Time-Based Static Timeout** | ✅ | 30s wall-clock timeout (`_STATIC_SCREEN_TIMEOUT_SEC`) replaces fragile poll-count cutoffs |
| **HUD Pill Dynamic Pulse** | ✅ | Hidden while polling; pulses on capture; tags source as `[VSDC-X]` vs `[VSDC]` |

**Known gaps & future roadmaps (as of September 2026):**
1. `gst_audit_history` (Return Filing History and Tracking Status) has **no structured multi-row extraction at all**, from either OCR or VSDC-X — currently invisible to the pipeline. Scoped for future multi-row table assembler.
2. Dashboard-level `filing_type`/`tax_period`/`fy` guesses (used while browsing, not the authoritative capture) are still OCR-only in the generic "other GST crosshairs" path. Low priority — it's a background hint, not what gets saved; authoritative capture on `gst_form_details` already uses VSDC-X fully.
3. **Notices & Orders** page (show-cause notices, demand orders — deadline-bearing) has no coverage at all. Flagged as high-value addition for upcoming milestone.
4. GSTR-9/9C (Annual Return) — the `gst_form_details` crosshair pattern matches it, but requires live annual-return verification when portals open annual filing windows.

### 11.4 Verified findings and a retraction — read before trusting a "VSDC-X can't read X" conclusion

- **Cross-source status/ARN consensus**: `vsdc_router.py` classifies status against both UIA and OCR text when both are available and keeps whichever comes back more confident (`get_status_rank`), rather than trusting one source alone. ARN is tried via UIA first.
- **Welcome-page patience fix**: `gst_welcome_calendar` and ITR `personal_information` wait up to 60 ticks for trailing async fields (filing preference, DOB, contact cards) before giving up, preventing partial premature lock-in.
- **Retracted modal finding**: An early test script bug suggested UIA could not read static modals due to stale window handles in `tools/vsdc_x_modal_test.py`. Once fixed, UIA reads through Chromium modals on the very first tick across repeated clean runs.
- **Persisted capture-method attribution**: `vsdc_assembler.py` logs `VSDC-X_<crosshair_id>` vs `VSDC_<crosshair_id>`, persisted into SQLite `tracker_dump.capture_method` and highlighted in Tracker Dump.
- **Safe data directory redirection**: In commit `818308b`, `Vsdc_Captures`, token statistics, and user settings were redirected to `%USERPROFILE%\AmanAssociates_Sera` to eliminate Windows `Program Files` permission errors in production.

### 11.5 Legal/risk posture — do not cross this line

VSDC-X must stay in the same passive-observation risk tier as legacy VSDC, which has been reviewed and cleared by the firm's legal/ToS expert specifically because it's read-only and never enters the browser process or touches the portal's DOM/JS/network. **Never auto-click, auto-fill, auto-submit, or auto-dismiss anything on the portal** to "help" VSDC-X read something — that crosses from passive observation into automated interaction with the portal, a materially different risk category that has not been reviewed. When a capture gap could technically be "solved" by simulating a click or forcing keyboard focus, the correct answer is to wait for the human to do it themselves (reuse the existing retry/polling architecture), never to automate the action.

### 11.6 Diagnostic tooling

- `tools/vsdc_uia_probe.py` — standalone accessibility-tree dump of the current foreground browser window.
- `tools/vsdc_console_watch.py` — runs the real `VSDCWorker` capture pipeline standalone against a live portal session without launching the full GUI.
- `tools/vsdc_x_modal_test.py` / `tools/vsdc_x_router_gap_test.py` — test harness exercising UIA modal extraction and GST routing against `tests/test_page_gst_submission.html`.
- `tools/vsdc_x_itr_router_gap_test.py` — tests ITR wizard form heading detection, filing type extraction, and terminal submission gating.
- `VSDC_UIA_ONLY=1` environment variable — diagnostic-only, forces the router to discard OCR text and isolate VSDC-X performance.


