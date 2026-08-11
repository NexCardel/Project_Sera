# Project Sera — Visual Redesign Blueprint
### "Amas-Sera Version Sky" — Phase 1

This blueprint turns your notes (sidebar structure, right-side panel pattern,
admin-gating rules) and the reference mockups into an implementation plan
that **never leaves the app in a broken state**. Every task below is additive
or swaps one working piece for another working piece — nothing is deleted
until its replacement is verified.

---

## 0. Ground rule: what does NOT change

This is a **UI-layer rewrite only**. The following are untouched, in full:
`database.py`, `security.py`, `automation.py`, `drs.py`, `native_host/`,
`sera_extension/`. Every existing DB method, every audit-log call, every
autofill/manual-copy code path keeps working exactly as-is — the new UI
calls the same backend functions the old UI called.

Confirmed from the actual code: `AdminWindow`'s actions (`_on_manage_mcl`,
`_on_manage_services`, `_on_import_fps`, `_on_view_audit_log`,
`_on_export_csv`, `_on_backup`, etc.) already exist as independent methods,
not tied to the toolbar buttons that currently trigger them. That means the
new sidebar can call the *same* methods — this is a rewire, not a rewrite,
of admin functionality.

---

## 1. Design tokens (sampled directly from your reference images)

| Token | Value | Source |
|---|---|---|
| `--sera-red` (sidebar / primary) | `#FF4D49` | pixel-sampled from Color Palette ref |
| `--sera-mint` (primary CTA / active states) | `#4CF9B7` | "Add Client" button, sampled |
| `--sera-mint-tint` (row hover/selection) | `#BFE3D7` | selected row wash, sampled |
| `--sera-beige-bg` (page background) | `#F3ECDD` | **replaces** the flat `#D9D9D9`/`#DBDBDB` gray in the mockup, per your instruction |
| `--sera-beige-panel` (card/table surface) | `#EAE1CB` | deeper beige, for panels sitting on top of the page bg |
| `--sera-ink` (primary text) | `#241F1B` | warm near-black, pairs with beige instead of cool slate |
| `--sera-ink-muted` (secondary text) | `#5C5347` | warm gray-brown, replaces `#616161`/`#94a3b8` |
| `--sera-border` | `#D8CDB4` | warm border tone to replace `#cbd5e1`/`#e2e8f0` |

Status tag colors (already defined in `tag_widget.py`, kept as-is since they're
functioning color-coding, not part of the chrome redesign): Submitted green
`#2e7d32`, Pending red `#d32f2f`, In-Progress amber `#f57f17`, Overdue
`#880e4f`.

**Dark mode**: keep the existing `DARK_STYLESHEET` slate palette as the base,
but swap its accent to `--sera-mint` for primary actions/active nav, so the
same component logic (button styles, tag styles) works in both themes without
duplicating every rule.

---

## 2. App shell architecture (replaces the top-level `QStackedWidget`)

Today, `main.py` puts four full-window classes (`SearchWindow`,
`ClientDetailWindow`, `AdminWindow`, `DashboardWindow`) into a
`QStackedWidget` and swaps the whole screen on navigation. The new shell
instead has three **persistent** regions:

```
┌──────────┬─────────────────────────────┬──────────────────┐
│          │                              │                  │
│ Sidebar  │      Content Area            │   Slide Panel     │
│(persist- │  (stacked pages, one         │  (hidden by       │
│  ent)    │   visible at a time)         │  default, slides  │
│          │                              │  in from right)   │
│          │                              │                  │
└──────────┴─────────────────────────────┴──────────────────┘
```

- **Sidebar** — new `ui/shell/sidebar.py`. Persistent nav, changes its item
  set based on Admin Mode on/off (§3).
- **Content Area** — a `QStackedWidget` *internal* to the shell, holding
  pages: Search (default/home), Audit Log, MCL Manager, Service Manager,
  Filing Types Manager, etc. Each existing admin screen becomes a page here
  instead of a floating `QDialog`.
- **Slide Panel** — new `ui/shell/slide_panel.py`, a reusable right-anchored
  overlay (~50% width, animated slide-in). Used for **Client Detail** and,
  per your note, for the admin dialogs that should "come out the same way as
  the client-detail window" — New/Edit Client form, Import CSV, Import
  Filing Periods, Backup/Restore, Settings. Each keeps its own internal
  content class; only the outer container changes from `QDialog` to a
  `SlidePanel`-hosted widget.

**Why this doesn't break anything**: `SearchWindow.client_selected`,
`ClientDetailWindow.load_client()`, `AdminWindow.refresh()`, and all other
existing signals/methods keep their names and behavior — they just get
mounted into the new shell's content area / slide panel instead of being
swapped via `QStackedWidget.setCurrentIndex()`. `main.py`'s signal-wiring
block (lines 179–193) is the only part that needs real rework, and it's
rework of *wiring*, not of the widgets themselves.

