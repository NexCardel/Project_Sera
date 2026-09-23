# Sera Global Tracker (SGT) — blueprint

**Status:** shadow mode built and field-tested. Its returns go into the tracker dump as their own
tagged rows (decided 2026-09-21: "mixed into the tracker, tagged"), beside the other engines'
rows, plus the HUD pill and a local log. Live mode (SGT replacing an engine) is not built.

**Tracker rows (2.10.1):** capture method `SGT_shadow`; dataset keys in an `SGT:` namespace
(`SGT:ITR:<PAN>:<FORM>:<PERIOD>`, stable across sessions). Written as soon as a return can be
keyed and again on every change - never only at session end, so a crash loses nothing; a return
in progress is written once complete and moved (old key superseded) if its form changes; rows
written before the client was known are rewritten under the PAN. In `insert_tracker_dump` every
clean-up (pending placeholders, 10-second duplicates, draft supersession) only looks at rows of
the same side, so SGT and the other engines can never remove each other's rows. SGT rows never
enter the router's `_dispatched_ids`, so they cannot stop VSDC247 saving its own capture. The
tracker window colours them orange and has a Source filter (All / Hide SGT / SGT Only).
**Date:** 2026-09-21

---

## 1. What SGT is

SGT tracks a client's work across the government portals by **reading every page**, instead of
recognising particular pages by their address.

**Main selling point:** it does not use crosshairs. It probes all pages (allowed government sites
only) and extracts datapoints through proximity scanning and validation rules that live in a
**configuration file, not in code** — so datapoints can be added and removed without touching the
program.

Like the rest of the capture stack it is **strictly passive**: it reads the screen through the
accessibility layer and never clicks, types, scrolls or injects anything.

### Where it sits next to what already exists

| Engine | Finds the page by | Reads with | Captures |
| :--- | :--- | :--- | :--- |
| SDC | DOM crosshair (extension) | the page's DOM | mapped pages |
| VSDC | URL crosshair | screen pixels + OCR | mapped pages |
| VSDC-X | URL crosshair | UI Automation text | mapped pages |
| VSDC247 | nothing — every page | screen pixels + OCR | submissions only (ARN / ack) |
| **SGT** | **nothing — every page** | **UIA text, OCR when UIA is blind** | **every datapoint, whole session** |

SGT is not built from scratch. Each of its three parts already works in this codebase:

* crosshair-independent capture — VSDC247, proven on real Edge;
* label/value proximity reading — VSDC-X, and the UIA probes confirm the portal renders a label
  and its value as adjacent elements (`"PAN :"` then the value);
* session boundaries and a records assembler keyed by (PAN, form, period) with monotonic status —
  `vsdc_assembler.py` today.

SGT re-wires proven parts around a different control flow. That is the main reason to believe the
build is tractable.

---

## 2. The pipeline

```
          Browser window on an allowed portal
                        |
   Gate 1 — is this one of the allowed portals?      (hostname only)
                        |  no -> read nothing at all
                        |
   Gate 2 — has the page changed since last probe?   (~50 ms)
                        |  no -> skip, cost ends here
                        |
   Probe — read the page as LINES OF TEXT            (41 ms typical, 935 ms heavy)
        UI Automation first, OCR when UIA is blind   (canvas-painted sites - see 2a)
                        |
   Resolver — for each field spec in sgt_fields.json (2-35 ms)
        find label -> take the value near it -> validate -> score
                        |
          +-------------+--------------+
          |                            |
   Profile slot                 Dataset slots
   latches once                 one per (form, period); status only promotes
          |                            |
          +-------------+--------------+
                        |
   SGT Assembler — holds the whole session, persisted to disk on every change
                        |
   Session ends — logout, portal timeout, idle, or app quit
                        |
   One payload -> tracker:  client_profile + datasets[] + timeline[]
```

---

## 2a. Two rendering models — why the probe is not UIA-only

Measured 2026-09-21. A probe of `traces.tdscpc.gov.in` returned **one** node:

```
Node count   : 1
[Button      ] () Enable accessibility
```

That is the Flutter Web **semantics placeholder**. Flutter paints its interface onto a canvas and
builds no accessibility tree at all until that button is activated. Every other portal probed from
the same browser on the same night returned 50-217 nodes, so this is the site, not the browser.

Reproduced locally to confirm the diagnosis: a page that paints its text onto a `<canvas>` behind
the same placeholder gives UI Automation exactly one line, while Windows OCR reads the same window
fine.

| Engine, on a canvas-painted page | Result |
| :--- | :--- |
| UI Automation | 1 line — "Enable accessibility" |
| Windows OCR | all 8 lines; TAN, form, period, status and token number all recovered |

Confirmed against the live site on 2026-09-21 (public login page, read-only, clean profile, nothing typed or clicked): UI Automation returned **0 lines**, Windows OCR returned **21 lines in 257 ms**, including the page's own headings and the TAN field labels. The OCR fallback works on the real portal, not just on a replica.

**Three ways the semantics tree can be switched on, and only one is available to us:**

