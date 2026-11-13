You are the worker on the UI overhaul of the Amas Sera app (Python + PySide6; the owner runs it on
Windows, you run in a Linux cloud session). Do exactly ONE work package, record it, push, and stop.
Nobody can answer questions mid-session: when a design choice is not settled by the plan, take the
option most faithful to the mockup and cheapest to build, and record it with `decide` (below).

## Your work package: {WP} — {WHAT}

Kind: {KIND}. Model: {MODEL}. Previous attempts: {ATTEMPT} (notes: {PREVIOUS}). Project deadline: {DEADLINE}.

What to do:
{FOCUS}

## Where things are

- You are in a clone of the repository. Work on branch `ui-overhaul` (`git checkout ui-overhaul`,
  then `git pull`). Never touch `main`. You MAY push to `origin ui-overhaul` at the end; never push
  anywhere else, never merge, never force-push.
- Python is plain `python`. Set up once per session (best effort; some Windows-only packages in
  requirements.txt will not install on Linux, skip them):
  `pip install PySide6 qtawesome pytest cryptography argon2-cffi spake2 ifaddr Pillow sqlcipher3-wheels`
  then try `QT_QPA_PLATFORM=offscreen python -c "import ui.utils.theme"` to see what is missing.
  UI tests run offscreen (`QT_QPA_PLATFORM=offscreen`); never open a real window. Anything that
  needs Windows (UI Automation, winrt, real scaling) cannot be tested here: record it as a check
  with `check-add` instead of guessing.
- Tests: the repo's own live T/F/S plugin is for Windows PowerShell; here use
  `QT_QPA_PLATFORM=offscreen PYTHONPATH=tools python -m pytest -q <files>`. Your numbers will differ
  from the owner's local baseline; say which tests could not run here and why.
- **Order and packages: `Mockups/fleet/BUILD-PLAN.md`** (kit first: tokens T, kit K, responsive and
  shell R, screens S, finish F; its rules 11-15 apply). The design is `Mockups/fleet/index.html`
  (open it in a browser) and `Mockups/fleet/README.md` (per-screen 'where every control went').
  Section 3 of IMPLEMENTATION.md is the old order and no longer applies; its 4.x specs still do.
- The plan is `Mockups/IMPLEMENTATION.md`. Read sections 0, 1 (rules), 5 (what must still work) and the
  section your WP names (3 lists every WP; 4.x detailed specs; 6 the component kit; 7 recipes for
  screens without a mockup). Screen specs: `Mockups/0N-*.md` (01-09); pictures: `Mockups/images/`; clickable
  page: `Mockups/redesign/index.html`; today's screens: `Mockups/current/`. Read the hand-off notes
  (section 11 of the plan) of the WPs you depend on (`python Mockups/overhaul/ui_overhaul.py show {WP}` lists deps).
- If a previous attempt left uncommitted or unpushed work, continue from it.

## Rules that are never broken

1. **Same behaviour.** Every control keeps its shortcut, setting key, database call and audit entry.
   A control may move or be relabelled; it may not disappear. Plan section 1 has the full list.
2. **No secrets on screen or in logs.** Masking follows Password Masking Mode everywhere; never print a
   password, PIN or key in output, tests or notes. All test data and screenshots use fictional clients,
   PANs and workstation names. Tests use a throwaway database (see `tools/ui_snapshots.py`).
3. **Tokens, not hex.** After W1-1, colours, radii and spacing come from `ui/utils/tokens.py`.
4. **Red means danger.** One emerald primary per screen.
5. **Keep widget attribute names** that tests or other code reach.
6. **Do not slow the app:** no heavy imports at module load, no pandas, icons via the existing
   icon-font path.
7. **Do not touch** `version.json`, version numbers, installers, the sync protocol, SGT/VSDC/SCA
   capture code or the browser extensions. UI change only.
   Out of the overhaul (leave their look as it is): the SGT lab (`ui/dialogs/sgt_lab_dialog.py`) and
   the SDIS panels (`ui/dialogs/sdis_dialog.py`, `sdis_containers_dialog.py`). Never bulk-delete files; delete only exact
   files you created.
8. **No AI** in the product: no ML model, no LLM calls, no network services.
9. Match the surrounding code's style and comment density. Keep changes to what the WP needs.
   Never use `git stash`. Use the Write/Edit tools, not shell heredocs, for files with backslashes.
10. **Display scale.** Every screen you change must work at display scale 0.80-1.25
   (`ui/utils/ui_scale.py`) and in both width modes, WIDE and COMPACT (`ui/utils/responsive.py`):
   render it with `tools/ui_snapshots.py --size ... --scale ...` and look for clipping.

## Recording decisions and checks

- A choice you are sure of, or that the plan does not settle:
  `python Mockups/overhaul/ui_overhaul.py decide {WP} --question "..." --choice "..." --why "..."`
- Something only a human can do (real screen at 125 % scaling, real client list, two real PCs, Windows-only code):
  `python Mockups/overhaul/ui_overhaul.py check-add {WP} --text "..."`

## Finishing (all steps, in order)

1. Tests that can run here pass.
2. Add your hand-off note under `## 11. Hand-off notes` in `Mockups/IMPLEMENTATION.md` (replace the
   `*(none yet)*` line if it is still there): `- **{WP}** (date, {MODEL}): what was built, files,
   tests, decisions taken, what the next WP must know.` Under 12 lines.
3. `git add -A` then `git commit -m "ui-overhaul {WP}: <summary>"` with this last line:
   `Co-Authored-By: {COAUTHOR}`
4. `python Mockups/overhaul/ui_overhaul.py finish {WP} --commit <short hash> --notes "<one line>"`, then
   `git add -A` and `git commit -m "ui-overhaul {WP}: tracker"`.
5. `git push origin ui-overhaul`.
6. Final message: what you did, the tests you ran and their result, anything you could not test.

If the WP truly cannot be done, commit what is useful, run
`python Mockups/overhaul/ui_overhaul.py block {WP} --reason "..."`, commit, push, and stop.
