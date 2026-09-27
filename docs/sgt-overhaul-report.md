# SGT overhaul — report (2026-09-27 19:19)

Deadline: 2026-09-29T01:30:00+05:30

| Status | WPs |
| :--- | ---: |
| Not started | 25 |
| In progress | 0 |
| Retry | 0 |
| Done | 2 |
| Blocked | 0 |

Runs: 4   output tokens: 151628   API-equivalent cost: $6.45

| WP | Status | Model | Commit | What | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W0-1 | Done | sonnet | 623424f | Find why the SGT page corpus stopped recording after 22 Sep and fix it | Root cause: sgt_record_pages flipped off silently (spec_stats.json proved reads continued); made PageRecorder.enabled echo every transition; regression tests ad |
| W0-2 | Done | haiku | 0238725 | Baseline: SGT tests and replay baseline, recorded for later diffs | SGT 281 passed, VSDC 388+6 pre-existing, baseline 8 sessions/1 dataset |
| W1-1 | Not started | opus |  | SGT-I host: the C/I contract in code |  |
| W1-2 | Not started | sonnet |  | Enrichment fields on tracker rows (no schema change) |  |
| W1-R | Not started | opus |  | Review: W0-1..W1-2 against the SGT-C safety rules |  |
| W2-1 | Not started | opus |  | Measure a cached UIA node read vs the current line reader |  |
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
