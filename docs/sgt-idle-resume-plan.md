# SGT idle-resume: plan for the implementer

File: `core/sgt/sgt_shadow.py` (tests: `tests/test_sgt_shadow.py`). Do NOT commit, bump version.json, or touch `package_dist/`.

## Problem (observed live, Sept GSTR-1 filing)
A session idles >20 min while staff calculate off-portal. On the next tick `_observe` calls
`_end_idle` first (line ~350), which ends the session and discards its profile (PAN/GSTIN/name),
confirmation and draft pieces (form, period). A new empty session is then created (line ~370). The
filing-success page produces a dataset with no client profile, so it is written unattributed and the
"client unknown" alert fires. Kind of bug: **any long pause loses identity**, on every portal.

## Goal
When a session ends ONLY because of the idle timeout, remember its identity so the SAME window can
resume it within a longer window. Everything else behaves exactly as today.

## Design
1. Constant `RESUME_WINDOW_SEC = 4 * 3600` next to `IDLE_END_SEC` (line ~99).
2. New dict `self._parked: Dict[int, Tuple[float, Dict[str, Any]]]` in `__init__` (near `_sessions`,
   line ~296): `hwnd -> (parked_at_clock, snapshot)`. Memory only; not persisted, not in crash recovery.
3. In `_end_idle` (line ~1222), for the idle path only (NOT `end_session` called for logout, login,
   "moved to", "a different PAN appeared", shutdown, switch-off): take `snap = _session_to_json(s)`
   BEFORE `end_session` runs (because `_finish` closes the draft), call `end_session` as today
   (rows still dispatch, `session_end` still logged), then store `self._parked[hwnd] = (now, snap)`
   only if `s.has_content` and the snapshot has a client PAN or GSTIN in `profile`.
   Also at the top of `_end_idle`: drop parked entries older than `RESUME_WINDOW_SEC`.
4. In `_observe` where a new session is created because `s is None` (line ~370): if `hwnd` is parked,
   age <= `RESUME_WINDOW_SEC`, `portal` equals the snapshot portal (or either is empty), and `url` is
   NOT matched by `_LOGIN`/`_LOGOUT`: build the new session with `_session_from_json(snap)` but
   - fresh `session_id`, `started`, `last_seen=now`;
   - `slots = []` and `draft.slot = None` (rows were already dispatched at idle end; dataset keys are
     stable so re-capture simply updates);
   - keep `profile`, `confirmed`, `confirm_note`, `timeline`, `draft.pieces`, `draft.claims`, `portal`,
     `strict`, `sdis` (check `ContainerSession.from_json` copes; if it carries instances already
     dispatched, clear them).
   Pop the parked entry in every case (resumed, expired, or login/logout url). Log
   `self._event(s, "session_resumed", from_session=<old id>, idle_sec=<n>)` and echo one `[SGT]` line.
5. Safety: no extra code needed for client switches. The restored profile makes the existing
   `_identity_conflict` check (line ~440) fire if the first page shows a different PAN/GSTIN, which
   ends the resumed session and starts a strict new one. Verify this with a test, do not bypass it.
6. Do not change `IDLE_END_SEC`, the pill/HUD behaviour, or `_finish`/`would_be_payload` output.

## Tests (add to `tests/test_sgt_shadow.py`, reuse its fake clock/helpers)
- idle gap > 20 min then a page with no PAN/GSTIN: new session carries the old profile and
  `client_known` is true; the resulting row has the client's PAN/GSTIN and is not unattributed.
- draft pieces (form + period) survive: success page with ARN joins to the earlier form/period.
- gap > `RESUME_WINDOW_SEC`: nothing resumes (empty profile as before).
- first page after the gap is a login URL: nothing resumes, old behaviour.
- first page shows a different PAN/GSTIN: ends the resumed session, new session is strict and does
  not inherit the old client's profile.
- portal differs: nothing resumes.
- logout / "login page - next client" / "moved to" / shutdown endings never park.
- parked entry is removed after use (a second idle does not resurrect an older snapshot).
- one existing-behaviour regression: idle end still logs `session_end` and dispatches rows.

## Verify
Run the full suite with the live plugin (plain pytest crashes at collection):

    $env:PYTHONPATH="tools"; python -m pytest -p live_tf -p no:terminal -p no:cacheprovider tests

Report: files changed, tests added, full-suite T/F/S summary. If a pre-existing test fails, say so
and do not alter it unless the failure is caused by this change.

## Notes
- Use Edit/Write tools for code, not shell heredocs with backslashes.
- Privacy: parked snapshots hold PAN/GSTIN in memory up to 4 h after idle; mention it in the final
  report. Nothing new is written to disk or logged beyond the `session_resumed` event (log session ids
  and idle seconds only, no PAN/GSTIN values).
- Out of scope: persisting parked sessions across app restarts; browser reopened (new hwnd).
