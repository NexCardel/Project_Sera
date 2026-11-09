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
| 3 | Opening the portal | A new tab every time is OK. |
| 4 | How SCA sees the login page | UIA reads: address bar + a password box present. No extension tracking. |
| 5 | Fast Autofill | Stays on the extension. Not part of this project. |
| 6 | Legal / ToS | The owner handles it. Not tracked here; workers don't raise it. |

Out of scope: Fast Autofill; porting the SCK card itself (D3); anything in rule 1's "never" list.

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
- **✎ button** — D3.

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
| D3 | The ✎ button in OS mode. Default **(a)**: still opens the SCK card through the extension (`automation.show_sck_widget`), and the button is hidden when no browser is connected to the bridge. Alternative (b): open the client's record in Sera. O2-2 asks the owner. | Asked by O2-2 |
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

*(none yet)*
