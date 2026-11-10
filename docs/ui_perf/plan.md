# UI speed plan (for Opus 5.5)

Written 2026-11-10. Goal: make the desktop UI faster and more responsive without changing what
any screen shows or does. This file is self-contained: read it, then do the work packages in order.

**Precondition:** the `ui-overhaul` branch must be merged into `main` first. Every path below is
the post-merge tree (the grids are `DataGrid` in `ui/components/kit.py`). If `ui/components/kit.py`
does not exist on your branch, stop and say so.

## House rules

- Read `CLAUDE.md`. Full test suite only through the live T/F/S plugin, in the background, log to a
  file; add `--basetemp` (plain `pytest` crashes at collection).
- Work in your own worktree on a branch `ui-perf` cut from `main`. Other sessions use the main
  checkout: never `git stash` there, never switch its branch.
- One commit per work package. Do not push. Do not bump `version.json`.
- No behaviour change. The `tests/test_ui_lock_*.py` files are the behaviour lock: they must stay
  green and must not be edited to fit a change.
- Existing rules that still hold (each has a test): no pandas/openpyxl/`core.vsdc`/numpy imported
  at start-up; only the `mdi` icon font; never create widgets per row per refresh; never capture
  row data in widget lambdas; working-set trims only while the window is hidden.
- Do not call `search_win.refresh()` for background redraws (it clears the search box); use
  `_on_search_changed()`.
- Never launch the real app from a script: it would open the owner's real data in
  `~/AmanAssociates_Sera`. Measure with the probe.

## How to measure

`docs/ui_perf/ui_probe.py` builds a throwaway encrypted database (500 clients, 400 filings),
builds the windows offscreen and times each action.

```
python docs/ui_perf/ui_probe.py <repo dir> <empty temp dir> 500 400
```

- The first run seeds the database (about 2 minutes); later runs on the same temp dir reuse it.
- `PROBE_PROFILE="search rebuild"` (or `detail load`, `admin refresh`, `tracker load`,
  `settings open`, or a single space for all) prints a cProfile for that action.
- Timings on the owner's PC vary about ±30 % run to run. Run three times, compare the best.
- Offscreen painting is not the real GPU path; treat paint numbers as relative.
- Record before/after for every package in `docs/ui_perf/results.md`.

Start-up is judged from `~/AmanAssociates_Sera/logs/memory.log` ("start-up: window painted",
seconds since process launch) and `python -X importtime -c "import main"`.

## Baseline (2026-11-10, probe, best of repeats; ranges cover both trees and run noise)

| Action | Now | Target |
|---|---|---|
| Search grid full rebuild (500 × 12) | 570–690 ms | ≤ 100 ms |
| Typing in the search box | 285–400 ms | ≤ 40 ms |
| Open client detail panel | 365–560 ms | ≤ 180 ms |
| Switch to Tracker | 480–770 ms (0.9–2.5 s first time) | ≤ 200 ms |
| Switch to Manage Clients | 200–270 ms | ≤ 90 ms |
| Open Settings | 430–1,550 ms | ≤ 350 ms |
| Open Audit log | 270–730 ms | ≤ 150 ms |
| Window resize, per step | 75–120 ms | ≤ 40 ms |
| Start-up to window painted (real log) | ~7 s typical, 2.9 s best | ≤ 4.5 s typical |

Real-app log: slow search-grid loads average 301 ms (max 535), tracker loads 302 ms (max 424).
Targets are estimates. A package is done when its number is met or when the profile shows the
remaining time is outside our code; write down which.

## Work packages

### WP1 — Database: stop opening a connection per query

`SeraDatabase._connect` (`database.py`) opens a new SQLCipher connection, sets the key and six
PRAGMAs, and closes it, for every call. Tracker load opened 52 connections, a client load 16
(about 0.4 s and 0.2 s under the profiler).

1. Before changing anything, read why it is per-call: threads (capture worker, sync engine), the
   sync database swap (`sync_peer.apply_pending_swap`), `_enter_connection` / `_seal_after_commit`,
   and `data_generation()` (bumped from `conn.total_changes`).
2. Preferred shape: a cached connection per thread (thread-local), with the same commit/rollback
   and seal semantics, closed and reopened whenever the database file is swapped, restored or
   re-keyed, and closed on quit. `total_changes` is cumulative on a kept connection: compare
   before/after instead of testing truthiness.
3. If a kept connection cannot be made safe for writes, keep per-call connections for writes and
   cache only a read connection for the UI thread. Say which you chose and why.
4. Add tests: two threads do not share a connection; a swap/restore reopens it; a failed
   transaction rolls back and the next call works; `data_generation()` only moves on real writes.

Acceptance: `_connect` no longer appears as a top cost in the tracker and detail profiles; full
suite green.

### WP2 — Tracker: batch the per-row work

`TrackerDumpWindow._populate_grouped_table` → `_row_actions` → `_autofill_action` →
`_service_for_row` calls `db.get_client_services` once per visible row (25 rows, ~430 ms).
`sera_db/srpf.py get_srpf_containers` costs ~700 ms: 1,200 `json.loads` and `_fetch_clients_batch`.

1. Fetch services for all clients on the page in one query (add a batch method beside
   `get_client_services` in `sera_db/clients.py`) and pass the map down.
