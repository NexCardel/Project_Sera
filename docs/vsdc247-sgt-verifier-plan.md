# VSDC247-V — the SGT verifier (OCR cross-check of SGT on the GST portal)

**Status:** PLAN, nothing built. Written 2026-10-17 from the owner's brief in chat.
**Base document:** [vsdc247-safety-net.md](vsdc247-safety-net.md) — VSDC247 as it exists today. This plan
*reuses* that engine and keeps its file in place (other docs and notes link to it). Read it first: the scope
gate, the ARN precision rules and the alert path described there are inherited unchanged.
**Name to search for:** `VSDC247-V`, "SGT verifier", `vsdc247_verify` (code names below).

## 1. Why

SGT stopped capturing the GST portal without anyone noticing (see the 2026-10-13 blind incident,
`logs/vsdc.log` work, `docs/sgt-stress-problem-report.md`). It has not happened again, but the owner wants
**independent confirmation that SGT is working**. SGT reads the page with UI Automation; VSDC247 reads
pixels with Windows OCR. If UIA goes blind, OCR still sees the page, and the two will disagree.

VSDC247-V is therefore a **checker, not a capture path**. It never decides what is in the tracker; it only
answers, for each GST filing the user completes: *did SGT also get it, and for the same client?*

## 2. Scope — decided by the owner, do not widen

* **GST portal only.** Not Income Tax. **Not global** like SGT: it knows exactly three page kinds.
  1. **GST login page** → read the **username** → look it up in the clients database → the client.
  2. **GSTR-1 submission/filed page** → read the **ARN**.
  3. **GSTR-3B submission/filed page** → read the **ARN**.
* Anything else on the portal is ignored (no status pages, no dashboards, no other forms).
* Strictly passive like all of VSDC: screenshot + OCR only; never clicks, types or injects.
* **It must not affect the tracker dump.** Two narrow exceptions, both owner-approved and both flagged:
  (a) a small `vsdc247` block inside the **payload JSON of the SGT row** that says matched / not matched;
  (b) when SGT captured nothing but OCR saw an ARN, an **independent, highlighted row** so the miss can be
  reconciled later (section 6).

## 3. What is reused, what is new

| Reused as-is | Where |
| :--- | :--- |
| GST ARN token + label-anchored detection, success wording, vetoes, GSTR form + period extraction | `core/vsdc/vsdc247.py` (`find_candidates`, `analyze_frame`) |
| Per-window change/green-box cadence, "first frame gets one read" rule | `Vsdc247Scanner` |
| Portal scope gate (parsed hostname, allowlist file, no look-alikes) | `core/vsdc/vsdc_scope.py` |
| Username → client (unique owner or nothing; never guesses) | `core/sgt/sgt_client_link.py` `ClientLink` (the router already holds it via `set_client_lookup`) |
| Payload helpers | `stamp_device_name`, `sanitize_page_url`, `build_247_payload` |
| ntfy phone alert, rate limit, no-client-data rule | `core/vsdc/vsdc_alerts.py` |
| Purple "VSDC247" colour in the tracker window | `ui/windows/tracker_dump_window.py` `_capture_method_color` |

New: the **page registry** (the three pages), the **login-username reader**, the **window→client link**, the
**ledger + reconciler**, the **annotation / flagged-row sink**, the **engine switch**, and the **row highlight**.

## 4. Independence rule (the whole point)

The observation path must not share SGT's failure modes. Therefore:

* Own screenshot (`ocr.capture_window_image`) and own OCR. **No UI Automation page reads.**
* SGT is consulted **only in the reconcile step** (what ARNs did SGT produce), never to decide what OCR sees.
* The address bar is currently read through UIA (`extract_browser_url` → `vsdc_uia_text.address_bar_here`),
  the same stack that wedged in the incident. If that read fails, the verifier must **not** go blind with
  SGT: fall back to the window title (GST-titled, last verified ≤ 8 s ago — the existing grace) and, past that,
  **OCR a crop of the address-bar strip** of the screenshot for the host. If neither works for a window titled
  like the GST portal, record `verifier_blind` in the verifier log and raise one HUD prompt. A verifier that
  silently stops is the original problem again.
