# Plan: "Automation 1" autofill button in Tracker Dump rows

Status: PLAN ONLY, not implemented. Written for an executing agent; follow it literally.

## 1. Goal

In the Tracker Dump table (`ui/windows/tracker_dump_window.py`), each row's last column ("Actions", after the "Updated" column) currently has an eye button (Inspect/View) and a kebab `⋮` button. Add **one more button** that runs the row's portal **Automation Mode 1** (the same action the red button in the Client Detail window's SERVICES list runs), so staff can open the portal for that client straight from the tracker dump.

Look and feel: **identical to the Client Detail primary action button**
(`ui/windows/client_detail_window.py`, ~lines 515-528).

- Size in Client Detail is 36x28. Use a size that fits the tracker row (see 4.4).
- If mode 1 is Fast Autofill (`automation.ACTION_AUTOFILL`): solid red `#FF4D4D` fill, hover `#E63939`, no border, radius 6px, white icon `mdi.flash`.
- If mode 1 is SMTI (`ACTION_SMTI`, icon `mdi.clipboard-account-outline`) or MECP (`ACTION_MECP`, icon `mdi.content-copy`): background `#1A1A1A`, border `1.5px solid #FF4D4D`, radius 6px, hover `rgba(255, 77, 77, 0.2)`, icon colour `#FF4D4D`.
- Tooltip: `"{action_label}: {service name}"` (labels: "Fast Autofill", "SMTI Manual Assist", "MECP Manual Copy").

Only Automation **Mode 1**. Do NOT add a Mode 2 button, and do NOT add Alt+N shortcuts.

## 2. Hard rules (from project memory, do not break)

- Never auto-click or auto-submit anything on the government portal. This feature only calls the existing launchers (`automation._send_to_extension` etc.), exactly as Client Detail does.
- Do not duplicate the launch logic in a second place that can drift. Extract it (see 4.1) so Client Detail and Tracker Dump share ONE implementation.
- Row action cells are REUSED across refreshes (see docstring of `_set_action_cell`): a new widget per row per refresh leaked about 3 MB per refresh. Build the new button once inside `_build_action_cell`, and never capture the row `item` dict in a lambda. Look the item up on click through `_action_cell_item(cell)`, as the existing buttons do.
- Inline `setStyleSheet` on the button, like the existing ones (class-based QSS is not reliably applied to `setCellWidget` children).
- Match the surrounding code style and comment density. If you write a backslash via a shell heredoc, use the Write/Edit tools instead (`\b`/`\n` become control chars).
- There are many uncommitted changes in the working tree from other work. Do NOT `git add -A`, `git checkout .`, `git stash` or otherwise touch files you did not need to edit. Do not commit unless asked.
- Do not edit anything under `SUDR/`, `source_2/`, `.restore_points/`.

## 3. Facts you need (already verified)

- `TrackerDumpWindow.__init__(self, db, parent=None, defer_first_load=False)`, at `tracker_dump_window.py:1425`. It is created in `main.py:1578`: `TrackerDumpWindow(self.db, defer_first_load=True)`. It has `self.db` but **no `actor`** and **no `action_alert_requested` signal**. `ClientDetailWindow` has both (`Signal(str, str)` at line 103; it is connected to `self.shell.show_action_alert` in `main.py:1600`).
- Action cell code: `_build_action_cell` (~line 2345), `_set_action_cell` (~2293), `_action_cell_item` (~2323), `_on_action_view` / `_on_action_more` / `_on_action_create` (~2331-2321). `_set_action_cell` is called from both the grouped table (`~2537`, `is_grouped=True`) and the flat table (`~2604`, `is_grouped=False`).
- Column widths are set in `_adjust_table_columns` (~1925): `COL_ACTIONS: 100`. Three buttons (30 + 24 + about 32, plus 4px spacing and margins) will not fit in 100px, so widen it (see 4.4).
- A row item (`item`) has `client_id` (int or None/0 when unmatched), `is_unassigned`, `portal`, and in flat view also `service_name` (used at ~line 2572: `r.get("service_name") or r.get("portal")`). Confirm the exact keys by reading `_populate_grouped_table` (~2466-2540) and the flat populate function (~2545-2610) and the SQL that builds the rows in `database.py`. Item keys may differ between the two views.
- Services live in the DB: `db.get_client_services(client_id)` (database.py:2292) returns the list of service dicts for a client (`id`, `name`, `automation_mode`, `automation_mode_2`, `userid_column_id`, `password_column_id`, ...). `db.get_client(client_id)` (database.py:2288) returns the client with `values` keyed by column id. `db.get_services()` returns all services (used at tracker_dump_window.py:588 with name matching).
- Mode helpers: `automation.service_action_mode(service)` returns one of `ACTION_AUTOFILL` / `ACTION_SMTI` / `ACTION_MECP`.
- Client Detail launchers, all in `client_detail_window.py`: `_launch_extension_autofill` (635), `_launch_manual_assist` (748), `_launch_manual_copy` (705), plus helpers `_get_credentials` (630), `_get_identity_label` (626), `_get_identity_parts` (619), `_suppress_sca_for_client` (700), `_on_autofill_failed` (771). They read `self.client`, `self.db`, `self.actor`, `self._bridge`. They call `self.window().showMinimized()` at the end.

## 4. Implementation steps

### 4.1 Reuse strategy (decide first, simplest safe option)

Recommended: **do not refactor the launchers into a new module.** Instead, have Tracker Dump reuse the real `ClientDetailWindow` launchers through the window that already exists in the app.

1. In `main.py` (around line 1574-1604, where `self.detail_win` and `self.tracker_dump_win` both exist), give `TrackerDumpWindow` a way to reach the launchers. Cleanest: add a public method to `ClientDetailWindow`:

   ```python
   def run_service_action(self, client_id: int, service: dict) -> None:
       """Loads the client (without showing the window) and runs the service's Automation Mode 1 action."""
   ```

   It must: fetch `self.client = self.db.get_client(client_id)` if `self.client` is None or `self.client["id"] != client_id`; pick `fn` by `automation.service_action_mode(service)` from a dict mapping `ACTION_AUTOFILL -> self._launch_extension_autofill`, `ACTION_SMTI -> self._launch_manual_assist`, `ACTION_MECP -> self._launch_manual_copy`; call `fn(service)`.

   **Check first** that the launchers only need `self.client` and do not need the widget to be visible or a UI build. `self.window().showMinimized()` at the end of each launcher minimises the top-level window, which is the main app shell, and that is the desired behaviour (same as Client Detail: the browser/portal comes to the front). Verify `self.window()` returns the shell when `detail_win` is a hidden page inside `self.shell` (`add_page`); if `detail_win` is not parented in the shell, `self.window()` would return only the detail widget itself and nothing would minimise. If so, minimise the shell from the tracker side instead (`self.window().showMinimized()` in the tracker's click handler after the call).

   **Danger:** overwriting `detail_win.client` while the user has a different client open in Client Detail would change what that window shows or saves (notes auto-save uses `self.client`). To avoid this, do NOT reassign `self.client` on `detail_win`. Instead, either (a) temporarily swap and restore in a `try/finally`, or (b, better) add an optional `client` argument to the launcher helpers. The simplest correct approach is (a): save `prev = self.client`, set `self.client = fresh`, call `fn(service)`, then in `finally` set `self.client = prev`. Launchers run synchronously up to the point where they hand off to `automation.*`, so the swap is safe. Confirm by reading each launcher fully that none of them defers use of `self.client` to a timer or callback (the `on_error` lambdas capture only `service['name']`; `_launch_manual_copy` reads `self.client` only synchronously). If any launcher DOES use `self.client` later in a callback, capture what it needs before the restore, or fall back to option (c) below.

   Option (c), if (a) proves unsafe: instantiate a private `ClientDetailWindow(self.db, actor=...)` once, kept hidden and never added to the shell, used only as a launcher host. Then connect its `action_alert_requested` to the shell like `main.py:1600` does, and minimise the shell yourself. Only use this if (a) and (b) fail, because it costs a whole extra window's memory (memory is a tracked concern, see `memory_performance.md`).

2. Wire it: add to `TrackerDumpWindow` a signal `service_action_requested = Signal(int, dict)` (client_id, service). The tracker's button handler emits it; in `main.py` connect it to a small slot that calls `self.detail_win.run_service_action(client_id, service)`. This keeps `TrackerDumpWindow` free of any reference to another window. Put the connect next to the other tracker-dump connects in `main.py` (search near 1595-1615).

### 4.2 Resolving the service for a row

Add `_service_for_row(self, item: dict) -> Optional[dict]` in `TrackerDumpWindow`:

1. `cid = item.get("client_id")`. If falsy (unmatched or unassigned capture), return None.
2. `services = self.db.get_client_services(cid)`; if empty, return None.
3. Match the row's portal to a service by name, using the same rule already used at `tracker_dump_window.py:594`: with `portal_name = (item.get("service_name") or item.get("portal") or "").lower()`, a service matches when `s["name"].lower() in portal_name or portal_name in s["name"].lower() or ("income" in portal_name and "income" in s["name"].lower())`. Guard against an empty `portal_name` (an empty string is "in" everything, so require `portal_name` non-empty). Extract this rule into one small helper function (module-level `_service_matches_portal(service_name, portal_name)`) and have the existing line 594 call it too, so there is one definition.
4. If exactly one service matches, return it. If several match, prefer an exact (case-insensitive) name equality, else the first one. If none match, return None.
5. Cache nothing on the row; the lookup runs only on click and (for the tooltip/visibility, see 4.3) at most once per row per refresh. `get_client_services` is a DB call, so do NOT call it for every row on every refresh if it is slow. Prefer resolving lazily on click, and decide the button's icon/style per click-independent rule: see 4.3.

### 4.3 Button visibility and appearance per row

Because the icon depends on the service's mode and resolving it per row on each refresh costs a DB query, use this rule:

- The button is **shown only when the row has a `client_id`** (matched client), and hidden on unassigned rows (where `act_create` is shown instead). Do this in `_set_action_cell` next to the existing `btn_create.setVisible(...)` logic, using `item.get("client_id")` (for grouped rows use the same test as `needs_create`; grouped `is_unassigned` rows have no client).
- Style: build the button once in `_build_action_cell` with the **Fast Autofill (solid red, `mdi.flash`)** look, since that is the default mode. Then in `_set_action_cell`, if you can cheaply know the mode, restyle it. If not cheap, keep it solid red and let the click do the mode dispatch. Do not do a DB query per row just for styling. (Optional improvement, only if `get_services()` results can be fetched ONCE per table populate and cached in a dict `{lowercase name: service}` on the window, reset at the start of each populate: then `_set_action_cell` can pick the right style and tooltip without per-row queries. This is nice to have, not required.)
- On click: resolve the service (4.2). If it is None, show a `QMessageBox.information` "No matching service" with the portal name and client, and do nothing else. If found, emit `service_action_requested(cid, service)`.

Object name for the button: `act_autofill`. Add it to the cell layout **between** the eye (`act_view`) and the kebab (`act_more`)? No: put it **first** (left of the eye), so the eye and kebab positions stay where the user is used to. Hmm: keep order `[autofill][eye][⋮]`. `_set_action_cell` has a guard `cell.findChild(QPushButton, "act_view") is None` to decide whether to rebuild; also rebuild when `act_autofill` is missing, so any cells built before this change get replaced: `if cell is None or cell.findChild(QPushButton, "act_view") is None or cell.findChild(QPushButton, "act_autofill") is None`.

### 4.4 Sizing

- Button: `setFixedSize(34, 26)` is fine, or match neighbours' height (they are default height with `padding: 3px 0px`). Icon size 16x16 (`setIconSize(QSize(16, 16))`; import `QSize` if not already imported in this file, check the `from PySide... import` lines at the top).
- Widen `COL_ACTIONS` in `_adjust_table_columns` from `100` to about `150` (34 + 30 + 24 + 30 for the hidden `act_create` which is only visible on other rows + spacing/margins; check by looking at the widest case: unassigned rows show eye + kebab + create but NOT the autofill button, matched rows show autofill + eye + kebab. Max needed is about 34+30+24+2*4+4 = 100, so 120 is safe). Use **120**. The Client column is the stretch column and absorbs the difference.
- Tooltip on the button: initial default `"Automation 1 (Fast Autofill)"`; the real per-service label is shown by the click, and optionally by the cached-services improvement.

### 4.5 Client Detail window: make sure nothing regresses

- `ClientDetailWindow` behaviour must be unchanged when used normally. `run_service_action` is an additive method. Do not change the existing launchers' signatures.
- Activity/audit logging: the launchers already call `record_client_activity` and `log_action` with `self.actor`. Because the swap in 4.1(a) keeps `self.actor`, the audit trail stays correct ("autofill_extension"). Good, no extra logging needed.
- The alert toast (`action_alert_requested`) is emitted by the launcher; it is already connected to the shell in `main.py:1600`, so the same toast appears. Good.

## 5. Tests

Add `tests/test_tracker_dump_autofill_button.py` (look at an existing test that builds `TrackerDumpWindow` or a Qt widget for the harness/pattern, e.g. grep tests for `TrackerDumpWindow`, `QApplication`, or a `conftest.py` fixture; follow it; use the offscreen platform if that is what the other tests do). Cover:

1. `_service_matches_portal`: "Income Tax Portal" matches a service "Income Tax"; "GST" matches "GST"; empty portal matches nothing; unrelated names do not match.
2. `_service_for_row`: with a fake `db` returning two services, picks the right one; returns None for `client_id` None/0; None for no services; exact name wins over substring.
3. `_build_action_cell` contains an `act_autofill` button, and it appears before `act_view` in the layout.
4. `_set_action_cell` hides `act_autofill` for an unassigned row (`client_id` None, `is_unassigned` True) and shows it for a matched row; reusing a cell that lacks `act_autofill` rebuilds it.
5. Clicking `act_autofill` on a matched row emits `service_action_requested(client_id, service)` once (use a spy slot), and on an unmatched service shows no emission (patch `QMessageBox.information`).
6. `ClientDetailWindow.run_service_action`: with a fake db and monkeypatched launchers, it dispatches by mode (extension -> `_launch_extension_autofill`, smti -> `_launch_manual_assist`, manual -> `_launch_manual_copy`), and `self.client` is restored to its previous value afterwards, even if the launcher raises.

Then run the related existing tests to check nothing else broke: `python -m pytest tests -q -k "tracker or client_detail or service_automation"` and finally the full suite `python -m pytest tests -q` (note any failures that already existed before your change; do not "fix" unrelated ones; `git stash` is NOT allowed, use `git diff` to judge).

## 6. Manual verification (if a display is available)

1. Launch the app (`python main.py`). Open Tracker Dump. Matched rows show the red lightning button left of the eye; unassigned rows do not.
2. Hover: tooltip shows. Click on a matched Income Tax row: the extension autofill opens the portal login for that client, the app minimises, and the usual "autofill" toast shows. A row whose portal has no attached service shows the "No matching service" message.
3. Check a service whose Automation Mode 1 is SMTI or MECP still launches the right helper (its button in Client Detail is outlined red; the tracker button stays solid red unless the optional cached-styling improvement was done).
4. With Client Detail open on client A, click the button on a row for client B, then check Client Detail still shows/saves client A (this verifies the swap-and-restore).
5. Check memory does not grow on repeated tracker refreshes (the cell reuse rule). `memory.log` exists per project notes.

## 7. Files expected to change

- `ui/windows/tracker_dump_window.py` (signal, `_service_matches_portal`, `_service_for_row`, button in `_build_action_cell`, `_set_action_cell` rebuild guard and visibility, `_on_action_autofill`, `COL_ACTIONS` width, line ~594 reuse of the helper)
- `ui/windows/client_detail_window.py` (new `run_service_action` only)
- `main.py` (one `connect` line for `service_action_requested`)
- `tests/test_tracker_dump_autofill_button.py` (new)

Nothing else. If you find you need to touch other files, stop and report why.

## 8. Definition of done

- Button visible on matched rows in both Grouped and Flat views, with the exact Client Detail look, and works.
- No new per-refresh widget allocation, no captured row dicts in lambdas.
- New tests pass; the full suite has no new failures.
- Report back: the files changed, which reuse option (a/b/c) you used and why, and anything unverified.
