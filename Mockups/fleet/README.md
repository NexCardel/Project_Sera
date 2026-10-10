# Sera fleet redesign mockup (W0-D)

Design only. Nothing in the app changed. Open `index.html` from this folder (so the `../current/<file>.png`
paths resolve) in any browser; it is one self-contained file, offline, no fonts, no CDN, about 155 KB.

The page has a navigation list of every screen, a 1366 × 768 app frame, a **WIDE / COMPACT** toggle
(frame 1366 or 1024 px wide), a **scale** toggle (80 / 100 / 125 %: the frame keeps its pixel size and
the app scales inside it, as `QT_SCALE_FACTOR` does), an **Admin** on/off toggle and a **Before** button
that shows today's screenshot. Below the frame a caption shows the logical size, the page width and
whether the daily screens are in WIDE or COMPACT (below 1080 logical px of page width, as in
`docs/ui-scale-adaptive-layout-plan.md`), and whether the sidebar auto-collapsed (below 1000).

Interaction that works: every tab, chip, switch, checkbox and stepper; grid row selection (shows the
selection bar); Open shows the client panel; Ctrl-click in Manage Clients shows the bulk bar; any
Settings change shows the unsaved bar and enables the footer Save; Discard / Save clear it; dialogs
open over the screen they come from (Add column, Edit, Change password, Rejoin office, Display, the
sidebar entries); Esc closes.

## Decisions taken by the owner (2026-10-10)

| Question | Decision | Applied |
| :--- | :--- | :--- |
| Tracker Dump grid tone | Keep both: client grids stay white, Tracker Dump keeps its dark table | Grid component gains a dark tone (`.gridwrap.dark`); Tracker Dump uses it |
| Separate "Search" sidebar entry | Not needed | One "All Clients" entry (both buttons called `go_to_search`) |
| Hide Sera Sync's Office members / Sync status tabs for non-admins | No | Tabs visible to everyone; admin-only actions keep their PIN confirmation |
| Manage Staff Users and the legacy settings dialog | Not live features | Both dropped from the sidebar, the mockup and the build list |
| COMPACT selection bar | Icons with tooltips | As drawn (Open keeps its label) |
| Sidebar Display entry | Stays visible for all staff | As drawn |

Build order: see **BUILD-PLAN.md** (kit first, screens reuse only the kit).

## Tokens (`:root` in index.html; `ui/utils/tokens.py` when built)

| Token | Value | Use |
| :--- | :--- | :--- |
| `--bg` / `--side` / `--panel` / `--card` / `--raise` / `--hover` | #202020 / #171717 / #141414 / #191919 / #1f1f1f / #262626 | page, sidebar, panels and inputs, cards, raised rows, hover |
| `--line` / `--line2` / `--line3` | #2a2a2a / #363636 / #444444 | hairlines, control borders, hover borders |
| `--text` / `--text2` / `--muted` / `--faint` | #f8fafc / #c9d1d9 / #8e8d88 / #6e6e6e | body, secondary, hints, labels |
| `--em` / `--em2` / `--emdim` / `--emdeep` / `--emline` / `--mint` | #2e9b5f / #34b76d / #1a382b / #0e1f16 / #1e4d34 / #4cf9b7 | the one primary; hover; active nav; soft fills; soft borders; active text |
| `--danger` / `--dangertx` / `--dangerdim` / `--dangerline` | #ff4d4d / #ff8d8d / #3a1a1a / #7a2b2b | destructive only |
| `--warn` / `--warndim` / `--warnline` | #e3b341 / #33290f / #5b4814 | attention, unsaved bar, fixed-string pieces |
| `--info` / `--infodim` / `--infoline`, `--purple` / `--purpledim` | #5aa7ff / #16263b / #24466e, #c4a8ff / #2a1f3d | sync / information pills and banners; manual-copy pills |
| `--g-bg` / `--g-alt` / `--g-line` / `--g-head` / `--g-headtx` / `--g-text` / `--g-mute` / `--g-hover` / `--g-sel` | #ffffff / #f7faf8 / #e7ece9 / #101713 / #cfe9dc / #1b2420 / #6b7a72 / #edf6f1 / #d9f2e4 | the white data grid |
| `--r-s` / `--r-m` / `--r-l` | 6 / 8 / 12 px | tags and keys / controls / cards, grids, dialogs |
| `--s-1` … `--s-6` | 4 8 12 16 20 24 px | spacing |
| `--f-xs` … `--f-xxl` | 11 12 13 14 17 21 px | section labels, hints, body, strong, dialog title, page title |
| `--h-ctl` / `--h-inp` / `--h-chip` / `--h-row` | 32 / 34 / 28 / 40 px | buttons, inputs, chips, grid rows |
| `--w-ctl` / `--w-side` / `--w-setnav` | 230 / 212 / 224 px | setting control column, sidebar, settings nav |
| `--ff` / `--mono` | Segoe UI, system-ui, sans-serif / Consolas, Cascadia Mono, monospace | text / PANs, IDs, logs |

