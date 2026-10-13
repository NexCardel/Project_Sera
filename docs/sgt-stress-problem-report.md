# SGT under real-life load: problem report

Written 2026-10-14 for **Fable 5.1 (cloud)**. Fable's job is to design the solution. This report
holds the problem only: what SGT is, what has gone wrong, what the code does today, and every
scenario in real tax-portal work that can stress SGT. It proposes no design. Where it mentions a
possible direction, that is context, not a decision.

**The owner's goal, in their words:** SGT must be 100% (not 99.99%) and never miss a single
capture, including under the heavy real-life work that broke it this week.

**What Fable can and cannot do:** Fable works in the cloud. It can read this repository. It cannot
run Windows, UI Automation, a browser, the tax portals, or the office PCs. It cannot see the private
logs or the page corpus, because they are kept off the repo on purpose (client data). Every
measurement in this report was taken on a Windows PC and is quoted with its source. Anything that
needs a measurement Fable cannot take must go in the solution as a step for the owner, or for a
local agent, to run.

**The repository is public.** The solution must not put client data, PC names, alert topics or
secrets in the repo.

---

## 1. SGT in one page

SGT (Sera Global Tracker) is Sera's only live capture engine since 2026-09-26 (blueprint section 13).
The older engines (VSDC = OCR crosshairs, VSDC-X = UIA crosshairs, VSDC 24/7 = OCR submission
safety net) are switched **off** on every PC. SGT running alone is the whole capture system, with no
second engine behind it.

It watches the two tax portals (GST: `*.gst.gov.in`; Income Tax: `*.incometax.gov.in`) passively,
from outside the browser. It reads the page's text through Windows UI Automation (UIA), the same
OS channel screen readers use. It turns those lines into the following, using config specs
(`core/sgt/sgt_fields.json`), not per-page code:

- a **client profile** (PAN, GSTIN, name, and so on). One sighting of the PAN/GSTIN attributes the
  session.
- **datasets** (form + period + submit status). Status climbs a 4-level ladder: 0 Not submitted,
  1 Draft, 2 Submitted, 3 Verified. It only ever promotes.
- **identifiers** (GST ARN, ITR acknowledgement number). An ARN or ack is the strongest evidence a
  submission happened.

Rows go into the office-wide tracker database (shared across PCs by Sera Sync) as they change. The
HUD pill shows a short toast for each real capture.

### The capture loop (one thread, serial)

`core/vsdc/vsdc_worker.py` runs `VSDCRouter.evaluate_tick()` every **0.35 s**, with 15 ms "burst"
ticks for 10 ticks after a URL change. One tick, in order (`core/vsdc/vsdc_router.py`):

1. Send one waiting SGT tracker row, if any (one per tick, `evaluate_tick` top).
2. `GetForegroundWindow()`. Only the window in front is ever looked at. A non-browser, untitled
   window ends the tick.
3. Read the address bar through UIA (`extract_browser_url`, its own UIA object, **no timeout
   guard**).
4. Scope gate: the host must be a portal host. If the address bar is unreadable, the window is
   trusted on its title for at most `_SCOPE_GRACE_SEC = 8 s` after the last verified read.
5. (Old engines, off on live PCs.)
6. `_run_sgt`: the selected browser tab's id (`vsdc_uia_text.selected_tab_id`, 1 s timeout).
   Then a screenshot of the window (`_capture_window`), which only feeds a 32x32 change-gate hash.
   Then `SgtShadow.observe(...)`.
7. Inside `observe` (`core/sgt/sgt_shadow.py` `_observe`): pick or create the session for this
   window/tab/portal. Then the change gate: read the page only if the URL changed, the frame hash
   changed, or `REREAD_AFTER_SEC = 15 s` passed. Then **one full UIA read of the page**
   (`vsdc_uia_text.read_page_text`, 3 s timeout). Then resolve specs, update profile/datasets,
   queue rows, and snapshot state to disk (at most every 2 s).

### Measured costs (Windows, real Edge, from the blueprint and the overhaul report)

| What | Cost | Source |
| :--- | :--- | :--- |
| Normal portal page, full UIA read | ~41 ms | blueprint section 7, memory 2026-09-21 |
| Heavy page (300-row table), full UIA read | ~935 ms | same |
| Same heavy page, single-call cached read (`core/sgt_i/uia_nodes.py`, not wired into capture) | ~530 ms (927 to 530) | `docs/sgt-overhaul-report.md` W2-1 |
| Resolver, 20 specs | 2-35 ms | blueprint section 7 |
| Change gate (screenshot + 32x32 hash) | ~50 ms | blueprint section 7 |
| Tab id with tab elements cached | 1-3 ms; a fresh tab-strip search 30-120 ms, once 545 ms | `vsdc_uia_text.selected_tab_id` docstring |

No one has measured a real GST B2B invoice page with hundreds of rows, a page that has been open
for hours, or a browser with many tabs. These are the pages that failed (section 3).