1. *The app enables it itself* (`ensureSemantics()` at start-up) - that is the portal's code, not ours.
2. *Something activates the placeholder* - a click or Enter keypress. Either our software does it
   (synthetic input into a tax portal - see below) or a person does it, once per page load, and it is
   lost again on reload.
3. *A browser flag* - *tested and ruled out*: launching with `--force-renderer-accessibility` against
   the live site changed nothing (1 line before, 1 line after). Flutter's gate is its own, not Chrome's.

**Activating that placeholder is a click, and VSDC never clicks the portal** (see the SAD/SDC/VSDC
risk review). So on such a site the semantics tree stays off and UIA stays blind.

**Consequence for SGT:** the probe reads *lines of text*, and where those lines come from is an
implementation detail — UI Automation first because it is exact and cheaper, Windows OCR when UIA
comes back blind. The resolver, the slots, the assembler and the payload are all unchanged, because
the resolver only ever sees lines. This is the reason SGT must not be specified as "the UIA engine".

**Fallback trigger:** UIA returns nothing usable — no lines, or only the Flutter placeholder. The
placeholder is a reliable signature, so a window known to be canvas-painted can skip straight to OCR
instead of paying for a UIA read that will fail.

This also means VSDC-X alone would be blind on such a portal. It has no effect today because TRACES
is not in the allowlist, but it decides what SGT must be built as.


## 3. Datapoints

### Profile builder

| Field | Portals |
| :--- | :--- |
| PAN / GSTIN / TAN | both |
| Name / Trade name | both |
| Date of birth | ITR |
| E-mail | ITR only |
| Phone number | ITR only |

The profile is the **label for client context**, attached to every dataset captured in the session.

**Latching rule (profile only):** once a profile datapoint is captured, SGT stops looking for it.
Datapoints not yet found keep being looked for until the session ends.

**Exception — the name promotes (decided 2026-09-21).** A spec with `"merge": "promote_longer"`
keeps looking, and a longer name replaces the held one **only if it extends it**: every word of
the held name appears in the new one, in order, and the held name's last word may be a cut-off
start ("RAVI MEHTA" → "RAVI KUMAR MEHTA", header "… KHAN" → "… KHANNA"). A longer but
different name is logged as "not promoted" and never taken; a shorter one never demotes. The
merge policies live in the toolbox (`MERGES`), so any other profile datapoint can opt in by
config.

### Dataset capture

| Field | Notes |
| :--- | :--- |
| Form type | ITR-4, GSTR-1, … — the list grows by config, not by code |
| Period | Month / assessment year |
| Status | Not submitted → Submitted, e-verification pending → Submitted & e-verified |
| ARN / acknowledgement | the key identifier |

**Dataset fields do not latch.** Status is the field that must be allowed to change — a return moves
from not-submitted to submitted to verified inside one session, and latching it early would lose the
filing. Status only ever *promotes*, never demotes (the existing `get_status_rank` ladder).

Datasets are held in **slots keyed by (form, period)**, so two returns in one session cannot
overwrite each other.

---

## 4. Datapoint resolution — the configuration file

The design goal: **add or remove a datapoint by editing a file, never by editing code.**

### What changes, and what does not

| Changes when a portal is redesigned | Never changes |
| :--- | :--- |
| labels, wording, layout, routes | the shape of a PAN, GSTIN, TAN, 15-digit ack, ARN, AY, a date |

So everything portal-specific lives in `sgt_fields.json`. Nothing about a datapoint lives in code.

### A field spec

```json
{
  "field": "tan",
  "labels": ["TAN", "Tax Deduction Account Number", "TAN of the Deductor"],
  "pattern": "[A-Z]{4}[0-9]{5}[A-Z]",
  "take": "after_label",
  "within": 3,
  "transforms": ["upper", "strip_spaces"],
  "checks": [],
  "portals": ["Income Tax"],
  "slot": "profile",
  "confidence": 90,
  "examples": ["MUMA12345B"],
  "counter_examples": ["ABCPD5678E", "Search Box Input Field"]
}
```

* `labels` — the words to look for on the page (synonyms allowed).
* `pattern` — the shape the value must have.
* `take` / `within` — where to look relative to the label.
* `transforms` / `checks` — names borrowed from a small shared toolbox (see below).
* `slot` — `profile` (latches) or `dataset` (does not).
* `examples` / `counter_examples` — the spec's own test cases.

### The shared toolbox — the one honest limit

`transforms` and `checks` are a small **generic** library in code that every spec borrows from —
things like `upper`, `digits_only`, `parse_date`, `is_real_date`, `years_consecutive`,
`date_is_today`. They are not per-datapoint validators.

Adding TAN, or form 140, or anything with an ordinary shape, is **pure configuration**. Only a
datapoint needing a genuinely new *kind* of arithmetic — as the "an ITR ack ends with its own filing
date" rule was when it was discovered — requires adding one tool to the toolbox, once, after which
every spec can use it.

### Self-testing specs

A regex in an editable file is powerful and dangerous: a wrong one matches garbage, and a badly
written one can hang.

