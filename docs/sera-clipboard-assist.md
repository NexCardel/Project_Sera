# Sera Clipboard Assist (SCA)

> **Current design: protocol v2 (2026-09-22).** The sections further down describe the
> original push design (v1) and are kept for history. Where they disagree, this box wins.
>
> **Why v2.** A review found that v1 pushed every one of a client's portal passwords to every
> browser on each copy. Those passwords ended up in a log (about 2,700 in plain text) and in
> `chrome.storage.local`, which Chrome writes to disk. v1 also filled the first service's
> password on *any* site where the PAN was pasted. It let any website read the PAN list from
> the app's local port and forge `scc_password_verified`. It also went silent in a tab after
> its first fill.
>
> **Flow** (`sca_protocol.py`, `clipboard_watch.py`, `sera_extension/sca/sca_coordinator.js`):
> 1. **Copy → arm.** Copying a client id (PAN, GSTIN, login id, token) arms SCA. The arm says
>    which client, which portals (with their hosts), which ids trigger it and when it expires
>    (5 min). It carries **no password**.
> 2. **Paste → match.** `login.js` reports id-shaped input to the background. The coordinator
>    fires only if the id is one of the arm's ids **and** the frame's site is one of the
>    client's portals. The site must match exactly or as a sub-domain, label by label, and be
>    on the approved-domain list. There is no fallback to another service.
> 3. **Request → grant.** The extension asks the desktop, over the WebSocket bridge
>    (`ui/ws_bridge.py`), for that one portal's password. The desktop checks the arm id, the
>    expiry, the uses left and the page host, then reads the password from the vault at that
>    moment. The ITR SCC gate still applies. The answer goes back only on the socket that asked
>    (`WSBridge.reply`) - never to every connected browser.
> 3b. **Income Tax passwords SCC has not verified** (your choice, 2026-09-22). These are never
>    filled automatically. Once the password box appears, a card warns that the password isn't
>    verified and offers **Inject Password**. Only that click sends `confirmed: true`, and the
>    desktop refuses an unverified IT password without it. Verified clients fill automatically.
>    If a portal has no saved password, a Sera alert says so instead of staying silent.
> 4. **Fill → report.** The password is filled in the frame where the id was entered, and only
>    into a real password box or the portal's configured selector. The result goes back as
>    `SCA_FILL_RESULT` (see SCA Diagnostics). The password is never stored.
>
> **Transport (2026-09-22: moved from native messaging + port 49152 to one local WebSocket -
> see `docs/app-extension-communication-report.md`).**
> - One WebSocket, `ui/ws_bridge.py`, on the first free port from `48765-48768`.
> - Only the Sera extension's own background page may connect; the connection's Origin is
>   checked against the extension's real id (Chrome/Edge: derived from the manifest key;
>   Firefox: trusted on first connect, then pinned - see that module for why). A website
>   cannot forge this the way it could fake an HTTP request's Origin header.
> - Page scripts (the SDC content script) never connect directly - only `background.js` does;
>   everything else goes through it via `chrome.runtime.sendMessage`.
>
> **Reliability.**
> - The id index refreshes itself when the database changes.
> - Copying the same id again re-arms it after 2 s.
> - Nothing stays "filled" per tab.
> - A client armed 5 times without a fill is paused for the session.
> - One source for Chrome and Firefox (`build_extension.py` copies `sca/` and the content
>   scripts).
>
> **Tests.** `tests/test_sca_v2.py`, `tests/test_clipboard_assist.py`,
> `tests/test_sca_protocol.py` and `tests/js/test_sca_coordinator.js`.

## Purpose

Employees currently trust Excel because copy-paste "just works" and there's no
friction. SCA closes that gap: the moment a staff member copies a UID from
their client sheet, Sera silently arms the matching password so that when
they paste that UID into the portal as usual, the password fills itself
right after — no toast, no extra click, no need to open the app, search the
client, or hit Autofill manually. The goal is repeated *ambient* exposure to
Sera doing something useful, not a one-time demo.

