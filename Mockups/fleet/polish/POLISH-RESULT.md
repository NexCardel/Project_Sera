# Fleet polish: result (P-R hand-off)

Branch `ui-overhaul` in the worktree `APP-ui`, commits `2e38689` (P-1) to `b1a8995` (P-11 part). Nothing pushed,
nothing merged. Plan: `Mockups/fleet/polish/POLISH-PLAN.md`. Owner answers in that plan's last section stand, except
decision 1 (password columns), which the owner withdrew on 2026-10-14: they keep their current display.

## Status by package

| WP | Status | Commit | What changed |
| :--- | :--- | :--- | :--- |
| P-1 | done | `2e38689` | Tokens from the mockup's CSS (`KIT`): icon boxes 14 / 16 / 20, `icon_scale` 1.14, `h_count` 21, page rhythm 16/20/12 (COMPACT 12/14/10), gap 12, tile numbers 22 / 18, label 17, client panel, Settings, Manage Clients. `f_chip` 12 → 13. `tests/test_ui_tokens.py` checks each size against the mockup CSS in `index.html`. |
| P-2 | done | `a96e64a` | `_icon()` passes `scale_factor=icon_scale` to qtawesome. IconButton, Input, Banner, UnsavedBar, Toast, cell actions at 16 px; IconTile and EmptyState at 20. LabelledAction icon-to-text gap 7 px (transparent 3 px pad). Theme leak guard: action, icon and NavItem sheets drop the dialog `min-height: 30px`; `::menu-indicator` off. PageHeader count pill 21 px, centred. Tile 55 px. FieldLabel sentence case, muted, 4 px below. Chip chevron stroked. Banner / Toast gap 10. |
| P-3 | done (scope changed) | `b0bd592`, `2839a91` | `LabelledAction(chevron=)`, `NavItem(edge_inset=)`, `dot_icon()`, orange Tile tone, `PageHeader(title_px=)`. Added `Tabs` and `KeyValueGrid` (the plan had them here; they are used in P-13 and P-12). Tinted list-mode service pills landed in P-8. |
| P-4 | done | `88de5a0` | Sidebar edge bar (inset 10, scroll content with no left margin; per-row side padding), item spacing 0, `dot_icon` on the Auto-sync pill, `TabFocus` on IconButton and LabelledAction, shell alert is `kit.Toast`. |
| P-5 | done | `a3ddbcb` | `set_page_margins()` on All Clients, Tracker Dump and Manage Clients, with the COMPACT values through each screen's width-mode hook. Columns, Tools and More use the chevron option; Prev and Next use chevron icons (plan had a trailing chevron on Next; leading icon kept). |
| P-6 | done, password item withdrawn | `29c10cd`, `9f8eeab` | Company column regular weight, No. 56, proprietor max share 24 %, All chip count badge, footer hints (Enter / Esc). Panel-open grid fits the page left of the 540 px panel; GSTIN, Ph. no., PAN and Services hide under it. Grid fit corrects a rounding overflow. Password columns: **not changed** (owner, 2026-10-14). |
| P-7 | done | `2e88afc` | Status pills carry the dot. Row actions Inspect, Autofill, ⋮ (owner decision 3). Filing type and Captured by plain text. Need-a-client tile orange. Filter gap 10, search box 360, page-size select 72 px. |
| P-8 | done | `281704c`, next commit | New client is the emerald primary with a plus (decision 4). Sort caption, editor avatar 38 px (both kit widgets now), Save check. List sub-line `#id · PAN` with GSTIN and activity in the row tooltip. List-mode grids draw services as tinted pills (decision 9). The list column is 360 px (300 in COMPACT, `w_mc_list` tokens; was 390 to 460), which leaves 726 px for the editor at 1366, so the cards are two columns from 700 px (Identity, Portal logins / Contact, Attached services; Notes full width); one column below that. The form factory (`dynamic_form_widgets.py`) builds `kit.Input` and `kit.Select`; the date field takes the same frame. Eye and copy stay `QLineEdit` actions (the lock tests pin them), so `add_trailing` was not used. |
| P-9 | done | `2485b3b`, `5cae210` | Header: ID chip beside the name, Edit, ⋯ menu (decision 2); the activity pill sits on the meta line under the name (on the name row it was squeezed to "Created ..."). Services rows: 28 px service tile, name over its modes, actions, per-row Alt n key, hairlines, muted header hint. Identity and Logins on `KeyValueGrid` (one column below 400 px) with the masking, click-to-copy, eye and quick-copy-off rules unchanged; one "Not set:" line; Logins header states the mask mode and clipboard timeout. Labels sentence-cased (decision 5). Notes "✓ Saved" muted hint. |
| P-10 | done | `0ac3acc` | Settings: nav spacing 2, pages 18/24/24 with gap 12, 820 px card column, titles 19 px (`PageHeader.set_title_px`, decision 8), Manage Columns and Services toolbars back to the small size. Rendered and compared with `renders/set-general.png`. |
| P-11 | done | `b1a8995`, `9414ac4` | Change-password note is the muted `Caption`. CSV file row is a dashed `DropArea` (drop a .csv or Browse, secondary button); the mapping preview is one titled card holding the empty state or the table. |
| P-12 | done | `967fe72` | Audit log: readable grid times (Today / Yesterday / 30 Sep 16:40, tooltip keeps UTC), headers "When" and "Client", entry details as labelled `KeyValueGrid` fields with the copy text kept. CSV export format unchanged (its test still asserts the old format). |
| P-13 | done | `7220e01`, `ab297fb` | Sera Sync tabs are `Tabs` (decision 6); device list card has no title; the Sync status tab counts conflicts + stalled streams (amber), else changes waiting to go out, hidden at zero. |
| P-R | this document | — | Full suite with the live plugin (below). Human checks (below). |

