You are the designer for the UI overhaul of the Amas Sera app, a Windows desktop app (Python +
PySide6) that a small accounting office uses all day to look up clients, open government portals
and track filings. You run in a cloud session. Do exactly ONE work package, record it, push, and stop.
Nobody can answer questions mid-session: when something is unclear, choose, and write the choice
down in your README (below).

## Your work package: {WP} — {WHAT}

Model: {MODEL}. Previous attempts: {ATTEMPT} (notes: {PREVIOUS}).

**The task:** redesign the whole app ("the fleet": every screen and dialog) from screenshots of
today's screens, and deliver it as **one new clickable HTML mockup**. This is design only. You
change no app code; the build is done later by other work packages from what you draw.

## Setup

- Work on branch `ui-overhaul` (`git checkout ui-overhaul`, then `git pull`). Never touch `main`,
  never merge, never force-push. You MAY push to `origin ui-overhaul` at the end.
- Everything you create goes in **`Mockups/fleet/`**. The only other files you may change are the
  tracker files that `Mockups/overhaul/ui_overhaul.py` writes when you run it.
- Python is plain `python`; nothing needs installing for this WP.

## What to read (in this order)

1. **Today's screens**: every PNG in `Mockups/current/` (31 files, 1875×1055). Look at each one.
   **Skip `dlg_sgt_lab.png`.** The SGT lab and the SDIS panels are out of the overhaul. Leave
   them out of the mockup and list them in the README as "not redesigned".
2. **The earlier redesign of six screens**: `Mockups/redesign/index.html` and its specs
   `Mockups/01-06*.md`, plus the pictures in `Mockups/images/`. It shows the direction the owner
   liked: dark surfaces, one emerald primary, white data grid, chips, cards, labelled actions.
   You may improve on it, but say where and why you departed from it.
3. **The rules** in `Mockups/IMPLEMENTATION.md` section 1, and the "Where every current control
   goes" tables in the 01-06 specs. Those tables show the standard: **no control disappears.**
4. When a screenshot leaves a control's purpose unclear, grep the code (`ui/windows/`,
   `ui/dialogs/`, `ui/shell/`) for its label. Read only; don't edit.

## Screens to cover

All of `Mockups/current/` except the SGT lab, grouped as:
- **Shell and daily screens:** sidebar, All Clients / search, Client detail panel, Manage Clients
  (admin), Tracker Dump.
- **Settings hub:** every `settings_*.png` page (general, actions, SCC, services, tracker, export,
  backup, purge, QC, columns/MCL, main and admin visibility).
- **Dialogs:** audit log, Sera Sync, MCL manager and column edit, service manager and edit, CSV
  import, change master password, office recovery, rejoin office, first run, force update, startup
  loading, legacy settings.
- **Display scale** (it has no screenshot): the per-PC scale picker (Automatic, 80, 90, 100, 110,
  125 %; applies at next start, with a "Restart now" button). It is reached from Settings → General
  and from a sidebar "Display" entry. Also show how the daily screens look in **COMPACT** width
  mode, which the app switches to when a page is narrower than 1080 px (a small office PC is about
  1280 px wide at 125 %). See `docs/ui-scale-adaptive-layout-plan.md` for what COMPACT does in
  Tracker Dump today.

## Design rules

- **Keep every control.** Each control in a screenshot appears in your design: moved, regrouped or
  relabelled is fine, dropped is not. A control may go into a menu if it is rarely used.
- **Dark theme only.** Keep Sera's emerald (`#2E9B5F`) as the one primary action colour per
  screen. Red only for destructive actions. Data grids stay highly readable (the white grid is the
  owner's preference; argue for a change if you have one).
- **Small laptop first:** every screen works at 1366×768. No sideways scroll except inside a data
  grid.
- **Fictional data only:** made-up client names, PANs like `ABCDE1234F`, made-up workstation names.
  Never show a real-looking password. Secrets appear masked (`••••••`) or as First-N / Last-N
  masks.
- **Build in a way the app can follow:** use one token block (`:root` CSS variables for colours,
  radii 6/8/12, spacing, font sizes, control height) and a small set of repeated components (chip,
  switch, card, pill, tag, labelled action, setting row, status strip, selection bar, unsaved bar,
  empty state; see `Mockups/IMPLEMENTATION.md` section 6). Every screen is made only from those
  tokens and components, because that is how it will be built in Qt.
- No new product features. Where you think one is needed, list it in the README under "Ideas"
  instead of drawing it.
- The Sera logo is `assets/logo/sera_mark.svg`. Embed it as is; don't redraw it.

## Deliverables (all in `Mockups/fleet/`)

1. **`index.html`**: one self-contained file. Inline CSS and JS only, no CDN, no web fonts (use
   `'Segoe UI', system-ui, sans-serif`). It must open offline from disk.
   - A navigation rail or list of every screen, grouped as above, and a 1366×768 app frame where the
     chosen screen renders.
   - A **WIDE / COMPACT** toggle (frame width 1366 vs about 1024) and a **scale** toggle (80 / 100 /
     125 %) so the owner can see the daily screens adapt.
   - Interactive enough to judge the design: tabs switch, chips toggle, a row can be selected so the
     selection bar shows, the Settings unsaved bar appears after a change, dialogs open over the
     screen they come from.
   - A "Before" button per screen that shows the matching `Mockups/current/` PNG (relative path
     `../current/<file>.png`).
2. **`README.md`**:
   - the token table and the component list;
   - for each screen, one paragraph on what changed and why, plus a short "Where every control
     went" list. You may stay brief for simple dialogs, but nothing may be missing;
   - where you departed from `Mockups/redesign/index.html`, and why;
   - the out-of-scope screens (SGT lab, SDIS);
   - Ideas, and open questions for the owner.

Keep `index.html` under about 400 KB. Don't copy the PNGs into `Mockups/fleet/`; link to them.

## Check before finishing

- Open `index.html` headless if you can (e.g. `python -m http.server` plus any available browser
  tool), or at least check that it parses and every nav entry has a screen. Click through every
  screen in both width modes.
- Compare each screen with its screenshot once more and confirm no control is missing.

## Finishing (all steps, in order)

1. `git add Mockups/fleet` then `git commit -m "ui-overhaul {WP}: fleet redesign mockup"` with
   this last line in the message: `Co-Authored-By: {COAUTHOR}`
2. `python Mockups/overhaul/ui_overhaul.py finish {WP} --commit <short hash> --notes "<one line>"`
3. Add your hand-off note under `## 11. Hand-off notes` in `Mockups/IMPLEMENTATION.md`:
   `- **{WP}** (date, {MODEL}): what you built, the screens covered, main departures, open questions.`
   Keep it under 12 lines.
4. `git add -A` then `git commit -m "ui-overhaul {WP}: tracker"`, then `git push origin ui-overhaul`.
5. Final message: the list of screens, where the mockup is, and the open questions for the owner.

If you truly cannot finish, commit what is useful, run
`python Mockups/overhaul/ui_overhaul.py block {WP} --reason "..."`, commit, push, and stop.
