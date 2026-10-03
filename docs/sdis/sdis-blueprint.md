# Sera Distill (SDIS) — blueprint

**Status:** approved 2026-10-03 for building; Parts S–U (containers, portal registration, the field
library) and decisions D3–D16 added 2026-10-04. **Nothing in Parts B–U is built yet.** Baseline: `main` at
`0f30543` (the pre-dev engine). Worktree `../APP-sdis`, branch `sdis`. Deadline **2026-10-06 01:30 IST** (Tuesday; moved from 2026-10-04 23:00 by the user). It is written from the
design conversation of 2026-10-03 and builds on `docs/datapoint-engine-goal.md` (the pre-dev record:
rules R1–R10, measurements, problems P1–P24, questions Q1–Q11). It is built the way Autofill tweaks
was: one set of rules, parts that each say *what is wrong → what changes*, and work packages run one
per fresh session by a dispatcher (`tools/sdis.py` = `tools/sgt_overhaul.py` via `use_project()`),
each ending with a hand-off note in §12.

**What SDIS is:** an engine that finds a portal page's useful datapoints by itself: what is template
and what is client data, and how relevant each datapoint is. It works by comparing the same page
across clients, using **counting and statistics only, with no AI**. The user sees the result in a
**Distill… dialog** in the Tracker dump window's Tools menu.

**What SDIS is for** *(user, 2026-10-03)*: a **quick datapoint-choosing feature**. It gets rid of the
usual useless fluff so the user can pick datapoints fast. It does not need to show absolute
confidence, and there is no truth set to score it against: the user is the final filter. It is a
**learning** feature, not real-time capture: it mines **on one PC (the admin PC), when the user asks**
("Find datapoints", like SGT-I), behind a loading dialog, at most 10% CPU.

**What already exists (pre-dev, `tools/pre_dev/class_diff/`, not wired into the app):**

| Stage | File | Does |
| :--- | :--- | :--- |
| read | `key_probe.py` | snapshots of a page (raw view + control-view flags) |
| key | `keys.py` | a key per element (types, ids, classes, `[n]` counters) |
| merge | `link_map.py` | one client's snapshots of one link → one map, by **alignment** (shape + face) + moved blocks; `group_clients` joins captures sharing a PAN/GSTIN |
| pair | `align.py` | anchors (same shape + same text) in page order, shape between anchors, `pair_moved` for moved blocks |
| memory | `memory.py` | the user's page-memory method: every client's map → memory + flat pending pool, confirmed by N clients, a verdict per node |
| compare | `compare.py` | latest client vs previous client: statuses, labels, checks (CSV) |
| tests | `tests/test_class_diff_align.py`, `tests/test_sdis_memory.py` | 14 tests (alignment labels; R8) |

---

## 0. Rules over everything

1. **Fix the kind, not the case** (R1). Every rule is global: element structure, value types and
   counts. Never a portal name, class name or piece of wording.
2. **No AI.** Counting, matching, percentages and simple statistics only (`docs/sgt-blueprint.md` §14).
3. **One client = one vote per page** (R8). Snapshots, repeat visits and re-renders are never votes.
4. **Matching is by shape and face** (R9): shape = the key without counters, face = the text. A key
   alone never decides that two elements are the same.
5. **Bare numbers are data** (R2). Only text, labels and sentences can be template.
6. **Never fake confidence.** Anything SDIS cannot decide (ambiguous pairing, unknown identity, too
   few clients) is shown with a low percentage or not counted. It is never a silent guess.
7. **Monitoring phase privacy** *(user, 2026-10-03)*: no hashing yet. SDIS runs **only on the admin
   PC**, over the corpus stored **only on the admin PC**. Every other PC deletes its copy once it has
   synced to the admin PC. Hashing comes back before SDIS leaves the monitoring phase.
8. **SGT capture must not change** unless a Part says so explicitly. Anything touching `core/sgt/`,
   `core/sgt_i/` or `core/vsdc/` passes the SGT tests and shows no change in `tools/sgt_replay.py diff`.
9. **Strictly passive toward portals.** SDIS only reads what was already captured.
10. Real captures (`tools/pre_dev/class_diff/output/`) hold client data. They stay on this PC, are
    never committed, and are never pasted into docs, hand-off notes or test fixtures. Tests use fictional pages.

---

## 1. The whole plan on one page

```
 Part A  Baseline: commit the pre-dev engine, engine package, regression runner
            |
            v
 Part B  Value history  ──>  Part C  Noise over time (furniture vs data)          [problem 1]
 Part D  Identity: SGT session id, then a data fingerprint for the rest          [problem 2]
 Part E  Look-alikes: scored pairing + AMBIGUOUS                                 [problem 3]
 Part F  variable_alignment: shared text in a data slot, user can reject         [problem 4]
 Part G  Screens: one link, several pages, told apart by weighted matching       [problem 5]
 Part H  Memory upkeep: retire what stopped appearing, by statistics             [problem 6]
            |
            v
 Part I  Statuses + labels from memory (compare.py becomes a view of memory)
            |
            v
 Part J  Relevance ranking (maths + statistics) -> Relevance %, Sure %, Found on
            |
            v
 Part K  SDIS's own input (raw view + session id + browser), tuned for SDIS (not SGT-I)
 Part M  Every browser captured; compared per browser at mining   Part N  Smart page link resolution
 Part O  "Find datapoints" on demand: loading dialog, app locked, <= 10% CPU, memory on disk
 Part P  Captures travel staff PC -> admin PC (Sera Sync v3 transport), deleted after receipt
 Part Q  A picked datapoint is registered on EVERY PC (synced table -> spec file SGT loads)
 Part R  Firefox support for SGT-C and SDIS (address bar, title, line parity, Firefox fixtures)
 Part S  Classes and containers: Profile builder + Others per portal, Dataset containers; all in one JSON
 Part T  Portal registration from the login links in service settings, same scope gate
 Part U  sdis_mcl: the field library every registered datapoint is a field of
            |
            v
 Part L  Distill… dialog
```

OCR portals (TRACES) are out of scope: SDIS needs an element tree (problem 8, later).

---

## 2. What is wrong or missing today