Also committed along the way: `6c3d62c` (gallery shows Caption and Avatar), `2839a91` (Tabs and KeyValueGrid), the
gallery and test updates that go with each package.

## What now matches the mockup

- Icon sizes and glyph fill, the page-header count pill, tile heights, chip size and chevron, field-label style.
- Dialog and Settings button heights (the theme leak is gone from kit widgets).
- Sidebar: edge bar on the sidebar's edge, 10 px right inset, item pitch 32.
- Page rhythm on All Clients, Tracker Dump and Manage Clients; Settings page rhythm and titles.
- Tracker Dump: pill dots, row action order, plain filing and captured-by text, filter gap.
- Manage Clients: header, sub-line, service pills in the list.
- Audit log: readable times and labelled entry details.
- Sera Sync: underline tabs.

## What still differs, and why

| Area | Difference | Why it is not done |
| :--- | :--- | :--- |
| All Clients | Password columns shown in clear by default | Owner decision withdrawn (2026-10-14). Keep as it is. |
| Qt limits | Soft shadows, focus glow, letter-spacing, transitions, sub-pixel borders | Closest approach as listed in POLISH-PLAN.md section 6. |

## Tests

- UI set (`tests/test_ui_*.py`, `test_responsive.py`, `test_unified_settings_lazy.py`, `test_tracker_dump*.py`) passed
  at the last full run (804 passed, 1 skipped, before the P-9 and P-11 commits). The P-9 and P-11 commits ran their
  narrow sets: `test_ui_lock_detail.py` 110 passed; the dialog set 139 passed.
- Full suite with the live plugin (final commit of this round, `b1a8995`+): **3654 tests: 3519 pass, 133 fail, 2 skipped.**
  Compared with `Mockups/MERGE-READINESS.md` section 4 (2026-10-12: 3643 tests, 137 fail):
  - 132 failures are the known database-fixture failures (sync_rejoin, sync_golive, sync_restore, sync_shadow_start,
    clipboard_assist, raw_payload_db_and_srpf, startup_speed::TestSearchGridReuse, sync_apply, sync_capture,
    sync_hotfix, sync_maintenance, sync_tables, audit_log_v2, cell_formatting, id_field, dual_pk,
    undo_redo_formatting, syncthing_restore). Sample: `Internal Primary Key 'NAME OF COMPANY' is mandatory`.
    Not fixed here, as the plan says.
  - 1 UI leak: `test_ui_kit_frames::test_slide_panel_takes_the_whole_width_in_compact`. Passes alone (52 passed),
    fails only after other UI files: the same known leak in section 11 of the merge notes.
  - The SDIS failure listed there did not occur in this run; checkbox/grid paint and compact selection-bar leaks
    pass in this run.
  - No failure is in a file this work changed for a reason outside the known list. Nothing new.
  - Only the UI set was rerun after the last two commits (P-9 `test_ui_lock_detail` 110 passed; P-11 dialog set 139
    passed).

