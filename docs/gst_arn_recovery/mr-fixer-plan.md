# mr_fixer.py: find, report and fix failed GST ARN rows (plan)

Status: plan, not built. Another agent builds it; Claude reviews.
Date: 2026-10-08. Follows [injector-plan.md](injector-plan.md); test data is in place on this PC
(3 injected rows, `sdis/recover_test/`, sync paused).

## The job

SGT can write a GST filing as a row holding only an ARN, with no client, form or period. It did so
on 5 and 6 Oct 2026 (the session-boundary and ARN bugs), and may again someday. `tools/mr_fixer.py`
is not tied to those dates: it works on any day that has corpus pages. It does three things, as three
separate commands:

1. **`scan`** finds those rows.
2. **`report`** finds each one's client, form and period in the SDIS corpus. It writes a report
   and changes nothing.
3. **`apply`** fixes the rows the report marks as fixable, exactly as the report says. **`undo`**
   puts them back.

`scan` is just `report` without the corpus work: a quick count. `apply` never works anything out
again. It reads the report file, so what gets fixed is exactly what you read, including any
picks you made in it.

## 1. Which rows (scan)

A row is a target when all of these hold:
- `capture_method` starts with `SGT`, and the portal is GST.
- `arn_number` is present (not `N/A`) and has the GST ARN shape: use the shipped `gst_submit_success`
  `arn` pattern from `core/sgt/sgt_fields.json`, not a new regex.
- There is no client: `client_id` is NULL, and the payload's `gstin` and `pan` are empty.
- There is no form or period: `period_label` is empty, or `dataset_key` is the ARN-only form SGT's
  `live_key` writes (`... SGT<session> ... ARN <arn>`).
- **There is no date filter by default:** every such row, whatever its date. `--from` / `--to`
  (local dates, inclusive) narrow it only when given. `created_at` is stored in UTC; read it as
  local time for the date.

On this PC that finds the 3 injected rows, plus any real old ones. `scan` prints only the count
per day and the row ids. It also marks the days that have no corpus file in the given folders:
those rows will come out `not found` in the report.

## 2. Where the pages are

`--corpus <dir>` can be given more than once. The default is the admin corpus
(`core.sdis.paths.data_dir()/corpus`, every `<device>/` folder) plus this PC's own
`sdis_capture/`. The tool reads `sdis_YYYY-MM-DD.jsonl` files recursively, but only the days the
target rows fall on (plus the day before, for a row just after midnight). A file is never opened for writing. For the test: `--corpus
~/AmanAssociates_Sera/sdis/recover_test`.

Each page is tagged with its folder (the device) and file. Session ids are random 12-hex values, so
a session id found in any folder pins down the device. A row's `captured_by` (a person's name) is
not used to find the device.

## 3. Finding the answer for one row (report)

Read every page with SGT's own reader (`resolve_page` on `lines_from_nodes(docs)` with the
built-in registry, exactly as `tools/inject_unknown_gst_arn.py` does). Never write new regexes for
GSTIN, name, form, period or ARN. Only resolve pages near an anchor: whole-day resolving costs
about 40 s per 100 MB.

**Step 1, the anchor** (the moment of filing):
- **a. ARN page:** a page whose SGT `arn` read equals the row's ARN. Take the first one. This is the
  strongest evidence.
- **b. Session:** if there is no ARN page, use the row's session id (payload `session_id` = `SGT-<id>`)
  to find that session's pages, and take the last one at or before the row's time (+2 min slack).
- If neither exists, the result is **not found**.

