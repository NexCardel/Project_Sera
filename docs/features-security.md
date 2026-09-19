# Features and Security Controls

## UI & Application Access

Project Sera uses a high-contrast dark design system (`#292929` body, `#0A0A0A` surfaces, `#171717` card tiles) with a red sidebar, staff-name profile footer, admin-gated management navigation, and crisp white cell grid tables.

- **Instant Auto-Unlock Vault**: On application launch, Project Sera auto-derives and unlocks your encrypted SQLCipher database using your local keyfile (`sera.key`). No master password login dialog pops up on startup.
- **Admin PIN Protection**: Admin functions (Admin Panel, Column Schema editing, Client Deletion, Database Reset, and System Settings) remain strictly protected by the Admin PIN (`1234` or custom PIN).
- **Windows Autostart**: Supports automatic launching on Windows PC boot via Windows Registry (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`). Toggleable from Settings → General.

Normal users can open the Add Client form without entering Admin Mode. Edit, Delete, and Attach/Detach remain admin-only.

Client Detail uses compact sections for:

- Identity and Contacts (includes clickable **Client Token ID** badge `Token: CLI-XXXXX`).
- Security Credentials.
- Service Management (with red outlined action buttons `#FF4D4D`).
- Notes (with pure white `#FFFFFF` input field).
- Autofill & Extension Filing Tracker.

Back navigation is available on detail/workspace views and slide-panel tools, with Material Design arrow icons. Layouts are DPI-aware and shrinkable on smaller displays.

---

## Sera VSDC & SDC Capture Subsystem

Project Sera relies on two high-reliability, zero-conflict capture engines:
1. **Sera VSDC & VSDC-X (Visual Screen Data Capture & UI Automation Sensor Layer)**:
   - **VSDC-X Exact-Text Sensor (`core/vsdc/vsdc_uia_text.py`)**: Direct, passive inspection of the Chromium accessibility tree via Windows UI Automation (`UIAutomationCore.dll`). Eliminates character confusions and optical ambiguity across both GST and Income Tax 2.0.
   - **DirectML OCR Fallback (`core/vsdc/vsdc_ocr.py`)**: Hardware-accelerated offline fallback running DirectML (`Windows.Media.Ocr`) for legacy or non-standard visual layouts.
   - **Statutory ITR Form & Filing Type Intelligence**: Extracts form types from page headings ahead of wizard URLs, and extracts statutory filing reasons (`Original`, `Revised`, `Belated`, and `Updated u/s 139(8A)`) into `filing_preference`.
   - **Personal Info & Readonly Input Capture**: Uses UIA `ValuePattern` to extract un-truncated Legal Name, Date of Birth (DOB), primary phone, and primary email directly into database columns.
   - **Terminal Submission Gating & Scoping**: Strictly gates 15-digit ITR Ack extraction to terminal submission crosshairs, and scopes `view-filed-returns` exclusively to the topmost return card to prevent historical "Processed" states from overriding new filings.
   - **Window-Isolated Session Tracking (`_WindowSession`)**: Completely encapsulates session identity, form context, and buffers per native window handle (`hwnd`), preventing cross-window or cross-portal state contamination.
   - **Zero-Leakage PAN Audio Beeper**: Provides instant local audible feedback upon client detection without transmitting any audio or sensitive data.
   - **Gemini Flash AI Structured Parser**: Pre-dump compliance enrichment converting complex return tables into clean structured records.
   - **Strict Privacy Guard**: Guaranteed exclusion of passwords, emails, phone numbers, bank details, and personal addresses from any telemetry or AI analysis.
   - **Ambient HUD Pill (`VsdcHudPill`)**: Hidden while polling; pulses on capture; displays active client identity, confirmed filing submissions, and engine attribution tags (`[VSDC-X]` vs `[VSDC]`).
2. **Sera SDC (DOM Crosshair Engine — Extension Layer)**:
   - Route-gated content script protocols (`itr_protocol.js`, `gst_protocol.js`, `sdc_core.js`) sleeping on non-target routes, aggregating multi-step filing fragments into `sdc_assembler`, and flushing atomic master payloads over direct local HTTP (`http://127.0.0.1:49152`).
   - *Permanent Retirement*: Sera SAD (API Interceptor / `net_interceptor.js`) and Sera SDS (Dataset Scanner / `sds_core.js`) are permanently retired and completely removed from the workspace.

---

## Tracker Dump Subsystem

The **Tracker Dump** workspace (`TrackerDumpWindow`) serves as a central audit repository for all statutory filings captured by **Sera VSDC / VSDC-X** and **Sera SDC**:

- **Real-Time Data Table**: Displays ID, Client Name & PAN/GSTIN, Service/Portal (`Income Tax (ITR-4)`, `GST (GSTR-3B)`), Period (`AY 2026-27`), ARN/Ack Number, Capture Method (`VSDC-X`, `VSDC_Visual`, `SDC`), Timestamp, and Actions.
- **Color-Coded Status Pill Badges & Darkened Cell Backgrounds**: Standardized Google Material Design icons (`mdi.*` via QtAwesome) and high-contrast color pills across all filing states with cell background rendering forced via a dedicated `QWidget` wrapper (Option A).
- **Monotonic Status Promotion Engine**: Submissions only upgrade their lifecycle rank (e.g. Draft -> Submitted -> Verified), ensuring statutory ARNs and valid verifications are never overwritten or downgraded by subsequent navigation passes.
- **Payload Inspector Drawer**: Click **View Payload** on any row to open the raw JSON drawer, inspecting exact timestamps, timeline sequences, and nested compliance structures.
- **AI Token & Cost Meter**: Real-time tracking of Gemini AI structured parsing usage and API cost metrics (`gemini_token_stats.json`).
- **Multi-Field Filtering & CSV Export**: Real-time search across Client Name, PAN, GSTIN, ARN, Period, or Portal, with capture method and status filter dropdowns, and one-click CSV export.
- **Row Deletion & Bulk Purge**: Individual **Delete** button per row for immediate removal, plus a **Clear All** action button to wipe stale test dumps.
- **Automatic Client Resolution**: Automatically maps incoming PAN numbers, token IDs (`CLI-00370`), or MCL Serial numbers to the corresponding client in SQLite.

---

## Search Grid, Formatting & Excel-Style Controls

- **White Grid Workspace**: The main screen presents a spreadsheet-style data grid of all clients (`results_table`) rendered with pure `#FFFFFF` background, `#241F1B` dark charcoal text, and `#0A0A0A` (`bl` dark shade) column headers.
- **Cell Selection & Copying (`Ctrl+C`)**: Grid selection mode allows selecting individual cells or cell blocks. Pressing `Ctrl+C` copies tab-separated values to the clipboard formatted like Excel.
- **Visual Copy Highlight**: Copying triggers a 500 ms green highlight flash (`#2E9B5F`) on selected cells and displays a toast notification.
- **Cell Fill & Text Formatting**: Right-clicking any cell selection opens cell fill and text formatting menus with vivid Excel color presets. Direct database persistence ensures fill highlights and text colors persist across app restarts.
- **Header Formatting Toolbar**: Top search header contains fill color, text color, clear formatting, undo, redo, refresh, archive, and manage service buttons.
- **Undo / Redo Engine (`Ctrl+Z` / `Ctrl+Y`)**: Reversible history stack tracks prior and updated formatting states per `(client_id, column_key)`. Shortcuts automatically route text undo when typing in the search bar and cell formatting undo otherwise.
- **Selection Preservation**: Search query updates preserve active cell cursor (`currentRow()`, `currentColumn()`) and range selections (`selectedRanges()`).

---

## Master Column List (MCL) & ID Token System

- **ID Field Type (`id`)**: Admins can assign the `ID` field type (`"ID (Primary Key / Auto-Serial)"`) to an MCL column.
- **Single-Column Exclusivity**: Only one column in MCL can hold the `ID` field type at any time. Assigning `ID` to a new column automatically unassigns any previous `ID` column.
- **Auto Serial Numbers**: The `ID` column automatically generates and resequences sequential numbers (`1`, `2`, `3`, `4`...) for all clients.
- **Backend Client ID Tokens**: Every client record is assigned a unique `client_id_token` (e.g. `CLI-00001`, `CLI-00002`, ...). This token allows identifying clients for cloud/internet uploading without exposing sensitive PAN or GST details.
- **Wiring Display**: General Settings displays the active `ID` column wiring status. Client Detail headers show a clickable `Token: CLI-XXXXX` badge with click-to-copy capability.

---

## Purge Duplicates Logic

- **Smart Normalization**: Duplicate detection normalizes text by removing punctuation, spaces, and converting to lowercase.
- **Serial Column Exclusions**: Automatically ignores serial/index columns (`No.`, `Sl No.`, `ID`, `#`) during duplicate matching.
- **Original Record Preservation**: Keeps the original record (lowest client ID) and purges/merges newer duplicates.

## Sera Clipboard Assist (SCA — Ambient Autofill)

- **Ambient Workflow**: When staff copy a client User ID (PAN, GSTIN, UID) from an Excel workbook, SCA detects the Excel clipboard marker in $O(1)$, matches the candidate against an in-memory index, and silently arms the matching client password for autofill.
- **Zero-Touch Injection**: When the user pastes that User ID into any recognized portal login field in the browser, the password field autofills itself immediately.
- **Security & Privacy Guardrails**:
  - Raw clipboard text is never logged, stored in SQLite, or written to disk.
  - Excel MIME format gating (`Csv`, `Biff12`, `XML Spreadsheet`, `Link`) ensures non-Excel clipboard events (chat, browser URLs, email, text editors) are rejected instantaneously at zero CPU cost.
  - 45-second TTL auto-expiry and 15-second debounce window.
  - Toggleable via **Settings → General** ("Enable SCA — Sera Clipboard Assist").

---

## Smart Credential Combinations (SCC / MECP)

**SCC (Smart Credential Combinations / Multi-Entry Credential Provider)** provides instant in-browser password formula testing and credential verification directly on portal password pages:

- **Formula-Based Password Generation**:
  - Dynamically computes standard firm credential formulas based on the assessee's PAN (e.g. `Pan@123`, `Pan#2024`, capitalized variants, firm standard prefix/suffix).
  - Integrates 2 configurable static fallback combinations alongside 2 dynamic PAN formulas.
- **Unmasked In-Browser Testing (MECP Widget)**:
  - Injects a cleartext floating assistant on tax portal password screens allowing staff to test combinations with single clicks.
  - Native clipboard replacement without disruptive dialog popups or lost cursor focus.
- **One-Click Vault Tagging & Automatic Verification Notes**:
  - Successfully logging in with an SCC password triggers a 20-second floating confirmation toast.
  - Automatically appends `"Password verified via SCC"` to the client's notes in `master.db`, tagging the verified credential without manual data entry.
- **Unregistered Client Support**:
  - Works seamlessly even for newly discovered or unregistered clients during portal triage.

---

## Admin Gating & Settings

Mutating controls are hidden in standard user mode. System Audit Log, Backup/Restore, and Settings are available only after Admin PIN authentication.

Centralized settings include:

- Application theme & Window display mode (Fullscreen/Maximized, Square Mode 1:1, Rectangular Mode 950x680).
- Windows PC Autostart toggle.
- Primary Key (ID) Column wiring status.
- Mask modes & password reveal button visibility.
- Manual credential copy controls.
- Spreadsheet column visibility & Quick-Copy column restrictions.
- Filing Success Tracker toggle.

---

## Credential Controls & Audit Recovery

Copied passwords and secret credentials are automatically cleared from the Windows clipboard after the configured timeout, defaulting to 30 seconds. The clear operation verifies the clipboard text has not already been replaced by the user.

Clicking Autofill or Manual Copy minimizes the main window so the browser portal comes forward.

The System Audit Log records client views, autofill triggers, manual copies, backups, restores, and CSV exports in an undeletable `audit_log` table with UTC timestamps and staff attribution labels.

Admins can create timestamped backup folders and restore from existing backups containing `master.db` and `sera.salt`. Restore creates a pre-restore safety snapshot.