**The loader runs each spec's own `examples` and `counter_examples` before installing it.** A spec
that fails its own tests is refused with a console line, and the previous configuration keeps
running. A staff member editing the file cannot silently break capture — it fails loudly at start-up
instead of quietly capturing the wrong thing.

Patterns are also compiled under a size/complexity bound so a pathological regex cannot stall the
worker thread.

### Migration safety net

The 368 existing VSDC tests already pin down what the hand-written extractors produce. If the SGT
configuration reproduces those same answers on the same inputs, the move from code to config
provably lost nothing.

---

## 5. Session model

**Boundary:** taken from the existing capture methods — `login` and `logout` as **keywords inside
the link**, not whole-link matches.

**One client per session.** If a CA opens two browser windows at once, each window gets its own
assembler slot — the per-window session isolation (`_WindowSession`) that already exists.

**A session ends on any of:** logout, portal session timeout (the portal shows its own countdown),
idle timeout, or app quit.

### Crash safety — required before SGT replaces anything

Today a capture is dispatched in the same tick it is made, so a crash loses nothing. SGT holds a
whole session before dispatching, so a crash, a power cut or a killed app would otherwise lose
**everything collected since login**.

Therefore:

* in-flight session state is **persisted to disk on every change** (it is a small dictionary — cheap);
* a session is flushed on app quit (`end_all_active_sessions` already exists);
* on start-up, an unfinished session found on disk is recovered or dispatched rather than dropped;
* idle timeout is treated as a session end.

---

## 6. Payload

```
Client Profile   = { pan, gstin, tan, name, trade_name, dob, email, phone }
Datasets Captured = [ { form, period, status, arn, ... }, ... ]
Timeline          = [ link 1 -> link 2 -> ... ]
```

This maps almost one-to-one onto the existing envelope, so the whole downstream pipeline keeps
working: `Datasets Captured` is already `raw_payload.assembler_captures` (a list, which `main.py`
already explodes into one tracker row each), `Timeline` is already `raw_payload.timeline`. Only
`client_profile` is new.

Every payload also carries `device_name` — which PC produced it.

---

## 7. Cost — measured, not estimated

Measured on this machine (Edge, real UI Automation, 2026-09-21):

| Step | Time |
| :--- | ---: |
| Probe a normal portal page (29 lines) | **41 ms** |
| Probe a heavy table page (300 rows, 2108 lines) | **935 ms** |
| Resolver, 20 specs × 80 lines | **2 ms** |
| Resolver, 20 specs × 500 lines | **8 ms** |
| Resolver, 20 specs × 2000 lines | **35 ms** |
| Read a canvas-painted page by OCR (capture + scan) | **276 ms** |
| Change gate (screenshot + 32×32 hash) | **50 ms** |
| Worker tick interval today | 350 ms |

**The rules are free; the reading is everything.** Going from 20 datapoints to 200 stays in the low
milliseconds. Scaling the *number* of datapoints is cheap, which is exactly what SGT is for.

**The change gate is what makes "probe every page" affordable.** Without it, a heavy page costs
935 ms against a 350 ms tick — the read never stops, burning roughly three-quarters of a CPU core
continuously while somebody sits on a big GST table. With it, a still page costs 50 ms instead of
935. The gate is 19× cheaper than the read it avoids.

The gate's screenshot is shared with VSDC247 when that engine is also on, so it is close to free in
the normal configuration.

A heavy page that genuinely *does* change still costs about a second. It runs on a background
thread so nothing freezes, and it is accepted (decided — see §9): capping the read depth would
risk missing rows far down a long table.

---

## 8. Decisions taken

* **MSP** = main selling point.
* **Form 140 and TAN** are deliberately left unresolved — adding them is what the configuration file
  is for, and doing it without a code change is the test of the design.
* **One client per session.** Two browsers at once → two separate assembler slots. ERI / multi-client
  flows are explicitly out of scope.
* **SGT is an option with a switch** (a fourth toggle in Settings → Tracker, alongside VSDC, VSDC-X
  and VSDC 24/7).
* **The latching rule applies to the profile builder only**, not to dataset capture.
* **No per-datapoint validators in code** — only the shared toolbox described above.
* **Status vocabulary** is to be settled by the extractors themselves rather than fixed up front.
* **Crash safety** is handled by persisting the in-flight session, as described in §5.

---

## 9. Decisions taken 2026-09-21 (were open)

1. **Heavy pages:** read the whole page (no depth cap). It runs on the worker thread and the
   change gate already skips still pages; a cap could miss the one row that matters.
2. **TRACES:** out of scope for tracking. The new portal is Flutter (UIA-blind, OCR only).
   The `traces61.tdscpc.gov.in/...xhtml` probe that gave UIA 223 nodes is the **old** TRACES
   site (JSF), not the new one, and says nothing about it.
3. **Shadow mode first:** yes.
4. **Idle timeout:** 20 minutes ends a session.
5. **Filing seen, client never identified:** SGT does not grow its own path for this — it is
   intermingled with VSDC247's existing client-unknown handling (tracker row + phone alert +
   attribute-later). In shadow mode it is logged with that note.