| # | Problem | Doc ref | Part |
| :--- | :--- | :--- | :--- |
| 1 | **Furniture looks like data.** A rotating notice or a "last updated" date differs between clients, so it reads as data. Only a change *within one client* exposes it today | P5, Q11 | B, C |
| 2 | **Voting needs identity.** A capture with no PAN/GSTIN is "unidentified". If it is a client already counted, that client's data looks like template. Solved by the SGT session id, plus maths for the rest (user, 2026-10-03) | P18, Q10 | D |
| 3 | **Look-alikes are paired silently.** Two values of one shape, no anchor between them, one missing: the first one is paired, with no warning | P15 | E |
| 4 | **Shared values look like template** ("Filed" for most clients). Words in controls are already safe; status words are not | P7 | F |
| 5 | **One link, several pages** (form / result / error) is one memory; truly different screens would wait forever | Q5 | G |
| 6 | **Memory never forgets.** After a portal redesign the old template stays forever | — | H |
| 7 | Privacy: memory and CSVs hold plain values | Q6 | rule 7, K |
| 8 | OCR portals have no tree | Q7 | later |
| 9 | `compare.py` still compares only two clients, with its own same-client skip; it uses neither memory, client grouping nor `pair_moved` | P14, P17 | I |
| 10 | Labels exist only in `compare.py`, not in memory | — | I |
| 11 | Relevance ranking is not built: the core idea's second half is untested | §6 | J |
| 12 | Production input is undecided: the pre-dev probe reads the raw view, SGT-I's corpus is the control view (R4 says production reads raw) | R4, P8 | K |
| 13 | The dialog is not built | R5–R7 | L |
| 14 | Pre-dev code is uncommitted in a checkout other sessions also commit from | P20 | A |
| 15 | Fixture tests were designed by Claude; the real proof is the real captures | P21 | A (regression runner) |
| 16 | **Different browsers give the same page different trees** (shapes differ), so a mixed corpus splits one page's votes. Only GST in Chrome is measured | Q7 | M |
| 17 | **Page links that carry client values in the path** (`/returns/2026-27/…`): every client becomes its own link and nothing is compared. `page_link` drops only the query | — | N |
| 18 | Memory is rebuilt from scratch on every run, so mining gets slower as the corpus grows, and a cancelled run loses its work | — | O |
| 19 | Sera Sync v3 replicates **database rows**, not files; nothing moves capture files to the admin PC | — | P |
| 20 | A field rule accepted today (SGT lab) goes into `sgt_fields.json` **on that PC only** (`sgt_specs.override_path()`); nothing spreads it to the other PCs | — | Q |
| 21 | The installed app is a frozen program: "run mining in a separate process" cannot start `python -m …` | — | O |
| 22 | **SGT gets no URL in Firefox.** Firefox's address bar is a ComboBox (automation id `urlbar-input`); both address readers (`vsdc_router` and `key_probe.read_url`) only look at Edit controls | measured 2026-10-03 | R |
| 23 | Firefox window titles end in " — Mozilla Firefox" (em dash); the title cleanup only strips "-" | measured 2026-10-03 | R |
| 24 | SGT-C has never been checked on Firefox: lines, dropdown/radio "Selected:" lines, the password guard, timing | — | R |
| 25 | **Firefox: SGT reads BACKGROUND TABS too, mixing clients.** Firefox exposes every tab's page (and its hidden New Tab page) as a Document; `read_page_text` and `uia_nodes` read every Document. Verified: client A in front, client B in a background tab → 227 lines holding **both** clients | verified 2026-10-03 | R |
| 26 | **A picked datapoint has no class.** Nothing says whether it is part of the client's profile, part of a dataset (one filing / application / refund), or a standalone value. Only SGT's hand-written specs know (sections `profile`, `records`, `current_dataset`) | user, 2026-10-04 | S |
| 27 | **Datasets cannot be built from picked datapoints.** SGT's datasets are hand-written (form + period + the submit ladder); SDIS has no way to group picked datapoints into one dataset or tell how complete it is | user, 2026-10-04 | S |
| 28 | **Portals are hard-coded.** The scope gate knows two domains (`vsdc_scope.IN_SCOPE_DOMAINS`) plus an optional file; the portals the office actually uses are already typed in as login links in service settings (`services.login_page_link`) | user, 2026-10-04 | T |
| 29 | **The same field on many pages is many datapoints.** PAN on the GST page and PAN on the ITR page are picked, labelled and registered separately; nothing makes them one reusable field | user, 2026-10-04 | U |

Not problems for SDIS any more: P19 (SDIS learns offline on the admin PC, rule 7; its own raw read is guarded in Part K) and Q9 (answered by rule 7: offline over the corpus, never inside SGT-I's thread).

---

## Part A — Baseline

**What changes:**
1. Commit the approved pre-dev work (alignment merge, `pair_moved`, `memory.py`, client grouping,
   `tests/test_sdis_memory.py`, the doc) **with an explicit file list**, before the worktree is made (D10).
2. **Engine package** (D1, taken): the engine moves to `core/sdis/`:

   | Module | From / for |
   | :--- | :--- |
   | `core/sdis/__init__.py` | package doc: the pipeline in five lines |
   | `core/sdis/paths.py` | `data_dir()`: env `SDIS_DATA_DIR`, else `~/AmanAssociates_Sera/sdis/` (admin PC data) |
   | `core/sdis/keys.py` | from `tools/pre_dev/class_diff/keys.py` (its learned-state file path comes from `paths`) |
   | `core/sdis/align.py` | from pre-dev `align.py` |
   | `core/sdis/link_map.py` | from pre-dev `link_map.py` (the capture-file readers stay in pre-dev) |
   | `core/sdis/memory.py` | from pre-dev `memory.py` (engine part; its `main()` stays a pre-dev front end) |
   | later | `history` (B), `noise` (C), `identity` (D), `screens` (G), `links` (N), `labels` (I), `relevance` (J), `store` + `mine` (O), `recorder` (K), `transfer` (P), `register` (Q) |

   The pre-dev files `keys.py`, `align.py`, `link_map.py` and `memory.py` become **module aliases** of the
   core ones: `sys.modules[__name__] = core.sdis.<name>`. A plain `from core.sdis.keys import *` is NOT
   enough, because tests and `compare.py` set `keys.VIEW` and that must reach the real module.
   Every pre-dev command still works. No behaviour change: same tests, same regression numbers.
3. **Regression runner** `tools/sdis_regress.py` (reads the real captures **in place** from
   `../APP/tools/pre_dev/class_diff/output/`, never copies them): runs the whole engine over the real captures in
   `output/` and prints **counts only** (double count, texts lost, verdict counts, both client orders
   equal, flagged counts per state). Every WP runs it before and after its change, and its hand-off note
   records the difference. This answers P21 with real data, without new captures (rule 10: the numbers
   go in notes, never the values).

## Part B — Value history

**Wrong:** a map keeps the distinct values of an element, but not *when* each was seen, so "changes
over days" cannot be computed.
**Change:** every map entry keeps `history`: a list of (time, text), one item each time the text
changes. Memory keeps it per client. This costs nothing to matching and is needed by C.

## Part C — Noise over time (problem 1)

Decided per node, from counts only. The first rule that applies wins:

1. **Changes within one client**: one client, several texts (a rotating notice). Already built.
2. **Changes with time**: on every day it was seen, all clients seen that day agree, **and** it
   changed between days ("Site last updated on …"). This is furniture, not data.
3. **Probably furniture** (low %): differs between clients, but is sentence- or label-shaped and has
   **no label** beside it. An address with a label stays data.
4. Otherwise the verdicts of `memory.py` stand.

Rules 1–2 are certain and rule 3 is a weak signal, so it only lowers the percentage. With few visits,
rule 3 is the only one available; that is stated in the dialog, not hidden. Days come from the capture
time, so the corpus must hold captures from different days (D5).

## Part D — Identity: SGT session id + maths (problem 2)

*(user, 2026-10-03: "easily solved by session id and maths")*

**1. Session id first.** SGT already gives every window session an id (`_Session.session_id` in
`core/sgt/sgt_shadow.py`). The corpus recorder stores it with every page (`record(session=…)`), and
the session's profile collects the PAN/GSTIN from whichever of its pages showed it. So a page with no
PAN/GSTIN on it is still identified: it belongs to its SGT session, and the session's PAN/GSTIN names
the client. Sessions that share a PAN/GSTIN are one client (`group_clients`, chained).

SGT already ends a session at a login page, at a logout, on moving to another portal, and when a
**different PAN/GSTIN** appears (`SgtShadow._observe`, `_identity_conflict`). So one session id
normally means one client. The pre-dev captures have no SGT session id: there, one capture session
stands in for one SGT session.

**2. Maths for what the session id cannot settle:**
* a session that never showed a PAN/GSTIN on any page;
* a session that may hold **two** clients. SGT already ends the session at a login page, a logout,
  or a different PAN/GSTIN, so this is only possible in a session that never shows a PAN/GSTIN. If
  such a session visited **the same page link twice** and the two visits' data disagree (fingerprint
  similarity ≤ 0.5), the session is **undecided** (no vote) rather than split by guesswork.

