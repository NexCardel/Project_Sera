# Sera Global Tracker (SGT) — blueprint

**Status (2026-09-26): LIVE — SGT is the main capture engine.** Shadow testing is over (user,
2026-09-26). SGT live runs alone: VSDC, VSDC-X and VSDC 24/7 are switched off. See §13. Shadow
mode still exists (Settings → Tracker) for comparing SGT against the other engines.

*Shadow mode, as first built:* its returns go into the tracker dump as their own tagged rows
(decided 2026-09-21: "mixed into the tracker, tagged"), beside the other engines' rows, plus the
HUD pill and a local log.

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

**Status:** proposed, nothing built. **Superseded by §14 (2026-09-27)**, which absorbs A, B1,
B2, C and D into the SGT-C / SGT-I design and drops the masked-LLM drafting (the user declined it). Kept
for its record of the limits found in the code; do not start work from this section's step table.
Two decisions are still the user's (see "Open decisions").
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

---

## 13. SGT live (built 2026-09-26)

Decided by the user on 2026-09-26, before any §12 enhancement: shadow testing is over, SGT
becomes the main capture engine. The four choices, all as recommended:

1. **SGT alone.** VSDC, VSDC-X and VSDC 24/7 off. (The SDC browser extension is separate and
   unaffected.)
2. **Merge.** Live rows use the tracker's canonical key, so a filing another engine saved
   earlier is the same row, updated in place; its status never moves down.
3. **Live on every PC.** Settings are office-wide (synced), so a one-time switch-over does it.
4. **Convert** the shadow rows already in the tracker.

**What was built:**

| Piece | Where |
| :--- | :--- |
| Mode `off / shadow / live`, default `live`; engine defaults all off | `core/vsdc/vsdc_engines.py`, Settings → Tracker |
| One-time switch-over: `sgt_mode=live`, the other three off, marker `sgt_live_rollout_done` | `apply_sgt_live_rollout`, called from `main._apply_vsdc_engine_settings` |
| Engine mode: capture method `SGT_live`, HUD tag "SGT (Live)", `raw_payload.source.mode` | `SgtShadow(mode=...)`, `set_mode()` (module name kept) |
| Canonical key, shared with the database | `core/dataset_key.py`; `SgtShadow.live_key` |
| Router: `SGT_MODE=live` accepted; changing mode ends open sessions first; the portal-address tripwire runs while SGT is on | `core/vsdc/vsdc_router.py` |
| Client never identified → row written unassigned + phone alert, once per dataset | `SgtShadow._alert_unknown_client` → `AlertSender.notify_unattributed_submission` |
| Shadow rows → live rows, status-aware merge, on the admin PC's start-up | `SeraDatabase._convert_sgt_shadow_rows` |

