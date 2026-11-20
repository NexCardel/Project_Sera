# SGT refactor: done, pending, progress

2026-11-20

## Summary

SGT has been refactored into a new `core/sgt` layout and its shadow mode removed, with every other behaviour kept. The full test suite matches the untouched code: 4,823 passed, and the same 16 tests that already failed before still fail. The work sits uncommitted on branch `sgt-refactor` in the worktree `Project_Sera_sgt_refactor`; it has not been run in the real app yet.

SGTv2 was dropped on 2026-11-19. The universal UIA capture worker is the next task, not started.

## Done

Files were moved with `git mv`, so their history is kept. Method bodies were moved unchanged.

| Area | Before | After |
| --- | --- | --- |
| Capture path | `core/vsdc/vsdc_capture, vsdc_reader, vsdc_ocr_lane, vsdc_windows, vsdc_gate, vsdc_uia_text, vsdc_scope` | `core/sgt/capture/loop, reader, ocr_lane, windows, gate, uia_text, scope` |
| Engine | `core/sgt/sgt_shadow.py` (2,663 lines, class `SgtShadow`) | `core/sgt/engine/` package, class `SgtEngine`, 12 parts plus `session` and `policy` |
| Specs | `core/sgt/sgt_specs, sgt_resolver, sgt_toolbox, sgt_fields.json` | `core/sgt/specs/specs, resolver, toolbox, sgt_fields.json` |
| Other SGT files | `core/sgt/sgt_<x>.py` | `core/sgt/<x>.py` (`sgt_health` is `spec_health`) |
| Router's SGT wiring | inside `core/vsdc/vsdc_router.py` | `core/sgt/router_link.py` (`SgtLink`, inherited by `VSDCRouter`) |
| Browser and portal names | `vsdc_router.PORTAL_KEYWORDS, BROWSER_EXE_NAMES` | `core/sgt/capture/windows.py` |

**Engine parts:** `engine` (constructor, entry points), `boundaries` (tabs, portals, login/logout, idle), `reading` (page reads, blind pages, OCR), `identity` (who the client is), `draft` (dataset in progress), `merge` (folding datasets into rows), `demotion` (asking before a status is lowered), `rows` (outbox, keys, payload), `sdis_rows` (SDIS containers), `ending` (parking, finishing), `persistence` (crash snapshot), `journal` (HUD, log, digest), `evidence_hooks` (the `sgt_ev` block).

**Shadow mode removed:**

- Settings offers Live / Off only. A stored "shadow" setting, or `SGT_MODE=shadow`, runs live.
- The engine always writes `SGT_live` rows with the tracker's canonical keys; the `SGT:` key namespace is gone from the engine.
- Database handling of old `SGT_shadow` rows is kept, because office databases still hold them.
- The data folder `sgt_shadow`, its log file names and `SGT_SHADOW_DIR` keep their names, so office PCs keep finding their crash snapshot and logs.

**Other changes:**

- The capture package imports without loading `core.vsdc`, ready to grow into the universal capture worker.
- Every importer (main, UI, tools, sera_db, SDIS, SGT-I, tests) was updated to the new paths; no old-path shims were left.
- VSDC crosshair engines, VSDC247 and VSDC247-V were left in place, including VSDC247-V's enrichment of each SGT row.
- Tests were updated where they used shadow mode or old key formats; two shadow-only tests were removed and one added (a stored "shadow" runs live). A helper, `tests/sgt_engine_patch.py`, pins `datetime`, `uuid` or `time` across all engine parts.

## Progress

The refactor passes the same tests as the untouched code. Both runs used the full suite with the live T/F/S plugin.

| Run | Passed | Failed | Skipped |
| --- | --- | --- | --- |
| Baseline (untouched main checkout, same commit) | 4,824 | 16 | 4 |
| Refactor (`sgt-refactor` worktree) | 4,823 | 16 | 4 |

The 16 failures are the same tests in both runs: the tracker dump parser (7), the SGT replay recorder (3), and one each in memory tuning, SGT-I furniture, SGT-I pairs, the payload inspector, tracker CSV export and the Manage Clients cards. The one fewer pass is two shadow-only tests removed and one new test added.

All 147 changed or new Python files compile, and pyflakes finds no new undefined names.

## Pending

- [ ] Commit the refactor on branch `sgt-refactor` (nothing is committed yet)
- [ ] Run the app from the worktree and check SGT captures a few ITR and GST pages
- [ ] Deploy to the office PC and run it for a few days
- [ ] Merge `sgt-refactor` once the office trial is clean
- [ ] Build the universal UIA capture worker, shared by SGT-C, SDISv2 and later frameworks, growing out of `core/sgt/capture`

**Known issues, not caused by this work:**

- `test_vsdc_reader.py::test_b5_early_abandon_frees_worker_without_wedging` fails when run alone, also on the untouched checkout, but passes in the full run: a timing-sensitive test.
- The legacy GST crosshair handler in `core/vsdc/vsdc_router.py` uses variables it never defines (`uia_lines`, `lines`, `full_text`, `img`).
- The 16 baseline test failures listed under Progress.
- Docs under `docs/` still name the old file paths; the new layout is described in `core/sgt/__init__.py`.
