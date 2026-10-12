# Fleet redesign: polish review and fix plan

Reviewed 2026-10-12 on branch `ui-overhaul` (HEAD `74b240a`) against `Mockups/fleet/index.html` and its
renders in `Mockups/fleet/renders/` (app frame at (283, 78), 1366 × 768, scale 100 %, admin on). The built
pictures are `Mockups/built/sized/*_1366x768.png` for All Clients, Manage Clients and Tracker Dump, and the
S-package goldens for the client panel, Settings, the dialogs, Audit log and Sera Sync. Pixel runs were measured
on the pairs; nothing was rendered here (no Qt in this session). No app code was changed.

Companion images (mockup left, built right, numbered marks): `polish-search.png`, `polish-panel.png`,
`polish-manage.png`, `polish-tracker.png`, `polish-sidebar.png` in this folder. The numbers in the per-screen
sections below match the marks.

## 1. Verdict

The build is a faithful **arrangement** of the mockup and a poor **rendering** of it. Every screen has the right
blocks in the right order, the token colours are exact (`tests/test_ui_tokens.py` proves it), and the kit widgets
have the right outer sizes (buttons 32 px, chips 28 px, rows 40 px, header 36 px, bars 44 px all measure correct).
What is wrong is inside and between those boxes: every icon is drawn at about 75 % of its box (qtawesome's
0.875 draw factor on top of the Material glyph padding, and 14 px boxes where the mockup uses 16), the chip
font was rounded down to 12 px where every other half-pixel size was rounded up, the page header's count pill
is stretched to the title's height (36 px instead of 21), tiles are 60 px instead of 55, dialog buttons and
Settings nav entries grow to 40 px because the old theme's `QDialog QPushButton { min-height: 30px }` leaks
into kit widgets, menu buttons and chips draw a filled triangle where the mockup draws a stroked chevron, the
sidebar's active bar sits 10 px inside the sidebar instead of on its edge, and every daily screen uses 24 px
margins and 16 px gaps where the mockup uses 16 / 20 / 12 with 12 px gaps. The owner's five observations are
confirmed; two are refined: the selection bar is the right height (44 px) and only looks tall because the gaps
around it are 20 px instead of 12, and the switch and sidebar accents are correct in the kit (the screenshots were
taken with admin mode off). Beyond the kit, the client detail panel is the one screen that departs from the
mockup's structure (one-value-per-row setting rows instead of the two-column key-value cards, no meta line,
no per-row Alt key), and Manage Clients lost its two-column editor. Twelve of the fixes land in `tokens.py`
and `kit.py` once and repair every screen; the rest are small per-screen arrangement fixes, plus one larger
one (the panel).

## 2. Token changes (`ui/utils/tokens.py`)

`TOKENS` equals the `:root` block exactly; nothing changes there. The mismatches are in `KIT` (values the kit hard-codes
or rounds) and in sizes the kit does not have a token for at all (it uses literals: 14, 16, 18, 22, 24, 2, 8 × 5).

| Name | Current | Mockup | Why |
| :--- | :--- | :--- | :--- |
| `f_chip` | 12 (`tokens.py:164`) | `.chip` 12.5 px → **13** | K-1 rounded every half pixel **up** (`f_btn` 13, `f_key` 11, `f_badge` 11); the chip alone was rounded down, so chips read a size smaller than buttons next to them. |
| `d_icon` (new) | literal 16 in `IconButton`, `NavItem`; 14 in `Input`, `CellAction`, sm `IconButton`; 18 in `Banner`, `UnsavedBar`, `Toast`; 17 in `IconTile` (`side // 2`); 22 `d_ring_icon` | `svg.i` **16** | The mockup's default icon box: `.ibtn` (both 32 and 24 px), `.nav`, `.field`, `.banner`, `.toast`, `.unsaved`, `.cellbtns .ibtn`, the header `Columns` / `Tools` buttons. |
| `d_icon_s` (new) | literal 14 | `svg.i.s` **14** | Icons inside `.btn` (every `btn()` helper call), `.cb`, chip chevrons, the avatar. |
| `d_icon_l` (new) | literal 17 (`IconTile`), 22 (`d_ring_icon`) | `svg.i.l` **20** | Dialog header tile and empty-state ring. Replace `d_ring_icon` 22 → 20. |
| `icon_scale` (new) | none (qtawesome default 1.0) | **1.14** | qtawesome paints the glyph at `0.875 × box`; the mockup's inline SVG fills its box. 1 / 0.875 = 1.143, so a 16 px box shows a 16 px em, as the SVG does. |
| `h_count` (new) | none (QLabel stretches to 36) | `.count` **21** (12 px × 1.4 + 1 + 1 padding + 2 border) | The page-header count pill. |
| `pad_main_top` / `pad_main_h` / `pad_main_bot` / `gap_main` (new) | screens use `s_6` 24 all round and `s_4` 16 gaps | `.main` **16 / 20 / 12**, gap **12** | Every daily window. COMPACT: `.app.compact .main` **12 / 14 / 10** (`pad_main_*_c`). |
| `gap_btn_icon` (new) | Qt default (≈4 px) | `.btn` gap **7**, `.chip` gap 6 | QPushButton has no icon-text gap property; the kit pads the icon pixmap instead (section 3). |
| `h_tile_num` (new) | `fm.height() × 1.1` ≈ 30 | `.tile b` 20 px × 1.1 = **22** | Tile height 2 × 8 + 22 + 17 = 55 (built 60). |
| `h_tile_lbl` (new) | `fm.height()` ≈ 16 | 12 × 1.4 = **17** | Same. |
| `d_svc_ic` / `r_svc_ic` (new) | 18 px bare icon | `.svc .ic` **28**, radius **7**, bg `#222` (`svc_ic_bg`) | Client panel service rows. |
| `f_sub` (new) | n/a | 11.5 → **12** | `.svc .nm small`, `.p-meta`, `.formula .p`, `.idchip` (11.5 mono). |
| `d_ibtn_kv` (new) | n/a | `.kv .v .ibtn` **24**, colour `#777` (`kv_ibtn_tx`) | Copy / eye buttons inside key-value cells. |
| `kv_line` (new) | n/a | `.kv .f` border-bottom `#222` (= `row_line`, reuse) | Key-value rows. |
| `w_pager_sel` (new) | Select default width | `.foot select` **72** | Tracker "Rows per page". |
| `w_mc_list` (new) | admin window literal | `.mc` **360** (COMPACT 300) | Manage Clients list column. |
| `gap_mc` (new) | `s_4` 16 | `.mc` gap **14**, left column gap 10 | Manage Clients. |
| `w_stack` (new) | none (cards fill the page) | `.stack` **820** | Settings pages. |
| `pad_set_main_*` (new) | `_kit_page` 20 / 12 / 20 / 24, gap 16 | `.set-main` **18 / 24 / 24**, gap **12** | Settings pages. |
| `f_set_title` (new) | `f_xxl` 21 | `.set-main h2` **19** | Settings page title (optional, owner decision 8). |
| `d_avatar_ed` / `r_avatar_ed` / `f_avatar_ed` (new) | theme `EditorAvatar` 40 px | `.mc .ed-hd .av` **38 / 10 / 15 px 700** | Manage Clients editor avatar. |
| `ink3` (viewer token, wrongly used) | `FieldLabel` colour `T["ink3"]` (`kit.py:760`) | `.lab` colour **`muted`** | `ink3` is the outer viewer's grey, not an app colour. |

