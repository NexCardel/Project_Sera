# OS Assist — SMTI, MECP and SCA without the extension

Status: **planned, ready for workers** (2026-11-08). WP0 spike done and accepted by the owner.
Work packages: `os-assist-plan.json`. Worker prompt: `os-assist-runner.md`. How to run: `README.md`.

Workers read: §0, §1, §2, the §4 parts your WP names, §6, and the §9 notes of the WPs you depend on.

## 0. Rules that are never broken

1. **Fill only what staff asked for.** OS Assist sets the text of a box when staff press a card
   button (SMTI), or when staff typed the armed client's id on that portal (SCA). It never clicks,
   presses Enter/Tab, submits, scrolls or dismisses anything on a page.
2. **One writer.** Only `core/os_assist/fill.py` may call `SetValue`, `SetFocus` or `SendInput`
   (plus the spike in `tools/pre_dev/os_assist/`). Every other UIA module stays read-only. A test
   enforces it (WP O1-2).
3. **Host check right before every write.** The address-bar host is read again just before each
   write and must still match the service (`sca_protocol.host_matches`). A change aborts.
4. **Passwords:** in memory only, for the card's life; cleared on ✕, timeout or fill. Never logged,
   printed, stored, put in an arm, or sent to the extension in OS mode. A password box's value is
   never read; it is judged by its bullet count only (D2). Test data is fictional.
5. **The extension is not touched** except to turn its SCA off in OS mode (§4.7). Fast Autofill
   stays on the extension. No tracking is added to the extension, ever.
6. **SGT / VSDC / SDIS capture must not change.** Don't edit `core/sgt*/`, `core/vsdc/`,
   `core/sdis/` beyond importing their helpers.
7. **The Extension engine keeps working exactly as today.** Every change is behind the engine
   switch (D1). With the switch on Extension, behaviour is unchanged and the existing tests pass.
8. No AI, no network services. Never bump `version.json`. Match the surrounding style.

## 1. Goal (owner's words, settled 2026-11-08)

Convert the SMTI, MECP and SCA widgets to OS level. They keep the same look and the same UI/UX;
only *where they live* and *how a box gets its text* change.

- The widget becomes a small Sera desktop window docked over the browser, where the in-page card
  sits today (top-right of the page). The extension draws nothing for these three in OS mode.
- A box is filled by **UI Automation `ValuePattern.SetValue`** (method 1). If the read-back check
  shows it did not take, Sera falls back to **`SendInput` Unicode keystrokes** (method 2). No
  clipboard for filling.

| # | Question | Owner's answer |
|---|---|---|
| 1 | Which fields | SMTI: user ID **and** password. SCA: password only (as today). |
| 2 | MECP | Behaviour unchanged: it **copies**, it does not fill. Only the card moves to the desktop. |
| 3 | Opening the portal | A new tab every time is OK. Changed 2026-11-13: a browser window already showing the portal's login page (the login link, or a visible password box) is reused; any other case still opens a new tab. |
| 4 | How SCA sees the login page | UIA reads: address bar + a password box present. No extension tracking. |
| 5 | Fast Autofill | Stays on the extension. Not part of this project. |
| 6 | Legal / ToS | The owner handles it. Not tracked here; workers don't raise it. |

Out of scope: Fast Autofill; porting the SCK card itself (the ✎ button keeps calling it, D3); anything in rule 1's "never" list.

## 2. What exists today (read before building)

| Piece | Where | Notes |
|---|---|---|
| SMTI widget | `sera_extension/background.js` `manualAssistWidget` (~L807–1556) | Green card, "⚡ Sera Assist" badge, ✎ + ✕, client title, **👤 Username** / **🔑 Password** buttons, timer bar (pause on hover, reset on click). Username filled → "✓  Username Injected" for 2 s. Password filled → card closes. No box → copies ("📋  Copied Username (Ctrl+V)"). **Flutter mode** (TRACES, `isFlutterPage`): buttons hidden, two step cards "👉 Step 1: Click the Username box" / "Step 2: Click the Password box"; Sera types into whichever box staff click. The CSS in that function is the visual spec. |
| MECP widget | `background.js` `mecpWidget` (~L2735–3150), `handleMECPTab`, `injectMECP` (90 s timer) | Dark card, rows "User ID (PAN)" / "Password" each with **📋 Copy**. **SCC mode** (`scc_mode`): combo rows, "This one worked", "None - I typed my own", failed rows, note line — fed by `core/scc/card.py`. |
| SCA card + chip | `sera_extension/sca/sca_coordinator.js` `showWidgetInPage`, `showFillChipInPage` | Card with **Inject** button for widget/assist mode and `needs_confirm` (amber, "This password has not been verified by SCC yet…"). Chip "Filled <portal> for <client>" ~2.4 s after every fill. Decision logic: `onCandidate`, `pickService`, `candidateMatches`, `isArmLive`. |
| SCA page probe | `sera_extension/content_scripts/login.js` | Sends any id-shaped text typed/pasted into a non-password box. |
| SCA desktop side | `clipboard_watch.py` (`_arm_client_services`, `handle_password_request`, `handle_fill_result`, `sca_notice`, `suppress_client`), `sca_protocol.py` (`host_matches`, `normalize_uid`, `looks_like_uid`, `build_arm_request`), `main.py` `_handle_sca_password_request` | All grant checks already live on the desktop. |
| Launch | `automation.py` `trigger_manual_assist`, `trigger_mecp`, `open_in_default_browser`, `service_action_mode`; SCC: `send_scc_card`, `update_scc_card`, `close_scc_card`; SCK: `show_sck_widget`; `main.py` `SccCard(open_card=…)` (~L1356), `_handle_sck_edit_requested` (~L1679) | |
| UIA reading | `core/vsdc/vsdc_uia_text.py` (`address_bar_here`, `onscreen_only`), `core/sca/page_nodes.py` (`UiaReader`: own UIA object per thread), `core/sca/uid_source_watch.py` (`_foreground_browser`), `core/sgt_i/uia_nodes.py` | All read-only. |
| Field rules | `sera_extension/content_scripts/sera_dom.js` `isVisible`, `findField` | Ported in §4.2. |
| Selectors | `services.username_selector` / `password_selector` (CSS) | Only `#id` maps to UIA (`AutomationId`). |
| **Spike** | `tools/pre_dev/os_assist/fill_spike.py`, `test_login.html` | Working reference code for find → SetValue → verify → keys. Port it, don't import it. |