## Component kit (CSS classes in index.html; `ui/components/kit.py` when built)

| Component | Class | Variants / notes |
| :--- | :--- | :--- |
| Labelled action | `.btn` | `pri` (emerald, one per screen), `soft`, secondary (default), `ghost`, `dng` (red, destructive only), `sm`, `dis`; icon + text |
| Icon button | `.ibtn` | `bd` bordered, `pri`, `sm`; always has a tooltip |
| Segmented | `.segbtn` | Containers / Raw captures |
| Chip | `.chip` | `on`; optional count `.n`; a ▾ chip opens a menu; `[data-chips=single]` groups |
| Switch | `.sw` | `aria-checked`, `disabled` with a reason tooltip |
| Checkbox | `.cb` | only inside lists / forms, never for an on/off setting |
| Input / select / textarea | `.inp`, `.field` (leading icon, trailing actions) | labels above fields (`.lab`) |
| Stepper | `.stepper` | numbers with units |
| Pill | `.pill` | tones: neutral, `g`, `w`, `r`, `b`, `p`, `o`; status, roles, action types |
| Tag | `.tag` | small mono label for types; tones `g`, `w`, `r`, `b` |
| Key | `.kb` | keyboard hints |
| Card | `.card` | `header h3` caps title + right-hand extra; `danger`, `em` edges; no nested cards |
| Setting row | `.srow` | title + sub-label left, control in the fixed `--w-ctl` column right; `auto`, `wide` |
| Status strip | `.strip` | one line of pills, facts and separators |
| Selection bar | `.selbar` | fixed height; `has` when something is selected; groups split by `.vr` and `.grp` |
| Unsaved bar | `.unsaved` | "N unsaved changes", Discard, Save; hidden when clean |
| Empty state | `.empty` | icon ring, one sentence, optional action |
| Banner | `.banner` | `w` attention, `r` error, `g` success, `i` information |
| Toast | `.toast` | bottom centre of the content area |
| Data grid | `.gridwrap` + `table.grid` | white tone (client lists, audit) and `dark` tone (Tracker Dump); sticky header, zebra, mint selection with emerald edge, `.gfoot` hints; `col-lo` / `col-c*` priority classes for COMPACT |
| Dark list table | `table.dt` | tables inside dialogs and settings |
| Dialog frame | `.dlg` + `.d-hd` / `.d-bd` / `.d-ft` | icon, title, one-line purpose, body, footer with one primary; `d-full` for full-window dialogs; `.scrim` behind |
| Client panel | `.panel` | 540 px slide-over, full width in COMPACT |
| Sidebar | `.sb`, `.nav` | brand with the real `sera_mark.svg`, admin switch, groups, footer |

## Screens

### Shell and sidebar
The sidebar keeps the real logo (embedded `assets/logo/sera_mark.svg`), the Admin mode switch, and
every entry, regrouped as Clients (All Clients, Manage Clients*, Tracker Dump, Audit Log*), Tools
(Import CSV, Display), Admin* (Options), then the staff row, the Auto-sync pill and
the version. Starred entries show only in Admin mode, as today. New: **Display** (open to everyone)
opens the per-PC display scale dialog. The sidebar auto-collapses below 1000 logical px and the
header toggle (Ctrl+B) brings it back; a manual toggle wins until restart.

