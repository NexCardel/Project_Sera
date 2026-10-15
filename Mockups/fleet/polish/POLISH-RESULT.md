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
| P-9 | partial | `2485b3b` | Panel header: ID chip beside the name, Edit button, ⋯ menu (Services…; Archive and Delete in admin mode), recent-activity pill (decision 2). Body 14 / 18 / 20, gap 12. `main.py` routes the new signals to the existing grid handlers and passes admin mode. **Not done:** Services card icon tiles (28 px, radius 7), Identity and Logins as `KeyValueGrid` (the cards carry the masking and clipboard rules; not rebuilt in this pass), the Notes "✓ Saved" hint. |
| P-10 | done | `0ac3acc` | Settings: nav spacing 2, pages 18/24/24 with gap 12, 820 px card column, titles 19 px (`PageHeader.set_title_px`, decision 8), Manage Columns and Services toolbars back to the small size. Rendered and compared with `renders/set-general.png`. |
| P-11 | partial | `b1a8995` | Change-password note is the muted `Caption`, not an info banner. CSV empty state wording from the mockup. **Not done:** dashed CSV drop area with a secondary Browse button; mapping card captions in the card header. |
| P-12 | done | `967fe72` | Audit log: readable grid times (Today / Yesterday / 30 Sep 16:40, tooltip keeps UTC), headers "When" and "Client", entry details as labelled `KeyValueGrid` fields with the copy text kept. CSV export format unchanged (its test still asserts the old format). |
| P-13 | done, one item left | `7220e01` | Sera Sync tabs are `Tabs` (decision 6). Device list card has no title. **Not done:** the "Sync status" count badge needs a data source. |
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
| Client panel | Services icon tiles; Identity and Logins as setting rows; Notes hint | Identity and Logins carry the masking and clipboard rules. Converting them is a separate, tested change. |
| CSV import | Solid file row, not dashed; no mapping captions | Needs a dashed-border paint in the kit; not started. |
| Sera Sync | No count badge on "Sync status" | No data source wired for the count. |
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
