# OS Assist — report (2026-11-08 21:04)

Deadline: 2026-11-15T23:00:00+05:30

| Status | WPs |
| :--- | ---: |
| Not started | 6 |
| In progress | 0 |
| Retry | 0 |
| Done | 11 |
| Blocked | 0 |

Runs: 12   output tokens: 212090   API-equivalent cost: $15.10

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
| O3-2 | Not started | opus |  | SMTI/MECP launch through the OS engine |  |
| O3-3 | Not started | opus |  | SCA engine in OS mode (§4.6) |  |
| O3-4 | Not started | sonnet |  | SCC card through the OS MECP card |  |
| O3-R | Not started | opus |  | Review phase 3 (wiring) |  |
| O4-1 | Not started | sonnet |  | Full suite, leak and idle checks |  |
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

## Checks waiting for you

- #1 (O1-3) On Windows 10/11 with clipboard history on: copy a fictional password via secure_clipboard.copy(secret=True) and confirm it does not appear in Win+V. — Not run
- #2 (O2-2) SMTI OS card on TRACES and on a normal portal: look matches the in-page card (buttons, step cards, timer), Username/Password fill, copy fallback pastes, ✎ opens SCK for that client — Not run
- #3 (O2-3) MECP OS card next to the extension's in-page MECP card (plain and SCC mode): same look, copies paste correctly, combo copies show in the SCC ledger, This one worked / None / x reach the desktop — Not run
- #4 (O2-4) Look at the OS SCA card and fill chip next to the extension's in-page ones (colours, amber needs_confirm border, spacing, fade) in a real browser; chip must not steal focus. — Not run
- #5 (O2-R) Compare docs/os_assist/shots/*.png with the extension cards on a real page — Not run