* Verifier health is logged next to `vsdc.log` (heartbeat: windows seen, GST frames read, logins seen, ARNs
  seen, matched / missed counts).

## 5. Observation

### 5.1 Page registry (`core/vsdc/vsdc247_verify_pages.py`, new)
A small table: page kind → host, URL path pattern, and the OCR cues that confirm it (so a URL alone never
makes a page "registered"). Kinds: `gst_login`, `gstr1_submit`, `gstr3b_submit`. **The exact URLs and
on-screen wording are not known from this repository alone** — WP0 collects them from real captures before
anything is coded (the SGT page corpus under `core/sgt/sgt_corpus.py`, the `gst_arn_recovery` work, and one
fresh walk-through on the live portal with the owner). Do not guess URLs.

### 5.2 Login username
On `gst_login`, OCR the frame and take the value of the **Username** field (label-anchored, like the ARN).
Never read, keep or log the password field or anything else on the page. Keep the **last stable** reading
(same value on two consecutive frames) per window, because the field is typed or autofilled before the click
and the page changes on login. Then `ClientLink("GST Portal", username)` → `{client_id, gstin, pan, name}` or
`None`.

### 5.3 Linking the username to a later ARN
OCR cannot see browser tabs. Rule: a username binds to the **window (hwnd)** it was read in and applies to
ARNs read in that window until another login page is read there or the window has been idle for 4 hours
(same limit SGT uses). Because that can be wrong with several GST tabs in one window:
* Every link carries `link: "window"` (low confidence) unless the ARN page itself shows a GSTIN / legal name
  that agrees with the client (`link: "page"`), in which case it is confirmed. A page identity that
  **contradicts** the linked client wins and the result is `client: "conflict"` — never a silent guess.
* No username seen in the window → `client: "unknown"` (not a failure of SGT).
* **The ARN is the verification key, the client is the second layer.** An ARN is unique, so "SGT has this
  ARN" is decided on the ARN alone; client agreement is reported separately.

### 5.4 ARN pages
Run `analyze_frame` for the GST portal with the page kind known. All of VSDC247's precision rules apply
(label-anchored, one identifier per frame, success wording, vetoes, CERTAIN vs PROBABLE, single-frame vs
two-read agreement). Only a **CERTAIN** reading enters the ledger; PROBABLE is logged only. Form (GSTR-1 /
3B) and period come from the frame when stated, else blank — never invented.

## 6. Reconciliation

### 6.1 Ledger
In memory plus a small append-only file (`~/AmanAssociates_Sera/logs/vsdc247_verify.jsonl`, structured
fields only — **no page text**): one entry per distinct OCR-seen ARN: `arn, form, period, seen_at, hwnd,
username, client link + confidence, page_url (query stripped), device_name`.

### 6.2 Looking SGT up
After a **grace period** (default 90 s, configurable; SGT may wait for a status or a second sighting), find
SGT's answer for the ARN in this order: SGT's own outbox/slots for sessions still open (cheap, in memory);
then `tracker_dump` rows with `capture_method LIKE 'SGT%'` and the same `arn_number`. Read-only.

### 6.3 Outcomes

| Outcome | Meaning | What is written |
| :--- | :--- | :--- |
| `matched` | SGT row exists with this ARN; client agrees (or both unknown) | block on the SGT row |
| `matched_client_differs` | same ARN, SGT names a different client than the username link | block on the SGT row + HUD/ntfy (this is a real SGT or link error) |
| `missed` | no SGT row with this ARN after the grace | **independent flagged row** + ntfy alert |
| `sgt_only` | SGT row with an ARN OCR never saw | block `{"checked": false, "reason": "not seen by OCR"}` — informational, no alert (the window may not have been in front) |

