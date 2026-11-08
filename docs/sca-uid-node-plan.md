# SCA UID-node plan (draft 1, 2026-11-07)

Goal: SCA arms faster and more precisely by reading one node, picked once per service, on the
service's own UID page.

## 0. The flow

1. **Pick (once per service).** In Settings -> SCA, staff type the service's UID page link and
   click **Pick UID**. Sera reads that page by UIA, uses render.py to rebuild it as an HTML page,
   staff click the node that holds the UID, and Sera saves that node against the service.
2. **Watch.** When that page is in front, Sera reads only the saved node.
3. **Clean.** It pulls the UID out of the node's text (a GSTIN, a PAN, an email, a login id).
4. **Look up.** It matches the UID against the client database.
5. **Arm.** It arms that client for that service only (the existing arm path).
6. **Deliver.** It sends the password through the widget, or whichever delivery method is selected
   (unchanged).

### What the UID page is

The UID page is the portal's login page that shows the password field with the UID already set.

- **One-page login:** the UID is the *value* of the username box that staff typed into. The
  password box is on the same page.
- **Two-page login:** page 1 takes the UID. Page 2 shows the UID as text (or as a read-only box)
  next to the password box. Page 2 is the UID page.

This link is entered separately from `login_page_link`. It is stored in `services.uid_page_link`
(that column already exists in the uncommitted work).

## 1. What exists today (uncommitted, main checkout)

- `core/sca/page_nodes.py`: `UiaReader.read_docs` / `read_url`, the same read the SDIS key probe
  makes. It never reads a password box's value.
- `core/sca/uid_source_watch.py`: watches for a saved page and reports up to 600 strings from
  the **whole page**. `clipboard_watch.handle_source_texts` arms when all the matches belong to one
  client.
  - **Imprecise:** any client id anywhere on the page counts, and it arms every service the client
    has.
  - **Slow:** each tick is a full-tree `FindAllBuildCache` followed by a Python walk, and ticks
    come every 1.2 s.
- Settings -> SCA has a "UID sources" box with one link field per service.
- `SCA_UID_PROBE` (the login-page trigger) has been removed from the desktop and bridge side.
  `login.js` changes are in progress.
- `tools/pre_dev/class_diff/render.py`: the renderer (917 lines, no Qt/UIA/network). Every drawn
  element carries its node index (`k`), and the page already highlights by `k`. It lives in
  `tools/`, which the installer does not ship.

## 2. Work packages

### WP1: render engine into core
- Move the engine to `core/uia_render/render.py` and keep a thin pre-dev alias at the old path.
  The pre-dev CLI stays working.
- Swap `tools.vsdc_uia_probe.CONTROL_TYPE_NAMES` for `core/sca/uia_types.CONTROL_TYPE_NAMES`.
- Keep the guarantee that the same input gives the same bytes. Existing render tests must still
  pass.

### WP2: picker capture
- The **Pick UID** button sits on each service row, next to the link field.
- Staff open the page in their browser with a real UID set (typed on a one-page login; on page 2
  for a two-page login), then press Pick.
- Sera waits up to about 60 s for a browser window in front whose `page_link` equals the saved
  link **and** whose page has a password box (`IsPassword`). Then it takes one `read_docs`.
  - The password-box check proves it is the right page and is also used again at run time.
- Nothing is written to disk: the loopback server (D1) serves the picker HTML itself.
  Password values are never read (page_nodes rule).

### WP3: picker page
- Add `build(..., pick=True)`: every text and box element becomes clickable. A click shows the
  node's text, plus a preview of the UID cleaned from it (WP5).
- The **Use this** button sends `{token, k}` back to Sera. See decision D1 for how.
- Sera stores a **node fingerprint** per service (new column `services.uid_node`, JSON):
  - `key` (strict, from `core/sdis/keys.flatten`) and `loose`;
  - `ctype`, the HTML `id`, the cleaned classes;
  - `name_role`: whether the UID is in the node's name or its value;
  - `anchor`: the label text before it, and its offset from the password box;
  - `clean`: the cleanup rule (WP5).

### WP4: fast, precise watcher (replaces the whole-page `page_texts`)
- Gate: the foreground window is a browser and its `page_link` equals a service's UID page. That
  is an address-bar read only, as now.
- **Fast path:** when the fingerprint has an HTML id, use a single `FindFirst` on AutomationId
  with a cache request for name/value/IsPassword. There is no tree walk. Expected cost is a few ms
  against a full read.
