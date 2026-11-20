# Universal Capture Worker (UCW): plan

2026-11-20 · branch `sgt-refactor` · worktree `Project_Sera_sgt_refactor`

## Restore point

Made before any UCW work, on top of the committed SGT refactor (`cd5c952`):

- git tag `restore-point/pre-ucw`
- full copy of the repository as a git bundle: `..\restore_points\pre-ucw.bundle`

To see everything UCW changed: `git diff restore-point/pre-ucw`. Going back throws away every commit
made after the tag, so ask before doing it.

## What UCW is

One Windows process, started by Sera, that does **all** the UI Automation reading of the browser.
Sera itself (the UI, SGT, the router) never makes a UIA call for these readers again; it sends read
requests and receives results.

Owner decisions (2026-11-20):

1. First version serves **SGT-C, the SDIS recorder and SGT-I's flash events**. The URL feed for the
   router and the SCA UID watcher comes after. OS Assist stays out (it writes).
2. It is a **separate process**, not threads.
3. SGT gets a **translation layer**: SGT keeps receiving exactly what it receives today, so the SGT
   engine and the capture loop do not change.
4. No shadow or comparison mode. The owner tests by deploying to an office PC for a few days.

Two hard requirements: **no SGT blind problem**, and **very fast**.

## Today (measured on this PC, vsdc.log 2026-11-12 to 11-20, 5,628 reads)

Seven parts of the app own a UIA object: `core/sgt/capture/reader.py`, `core/sgt/capture/uia_text.py`,
`core/sdis/recorder.py`, `core/sgt_i/uia_events.py`, `core/vsdc/vsdc_router.py`,
`core/sca/page_nodes.py` (SCA UID watcher), `core/os_assist/worker.py`. Every page SGT reads is read a
second time by the SDIS recorder (raw view, with screen boxes). The address bar is read by three parties.

| Measure | Today |
| --- | --- |
| Page read | 78 ms p50, 203 ms p90 (last read per minute); per-minute max 156 ms p50, 563 ms p99, 1,219 ms worst |
| Heavy page (lab, W2-1) | 530 ms control view, 767 ms raw view |
| Capture tick | 16 ms p50, **469 ms p95, 80.7 s worst** |
| Reads reported blind | 273 of 5,628 (4.9 %) |
| Page snapshot size | 229 nodes p50, 739 p95, 1,871 max; largest record 204 KB |
| Pickle round trip of the largest page | 2.5 ms |
| New process to UIA ready (venv Python) | 300-420 ms |

## Why SGT goes blind, and what UCW does about each cause

| Cause | Today | UCW |
| --- | --- | --- |
| A read was slow or stuck and its empty result counted as a blind page | Patched: 20 s slow grace, stuck threads abandoned, 5 stuck = UIA paused 5 min | Every result has a status. A stuck worker process is killed and restarted in about 1 s; its open requests come back as `worker-restart`, which the loop asks again and never counts as blind. No 5-minute pause. |
| Something else stalls Sera's capture thread (GIL, a UIA call with no timeout) | Tick p95 469 ms, one 80.7 s stall | No UIA and no unpacking on Sera's side: the capture thread only sends and receives. WP4 finds the stalls in `hang.log` and moves whatever UIA is left on that thread. |
| The page was caught mid-load (empty Document between pages) | Counted after 2 retries 300 ms apart | A Document with no nodes right after a URL change is `loading`; the worker reads again when the page's structure-changed event fires, or after the retry gap. |
| A canvas page UIA can never read | OCR (not GST) | Unchanged: only `empty` after the retries is blind and goes to OCR. |

## Target numbers

Lab and this-PC figures are the baseline; the office PC is slower, so WP0 measures there first and
these targets are confirmed or adjusted from that.

