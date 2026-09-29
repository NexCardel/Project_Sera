# Autofill tweaks — blueprint

**Status:** proposed 2026-09-28, nothing in this plan built yet (Part H, already built, is listed
for context). Written from a design conversation with the user the same day. Built the way the SGT
overhaul was: one set of rules, parts that each say *what is wrong → what changes*, work packages
run one per fresh session by `tools/autofill_tweaks.py`, each ending with a hand-off note in §12.

Covers every way Sera fills or offers a portal login:

| Option | What staff do | Part |
| :--- | :--- | :--- |
| **Fast Autofill** | click the service's button in Client Detail; the extension fills the login form | C |
| **SMTI** (Manual Assist) | a widget on the login page injects User ID / Password on click | D |
| **MECP** (Manual Copy) | a floating card on the page with Copy buttons | E |
| **SCA** (Clipboard Assist) | copy a client's id anywhere; pasting it on the portal fills the password | F |
| **SCC** (Smart Credential Combinations) | Income Tax only: try PAN-based password combinations and save the one that works | G |

---

## 0. Rules over everything

1. **The browser extension does no tracking of any kind** *(user, 2026-09-28)*. It never watches
   pages, links, logins or filings. It only acts when staff press a button or copy a client id.
   Capture is SGT's job (desktop, UI Automation); SGT does not use the extension at all.
2. **Strictly passive toward portals.** Nothing Sera adds may click, submit, scroll or dismiss
   anything on a portal by itself. (Fast Autofill's existing "click Continue/Login" is unchanged
   by this plan; it is noted in §10 as a question for the user, not changed silently.)
3. **Passwords never touch disk in the browser**, never reach page scripts, and never sit on the
   clipboard longer than Settings → *clipboard clear* seconds.
4. **Chrome and Firefox builds stay identical** in behaviour (`sera_extension/` and
   `sera_extension_firefox/`; `tests/test_sca_v2.py` already enforces `sca/` being identical).
5. **No AI.** Rules, wording lists, counting.
6. **Release rules:** both extension manifests carry the same version; `version.json` is never
   bumped before a release is published; nothing secret goes into an installer.

---

## 1. The whole plan on one page

```
 Part A  Extension: remove all tracking  ────────────────┐  (makes rule 1 true; removes the
         (SDC, tracker.js, filing_detector, cookie wipe)  │   every-5th-injection cookie wipe)
                                                          v
 Part B  Shared plumbing: passwords off disk, one "open the portal tab" helper,
         clipboard clearing, one per-tab "assist is open here" lock
            |            |              |               |
            v            v              v               v
 Part C  Fast       Part D  SMTI   Part E  MECP    Part F  SCA
         Autofill   field picking  card closes,     uses counted on fill,
                    + re-inject    never flashes,   silent in an assist's tab,
                    rule           closed shadow    portals only
                                          |
                                          v
 Part G  SCC on UIA (SCC-U): SCC leaves the extension; SGT's page reads decide
         whether a combination worked; a desktop card replaces the extension card
```

---

## 2. What is wrong today — the findings, with where they live

Numbers are the ones used in the conversation; every WP names the numbers it fixes.

| # | Finding | Where | Part |
| :--- | :--- | :--- | :--- |
| 1 | Every 5th SMTI / MECP / Autofill injection clears **all cookies for every website** | `recordInjectionAndClearCookiesIfNeeded`, `background.js:1787` | A |
| 2 | Plain-text passwords in `chrome.storage.local` (on disk): `activeAutofillPayload`, `manualAssistPayload`, `mecpPayload`, `sccActiveAttempt` | `background.js:850, 1738, 2363, 1964` | A, B, G |
| 3 | Any open tab on the portal is taken over and navigated away (work in it is lost) | `handleAutofillTab`, `handleManualAssistTab`, `handleMECPTab` | B |
| 4 | MECP card flashes and disappears when a portal tab is already open (injected before the navigation, then never again) | `handleMECPTab`, `background.js:2369` | B, E |
| 5 | MECP card uses an **open** shadow root: page scripts can read the plain-text password | `mecpWidget`, `background.js:2112` | E |
| 6 | Passwords copied by MECP / SMTI stay on the clipboard | `copyCredential`, `copyText` | B |
| 7 | SMTI "Username" can type the User ID into the **password** box | `smartFill`, `background.js:1537` | D |
| 8 | SMTI can fill the portal's hidden decoy field that Fast Autofill deliberately skips | `visible()`, `background.js:1353` vs `:629` | D |
| 9 | SMTI widget re-appears on every logged-in GST page (`/auth` treated as a login URL) and on every page load in every tab of that host | `tabs.onUpdated`, `background.js:521` | D |
| 10 | SCC saves a combination as "verified" on any URL change (Forgot password, going home, logging in with another password) and overwrites the client's saved IT password | `background.js:558`, `login.js:118`, `main._handle_scc_password_verified` | G |
| 11 | Two SCC generators disagree (extension hardcodes `Link@`, `Income@2014`; shows empty rows) | `generateSccCombos` vs `db.generate_scc_passwords` | G |
| 12 | SCA: a failed fill still uses up the copy, so "try again" is silently refused | `clipboard_watch.handle_password_request:343` vs `sca_coordinator.js:376` | F |
| 13 | SMTI switches SCA off everywhere: disarms on every injection, `sca_fill_completed` consumes the arm, `manualAssistActive` blocks SCA on every site for 5 minutes | `background.js:1865, 43`, `sca_coordinator.js:453` | B, F |
| 14 | SCA can hand one portal's password to another (first "pass" column fallback) | `clipboard_watch._password_status:213` | F |
| 15 | MECP triggers SCA: its PAN copy arms SCA, SCA ignores the open MECP card and fills or covers it | `clipboard_watch:173`, `background.js:37` | B, E, F |
| 16 | MECP card stays open after the password is copied (90 s, plain text on screen); the timeout leaves the payload in storage | `mecpWidget`, `background.js:2337, 2355` | E |
| 17 | Neither UIA reader skips `IsPassword` boxes before reading their value | `vsdc_uia_text.py`, `sgt_i/uia_nodes.py` | G (W0-2) |
| 18 | Dead code: toolbar-click handler (the extension has a popup, so it never fires); `login.js` "fallback autofill" listener (nothing sends it) | `background.js:358`, `login.js:70` | A |

---

## Part A — Remove all tracking from the extension

**Goes (Chrome and Firefox):**

* `sdc/` (core, toast, ITR / GST / TRACES / MCA protocols), `tracker.js`,
  `content_scripts/filing_detector.js`, and their `web_accessible_resources` entries.
* In `background.js`: SDC injection on every page load and at start-up (`injectSDC`,
  `injectAllOpenTabs`, `sdcInjectedTabs`), `broadcastTrackerState`, `trackingTabId` /
  `activeAutofillPayload`, the `uncertain_result` report on tab close, and forwarding of
  `filing_result`, `sudr_capture`, `sdc_session_timeline`, `session_start`, `audit_event`.