### How a UIA read works today (`core/vsdc/vsdc_uia_text.py`)

- One persistent COM worker thread (`ThreadPoolExecutor(max_workers=1)`). It is initialised with
  `comtypes.CoInitialize()`, which is **single-threaded apartment (STA)**, and it never pumps
  window messages.
- `read_page_text`: `ElementFromHandle(hwnd)`, then `FindAll(Descendants, ControlType=Document)`
  with an offscreen filter. Then, for each Document, `FindAll(Descendants, TrueCondition)` and then
  **element by element**: `GetElement(i)`, `CurrentName`, `CurrentControlType`, and for value
  controls `IsPassword` + `ValuePattern`, plus selection/toggle state when asked. That is about
  3 or more cross-process calls per element. Each call is answered by the browser's own UI thread.
  So read cost grows with page size and with how busy the browser is, not with Sera's PC speed.
- `_run_with_timeout` (rewritten 2026-10-13, released in v2.12.10):
  - The caller waits up to `timeout_sec` (3 s for page reads, 1 s for the tab id).
  - If the previous read is still running and has run for less than `SLOW_GRACE_SEC = 20 s`, this
    call is skipped as **busy**.
  - If it has run for 20 s or more, its worker is abandoned and replaced.
  - Five stuck workers at once pause UIA for 5 minutes.
  - Everything is logged to `~/AmanAssociates_Sera/logs/vsdc.log` (`core/vsdc/vsdc_diag.py`): a
    once-a-minute heartbeat with read counts, slowest/last ms, late, busy, abandoned and stuck.
- Before v2.12.10, any read still running at the next call counted as hung, the count never went
  down, and after 5 of them **UIA was switched off for the rest of the run**, silently.

### What SGT does with a read

`sgt_shadow._observe` (around lines 608-660):

- `res.get("busy")`: the page is treated as not read. The hash and read time are restored, so the
  next tick asks again.
- Fewer than `MIN_UIA_LINES = 3` real lines: the page is **blind**. On the GST portal
  (`NO_OCR_PORTALS`), a blind page is simply skipped. OCR was disabled there on 2026-10-06 because
  canvas OCR cleared values; see `docs/sgt-gst-fixes-plan.md`. The owner lifted that ban on
  2026-10-14 (constraint 7); the code has not changed yet. On Income Tax, OCR of the screenshot
  is used instead.
- **A read that times out returns an empty result, not "busy".** SGT counts it as a blind read and
  records the frame hash as read. The read itself keeps running on the worker and its result is
  thrown away when it finishes (`_timed` only counts it as "late").

### Sessions (`sgt_shadow.py`)

- One session per window, and since 2026-10-13 per browser tab (tab id = UIA RuntimeId of the
  selected TabItem). Sessions of a window that show another portal or client are put on a shelf,
  not ended.
- A session ends on a URL containing a `login` keyword (when it has content), on `logout`, on idle
  (`IDLE_END_SEC = 2 h`), or when the window closes.
  - After an idle end, a session with a PAN/GSTIN is **parked** in memory. The same window can
    resume it for 4 h (`RESUME_WINDOW_SEC`). Parked sessions are not persisted.
- Contradiction: another client's PAN/GSTIN on the page shelves the session and starts a new
  "strict" one.
- Crash safety: every session with content is snapshotted to `sgt_shadow/sessions_state.json`
  (atomic, fsync, at most every 2 s) and recovered at start-up. The outbox of rows waiting to be
  sent is in memory; recovery re-queues rows from the recovered sessions.
- The router keeps its own per-window state and prunes a window idle for
  `_SESSION_IDLE_TIMEOUT_SEC = 2 h`. Pruning calls `sgt.end_session(hwnd, "window closed")`, which
  ends every session of that window **without parking**.

---

## 2. Non-negotiable constraints (owner decisions: the solution must respect all of them)

1. **Passive only.** Never click, type, fill, dismiss, scroll, focus, or otherwise act on a portal
   page or the browser to help capture. The owner's legal/ToS review covers observing the screen
   and the accessibility tree only. "Click Enable accessibility" and "dismiss the modal" are out.
2. **No browser-side tracking.** The extension does no page/URL watching of any kind (user rule
   2026-09-28). No content scripts, no DOM access, no network interception (SAD was retired for
   ToS risk).
3. **No AI anywhere.** No model, no LLM, not even offline drafting (section 14 rule).
4. **Config-driven, "fix the kind, not the case".** No per-page or per-portal code shaped around
   one screen. Portal specifics live in `sgt_fields.json`. One rule for every place.
5. **One sighting of the PAN/GSTIN attributes at once**, and SGT never drops a dataset. If the
   client cannot be named, the row is written unattributed and the phone alert fires. **Never
   borrow identity across rows or PCs** (the 2026-10-13 borrowed-client incident).
