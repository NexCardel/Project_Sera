# Sera Distill (SDIS) — blueprint

**Status:** approved 2026-10-03 for building; **nothing in Parts B–Q is built yet.** Baseline: `main` at
`0f30543` (the pre-dev engine). Worktree `../APP-sdis`, branch `sdis`. Deadline **2026-10-04 23:00 IST**. It is written from the
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
* filters: relevance, sure, page, state, browser;
* the **Find datapoints** button and the time of the last mining run (Part O).

Accepting a datapoint is D7.

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
irrelevant (K); "Filed" is mitigated by `variable_alignment`, not solved.

**Open (default in bold):**

| # | Question | Default | Asked by |
| :--- | :--- | :--- | :--- |
| D1 | ~~Engine location~~ | **Taken:** `core/sdis/` package, pre-dev scripts as module aliases (Part A) | — |
| D2 | ~~Production input~~ | **Taken:** SDIS's own recorder, SGT-I irrelevant (Part K) | — |
| D3 | Confirm threshold N, and the minimum clients before a % is shown (Q2) | **N = 2 to confirm; 5 for full Sure %** | W3-2 |
| D4 | Capture whose identity stays undecided (Q10) | **no vote** / low-weight vote | W1-3 |
| D5 | Captures on different days (Q11) | **automatic, by SDIS's recorder** / by hand | W2-2 |
| D6 | Where rejections and edited labels are stored (Q4) | **office DB tables, synced** (Part Q) / a file on the admin PC | W4-4 |
| D7 | An accepted datapoint becomes an SGT-C spec after approval (Q4); its label (as the user edited it) stays editable afterwards | **yes, by the user's approval only** | W4-4 |
| D8 | Do the user's picks and rejections feed back into the ranking (Q3) | **yes: a picked kind ×1.5, a rejected kind left out** | W3-2 |
| D9 | ~~Truth set~~ | **Taken:** none. SDIS removes the fluff and the user picks; no absolute confidence | — |
| D10 | ~~Baseline~~ | **Taken:** committed on `main` as `0f30543` | — |
| D11 | Thresholds: screen 0.5, retire 1%, identity 0.9 / 0.5 over 3 values, ambiguity margin 0.5 | **these; changed only when the regression runner shows a reason** | W2-R |
| D12 | ~~Idle time before learning~~ | **Taken:** no idle learning; "Find datapoints" on demand, app locked by the loading dialog, ≤ 10% CPU (Part O) | — |
| D13 | Raw-read budget per changed page, and the staff-PC size cap for raw records | **1.5 s; 500 MB** | W4-1 |
| D14 | How captures reach the admin PC | **push over Sera Sync v3's transport, new `sdis_push` frame, delete after ack** (Part P) / shared folder | W4-2 |
| D15 | Add a tracker column automatically for a registered datapoint | **no** (as the SGT lab today) / yes | W4-4 |

## 11. Build order

Run by `tools/sdis.py` (the SGT-overhaul dispatcher via `use_project()`), in the worktree `../APP-sdis`
on branch `sdis`. One fresh session per WP, with retries, usage-limit sleeps, questions by pop-up,
CSV trackers, and nothing merged automatically. The step-by-step instructions for each WP are in
`sdis-plan.json` (generated by `make_plan.py`). Deadline **2026-10-04 23:00 IST**.

| Phase | WP | What | Model | Needs |
| :--- | :--- | :--- | :--- | :--- |
| 0 | W0-1 | Baseline: tests + the regression runner tools/sdis_regress.py | sonnet | - |
| 0 | W0-2 | Engine package core/sdis/ (pre-dev files become module aliases) | sonnet | W0-1 |
| 1 | W1-1 | Part B: value history with times | sonnet | W0-2 |
| 1 | W1-2 | Part G: screens (one link, several pages) by weighted matching | sonnet | W0-2 |
| 1 | W1-3 | Part D: identity by session id + data fingerprint | sonnet | W0-2 |
| 1 | W1-4 | Part N: smart page link resolution | sonnet | W0-2 |
| 1 | W1-5 | Part M: per-browser memory | sonnet | W0-2 |
| 1 | W1-R | Phase 1 review | opus | W1-1, W1-2, W1-3, W1-4, W1-5 |
| 2 | W2-1 | Part E: look-alikes scored, AMBIGUOUS | sonnet | W1-R |
| 2 | W2-2 | Part C: noise over time | sonnet | W1-R |
| 2 | W2-3 | Part F: the variable_alignment state | sonnet | W2-1 |
| 2 | W2-4 | Part H: memory upkeep (retire) | sonnet | W1-R |
| 2 | W2-R | Phase 2 review | opus | W2-1, W2-2, W2-3, W2-4 |
| 3 | W3-1 | Part I: statuses + labels from memory | sonnet | W2-R |
| 3 | W3-2 | Part J: relevance by occurrences + slots | sonnet | W3-1 |
| 3 | W3-3 | Part O engine: memory on disk, incremental mining | sonnet | W3-2 |
| 4 | W4-1 | Part K: SDIS's own recorder (raw view, session id, browser) | opus | W3-3 |
| 4 | W4-2 | Part P: captures travel to the admin PC | opus | W4-1 |
| 4 | W4-3 | Part O: mining process capped at 10% CPU | opus | W3-3 |
| 4 | W4-4 | Part Q: registration on every PC (synced tables) | opus | W3-3 |
| 4 | W4-5 | Part L: the Distill dialog + loading dialog | sonnet | W3-2, W4-3, W4-4 |
| 5 | W5-R | Final review and merge-readiness note | opus | W4-2, W4-5 |

Every WP: tests green, the regression runner before and after in its hand-off note (counts only),
rules 5–8 of the runner kept. Decisions are asked by the WP named in section 10.

## 12. Hand-off notes

(written by each WP)