### 6.4 The `vsdc247` block on the SGT row
Added to the row's `raw_payload` as `raw_payload["vsdc247"]`, beside `sgt_dataset` / `sgt_i`, never
touching those keys:

```json
{"checked": true, "result": "matched", "arn_seen": "AA0000000000000", "seen_at": "...",
 "form": "GSTR-3B", "period": "...", "page_kind": "gstr3b_submit",
 "username": "...", "client": "agrees|unknown|conflict", "link": "window|page",
 "sgt_delay_sec": 12, "page_url": "...", "verifier_version": 1}
```

It is written by a **new targeted function in `sera_db/tracker_dump.py`** (`annotate_vsdc247(arn, block)`:
merge one key into `raw_payload_json`, idempotent, no other column changed). It must **not** go through
`insert_tracker_dump` (that function purges and de-duplicates by dataset key, status rank and drafts, none of
which may be triggered by a checker).

### 6.5 The independent flagged row (`missed`)
* `capture_method = "VSDC247_verify"` (already purple in the table; the existing `VSDC247_` prefix rule).
* Its own `dataset_key` namespace (`V247:GST:<ARN>`), exactly like SGT shadow's `SGT:` namespace, so it can
  never merge with, replace or be replaced by a real row and never trips the monotonic-status or draft
  guards.
* Client from the username link when known, else `Pending_<ARN>`; status "Submitted" with the ARN, since the
  page said so; `raw_payload.vsdc247 = {"result": "missed", ...}`.
* **Highlighted** in the Tracker Dump window (row background + a "Seen only by OCR — SGT missed it" tooltip).
  Minimal change in `ui/windows/tracker_dump_window.py`; the UI overhaul worktree (`../APP-ui`) also touches
  this file, so keep the edit small and isolated and tell that session.
* If SGT **later** delivers the same ARN, the flagged row is removed and the SGT row gets
  `{"result": "matched", "late": true, "sgt_delay_sec": N}` — the miss stays in `vsdc247_verify.jsonl`.
  (Recommended; confirm with the owner.)
* One ntfy alert per missed ARN, using the existing no-client-data format.

## 7. Wiring

* New setting `vsdc247_verify` (Settings → Tracker, next to the existing engine switches; default **off**
  until the owner ticks it, like `vsdc247_enabled`). **Independent of `vsdc247_enabled`** — that one is the
  old capture engine and must stay off for the verifier to mean anything.
* Today VSDC247 only runs inside `_crosshair_tick`, and with SGT alone (`_sgt_alone`) that tick is skipped.
  The verifier needs its own lane in `evaluate_tick`, **running whenever the setting is on, in every SGT
  mode**, after `_run_capture`, wrapped so its errors can never stop SGT's tick or the crosshair engines.
* It must not call `_remember_dispatch` / `_dispatched_ids` (those are the capture dedupe set).
* Rows leave through the same dispatch path as SGT's (`pop_dispatch` precedent); annotations go straight to
  the DB function. Both must be safe to call from the capture thread.
* Env overrides for tests only: `VSDC247_VERIFY=1|0`.

## 8. Work packages (build order)