Half-pixel sizes stay rounded up (12.5 → 13, 13.5 → 14, 11.5 → 12, 10.5 → 11); Qt pixel sizes are integers.

## 3. Kit components (`ui/components/kit.py`): what to change, with the numbers

Fixes are in the kit's own stylesheets and paint code; no API change except the additive arguments marked **(additive)**.

**`_icon()` (`kit.py:253-261`), every icon.** Pass `scale_factor=K["icon_scale"]` (1.14) to `qta.icon`. Check the installed
qtawesome accepts the `scale_factor` option (it has since 0.4; if not, render the glyph at `round(side × 1.14)` into a pixmap
and crop to `side`). This one line is the biggest single fix: it turns the ~10 px glyphs into ~12-13 px ones, the same as the
mockup's 14 / 16 px SVGs.

**Icon boxes to 16 where the mockup uses 16.** `IconButton` sm `QSize(14, 14)` → 16 (`kit.py:429`; `.ibtn.sm` keeps the
16 px icon in a 24 px box). `Input` icon label 14 → 16 at `kit.py:791, 814, 827` (`.field > svg.i` is 16 px at `left: 11px`;
`pad_inp_icon` 34 stays). `Banner` `d_banner_icon` 18 → 16 (`kit.py:1347, 1368`). `UnsavedBar` 18 → 16 (`kit.py:2102-2106`).
`Toast` 18 → 16 (`kit.py:1405, 1425`). `IconTile` `side // 2` → `d_icon_l` 20 (`kit.py:3106`). `EmptyState` ring icon 22 → 20
(`kit.py:1484`). `GridDelegate._paint_actions` button icon 14 → 16 (`kit.py:2499`; the 26 px cell button holds a 16 px icon).
`LabelledAction` keeps 14 (`kit.py:326, 373`: the mockup's `btn()` helper uses `.s`).

**`LabelledAction` icon-text gap.** Qt draws icon and text ~4 px apart; the mockup uses `gap: 7px`. Build the icon pixmap
`side + 3` wide with a transparent 3 px right margin (`_apply_icon`, `kit.py:367-391`, the same way `set_swatch` composes
a pixmap) so the visible gap is 7. Chips are painted and already use `gap_chip` 6.

**Theme leak guard (`_action_qss` `kit.py:297-308`, `_icon_qss` `kit.py:405-414`, `NavItem`).** Inside any `QDialog` the app
sheet's `QDialog QPushButton { min-height: 30px; padding ... }` adds to the kit's sizes: Settings nav entries measure
**40 px** (mockup 32; `Mockups/built/settings/general_1366x768.png`, rows at y 158-197), and S-4 recorded that `sm` buttons
clip. Add `min-height: 0; max-height: none; padding: 0 <pad>px;` to the kit's `QPushButton` / `QToolButton` rules and give
`NavItem` its own `QPushButton { min-height: 0; padding: 0; border: none; background: transparent; }` sheet (it paints
itself; `kit.py:3834-3850` sets no sheet, so the theme rule reaches it). After this, S-4's "regular-height buttons only"
workaround can go back to `sm` where the mockup uses `sm`.

**Menu chevron (additive).** `LabelledAction(..., chevron=False)` → when `True`, reserve 14 + 6 px on the right and paint a
stroked chevron (pen 1.8, round caps, points (−3, −1.5) (0, 1.5) (3, −1.5) as `Select.paintEvent` draws at
`kit.py:867-877`) in the text colour, and add `QPushButton::menu-indicator { image: none; width: 0; }` to `_action_qss` so a
button given `setMenu()` (Manage Clients "More", `admin_window.py:457`) loses Qt's filled triangle. Screens then drop the
literal "▾" from their labels (`search_window.py:150`, `tracker_dump_window.py:1493`).

**`Chip` chevron (`kit.py:1718-1731`).** Replace the filled 8 × 5 triangle with the same stroked chevron (14 px box,
`d_icon_s`). Chip font: `_px_font(self, K["f_chip"], QFont.Medium)` at `kit.py:1635, 1689` follows the token (13).
`test_ui_kit_gallery.py:304` reads `f_chip`, so it follows too.

**`PageHeader` count pill (`kit.py:3510-3513`, `_page_hd_qss` `kit.py:3481-3483`).** `row.addWidget(self.count_label, 0,
Qt.AlignVCenter)` and `setFixedHeight(K["h_count"])`; padding `0 10px`, font 12, radius 12 (`r_count`). Today the QLabel fills
the 36 px row (measured 25-58 on All Clients, 21 px in the mockup).

**`Tile` height (`kit.py:3642-3648`).** `_value_height()` → `K["h_tile_num"]` (22; 18 in COMPACT: 16 × 1.1), label line →
`K["h_tile_lbl"]` (17). Height = 2 × `pad_tile_v` + 22 + 17 = **55** (built 60). Keep the floor 50 / 40.

**`FieldLabel` (`kit.py:755-760`).** The mockup's `.lab` is `11px`, `color: var(--muted)`, sentence case, `margin-bottom: 4px`.
Remove `AllUppercase` and the 108 % letter spacing, colour `muted`, bottom margin 4. `tests/test_ui_kit.py:250` asserts the
upper-casing and changes with it (it tests the look, not behaviour). Every form (Manage Clients cards, MCL / service edit,
CSV, passwords, first run, rejoin) follows at once. `Card` titles, `NavLabel`, grid headers and `SectionLabel` stay upper-case
(the mockup upper-cases those).

**`NavItem` edge bar (`kit.py:3901-3905`).** The mockup's `.nav.on::before` sits at `left: -10px`: on the sidebar's edge, not
inside the item. Give `NavItem` an **(additive)** `edge_inset=0` argument: the sidebar passes 10, the item paints its wash from
`x = inset` and the 3 px bar at `x = 0` (height - 16, radius `0 3 3 0`), and the text starts at `inset + pad_nav_h`. The
sidebar's scroll content then uses a 0 px left margin (section 4). Settings' nav keeps inset 0 (the mockup's `.set-nav` bar
also sits at the pane edge: same call).

**`StatusDot` as an icon (additive).** `dot_icon(tone, size=14) -> QIcon` painting the 8 px dot with its 3 px halo, for the
sidebar's Auto-sync pill (`.btn.soft.sm` + `.dot`) and the Sera Sync strip; today `mdi.circle-medium` at 14 px gives a 4 px
dot with no halo (`sidebar.py:229`).

**`DataGrid` list tone services (additive).** In `list_mode` draw `ROLE_SERVICES` as tinted pills (`.gpill`: 20 px, radius
10, padding 0 8, font 11 600, background = service colour at 13 % alpha, text = service colour, right-aligned, wrapping to a
second line in 52 px rows) as the Manage Clients mockup draws them; the white and dark grids keep dots. Owner decision 9.

**`KeyValueGrid` (additive, for the client panel).** Two columns (`gap 0 20px`, padding `4 16 12`), each cell a label
(11 px muted, 1 px below) over a value row (13 px, mono variant 12 px, elided, then 24 px ghost icon buttons: copy, eye),
hairline `row_line` under every row except the last pair, and a `set_not_set([labels])` line (`.notset`: `padding 0 16 12`,
12 px `#7c7c77`, bold `#a0a09a` "Not set:"). `add_field(label, value, *, mono=False, actions=())`.

**`Tabs` (additive, for Sera Sync).** `.tabs2`: underline tabs, 12.5 → 13 px 600, padding 9 12, muted text, selected mint with a
2 px mint bottom border, optional count badge (`.n`: 10.5 → 11 px, `#2a2a2a` / `#ccc`, amber variant). One focus stop, Left /
Right, `currentChanged(int)`; same API shape as `Segmented`.

**`Banner` / `Toast` spacing.** Layout spacing `s_3` 12 → 10 (`.banner gap: 10px`, `kit.py:1344`; `.toast gap: 10px`,
`kit.py:1403`). Banner radius `r_l - 2` = 10 ✓.

**`SlidePanel` depth (`ui/shell/slide_panel.py`).** The mockup's `box-shadow: -16px 0 40px rgba(0,0,0,.45)` cannot be
drawn in QSS; paint a 6 px left gradient (black 0.35 → 0) outside the 1 px `line2` border in `paintEvent` (section 6).

Already matching, leave alone: `LabelledAction` heights 32 / 28 and padding 13 / 10; `IconButton` 32 / 24; `Segmented`;
`Key` 20; `Pill` 22; `Tag` 20; `Switch` 38 × 22 with the 16 px thumb; `Checkbox` 16; `Input` / `Select` / `Stepper` 34; `Card`
title row and body padding; `SettingRow` 11 / 16 and the 230 px column; `StatusStrip`; `SelectionBar` 44 with 14 / 10 padding
and the `.vr` / `.grp` look; `UnsavedBar`; grid header 36 with 0.8 px spacing, rows 40 / 52, footer 30, cell buttons 26,
`DarkListTable` 32 / 36; `DialogFrame` header 14 / 18, body 16 / 18, footer 12 / 18 on `#1b1b1b`; `LogView`; `Scrim`.

## 4. Per screen: what remains after the token and kit fixes

Numbers refer to the marks on the companion images.

### Sidebar (`ui/shell/sidebar.py`) — `polish-sidebar.png`

1. **Active bar inside the item** (`kit.py:3904`): fixed by `NavItem(edge_inset=10)` plus `self.layout.setContentsMargins(0, 0, 0, 0)`
   staying and the scroll content's left margin at 0 with the items' own inset (`sidebar.py:168-170`).
2. **Item pitch 34 px, mockup 32**: `self.layout.setSpacing(2)` → 0 (`sidebar.py:170`); the mockup stacks `.nav` with no gap.
3. **Auto-sync pill dot**: `icon="mdi.circle-medium"` → `dot_icon("ok")` (`sidebar.py:228-230`); the pill stays `soft`, `sm`, centred.
4. **Toggle button shows its focus ring on start** (R-R noted it): `IconButton` and `LabelledAction` take `Qt.TabFocus`
   (kit, section 6, "focus-visible"); the shell gives the search box focus after `show()` (`app_shell.py`, where the first
   page is shown; `search_window.py:297` already calls `setFocus()` but the shell's later focus wins).
5. **Staff name missing** in the snapshot: `set_user_name` is only called by `main.py`; the snapshot harness should call it.
   Not a code defect; check on a real run.
6. Icons ~10 px: kit `icon_scale`. Brand, admin row, group labels, footer rule, version: match.

### All Clients (`ui/windows/search_window.py`) — `polish-search.png`

1. **Page rhythm**: `layout.setContentsMargins(24, 24, 24, 24)` / `setSpacing(16)` (`search_window.py:140-141`) → the
   `pad_main_*` / `gap_main` tokens (16 / 20 / 12, gap 12; COMPACT 12 / 14 / 10). Measured: header block 0-77 (mockup 16-52),
   search box at 78 (mockup 66), selection bar at 128 (mockup 113), grid at 190 (mockup 172), bottom margin 28 (mockup 10).
   This is what makes the empty selection bar look like "a tall box": the bar is 44 px in both, the gaps around it are 20 px here and 12 in the mockup.
2. **Count pill 36 px tall**: kit `PageHeader` fix.
3. **"Columns ▾" literal** (`search_window.py:150`): `LabelledAction("Columns", chevron=True)`; icon box stays 14 (the kit) although
   the mockup draws this one icon at 16; acceptable.
4. **Chips**: font 13 (kit); the mockup's `All` chip carries the count badge (`All 16`): call `self.chip_bar.chip("all").set_count(shown)`
   next to `self.page_header.set_count(...)` (`search_window.py:686`).
5. **Company column bold**: `_column_spec` returns `kind="strong"` for company (`search_window.py:83`); the mockup's grid is
   regular weight (only the selected row is 500). Use `kind="text"`; bold stays for the Manage Clients list (its own `ROLE_SUB` cell).
6. **Column widths**: mockup `colgroup` No. 56, company (rest), proprietor 24 %, GSTIN 150, PAN 112, Ph. no. 110, Services 210.
   Built: 52 / stretch 3 max 34 % / stretch 2 max 28 % / 140 / 104 / 104 / stretch 2 min 140 (`search_window.py:70-88, 541`).
   Set `width=56, minimum=44`; GSTIN `width=150, minimum=110`; PAN `width=112`; phone `width=110`; services `width=210, minimum=150`;
   proprietor `max_share=0.24`. The crowding in the screenshot is not the widths but the three extra columns (next item).
7. **Password / secret columns shown in clear and by default** (USER ID, EMAIL, GST_PASSWORD): the visible set is the per-column
   `show_in_search` flag (data). The mockup leaves them out. Owner decision 1; if taken, the default for `field_type == "password"`
   becomes off and the Columns ▾ menu still turns them on (the flag writer is `search_window.py:690-755`).
8. **Horizontal scrollbar** at 1366 (y 705): disappears with 6 and 7.
9. **Footer hints** (`search_window.py:290-292`): mockup says `Enter` "opens the top result", `↑ ↓` "move ·", `Ctrl C` "copies the
   selected cells", `Esc` "closes the panel"; built says "opens the selected result" and has no `Esc` hint. Wording only.
10. **Search box icon 14 → 16**: kit `Input`. Width 220-560 and the chips beside it match.
11. **Grid beside the open panel**: the mockup hides GSTIN / Ph. no. (`col-lo`) and PAN / Services (`col-lo2`) while the panel is open
    (`.withpanel`), leaving No., company, proprietor in the 1366 − 212 − 540 = 614 px. Built keeps every column under the panel. Give those
    columns `priority` 1 / 2 and call `fit_columns(available = viewport − panel width)` when the panel opens (S-7's `WidthAware` hook).

### Client detail panel (`ui/windows/client_detail_window.py`, `ui/shell/slide_panel.py`) — `polish-panel.png`

The panel is the one daily screen whose structure differs from the mockup; S-8 kept the old one-value-per-row layout.

1. **Header** (`client_detail_window.py:99-128`): mockup `.p-hd` padding 14 18 12 with a hairline under it; `.p-top` gap 10:
   bordered `IconButton` Back 32 px (built: ghost sm arrow), name 17 px 700 elided (built: theme `ClientName` 16 px), the ID
   chip right after the name (mono 12 600 mint on `emdeep`, `emline` border, radius 8, padding 3 9; built `token_badge` is a
   soft sm button at the far right: keep the widget, move it next to the name, mono font), then ⋯ and Edit (owner decision 2).
2. **Meta line**: `#3 · PAN ABCDE1002F · Kiran Mehta · [emerald pill: clock 14 px "Autofilled today 11:42"]`, 12 px muted,
   `·` in `#444`. Built: proprietor as a subtitle under the name, no PAN, no activity. The activity text exists (`_ACTIVITY_ROLE`
   in the search grid); show it here as the pill when known.
3. **Padding**: outer 24 / 14 / 24 / 14 spacing 8, scroll 4 / 8 / 4 / 12 spacing 12 (`client_detail_window.py:99-100, 136-137`)
   → body 14 / 18 / 20, gap 12; no inner scroll margins.
4. **Services card rows** (`client_detail_window.py:263-320`): mockup `.svc` padding 9 16, gap 10, hairline between rows: 28 px
   icon tile (radius 7, `#222`, 16 px icon in the portal colour; built 18 px bare icon), name 13 px 600 with the mode names under
   it 12 px muted ("Fast Autofill", "Autofill · Assist", "Copy login"), then `Autofill` (`sm` primary, bolt), `Assist` (`sm`,
   assist icon), `Copy login` (`sm`, copy), an "off" action as a dimmed `sm` button with its reason, and a `Key("Alt n")` per row.
   Built shows one `Key("Alt+1..9")` in the card header and no per-row key; the header hint should be the muted 11 px
   "ⓘ Alt+1…9 · Alt+Ctrl+1…9 for mode 2" with the full tooltip.
5. **Identity and Logins cards** (`client_detail_window.py:322-384`): built uses a `SettingRow` per field (upper-case DB label
   13.5 px 600 on the left, value right-aligned, copy at the far right, "Not set" per empty row). Mockup: `KeyValueGrid`, two
   columns, label above value, copy (and eye for passwords) right after the value in 24 px ghost icon buttons, empty fields
   collapsed into one "Not set: DOB" line. Use the new kit component; Logins keeps the masking rule and gets the header hint
   "Masked: first 5 characters shown · clipboard clears after 30 s" (the two settings' values).
6. **Column labels** come from the schema in upper case (NAME OF COMPANY); the mockup shows "Name of company". Owner decision 5.
7. **Notes**: mockup `TextArea` 3 rows with a "✓ Saved" muted hint in the card header; built has the Pill. Keep the Pill text, make it
   the muted 11 px hint with a 14 px check icon.
8. **Portal values**: collapsed card with a `Show` ghost `sm` button and the muted 12 px sentence; built matches in structure.
9. **Toast** "Copied PAN · clears in 30 s": the shell still uses `SeraAlert` with the old greens (MERGE-READINESS §5.2); swap
   `AppShell.alert` for `kit.Toast` (`app_shell.py:143-144`, same API). Owner decision 7.
10. **Depth**: 540 px and the `line2` left border match; add the painted 6 px shadow strip (section 6).

### Tracker Dump (`ui/windows/tracker_dump_window.py`) — `polish-tracker.png`

1. **Page rhythm**: `main_layout` 24 / 16 (`tracker_dump_window.py:1477-1478`) → tokens (16 / 20 / 12, gap 12). Measured: tiles at
   85 (mockup 76), segmented at 165 (146), grid at 214 (191).
2. **Tiles 60 px** (mockup 55): kit `Tile`. Tile `tone` colours: mockup "Need a client" is `#ffa657` (orange, not amber) and
   "Captured today" mint; built uses amber for both "Need a client" and "Awaiting": pass `tone="orange"` for the first
   (`Tile` gains the orange tone from `K["orange"]`, additive) — optional.
3. **"Tools  ▾" literal** (`tracker_dump_window.py:1493`): `chevron=True`; icon 16 in the mockup (keep 14).
4. **Filter row gap** 8 (`tracker_dump_window.py:1519`) → 10 (`.chips-row gap: 10px`); search box 180-320 → max 360 so the
   placeholder "Search client, PAN, GSTIN, ARN, period, portal…" is not cut.
5. **Chip chevrons** filled → stroked: kit `Chip`.
6. **Status pills have no dot**: `_status_pill` (`tracker_dump_window.py:235`) returns `(text, tone)`; the mockup's `.gpill` carries
   a 6 px dot: return `(text, tone, True)`.
7. **Row actions order**: mockup eye · bolt (primary) · ⋮; built bolt · eye · ⋮ (S-10 decision). Owner decision 3.
8. **Filing type and Captured by in colour** (portal blue / green text, orange "System"): data palette from `STATUS_THEMES`;
   the mockup draws plain `text2`. Owner decision 3.
9. **Footer**: `"◀ Prev"` / `"Next ▶"` text glyphs (`tracker_dump_window.py:1667, 1675`) → `icon="mdi.chevron-left"` /
   `"mdi.chevron-right"` (right-hand icon: text "Next" + chevron needs the `chevron=True` variant or a trailing-icon option),
   "Page 1 / 1" 600, `cmb_page_size` fixed 72 px wide and 28 px high (it is `h_sm` already). The mockup puts this footer *below*
   the grid frame (`.foot`, 22 px, muted 12 px); the built keeps it inside the `GridFooter`. Keep inside (one grid look), fix the
   buttons.
10. **Header lead** 12 px muted: matches. Segmented, search icon (kit), grid header, 52 px rows, cell buttons: match.

### Manage Clients (`ui/windows/admin_window.py`) — `polish-manage.png`

1. **Page rhythm**: `layout` 24 / 20 / 24 / 20, spacing 12 (`admin_window.py:283-284`) → 16 / 20 / 12; `body.setSpacing(16)`
   (`admin_window.py:315`) → `gap_mc` 14; left column gap 8 → 10.
2. **Count pill** (kit).
3. **New client**: `variant="soft"`, `mdi.account-plus` (`admin_window.py:294-295`) → mockup `primary`, `mdi.plus`
   (`btn('New client','pri','plus')`). Owner decision 4 (two primaries on the screen: New client and Save).
4. **"Sort" label** is a `FieldLabel` (`admin_window.py:357`): mockup is a plain muted 12 px "Sort". After the kit fix the
   FieldLabel is sentence case; replace with a muted `QLabel` anyway (it is not above a field).
5. **List sub-line** (`admin_window.py:1026-1028`): built "#1 · Sharma Traders Pvt Ltd · ABCDE1000F · Created…" elides;
   mockup "#1 · ABCDE1000F" (id · PAN) in mono 11. Keep GSTIN, created and activity in the row tooltip.
6. **Services as dots** vs tinted pills: kit list-tone option (owner decision 9).
7. **Editor header** (`admin_window.py:422-470`): avatar 40 px theme label → 38 px, radius 10, `emdim` background, mint 15 px 700,
   initials ("ST") when a client is loaded and "+" for a new one (built always "+"); title 16 px 700 and 12 px sub match; "More"
   uses `setMenu()` → chevron variant; Save icon `content-save-outline` → `mdi.check` (mockup). The dirty text "Unsaved changes"
   goes in amber inside the sub line (built: a separate `DirtyLabel`), as `· Unsaved changes`.
8. **Cards in one column**: `_card_cols` (`admin_window.py:477, 756-758`) lays the cards out in one column at 1366 × 768; mockup
   `.cards2` is two columns (Identity | Portal logins, Contact | Attached services) with Notes full width, gap 10; one column in COMPACT.
9. **Form fields** come from `ui/utils/dynamic_form_widgets.py:47-97` as plain `QLineEdit` / `QComboBox` under the global sheet
   (legacy `#262626` border, no mint focus, label above from the theme's `FieldLabel` class). Rebuild the factory on `kit.Input`
   (`add_trailing` for eye / copy, which the cards already place), `kit.Select`, `kit.TextArea` and `kit.FieldLabel`; the
   read-only ID field keeps its mint mono look (F-R). S-9 called this out of scope; it is the last non-kit surface on a daily screen.
10. **Attached services**: mockup `.svcchip` (30 px, radius 8, 600, `line2` border; on = `emdeep` / `em` / mint). Built uses kit
    `Chip` (28 px, radius 14). Accept the kit chip (one chip look) — note only.
11. **Bulk bar** (`SelectionBar`, 40 px in the mockup, 44 in the kit) and the "Ctrl- or Shift-click…" hint in the grid footer: accept.

### Settings hub (`ui/dialogs/unified_settings_dialog.py`)

1. **Nav entries 40 px** (`general_1366x768.png` y 158-197; mockup 32): the theme leak; kit guard fixes it. `_nav_layout.setSpacing(1)`
   (`unified_settings_dialog.py:289`) → 2 (`.set-nav gap: 2px`). Nav padding 12 / 10 ✓, width 224 ✓.
2. **Page margins**: `_kit_page` 20 / 12 / 20 / 24, spacing 16 (`unified_settings_dialog.py:388-389`) → 18 / 24 / 24, gap 12, and a
   `w_stack` 820 px maximum on the card column (the built cards run to the right edge, 1170 px wide).
3. **Page title 21 px** (`PageHeader`) vs `.set-main h2` 19 px: owner decision 8 (additive `PageHeader(title_px=)`).
4. Dialog header, search box, group labels, unsaved bar, footer, cards, rows, switches, selects, steppers: match. The MCL and
   Services pages' toolbar buttons can return to `sm` once the guard is in (S-4 / S-5 used regular height to dodge the clipping).

### Dialogs (`ui/dialogs/*.py`)

1. **Field labels** upper-case grey (`FieldLabel`): kit fix → sentence case muted, as `.lab` in every dialog mockup.
2. **Header icon tile** 17 → 20 px icon, **banner icon** 18 → 16, **empty-state ring icon** 22 → 20, button icons via `icon_scale`: kit.
3. **Change master password**: the note is a plain muted 12 px line in the mockup, not an info `Banner`
   (`change_master_password_dialog.py`); CSV import: the file row is a dashed `#3a3a3a` 1 px drop area with "No CSV file
   selected. The file's encoding is detected and shown here." and a secondary `Browse CSV…` (built: solid card, soft bordered
   button); the mapping preview card title row holds the two column captions (CSV HEADER · MAPS TO MCL COLUMN).
4. **MCL column edit**: Dropdown options stays a disabled field in the mockup; built hides it (S-5 decision, keep). Identity /
   PK as `SettingRow` + `Switch` (S-5) instead of the mockup's checkboxes: keep.
5. **Buttons in dialogs 40 px**: the theme leak (kit guard); `Cancel` / `Save` measure 36-40 px in the goldens, mockup 32.
6. First run, force update, loading, recovery, rejoin: structure matches; only 1, 2 and 5 apply.

### Audit log (`ui/dialogs/audit_log_dialog.py`)

1. **Times**: `strftime("%d-%b-%Y %I:%M:%S %p")` (`audit_log_dialog.py:85`) → the mockup's readable "Today 12:49", "Yesterday 18:20",
   "30 Sep 16:40" (IMPLEMENTATION 4.5); header "DATE & TIME (LOCAL)" → "When", "CLIENT (NAME / T…" → "Client".
2. **Details card** shows the raw detail text in `inspector_box` (`audit_log_dialog.py:573`); mockup: labelled fields When /
   Staff · workstation / Action (pill) / Client / Service / Detail, then `Copy details` and `Show only this client`. Use the
   `KeyValueGrid` in one column.
3. Workstations list, filter row, chips, export button, grid pills: match.

### Sera Sync (`ui/dialogs/sera_sync_dialog.py`)

1. **Tabs** are a `Segmented` (`sera_sync_dialog.py:185`); mockup `.tabs2` underline tabs with the "Sync status 1" count badge:
   kit `Tabs`. Owner decision 6.
2. Devices as a `DarkListTable` (S-12, owner-approved), strip, banner, activity log, buttons: match. "Discovered LAN devices"
   card title is extra (mockup has none): drop the title.

## 5. Root cause, in one line

Each package was judged by tests and attribute names, never by a side-by-side; the kit got the boxes right and the
contents wrong, and three rendering facts were never checked: qtawesome's 0.875 draw factor, QLabel stretching in a
QHBoxLayout, and the old theme's `QDialog QPushButton` rule reaching kit widgets.

## 6. What Qt cannot reproduce, and the closest approach

| Mockup | Qt | Closest approach |
| :--- | :--- | :--- |
| Soft shadows (`.gridwrap`, `.panel`, `.dlg`, `.toast`, `.card` menus) | QSS has no `box-shadow`; `QGraphicsDropShadowEffect` blurs the whole widget, breaks masks and costs a repaint per frame | 1 px border (`#333` grid, `#3a3a3a` dialog, `line2` panel) + the scrim (done); for the panel paint a 6 px gradient strip (black 0.35 → 0) on its left in `paintEvent`; toast gets a 1 px `banner_g_line` border |
| Focus glow `box-shadow 0 0 0 3px rgba(mint,.12)` | no outer glow in QSS | 1 px mint border (done); painted widgets (Chip, Tile, Switch, NavItem) draw the 2 px ring inside |
| `:focus-visible` (ring only for keyboard focus) | QSS `:focus` fires on mouse focus too | `Qt.TabFocus` on buttons, chips and tiles: clicks never take focus, Tab still reaches them; the shell focuses the search box on start |
| `letter-spacing: .07-.1em` | QSS ignores it | `QFont.setLetterSpacing(AbsoluteSpacing, 0.8-1.0)` on the painted / label fonts (done for grid headers, card titles, nav labels) |
| `transition .15s` (switch thumb, hover) | no timers by decision | instant state change; keep |
| `line-height: 1.4` in labels | `QLabel` uses the font's natural height (~1.33 × 13 px) | fixed heights where a box is sized by text (count pill 21, tile lines 22 / 17, grid header 36, rows 40) |
| Half-pixel sizes 12.5 / 13.5 / 11.5 / 10.5 px | integer pixel sizes | round up (13 / 14 / 12 / 11), applied the same way everywhere |
| `0.5px` hairlines (`PageSeparator`, dividers in `theme.py`) | drawn as 1 px | 1 px `line` / `row_line` |
| icon-text gap in a button | `QPushButton` has no gap property (~4 px) | pad the icon pixmap 3 px on the right |
| icon after the text (`Columns ▾`, `Next ▸`) | `QPushButton` puts the icon first | `chevron=True` paints it; a trailing icon is the same code path |
| `text-overflow: ellipsis` everywhere | automatic only in item views | `_ElideLabel` and painted elision (done) |
| `scrollbar-width: thin`, overlay scrollbars | theme scrollbars take layout space | 6 px theme scrollbar (done); the Settings goldens show it |
| tinted service pills (`${colour}22`) | fine: `QColor.setAlphaF(0.13)` | kit list-tone option (section 3) |
| `font-variant-numeric: tabular-nums` | Segoe UI digits are tabular already | nothing to do |
| `select` popup look | `QComboBox` popup is a `QListView`; limited rounding | the kit's `QAbstractItemView` rule (done) |
| 125 % scale: 1 px borders become 1.25 px | Qt rounds device pixels | accept; check borders at 125 % on a real screen (human check 33) |

## 7. Work packages

Same columns as `BUILD-PLAN.md`. Kind `auto`: an unattended session; `review`: compares renders. Every package ends with the
**render check**: `tools/ui_snapshots.py` (and `tools/ui_gallery.py` for kit packages) at 1366 × 768, 1024 × 700 and 1366 × 768 @ 1.25
into `Mockups/built/`, then a side-by-side against the matching `Mockups/fleet/renders/<screen>.png` (crop (283, 78, 1649, 846)
for the app frame) with pixel-run measurements of the items the package changed, written into the package's hand-off note.
Rules: tokens only, no literal stylesheet in a screen, no behaviour change, same attribute names, `tests/test_ui_overhaul_rules.py`
green after every package.

| WP | Model | Kind | Size | Deps | What |
| :--- | :--- | :--- | :--- | :--- | :--- |
| P-1 | sonnet | auto | S | — | **Tokens.** `KIT` gains `d_icon` 16, `d_icon_s` 14, `d_icon_l` 20, `icon_scale` 1.14, `h_count` 21, `pad_main_top/h/bot` 16 / 20 / 12 (+ `_c` 12 / 14 / 10), `gap_main` 12, `gap_btn_icon` 3, `h_tile_num` 22 (`_sm` 18), `h_tile_lbl` 17, `d_svc_ic` 28, `r_svc_ic` 7, `svc_ic_bg` #222222, `f_sub` 12, `d_ibtn_kv` 24, `kv_ibtn_tx` #777777, `notset_tx` #7C7C77, `notset_b` #A0A09A, `w_pager_sel` 72, `w_mc_list` 360 (`_c` 300), `gap_mc` 14, `w_stack` 820, `pad_set_top/h/bot` 18 / 24 / 24, `f_set_title` 19, `d_avatar_ed` 38, `r_avatar_ed` 10, `f_avatar_ed` 15, `idchip_r` 8, `pad_idchip_h` 9, `pad_idchip_v` 3; `f_chip` 12 → 13; `d_ring_icon` 22 → 20. `tests/test_ui_tokens.py` asserts the new names and that every `KIT` size the mockup states equals the mockup value. |
| P-2 | opus | auto | M | P-1 | **Kit rendering fixes** (section 3): `_icon` scale factor; 16 px icon boxes (IconButton sm, Input, Banner, UnsavedBar, Toast, cell actions), 20 px (IconTile, EmptyState); LabelledAction icon gap; theme-leak guard (`min-height: 0`, padding) in `_action_qss`, `_icon_qss`, `_segmented_qss`, NavItem sheet, `::menu-indicator` off; PageHeader count pill 21 px; Tile 55 px; FieldLabel sentence case / muted / 4 px; Chip font 13 + stroked chevron; Banner / Toast gap 10. Update `tests/test_ui_kit.py:250, 604` and the gallery tests; re-render all 14 gallery PNGs; measure glyph bounding boxes at 14 / 16 px before and after (expect ≈ 10 → 12-13 px). |
| P-3 | sonnet | auto | M | P-2 | **Kit additive components**: `LabelledAction(chevron=)`, `NavItem(edge_inset=)`, `dot_icon()`, `KeyValueGrid`, `Tabs`, list-tone tinted service pills, `Tile` orange tone, `PageHeader(title_px=)`. Gallery states and tests for each; API summary in the module docstring. |
| P-4 | sonnet | auto | S | P-3 | **Sidebar**: edge bar (inset 10, scroll content margin 0), item spacing 0, `dot_icon` on the Auto-sync pill, `TabFocus` on the toggle, shell focuses the search box on start (`app_shell.py`); `AppShell.alert` → `kit.Toast` (owner decision 7). Renders of the shell at the three sizes against `renders/search.png` left 212 px. |
| P-5 | sonnet | auto | S | P-3 | **Page rhythm**: `search_window.py:140-141`, `tracker_dump_window.py:1477-1478`, `admin_window.py:283-284, 315` on the `pad_main_*` / `gap_main` / `gap_mc` tokens, COMPACT values through `on_width_mode_changed`; "▾" literals → `chevron=True` on Columns, Tools, More, Next. Measure the y of header, search row, bar and grid on all three screens against the mockup (section 4 numbers). |
| P-6 | sonnet | auto | M | P-5 | **All Clients**: company `kind="text"`, mockup column widths, `All` chip count, footer hint wording, panel-open column priorities (`withpanel`), password-column default per owner decision 1. `tests/test_ui_lock_search.py`, `test_ui_search_columns_menu.py`, `test_ui_search_selection_bar.py` green. |
| P-7 | sonnet | auto | S | P-5 | **Tracker Dump**: status pill dot, filter gap 10 and search max 360, pager buttons with chevrons and 72 px select, tile tones; row order and text colours per owner decision 3. `test_ui_lock_tracker.py`, `test_ui_tracker_kit.py`, `test_tracker_dump_*.py` green. |
| P-8 | sonnet | auto | M | P-5 | **Manage Clients**: New client primary (decision 4), plain Sort label, list sub-line `#id · PAN`, services pills (decision 9), editor avatar 38 px with initials, Save check icon, dirty text in the sub line, two-column cards in WIDE, `dynamic_form_widgets.py` on kit `Input` / `Select` / `TextArea` / `FieldLabel`. `test_ui_lock_manage.py`, `test_ui_manage_clients_kit.py` green. |
| P-9 | opus | auto | L | P-3, P-4 | **Client detail panel**: header (bordered Back 32, 17 px name, ID chip beside it, meta line with PAN · proprietor · activity pill), body 14 / 18 / 20 gap 12, services rows (28 px icon tile, mode sub-line, labelled `sm` actions, per-row `Key("Alt n")`, header hint), Identity / Logins on `KeyValueGrid` with the "Not set:" lines and the masking hint, Notes hint, panel shadow strip; ⋯ / Edit only if owner decision 2 says so. Alt+n, copy, 30 s wipe, notes autosave untouched (`test_ui_lock_detail.py` 34 green). |
| P-10 | sonnet | auto | S | P-3 | **Settings hub**: nav spacing 2, page margins 18 / 24 / 24 gap 12, `w_stack` 820, title 19 (decision 8), MCL / Services toolbars back to `sm`. Settings goldens re-rendered (12 pages × 3 sizes) against `renders/set-*.png`. |
| P-11 | haiku | auto | M | P-2 | **Dialog sweep**: labels and icons come from the kit; change-password note as muted text, CSV drop area dashed with the mockup wording and a secondary Browse, mapping captions in the card header; every dialog golden re-rendered at 100 % and 125 % against `renders/<dialog>.png`. |
| P-12 | sonnet | auto | M | P-3 | **Audit log**: readable times, header names, details card on `KeyValueGrid`. `test_ui_lock_audit.py`, `test_ui_audit_screen.py` green; CSV export golden unchanged. |
| P-13 | sonnet | auto | S | P-3 | **Sera Sync**: `Tabs` for the three tabs with the Sync status count, drop the extra "Discovered LAN devices" title. `test_ui_lock_sync.py`, `test_ui_sync_screen.py`, `test_sync_*_ui.py` green. |
| P-R | opus | review | M | P-4…P-13 | **Polish review**: every screen and dialog re-rendered at 1366 × 768, 1024 × 700 and 1366 × 768 @ 1.25; side-by-sides against every `renders/*.png` with pixel-run checks of header, bar, chip, tile, pill and nav heights and the icon glyph boxes; gallery against the Foundation page; `test_ui_overhaul_rules.py`, kit, lock and screen tests; full suite with the live plugin compared with MERGE-READINESS §4; hand-off note and the human-check rows (real 125 % laptop) in `Mockups/overhaul/ui-overhaul-checks.csv`. |

**Count:** 15 packages (1 review). Chain: P-1 → P-2 → P-3 → P-4 / P-5 → P-6 … P-13 (parallel) → P-R. P-2 alone fixes the
icons, count pill, tiles, chip size, labels and the dialog / Settings button heights on every screen; it is the package to run first
if only one can run.

## 8. Decisions the owner must take

1. **Password and secret columns in All Clients**: hide them by default (password-type columns' `show_in_search` off until switched on
   in Columns ▾ or Settings → Main screen) and / or mask them per Password masking mode. Today they show in clear by default.
2. **Client panel actions**: build the ⋯ menu (Services…, Archive, Delete… in admin) and the Edit button the design shows, or keep
   the panel without them (S-8 left them out). Also: show the "Autofilled today 11:42" pill from the activity data.
3. **Tracker Dump**: row actions in the mockup order (Inspect · Autofill · ⋮) or the S-10 order (Autofill · Inspect · ⋮); keep the
   portal / status colours on the Filing type and Captured by text, or plain text as drawn.
4. **Manage Clients "New client"**: emerald primary with a plus (mockup; two primaries on the screen with Save) or the S-9 soft button.
5. **Schema labels in the panel and cards**: show them as stored (NAME OF COMPANY) or sentence-cased as the mockup draws them
   (rule: labels longer than five letters are sentence-cased; GSTIN, PAN, TAN, DOB stay).
6. **Sera Sync tabs**: underline tabs (new kit `Tabs`) as drawn, or keep the `Segmented`.
7. **Toast**: replace `SeraAlert` (old Material greens) with `kit.Toast`; same API (MERGE-READINESS §5.2).
8. **Settings page title**: 19 px as the mockup, or the page header's 21 px everywhere.
9. **Manage Clients list services**: tinted per-service pills (mockup) or the dots the other grids use.
10. **Panel-open grid**: hide GSTIN / PAN / Ph. no. / Services under the 540 px panel (mockup) or leave the columns in place.
