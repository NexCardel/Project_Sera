# SGT GST fixes: plan for the implementer

Files: `core/sgt/sgt_shadow.py`, `core/sgt/sgt_specs.py` / builtin `sgt_fields.json`, `core/sgt/sgt_resolver.py`,
tests in `tests/test_sgt_shadow.py` (+ new `tests/test_sgt_gst_fixes.py`), repair tool in `tools/`.
Do NOT commit, bump version.json, or touch `package_dist/`. Use Edit/Write for code (no backslash heredocs).
`sgt_fields.json` already has an uncommitted change from before this plan: keep it, do not revert it.
Run the full suite with the live plugin, per CLAUDE.md:

    $env:PYTHONPATH="tools"; python -m pytest -p live_tf -p no:terminal -p no:cacheprovider tests

13 sync-UI tests (`test_sync_golive_ui`, `test_sync_shadow_start_ui`, `test_sync_status_panel_ui`) fail on this PC
because it is in live sync mode. They are unrelated; do not touch them, just report them separately.

## What went wrong (evidence in ~/AmanAssociates_Sera/sgt_shadow/*.jsonl, corpus in sgt_corpus/)
1. On the GST returns dashboard (`return.gst.gov.in/returns/auth/dashboard`) the period dropdown and a tile
   "Status - Filed" fed the draft. A form from a `/gstr3b` link (opened for June) plus a period switched on the
   dashboard (October) gave a "complete" draft. `_update_draft` then MOVED the already-dispatched June slot
   (`prev.values.update(vals)`, sgt_shadow.py ~line 612): same slot, new key, `supersedes_dataset_key` = the June key.
   Result: the genuine June row is replaced by a false October "Submitted & Verified" row with no ARN.
2. Known cases (all on the GST portal, June row rewritten to a later period): four clients, on 2026-09-22,
   2026-09-27, 2026-10-02 and 2026-10-06 (names are in the private SGT log, not here).
3. The 'period' piece was cleared 9-10 times by OCR reads of the dashboard (OCR reads labels as `Period •`,
   which the resolver rejects), so the "different period was opened" check (`before` is empty) never fired.
4. All 11 GST "Submitted & Verified without ARN" rows took their status from the dashboard tile text.

## Owner decisions (final)
- Never move a finished slot; GST never moves rows at all.
- SGT ignores the GST returns dashboard page for the dataset (see WP1 for the exact scope).
- OCR is disabled for the GST portal. Blind GST pages are simply not read.
- Quarter ranges are captured from the page nodes and shown by END month in the tracker (Apr-Jun -> June).
- A dashboard status never proves filing.
- Fix is also needed on 5 office PCs that already shipped the fault: a repair tool is part of the work.

## Work packages (in this order; run the full suite after each)

### WP1: Ignore the GST returns dashboard for the dataset  (decisions: dashboard ignored, rule 5, rule 7)
- Resolve and absorb nothing into the in-progress dataset (form, fy, tax_period, status) from URLs matching
  `returns/auth/dashboard` on the GST portal. Keep reading it for PROFILE (GSTIN/name) and anything else it did before;
  only the `current_dataset` fields are suppressed. Implement generically as a spec/URL "skip_current" list in the
  builtin json, not a hardcoded check in `_update_draft`.
- PRECONDITION, check first and STOP/REPORT if it fails: today the period and FY come from the dashboard only.
  From the corpus (`sgt_corpus/pages_*.jsonl`, both `lines` and nodes) find what `/returns/auth/gstr1` and
  `/gstr3b` pages (and the filing confirmation pages) print for FY and the return period. If the return pages carry
  them, add/adjust current_dataset specs for them (labels as they appear on those pages). If they do not carry them
  in lines, use the node dump (WP5). If neither does, write the finding in `docs/sgt-gst-fixes-findings.md` and stop
  before WP2; do not leave GST capture unable to find a period.
- Existing GST tests that relied on the dashboard supplying the period must be rewritten to use a return page.

### WP2: Period-scoped pieces, never move finished slots  (B1-B4)
- Status and its evidence are stored with the (form, period) they were read for. When a piece set changes the draft's
  period or form, drop status/evidence pieces that were read under the old identity.
- Remember the draft's last non-empty period (`last_period`); the "a different period was opened" check compares the
  new period with `before or last_period`, so a transient clear cannot defeat it.
- A slot is movable only if: portal is not GST, it has no arn, it was never dispatched above Draft, and its page is
  still the current page. Otherwise a new period or form closes the draft and starts a new slot/key.
  The old key must never appear in `supersedes_dataset_key` for such a change.
- GST: never take the "moved" branch; always close + new slot.

