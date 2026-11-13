# UI overhaul — merge-readiness report (F-R)

Branch `ui-overhaul` (worktree `../APP-ui`) against `main`, reviewed 2026-10-12 by claude-opus-5-5.
**Merging is the owner's call.** This report only says what a merge needs. Nothing was merged, rebased or pushed.

## Verdict

**Ready to merge after two manual conflict fixes (below) and the human checks you choose to run first.**
Every screen in the plan is built on the kit. The behaviour-lock tests (R1–R9) pass. No colour literals are left in
any screen file. Nothing outside the UI changed, apart from the dispatcher tool noted below.

## 1. What the branch changes

77 files outside `Mockups/` (+18,052 / −7,317 lines). In detail:

| Area | Files |
| :--- | :--- |
| New kit + tokens | `ui/components/kit.py`, `ui/utils/tokens.py`, `ui/utils/responsive.py` (WidthAware) |
| Rebuilt screens | search, client detail, admin (Manage Clients), tracker dump windows; settings hub; audit log; Sera Sync; MCL / service managers; CSV import, first run, office recovery, rejoin, change password, update, loading, SCA diagnostics, LTT rules, display scale |
| Shell | `ui/shell/sidebar.py`, `slide_panel.py`, `app_shell.py` (width from token) |
| Removed | `ui/dialogs/settings_dialog.py` (legacy dialog that no code opens; see conflict 1) |
| `main.py` | `QMessageBox` → `kit.MessageBox` (same buttons and results), the SGT demotion prompt via `MessageBox.choose` (Esc = keep), dropped the dead `action_manage_staff` connection |
| Tests | About 25 new UI test files (kit, lock R1–R9, screens, rules), 12 existing files retargeted (`MessageBox`, roles instead of cell widgets) |
| Tools | `tools/ui_gallery.py` (new), `tools/ui_snapshots.py` (font dir, no legacy dialog) |

**Out-of-scope check (rule 7):** `version.json`, installers, the sync protocol, SGT/VSDC/SCA capture code, the browser
extensions, `sgt_lab_dialog.py`, `sdis_dialog.py` and `sdis_containers_dialog.py` are **unchanged**.
One file outside the UI changed: `tools/sgt_overhaul.py` (+13 lines: a "Models" sheet in the tracker workbook and
extra network-error wording for the agent runner). It came from the fleet dispatcher, not from a screen WP. It is
harmless, but you may prefer to commit it to `main` separately.

## 2. Conflicts with `main` (3 commits ahead since the branch point `42f9998`)

`main` has `cf1f943` (tracker complete-lock), `caf4db3` (ARN fixer switch) and `7c670d4` (tracker latest period).
Only two files overlap:

1. **`ui/dialogs/settings_dialog.py` — modify/delete.** `caf4db3` added the "Run the ARN fixer automatically"
   checkbox (`arn_autofix_enabled`) to the legacy dialog. That dialog is never opened on `main` either: only
   `UnifiedSettingsDialog` is opened, so the switch is unreachable there today. **F-R added the switch to this branch:**
   Settings → General → Start-up card, kit `Switch` `arn_autofix_check`, same key, same default (on), staged and
   saved like the other General settings. A test and the golden settings file cover it.
   **Resolution:** keep the deletion (`git rm ui/dialogs/settings_dialog.py`).
2. **`ui/windows/tracker_dump_window.py` — one hunk, textual.** `7c670d4` changes how the Filings & History cell
   gets its text (it shows the latest period, with "Assessment Year" shortened to "AY"). The branch changed the line
   above it (`_get_item(..., color="#58A6FF")` → `self._table_item(row_idx, self.COL_PERIOD, K["blue_tx"])`).
   **Resolution:** keep the branch's `hist_item = self._table_item(...)` line and take main's two new lines:
   ```python
   latest = str(r.get("latest_period") or "").strip()
   hist_item.setText(re.sub(r"\bAssessment Year\b", "AY", latest) if latest and latest != "N/A" else _filings_short(period_sum))
   hist_item.setToolTip(f"Latest period captured: {latest}\n{period_sum}" if latest else period_sum)
   ```
   (`re` is already imported.) After merging, run `tests/test_tracker_arn_kept.py`, `test_tracker_complete_lock.py`,
   `test_ui_lock_tracker.py` and `test_ui_tracker_kit.py`.

All other `main` changes (`core/sdis`, `core/sgt`, `sera_db`, their tests) do not touch files this branch changed.

## 3. W8-R sweep

**Hard-coded colours.** `tests/test_ui_overhaul_rules.py` (76 cases) forbids literal `setStyleSheet`, hex and
`QMessageBox` in every screen file and `main.py`. A grep of `ui/` finds hex only in:
- `ui/utils/tokens.py` and `theme.py` (the token source and the stylesheet built from it);
- data palettes: the user's cell-format colours in `search_window.py`, and the tracker status and portal colours in
  `tracker_dump_window.py` (stored or shown data, allowed by the rules test);