## 3. Shape

```
Client Detail button ──► automation.trigger_manual_assist / trigger_mecp
                              │  engine = OS                 engine = Extension: unchanged
                              ▼
                   core/os_assist/launcher.py   open_in_default_browser(url), wait for the window
                              ▼
                   core/os_assist/locate.py     browser window + Document + boxes for the host
                              ▼
                   ui/os_assist/*_card.py       the same card, as a docked Qt window
                              │ button click
                              ▼
                   core/os_assist/fill.py       SetValue → verify → SendInput fallback
```

SCA: `clipboard_watch` arms as today. In OS mode the arm goes to `core/os_assist/sca_engine.py`
instead of the browsers. The engine watches the browser in front and, when the armed id is in a box
on an armed portal, calls `handle_password_request` **in-process** and fills the password box.

UIA work runs on **one worker thread** (`core/os_assist/worker.py`) that owns its own UIA object
(like `UiaReader`), never `vsdc_uia_text`'s shared worker, so SGT can't stall on it. Qt cards talk
to it by queued calls and get results back by Qt signals.

## 4. Design

### 4.1 One writer — see rule 2.

`fill.py` never logs a value: only `user|pass  filled|typed|copied|failed  <reason>`.

### 4.2 Finding the box (port of `sera_dom.findField`)