6. **Status only promotes.** A demotion is never applied on its own; the user is asked.
7. **OCR is allowed, on every portal including GST, if it gives the best solution** (owner decision
   2026-10-14; this replaces the 2026-10-06 "no OCR on GST" rule). Today's code still skips blind
   GST pages (`NO_OCR_PORTALS`). The lesson behind the old ban must still be honoured. On the GST
   dashboard, a full-page canvas OCR read the label as `Period •`, and those reads cleared values
   UIA had just set (`docs/sgt-gst-fixes-plan.md`). So an OCR reading must never overwrite or clear
   what a UIA reading established. How OCR and UIA readings are combined is for Fable to design.
8. **HUD pill: no persistent "watching" indicator.** It appears only on an event, briefly. A
   "capture is failing" alert counts as an event; a permanent status light does not.
9. **Privacy.** Logs never hold page text, values, names, PAN/GSTINs, or links outside the portals.
   UIA must never read a password field's value (`IsPassword` checked first). The corpus stays off
   the repo.
10. **Scope.** Only the allow-listed portal hosts are ever read (`core/vsdc/vsdc_scope.py`,
    `vsdc_scope.json`). TRACES (Flutter, canvas, UIA-blind) is out of scope today.
11. **ERI / multi-client logins are out of scope.** One client per portal login.
12. **Process.** SGT changes are built from a blueprint in `docs/`, as ordered work packages, each
    run as its own session, with the owner deciding open questions. Any change must pass
    `tests/test_sgt_replay.py` and the full suite (live T/F/S plugin; see `CLAUDE.md`).

---

## 3. What has actually gone wrong (field evidence)

| Date | PC / workflow | What happened | What is known |
| :--- | :--- | :--- | :--- |
| 2026-10-11 and 12 evenings | One office PC, normal GST work | Stopped capturing anything on the GST portal (even the welcome page) 3-3.5 h into the evening, both nights. Other PCs fine. | Code reading: the pre-v2.12.10 "5 slow reads = UIA off forever" rule. Fixed in v2.12.10. That PC's logs are not reachable until 2026-10-15, so not yet confirmed. |
| 2026-10-14 | Same PC, on v2.12.10 | Did **not** lose capture. | Consistent with the fix. One day is not proof. |
| 2026-10-14 | Two other office PCs, staff entering **B2B invoices by hand for 4-5 hours, switching screens quite fast**. One PC is powerful. | Lost capture ability. | Probably still on v2.12.9: they run all day and the updater only applies on restart. Not yet confirmed. The fast PC failing too points at the browser side, not Sera's CPU. |
| 2026-10-13 | PC1 GST submission | Row briefly showed under the client open on PC2 | Borrowed-client bug (tracker proximity across PCs). Fixed on a branch, see memory. Identity, not missing. |
| 2026-09-22 to 10-06 | GST, 4 clients | June row rewritten to a later period; "Verified without ARN" rows | Dashboard tile/period read as dataset evidence. Fixed (`docs/sgt-gst-fixes-plan.md`). Wrong, not missing. |
| Sept GSTR-1 | Staff off-portal for over 20 min | Success page captured with no client (identity lost at idle end) | Idle end now 2 h + resume parking (`docs/sgt-idle-resume-plan.md`). |

"Lost capture ability" from the owner's point of view means rows stop appearing in the tracker,
and nothing tells anyone. **Silence is the worst part.** Each incident was found by a human days
later.

---

## 4. Weak points found in today's code (confirmed by reading, not yet by field logs)

Each one is a *kind* of failure. The scenarios in section 5 point back to these by number.

- **W1. A slow page can be permanently invisible.** A read over 3 s returns empty, and SGT records
  the page as read and blind (GST: skipped). The real result arrives later and is thrown away. The
  next attempt is equally slow, so a page that always takes more than 3 s is never captured.
- **W2. One reader, serial, and "busy" loses pages.** While an abandoned slow read is still running
  (up to 20 s), every tick on every page is skipped as busy, and the tab-id read is skipped too.
  A page the user shows for less than that time (an acknowledgement, a toast, a success modal) is
  never read if they move on. Fast screen switching on heavy pages is exactly this.
- **W3. Cost scales with page size and browser load, not PC power.** There are about 3 or more
  cross-process calls per element, all answered by the browser's UI thread. Huge GST tables and
  long-lived pages make every read slower. A faster Sera PC barely helps.
- **W4. Time-dependent degradation is possible and not measured.** These are candidate causes,
  none confirmed:
  - The browser's accessibility tree grows on a single-page portal open for hours.
  - COM proxies or element references build up.
  - The STA worker never pumps messages, which goes against Microsoft's guidance for UIA client
    threads (to verify).
  - Abandoned worker threads stay alive inside wedged calls.
  - Python object churn from tens of thousands of COM wrappers per read.
- **W5. The address-bar read has no timeout.** `extract_browser_url` runs on the capture thread
  with a UIA object created on the **GUI thread** (`VSDCRouter.__init__` runs in
  `VSDCWorker.__init__`, constructed from `main.py` on the main thread). A hang there freezes the
  whole capture loop. The stall watch only logs it.
