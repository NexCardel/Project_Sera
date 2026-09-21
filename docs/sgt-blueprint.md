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

### The return in progress — datasets from many pages (added after the first shadow test)

The first shadow test showed form and period only being captured on View Filed Returns: the
filing-wizard pages had no specs, and a piece that could not identify a return on its own page
(a form without a period) was thrown away. Writing a spec per wizard page would have turned SGT
into VSDC-X in a JSON file, so the fix is page-agnostic (`current_return` in `sgt_fields.json`):

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
  a complete return nothing submitted → "In Progress", but only if some piece came from the
  link (the portals only put the form/year in the link while you are inside that return).
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

---

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
