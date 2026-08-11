# Features and Security Controls

## UI

Project Sera uses a red sidebar with mint active states, a staff-name profile footer, admin-gated management navigation, and cream/card styling across management tools.

Normal users can open the Add Client form without entering Admin Mode. Edit, Delete, and Attach/Detach remain admin-only.

Client Detail uses compact sections for:

- Identity and Contacts.
- Security Credentials.
- Service Management.
- Notes.
- Autofill.
- DRS.

Back navigation is available on detail/workspace views and slide-panel tools, with Material Design arrow icons. The same component rules apply to Manage Clients, Audit Log, MCL, Settings, Services, Filing Types, Filing Periods, CSV Import, and Data Management screens. Layouts are DPI-aware and shrinkable on smaller displays.

## Search

The main screen presents a spreadsheet-style data grid of all clients with instant filtering. Active cells use a blue focus ring for keyboard navigation.

In Admin Mode, the search toolbar includes an archive action for the selected client. Archiving confirms the action, hides the client from active results, and records it in the audit log without deleting data.

## Admin Gating

Mutating controls are hidden in standard user mode. System Audit Log, Backup/Restore, and Settings are available only after Admin PIN authentication.

## Settings

Centralized settings include:

- Application theme.
- Window display mode.
- Mask modes.
- Password reveal button visibility.
- Manual credential copy controls.
- Spreadsheet column visibility.
- Quick-Copy column restrictions.
- Feature toggles.

Window display modes include Fullscreen/Maximized, Square Mode, and Rectangular Mode.

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