6. **Where shadow runs:** a fourth Settings → Tracker control, **Off / Shadow**, default **Off**
   on every PC. The log stays on the PC in `~/AmanAssociates_Sera/sgt_shadow/`, one file a day.

---

## 10. Build order

1. **Field-spec registry and resolver** — **built.** `core/sgt/sgt_toolbox.py`,
   `sgt_specs.py`, `sgt_resolver.py`, `sgt_fields.json` (9 profile specs, 3 record types).
   Run over every real UIA probe on this PC: all four filed returns, both GST calendar rows and
   the full profile page resolved correctly, no false captures, 0.2–6 ms a page.
2. **Shadow mode** — **built.** `core/sgt/sgt_shadow.py`, called from the router after
   VSDC247 on scope-gated ticks only; it cannot change what the pipeline returns. Verified in
   real Edge: UIA path on an HTML page, OCR fallback on a canvas page behind Flutter's
   "Enable accessibility" placeholder.
3. **Session model** — slots, latching and status promotion exist inside shadow mode;
   crash-safe persistence is still to do (required before live).
4. **Session-end dispatch** — and the adapter onto the existing payload envelope.
5. **The switch** — **built** as Off / Shadow; Live is added with step 4.

### Records, not just fields

A list page (View Filed Returns, the GST Returns Calendar) shows several returns at once, so a
dataset is resolved as a **record**: a block of lines that starts where the record's `start`
pattern matches and ends at the next start. Each record field is resolved inside its own block,
so one card's status can never be attached to another card's ack. `nearest_above` reads a value
from the closest heading above the block (the GST calendar's form name). `require` lists the
fields without which a block is not a return. A page with no `start` is one block.

### The dataset in progress — built from many pages (added after the first shadow test)

The first shadow test showed form and period only being captured on View Filed Returns: the
filing-wizard pages had no specs, and a piece that could not identify a return on its own page
(a form without a period) was thrown away. Writing a spec per wizard page would have turned SGT
into VSDC-X in a JSON file, so the fix is page-agnostic (`current_dataset` in `sgt_fields.json`;
the old section name `current_return` is still accepted in override files):

* **One value on a page = that page's return; many = a list.** A form or AY shown once is a
  piece of the return being worked on; several different ones are ignored — except periods,
  where the latest is taken (`"multiple": "latest_period"`).
* **The link and the window title are text too.** `fo-itr4-ay2026` / `/returns/auth/gstr1` /
  "ITR4 Part A" are read with the same patterns (`"source": "link" | "title"`).
* **Each piece belongs to the page it came from.** Going back to that page and changing the
  field replaces the piece; emptying it clears it. (The user's forward/back idea, made safe:
  a blanket reset on every revisit would wipe good data on dashboard round-trips.)
* **A different period starts a different return.** The old one is closed.
* **Status from evidence, not pages:** submission wording + today-dated ack → submitted;
  a complete dataset nothing submitted → "Draft", but only if some piece came from the
  link (the portals only put the form/year in the link while you are inside that filing).
* **Only complete returns are dispatched** (`complete_when`). An incomplete one is logged as
  "incomplete – not dispatched".
* **List pages never feed it.** A confirmation page's ack joins the return in progress (it
  takes the form and period from it) and completes it.
* **A page listing several returns keeps only the latest period's** (user rule, 2026-09-21);
  several forms for that same latest period all stay.
* `compose` builds a field from parts: GST `"{tax_period} (FY {fy})"`.

**Dropdowns and radio buttons (measured in Edge):** a closed dropdown's selected value IS read
(ValuePattern). Radio buttons and checkboxes were the real hole — UIA reads their labels but
not which is ticked. `read_page_text(..., include_selection=True)` now adds a
`Selected: <label>` line (SGT only; VSDC-X lines unchanged). The probe tool was also hiding
dropdown values (it skipped unnamed elements and never read values); it now prints
`=> value` and `[selected]`, so a probe shows what SGT actually reads.

### Second shadow test (2026-09-21) — fixes

* **A choice only counts if the page marks it chosen.** The personal-information page lists
  every filing section (139(1), 139(4) Belated, 139(5) Revised…) as plain text under
  "Filed u/s"; a "line after the label" rule read "Belated" off that option list. Filing type
  now comes only from `Selected:` lines — the reader marks a ticked radio/checkbox
  `Selected: <label>` and a dropdown value `Selected: <label> = <value>` (SGT only). Form and
  AY can also come from `Selected:` lines, and a chosen AY outranks the link.
* **No flicker:** a piece is cleared only after its page misses it on 2 reads in a row.
* **PAN on the password page:** login pages are now read (logout pages still are not) — the
  ITR password page shows the PAN being logged into. The login keyword still closes the
  previous client's session first.
* **Portal:** a session took the portal of its first page (a GST login page labelled a whole
  ITR session "GST"). An empty session now takes the portal it is on; a session with a
  client that moves to the other portal is closed.

