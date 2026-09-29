# Autofill tweaks — report (2026-09-29 13:05)

Deadline: 2026-09-29T23:00:00+05:30

| Status | WPs |
| :--- | ---: |
| Not started | 17 |
| In progress | 0 |
| Retry | 0 |
| Done | 8 |
| Blocked | 0 |

Runs: 11   output tokens: 216894   API-equivalent cost: $15.60

| WP | Status | Model | Commit | What | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| W0-1 | Done | haiku | ec6e0dd | Baseline: tests before any change | Part H confirmed; test files verified; sgt_replay blocked by permissions |
| W0-2 | Done | sonnet | b8f7fa5 | Password guard in both UIA readers (finding #17) | Password guard added to vsdc_uia_text and uia_nodes; both UIA readers now skip ValuePattern reads for IsPassword elements; 982 tests pass, 6 pre-existing unrela |
| W1-1 | Done | sonnet | e3027bb | Part A: remove all tracking and the cookie wipe from the extension | Removed tracking/cookie-wipe from both extension builds; sca/ unchanged, mirrored firefox to chrome |
| W1-2 | Done | sonnet | 59e4f02 | Part A, desktop side: stop serving extension tracking | Desktop side stops serving extension tracking; SGT/VSDC capture pipeline confirmed unchanged |
| W1-R | Done | opus | 3fda16f | Review Part A | Part A passes review; fixed dead settings_dialog tracker kwargs, manifest description, stale comment; passwords in storage.local left for W2-1/Part G |
| W2-1 | Done | sonnet | 8cc2b00 | Part B1: passwords off disk in the extension (#2) | Passwords moved from storage.local to storage.session (memory fallback for old Firefox) in both extension builds; startup wipe of stale local copies; new scan t |
| W2-2 | Done | sonnet | 61ddae6 | Part B2: one openPortalTab helper (#3, #4) | openPortalTab helper in both builds; reuse only login-page tabs (D5); listener before navigate, inject once, no reload, 30 s cleanup; JS test added |
| W3-1 | Done | haiku | 7a54db4 | Part C: Fast Autofill on the helper; decision D8 | Removed auto-click; D8 user decision. Tests pass. |
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

## Decisions taken for you

- **W1-2** Which ws_bridge.py/main.py handlers and Settings toggles are extension-tracking-only (remove) vs desktop capture (keep)? → Removed: uncertain_result/session_start/sdc_session_timeline/sudr_capture dispatch+signals+handlers (_handle_session_started, _handle_sdc_timeline, _handle_sudr_capture); filing_result/audit_event dispatch branch (dead since W1-1 removed extension's sdc/); audit_event branch in _process_extension_result; tracker_enabled/sdc_enabled/fst_enabled/vsdc_enabled fields from extension_settings_updated handler, request_settings reply payload, update_extension_settings()/_send_to_extension() payloads, Settings general-page FST toggle (fst_check, 'Sera SDC - DOM Crosshair (FST)'), client_detail_window.py's _fst_enabled/_tracker_enabled per-call flags, and main.py's startup default-init of sdc_enabled/fst_enabled/tracker_enabled. Kept: vsdc_enabled/vsdc_x_enabled/vsdc247_enabled/vsdc_hud_enabled/sgt_mode/sgt_record_pages/sgt_i_mode (unified_settings_dialog.py VSDC page, core/vsdc/vsdc_engines.py reads them), the vsdc_enabled pause/resume wiring is gone only because its sole feed (extension popup toggle) no longer exists - VSDC is still controlled from the desktop Settings dialog; scc_password_verified dispatch branch (Part G still needs it); filing_result_received Signal itself + _handle_extension_result/_process_extension_result/_capture_queue pipeline (fed live by vsdc_worker.filing_captured for SGT/VSDC captures, confirmed via main.py's vsdc_worker.filing_captured.connect). (Verified extension no longer sends these message types/fields after W1-1 (grep of sera_extension/ found zero matches for audit_event, sdc/fst/tracker/vsdc settings fields); SGT/VSDC captures reach main.py via a separate signal (vsdc_worker.filing_captured), not via the websocket, so that pipeline had to stay.)
- **W1-2** sera_extension's fst_check/tracker settings analog in the dead legacy ui/dialogs/settings_dialog.py (unreachable, never instantiated by admin_window.py - confirmed by grep) - update it too or leave as-is? → Left as-is; not touched. (Confirmed unreachable dead code (only unified_settings_dialog.py is instantiated live); editing it would be scope creep with zero behavioral effect and extra risk of typos in code nothing runs.)
- **W2-2** When Sera opens a portal login, should it reuse an already open tab of that portal? → Reuse only a tab showing the login page (asked as Q1)
- **W3-1** Fast Autofill clicks the portal's Continue/Login button after filling. Keep that? → Stop after filling (asked as Q2)
- **W3-1** D8: Fast Autofill auto-click removed per user decision 'Stop after filling' → Stop after filling (User chose to stop after filling instead of auto-clicking the Continue/Login button)

## Checks waiting for you

- #1 (W2-2) Real browser (Chrome and Firefox): with a portal login tab already open, Fast Autofill/SMTI/MECP reuse it, inject once (MECP card stays up, no flash); with only a logged-in page of that portal open, a NEW tab opens and the logged-in tab is untouched. — Not run