| WP | What | Status | Done when |
| :-- | :--- | :--- | :--- |
| 0 | Collect the three pages' real URLs, OCR wording, field positions; one live walk-through. | **COMPLETED** | A short fixtures doc + ≥ 2 OCR dumps per page kind in `tests/fixtures/vsdc247_verify`. |
| 1 | Page registry + username reader + window→client link (5.1–5.3). | **COMPLETED** | Unit tests on fixtures (`test_vsdc247_verify_fixtures.py`, `test_vsdc247_verify_ledger.py`); password text never appears in any stored/logged value. |
| 2 | ARN observation on the two submit pages + ledger + jsonl (5.4, 6.1). | **COMPLETED** | GSTR-1 and 3B fixtures yield one ledger entry each; list/history pages yield none. |
| 3 | SGT lookup + reconciler + outcomes (6.2–6.3). | **COMPLETED** | Table of 6.3 covered by tests (`test_vsdc247_verify_reconciler.py`) incl. late SGT, grace edges, client conflict. |
| 4 | `annotate_vsdc247` + flagged-row sink + removal on late SGT (6.4–6.5). | **COMPLETED** | Annotation changes only `raw_payload_json`; real rows' dedupe/purge behaviour unchanged (`test_vsdc247_verify_db.py`). |
| 5 | Engine switch, router lane, settings checkbox, health heartbeat, ntfy (7). | **COMPLETED** | Verifier runs with SGT alone and with all engines on; kill-switch works live (`test_vsdc247_verify_router.py`). |
| 6 | Tracker window highlight + tooltip (6.5). | **COMPLETED** | Flagged row visibly distinct (purple tint `#241633`, tooltip) in grouped and raw views; "Hide SGT" filter behaviour verified (`test_vsdc247_verify_ui.py`). |
| 7 | Real-window end-to-end: `tools/vsdc247_verify_e2e_test.py` (Edge, real OCR, local test pages with `VSDC_ALLOW_LOCAL_TEST=1`), plus the owner's live check. | **COMPLETED** | 10/10 checks PASS in `tools/vsdc247_verify_e2e_test.py` with real Edge browser and Windows Media OCR. |

Follow `CLAUDE.md` for the full suite (live T/F/S plugin, log file, user watches it). Work in **its own
worktree** (other sessions use the main APP folder).

## 9. Risks and open points

**Owner decisions, 2026-10-17 (confirmed in chat):** (1) a flagged `missed` row shows in the Tracker Dump
window only and is excluded from derived views until reconciled; (2) the Sera Sync behaviour of an annotated
row is checked in WP4 before relying on it; (3) if SGT later delivers the ARN, the flagged row is removed and
the SGT row is marked `late`. A new row is created **only** when SGT missed; a matched filing gets just the
`vsdc247` block on the SGT row.

1. **Downstream readers of `tracker_dump`.** Status matrices, LTT, the daily digest, `arn_fixer`, GST ARN
   recovery and Sera Sync all read the table. WP4 begins by auditing every reader (grep `FROM tracker_dump`)
   and deciding whether the flagged row must be excluded (a `NOT_CARRIER_SQL`-style helper in
   `core/dataset_key.py`). Recommendation: visible in the Tracker Dump window only, excluded from derived
   statistics until reconciled. **Owner to confirm.**
2. **Sera Sync.** The annotation edits an existing row. WP4 must check how v3 replicates a changed
   `raw_payload_json` (and whether a later sync of the same row from another PC overwrites it). The
   flagged row is new and replicates normally. Confirm before relying on annotations across PCs.
3. **Several GST tabs in one window** can mislink a username (5.3). Mitigated by page identity and by
   keying verification on the ARN; residual cases show as `link: "window"`.
4. **Login-page OCR** depends on the username being visible in the field when read (typed/autofilled).
   If the portal masks or clears it, WP0 will show it and the fallback is the GSTIN/legal name on the later
   page.
5. **A GST ARN's internal structure is still unconfirmed** (see the base document); the "two letters first"
   pattern stays evidence, not a gate.
6. **Privacy.** The ledger and block hold structured fields only. The username is stored because it is the
   evidence for the client link; it already exists in the clients database. No page text, no password.
7. **OCR load.** One extra OCR per changed frame on three page kinds only; the registry check runs before
   any OCR so non-registered pages cost one hash.

## 10. Verifying (when built)

* `python -m pytest tests/test_vsdc247_verify*.py tests/test_vsdc247.py tests/test_vsdc247_integration.py tests/test_vsdc_scope.py tests/test_vsdc_engine_settings.py`
* `python tools/vsdc247_verify_e2e_test.py`
* Owner's live check: complete one GSTR-1 and one GSTR-3B; confirm the block appears on the SGT row, then
  switch SGT off for one filing and confirm the highlighted `missed` row and the phone alert.
