# OS Assist — report (2026-11-08 22:28)

Deadline: 2026-11-15T23:00:00+05:30

| Status | WPs |
| :--- | ---: |
| Not started | 1 |
| In progress | 0 |
| Retry | 0 |
| Done | 16 |
| Blocked | 0 |

Runs: 16   output tokens: 293578   API-equivalent cost: $22.55

| WP | Status | Model | Commit | What | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| O0-1 | Done | haiku | a858454 | Baseline: tests before any change | baseline tests complete: 393 pass, 24 pre-existing failures |
| O1-1 | Done | sonnet | dda85fd | core/os_assist: UIA worker thread + locate (§3, §4.2) | worker + locate built, 12 fake-element tests pass |
| O1-2 | Done | sonnet | 2a165fc | core/os_assist/fill.py + one-writer test (§4.1, §4.3) | fill.py one writer + FocusWatcher + type_into_focused; 15 fill tests + one-writer test pass |
| O1-3 | Done | sonnet | 9d5b41e | core/os_assist/secure_clipboard.py (§4.5) | secure_clipboard.copy + clear_if_unchanged, 4 tests pass |
| O1-R | Done | opus | cec854b | Review phase 1 (core) | 4 fixes: host check before SetFocus+keys, IsPassword boxes judged by bullets only, #id selector lists, UIA only on worker + per-thread queue; no-click test; 24  |
| O2-1 | Done | opus | 4267738 | ui/os_assist/dock_card.py: the docked, no-focus base window (§4.4) | DockCard base + TimerBar + css_px/to_logical helpers; 14 offscreen tests pass; not real-browser tested |
| O2-2 | Done | opus | 2669d18 | SMTI card (normal + Flutter steps) (§4.4) | SmtiCard on DockCard: Username/Password fill via worker, copy fallbacks, Flutter step cards with FocusWatcher; dock_card.wire_sck_edit/set_sck_edit_handler for  |
| O2-3 | Done | opus | ae71e8c | MECP card (plain + SCC mode) (§4.5) | mecp_card.py plain + SCC mode; set_scc_hooks + thread-safe update_card/close_card for O3-4; 19 tests pass |
| O2-4 | Done | sonnet | eca8c9b | SCA card + fill chip (§4.4, §4.6) | ScaCard (Inject signal, amber needs_confirm, SCK edit) and ScaChip (2.4 s fade) built; 10 new tests pass |
| O2-R | Done | opus | 9b283ec | Review phase 2 (cards) + look-alike shots | Cards match extension CSS per card (MECP/SCA/chip geometry, fonts, shrink-to-fit), 7 shots in docs/os_assist/shots, queued SMTI jobs cancelled on dismiss; 120 o |
| O3-1 | Done | sonnet | 9b7efaa | Engine switch setting (§4.7, D1) | assist_engine()/set_assist_engine + listeners in automation.py; Assist engine combo on Action Buttons page; 5 new tests pass |
| O3-2 | Done | opus | 44deb02 | SMTI/MECP launch through the OS engine | launcher.py: open new tab, 20 s window_for_host wait off-worker, card on Qt thread, one card per kind; triggers route on assist_engine; 18 tests |
| O3-3 | Done | opus | 9c9fc36 | SCA engine in OS mode (§4.6) | sca_engine.py on worker+Qt: probe/match/grant/90s wait/fill/chip, card+Inject, assist lock; arms routed in OS mode; extension SCA forced off in OS mode; 29 test |
| O3-4 | Done | sonnet | 5f88cf0 | SCC card through the OS MECP card | SCC open/update/close route to the OS MECP card (docked to att.hwnd) in OS mode; extension unchanged; 9 new tests, all scc tests pass |
| O3-R | Done | opus | 756e0e0 | Review phase 3 (wiring) | Phase 3 reviewed: extension SCA password requests denied in OS mode, settings.ini edited one line only, queued SCA fill cancelled on disarm; 8 new tests |
| O4-1 | Done | sonnet | fa13fa2 | Full suite, leak and idle checks | no-leak (10) and idle tests added and passing; full suite 149 F all pre-existing/non-project, 2 cards_review geometry flakes under load |
| O4-R | Not started | opus |  | Final review + merge-readiness note |  |

## Decisions taken for you

