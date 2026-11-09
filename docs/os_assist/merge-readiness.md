# OS Assist — merge readiness (O4-R, 2026-11-08)

Branch `os-assist`, reviewed at `c055c65`. **Not merged.** Code-complete and unit-tested against fake
UIA elements; **not yet run in a real browser** — the owner checks below decide the merge.

## What was built

SMTI, MECP and SCA can run without the extension's in-page cards. With `assist_engine = os`:

- **SMTI** (Client Detail → Username/Password): Sera opens the login page in a new tab, waits up to 20 s
  for the browser window, and docks a Sera card top-right of the page. A button fills the box through
  UIA `ValuePattern.SetValue`, checks it (user box: text; password box: bullet count only), and falls
  back to `SendInput` Unicode keys; no box found → copy, as today. TRACES (Flutter) keeps the step cards:
  Sera types into the box staff click.
- **MECP** (plain and SCC mode): the same card as a docked window. It still **copies** (secure
  clipboard: kept out of Win+V history and cloud sync, cleared after the *clipboard clear* seconds).
  SCC attempts dock to the attempt's browser window; worked / none / ✕ reach the existing `_handle_scc_*`.
- **SCA**: the arm goes to the desktop's own engine instead of the browsers. While an arm is live it
  watches the browser in front (0.6 s), matches the armed id typed on an armed, approved portal, asks
  `handle_password_request` in-process, waits up to 90 s for the password box, fills it and shows the
  "Filled <portal> for <client>" chip. Unverified / widget mode → the SCA card with **Inject**. The
  extension's SCA is sent off, and any password request from a browser is denied, in OS mode.
- ✎ on every card opens the SCK widget as today (D3). Fast Autofill and SCK stay on the extension.

## Files

| Area | Files |
|---|---|
| Core (UIA) | `core/os_assist/worker.py` (own UIA thread), `locate.py` (window, Document, boxes), `fill.py` (the only writer), `secure_clipboard.py`, `launcher.py` (open tab, wait, show card), `sca_engine.py` |
| Cards (Qt) | `ui/os_assist/dock_card.py` (no-focus, browser-owned, docked base), `smti_card.py`, `mecp_card.py`, `sca_card.py`, `sca_chip.py` |
| Wiring | `automation.py` (`assist_engine`, `set_assist_engine`, engine listeners, routing in `trigger_manual_assist` / `trigger_mecp` / `send_scc_card` / `update_scc_card` / `close_scc_card`, `sca_enabled` forced off), `clipboard_watch.py` (arm → OS engine, `handle_extension_password_request`, disarm on SCA off), `main.py` (✎ / clear-seconds / SCC hooks, engine listener, settings payload), `core/scc/card.py` (`attempt_hwnd`), `ui/dialogs/unified_settings_dialog.py` (combo) |
| Tools / docs | `tools/pre_dev/os_assist/render_cards.py` → `docs/os_assist/shots/*.png`; `docs/os_assist/*` tracker |
| Not touched | `sera_extension*/`, `core/sgt*/`, `core/vsdc/`, `core/sdis/`, `version.json` |

## Tests

- 15 files, **199 pass**: `tests/test_os_assist_*.py` (fill, locate, one_writer, clipboard, dock_card,
  smti/mecp/sca cards, cards_review, launcher, sca_engine, scc_routing, wiring_review, no_leak) +
  `tests/test_assist_engine.py`. Extension side: `tests/js/test_sca_coordinator.js` 39/39;
  `test_ws_bridge`, `test_unified_settings_lazy`, `test_scc_*` pass.
- Guard tests: only `fill.py` contains `SetValue(` / `SendInput` / `.SetFocus(`; no Invoke / Toggle /
  Select / mouse or key-event / Enter / Tab / Esc in OS Assist code; fictional passwords never reach
  stdout, stderr or any log record; idle (no card, no arm) = no UIA job, no worker thread, no timer.
- Full suite (O4-1, 3900 tests): 149 failures, all outside this project and of the kinds O0-1 found
  (Internal-PK column validation: clipboard_assist, sca_audit_log, startup_speed's search grid, …;
  sync, sgt_i, sdis, render, icons). `test_os_assist_cards_review` geometry failed twice under load in
  that run and passes alone.

