# SCK at OS level + new-client capture: implementation plan

Written 2026-11-15 for the implementing agent. Read this whole file, then the files in §2, before
changing anything. Work the packages in §5 in order; each one ends with its own tests green.

## 1. Goal

1. **SCK at OS level.** When the assist engine is `os`, the SCK new-password card is a docked Sera
   desktop card (like the SCA / MECP / SMTI OS cards) and the portal's password box is filled from
   outside the browser through `core/os_assist/fill.py` (UIA `SetValue`, `SendInput` fallback). No
   extension involvement. With the engine on `extension`, today's in-page card keeps working.
2. **New-client capture.** When staff log in to a registered service with an ID that belongs to no
   client, SCK offers the same card. Staff type the password, SCK fills the portal's password box
   next to that ID, and when the login works:
   - internal PK value **and** a client name (company or owner) are known -> create the client;
   - otherwise -> do not create a client; store the details as a tracker-dump row.
3. **Save safety** (found in review, needed by both of the above): attempts bound to a window and
   a client, candidates that expire, untested saves not marked verified, SCA paused while a card is
   open, a lockout that sticks.

Out of scope: Fast Autofill, the Firefox/Chrome card's look, password-reset "generate" flow, new
wrong-password rules for more portals.

## 2. What exists (read these first)

| Area | File | Note |
|---|---|---|
| Failure detection | `core/rotation/watcher.py` | `SckWatcher`, an `SccHost` handler fed SGT `Observation`s (`core/sgt_i/observation.py`) with the browser `hwnd`. Pending is keyed by service id only. |
| Vault save | `core/rotation/detector.py` | `save_verified_for_rotation` always calls `tag_client_scc_verified`. `FailureDetector` is unused. |
| Unused attempt model | `core/rotation/session.py` | `RotationSession` / `suppressed_clients`; nothing instantiates it. |
| Desktop glue | `main.py` ~L1485-1773 | `_sck_show`, `_handle_sck_outcome`, `_sck_candidates` (service id -> candidate, no expiry), `_save_sck_password`, `_sck_resolve_client`, `_handle_sck_edit_requested`. |
| Engine routing pattern | `automation.py` | `assist_engine()`; `send_scc_card` / `close_scc_card` / `update_scc_card` branch on it. `show_sck_widget`, `hide_sck_widget`, `sck_save_result`, `sck_outcome` (L394-449) do **not** branch yet. |
| OS card base | `ui/os_assist/dock_card.py` | `DockCard`: docks to a browser hwnd, `hold_secret`, `dismissed`. **Never takes focus** (`WindowDoesNotAcceptFocus`, `WS_EX_NOACTIVATE`). |
| OS card examples | `ui/os_assist/sca_card.py`, `mecp_card.py` | `mecp_card` keeps a module registry of cards by attempt id with `update_card` / `close_card`. |
| The one writer | `core/os_assist/fill.py` | `fill(hwnd, element, text, is_password, service, late_check=...)`. Only this module may call `SetValue`, `SetFocus`, `SendInput` (there is a one-writer test). |
| Finding boxes | `core/os_assist/locate.py` | `find_fields_cached(hwnd, user_sel, pass_sel, want="pass")`, `warm_fields`, `host_matches_service`, `read_url`. |
| Worker thread | `core/os_assist/worker.py` | `submit` / `submit_urgent`; all UIA runs here, never on the Qt thread. |
| OS SCA engine | `core/os_assist/sca_engine.py` | Pattern to copy: `fill_job`, `warm_job`, `_later`, pending fill with deadline, `_box_value` (non-password boxes only). |
| Card launch | `core/os_assist/launcher.py` | `launch_scc(..., hwnd=...)` -> `_on_qt(_show_scc)`. |
| Client creation | `sera_db/clients.py` | `add_client(values, notes, service_ids, actor)`; `_validate_internal_pk_values` (PK column = `mcl_columns.is_internal_pk`, must be non-empty and unique). |
| Create-from-capture precedent | `ui/windows/tracker_dump_window.py` ~L560-673 | `map_profile_to_mcl_columns`, then `add_client`, then `link_unassigned_tracker_dumps`. |
| Tracker dump | `sera_db/tracker_dump.py` | `insert_tracker_dump(...)` (L268), `link_unassigned_tracker_dumps` (L1021). Rows live in `rawPayload.db`. |
| Tests | `tests/test_sck_watcher_outcome.py`, `tests/js/test_sck_widget.js`, `tests/test_ws_bridge.py`, `tests/test_os_assist_*.py` | Keep all green. |

## 3. Rules (do not break)

