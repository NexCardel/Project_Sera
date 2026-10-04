# Sera Distill — report (2026-10-04 15:31)

Deadline: 2026-10-06T01:30:00+05:30

| Status | WPs |
| :--- | ---: |
| Not started | 21 |
| In progress | 0 |
| Retry | 0 |
| Done | 9 |
| Blocked | 0 |

Runs: 9   output tokens: 566929   API-equivalent cost: $8.31

| WP | Status | Model | Commit | What | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W0-1 | Done | sonnet | 36e67ef | Baseline: tests + the regression runner tools/sdis_regress.py | tools/sdis_regress.py + test + baseline; 16 tests pass; numbers as expected (double count 0, lost 0, orders agree yes) |
| W0-2 | Done | sonnet | c8f3bd6 | Engine package core/sdis/ (pre-dev files become module aliases) | core/sdis package built; pre-dev keys/align/link_map/memory/tables are aliases; regression printout identical to baseline; 70 SDIS tests pass |
| W1-1 | Done | gemini_first | 6260bab | Part B: value history with times | value history in LinkMap and PageMemory, core/sdis/history.py with day_of, 76 SDIS tests pass, regression identical |
| W1-2 | Done | gemini_first | 2a52bda | Part G: screens (one link, several pages) by weighted matching | Part G screens by weighted matching; 82 SDIS tests pass; regression identical |
| W1-3 | Done | gemini_first | 18a9998 | Part D: identity by session id + data fingerprint | Part D identity resolution by session id and data fingerprinting; 91 tests pass; regression 5 links -> 3 links (fictional dropped per D4); real GST clients stay |
| W1-4 | Done | gemini_first | 2b2b592 | Part N: smart page link resolution | Part N smart page link resolution; 101 tests pass; regression identical |
| W1-5 | Done | gemini_first | 1d03032 | Part M: per-browser memory | Part M per-browser memory; 105 tests pass; regression identical |
| W1-6 | Done | opus | 2643145 | Part R.1: Firefox address bar, title, and never reading background tabs (SGT-C) | Firefox URL (Edit or ComboBox), dash-agnostic title suffix, offscreen Documents never read; 12 new tests; regress unchanged |
| W1-7 | Done | opus | 9f2f751 | Part R.2: browser parity tool + Firefox fixtures | Parity tool + fixtures; client lines identical in 3 browsers; 4 fixes for W2-5 (select options, doubled labels, cached IsOffscreen in read_page_nodes, Edge titl |
| W1-8 | Not started | opus |  | Part T: portal registration from service-settings login links |  |
| W1-R | Not started | opus |  | Phase 1 review |  |
| W2-1 | Not started | gemini_first |  | Part E: look-alikes scored, AMBIGUOUS |  |
| W2-2 | Not started | gemini_first |  | Part C: noise over time |  |
| W2-3 | Not started | gemini_first |  | Part F: the variable_alignment state |  |
| W2-4 | Not started | gemini_first |  | Part H: memory upkeep (retire) |  |
| W2-5 | Not started | sonnet |  | Part R.3: Firefox line parity fixes (SGT-C) |  |
| W2-R | Not started | opus |  | Phase 2 review |  |
| W3-1 | Not started | sonnet |  | Part I: statuses + labels from memory |  |
| W3-2 | Not started | gemini_first |  | Part J: relevance by occurrences + slots |  |
| W3-4 | Not started | gemini_first |  | Part R.4: SDIS on Firefox trees |  |
| W3-5 | Not started | gemini_first |  | Part S.1: class suggestion (what a value stays the same with) |  |
| W3-3 | Not started | sonnet |  | Part O engine: memory on disk, incremental mining |  |
| W4-1 | Not started | opus |  | Part K: SDIS's own recorder (raw view, session id, browser) |  |
| W4-2 | Not started | opus |  | Part P: captures travel to the admin PC |  |
| W4-3 | Not started | opus |  | Part O: mining in its own process (no CPU cap) |  |
| W4-4 | Not started | opus |  | Part Q: registration on every PC (synced tables) |  |
| W4-5 | Not started | sonnet |  | Part L: the Distill dialog + loading dialog + containers |  |
| W4-6 | Not started | opus |  | Parts U + S.3: sdis_mcl, the containers file (sync + checks) |  |
| W4-7 | Not started | opus |  | Part S.2 in SGT: container instances, completion, Others values |  |
| W5-R | Not started | opus |  | Final review and merge-readiness note |  |

## Decisions taken for you

- **W1-7** Chrome/Edge started by the parity tool showed no page tree (0 lines); how to get a real read? → Launch Chrome/Edge with --disable-features=CalculateNativeWinOcclusion --disable-backgrounding-occluded-windows and Firefox with widget.windows.window_occlusion_tracking.enabled=false (throwaway profiles only) (Measured: a window opened behind others is occluded; Chromium builds no accessibility tree (even with --force-renderer-accessibility) and Firefox marks the front page offscreen. A user reads a page in front, so this reproduces the real condition.)
- **W1-7** Where do fixture docs and the background-tab filter come from, and what about W1-6's ineffective node filter? → Fixtures from key_probe.read_keys (control+raw) + key_probe._typed; read_keys filters Documents by the CACHED IsOffscreen. core/ untouched (rule 6): the read_page_nodes filter bug goes to W2-5 (key_probe's node fields are the fixture format; onscreen_only's CurrentIsOffscreen raises on AutomationElementMode_None elements so every Document is kept. W1-7 is not allowed to change SGT capture.)

## Checks waiting for you

- #1 (W1-6) Real GST and ITR sessions in Firefox: the HUD/SGT sees the portal (URL read) and captures the same fields as the same pages in Chrome — Not run