## §0 rules — checked against the code

| Rule | Result |
|---|---|
| 1 Fill only what was asked | OK. Writes happen only on a card button, a Flutter box staff clicked, or an SCA match / Inject. No click, Enter, Tab, submit, scroll or dismiss (guard test). The keys fallback may bring the browser to the front (`SetForegroundWindow`) — a window action, not a page action. |
| 2 One writer | OK. Outside `fill.py` only comments in `core/vsdc`/`core/sgt_i` mention SetValue (test passes). |
| 3 Host check before every write | OK. `locate.host_matches_service` right before SetValue, before SetFocus, and before the keys; Flutter typing checks too. A change → `refused`. |
| 4 Passwords | OK. Held only in `DockCard.hold_secret` / the SCA pending fill; cleared on every dismiss path and on fill/disarm; queued jobs cancelled on ✕ / disarm. The one log line is `user|pass outcome reason`. Password boxes are read only for their bullet count (D2). Arms carrying a password are refused; OS mode denies every extension password request. |
| 5 Extension untouched | OK — no file under `sera_extension*/` changed; its SCA is turned off by the settings payload only. |
| 6 SGT/VSDC/SDIS | OK — not edited. UIA runs on OS Assist's own worker, not `vsdc_uia_text`'s. |
| 7 Extension engine unchanged | OK. Every route is `if assist_engine() == "os": … return`; default is `extension` (D1). Extension mode gains only a settings.ini read and the startup imports of `dock_card` / `launcher` (no UIA object until an OS card or arm). |
| 8 No AI / network / version bump | OK. |

## Known gaps

1. **No real-browser run yet.** Every UIA path is proven only with fakes (plus the WP0 spike). The owner
   checks below are the gate; D1 keeps `extension` the default until they pass.
2. Visual: box-shadows (MECP/SCC glow) and the chip's 6 px drop-in are not ported (Qt QSS has no
   shadow); compare `docs/os_assist/shots/*.png` with the real cards.
3. Field finding: the revealed-password (`type=text`) and focused-element fallbacks of `sera_dom` are not
   ported — SMTI copies instead. Only `#id`-style selector parts map to UIA.
4. Engine switch: an extension SCA arm live at Extension → OS is not moved (staff copy the id again);
   OS → Extension drops the OS arm. An open SCC card follows the new engine at its next update/close.
5. Launcher `on_error` runs on its wait thread, like the extension path.
6. `Keyboard.type_text` sends Ctrl+A / Delete before its per-character foreground check; focus on the
   box was verified ~0.15 s earlier, and the host just before.
7. Win+V / cloud-clipboard exclusion of secure copies is untested on a real machine.
8. **Before merging:** the branch carries runner files — `.os-assist.lock` (the dispatcher's live lock,
   committed by O4-1's `git add -A`) and `logs/os-assist/*.json`. Drop them (`git rm --cached`) or keep
   them out of `main` when merging; `docs/os_assist/` tracker CSVs are fine to keep.

## How to switch engines

- **Settings → Action Buttons → Assist engine: Extension / OS.** Takes effect at once, no restart.
- Or by hand: `~/AmanAssociates_Sera/settings.ini`, section `[Automation]`, `assist_engine = os`
  (or `extension`). Any other value or a missing line = Extension.
- One value covers SMTI, MECP and SCA. Switching to OS turns the extension's SCA off (resent to every
  browser); switching back restores the user's SCA setting and drops any OS arm.

## Owner checks (blueprint §7; also in `os-assist-checks.csv` with the earlier WPs' checks)

1. SMTI on GST, Income Tax (PAN page → Continue → password page), MCA, EPFO in Chrome, Edge, Firefox:
   both buttons fill, login works.
2. SMTI on TRACES: step cards; click the user box → typed; click the password box → typed.
3. MECP plain + SCC mode: copies work; nothing appears in Win+V history.
4. SCA: copy a PAN in Excel, type it on Income Tax → password filled + chip; unverified client → amber
   card, Inject fills.
5. Cards sit top-right of the browser, follow a window drag, hide on another tab, don't cover other apps,
   and never take focus from the browser.
6. Switch back to Extension: everything works as before.