2. In `get_srpf_containers`, parse each payload once and reuse it; profile `_fetch_clients_batch`
   and `headline_entry` and remove repeated work.
3. Keep the row-widget reuse (`_set_action_cell` / `_set_status_cell`) and
   `tests/test_tracker_refresh.py` intact.

Acceptance: switch to Tracker ≤ 200 ms on the probe.

### WP3 — Search grid: no rebuild per keystroke

`SearchWindow._on_search_changed` (`ui/windows/search_window.py`) rebuilds the whole table on
every change: 6,000 `setItem` and 12,500 `setData` calls plus a database search.

1. First choice: back the grid with a `QAbstractTableModel` (rows held as plain Python data,
   `data()` answers roles) so nothing is created per cell. This needs `DataGrid` in `kit.py` to
   accept a model; check every user of `DataGrid` and of `results_table.item(...)` /
   `selectedRanges()` (copy, selection bar, column menu, cell formatting, activity dots) and the
   lock tests before committing to it.
2. If the model route would break the behaviour lock, do the cheaper route: keep the items,
   rebuild only when `data_generation()` or the column set changed, and filter by hiding rows
   against a precomputed lowercase text per row. Text typing must not hit the database.
3. Keep scroll position, current cell and selection across refreshes as today, and the
   unchanged-grid shortcut in `refresh()`.
4. Apply the same route to the Manage Clients grid (`AdminWindow.refresh`,
   `_client_key_values` runs 500 times per refresh).

Acceptance: typing ≤ 40 ms, full rebuild ≤ 100 ms, Manage Clients ≤ 90 ms.

### WP4 — Client detail panel

`ClientDetailWindow._load_client_internal`: 16 database opens, synchronous
`record_client_activity` + `log_action` writes (~180 ms) before the panel appears, and 59
`addWidget` calls restyling a visible panel (~160 ms).

1. Read everything the panel needs in one connection.
2. Show the panel first; write the activity and audit rows after the paint
   (`QTimer.singleShot(0, ...)`), still exactly once per open.
3. Build the cards with updates disabled or off-parent and attach once.

Acceptance: ≤ 180 ms on the probe; the audit and activity rows are still written (test it).

### WP5 — Settings and dialogs

`UnifiedSettingsDialog`: 96 per-widget `setStyleSheet` calls, 117 `addWidget` calls on a styled
visible parent, and `ui/utils/autostart.py is_autostart_enabled` runs `schtasks` (120 ms) on the
UI thread in `_load_settings`.

1. Move the autostart check off the UI thread (worker + signal; the switch shows its last known
   state until the answer arrives) or cache it for the session.
2. Replace per-widget style sheets with object names / properties styled once in the theme or
   one dialog-level sheet.
3. Build each page off-screen and add it to the stack once. Pages are already built on first
   visit; keep that.
4. Run the same profile on the Audit log dialog and apply what carries over.

Acceptance: Settings ≤ 350 ms, Audit log ≤ 150 ms.

### WP6 — Start-up

One 7.1 s run: imports 1.5 s; "code loaded" → "loading dialog shown" 2.1 s (not broken down
yet); window build 2.2 s (shell 0.7, search 0.3–0.7, admin 0.4, tracker 0.2–0.5); paint 0.4.

1. Add `memory_mark` stages inside the 2.1 s gap in `SeraApp.__init__` (bridge start, capture
   outbox import, pending rejoin/shadow/go-live/restore/catch-up checks, `_resolve_encryption_key`)
   and report the breakdown before fixing anything. Key derivation cost must not be weakened.
   If the gap contains a password prompt on some installs, say so: that is user time.
2. Imports: `qtawesome` costs ~0.6 s because `qtpy` imports extra Qt modules (QtOpenGL,
   QtDataVisualization); `argon2` ~0.13 s; `kit.py` ~0.14 s of its own. Trim what is safe (lazy
   `argon2`, avoid the extra Qt modules) and pin it with a test like
   `tests/test_startup_speed.py`.
3. Build `AdminWindow` and `TrackerDumpWindow` on first visit instead of at start-up. `main.py`
   wires many signals to both in `_build_ui` and other code reaches `self.admin_win` /
   `self.tracker_dump_win` (sync refresh, capture refresh, sidebar actions): every such use must
   work before the page exists. If that is too invasive, build them in an idle timer right after
   the first paint instead.

Acceptance: "window painted" ≤ 4.5 s typical in `memory.log` on the owner's PC (the owner checks
this; give them the before/after import and build numbers).

### WP7 — Grid painting and resize

`DataGrid` cell delegate `paint` / `_paint_text` in `kit.py`: ~1.3 ms per cell under the profiler;
a resize step costs 75–120 ms.

1. Cache per-cell work that does not change between paints (elided text per width, fonts, pens,
   `_has_check`).
2. Check `ui/utils/responsive.py` and `ChipBar` do not relayout more than once per resize event.

Acceptance: resize ≤ 40 ms per step; a page repaint roughly halves.

## Finish

- Full suite green with the T/F/S plugin; list any failure that also fails on `main` before your
  first commit.
- `docs/ui_perf/results.md`: the table above with a measured "After" column, what was not reached
  and why, and what the owner must check in the real app (start-up time, typing in search, tracker
  switch, settings open, a sync arriving while each page is open).
- Leave the branch unmerged and unpushed; merging is the owner's call.
