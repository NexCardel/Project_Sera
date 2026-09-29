# Autofill tweaks — report (2026-09-29 13:49)

Deadline: 2026-09-29T23:00:00+05:30

| Status | WPs |
| :--- | ---: |
| Not started | 9 |
| In progress | 0 |
| Retry | 0 |
| Done | 16 |
| Blocked | 0 |

Runs: 19   output tokens: 435918   API-equivalent cost: $31.27

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
| W3-2 | Done | sonnet | 02c0b88 | Part D: SMTI field picking, visibility, re-inject rule (#7, #8, #9) | Part D: shared sera_dom.js visibility/field rules, Username never types into password box, re-inject only in SMTI's own tab with a visible login form, SCA disar |
| W3-3 | Done | sonnet | 4719072 | Part E: MECP card (#5, #16) and clipboard clearing (B3) | MECP card: closed shadow, timer bar, closes after both copied (D6), MECP_CLOSED clears payload; clipboard clear via sera_dom.js + desktop clipboard_clear_second |
| W4-1 | Done | opus | 1592504 | Part B4: per-tab assist lock; MECP/SMTI launches don't arm SCA (#13, #15) | Per-tab assistTabs lock replaces manualAssistActive (both builds, sca/ identical); clipboard_watch.suppress_client(300s) called by Client Detail MECP/SMTI launc |
| W4-2 | Done | sonnet | 3b061fc | Part F: SCA use counted on fill, denials shown, no 'pass' fallback (#12, #14) | SCA use counted on fill; grants capped max_uses+2; denials reported as failed fill; no pass-label fallback. JS 20/20; test_clipboard_assist setUp still fails (p |
| W4-3 | Done | sonnet | c169294 | Part F: SCA scope (decision D7) | SCA login.js/sca_adapters.js registered only on approved portal domains via scripting API (D7: portals only); both builds; main.py sends allowed_domains; test_s |
| W4-R | Done | opus | 2fe7cba | Review Parts B-F | 6 defects fixed: Fast Autofill off-portal ReferenceError, password-bearing message logs, URL-substring tab match, stale SMTI tab, Firefox SMTI parity, SCA scope |
| W5-1 | Done | opus | 4b893bf | Part G frame: core/scc host, _hand_to_scc hook, setting | core/scc SccHost + _hand_to_scc hook + 30 s read window + scc_detect_mode setting (default off); SGT tests pass, replay no changed rows |
| W5-2 | Done | sonnet | b54fbe7 | Part G steps 1-2, scc_rules.json and its loader | scc_rules.json + loader (self-tests, refuses failing rules), AttemptOpener steps 1-2 (D3 Yes); wording unconfirmed except header; not wired to router yet; 45 ne |
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
- **W3-3** When should the MECP card close by itself? → After both User ID and password are copied (asked as Q3)
- **W3-3** SCC card behaviour in the MECP card change → Timeout and both-copied send MECP_CLOSED (clears mecpPayload only); only the x sends MECP_DISMISSED (also ends an SCC attempt); SCC card gets no auto-close and no clipboard clearing (Part G owns SCC; a timeout must not stop SCC verifying)
- **W4-1** Where does the per-tab assist lock live and when does it end? → In _passwordStore (storage.session / memory) as tabId->{kind,expiresAt}, with an in-memory mirror; locked on every SMTI/MECP inject (incl. re-inject), cleared on MANUAL_ASSIST_CLEAR / MECP_DISMISSED / MECP_CLOSED for the sender's tab and kind, on tab close, and after 5 min (MV3 service worker can die while a card is open; session storage survives that without touching disk. 5 min matches the payload lifetime as a safety cap.)
- **W4-1** How does Client Detail reach clipboard_watch to suppress a client? → Module-level clipboard_watch.suppress_client(client_id, seconds=300) / is_suppressed(); the service checks it in _on_clipboard_changed (ClientDetailWindow has no reference to the running ClipboardWatchService; module state needs no new wiring through main.py.)
- **W4-1** Firefox SMTI widget sent nothing on dismiss; how to clear its lock? → Its dismiss now sends MANUAL_ASSIST_CLEAR like Chrome; Firefox background clears manualAssistPayload and the tab's smti lock (Parity with Chrome; without it the Firefox lock would only end at the 5 min cap.)
- **W4-3** SCA watches paste and typing to spot a client id. Where should it run? → Only on approved portals (asked as Q4)
- **W4-R** Firefox SMTI widget differed from Chrome (every frame, no Angular re-sync, no re-inject, no settings pull) → Copy Chrome's manualAssistWidget verbatim into Firefox and port re-inject + request_settings sync (Rule 0.4 builds identical; Chrome's widget uses no Chrome-only API; smallest way to make behaviour identical)
- **W5-1** SCC-U host: handler interface and how a copy opens the read window → handlers implement observe(obs, hwnd, ctx); SccHost.open_read_window(session_id, seconds<=30) callable from any thread (the card's copy); ctx.read_harder for handlers; page budget 1 s, hang 15 s, queue 8; trip reason logs exception type only (mirrors SGT-I host; hwnd is needed for the card and outcome matching; exception text could carry page content so it is not logged)
- **W5-2** Show the client's saved (unverified) IT password as a row on the SCC card, so it can be verified too? → Yes (asked as Q5)

## Checks waiting for you

- #1 (W2-2) Real browser (Chrome and Firefox): with a portal login tab already open, Fast Autofill/SMTI/MECP reuse it, inject once (MECP card stays up, no flash); with only a logged-in page of that portal open, a NEW tab opens and the logged-in tab is untouched. — Not run
- #2 (W3-2) SMTI on the ITR password step: Username puts nothing into the password box — Not run
- #3 (W3-3) MECP on an already open GST login tab stays on screen (card does not flash away), and after copying User ID and password it closes by itself — Not run
- #4 (W3-3) In real Chrome and Firefox: copy a password from the MECP card and from SMTI, wait the clipboard-clear seconds, and confirm the clipboard is emptied (and that no clipboard-read permission prompt appears on the portal; the manifests have no clipboardRead permission) — Not run
- #5 (W4-1) Real browser (Chrome and Firefox), SCA armed: open SMTI or MECP for client A in tab 1 and type A's id there - no SCA fill; in tab 2 on the same portal SCA still fills; close the card (x or timeout) and SCA works again in tab 1. Also: Client Detail > Manual Copy, copy the User ID from the card - desktop log shows 'not arming' for 5 min, another client's id still arms. — Not run
- #6 (W4-3) Load both extension builds in a real Chrome and Firefox: confirm login.js/SCA runs on gst.gov.in / incometax.gov.in and a configured custom-service host, and does NOT run on an unrelated https site; confirm Autofill/SMTI/MECP still work on a custom service. — Not run
- #7 (W4-R) Firefox: SMTI now uses Chrome's widget (top frame only) and re-appears after a failed login in its own tab; check both on a real Firefox with the extension loaded. Chrome: Fast Autofill fills a custom service whose host is NOT in the SCA portal list. — Not run
- #8 (W5-2) Run tools/vsdc_uia_probe.py once on ONE real Income Tax wrong-password attempt (a single mistyped password, never repeated, NEVER to a lock-out) on the password page, and once on the OTP / secure-access page if you meet it. Then compare the wording with core/scc/scc_rules.json: rules marked unconfirmed (wrong password, locked, OTP/secure access, forgot/reset) should be replaced with the real text and lose the flag. — Not run
