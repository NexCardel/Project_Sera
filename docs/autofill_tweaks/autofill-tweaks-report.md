# Autofill tweaks — report (2026-09-29 00:30)

Deadline: 2026-09-29T01:30:00+05:30

| Status | WPs |
| :--- | ---: |
| Not started | 22 |
| In progress | 0 |
| Retry | 1 |
| Done | 2 |
| Blocked | 0 |

Runs: 6   output tokens: 73700   API-equivalent cost: $3.51

| WP | Status | Model | Commit | What | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W0-1 | Done | haiku | ec6e0dd | Baseline: tests before any change | Part H confirmed; test files verified; sgt_replay blocked by permissions |
| W0-2 | Done | sonnet | b8f7fa5 | Password guard in both UIA readers (finding #17) | Password guard added to vsdc_uia_text and uia_nodes; both UIA readers now skip ValuePattern reads for IsPassword elements; 982 tests pass, 6 pre-existing unrela |
| W1-1 | Retry | sonnet |  | Part A: remove all tracking and the cookie wipe from the extension | stopped by usage limit, resumes after 01:00 |
| W1-2 | Not started | sonnet |  | Part A, desktop side: stop serving extension tracking |  |
| W1-R | Not started | opus |  | Review Part A |  |
| W2-1 | Not started | sonnet |  | Part B1: passwords off disk in the extension (#2) |  |
| W2-2 | Not started | sonnet |  | Part B2: one openPortalTab helper (#3, #4) |  |
| W3-1 | Not started | haiku |  | Part C: Fast Autofill on the helper; decision D8 |  |
| W3-2 | Not started | sonnet |  | Part D: SMTI field picking, visibility, re-inject rule (#7, #8, #9) |  |
| W3-3 | Not started | sonnet |  | Part E: MECP card (#5, #16) and clipboard clearing (B3) |  |
| W4-1 | Not started | opus |  | Part B4: per-tab assist lock; MECP/SMTI launches don't arm SCA (#13, #15) |  |
| W4-2 | Not started | sonnet |  | Part F: SCA use counted on fill, denials shown, no 'pass' fallback (#12, #14) |  |
| W4-3 | Not started | sonnet |  | Part F: SCA scope (decision D7) |  |
| W4-R | Not started | opus |  | Review Parts B-F |  |
| W5-1 | Not started | opus |  | Part G frame: core/scc host, _hand_to_scc hook, setting |  |
| W5-2 | Not started | sonnet |  | Part G steps 1-2, scc_rules.json and its loader |  |
| W5-3 | Not started | sonnet |  | Part G step 3: the SCC card and its copies |  |
| W5-4 | Not started | opus |  | Part G step 4: the outcome reader |  |
| W5-5 | Not started | opus |  | Part G step 5: which password worked |  |
| W5-6 | Not started | sonnet |  | Part G step 6: the guarded save |  |
| W5-7 | Not started | haiku |  | Part G step 7: close-out and counts |  |
| W5-8 | Not started | sonnet |  | Part G: Client Detail opens the SCC card |  |
| W5-9 | Not started | sonnet |  | Part G: remove SCC from the extension and the old desktop handler |  |
| W6-1 | Not started | haiku |  | Docs and extension version |  |
| W6-R | Not started | opus |  | Final review |  |