**Keys in live mode.** `compute_dataset_key(portal, GSTIN or PAN, form, period)` — the same
identifier order the database uses for any row. Until the client is known the identifier is
`SGT<session>` (it can never be a real client's key); the row is superseded under the client's
key once the PAN is read. A dataset known only by its ARN is keyed `...:FORM:ARN_<n>`.

**Database rules that changed with it** (`insert_tracker_dump`):
- The "same side only" isolation now applies to *shadow* rows only; live rows are an ordinary
  engine's rows.
- The 10-second ARN burst guard skips SGT rows: SGT re-sends a row only when it changed, and
  the guard was dropping a status that climbed seconds after the ARN.
- No proximity attribution for SGT rows: a row without a PAN is one whose client SGT has not
  identified, and "whoever was captured on this portal in the last 15 minutes" put filings on the
  previous client. (This affected shadow rows too.)
- `delete_sgt_rows_by_dataset_key` accepts any key but only ever deletes rows SGT wrote.
- The admin PC's start-up key rewrite skips SGT rows. It used to turn shadow rows' `SGT:` keys
  into ordinary keys, and the purge after it folded them into other engines' rows.

**Still true / not done:**
- The toast / tray balloon stays off for SGT rows; the HUD pill is SGT's notifier.
- The measured agreement with VSDC (§10a's go-live bar) was never run — the user judged the field
  testing sufficient.
- The folder stays `sgt_shadow/` (log, crash snapshot, spec stats) so recovery works across the
  switch.

Tests: `tests/test_sgt_live.py`.

---

## 14. SGT-C and SGT-I — a smarter SGT, without AI (proposed 2026-09-27)

**Status:** proposed, nothing built. Supersedes §12. Written from a design conversation with the
user on 2026-09-27; the ideas marked *(user)* are the user's own, the rest were proposed and
checked in that conversation.

**The one rule over everything in this section: no AI.** No trained model and no LLM, anywhere
— not in capture and not in offline authoring. Everything below is geometry, checksums, grammar
rules, constraint checks and counting. Every decision can be repeated and explained in one line.

Every rule from earlier sections still holds: config not code, strictly passive (never click or
type into a portal), global fixes rather than page-shaped ones, "dataset" not "return", the
4-level submit ladder, one sighting of a PAN/GSTIN attributes at once, never drop a dataset, and
every change passes `tests/test_sgt_replay.py` plus a `tools/sgt_replay.py diff` on the corpus.

---

### 14.0 The whole idea on one page

SGT is split into two halves *(user)*:

* **SGT-C (Core)** is SGT as it is today. It captures the datapoints someone registered by hand
  in `sgt_fields.json`, the same way every time. It is what the tracker trusts.
* **SGT-I (Intelligence)** watches the same pages *beside* the Core. It learns how each portal is
  built, works out where the user is, finds values nobody registered, discovers the rules those
  values follow — and turns what it learns into **proposals**. It never changes what the Core
  captures.

The only road from SGT-I into SGT-C is **your approval**: an accepted proposal becomes an ordinary
spec in `sgt_fields.json`, and from then on it is Core.

```
                 the browser page (read ONCE)
                           |
          +----------------+-----------------+
          |                                  |
      SGT-C (Core)                     SGT-I (Intelligence)
   registered specs only          read-only copy of the page and
   -> tracker rows                of what the Core captured
          ^                                  |
          |                          1  page map (nodes, zones, sections)
          |                          2  every label -> value pair
          |                          3  maths: the rules values follow
          |                          4  the atlas: the portal, built bit by bit
          |                          5  the GPS: where the user is
          |                          6  what sentences claim, what pages are
          |                          7  evidence, explanations, second opinion
          |                          8  what Sera already knows
          |                          9  what flashes, what is downloaded
          |                         10  what was not understood
          |                                  |
          |                         11  the miner -> proposals -> SGT lab
          |                                  |
          +------------ YOUR APPROVAL -------+
             (a proposal becomes a Core spec)

   SGT-I may also: add extra facts to a row (route, explanation) in their own
   fields, and ask the Core to read MORE often. Never less, never different.
```

Each numbered step in 14.4 uses what the step before it produced. Read them in order.

---

### 14.1 Why: the ceiling SGT is hitting

SGT flattens each page into lines, then a regex looks for "label, then value". Every bug fixed so
far was the same missing understanding, patched one at a time:

| Bug we hit | What SGT did not understand | Fixed in general by |
| :--- | :--- | :--- |
| "Belated" read from the option list under "Filed u/s" | an option list vs the chosen value | step 1 (page map) |
| A revised-return wizard's original ack read as a new submission | a reference to the past vs an event now | step 6 (sentences) |
| "Search Box Input Field" captured as a client name | a control vs content | step 1 (zones) |
| Landlord / donee PANs in the ITR wizard | whose section a value sits in | step 1 (sections) |
| The truncated header name beating the profile name | how far each part of a page is trusted | step 1 (zones) + step 7 |
| "Return Successfully Verified" drawn on step 1 of a stepper | what is to come vs what has happened | step 6 (sentences) |

"Smarter" means SGT makes those distinctions itself, the same way on every portal.

---

### 14.2 The big rule: two halves, and the contract between them *(user)*

**Why split at all:** SGT is now the office's only capture engine (§13). Intelligence is where
new mistakes come from. The split means that if SGT-I ever misbehaves, it is switched off and SGT
behaves exactly as it does today — nothing it learned can have damaged a single Core capture.

**The contract — six rules:**

1. **Information flows one way.** SGT-I gets a read-only copy of what the Core read and captured.
   It never writes into the Core's sessions, slots or rows.
2. **SGT-I reaches the Core only through your approval.** Proposals are accepted in the SGT lab
   (step 11) and written as ordinary specs. Nothing learned is ever applied silently.
3. **SGT-I may make the Core look harder, never less.** Asking for extra reads near a submission
   (step 5) is allowed. Skipping a read, vetoing a capture or changing a value is not.
4. **Enrichment sits beside the Core's values, never in place of them.** Route position
   ("stopped at Payment"), explanations and second opinions go in their own fields on the row.
5. **Failure is isolated.** SGT-I runs after the Core has finished each tick, on its own thread,
   with a time budget. An exception, a budget overrun or a hang (watched by
   `core/hang_watchdog.py`) switches SGT-I off for the rest of the run. The Core never notices.
6. **One switch:** Settings → Tracker → **SGT-I: Off / On**. Off = exactly today's behaviour.

**Graduation — how an intelligent rule becomes a Core rule.** Some intelligence would change
captures if applied directly: OCR correction, "this ack is only a reference", dataset
constraints, correction from the client list. Under rule 3 they start as a **second opinion**:
SGT-I flags its disagreement on the row, and the Core's value stands. A rule graduates into the
Core only when (a) the replay suite and a corpus diff show it helps and breaks nothing, and
(b) you approve it. It then becomes a toolbox tool or a spec like any other. This is the same
road SGT itself took from shadow to live.

**The one shared part: reading the page.** The Core reads *lines*; SGT-I wants *nodes*. Reading
twice would double the UIA cost (41–935 ms a page, §7), so both share **one** read. The gate:
the lines produced from the new node read must be **identical** to today's lines on the whole
recorded corpus before the Core switches to it. Until they are, the Core keeps its current
reader and SGT-I does its own read, on its own budget.

---

### 14.3 What goes where

| SGT-C (Core) — built today, registered by hand | SGT-I (Intelligence) — learns, infers, proposes |
| :--- | :--- |
| scope gate (allowed portals), change gate | page map: zones, sections, layout pairing (step 1) |
| reading the page: UIA, OCR fallback (shared read) | every label → value pair, generic types (step 2) |
| `sgt_fields.json`, loader + self-tests, toolbox | maths: shapes, checksums, relations (step 3) |
| resolver (profile, records, dataset in progress) | the atlas (step 4) |
| profile builder, latching, name promotion | the GPS (step 5) |
| submit ladder, `submit_rules`, `dataset_rules` | sentences and page kinds (step 6) |
| identity rules, contradiction splits | evidence ledger, explanations, second opinion (step 7) |
| sessions, crash snapshot, recovery | client list, tracker expectations, self-healing (step 8) |
| tracker rows, live keys, unattributed path + phone alert | UIA events, diffing, Downloads (step 9) |
| HUD pill | the "not understood" report (step 10) |
| replay harness, golden tests | the miner and the SGT lab (step 11) |
| | page recorder (`sgt_corpus.py`), health watch (`sgt_health.py`) — they only observe |

---

### 14.4 One page's journey through SGT-I, step by step

#### Step 1 — Read the page as a map, not a list of lines

*In plain words:* today SGT sees a page like a shopping list. Step 1 sees it like a floor plan —
what is a heading, what sits in which box, what is a table, what is a pop-up.

* **Nodes, not lines.** Each piece of text keeps its role, its box on screen, its table row and
  column, its heading level, its region (header / navigation / main / dialog), and whether it is
  selected. OCR also returns boxes, so the same works on canvas portals.
* **One cross-process call.** A UIA *cache request* (`FindAllBuildCache`) fetches all of this at
  once. Today's reader asks element by element, so the richer read may even be *cheaper* than the
  935 ms heavy page. **Measured before anything is built on it.**
* **Zones, each trusted differently:** page header (the logged-in identity), main content, dialog
  (what just happened), stepper (progress), help / FAQ (never evidence), navigation and footer
  (ignored).
* **Labels pair with values by layout:** to the right, directly below, or the same table row under
  a column header — not by line order.
* **Sections:** a value belongs to the heading it sits under. A PAN under "Landlord details" is
  the landlord's.

*Gives:* a structured page. *Feeds:* every later step.

#### Step 2 — Pick up every pair: container → value *(user)*

*In plain words:* SGT writes down every "label: value" pair on the page, even the ones nobody
asked for — but it does not pretend to know what they mean.

* The label is the **container**; what sits beside it is the **value**. Containers can nest:
  `Bank Details › Account Number`. In a table, the column header is the container.
* Values get **generic types**, not meanings: text, number, amount, date, code (letters and
  digits mixed), email, phone, choice (a ticked option or dropdown value), percentage, yes/no.
  Specific meanings (PAN, period…) exist only in the Core's registered specs.
* **Privacy rule:** unknown pairs include things SGT must never store — Aadhaar, bank account
  numbers, IFSC, addresses, family members' PANs. So SGT-I keeps the **container, the type and the
  masked shape** (`9999 9999 9999`), **never the value**. A value is stored only after you
  register that container as a Core datapoint.

*Gives:* a list of (container, type, shape) for every page. *Feeds:* step 3 (what to measure) and
step 4 (what to remember).

#### Step 3 — Recognise patterns with mathematics *(user)*

*In plain words:* by looking at many values from the same container, SGT works out the rules they
obey — their shape, their check digit, what is hidden inside them — and proves it with numbers.

* **Shape grammar.** Each value becomes a class string (`AAAAA9999A`: A = letter, 9 = digit).
  Across many values the common shape is generalised into a pattern ("always exactly 15 digits").
  This drafts a spec's regex without anyone writing it.
* **Checksum discovery.** Known check-digit schemes (Luhn, Verhoeff, mod-11, mod-36) are tested
  against a container's values. The maths bounds luck: a random value passes a mod-36 check 1 time
  in 36, so 30 values all passing by chance is about 1 in 10^46. Found this way, a checksum is a
  proven fact, not a guess.
* **Values inside values.** Is part of one container always equal to another? This finds, unaided,
  that a GSTIN contains the PAN (characters 3–12) and that an ITR ack ends with its own date
  (DDMMYY).
* **Relations between containers.** "Filed on ≤ Processed on" always; "Total = sum of the rows".
  Invariant mining — counting, the idea behind the Daikon tool, not learning.
* **What kind of container is it?** Pure counting answers it:

  | How its values behave | What the container is |
  | :--- | :--- |
  | identical for every client | template text, not data |
  | a few values that repeat | a vocabulary (e.g. status wording) |
  | stable for one client across sessions, different between clients | a **profile** datapoint |
  | changes for the same client from period to period | a **dataset** datapoint |
  | unique every time | an identifier |

* **Where maths stops:** it proves *structure*, never *meaning*. It can prove a container holds a
  stable, checksummed, 15-character code belonging to the client; it cannot know the code is
  called a GSTIN. The label and you supply the meaning.
* **Measuring without keeping values** (step 2's privacy rule): SGT-I keeps only summaries —
  shape counts, how often each checksum passes, and *salted hashes* to count distinct values and
  test per-client stability. The hashes and their salt stay on their PC and never sync: the
  space of possible PANs is small enough that an unsalted hash could be reversed by trying them
  all.

*Gives:* for every container, its proven shape, checks, relations and kind. *Feeds:* step 4 (the
atlas records them) and step 11 (the miner drafts specs from them).

#### Step 4 — Remember the portal: the atlas *(user — the fingerprint idea)*

*In plain words:* like a phone enrolling a fingerprint — each touch captures part of the finger,
the phone ignores the parts it already has and keeps only the new ones — SGT builds each portal's
structure bit by bit, visit after visit.

| Fingerprint enrolment | Portal atlas |
| :--- | :--- |
| each touch captures part of the finger | each page read captures part of a portal |
| matched against what is already enrolled | matched against the pages already in the atlas |
| known area ignored, new ridges added | known structure only counted; new sections, dialogs, columns added |
| the print fills in over many touches | the portal fills in over many visits and many clients |

**Where the analogy breaks — and what the atlas does about it:**

1. **A finger never changes; a portal does.** Every part of the atlas carries first-seen and
   last-seen dates. What is not seen for a while fades, then retires. A redesign shows exactly
   what changed ("Date of Birth" gone, "DOB" appeared in the same place) — a far sharper alarm
   than today's `sgt_health.py`.
2. **A page mixes the portal's words with client data.** On a first visit SGT cannot tell the
   label "PAN" from "RAVI MEHTA". Rule: text enters the atlas as *structure* only after it has
   been seen **identical for several different clients**; until then only its masked shape is
   stored. So the atlas holds no client data, can be synced between PCs, and outlives the 30-day
   page recordings.

**How it works:**

* *Same page?* Decided by structure — the overlap of (role, label) sets — with the address only as
  a hint (single-page apps reuse addresses).
* *Lists:* a page with 3 cards and one with 14 are the same page; repeated blocks collapse into
  one "repeating block" entry, or the atlas would grow without limit.
* *Mixed text:* "Welcome, RAVI MEHTA" is stored as `Welcome, «name»`.
* *Sometimes-there parts* (dialogs, error banners, expanded sections, tab panels) are optional
  regions with how often they appear — the "new ridges".
* *Cost:* merging a page is a few milliseconds.

**Format:** one JSON file per portal, e.g. `sgt_atlas/gst.gov.in.json`, written atomically like
`sessions_state.json`. Readable, diffable, syncable; SQLite only if it ever outgrows JSON.

```json
{
  "portal": "gst.gov.in",
  "version": 17,
  "pages": [
    {
      "id": "p-3f9a",
      "fingerprint": ["heading:Returns Dashboard", "label:Financial Year"],
      "url_hints": ["/returns/auth/dashboard"],
      "kind": "dashboard",
      "first_seen": "2026-09-28", "last_seen": "2026-10-14",
      "visits": 212, "clients": 41,
      "regions": [
        {"role": "dialog", "label": "Filing Successful", "optional": true, "seen": 38}
      ],
      "slots": [
        {"container": "Financial Year", "type": "choice", "shape": "9999-99",
         "kind": "dataset", "claimed_by": "gst_fy"}
      ]
    }
  ],
  "transitions": [{"from": "p-3f9a", "to": "p-81c2", "count": 96}]
}
```

**Written rule:** the Core never *needs* the atlas. Empty or corrupt atlas = SGT exactly as today.

*Gives:* a lasting map of every portal. *Feeds:* step 5 (transitions), step 6 (page kinds),
step 10 (what is unclaimed), step 11 (the miner works from it instead of raw recordings).

#### Step 5 — Know where the user is: the GPS *(user)*

*In plain words:* the atlas is the map; the GPS says where on it the user stands, which route they
are on, and what usually comes next.

* The atlas's transitions ("this page led to that one, 96 times") plus the last few pages visited
  answer: **which page**, **which route** (e.g. a GSTR-3B filing path: dashboard → return →
  preview → payment → submit → confirmation), **how far along** (step 4 of 6, confirmation next).
* It is a **graph, not a chain**: back/forward, reloads and bookmarks are normal. A page shared by
  several routes (payment) is settled by the short lookback; if still unclear, no guess.
* Routes are **not** written by hand: a route is a frequent path from a starting kind to a
  confirmation kind, named by what its pages say (the form).

**What it gives:**

1. **Read harder near the finish line** — the biggest capture gain. When the next page is usually
   the confirmation, SGT-I asks the Core to loosen the change gate and read more often, so a
   success toast shown for under a second is not missed. Elsewhere reads can stay as they are.
   (Contract rule 3: more, never less.)
2. **Context carried along the route.** A confirmation that leaves out the form or period gets them
   from the route it came along — a principled version of §10's "dataset in progress". Offered as a
   second opinion until it graduates.
3. **Rows show where work stopped** — *decided 2026-09-27: option 1, rows only.* A row can say
   "reached Payment, not submitted" instead of a bare "Draft". There is **no** live view of where
   each PC is right now.
4. **Dead reckoning:** when a page cannot be read (loading, UIA blind), the likely position comes
   from the previous page plus the usual transition.
5. **Odd jumps** (a GSTR-1 route jumping into a GSTR-3B confirmation) are flagged rather than given
   the wrong context. A tab switch in the same window shows up as such a jump and closes the route.

*Guards:* context flows only along observed transitions, inside one session; a value read on the
page itself always wins; the dashboard or a logout ends the route. The GPS stores no client data.

*Gives:* position, route and progress. *Feeds:* step 7 (context as evidence) and the Core's read
timing.

#### Step 6 — Understand what sentences claim, and what kind of page this is

*In plain words:* "your return has been verified" and "you will receive an acknowledgement" both
contain the right words; only one of them says something happened.

* **Assertion checker** (rule-based, the NegEx technique from clinical text): every statement is
  one of

  | Class | Example |
  | :--- | :--- |
  | happened | "Your return has been successfully e-verified" |
  | negated | "e-Verification failed", "not yet filed" |
  | future / conditional | "You will receive an acknowledgement…", a stepper step not reached |
  | reference to the past | "Acknowledgement Number of Original Return: …" |

  Trigger words live in config. One mechanism covers the past-tense rule, the future-stepper rule
  and the ack gate on every portal.
* **Page kinds:** login, dashboard, profile, list, wizard step, confirmation, error, payment —
  from content signals (a stepper, repeated cards, a dialog holding an identifier and *happened*
  wording, a page that is mostly inputs) and from the atlas. Rules can then say "confirmation +
  identifier + happened" once, for every portal, with no URLs and no per-page specs.

*Gives:* what each statement means and what each page is. *Feeds:* step 7.

#### Step 7 — Weigh the evidence, explain it, and give a second opinion

*In plain words:* SGT-I keeps a notebook of *why* it believes each value — and says so on the row.

* **Evidence ledger:** every value is a belief with its sightings — page, zone, source (UIA / OCR /
  PDF), checks passed, route context (step 5), times seen. Its score comes from weights in config:
  hand-set, then tuned by counting on the corpus. Statistics, not a model.
* **Retraction:** when evidence turns out to come from another client's page, everything that
  depended on it is withdrawn automatically (truth maintenance).
* **Explanations on the row:** e.g. "Submitted & Verified — ack …270926 on a confirmation page +
  'successfully e-verified', read twice, UIA." In its own field, beside the Core's value.
* **Second opinion:** where SGT-I disagrees with the Core it says so on the row — "this ack looks
  like a reference to the original return", "ITR-6 with an individual's PAN (P) cannot be right".
  The Core's value stands until the rule graduates (14.2).
* **Dataset constraints** it checks: ITR-6 needs a company PAN (C) and ITR-1 an individual's (P);
  a quarterly form needs a quarterly period; an identifier's own date falls inside its period; a
  form belongs to its portal.

*Gives:* confidence, reasons and disagreements. *Feeds:* the rows (enrichment) and step 11
(disagreements that keep being right are graduation candidates).

#### Step 8 — Use what Sera already knows

*In plain words:* Sera already has the office's client list and the tracker. They are the answer
key.

Local and read-only; nothing leaves the PC.

* **The client list as a dictionary.** A PAN/GSTIN read that *fails* its shape and, after
  correction, matches exactly one known client is recovered. A read that is already valid is
  **never** swapped for another client's — a new client must not be snapped onto an old one.
* **OCR correction by constraints** (from steps 2–3): candidates from known confusions (O/0, I/1,
  S/5, B/8, Z/2) in the slots where they are possible, kept only if they pass the type's
  arithmetic. A GSTIN with one misread character is recovered exactly by its checksum. More than
  one survivor = no value.
* **Name ↔ PAN cross-check** against the client list catches wrong-client reads. A masked value
  (`98XXXXXX12`) may confirm an identity; it is never stored.
* **Expectations from the tracker.** The tracker knows what is due (client X, GSTR-3B, August, not
  submitted). When X's route reaches a confirmation, that filing is the prime candidate; a filing
  for a period already filed is flagged as a revision or duplicate.
* **Self-healing (known-value anchoring).** When a value Sera already holds for this client (PAN,
  DOB, email, ack) appears where no spec reads it, the container beside it is recorded as a new
  wording for that known field. "Date of Birth" renamed "DOB" is found this way.

All of it is second opinion or proposal until graduated.

*Gives:* corrections, cross-checks, expectations, new label wordings. *Feeds:* step 7 and step 11.

#### Step 9 — Catch what flashes, and what is downloaded

*In plain words:* some evidence is on screen for under a second, and the best evidence of all is
a PDF the user saves.

* **UIA events:** live-region-changed, notification and window-opened events catch a toast shown
  for under a second.
* **Diffing:** comparing each page map with the previous one says what just *appeared*; a new
  dialog *is* the event.
* **Downloads folder** *(only if put in scope — open decision)*: the ITR-V / acknowledgement / ARN
  receipt PDF, read as a third source beside UIA and OCR.

*Gives:* events the polling misses. *Feeds:* step 7.

#### Step 10 — Report what was not understood

*In plain words:* SGT-I tells you where it is blind.

Every page leaves a residue: containers and typed values that no spec claimed. Only counts and
shapes are kept. The health report then says, for example, "GST confirmation pages: ARN-shaped
value seen 4×, claimed 0×". Silent misses become visible, and each one is a lead for step 11.

#### Step 11 — The miner and the SGT lab: from learning to a Core spec

*In plain words:* everything steps 1–10 learned becomes a short list of suggestions. You say yes
or no. A yes becomes part of the Core.

**Where proposals come from** (all counting, none of it AI):

1. **Template vs data** — from the atlas (step 4): every "fixed container → changing value" slot
   that no spec claims. The same idea as the classic wrapper-induction algorithms RoadRunner and
   ExAlg (string alignment, not learning).
2. **Known-value anchoring** — from step 8: a new wording for a field the Core already knows.
3. **Repeated structure → list records** — from the atlas's repeating blocks: the record spec for
   a new list or table (where a card starts, its fields, which are required), with no
   hand-written `start` regex.
4. **Status wording from outcomes** — phrases that appeared just before an identifier first showed
   up, for datasets whose status was later settled. **You map each to a ladder level**; the miner
   never decides a level.
5. **Graduation candidates** — second opinions (steps 7–8) that the replay shows would have been
   right, offered as toolbox rules.

**Every drafted spec is filled in from step 3:** the regex from the shape grammar, the checks from
the proven checksums and relations, profile vs dataset from the container's behaviour.

**Ranking and gates:**

* *support* — seen for at least N different clients, never one session only;
* *type consistency* — e.g. 98 % of the slot's values are dates; a mixed slot is dropped;
* *placement* — never from help zones, controls or navigation, never a placeholder;
* *replay diff on the whole corpus* — what the proposal adds; a proposal that would change **any**
  existing capture is flagged red.

**A proposal** is a card in the **SGT lab** screen: container, page kind, type, support, proven
rules, drafted spec, replay diff. **Accept** writes it to the local override `sgt_fields.json`; a
developer later promotes it into the shipped file.

**Examples must be fictional.** Specs must carry examples (§4), and the shipped file goes into the
installer and the repository. So drafted examples use made-up values of the same shape (a
structurally valid fictional PAN, a GSTIN with a valid checksum) from a small generator per type.
Real client values never reach it.

**Teach by pointing** is the manual way in: click a value on a recorded page and name it; SGT
works out container, zone and type, finds every other occurrence, and shows the same replay diff.

---

### 14.5 Privacy rules, all in one place

1. Unknown values are never stored — only container, type and masked shape (step 2).
2. Text enters the atlas as structure only when identical across several different clients
   (step 4).
3. Statistics over values use salted hashes; hashes and salt never leave their PC (step 3).
4. The client list and tracker are read locally and read-only (step 8).
5. Masked values confirm; they are never stored (step 8).
6. Spec examples are fictional (step 11). The corpus never goes into the repository.
7. What may sync between PCs: the atlas, GPS transitions and proposals — structure, counts and
   shapes only.
8. The GPS enriches rows only; there is no live view of where each PC is (decision 2026-09-27).

---

### 14.6 Limits and risks

| Risk | Answer |
| :--- | :--- |
| More machinery, more ways to be wrong | the SGT-C / SGT-I split: switch SGT-I off and nothing it did remains in a capture |
| SGT-I slows or hangs the Core | runs after the Core's tick, own thread, time budget, hang watchdog, switches itself off |
| The shared read changes Core lines | node read adopted by the Core only when its lines are identical on the whole corpus |
| New wording misread at first | sentences and page kinds abstain when unsure; step 10 shows where |
| Weights and statistics need data | hand-set first; tuned by counting once the corpus has weeks of pages |
| Cold start (atlas empty) | the Core never depends on SGT-I; SGT-I only adds as it learns |
| Snapping a new client onto a known one | only reads that *fail* their shape are corrected, only to a unique match |
| Maths finds structure, not meaning | meaning comes from the label and from you, in the SGT lab |

**Found 2026-09-27:** `~/AmanAssociates_Sera/sgt_corpus/` on the dev PC holds only
`pages_2026-09-22.jsonl`, while the SGT logs continue to 26 Sep. Recording appears to have
stopped. The corpus is the fuel for steps 3, 7, 11 and every replay check, so it is step 0.

---

### 14.7 Build order

Each step starts in a fresh session from this section and ends with a hand-off note in 14.9.

| Step | What | Why here |
| :--- | :--- | :--- |
| 0 | Confirm / fix corpus recording | the fuel for everything |
| 1 | The SGT-C / SGT-I split: the contract, the thread + budget + watchdog, the SGT-I switch, row fields for enrichment | the safety frame, before any intelligence exists |
| 2 | Step 1 — page map; measure the cached read; lines-equivalence check on the corpus; recorder stores nodes | the foundation |
| 3 | Step 2 — container → value pairs, generic types, masking | needs the page map |
| 4 | Step 3 — the maths (shapes, checksums, values inside values, relations, container kinds) | needs pairs |
| 5 | Step 4 — the atlas | needs pairs and maths |
| 6 | Step 5 — the GPS; "read harder near the finish line"; rows show where work stopped | needs the atlas |
| 7 | Step 6 — sentences and page kinds | uses atlas + page map |
| 8 | Step 7 — evidence ledger, explanations, second opinions | uses everything above |
| 9 | Step 8 — client list, tracker expectations, OCR correction, self-healing | needs decision 1 |
| 10 | Step 9 — UIA events and diffing; Downloads if in scope | needs decision 2 |
| 11 | Step 10 — the "not understood" report | small; can ride along from step 5 on |
| 12 | Step 11 — the miner and the SGT lab screen | after weeks of atlas and corpus |

---

### 14.8 Decisions

**Taken (2026-09-27):**

* No AI anywhere, including offline spec drafting (the §12 masked-LLM idea is dropped).
* SGT is split into SGT-C and SGT-I; intelligence must never affect Core capture.
* GPS: rows only ("stopped at Payment"); no live position view.

**Open (the user's):**

1. **Client list and tracker:** may SGT-I read them (local, read-only) for step 8?
2. **Downloads folder:** in scope for step 9, or does SGT stay page-only?
3. **Mined datapoints with no tracker column** (refund amount, intimation date…): once registered,
   stored in a per-dataset "details" bag shown in the row detail, or only shown in the SGT lab?
4. **Approval:** approve every proposal (recommended for the first months), or auto-accept the
   safest kind — a new wording for an existing field whose replay diff only adds captures?
5. **Pooling:** should the admin PC combine every PC's atlas and proposals (structure, counts,
   shapes — never values) through Sera Sync?

---

### 14.9 Words used in this section

| Word | Meaning |
| :--- | :--- |
| node | one piece of a page with its role, box, table cell and region |
| zone | a region of the page with its own trust: header, main, dialog, stepper, help, navigation |
| container | the label a value sits under (`Bank Details › Account Number`) |
| generic type | what a value looks like (date, amount, code…), not what it means |
| shape | a value with letters as A and digits as 9 (`AAAAA9999A`) |
| template | text that is the same for every client — the portal's own words |
| atlas | SGT-I's lasting map of a portal, built visit by visit |
| route | a usual path through a portal, e.g. a GSTR-3B filing |
| second opinion | SGT-I's disagreement shown on a row; the Core's value stands |
| graduation | an SGT-I rule becoming a Core rule, after replay proof and your approval |
| proposal | a drafted spec waiting for your yes or no in the SGT lab |

### 14.10 Hand-off notes

- **W0-1** (2026-09-27, claude-sonnet-5): `sgt_shadow/spec_stats.json` shows `record_read` firing
  23-27 Sep — right beside `PageRecorder.record()` in `SgtShadow._absorb` — proving SGT kept
  reading real pages while the corpus stayed empty. `record()` only skips silently on
  `not self.enabled or not lines`; lines were non-empty, so `enabled` (the `sgt_record_pages`
  setting) must have gone off with nothing saying so — traced its read/write paths and the
  6ae61c5 mode switch end to end, all correct, none the culprit. Real defect: the flip itself was
  silent. Fix in `core/sgt/sgt_corpus.py`: `enabled` is now a property; every transition and the
  construction-time state are echoed, matching the existing size-cap-pause echo. Behaviour
  otherwise identical (SGT-C unaffected). Tests added in `tests/test_sgt_replay.py` (day
  rollover, restart mid-day dedup, size-cap lift, on/off echo). `pytest tests/test_sgt_*.py`:
  281 passed. If `sgt_record_pages` is still "0" on a real PC, that's Settings -> Tracker to flip.

- **W0-2** (2026-09-27, claude-haiku-4-5-20251001): Baseline established. **SGT tests:** 281 passed
  (all test_sgt_*.py, no regressions). **VSDC tests:** 388 passed, 6 pre-existing failures (all in
  Gemini enricher tests — missing API key, unfinished feature), 1 skipped. **Replay baseline:**
  `C:\Users\Nex\AmanAssociates_Sera\sgt_corpus\replay_baseline.json` holds 8 session(s), 1 dataset
  row(s), replayed from 204 recorded pages. Baseline ready for diff tool. No code changes.

- **W1-1** (2026-09-27, claude-opus-5-5): the C/I contract in code. New `core/sgt_i/`:
  `observation.py` (frozen `Observation`: lines, url, title, portal, source, `PageResult.as_dict()`,
  profile, draft values, session id, ts/today; `make_observation`) and `host.py` (`SgtIntelligence`:
  own daemon thread, queue of 8, 2.0 s page budget, hang = 15 s checked on next `submit`; any
  exception/overrun/hang/bad enrichment trips it off for the run). Components = objects with
  `name` + `observe(obs, ctx)`; `ctx.enrich(dict)` (JSON, <=4 KB) and `ctx.ask_more_reads(n)`
  (max 3/session, 60 s TTL). `default_components()` is empty - add new steps there. Core hooks in
  `sgt_shadow.py`: `_hand_to_sgt_i` after `_absorb`, `wants_read` only when the change gate would skip,
  enrichment into `raw_payload['sgt_i']` in `_tracker_payload`. Setting `sgt_i_mode` (off/on,
  default off) in `vsdc_engines.py`, Settings -> Tracker, `main.py`, router `_apply_sgt_i`.
  `replay_session(..., intelligence=, keep_payloads=)`. Tests `tests/test_sgt_i_host.py` (16); all
  test_sgt_*.py pass (297). Not yet checked in the real app (check-list item added).

- **W1-2** (2026-09-27, claude-sonnet-5): the enrichment channel end to end - no DB schema change
  (decision already made by the WP). Traced the existing plumbing: `sgt_shadow._tracker_payload`
  already puts SGT-I's output at `raw_payload["sgt_i"]` (W1-1); `main.py._handle_extension_result`
  dumps the whole row with `json.dumps(dataset_msg)` into `raw_payload_json`, and
  `database.insert_tracker_dump` stores that TEXT column verbatim - so any component's enrichment
  already survives, untouched, with zero schema work. Added: `PayloadInspectorDialog` in
  `ui/windows/tracker_dump_window.py` now shows an "SGT-I - advisory, read-only" card (pretty-
  printed JSON, `QLabel`, text-selectable, no edit path) whenever `raw_payload.sgt_i` is non-empty,
  right beside the existing Gemini card; absent otherwise, so today's rows are unchanged. Tests:
  `tests/test_sgt_tracker_rows.py` - `test_sgt_i_enrichment_round_trips_beside_the_core_values`
  (DB round trip: `sgt_i` and `sgt_dataset` both come back byte-identical, Core columns/`pan`/`arn`
  unaffected) and `test_payload_inspector_shows_sgt_i_enrichment_read_only` (card appears/hidden
  correctly). `pytest tests/test_sgt_*.py`: 299 passed (was 297; the two new tests). No component
  produces route/explanation/second-opinion data yet (steps 5/7, later WPs) - this only builds and
  proves the channel they will use; nothing to build on for those WPs beyond what W1-1 gave them.

- **W1-R** (2026-09-27, claude-opus-5-5): reviewed W0-1, W1-1, W1-2 against 14.2. Holds: Off = no hook
  runs (`_sgt_i` is None, `core.sgt_i` not even imported); Observation is a deep frozen copy (draft
  `values()` is pure); host lock is never held while calling a component, the Core or echo, so no
  deadlock; `wants_read` can only add reads. **Two defects fixed:** (1) `sgt_shadow` read `self._sgt_i`
  twice while Settings (UI thread) can detach it mid-tick - an AttributeError could escape `observe`
  or, in `_tracker_payload`, make `pop_dispatch` DROP a Core row; now read once into a local.
  (2) Four deep payload scanners (`database._extract_identity_candidates_from_payload`,
  `tracker_dump_parser/identity_resolver._walk_values`, `name_resolver._walk`,
  `ui/utils/profile_parser._collect`) read every string in `raw_payload_json`, so a masked shape like
  "AAAAA9999A" or a `name` key in `raw_payload.sgt_i` would become a client PAN/name candidate (rule 4
  breach); all four now skip the `sgt_i` key. Test: `test_sgt_i_enrichment_is_never_identity_or_name_evidence`.
  `test_sgt_*.py`: 300 passed. Not caused by this change (no payload in them has `sgt_i`): 3 failures in
  test_raw_payload_db_and_srpf / test_dual_pk_and_sad_resolution, plus the 5 known Gemini ones. Next WPs: any new
  deep walker over payloads must skip `sgt_i` too.