Where every control went: logo + "Aman Associates" → brand (name no longer cut off); sidebar toggle
→ brand row (and the page header); Admin Mode switch → same place; Clients accordion → "Clients"
group label (always open; the accordion chevron goes because the group is three to five items);
All Clients and Search → one entry, **All Clients** (both buttons call the same `go_to_search` today;
Ctrl+K / Ctrl+F focus the search box); Tracker Dump, Manage Clients, Audit Log → Clients group;
Import CSV → Tools; Options → Admin group; Manage Staff Users → dropped (owner: not a live feature); profile avatar + name → staff row
(click still opens Sera Sync for admins); "🟢 Auto-Sync Active" pill → soft pill with a status dot
(click still broadcasts a sync; the "Syncing…", "Synced", "Live Sync" texts keep the same pill);
version label → unchanged.

### All Clients / Search (`01_all_clients_search.png`)
The five cell-formatting icons leave the title row and join the client actions in a **selection bar**
above the grid, so the header holds only the title with a live count, Columns ▾, Refresh and one
primary (+ Add client). The filter combo becomes chips; rare presets go under More ▾. The grid shows
seven columns at widths that fit 1366 px with no sideways scroll. COMPACT: chips drop under the search
box, bar buttons keep icons with tooltips (Open keeps its label), GSTIN and Ph. no. hide first.

Where every control went: sidebar toggle → header left; "All Clients / Search" title → "All Clients"
+ count; Fill colour / Text colour / Clear formatting / Undo / Redo → Cell group of the selection bar
(Fill and Text show the current colour and open the same palette menus; Ctrl+Z / Ctrl+Y unchanged);
+ Add Client → header primary; Refresh → header icon button; search box → same place, hint moves to
the grid footer; FILTER combo (All, Most viewed, Active today, Has services, No services,
Formatted/Highlighted, Has logins, Missing passwords, Archived, Service: …) → chips All, Active today,
Has services, No services, Highlighted, Archived + More ▾ (Most viewed, Has login credentials, Missing
passwords, Service: …); combo stays hidden as the source of truth; Archive icon → Archive in the bar;
Edit / Services / Delete icons (Admin mode) → Edit, Services and Delete… in the bar (admin only; Delete
set apart in red and disabled with no selection); grid columns → No., Name of company, Name of
proprietor, GSTIN, PAN, Ph. no., Services (the visible set still comes from Settings → Main screen; the
new Columns ▾ menu edits the same flags); "(Created …)" text in the No. column → the row tooltip; footer
hint → grid footer with keys; the right-click cell menu (fill, text colour, clear all) → unchanged.

