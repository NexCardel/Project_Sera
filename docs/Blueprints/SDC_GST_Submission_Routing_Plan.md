# SDC GST Submission Routing — One-Time Implementation Plan

**Purpose:** Preserve SDC’s current multi-period dataset assembler while ensuring that the Tracker Dump records only meaningful session outcomes.

**Scope:** GST Portal SDC captures, session assembly, Tracker Dump routing, and LTT/timeline visibility.

**Status:** Planning document for one implementation cycle.

## 1. Required behavior

During a GST session, SDC may capture multiple forms and periods. The assembler must continue to retain all unique datasets using the stable dataset identity:

```text
GSTIN | Filing Form | Filing Period
```

At session finalization:

1. Every dataset with a confirmed submission during the current session is eligible for Tracker Dump.
2. The last dataset viewed in the current session is also eligible for Tracker Dump, even if it was not submitted.
3. If the last viewed dataset is also a submitted dataset, it is included only once.
4. All other captured but non-submitted datasets remain available to LTT/session timeline data only.
5. A calendar status of `Filed` must not by itself count as a submission performed during the current session.

## 2. Example outcomes

### Submitted datasets plus the same last viewed dataset

```text
Submitted: GSTR-1 / June
Submitted: GSTR-3B / June
Last viewed: GSTR-3B / June
```

Tracker Dump receives two logical datasets:

```text
GSTR-1 / June
GSTR-3B / June
```

### Submitted dataset plus a different last viewed dataset

```text
Submitted: GSTR-1 / June
Last viewed: GSTR-3B / June
```

Tracker Dump receives:

```text
GSTR-1 / June       → submitted
GSTR-3B / June      → last viewed, not submitted
```

Other viewed periods remain in LTT/timeline data and do not enter Tracker Dump.

## 3. Dataset state model

Keep `dataset_key` stable. Do not encode capture type into the key, because that would create separate records for the same return and weaken deduplication.

Add session-scoped metadata to each assembled dataset:

```json
{
  "dataset_key": "GSTIN|GSTR-1|June 2026",
  "capture_origin": "calendar_view",
  "submitted_in_session": false,
  "submission_timestamp": "",
  "submission_arn": "",
  "last_viewed_at": "..."
}
```

Recommended origins include:

- `calendar_view`
- `form_view`
- `submission_success`
- `status_view`

`submitted_in_session` is a sticky session flag. Once set to `true`, later calendar or status captures for the same dataset must not set it back to `false`.

## 4. Submission detection

Only an explicit GST submission-success crosshair may set `submitted_in_session = true`.

The submission-success capture should provide, where available:

- GSTIN/PAN
- Form
- Period
- Confirmed submission status
- ARN
- Submission timestamp
- `capture_origin: "submission_success"`

A calendar row showing `Filed`, an old ARN, or a previously filed status is historical evidence for LTT—not proof of a submission in the current session.

## 5. Assembler changes

Continue buffering one logical dataset per `dataset_key`.

When a capture matches an existing key:

- Merge the newest fields.
- Preserve `submitted_in_session: true` once it has been established.
- Preserve the submission ARN unless a newer confirmed submission provides a replacement.
- Update `last_viewed_at` for every relevant view/capture.
- Record the latest `capture_origin` separately from the submission state.

At finalization, derive two logical collections:

```text
submitted_datasets = datasets where submitted_in_session == true
last_viewed_dataset = the dataset with the greatest last_viewed_at
tracker_datasets = submitted_datasets + last_viewed_dataset, deduplicated by dataset_key
ltt_datasets = all assembled datasets
```

The final session payload should retain enough information for LTT to reconstruct all datasets, while clearly identifying the Tracker Dump candidates. Suggested fields:

```json
{
  "raw_payload": {
    "assembler_captures": [],
    "tracker_dump_captures": [],
    "ltt_captures": [],
    "last_viewed_dataset_key": "..."
  }
}
```

If keeping only one canonical collection is preferable, `assembler_captures` may remain the authoritative full set and `tracker_dump_captures` may be derived by the desktop listener. The selection rule must be implemented in one place only.

## 6. Transport and desktop routing

The current live SDC capture dispatch must not allow every calendar/form capture to create a Tracker Dump row immediately.

Recommended routing:

```text
Live capture → session timeline/LTT visibility
Session finalization → one assembled filing_result envelope
Desktop listener → select tracker_dump_captures only
```

The desktop handler should insert only the final selected candidates into `tracker_dump`.

The existing dataset-key upsert behavior remains useful: if the submitted dataset and last viewed dataset share a key, the upsert must produce one logical row. The merge must preserve confirmed submission information rather than allowing a later view capture to downgrade it.

## 7. LTT behavior

LTT must continue receiving all datasets captured in the session, including:

- Submitted periods
- Previously filed periods viewed in the calendar
- Not-filed periods
- Draft/form-view periods where the dataset identity is sufficiently known

LTT should use the session timeline and/or the full assembler collection, not only Tracker Dump rows. This prevents the Tracker Dump filter from hiding useful browsing and compliance context.

## 8. Likely implementation areas

Review and update the following areas in both Chromium and Firefox extension copies where applicable:

- `sera_extension/sdc/sdc_core.js`
- `sera_extension/sdc/protocols/gst_protocol.js`
- `sera_extension/content_scripts/filing_detector.js`
- `ui/extension_listener.py`
- `main.py`
- `SDC_Parser/sdc_parser.py`

Keep mirrored `source_2` copies synchronized if they are still part of the packaging workflow.

## 9. Deduplication and safety rules

- Deduplicate Tracker Dump candidates by `dataset_key` before insertion.
- Do not use `status == "Filed"` as a session-submission signal.
- Do not let a calendar revisit overwrite `submitted_in_session = true`.
- Do not let a non-submission capture replace a confirmed ARN with `N/A`.
- Preserve all full session timeline data for LTT/audit purposes.
- Ensure a session with no submission still records its final viewed dataset in Tracker Dump.
- Ensure a session with no identifiable dataset produces no phantom Tracker Dump row.
- Keep the existing session and double-flush guards intact.

## 10. Acceptance tests

1. View three GST periods without submitting anything. Only the last identifiable period enters Tracker Dump; all three remain in LTT.
2. Submit one period and then view a different period. Both enter Tracker Dump; only the submitted one is marked submitted.
3. Submit two periods and revisit the second one. Exactly two logical Tracker Dump datasets are produced.
4. Submit a period, revisit its calendar row, and finalize. The record remains marked as submitted and retains its ARN.
5. Start a second session and view a period submitted in the first session. It appears in LTT but does not create a new submission record in Tracker Dump unless it is the last viewed dataset under the explicit last-viewed rule.
6. Capture multiple forms for the same period. Each form remains a separate dataset.
7. Revisit the same form and period repeatedly. No duplicate Tracker Dump rows are created.
8. Trigger logout/finalization through competing paths. Only one final assembled envelope is processed.
9. Verify LTT still includes non-submitted datasets after Tracker Dump filtering.
10. Repeat the tests in both packaged browser-extension variants used for production.

## 11. Completion criteria

The change is complete when:

- SDC still supports multiple forms and periods in one session.
- Tracker Dump receives all session submissions plus the last viewed dataset, deduplicated by stable key.
- Historical calendar views cannot falsely become current-session submissions.
- LTT retains the full captured session context.
- Existing delivery, compression, session timeline, and double-flush behavior remains intact.
- The acceptance tests pass against a rebuilt/reloaded extension and restarted desktop application.