---

## 3. Sidebar specification

### 3.1 Outside Admin Mode

```
[Search]                 ← default/home page
[Import]
    New Clients
    Download CSV Template
[Purge Duplicates]
[DRS]
─────────────────────    (spacer, pushes below to bottom)
[Settings]
[Restore DB]
```

Per your note, **Settings and Restore DB stay pinned at the bottom** in both
modes — implemented as a spacer/stretch in the sidebar layout separating the
main nav group from a fixed footer group, so they never get pushed around as
other items are added.

### 3.2 Admin Mode ON (sidebar rearranges)

Everything from §3.1, **plus**:

```
[Search]
[Import]
    New Clients
    Download CSV Template
[Purge Duplicates]
[DRS]
[Audit Log]
[Manage MCL]
[Manage Services]
[Filing Types]
    Manage Filing Types
    Import Filing Periods
[Export CSV]
[Backup DB]
─────────────────────
[Settings]
[Restore DB]
```

The exact grouping/order of the admin-only additions (Audit Log / Manage MCL
/ Manage Services / Filing Types / Export CSV / Backup DB) is left as a
visual-polish decision during implementation — your note says "arrange
according to how good it looks." The one hard constraint is Settings and
Restore DB always last.

### 3.3 Wiring (non-breaking)

Each sidebar item maps directly to an existing handler:

| Sidebar item | Existing method (unchanged) |
|---|---|
| Audit Log | `AdminWindow._on_view_audit_log` |
| Manage MCL | `AdminWindow._on_manage_mcl` |
| Manage Services | `AdminWindow._on_manage_services` |
| Manage Filing Types | `AdminWindow._on_manage_filing_types` |
| Import Filing Periods | `AdminWindow._on_import_fps` |
| Export CSV | `AdminWindow._on_export_csv` |
| Backup DB | `AdminWindow._on_backup` |
| Restore DB | `AdminWindow._on_restore_backup` |
| Import → Download CSV Template | `AdminWindow._on_download_template` |
| Import → New Clients | `AdminWindow._on_import_csv` |
| Purge Duplicates | `AdminWindow._on_purge_duplicates` |
| Settings | `AdminWindow._on_open_settings` |

---

## 4. Top action bar (New / Save / Archive / Attach-Detach Services)

Per your note: these buttons sit **above the search bar**, not below it like
the Gemini mockup showed.

```
┌ [+ New] [💾 Save] [🗄 Archive] [🔗 Attach/Detach Services] ─────────┐
│ [Search box...........................] [Clear] [Service ▾]        │
├──────────────────────────────────────────────────────────────────┤
│  results table                                                     │
```

