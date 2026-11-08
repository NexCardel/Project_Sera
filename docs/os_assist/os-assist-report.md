# OS Assist — report (2026-11-08 20:39)

Deadline: 2026-11-15T23:00:00+05:30

| Status | WPs |
| :--- | ---: |
| Not started | 11 |
| In progress | 0 |
| Retry | 0 |
| Done | 6 |
| Blocked | 0 |

Runs: 7   output tokens: 84533   API-equivalent cost: $5.81

| WP | Status | Model | Commit | What | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| O0-1 | Done | haiku | a858454 | Baseline: tests before any change | baseline tests complete: 393 pass, 24 pre-existing failures |
| O1-1 | Done | sonnet | dda85fd | core/os_assist: UIA worker thread + locate (§3, §4.2) | worker + locate built, 12 fake-element tests pass |
| O1-2 | Done | sonnet | 2a165fc | core/os_assist/fill.py + one-writer test (§4.1, §4.3) | fill.py one writer + FocusWatcher + type_into_focused; 15 fill tests + one-writer test pass |
| O1-3 | Done | sonnet | 9d5b41e | core/os_assist/secure_clipboard.py (§4.5) | secure_clipboard.copy + clear_if_unchanged, 4 tests pass |
| O1-R | Done | opus | cec854b | Review phase 1 (core) | 4 fixes: host check before SetFocus+keys, IsPassword boxes judged by bullets only, #id selector lists, UIA only on worker + per-thread queue; no-click test; 24  |
| O2-1 | Done | opus | 4267738 | ui/os_assist/dock_card.py: the docked, no-focus base window (§4.4) | DockCard base + TimerBar + css_px/to_logical helpers; 14 offscreen tests pass; not real-browser tested |
| O2-2 | Not started | opus |  | SMTI card (normal + Flutter steps) (§4.4) |  |
| O2-3 | Not started | opus |  | MECP card (plain + SCC mode) (§4.5) |  |
| O2-4 | Not started | sonnet |  | SCA card + fill chip (§4.4, §4.6) |  |
| O2-R | Not started | opus |  | Review phase 2 (cards) + look-alike shots |  |
| O3-1 | Not started | sonnet |  | Engine switch setting (§4.7, D1) |  |
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

## Checks waiting for you

- #1 (O1-3) On Windows 10/11 with clipboard history on: copy a fictional password via secure_clipboard.copy(secret=True) and confirm it does not appear in Win+V. — Not run
