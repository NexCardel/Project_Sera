# SGTv2: capture engine design

Started 2026-11-15. Status: design agreed; step 0 (the evidence collector) built; nothing else yet.

## 1. Why a new engine

- **One capture engine for every feature.** SGT, SDIS and the features still to come each need page
  data. Today SDIS has its own capture worker. Several workers reading the same browser is heavy and
  causes problems, so SGTv2 is the single engine all of them read from.
- **Wrong attribution.** Current SGT sometimes puts captured data on the wrong client. The cause is
  not known, and the dev PC has no corpus to find it with.
- **Modular.** Each part does one job and can be tested and replaced on its own.

Current SGT keeps running until SGTv2 has proved itself in shadow (section 7).

## 2. Demands

1. Blazing fast, also on a weak PC.
2. No blinding.
3. No wrong payload attribution.
4. Session boundary logic is taken from current SGT (it is optimised), with its tests. Not rewritten.

## 3. Parts

| # | Part | Job | Rule |
|---|------|-----|------|
| 1 | Capture agent | Reads the page: UIA first, snapshot and OCR later. Emits one sealed **frame**: window, tab id, URL, content, source, start and end time | Knows nothing about clients. Tab and URL are read before and after the content; if they differ, the frame is discarded |
| 2 | Node extractor and parser | Frame to fields, mainly for OCR (later), using the existing specs (`core/sgt/sgt_fields.json`) | A pure function with no state, so any frame can be replayed |
| 3 | Session manager | Several browsers and tabs, client login and logout, session boundaries; builds the JSON payload for dispatch | Boundary logic ported from current SGT |
| 4 | Verifier | Gate before dispatch | Every row carries its evidence chain. Any mismatch means quarantine and an alert, never a guess |
| 5 | Dispatcher | Outbox to the tracker | Sends only verified payloads; idempotent by dataset key |

Parts talk only through frames and payloads.

### Capture modes

The capture agent serves every consumer from one read. Each consumer subscribes with a mode:

- **Full node tree:** every node of the page (SDIS, SGT-I, the page corpus).
- **Precise nodes:** only the nodes named by rules (for example `sgt_fields.json`). This is the cheap
  path and the default.

One read per page change, shared by all subscribers. A consumer never starts its own read.

## 4. How the demands are met

- **Fast:** each stage on its own thread with a bounded queue; the capture agent never waits on
  parsing; the change gate skips unchanged pages; precise mode reads only what the rules name.
- **No blinding:** today's slow / wedged / pause rules are kept (blueprint, "UI Automation hang").
  Each stage has a watchdog. A page that could not be read is recorded as a gap, not skipped silently.
- **No wrong attribution:** identity and data must come from the same session epoch. A login, a
  logout or a different PAN/GSTIN starts a new epoch, and nothing crosses it.

## 5. Decisions (owner, 2026-11-15)

- The evidence log runs on every office PC **before** SGTv2 is built, to find the bug first.
- On doubt: quarantine and alert. Never attribute and hope.
- Single-sighting attribution is to be replaced: a second sighting is required. Cost: a delay, and
  pages that show the PAN only once may leave rows unattributed until the client is named again.
- SGTv2 runs in shadow beside current SGT; payloads are compared; the switch happens at zero
  differences.

## 6. Step 0: the evidence collector (built)

Goal: when a row sits on the wrong client, the row itself says how it got there.

### Where it lives

A small block, `raw_payload.sgt_ev`, on every SGT tracker row. `tracker_dump` is replicated by Sera
Sync, so the evidence reaches every PC with the row. No new log file, no new sync code.

- Size: about 1 KB per row, capped (30 trail entries, short keys).
- Privacy: PAN/GSTIN, the page path (no host, no query), a short tab id and times. Never page text.
- The block is never read as identity: the tracker's candidate scan skips it.

### What it holds

