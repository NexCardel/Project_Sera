# GST ARN recovery: test injector (plan)

Status: plan, not built. The builder is another agent; Claude reviews the result.
Date: 2026-10-08.

## Why

On 5 and 6 October 2026 (the first two days the app ran in the office), the SGT session-boundary
bug and the ARN capture bug left GST filings in the tracker as **unknown rows that have only an
ARN**: no client, no form, no period. The SDIS corpus from those days holds the full page text, so
a later recovery tool (`tools/recover_gst_arn.py`, separate plan) will rebuild each row from it.

The office files are not available until tomorrow at the earliest. This PC has real GST corpus
(`sdis_capture/sdis_2026-10-06.jsonl`, 42 GST sessions, 35 with a header GSTIN) but **no ARN
pages**, because no real filing was made here. The injector creates a realistic test case on this PC
so the recovery tool can be built and checked today. This PC's tracker dump is test data only.

## What it makes

`tools/inject_unknown_gst_arn.py`, one script with three commands:

- `inject` creates 3 failed GST rows dated **2026-10-06** and a matching test corpus file.
- `remove` deletes exactly what `inject` made, nothing else.
- `status` lists what is currently injected.

### 1. Test corpus (never the live capture folder)

- Read the real `~/AmanAssociates_Sera/sdis_capture/sdis_2026-10-06.jsonl`. **Never write to it**,
  and never write into `sdis_capture/` or `sdis/corpus/`. The recorder and the excavator own those
  folders; anything there gets shipped or mined.
- Write a copy to `~/AmanAssociates_Sera/sdis/recover_test/<device>/sdis_2026-10-06.jsonl`, with
  the synthetic ARN pages inserted in timestamp order. `<device>` is this PC's real device id (the
  same folder name the admin corpus uses), so the recovery tool reads it exactly like
  `corpus/<device>/`.
- Pick 3 host sessions from the real file: GST sessions where SGT's **own** header rule finds
  exactly one client GSTIN (use the shipped `gst_gstin` / `gst_header_name` specs through the SGT
  resolver, not a new regex), preferably ones that also show a "Return Type" and a tax period.
  If fewer than 3 qualify, stop and say so; don't fall back to weaker sessions.
- Each synthetic ARN page is a **clone of a real page record from its host session** (same record
  shape `{v, ts, started, session, portal, url, link, title, browser, docs}`), with one extra text
  node added: the GST success banner carrying the ARN. Take the banner wording from the real
  recorded success pages the SGT tests already use (see `tests/test_sgt_resolver.py` and the specs
  from commits 1846584 / c8ff732). Don't invent wording. The page must pass SGT's own by-shape ARN
  read; check this in the script by running the resolver on the cloned page.
- ARN: `AA` + the GSTIN's 2-digit state code + `1026` + `9999` + 2 digits + a check letter
  (for example `AA191026999901T`). It's valid by shape, and the `9999` marks it as fake at a glance.

### 2. The three cases (one per row)

| # | Case | Corpus | Expected recovery result |
|---|------|--------|--------------------------|
| A | Clean | The ARN page has the **same** `session` as the header page, a few minutes later | sure: the host session's GSTIN, form and period |
| B | Boundary split | The ARN page has a **new** session id with no header in it (as after the tab-switch bug); the header is in the host session, same `browser`, 1–10 min earlier | sure, found only by looking back by time and browser, not by session id |
| C | ARN page dropped | **No** ARN page is written (as when the recorder drops a slow read); the row's time and session point at the host session | not "sure" from the ARN; the tool must fall back to time and session and say so (sure only if exactly one client is in that window, otherwise "needs your pick") |

Write the truth to `~/AmanAssociates_Sera/sdis/recover_test/expected.json`: per row, the ARN, the
injected row id, the case, the host session, and the true GSTIN, name, form and period. This is what
the recovery tool's dry run is scored against.

### 3. Tracker rows

Each row must look exactly like what SGT live wrote for an unconfirmed session in 2.12.2–2.12.5.
Don't hand-write a guess:

- Build the payload with `SgtShadow._tracker_payload` (core/sgt/sgt_shadow.py) on a stub session
  that is **not confirmed** and has a slot holding only `arn` and `status` "Submitted (Not
  Verified)". This gives `gstin`/`pan` "", `filing_type` "GST Return", `period_label` "",
  `capture_method` "SGT_live", `session_id` `SGT-<id>`, and a `live_key` of the form
  `GST…:SGT<id>:…:ARN <arn>`.
- `session_id`: the ARN page's session (cases A and B) or the host session (case C).
- Insert through the **same path the app uses**: `insert_tracker_dump(...)` with the arguments
  main.py:1084 passes. Don't use raw SQL for the insert.
- Then set `created_at` (UTC ISO, as `insert_tracker_dump` writes it) and the payload's
  `filing_date` to the ARN page's time (for case C, the time the dropped page would have had).
  This is the only direct UPDATE.
- `captured_by`: this PC's actor name, as the app sets it.
- Record every inserted row id in `expected.json`. `remove` deletes only those ids, through
  `delete_tracker_dump`, and the `recover_test/` folder. Never match rows by pattern (see the rule
  on never wildcard-deleting user output).

### 4. Safety gates (refuse to run otherwise)

- The database is SQLCipher-encrypted. Open it the way main.py does (same `SeraDatabase` and key
  loading). Never print or log the key.
- **Sync must be off.** Read `_sync_meta.mode`. If it is `shadow` or `live`, the capture triggers
  would send these fake rows to the office PCs, so refuse with a clear message.
- The app must be closed. If it is running, refuse; the app holds the database and its
  in-memory tracker.
- `inject` refuses if `expected.json` already exists (run `remove` first), so a second run never
  adds rows twice.
- Logs and console print counts, ids, ARNs and times only, never page text. The corpus holds client
  data.

### 5. Done when

- `inject`, then open the app: the tracker dump shows 3 unknown GST rows on 6 Oct with ARNs ending
  `9999..`, the same way the real failed office rows show.
- Run SGT's resolver over each injected ARN page: it reads the ARN (cases A and B).
- `remove`, then open the app: those 3 rows are gone, every other row is unchanged (compare the row
  count and the max id before and after), `recover_test/` is gone, and `sdis_capture/` is
  byte-identical to before (compare hashes).
- Tests in `tests/test_inject_unknown_gst_arn.py` use a temp database and a temp corpus. They
  cover: the three cases written correctly, the sync gate, the double-inject refusal, `remove`
  touching only its ids, and the live capture file never opened for write. Run the suite the way
  CLAUDE.md says (live T/F/S plugin).

## Not in this plan

- The recovery tool itself, which is the next plan, once the injector is reviewed.
- Phase 2 SDIS mining (`mine.py` reading `corpus/<device>/`).
- Any change to SGT or the recorder.