* **Name from its parts:** `first_name` / `middle_name` / `last_name` specs plus
  `profile_rules.compose` = `"{first_name} {middle_name?} {last_name}"` (`?` = may be missing).
  The joined name is offered like any read value, so it still only promotes a name it extends
  (real page: header "RAVI MEHTA" → "RAVI KUMAR MEHTA").
* **A label is never a value.** The loader collects every label in the spec file and gives it
  to every spec; looking for a value after a label stops at another field's label (the field is
  empty). Found by the self-test: an empty "Last Name" was about to take "PAN" as its value.

* **The form-choice page IS a dropdown** ("I know which ITR Form I need to file => ITR - 2").
  It was missed because the portal writes `ITR - 2` (space before the dash); every form pattern
  now accepts it. Lesson: same portal, same component library, same exposure — a silent page is
  first a pattern to check, not a blind control.
* **Clearing waits 5 s as well as 2 reads:** after Continue the old link stays in the address
  bar while the page goes blank, which cleared a just-picked AY in a real test.

### Found while building

* The loader's self-test refused a spec on its first run — "Name of the Bank" was being read as
  the label "Name" with the value "of the Bank". Fixed with `label_rest: "separated"` (the text
  after a label only counts as the value when a ":" or "-" separates them).
* Windows OCR silently dropped the "ITR-4" line on the canvas test page, which threw the whole
  card away while `form` was required. A filed return is identified by its ack + period +
  status, so `form` is no longer required there — a one-line config change, as intended.

### Submit status: one ladder on every portal (2026-09-22)

Field report: the ITR e-Verify picker (`.../eVerifyReturn/eVerifyReturn-al`) was read — form and
AY were picked up — but nothing was saved: no rule described a card headed "Assessment Year" on
its own line, and the page never prints a status word (its status is what the page *is*). The
fix was made for every portal, not that page (user rule: SGT fixes are global).

* **A dataset = form + period + submit status.** The submit status carries the identifier
  (`arn`: ARN / ack), the level, and the portal's own wording as `status_evidence`. The code and
  config say *dataset*, not *return* — returns, applications and refunds are all datasets.
