# Datapoint engine — end goal (parked 2026-10-03)

The user's end goal for the class_diff probing and tests (`tools/pre_dev/class_diff/`). Nothing below is built yet except the pre-dev tools; this file holds the goal so work can resume later.

## The goal (user's words, condensed)

Build an **engine that reliably extracts the useful datapoints on its own**, so they no longer have to be written and wired by hand (today: specs in `core/sgt/sgt_fields.json`).

1. **Differential analysis** (the stage tested so far): compare the same page across different clients. Fixed text is template, variable text is data, and labels come from the page's structure.
2. **Relevance ranking.** The engine orders the extracted datapoints by relevance, decided by **maths and statistics**: "something like machine learning but much simpler". There is **no AI and no model** (§14 rule in `docs/sgt-blueprint.md`).
3. **Where the user sees it:** a **dialog box opened from the Tools menu of the Tracker dump window** (`ui/windows/tracker_dump_window.py`, the "Tools ▾" button). Visibility must be good: clear and readable, **not like the SGT lab screen**.

**Name:** not chosen yet; the user is open to suggestions (see the bottom of this file).

## Rules already agreed (do not re-litigate)

- **Fix the kind, not the case.** Every rule must be global: based on element structure, types and shared-vs-different, never on a portal name, class name or text string.
- **Never compare a client with itself.** The client is identified by the PAN/GSTIN that SGT-C's own specs find (done in `compare.py`).
- **Analyse what production can see.** `compare.py` defaults to SGT's control view, with `--view raw` for study.
- **Bare numbers are data:** a shared 0 is semi-variable, never template.
- **Labels:**
  - never a link, button, image, or a composite (text that is only its children joined);
  - a table cell's label is "row / column", with the column found by the browser's grid column or the screen box, never by counting cells;
  - a label must contain a real letter.
- **Problems that maths and stats can fix are deferred to the production maths phase.** These include list rows matched by position, values two clients happen to share, and furniture such as rotating notices and "Site last updated".

## Decided direction for key stability

- **Raw view + sequence alignment.**
  - Chrome's control view flattens the page, so "nth text on the page" keys shift when one client has an extra line.
  - The raw view keeps the nesting, so shifts stay local.
  - Alignment matches pages by their shared texts. It also fixes list rows and helps when the portal renames classes.
- **Raw view cost, measured 2026-10-02/03 with `key_probe.py --timing` (SGT's own reader, GST in Chrome):**
  - light dashboard: 57 → 75 ms;
  - services dashboard: 180 → 242–276 ms;
  - heavy page (2462 elements): 478 → 560 ms.
  - So raw costs 1.2–1.5× the control view's time. The verdict was "affordable".
  - The heavy page is already 478 ms in the control view, so the change gate must stay.

## Still open before production

1. **Build the alignment** (maths phase), then check that clients with different list lengths give zero "types differ" warnings.
2. **A truth set:** a few pages where the user marks the right datapoints and labels, so every change gets a precision/recall score.
3. **Production privacy:** compare salted hashes, never stored values (§14). The CSVs hold real client data and are a local test only.
4. **Coverage:** ITR, Edge and Firefox are untested; OCR/canvas pages (new TRACES) need line-based alignment.
5. **How relevance is scored:** the signals and the maths (still to design).
6. **Dialog design** for the Tracker dump Tools menu.
7. **The name.**

## Name suggestions (Claude, 2026-10-03)

- **Assay**: the test that finds what a sample is made of. It fits "compare pages, find the data".
- **Sieve**: shakes out template and keeps the values.
- **Distill**: draws the essence out of many pages.
- **Lens**: shows what matters on a page.

Recommendation: **Assay** (e.g. "Sera Assay", Tools → "Assay…").

## Where things are

- Tools: `tools/pre_dev/class_diff/`, containing `key_probe.py` (capture and `--timing`), `link_map.py`, `compare.py`, `snapshot_diff.py` and `keys.py`. Outputs go to its git-ignored `output/` folder and contain client data.
- Related design: `docs/sgt-blueprint.md` §14 (SGT-C / SGT-I, atlas, miner, graduation by user approval).