| Measure | Target |
| --- | --- |
| Page read, typical | ≤ 80 ms p50, ≤ 250 ms p95 |
| Heavy page | ≤ 800 ms |
| Browser time per page, SGT + SDIS together | about 0.77 s instead of 1.3 s on the heavy page (one read instead of two) |
| Request/result between the processes | ≤ 3 ms for the largest page, under 1 ms typical |
| Capture tick on Sera's side | ≤ 5 ms p99, never blocked by UIA |
| URL change to read started | ≤ 150 ms |
| Page shown to SGT having its lines | p50 ≤ 200 ms, p95 ≤ 500 ms, heavy ≤ 1 s |
| Recovery from a stuck read | ≤ 4 s (3 s heartbeat silence, then a restart); packaged-exe restart ≤ 1.5 s until measured |
| Blind reads that are not canvas | 0 |

The one limit no design beats: a browser answers UIA for all its windows on one UI thread, so a heavy
page costs what the browser charges. UCW removes duplicate reads and Sera's own waiting.

## Design

```
Sera process                                         sera-capture process (<exe> --capture-worker)
-------------                                        --------------------------------------------
CaptureLoop (unchanged)                              PageReader (unchanged): one lane per window,
  scope gate, screenshot, frame gate                   content + URL worker threads, MTA, wedge rules
  asks RemoteReader for reads    --- requests --->   UiaBackend (unchanged)
RemoteReader  (translation layer) <--- results ---   snapshot: control view, or raw when SDIS wants it
  same methods as PageReader                         FlashWatcher (moved in, WP6)
  supervisor: heartbeat, restart                     page events -> early reads (WP7)
SgtEngine, SGT-I, SDIS recorder (fed, no UIA)
```

- **Starting the worker:** the app's own exe with `--capture-worker`, dispatched at the top of `main.py`
  before Qt or the database, the same way as `--sdis-mine`. In the venv: `python main.py --capture-worker`.
  Using the app's exe (not `multiprocessing`) avoids PyInstaller's frozen-child quirks.
- **Messages:** length-prefixed pickle frames over the child's stdin/stdout (both sides are Sera's own
  code). Sera to worker: `request(ReadContext)`, `close_lane(hwnd)`, `abandon_slow(hwnd)`, `stats()`,
  `sdis_want(hwnd, ...)`, `flash_watch(hwnd, key)`, `stop`. Worker to Sera: `result(ReadResult)`,
  `stats(...)`, `flash(...)`, `beat` every 0.5 s. One reader thread on Sera's side turns frames into a
  queue that `results()` drains, so nothing on the capture thread waits on the pipe.
- **Translation layer (`RemoteReader`):** offers exactly the methods `CaptureLoop` uses on
  `PageReader` (`request`, `results`, `inflight`, `abandon_slow_content`, `page_ms`, `paused`,
  `close_lane`, `stats`, `close`) and hands back the same `ReadContext`/`ReadResult` objects. SGT's
  engine and the loop are not edited. It keeps its own record of in-flight requests, so `inflight()`
  and `page_ms()` answer locally without a round trip.
- **Supervisor:** no `beat` for 3 s, or the process exits: kill it, start a new one, answer every open
  request with `error="worker-restart"`. 3 restarts within 60 s: wait 30 s before the next start and
  report the health state `uia-restarting`. Never off for the run.
- **Kill switch, not a comparison:** setting `capture_worker` = `process` (default) or `inproc` (today's
  in-process `PageReader`). Only one runs at a time. It exists so an office PC can be put back without a
  new build if the worker misbehaves.
- **Privacy:** the pipe carries page text; it never leaves the PC and nothing new is written to disk.
  Logs stay counts and times only, as today.

## Work packages

Each WP is its own commit on `sgt-refactor`, with its tests. Only the affected test files run per WP;
the full suite (live T/F/S plugin) runs at WP5 and at the end.

**WP0 - Office probe (`tools/ucw_probe.py`).** A one-off tool the owner runs on the office PC with a few
portal pages open. It measures: packaged-exe start to UIA ready; control-view and raw-view read times
per page; whether raw view filtered to control elements (`IsControlElement`) gives exactly the same
lines as today's control-view read; and the pipe cost for each page. Also summarises `hang.log` stalls.
Output: counts and times only, no page text. Decides WP5's path and confirms the target table.