- **O2-2** D3: what the pencil (edit password) button does in OS mode → Keep it; it opens the SCK widget as today (automation.show_sck_widget) (Owner decision 2026-11-08)
- **O1-R** Port sera_dom's revealed-password fallback (type=text box hinted password) and its focused-element fallback to locate.find_fields? → No: only IsPassword boxes count as password boxes; with none found SMTI copies (blueprint 4.2 step 4) (Verifying a revealed box means reading its plain value, which rule 4 forbids; the blueprint's 4.2 rules do not list either fallback)
- **O2-1** Browser window closed (IsWindow false) while a card is open: hide or dismiss? → dismiss('browser_closed'), which drops the card's secrets (the window cannot come back; rule 4 wants secrets cleared when the card goes)
- **O2-1** How are the extension's CSS px sizes and the 18/24 px offsets scaled? → css_px(v) = v / ui_scale.running_scale(); Qt applies monitor DPI, the app UI scale is undone; Win32/UIA physical rects mapped per screen by to_logical (the card must match the in-page card 1:1 whatever the app's own small-screen scale is)
- **O2-2** When does a non-TRACES page switch SMTI to Flutter step mode? → One worker probe 1.5 s after the card shows (retried up to 3 times while the page has no Document): 0 visible Edits -> step mode; never after a button was pressed (UIA cannot see flt-* DOM markers; a box count is the cheapest accurate signal and step mode still works if wrong)
- **O2-2** Flutter step mode: Tab after step 1, and what if typing fails? → No Tab (rule 1); a step that is not typed copies the value (password secure) with the button copy texts and moves on, as the extension's flutter_no_focus copy (Rule 1 forbids Tab; copy fallback matches today's behaviour and the WP's copy texts)
- **O2-3** How does the OS MECP card reach main.py's SCC handlers and the ledger? → mecp_card.set_scc_hooks(send=dispatch on msg type to _handle_scc_row_worked/_handle_scc_row_none/_handle_scc_card_closed, ledger=SccCard.note_clipboard); per-card scc_send/ledger override; module update_card/close_card with automation's signatures, thread-safe via a queued signal (Same message dicts as the bridge emits, no socket; O3-4 only has to plug them in)
- **O2-R** Card geometry where the three extension cards differ → Each OS card copies its own extension card: MECP right 18, content-box 355/395 px, x, no slide, no click reset; SCA top 20, 320 px border-box, radius 12, no hover pause/click reset; chip top 20, one line up to 340 px (Blueprint 4.4 says visuals 1:1 from the extension CSS; the cards had inherited the SMTI card's values)
- **O3-2** Where does the 20 s window wait run? → A plain launch thread polls every 0.5 s (first look after 1 s), each look is one worker.submit(window_for_host) job (Holding the UIA worker for 20 s would stall card probes and fills; the UIA calls still only run on the worker)
- **O3-2** Who calls clipboard_watch.suppress_client for OS launches? → Client Detail, unchanged (it already suppresses before every MECP/SMTI trigger in both engines); the launcher does not repeat it (Keeps today's behaviour with no duplicate path)
- **O3-3** When does the OS SCA engine act on a box holding the armed id? → When the match appears in that window (or on a new arm), not every tick; probe checks the focused non-password box first, then up to 8 visible non-password boxes (The extension acts on input events; a polled state would re-request and re-notify every 0.6 s after a denial or failed fill. Checking every visible id box costs little and is more accurate than first-box-only (search boxes on GST).)
- **O3-3** Where is the extension's SCA forced off in OS mode? → Inside automation.update_extension_settings and main._get_extension_settings_payload (sca_enabled AND engine != os); engine listener resyncs on switch (Covers every caller (Settings save, Services dialog, startup sync, extension connect, popup toggle) in one place, and switching back sends the user's own setting.)
- **O3-R** Extension SCA password requests while the engine is OS? → Desktop refuses them (clipboard_watch.handle_extension_password_request), whatever arm the extension holds (An arm sent before the switch, or ws_bridge replaying it to a new browser, could otherwise get a password to the extension in OS mode)
- **O3-R** How set_assist_engine writes ~/AmanAssociates_Sera/settings.ini → Edit only the assist_engine line in [Automation]; keep every other byte, BOM and encoding (The file sits next to master.db and is the MCL_Columns seed; a configparser rewrite lower-cased labels, dropped comments and failed on a BOM)

## Checks waiting for you

- #1 (O1-3) On Windows 10/11 with clipboard history on: copy a fictional password via secure_clipboard.copy(secret=True) and confirm it does not appear in Win+V. — Not run
- #2 (O2-2) SMTI OS card on TRACES and on a normal portal: look matches the in-page card (buttons, step cards, timer), Username/Password fill, copy fallback pastes, ✎ opens SCK for that client — Not run
- #3 (O2-3) MECP OS card next to the extension's in-page MECP card (plain and SCC mode): same look, copies paste correctly, combo copies show in the SCC ledger, This one worked / None / x reach the desktop — Not run
- #4 (O2-4) Look at the OS SCA card and fill chip next to the extension's in-page ones (colours, amber needs_confirm border, spacing, fade) in a real browser; chip must not steal focus. — Not run
- #5 (O2-R) Compare docs/os_assist/shots/*.png with the extension cards on a real page — Not run
- #6 (O3-2) OS engine: Client Detail SMTI and MECP buttons open a new portal tab and the card docks over that browser within a few seconds; a second press replaces the card; with no browser window found in 20 s an error dialog names the portal — Not run
- #7 (O3-3) OS mode SCA: copy a PAN in Excel, type it on GST and on Income Tax (PAN page, Continue, password page): password filled + chip; unverified IT client: amber card, Inject fills; with an SMTI card open on the window SCA stays quiet; extension draws no SCA card in OS mode and works again after switching back to Extension. — Not run
- #8 (O3-4) Real browser: with assist_engine=os, trigger an SCC attempt (SGT sees an Income Tax password page) and from Client Detail MECP on an unverified PAN; confirm the SCC card docks to that browser window, Copy / This one worked / None / x work, marks update, and the card closes on save. — Not run
- #9 (O3-R) Engine OS with the extension connected: copy a PAN, type it on GST - exactly one fill (Sera's chip), no extension SCA card or chip; then switch to Extension in Settings without a restart and repeat - the extension fills as before. — Not run
