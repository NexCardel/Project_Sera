# UI scale + adaptive layout — plan

Written 2026-10-08 for a separate agent to carry out. Everything you need should be in this file; read the
files it names before you change them.

## 1. The problem

One office PC runs Sera 2.12.6 on a broken display setup (probably the Windows basic display driver:
about 1280 px wide, possibly at 125 % scale). We can't fix that PC's resolution and can't reach it.
It does get updates through the fixed update agent, so the fix has to ship in a normal release.

What the screenshot shows: Sera's logical window is narrow (estimated at about 1024–1280 logical px; the
sidebar draws about 215 px for a 172 px `setFixedWidth`, which suggests 125 %). Clients/Search is passable;
**Tracker Dump is badly cluttered.**

Root cause found in `ui/windows/tracker_dump_window.py`:
- `_adjust_table_columns()` (around line 2089) gives six columns **fixed** widths:
  120 + 250 + 280 + 130 + 150 + 120 = **1050 px**, and the Client column only gets what's left
  (minimum 60). Add the 172 px sidebar and 28 px of margins and the page needs about 1250 logical px
  before Client gets any room at all.
- The filter row has a 320 px fixed search box, the segmented view buttons, 5 filter chips and Reset,
  all in one `QHBoxLayout` that can't wrap.
- The 5 summary tiles, the subtitle and the 48 px rows all assume a wide screen.

Hard requirement: **the other PCs must look exactly as they do today.** Nothing keys on hostname.
Both fixes depend only on screen and window size, so any future small screen is handled too.

## 2. The two fixes (they work together)

| Fix | What it does | Covers |
|---|---|---|
| **A. Per-PC UI scale** | Sets `QT_SCALE_FACTOR` before `QApplication` is created; an auto default for small screens, plus a manual override | A screen that is too small as a whole |
| **B. Adaptive layout** | Pages rearrange by their own current width (wide / compact) | Narrow windows, including a scaled small screen and a half-snapped window |

Order: A works out the scale, and B then measures the width that's left in logical px after scaling.

Guardrails:
- Scale is clamped to **0.80–1.00** (manual override may go up to 1.25). Below 0.80 text is too small;
  let compact layout take over instead.
- A is never stored in `app_settings`: that table **is synced** (`sync_schema.py:140`), and a scale value
  would spread to every PC. Store it per PC in `QSettings("AmanAssociates", "ProjectSera")`
  (HKCU registry; already used in `ui/windows/search_window.py:542`). `QSettings` with explicit
  org/app names works before `QApplication` exists.

## 3. Part A: per-PC UI scale

### A1. New module `ui/utils/ui_scale.py`
Keep it pure and testable:
- `compute_auto_scale(logical_work_width: float, target: float = 1280.0) -> float`:
  `s = logical_work_width / target`, rounded **down** to 0.05, clamped to [0.80, 1.00].
  Examples: 1920 → 1.00; 1366 → 1.00; 1280 → 1.00; 1152 → 0.90; 1024 → 0.80; 800 → 0.80.
- `resolve_scale(stored_mode: str, stored_value: float|None, probe: ScreenProbe|None) -> float`:
  modes are `"auto"` (default) and `"manual"`. Manual clamps to [0.80, 1.25]. If auto has no probe, use 1.0.
- `probe_primary_screen() -> ScreenProbe|None` (Windows only; returns None anywhere else or on any error):
  primary monitor work-area width in **physical** px and the effective DPI, so
  `logical = physical_width / (dpi / 96)`.
- `apply_scale_env(scale: float)`: sets `os.environ["QT_SCALE_FACTOR"]` only when scale != 1.0, and only
  when the user hasn't already set `QT_SCALE_FACTOR` themselves (respect an existing env var).
- `read_settings()` / `write_settings(mode, value)` using the QSettings keys `ui_scale_mode` and
  `ui_scale_value`.

