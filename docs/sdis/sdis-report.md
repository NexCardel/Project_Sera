# Sera Distill — report (2026-10-04 14:02)

Deadline: 2026-10-06T01:30:00+05:30

| Status | WPs |
| :--- | ---: |
| Not started | 28 |
| In progress | 0 |
| Retry | 0 |
| Done | 2 |
| Blocked | 0 |

Runs: 2   output tokens: 44009   API-equivalent cost: $3.60

| WP | Status | Model | Commit | What | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W0-1 | Done | sonnet | 36e67ef | Baseline: tests + the regression runner tools/sdis_regress.py | tools/sdis_regress.py + test + baseline; 16 tests pass; numbers as expected (double count 0, lost 0, orders agree yes) |
| W0-2 | Done | sonnet | c8f3bd6 | Engine package core/sdis/ (pre-dev files become module aliases) | core/sdis package built; pre-dev keys/align/link_map/memory/tables are aliases; regression printout identical to baseline; 70 SDIS tests pass |
| W1-1 | Not started | gemini_first |  | Part B: value history with times |  |
| W1-2 | Not started | gemini_first |  | Part G: screens (one link, several pages) by weighted matching |  |
| W1-3 | Not started | gemini_first |  | Part D: identity by session id + data fingerprint |  |
| W1-4 | Not started | gemini_first |  | Part N: smart page link resolution |  |
| W1-5 | Not started | gemini_first |  | Part M: per-browser memory |  |
| W1-6 | Not started | opus |  | Part R.1: Firefox address bar, title, and never reading background tabs (SGT-C) |  |
| W1-7 | Not started | opus |  | Part R.2: browser parity tool + Firefox fixtures |  |
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
| W4-3 | Not started | opus |  | Part O: mining process capped at 10% CPU |  |
| W4-4 | Not started | opus |  | Part Q: registration on every PC (synced tables) |  |
| W4-5 | Not started | sonnet |  | Part L: the Distill dialog + loading dialog + containers |  |
| W4-6 | Not started | opus |  | Parts U + S.3: sdis_mcl, the containers file (sync + checks) |  |
| W4-7 | Not started | opus |  | Part S.2 in SGT: container instances, completion, Others values |  |
| W5-R | Not started | opus |  | Final review and merge-readiness note |  |