- **W6. A tick is torn.** The URL, tab id, screenshot and page text are four separate reads at
  four moments. A tab switch, navigation or modal between them can pair one page's text with
  another page's URL, tab or session (mis-attribution), or pair the gate hash with the wrong
  content.
- **W7. Foreground-only.** Only the window in front is read. Evidence on a second monitor, or in
  a window that loses focus within one tick plus one read, is never seen.
- **W8. The change gate is a tiny screenshot hash.**
  - Typing, a blinking caret, spinners and countdown timers change it every tick, so every tick
    becomes a full read and the reader stays saturated.
  - A real text change too small to move the 32x32 hash waits up to 15 s (`REREAD_AFTER_SEC`).
  - A screenshot of an occluded or minimised window is meaningless.
- **W9. Identity loss paths.**
  - The router's 2 h prune ends a window's sessions as "window closed" with no parking, for
    example when the user works in another browser window for 2 h.
  - Parked sessions live in memory only, so an app restart or update loses them.
  - A login keyword in a URL ends a session that has content.
- **W10. No end-to-end guarantee signal.** Nothing checks "the user was on an in-scope portal page
  and SGT got no readable text for N seconds". Nothing tells the user or the office. The
  heartbeat log exists only on v2.12.10 and later, and only on disk on that PC.
- **W11. Old builds keep running.** PCs that run all day never apply an update. A fix can exist for
  days without reaching the PC that needs it, and nobody can see which PC runs which build.
- **W12. SGT is the only engine.** There is no independent second witness for a submission on
  GST. VSDC 24/7 (OCR) is OFF and SGT's own OCR fallback is disabled on GST in code. Since
  2026-10-14 OCR is allowed again (constraint 7), so the only reason left is that nobody has
  rebuilt it.

---

## 5. Stress scenarios: the full catalogue

Each scenario has an id, what happens in real use, what SGT experiences, and today's handling:
**OK** (handled and tested), **Partial**, **Gap** (known to fail or not handled), or **Unknown**
(needs measurement or field data). Portal behaviour is described from general knowledge of
the GST and Income Tax portals. Only the items marked so have been confirmed live in this project.

### A. Read load and timing

| Id | Scenario | What SGT experiences | Today |
| :--- | :--- | :--- | :--- |
| A1 | GSTR-1 B2B / CDNR / B2CL invoice entry for 4-5 h: add invoice form, save, the table grows to hundreds of rows, edit, delete, page through | Every save changes the screen; the page has thousands of UIA elements; reads approach or exceed 3 s | Gap (W1, W2, W3) |
| A2 | Typing into invoice fields (GSTIN, invoice number, values, rates) | Frame hash changes every tick, so a full read is attempted every 0.35 s, the reader is saturated, and every other page waits as busy | Gap (W8, W2) |
| A3 | Rapid screen switching (Alt+Tab between browser and Excel/Tally/PDF, Ctrl+Tab between tabs, clicking through tiles) at under 1 s per screen | Reads started on screen X finish after the user is on screen Y; busy skips; torn ticks | Gap (W2, W6) |
| A4 | Very large read-only pages: GSTR-2A/2B views, ledgers (cash, credit, liability), HSN summary, "View filed returns" lists, ITR prefill schedules, 26AS-like tables inside e-filing | Read time of seconds | Unknown (never measured live) |
| A5 | A page open for hours (a GSTR-1 section, returns dashboard) | Tree may grow; reads slow down over time | Unknown (W4) |
| A6 | Many tabs (10-40) and several browser windows open all day | Bigger tab strip (tab-id search), more browser memory, slower UI thread | Unknown |
| A7 | The browser itself is slow: portal JS busy, page loading, the "Please wait" overlay, a PDF generating | UIA calls queue behind the browser's UI thread; reads late or wedged | Partial (busy/abandon logic, but W1) |
| A8 | Sera's own other work on the same process (Sera Sync digests, tracker window refresh, Excel export, SDIS recorder, SGT-I, SCC-U, autofill) competing for the Python GIL and the UIA worker | Capture ticks slow; SGT-I/SDIS add their own UIA reads on the same single worker | Unknown |
| A9 | Low-end PC under memory pressure (browser + Excel + Tally + WhatsApp Desktop) | Everything slower; paging | Unknown |
| A10 | Constantly animating pages (spinners, marquee notices, countdown timers, carousels on the portal home) | The change gate never settles, so a read every tick | Partial (title timer stripped for the URL only) |

### B. Torn ticks and races

| Id | Scenario | What SGT experiences | Today |
| :--- | :--- | :--- | :--- |
| B1 | User switches tab between the tab-id read and the page read | Client A's tab id with client B's page text | Gap (W6) |
| B2 | Navigation (SPA route change) between the address-bar read and the page read | Page text of the new route under the old URL, or the reverse; URL-scoped rules (dashboard skip, strict block) misapply | Gap (W6) |
| B3 | Foreground window changes mid-tick | The read uses the hwnd from the start of the tick, but the screenshot may show something else | Partial |
| B4 | A modal opens or closes mid-read | Lines from both the page and the modal, or half of either | Unknown |
| B5 | A read result arrives after the page it belongs to is gone | Today it is dropped (W1). If it were kept, it would need provenance to be safe | Gap |
| B6 | Two windows on two different clients, user alternating between them every few seconds | Each switch can start a slow read the next switch makes stale | Partial (per-window sessions) |