Probe: this must run **before** `QApplication`, so Qt screen APIs aren't available. Recommended:
ctypes `MonitorFromPoint((0,0), MONITOR_DEFAULTTOPRIMARY)` → `GetMonitorInfoW` (work area) and
`shcore.GetDpiForMonitor(MDT_EFFECTIVE_DPI)`. Those return virtualized values unless the process is DPI-aware,
so first check what Qt 6 / the PyInstaller manifest already sets (`build_tools/Amas_Sera.spec` is gitignored,
so look at the file itself) and do one of these:
1. Set the same awareness Qt would set (per-monitor v2) before probing, and confirm Qt doesn't log an error
   or change behaviour because of it; **or**
2. Fallback: once the app is up, save the real `QGuiApplication.primaryScreen().availableGeometry().width()`
   and `devicePixelRatio()` in QSettings (`ui_scale_last_screen`) and use those numbers on the next start.

Pick whichever you can verify. Write down what you chose and why at the top of the module. If you use only
option 2, auto scale takes effect from the second start; say so in the settings hint.

### A2. Hook in `main.py`
In `__init__`, before `QApplication(sys.argv)` (around line 186, next to the `HighDpiScaleFactorRoundingPolicy`
call): read settings → probe → resolve → `apply_scale_env`. Wrap all of it in try/except: a failure here must
never stop Sera starting (fall back to 1.0). Add a `memory_mark` / log line such as
`[UI] scale=0.80 (auto, logical width 1024)`. Log no other screen details.
`self.app.setFont(QFont("Segoe UI", 10))` and the `px` stylesheets scale with Qt's factor; leave them alone.

### A3. Manual control
- In `ui/dialogs/unified_settings_dialog.py`, add a "Display" group on the General page
  (`_build_page_general`): Auto / 80 / 90 / 100 / 110 / 125 %, a hint ("Applies after restarting Sera. Saved on
  this PC only."), and a Restart-now button if the app already has a restart path (look first; otherwise just
  the hint). Check whether that dialog is admin-only. Display has to be reachable by non-admin staff, so if
  the dialog is admin-only, put the control somewhere ordinary users can open.
- Shortcuts in the app shell: `Ctrl+=` / `Ctrl+-` / `Ctrl+0` step the manual value by 0.05 (0 = back to Auto),
  save it, and show a small toast saying "Display scale 85 % — restart Sera to apply". No live re-layout: Qt
  reads the factor only at start-up.

## 4. Part B: adaptive layout (Tracker Dump first)

### B1. Shared helper `ui/utils/responsive.py`
- `class WidthMode(Enum): WIDE, COMPACT`.
- `mode_for(width: int, current: WidthMode) -> WidthMode` with hysteresis: go COMPACT below **1080**, back to
  WIDE above **1120** (logical px of the *page* widget, not the screen). Put the thresholds at the top as
  constants.
- Pages call it from `resizeEvent` and re-layout only when the mode changes.

### B2. Tracker Dump (`ui/windows/tracker_dump_window.py`)
WIDE mode must render **pixel-identical to today** (check with snapshots, B5).

