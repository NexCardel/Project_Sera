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

## Tracker Dump Subsystem

The **Tracker Dump** workspace (`TrackerDumpWindow`) serves as a central hub for all network response captures recorded by **Sera_API_detection (SAD)** and the companion browser extension:

- **Real-Time Data Table**: Displays Client Name, PAN/GSTIN, Portal, Submission Period, ARN/Ack Number, Capture Method (`SAD_API_Interceptor` in neon emerald, `DOM_Tracker`, `Manual_Fallback`), Timestamp, and Actions.
- **Payload Inspector**: Click **View Payload** on any row to open the raw JSON drawer, inspecting exact API response headers, HTTP codes, and body objects.
- **Multi-Field Filtering & CSV Export**: Real-time search across Client Name, PAN, GSTIN, ARN, Period, or Portal, with capture method and status filter dropdowns, and one-click CSV export.

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