**WP1 - Capture package and worker entry.** Move the UIA-only parts of `core/sgt/capture` (`reader`,
`uia_text`, `scope`, `windows`) to `core/capture/`; the loop, gate and OCR lane stay with SGT. Add
`core/capture/worker_main.py` (the child: reads frames, hosts `PageReader`, sends results and beats)
and `core/capture/protocol.py` (frames, message types). `main.py` dispatches `--capture-worker`.
Tests: framing round trip; the child run with a fake backend answers requests.

**WP2 - RemoteReader and supervisor.** `core/capture/remote.py`. Wired in `core/sgt/router_link.py`
where `PageReader(...)` is built today, behind `capture_worker`. Tests: the `CaptureLoop` test suite
passes unchanged against `RemoteReader` over a fake child; kill-and-restart answers open requests with
`worker-restart`; restart backoff; `inflight`/`page_ms` without a round trip.

**WP3 - Statuses that are never blind.** `worker-restart` and `loading` join `ReadResult.error`; the loop
treats both as "ask again" and the health record counts them separately from blind. Tests: a restart
mid-read and an empty Document after a URL change never raise the blind count.

**WP4 - Capture-thread stalls.** From WP0's `hang.log` summary, find what blocked the capture tick (the
80.7 s stall, the 469 ms p95) and move any UIA left on that thread into the worker. Target: tick ≤ 5 ms
p99. Tests: a tick with a stalled worker still finishes within its budget.

**WP5 - SDIS recorder joins (one read per page).** SGT already tells the recorder which pages it
wants (`offer`). Instead of reading the page again, the recorder asks the worker to make that page's
read a raw-view read. Path depends on WP0:
- filtered raw lines identical: the worker reads raw once; the translation layer filters to control
  elements for SGT (parents and depths recomputed); SDIS gets the raw nodes.
- not identical: the worker reads control view for SGT then raw for SDIS back-to-back on the same
  lane, so it is still one queue and SGT never waits for SDIS.
Tests: filter equality on recorded pages; SGT lines unchanged; recorder output unchanged; the 1.5 s
budget and size cap kept.

**WP6 - SGT-I flash events move in.** `FlashWatcher` runs inside the worker; flashes come back as
messages to SGT-I. Tests: the existing flash tests against the new path.

**WP7 - Event-driven reads.** The worker listens for structure and name changes on watched Documents
and the URL lane reacts to them, so a page change starts a read without waiting for the next poll.
Polling stays as the safety net. Target: URL change to read started ≤ 150 ms.

**WP8 - Health and diagnostics.** The vsdc.log heartbeat gains worker restarts, pipe ms and read ms
(p50/p95); the health record gains `uia-restarting`. A worker crash writes its stack to `hang.log`.

**WP9 - Office trial.** Full suite, build, deploy to an office PC for a few days, compare vsdc.log
against the target table. Merge only after a clean trial.

**Later (not in this plan):** the router's address bar and the SCA UID watcher use the worker's URL
feed; the legacy `uia_text` users (crosshair engines, VSDC-X) move or retire.

## Risks

- **Packaged-exe start time** is unknown (venv: 300-420 ms). WP0 measures it; restart targets follow.
- **Raw-to-control equality** is expected (UIA defines control view as raw filtered by
  `IsControlElement`) but Firefox's Gecko handling in `uia_nodes._nodes_of` needs checking. WP5 has a
  fallback path.
- **Two processes on a slow PC:** the worker adds about one Python process of memory (tens of MB). The
  browser load goes down, not up.
- **Antivirus** may look at a second Sera process. It is the same signed exe.

## Progress

- [x] Refactor committed (`cd5c952`)
- [x] Restore point `restore-point/pre-ucw` and `..\restore_points\pre-ucw.bundle`
- [x] This plan
- [x] WP0 - WP8 built (2026-11-20). WP9 (build, office trial) is the owner's.

