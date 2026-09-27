# SGT overhaul — report (2026-09-27 19:49)

Deadline: 2026-09-29T01:30:00+05:30

| Status | WPs |
| :--- | ---: |
| Not started | 21 |
| In progress | 0 |
| Retry | 0 |
| Done | 6 |
| Blocked | 0 |

Runs: 8   output tokens: 243471   API-equivalent cost: $12.78

| WP | Status | Model | Commit | What | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W0-1 | Done | sonnet | 623424f | Find why the SGT page corpus stopped recording after 22 Sep and fix it | Root cause: sgt_record_pages flipped off silently (spec_stats.json proved reads continued); made PageRecorder.enabled echo every transition; regression tests ad |
| W0-2 | Done | haiku | 0238725 | Baseline: SGT tests and replay baseline, recorded for later diffs | SGT 281 passed, VSDC 388+6 pre-existing, baseline 8 sessions/1 dataset |
| W1-1 | Done | opus | bea35a6 | SGT-I host: the C/I contract in code | core/sgt_i host (thread, budget, hang trip, advisory+enrichment channels), sgt_i_mode Off/On, Core byte-identical On vs Off; 297 SGT tests pass |
| W1-2 | Done | sonnet | e724987 | Enrichment fields on tracker rows (no schema change) | Enrichment channel already round-tripped via existing raw_payload_json plumbing (no schema change); added read-only SGT-I card in PayloadInspectorDialog; tests: |
| W1-R | Done | opus | d4d6da4 | Review: W0-1..W1-2 against the SGT-C safety rules | Review OK except 2 fixed defects: SGT-I detach race could drop a Core row; 4 payload scanners read raw_payload.sgt_i as identity/name evidence |
| W2-1 | Done | opus | 54ae8d7 | Measure a cached UIA node read vs the current line reader | Cached node read: heavy 927->530 ms, normal ~40->~32 ms, control-view lines identical; unpack ~205 ms is next cost |
| W2-2 | Not started | opus |  | Page map: node model, zones, sections, layout pairing |  |
| W2-3 | Not started | sonnet |  | Lines view from nodes + equivalence check against today's reader |  |
| W2-4 | Not started | sonnet |  | Recorder stores nodes (corpus format v2, backwards compatible) |  |
| W3-1 | Not started | sonnet |  | Container -> value pairs with generic types and masking |  |
| W4-1 | Not started | sonnet |  | Maths 1: shape grammar induction with rule-of-three bounds |  |
| W4-2 | Not started | sonnet |  | Maths 2: checksum discovery, values inside values, relations |  |
| W4-3 | Not started | sonnet |  | Maths 3: container kinds from salted-hash statistics |  |
| W5-1 | Not started | opus |  | The atlas: model, page matching, merge, template promotion, ageing |  |
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

## Checks waiting for you

- #1 (W1-1) Settings -> Tracker shows the new 'SGT-I' Off/On row under SGT; flip it On, save, and confirm the app log is quiet and SGT rows are unchanged on a real portal session — Not run
- #2 (W2-1) Re-run tools/sgt_i_uia_node_bench.py on a real GST/ITR portal page in the office Edge session (only local mocks measured) and confirm node-read lines are IDENTICAL to read_page_text there. — Not run