**Visibility rule** (per your note — "no delete or edit option outside
admin mode"): this entire action bar is **Admin-Mode-only**. New, Save,
Archive, and Attach/Detach Services are all mutating actions, same category
as Delete/Edit, so the same gate applies. Outside Admin Mode, the screen
shows only the search bar + results table — browse, autofill, manual copy,
quick-copy, and view remain available, nothing that mutates client records.

---

## 5. Right slide-in panel pattern (reusable component)

New `ui/shell/slide_panel.py` — a shell, not a rewrite of what's inside it:

```python
class SlidePanel(QWidget):
    closed = Signal()
    def set_content(self, widget: QWidget, title: str): ...
    def open(self): ...   # animates width 0 -> target
    def close_panel(self): ...  # animates back to 0
```

Each of the following keeps its **existing internal class and logic**;
only its container changes from `QDialog.exec_()` to
`slide_panel.set_content(existing_widget_instance, title)`:

- `ClientDetailWindow` (already spec'd from your reference — Identity &
  Contacts / Security Credentials / Service Management sections)
- `AuditLogWindow`
- MCL Manager, Service Manager, Filing Type Manager dialogs
- `FilingPeriodImportDialog`, `CSVImportDialog`
- `SettingsDialog`
- Backup/Restore confirmation flows

This is the change that also **fixes the truncated-button and truncated-column
bugs** from the earlier screenshots: moving from fixed-width `QDialog`s to a
panel that's ~50% of a maximized window gives every button and column
meaningfully more horizontal room, on top of the width fixes in §6.

---

## 6. Component-level fixes (carried from the earlier code/screenshot review)

- **Button truncation** (`"Sho"`, `"Cop"` in Client Detail): replace fixed
  `setFixedWidth(70)`/`(65)` with `sizeHint()`-based sizing or a minimum
  width computed from font metrics, same fix pattern the codebase already
  uses for table columns (`search_window.py`'s `resizeColumnsToContents`
  logic can be reused here).
- **Icon replacement**: swap emoji (`👁️`, `📋`, `🔄`, `🍷`) for a proper icon
  set — recommend `qtawesome` (installable via `pip`, MIT-licensed, ships
  Font Awesome icons as native `QIcon`s, no internet needed at runtime).
  Fixes both the truncation (icons are fixed-width, unlike emoji) and the
  stray "wine glass for OVERDUE" issue flagged earlier.
- **Service badges**: replace the comma-joined `"GST, Income Tax, Email"`
  text column with small icon+label chips (as in the reference mockup),
  shrinking the oversized "Services" column from the current screenshots.
- **Theme consolidation**: every `setStyleSheet()` call currently inline in
  `search_window.py`, `dashboard_window.py`, and the dialogs gets removed in
  favor of QSS object-name selectors added to the central `theme.py`. This
  is the fix for dark mode being broken on the main grid and dashboard
  cards (flagged earlier — hardcoded `#ffffff`/`#000000` overriding the
  theme).
- **Empty states**: FPS Import / CSV Import dialogs get a placeholder
  message ("Select a file to preview its contents") instead of blank white
  space before a file is chosen.

---

## 7. Notification & Feedback UX (reducing popup interruptions)

The codebase already contains the right instinct in one place and the wrong
one in another. `client_detail_window.py` has a working non-blocking toast
(`_toast_label`, auto-hides after 2.5s) used for clipboard-copy confirmations
— but two lines away, "Notes Saved" still uses a blocking `QMessageBox` that
needs a click to dismiss. Elsewhere, `admin_window.py` has a developer
comment that says the quiet part out loud:

```python
# FIX: Added actual popups to tell you it worked!
QMessageBox.information(self, "Success", "Client created successfully.")
```

That instinct — "make sure they know it worked" — is right, but a modal
dialog is the heaviest possible way to say it. The fix is a **shared toast
component**, not more dialogs.

### 7.1 Decision framework

Every existing `QMessageBox` call falls into one of four buckets:

| Bucket | Rule | UI treatment |
|---|---|---|
| **A — Pure success confirmation** | Action already completed, nothing more for the user to decide | Toast (non-blocking, auto-dismiss) |
| **B — Preventable no-op** | "No selection," "No services defined," "Multiple clients selected" — the user did something that was never going to work | **Don't show it at all** — disable the button/action so it can't be triggered in the invalid state |
| **C — Destructive or high-stakes confirmation** | Delete, Archive, Purge, Restore DB (overwrites the synced live database) | Keep as a modal Yes/No — restyled to match theme, not default OS chrome |
| **D — Genuine error** | Something failed and the user needs to know why (bad JSON, wrong PIN, missing credentials, DB error) | Keep as a modal — this is the one case a popup is still the right call |

### 7.2 Concrete conversions (representative, not exhaustive)

**→ Toast (Bucket A)**
- `settings_dialog.py` "Settings Saved"
- `admin_window.py` "Client created/updated successfully" (the commented `# FIX`)
- `admin_window.py` "Backup complete," "Export Complete," "Download Complete" — path shown in the toast body, slightly longer duration (~4s) since there's more to read
- `admin_window.py` "No Conflicts," "No Duplicates Found"
- `audit_log_dialog.py` "Exported successfully"
- `client_detail_window.py` "Notes Saved" — literally just reuse the existing `_toast_label` that's already two lines away
- `main.py` "Saved filing record for period X"

**→ Eliminated entirely (Bucket B)**
- "No selection" → bulk action buttons (Archive/Restore/Delete/Attach Service) are simply disabled until a row is selected
- "No services" → Attach/Detach button disabled until at least one service exists
- "Multiple clients selected" for the edit form → Save button disabled (or form itself disabled) when the selection count isn't exactly 1, with a small inline hint text instead of a click-then-get-told-off popup
- Same principle applies to field-level validation ("Missing Name," "Missing Label," "Missing Options," "PIN must be at least 4 characters") — these become **inline red-text hints under the field**, shown live as the user types/blurs, not a popup after they hit Save

**→ Stays modal, restyled (Bucket C)**
- Delete service / delete MCL column
- Archive / purge duplicates
- Bulk attach/detach services
- Restore DB from backup — if anything, this one should get **more** friction given it overwrites the live synced database, e.g. requiring the admin to type the word "RESTORE" to confirm, not less

**→ Stays modal, as error (Bucket D)**
- Invalid JSON / empty CSV / empty FPS file
- Incorrect Admin PIN
- Missing credentials for autofill
- Autofill failure
- Database error on startup
- Restore/backup/export failure

### 7.3 The shared Toast component

New `ui/shell/toast.py`, generalizing the existing `_toast_label` pattern
into something every screen can call instead of each screen rolling its own:

```python
class Toast(QWidget):
    def show_message(self, text: str, kind: str = "success", duration_ms: int = 2500): ...
    # kind: "success" (mint) | "error" (red) | "info" (neutral)
```

- **Placement**: bottom-anchored inside the Content Area for page-level
  actions (e.g. "Client created"), or bottom-anchored inside the Slide
  Panel when the action happened there (e.g. "Notes Saved") — never
  center-screen, never steals focus.
- **Non-blocking**: keyboard focus stays exactly where it was; the user can
  keep typing/navigating while it's showing.
- **Stacking**: at most one toast visible at a time; a new one replaces the
  current one rather than piling up.
- **Duration**: 2.5s default (matches the existing clipboard-copy toast),
  4s for anything that includes a file path or count worth reading.

### 7.4 Net effect

Of the ~50 `QMessageBox` call sites cataloged in the current codebase,
roughly 15 become toasts, ~6 are eliminated outright by disabling the
triggering control, and the rest — genuine destructive confirmations and
genuine errors — stay exactly where popups belong. That's the target: fewer
interruptions, not zero feedback.



## 8. Phased delivery plan

Per your note — *"do task 1 first before everything else"* — navigation
comes before visual polish. Each task below leaves the app fully usable;
nothing is torn out until its replacement works.

### Task 1 — Sidebar shell + navigation (do this first)
- Build `ui/shell/sidebar.py` and the 3-region `AppShell` container.
- Wire sidebar items to the **existing** `AdminWindow` handler methods
  (§3.3) — no new business logic, just new entry points into old code.
- Both sidebar states (§3.1 outside-admin, §3.2 admin-on) working.
- App is navigable end-to-end via sidebar; visuals can still look rough.

### Task 2 — Design tokens & theme consolidation
- Add the tokens from §1 to `theme.py` (light + dark).
- Sweep and remove inline `setStyleSheet()` calls across `ui/`, replacing
  with QSS selectors in the central stylesheet.
- Beige swap applied everywhere the old flat gray background would've been used.

### Task 3 — Slide panel shell + Client Detail migration
- Build `slide_panel.py`.
- Move `ClientDetailWindow` into it, restyled per §3 of the earlier analysis
  (Identity & Contacts / Security Credentials / Service Management sections,
  icon+text Show/Copy buttons at full width).
- Verify: autofill, manual copy, quick-copy, notes-saving, DRS panel all
  still function identically.

### Task 4 — Migrate remaining admin dialogs into the slide panel
- One dialog at a time: Audit Log → MCL Manager → Service Manager → Filing
  Type Manager → FPS/CSV Import → Settings → Backup/Restore.
- Each migration is independently testable and revertable (old `QDialog`
  class stays importable/usable until its panel version is verified).

### Task 5 — Top action bar relocation + admin gating
- Move New/Save/Archive/Attach-Detach Services above the search bar (§4).
- Apply the admin-only visibility rule.
- Confirm outside-admin mode has zero mutating controls, per your note.

### Task 6 — Table, button, and icon polish pass
- Button/column truncation fixes, `qtawesome` icon swap, service badge chips
  (§6).

### Task 7 — Notification & feedback UX pass
- Build `ui/shell/toast.py` (§7.3).
- Convert Bucket A `QMessageBox` calls to toasts, per the table in §7.2.
- Eliminate Bucket B popups by disabling their triggering controls instead.
- Restyle Bucket C/D dialogs to theme (still modal, just not default OS chrome).

### Task 8 — Cleanup
- Remove the now-unused `QStackedWidget` top-level swap and any dialog
  classes fully superseded by their slide-panel versions.
- This is the *only* deletion step, and only after Tasks 1–7 are verified.

---

## 9. Non-breaking checklist (applies at the end of every task)

- [ ] App launches, master password + staff label prompts unaffected
- [ ] Search, filter, and keyboard nav (↓ into table, Enter to open) still work
- [ ] Autofill + manual copy + quick-copy + clipboard auto-clear still work
- [ ] Audit log still records every action with correct attribution
- [ ] DRS status changes still write to `client_filing_status` correctly
- [ ] Admin PIN still gates CRUD/backup/restore/export/audit-log access
- [ ] Backup/Restore + Syncthing file layout (`master.db`, `sera.salt`) unchanged
- [ ] Light and dark theme both render correctly on every touched screen
- [ ] Every destructive action (delete/archive/purge/restore) still confirms before acting; no confirmation was silently dropped in the toast conversion