**Step 2, the client:** in the anchor's device file, same `browser`, look back from the anchor's
time at most 30 minutes. The nearest page with a header GSTIN (SGT's `gst_gstin` header rule) gives
the client; the name comes from the same header (`gst_header_name`).

**Step 3, form, period and filing preference:** take them from the same client's pages in that
window (latest first), composed the way SGT composes them (`compose_values`, as the injector's
`survey_hosts` does). The status stays the row's own; it is never lowered.

**Step 4, confidence.** Each row gets one result:

| Result | When | apply |
|---|---|---|
| `sure` | The anchor is an ARN page, and no **other** client's header is in the same browser between the client's last header and the anchor | fixes |
| `sure` | The anchor is a session, the session itself shows exactly one client, and no other client's header is in the same browser between that client's last header and the row time | fixes |
| `client only` | One of the `sure` rules holds, but no form + period was found | skipped unless `--include-client-only`; the key then keeps `ARN <arn>` in the period place |
| `needs pick` | Another client's header sits in the window (two clients interleaved), or the anchor's session shows more than one client | skipped until you fill `pick` in the report |
| `not found` | No corpus file for the row's day, no anchor, or no header within 30 min (the reason says which) | never |

Also say whether the fix will **merge** with a row that already exists. If a row for the new
dataset key exists (for example the same filing's Draft row under the right client), name its id
and status: `insert_tracker_dump` will replace it and keep the higher status.

## 4. The report

Write it to `~/AmanAssociates_Sera/mr_fixer/report_<YYYYmmdd_HHMMSS>.csv`, a CSV only (UTF-8 with a
BOM so Excel shows names right). Keep it on this PC: it holds client names and GSTINs, so it is never logged, synced or
sent anywhere. Columns:

`row_id, arn, row_time, result, reason, evidence (ARN page / session, device, page time, minutes
looked back), gstin, client_name, master_client (the name in the vault if the GSTIN is a known
client, else "new"), form, period, filing_pref, merges_with_row, old_dataset_key, new_dataset_key,
candidates (for needs pick: each GSTIN + name + last seen time), pick`

`pick` is empty. To settle a `needs pick` row, type one of its candidate GSTINs there. The console
prints only counts per result and the report path.

**`--expected <expected.json>`** (test only) scores the report against the injector's truth and
prints per case: right, wrong or not fixed. **A wrong `sure` is a failure**; a `needs pick` is
acceptable.

## 5. Fixing (apply)

`apply <report.csv>` refuses while Sera is open (same check as the injector; move
`app_is_running` and `_open_database` into a shared `tools/sera_tool_common.py` used by both
scripts). For each `sure` row (plus picked rows, plus `client only` with the flag):

1. **Check the row is unchanged.** Re-read the row by id. Its `arn_number`, `dataset_key` and
   `capture_method` must still match the report; otherwise skip it with "changed since the
   report".
2. **Save it for undo.** Append the whole old row (every column) to
   `mr_fixer/undo_<report stamp>.jsonl` before touching it.
3. **Fix it the way SGT does** when it learns the client (main.py:1077-1098). Build the new payload
   with `SgtShadow._tracker_payload` on a **confirmed** stub session: profile gstin, pan
   (`gstin[2:12]`) and name; slot values arn, status (the row's), form, period, filing_type (pref).
   This makes the canonical `live_key`, and `supersedes_dataset_key` = the old key. Then call
   `db.delete_sgt_rows_by_dataset_key(old_key)` and `db.insert_tracker_dump(...)` with exactly the
   arguments main.py passes. The payload's `raw_payload` also gets
   `"recovered_by": {"tool": "mr_fixer", "report": <name>, "evidence": <short evidence>}`.
4. **Keep the filing time.** Set the new row's `created_at` to the old row's `created_at`, and the
   payload's `filing_date` to the old row's.
5. Log one line per row: id old → new, ARN, result. No names or GSTINs.

**Sync is not gated.** On the admin PC with sync live, the fixes go to every PC. That's the point of
the real run, and `apply` says so and asks `type YES` before starting. On this test PC sync is
paused by the injector, so nothing leaves.

`undo <report.csv>` reads the undo file. For each saved row, it deletes the new row (by the id the
apply log recorded, only if it still holds the same ARN), then re-inserts the old row with its
**original id** and columns. That way the injector's `remove` still works after a test round.

## 6. Test round on this PC (done when)

1. `python tools/mr_fixer.py scan` → 3 rows (plus any real old ones, listed separately).
2. `python tools/mr_fixer.py report --corpus ~/AmanAssociates_Sera/sdis/recover_test --expected
   ~/AmanAssociates_Sera/sdis/recover_test/expected.json` → A sure ✓, B sure ✓, C sure ✓ or
   needs pick, **no wrong**.
3. Close Sera, `apply` → open Sera: the 3 rows now show under their clients with form and
   period, dated 6 Oct.
4. Close Sera, `undo` → the 3 unknown rows are back with their ids. Then the injector's `remove`
   → clean, and sync is back to live.
5. Tests in `tests/test_mr_fixer.py` use a temp database and the injector's fictional pages:
   - scan's filters: has client, ITR, non-SGT and N/A ARN are all left out
   - with no `--from`/`--to`, rows on any date are found; with them, only those dates
   - a row on a day with no corpus file → `not found`, reason "no pages for that day"
   - each confidence result
   - interleaved clients → needs pick, never sure
   - apply refusing a changed row
   - merge with an existing Draft row keeps the higher status
   - undo restoring ids
   - apply acts only on report rows
   - no page text, GSTINs or names in the logs

   Run the full suite the CLAUDE.md way.

## 7. The real run (tomorrow, admin PC)

1. Collect the office PCs' 5 and 6 Oct files: through the excavator, or copied by hand into a
   folder passed with `--corpus`.
2. Run `report` and review it with the user; fill `pick` where needed.
3. Run `apply`. First check that one real failed row matches what `scan` expects. The injector
   copied SGT's unconfirmed-row shape from the code, not from a real row.

## Not in this plan

- Rows on days without corpus pages, such as before the recorder shipped on 4–5 Oct. The report
  lists them as `not found`; it can't fix them.
- ITR rows.
- Phase 2 SDIS mining.
- Changing SGT itself (the bugs are fixed in 2.12.6).

## Change 2026-10-09: it runs on its own at start-up (no human review)

Decision (user): instead of excavating first, then reviewing a report, the app runs the fixer itself
on the admin PC a while after start-up, fixes what comes out `sure`, and the excavator brings the
staff corpus in meanwhile. Each start has less corpus to cover; rows with no corpus yet wait.

- Engine moved to `core/sdis/arn_fixer.py` (the installed app cannot import `tools/`);
  `tools/mr_fixer.py` is the command line over it, and is the way to **undo**.
- `core/sdis/arn_autofix.py`: `start_background` (admin PC only, `sync_admin.is_admin_pc`, 45 s after
  start-up, daemon thread, never raises) and `run_once`. Started from `main.py` after the bridge is
  wired; a `sync_bridge` signal refreshes the tracker view on the main thread; one sync-panel line.
- Only `sure` rows are applied. `client only`, `needs pick`, `not found` stay and are tried at the
  next start. Rows younger than 1 hour are left (a live SGT session may fix its own row).
- A pass that changes anything first writes `mr_fixer/auto_report_<time>.csv` (local, has names) and an
  undo file: `python tools/mr_fixer.py undo ~/AmanAssociates_Sera/mr_fixer/auto_report_<time>.csv`.
- Off switch: an empty file `~/AmanAssociates_Sera/mr_fixer/disabled`.
- Fixes sync to every PC (sync is not paused), as with the manual apply.
- While the injector's test rows exist, `sdis/recover_test` is also read, so the test rows fix on
  the next start; undo with the auto report, then the injector's `remove`.