The maths is the data fingerprint. A client's data is its fingerprint. For such a capture (U),
against every other capture (S) that shares a page with it:

* Pair their maps (alignment). Take only the **data-shaped** pairs (numbers, codes, dates, amounts,
  periods: never text, labels or sentences, rule 5).
* Each value gets a **rarity weight** = 1 / (number of captures that show it). A "0" that everyone
  has weighs almost nothing; an ARN weighs 1.
* similarity = weight of equal pairs / weight of all pairs.
* **Same client** if similarity ≥ 0.9 over at least 3 data values. **Different** if ≤ 0.5 over at
  least 3. Otherwise **undecided**.
* U is merged into S's client if it is the same as any S. It is its own client only if it is
  different from **every** capture it shares a page with. Undecided → **no vote** (D4).

Chained like `group_clients`. Check on today's captures: the two fictional sessions (no ids, the same
fictional page) must come out as **one** client, and the two real GST clients as two.

## Part E — Look-alikes (problem 3)

Between two anchors, when one side has **more** elements of a shape than the other, page order cannot
tell which one is missing. Those elements are paired by **score** instead, keeping page order:

* same **value pattern** (letters → A, digits → 9, runs collapsed: `AB1234` → `A9`, `Filed` → `A`): +1;
* same **screen column** (sideways overlap of the boxes, 0–1): + overlap.

The best order-keeping pairing wins. An element whose best partner beats its best alternative by
less than **0.5** is **AMBIGUOUS**: it gets no vote in memory, shows a low "Sure %", and `compare`
flags it. Where counts are equal, nothing changes (page order is unambiguous).
(A version of this was written and reverted on 2026-10-03 to plan first. It passed the 14 tests and
left the merge numbers unchanged.)

## Part F — variable_alignment (problem 4)

A new state, **`variable_alignment`**: a text **shared by all clients** (would be template) whose
**shape also holds differing values** for some clients, i.e. it sits in a slot that is data elsewhere
("Filed" in a Status column where another row shows "Not filed").

* It is **flagged, not decided**. The dialog lists it, and the user keeps or rejects it (rejections are
  stored, D6). A rejected one is template from then on, for every client.
* It **stays usable as a label** (row names like "Cash ledger" share a shape with values too and will
  be flagged; the user rejects them once).
* Not flagged: label-shaped text (ending in `:`), composites, controls (already data).

## Part G — Screens (problem 5)

One link can show truly different pages. Telling them apart is done by **weighted matching**:

* **Weight** of a node = how rare its (shape, text) is **across links**:
  `log((1 + links) / links showing it)`. Site furniture (header, menu, footer) is on every link and
  weighs about 0; text found only on this link weighs the most. Nodes without text weigh 0.
* For a new snapshot against a screen's map: **cover_new** = weight of the snapshot that matched /
  its total weight; **cover_old** = weight of the map that matched / its total weight.
* **Same screen** if either cover is ≥ 0.5 (a popup on top keeps the old page; a page that grew keeps
  the old part), or if the map weighs ~0 (a loading shell). **New screen** if both are low: the page's
  own content was replaced.
* `link_map` splits a client's snapshots into screens this way. `memory` assigns each client's
  screens to the link's screen memories the same way: best cover, else a new screen memory.

Check: on today's captures, no link splits (none has a real screen change); the fictional popup and
success screens stay one screen; a test page whose content is replaced splits.

## Part H — Memory upkeep (problem 6)

For each **confirmed** memory node: chances = clients since it first appeared; seen = clients that
had it; misses = clients in a row that did not.
`p = (seen + 1) / (chances + 2)` (how often it shows up, smoothed).
**Retire** it when misses ≥ 2 and `(1 − p)^misses < 1%`: the chance that a node this regular is
missing this often by luck is under 1%. Worked examples: a node all of 10 clients had retires after
**4** misses in a row (`p = 11/16`, `(5/16)^4 = 0.0095`); after 3 it stays (`p = 11/15`, `0.019`).
A node 5 of 10 clients had needs about 30. Rarer nodes need more evidence before they are dropped;
that is intended.
Retired nodes leave the matching order (kept in the record as "retired"). If the page goes back, they
return through pending like anything new. Unconfirmed nodes never retire (they may be one client's
own data).

## Part I — Statuses and labels from memory

`compare.py`'s two-client logic becomes a **view of memory**: statuses (fixed, semi-variable,
variable, `variable_alignment`, ambiguous, furniture, waiting) and labels (table row/column, nearest
fixed text in the box, composites never labels, a real letter required, all from the pre-dev rules)
are computed once per node from memory, over every client, not one pair. It uses client grouping,
`pair_moved` and screens. The CSV stays as a debugging view.

## Part J — Relevance ranking

*(user, 2026-10-03: "use the total number of occurrences as comparison weight")*

**The weight is the number of occurrences, counted the right way:**

* **fixed values are ignored** *(user, 2026-10-03)*, so menus, headers and other high-frequency
  template never count, however common they are. **Noise counts as fixed** for this: Part C's
  "changes within one client" and "changes with time" are ignored too. Without that, furniture that is
  *not* fixed ("Site last updated on …": the same for everyone on a day, different between days, on
  every page) would have the most occurrences of all and rank first. Ambiguous pairings (Part E) and
  rejected `variable_alignment` (Part F) are not counted either;
* **once per (client, page)** (R8). Never per row or per snapshot, or a 50-row list would outweigh
  everything;
* **as a share of the clients who visited that page**, so a field on a rarely visited page that every
  visitor has is not ranked below a field on a busy page;
* a datapoint found on several pages (R5) adds up its pages.

**Relevance %** = that share, summed over the pages it was found on and scaled to the best datapoint.
**Found on** = the page links (R5). A datapoint with too few votes (D3) or any undecided-identity
votes shows a low %, never a hidden or confident one (rule 6). No absolute confidence is promised:
the aim is to drop the fluff, and the user picks.

**Slots** learn what they usually hold ("a date 98% of the time"), and a surprise is flagged.

## Part K — SDIS's own input (rule 7)

*(user, 2026-10-03: "fine tune according to SDIS; SGT-I is irrelevant; make sure the core functions work")*

SDIS gets **its own recorder**, built for what SDIS needs. It does not depend on SGT-I or its corpus.
Each record holds:

* the page's **raw-view tree** with parents and screen boxes (R4; `uia_nodes.read_page_nodes(raw_view=True)`
  already reads it);