**Table columns** — replace the fixed widths in `_adjust_table_columns` with a priority fit:
- Client keeps a minimum of **240 px** and always stretches.
- Preferred widths stay as today. When they don't fit, first shrink each toward a minimum
  (Filing 90, Period 170/140, Status 200, Actions 110, Updated 120, Device 100), then hide columns in this
  order: **Device → Updated → Filing** (Filing only if the client column still can't reach 240). Status,
  Period and Actions are never hidden.
- Put the layout maths in a pure function, e.g.
  `fit_columns(available: int, is_grouped: bool, user_widths: dict, user_hidden: set) -> (widths, hidden)`,
  so it can be unit-tested without a widget.
- Values from auto-hidden columns go into the Client cell tooltip (e.g. "Updated 2 h ago · DESKTOP-ABC"),
  so nothing is lost.
- Header right-click menu: tick columns to show or hide. The user's choice is saved per PC in QSettings
  (`tracker_dump_hidden_cols`) and wins over auto-hide. Columns dragged by hand (`_user_col_widths`) keep working.

**Header / filters (COMPACT only):**
- Hide the subtitle label.
- Search box: drop `setFixedWidth(320)` for min 180 / max 320, so it can stretch.
- Segmented buttons: shorter text ("Containers" / "Raw").
- Filter chips: show at most what fits; put the rest behind one "More ▾" chip (a menu with the same
  actions). The simpler option is fine too: in COMPACT, move the chips to a second row under search.
  Choose one and say which.
- Summary tiles: min height 50 → 40, value font 20 → 16 px, captions elided with a tooltip.
- Row height 48 → 40.

**Status bar:** in COMPACT, hide the "Rows per page:" label (keep the combo).

### B3. Sidebar auto-collapse (`ui/shell/app_shell.py`)
When the shell's width drops below **1000 logical px**, call `collapse_sidebar()` on its own, but only if
the user hasn't toggled the sidebar themselves this session. Expand again above 1060 under the same rule.
A manual toggle always wins until restart.

### B4. Other pages — check only
Render All Clients/Search, client detail, LTT rules, settings and the main dialogs at 1024×720 logical.
Fix only real breakage (cut-off buttons, controls overlapping, a dialog bigger than the screen; for example
`PayloadInspectorDialog.setMinimumSize(760, 560)` is fine, but check nothing is above 1000×700).
List anything bigger as follow-ups in your report instead of redesigning it.

### B5. Snapshot tool
Extend `tools/ui_snapshots.py` with `--size WxH` (repeatable) and `--scale F` (sets `QT_SCALE_FACTOR` before
the app is created). Produce Tracker Dump at 1920×1000, 1280×800 and 1024×720, plus 1024×720 at scale 0.8.
Seed enough tracker rows so all 7 columns have real-looking (fake) data. Compare WIDE before and after: it
must not change.

## 5. Tests (add under `tests/`)
- `test_ui_scale.py`: `compute_auto_scale` table (the examples in A1), clamping, manual mode, an existing
  `QT_SCALE_FACTOR` is respected, probe returns None off-Windows / on error, and **the scale is never written
  to `app_settings`** (grep-style assertion that `ui_scale` doesn't appear in the DB settings code paths, or a
  behavioural test with a temp DB).
- `test_responsive.py`: hysteresis both ways; no flapping at 1080–1120.
- `test_tracker_dump_columns.py`: `fit_columns` at 1600 / 1100 / 900 / 700 — Client ≥ 240 whenever
  possible, hide order Device → Updated → Filing, Status/Period/Actions never hidden, user-hidden and
  user-widths respected, and widths at ≥ 1300 equal today's numbers exactly.
- Keep `tests/test_startup_speed.py` passing (the probe must be cheap; no heavy imports at start-up — see
  the memory/performance rules in that test).

Run the full suite the way `CLAUDE.md` says (live T/F/S plugin, in the background, writing to a log file).
Report the summary and any failures. Plain `pytest` crashes during collection.

## 6. Rules for this task
- Work on a branch `ui-adaptive-layout`. Commit there; **do not push, do not merge, do not open a PR.**
- The main checkout has uncommitted `CLAUDE.md` and `tools/live_tf.py` changes that aren't yours: never stage
  or commit them.
- **Don't bump any version, don't touch `version.json`, don't build or publish an installer.** Releasing is
  the owner's call.
- Don't add hostname or PC-name checks anywhere.
- The repo is public: no real client names, PANs, GSTINs or ARNs in tests, seeds or snapshots. Use fake data.
- Write code that reads like the code around it (inline QSS, `_safe_qta_icon`, comment style).
- Shell gotcha: backslashes in bash heredocs turn into control characters. Use the Write/Edit tools for files.

## 7. Done means
1. On a simulated 1024-wide logical screen: auto scale 0.80, Tracker Dump fits with no clipped columns,
   Client ≥ 240 px, filter row not overflowing (snapshots attached to the report).
2. At 1920 wide: scale 1.00, all snapshots identical to before.
3. Manual scale and the shortcuts work and are saved per PC only.
4. New tests pass. Full suite: report the count; no new failures compared with `main`.
5. A short report: what changed, the probe approach chosen, before/after snapshot paths, follow-ups.