- `kit.py`: only the `rgba()` helper for disabled states;
- out of scope: `sgt_lab_dialog.py`, `sdis_dialog.py`, `sdis_containers_dialog.py`, `vsdc_hud_pill.py` (capture HUD);
- **left over (not fixed):** `ui/components/toast.py` (the app's `SeraAlert` toast still uses the old Material
  greens and reds; K-3 left swapping `AppShell.alert` for `kit.Toast` to the shell work, and no WP did it), and
  `ui/utils/tag_widget.py` (status tags with old colours; nothing imports it, so it is dead code).
- **fixed in F-R:** `ui/utils/dynamic_form_widgets.py` drew the read-only ID field as a light-grey box inside the
  dark form. It now uses the panel, mint and line tokens.

**Screens against the recipe.** S-R compared every screen with `index.html` and ticked the README "where every
control went" rows. F-2 re-rendered the flat set in `Mockups/built/`. F-R spot-checked All Clients, Tracker Dump,
Manage Clients, Settings General and Display scale. F-R fixed the one defect F-2 found: the Display scale dialog
clipped its "Running at …" line. The row is now stacked and the dialog is 600 px wide; the re-rendered images at
100 % and 125 % show no clipping.

**125 % / 150 % scaling.** Offscreen renders at Sera scale 1.25 exist for every screen (subfolders of
`Mockups/built/`). Real Windows 125 % / 150 % scaling still needs a human (section 9, check 7; tracker checks 2, 33).

## 4. Tests

Full suite on this branch with the live plugin, 2026-10-12: **3643 tests: 3505 pass, 137 fail, 1 skipped.**
None of the 137 failures comes from a file this branch changed:

- **132 database-fixture failures.** In `sync_rejoin`, `sync_golive`, `sync_restore`, `sync_shadow_start`,
  `clipboard_assist`, `raw_payload_db_and_srpf`, `startup_speed::TestSearchGridReuse` (5), `sync_apply`,
  `sync_capture`, `sync_hotfix`, `sync_maintenance`, `sync_tables`, `audit_log_v2`, `cell_formatting`, `id_field`,
  `dual_pk`, `undo_redo_formatting` and `syncthing_restore`, the fixtures fail while seeding the throwaway
  database. Either `add_client` refuses a client because "Internal Primary Key 'PAN' / 'NAME OF COMPANY' is
  mandatory", or the default services have no `EPFO`. The branch does not change `sera_db/`, `database.py` or
  `core/`. Earlier S-package notes (section 11) already list these as known.
- **1 SDIS failure:** `test_sdis_tables::test_command_line_prints_no_values` (`core/sdis`, out of scope, untouched).
- **4 UI tests fail only when run in the full suite and pass on their own** (each file rerun separately):
  `test_ui_kit` checkbox and grid paint, `test_ui_kit_frames` slide panel, and `test_ui_search_selection_bar` compact
  width. The kit elision tests leak state in the same way when run after other UI files. Section 11 lists this as
  "kit elision order leaks (pass alone)".

The checks for this WP pass: `test_ui_lock_settings` (with the new ARN fixer switch and its golden file),
`test_ui_lock_display`, `test_ui_overhaul_rules`, `test_memory_tuning`, and the startup-speed tests other than the 5
fixture failures. **After merging, compare the failure list against this one.** Any failure not listed here is new.

## 5. Owner decisions still open

1. **Passwords in the All Clients grid show in clear**, as on `main`. The new Columns ▾ menu makes it easier to show
   those columns. Should the grid follow Password Masking Mode? (S-R raised this; not changed.)
2. **`SeraAlert` toast** keeps its old colours. Swap it for `kit.Toast` (same API) in a follow-up?
3. **Subfolder goldens** (`Mockups/built/{search,settings,dialogs,…}/`) are from the S-package runs, not re-rendered
   at HEAD. They are reference images, not test inputs, so no test depends on them.
4. **`tools/sgt_overhaul.py`** change on this branch: keep it here or move it to `main`.
5. Behaviour changes already recorded in the decisions file: Force update "Exit application" now asks first;
   Delete in All Clients is disabled with no selection; the legacy settings dialog is removed; "Manage Staff Users"
   is gone from the sidebar (Sera Sync is still reachable).

## 6. Human checks before or after merging

33 checks are in `Mockups/overhaul/ui-overhaul-checks.csv`. All are "Not run". Check 32 (Display scale clipping)
was fixed offscreen by F-R. The ones that matter most for a merge:

- **2 / 7 / 33:** every screen and dialog on a real 1366×768 laptop at Windows 125 % and 150 %.
- **3:** real keyboard: Ctrl+= / − / 0, Alt+n, Alt+Ctrl+n, Ctrl+B, F5, Tab order.
- **1 / 18:** All Clients actions (edit, archive, delete, services) act on the clicked client.
- **20 / 21:** the clipboard wipe after 30 s, and Alt+n autofill in a real browser.
- **27:** Sera Sync between two real PCs.

## 7. How to merge (owner)

```
git checkout main
git merge --no-ff ui-overhaul
git rm ui/dialogs/settings_dialog.py          # conflict 1
# edit ui/windows/tracker_dump_window.py      # conflict 2, as in section 2
git commit
```
Then run the full suite with the live plugin (CLAUDE.md) and compare with section 4.