* **SGT's session id** (SGT core `_Session.session_id`, for Part D);
* the **browser** (from the window's process, for Part M);
* the page link and title, and the time (Part B).

**The cost, and the guards for it:**
* SGT keeps reading the control view (rule 8), so a changed page is read twice. That is about 2.2–2.5×
  SGT's read time, so the heavy GST page goes from 478 ms to about 1 s.
  Guards: the raw read runs **only after SGT's change gate says the page changed**, never on its own
  timer; it runs **after SGT's own read**, never before or inside it; it has a **time budget**, and a
  read over budget is dropped, not retried; and a setting turns it off.
  Reading once and rebuilding SGT's lines from the raw read is not allowed (it would change SGT capture).
* **Retention:** raw trees are big. Each staff PC keeps them only until they have synced to the admin
  PC, then deletes them (rule 7). There is a size cap: past it, the oldest are dropped.
* Sync to the admin PC rides on Sera Sync v3 (`docs/sera-sync-v3-blueprint.md`); this Part only
  defines what SDIS needs from it.

**Core functions must keep working on this input.** The regression runner (Part A) runs over this
recorder's output exactly as over the pre-dev captures.

## Part M — Every browser captured, compared per browser (problem 16)

*(user, 2026-10-03: capture both, as SGT-C already does, tuned for SDIS; SDIS sorts it out when
mining starts)*

* The recorder (Part K) captures the page in **every browser**, each record carrying its browser.
* At mining time, pages are compared **only with the same browser** (the trees differ), so memory is
  per (page, browser).
* The datapoints found are then **joined across browsers** at the datapoint level (same label and
  value type, or the same spec once picked), and their occurrences (Part J) add up.
* Cost: each browser has fewer clients per page, so a thin page shows a lower %. The results are not
  lost, they are only spread out. The dialog shows the clients per browser.

## Part N — Smart page link resolution (problem 17)

Statistics over the links of one host, per path position:

* a segment that **differs between clients** while every other segment stays the same is a **value**
  (a year, an ARN, an id). It is masked, and those links become one page;
* a segment that **differs within one client** (the same person visits `gstr1` and `gstr2b`) is a
  **different page**. It is never masked;
* until enough clients have been seen to tell the two apart, links stay as they are (nothing is merged
  on a guess).

It never uses SGT-I's digit masking (`atlas.url_hint`), which already merges `gstr1` with `gstr2b`.
Page link resolution comes before screens (Part G): Part G splits one link into pages, and Part N joins
links that are one page.

## Part O — "Find datapoints": mining on demand

*(user, 2026-10-03: no idle-time learning. It works like SGT-I's "Look for new datapoints", but faster,
with a loading dialog that locks the screen, and at most 10% CPU)*

* Mining runs **only when the user asks**: a **Find datapoints** button (in the Distill… dialog), on
  the admin PC only (rule 7).
* A **loading dialog** shows progress (captures done / total, the current page) and **locks the app**
  (modal) until mining ends. It locks the app, not Windows. It has a **Cancel** button: everything
  finished so far is already saved, so cancelling loses nothing.
* **At most 10% CPU, enforced by the OS:** mining runs in a **separate process** under a Windows Job
  Object with a hard CPU-rate cap of 10% (`JobObjectCpuRateControlInformation`,
  `JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP`, `CpuRate = 1000`; via
  `ctypes`, no new library). The installed app is frozen, so the child is **the app's own program
  started with a flag** (`<exe> --sdis-mine <args>`), handled at the very top of `main.py` before Qt or
  the database load. From source it is `python main.py --sdis-mine …`. Progress comes back as JSON lines
  on the child's stdout; Cancel ends the job. The app and SGT capture keep running normally; the dialog
  only reads progress.
* **Fast because it is incremental:** memory is **saved on disk** (admin PC) and each run processes
  only the captures that arrived since the last run, one client map at a time (merge → link
  resolution (N) → identity (D) → per browser (M) → memory (G, H) → relevance (J)).
  Measured on today's 11 pre-dev captures: the whole engine takes 1.8 s with Python start-up included.
  One Python thread is about 12.5% of an 8-core PC, so the cap barely slows it there. On a 4-core PC,
  10% is 40% of one core, about 2.5× slower. The first run over a big corpus is the slow one; the dialog
  shows it honestly.
* Opening the dialog never starts mining by itself; it shows the last saved results.

## Part P — Captures travel to the admin PC (rule 7)

Sera Sync v3 replicates database rows, not files, but its mutual-TLS transport
(`sync_transport.Session.send_file` / `recv_file`, port 49159) can carry files between office members,
and `sync_office.dispatch_session` routes an incoming session by its first frame type.

* **Admin side:** a new frame type `sdis_push` in `sync_office.dispatch_session` →
  `core/sdis/transfer.py: handle_push(session, app_dir)`: receive a manifest (file names, sizes,
  SHA-256), receive each file into `data_dir()/corpus/<device id>/`, check every hash, and reply
  `{"t": "sdis_ack", "files": [...]}` listing only the files that arrived whole.
* **Staff side:** after each sync round (or every 15 minutes while the app runs), push the finished
  capture files to the admin PC. **Delete each file only after the admin's ack names it.** The day's
  open file is pushed only after the day ends.
* **On the admin PC itself,** captures are written straight into the corpus folder (no push).
* Never logs file contents. A member that is not the admin never accepts `sdis_push`.

## Part Q — Registration on every PC

A datapoint the user picks becomes a field spec **on every PC** (D7):

* A new **synced table** in the office database, `sdis_fields`: name, portal, section, the spec as
  JSON, the **label** (user-edited, R6), status (`active` / `retired`), who and when. It replicates
  like other office tables, so every PC gets it.
* Each PC writes the active rows to `<Sera data>/sdis_fields.json` whenever the table changes.
  `sgt_specs.SpecStore` loads it as a **third spec file** after the built-in file and the local override.
  This is the only change to SGT capture in this Part: a datapoint the user registered is now captured.
  It must pass the SGT tests and show no change in `tools/sgt_replay.py diff` while the table is empty.
* The spec is drafted the same way the SGT lab drafts a mined one (`core/sgt_i/lab.py`
  `accept()` / `merge_into_override`, `core/sgt_i/miner.py` spec drafting); SDIS supplies the label
  and the values' type.
* **Renaming** a registered datapoint updates only its label (row + file); what is captured does not
  change. A later mining run never overwrites a label the user edited.
* Rejections (`variable_alignment` rejected, datapoints dismissed) live in a second synced table,
  `sdis_decisions`, so mining on the admin PC remembers them (D6).
* No tracker column is added automatically (D15), the same as the SGT lab today.
* `sdis_fields.json` holds only **how a field is captured** (its specs, named by their `sdis_mcl`
  field). **Which container a field is in** lives in the containers file (Part S.3), so moving a field
  between containers never touches its capture spec.

## Part R — Firefox support for SGT-C and SDIS

*(user, 2026-10-03: "we need the Firefox support for SGT-C and SDIS")*

**Measured 2026-10-03** (a fictional page, `tests/class_diff_align/client_A.html`, in a throwaway
Firefox profile, never a real tab):

| | Firefox |
| :--- | :--- |
| UI Automation | exposed natively (framework "Gecko"); the page is a Document under `tabbrowser-tabpanels` |
| SGT's text reader (`read_page_text`) | **works**: 82 lines, the right page text, 68 ms |
| SDIS's node tree (`uia_nodes`) | **works**: control view 174 nodes, raw view 225; ids, classes and screen boxes present; **no grid (column) info**, so tables use the screen-box fallback |
| Address bar | **not read**: it is a ComboBox (50003), automation id `urlbar-input`, name "Search with Google or enter address", ValuePattern = the full URL. Both readers look only at Edit (50004) |
| Window title | "<page> — Mozilla Firefox" (em dash) |
| Background tabs | **every tab's page is a Document** (plus a hidden "New Tab" page); the tab in front has `IsOffscreen = false`, every other `IsOffscreen = true` |

**Verified 2026-10-03** (scratch scripts, no repo change; fictional pages; throwaway profile with
Firefox's first-run screens turned off by a `user.js` in the profile - without it Firefox's
"Welcome / Terms of Use" screen covers the page):

| Check | Result |
| :--- | :--- |
| Planned address reader (Edit **or** ComboBox, same keyword test, ValuePattern) | returns `http://127.0.0.1:8765/client_A.html` from `urlbar-input` in 40 ms |
| SGT lines on client A (front tab only) vs Edge's texts for the same page | all 52 Edge texts present, **same order**; nothing missing |
| SGT today, A in front, B in a background tab | 227 lines, **clients A and B mixed** |
| Same window, reading only Documents with `IsOffscreen = false` | 52 lines, **client A only** (= Edge) |

**R.1 Address bar, title and background tabs** (W1-6). The address readers accept an Edit **or a
ComboBox** whose name or automation id says address / url / search, exactly as they treat an Edit
today, so Chrome and Edge are unchanged. The title cleanup strips " - ", " – " and " — " browser
suffixes. **Both page readers read only Documents that are on screen** (`IsOffscreen` false):
`vsdc_uia_text._find_document_elements` (SGT's lines) and `core/sgt_i/uia_nodes.read_page_nodes`
(node trees, SDIS's recorder). This is a fix of a kind, not of Firefox: a page nobody can see is never
read, in any browser. If every Document is offscreen (a minimised window), nothing is read, which is
correct. With the URL, SGT-C's
portal detection and specs work in Firefox, and SDIS gets page links (Part N) from Firefox captures.

**R.2 Parity tool and Firefox fixtures** (W1-7). `tools/browser_parity.py` serves the fictional pages
(the two `class_diff_align` clients plus a new fictional form page with a dropdown, radio buttons, a
checkbox and a password box holding a fictional password) on 127.0.0.1, opens each installed browser
(Chrome, Edge, Firefox) with a **throwaway profile**, reads the page the way SGT and SDIS do, closes
**only the processes it started**, deletes the profiles, and prints a parity table: URL read, SGT
lines (plus "Selected:" lines), password value never read, node counts, timings. It also saves the
fictional reads as test fixtures, `tests/class_diff_align/client_{A,B}_{browser}.json`.
Opening browser windows takes focus for about half a minute, so the worker asks first (D16).

**R.3 Line parity fixes** (W2-5). Every difference R.2 finds in SGT's lines for Firefox is fixed **as a
kind** in `core/vsdc/vsdc_uia_text.py` / `core/sgt_i/uia_nodes.py`, never per page. Chrome and Edge lines
must not change (`tools/sgt_replay.py diff` unchanged).

**R.4 SDIS on Firefox trees** (W3-4). The alignment and label tests run on the Chrome **and** Firefox
fixtures: 0 template pairing errors and every value labelled, in both browsers. Part M keeps the
browsers' memories apart; this proves the engine itself works on Firefox's tree.

Hands-on: a real GST and ITR session in Firefox captures the same fields as in Chrome (W1-6's check).

## Part S — Classes and containers (problems 26, 27)

*(user, 2026-10-04: "a container called dataset; we add whatever datapoints we want into it")*

Every registered datapoint lives in exactly **one container**. There are three kinds:

| Container | Holds | How many | What SGT does with it |
| :--- | :--- | :--- | :--- |
| **Profile builder** | profile datapoints (PAN, name, address…) | **one per registered portal** (D19, Part T) | adds the value to the session's **profile context** on that portal, exactly like today's `profile` specs (latches; identity, Part D, uses it too) |
| **Dataset** containers | whatever datapoints the user adds | as many as the user makes ("GSTR-3B submission", "Refund application"…) | builds one **instance** per (client, key) (D17) and gives it a **completion level** defined in the containers file (S.3); written to the tracker dump |
| **Others** | info datapoints (aggregate turnover…) | **one per registered portal** (Part T) | a standalone value per client and portal; the latest wins and the history is kept (D20); stored in the client's **SRPF container** as JSON (S.4) |

### S.1 Class suggestion: "what does this value stay the same with?"

*(user, 2026-10-04: "I like idea 1, apply it too")*

SDIS suggests a container for each datapoint from counts over its value history (Part B), per client
(Part D) and per period:

| The value stays the same for… | Suggested container |
| :--- | :--- |
| the same **client**, on every page, day and period it was seen | Profile builder |
| the same **client + period**, but changes between that client's periods | a Dataset container |
| neither | Others |

* **Period** of an observation: the period-shaped value (`labels.is_period`) shown **once** on that page,
  or the masked link segment (Part N) when it is period-shaped; none → the observation is not used for
  the dataset test.
* A class is suggested only when **≥ 90%** of the clients with enough evidence agree, where "enough" is
  two or more observations (profile test: on 2+ days or 2+ periods; dataset test: 2+ periods). Too
  little evidence → **no suggestion** (rule 6); the dialog says why.
* It is **only a suggestion**: it preselects the container in the Distill dialog. The user moves the
  datapoint anywhere. A move is stored (`sdis_decisions`, D6) and fed back like a pick (D8): that
  kind is never suggested into the rejected container again.
* Known limit: a value fixed per client per year (aggregate turnover) can pass the dataset test. The
  user moves it to Others once.

### S.2 Dataset containers and completion

*(user, 2026-10-04)*

* The user **creates** a dataset container, names it, and **adds datapoints** to it from the Distill
  list or from the field library (Part U). A container holds **fields** (Part U), so a field picked on
  several pages fills the same slot from any of them.
* **Completion levels are not in code.** *(user, 2026-10-04: "drop the dataset level I told above, it
  will be added to the JSON")* Which levels exist, when each is reached and how each maps onto the
  tracker's submit ladder are written in the containers file (S.3: `levels`, `level_map`). The code
  only evaluates them; it assumes no level names and no thresholds. A container with no levels shows
  only "k of n" (k = captured counted fields, n = counted fields) and is written to the tracker under
  SGT's existing rule: a keyed dataset is at least a Draft.
* **Levels only promote**, like the submit ladder: a value that disappears later never lowers the level.
  The fields that made the level are kept as its evidence, with "k of n".
* **Editing a container** (adding or removing a field, changing its levels) recomputes instances; an
  instance never moves down.
* Which instance a value belongs to is the **key** (D17).
* **SGT side** (rule 8): containers are defined in the containers file (S.3); SGT builds container
  instances the way it builds `current_dataset` today (a value shown once on a page belongs to the
  instance being worked on; a new key starts a new instance) and writes each instance to the tracker
  dump under the canonical key (`core/dataset_key.py`). With no containers registered, `tools/sgt_replay.py
  diff` shows no change. SGT's built-in GST/ITR datasets and their ladder are untouched.
* Pages listing **several instances** (a table of filings) are D22: the first version builds one
  instance per page and key, like `current_dataset`.
* **Key** (D17, taken): the container's name is the dataset's form, unless a field is marked `form`;
  one field is marked `period`; an instance = (client, form, period). Values seen before the period is
  known wait in the session, as SGT's current dataset does.
* **Tracker ladder:** each level names the ladder status it writes (`level_map`, S.3). The mapping the
  user agreed as D18 (Draft → Draft, In progress → Submitted (Not Verified), Complete → Submitted &
  Verified) is what goes into the file; it is not built in.

### S.3 The containers file: `sdis_containers.json`

*(user, 2026-10-04: "we will require a json to maintain containers and establish exceptions")*

One JSON document holds every container and every exception, in the same spirit as
`sgt_fields.json`: edit it to change behaviour, no code change; every exception carries examples; a
file that fails its own examples is **refused at load and the previous version keeps running**.

* **Where it lives:** the office copy is one row of a synced table (`sdis_config`, the whole document +
  a version number), so every PC gets the same file. Each PC writes it to
  `<Sera data>/sdis_containers.json` when the row changes, and SGT and SDIS load that file. The Distill
  dialog edits it (creating a container, adding a field, marking key fields); an admin can also
  **export it, edit it by hand and import it** (the import runs the same checks). A built-in copy
  shipped with the app (`core/sdis/sdis_containers.json`) is empty: no levels, no containers; the
  office copy is laid over it.
* **Shape** (field names are `sdis_mcl` names, Part U):

```json
{
  "version": 1,
  "levels": [{"name": "Draft",       "when": {"captured": 1}},
             {"name": "In progress", "when": {"captured": "51%"}},
             {"name": "Complete",    "when": {"captured": "all"}}],
  "level_map": {"Draft": "Draft", "In progress": "Submitted (Not Verified)",
                "Complete": "Submitted & Verified"},
  "profile": {"GST Portal": ["pan", "legal_name"], "Income Tax": ["pan", "name"]},
  "containers": [
    {"name": "GSTR-3B submission", "portal": "GST Portal",
     "form_field": null, "period_field": "tax_period",
     "fields": ["tax_period", "tax_paid", "arn", "filing_date"],
     "exceptions": {"optional": ["filing_date"],
                    "proves": {"arn": "Complete"},
                    "levels": null, "level_map": null},
     "examples": [{"captured": ["tax_period"], "level": "Draft"},
                  {"captured": ["tax_period", "filing_date"], "level": "Draft"},
                  {"captured": ["tax_period", "tax_paid"], "level": "In progress"},
                  {"captured": ["tax_period", "arn"], "level": "Complete"}]}
  ],
  "others": {"GST Portal": ["aggregate_turnover"]},
  "class_exceptions": {"aggregate_turnover": "info"},
  "portal_exceptions": {"extra_domains": {"Some Portal": ["work.example.gov.in"]},
                        "never_register": ["login.microsoftonline.com"]}
}
```

  The levels above only illustrate the syntax; the real ones are whatever the user writes.

* **Levels** (`levels`, file-wide; a container's own `levels` replaces them): an ordered list, lowest
  first. Each level has a `name` and a `when`; the instance is at the **highest** level whose `when`
  holds (and never lower than it has been). `when` may hold:
  * `captured`: a number of counted fields (`2`), a share of n (`"51%"`, rounded up), or `"all"`;
  * `fields`: a list of fields that must all be captured;
  both, when given, must hold.

* **The exceptions:**

  | Exception | Where | Does |
  | :--- | :--- | :--- |
  | `optional` | a container | the field is captured and shown, but **not counted in n** (a field many instances never have) |
  | `proves` | a container | capturing this field alone lifts the instance to **at least** that level (the same idea as SGT's `identifier_proves`: an ARN may prove the filing is done even if other fields were never seen). The level is the higher of the count and every `proves` hit; it still only promotes |
  | `levels` / `level_map` | file, or a container | a container's own levels, and its own mapping onto the tracker's submit ladder, replace the file's |
  | `form_field` | a container | the form comes from this field instead of the container's name (D17) |
  | `class_exceptions` | file | a field is always in this class, whatever S.1 suggests |
  | `portal_exceptions.extra_domains` | file | extra domains for a registered portal (a portal that moves to another domain after login, Part T) |
  | `portal_exceptions.never_register` | file | domains a login link may never register (shared sign-in sites); checked before Part T's confirmation |

* **Load checks** (refuse the whole file, keep the previous one, say why in the log and the dialog):
  every field name exists in `sdis_mcl`; `profile` and `others` name registered portals; `period_field` and `form_field` are fields of their container;
  `optional` and `proves` name fields of their container; `optional` never names the period field;
  level names are unique; a `when` uses only `captured` / `fields`, with a share between 1% and 100%
  and fields of the container; every `proves` level is a defined level; every `level_map` key is a
  defined level and every value a real status of the ladder; at least one field counts
  (n ≥ 1); a field is in at most one container per portal; every container's `examples` give the levels
  they claim; `never_register` and `extra_domains` entries pass Part T's domain checks.

### S.4 Where Others and Profile builder values are kept: the SRPF container

*(user, 2026-10-04: "keep it in the SRPF container, JSON payload")*

* The client's SRPF container (`client_raw_containers`, `sera_db/srpf.py`) already has two JSON columns
  that are created and always written as `{}`: **`raw_aggregates`** and **`portal_profiles`**. They
  become:
  * `raw_aggregates` = the **Others** values: `{portal: {field: {"value", "updated_at", "history":
    [{"value", "at"}]}}}`. The latest value wins and every change is kept in `history` (D20).
  * `portal_profiles` = the **Profile builder** values per portal (D19): `{portal: {field: value}}`,
    latching like SGT's profile (a `promote_longer` field may replace a value with a longer one).
* **They must travel inside `tracker_dump`.** SRPF containers are a local cache rebuilt from
  `tracker_dump` (`re_resolve_all_tracker_dumps()` deletes and re-inserts them; the table is `local`
  in `sync_schema.py`), so anything written only into the container is lost at the next rebuild and
  never reaches another PC. Therefore:
  * SGT puts the values in the payload JSON of the rows it writes for that client, under one key:
    `"sdis": {"portal": …, "portal_profile": {field: value}, "others": {field: value}, "at": …}`;
  * a session that captured Others or profile values but wrote no dataset row writes one **carrier
    row** per (client, portal): capture method `SGT_sdis_info`, no form, no period. Every tracker view,
    counter and status resolver ignores carrier rows (they are not datasets);
  * `_update_srpf_container` folds every row's `sdis` key into the two columns (latest `at` wins,
    history appended), so a rebuild gives the same result and every PC gets the values through
    `tracker_dump`'s sync.
* Shown in the client detail window and the Tracker dump's container view, per portal, labelled with
  the `sdis_mcl` labels.

## Part T — Portal registration from service settings (problem 28)

*(user, 2026-10-04: "take the link and dissect it to obtain the domain and use the gates as SGT-I")*

* **Source:** the login links the user already types in **service settings**
  (`ui/dialogs/service_manager_dialog.py` → `services.login_page_link`, `sera_db/mcl_services.py`;
  the `services` table already replicates to every PC, `sync_schema.py`).
* **Dissect:** `vsdc_scope.extract_host(link)` (parsed hostname, never a substring), then the
  **registered domain** = the host down to one label above its public suffix
  (`services.gst.gov.in` → `gst.gov.in`, `unifiedportal-mem.epfindia.gov.in` → `epfindia.gov.in`).
  Suffixes come from a short built-in list (`gov.in`, `nic.in`, `co.in`, `org.in`, `net.in`,
  `edu.in`, `ac.in`, `res.in`, `com`, `org`, `net`, `in`…); a host whose suffix is not on the list
  keeps its **full host** (the narrowest scope). A bare suffix is never accepted
  (`_FORBIDDEN_ENTRIES`), nor `localhost` or an IP outside tests.
* **The same gates:** the domain joins the scope gate exactly as the built-in two do (`is_in_scope_url`,
  `portal_for_url`: the domain or a subdomain of it, by parsed hostname). Income Tax and GST stay
  built in. The portal's name is the service's name.
* **What registration gives:** the portal is in scope for SGT-C and the SDIS recorder (D21), it gets
  its **Others** container (Part S), and it is the `portal` of every spec registered on it.
* **Exceptions** come from the containers file (S.3): `never_register` domains are refused, and
  `extra_domains` add a working domain to a registered portal.
* **Shown to the user** (D21, taken): saving a service shows the domain that will be watched and asks
  once; the confirmed domain is in scope for **both** SGT-C and the SDIS recorder.
  Deleting the service unregisters the portal: capture stops there; its registered datapoints stay,
  marked inactive.
* **Login domain ≠ working domain** (a portal that moves to another domain after login): the existing
  tripwire (`vsdc_router` `_tripwire_hosts`, a portal title on an out-of-scope host) already notices
  it; it offers "watch <domain> for <portal> too?" instead of only warning.

## Part U — `sdis_mcl`: the field library (problem 29)

*(user, 2026-10-04: "the fields that are approved or entered go into sdis_mcl so that we can reuse them
for multiple datapoints")*

* A synced office table **`sdis_mcl`**: one row per **field** (gid, name, label, value type, class =
  profile / dataset / info, portal or "all", status, who, when). Every field the user approves in the
  Distill dialog, or types in by hand, goes in.
* A registered datapoint **is a field** of the library: PAN picked on the GST page and PAN picked on
  the ITR page are two datapoints (two specs, two pages) of **one** field. Containers (Part S) list
  fields, not datapoints. Part M's join across browsers uses the field once it is picked.
* Naming a datapoint in the dialog **offers the existing fields first** (type-ahead); a new name
  creates a new field. Renaming a field renames it everywhere (R6); what is captured does not change.
* Its relation to Sera's existing **Master Column List** (`mcl_columns`, the client columns) is D23.

## Part L — The Distill… dialog

Tracker dump window → Tools ▾ → **Distill…**. Clear visibility, **not like the SGT lab** screen:

* one row per datapoint, shown **once**, with a **collapsible arrow** listing the page links it was
  found on (R5);
* a **suggested label the user can edit** (R6); every number a **percentage** (R7). *(user,
  2026-10-03: the label must be editable because SDIS may not get it right.)* The edited label is the
  one registered and shown everywhere. It **stays editable after registration**: renaming a registered
  datapoint changes only its name, never what is captured. An edited label is never overwritten by a
  later mining run;
* a `variable_alignment` list with keep / reject;
* **containers** (Part S): beside the datapoint list, one Profile builder per registered portal, the Dataset containers
  (create, rename, delete; each shows its datapoints and n) and one Others per registered portal
  (Part T). Each datapoint shows its **suggested container** (S.1); the user adds it to a container,
  which registers it (D7) as a field of `sdis_mcl` (Part U); the label box offers existing fields first;
* filters: relevance, sure, page, state, browser, suggested container;
* the **Find datapoints** button and the time of the last mining run (Part O).

Accepting a datapoint is D7.

**The design** *(user, 2026-10-04: "make a good dialog design, I trust you")* is
`docs/sdis/distill-dialog-mockup.html` (fictional data only), in the Tracker Dump window's dark theme
and green accent. W4-5 builds to it:

* **A · Main view:** a header with the last run (time, captures, clients, browsers) and **Find
  datapoints**; five stat cards (found, new since last run, please check, already captured, too little
  evidence); two tabs (Datapoints, Please check). The datapoint table has these columns: Label (edited
  in place) · Example · Type · Relevance (bar + %) · Sure % (amber below 60%) · Found on · Suggested
  (a Profile / Dataset / Others chip, or "? not sure") · **Add to ▾**. An expanded row lists its pages
  ("38 of 41 clients (93%)", browsers) and one plain "why" line for the suggestion. The Add to menu
  puts the suggested container first (★), then the portal's containers, "New dataset container…" and
  "Dismiss". The **containers side panel** on the right shows the portal tabs and, for each portal, its
  Profile builder, its Dataset containers (period key outlined, optional fields in italics, a `proves`
  badge, the levels in use) and its Others. Datapoints can be dragged in. At the bottom: Levels…,
  Export JSON, Import JSON, and the file version with its check state.
* **B · Container editor:** name and portal; a fields table (Period / Form radio buttons, Optional
  tick, Proves level); a levels table (the file's levels or the container's own; reached when
  captured: n / % / all; also needs fields; tracker status); a **live preview** strip (0 … n →
  level); the checks run live, and Save stays disabled with the reason in words while a check fails.
* **C · Please check:** one row per `variable_alignment` text: the text, where it is, what SDIS saw in
  one sentence, and **Keep as data** / **Template**.
* **D · Loading dialog:** application-modal; a progress bar, "128 of 312 captures", the time left, the
  current page link, "CPU capped at 10%, finished work is saved", Cancel.
* **E · States:** not mined yet; not the admin PC; containers file refused (the reason in words, the
  version still in use).

---

## 9. Privacy, all in one place

1. Monitoring phase: SDIS runs only on the admin PC, over its own corpus; other PCs delete after sync
   (rule 7). No hashing yet.
2. Real captures and their CSVs never leave this PC and are never committed (rule 10).
3. Logs, hand-off notes and the regression runner print counts, never values.
4. Before leaving the monitoring phase: salted hashes for faces (equal texts give equal hashes, so
   matching and fingerprints still work), values shown live only.

## 10. Decisions

**Taken (2026-10-03):** rules R1–R10; problems 1–6 solved as Parts C–H describe; identity by the
SGT session id + maths (Part D); rule 7 (no hashing, admin PC only); OCR later; plan first, then build
like Autofill tweaks; every browser captured and compared per browser (M); smart page link resolution
(N); "Find datapoints" on demand with a locking loading dialog and ≤ 10% CPU, no idle learning (O);
relevance by occurrences of non-fixed values, noise counted as fixed (J); no truth set, no absolute confidence; SDIS's own input, SGT-I
irrelevant (K); "Filed" is mitigated by `variable_alignment`, not solved; Firefox support for SGT-C and
SDIS (R). **2026-10-04:** containers (Profile builder and Others per portal, Dataset containers whose
completion levels are written in the containers file) and the class suggestion (S); portal registration from service-settings login
links through the same scope gate (T); the `sdis_mcl` field library (U); D3–D23 answered (below).

| # | Decision | Taken (user, 2026-10-04 unless marked) |
| :--- | :--- | :--- |
| D1 | Engine location | `core/sdis/` package, pre-dev scripts as module aliases (Part A) (2026-10-03) |
| D2 | Production input | SDIS's own recorder, SGT-I irrelevant (Part K) (2026-10-03) |
| D3 | Confirm threshold N, and the minimum clients for a full Sure % (Q2) | N = 2 to confirm; 5 for full Sure % |
| D4 | Capture whose identity stays undecided (Q10) | **no vote** |
| D5 | Captures on different days (Q11) | automatic, by SDIS's recorder |
| D6 | Where rejections, edited labels and container moves are stored (Q4) | office DB tables, synced (Part Q) |
| D7 | An accepted datapoint becomes an SGT-C spec (Q4); its label stays editable | yes, by the user's approval only |
| D8 | Picks, rejections and container moves feed back into the ranking (Q3) | yes: a picked kind ×1.5, a rejected kind left out |
| D9 | Truth set | none; SDIS removes the fluff and the user picks (2026-10-03) |
| D10 | Baseline | committed on `main` as `0f30543` (2026-10-03) |
| D11 | Thresholds: screen 0.5, retire 1%, identity 0.9 / 0.5 over 3 values, ambiguity margin 0.5 | these; changed only when the regression runner shows a reason |
| D12 | Idle time before learning | no idle learning; "Find datapoints" on demand (Part O) (2026-10-03) |
| D13 | Raw-read budget per changed page; staff-PC size cap | 1.5 s; 500 MB |
| D14 | How captures reach the admin PC | push over Sera Sync v3's transport, `sdis_push`, delete after ack (Part P) |
| D15 | Tracker column added automatically for a registered datapoint | no |
| D16 | A worker may open Chrome, Edge and Firefox (throwaway profiles, fictional local pages) | **yes** (no pop-up needed) |
| D17 | Dataset key | the container's name is the form (unless a field is marked `form`); one field is marked `period`; instance = (client, form, period) (Part S.2) |
| D18 | Completion → tracker ladder | **superseded the same day:** levels and their ladder mapping are written in the containers file (S.3), not built in. Agreed mapping, for the file: Draft → Draft; In progress → Submitted (Not Verified); Complete → Submitted & Verified; "k of n" kept; overridable per container (S.3) |
| D21 | Portal registration | the domain is confirmed once on save; it is in scope for SGT-C **and** SDIS; exceptions in the containers file (Part T, S.3) |
| — | Containers and exceptions | one JSON document, `sdis_containers.json`, synced, validated with examples like `sgt_fields.json` (Part S.3) |
| D19 | Profile builder | **one per registered portal** |
| D20 | An Others value that changes | the latest wins; the history is kept |
| — | Where Others and Profile builder values are kept | the client's SRPF container, JSON (`raw_aggregates`, `portal_profiles`), carried in `tracker_dump` payloads (S.4) |
| — | Ideas 2–4 (role tagging, session stickiness, ARN cross-check) | dropped; containers are filled by the user (Part S) |
| D22 | Pages that list several instances of a container | later; first version: one instance per page and key |
| D23 | `sdis_mcl` and Sera's Master Column List (`mcl_columns`) | separate tables; a field may be linked to a column later |
| — | Dataset completion levels | not in code: defined in the containers file (`levels`, `level_map`) |
| — | Deadline | moved to **2026-10-06 01:30 IST** (Tuesday) |

No decision is open. Workers record any new choice with `decide` and ask with `ask`.

## 11. Build order

Run by `tools/sdis.py` (the SGT-overhaul dispatcher via `use_project()`), in the worktree `../APP-sdis`
on branch `sdis`. One fresh session per WP, with retries, usage-limit sleeps, questions by pop-up,
CSV trackers, and nothing merged automatically. The step-by-step instructions for each WP are in
`sdis-plan.json` (generated by `make_plan.py`). Deadline **2026-10-06 01:30 IST** (Tuesday).

| Phase | WP | What | Model | Needs |
| :--- | :--- | :--- | :--- | :--- |
| 0 | W0-1 | Baseline: tests + the regression runner tools/sdis_regress.py | sonnet | - |
| 0 | W0-2 | Engine package core/sdis/ (pre-dev files become module aliases) | sonnet | W0-1 |
| 1 | W1-1 | Part B: value history with times | sonnet | W0-2 |
| 1 | W1-2 | Part G: screens (one link, several pages) by weighted matching | sonnet | W0-2 |
| 1 | W1-3 | Part D: identity by session id + data fingerprint | sonnet | W0-2 |
| 1 | W1-4 | Part N: smart page link resolution | sonnet | W0-2 |
| 1 | W1-5 | Part M: per-browser memory | sonnet | W0-2 |
| 1 | W1-6 | Part R.1: Firefox address bar, title, and never reading background tabs (SGT-C) | opus | W0-1 |
| 1 | W1-7 | Part R.2: browser parity tool + Firefox fixtures | opus | W1-6 |
| 1 | W1-8 | Part T: portal registration from service-settings login links | opus | W0-1 |
| 1 | W1-R | Phase 1 review | opus | W1-1, W1-2, W1-3, W1-4, W1-5, W1-6, W1-7, W1-8 |
| 2 | W2-1 | Part E: look-alikes scored, AMBIGUOUS | sonnet | W1-R |
| 2 | W2-2 | Part C: noise over time | sonnet | W1-R |
| 2 | W2-3 | Part F: the variable_alignment state | sonnet | W2-1 |
| 2 | W2-4 | Part H: memory upkeep (retire) | sonnet | W1-R |
| 2 | W2-5 | Part R.3: Firefox line parity fixes (SGT-C) | sonnet | W1-R |
| 2 | W2-R | Phase 2 review | opus | W2-1, W2-2, W2-3, W2-4, W2-5 |
| 3 | W3-1 | Part I: statuses + labels from memory | sonnet | W2-R |
| 3 | W3-2 | Part J: relevance by occurrences + slots | sonnet | W3-1 |
| 3 | W3-4 | Part R.4: SDIS on Firefox trees | sonnet | W3-1 |
| 3 | W3-5 | Part S.1: class suggestion (what a value stays the same with) | sonnet | W3-2 |
| 3 | W3-3 | Part O engine: memory on disk, incremental mining | sonnet | W3-2 |
| 4 | W4-1 | Part K: SDIS's own recorder (raw view, session id, browser) | opus | W3-3 |
| 4 | W4-2 | Part P: captures travel to the admin PC | opus | W4-1 |
| 4 | W4-3 | Part O: mining process capped at 10% CPU | opus | W3-3 |
| 4 | W4-4 | Part Q: registration on every PC (synced tables) | opus | W3-3 |
| 4 | W4-6 | Parts U + S.3: `sdis_mcl`, the containers file (sync + checks) | opus | W4-4, W1-8 |
| 4 | W4-7 | Part S.2 in SGT: container instances, completion, Others values | opus | W4-6 |
| 4 | W4-5 | Part L: the Distill dialog + loading dialog + containers | sonnet | W3-5, W4-3, W4-6 |
| 5 | W5-R | Final review and merge-readiness note | opus | W4-2, W4-5, W4-7, W3-4 |

Every WP: tests green, the regression runner before and after in its hand-off note (counts only),
rules 5–8 of the runner kept. Decisions are asked by the WP named in section 10.

## 12. Hand-off notes

(written by each WP)
