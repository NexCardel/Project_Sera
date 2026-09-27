# SGT overhaul — report (2026-09-28 01:03)

Deadline: 2026-09-29T01:30:00+05:30

| Status | WPs |
| :--- | ---: |
| Not started | 13 |
| In progress | 0 |
| Retry | 0 |
| Done | 14 |
| Blocked | 0 |

Runs: 20   output tokens: 565708   API-equivalent cost: $25.81

| WP | Status | Model | Commit | What | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W0-1 | Done | sonnet | 623424f | Find why the SGT page corpus stopped recording after 22 Sep and fix it | Root cause: sgt_record_pages flipped off silently (spec_stats.json proved reads continued); made PageRecorder.enabled echo every transition; regression tests ad |
| W0-2 | Done | haiku | 0238725 | Baseline: SGT tests and replay baseline, recorded for later diffs | SGT 281 passed, VSDC 388+6 pre-existing, baseline 8 sessions/1 dataset |
| W1-1 | Done | opus | bea35a6 | SGT-I host: the C/I contract in code | core/sgt_i host (thread, budget, hang trip, advisory+enrichment channels), sgt_i_mode Off/On, Core byte-identical On vs Off; 297 SGT tests pass |
| W1-2 | Done | sonnet | e724987 | Enrichment fields on tracker rows (no schema change) | Enrichment channel already round-tripped via existing raw_payload_json plumbing (no schema change); added read-only SGT-I card in PayloadInspectorDialog; tests: |
| W1-R | Done | opus | d4d6da4 | Review: W0-1..W1-2 against the SGT-C safety rules | Review OK except 2 fixed defects: SGT-I detach race could drop a Core row; 4 payload scanners read raw_payload.sgt_i as identity/name evidence |
| W2-1 | Done | opus | 54ae8d7 | Measure a cached UIA node read vs the current line reader | Cached node read: heavy 927->530 ms, normal ~40->~32 ms, control-view lines identical; unpack ~205 ms is next cost |
| W2-2 | Done | opus | 63c7f4c | Page map: node model, zones, sections, layout pairing | core/sgt_i/page_map.py: Node, UIA+OCR builders, zones, sections, layout pairs; 10 tests on 14.1 cases; 312 SGT tests pass |
| W2-3 | Done | sonnet | 56b03e3 | Lines view from nodes + equivalence check against today's reader | tools/sgt_lines_equivalence.py: 14.2 gate, synthetic+corpus+live modes; 316 tests pass |
| W2-4 | Done | sonnet | cec95e6 | Recorder stores nodes (corpus format v2, backwards compatible) | corpus v2: PageRecorder records nodes alongside lines while SGT-I is on; replay reads v1/v2 unchanged; 322 tests pass |
| W3-1 | Done | sonnet | ede9b8d | Container -> value pairs with generic types and masking | core/sgt_i/pairs.py: pairs_from_page produces container/type/masked-shape only; disk-level privacy test round-trips through insert_tracker_dump and scans file b |
| W4-1 | Done | sonnet | 27201bb | Maths 1: shape grammar induction with rule-of-three bounds | shapes.py: shape grammar generalisation + rule-of-three confidence bound; 13 new tests, 345 total pass |
| W4-2 | Done | sonnet | 4af6a8f | Maths 2: checksum discovery, values inside values, relations | invariants.py: checksum discovery (Luhn/Verhoeff/mod11/mod36, Bonferroni-corrected), value containment, order/sum relations; 19 new tests, 364 total pass |
| W4-3 | Done | sonnet | 38f3e57 | Maths 3: container kinds from salted-hash statistics | stats.py: salted-hash container-kind classifier (template/vocabulary/profile/dataset/identifier), persisted counts+hashes only; 19 new tests, 383 total pass |
| W5-1 | Done | opus | 3f5464e | The atlas: model, page matching, merge, template promotion, ageing | core/sgt_i/atlas.py: public+private per-portal JSON, Jaccard page identity with URL hint, per-word template promotion (>=3 clients, >=60% share), optional regio |
| W5-2 | Not started | sonnet |  | Atlas tool: show / diff / coverage |  |
| W6-1 | Not started | sonnet |  | The GPS: position, route, progress, row enrichment |  |
| W6-2 | Not started | opus |  | Read harder near the finish line (advisory to the Core) |  |
| W7-1 | Not started | sonnet |  | Assertion checker (NegEx-style), trigger words in config |  |
| W7-2 | Not started | sonnet |  | Page kinds classifier |  |
| W8-1 | Not started | opus |  | Evidence ledger, retraction, explanations, second opinions |  |
| W9-1 | Not started | sonnet |  | Sera's own data: client list and tracker, read-only |  |
| W9-2 | Not started | sonnet |  | Tracker expectations and self-healing (known-value anchoring) |  |
| W10-1 | Not started | opus |  | UIA events for short-lived messages + page diffing |  |
| W11-1 | Not started | haiku |  | 'Not understood' residue report |  |
| W12-1 | Not started | opus |  | The miner: proposals from the atlas, maths and anchoring |  |
| W12-2 | Not started | sonnet |  | SGT lab screen |  |
| W12-R | Not started | opus |  | Final review and merge-readiness report |  |

## Decisions taken for you