Critically: SCA should **assist**, not **act blindly**. Silent, unconditional
auto-submission on clipboard match alone is the single biggest risk in this
feature (see Guardrails). The design below only ever fills the password once
the user pastes the UID into a recognized portal field — arming is silent,
but firing still requires a real user action.

---

## High-Level Flow

```text
[Excel] --user copies a cell-->  Windows Clipboard
                                        |
                                        v
                        [main.py: ClipboardWatchService]
                         (Qt QClipboard.dataChanged signal,
                          NOT a polling loop)
                                        |
                     mimeData().formats() carries an Excel
                     marker (Csv / Biff12 / XML Spreadsheet)?
                                        |
                         no ---(reject, no further work)
                          |
                         yes
                          |
                          v
                          normalize candidate text
                                        |
                          lookup against in-memory userid
                          index (PAN/UID/GSTIN -> client_id)
                                        |
                                match found?
                                        |
                         yes -----------+----------- no (ignore, no-op)
                          |
                          v
        look up this client's registered
        Service Management link(s) from DB
        (already stored per client/service)
                          |
                          v
        [Native Host Bridge TCP 49153] -- SCA_ARM {client_id_token, service_urls[], expires_in}
                          |
                          v
              [sera_extension/background.js]
                          |
             does the currently focused tab's
             URL match one of service_urls[]?
             (single focused tab = no ambiguity
              to resolve, just a direct check)
                          |
                 yes ------+------ no
                  |               |
                  v               v
        arm "ready" state    hold in queue, re-check
        (silent, no toast)   on next tab focus change,
                              expire after TTL
                  |
       user pastes UID into the portal's
       UID field (the only fill trigger)
                  |
                  v
        fillCredentialsInPage() runs
        (password only — UID was already
        pasted manually by the user)
                  |
                  v
        audit_log: "SCA autofill" event
```

---

## Component Breakdown

### 1. Desktop-side clipboard watcher

New module: `clipboard_watch.py`, owned by `main.py` alongside the other
background services.

- Use `QApplication.clipboard().dataChanged` — **event-driven**, not a
  polling loop. Polling the clipboard every N ms is wasteful and looks like
  spyware in Task Manager / AV heuristics.
- On change: read `clipboard.text()` once, trim whitespace, uppercase-normalize
  for PAN/GSTIN comparison (these are case-insensitive in practice).
- Look up against an **in-memory index** built at vault unlock:
  `{normalized_userid: client_id_token}` — never re-query SQLCipher on every
  clipboard event; that's both slow and creates decryption traffic tied to
  arbitrary background clipboard activity.
- **Do not store or log the raw clipboard string anywhere.** Only the
  resulting `client_id_token` (already a non-sensitive identifier, see
  `features-security.md`) crosses into logs or the native bridge payload.
- Debounce: ignore repeat matches for the same token within a short window
  (e.g. 15s) so re-copying the same UID doesn't re-arm/re-toast repeatedly.

### 2. Matching & lookup logic

- **Source-gated by design**: UIDs are only ever copied from the firm's
  existing Excel sheets (per current staff workflow), not typed or copied
  from arbitrary apps. SCA takes advantage of this directly — Excel copy
  events carry extra clipboard formats beyond plain text (`Csv`, `Biff12`,
  `XML Spreadsheet`, `Link` on Windows), and the watcher checks
  `mimeData().formats()` for one of these markers *before* reading any text
  payload at all. A copy from Chrome, Outlook, Notepad, WhatsApp, etc. never
  has these formats and is rejected at this first gate — no string
  processing, no regex, no index lookup for the overwhelming majority of
  clipboard activity on the machine.
- Match is **exact, normalized, full-string** — no substring/fuzzy matching.
  A clipboard value that merely *contains* a PAN inside a longer copied
  sentence should not trigger (avoids false positives from copied emails,
  chat messages, filenames, etc.).