### C. Short-lived evidence (the submission moment)

| Id | Scenario | What SGT experiences | Today |
| :--- | :--- | :--- | :--- |
| C1 | GST success message with the ARN ("filed successfully", ARN) shown as a banner/toast that fades in a few seconds | Must be read inside that window, even while the reader is busy | Gap (W2) |
| C2 | The portal moves on automatically after success (redirect to dashboard, refreshed tiles) | Success page visible for a moment only | Gap (W2) |
| C3 | The user clicks OK or closes the success modal immediately, or presses Back | Same | Gap (W2) |
| C4 | ITR success page ("submitted and verified", ack number) followed at once by downloading the ITR-V/ack PDF, which opens in a new tab or the PDF viewer | Focus moves to the PDF tab; the PDF viewer is a different document type | Partial (ack-carries-its-date rule; PDF viewer behaviour unknown) |
| C5 | Evidence appears only in a downloaded file (acknowledgement PDF, challan PDF, filed-return PDF) | Never on screen as text | Gap (out of reach today; Downloads watching is an open owner decision, blueprint section 12 B2) |
| C6 | Evidence arrives outside the PC: the ARN by SMS/email; e-verification done later by the client on their phone (Aadhaar OTP / net banking) | Sera never sees it | Gap: physically unobservable here. Only catch-up when status is later seen on a portal page |
| C7 | UIA toasts / live regions / role=alert that Chromium exposes only as events, or for a moment | `core/sgt_i/uia_events.py` caught 4 of 13 in a live test (2026-10-01); every toast shown with a modal was missed | Gap (events unreliable, see memory "snapshot-on-change") |
| C8 | Success shown while the browser is not the foreground window (second monitor, or the user already switched to Excel/WhatsApp during the server wait) | Foreground-only, so never read | Gap (W7) |
| C9 | The submission happens near midnight and is read after it | ITR ack carries its own date (DDMMYY) | OK (`date_is_today` allows plus or minus 1 day) |

### D. Navigation topology (how users move through the portals)

| Id | Scenario | What SGT experiences | Today |
| :--- | :--- | :--- | :--- |
| D1 | GST is a single-page app: one URL, many screens ("one link, many screens"); every client's welcome page has the same URL | URL cannot tell screens or clients apart | Partial (per-tab sessions; class-diff work in pre-dev) |
| D2 | Back / Forward / refresh / F5 in the middle of a form or after submit (re-showing an old success page) | An old ARN page re-read: duplicate, or attributed to the wrong time or client | Partial (status only promotes; datasets keyed) |
| D3 | Opening links in new tabs (Ctrl+click, middle-click), the portal opening its own new tabs or popups (track status, payment, downloads, "view" links), duplicate tab | The new tab joins the session in view unless its first page names another client | Partial (new 2026-10-13 tab logic; popup windows may have no normal address bar) |
| D4 | Dragging a tab out to a new window, or merging windows | Tab id stable? Session keyed by hwnd plus tab, and the hwnd changes | Unknown |
| D5 | Bookmarks and deep links straight into a return page, skipping the welcome/dashboard pages that carry identity | First page has no PAN/GSTIN header | Partial (the GST header line carries the GSTIN on logged-in pages; not every page) |
| D6 | Restored session after a browser crash/restart, with 15 portal tabs restored at once | Many tabs, some logged out, some stale | Unknown |
| D7 | Payment flow: GST create challan, bank gateway (out of scope host), back to the portal | Out-of-scope pages in between; the session must survive | Partial (shelf/scope grace) |
| D8 | Net-banking e-verify for ITR: redirect to a bank site, then back with the ack | Same | Partial |
| D9 | Offline utility / JSON upload filing (ITR, GST offline tool upload) | The submission page is short and mostly upload widgets | Unknown |
| D10 | DSC signing: emSigner / token PIN dialog takes the foreground, a Windows-level window | Browser not in front during the signing; success appears after | Partial (C8 risk if the user does not come back) |
| D11 | EVC OTP: the user goes to email/phone for the OTP, then returns | Focus leaves the browser for minutes | Partial |
| D12 | Browser in full-screen (F11), kiosk, "open as app", or a popup window without an editable address bar | Address bar unreadable, so scope rests on the title for 8 s only, then nothing is read | Gap (W-scope: `_SCOPE_GRACE_SEC`) |
| D13 | The user types in the address bar or the omnibox drop-down is open | The address bar value is typed text, not the page URL | Partial |
| D14 | Pages inside iframes / embedded viewers / extra Documents (Edge sidebar, Copilot, PDF viewer, translate bar) | More than one Document; the wrong one read, or lines mixed | Unknown |
| D15 | Portal maintenance pages, "Something went wrong", session-expired pages, captcha pages | No dataset; must not end or poison a session | Partial |