Built as one commit instead of one per WP, because the WPs share files (`core/capture/reader.py` has WP1,
WP3 and WP5 changes; `loop.py` and `router_link.py` carry both the move's imports and WP2-WP4). The old
`core/sgt/capture/{reader,uia_text,scope,windows}` paths stay as aliases of the same module objects.

| WP | What was built | Tests |
| --- | --- | --- |
| WP0 | `tools/ucw_probe.py`: worker start to UIA ready, control/raw read ms per portal page, filtered-raw line equality, pipe round trip per page, hang.log capture-stall summary. Counts and times only. | run on this PC (below) |
| WP1 | `core/capture/` (reader, uia_text, scope, windows moved; `protocol.py`, `worker_main.py`); `main.py --capture-worker` | `test_ucw_worker.py` (frames, real worker process with a fake backend) |
| WP2 | `core/capture/remote.py`: RemoteReader, WorkerProcess, ThreadChild, supervisor (3 s silence, 3 restarts/60 s then 30 s wait). Setting `capture_worker` = `process` (default) / `inproc`, env `SERA_CAPTURE_WORKER`; wired in `core/sgt/router_link.py` | the whole CaptureLoop suite again over RemoteReader (`test_ucw_remote_capture.py`, 39/39); restart, backoff, local `inflight`/`page_ms` |
| WP3 | `loading` (empty in-scope Document on a `url`/`burst` read) and `worker-restart`; the loop asks again (`frame`, so the burst bounds it), counts `loop_loading` / `loop_restart`, never `errors` or blind | loading, restart mid-read |
| WP4 | hang.log on this PC: the capture thread was in `time.sleep` in every capture-stall dump that still exists (the 80.7 s stall's dump rotated out). UIA left on the capture thread: the portal-address tripwire's `read_page_text` (waits up to its timeout) - now on its own thread. Suspect left: `PrintWindow` for windows not in front (a message the busy browser must answer); now noted in vsdc.log when a screenshot takes over 300 ms (`loop_slow_shots`) | a tick with a stuck worker stays under 50 ms |
| WP5 | the SDIS recorder reads through the worker (`RemoteReader.read_raw`): its raw read queues on the window's lane behind SGT's page read, and its 1.5 s budget is judged on the read's own time. Path: two reads back to back (the safe one). `core/capture/rawfilter.py` + the probe check the one-read path | ordering, budget, filter equality |
| WP6 | `RemoteFlashes` (FlashWatcher's interface); the worker hosts FlashWatcher; `SgtIntelligence.set_flashes` | flashes reach SGT-I |
| WP7 | `core/capture/page_events.py`: WinEvent title / value changes of watched windows start a URL-only read (at most one per 0.3 s per window); polling stays | event -> URL read, rate limit |
| WP8 | heartbeat `uia_restarts`, `uia_pipe_ms`, `uia_read_p50/p95`, `uia_event_reads`; health state `uia-restarting`; a dead worker's faulthandler stack copied into hang.log | numbers, state |

**Measured on this PC (2026-11-20, venv, Edge, fictional local pages - not the office):**

| Measure | Result |
| --- | --- |
| Worker start to UIA ready | 331-407 ms (venv; the packaged exe is not measured) |
| Heavy page (300-row table), control / raw read of the Document | 887-1,043 ms / 1,202-1,427 ms; 2,421 / 4,533 nodes |
| Raw view filtered to control elements, same lines as the control view | **yes** on both pages tried (Edge). The office probe decides WP5's one-read path |
| Pipe round trip with the heavy page's raw nodes both ways | 13.6-14.2 ms (about 7 ms one way for 4,533 nodes); empty frame 0.13 ms |
| Reader cost per read on the heavy page, in Sera vs in the worker | the same (frame read 2,016 vs 2,078 ms): the worker adds no read time |

**Open for WP9:** run `python tools/ucw_probe.py --exe <Sera.exe> --out probe.json` on an office PC with
portal pages open; if every page says `same = yes`, switch WP5 to one raw read per page. Watch vsdc.log for
`loop_slow_shots` (the PrintWindow suspect) and `uia_restarts`. Put a PC back with `capture_worker = inproc`.
Known, not from UCW: `tests/test_vsdc_reader.py::test_b5_early_abandon_frees_worker_without_wedging` fails
most runs at the restore point too (its second read has no gate and can finish first).