- Optional: require a minimum confidence signal beyond string match — e.g.
  the value's format also matches the expected pattern for its field type
  (PAN regex `[A-Z]{5}[0-9]{4}[A-Z]`, GSTIN 15-char pattern) before even
  attempting the DB-index lookup. Cheap pre-filter, avoids hashing every
  clipboard event (including large copied blocks of text).

### 3. WebSocket bridge protocol extension

Extend the existing `automation.py` <-> `ui/ws_bridge.py` <-> extension
channel (WebSocket ports 48765-48768) with a new lightweight message type, distinct from the
existing Autofill trigger:

```json
{
  "type": "SCA_ARM",
  "client_id_token": "CLI-00370",
  "service_urls": ["https://services.gst.gov.in/services/login"],
  "ttl_ms": 45000
}
```

- `service_urls` comes straight from the client's existing **Service
  Management** records (`database.py`, already surfaced via
  `service_manager_dialog.py`) — the same login links already attached to
  each service Sera knows about for that client. No new data model, and no
  guessing from field type: if a service link is on file, it's
  authoritative for what "the right tab" looks like for this client.
- This directly resolves the earlier portal-ambiguity question: since the
  browser only ever has one *focused* tab at a time, the extension doesn't
  need to disambiguate between candidate portals — it just checks whether
  the currently focused tab's URL matches one of `service_urls[]`. If a
  client has more than one service on file (e.g. both GST and Income Tax),
  `service_urls[]` simply holds multiple entries, and whichever one matches
  the focused tab at paste-time is the relevant one. No inference needed.
- `ttl_ms` — how long the extension should keep this armed before discarding
  it. Prevents a stale arm from firing password fill on some unrelated tab
  the user opens 20 minutes later.
- Reuse the existing wake logic from `automation.py` (wake service workers,
  retry up to 10s) if the extension is asleep when SCA_ARM is sent — but
  **only if a portal tab is already open**. SCA should never proactively
  open a browser tab or navigate anywhere; that would cross from "assist"
  into "unsolicited action" and would surprise/alarm users far more than it
  builds trust.

### 4. Extension-side handling (`background.js`)

- Maintain a small in-memory `armedQueue` (max 1–2 entries, TTL-checked).
- On `tabs.onActivated` / `tabs.onUpdated`, check the **currently focused**
  tab's URL directly against `service_urls[]`. A single focused tab means
  this is a plain membership check, not a ranking or scoring problem — if
  it matches, arm; if not, hold and re-check on the next focus change. No
  toast, no popup, no page injection at this point — arming is silent.
- **Single fill trigger, no toast-click alternative**: the user pastes the
  UID into the portal's UID field as normal. The extension detects that
  paste event on a field it recognizes and immediately follows with the
  password fill + existing checkbox/Continue-click logic from
  `fillCredentialsInPage()`. That's the only way a fill happens — there is
  no secondary "click to fill" affordance to build or maintain.
- The *password autofill* action itself is the same `fillCredentialsInPage()`
  path already used by the manual Autofill button, scoped to
  password-field-only since the UID was already user-entered via paste.
- Clear the armed entry immediately after a successful fill, after TTL
  expiry, or when the tab navigates away from the matched portal domain.
- Optional, cheap signal in place of a toast: a brief `chrome.action.setBadgeText()`
  change on the extension icon itself (e.g. a small dot or checkmark for
  ~2s after a successful fill) — this is a one-line API call against an
  icon that's already there, not a new UI surface, and costs nothing to
  build or maintain. Purely optional; skip it too if you want SCA
  completely silent end-to-end.

---

## Performance & Footprint

SCA has to be invisible in Task Manager, not just invisible in the UI. Every
design choice below exists to keep it at effectively-zero idle cost:

1. **No thread, no polling loop, no timer tick.** `QClipboard.dataChanged` is
   a native Qt signal fired by the OS clipboard viewer chain — the watcher
   is a plain slot on the existing main-thread event loop. Idle cost is
   zero; there is no periodic wake-up to budget for at all.