### E. Identity and session boundaries

| Id | Scenario | What SGT experiences | Today |
| :--- | :--- | :--- | :--- |
| E1 | Back-to-back clients in one tab: logout, login as the next client, file, repeat, 20-40 times on a due date | Every boundary must be exact; one slip puts client A's filing on client B | Partial (login/logout keywords, contradiction rule) |
| E2 | Client switch without logout (the portal's session expires, the user logs in as someone else in the same tab) | URL keyword `login` should end the session | Partial |
| E3 | Same client in two tabs or windows at once | Two sessions, one client | Partial |
| E4 | Two clients side by side in two windows or browsers (Edge + Chrome) | Per-window sessions | OK (tested `test_two_gst_clients_in_two_tabs_never_end_or_mix`) |
| E5 | **B2B pages are full of OTHER parties' GSTINs** (recipients, suppliers in 2A/2B) | The GSTIN spec takes the header line only ("NAME GSTIN"), but a table row printed the same way could look like a client and trigger a contradiction, splitting the session | Partial (header-only spec + strict block); never tested on a real B2B page |
| E6 | Long off-portal pause (calculation in Excel, a phone call, lunch) for over 2 h, then submit | Idle end + parking (4 h) if the same window resumes. Router prune (W9) and restarts lose it | Partial (W9) |
| E7 | Portal session timeout (GST/ITR auto-logout after inactivity), then re-login as the same client | Logout keyword ends the session; a new session must re-identify | Partial |
| E8 | The identifying page is never shown in this session (deep link, D5) and the success page has no PAN/GSTIN | Row unattributed + phone alert | OK by rule (never dropped), but a gap for "100%" attribution |
| E9 | The Sera app restarts mid-session (update, crash, Windows restart) | Crash snapshot restores sessions; parked sessions lost | Partial |
| E10 | The same client filed from two PCs on the same day | Two PCs' rows for one dataset; merge rules | Partial (canonical key + promote-only) |
| E11 | A representative or CA login viewing several clients' data | Out of scope (ERI), but may happen | Out of scope; must not mis-attribute |

### F. Portal-side behaviour

| Id | Scenario | What SGT experiences | Today |
| :--- | :--- | :--- | :--- |
| F1 | Due-date rush: the portal is very slow, pages half-load, errors, retries | Partial pages; slow browser; many retries | Unknown |
| F2 | Submit pressed, the portal says "in progress, check later", and the ARN appears only later in Track Return Status / the dashboard | Submission evidence is delayed and on another page | Partial (status from evidence; dashboard tiles no longer prove filing, by design) |
| F3 | Duplicate submission attempt, "already filed" message | Must not create a second dataset or demote | Partial |
| F4 | Filing errors after submit (validation failures, "error in processing") | Must not promote | Partial (failure wording vetoes) |
| F5 | The portal changes its wording, layout or labels (a new release) | Specs stop matching; capture quietly drops | Partial (`sgt_health.py` portal-change watch; not an alert to the office) |
| F6 | The portal changes hosts or subdomains | Scope gate refuses, so nothing is read | Partial (tripwire: one HUD prompt) |
| F7 | The portal switches a page to canvas rendering (as TRACES did, Flutter) | UIA blind | Gap on GST (its OCR fallback is off in code; OCR is now allowed); Partial elsewhere |
| F8 | Multi-step filings across days (save a draft today, file tomorrow) | Dataset at Draft, then later Submitted | OK by design (status ladder) |
| F9 | Hindi language toggle on the portal | Labels change language | Unknown |

### G. Browser and Windows environment

| Id | Scenario | What SGT experiences | Today |
| :--- | :--- | :--- | :--- |
| G1 | Window occluded (behind another window) or minimised | Chromium builds no tree (0 lines), Firefox marks the Document offscreen; measured 2026-10-04 for test-launched browsers | Gap (only matters when not foreground, see W7) |
| G2 | Browser auto-updates (Chrome/Edge every few weeks): UIA tree shape, names or naming change; native UIA vs IAccessible2 bridge | Specs or tab-strip logic silently stop matching | Unknown; no canary |
| G3 | Chromium turns its accessibility support off/on based on detected assistive-technology use (behaviour to verify), so the first read after a quiet period is partial | Blind or short reads after idle | Unknown |
| G4 | Edge "sleeping tabs" / Chrome "Memory Saver" discarding background tabs; tab reloads when shown | Page reloads, maybe to a logged-out state | Unknown |
| G5 | Multi-monitor, different DPI per monitor, browser zoom 67-150% | Screenshot gate differs; UIA unaffected | Partial |
| G6 | Screen locked, PC asleep, RDP minimised or disconnected | Foreground is the lock screen; capture blank; submissions cannot happen anyway, but sessions age | Partial |
| G7 | Firefox vs Chromium tree differences (labels, selects, offscreen marks) | Handled in the reader for known cases | Partial |
| G8 | Brave/Opera/Vivaldi or a browser outside `BROWSER_EXE_NAMES` | Never read | Gap if used |
| G9 | Enterprise policy or flags disabling renderer accessibility | Blind | Unknown |
| G10 | Antivirus or Windows updates pausing the process; Windows restart overnight with Sera queued rows | Pending rows | Partial (snapshot) |

### H. Sera's own process

| Id | Scenario | What SGT experiences | Today |
| :--- | :--- | :--- | :--- |
| H1 | Capture thread stalls (W5 hang, an exception loop) | Nothing captures; `vsdc.log` says "STALLED" and dumps stacks | Partial (detected on disk only, nobody told, no self-recovery) |
| H2 | UIA worker wedged, paused for 5 min | Up to 5 min with no reads on GST | Partial |
| H3 | Rows queued but not yet written to the database when the app quits or crashes | Outbox in memory; re-queued from the snapshot | Partial (verify every path) |
| H4 | Database busy/locked (Sera Sync writing, another thread) when a row is written | Must retry, never drop | Unknown (`main._process_extension_result` path) |
| H5 | Sera Sync slow or a PC offline: rows reach the other PCs late | Not a miss, but the office sees it as one | Partial |
| H6 | Disk full, log rotation, a corrupt state file | Snapshot write fails | Partial (`.prev` copy) |
| H7 | Settings changed mid-session (SGT mode switched, engines toggled; settings sync office-wide) | Sessions ended on a mode change | Partial |
| H8 | Clock jumps (NTP correction, manual change, DST is not used in India) | `time.time()` used for sessions; idle/parking maths | Unknown |
| H9 | A memory leak over a 10-12 h day | Slower GC, a slower process | Unknown (`memory.log` exists) |

### I. Fleet and deployment

| Id | Scenario | Today |
| :--- | :--- | :--- |
| I1 | PCs run for days without restarting, so updates never apply (the two PCs on 2026-10-14) | Gap (W11) |
| I2 | Nobody knows which PC is on which build, or which PC is blind right now | Gap (W10) |
| I3 | A PC whose specs, scope file or alert config differs from the others (alert topic not copied to every PC) | Partial |
| I4 | A new staff member's PC, a new browser, a new monitor layout | Unknown |

### J. Real-life day profiles (the loads the solution must survive together)

These combine the scenarios above. Numbers are the owner's descriptions or reasonable office
estimates, **not measurements**. The solution should state which numbers it needs measured.

1. **B2B marathon:** one GST client, GSTR-1 invoice entry for 4-5 h. Hundreds of invoices, each
   about 10 fields typed, saves every 30-90 s, switching to Excel/Tally/the invoice PDF every few
   seconds. It ends with generate summary, preview, file with EVC/DSC, ARN. (A1, A2, A3, A5, C1,
   D10, D11, E5, E6)
2. **Due-date rush:** 20-40 GST clients back to back in one tab, GSTR-3B each: login, dashboard,
   3B, payment (bank gateway), offset, file, ARN, logout, in 5-10 minutes each. The portal is slow
   and erroring. (E1, F1, F2, D7, C1, C2)
3. **Parallel filing:** two or three browser windows, each a different client, the user alternating
   while each portal is "processing". (B6, E4, C8, A6)
4. **ITR season:** many ITRs per day. Long schedule editing, validation, e-verify via Aadhaar OTP or
   net banking redirect, ack page, ITR-V download; some clients verify later on their own phone.
   (C4, C5, C6, D8, D11, A4)
5. **All-day PC:** the app is up for 10-12 h a day for weeks, never restarted, with a browser holding
   30 tabs, plus Sera Sync and the tracker window open. (A5, A6, A8, H9, I1)
6. **Interrupted day:** a power cut / UPS switch, a Windows update restart, or a browser crash in the
   middle of a filing. (E9, D6, H3, G10)

---

## 6. What "100%" can and cannot mean

These are facts the solution has to plan around, not opinions:

- **Some submissions are never on this PC's screen.** A client e-verifies on their own phone; a
  filing is done on a PC without Sera, or with Sera not running; the ARN comes by SMS/email only.
  No on-screen observer can capture those at the moment they happen.
- **Some screens are shorter than a UIA read.** A toast shown for 1 s while the browser's UI thread
  is busy for 2 s cannot be read through UIA during that second, and under constraint 1 nothing
  may hold it open. A screen capture of that moment does not depend on the browser's UI thread,
  and OCR is now allowed (constraint 7). Whether that closes this gap is for Fable to judge.

So "not missing a single capture" has to be a layered guarantee, and Fable's design should say
exactly how it reaches each layer:

1. **Seen means captured.** Every submission whose evidence is on screen in a Sera PC's
   foreground browser while Sera runs is captured, under every profile in section 5J.
2. **Not seen means known.** Any moment SGT could not read an in-scope page (blind, busy, wedged,
   stalled, scope lost, old build) is detected within seconds, and the user and the office are
   told, not left to find it days later.
3. **Missed means recovered.** Every submission missed at the moment is recovered later from
   evidence the user shows in normal work (track status, filed returns, dashboard *as evidence
   only*, downloaded acknowledgements if the owner approves), and the tracker shows which rows
   came from recovery.
4. **Unknowable is listed.** What still cannot be known is reported, never silently absent.

Fable should say plainly whether the goal is met within constraints 1-12. If a constraint blocks
it, Fable should name the constraint and the smallest owner decision that would unblock it, and
not quietly break it.

---

## 7. What is not known yet (data to collect; Fable cannot collect it)

| Question | How to answer it (on a Windows PC, by the owner or a local agent) |
| :--- | :--- |
| Were the two failing PCs on v2.12.9 or v2.12.10? | Sera's About/version; `logs/vsdc.log` exists only on v2.12.10 and later |
| What did UIA do before they went blind? | `vsdc.log` heartbeat (`uia_busy`, `uia_late`, `uia_stuck`, `paused`), `hang.log`; SGT `sgt_shadow/*.jsonl` `session_end` stats (`blind_pages`, `ocr_skips`, read counts) |
| How long does a full read of a real GSTR-1 B2B page with N rows take, element-by-element vs cached, fresh vs after 4 h? | A timing run with `tools/sgt_i_uia_node_bench.py` / `tools/pre_dev/class_diff/key_probe.py --timing` on live pages (read-only) |
| Does read time grow with how long the page/browser has been open? | Repeat the same read every few minutes for hours; log ms + element count + browser memory |
| Does the STA, no-message-pump worker matter? | Same soak with the worker in multi-threaded (MTA) mode; compare drift and hangs |
| Do success toasts/banners on GST and ITR show as text in the tree, as events, both, or neither, and for how long? | Live probe at a real filing (read-only); `uia_events.py` re-test |
| How often does the change gate fire during typing? | Count gate passes vs reads in the heartbeat during invoice entry |
| How many screen switches per minute in real B2B work? | Count foreground/tab changes in the heartbeat |
| Which builds are on which PCs right now? | No way today (I2) |

---

## 8. What the solution must deliver

1. A design that closes every **Gap** and **Unknown** in section 5, or says why it cannot under
   section 2, and that keeps every **OK** working.
2. The 4-layer guarantee of section 6, with a measurable acceptance test for each layer.
3. A test plan Fable can specify without Windows and the owner can run on Windows:
   - deterministic tests with a simulated reader (fake latency, wedges, partial trees, torn
     ticks, toasts with given lifetimes) for the section 5J profiles at real speed and at
     accelerated time;
   - replay on the recorded corpus (`tools/sgt_replay.py`, `tests/test_sgt_replay.py`);
   - a live soak on a real PC for at least 5 h of B2B-like load, pass/fail by numbers (zero
     missed submissions, read-time drift, memory, stalls).
4. A deployment answer for I1/I2: how a fix reaches a PC that never restarts, and how the office
   knows each PC's build and capture health.
5. Output in the project's usual form: a blueprint in `docs/` with ordered work packages
   (each small enough for one session), owner decisions listed with a recommended default,
   files to touch per package, and tests per package.

---

## 9. Files to read first

| File | Why |
| :--- | :--- |
| `docs/sgt-blueprint.md` | The SGT design; sections 5 (sessions), 7 (cost), 10a (reliability), 11 (risks), 13 (live), 14 (SGT-C/SGT-I) |
| `core/vsdc/vsdc_worker.py` | The capture loop, heartbeat, stall watch |
| `core/vsdc/vsdc_router.py` | `evaluate_tick`, `extract_browser_url`, scope gate, `_run_sgt`, `_prune_stale_sessions` |
| `core/vsdc/vsdc_uia_text.py` | The reader, `_run_with_timeout`, tab id |
| `core/vsdc/vsdc_diag.py` | What `vsdc.log` records |
| `core/sgt/sgt_shadow.py` | Sessions, change gate, blind handling, identity, idle/park, outbox, snapshot |
| `core/sgt/sgt_fields.json`, `core/sgt/sgt_resolver.py`, `core/sgt/sgt_toolbox.py` | Specs, the submit ladder, checks |
| `core/sgt_i/uia_nodes.py`, `core/sgt_i/uia_events.py` | Cached single-call read; UIA events (unreliable so far) |
| `core/vsdc/vsdc_scope.py` | Portal scope gate |
| `docs/sgt-gst-fixes-plan.md`, `docs/sgt-idle-resume-plan.md` | Recent fixes and the reasoning behind them |
| `docs/vsdc247-safety-net.md` | The earlier crosshair-independent submission net (OCR), now off |
| `tests/test_sgt_*.py`, `tests/sgt_golden/` | What is already pinned by tests |
