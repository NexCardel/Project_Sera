# SGT dataset gating: plan (2026-10-06)

Owner rules (decided in chat, 2026-10-06):
1. **Identity first.** A dataset exists once its form (filing type) and period are captured. Nothing else
   (ack, submit message) may create or upgrade a dataset without that identity.
2. **Evidence is per portal, bound to the identity.**
   - ITR: form+period -> ack (makes it Submitted) -> an e-verify message *tied to that ack* (makes it Verified).
   - GST: form+period -> a status read on the dataset's own return page / calendar card (a Filed status
     makes it Submitted); the ARN, when it appears, proves Submitted & Verified (existing `identifier_proves`).
   - A submit message that is not bound (banner, help text, a page that carries no form+period of its own,
     a wizard step label) is disregarded: it never creates or upgrades anything.
3. **Fixed elements follow the latest value the same page shows.** A value belongs to the page that supplied
   it. If that same page now shows a different ack / status / filing type / year for the same dataset, the
   row follows it (a status may go down only this way). Another page can only add: first value kept, status
   only up. The old tracker key is superseded when the key changes.
4. **No link dependence.** SGT specs must not depend on URLs. The five `urls` filters added on 2026-10-06
   (GST current-dataset specs) are replaced by content rules. (The seven older ITR / GSTIN filters stay for
   now: separate decision.)
5. **View-only / abolished returns are never datasets** (GSTR-2, 2A, 2B ...). Seen live 2026-10-06: the
   `gstr2/preview` viewer produced three false "GSTR-2 Draft" rows.
6. **Tracker dump display** (UI only, nothing stored changes):
   - form + period + message, no ARN  -> "Submitted", light green
   - form + period + message + ARN    -> "Submitted & E-verified", dark green
   - ITR "e-verification pending" (ack present, message says pending) keeps its yellow state.

## Build order (this round)

| WP | What | Where |
|----|------|-------|
| G1 | A message-only record (no ack on the page) is tied to the session's single open Submitted (Not Verified) row, else held with an alert event; it never uses the draft's form/period. | `_merge`, `_merge_values` in sgt_shadow.py |
| G2 | An ack-only row (no form+period) is HELD (existing `held` machinery, reason "form and period not known yet"), released when its identity arrives, and written with an alert when the session ends without one (a filing is never lost). | `_queue`, `_finish` |
| G3 | Slot fields remember the page that set them (`owner`). Same page + different value: ack, status (either way), filing type, status evidence, year/period parts follow the page. List/card records never do this (many cards per page). A whole-page confirmation re-read finds its slot by the page that supplied its ack. | `_Slot`, `_find_slot`, `_merge_values` |
| G4 | Spec option `page_excludes` (lines that switch a spec off); GST current-dataset specs use it instead of `urls`; the bare-year spec loses its URL. | sgt_specs.py, resolver, sgt_fields.json |
| G5 | GSTR-2 / 2A / 2B (any `gstr2...`) never a form: link spec and heading spec. | sgt_fields.json |
| D1 | Display states above; status filter gets "Submitted". | ui/windows/tracker_dump_window.py |
| T  | Tests for every WP, the sequences from the logs, replay of the real corpus (old vs new), full suite. | tests/ |

## Phase 2 (planned, not built in this round, needs a go-ahead)
- Header identity specs (`NAME GSTIN` header line, no link filter) so a session that starts mid-session still
  learns the client; restart persistence of the identity (shutdown snapshot, same 4 h window).
- Clean-up of the three false GSTR-2 rows (repair tool, dry run first) and the content-based replacement of
  the seven older ITR / GSTIN `urls` filters.
- LTT feed / Excel: unchanged here (they follow the stored ladder, not the new display states). Decide later
  whether LTT should show the light-green "Submitted" too.

## Acceptance
- Old failure sequences replayed from the logs: no formless second-ack row, no stuck "Filed" after the page
  says "Not Filed", no row from a bare message, no GSTR-2 row, no October row from the dropdown.
- Replay of the recorded corpus: differences old vs new reviewed row by row.
- Full suite with the live plugin; only the 13 known sync-UI failures (live-mode PC) allowed.

## Added later the same day (owner decisions)
- **Filing preference in the dataset key:** `...:FORM:PERIOD:REVISED` (Belated / Updated likewise). Original or no
  preference adds nothing, so rows captured before this keep their keys. Old rows are not rewritten.
- **Quarter with no year in the label** keys as the whole range (`APR_JUN`), no longer as its first month.
- **Lowering a status that has an ARN is asked, never assumed:** SGT keeps the recorded status, raises a dialog
  (Keep current status = default / Lower the status); a "yes" is applied on SGT's next tick and the row is sent
  with `status_correction`, which the tracker accepts (it otherwise refuses to lower a status). A lower status
  on a row with no ARN (GST "Filed" then "Not Filed") is applied directly, with the same flag.
- Noted, not built: at ARN capture SGT re-reads the GSTIN / name (ITR: the header name only); if nothing is
  found the identity already captured stays the profile. Everything else in phase 2 is left for later.
- Known and left alone by decision: cross-engine duplicates when one engine keys by PAN and another by GSTIN, an
  ack-only row not merging with its full row, `GSTR-3B` / `GSTR-3BQ` key spelling.