- **Full path:** a full `read_docs` + `flatten`. The node is matched by strict key, then by loose
  key + ctype + anchor. Exactly one match is required; zero or several means no arm.
- Tick: 0.4 s while a UID page is in front, 3 s idle (now 1.2 s / 3 s).
- One-page login: the UID is typed, so a value counts only after it has stayed the same for two
  reads in a row (about 0.4 s). An arm never happens on a half-typed id.
- Report `(service_id, uid)` instead of a text list.
- **Not found -> warning** (owner choice), shown once per page visit:
  "SCA: can't find the UID on <service>'s login page - pick it again in Settings -> SCA."
  There is no fallback to whole-page matching.

### WP5: cleanup rule
- Built-in rules, picked automatically and shown in the picker, with an option to change them:
  `GSTIN`, `PAN`, `EMAIL`, `LOGIN_ID` (one compact token), `WHOLE` (the text as is).
- A rule *finds* the UID inside longer text, for example "GSTIN: 27ABCDE1234F1Z5" ->
  "27ABCDE1234F1Z5", or "Welcome abc@gmail.com" -> "abc@gmail.com".
- Email: the UID is the part before "@" ("abc@gmail.com" -> "abc"), because that is what the
  database stores (D3).
- Uses `sca_protocol.normalize_uid` so the Python and extension sides agree.

### WP6: arm per service
- `handle_source_uid(service_id, uid)` looks the UID up in the client index.
  - Only a client who has that service is armed, and only for that service.
  - This brings back a host check: the arm is no longer for all of the client's services.
- The existing rules still apply: suppressed and paused clients, the unused-arm streak, and the
  "already armed for this client" check.
- The trigger name `"source"` stays. Delivery (widget, etc.) is unchanged.

### WP7: retire old triggers
- `SCA_UID_PROBE`: finish removing it, including the `login.js` probe code in both extensions.
  Delete `tests/js` cases that only covered it.
- Clipboard trigger: see decision D2.
- Remove `page_texts` and `handle_source_texts` once WP4 and WP6 are in.

### WP8: tests and a live check
- Unit tests:
  - fingerprint matching: same page; a node shifted by an inserted banner; a renamed class; two
    candidates (no arm);
  - each cleanup rule;
  - the half-typed-id debounce;
  - the not-found warning shown once per visit;
  - arming only for that service.
- Live check: one one-page portal and one two-page portal. Pick, then measure how long it takes
  from the page being in front to the arm (target under 1 s). Report the result.
- Run the full suite with the live T/F/S plugin.

## 3. Order

WP1 -> WP2 -> WP3 -> WP5 -> WP4 -> WP6 -> WP7 -> WP8. WP5 comes before WP4 because the picker
preview and the watcher share the cleanup code.

## 4. Decisions for the owner

- **D1. DECIDED (2026-11-07): loopback + token.** A one-shot server on 127.0.0.1, on a random
  port, with a one-time token.
  - It accepts a single POST `{token, k}`, then closes when the pick is done or after 5 min.
  - A wrong token or a second POST is refused.
  - The POST body holds only the node index. Sera already holds the read.
  - The WS bridge is not used: its origin rules (SCA v2) would have to be widened for `file://`.
- **D2. DECIDED:** the clipboard trigger stays until WP8 proves the node watcher on real portals.
  Removing it is a separate, final step.
- **D3. DECIDED:** the database stores only the part before "@". The EMAIL rule finds the address
  and keeps that part ("abc@gmail.com" -> "abc").
- **D4. DECIDED:** build in a separate worktree (`../APP-sca`, branch `sca-uid-node`) on top of
  the uncommitted SCA work, which is copied in as its first commit.

## 5. Status (2026-11-07, branch `sca-uid-node`, worktree `../APP-sca`)

- **Built:** WP1 through WP7.
  - Code: `core/uia_render/`, `core/sca/uid_clean.py`, `uid_node.py`, `uid_picker.py`, the rewritten
    `uid_source_watch.py`, `clipboard_watch.handle_source_uid` / `handle_source_warn`,
    `services.uid_node`, and the Settings -> SCA "Pick UID" button.
  - Tests: `tests/test_sca_uid_node.py`.
  - Removed: the whole-page `page_texts` / `handle_source_texts`. `SCA_UID_PROBE` is gone from both
    extensions and from the desktop.
- **Not done:**
  - the WP8 live check on a real one-page and a real two-page portal (pick, then time from the page
    being in front to the arm);
  - removing the clipboard trigger (D2: only after the live check).
