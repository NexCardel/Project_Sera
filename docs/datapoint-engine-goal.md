# Sera Distill (SDIS) — datapoint engine

Status: **pre-development**, last updated 2026-10-03. The pre-dev tools live in `tools/pre_dev/class_diff/`. The user is thinking it over; nothing beyond those tools is built.

## 1. The goal (the user's words, condensed)

Build an **engine that reliably extracts the useful datapoints on its own**, so they no longer have to be written and wired by hand (today they are specs in `core/sgt/sgt_fields.json`).

1. **Differential analysis:** compare the same page across different clients. Fixed text is template, changing text is data, and labels come from the page's structure.
2. **Relevance ranking:** the engine orders the datapoints by relevance, decided by **maths and statistics**, "something like machine learning but much simpler". There is **no AI and no model** (the §14 rule in `docs/sgt-blueprint.md`).
3. **Where the user sees it:** a **dialog opened from the Tools menu of the Tracker dump window** (`ui/windows/tracker_dump_window.py`, the "Tools ▾" button, entry "Distill…"). Visibility must be good: **not like the SGT lab screen**.

**Name:** Sera Distill, short form **SDIS** (the user chose it on 2026-10-03 from Assay / Sieve / Distill / Lens).

## 2. User requests and decisions (in the order they were made)

**Agreed rules (do not re-litigate):**

| # | Decision |
|---|---|
| R1 | **"Fix the kind, not the case."** Every rule must be global: based on element structure, value types and shared-vs-different, never on a portal name, class name or piece of text. |
| R2 | **Bare numbers are data.** A 0 two clients share is semi-variable, never template. Only text, labels and sentences can be "fixed". |
| R3 | **Problems maths and statistics can fix are handled in the maths phase**, not patched by hand. |
| R4 | **Production reads the raw view** (measured affordable, see §4), plus **alignment**. |
| R5 | **Dialog:** a datapoint found on several pages is shown **once**, with a **collapsible dropdown arrow** listing the **page links** where it was found. |
| R6 | **Dialog:** SDIS **suggests** the field label and the **user can edit it**. |
| R7 | **Dialog:** every number shown to the user is a **percentage**. |
| R8 | **SDIS counts per page and per client.** Snapshots of the same page must never count as extra comparisons or extra votes. |

**Requests completed in pre-dev:**
- Run and compare captures by hand: the commands are in §8.
- Make numbers semi-variable.
- Use the same-client guard.
- Make the analysis match what production reads.
- Add raw-view timing.
- Build alignment and test it.
- Move the test into `tests/` and commit.

## 3. What is built (pre-dev only, not wired into the app)