### Client detail panel (`04_client_detail_panel.png`)
Header: Back, name, ID chip (click copies), ⋯ (Services, Archive, Delete while the panel is open),
Edit; one meta line (#, PAN, proprietor, last activity). **Services first**, with labelled Autofill /
Assist / Copy login buttons and a per-row Alt key; "off" is a dimmed button with a reason tooltip, not
a red outline. Identity and Logins cards with the copy icon next to each value and empty fields
collapsed into one "Not set" line; Notes gets a real box; the Portal values section (in code, shown
when a client has SGT captures) becomes a collapsed card. WIDE: 540 px panel beside a three-column
grid. COMPACT: the panel fills the content area.

Where every control went: Back (Esc) → header; name + proprietor subtitle → name + meta line;
CLI-00001 badge → ID chip; Identity fields (No., Company, Proprietor, GSTIN, PAN, Ph. no., DOB) with
copy icons → Identity card (copy next to value; empty ones in the "Not set" line); Security credentials
(User ID, Email, TAN, GST_Password, IT_Password, Email_Password, TDS_Password) masked, copy, show/hide
→ Logins card, same masking rule and 30 s clipboard wipe, eye buttons per Settings → Action buttons;
"shortcuts: Alt+1..9" hint → card header hint with the full tooltip; per-service red buttons (mode 1 /
mode 2) → Autofill / Assist / Copy login labelled actions with the same tooltips; Notes (auto-save) →
Notes card with "Saved" state; Portal values → collapsed card.

### Manage Clients, admin (`02_manage_clients_admin.png`)
Already redesigned; moved onto the kit only: white list, shared chips, cards, eye + copy inside the
login fields, bulk actions on the shared selection bar. COMPACT: list 300 px without service pills,
editor cards in one column.

Where every control went: Back, title, count, New client → header; search → same; chips All / Has
services / No services / Archived / More → same (More: Most viewed, Active today, Has login
credentials, Missing passwords, Service: …); Show Archived checkbox → stays hidden behind the Archived
chip; Sort combo (7 orders) → same; list rows (name, #id · PAN, service pills, created/updated) → white
list; multi-select bulk strip (count, Attach / detach service…, Archive, Restore, Delete permanently,
Clear) → selection bar under the list; editor header (avatar, title, subtitle with dirty state, More ▾
with Archive / Restore / Delete permanently…, Save) → same, Save primary; Identity card (Company,
Proprietor, GSTIN, PAN*, DOB) → same; Portal logins (User ID, Email, TAN, GST/IT/Email/TDS passwords,
eye, copy) → same; Contact (Ph. no.) → same; Other details → appears when a column falls outside the
others; Attached services chips (GST, Income Tax, Email, TRACES, EPFO) → same; Notes → same; conflict
banner + "Inspect Conflicts…" → above the search box when Sera Sync reports one; Add Client dialog and
Admin PIN dialog → same dialog frame (not drawn).

### Tracker Dump (`03_tracker_dump.png`)
Already redesigned; moved onto the kit: tiles, chips showing their value when on, one primary per row.
The grid keeps its **dark tone** (owner decision); it is the same grid component as the white client
grids, so header, zebra, selection and row buttons match. COMPACT as the app already does: subtitle hidden, chips on a
second row, "Raw", smaller tiles, 40 px rows, "Rows per page" label hidden, columns hide Captured by →
Updated → Filing type with hidden values in the Client tooltip.

Where every control went: title + subtitle → same; Refresh (F5) → icon button; Tools ▾ (Re-Resolve
Identities (SRPF), Map Datapoints to MCL Columns…, Open LTT sheet, Export Captures (CSV), Distill…,
Clear All Captures) → Tools ▾, same items; five summary tiles → tiles (click filters, tooltips kept);
Containers / Raw captures segmented → same (hidden view-mode combo stays the source of truth); search →
same; Status / Portal / Client / Time / Source chips with their menus → same; Reset → same (ghost);
grid (Client, Filing type, Filings & History or Period, Submission Status, actions, Updated, Captured
by) → same columns, grid component in its dark tone; row buttons Inspect / Fast Autofill / ⋮ (Inspect, + Create Client, Delete Container
or Record) → same; header right-click (Filing type, Updated, Captured by, Automatic) → same; status bar
(Showing x to y of z, Rows per page, Prev, Page n / m, Next) → footer; Container inspector, Create
client from capture and Map datapoints dialogs → same dialog frame (not drawn).

### Display scale (no screenshot; `display_scale_dialog.py`)
Reached from the sidebar **Display** entry (everyone) and inline on Settings → General. One setting
row: Automatic (recommended) / 80 / 90 / 100 / 110 / 125 %, a running-at line, Apply (saves on this PC)
and Restart now (asks, then restarts). The hint names Ctrl+= / Ctrl+− / Ctrl+0. Nothing applies live.

Where every control went: title + hint → dialog header; combo → setting row; Apply, Restart now,
status label → same row; Close → footer.

### Settings hub (`settings_*.png`)
One dialog, same twelve pages and names in the same order (Columns (MCL) is the Master Column List).
Real switches, cards with caps titles, every control in a 230 px right column, an **unsaved bar** at
the top of the page once something differs from the loaded values, footer Close + Save changes (Save
enabled only when dirty; hidden on pages that apply immediately: Columns, Services, Export, Backup,
Purge). Closing with unsaved changes asks Save / Discard / Keep editing.

- **General** (`settings_general.png`): Display (Theme "Dark" only; Window size on launch; **Screen
  scale** row with Automatic…125 %, Apply, Restart now, running-at line, saved per PC on its own
  buttons), Passwords and clipboard (Password masking with preview, Characters shown, Clear clipboard
  after, Click-to-copy), Start-up (Keep running when closed, Start with Windows), Clipboard assist
  (SCA switch, SCA action mode, SCA uses per copied User ID), Schema info (ID / primary key column,
  read-only). Every control from the page and its cut-off lower half is here.
- **Action buttons** (`settings_actions.png`): lead text; Show / hide eye buttons switch.
- **SCC vault presets** (`settings_scc.png`): SCC one-time verification switch; Detect login
  automatically (Off / On); Counts as eight stat tiles (Attempts, Worked, Failed, Locked, No
  conclusion, Asked, Saved, Not understood; meanings in tooltips); Option 1 and 2 cards (formula pieces,
  label, fixed string); Option 3 and 4 cards (label, plain fixed password); Live combination preview
  (Test with PAN, four resolved lines).
- **Tracker** (`settings_tracker.png`): SGT card first (SGT mode Live / Shadow, Record pages for SGT
  testing, Record pages for Sera Distill (SDIS), SGT-I Off / On, SGT lab → Open SGT lab, Show HUD
  pill), then Older engines (VSDC, VSDC-X, VSDC 24/7).
- **Columns (MCL)** (`settings_mcl.png`): + Add column (primary), Move up / Move down / Delete column…
  for the selected row (red, set apart), table Column · Type · Role · Shows on M·Q·A · Edit; drag or
  Alt+↑ / Alt+↓ to reorder. The three "Shows on" ticks are a second editor for the three visibility
  pages, staged until Save. The old +, pencil, bin, ↑, ↓ toolbar maps to Add column, Edit, Delete
  column…, Move up, Move down.
- **Services** (`settings_services.png`): + Add service, Delete service…, table Service · Automation
  mode · Mode 2 · Login URL · Edit. The old +, pencil, bin map to Add service, Edit, Delete service….
- **Main screen / Quick-copy fields / Admin screen** (`settings_main_vis.png`, `settings_qc.png`,
  `settings_admin_vis.png`): the same lists with a Find box, "N of 14 shown", a type tag and a switch
  per column (the green squares were checkboxes).
- **Export CSV** (`settings_export.png`): Export all clients (soft), Download template.
- **Backup & restore** (`settings_backup.png`): Choose backup folder (soft); Office recovery kit and
  security card (Export recovery kit, Change password; the admin-PC rows the code adds); Danger zone:
  Restore from backup… (red, asks first).
- **Purge duplicates** (`settings_purge.png`): Danger zone: Run deduplication… (red, asks first).
- Settings search box, nav groups, Close → same.

### Audit log (`dlg_audit_log.png`)
One filter row (search, Action ▾, Staff ▾, Date ▾ with the five presets and Custom range From / To,
Refresh, Export CSV…), count and range under it, white grid with action pills and readable times, a
details card on the right that fills when a row is picked, workstation list with counts, aggregator
state as a pill. COMPACT: the workstation column folds away, Service and Detail leave the grid (the
card shows them).

Where every control went: title → dialog header; "Host Aggregator Active" button → status pill; Close
→ ✕ (and the window's own close); Workstations list (All Workstations (this office), per host) → left
list with counts; search → same; Action combo (All Actions + 17 types) → Action ▾ chip; Staff/Actor
box → Staff ▾ chip (type a name); Refresh → icon button; Export CSV… → soft button; Date Range preset
(All Time, Today, Yesterday, Last 7 Days, Last 30 Days, Custom Date Range) + From / To → Date ▾ chip
with From / To inside Custom; "N log entries" → count line; grid (ID, Date & Time, Actor, Action,
Client, Service, Detail) → same columns, white grid; Audit entry drill-down box → details card; Copy
Details → Copy details; Filter Client → Show only this client.

### Sera Sync (`dlg_sera_sync.png`, plus the groups the code shows on an office PC)
A one-line status strip, three tabs on the left (Devices, Office members, Sync status) and the Live
activity card always on the right. Devices are cards with all seven facts and a tick; the empty table
becomes a sentence; Sync selected (N) is the one primary. The network warning is an amber banner.

Where every control went: title → dialog header; "🟢 N devices online" → strip; network warning label
→ amber banner; Discovered LAN Devices table (Username, Hostname, IP, App Version, DB Modified,
Clients / Dumps, Mode / Status) → device cards, right-click menu kept; Refresh → Scan again; Add PC by
IP, Remove PC by IP, Rejoin office, Export recovery kit → Devices tab buttons; Sync selected / Sync to
all (hidden source of truth in the current code's selection logic) → primary / secondary; Office
Members table (Name, Online, Role, Last successful sync, This PC), members warning, Add workstation,
Remove, Hand over admin, Become admin → Office members tab; Sync Status group (Pending outgoing /
Parked summary, warning label, Shadow mode label, Start shadow mode, Reset shadow mode, Go live,
Shadow checks label, Run now, Conflicts table, Keep, Use discarded value) → Sync status tab; Live Sync
Activity Stream + Clear → Activity card; Close → footer and ✕. Add workstation code window, Add / Remove
PC by IP prompts, Become admin password prompt and all confirmations → same dialog frame (not drawn).

### MCL manager (`dlg_mcl_manager.png`) and column edit (`dlg_mcl_column_edit.png`)
Manager: table with type and role tags; Add column (primary), Edit, Move up / down (icon buttons with
tooltips), Delete… set apart in the footer, Close. Editor: labels above fields; Column label; Field
type (Text, Number, Alphanumeric, Password / Secret, Date, Dropdown, ID); Dropdown options (drawn
disabled; shown only for Dropdown); Display identity column; Internal primary key anchor; Cancel; Save
column. Every control kept.

### Service manager (`dlg_service_manager.png`) and edit (`dlg_service_edit.png`)
Manager: table with Service, Automation mode, Mode 2; Add service (primary), Edit, Delete… in the
footer, Close. Editor: Service name, Login URL, User ID column, Password column, Automation mode (Fast
Autofill, SMTI Manual Assist, MECP Manual Copy; help in the tooltip), Automation mode 2 (None + the
three), Extension login flow (Two-step, Single page), Browser (Default, Chrome, Edge, Firefox; in code,
cut off in the screenshot), Cancel, Save service. Every control kept.

### CSV import (`dlg_csv_import.png`)
Rules as one info banner; file row as a drop area with Browse CSV…; Column mapping preview table (CSV
header → Maps to MCL column) with an empty state; Cancel; Import data (disabled until a file is
chosen). The Import summary dialog (new, updated, warnings, Done) uses the same frame; not drawn.

### Change master password (`dlg_change_master_password.png`)
Current, New (min 8), Confirm; the note; Cancel; Change password. Opens from Backup & restore.

### Office recovery (`dlg_office_recovery.png`)
Master password with eye; wrong-password banner (hidden until needed, shows attempts remaining);
Restore from recovery kit file…; Exit; Unlock.

### Rejoin office (`dlg_rejoin_office.png`)
Explanation as an info banner; old master password (drawn disabled: asked only when the saved one
fails); Admin PC address; Pairing code; This PC's name; Cancel; Rejoin; error line. The follow-up
"Import this PC's data" dialog (counts, per-column checkboxes, Import / Not now / Don't import) uses
the same frame; not drawn.

### First run (`dlg_first_run.png`)
Two choice cards (Create a new office, Join an existing office) and Exit. The two next pages from the
code are drawn behind mockup-only step tabs: Create (Office name, Master password, Confirm, Back,
Create office) and Join (Scan LAN, peers table Office / Admin PC / IP, manual IP, Pairing code, status
line, Back, Pair & join). The admin-side "Workstation join request" prompt (Allow / Reject, timer) uses
the same frame; not drawn.

### Force update (`dlg_force_update.png`)
Amber header (attention, not danger), version pills, Release notes card, progress and status line,
Exit application (asks first), Update now (primary). Cannot be closed; Esc ignored.

### Start-up loading (`dlg_startup_loading.png`)
Real logo, "Project Sera", the status line, emerald progress, on the dark dialog frame.

### Legacy settings dialog (`dlg_settings_legacy.png`)
Dropped (owner decision: not a live feature). Not drawn; the build removes `settings_dialog.py` from
the screen list after confirming nothing opens it.

## Departures from `Mockups/redesign/index.html` and the 01–06 specs

1. **Tracker Dump keeps its dark table, as a tone of the one grid component.** The first draft made it
   white; the owner chose to keep both tones. Header, zebra, selection and row buttons are shared.
2. **Sera Sync: three tabs plus an always-visible activity column**, not four tabs. The 04 spec drew
   cards only for devices; the redesign HTML drew four tabs with activity as a tab. Today the stream is
   always visible and staff watch it while syncing, so it stays on the right; the admin groups (Members,
   Status) that do not fit at 1366 × 768 go into tabs.
3. **Audit log keeps all seven grid columns in WIDE** and a fixed details card, instead of a slide-in
   drawer that hides Service and Detail. The drawer only trades space in COMPACT, where Service and
   Detail do leave the grid.
4. **Sidebar: "All Clients" and "Search" are one entry**, as the redesign proposed, because both call
   `go_to_search` (confirmed by the owner). Audit Log stays in Clients, a new "Display" entry sits
   under Tools, and Manage Staff Users is dropped (not a live feature).
5. **Settings keeps the footer Close + Save** beside the unsaved bar (IMPLEMENTATION 4.4 asks for the
   footer Save to stay), and the unsaved bar is amber (attention), not emerald, so the one emerald
   primary on the page stays the Save button.
6. **Theme row stays**, showing "Dark" only (decided 2026-10-05); the redesign notes said "Theme row
   removed".
7. **Screen scale** is a new setting row in General (per the display-scale plan) and a sidebar entry;
   neither existed in the earlier redesign.
8. **Client panel ⋯ menu** holds Services / Archive / Delete while the panel is open (as the redesign
   notes said) and the panel is 540 px, slightly wider, so Identity and Logins keep two columns at 1366.
9. **Danger zones** are red-edged cards (as in the redesign HTML) but the destructive buttons say
   they ask first ("Restore from backup…", "Run deduplication…") so nobody fears a one-click wipe.
10. **Chips never wrap in WIDE**; in COMPACT they move to their own row (the plan's simpler option),
    the same on All Clients, Tracker Dump and Manage Clients.

## Not redesigned

- **SGT lab** (`dlg_sgt_lab.png`) and the **SDIS** dialogs (`sdis_dialog.py`, `sdis_containers_dialog.py`)
  are out of the overhaul. They keep their current look; the Settings → Tracker page still links to the
  SGT lab.
- Also not drawn (same frame, no new layout): Add Client dialog, Admin PIN dialog, Container inspector,
  Create client from capture, Map datapoints to MCL columns, Import summary, Import this PC's data,
  Workstation join request, Add workstation code window, Shadow start dialog, SCA diagnostics, LTT
  rules, the ordinary (non-mandatory) update dialog, and every QMessageBox confirmation. Each gets the
  dialog frame recipe of `IMPLEMENTATION.md` section 7.

## Ideas (not drawn; no new features in the mockup)

- A "Recent" group at the top of All Clients (last five opened clients) for the search-and-open loop.
- Keyboard focus ring on grid cells that matches the mint selection, and a visible "copied" flash on
  the cell that was Ctrl+C'd.
- In the client panel, a per-service "last autofilled" time under the service name.
- Tracker Dump: a saved-filter chip ("My clients") per staff member.
- Audit log: an "Only this PC" quick chip beside Staff.
- Settings search that filters rows on the page, not only the page list.

## Open questions for the owner

None. All questions were answered on 2026-10-10:

- The first six are in the Decisions table above.
- **Audit log** stays admin-only, as today and as drawn.
- **Email and TAN** types are part of the EAV column schema and are changed in Settings → Columns when
  wanted; not a design matter. The Columns table simply shows whatever type a column has.
- **Sera Sync ahead / behind label**: redesign only, no protocol change; the label is not built.
