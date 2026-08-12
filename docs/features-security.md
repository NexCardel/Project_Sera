# Features and Security Controls

## UI

Project Sera uses a red sidebar with mint active states, a staff-name profile footer, admin-gated management navigation, and cream/card styling across management tools.

Normal users can open the Add Client form without entering Admin Mode. Edit, Delete, and Attach/Detach remain admin-only.

Client Detail uses compact sections for:

- Identity and Contacts (includes clickable **Client Token ID** badge `Token: CLI-XXXXX`).
- Security Credentials.
- Service Management.
- Notes.
- Autofill.
- DRS.

Back navigation is available on detail/workspace views and slide-panel tools, with Material Design arrow icons. The same component rules apply to Manage Clients, Audit Log, MCL, Settings, Services, Filing Types, Filing Periods, CSV Import, and Data Management screens. Layouts are DPI-aware and shrinkable on smaller displays.

## Search Grid & Excel-Style Copying

- The main screen presents a spreadsheet-style data grid of all clients with instant filtering.
- **Cell Selection & Copying (`Ctrl+C`)**: Grid selection mode allows selecting individual cells or cell blocks. Pressing `Ctrl+C` copies tab-separated values to the clipboard formatted like Excel.
- **Visual Copy Highlight**: Copying triggers a 500 ms green highlight flash (`#2E9B5F`) on selected cells and displays a toast notification.
- In Admin Mode, the search toolbar includes an archive action for the selected client. Archiving confirms the action, hides the client from active results, and records it in the audit log without deleting data.

## Master Column List (MCL) & ID Token System

- **ID Field Type (`id`)**: Admins can assign the `ID` field type (`"ID (Primary Key / Auto-Serial)"`) to an MCL column.
- **Single-Column Exclusivity**: Only one column in MCL can hold the `ID` field type at any time. Assigning `ID` to a new column automatically unassigns any previous `ID` column.
- **Auto Serial Numbers**: The `ID` column automatically generates and resequences sequential numbers (`1`, `2`, `3`, `4`...) for all clients.
- **Backend Client ID Tokens**: Every client record is assigned a unique `client_id_token` (e.g. `CLI-00001`, `CLI-00002`, ...). This token allows identifying clients for cloud/internet uploading without exposing sensitive PAN or GST details.
- **Wiring Display**: General Settings displays the active `ID` column wiring status. Client Detail headers show a clickable `Token: CLI-XXXXX` badge with click-to-copy capability.

## Purge Duplicates Logic

- **Smart Normalization**: Duplicate detection normalizes text by removing punctuation, spaces, and converting to lowercase.
- **Serial Column Exclusions**: Automatically ignores serial/index columns (`No.`, `Sl No.`, `ID`, `#`) during duplicate matching.
- **Original Record Preservation**: Keeps the original record (lowest client ID) and purges/merges newer duplicates.

## Admin Gating

Mutating controls are hidden in standard user mode. System Audit Log, Backup/Restore, and Settings are available only after Admin PIN authentication.

## Settings

Centralized settings include:

- Application theme.
- Window display mode (Fullscreen/Maximized, Square Mode 1:1, Rectangular Mode 950x680).
- Primary Key (ID) Column wiring status.
- Mask modes.
- Password reveal button visibility.
- Manual credential copy controls.
- Spreadsheet column visibility.
- Quick-Copy column restrictions.
- Feature toggles (DRS, Extension Tracker).

## Credential Controls

Quick-Copy can allow authorized fields on a client profile to copy instantly. Unauthorized fields, such as passwords, remove copy affordances to enforce tracked extraction.

Copied passwords and secret credentials are automatically cleared from the Windows clipboard after the configured timeout, defaulting to 30 seconds. The clear operation first verifies the clipboard text has not already been replaced by the user.

Clicking Autofill or Manual Copy, including `Alt+1..9` shortcuts, keeps the app on the client detail page and minimizes the main window so the browser portal comes forward.

## Audit and Recovery

The System Audit Log records client views, autofill triggers, manual copies, backups, restores, and CSV exports in an undeletable `audit_log` table with UTC timestamps and staff attribution labels.

Admins can create timestamped backup folders and restore from existing backups containing `master.db` and `sera.salt`. Restore creates a pre-restore safety snapshot.

## Alerts

The Sera Alert System shows non-modal bottom-left notifications for autofill, manual copy, create, update, archive, delete, backup, restore, and CSV export events.

Alerts use semantic levels: `success`, `info`, `warning`, and `error`. They use safe client identity formatting and auto-dismiss after 3 seconds.

## CSV

Admin Mode supports CSV import/export, client identity export, system log export, and a dynamic schema template that maps active column headers plus Services and Notes.