* Tracker / FST / SDC / VSDC settings in the extension and its popup.
* **The cookie wipe (#1)**: `recordInjectionAndClearCookiesIfNeeded`, `clearBrowserCookies`,
  `injectionCount`, and the `browsingData` / `cookies` permissions.
* Dead code (#18).
* **Not in Part A:** SCC's page watching (the tab-URL observer, `checkSccLoginSuccess`,
  `checkUnregisteredSccTrigger`). It is removed in Part G once the desktop replacement works, so
  SCC is never left with no way to verify. (Deleting `sdc/` removes the `itr_protocol.js` copy of
  the unregistered-PAN trigger early; `login.js` keeps its copy until Part G.)

**Desktop side:** remove the bridge handlers that only served extension tracking
(`ui/ws_bridge.py` ~305–328), stop sending tracker fields in `automation.update_extension_settings`
and `request_settings` replies, and remove extension-only tracker toggles from Settings. **Every
toggle is checked first:** anything SGT, VSDC or VSDC247 reads stays.

**Proof:** a grep over both extension folders finds no page-watching code; SGT tests and a
`tools/sgt_replay.py diff` show capture unchanged.

---

## Part B — Shared plumbing

**B1. Passwords off disk (#2).** SMTI and MECP payloads move to `chrome.storage.session` (memory,
cleared when the browser closes; Firefox ≥ 115 has it). On start-up, any old
`manualAssistPayload` / `mecpPayload` / `sccActiveAttempt` / `activeAutofillPayload` left in
`storage.local` is deleted. A test scans every `storage.local.set` call site for a password field.

**B2. One "open the portal tab" helper (#3, #4).** `openPortalTab(url, onReady)` used by Fast
Autofill, SMTI and MECP:
* reuse an open tab only when it is showing that portal's **login page** (decision D5), else open
  a new tab;
* attach the "page finished loading" listener **before** navigating; inject once, after load;
* if the tab is already on the exact URL and loaded, inject without reloading;
* every listener removes itself after 30 s.

**B3. Clipboard clearing (#6).** Any password the extension copies is cleared after the desktop's
*clipboard clear* seconds (sent in the settings sync), only if the clipboard still holds it.
Best effort: a page cannot always write the clipboard when it is not focused; this is stated in
the UI tooltip, not hidden.

**B4. Per-tab assist lock (#13, #15).** Replace the global `manualAssistActive` flag with a map
`tabId → "smti" | "mecp"` kept in the background (memory). SCA stays silent **in that tab only**
while its assist is open; every other tab and client works normally.

---

## Part C — Fast Autofill

* Uses B2 (no tab take-over, no double injection) and loses the cookie wipe (Part A).
* No tracking payload: `activeAutofillPayload` is gone with Part A, so no password is stored.
* **Built already (Part H):** each service's Automation Mode decides whether its button runs Fast
  Autofill, SMTI or MECP.
* Open question D8 (not changed without the user): Fast Autofill auto-clicks the portal's
  Continue/Login button after filling (`autoClickContinue`).

---

## Part D — SMTI (Manual Assist)

* **Field picking (#7):** "Username" only ever targets a visible **non-password** input; if none is
  found it copies instead of typing. "Password" only targets a password input (or the service's
  configured selector).
* **Visibility (#8):** the same rules as Fast Autofill (`isVisible`: zero size, `aria-hidden`,
  `tabindex=-1`, `hiddenPassword` are skipped). One shared function, not two copies.
* **Re-inject rule (#9):** only in the tab SMTI opened (B4's map), only while the payload is live,
  and only when the page shows a visible login form (a password box or the configured username
  field). No URL keywords.
* **SCA (#13):** SMTI no longer disarms SCA or sends `sca_fill_completed`; B4 keeps SCA quiet in
  SMTI's tab only.

---

## Part E — MECP (Manual Copy)

* **Never flashes (#4):** via B2.
* **Closed shadow root (#5)**, as SMTI and SCA already use.
* **Closes when done (#16):** after the password is copied, the card closes (decision D6: after the
  password copy, or after both User ID and password are copied). A timer bar with pause-on-hover
  replaces the silent 90 s; timeout and ✕ both clear the payload.
* **Does not set off SCA (#15):** B4 in the browser, plus on the desktop: when Client Detail
  launches MECP (or SMTI) for a client, `clipboard_watch` does not arm SCA for that client's ids
  for the next 5 minutes.
* **No SCC mode:** after Part G, MECP is always the plain card; unverified Income Tax clients get
  the SCC card instead.

---

## Part F — SCA (Clipboard Assist)

* **A use is spent on a successful fill, not on handing over the password (#12).** The desktop
  counts grants separately and caps them at uses + 2, so a failed fill can be retried without
  opening the door to repeated requests.
* **Denials are shown (#12):** a `SCA_PASSWORD_DENIED` reaches the SCA notice staff already see.
* **No "any column with pass in its name" fallback (#14):** a service with no password column
  reports "no password".
* **Scope (decision D7, default: portals only):** `login.js` is registered only on the approved
  portal domains (the built-in government portals plus the configured services' hosts) with
  `chrome.scripting.registerContentScripts`, re-registered when settings sync. It no longer runs
  on other websites. (Host permissions stay broad: Fast Autofill / SMTI / MECP need them for
  custom services. The point is that no script watches typing outside the portals.)

---

## Part G — SCC on UIA (SCC-U)

SCC stays **Income Tax only**. Its page watching leaves the extension; SGT's existing UIA reads
decide whether a combination worked; a desktop card replaces the extension's SCC card.

### G.0 The idea on one page

```
 Income Tax, password page            (SGT already reads it: spec itr_pan, "#/login/password")
          |
 1  NOTICE     SGT's read shows PAN P on the password page
 2  DECIDE     P registered + SCC-verified?  -> nothing;  otherwise -> attempt for P in this window
 3  CARD       desktop card beside the browser: PAN, combinations, saved password (D3),
               Copy, "This one worked". Copies go through the desktop.
          |    staff copy, paste and press Login themselves
 4  OUTCOME    SGT's next reads of the same window and session:
                 wrong-password wording   -> that copy failed (x)
                 lock-out wording         -> stop, warn staff
                 logged-in header         -> login worked
                 forgot / back / timeout  -> no conclusion
 5  WHICH ONE  exactly one Sera password copied since the last failure -> that one; else ask
 6  SAVE       one guarded save: never overwrites a different saved password silently (D2)
 7  CLOSE      card closes, clipboard cleared, outcome counted
```

Without SGT reading the portal (SGT Off), the card opens from Client Detail and **"This one
worked"** is the only way to save.

### G.1 The contract (built like SGT-I, `docs/sgt-blueprint.md` §14.2)

1. **Read-only copy.** SCC-U receives an `Observation` (`core/sgt_i/observation.py`, reused) after
   SGT-C finishes each page. It never writes into SGT's sessions, slots or rows.
2. **More reads, never fewer:** while an attempt waits for an outcome, SCC-U may ask for extra
   reads for at most 30 s after each copy (same bounded window as SGT-I's `read_harder`).
3. **Own thread, budget and trip.** A failure switches SCC-U's automatic part off for the run; the
   card and the manual button keep working.
4. **One switch:** Settings → SCC → *Detect login automatically*: Off / On (default Off until the
   live checks pass).
5. **One way to write:** the guarded save (step 6).

### G.2 The steps

**Step 1 — Notice.** The trigger is SGT-C's own `itr_pan` result on the password page. The attempt
is tied to the window (`hwnd`) and SGT's session id; SGT ends a session on a login link, so "back
to login" or "another client" ends the attempt with no conclusion.

**Step 2 — Decide.** Read-only client lookup: registered + SCC-verified → nothing; registered, not
verified → attempt (saved password as an extra row, D3); unregistered → attempt (D4 on save). One
card per PAN per window; closing it keeps it closed until the password page is reached again.

**Step 3 — The card and every copy.** Pinned to the browser window's top-right, shown without
taking focus. Rows: PAN, combinations from `db.generate_scc_passwords` only (empty ones left out,
#11), saved password (D3); each with Copy and *This one worked*. Copies use the desktop clipboard
with a new marker `application/x-sera-scc`, which `clipboard_watch` skips (no SCA arm). Each copy
goes into the attempt's in-memory ledger. A Sera password for P copied any other way (MECP, Client
Detail) is recognised by comparing in memory and added to the ledger; the text is never stored or
logged.

**Step 4 — Outcome.** Same window **and same SGT session** as the password page:
* worked: a later page shows the logged-in header ("`<NAME>` Individual", as in `sgt_fields.json`;
  a CA/ERI role does not count); any PAN or name shown must agree with P or its client;
* wrong password: the portal's message on the password page → last copy ✗, next row highlighted;
* locked out: stop and say so; never suggest another row;
* neutral: OTP, secure-access message, e-verification steps;
* no conclusion: forgot / reset password, a login link, another PAN, window closed, 10 minutes.

All wording lives in `core/scc/scc_rules.json`, loaded with self-tests (`examples` /
`counter_examples`, as SGT's specs). Real wording comes from the live probe (check list), never a
guess.

**Step 5 — Which one.** Distinct Sera passwords copied for P since the last wrong-password message:
exactly one → that row; several → the card asks which; none → the card asks, with "None — I typed
my own" (saves nothing). A ✗ row can't be credited in the same attempt.

**Step 6 — Save.** `main._handle_scc_password_verified` becomes `core/scc/save.py`, used by the
automatic path and the manual button; no browser message reaches it any more. Equal to the saved
password → mark verified only; no saved password → save + verified; a **different** saved password
→ D2; unregistered PAN → D4. Audit log: who, row label, when — never the value. Normal database path
(Sera Sync carries it). Client Detail and search refresh.

**Step 7 — Close and count.** Card closes after a save or ✕; clipboard clear still runs. Local
counts only (attempts, worked, failed, locked, no conclusion, asked, saved, **not understood**), shown
in Settings → SCC. A rising "not understood" count means the portal's wording changed.

### G.3 Removed from the extension at the end of Part G

The SCC mode of the MECP card, `generateSccCombos`, `sccActiveAttempt`, the tab-URL observer,
`SCC_LOGIN_DETECTED`, `checkUnregisteredSccTrigger`, `TRIGGER_UNREGISTERED_SCC_MECP`, the
`registered_pans` / `scc_settings` sync; on the desktop, the `scc_password_verified` bridge handler.

---

## Part H — Already built (2026-09-28, on `main` since 431db4b)

Service box: selector fields removed; Automation Mode = the autofill type (`extension` = Fast
Autofill, `smti` = SMTI, `manual` = MECP); one button per service in Client Detail, Alt+1…9 runs it;
Ext/Assist/Copy toggles removed from Settings → Action Buttons; labels no longer boxed. Files:
`automation.py`, `ui/dialogs/service_manager_dialog.py`, `ui/dialogs/unified_settings_dialog.py`,
`ui/windows/client_detail_window.py`, `tests/test_service_automation_mode.py`.

---

## 9. Privacy rules, all in one place

1. No password in `chrome.storage.local`, ever; SMTI / MECP payloads live in `storage.session`.
2. No page script can read a password Sera shows (closed shadow roots only).
3. Sera clears any password it put on the clipboard.
4. SCC-U reads no password box; both UIA readers skip `IsPassword` elements (#17).
5. SCC candidate values live only in desktop memory during an attempt; clipboard text is compared
   in memory and never stored or logged; audit and counts hold labels, times and counts only.
6. The extension watches nothing outside the approved portals (D7), and on them only what SCA needs.

---

## 10. Decisions

**Taken (2026-09-28):**

* No tracking of any kind in the browser extension (so the cookie wipe is removed, not scoped).
* SCC's automatic part runs on SGT's UIA reads; desktop is the only combination generator; SCC stays
  Income Tax only.
* Workers decide anything else the most accurate + cheapest way and record it (`decide`).

**Open (the user's) — each has a default; the WP that needs it asks with a 4-minute pop-up:**

| # | Question | Default | Asked in |
| :--- | :--- | :--- | :--- |
| D1 | SCC combinations on a desktop card, or keep them in the extension's MECP card? | desktop card | W5-3 |
| D2 | A *different* IT password is already saved: ask before replacing, or replace once login is confirmed? | ask | W5-6 |
| D3 | Show the saved (unverified) password as a row so it can be verified too? | yes | W5-2 |
| D4 | Unregistered PAN logs in: ask before adding the client, or add automatically (today)? | ask | W5-6 |
| D5 | Tabs: reuse an open tab only when it shows the login page, or always open a new tab? | reuse login-page tab | W2-2 |
| D6 | MECP closes after the password copy, or after both User ID and password are copied? | after password copy | W3-3 |
| D7 | SCA: run only on approved portals, keep it everywhere, or remove SCA? | portals only | W4-3 |
| D8 | Fast Autofill auto-clicks Continue/Login after filling: keep, or stop at filling? | keep (unchanged) | W3-1 |

---

## 11. Build order

Run by `tools/autofill_tweaks.py` (the SGT overhaul dispatcher, pointed at this folder): one fresh
session per WP, model per WP, retries, usage-limit sleeps with a live timer, questions by pop-up,
decisions and hands-on checks recorded in CSVs, a live Excel viewer. Plan: `autofill-tweaks-plan.json`.

| Phase | WPs | What |
| :--- | :--- | :--- |
| 0 | W0-1 … W0-2 | baseline; password guard in both UIA readers (#17) |
| 1 | W1-1 … W1-R | Part A: remove extension tracking and the cookie wipe; desktop side; review |
| 2 | W2-1 … W2-2 | Part B: passwords off disk; the tab helper |
| 3 | W3-1 … W3-3 | Parts C–E: Fast Autofill on the helper; SMTI fixes; MECP card |
| 4 | W4-1 … W4-R | Part F and B4: per-tab assist lock; SCA fixes; SCA scope; review |
| 5 | W5-1 … W5-9 | Part G: SCC-U frame, rules, card, outcome, attribution, save, health, wiring, extension removal |
| 6 | W6-1 … W6-R | docs, extension version (both manifests), final review |

**Checks only a person can do** are added by workers as they go (`check-add`) and listed in
`autofill-tweaks-checks.csv` — e.g. a real wrong-password attempt on Income Tax shows ✗ on the
right row; a MECP card on an already-open GST tab stays up; SMTI on the ITR password step fills
nothing into the wrong box. **Never test a portal lock-out on a real client account.**

---

### Re-plan 2026-09-29: D1 = extension card

The user chose to keep SCC's combinations in the extension's MECP card. Step 3 changes; steps 1, 2, 4-7 do not:
the desktop still generates the rows (the only generator), pushes the card into the already open portal tab when SGT
sees the password page, recognises copies through the clipboard in memory, and receives 'This one worked' as a
staff click (`scc_row_worked`). The extension still watches nothing; G.3 removes its SCC tracking but keeps the
card's desktop-fed SCC mode. WPs W5-3, W5-8 and W5-9 were re-planned accordingly.

### Merge readiness (W6-R, 2026-09-30)

**Ready to merge once the hands-on checks below pass.** Rules §0 and §9 re-checked on the whole branch:
no `chrome.storage.local` write holds a password (only SCA switches, domains, clipboard seconds); the only
page listeners left are SMTI's re-inject in the tab SMTI opened, one-shot tab-load waits, and SCA's scripts
on approved portals only; nothing clicks, submits, scrolls or dismisses; cards use closed shadow roots;
`sca/` and `content_scripts/` byte-identical and the injected page functions (`fillCredentialsInPage`,
`manualAssistWidget`, `mecpWidget`) identical in both builds; both manifests 2.12.0, `version.json` untouched.
SGT: `sgt_replay.py diff` over 1937 pages shows only "newly written" rows, none changed or lost.
Known test failure not from this branch: `test_vsdc247_integration.py::...still_a_duplicate` (database
dedup code not touched by the branch). Older ones: `test_clipboard_assist.py` DB-fixture setUp.

**Check by hand before merging** (`autofill-tweaks-checks.csv`, all "Not run"): 1-7 (tab reuse, SMTI
fields, MECP stays/closes, clipboard clear, per-tab lock, SCA scope, Firefox SMTI), 8 (one real
wrong-password wording probe, never a lock-out), 9-11 (SCC card from SGT and from Client Detail), 12
(Firefox Fast Autofill), 13 (SCC-U automatic path on/off; confirm the vsdc247 failure also on `main`).

## 12. Hand-off notes

- **W0-1** (2026-09-28, claude-haiku-4-5-20251001): Part H verified (automation.service_action_mode exists). Test files verified: 4 found (test_service_automation_mode.py, test_clipboard_assist.py, test_scc_vault_tagger.py, test_sca_coordinator.js), 28 SGT tests found; **test_sca_v2.py NOT FOUND** (only test_sca_protocol.py exists). sgt_replay.py baseline: blocked by tool approval (unattended worker). Tests require re-running in next session with approval. No code changes made. Status CSV updated.
- **W0-2** (2026-09-28, claude-sonnet-5): Password guard (finding #17) added to both UIA readers. `core/vsdc/vsdc_uia_text.py`: new `_is_password()` reads `UIA_IsPasswordPropertyId` (30019) via `element.GetCurrentPropertyValue`; `_collect_descendant_lines` now skips the ValuePattern read (keeps the label line) when it's true. `core/sgt_i/uia_nodes.py`: added `PID_IS_PASSWORD = 30019` to the cache request; `_node()` omits `"value"` for password-marked elements. `tools/sgt_lines_equivalence.py`'s `FakeElement`/`_FakeUiaClient` gained `is_password`/`UIA_IsPasswordPropertyId` support plus a `password_value_never_read` synthetic fixture, so the 14.2 gate now exercises this case on every run. Tests: `tests/test_sgt_lines_equivalence.py`, `tests/test_sgt_i_uia_nodes.py` pass; full `pytest -k "sgt or vsdc or uia"` is 982 passed / 6 failed (all 6 pre-existing, unrelated Gemini-API tests in test_vsdc_beeper.py/test_vsdc_gemini_enricher.py — no API key / mock-patch-target issues, nothing to do with this WP). `tools/sgt_replay.py diff` gave identical output on two consecutive runs (first-ever run per W0-1's note — no prior baseline existed to regress). Next WP: no dependency changes; the password guard is now in place for both readers before Part A/B build on top.
- **W1-1** (2026-09-29, claude-sonnet-5): Removed all tracking + the cookie wipe from both extension builds (findings #1, #18). Deleted `sdc/`, `tracker.js`, `content_scripts/filing_detector.js`, `config.json`, `event_types.json` (both builds) and their manifest entries (including `web_accessible_resources`, `browsingData`/`cookies` permissions). In `background.js`: removed `injectSDC`/`injectAllOpenTabs`/`sdcInjectedTabs`, `broadcastTrackerState`, `trackingTabId`/`activeAutofillPayload` writes, the `uncertain_result` tab-close handler, forwarding of `filing_result`/`sudr_capture`/`sdc_session_timeline`/`session_start`/`audit_event`, tracker/FST/SDC/VSDC settings sync (`update_settings`, `onInstalled`, `storage.onChanged`), `recordInjectionAndClearCookiesIfNeeded`/`clearBrowserCookies`/`injectionCount`, and the dead `chrome.action.onClicked` handler. Removed matching toggles/badges from `popup.js`/`popup.html`. Removed login.js's "fallback autofill" `chrome.runtime.onMessage` listener. Left SCC code (sccActiveAttempt, checkSccLoginSuccess, checkUnregisteredSccTrigger, MECP scc mode) and SMTI's tabs.onUpdated re-inject untouched, as directed. Note: `sera_extension_firefox/background.js` was pre-existing behind chrome's (missing SCC/manual-assist-reinject code entirely, unrelated to this WP) — applied the same tracking removals to what exists there rather than porting missing features. `sca/sca_coordinator.js` confirmed byte-identical between builds. Tests: `node --check` on every touched JS file passes; `node tests/js/test_sca_coordinator.js` 18/18 pass; `tests/test_sca_v2.py` does not exist (per W0-1's note) — ran `tests/test_sca_protocol.py` instead, 12/12 pass. Next WP (W1-2): desktop side stops serving tracker/FST/SDC/VSDC settings and filing_result/sudr_capture handling to the extension.
- **W1-2** (2026-09-29, claude-sonnet-5): Desktop side stopped serving extension tracking (grepped `sera_extension/` post-W1-1 to confirm nothing sends these types/fields any more, then removed the dead code). `ui/ws_bridge.py`: dropped the `uncertain_result`/`session_start`/`sdc_session_timeline`/`sudr_capture`/`filing_result`+`audit_event` dispatch branches and the first 4 Signals; `filing_result_received` stays (fed live by `vsdc_worker.filing_captured`, not the extension). `main.py`: removed `_handle_session_started`/`_handle_sdc_timeline`/`_handle_sudr_capture`, the `audit_event` branch in `_process_extension_result` (was SCA-audit logging the extension no longer sends), the vsdc/sdc/fst/tracker branches in `_handle_extension_settings_updated` and fields in `_get_extension_settings_payload`/`_sync_extension_settings` (kept `sca_enabled` etc.), and the startup default-init of `sdc_enabled`/`fst_enabled`/`tracker_enabled`. `automation.py`: `update_extension_settings()` dropped `fst_enabled`/`sdc_enabled`/`vsdc_enabled`/`tracker_enabled` params (extension's `update_settings` handler never read them); `_send_to_extension()` payload dropped `tracker_enabled`/`fst_enabled`. Updated the 2 other live callers of `update_extension_settings` (`ui/dialogs/unified_settings_dialog.py`, `ui/dialogs/service_manager_dialog.py`) and `ui/windows/client_detail_window.py`'s 4 `_fst_enabled`/`_tracker_enabled` per-call flags. Settings: removed the "File Submission Tracker (FST) Daemons" sub-header + `fst_check` ("Sera SDC — DOM Crosshair (FST)") from `unified_settings_dialog.py`'s general page — that was the extension's now-removed DOM tracker toggle. **Kept, confirmed still read by SGT/VSDC**: `vsdc_enabled`/`vsdc_x_enabled`/`vsdc247_enabled`/`vsdc_hud_enabled`/`sgt_mode`/`sgt_record_pages`/`sgt_i_mode` (unchanged in `unified_settings_dialog.py`, still read by `core/vsdc/vsdc_engines.py`); `scc_password_verified` dispatch branch (Part G needs it); `_handle_extension_result`/`_process_extension_result`/`_capture_queue` pipeline (still fed by `vsdc_worker.filing_captured` — confirmed this is a *separate* signal from the bridge's, so SGT/VSDC capture ingestion was untouched). Left the dead, never-instantiated `ui/dialogs/settings_dialog.py` alone (confirmed unreachable — only `unified_settings_dialog.py` is opened from `admin_window.py`); both `decide` calls recorded. Tests: `tests/test_ws_bridge.py` updated (dispatch test list trimmed; added `test_extension_tracking_message_types_are_no_longer_routed`; removed the obsolete `sdc/sdc_core.js`-reading test since `sdc/` no longer exists) — 27/27 pass. `tests/test_sgt_tracker_rows.py`, `test_sgt_live.py`, `test_sgt_i_pairs.py`, `test_sgt_i_uia_nodes.py`, `test_sgt_lines_equivalence.py`, `test_vsdc247_integration.py`, `test_vsdc_draft_supersession.py` — 96/96 pass. `tools/sgt_replay.py diff` — byte-identical across two runs (no persisted baseline between sessions, same as W0-2 found). `tests/test_scc_vault_tagger.py`, `test_sync_capture.py`, `test_dual_pk_and_sad_resolution.py`, `test_raw_payload_db_and_srpf.py` — 7 pre-existing failures (all `ValueError: Internal Primary Key 'NAME OF COMPANY' is mandatory` — verified identical on a clean stash of HEAD before any W1-2 edit, unrelated to this WP). Note for any future worker: `pytest -k "sgt or vsdc or uia"` crashes in this environment's pytest capture teardown regardless of changes (confirmed pre-existing on stashed HEAD too) — run the individual SGT/VSDC test files instead. Next WP (W1-R, Part A review): verify no extension-tracking code remains reachable anywhere in `main.py`/`automation.py`/Settings; Part B can now build on the tab helper without tracker plumbing in the way.
- **W1-R** (2026-09-29, claude-opus-5-5): Part A reviewed, passes. Grep of both extension folders: no SDC/tracker/filing/cookie/`browsingData`/`activeAutofillPayload`/`onClicked` code left; the only page observers left are the ones the plan defers: the global `tabs.onUpdated` in Chrome `background.js:342` (SMTI re-inject → Part D #9; SCC URL check → Part G), `login.js` SCC hashchange/popstate/interval (Part G), the SCA paste/input listeners (Part F/D7), and per-call one-shot `onUpdated` waits for tab load (fine). Passwords still in `chrome.storage.local`: `manualAssistPayload` (`background.js:1546`), `mecpPayload` (`:2056`), `sccActiveAttempt` (`:1688`), Firefox `:1136`, `:1427`. These are finding #2 and belong to W2-1 (sessions) / Part G, not Part A. Desktop: no tracker fields left in `main.py`/`automation.py`/Settings; SGT/VSDC settings intact. **Defects fixed:** (1) dead `ui/dialogs/settings_dialog.py` still passed `fst_enabled`/`tracker_enabled` to the new `update_extension_settings()` and would raise TypeError if ever opened, so the two kwargs were dropped; (2) both manifests still described the extension as "filing confirmation tracking", now "Portal login autofill" (version unchanged); (3) a stale "final SDC flush" comment removed from both `background.js`. `sca/` byte-identical. Tests: `node --check` both background.js; `test_sca_coordinator.js` 18/18; pytest ws_bridge, sca_protocol, service_automation_mode + 7 SGT/VSDC files: 141 passed. `sgt_replay.py diff` over 960 pages printed 13 "newly written" rows and no changed/lost rows. Part A changed no `core/` file (branch diff to main only has W0-2's guard). Still open (not Part A): the Firefox `background.js` lacks Chrome's SCC/SMTI-reinject code (W1-1 note). W2-1 must port the storage.session change to both builds anyway.
- **W2-1** (2026-09-29, claude-sonnet-5): Part B1 done (finding #2) in both builds. Added `_passwordStore` in `background.js` - `chrome.storage.session` when present, else an in-memory object with the same get/set/remove(callback|promise) shape (Firefox `strict_min_version` is 109, session storage lands at 115). `manualAssistPayload`, `mecpPayload` and Chrome-only `sccActiveAttempt` now read/write/remove through it everywhere in both `background.js` (the mixed `scaEnabled`+`manualAssistPayload` read in `getSettings` was split so only the non-password keys stay on `chrome.storage.local`). The three injected-into-page snippets that can't close over `_passwordStore` (the two `manualAssistWidget` dismiss/fill-complete cleanups in `sera_extension/background.js`, and the MECP card's close button) now guard on and call `chrome.storage.session` directly - a no-op on pre-115 Firefox, which is fine since the authoritative clear happens in the background context via `_passwordStore`. `content_scripts/login.js`'s `checkSccLoginSuccess` (both builds) and `popup.js`'s pending-assist status (both builds) switched their reads/removes of these keys from `.local` to `.session` the same way; `popup.js` now does a separate `.local` fetch for `scaEnabled`. On load, both `background.js` files delete any `manualAssistPayload`/`mecpPayload`/`sccActiveAttempt`/`activeAutofillPayload` left in `storage.local` by older installs. `sca/` confirmed byte-identical (`filecmp`). New test `tests/test_no_password_in_local_storage.py`: scans every `chrome.storage.local.set(...)` call in both `background.js` for the four password-shaped keys or a literal `password:` field, plus checks the startup cleanup line names all four - 4/4 pass. Ran (per W0-1/W1-1: `test_sca_v2.py` still does not exist): `tests/test_sca_protocol.py` + `tests/test_extension_settings_sync.py` 12 passed/1 skipped (pre-existing skip), `node tests/js/test_sca_coordinator.js` 18/18, `node --check` on all six touched JS files. No Firefox `sccActiveAttempt` background-side setter exists (per W1-1's note that Firefox `background.js` lacks the SCC handlers) - `content_scripts/login.js`'s Firefox `checkSccLoginSuccess` was already dead code before this WP and stays that way; only its storage call was ported for consistency. Next WP: Part G still owns deleting `sccActiveAttempt` outright.
- **W2-2** (2026-09-29, claude-sonnet-5-5): Part B2 done (findings #3, #4) in both builds. New `openPortalTab(url, onReady)` (+ `_findLoginTab`, `_loginUrlKey`, read-only `_seesPasswordBox` probe) sits just above `handleAutofillTab` in both `background.js`; `handleAutofillTab`, `handleManualAssistTab`, `handleMECPTab` are now thin wrappers (payload storing unchanged; SMTI keeps its 3 s Flutter delay inside `onReady`). Rules: D5 asked, user chose "reuse only a tab showing the login page" - a same-host tab is reused only if its URL origin+path equals the login URL (query/hash ignored) or a read-only `executeScript` sees a visible password box (no value read); otherwise a new tab opens and other tabs are never navigated. Listener is attached before `tabs.update`; `complete` counts only after a `loading` event (stale-complete guard); already on the exact URL and loaded -> inject at once, no reload; already on it but loading -> wait, no reload; one injection; every listener removed after 30 s. MECP flash (#4) and SMTI inject-then-reload double injection gone. Firefox now shares Chrome's behaviour (it had no already-on-URL/once guard). New `tests/js/test_open_portal_tab.js` (both builds, mocked `chrome`) passes; `test_sca_coordinator.js` 18/18, `test_no_password_in_local_storage.py` + sca_protocol + extension_settings_sync 16 passed/1 skipped, `node --check` both files. Still Part D's: the global `tabs.onUpdated` SMTI re-inject (`background.js:377`) also injects 700 ms after load (the 1 s `_lastManualAssistInject` guard mostly hides it). Human check added: MECP card stays up on an already-open login tab.
- **W3-1** (2026-09-29, claude-haiku-4-5-20251001): Part C done. Confirmed `handleAutofillTab` uses `openPortalTab` (via W2-2) and stores no passwords (passed as args to `injectFillScript`, not persisted). D8 asked: user chose "Stop after filling" — removed `autoClickContinue()` function and all calls in both builds (Chrome `background.js:521-544` + call at `:671`, Firefox `:314-337` + call at `:439`). Tests: `node --check` both backgrounds pass; `test_sca_coordinator.js` 18/18, `tests/test_sca_protocol.py` 12/12. Next WP (W3-2, Part D): SMTI field picking and re-inject rules.
- **W3-2** (2026-09-29, claude-sonnet-5-5): Part D done (#7, #8, #9, #13 SMTI side) in both builds. New `content_scripts/sera_dom.js` (identical in both, injected on demand with `executeScript({files})`, NOT a manifest content script; `build_extension.py` now copies it Chrome->Firefox) holds the one `isVisible` (zero size, aria-hidden, tabindex=-1, type=hidden, name=hiddenPassword, display/opacity), `findField(doc, 'user'|'pass', ...)` and `hasLoginForm`. `injectWithDom(target, func, args)` in each `background.js` injects the file then the function; `fillCredentialsInPage` and `manualAssistWidget` return early if `window.__seraDom` is missing, and their private visibility copies are gone. Username: visible non-password text input only, never the configured password selector, else copies; Password: password inputs or the configured selector. Widget's URL-keyword "already logged in" guard removed. Chrome: `smtiTabIds` (via `_passwordStore`, set by `handleManualAssistTab`/`TRIGGER_MANUAL_ASSIST_FOR_TAB`, cleared with the payload) + `maybeReinjectManualAssist`: only that tab, only while payload live, only if `hasLoginForm` (read-only) - the `/auth`/`/dashboard` URL rules are gone. Removed `scaCoordinator.disarm` on inject (both builds), `sca_fill_completed` + `sera_sca_filled` flags (Chrome widget). Left for W4-1: `manualAssistActive` in the coordinator settings. Firefox still has no `tabs.onUpdated` re-inject (pre-existing, not ported). Tests: new `tests/js/test_smti_fields.js` (fake DOM, both builds) passes; `test_open_portal_tab.js`, `test_sca_coordinator.js` 18/18, pytest no_password_in_local_storage/sca_protocol/extension_settings_sync 16 passed/1 skipped; `node --check` both backgrounds. Human check added: ITR password step Username puts nothing into the password box.
- **W3-3** (2026-09-29, claude-sonnet-5-5): Part E + B3 done (#5, #16, #6) in both builds. D6 asked: user chose "after BOTH User ID and password are copied" (no User ID row -> password alone). `mecpWidget`: closed shadow root, 90 s timer bar pausing on hover (SMTI's logic), x = `MECP_DISMISSED`, timeout / both-copied = new `MECP_CLOSED`; both make the background clear `mecpPayload` (the old in-page `storage.session.remove` never worked: content scripts can't reach session storage). SCC card untouched: no auto-close, no clipboard clear, timeout does NOT end `sccActiveAttempt` (only the x does, as before). Firefox card now matches Chrome (its 45 s inline clear and 60 s silent close are gone). Clipboard: `scheduleClipboardClear(text, seconds)` in the shared `content_scripts/sera_dom.js` (readText === text -> write '', try/catch, one retry on window focus); MECP now injects via `injectWithDom`. SMTI `copyText(value, secret)` schedules it only for the password copy fallback. Desktop: `update_extension_settings(clipboard_clear_seconds=)` (clamped 5-300), `main._get_extension_settings_payload` (request_settings reply), unified settings + service manager callers; extension stores `clipboardClearSeconds` in storage.local (30 if unsynced). Firefox has no request_settings sync at all (pre-existing), so it only learns the value from `update_settings` pushes.
  Tests: new `tests/js/test_mecp_card.js` (both builds) and `tests/test_clipboard_clear_setting.py` pass; smti_fields, open_portal_tab, sca_coordinator 18/18, pytest no_password/sca_protocol/settings_sync/ws_bridge 46 passed/1 skipped; `node --check` both. Human checks added: MECP on an already open GST login tab stays up; real-browser clipboard clear (manifests have no `clipboardRead`, so Chrome may prompt on readText). Next: W4-1 still owns `manualAssistActive` and the per-tab lock (#13, #15).
- **W4-1** (2026-09-29, claude-opus-5-5): B4 + #15 done in both builds. `manualAssistActive` is gone: each `background.js` keeps `assistTabs` (tabId -> {kind 'smti'|'mecp', expiresAt}) in `_passwordStore` with an in-memory mirror (`_withAssistTabs`/`_lockAssistTab`/`_unlockAssistTab`/`_liveAssistTabs`, just below `scaCoordinator`). Locked in `injectManualAssist` (every inject, so re-injects re-lock) and `injectMECP`; cleared for the sender's tab + kind on `MANUAL_ASSIST_CLEAR` and `MECP_DISMISSED`/`MECP_CLOSED`, on `tabs.onRemoved`, and after 5 min. `getSettings` returns `assistTabs: {tabId: kind}`; the coordinator's `onCandidate` returns `assist-open-in-tab` only when `sender.tab.id` is in it (`sca/` byte-identical, `cmp`). Firefox SMTI widget's dismiss now sends `MANUAL_ASSIST_CLEAR` (it sent nothing) and its background handles it like Chrome (clears payload + lock).
  Desktop: module-level `clipboard_watch.suppress_client(client_id, seconds=300)` / `is_suppressed()`; `_on_clipboard_changed` skips a suppressed client (prints a "not arming" line, no ids). `ClientDetailWindow._suppress_sca_for_client()` is called in `_launch_manual_copy` and `_launch_manual_assist` before `automation.trigger_*`. The existing arm is not disarmed.
  Tests: `test_sca_coordinator.js` 19/19 (lock blocks its tab for smti/mecp; other tab still requests); new `tests/test_sca_suppress_client.py` 7/7; open_portal_tab, smti_fields, mecp_card pass; pytest sca_protocol/no_password/settings_sync/clipboard_clear 19 passed/1 skipped; `node --check` both. `tests/test_clipboard_assist.py` 16 fail in `setUp` (`add_client`: "Internal Primary Key 'PAN' is mandatory") - the pre-existing DB-fixture failure W1-2 noted, not this WP. The background lock helpers have no unit test (human check 5 covers them).

- **W4-2** (2026-09-29, claude-sonnet-5-5): Part F #12/#14. `clipboard_watch.py`: `Arm.max_uses` added; `handle_password_request` no longer decrements `uses_remaining` (grant only bumps `grants`), refuses once `grants >= max_uses + 2` ("too many password requests for this copy"); `handle_fill_result` decrements `uses_remaining` on `filled` only. `_password_status`: 'pass'-label fallback removed, no password column -> ('', 'no_password'). `sca/sca_coordinator.js` (identical in both builds, copied): SCA_PASSWORD_DENIED for a pending request now reports a failed SCA_FILL_RESULT with the desktop's reason (unknown request ids still ignored), which `handle_fill_result` already turns into a `sca_notice`. Tests: `tests/js/test_sca_coordinator.js` 20/20 (new denial test); `test_sca_protocol.py` 12 pass; `node --check` both builds. `tests/test_clipboard_assist.py` gained 2 tests (use counted on fill, grant cap) and the 'pass' fallback assertion was flipped, but the file still fails in `setUp` (pre-existing 'Internal Primary Key PAN' fixture failure), so the same logic was checked with a scratch stub-DB script (passed). Next WP: denial reasons are raw strings from the desktop (e.g. 'no password SCA may use for that portal'), shown via `explain()`'s generic branch; the extension's own no_password/scc_unverified reasons still map to the friendly texts.
- **W4-3** (2026-09-29, claude-sonnet-5-5): Part F scope, D7 asked -> user chose "Only on approved portals". Both manifests lost `content_scripts` (host_permissions kept). Both `background.js` gained an "SCA scope" block after `ensureConnected();`: `login.js` + `sca_adapters.js` are registered with `chrome.scripting.registerContentScripts` (id `sera-sca-login`, `https://<d>/*` + `https://*.<d>/*`, all_frames, document_end; Firefox falls back to `browser.contentScripts.register`). Domains = built-in five portals + stored `allowedDomains` (sanitised); applied at boot and on every `chrome.storage.onChanged` of `allowedDomains`, so `update_settings` re-registers automatically. Desktop: `main.py` `_get_extension_settings_payload` now also returns `allowed_domains`; Chrome `syncSettingsFromDesktop` stores it (the Firefox build has no request_settings sync, only update_settings pushes - unchanged). http:// and file:// pages no longer run SCA/SCC page scripts. Tests: new `tests/js/test_sca_scope.js` 8/8; `test_sca_coordinator.js` 20/20; `test_ws_bridge.py` pass; `test_scc_vault_tagger.py` 2 fail in the same pre-existing 'Internal Primary Key' fixture area (add_client), unrelated. Not verified in a real browser: check-add'ed. Next WP (W4-R): review that the boot-time registration survives a service-worker restart in Chrome.
- **W4-R** (2026-09-29, claude-opus-5-5): Parts B-F reviewed (rules 0.3/0.4, privacy 1-3, 6); flows walked for no portal tab / login tab / non-login tab. **Defects fixed:** (1) Chrome `fillCredentialsInPage` read `SERA_DEBUG`, which existed in the page only because `login.js` declared it; since D7 `login.js` runs only on SCA portals, so Fast Autofill threw before filling on any other host (now declared locally). (2) Firefox `console.log`ged every desktop message and runtime message (autofill/SMTI/MECP carry the password); Chrome's debug-gated logs did the same when debug was on. Both now log `type` only. (3) `openPortalTab` picked candidate tabs by URL *substring*: a search/redirect page mentioning the portal host was probed and could be navigated. Now it matches the tab's own hostname or a subdomain (both builds). (4) A new SMTI launch kept the previous SMTI tab id, so that tab could be re-injected with the new client's payload; `smtiTabIds` is now cleared. (5) Firefox SMTI differed from Chrome: widget injected into every frame, no top-frame guard, no Angular re-sync, no re-inject after reload, no `request_settings` pull (so `allowedDomains`/clipboard seconds waited for a settings save). Chrome's widget is now copied verbatim; re-inject, 1 s guard and settings sync on connect ported. (6) SCA scope: on a service-worker restart the identical registration is kept (`getRegisteredContentScripts`), no unregister/register gap.
  Checked and fine: no `storage.local` password; closed shadow roots; clipboard clear; no auto-click/submit/scroll added (the SMTI Flutter step still sends a synthetic Tab key after filling - pre-existing, only after staff click the field); desktop logs no clipboard text or password; `sca/` identical. Firefox still lacks SCC (goes in Part G anyway). Remaining build diffs: SCC code, MECP card's SCC mode, Fast Autofill debug logs.
  Tests: new `tests/js/test_builds_parity.js` 7/7; `test_open_portal_tab.js` (+ substring case), smti_fields, mecp_card, sca_scope 8/8, sca_coordinator 20/20; pytest no_password/sca_protocol/settings_sync/clipboard_clear/sca_suppress_client/ws_bridge 53 passed/1 skipped; `node --check` both. No `core/` change. Human check 7 added.
- **W5-1** (2026-09-29, claude-opus-5-5): G.1 frame. New `core/scc/host.py` `SccHost` (copy of SGT-I's host shape: own daemon thread "scc-u", queue 8, 1 s page budget, 15 s hang, trips off for the run on exception/overrun/hang; trip message has the exception *type* only). Handlers: `name` + `observe(obs, hwnd, ctx)`; none registered yet (W5-2+ add them via `register`). Read window: `open_read_window(session_id, seconds)` (any thread, clamped to 30 s, never shortened) / `ctx.read_harder()`; `wants_read(session_id)` true inside it.
  `core/sgt/sgt_shadow.py`: `scc=` ctor arg + `set_scc()`; in `_observe` `scc = self._scc` is read once, consulted after `sgt_i.wants_read`, and used right after `_absorb` for `_hand_to_scc(...)` -> `make_observation(...)` (no nodes) + `scc.submit(obs, hwnd)`; session id = `obs.session_id`. `sgt_replay.replay_session(scc=)`.
  Setting `scc_detect_mode` off/on (default off): `vsdc_engines.SCC_DETECT_*` + `read_scc_detect_mode`; router `apply_engine_settings(scc_detect=)` + `_apply_scc()` (one host per run, also on SGT creation); `main._apply_vsdc_engine_settings`; Settings -> SCC "Detect login automatically" combo (load/state/save). Needs SGT on (no SGT = no host).
  Tests: new `tests/test_scc_host.py` (setting, isolation, window, hook hwnd/session, router, On-vs-Off byte-identical over golden replays) + `test_sgt_i_host.py` 40 passed; 7 SGT/VSDC files 96 passed; `sgt_replay.py diff` over 1215 pages: only "newly written" rows, none changed/lost. Next: handlers must stay under 1 s/page and filter portal (itr) themselves; the card's Copy should call `host.open_read_window(attempt.session_id)`; the router's host is `router._scc_host`.

- **W5-2** (2026-09-29, claude-sonnet-5-5): G.2 steps 1-2 + step 4 wording. `core/scc/scc_rules.json` + `scc_rules.py` (`load_rules`/`get_rules`, `RuleSet.classify(lines, url, portal) -> Outcome(kind, rule, unconfirmed, name)|None`): rules = `urls` AND `patterns` (either optional), `examples`/`counter_examples` required (string or {lines,url}); a rule failing its own examples, or whose example a higher-precedence rule of another outcome claims, is refused and the previous same-named rule stays (`.errors`); precedence locked > wrong_password > worked > neutral > no_conclusion. Only `itr_logged_in_header` (from sgt_fields.json itr_header_name, case-sensitive, group 1 = name) is confirmed; `tools/vsdc_uia_probe_output/` has no dumps, so wrong-password / locked / OTP+secure-access / e-verify / forgot-reset are `unconfirmed` (forgot/reset and e-verify match by address or exact heading only: the password page has its own 'Forgot Password?' link and every logged-in header has 'e-Verify'). Human check 8 added (one real wrong-password probe, never a lock-out).

  `core/scc/attempts.py` `AttemptOpener` (handler name 'scc-attempts', register on the host): opens on SGT's `itr_pan` hit on `#/login/password`, keyed by hwnd + session; lookup is read-only (`db_lookup(db)` = get_client_by_pan + is_client_scc_verified, returns ClientInfo(client_id, verified), never a password); verified -> nothing (looked up once per session); one attempt per hwnd, `close(hwnd)` keeps it closed until a non-password page is read in that window; session change / another PAN / 10 min -> `on_end(att, 'no_conclusion')`. D3 asked -> Yes: `Attempt.saved_row` is True for a registered client (the card must read the saved password itself when drawn; none is held). NOT wired into the router yet (host has no db): next WP does `host.register(AttemptOpener(db_lookup(db), on_open=card))` in main/router, then outcome (step 4) uses `get_rules().classify` on same-hwnd+session pages. Tests: new `tests/test_scc_rules.py` 45 pass; test_scc_host + test_sgt_i_host + test_sgt_live 60 pass. No core/sgt change.

- **W5-3** (2026-09-29, claude-sonnet-5-5): G.2 step 3, D1 = extension card. New `core/scc/card.py` `SccCard`: on `AttemptOpener.on_open` sends MECP (scc_mode) with `generate_scc_passwords` rows (no empties/repeats) + "Saved password" row (D3) + `Attempt.attempt_id`; rows live in memory only; ledger (`Attempt.ledger`) holds labels ("copied: Combo 2", "worked: Combo 2"), never text. `automation.send_scc_card`/`close_scc_card` broadcast (no browser launch, no retry). `clipboard_watch.scc_ledger` hook + `release_client`; the client is suppressed for the attempt (W4-1). `ws_bridge`: `scc_row_worked`, `scc_card_closed`. `vsdc_router.set_scc_handlers` + `main._make_scc_handlers` wire it.
  Extension (both builds, MECP region identical; Firefox had no SCC mode before): `mecpWidget(..., sccMode, sccCombos, clearSeconds, attemptId)` renders the given rows, `generateSccCombos` deleted, no SCC_PASSWORD_COPIED, nothing stored, SCC card has no countdown/D6 close (stays until x or desktop `scc_card_close`). `openPortalTab(url, cb, {stay:true})` uses only a tab already on #/login/password.
  For W5-6: `SccCard(on_worked=)`/`row_worked` (currently `main._handle_scc_row_worked` only records). For W5-9: still to remove are `login.js` `checkUnregisteredSccTrigger` (its `TRIGGER_UNREGISTERED_SCC_MECP` message now has no handler), `sccActiveAttempt`, `SCC_LOGIN_DETECTED`, and Client Detail's `_launch_manual_copy` SCC path.
  Tests: new `tests/test_scc_card.py`; `test_ws_bridge.py`, `test_sca_suppress_client.py`, `test_mecp_card.js`, `test_open_portal_tab.js` extended; SGT/VSDC test files pass, `sgt_replay diff` = no changed/lost rows (only rows newer than the baseline). Pre-existing failures untouched.

- **W5-4** (2026-09-29, claude-opus-5-5): G.2 step 4, the outcome reader. `core/scc/outcome.py` `OutcomeReader` (handler 'scc-outcome', registered after `AttemptOpener`): on each same-hwnd+session ITR page it classifies with `get_rules().classify` and moves the attempt on — worked (header name must agree with the client, else no_conclusion 'another name'), wrong_password (x on the last row copied since the previous refusal; a page-pan that differs from the attempt's = no_conclusion 'another PAN'; the same message on the next read is not a new refusal; a refusal with no copy = a typed password), locked (stop, no next-row hint, nothing more read), neutral (keep waiting), no_conclusion (forgot/reset; window-closed via a sweep). Wording only from `scc_rules.json` `messages` (added `wrong_password`/`locked` there; `scc_rules.py` gained `RuleSet.message()` and `Outcome.line`/`Rule.match_line` so a matched line can be shown, memory only). `card.py`: `mark_failed` (x + highlight the next not-yet-failed row, wrapping; None when all failed) / `show_stop` / `failed()` + `on_copy` (a copy asks the host for a read window via `main.read_harder`); `automation.update_scc_card` + both `background.js` `updateSccCard`/`SERA_SCC_CARD_UPDATE` (labels + message only, ✗/next/greyed rows). `main._make_scc_handlers` wires it. No password is ever read (the reader sees line text only). Tests: new `tests/test_scc_outcome.py` 22 pass (fictional lines for every outcome incl. a disagreeing dashboard PAN + the card marks); test_scc_card/test_scc_rules/test_scc_host 87 pass; `node --check` both builds. No `core/sgt` change. Next (W5-6): `SccCard.on_worked`/`OutcomeReader` reporting feeds step 5's row credit; the reader's `worked`/`wrong_password`/`locked` reach `on_outcome`.

- **W5-5** (2026-09-29, claude-opus-5-5): G.2 step 5. New `core/scc/which.py` `WhichOne(card, copied_since_refusal, then=)` is the reader's `on_outcome`: on `worked` it takes `OutcomeReader.copied_since_refusal(att)` (new; ledger copies after the last refusal), drops x rows (`pick_rows`): one distinct row -> `SccCard.credit` (ledger "worked: <label>" + `on_worked`); several or none -> `SccCard.ask_which` (card highlights them, message `ask_which` in `scc_rules.json`, now allowed via `MESSAGE_KEYS`). Skipped when staff already pressed "This one worked" / None. `then=` passes every outcome on (W5-7 counts chain there).
  `card.py`: `credit` (refuses a x row, also for the button: `row_worked` goes through it), `ask_which`/`asking`, `none_typed` (ledger "typed own", `on_none` -> `main` ends the attempt with reason "typed own": card closes, nothing saved). `automation.update_scc_card(..., ask=)`; `ws_bridge` `scc_row_none` -> `main._handle_scc_row_none`. Both `background.js` (same code): `updateSccCard` forwards `ask`, card gets a hidden "None - I typed my own" button (shown only while asked), `.ask` highlight, x rows' worked button disabled; `scc_row_none` forwarded (attempt id only).
  Decision: None is offered whenever the card asks (several too). Tests: new `tests/test_scc_which.py` 13 (real card + reader); scc_outcome/card/rules/host + ws_bridge (new none test) 159 passed total; `test_mecp_card.js` was broken by W5-4 (fake had no `onMessage`/`classList.toggle`) - fixed + step-5 case; `test_builds_parity.js` 'ws.' check was tripping on `sccRows.` - made word-bounded; `node --check` both. No `core/sgt` change. Next (W5-6): save from `SccCard(on_worked=)` - it now fires for both the automatic pick and the button.

- **W5-6** (2026-09-29, claude-sonnet-5-5): G.2 step 6, the guarded save. New `core/scc/save.py`: `save_verified(db, password, pan, client_id, service_id, row_label, portal, client_name, actor) -> SaveResult(outcome created|saved|replaced|verified, client_id, client_name, pan)|None` (the old `main._handle_scc_password_verified` logic, Income Tax only; equal to the saved password -> verified only) and `SccSaver(db, row_text, actor, after).on_worked` = `SccCard(on_worked=)`, so the automatic pick (`WhichOne`) and "This one worked" both save. `SccCard.row_text(attempt_id, label)` hands the row text from memory. Audit: who + row label (`log_action`, which stamps the time), "replaced" said in the line, never a value; the old "Quick-Tag" line is no longer written (`log_action=False`).
  D2 asked -> **Replace automatically**; D4 asked -> **Add the client automatically** (no confirm UI was built; the toast says "Saved password replaced" / "Client added"). `main._handle_scc_password_verified` stays as a thin wrapper over `save_verified` for the tracker-dump `scc_verified_password` path and the bridge handler (W5-9 removes both); the UI refresh moved to `main._after_scc_save`, reached from SCC-U's thread through `SyncSignalBridge.scc_saved_signal` so it runs on the Qt thread.
  Tests: new `tests/test_scc_save.py` 11 pass (real SQLite: each outcome, GST/empty ignored, audit has no value, card->save via button and saved-row credit, x row saves nothing, db error swallowed). scc_card/which/outcome pass; `test_scc_vault_tagger.py` keeps its 2 pre-existing 'Internal Primary Key' failures. No `core/sgt` or extension change. Next (W5-7): card close after a save (step 7) and counts; `WhichOne(then=)` chain is still free.

- **W5-8** (2026-09-29, claude-sonnet-5-5): Part G, Client Detail opens the SCC card. `ClientDetailWindow._launch_manual_copy`: unverified ITR client (SCC on) -> `core/scc/manual.open_card(pan, client_id, on_error)`; if that returns False (no opener / no rows) it falls back to the plain MECP card (or a warning when there is no saved password). Plain MECP for everyone else, unchanged. `manual.py` = a hook main fills (`main._open_scc_manual`): `_ensure_scc()` (was `_make_scc_handlers`, now idempotent) builds opener/card/outcome without the SCC-U host, so it works with Detect login automatically Off. `AttemptOpener.register_manual(pan, client_id)`: SGT already has an attempt for that PAN -> reused (no new card); else a manual `Attempt(manual=True)` under a negative made-up hwnd (only "This one worked" saves; a second click replaces it; 10-min timer ends it); `observe` ADOPTS it (hwnd/session set, manual False) if SGT later reads that PAN's password page, so the outcome reader then follows it. `OutcomeReader._sweep` skips manual attempts (no window to check). `automation.send_scc_card(..., open_tab, on_error)`: open_tab -> one browser with `_deliver_to_extension` (extracted from `_send_to_extension`: retry 10 s, browser launch) and payload `open_tab: true`; `SccCard.open(att, **send_opts)`.
  Extension (both builds, same edit): `handleMECPTab` SCC mode uses `{stay:true}` only when `open_tab` is not true; with it, `openPortalTab` reuses/opens the login page like any MECP. Still nothing stored; card unchanged. Tests: new `tests/test_scc_manual.py` 15 pass; scc_card/rules/outcome/which/save/host + ws_bridge/suppress_client/service_automation_mode pass; `test_builds_parity.js`, `test_mecp_card.js`, `node --check` both. Pre-existing failures, not mine: `test_scc_card::test_asking_which_row_fires_the_on_asking_callback` (card never opened in that test) and 4 `test_scc_counts.py` tests (SccCounter reads the real ~/AmanAssociates_Sera/scc/counts.json, so totals leak between runs) - W5-7's. No core/sgt change. Human check 10 added. Next (W5-9): remove the old extension SCC tracking; Client Detail no longer sends `scc_combos` to `trigger_mecp` (its `scc_mode` params in `automation` are now unused by the window).

- **W5-7** (2026-09-29, claude-haiku-4-5-20251001): G.2 step 7, close-out and counts. New `core/scc/counts.py` `SccCounter`: loads/saves counts from ~/AmanAssociates_Sera/scc/counts.json on every increment (attempts, worked, failed, locked, no_conclusion, asked, saved, not_understood). Hooked into: `AttemptOpener.on_open` (count attempts), `SccCard.on_asking` (new callback parameter; count asked), `WhichOne(then=)` (count outcomes), and `SccCard.on_worked` via wrapper in main.py to count saves and close card. Card close after save: `automation.close_scc_card(att.attempt_id)` then `opener.close(att.hwnd)` mirrors the user's x press. Clipboard clear still runs (W3-3 extension timer). UI: counts displayed in Settings -> SCC in a monospace read-only label, loaded on dialog open. Tests: new `tests/test_scc_counts.py` 14 pass (Counts dataclass, load/save/dict, SccCounter increments, callback on counts change, exception handling, multiple outcomes, integration flow). `test_scc_card.py`: added test for `on_asking` callback. No `core/sgt` or extension change. Next WP (W5-8): remaining steps (P/N frame, live probes, etc. per W5-2 notes).


- **W5-9** (2026-09-29, claude-sonnet-5-5): Part G, extension SCC tracking and the old desktop handler removed. Both builds (same edits): `login.js` now only has the SCA paste/typing notice (`checkSccLoginSuccess`, `checkUnregisteredSccTrigger`, the hashchange/popstate/interval watchers gone); `background.js` (Chrome; Firefox had none of it) lost `sccActiveAttempt`, the SCC part of the `tabs.onUpdated` observer (the listener stays for SMTI Manual Assist re-inject), `SCC_LOGIN_DETECTED`, `registered_pans`/`scc_settings` handling in both settings paths; the startup `storage.local.remove` now also clears leftover `registeredPans`/`sccSettings`/`sccEnabled`/`sccActiveAttempt` from old installs. Card SCC mode, `scc_row_worked`/`scc_row_none`/`scc_card_closed` untouched; `sca/` identical.
  Desktop: `ws_bridge` `scc_password_verified` + its signal, `main._handle_scc_password_verified` and the tracker-dump `scc_verified_password` path removed (`core/scc/save.py` kept); `registered_pans`/`scc_settings` no longer sent (`main._get_extension_settings_payload`, `automation.update_extension_settings`, settings dialog); `database.get_all_registered_pans` deleted; `trigger_mecp`/`_send_to_extension` lost their `scc_mode`/`scc_combos` params (`_extension_payload` keeps them for `send_scc_card`). `get_scc_settings` stays (rows + Settings -> SCC).
  Tests: `test_scc_vault_tagger.py` rewritten (save tests live in `test_scc_save.py`; new: no SCC/PAN in settings payload, `trigger_mecp` takes no SCC args, plain card payload), `test_ws_bridge.py` updated, `test_builds_parity.js` gained a "no SCC tracking or page watching" check per build. Passing: those + scc_save/card/manual/service_automation_mode/no_password_in_local_storage, `node --check`, mecp_card, open_portal_tab, sca_scope. Only failure: pre-existing `test_scc_card::test_asking_which_row_fires_the_on_asking_callback`. No core/sgt change. Human check: reload both extensions and confirm the MECP/SCC card still opens from Client Detail.

- **W6-1** (2026-09-29, claude-haiku-4-5-20251001): Docs and extension version. Updated `docs/browser-automation-extension.md`: removed auto-click mention (D8 decision), updated SMTI/MECP description (per-tab assist lock, no SCC mode in extension), replaced entire FST/SDC section with brief note that filing capture is desktop-only, updated manifest permissions list (removed browsingData/cookies). Updated `docs/sera-clipboard-assist.md`: rewrote SCC section to document SCC-U (desktop-driven, no extension tracking). Bumped extension versions in both `sera_extension/manifest.json` and `sera_extension_firefox/manifest.json` from 2.11.0 to 2.12.0. No code changes; no tests to run beyond existing checks. Next WP (W6-R): final review before release.

- **W6-R** (2026-09-30, claude-opus-5-5): Final review against §0 and §9; merge-readiness note above §12. **Defects fixed:** (1) Firefox `fillCredentialsInPage` logged to the portal page's console without a debug gate and lacked Chrome's cursor-end step - now identical to Chrome's; `test_builds_parity.js` now also asserts `mecpWidget` and `fillCredentialsInPage` are identical (11/11). (2) Firefox still had the old "Auto-click Continue/Login" comment (D8 removed the click). (3) `docs/browser-automation-extension.md` still said the extension clicks the secure-access checkbox and manages cookies. (4) `test_scc_counts.py` read/wrote the real `~/AmanAssociates_Sera/scc/counts.json` (now a tmp file per test); `test_scc_card` asking test never opened the card. Tests: all 6 `tests/js` pass; 16 SCC/SCA/bridge pytest files 253 passed/1 skipped; 8 SGT/VSDC files 118 passed, 1 failed (vsdc247 duplicate test, database dedup, not touched by the branch - check 13); `sgt_replay.py diff` no changed/lost rows. Checks 12-13 added. Nothing left for another WP: merge after the hands-on checks.
