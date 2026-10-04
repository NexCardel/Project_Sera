# Sera Distill — report (2026-10-04 18:24)

Deadline: 2026-10-06T01:30:00+05:30

| Status | WPs |
| :--- | ---: |
| Not started | 5 |
| In progress | 0 |
| Retry | 0 |
| Done | 25 |
| Blocked | 0 |

Runs: 26   output tokens: 1305649   API-equivalent cost: $33.75

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
| W1-8 | Done | opus | 2797f2f | Part T: portal registration from service-settings login links | Part T: service login links register portals in vsdc_scope (D21 confirm on save), sdis_containers.json loader with version+portal_exceptions checks; 31 new test |
| W1-R | Done | opus | c627eb0 | Phase 1 review | Review: fixed per-browser map overwrite, identity per (link, browser) + screen-safe, fingerprint pair weighting, Part N votes per client; 140 passed; regression |
| W2-1 | Done | gemini_first | 33f376c | Part E: look-alikes scored, AMBIGUOUS | Part E: look-alikes scored pairing, ambiguous detection under margin 0.5, wired into memory and compare |
| W2-2 | Done | gemini_first | f5e5d3d | Part C: noise over time | Part C: noise over time, changes_with_time, probably_furniture heuristic, wired into memory.py |
| W2-3 | Done | gemini_first | c9d12c0 | Part F: the variable_alignment state | Part F: variable_alignment state, rejected hook, compare_flat shared labels |
| W2-4 | Done | gemini_first | ede141d | Part H: memory upkeep (retire) | Part H: memory upkeep (retire), RETIRE_P = 0.01, client history and misses |
| W2-5 | Done | sonnet | b551792 | Part R.3: Firefox line parity fixes (SGT-C) | Firefox lines now identical to Chrome/Edge on the fictional pages (dropdown options, wrapped labels, cached offscreen, Edge ZWSP title); regression unchanged |
| W2-R | Done | opus | f0f325f | Phase 2 review | Phase 2 review: one documented verdict order; 6 defects fixed with 6 tests; regression unchanged, orders agree yes |
| W3-1 | Done | sonnet | e7854ea | Part I: statuses + labels from memory | Part I: memory.status/label from client views, table cell labels, furniture via real labels, compare memory view; 204 tests pass, regression counts unchanged |
| W3-2 | Done | gemini_first | 8460cf1 | Part J: relevance by occurrences + slots | Part J: core/sdis/relevance.py datapoints(); 93 datapoints on real captures; regression unchanged; 213 SDIS tests pass |
| W3-4 | Done | gemini_first | 128efd5 | Part R.4: SDIS on Firefox trees | alignment + label tests parametrized over edge/msedge/chrome/firefox fixtures; all pass, no core change |
| W3-5 | Done | gemini_first | 76d79f7 | Part S.1: class suggestion (what a value stays the same with) | classes.py suggest/annotate (S.1), read_info per read, 9 tests, regress: suggested class profile 0 dataset 3 info 7 none 83, nothing else changed |
| W3-3 | Done | sonnet | eb2abfa | Part O engine: memory on disk, incremental mining | store.py + mine.py: incremental mining with per-client save/cancel/resume; 265 SDIS tests pass; regression identical, mine agrees |
| W4-1 | Done | opus | f6dd800 | Part K: SDIS's own recorder (raw view, session id, browser) | SdisRecorder (own UIA thread, 1.5 s budget, 500 MB cap) offered by SgtShadow after SGT's read; sdis_record setting; sources.py feeds mine/link_map; SGT tests +  |
| W4-2 | Done | opus | 864adf5 | Part P: captures travel to the admin PC | Part P: core/sdis/transfer.py push/handle_push/Pusher, sdis_push in dispatch_session, main.py trigger; 6 new tests; regression identical |
| W4-3 | Done | opus | 5b16e05 | Part O: mining in its own process (no CPU cap) | mine_process.run via main.py --sdis-mine (JSON lines), MinerClient (reader thread, instant cancel, no CPU cap); 8 new tests, SDIS 294 passed, regression unchang |
| W4-4 | Not started | opus |  | Part Q: registration on every PC (synced tables) |  |
| W4-5 | Not started | sonnet |  | Part L: the Distill dialog + loading dialog + containers |  |
| W4-6 | Not started | opus |  | Parts U + S.3: sdis_mcl, the containers file (sync + checks) |  |
| W4-7 | Not started | opus |  | Part S.2 in SGT: container instances, completion, Others values |  |
| W5-R | Not started | opus |  | Final review and merge-readiness note |  |