| Key | Contents |
|---|---|
| `v`, `pc`, `at`, `sid` | App version, capturing PC, wall time, session id |
| `born`, `parent` | How the session started (`new`, `parked`, `strict`, `split`, `tab`) and the session it came from |
| `tabs` | How many tabs the session owns |
| `id` | Per identity field (PAN, GSTIN): the stamp of the read that named the client, the spec, and how many other PANs were on that page |
| `d` | Per data field (form, period, ARN, status): the stamp of the read that set it |
| `tr` | The last 30 session events: `[seconds, code, tab, detail]` |
| `m` | How the tracker chose the client record when saving |
| `f` | Flags (below) |
| `prev` | Up to two older blocks of this row from another session or PC |

A stamp is `{t, tab, pg, src}` plus `late` (the read was absorbed into a session that was no longer
in view) and `lag` (seconds between asking for the read and absorbing it, when over 2 s).

Trail codes: `new`, `resumed`, `resumed-client`, `shelved`, `back`, `tab+`, `split`, `clash`,
`strict`, `cut`, `late`, `parked`, `end`.

### Flags

| Flag | Meaning |
|---|---|
| `tab-diff` | Identity and a data field were read in different tabs |
| `data-first` | A data field was read before the client was named |
| `link-id` | The client was named by the saved-client link (portal user ID), not by a page |
| `late` | A late read was absorbed |
| `resumed` | The session was resumed from a parked one |
| `other-pan` | Another PAN was on the page that named the client |
| `ocr` | A field came from OCR |
| `strict` | The session was born from a PAN/GSTIN clash |
| `no-tab` | The tab id was not known when the client was named |
| `no-id` | The row was written with no client |
| `match-other` | The tracker matched the row to a client through a value that is not the row's own PAN/GSTIN |
| `match-none` | The row's PAN/GSTIN is not in the client list |

### Audit of rows already stored

Evidence only exists on rows captured after the update. The export also checks every stored SGT row,
old or new: if the client it sits on does not hold the row's own PAN/GSTIN, the row is reported as
`client-lacks-id`.

### Getting it out

- **Wrong client** mark: right-click a tracker row. It is stored on the row, so it syncs.
- **Export SGT evidence**: tracker Preferences menu. Writes every marked, flagged or audited row to
  one JSON file.

### Code

| File | What |
|---|---|
| `core/sgt/sgt_evidence.py` | The block, stamps, trail, flags, match, audit and export. No Qt |
| `core/sgt/sgt_shadow.py` | Hooks: session birth, trail, identity and field stamps, block on each row |
| `sera_db/tracker_dump.py` | Match record, older blocks carried over, the mark, the export rows |
| `sera_db/srpf.py` | The candidate scan skips `sgt_ev` |
| `ui/windows/tracker_dump_window.py` | The mark and the export |
| `tests/test_sgt_evidence.py` | Tests |

## 7. Order of work

0. Evidence collector on all office PCs (built). Collect marked rows; find the cause.
1. Fix the cause in current SGT if it can be fixed there.
2. Capture agent with both modes; SDIS and SGT read from it in shadow.
3. Session manager (boundaries ported) and verifier.
4. Dispatcher; SGTv2 in shadow beside SGT; compare payloads.
5. Switch at zero differences. Retire the extra capture workers.

## 8. Open points

- **Suspect found while building step 0 (not fixed):** the tracker's client matching
  (`_extract_identity_candidates_from_payload`, `sera_db/srpf.py`) collects every PAN, GSTIN or
  TAN shaped value anywhere in the payload and takes the first one that equals any value of any
  saved client. When the row's own PAN is not in the client list, another value in the payload can
  put the row on a different client. `link_unassigned_tracker_dumps` and
  `re_resolve_all_tracker_dumps` re-attribute rows the same way later. The `match-other` flag and
  the audit are there to confirm or rule this out.
- A repair path for rows already on the wrong client.
- The frame log of SGTv2 will hold client data: it stays on each PC, never in the repo (public).