Searched inside the on-screen page **Document** elements of the matched browser window only
(never the browser's own UI; skip `NativeViewHost` panes):

1. A configured selector of the form `#id` (after `cleanSelector`) → the Edit with that
   `AutomationId`. Any other selector shape → ignored, use 2.
2. **Password**: the first visible Edit with `IsPassword = true`.
   **Username**: the visible non-password Edit closest *before* the password box in tree order; with
   no password box yet (Income Tax PAN page), the first visible non-password Edit.
3. Visible = not `IsOffscreen`, `IsEnabled`, bounding rect width and height > 0, `AriaProperties`
   without `hidden=true`, and `AutomationId`/name not `hiddenPassword`.
4. Nothing found → SMTI copies instead (today's behaviour); SCA keeps waiting (§4.6).

### 4.3 Fill and verify (proven by the spike)

```
fill(hwnd, element, text, kind, service):
  1. host check (rule 3)
  2. ValuePattern: missing or read-only → step 5
  3. SetValue(text); verify at 0.15 s and again at 1.0 s (Angular resets)
       user box → Value == text
       pass box → D2: Value is bullets, count == len(text)
  4. verified → "filled"
  5. fallback: host check; bring the browser to the front if needed; element.SetFocus();
     GetFocusedElement() must be that element; Ctrl+A, Delete; SendInput KEYEVENTF_UNICODE per
     character (layout-independent); stop at once if the foreground window changes; verify again
  6. still not verified → "failed" (SMTI: copy as today; SCA: fill result failed → sca_notice)
```

- Spike result (Chrome, offline page): SetValue fired the page's `input`/`change` handlers, so a
  framework model saw both values; bullet count matched; the hidden decoys were skipped; nothing
  was submitted. The owner then ran it on real pages: works.
- **Flutter (TRACES):** keep the step-card mode. The worker watches the **focused element**
  (200 ms `GetFocusedElement` poll while the card is open; not the browser's address bar). When
  staff click into an Edit in that window, it types step 1 (user ID), then, on the next different
  Edit, step 2 (password). Keystrokes directly (the box already has focus). Same step-card updates.

### 4.4 The cards (same look)

- **Base window** `ui/os_assist/dock_card.py`: frameless, translucent background, tool window (no
  taskbar entry), **never takes focus** (`Qt.WindowDoesNotAcceptFocus`, `WA_ShowWithoutActivating`,
  `WS_EX_NOACTIVATE`), so a button click leaves the browser focused.
- **Owned by the browser window** (`SetWindowLongPtrW(GWLP_HWNDPARENT, browser_hwnd)`): above that
  browser only, not over other apps; minimises with it.
- **Docked** top-right of the browser's page area (the on-screen Document's bounding rect), at the
  same 18–24 px offsets as the in-page card. Follows moves and resizes (100 ms `GetWindowRect`
  check while visible); hidden while minimised.
- **Tab change:** if the address-bar host stops matching the card's service, the card hides; it
  comes back when the host matches again. The timer keeps running.
- Visuals copied 1:1 from the extension CSS (colours, radii, Segoe UI, emoji labels, slide-in from
  the right, timer bar with pause on hover / reset on click). Per-monitor DPI aware (use the app's
  existing scale helpers; see `tests/test_ui_scale.py`).
- Cards: `smti_card.py` (normal + Flutter steps), `mecp_card.py` (plain + SCC mode),
  `sca_card.py` (Inject + needs_confirm note), `sca_chip.py`.
- **✎ button** — kept; opens the SCK widget as today (D3).

### 4.5 MECP specifics

- Copy stays a copy, through `core/os_assist/secure_clipboard.py`: the text plus the Windows formats
  `ExcludeClipboardContentFromMonitorProcessing`, `CanIncludeInClipboardHistory` = DWORD 0 and
  `CanUploadToCloudClipboard` = DWORD 0, so passwords stay out of Win+V history and cloud sync.
  Cleared after the *clipboard clear* seconds setting, only if the clipboard still holds it.
- SCC mode: `core/scc/card.py` already takes `open_card` / `close_card` / `update_card`. The OS card
  is another implementation of them (`main.py` ~L1356 picks by engine). The SCC attempt knows its
  browser `hwnd` (`att.hwnd`): dock to it. Copies call `SccCard.note_clipboard` directly as well as
  going through the clipboard, so the ledger works whatever the clipboard monitor sees.
- `clipboard_watch.suppress_client` stays, so a MECP copy doesn't arm SCA.

### 4.6 SCA engine (`core/os_assist/sca_engine.py`)

Runs on the worker only while an arm is live (no arm → no UIA at all). Each tick (~0.6 s):

1. Foreground window is a browser, else sleep.
2. Address-bar host matches one of the armed services and is an approved domain, else sleep.
3. Read the page's Edits (§4.2). **Login-page probe:** the value of the focused or first visible
   non-password Edit, through `normalize_uid`, compared with the arm (`candidateMatches` ported to
   Python). A password box's value is never read.
4. Match → the `onCandidate` branch, ported:
   - service has no password → `handle_fill_result(failed, reason)` once → `sca_notice`.
   - `needs_confirm`, or mode widget/assist → show `sca_card`; **Inject** continues below with
     `confirmed: true`.
   - else → `handle_password_request` called directly with the same dict shape the extension sends
     (`request_id`, `arm_id`, `service_id`, `page_host`, `matched_uid`, `confirmed`).
   Granted → wait for a visible password box in the same window, up to 90 s (Income Tax: PAN page,
   Continue, password page), then fill (§4.3).
5. `handle_fill_result` + chip "Filled <portal> for <client>".
6. One fill per arm / window / service (the `busy` key, hwnd in place of tab id).
7. **Assist lock:** while an OS SMTI/MECP card is open on a window, SCA stays quiet on that window.

The UID-node / UID-source work (`core/sca/page_nodes.py`, `core/sca/uid_source_watch.py`,
`docs/sca-uid-node-plan.md`) is reused, not duplicated. It is on `main` since 62908de (merge of
`sca-uid-node`, 2026-11-08); O0-1 still checks it is on this branch.

### 4.7 Engine switch and the extension

- `settings.ini` `[Automation] assist_engine = extension | os`, shown in Settings → Automation as
  **Assist engine: Extension / OS** (D1). One value for SMTI, MECP and SCA. Switching takes effect
  without a restart.
- OS mode: `automation.trigger_manual_assist` / `trigger_mecp` / `send_scc_card` go to the OS path;
  arms go to the SCA engine, not to the browsers; the desktop sends
  `update_extension_settings(sca_enabled=False)` so the extension's SCA can't fill as well.
- Fast Autofill and SCK keep using the extension in both modes.

## 5. Work packages

In `os-assist-plan.json` (deps, model, full instructions). Summary:

| Phase | WPs |
|---|---|
| 0 | O0-1 baseline tests + check the UID-node work is on the branch |
| 1 core | O1-1 worker + locate · O1-2 fill + one-writer test · O1-3 secure clipboard · O1-R review |
| 2 cards | O2-1 dock base window · O2-2 SMTI card · O2-3 MECP card · O2-4 SCA card + chip · O2-R review |
| 3 wiring | O3-1 engine setting · O3-2 SMTI/MECP launch · O3-3 SCA engine · O3-4 SCC via OS card · O3-R review |
| 4 finish | O4-1 tests, leak + idle checks · O4-R final review + merge note |

## 6. Decisions

| | Decision | Status |
|---|---|---|
| D1 | Engine switch `assist_engine`, default **extension** until the owner checks pass. | Settled (default) |
| D2 | Password box verified by bullet count only, never by text. If a browser exposes no value, trust SetValue's return and say so in the result. | Settled (spike proved it on Chrome) |
| D3 | The ✎ button stays on every OS card and does what it does today: opens the SCK widget for that client and service (`main.py` `_handle_sck_edit_requested` → `automation.show_sck_widget`). | Settled by the owner 2026-11-08 |
| D4 | Two browser windows on the same portal: the one in front wins; a card never moves to another window on its own. | Settled (default) |

## 7. Owner checks (O4-R lists them in the checks CSV)

1. SMTI on GST, Income Tax (PAN page → Continue → password page), MCA, EPFO in Chrome, Edge,
   Firefox: both buttons fill, login works.
2. SMTI on TRACES: step cards; click the user box → typed; click the password box → typed.
3. MECP plain + SCC mode: copies work; nothing appears in Win+V history.
4. SCA: copy a PAN in Excel, type it on Income Tax → password filled + chip; unverified client →
   amber card, Inject fills.
5. Cards sit top-right of the browser, follow a window drag, hide on another tab, don't cover other
   apps, and never take focus from the browser.
6. Switch back to Extension: everything works as before.

## 8. Testing notes for workers

- UIA code is tested with **fake elements** (small classes exposing the `Current*` properties and
  `GetCurrentPattern`), not a real browser. Real-browser checks go in the checks CSV.
- A real browser started by a script opens behind other windows and exposes no page tree. If a WP
  really needs one, start a throwaway profile with `--disable-features=CalculateNativeWinOcclusion
  --disable-backgrounding-occluded-windows --force-renderer-accessibility` on
  `tools/pre_dev/os_assist/test_login.html`, use `--method setvalue` paths only (keystrokes need the
  window in front and would type on the owner's screen), and close it after.
- Qt widgets: `QT_QPA_PLATFORM=offscreen` for tests.
- Full suite runs only in O4-1, with the live T/F/S plugin (CLAUDE.md).

## 9. Hand-off notes

- **O0-1** (2026-11-08, claude-haiku-4-5-20251001): Baseline tests run. All required UID-node files (core/sca/page_nodes.py, core/sca/uid_source_watch.py, docs/sca-uid-node-plan.md) exist and are committed. Reference code fill_spike.py present. Test results: 393 passed, 24 pre-existing failures. Failures: test_clipboard_assist.py (19 tests, PAN column validation), test_sca_audit_log.py (5 tests, NAME OF COMPANY column validation). Note: test_sca_uid_source_watch.py does not exist (file not found); test_sca_uid_node.py runs successfully instead. JavaScript tests (test_sca_coordinator.js): 39/39 passed.
- **O1-1** (2026-11-08, claude-sonnet-5-5): Built core/os_assist/{__init__,worker,locate}.py + tests/test_os_assist_locate.py (12 pass). worker.py: `submit(fn,*args)->Future`, `stop()` (restarts lazily on next submit), own daemon thread "os-assist-uia"; `Uia` class (url/documents/edits, read-only) is created per worker thread via `worker.current_uia()`; tests install a fake with `worker.bind(fake)`. locate.py functions take no uia argument: they MUST run inside `worker.submit(...)`. API: browser_windows, read_url, window_for_host (foreground first, D4), host_matches_service(hwnd, service dict or object with login_page_link), page_documents (skips offscreen + NativeViewHost), page_rect -> (l,t,r,b)|None, find_fields(hwnd, user_sel, pass_sel) -> (user_el, pass_el), plus describe/visible helpers O1-2 can reuse. A #id selector matching no visible box falls back to the rule-2 heuristic. Nothing here calls a pattern method. O1-2 must re-check the host via host_matches_service right before each write.
- **O1-2** (2026-11-08, claude-sonnet-5-5): Built core/os_assist/fill.py (the one writer) + tests/test_os_assist_fill.py (15) + tests/test_os_assist_one_writer.py (scans repo .py; fill.py and tools/pre_dev/os_assist allowed). Must run inside `worker.submit(...)`. API: `fill(hwnd, element, text, is_password, service, method='auto'|'setvalue'|'keys', keyboard=None) -> FillResult(outcome filled|failed|refused, method setvalue|keys|'', reason)`; `type_into_focused(hwnd, text, is_password, service, keyboard=None)` (host check, browser in front, focused element must be an Edit, Ctrl+A/Delete then type, verify); `FocusWatcher(hwnd).poll()` returns each new focused Edit once (None otherwise; skips address bar by name/id, other windows) - caller polls every `FocusWatcher.POLL_S` = 0.2 s and then calls type_into_focused. `Keyboard` (foreground, bring_to_front, sleep, type_text->bool) is the injectable object; tests pass a fake.
  Decisions: host check via `locate.host_matches_service` before SetValue and again before keys (refused outcome); verify at 0.15 s and 1.0 s; password verified by bullet count only (all chars equal and len == len(text)); if a password box exposes no value the result is `filled` with reason "value not exposed; trusted the write" (D2); a user box with no readable value is failed. method='setvalue' never focuses, brings to front or types. Log line is `user|pass outcome reason`, never a value (logger `os_assist.fill`).
  The fill needs `worker.current_uia()` to expose `.uia` (GetFocusedElement, CompareElements) and `.c` (UIA_ValuePatternId, IUIAutomationValuePattern); the real Uia already does. Not tested on a real browser (O1-R / owner checks).

- **O1-3** (2026-11-08, claude-sonnet-5-5): `core/os_assist/secure_clipboard.py` — `copy(text, secret, clear_after_s)` (Qt main thread, QMimeData; secret adds the 3 Windows formats via `application/x-qt-windows-mime;value="<Name>"`, constants EXCLUDE_MONITOR/NO_HISTORY/NO_CLOUD) and `clear_if_unchanged(text)` (in-memory compare, never logged). Tests: `tests/test_os_assist_clipboard.py` (4 pass, offscreen). Not checked: that Win+V really skips the entry (check-add if wanted). Caller passes the *clipboard clear* seconds setting; `clipboard_watch.suppress_client` is the caller's job.
- **O1-R** (2026-11-08, claude-opus-5-5): Reviewed core/os_assist/ against §0, §4.1–4.3, 4.5. Checked: no value logged/printed (only fill.py logs, `user|pass outcome reason`; reasons carry lengths only); SetValue/SetFocus/SendInput only in fill.py; secure_clipboard never logs. Fixed: (1) fill.py keys path had no host check right before SetFocus or before typing (only before bring-to-front) → now checked before SetValue, SetFocus and the keys (`_type_and_verify` takes `service`); (2) `_check` now judges any box with `IsPassword` by bullet count even if the caller passed `is_password=False`, so a password value is never compared as text; (3) locate ignored selector lists — real configs use `#passwordInput, input[id='passwordInput'], input[type='password']` — now cleanSelector + split on `,`, and `#x`, `input#x`, `[id='x']` parts map to AutomationId in order; (4) worker: `current_uia()` raises off the worker thread (no UIA/COM object can be created on the Qt thread; tests still `bind` a fake); each worker thread gets its own queue so a stop()/submit() race can't leave two workers; docstring: returned elements are opaque handles, only used inside later jobs. New test: OS Assist code (core/ui os_assist, fill.py included) has no Invoke/Toggle/Select/mouse_event/keybd_event/Post/SendMessage/VK_RETURN/TAB/ESCAPE.
  Field rules vs sera_dom: matches §4.2 (username "closest before password" is the blueprint's own choice). Not ported, by decision: revealed-password (type=text) fallback and focused-element fallback → SMTI copies. UIA can't see `tabindex=-1`; IsEnabled is checked though sera_dom doesn't.
  Tests: phase-1 38 pass; O0-1 list same 24 pre-existing failures (clipboard_assist 19, sca_audit_log 5); JS 39/39. Not real-browser tested. Callers (O2/O3) must run all locate/fill calls via `worker.submit`.
- **O2-1** (2026-11-08, claude-opus-5-5): Built `ui/os_assist/{__init__,dock_card}.py` + `tests/test_os_assist_dock_card.py` (14 pass, offscreen). `DockCard(badge=None, timeout_ms=None, locator=None, win=None, clock=time.monotonic)`: flags Tool|Frameless|WindowDoesNotAcceptFocus, WA_ShowWithoutActivating, translucent; `attach(hwnd, service)` sets WS_EX_NOACTIVATE|WS_EX_TOOLWINDOW and GWLP_HWNDPARENT (native calls only on the "windows" platform; again on every show). `present()` starts the timer + 100 ms `_follow` tick: IsWindow false → `dismiss("browser_closed")`; IsIconic → hidden "minimised"; `locator.probe(hwnd, service)` (default `WorkerLocator` = worker.submit of host_matches_service + page_rect; a Future or a plain tuple) at most every 0.5 s, one in flight, error = mismatch → hidden "host"; page rect kept as an inset of GetWindowRect so drags are followed every tick. Docked right edge = page right − 24, top = page top + 18 (`css_px`).
  Shared: `css_px(v)` = v / ui_scale.running_scale() (decided: match the browser 1:1), `to_logical` (physical → Qt per screen), `slide_curve()` (cubic-bezier 0.16,1,0.3,1), slide-in of the inner `self.card` frame + fade, `TimerBar` (start/pause/resume/reset/stop, `expired`; resume with ≤500 ms left expires, as the extension; click resets). Header: `badge` (uppercased), `edit_btn` → `edit_requested`, `close_btn` → dismiss("close"). `dismissed(str reason)` is emitted after `clear_secrets()`; dismiss is idempotent; a dismissed card refuses `hold_secret`.
  Next WPs: put content in `self.body`, call `refit()` after building it, keep secrets only via `hold_secret`/`secret`, call `self.timer.reset()` on button clicks (hover pause is the base's), override `card_qss()`/`ACCENT` for MECP's look, wire `edit_requested` to SCK (D3). Not tested on a real browser (owner check 5).
- **O2-2** (2026-11-08, claude-opus-5-5): Built `ui/os_assist/smti_card.py` + `tests/test_os_assist_smti_card.py` (27 pass; dock_card + one_writer still pass). `SmtiCard(service, user_id, password, client_id, client_name, clear_seconds=30, timeout_ms=30000, flutter=None|bool, filler, copier, sck_handler, **DockCard kw)`; launcher (O3-2) then calls `attach(hwnd, service)` + `present()`. User ID and password only in `hold_secret("user"/"pass")`.
  Jobs via `WorkerFiller` (one per card, Futures from worker.submit): `fill` (find_fields + fill.fill -> outcome or "nobox"), `edit_count` (visible Edits, -1 = no Document), `focus_step` (card's own FocusWatcher, then type_into_focused). Results reach the Qt thread through the `_deliver` signal; results after dismiss are ignored; a second click while a job runs is ignored.
  Texts/timings as the extension: Injected 2 s, Copied Username 2.5 s (copy not secret), Password filled -> dismiss("filled"), else secure copy (clear_seconds) + dismiss("copied") after 1.2 s. QSS ports `.btn` primary/done/warn, `.client-title`, `.guide-step` active/done (no box-shadow/opacity in QSS).
  Flutter: TRACES host (`is_flutter_service`: tdscpc.gov.in / traces) or a probe 1.5 s after present finding 0 visible Edits (retried ≤3 while no Document; never after a button press). 200 ms focus poll (skipped while hidden or a job runs), step 1 user, step 2 password, done -> dismiss after 0.4 s. No Tab (rule 1); a step not typed copies with the button copy texts and moves on.
  D3: `dock_card.set_sck_edit_handler(fn)` (main.py registers `_handle_sck_edit_requested` next to the bridge hookup) + `dock_card.wire_sck_edit(card, client_id, service_id, portal, handler=None)` — MECP/SCA cards call the same. Portal = service name lower-cased (as `_extension_payload`).
  Next: the username copy goes through the normal clipboard (not secret), so clipboard_watch may see it as today; the assist lock (§4.6.7) is O3-3's. Not real-browser tested (check added).
- **O2-3** (2026-11-08, claude-opus-5-5): Built `ui/os_assist/mecp_card.py` + `tests/test_os_assist_mecp_card.py` (19 pass; dock/smti/one_writer still pass, 60 total). `MecpCard(service, user_id, password="", client_id, client_name, clear_seconds=30, timeout_ms=90000, scc_rows=None|[{id,label,value}], attempt_id, copier, scc_send, ledger, sck_handler, **DockCard kw)`; then `attach(hwnd, service)` + `present()` (SCC: `att.hwnd`). Look ported from mecpWidget (dark #161B22, 320/360 px, header line, field rows, toast "<label> copied!" 2.5 s, "✓ Copied" 2 s).
  Plain: User ID row only if non-blank (plain copy), Password row (secret copy, clear_seconds); both copied -> timer stopped, dismiss("copied") after 1.2 s; timeout -> "timeout"; ✕ -> "close". No messages sent in plain mode.
  SCC: badge "⚡ Sera Assist (SCC)", PAN row, combo rows (secret copy + `ledger(text)` directly), "This one worked" -> `{"type":"scc_row_worked",attempt_id,row_label}`, None -> `{"type":"scc_row_none",attempt_id}`, ✕ only -> `{"type":"scc_card_closed",attempt_id}` — same dicts the bridge emits, so `send` just dispatches to main.py's `_handle_scc_*`. No timer. `apply_update(failed, next, message, stop, ask=None)` = the extension's onUpdate (not `update`: that is QWidget's).
  Hooks for O3-4: `mecp_card.set_scc_hooks(send=, ledger=)` (per-card args override), and module `update_card` / `close_card` with automation.update_scc_card / close_scc_card signatures, safe from SCC-U's thread (queued signal); open cards keyed by attempt id, a new card for the same attempt dismisses the old ("replaced"); close_card dismisses with "closed" (desktop not told back).
  Values are shown (as today), held only in secrets, wiped from the labels on dismiss, never logged. Not done here (launcher, O3-2): removing an open SMTI/MECP card first, `suppress_client`. Copies don't reset the timer (the extension's MECP doesn't). Not real-browser tested (check added).
- **O2-4** (2026-11-08, claude-sonnet-5-5): `ui/os_assist/sca_card.py` `ScaCard(arm_id, service_id, business_name, owner_name, portal_name, note="", client_id=None, sck_handler=None)` on DockCard: badge "SCA", title, "owner • portal" subtitle, amber note + amber border (`.amber`) when `note` is set (the engine passes the needs_confirm text), "Inject Password" -> `inject_requested(arm_id, service_id)` then dismiss("injected"); ✕ dismisses with "close" and emits nothing; 60 s timer bar; ✎ via `wire_sck_edit`. Holds no password.
  `ui/os_assist/sca_chip.py` `ScaChip(business_name, portal_name, token="", life_ms=2400)`: a DockCard too (docked, no focus), text "Filled <portal> for <client>[ (<token>)]", 250 ms fade-in, dismissed("timeout") at 2.4 s then a 400 ms fade-out and hide; no header or buttons.
  Both need `attach(hwnd, service)` + `present()` from the engine. The engine should show one chip/card per window. Tests: `tests/test_os_assist_sca_card.py` (10) plus the dock/smti/mecp card tests pass. Not real-browser checked (owner check added).
- **O2-R** (2026-11-08, claude-opus-5-5): Reviewed ui/os_assist/ against §0, §4.4–4.6 and the extension CSS. Shots: `tools/pre_dev/os_assist/render_cards.py` (offscreen, `QT_QPA_FONTDIR` = Windows fonts, 2x) → `docs/os_assist/shots/{smti,smti-flutter,mecp,mecp-scc,sca,sca-confirm,chip}.png`; rerun after any card change.
  Drift fixed: (1) DockCard geometry is now class attributes (DOCK_TOP/RIGHT, PADDING, HEADER_GAP, TIMER_GAP/HEIGHT, BADGE_SPACING, CLOSE_TEXT, SLIDE_IN_MS/FADE_IN_MS, TIMER_INTERACTIVE); MECP = right 18, 355/395 px (its `.box` is content-box), "×", no slide-in/out, field labels + section caps via QFont (text unchanged), margin-collapse spacings; SCA = top 20, 320 px fixed, radius 12, own gradient/badge/✕ sizes, title/subtitle elided on one line, 3 px timer 10 px below, no hover pause or click reset (as showWidgetInPage); chip = top 20, one line up to 340 px. (2) QSS font on `QFrame#card` didn't reach children → `QWidget { font-family: 'Segoe UI' }`; ✎/✕ use 'Segoe UI Symbol'. (3) refit used the narrow size-hint height (gap under wrapped titles) and a wrapping QLabel's guessed width → now CSS shrink-to-fit (no-wrap width, 320–420) and height for that width; fixed-width cards keep theirs. (4) Step-card border opaque #222826 (Qt overdraws translucent border corners).
  Focus: every widget of every card is NoFocus + WindowDoesNotAcceptFocus + WA_ShowWithoutActivating (test). Secrets: all dismiss paths go through DockCard.dismiss → clear_secrets (chip too now). Fixed: an SMTI fill or Flutter focus job still queued on the worker when ✕ is pressed is now cancelled (after the card is marked dismissed), so nothing types after the card goes; a job already running finishes and its result is ignored.
  Tests: new `tests/test_os_assist_cards_review.py` (14); mecp width asserts → 355/395; smti "result after dismiss" test marks its Future running. All 9 os_assist test files: 120 pass. Not done: box-shadows (MECP/SCC glow) and the chip's 6 px drop-in; owner check 5 = compare shots with real extension cards.
- **O3-1** (2026-11-08, claude-sonnet-5-5): Engine switch. `automation.py`: `assist_engine()` ('extension' default | 'os', read fresh from settings.ini [Automation] assist_engine), `set_assist_engine(x)` (writes the ini, keeps other keys, bad value -> extension), `add_engine_listener(cb)` / `remove_engine_listener(cb)` (cb gets the new engine only when it changed; later WPs hook here to react without a restart).
  `ui/dialogs/unified_settings_dialog.py`: "Assist engine" combo (Extension / OS) with the required hint, on the Action Buttons page (the dialog has no Automation page; Automation Mode is described there). Lazy-built; in `_capture_state`, load and save. Save writes settings.ini through `set_assist_engine`, not the db.
  Tests: `tests/test_assist_engine.py` (5) + test_unified_settings_lazy pass.
  Next WP: nothing is routed on the value yet. trigger_* routing and `update_extension_settings(sca_enabled=False)` must call `assist_engine()` and listen via `add_engine_listener`.
- **O3-2** (2026-11-08, claude-opus-5-5): `core/os_assist/launcher.py` + `tests/test_os_assist_launcher.py` (18 pass; 135 with the other os_assist/engine/SCC trigger tests). `automation.trigger_manual_assist` / `trigger_mecp` call `_launch_os_card` → `launcher.launch(kind, service, user_id, password, client_id, service["_client_name"], on_error)` when `assist_engine()=='os'`, else `_send_to_extension` exactly as before.
  `Launcher(opener, finder, card_factory, sleep, clock, spawn)`: launch bumps a per-kind generation, dismisses the live card of that kind ("replaced", secrets go), then a daemon thread opens the login link (`open_in_default_browser(url, service browser)`, new tab), first look after 1 s, then every 0.5 s one `worker.submit(locate.window_for_host, url)` job (5 s cap each) up to 20 s; the worker is never held for the wait. Found → `_show` on the Qt thread (queued `_QtCall`): builds SmtiCard (30 s) / MecpCard (90 s), `attach(hwnd)`, `present()`; attach failure dismisses the card + on_error. Timeout → `on_error(timeout_message(service))` (names the portal, no values). A superseded wait shows and reports nothing. `launcher.default().card(kind)` / `.dismiss(kind)` give the live card (O3-3 assist lock can read `card.browser_hwnd`).
  Clear seconds: `launcher.set_clear_seconds_provider` — main.py registers `db.get_setting("clipboard_clear_seconds")` next to the ✎ hookup; clamped 5–300, default 30. suppress_client stays in Client Detail (both engines). MECP via SCC (`core/scc/manual.open_card`) is untouched here (O3-4). Not real-browser tested (check 6).
- **O3-3** (2026-11-08, claude-opus-5-5): `core/os_assist/sca_engine.py` + `tests/test_os_assist_sca_engine.py` (29 pass). `ScaEngine(watcher, submit, probe, filler, host_of, card_factory, chip_factory, locked, now)` is a Qt-thread QObject (handle_password_request reads the vault there); UIA only in worker jobs: `probe_job` (fg browser, host on an armed portal + approved domain, arm id in the focused / up to 8 visible non-password boxes; box text never leaves the job, password boxes never read), `host_job`, `fill_job` (only when that window is in front; find_fields + fill.fill). 600 ms QTimer runs only while armed; expiry, SCA off (`watcher.enabled`), consumed (fills >= max_uses) or `disarm_all()` stop it. Ported: `pick_service`, `blocked_service`, `is_approved_domain`, `is_arm_live`, `candidate_matches`, onCandidate (blocked notice once, widget/assist/needs_confirm -> ScaCard with CONFIRM_NOTE, else in-process request with the extension's dict), onWidgetFill (same-client older card ok, host re-read), actOnUidPage (source arm, once, page_link of the window in front). Grant -> password kept only in the pending fill, re-tried each tick up to 90 s ("no visible password field"), then handle_fill_result + ScaChip. busy key = arm:hwnd:service. Assist lock = `assist_hwnds()` (launcher SMTI/MECP cards + SCC MecpCards).
  Decided: a box is acted on when its match appears (or on a new arm), not every tick — a denial/failed fill is retried only when the id is typed again. Wiring: `clipboard_watch._arm_client_services` sends the arm to `sca_engine.for_watcher(self).arm(arm, allowed_portal_domains)` when `assist_engine()=='os'`, else `arm_sca` as before; `set_enabled(False)` disarms (only if the module is loaded). `automation.update_extension_settings` and `main._get_extension_settings_payload` send `sca_enabled` = user setting AND engine != os (all callers covered); main registers `_on_assist_engine_changed` (resync; leaving OS disarms) and resyncs after a popup toggle in OS mode.
  Tests: O0-1 list same 24 pre-existing failures (clipboard_assist 19, sca_audit_log 5); JS 39/39; os_assist/engine/ws_bridge/scc tests pass. Not real-browser tested (check 7). Switching OS→Extension drops the OS arm; staff copy the id again.
- **O3-4** (2026-11-08, claude-sonnet-5-5): SCC card through the OS MECP card. `automation.send_scc_card` (new `hwnd=0` kw), `update_scc_card`, `close_scc_card` route when `assist_engine()=='os'`: send -> `launcher.launch_scc(service, pan, rows, title, client_id, attempt_id, hwnd, open_tab, on_error)` (Qt thread, `MecpCard(scc_rows, attempt_id)` attached to `hwnd`); update/close -> `mecp_card.update_card/close_card`. Extension mode untouched (same bridge calls).
  `launch_scc` with no hwnd works only for Client Detail's button (open_tab): opens the login page and waits for the browser window like `launch`; otherwise returns False (no card). main.py: open_card lambda passes `hwnd=SccCard.attempt_hwnd(attempt_id)` (new public method); `_ensure_scc` calls `mecp_card.set_scc_hooks` so ✕ / worked / none go to the existing `_handle_scc_*` handlers and combo copies reach `note_clipboard` directly. All SccCard callbacks unchanged.
  Tests: new `tests/test_os_assist_scc_routing.py` (9 pass); all tests/test_scc_*.py and mecp_card/launcher tests pass. Not real-browser tested (check added). Switching engines mid-attempt: an open card stays with the engine that opened it only until the next update/close, which then goes to the new engine.
- **O3-R** (2026-11-08, claude-opus-5-5): Reviewed phase 3 wiring against §0, §4.6–4.7. OK as built: Extension mode only gains an `assist_engine()` read before the old calls; arms carry no password (engine refuses one); fills go through fill.fill (host check before each write); extension SCA forced off in every settings path + popup resync; engine timer runs only while armed, worker thread starts only on first submit, main imports launcher only (no UIA).
  Fixed: (1) `main._handle_sca_password_request` now calls new `clipboard_watch.handle_extension_password_request`, which denies every extension request in OS mode — before, an arm sent before the switch (or ws_bridge's `_replay_sca_arm` to a newly connected browser) could still get a password to the extension. (2) `set_assist_engine` rewrote `~/AmanAssociates_Sera/settings.ini` (next to master.db, the MCL_Columns seed file) with configparser: lower-cased every key, dropped comments, raised on a UTF-8 BOM. Now edits only the `assist_engine` line in [Automation] (BOM/encoding kept, latin-1 fallback); `assist_engine()` reads BOM files too. (3) `ScaEngine._drop` cancels a still-queued fill job, so a disarm (SCA off, engine switch, new arm) can't type afterwards.
  Tests: new `tests/test_os_assist_wiring_review.py` (8); os_assist/engine/launcher/SCC/ws_bridge/settings tests pass; O0-1's 24 pre-existing failures unchanged. Decisions recorded (2), check 9 added (one fill only with the extension connected; live switch back).
  Next (O4-1): an extension arm live at the Extension→OS switch is not moved to the OS engine (staff copy again); `on_error` from launcher runs on its wait thread, as the extension path does.
- **O4-1** (2026-11-08, claude-sonnet-5-5): Added tests/test_os_assist_no_leak.py (10 tests): fake-UIA fill paths, SMTI/MECP cards and the SCA engine (ok/short/host-changed) driven with fictional passwords, capfd + every logging record (level 1) asserted free of them; idle test: no card and no arm for 5 s -> no UIA job, no worker thread, engine timer off, no launcher card. All pass. Full suite (live T/F/S, 3900 tests): 149 F, all outside this project: test_sync_* (golive/rejoin/restore/shadow_start/apply/capture/hotfix/maintenance/tables), test_clipboard_assist + test_sca_audit_log + audit_log_v2/cell_formatting/dual_pk/id_field/raw_payload (PK-column validation, same family as O0-1), startup_speed, sgt_i_*, sgt_replay, sdis_tables, class_diff_render, validate_icons, undo_redo, vsdc_reader b5, syncthing_restore. test_os_assist_cards_review geometry x2 failed once in the full run but pass alone and in every ordered subset (flaky under load, I ran other pytest runs concurrently). No product code changed. Tip: plugin run needs tools on sys.path (python -c with sys.path.insert + pytest.main).
- **O4-R** (2026-11-08, claude-opus-5-5): Final review, no code changed. Wrote `docs/os_assist/merge-readiness.md` (what was built, files, tests, §0 rule-by-rule result, known gaps, how to switch engines, owner checks). All 8 §0 rules hold in the code: writers only in fill.py, host check before SetValue/SetFocus/keys, passwords only in card secrets / SCA pending fill, no edits under sera_extension*/, core/sgt*/, core/vsdc/, core/sdis/, version.json; every route is behind `assist_engine()=='os'`, default extension.
  Tests: 15 os_assist/engine files 199 pass; JS sca_coordinator 39/39; ws_bridge/settings_lazy pass; test_startup_speed's 5 search-grid failures are the pre-existing Internal-PK family (O0-1/O4-1).
  Owner checks §7 1–6 added as checks 10–15. Known gaps listed in the note: no real-browser run yet (D1 keeps extension default), shadows/chip drop-in not ported, revealed-password fallback not ported, arm not carried across an engine switch, Win+V exclusion untested.
  **Before merging:** `.os-assist.lock` (dispatcher lock) and `logs/os-assist/*.json` were committed on this branch (O4-1's `git add -A`); keep them out of main. Not merged.