## Human checks still needed

These go in `Mockups/overhaul/ui-overhaul-checks.csv` under F-R (the tool's check-add command).

1. Real 125 % laptop: borders, icon sharpness, count pill and tile heights.
2. Real Windows fonts without `QT_QPA_FONTDIR`: the offscreen renders need the font directory set; confirm on the PC.
3. Client panel: Edit opens the editor; ⋯ shows Services… for staff and Archive / Delete only in admin mode; activity
   pill appears after an autofill.
4. Audit log: times read naturally across midnight; CSV export still opens with the old time format.
5. Sera Sync: tab keyboard (Left / Right) and the device list without its title.
6. Manage Clients: New client is the single primary; service pills readable in the list.


## Round 2 (2026-11-09): finish, merge main, newly added UI

Order taken: the merge of main (`69789eb`) was already resolved but uncommitted in this worktree, so it was
committed first (`5c3ab69`, with its post-merge fixes) and the remaining packages were finished on top. The
pre-merge resolution is kept at `refs/backup/ui-merge-resolution-20261108`.

| Commit | What |
| :--- | :--- |
| `5c3ab69` | Merge main. Fixed: `_show_row_action_menu` body swallowed by the conflict resolution; lock tests updated for main's SCA audit filters, service names in the audit log, the DRS System nav entry. |
| `5cae210` | P-9 (above). **Kit bug fixed:** the P-2 theme guard `min-height: 0` is applied at polish time and undid `setFixedHeight`, so sidebar nav items were 13 px tall (17 px pitch, mockup 32) and `sm` buttons could grow to 34 px. The sheets now pin the real heights. |
| `9414ac4` | P-11 (above); kit `DropArea`. |
| `ab297fb` | P-13 (above). |
| `ba2d4ab` | DRS dialog (new from main) on the kit: settings card, Columns card with kit checkboxes and source tags, Detected forms table with feed pills; side by side from 900 px, stacked and scrolling below. Off the `OUT_OF_OVERHAUL` list. |
| `32c3e7d` | Settings: the unsaved bar floats over the page top while dirty, so no page carries a 56 px empty band; SCA notice popup (new from main) on kit colours. |
| `8a7cf41` | `ChipBar` folds chips that do not fit into its More menu (Manage Clients showed "Has servic"); chevron buttons open their own menu so the label no longer runs into the chevron; Manage Clients labels sentence-cased; Tracker Dump shows the Notes action only when something is attached (it stays in ⋮). |

Decisions taken as tech lead (owner asked for it): commit the resolved merge before finishing; Notes in the
Tracker row only with content; unsaved bar as an overlay; chips fold into More rather than shrink; DRS cards stack
below 900 px; schema labels with underscores read as spaces.

### Round 2 tests (full suite, live plugin, 2026-11-09)

4405 tests: **4393 pass, 9 fail, 3 skipped** (round 1: 3654 tests, 133 fail; the merge brought main's fixture fixes).
Three of the nine were fixed after the run: DRS used a non-existent icon (`mdi.content-save-check-outline`), and
`test_vsdc247_verify_ui` compared against the literal `#241633` that the overhaul moved onto `purpledim`
(the test now reads the token); `test_sca_audit_log` imports the renamed badge helper. The other six fail the same
way on a clean copy of main `69789eb`, so they are not from this branch: `test_sgt_replay` (3, recorder day
roll-over), `test_sgt_i_furniture::test_lab_lists_the_furniture_per_portal`,
`test_sgt_i_pairs::test_nothing_sgt_i_writes_to_disk_ever_contains_a_fixture_value` (PermissionError on a
`media` folder), `test_vsdc_reader::test_b5_early_abandon_frees_worker_without_wedging`.