### WP3: No false "Filed"  (C6, C7)
- A GST dataset reaches Submitted & Verified only with an ARN (identifier_proves already says an ARN proves
  Verified on GST), or with a status read on that form's own return page for that period.
- With the dashboard ignored (WP1) tile statuses no longer reach the draft; also make the status spec require that
  the page's form (link or heading) equals the draft's form, so a status line on one return page cannot be applied
  to another form's draft.

### WP4: Disable OCR on the GST portal
- In `_observe` (sgt_shadow.py ~line 403): when `portal` is the GST portal, never call `ocr.scan_image`. A blind GST
  page returns None WITHOUT touching pieces, so an empty read can never clear what UIA set. Make the set of
  "no-OCR portals" a constant/config (`NO_OCR_PORTALS = {"GST Portal"}`), log one `[SGT]` line per session when it
  skipped OCR, and count skipped reads in the stats.
- Out of scope: the resolver label-marker fix and the OCR-must-not-clear-UIA rule (owner left them blank).
  Mention in the final report that ITR still uses OCR fallback and still has both weaknesses.

### WP5: Quarter ranges from the nodes, displayed by end month  (D10)
- QRMP filers pick a quarter (Jan-Mar, Apr-Jun, Jul-Sep, Oct-Dec) and then a month within it. Capture what the page's
  node dump shows for the selected period (see `_nodes_for_recording` / `core/sgt_i` node reading and
  `sgt_i_uia_node_bench.py` for how nodes are read; `Selected: <value>` style lines also appear in lines).
- Store a quarter as its END month: Apr-Jun -> `June`, Jul-Sep -> `September`, Oct-Dec -> `December`,
  Jan-Mar -> `March`; period composes as usual (`June (FY 2026-27)`). Keep the raw text as evidence
  (`quarter: Apr-Jun`) so a monthly June and a quarterly Apr-Jun stay distinguishable in the log.
- Specs with examples and counter_examples as the loader requires.

### WP6: Tests  (E)
Replay as fixtures (copy the relevant log/corpus lines into the test file, no dependence on ~/AmanAssociates_Sera):
- the four logged "moved" sequences: June row stays, new row is October/September, no supersede of the June key;
- dashboard dropdown change alone does not alter the draft;
- transient clear of the period then set to another period closes the draft (uses last_period);
- GST OCR is never called, a blind GST page leaves pieces untouched;
- status read on one form's page never lands on another form's draft;
- quarter strings: Apr-Jun -> June, Jan-Mar -> March, monthly June stays June but with different evidence;
- ITR wizard "move" (change AY before submit) still works; ITR behaviour unchanged.
Re-run `tests/test_sgt_shadow.py`; the 9 idle-resume tests must still pass.

### WP7: Repair tool for this PC and the 5 office PCs  (F)
Build `tools/sgt_repair_moved.py`; DEFAULT IS DRY RUN. Only `--apply` writes anything and it writes a backup first.
- Detect from THAT PC's own logs (`~/AmanAssociates_Sera/sgt_shadow/sgt_shadow_*.jsonl`, GST portal only):
  1. every `dataset` event with `change == "moved"` whose `previous.period != values.period` and
     `previous.status` is above Draft (the June row that got overwritten), with session, client (profile events of
     that session: GSTIN/name), previous values, new values;
  2. every `current_dataset` row with status Submitted & Verified and no ARN whose evidence is dashboard text.
- Output a report (CSV + readable text, in the tool's own output folder, never over existing files): per client the
  row to RESTORE (the previous June values, key as it was before the move) and the row to REMOVE (the false one).
  Do not guess: anything ambiguous is listed as "needs a human".
- Before building `--apply`, read the tracker/storage code and `docs/sera-sync-v3-blueprint.md` and write down
  (in `docs/sgt-gst-fixes-findings.md`) HOW a corrected or removed row propagates to the other PCs, how a stale PC
  could re-push the false row, and what order of operations avoids that. Implement `--apply` only per that finding,
  using the same writer the app uses (not raw SQL unless that is how the app does it). If safe propagation is not
  possible, stop at the dry-run report and say so.
- The tool must run standalone on an office PC (no dev checkout assumptions; it ships with the release or is a
  single script plus the app's installed modules). State exactly how it is run there.
- Never delete anything from the shadow logs or the corpus.

## Report back
Files changed, tests added, full-suite T/F/S with the 13 known sync-UI failures listed separately, what WP1's
precondition found (period source on return pages), the WP7 findings document, and any decision you had to make.
Mention the open items: OCR still on for ITR; label-marker fix and OCR-clear rule not implemented by decision.