2. **Cheapest check first, most expensive check last** — reject in this
   order, bailing out at the first failure so the common case (clipboard
   text is irrelevant to Sera) costs almost nothing:
   - **Excel-source marker check** — `mimeData().formats()` is a short list
     (typically 5–10 entries) attached to the clipboard event *before* any
     text is read. If none of `Csv` / `Biff12` / `XML Spreadsheet` / `Link`
     are present, reject immediately — the copy didn't come from Excel, so
     it can't be a UID lookup under the current workflow. This single check
     eliminates essentially all non-Excel clipboard activity (browser URLs,
     chat messages, code, file paths) at zero string-processing cost, and
     runs ahead of every other check below.
   - `len(text)` outside 4–20 chars → reject immediately (PAN=10, GSTIN=15,
     typical UID range). This alone discards the vast majority of
     real-world clipboard events (URLs, paragraphs, code, file paths)
     before touching regex or the index.
   - `text.strip().isalnum()` → reject anything with spaces/punctuation.
     A native string method, no regex engine invoked.
   - Precompiled regex (module-level, compiled once at import, not per
     event) only runs against the small set of candidates that already
     passed the two checks above — format validation for PAN/GSTIN shape.
   - Final step is a single `dict.get()` against the in-memory index —
     O(1), no DB hit, no disk I/O, no decrypt.

3. **In-memory index, not a live query.** `{normalized_value: client_id_token}`
   is built once at vault unlock and kept incrementally in sync with
   client add/edit/delete (hook into the existing DB write paths that
   already fire on those actions — no polling the DB either). For a firm
   with a few thousand clients this index is a few hundred KB at most.

4. **WebSocket bridge stays silent on the non-match path.** No socket write,
   no chatter, nothing crosses into `ui/ws_bridge.py` unless step 2 above
   actually resolves to a real client. This means SCA adds zero IPC
   overhead for the 99%+ of clipboard events that aren't a UID.

5. **Debounce via a single scheduled timer, not busy-waiting.** The 15s
   repeat-suppression window uses one `QTimer.singleShot` per armed token,
   not a sleep loop or a recurring interval timer.

6. **No persistent storage, no growing memory.** The watcher holds at most
   one "recently armed" token + its expiry timestamp at a time — nothing
   is queued, buffered, or written to disk. Clipboard text itself is a
   local variable inside the slot function and is eligible for GC the
   moment the function returns.

7. **Extension side matches this discipline.** `armedQueue` caps at 1–2
   entries; TTL expiry is a single `setTimeout`, not a recurring check
   loop; there's no popup or on-page element rendered at arm time at all —
   the only visible side effect (if the optional badge signal is kept) is
   a one-line icon update after a fill actually happens.

Net effect: SCA's steady-state cost is "one native signal handler sitting
idle," and its active cost only fires on the rare event that's actually a
known UID — comparable to the overhead Sera already carries for its
existing search-grid and formatting-undo signal handlers, not a new
standing service.

---

## Security & Safety Guardrails

These are the parts most likely to go wrong if skipped:

1. **Never log or persist raw clipboard content.** Only `client_id_token`
   should ever leave the clipboard watcher's local scope.
2. **No blind auto-submit.** SCA arms a *ready* state silently; only the
   real user action of pasting the UID into the portal's UID field triggers
   the actual password injection — there is no click-to-fill affordance and
   no notification prompting one. This avoids the failure mode of a
   password silently appearing in the wrong field on the wrong site because
   a UID happened to be copied for an unrelated reason (e.g. pasting into an
   email to a client, a WhatsApp message). The Excel-source gate (see
   Performance & Footprint) also strengthens this: because SCA only reacts
   to copies that carry Excel's clipboard markers, a UID copied out of a
   chat app or PDF for some unrelated reason won't arm SCA at all — the
   trigger surface is already narrowed to "staff member is working from
   their client sheet," which is a meaningfully stronger intent signal than
   "matching text copied from anywhere."
3. **Domain allowlist enforcement stays in the extension**, same as existing
   Autofill — SCA cannot expand where credentials get typed beyond the
   already-approved portal domains.