| Piece | What it does |
|---|---|
| `key_probe.py` | Reads the page in both views. Default: a 30 s capture, with snapshots merged per page link. Also `--once` (single read) and `--timing` (SGT's own reader, control vs raw view, nothing written). |
| `keys.py` | A key per element: type, stable id and classes per step, plus `[n]` sibling counters. Generated (digit) and state classes are dropped. `VIEW` = raw or sgt (control view, re-parented). |
| `link_map.py` | One map per (capture session, page link): the snapshots merged **by key**. |
| `align.py` | Pairs two pages like a text diff. Anchors are the same shape (the key without counters) **and** the same text, in page order; between anchors, elements pair by shape. |
| `compare.py` | Latest client vs the most recent **different** client, per page link. Labels and checks go to a CSV. Defaults: raw view, alignment on. |
| `snapshot_diff.py` | One client, before vs after: NEW STATE / RETURNED / PART CLOSED / SAME SCREEN. |
| `tests/test_class_diff_align.py` | Two fictional clients. B has an extra notice line and 5 list rows against A's 3. 8 tests. |

**Label rules in `compare.py`:**
- A table cell gets "row / column", with the column matched by the browser's grid column or the screen box, **never by counting cells**.
- Never a label: a link, a button, an image (its name is alt text or the browser's own words), or a **composite** (text that is only its children joined).
- A label must contain a real letter.
- A shared text of words with digits can be a label ("9B - Credit / Debit Notes").
- An unpaired text that repeats a template text of the same shape is template too (labels in extra list rows).

## 4. Measurements

- **Raw view cost** (`key_probe.py --timing`, GST in Chrome):

  | Page | Control view | Raw view |
  |---|---|---|
  | Light dashboard | 57 ms | 75 ms |
  | Services dashboard | 180 ms | 242–276 ms |
  | Heavy page (2462 elements) | 478 ms | 560 ms |

  - Raw costs 1.2–1.5× the control view's time, which is **affordable**.
  - The heavy page is already 478 ms in the control view, so **the change gate must stay**.
  - Fictional pages in Edge gave the same ratio (1.1–1.3×).
- **What the control view loses:** almost no text. Every raw-only text is either a duplicate or a piece of a joined text that SGT sees whole. It does lose **the split** ("…Invoices 5") and **the nesting**.
- **Key stability on the real GST captures (raw view, two clients):**
  - services dashboard: 165 of 165 keys the same;
  - GSTR-1: 116 of 120;
  - returns dashboard: 96 of 105.
- **Alignment, fictional test:**

  | Setup | Template texts paired wrongly | B's values labelled correctly |
  |---|---|---|
  | Raw view + alignment | 0 | 20 of 20 |
  | SGT view + alignment | 0 | 20 of 20 |
  | SGT view, no alignment | 31 | 6 of 20 |

- **Alignment on the real GST captures, raw view:** only 2 pairings changed, both for the better (a rotating notice matched to the same notice).
- **Snapshot merge by key, returns dashboard:**
  - repeated elements grew from 7 in one snapshot to 18 in the merged map (client 1), and from 8 to 15 (client 2);
  - the whole footer was stored twice after the page re-rendered.

## 5. Problems encountered

| # | Problem | Cause | Status |
|---|---|---|---|
| P1 | Every value appeared twice in the CSV | The raw view has the cell **and** the text inside it | Not a real problem; filter `sgt_sees = yes` |
| P2 | Glued texts ("<name> <GSTIN>", "…Invoices 5", "79,99,235.00 View/Update") | A parent's name is its children's texts joined | **Fixed:** flagged "composite", never a label |
| P3 | Zeros marked fixed and used as labels | `number` was a fixable type | **Fixed** (R2) |
| P4 | Wrong labels: "View/Update", Chrome's "To get missing image descriptions…", ledger values with no column | Links and images accepted as labels; no table logic | **Fixed:** link/button/image never a label; tables get "row / column" |
| P5 | Furniture counted as data (rotating notices, "Site Last Updated on…") | It changes by day or visit, not by client | **Open:** maths phase (same client over time) |
| P6 | The same client captured twice would make all its data look like template | One capture was assumed to be one client | **Fixed:** client from SGT-C's PAN/GSTIN specs. A capture with no PAN/GSTIN only gets a warning |
| P7 | List rows matched by position | Counters | **Mostly fixed by alignment.** Data types already protected periods, dates and amounts. Status words ("Filed") are the remaining risk with few clients (maths phase) |
| P8 | The probe read more than SGT does | Probe = raw view, SGT = control view | **Decided:** production reads the raw view (R4) |
| P9 | The control view drops an empty table corner cell, shifting columns by one | Counting cells | **Fixed:** columns by grid column or screen box |
| P10 | The control view flattens the page, so a key becomes "nth text on the page" and one extra line shifts everything below | Chrome removes containers in the control view | **Fixed** by raw view + alignment (31 errors to 0 in the fictional test) |
| P11 | An icon glyph plus a digit ("<bell> 0") used as a label | Counted as words with digits | **Fixed:** a label must contain a real letter |
| P12 | GSTR-1 tile counts took the previous tile's title | Titles with digits could not be labels | **Fixed:** shared words-with-digits allowed as labels |
| P13 | Extra list rows lost their labels | Their "Period"/"ARN" had no partner | **Fixed:** repeated template rule |
| P14 | A block in a different **order** on the two pages stays unpaired | Alignment works in page order only | **Open:** move detection on unique anchors (proposed) |
| P15 | Two look-alike values with no anchor between them, one missing: **the first is paired silently** | The information is not on the page | **Open:** mark as **ambiguous** (low %), then screen position, value type, and many clients |
| P16 | **Snapshot merge double-counts elements** (the footer stored twice) | `link_map` merges snapshots by key, counters included | **Open, blocks R8:** merge snapshots with alignment |
| P17 | Maps are per capture session, not per client | Grouping by session | **Open, blocks R8:** group by PAN/GSTIN; one vote per (client, page) |
| P18 | A capture that never shows a PAN/GSTIN cannot be told apart from the same client | No identity on the pages visited | **Open:** see Q10 |
| P19 | The heavy page takes 478 ms even in the control view | A big page | Known; the change gate must stay |
| P20 | Another session in the same checkout swept SDIS files into its commit (`3015b26`) | Two sessions committing in `../APP` | Process: work in a separate worktree, or commit only with an explicit file list |
| P21 | The fictional tests were designed by Claude | — | **Open:** a real-portal test with different list lengths is the real proof |

**Keys are not useless.** The **shape** (types, ids, classes) is reliable and is half of the alignment. The **counters `[n]`** are the fragile part: they are right about 90–100% of the time, but the rest fails silently. The plan: shape for recognition, full key as a fast path, alignment as the referee.

## 6. How maths and statistics would make SDIS precise (proposed, not built)

All of it is counting turned into percentages, on salted hashes (no client values stored).

1. **Template or data, by voting across clients.** For example, "GSTIN :" is the same for 10 of 10 clients (template), and "Filed" for 7 of 10 (data that often repeats).
2. **Noise vs data, over time.** Something that changes on **every visit for the same client** is noise (notices, dates). Something stable for one client but different between clients is real client data.
3. **Pairing confidence.** Points for the same nearby label, the same screen column or spot, and the same value type. A clear winner is paired; a near tie is **ambiguous** with a low %.
4. **Template slots from the most complete pages.** A lone value is fitted to the slot it matches best by type and position.
5. **Every slot learns what it usually holds** ("a date 98% of the time"). A surprise is flagged.
6. **Relevance:** differs between clients, stable per client, labelled, typed, found on several pages, not noise. These combine into a %.
7. **Percentages shown:** Relevance, Sure, Found on.
8. **Honest limit:** the numbers need volume, roughly **5 or more different clients per page**. Below that, SDIS must show low percentages, not fake confidence.

## 7. Open questions (for the user)

| # | Question | Notes |
|---|---|---|
| Q1 | **Build order.** Recommended: P16 + P17 (count per page and per client, with a test enforcing R8), then P15 (ambiguity), P14 (moves), then voting, slots and relevance. | P16/P17 block R8 |
| Q2 | **Minimum clients** before SDIS shows a datapoint or a percentage, and what it shows below that | Proposed: about 5 |
| Q3 | **Which relevance signals matter most** to you, and should your approvals and label edits feed back into the ranking? | |
| Q4 | **What happens to a datapoint you accept?** Does it graduate into an SGT-C spec after your approval (§14), and where are edited labels stored? | |
| Q5 | **What is a "page" in the dialog?** A page link can show several screens (form, popup, success message). One entry per link, or per screen? | Links to the snapshot-on-change work |
| Q6 | **Privacy vs visibility.** Production must not store client values (§14), but the dialog shows datapoints. Does it show an example value, a masked shape (`AA99AAAA9999A9Z9`), or the current client's live value only? | Important |
| Q7 | **Scope:** ITR, GST, TRACES (OCR, no tree); Chrome, Edge, Firefox | Only GST in Chrome has been measured |
| Q8 | **Truth set:** who marks the right datapoints and labels, and on how many pages? | Needed to score every change |
| Q9 | **Where SDIS runs:** live inside SGT-I's thread (budget and watchdog), or offline over the recorded corpus when the dialog opens? | |
| Q10 | **Captures with no PAN/GSTIN:** ignore them, or count them with a lower weight? | P18 |
| Q11 | **Furniture over days** needs captures on different days. Is that recorded automatically by the corpus recorder, or captured by hand? | P5 |

## 8. Where things are and how to run them

- Tools: `tools/pre_dev/class_diff/`. Outputs go to its git-ignored `output/` folder and contain **real client data**; keep them on this PC.
- Tests: `tests/test_class_diff_align.py`, with fixtures and their generator in `tests/class_diff_align/`.
- Related design: `docs/sgt-blueprint.md` §14 (SGT-C / SGT-I, atlas, miner, graduation by user approval).
- Commands, from the `APP` folder:

  ```
  venv\Scripts\python.exe tools\pre_dev\class_diff\key_probe.py                        # 30 s capture of one client
  venv\Scripts\python.exe tools\pre_dev\class_diff\compare.py                          # latest client vs previous different client
  venv\Scripts\python.exe tools\pre_dev\class_diff\compare.py --view sgt --align off   # the old behaviour, for comparison
  venv\Scripts\python.exe tools\pre_dev\class_diff\key_probe.py --timing               # raw vs control view cost
  ```