## Decisions taken for you

- **W1-7** Chrome/Edge started by the parity tool showed no page tree (0 lines); how to get a real read? → Launch Chrome/Edge with --disable-features=CalculateNativeWinOcclusion --disable-backgrounding-occluded-windows and Firefox with widget.windows.window_occlusion_tracking.enabled=false (throwaway profiles only) (Measured: a window opened behind others is occluded; Chromium builds no accessibility tree (even with --force-renderer-accessibility) and Firefox marks the front page offscreen. A user reads a page in front, so this reproduces the real condition.)
- **W1-7** Where do fixture docs and the background-tab filter come from, and what about W1-6's ineffective node filter? → Fixtures from key_probe.read_keys (control+raw) + key_probe._typed; read_keys filters Documents by the CACHED IsOffscreen. core/ untouched (rule 6): the read_page_nodes filter bug goes to W2-5 (key_probe's node fields are the fixture format; onscreen_only's CurrentIsOffscreen raises on AutomationElementMode_None elements so every Document is kept. W1-7 is not allowed to change SGT capture.)
- **W1-8** Service settings, on save: Sera shows 'will watch gst-like.gov.in for <service>' (D21). If the user answers No, what happens? A = nothing is saved, the edit dialog stays open so the link can be changed. B = the service is saved but its domain is not watched (Sera remembers this choice per service in a synced setting). → A (asked as Q1)
- **W1-8** Which services overlap rules apply when registering portals? → A domain equal to or covering/under Income Tax or GST is not registered (built-in wins); a domain overlapping one an earlier service (sort order) claimed is skipped; extra_domains only for portal names that are services; a single-label host gives no domain (no duplicate portals for one domain; narrowest scope; built-ins stay untouched)
- **W1-R** Part N: what counts as a value vote for a link position? → each distinct client once (VALUE_CLIENTS = 3, page votes 0), not each link pair (R8: link-pair counting let 2 clients with 3 sibling pages mask a position, while many clients sharing 2 years never did; 3 keeps W1-4's tested 2-clients-not-enough / 3-clients-masked behaviour)
- **W1-R** Part D: rarity weight of a value both compared captures show → count the compared pair as one capture: 1 / (rarity - 1) (an equal value is always shown by both, an unequal one by one; literal 1/rarity halved agreement, so one changed value among 10 rare ones gave 0.82 (undecided) instead of 0.9 (same); blueprint says an ARN weighs 1)
- **W1-R** Part D check: the two fictional local-page sessions should be one client → keep them undecided (no vote) (counts on today's captures: per page 3 codes, 2 periods and 1 alphanumeric value differ (sim 0.70, was 0.56): not one client; the blueprint's expectation was wrong, undecided is the honest result (rule 6))
- **W2-3** How does memory match rejected signatures? → (link, shape, text) matching either self.link or self.page (Allows callers to pass either resolved base link or page string cleanly)
- **W2-5** Edge window title with profile name: strip only the zero-width space, or also the profile segment? → Allow zero-width spaces inside 'Microsoft Edge' in BROWSER_SUFFIX_RE only (Chrome/Edge titles must not change; the leftover '- Profile 1' is stable and only used when the address bar is unreadable)
- **W2-5** How to key Firefox-only line rules (dropdown options, doubled label)? → On the Document's framework id 'Gecko' (live CurrentFrameworkId / cached 30024); node trees carry gecko:true on the document's first node (Blueprint rule: browser-dependent rules key on framework id; Chrome/Edge lines and nodes stay byte-identical)
- **W2-R** Retirement follows time order, so forward/reversed builds can legitimately differ once 4+ clients exist; how is 'orders agree' kept meaningful? → The order check builds both orders with retire=False (PageMemory/build retire flag); the runner prints 'retired (time order): k' from the forward build (Part H counts misses in a row by time; the order check is meant to prove alignment and verdicts do not depend on client order)
- **W2-R** Part C rule 2 'changes with time': does a day seen by only one client count as agreement? → No: the texts must differ between days that 2+ clients were seen on (Rule 6: two clients captured on different days with their own data would otherwise be called noise and their datapoint lost)
- **W3-1** Which verdicts may a label candidate come from, given probably furniture asks for labels while verdicts are being computed? → Base verdict 'same for all clients' with a fixable or alphanumeric type, plus unpaired repeats of such a (shape, text) (Same set as compare.py's fixed + variable_alignment + semi-variable alphanumeric (variable_alignment only ever comes from a fixable text); avoids the label -> verdict -> furniture -> label loop)
- **W3-1** A node in a real table whose column has no name (or a header cell): what label? → Fall back to the screen-box rules (labels._label, which still tries the structural _table_label first) (Blueprint: box rules apply to values outside any table and to tables without table markup; a nameless column gives the table nothing to say)
- **W3-5** How do container moves reach suggest()? → moves={key: class} argument; override with reason 'moved by the user' (sdis_decisions table does not exist before W4-4; the dict is what that reader will pass)
- **W4-1** Which UIA worker does SDIS's raw read use? → Its own: the recorder thread joins the MTA and creates its own CUIAutomation8 (as sgt_i/uia_events); new uia_nodes.read_page_nodes_here reads on that thread; read_page_nodes unchanged (read_page_nodes runs on vsdc_uia_text's single shared worker; a raw read still in flight when SGT's next read starts would make SGT abandon that worker and count toward UIA switching off (5 per run), which would change SGT capture (rule 6))
- **W4-1** How is a recorder session named for the engine? → record carries started (time of the session's first record this run); sources names it 'sgt <session id> <started>' (identity._by_time orders sessions by their last word as a time; a bare SGT session id is random hex, so client order and retirement (time order) would be random)
- **W4-2** Push trigger, batch size and repeat files → Any successful sync session triggers a push at most every 15 min on its own thread; a push carries at most 200 files / 200 MB (rest next time); a file written in the last 60 s is not finished; a re-pushed name replaces the stored copy (atomic .part + os.replace); names other than sdis_YYYY-MM-DD.jsonl refuse the whole manifest (Fits the transport's 600 s session deadline, never sends a file still being written, and a lost ack only causes a harmless resend)
- **W4-3** Which captures folders does one mining run take, given pushed files sit in corpus/<device id>/ with the same day names on every PC? → One --captures folder per run, as mine() takes today; walking corpus/<device>/ plus sdis_capture (file keys by relative path) is left to the WP wiring the button (W4-5) (mine() keys files by bare name, so a multi-folder run needs an engine change to state keys; out of W4-3's scope and not on the regression path)
- **W4-3** Does the PyInstaller spec need core.sdis.mine_process added by hand? → No: build_tools has no .spec in the repo and no hand-listed hiddenimports; main.py imports the module statically so PyInstaller finds it (grep of build_tools for hiddenimports found none; nothing to edit, no installer built)

## Checks waiting for you

- #1 (W1-6) Real GST and ITR sessions in Firefox: the HUD/SGT sees the portal (URL read) and captures the same fields as the same pages in Chrome — Not run
- #2 (W1-8) Admin PC: Manage Services, add a service with a non-GST/ITR login link (e.g. EPF), confirm the domain prompt, open that portal in Chrome and check SGT/VSDC now reads it; delete the service and check capture stops there; also edit a link to a shared sign-in site listed in never_register and check the 'not registered' message — Not run
- #3 (W4-1) Real Chrome, Edge and Firefox on a GST page: sdis_capture gets one record per changed page with the right browser; SGT capture behaves as before — Not run
- #4 (W4-2) Two real office PCs: a staff PC's sdis_capture files arrive on the admin PC and are deleted on the staff PC only after arriving — Not run
- #5 (W4-3) Find datapoints on the admin PC: the loading dialog shows progress, Sera stays responsive behind it, Cancel stops mining at once and the next run continues — Not run