4. **TTL expiry is mandatory.** An armed-forever state is a standing risk;
   45–60s is generous for "just copied it, about to paste" behavior.
5. **Opt-in, not default-on.** Ship it as a Settings → General toggle
   ("Enable Clipboard Assist"), off by default, admin can enable per
   workstation. This also means SCA doesn't need to justify itself to every
   employee on day one — admins roll it out to the comfort-zone holdouts
   deliberately.
6. **Audit trail entry, no raw values.** Log `SCA autofill triggered —
   client CLI-00370 — portal GST — workstation <label>` in the existing
   undeletable `audit_log` table, consistent with how manual Autofill events
   are already recorded.
7. **Rate/volume sanity check.** If SCA arms and expires without a fill
   happening more than, say, 5 times in a row for the same client, suppress
   further arming for that client for the session — likely means the value
   is being copied for something unrelated to portal login, and there's no
   point re-arming a state that never gets used.

---

## Edge Cases to Handle Explicitly

- **Multiple clients share a similar-looking UID pattern format but not the
  actual value** — normal exact-match lookup handles this; no special case
  needed beyond the exact-string requirement.
- **User copies the UID while the vault is locked** (app not yet unlocked,
  or admin PIN gate irrelevant here since vault unlock ≠ admin mode) — the
  in-memory index won't exist yet; SCA should simply no-op until the vault
  is unlocked, not queue clipboard events from before unlock.
- **Multiple monitors / multiple portal tabs open simultaneously** — arm
  state binds to whichever tab is *focused* at fill-trigger time, which is
  now a direct `service_urls[]` membership check rather than a guess, so
  there's nothing to disambiguate even with several matching-domain tabs
  open across monitors.
- **Client has no Service Management link on file yet** — `service_urls[]`
  would be empty, so SCA has nothing to match the focused tab against and
  simply never arms for that client. This is a natural, safe fallback: SCA
  only ever activates for clients whose services are already properly set
  up in the app, which also nudges staff toward keeping that data current.
- **Extension disabled or not installed on that workstation** — `SCA_ARM`
  send over the native bridge simply fails silently (same behavior as
  today's Autofill-when-extension-absent case); no error dialog interrupts
  the user's work.
- **Clipboard managers / clipboard history tools** running alongside SCA —
  since SCA only reacts to the live `dataChanged` event and never persists
  clipboard history itself, it shouldn't conflict, but worth a smoke test
  against common tools (Windows 11's built-in clipboard history, Ditto).

---

---

## Smart Credential Combinations (SCC / MECP)

**SCC (Smart Credential Combinations)** extends the ambient assistance workflow to portal password discovery, credential verification, and automated vault tagging.

### 1. In-Browser Multi-Entry Credential Provider (MECP Widget)
When navigating to tax portal login and password pages, the browser companion attaches the unobtrusive **MECP Assistant**:
- **Clean Unmasked Presentation**: Displays cleartext buttons showing the 4 candidate password variations directly alongside the password input.
- **Single-Click Injection**: Clicking any combination injects the password into the active field, triggers native React/Angular input events, and moves the cursor without breaking the field state.
- **Fast Dismissal & Auto-Slide**: Clicking the password button slides the widget out immediately to prevent re-triggering or blocking submit buttons.

### 2. Password Formulas & Combination Architecture
SCC provides 4 standard slots:
1. **Dynamic PAN Formula 1**: `Pan@123` / `Pan#123` (computed from extracted PAN).
2. **Dynamic PAN Formula 2**: `Pan@2024` / `Pan#2026` / capitalized variant.
3. **Fixed String 1**: Configurable firm standard default password.
4. **Fixed String 2**: Secondary firm standard default password.

### 3. Automatic Vault Tagging & Audit Trail
- When a login succeeds with an injected SCC formula, Sera detects the navigation transition.
- Automatically records `"Password verified via SCC"` into the client's notes in `master.db`.
- Floats a 20-second non-intrusive dark notification banner confirming that the client credentials have been verified and tagged.