- **W1-1** SGT-I budget, hang limit and what trips it → 2.0 s per page for all components, hung = still running 15 s (checked when the Core hands over the next page); any component exception, overrun, hang or non-JSON/over-4KB enrichment trips ALL of SGT-I off for the run; re-enabling does not reset (Blueprint 14.2 rule 5 read literally; cheapest safe guard, Python threads cannot be killed)
- **W1-1** Advisory and enrichment channel shape → ask_more_reads: max 3 outstanding per session, each expires after 60 s, consumed only when the Core's change gate would have skipped the read; enrichment keyed session -> component, merged into raw_payload['sgt_i'] at row dispatch (best effort, async) and dropped when SGT-I is off/tripped; queue holds 8 pages, oldest dropped (Rule 3 (more reads only, bounded) and rule 4 (own key only); Off leaves rows byte-identical)
- **W2-1** Which UIA tree view should the cached node read use? → Control view (UIA default TreeFilter) (Its lines are identical to read_page_text on all 3 test pages and it is fastest (heavy 530 ms vs raw 767 ms); raw view adds ~2x nodes and different lines.)
- **W2-2** Where do the zone words (help/FAQ, stepper) live? → Generic UI words as DEFAULT_VOCAB in page_map.py, overridable via a vocab argument; no portal wording in code (They are portal-neutral UI words; portal-specific wording can be passed from config by the step-2 component without changing page_map)
- **W2-2** How is a stepper told apart from a tab bar or a row of labels, and how is the current step known? → Row of >=3 short labels that are numbered or inside a container named like a stepper; current = the selected step (uia_nodes now caches SelectionItem.IsSelected for TabItems too) (Tab bars and form column labels must not become steppers; selection is already in the cache request so it costs nothing extra; without it step state stays unknown rather than guessed)
- **W2-3** Build the equivalence check as a real side-by-side (fake UIA layer feeding both readers) or as hand-written expected-output tests like W2-1's? → Real side-by-side: one FakeElement tree implements both the live pattern API and the cached-property API, fed into the actual vsdc_uia_text._collect_descendant_lines and uia_nodes.lines_from_nodes (hand-written expectations (W2-1's test_sgt_i_uia_nodes.py) only prove lines_from_nodes matches what a human expects, not that it matches the Core's real function; a shared fixture tree makes any future edit to either reader show up as a real diff, which is what the 14.2 gate is for)
- **W2-4** Where does the extra node read for corpus v2 happen? → Synchronous, in SgtShadow._observe on the Core thread, gated on recorder.enabled and sgt_i.active; injectable via a new read_nodes= constructor param (default: core.sgt_i.uia_nodes.read_page_nodes), so tests and replay never touch real UIA (One JSONL record needs lines+nodes together; a separate async write from SGT-I's own thread would need to merge into an already-written line, which is much more complex for no gain since read_page_nodes already bounds itself to a few hundred ms (W2-1 bench) and has its own 3s timeout, same as the existing lines read)
- **W4-2** Which mod-11 check-digit scheme, and how to bound multiple testing? → ISBN-10-style mod-11 (weights n..1, no X digit); checksum p-values Bonferroni-corrected by the 4 schemes tried; containment/order/sum relations reuse shapes.py's rule-of-three bound, corrected by the number of candidate offsets/directions tried (blueprint names mod-11 as a known scheme but not an exact weighting; ISBN-10's is the most standard. Bonferroni keeps the multi-scheme test from manufacturing false discoveries, matching the WP's explicit ask for a multiple-testing bound)
- **W4-3** When a container's counts fit more than one row of the kind table (e.g. an identifier's values are also technically per-client 'changing'), which row wins? → Priority order template > identifier > profile > dataset > vocabulary; template/profile additionally require >=2 distinct clients or >=2 stable clients (a single client's stability is ambiguous between template and profile, so it stays unclassified until a second client is seen) (Blueprint's table lists rows narratively, not by precedence; identifier (never repeats, anywhere) is a stronger, more specific claim than dataset (repeats allowed, just changes per client), so it is checked first. Cheapest and most accurate to build without extra machinery.)
- **W5-1** How is mixed text (Welcome, <name>) split into template and client parts without AI, and what names the slot? → Word-level promotion: a word is template when seen at the same page/role/zone/position for >=3 different clients AND >=60% of the page's clients (config template_min_clients, template_share); runs of other words become «generic type», e.g. 'Welcome, «text»' (Whole-text identity can never learn mixed text; the share test stops a surname shared by 3 of 50 clients being promoted; the atlas cannot know meaning, so the placeholder is the step-2 generic type, not 'name')
- **W5-1** Where do the hashes the atlas counts with live, given the atlas may sync but hashes must never leave the PC? → Two files per portal: sgt_i/atlas/<portal>.json (public: structure, counts, shapes, dates) and sgt_i/atlas_private/<portal>.json (salted page tokens, word/client counters, url counts); page matching uses the private token hashes so it is stable across promotion (14.5 rules 3 and 7; losing the private file only means pages are re-learnt; the Core never reads either)

## Checks waiting for you

- #1 (W1-1) Settings -> Tracker shows the new 'SGT-I' Off/On row under SGT; flip it On, save, and confirm the app log is quiet and SGT rows are unchanged on a real portal session — Not run
- #2 (W2-1) Re-run tools/sgt_i_uia_node_bench.py on a real GST/ITR portal page in the office Edge session (only local mocks measured) and confirm node-read lines are IDENTICAL to read_page_text there. — Not run
- #3 (W2-3) tools/sgt_lines_equivalence.py --title "<substr>" live-window mode is untested against a real open portal window (needs an unlocked desktop with Edge open on a page) - only the no-window-found path was exercised here — Not run
- #4 (W2-4) Switch SGT-I on for a real portal session so the corpus actually gains v2 nodes records; then re-run tools/sgt_lines_equivalence.py (no --skip-corpus) to see it move off '0 comparable'. — Not run
