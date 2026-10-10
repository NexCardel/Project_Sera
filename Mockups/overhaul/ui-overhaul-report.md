# UI overhaul — report (2026-10-10 15:54)

Deadline: 2026-10-12T23:00:00+05:30

| Status | WPs |
| :--- | ---: |
| Not started | 31 |
| In progress | 0 |
| Retry | 0 |
| Done | 5 |
| Blocked | 0 |

Runs: 3   output tokens: 200248   API-equivalent cost: $9.99

| WP | Status | Model | Commit | What | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W0-1 | Done | haiku | 40bc942 | Baseline: full test suite, start-up and memory numbers, current snapshots | Baseline: startup/memory tests pass, 31 UI screenshots rendered, cloud environment viable for offscreen testing |
| W0-2 | Done | sonnet | 537924a | Behaviour lock: tests for every 'must still work' item, green on the unchanged code | Behaviour lock: 8 offscreen test files tests/test_ui_lock_*.py (R1-R9 gaps) + goldens, no app code changed; Tracker CSV export bug (no import csv) locked as-is; |
| W0-3 | Done | mechanical | 5191b06 | Screen inventory | Screen inventory written to Mockups/INVENTORY.md |
| W0-D | Done | fable | e0d32a5 | Fleet redesign mockup (cloud, Fable 5.1): every screen in one new HTML mockup | Fleet mockup by Fable 5.1 (cloud); owner decisions and BUILD-PLAN.md in 9d66d4e |
| W0-R | Done | opus | 946bebe | Review the safety net | 5/5 mutations caught (detail, search, settings, audit, sync); added audit search-by-id case; INVENTORY covers all 31 PNGs; 147 lock tests pass |
| T-1 | Not started | haiku |  | Tokens: ui/utils/tokens.py from the :root block of Mockups/fleet/index.html (every colour, ... |  |
| K-1 | Not started | opus |  | Kit: Primitives: LabelledAction (primary / soft / secondary / ghost / danger, sm, disabled ... |  |
| K-2 | Not started | sonnet |  | Kit: Inputs: Input (leading icon, trailing action slot), Select, TextArea, Stepper, Checkbox ... |  |
| K-3 | Not started | sonnet |  | Kit: Containers: Card (caps title, right-hand slot, danger and em edges, no nesting), ... |  |
| K-4 | Not started | sonnet |  | Kit: Bars: Chip + ChipBar (single-select group, count badge, ▾ menu chip, "More ▾" overflow, ... |  |
| K-5 | Not started | opus |  | Kit: Data grid: DataGrid (QTableView + delegate) with the white tone and the dark tone, sticky ... |  |
| K-6 | Not started | sonnet |  | Kit: Frames: DialogFrame (icon, title, one-line purpose, body, footer with one primary, full ... |  |
| K-7 | Not started | sonnet |  | Kit: tools/ui_gallery.py: every component, every state (normal, hover, focus, disabled, ... |  |
| K-R | Not started | opus |  | Review phase K (kit) |  |
| R-1 | Not started | mechanical |  | Responsive and shell: ui/utils/responsive.py is already there (WIDE / COMPACT with hysteresis); add a ... |  |
| R-2 | Not started | sonnet |  | Responsive and shell: Sidebar on the kit: real logo (sera_mark.svg), brand not truncated, groups Clients / ... |  |
| R-3 | Not started | haiku |  | Responsive and shell: Display scale: DisplayScaleControl rebuilt as a SettingRow + two LabelledActions; ... |  |
| R-R | Not started | sonnet |  | Review phase R (responsive and shell) |  |
| S-1 | Not started | sonnet |  | Screens: Settings hub shell + unsaved bar + dirty tracking (IMPLEMENTATION 4.4) + General page |  |
| S-2 | Not started | haiku |  | Screens: Settings: Action buttons, Tracker, Export, Backup, Purge |  |
| S-3 | Not started | haiku |  | Screens: Settings: SCC vault presets |  |
| S-4 | Not started | opus |  | Screens: Settings: Columns (MCL) with Shows-on write-through (IMPLEMENTATION 4.3) + the three ... |  |
| S-5 | Not started | sonnet |  | Screens: Settings: Services; MCL manager, MCL column edit, Service manager, Service edit dialogs |  |
| S-6 | Not started | sonnet |  | Screens: All Clients / Search: header, chips (combo stays hidden), grid tone white, Columns ▾ menu ... |  |
| S-7 | Not started | opus |  | Screens: All Clients: selection bar (IMPLEMENTATION 4.1) and COMPACT behaviour |  |
| S-8 | Not started | haiku |  | Screens: Client detail panel |  |
| S-9 | Not started | sonnet |  | Screens: Manage Clients onto the kit (list tone white, chips, cards, bulk bar as SelectionBar) |  |
| S-10 | Not started | sonnet |  | Screens: Tracker Dump onto the kit (dark tone grid, tiles, chips, row actions by delegate; WIDE ... |  |
| S-11 | Not started | sonnet |  | Screens: Audit log (IMPLEMENTATION 4.5) |  |
| S-12 | Not started | opus |  | Screens: Sera Sync: feature map first (IMPLEMENTATION 4.6), then strip, three tabs, device cards, ... |  |
| S-13 | Not started | sonnet |  | Screens: Small dialogs: CSV import (+ summary), Change master password, Office recovery, Rejoin ... |  |
| S-14 | Not started | haiku |  | Screens: Remaining not-drawn dialogs by recipe: Container inspector, Create client from capture, ... |  |
| S-R | Not started | opus |  | Review phase S (screens) |  |
| F-1 | Not started | mechanical |  | Finish: Rewrite docs/Sera_UI.md from the kit docstring and tokens; update Mockups/README.md and ... |  |
| F-2 | Not started | mechanical |  | Finish: Real screenshots of every screen into Mockups/built/ (fictional data). |  |
| F-R | Not started | opus |  | Review phase F (finish) |  |

## Decisions taken for you

- **W0-2** Bulk archive/delete write no audit row, and Restore asks no confirmation, in Manage Clients. Keep or fix? → Locked as-is in tests/test_ui_lock_manage.py; the overhaul must not change it unless the owner asks (Old code behaviour, not a regression to hide; changing it is a product decision for a later WP)
- **W0-2** Manage Clients 'Save anyway' on a duplicate that also shares a primary key (company name / PAN) still fails with the Validation Error; and Search > Manage services leaves the table selection empty offscreen. Keep? → Locked as current behaviour: the duplicate prompt only lets a save through for identity values that are not primary keys (e.g. proprietor); manage_client_services is tested by its hand-off only (Both are old-code quirks; the real-desktop part of the second is a human check (check-add))
- **W0-2** Tracker Dump > Tools > Export Captures (CSV) always fails: tracker_dump_window.py never imports csv, so the user sees 'Export Failed'. Fix it in W0-2? → Locked as-is (test pins the failure); not fixed, W0-2 must not change app code. Fix it when the Tracker Dump WP touches the file: add 'import csv' and flip tests/test_ui_lock_tracker.py::test_export_csv_currently_fails_because_tracker_dump_window_never_imports_csv (Behaviour-lock WP changes no app code; one-line fix is safe for a later WP.)

## Checks waiting for you

- #1 (W0-2) R1.6 on a real desktop: from Search, use Edit / Archive / Delete / Manage services on one client and confirm each acts on the client that was clicked (offscreen, open_client_editor leaves the table selection empty after setCurrentCell, so Manage services may report 'Select one or more clients first.') — Not run
- #2 (W0-2) R6.3 on a real PC: open Search, Manage Clients, Tracker Dump, Audit, Settings and Sera Sync at 100 %, 125 % and 150 % Windows scaling and at 1366x768; nothing clipped, no horizontal scroll, text readable — Not run
- #3 (W0-2) Real keyboard on a real desktop: press Ctrl+= / Ctrl+- / Ctrl+0 (alert appears, restart applies the scale), Alt+n and Alt+Ctrl+n on a search result, Ctrl+B, F5 in Tracker Dump, and Tab order through the Manage Clients form; offscreen tests call the handlers/shortcuts directly — Not run