1. A password is never logged, printed, put in an audit detail, a signal payload that is stored, or
   a setting. Memory only until the vault write (or the tracker-dump write in §4.5).
2. Only `core/os_assist/fill.py` writes into a browser. SCK calls `fill.fill`, nothing else.
3. A password box is never read (`_box_value` is for non-password boxes only).
4. UIA only on the OS Assist worker thread; DB and vault writes only on the Qt thread.
5. Every fill checks `locate.host_matches_service` right before the write (`fill.fill` does this).
6. SCK never submits the form. Staff solve the CAPTCHA and press Login.
7. Extension mode must behave as it does today, apart from the §4.1 safety fixes.

## 4. Design

### 4.1 Attempt model (replaces `_sck_candidates`)

New `core/rotation/attempts.py`: `SckAttempts`, thread-safe, one `SckAttempt` per
`(service_id, hwnd)` (extension mode has no hwnd: use `hwnd=0`).

Fields: `attempt_id`, `service_id`, `hwnd`, `client_id | None`, `client_name`, `portal`,
`identity` (the PK-shaped value read on the page, upper-case, may be ""), `user_id` (the login ID
typed, may be ""), `kind` (`"rotation"` or `"new_client"`), `candidate` (password, memory only),
`candidate_at`, `tested` (bool: it came through Fill & Test), `opened_at`.

Behaviour:
- `CANDIDATE_TTL_SEC = 600`. An expired candidate is dropped on every access.
- `take_candidate(attempt)` returns the password and blanks the slot.
- `suppressed()` -> set of `(client_id, service_id)` with an open attempt.
- Closing an attempt (saved, dismissed, card gone, browser closed, locked) blanks its candidate.

Then:
- `SckWatcher.on_outcome` gains `hwnd` and the identity read on that page:
  `on_outcome(service_id, kind, hwnd, identity)`. `worked` saves only the attempt whose
  `(service_id, hwnd)` matches; in extension mode (`hwnd=0` attempt) save only when the page
  identity equals the attempt's identity or the page identity is empty.
- `save_verified_for_rotation` gets `verified: bool = True`. "Save Directly" passes `False`: no
  `tag_client_scc_verified`, and the audit detail says `untested`.
- SCA pause: in `clipboard_watch.py` `handle_password_request` (both engines call it), deny with a
  clear reason when `(client_id, service_id)` is in `suppressed()`. Read that function first and
  follow its existing deny shape.
- Locked: on a `locked` outcome store setting-free state `sck_locked[(client_id, service_id)]` in
  memory for the run, deny SCA the same way, show a tray warning, and write one audit line
  (`SCK: portal reports the account locked`). Cleared when staff press the card's ✎ again or a
  `worked` is seen for that client.
- Delete `FailureDetector` and `core/rotation/session.py`; one shared CAPTCHA fragment list in
  `watcher.py`.

### 4.2 Engine routing

In `automation.py`, branch the four SCK functions on `assist_engine()` exactly like
`send_scc_card` does. `show_sck_widget` gains `hwnd: int = 0`, `attempt_id`, `kind`, `user_id`.
OS branch -> `core/os_assist/sck_engine.py` (below). `main.py` `_sck_show` passes
`failure.hwnd`. `_handle_sck_edit_requested` has no hwnd: in OS mode use the hwnd of the OS card
that raised ✎ (`card.browser_hwnd`; extend the `wire_sck_edit` message with it), and for the
extension's ✎ fall back to `locate.window_for_host(service login host)` on the worker.

In OS mode the extension must not also show its card: do not broadcast `sck_failed_login`.

### 4.3 OS card: `ui/os_assist/sck_card.py`

`SckCard(DockCard)`, same content and wording as the in-page card (`sckWidget` in
`sera_extension/background.js` ~L2125-2750): badge "Sera Credential Keeper", title
`client • portal`, subtitle, password box with eye toggle, **Fill & Test**, **Save Directly**,
status line, red accent. No timer bar (persistent card). ✎ hidden on this card.

Focus is the one real difference from the other OS cards, which never take focus:
- The card window is created without `Qt.WindowDoesNotAcceptFocus` and without
  `WS_EX_NOACTIVATE`, but still `WA_ShowWithoutActivating` so showing it does not steal focus.
  Add class attributes on `DockCard` (`ACCEPTS_FOCUS = False`) rather than forking the base; the
  three existing cards must be unchanged (their tests cover it).
- Clicking the password box activates the card; typing goes to it.
- Fill & Test hands focus back: `fill.fill` already brings the browser to front and focuses the
  box when it has to type. After a `setvalue` fill, call the launcher's `_bring_to_front(hwnd)`
  so staff land on the page for the CAPTCHA.