* **One ladder, four levels, every portal** (`sgt_toolbox.SUBMIT_LEVELS`):
  `Not Submitted` (default) → `Draft` → `Submitted (Not Verified)` → `Submitted & Verified`.
  Status only moves up. Portal jargon ("Pending for e-verification", "Filed", "Processed with
  refund", "Ready to File") is evidence that a spec's `map` turns into a level; the **loader
  refuses a status map with any other output**, so a new portal cannot bring its own words.
* **The level climbs with the datapoints captured** (user's rule, 2026-09-22):
  form + period of the dataset being worked on → `Draft`; + ARN/ack → `Submitted (Not
  Verified)`; + a submit message → whatever its wording says ("still need to e-Verify" stays
  Not Verified, "e-Verified successfully" is Verified). Each step only raises the level, and
  `status_evidence` records which step did (`form + period captured`, `ARN captured`, or the
  full line of the message).
* **One portal exception, in config** (`submit_rules.identifier_proves`): GST issues the ARN
  only when the return is filed with DSC/EVC, so a GST ARN alone proves `Submitted & Verified`.
  Every other portal uses the ladder's default.
* The ITR submit confirmation needs only the today-dated ack (the message is optional); the
  e-Verify confirmation carries the ack of the return verified and joins by it.
* **Page-scope record fields** (`"scope": "page"`): a fact the page states once about every card
  on it — "select the return you would like to verify" makes every card awaiting verification.
  Only one distinct value counts. A card's own status, when it has one, wins.
* **One card rule for the ITR portal's lists** (`itr_dataset_cards`): cards headed `A.Y. 2025-26`
  (View Filed Returns) or `Assessment Year` / `2025-26` (e-Verify picker); needs period + ack.
  A card naming another PAN than the session's client is never attributed.
* **Future stepper steps are never evidence.** UIA marks them "Unvisited Step N of M"; the
  label and number after that marker are dropped before any rule sees the page (the e-Verify
  wizard draws "Return Successfully Verified" on step 1).
* **A confirmation is never read on a list page.** Whole-page records (submit / verification
  confirmations) are skipped on a page whose cards matched — an older card's "Successfully
  e-Verified" is not a confirmation.
* New records: `itr_verification_success` (form + year, no ack — joins the dataset by form +
  period) and `gst_submit_success` (ARN + past-tense wording). **Both use the common success
  wording, not wording confirmed on the live portals** — check them in the next shadow test.
* The tracker, `get_status_rank` and the LTT status reader all understand the four labels
  ("not verified" was read as verified before). The tracker shows `Draft` as "Not submitted" -
  it has no Draft pill yet.

---

## 10a. Reliability by design (2026-09-22)

Built after a review of SGT itself. The finding was that SGT's rules were good, but there was
almost no evidence they were right (7 shadow sessions, 5 datasets) and no guard against its worst
mistake. Measuring agreement with VSDC (the go-live bar) is deliberately left for later. What
exists now:

**Replay: every field bug becomes a permanent test.**
- `core/sgt/sgt_corpus.py` records the text lines of every page SGT resolves. They go to
  `~/AmanAssociates_Sera/sgt_corpus/`, one file per day, deduplicated, kept 30 days, 50 MB a
  day at most. It is controlled by Settings → Tracker → "Record pages for SGT testing" (on by
  default). It holds client data, so it never goes into the repository.
- `core/sgt/sgt_replay.py` runs recorded pages through a fresh `SgtShadow` with the recorded time
  and date. It touches nothing real.
- `tools/sgt_replay.py` has four commands:
  - `baseline`, then edit specs, then `diff` (or `diff --specs file.json`): shows what a spec
    change does on real pages;
  - `show`: what SGT would write for each recorded session;
  - `health`: pages read, OCR share and spec hits over the last 7 days.
- `tests/sgt_golden/*.json` holds fictional sessions with their expected rows:
  - positive: ITR login to filed returns, the ITR wizard to submission, GST filed;
  - held: an ack dated before its assessment year;
  - negative: a help page, and another client's card.
- `tests/test_sgt_replay.py` replays them exactly, then runs noise over them: OCR confusions,
  repeated blocks, stray, blank and lost lines, and shuffles. The rules it enforces:
  - SGT never crashes;
  - it never *invents* a value (one printed nowhere on the clean pages);
  - it never attributes another client's card while that client's PAN is readable.

**Identity: never the wrong client, and never lose a dataset.**
- The first sighting of the PAN or GSTIN attributes the rows. The portals often show it only
  once. A two-sightings rule was tried and dropped the same day at the user's request: it
  delayed attribution and could leave datasets unwritten. The log notes "seen once" or "seen
  on two pages".
- The protection comes from contradiction, not repetition. A different PAN or GSTIN ends the
  session on the spot; nothing is overwritten, and the clashing value is not taken from that
  page.
- Datasets carry the PANs printed where they were read: their card's own PAN, or every PAN on a
  page whose text built them. They are attributed only to a client among those PANs, and wait
  while the client is unknown. At session end, a dataset still waiting is written
  **unattributed** (the VSDC247 client-unknown path), never dropped.
- While another PAN is readable on a page, that page's cards are not attributed, and its text
  doesn't feed the dataset in progress. Its link and title still do, because the wizard lists
  landlord and donee PANs.

**Dataset rules: a dataset is checked as a whole.** The `dataset_rules` section of
`sgt_fields.json` holds rules built from `when`, `require`, `checks` and `date_tail`, each with
examples it passes and holds. The loader refuses a rule whose examples fail, and the previous
version keeps running. A dataset that breaks a rule is held with the reason, and written once it
passes. The built-in rules:
- an ITR period is an assessment year with consecutive years;
- an ITR ack's own DDMMYY date falls between the start of the AY and today;
- a GST period is a tax period of a financial year;
- a form belongs to its portal.

**Crash safety.** Open sessions are snapshotted to `sgt_shadow/sessions_state.json` (atomic
replace, at most every 2 s). On the next start, sessions the app never ended are finished and
their datasets re-written. Dataset keys make that idempotent.

**Portal-change watch.** `core/sgt/sgt_health.py` counts reads, OCR reads and spec hits per
portal per day in `sgt_shadow/spec_stats.json` (counts only). Once a day it raises a HUD prompt
in two cases:
- a spec that usually hits has been silent on the last 3 busy days;
- a portal's OCR share jumps above 50 % from under 10 %, meaning it went canvas.

**UI Automation hang.** `vsdc_uia_text._run_with_timeout` used to leave a wedged worker in
place, so every later read queued behind it and failed after 3 s, until a restart. That blinded
VSDC-X and SGT alike. Now the wedged worker is abandoned and a fresh one (with its own COM
objects, kept thread-local) takes over. After 5 in one run, UIA is switched off.

## 11. Risks to hold onto

| Risk | Mitigation |
| :--- | :--- |
| Session-end dispatch loses a session on a crash | persist on every change; flush on quit; recover on start-up |
| Reading every page multiplies false positives | validator per field, UI-chrome deny list, confidence scoring — all in the first commit |
| A bad config silently breaks capture | specs carry their own tests; a failing spec is refused and the old config keeps running |
| Heavy pages are expensive | the change gate; optional read-depth cap |
| Five overlapping engines disagree | the switch, plus shadow mode before SGT is trusted |

Every false capture this project has hit so far — `"Search Box Input Field"` read as a client name,
a PAN's letters read as a name, a revised-return wizard's old ack read as a fresh submission —
happened while looking at *mapped* pages only. SGT looks at every page, so the exposure multiplies.
That is the reason the validation rules are not optional extras.


---

## 12. Enhancement roadmap — a more flexible, smarter SGT (proposed 2026-09-23)

**Status:** proposed, nothing built. Two decisions are still the user's (see "Open decisions").
Every phase keeps the existing rules: config not code, strictly passive (never click or type
into a portal), global fixes rather than page-shaped ones, "dataset" not "return", the 4-level
submit ladder, and every change passes `tests/test_sgt_replay.py` plus a `tools/sgt_replay.py diff`
on the recorded corpus.

### What limits SGT today (checked in the code)

1. **The probe flattens the page.** `vsdc_uia_text.read_page_text` turns the UIA tree (or OCR) into
   a list of strings; the only structure kept is `Selected:` lines. Which line is a label, which
   cell sits in which table row/column, what is a heading, and where anything is on screen are all
   thrown away, so every spec rebuilds structure with a regex (`after_label`, `nearest_above`,
   record blocks split by `start`). That pushes specs towards one pattern per page — the VSDC-X
   shape this design rejected.
2. **Everything is polled.** A success toast shown for about a second can fall between two 350 ms
   ticks, or be skipped by the change gate if the screenshot hash barely moves.
3. **The page is the only source.** The strongest evidence of a filing — the ITR-V /
   acknowledgement PDF the user downloads — and the fact that SCA just filled a given client's
   credentials are both ignored.
4. **Nothing proposes new specs.** The corpus and replay show where SGT fails, but every new
   datapoint is a hand-written regex with hand-written examples.

### The phases

**A — Read structure, not just lines.**
- The probe emits *nodes*: text, control type, bounding box, table row/column, heading level,
  selected state. UIA already exposes these (Table/Grid patterns, bounding rectangles); OCR
  returns boxes, so rows and columns can be rebuilt from geometry. The resolver keeps a `lines`
  view, so every existing spec runs unchanged.
- **Header-keyed table specs:** `{"form": ["Return Type","Form"], "period": ["AY","Return Period",
  "Tax Period"], "status": ["Status"], "arn": ["Ack No","ARN"]}`. The ITR filed-returns list, the
  GST returns calendar and a future TRACES table become one mechanism plus config.
- **Label → value harvesting by layout** on every page ("PAN:" and whatever sits to its right or
  just below). Specs map label *synonyms* to fields; a new portal wording is one more synonym,
  not a new pattern.

**B — More sources, still fully passive.**
- **B1 UIA events:** subscribe to live-region-changed and notification events (what screen readers
  use for toasts). Catches a success message shown for under a second; structure-changed events
  can replace some screenshot polling.
- **B2 Downloads watcher:** portal documents (ITR-V, acknowledgement PDF, GST ARN receipt) become a
  third line source beside page and OCR, feeding the same resolver. Strongest single evidence for
  Submitted / Verified, with no clicking.
- **B3 SCA identity head start:** when SCA fills client X's credentials and a portal session starts,
  the session begins as *probably client X*; the PAN on the page confirms or contradicts it. Only
  the client id may cross over — never anything the SCA v2 rules keep secret.

**C — Checks per value *type*, not per datapoint (toolbox only).**
- GSTIN mod-36 check digit (hard pass/fail); GSTIN characters 3–12 are the PAN; PAN 4th letter =
  entity type (P individual, F firm, C company, H HUF…), 5th letter = first letter of the name or
  surname. These catch OCR misreads and wrong-client reads with no per-datapoint spec.
- Position-aware OCR correction before validating (PAN `AAAAA9999A`: an O in a digit slot is 0, a 1
  in a letter slot is I), then the checksum.
- **Evidence that builds up:** a value's confidence grows with the number of pages and sources
  (UIA, OCR, file) that show it and drops on contradiction; the tracker can show it, and status
  promotion can require a minimum amount of evidence. The 2026-09-22 rule stands: one sighting of
  a PAN/GSTIN still attributes at once.

**D — SGT proposes its own specs; the user approves each one.**
- **Miner** over the recorded corpus: recurring label → value pairs no spec captures, ranked by
  frequency and value type, drafted as specs whose examples come from the corpus, pre-checked by
  self-test and replay diff.
- **Teach by pointing** in an "SGT lab" screen: click a value on a recorded page, name the field;
  SGT infers label and type, finds every other occurrence, shows the replay diff, and writes to the
  override `sgt_fields.json` only on Accept.
- **Masked LLM drafting:** page *shapes* go to Gemini with letters replaced by `A` and digits by `9`,
  so it never sees taxpayer data. Offline authoring only — live capture stays deterministic. *(Note: no thanks bro)*

**E — Portal packs.** One JSON per portal: hostnames, login/logout keywords, identity field types,
jargon → ladder mapping, label synonyms, table-header vocabulary. TAN / form 140 stay pure config;
TRACES becomes a pack plus A's OCR geometry whenever it is brought into scope.

### Order, cost and model per phase

Token figures are rough estimates made 2026-09-23. "Processed" is dominated by context re-sent on
every tool call; starting each phase in a **fresh session** (from this document and the memory
notes) should roughly halve it.

| Order | Phase | Model | Why this model | Tool calls | Processed | Written |
|---|---|---|---|---|---|---|
| 1 | **C** type checks, OCR correction, evidence | **Sonnet 5** | Well-specified algorithms (checksums, templates) plus tests; little design risk | 25–40 | ~1.5–2.5M | ~30–50k |
| 2 | **A** page model, header-keyed tables, label→value | **Opus 5.5** for the node model, resolver and loader; **Sonnet 5** for migrating specs and golden tests | Foundation every later phase builds on; must keep all existing specs and replay results unchanged | 100–150 | ~10–15M | ~100–150k |
| 3 | **B1** UIA events for toasts | **Opus 5.5** | COM event handlers on the UIA worker thread, interaction with the hung-worker replacement, verification in real Edge | 40–60 | ~3–5M | ~40–60k |
| 4 | **B2** Downloads watcher + PDF text | **Sonnet 5** | A contained new source feeding the existing resolver | 30–40 | ~2–3M | ~30–40k |
| 5 | **B3** SCA identity head start | **Opus 5.5** | Crosses the SCA v2 security boundary; needs a careful review, not much code | 20–30 | ~1.5–2M | ~15–25k |
| 6 | **E** portal packs | **Sonnet 5** | Mostly restructuring JSON and the loader behind existing tests | 40–60 | ~3–5M | ~30–50k |
| 7 | **D** miner, SGT lab, masked LLM drafting | **Opus 5.5** for the miner and the masking rules; **Sonnet 5** for the SGT lab screen | Masking is a privacy guarantee; the UI is routine | 100–150 | ~10–15M | ~100–150k |
| any | Replay diffs, health checks, test triage, doc sweeps | **Haiku 4.5** | Running `tools/sgt_replay.py` and summarising results needs no design judgement | small | small | small |
| | **Total** | | | | **~30–50M** | **~0.4–0.55M** |

Suggested stopping point: build C, look at its accuracy gain on the corpus, then decide on A.
D depends on A (the miner is far easier over nodes than over lines), so it goes last.

### Model per step — rules for the agent doing the work

Phases 2 and 7 use two models, so they are split into steps with a **hard stop** between them.

**Rules:**
- **Check the model first.** Before starting a step, check the model you are running as against
  the table. If it doesn't match, do no work on that step: tell the user which model the step
  needs and stop.
- **Stop at every STOP.** When a step marked STOP is finished, stop. Report what was built, what
  was tested, and which step and model come next. Do not go on to the next step, even if it looks
  small or obvious, and even if the user's earlier instruction was to "do phase 2" or "do phase 7".
  The user switches the model and starts the next step themselves, ideally in a fresh session.
- **Leave a hand-off note.** Put it in the "Hand-off notes" list below this table, so the next step
  can start from this document without the previous session's history.
- **Haiku 4.5 row:** it is not a phase. Any step may hand routine runs to it; it never needs a stop.

| Order | Step | Model | Why | Stop after? |
|---|---|---|---|---|
| 1 | **C**: GSTIN checksum, PAN cross-checks, OCR slot correction, confidence that builds up across pages | **Sonnet 5** | Well-defined algorithms plus tests, with little design risk | **STOP** — user judges the accuracy gain on the corpus before approving A |
| 2a | **A, design and core**: node model from the probe (text, type, box, table cell, selected), resolver and spec loader changes, header-keyed table specs, label→value by layout, `lines` view kept for old specs | **Opus 5.5** | Everything later builds on it, and all existing specs and replay results must stay unchanged | **STOP** — hand off to Sonnet 5 |
| 2b | **A, migration**: move existing record specs to header-keyed tables where it helps, new golden scenarios, test updates, replay diff clean | **Sonnet 5** | Mechanical work against the design 2a fixed | **STOP** |
| 3 | **B1**: catch success messages that flash on screen for under a second (UIA live-region / notification events) | **Opus 5.5** | Tricky Windows event handling alongside the existing UIA worker, and it has to be checked in real Edge | **STOP** |
| 4 | **B2**: watch the Downloads folder for ITR-V and acknowledgement PDFs (only if the user puts it in scope) | **Sonnet 5** | A contained new source feeding the existing resolver | **STOP** |
| 5 | **B3**: use the SCA password fill as a first guess at which client this session is (only if the user puts it in scope) | **Opus 5.5** | Touches the SCA v2 security rules, so it needs careful review more than code | **STOP** |
| 6 | **E**: one config file per portal (portal packs) | **Sonnet 5** | Mostly reorganising JSON and the loader, with existing tests to catch breakage | **STOP** |
| 7a | **D, logic**: corpus miner, spec drafting, masked-LLM drafting and its masking rules | **Opus 5.5** | The masking is a privacy guarantee: no taxpayer value may reach the LLM | **STOP** — hand off to Sonnet 5 |
| 7b | **D, screen**: the "SGT lab" screen (teach by pointing, draft review, replay diff view, Accept writes the override file) | **Sonnet 5** | Routine UI on top of 7a's logic | **STOP** |
| any | Running replay comparisons, health checks, triaging test results, tidying docs | **Haiku 4.5** | Needs no design judgement | no stop needed |

### Hand-off notes

One entry per finished step: date, step, what was built, what's left, and anything the next
step's model must know.

- *(none yet)*

### Open decisions (the user's)

- **Scope of B2 and B3:** both read something outside the browser window (the Downloads folder;
  the SCA fill event). Do they fit how SGT should be scoped, or does SGT stay page-only?
- **Start order:** C first (cheap, quick accuracy win) as proposed, or A first.
