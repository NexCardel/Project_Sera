# SGT never-miss — report (2026-10-14 14:51)

Deadline: 2026-10-17T23:00:00+05:30

| Status | WPs |
| :--- | ---: |
| Not started | 14 |
| In progress | 0 |
| Retry | 0 |
| Done | 3 |
| Blocked | 0 |

Runs: 3   output tokens: 121601   API-equivalent cost: $8.49

| WP | Status | Model | Commit | What | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| WB | Done | haiku | bd0570a | Baseline: the test suite before any change | Baseline: T=3078 F=2 S=1; failing: test_class_diff_render::test_output_paths_never_recorder_or_tracked_folders, test_sdis_tables::test_command_line_prints_no_va |
| WP0 | Done | sonnet | db317c5 | Probe pack: the Windows measurements tool | tools/sgt_probe_pack.py + 7 tests (1089 sgt/vsdc tests pass); 6 owner checks added; no probe run |
| WP1 | Done | sonnet | 0cc5e4a | Capture health: states, HUD event, phone alert, log | HealthMonitor (13 states), HUD event, phone alert, health log lines; 33 new tests, narrow run 1200 passed 1 skipped; owner check 7 added |
| WP2 | Not started | sonnet |  | Rows never lost: database retry and dead letter |  |
| WP3 | Not started | opus |  | Reader v2: MTA lanes, cached read, tickets, torn detection, Document URL |  |
| WP4 | Not started | opus |  | CaptureLoop: the SGT path out of the router |  |
| WP5 | Not started | opus |  | SGT absorbs by provenance: late results, torn and blind rules, source policy |  |
| WP6 | Not started | sonnet |  | Simulation harness and the six day profiles |  |
| WP7 | Not started | sonnet |  | Change gate v2 tuning and the flash OCR trigger |  |
| WP8 | Not started | sonnet |  | Blind pages on every portal |  |
| WP9 | Not started | sonnet |  | Identity persistence |  |
| WP10 | Not started | opus |  | Visible windows that are not in front |  |
| WP11 | Not started | sonnet |  | Recovery: the latest filing on list and status pages |  |
| WP12 | Not started | sonnet |  | Fleet health view and the mandatory update path |  |
| WP13 | Not started | sonnet |  | Live soak tool and pass/fail |  |
| WP14 | Not started | haiku |  | Scenario sweep and documentation |  |
| WPR | Not started | opus |  | Final review |  |

## Checks waiting for you

- #1 (WP0) doc-url: on the office PC open a GST/portal page in Edge, Chrome and Firefox (also one in F11 full screen, one in a popup window, and one PDF viewer tab), then run: venv/Scripts/python.exe tools/sgt_probe_pack.py doc-url   Report is ~/AmanAssociates_Sera/logs/probe_pack_<date>.txt (never commit it). Fill blueprint section 12 rows 'Document Value/URL in Edge/Chrome/Firefox, full screen, popup' (per browser: 'Value is a URL' vs 'documents', 'equal to the address bar') -> decides D1; and the row 'PDF viewer exposes text through UIA' (PDF viewer line YES/NO) -> C4. — Not run
- #2 (WP0) read-timing (incl. R9): open the heaviest real page you have (GSTR-1 B2B with hundreds of rows) in the browser, then run: venv/Scripts/python.exe tools/sgt_probe_pack.py read-timing --minutes 240   Then, with TWO windows of the SAME browser on portal pages: venv/Scripts/python.exe tools/sgt_probe_pack.py read-timing --two-windows --minutes 10   Fill section 12 rows 'full read of a real B2B page, element-by-element vs cached (ms, element count)' and 'read-time growth over hours' (first quarter -> last quarter of the SUMMARY) -> D2 (STA side); the two-windows SUMMARY verdict SERIALISED/PARALLEL/PARTLY (and its ratio) settles R9's one-heavy-read-per-browser rule. — Not run
- #3 (WP0) read-timing --mta: same page and same hours as the plain read-timing run, so the two compare: venv/Scripts/python.exe tools/sgt_probe_pack.py read-timing --minutes 240 --mta   Fill the section 12 row 'read-time growth over hours, STA vs MTA' with the MTA numbers (ms median/p95/max, first quarter -> last quarter, timeouts, 'read worker' abandoned/stuck line) next to the STA ones -> decides D2 (keep STA or move the read worker to MTA). — Not run
- #4 (WP0) gate: work normally on a portal page in front (type invoice rows, let a toast / banner / modal / spinner appear) and note the clock time of each: venv/Scripts/python.exe tools/sgt_probe_pack.py gate --minutes 5   Fill section 12 rows 'frame tick cost' (TOTAL 'frame tick ms' median/p95) -> D7 and R10: 0.2 s tick if a tick costs under 15 ms, else the 0.35 s fallback; and 'cells changed by typing/caret/spinner/toast/banner/modal' (histogram and 'largest changes' matched to your notes, plus how often the legacy hash fired vs >= GATE_MIN_CELLS 3 / FLASH_MIN_CELLS 8) -> D4. — Not run
- #5 (WP0) toast: do a REAL filing yourself (the tool submits nothing) while this runs on the portal tab in front: venv/Scripts/python.exe tools/sgt_probe_pack.py toast --minutes 20   Fill section 12 row 'ARN visibility UIA vs OCR plus what FlashWatcher catches' from the 'ARN visibility' lines (visible seconds per channel, sample interval, FlashWatcher caught / did not) and the OCR ms line -> D5 (and the C1-C3 capture-channel choice). Add --no-ocr if OCR is unavailable on that PC. — Not run
- #6 (WP0) switches: during a normal hour of B2B work run: venv/Scripts/python.exe tools/sgt_probe_pack.py switches --minutes 60   It keeps counts only (no titles, links or text). Fill section 12 row 'foreground/tab switches per minute' with the TOTAL foreground changes and tab changes per minute median/max -> D1 (how often the page identity must be re-resolved) and D4 (harness profile 1: how bursty switching is). — Not run
- #7 (WP1) OWNER: read the HUD wording and D8. Title 'Capture paused on this PC' with one fixed sentence per state (stalled, uia-paused, blind, scope-lost, busy-long, specs-silent, disk; unlisted-portal keeps the router's own prompt and only phones), recovery 'Capture back to normal'. D8: blind after 20 s with a failed read, HUD at most once per 60 s per state and repeating every 5 min, phone at most once per 30 min per state and inside the shared hourly limit. Pass = wording is clear to staff and the timings feel right; fail = say what to change. — Not run