- Buttons stay `Qt.NoFocus`.

Signals: `fill_requested(attempt_id)`, `save_requested(attempt_id)`, `dismissed(reason)`.
The typed password is read from the box only inside the engine's handler and held with
`hold_secret` until the fill job is submitted.

States (methods the engine calls): `set_filled()`, `set_fill_failed(reason)`,
`set_outcome("wrong_password" | "locked" | "worked")`, `set_saved(message)`,
`set_save_failed(message)`, `set_no_outcome()` (60 s after a fill with nothing seen: "No result
seen yet. Press Login on the portal, or Save Directly as untested.").

New-client variant (`kind="new_client"`): title is the ID read on the page, subtitle "This login
is not in Sera. Enter its password and Sera will fill it and remember it.", badge unchanged.

Registry by `attempt_id` in the module, like `mecp_card`.

### 4.4 OS engine: `core/os_assist/sck_engine.py`

Qt-thread `QObject`, one instance, modelled on `ScaEngine` but with no polling: it only acts on
calls from `automation.py` and on card signals.

- `show(attempt)`: build the card, `attach(hwnd, service)`, `present()`, submit `warm_job`.
- Fill & Test: take the text from the card, store it as the attempt's candidate
  (`tested=True`), submit on the worker a job equal to `sca_engine.fill_job` (reuse it: same
  foreground + host check, `find_fields_cached(..., want="pass")`, `fill.fill(..., True, service,
  late_check="background")`). Poll like `_tick_fill` until a visible password box exists, with a
  short deadline (10 s, not 90 s: staff are looking at the page). Results map to the card states.
  Call `watcher.reset_outcome_throttle(service_id)` after a successful fill.
- Outcome from the watcher: forward to the card; `worked` -> save (§4.1) or new-client path
  (§4.5); `locked` -> close and lock.
- Save Directly: candidate `tested=False`, then the same save path with `verified=False`.
- Card dismissed / browser closed -> close the attempt.

Unit-test it the way `ScaEngine` is tested: inject `submit`, `filler`, `card_factory`.

### 4.5 New client

Detection, in `SckWatcher._observe` (it already sees every page of a registered service):
on the service's login page, with no outcome classified and no pending failure for that
`(service, hwnd)`, read the identity the page shows (`profile_value(obs, "pan")`, then the PAN
inside `gstin`, then `username` / `userid`). If it is PK-shaped (PAN `^[A-Z]{5}[0-9]{4}[A-Z]$`,
GSTIN, or TAN `^[A-Z]{4}[0-9]{5}[A-Z]$`) and `resolve_client` returns no client, fire
`on_unregistered(SckFailure-like)` once per `(service, hwnd, identity)`. A dismissed offer is
remembered for the run so the card does not come back for the same ID.

If SGT's profile does not carry the typed ID on a portal's login page (check with a real
observation dump before coding around it), add a worker job that reads the service's user-ID box
with `locate.find_fields_cached(..., want="user")` + `sca_engine._box_value`. Non-password only.

The existing wrong-password path with `client_id is None` becomes the same `new_client` attempt
instead of the disabled "Copy the client's ID first" card.

On `worked` for a `new_client` attempt (Qt thread), new `core/rotation/new_client.py`:

1. Collect: `identity` and `user_id` from the attempt; name from the `worked` observation's
   profile (the watcher passes the profile dict along with the outcome). Map with
   `ui/utils/profile_parser.map_profile_to_mcl_columns(profile, mcl_cols)`, the same call the
   "Create client from capture" dialog uses. Move that helper out of `ui/` if importing it from
   `core/` breaks a layering test.
2. **Create** when the internal-PK column value is present and a company name or owner name is
   present:
   - `values` = mapped profile + PK column + the service's `userid_column_id` = `user_id or
     identity` + the service's `password_column_id` = candidate.
   - `db.add_client(values, notes="Created by SCK from a portal login", service_ids=[service_id],
     actor=...)`. A `ValueError` (duplicate PK: another PC created it meanwhile) -> fall back to
     the rotation save on the existing client.
   - If the candidate was tested, `tag_client_scc_verified`.
   - `db.link_unassigned_tracker_dumps(new_id, identity)`.
   - Tray toast "New client created: <name> (<portal>)", `_refresh_client_views(new_id)`.
3. **Otherwise store in the tracker dump**: `db.insert_tracker_dump(client_id=None,
   service_id=..., portal=..., pan=identity or None, capture_method="SCK_Login",
   status=<see D3>, captured_by=actor, raw_payload_json=json.dumps({...}))` with payload keys
   `sck_login: {user_id, identity, name_found, missing: ["pk" | "name"], password}` (see D2 for
   the password). Tray toast "Login details kept in Tracker Dump: <what is missing>".
4. When a client is later created from that dump row (the existing dialog), prefill the service's
   user-ID and password columns from `sck_login`, and strip `sck_login.password` from the row once
   the client is saved. Add this to `tracker_dump_window.py` `_on_save`.

"Save Directly" on a new-client card runs the same steps with whatever name is known (usually
none before login, so it ends in the tracker dump).

## 5. Work packages

| WP | What | Done when |
|---|---|---|
| S1 | §4.1 attempt model, safety fixes, dead-code removal. Both engines. | New `tests/test_sck_attempts.py`; a test that a `worked` in window B does not save window A's candidate; untested save leaves the client untagged; SCA request denied while an attempt is open; existing SCK tests green. |
| S2 | Watcher: outcome carries hwnd, identity and profile; `on_unregistered`. | Extended `tests/test_sck_watcher_outcome.py`: fires once per ID, not for a known client, not for a non-PK-shaped value, not again after dismissal. |
| S3 | `SckCard` + `DockCard.ACCEPTS_FOCUS`. | `tests/test_os_assist_sck_card.py` (states, signals, secrets dropped on dismiss); existing OS card tests green; add the card to `tools/pre_dev/os_assist/render_cards.py`. |
| S4 | `sck_engine.py` + `automation.py` routing + `main.py` wiring + ✎ in OS mode. | Engine tests with fakes; `tests/test_assist_engine.py`-style test that OS mode does not broadcast `sck_failed_login`; one-writer test still passes. |
| S5 | §4.5 new client: create, tracker-dump fallback, later promotion. | `tests/test_sck_new_client.py` against a temp DB: creates with PK + name, dumps without name, dumps without PK, duplicate PK falls back, password stripped after promotion. |
| S6 | Extension parity for new-client (in-page card shows the new-client wording and no longer disables itself without a client), Chrome and Firefox. Docs: update `docs/os_assist/os-assist-blueprint.md` lines that say SCK stays on the extension. | `tests/js/test_sck_widget.js` updated and green. |

Run the full suite at the end as `CLAUDE.md` says (the `live_tf` plugin, log to a file).

## 6. Decisions for the owner (defaults in bold are what to build if unanswered)

- **D1. When is a new client created?** **Only after the portal login worked** (or Save Directly,
  untested and untagged). Creating on Fill alone would store typos.
- **D2. Password in the tracker dump. Settled by the owner 2026-11-15: store it in the row,
  redacted in viewers.** Store the password under `sck_login.password`. `rawPayload.db` is opened
  with the office key, but tracker-dump rows are shown in the Tracker Dump window, exported (DRS)
  and sent to other PCs (`store_peer_tracker_dumps`). As part of S5, find every reader of
  `raw_payload_json` (viewers, export, logs, debug prints) and redact that key in each, through
  one shared helper, with a test per reader. Sync to other PCs carries the row unredacted (they
  hold the same office key and need the password to create the client there); say so in the S5
  hand-off note so the owner can object.
- **D3. Status of an `SCK_Login` dump row.** A login is not a filing. **Use a status that
  `is_complete()` treats as not complete and check how the row renders and whether LTT, DRS and
  the tracker status matrix count it; exclude `capture_method="SCK_Login"` where it would be
  counted as a filing.**
- **D4. Offer on every unknown ID?** **Yes, once per ID per run, dismissable.** Alternative: a
  setting "Offer to save unknown logins" under the SCK switch.

## 7. Hands-on checks (a person, real browser, OS engine)

1. Income Tax, known client, wrong saved password: card docks, type the new password, Fill &
   Test fills the box, solve CAPTCHA, login -> "Saved password replaced", card closes, SCA fills
   the new one next time.
2. Same, wrong new password: card shakes "Portal rejected", SCA does not refill the old one.
3. Two browser windows on the same portal with different clients: a login in one never saves the
   other's candidate.
4. GST and TRACES: Fill & Test finds the box (TRACES is Flutter: `fill.py` types with keys).
5. Unknown PAN on Income Tax: card offers, fill, login -> client created with name, PAN, user ID,
   password, the service attached.
6. Unknown ID where the dashboard shows no name: no client; a row appears in Tracker Dump;
   creating the client from that row prefills the password and removes it from the row.
7. Typing in the card's password box works, and the browser keeps focus when the card first shows.
8. Engine on `extension`: everything behaves as before plus the safety fixes.
