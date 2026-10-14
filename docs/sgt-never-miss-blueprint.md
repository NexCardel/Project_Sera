# SGT never-miss blueprint

Written 2026-10-14 by Fable 5.1 (cloud) from `docs/sgt-stress-problem-report.md` and the code on
branch `sgt-stress-report` (commit `44b99d6`). It answers the report's sections 2, 6 and 8. It is
the complete design; nothing in the code was changed to write it.

**Who builds it.** Lower-level agents (Sonnet, Haiku), one work package (WP) per session. Every WP
in section 9 stands alone: the files and functions to change, signatures, data structures,
constants, pseudocode, edge cases, tests with names, done-criteria, dependencies, the model tier
and an owner STOP point. An agent needs this file, the WP it was given, and the code. It does not
need the chat that produced this file.

**Read section 10 ("Owner answers") and section 10a ("Review corrections") first.** They were
added after review and override the design text where they differ.

**How to read it.** Section 1 says what the code really does today (the report, checked).
Section 2 is the answer to "can 100% be met". Section 3 is the design on one page. Sections 4 to
7 are the design in detail. Section 8 is the test plan. Section 9 holds the work packages.
Section 10 lists every owner decision with a recommended default. Section 11 maps every scenario
of the report to what closes it. Section 12 is what the owner must measure on Windows.

---

## 1. The report checked against the code

Everything in the report that could be checked by reading the code holds. These details matter
for the design and were not in the report, or differ slightly:

| Claim | What the code does | Where |
| :--- | :--- | :--- |
| A read over 3 s returns empty and the page counts as read and blind | Confirmed. `_run_with_timeout` returns None on the future timeout, `read_page_text` turns None into an empty result unless `was_busy()`; `_observe` records the frame hash and read time before the read and only restores them on `busy`. The late result is thrown away by `_timed` after counting it. | `core/vsdc/vsdc_uia_text.py:177-233, 521-524`; `core/sgt/sgt_shadow.py:603-616` |
| "Busy" is global | Confirmed, and stronger: the busy flag is per calling thread (`_caller` is thread-local) and the tab-id read goes through the same `_run_with_timeout`, so while any read is in flight the tab id is also skipped and `_run_sgt` returns before `observe` is even called. | `vsdc_uia_text.py:118, 291, 320`; `vsdc_router.py:506-510` |
| About 3 cross-process calls per element | Confirmed: `CurrentName`, `CurrentControlType`, then `IsPassword` + `ValuePattern` on value controls, plus `GetElement(i)` per element. | `vsdc_uia_text.py:427-477` |
| The cached single-call read exists but is not wired in | Confirmed. `read_page_nodes` uses `FindAllBuildCache` with 15 cached properties; W2-1 measured 927 to 530 ms on the heavy page and lines identical to the Core reader in the control view. SGT-I's own extra node read runs on the SAME shared worker while SGT-I is on (`_nodes_for_recording`). | `core/sgt_i/uia_nodes.py`; `sgt_shadow.py:754-760`; `docs/sgt-overhaul-report.md` W2-1 |
| The worker is STA with no message pump | Confirmed (`comtypes.CoInitialize()` in the pool initialiser). Two other parts of the app already use MTA UIA threads successfully: `core/sgt_i/uia_events.py` (`CoInitializeEx(COINIT_MULTITHREADED)`) and `core/sdis/recorder.py`. So MTA works with this comtypes setup. | `vsdc_uia_text.py:73-84`; `uia_events.py:96`; `recorder.py:131` |
| The address-bar read has no timeout and uses a GUI-thread UIA object | Confirmed. `_init_uia` runs in `VSDCRouter.__init__`, built inside `VSDCWorker.__init__` on the main thread; `extract_browser_url` runs on the capture thread with that object and no guard. | `vsdc_router.py:226-230, 915-1021`; `vsdc_worker.py:40` |
| The 2 h router prune ends sessions as "window closed" with no parking | Confirmed. `_prune_stale_sessions` calls `sgt.end_session(hwnd, "window closed")`, which pops parked entries too. | `vsdc_router.py:845-872`; `sgt_shadow.py:403-415` |
| Parked sessions are memory only | Confirmed. `_parked` is not in `_save_state`. | `sgt_shadow.py:1842-1872` |
| A GST blind page is skipped | Confirmed (`NO_OCR_PORTALS`). | `sgt_shadow.py:111, 621-626` |
| Foreground only | Confirmed (`GetForegroundWindow` once per tick in `get_foreground_info`). | `vsdc_router.py:938, 1035` |
| The change gate is a 32x32 hash of the whole frame | Confirmed, with `hash(bytes)`: any single pixel change in the resized image changes it. | `sgt_shadow.py:149-156` |
| Updates never apply on an all-day PC | Not quite. The SYSTEM update agent stages the installer; the app closes itself after 3 minutes without input, or after the update has waited 8 h, after 30 s without input, at most twice a day. So an all-day PC gets the update at the first quiet 30 s after 8 h. The gap is that nobody can see which PC is on which build, and a fix is not forced through sooner. | `core/update_agent.py:111-120`; `main.py:2013-2024` |
| Rows can be lost when the database is busy | Confirmed gap. `insert_tracker_dump` is called from the capture queue worker; an `OperationalError` ("database is locked") propagates out of `_process_extension_result`, the worker prints `[Capture Worker Error]` and the row is gone. No `busy_timeout` pragma is set. SGT's crash snapshot does not help here: the row left SGT's outbox already. | `main.py:829-839, 1117-1131`; `database.py:140-142` |
| Crash safety of the outbox | Mostly fine. On recovery `_queue_all(every=True)` re-queues every slot of every snapshotted session, so a row popped from the outbox and lost in a crash is re-sent (keys make that idempotent). The only loss window is the 2 s snapshot throttle. | `sgt_shadow.py:1125-1138, 1874-1913` |
| Two engines never disagree | Not applicable any more: VSDC247 is off. Its scoring code in `core/vsdc/vsdc247.py` (green box, success wording, one-identifier rule) is reusable as an OCR trigger. | `docs/vsdc247-safety-net.md` |
| `latest_period_only` on list pages | A list page that shows several filed returns keeps only the latest period's cards. That is right for "the dataset being worked on" but wrong for recovery: older filed rows with their own ARN are dropped. | `core/sgt/sgt_resolver.py:245-259, 329` |

Two facts the report left open that the design depends on, and that only a Windows probe can
settle (section 12): whether Chromium and Firefox expose the page URL as the `Value` of the
Document element (the design uses it as the first source of the URL with the address bar as the
fallback), and whether the browser's PDF viewer exposes the PDF's text through UIA.

---

## 2. Can "100%" be met? The plain answer

Yes for three of the four layers, within constraints 1 to 12, and the fourth layer is met by
definition once the first three report what they could not do. Precisely:

**Layer 1, seen means captured.** Met for every submission whose evidence is on screen in a
visible browser window on a Sera PC for at least one frame of the frame watch (default 200 ms,
section 5.3) while Sera runs. The remaining gap is evidence that is on screen for less than one
frame, which a human cannot read either. Nothing in constraints 1 to 12 blocks this layer. It
needs OCR on GST (constraint 7, now allowed) and reading visible windows that are not in front
(decision D6; reading is passive, so constraint 1 is kept).

**Layer 2, not seen means known.** Met. Every reason SGT can fail to read an in-scope page
(blind, busy, wedged, paused, stalled, scope lost, specs silent, old build, disk, no alert config)
becomes a named state of one health record. Locally it is noticed within `BLIND_ALERT_SEC` (20 s)
and shown as a brief HUD event (constraint 8 kept) and a phone alert (existing ntfy sender,
rate-limited). Office-wide the record reaches every PC through Sera Sync as a settings key and is
shown in a "Capture health" panel. No constraint blocks it.

**Layer 3, missed means recovered.** Met for evidence the user later shows on any portal page:
filed-returns lists, track-status pages, dashboard rows treated as evidence of a filing (not of
a period), confirmation pages re-opened. Rows that came from recovery carry a `recovered` mark.
Blocked for evidence that exists only in a downloaded file (scenario C5) until the owner takes
decision D9 (Downloads watching). The smallest unblocking decision is D9 as recommended: read
the text layer of PDFs the browser saved whose file name matches a portal pattern, nothing else.

**Layer 4, unknowable is listed.** Met. A daily capture digest on the admin PC lists: minutes
each PC was blind or stalled, rows written unattributed, rows seen only by OCR and not yet
confirmed by UIA or a list page, ITR rows still "Submitted (Not Verified)" after N days (the
client may have verified on their own phone, scenario C6), PCs on an old build. What is
physically unobservable (C6, a filing done on a PC without Sera) can only be listed this way.

**What stays out by constraint.** ERI logins (constraint 11), TRACES (constraint 10), evidence
that never reaches a Sera PC (section 6 of the report). No design within the constraints can
change these, and this one does not try.

---

## 3. The design on one page

Today one thread does everything in order: foreground window, address bar, tab id, screenshot,
one UIA read with a 3 s wait, resolve. A slow read makes every other page wait, a late read is
thrown away, and nothing reports a failure. The redesign changes six things and keeps everything
else (specs, resolver, sessions, ladder, keys, replay).

1. **Reads are tickets, not calls.** The capture loop asks for a read with a `ReadContext`
   (window, tab, URL, title, portal, frame signature, time). The reader answers later with a
   `ReadResult` that carries that context back. SGT absorbs a result into the session that owned
   the context when the read was asked for, even if the user has moved on. A late read is
   evidence with a known origin, never waste. (Closes W1, W2, B5.)
2. **One read lane per browser window, workers in MTA, one cached UIA call.** Each window has its
   own worker thread with its own UIA objects, so client A's heavy page never blocks client B's
   window. The read uses `FindAllBuildCache` (already written in `core/sgt_i/uia_nodes.py`) and
   returns both lines and nodes, so SGT-I's second read disappears. The tab id, the Document URL
   and the page come from the same worker in one job, before and after the read, so a torn read
   is detected instead of mis-attributed. (Closes W3, W4 in part, W5, W6, B1, B2, A8.)
3. **The URL comes from the Document element first**, the address bar second, the title third.
   This is atomic with the page, works in full screen and popups, and filters sidebars and
   viewers per document. The address-bar read moves to the worker with a timeout. (Closes W5,
   D12, D13, D14, D3.)
4. **The change gate counts changed cells, and a frame watch runs faster than the reads.** A
   caret, typing and spinners no longer force a read; a toast or a banner does. The screenshot is
   taken every 200 ms (cheap) and the UIA read at most every 350 ms per window and never faster
   than the last read took. (Closes W8, A2, A10.)
5. **The Submission Net: OCR on every portal, add-only.** OCR runs when UIA is blind, and when the
   frame changed a lot while UIA was busy or slow. OCR may add an identifier, promote a status or
   fill an empty piece. It may never set the client, change a UIA value, or clear anything. That
   honours the lesson behind the old GST ban. (Closes W12, C1, C2, C3, F7, G9.)
6. **Health is a first-class output.** One state machine, fed by the loop, the reader and SGT,
   produces a `CaptureHealth` record every minute: build, state, counters. It drives the HUD
   event, the phone alert, an office-wide view through Sera Sync, and a daily digest. The update
   agent gets a `mandatory` path so a fix lands at the first 30 s of idle. (Closes W10, W11, H1,
   H2, I1, I2.)

Plus three smaller changes: parked sessions go to disk and the router prune parks instead of
ending (W9, E6, E9); list pages keep every card that has its own identifier and rows from lists
carry a `recovered` mark (layer 3); visible windows that are not in front are watched at a slower
cadence (W7, C8, B6).

---

## 4. The capture loop, redesigned

### 4.1 Threads and who owns what

| Thread | Today | After |
| :--- | :--- | :--- |
| Capture thread (`VSDCWorker.run`) | everything | Win32 only: list windows, titles, process names, screenshots, frame signatures; ask the reader; take results; call SGT; write health |
| Read lane workers (new, MTA, one per watched window, at most `MAX_LANES`) | one STA worker for everything | tab id, Document URL, address bar, cached page read; all timed, all abandonable |
| OCR lane (new, one thread) | OCR inline on the capture thread | OCR of a frame, delivered as a `ReadResult` with `source="ocr"` |
| Capture queue worker (`main.py`) | writes rows, drops on error | writes rows with retry and a dead-letter file |
| SGT-I, SDIS, SCC-U | own threads, SGT-I reads nodes on the shared worker | unchanged, but SGT-I gets the nodes from the Core's result and reads nothing itself |

Rule: nothing that can block on the browser runs on the capture thread. The only COM object on
the capture thread today (the router's `_uia`) is removed.

### 4.2 Data structures

```python
# core/vsdc/vsdc_reader.py

@dataclass(frozen=True)
class ReadContext:
    seq: int                    # increasing, unique per process run
    hwnd: int
    title: str                  # window title at request time (no browser suffix)
    portal: str                 # "GST Portal" / "Income Tax" / registered portal name
    url: str                    # sanitized URL the loop believed at request time ("" if none)
    url_source: str             # "document" | "address_bar" | "title"
    tab: Optional[str]          # tab id if the loop already knows it (None = worker finds it)
    frame_sig: Optional[bytes]  # 1024 nibbles, see 4.4 (None = no screenshot)
    asked_at: float             # time.monotonic()
    reason: str                 # "url" | "frame" | "periodic" | "burst" | "torn-retry" | "blind-retry" | "wants_read"
    background: bool            # window was not in front when asked

@dataclass
class ReadResult:
    ctx: ReadContext
    source: str                 # "uia" | "ocr"
    lines: List[str]
    docs: List[List[Dict[str, Any]]]   # node lists per in-scope Document (uia only; [] for ocr)
    doc_urls: List[str]         # Document Value URLs of the documents read (uia only)
    tab_before: Optional[str]
    tab_after: Optional[str]
    url_before: str             # what the worker saw just before reading (document, else address bar)
    url_after: str
    started_at: float
    finished_at: float
    error: str                  # "" | "no-window" | "uia-init" | "timeout-abandoned" | "exception:<Name>"
    @property
    def ms(self) -> float: ...
    @property
    def torn(self) -> str:      # "" | "tab" | "url"
        ...
    @property
    def late(self) -> bool:     # finished after the loop stopped waiting (always true now, kept for stats)
        ...
```

`ReadContext` is frozen so a result can never be re-pointed at another page.

### 4.3 The reader

```python
# core/vsdc/vsdc_reader.py

MAX_LANES = 3                 # watched windows at once (foreground + 2 visible); D6
WEDGE_SEC = 20.0              # a read running this long is abandoned (keeps today's SLOW_GRACE_SEC)
MAX_WEDGED_WORKERS = 5        # as today
PAUSE_SEC = 300.0             # as today
RESULT_MAX_AGE_SEC = 60.0     # a result older than this is logged and dropped, never absorbed
LANE_IDLE_SEC = 120.0         # a lane whose window was not asked about for this long is closed
TAB_TIMEOUT_SEC = 1.0
PAGE_TIMEOUT_SEC = 3.0        # now means "how long a read may take before the loop is told it is slow"
                              # (health state "slow"), not "when its result is thrown away"

class PageReader:
    def __init__(self, backend=None, clock=time.monotonic, diag=vsdc_diag.note): ...
    def request(self, ctx: ReadContext) -> str:
        """'started' (a worker took it), 'queued' (that window's lane is busy: ctx replaces any
        earlier queued ctx for the window), 'paused' (UIA paused), 'no-lane' (MAX_LANES reached
        and this is a background window)."""
    def results(self) -> List[ReadResult]:
        """Every finished read, oldest first, drained. Never blocks."""
    def inflight(self, hwnd: int) -> Optional[ReadContext]: ...
    def stats(self) -> Dict[str, Any]:
        """For the heartbeat and health: reads, max_ms, last_ms, late, queued_replaced,
        abandoned, stuck, paused, lanes."""
    def close(self) -> None: ...
```

A lane:

```python
class _Lane:
    hwnd: int
    pool: ThreadPoolExecutor      # max_workers=1, initializer=_init_mta_worker
    inflight: Optional[Tuple[ReadContext, Future]]
    queued: Optional[ReadContext] # the latest wanted context (coalesced)
    last_used: float
```

`request()` pseudocode:

```
with lock:
    if paused and now < paused_until: return "paused"
    lane = lanes.get(ctx.hwnd)
    if lane is None:
        if len(lanes) >= MAX_LANES:
            if ctx.background: return "no-lane"
            close the least recently used background lane (abandon its inflight read: it is
            counted as a result with error="timeout-abandoned" when it comes back, not absorbed)
        lane = new _Lane(ctx.hwnd)
    if lane.inflight is not None and not future.done():
        running = now - inflight.ctx.asked_at
        if running < WEDGE_SEC:
            lane.queued = ctx           # latest wins; the old queued ctx is dropped and counted
            return "queued"
        # wedged: replace the pool, keep a reference to the old future in `wedged`
        ... as today's _run_with_timeout: abandoned += 1, wedged list pruned, pause if >= MAX_WEDGED_WORKERS
    submit _job(ctx) on lane.pool; lane.inflight = (ctx, future); return "started"
```

The job, on the lane's MTA worker thread (own UIA objects via thread-local, exactly as
`_get_uia_on_worker` today):

```
def _job(ctx):
    started = now()
    root = uia.ElementFromHandle(ctx.hwnd)                  # "no-window" if it fails
    tab_before = selected_tab_id_here(root)                 # tab strip cached per thread per hwnd
    docs, url_before = find_documents_with_urls(root)       # 4.5: one FindAllBuildCache, cache = {IsOffscreen, Value}
    if not url_before: url_before = address_bar_here(root)  # today's extract_browser_url, moved here
    in_scope_docs = [d for d in docs if scope.is_in_scope_url(d.url)]   # the privacy gate, per document
    lines, nodes = [], []
    for d in in_scope_docs:
        n = read_cached_subtree(d)                          # uia_nodes.build_cache_request + _walk
        nodes.append(n); lines.extend(uia_nodes.lines_from_nodes([n], include_selection=True))
    tab_after = selected_tab_id_here(root)
    url_after = document_url_here(root) or address_bar_here(root)
    return ReadResult(ctx, "uia", lines, nodes, [d.url for d in in_scope_docs], tab_before, tab_after,
                      url_before, url_after, started, now(), "")
```

When the job finishes, the future's callback appends the result to `self._done` (a deque with a
lock) and, if `lane.queued` is set, submits it at once. The capture thread never waits on a future.

Edge cases the implementer must handle:

- A lane's window closes: `ElementFromHandle` fails, result has `error="no-window"`; the loop
  tells SGT `window_gone(hwnd)` (section 6) and closes the lane.
- A result for an abandoned (wedged) read that finally returns: delivered with
  `error="timeout-abandoned"` so stats count it; SGT drops it (it may be from a replaced worker
  whose window state is unknown).
- Pause: as today, `MAX_WEDGED_WORKERS` stuck at once pauses UIA for `PAUSE_SEC`; the loop keeps
  the OCR lane running during a pause, so layer 1 degrades to OCR instead of nothing.
- `request()` called for a window whose lane is paused is counted in stats as `paused`.
- The old `vsdc_uia_text.read_page_text` and `selected_tab_id` stay for the tripwire and tests;
  they are rebuilt on top of one shared helper module so the two readers cannot drift.

### 4.4 Frame signature and the change gate

```python
# core/vsdc/vsdc_gate.py

GATE_SIZE = 32                   # cells per side
GATE_LEVELS = 16                 # grey levels per cell
GATE_MIN_CELLS = 3               # cells that must change for a UIA read (D4)
FLASH_MIN_CELLS = 8              # cells that must change for an OCR flash read (D4)
GATE_LEVEL_DELTA = 2             # a cell "changed" when its level moved by at least this

def frame_signature(img) -> Optional[bytes]:
    """img.convert('L').resize((GATE_SIZE, GATE_SIZE), Image.BOX) then each pixel // (256 // GATE_LEVELS).
    1024 bytes. None when img is None or conversion fails."""

def cells_changed(a: Optional[bytes], b: Optional[bytes]) -> int:
    """Count of positions where abs(a[i] - b[i]) >= GATE_LEVEL_DELTA. 1024 when either is None."""
```

Why it works: on a 1600x900 client area a cell is 50x28 px. A blinking caret (1x14 px) or one
typed character (about 8x14 px) moves a cell's average by under one level. A success toast
(about 300x60 px) covers 12 cells; a banner, a modal, a table row inserted (everything below it
shifts) cover far more. Numbers to confirm in WP0 (section 12).

The gate decision, per watched window and tab, in the loop (not in SGT, which only sees contexts
and results):

```
read_now = url_changed
        or sig is None or prev_sig is None
        or cells_changed(prev_sig, sig) >= GATE_MIN_CELLS
        or now - last_read_finished >= REREAD_AFTER_SEC        # 5 s, down from 15 (D5)
        or sgt.wants_read(hwnd, tab)                           # SGT-I / SCC-U advisory, as today
spacing_ok = now - last_read_finished >= max(MIN_READ_GAP_SEC, READ_GAP_FACTOR * last_read_ms / 1000)
if read_now and spacing_ok and reader.inflight(hwnd) is None: request
elif read_now: mark wanted; request when the inflight read's result arrives (the lane coalesces anyway)
```

```python
REREAD_AFTER_SEC = 5.0
MIN_READ_GAP_SEC = 0.35
READ_GAP_FACTOR = 1.0            # a 500 ms page is read at most every ~1 s: 50 % duty per lane (D5)
BURST_READS = 3                  # after a URL change: 3 reads 300 ms apart regardless of the gate
```

Burst after a URL change replaces today's 15 ms burst ticks (which only made sense for the
crosshair OCR engine).

### 4.5 The URL: Document first, address bar second, title third

Chromium and Gecko expose the page URL as the `Value` property of the Document (root web area)
element. This is to be confirmed on Windows in WP0 (it is a single property read, the same
privacy footprint as the address bar). If confirmed:

- The worker's first call is `FindAllBuildCache(Descendants, ControlType=Document, cache={IsOffscreen, Value})`.
  No page content is fetched by that call. Each on-screen Document's URL goes through
  `vsdc_scope.is_in_scope_url`. Only in-scope Documents are read. Edge sidebar, Copilot,
  translate bars and extension panes are separate Documents with out-of-scope URLs and are never
  read (D14). A PDF viewer Document whose URL is on the portal host is in scope and is read
  (C4, if the viewer exposes text; WP0 checks).
- The address bar is read only when no Document gives a URL (an app window, a page still
  loading). It is read on the worker with the lane's own UIA object and inside the job's
  timeout, so W5 is gone.
- The title grace (`_SCOPE_GRACE_SEC = 8 s`) stays as the last fallback, unchanged, so the scope
  rule is never looser than today. D12 (full screen, kiosk, popups) is closed by the Document URL,
  not by stretching the grace.

If WP0 finds the Document Value is not the URL in some browser, that browser keeps the address
bar as its first source and the design still holds; only D12 stays partial there.

### 4.6 Torn reads

A result is torn when `tab_after != tab_before` ("tab") or, with the same tab, when
`url_after != url_before` ("url"). Rules in SGT (section 6.3): a "tab" torn result is never
absorbed and the loop re-requests at once (`reason="torn-retry"`, at most `TORN_RETRIES = 3` per
page visit); a "url" torn result is absorbed for its page text only (link-sourced hits removed,
no piece misses counted). A modal opening mid-read (B4) is not detectable and is handled by the
ARN key and the next read; the harness has a case for it.

### 4.7 Visible windows (D6)

```python
# core/vsdc/vsdc_windows.py

MAX_WATCHED_WINDOWS = 3
BG_TICK_SEC = 1.0                # a window not in front is considered this often

def browser_windows(foreground: int) -> List[WindowInfo]:
    """Top-level windows whose process exe is in BROWSER_EXE_NAMES, IsWindowVisible and not
    IsIconic, with a non-empty title. The foreground window first, then the others by z-order.
    Capped at MAX_WATCHED_WINDOWS. A window whose rectangle is entirely inside the foreground
    window's rectangle on the same monitor is left out (it is covered; Chromium builds no tree
    for it anyway, measured 2026-10-04)."""
```

The loop treats every returned window exactly like the foreground one today (its own
`_WindowSession`, its own scope gate, its own lane), except that a background window is only
considered every `BG_TICK_SEC`, gets `ctx.background=True`, and is screenshotted with the
PrintWindow fallback in `VSDCOcrEngine.capture_window_image` (already there). `BROWSER_EXE_NAMES`
is not changed (owner rejected D11, 2026-10-14).

### 4.8 The loop, step by step (after)

```
every FRAME_TICK_SEC (0.2 s):                                      # D7
  results = reader.results(); for r in results: sgt.absorb(r); health.read_result(r)
  for row in sgt.pending rows: hand one to the worker's filing_captured (as today, one per tick)
  windows = browser_windows(GetForegroundWindow())                 # cheap Win32
  for w in windows (background ones only when their BG_TICK is due):
      ws = window_state[w.hwnd]                                    # replaces _WindowSession for SGT
      img = screenshot(w.hwnd); sig = frame_signature(img)
      scope = scope_gate(ws, w)   # last known Document/address URL from the lane's last result,
                                  # else title grace; a window never verified is not read
      health.window(w, scope)
      if not scope.ok: continue
      sgt.observe_window(w.hwnd, ws.tab, scope.portal, scope.url, w.title)   # session bookkeeping only
      decide read (4.4) -> reader.request(ReadContext(...))
      decide OCR flash (5.2) -> ocr_lane.request(img, ctx)
  health.tick()
  if health.due(): health.write(...)
```

The scope gate before the first read of a window: the loop has no URL yet. It asks the reader
for a "URL-only" job (`reason="scope"`, documents' Value only, no content). This replaces today's
"address bar then read" order and keeps the rule that no page content is read before the host is
known. The URL-only job takes a few milliseconds.

---

## 5. The Submission Net (OCR) and the frame watch

### 5.1 When OCR runs

| Trigger | Condition | Why |
| :--- | :--- | :--- |
| Blind | A UIA result for this page has fewer than `MIN_UIA_LINES` real lines after `BLIND_RETRIES` (2) immediate re-reads 300 ms apart | canvas pages (F7), policy-disabled accessibility (G9), Chromium's first partial read after idle (G3) |
| Flash | `cells_changed >= FLASH_MIN_CELLS` and (the window's lane is inflight, or the last UIA read of this page took more than `FLASH_UIA_SLOW_MS` (700), or UIA is paused) | a toast or banner on a heavy or busy page (C1 to C3, A7, H2) |
| Green | `vsdc247.find_green_boxes` finds a success-coloured box that was not there in the previous frame | the portals' success banners (reused scoring, measured 9 ms) |
| Paused | UIA paused (section 4.3): every frame change over `FLASH_MIN_CELLS` | layer 1 survives a pause |

OCR never runs for a window outside the scope gate (same rule as today). OCR runs at most once per
`OCR_MIN_GAP_SEC` (1.0) per window, on the OCR lane, with `region_type="full"`. Cost measured on
the dev PC: 45 to 100 ms per scan.

### 5.2 Merge rules for OCR (the lesson behind the old GST ban, as code)

A `ReadResult` with `source="ocr"` goes through the same resolver and the same sessions, under
`SOURCE_POLICY["ocr"]`:

```python
# core/sgt/sgt_shadow.py
SOURCE_POLICY = {
    "uia": {"profile": "all", "pieces": "set_change_clear", "datasets": "merge_follow", "identity_check": True},
    "ocr": {"profile": "none", "pieces": "fill_empty_only", "datasets": "merge_add_only", "identity_check": False},
}
```

- **profile "none"**: OCR never sets any profile field. In particular it never sets PAN or GSTIN,
  so an OCR misread can never name or split a session (constraint 5).
- **pieces "fill_empty_only"**: OCR may set a piece of the dataset in progress only when no piece
  exists for that field. It never changes a piece and never counts a miss, so nothing OCR reads
  or fails to read can clear what UIA set (the `Period •` case).
- **datasets "merge_add_only"**: `_merge_values(..., follow=False)`: a new slot may be created
  (an ARN seen only by OCR), a status may climb, a missing field may be filled. No correction, no
  demotion request, no `owner` change. The slot gets `evidence_source="ocr"` until a UIA read or
  a list page shows the same ARN (then `"uia"` or `"recovered"`). The tracker row carries it in
  `raw_payload.source.evidence_source`.
- **identity_check False**: `_identity_conflict` is not run on OCR results. `_other_client` still
  is: a card whose PAN is readable and is not the session's client is not attributed (conservative
  direction).
- OCR results are recorded in the corpus with `source="ocr"` as today, so replay covers them.

### 5.3 The frame watch

The loop runs every `FRAME_TICK_SEC = 0.2` s instead of 0.35 s. Each frame tick costs one
screenshot (BitBlt, about 2 ms for the foreground window) and one signature (resize to 32x32,
a few ms). UIA reads stay bounded by section 4.4's spacing. Owner decision D7 sets the tick; WP0
measures the real cost of a frame tick so the owner decides with numbers.

---

## 6. Sessions, identity and recovery

### 6.1 Provenance when absorbing

`SgtShadow` gains two entry points and keeps `observe()` for replay and tests:

```python
def observe_window(self, hwnd: int, tab: Optional[str], portal: str, url: str, title: str) -> _Session:
    """Session bookkeeping for the window in view (tab switch, portal switch, login/logout keywords,
    idle, parked resume, claim tab) - today's _observe up to and including _claim_tab. No read."""

def absorb(self, result: ReadResult) -> Optional[PageResult]:
    """Resolve and merge one read. The session is the one that owned (hwnd, tab) when the read was
    asked for: self._owners[result.ctx.seq] -> session_id, set by request_made(). Found among open
    sessions, the shelf, or the closed ring. Then today's _observe from `lines = ...` on, with the
    SOURCE_POLICY of result.source and the torn rules."""

def request_made(self, ctx: ReadContext, s: _Session) -> None:
    """Remembers which session asked. Entries older than RESULT_MAX_AGE_SEC are dropped."""
```

Closed ring: `self._closed: OrderedDict[str, Tuple[float, _Session]]` holds the last
`CLOSED_RING = 20` ended sessions for `CLOSED_KEEP_SEC = 300` s. A result whose session has
ended is absorbed into that session object; any new or changed slot is queued as usual (the
session's client is still there; the read was requested while the session was live and the
result's `tab_after == tab_before`). Logged as `late_absorbed_after_end`.

Drop rules (logged, counted in health): `error != ""`; `now - finished_at > RESULT_MAX_AGE_SEC`;
`torn == "tab"`; owner unknown (seq not remembered, which means the process restarted).

### 6.2 What a result changes in the session

Everything from today's `_observe` after the read, unchanged, with these additions:

- `s.last_read` is set from `result.finished_at`, `s.read_ms` from `result.ms`.
- Blind handling: `is_blind(lines)` on a page that read fine before (`s.last_good_lines > MIN_UIA_LINES`
  for the same URL) does not count as blind yet; the loop re-requests up to `BLIND_RETRIES` with
  `reason="blind-retry"`; only then the page is blind and the OCR trigger fires (any portal).
  `NO_OCR_PORTALS` is deleted.
- `docs` (nodes) replace `_nodes_for_recording`: the recorder and SGT-I get them from the result.

### 6.3 Torn rules

- `torn == "tab"`: dropped; the loop re-requests at once; counted.
- `torn == "url"`: absorbed with `res.current` and `res.profile` entries whose `source == "link"`
  removed, `is_list` kept, and the miss counter of pieces owned by `url_before` not advanced for
  this read. The page visit counter is advanced by the loop on the next context with the new URL,
  as today.

### 6.4 Identity persistence (W9)

- `_parked` is written into `sessions_state.json` under `"parked"` and restored by `_recover`
  (entries older than `RESUME_WINDOW_SEC` dropped). A parked snapshot holds PAN/GSTIN on disk
  like the open-session snapshot already does.
- `SgtShadow.window_gone(hwnd)` ends every session of the window without parking (window really
  closed: `IsWindow` false). `SgtShadow.window_idle(hwnd)` parks sessions with a client and ends
  the rest. The router prune calls `window_idle` when `IsWindow` is still true (the user just
  worked elsewhere for 2 h) and `window_gone` otherwise.
- Resume by client (D4, D5): a session that has just been named by its first page (PAN or GSTIN
  read from the page itself) adopts the draft pieces, timeline and `confirm_note` of a parked
  session of the same client from any window, if that session idled less than
  `RESUME_WINDOW_SEC` ago. Identity is never borrowed: the page named the client first.
- Idle and parking maths use `time.monotonic()` through the injected clock; wall-clock time is
  used only for timestamps in logs and rows (H8).

### 6.5 Recovery (layer 3)

- `latest_period_only` keeps every card that has its own `arn`; it only thins cards without an
  identifier. A filed-returns list of five years yields five rows, each keyed by its ARN, each at
  the status the ladder derives (GST: an ARN proves Submitted & Verified; ITR: the card's status
  wording decides, else Submitted (Not Verified)).
- A slot first created from a record spec with `start` (a list page) is marked
  `evidence_source="recovered"`; a slot that later gains UIA confirmation from a confirmation
  page keeps the earlier mark in `raw_payload.source.first_seen_as`. The tracker's row detail
  shows it (one label, no new column).
- The GST "Track Return Status" page and the ITR "View Filed Returns" page get record specs in
  `sgt_fields.json` (config, not code). The implementer takes the label wording from the corpus
  on the owner's PC (`tools/sgt_replay.py show` and the corpus files), never from memory; the
  blueprint names the fields: form, period, arn, status, filing date. WP11 includes the exact
  procedure and the owner step.
- Downloads watching (C5) is decision D9, specified as WP15 and built only if approved.

---

## 7. Health, alerts, fleet and deployment

### 7.1 The health record

```python
# core/vsdc/vsdc_health.py

STATES = ("ok", "no-portal", "slow", "busy-long", "blind", "uia-paused", "stalled", "scope-lost",
          "specs-silent", "unlisted-portal", "disk", "alerts-unconfigured", "old-build")
BLIND_ALERT_SEC = 20.0          # in-scope page, no usable text for this long (D8)
SLOW_ALERT_SEC = 60.0           # reads over PAGE_TIMEOUT_SEC for this long
SPECS_SILENT_SEC = 300.0        # a session on logged-in pages with zero profile and zero dataset hits for this long
HEALTH_WRITE_SEC = 60.0
HUD_REPEAT_SEC = 300.0          # a persisting failure is shown again at most this often (constraint 8)
PHONE_REPEAT_SEC = 1800.0

@dataclass
class CaptureHealth:
    device: str                 # device_name()
    version: str                # version.APP_VERSION
    at: str                     # ISO time of the record
    state: str
    since: str                  # ISO time the state began
    detail: str                 # short, no client data, no links outside the portals
    reads: int; blind: int; busy: int; late: int; abandoned: int; stuck: int
    ocr_reads: int; ocr_only_rows: int; unattributed_rows: int; rows_sent: int; rows_dead_letter: int
    blind_sec_today: int; stalled_sec_today: int
    lanes: int; mem_ws_mb: int
    pending_update: str         # staged version or ""
    alerts_configured: bool; scope_extras: int; spec_override: str   # sha1[:8] of the override file or ""

class HealthMonitor:
    def __init__(self, clock=time.monotonic, hud=None, phone=None, write=None, echo=print): ...
    def window(self, hwnd, scope_ok: bool, portal: str) -> None
    def requested(self, ctx) -> None
    def read_result(self, r: ReadResult, usable: bool) -> None
    def row(self, kind: str) -> None         # "sent" | "dead_letter" | "unattributed" | "ocr_only"
    def stall(self, seconds: float) -> None  # from vsdc_diag.watch_worker
    def tick(self) -> None                   # evaluates the state machine
    def due(self) -> bool
    def write(self, extra: Dict[str, Any]) -> CaptureHealth   # calls `write` (settings key), returns the record
```

State machine, evaluated every tick, highest priority first:

```
stalled        no tick for STALL_S (from the watch thread)
uia-paused     reader.stats()["paused"]
blind          an in-scope window has had no usable text (uia or ocr) for BLIND_ALERT_SEC
scope-lost     an in-scope-looking window (portal title) has had no readable URL for SCOPE_LOST_SEC (30)
busy-long      a lane has been inflight for over PAGE_TIMEOUT_SEC continuously for SLOW_ALERT_SEC
slow           last_ms > PAGE_TIMEOUT_SEC * 1000
specs-silent   SPECS_SILENT_SEC of reads on a session with no hits at all
unlisted-portal the tripwire fired this run
disk           a snapshot, log or dead-letter write failed in the last 10 min
alerts-unconfigured  AlertSender.enabled is False
old-build      pending_update != ""
no-portal      no in-scope window in the last 10 min (informational, never alerted)
ok
```

Entering any state other than `ok`, `no-portal`, `slow`, `old-build` raises: one HUD event
(`"prompt"`, title "Capture paused on this PC", subtitle = the state's plain sentence, 4 s), one
phone alert through a new `AlertSender.notify_capture_health(device, state, detail)` (same
privacy rules as `notify_unattributed_submission`), and a `vsdc_diag.note`. Leaving the state
raises one HUD event "Capture back to normal". While it persists, the HUD repeats at most every
`HUD_REPEAT_SEC`, the phone at most every `PHONE_REPEAT_SEC`. The old `_disabled_reason` message
and the heartbeat line stay.

### 7.2 Office-wide view through Sera Sync

The record is written once a minute as an `app_settings` row: key `capture_health:<device_id>`,
value = the JSON of `CaptureHealth` (under 1 KB). `app_settings` is already replicated LWW by key
(`sync_schema.py:140`), so every PC sees every PC's record with no schema change (D10; the
alternative, a dedicated replicated table, is listed there). Rules: the value holds no client
data (checked by a test that scans it for PAN, GSTIN and ARN shapes); the write goes through the
main thread's capture queue like a row, never from the capture thread directly.

Settings → Tracker gains a "Capture health" panel (`ui/dialogs/unified_settings_dialog.py`, next
to the SGT mode combo): one line per PC: device, build, state, since, last record age, blind
minutes today, rows today. Colours: green `ok`/`no-portal` and record under 3 min old; amber any
other state; red record older than 10 min while the PC's last state was not `no-portal` (a PC
that is simply off shows grey "off since"). A "Copy report" button copies the table as text.

### 7.3 Daily digest (layer 4)

Once a day on the admin PC (the PC where `sync_admin` is active; else every PC for its own rows),
`core/sgt/sgt_digest.py` writes one `digest` event into the SGT log and shows one HUD event
"Capture digest ready" with counts: PCs with blind or stalled minutes, rows unattributed, rows
`ocr_only`, ITR rows still "Submitted (Not Verified)" older than `DIGEST_UNVERIFIED_DAYS` (7),
PCs on an old build, PCs with alerts unconfigured. The full text is in the "Capture health"
panel ("Yesterday's digest"). No client values in the HUD text; the panel shows them because it
runs inside the unlocked app.

### 7.4 Deployment (I1)

- `build_tools/updater/sera_update_agent.ps1` copies `mandatory` from `version.json` into
  `pending.json`. `update_agent.should_close_now` uses `LONG_WAIT_IDLE_SECONDS` (30 s) at once when
  `pending.json` has `mandatory: true`, still never while `captures_busy`, and never while
  `sessions_open` (a new argument: SGT has a session with content touched in the last
  `SESSION_QUIET_SEC = 600` s). A mandatory fix therefore lands at the first quiet minute.
- One HUD event when an update is staged: "Update vX ready, Sera restarts itself when idle"
  (brief, once per version).
- The health record carries `version` and `pending_update`, so the office sees who is behind.

---

## 8. Test plan

### 8.1 Three kinds of tests, all required

1. **Deterministic simulation** (`tests/sgt_sim/`, no Windows): a fake browser with windows, tabs,
   pages, toasts with lifetimes, per-page read latency, wedges, partial trees and torn switches,
   driven by a virtual clock; the real `PageReader` (with a fake backend), the real `CaptureLoop`
   (with a fake `Platform`), the real `SgtShadow`. Each section 5J profile is a script. Assertions
   count missed and mis-attributed submissions. Runs in accelerated time by default; a `--real`
   marker runs selected profiles with real sleeps.
2. **Replay** on the corpus and the golden sessions (`tests/test_sgt_replay.py`,
   `tools/sgt_replay.py diff`), unchanged and mandatory for every WP.
3. **Live soak** on a Windows PC (`tools/sgt_soak_report.py`): the owner runs 5 h of B2B-like work
   (or the scripted mock pages in `tools/sgt_i_uia_node_bench.py` extended with a toast and a
   growing table); the tool reads `vsdc.log`, the SGT log and `memory.log` and prints pass/fail.

### 8.2 Acceptance tests per layer (measurable)

| Layer | Test | Pass |
| :--- | :--- | :--- |
| 1 | `tests/test_sgt_sim_profiles.py::test_profile_<n>_zero_missed` for profiles 1 to 6, accelerated | every scripted submission has a tracker row with the right client and status within 10 s of its evidence being on screen; 0 wrong-client rows |
| 1 | the same with `--real` for profiles 1 and 2 (slow, nightly) | same |
| 1 | live soak | 0 missed scripted submissions; p95 read time at hour 5 under 2x hour 1; working set growth under 100 MB; 0 stalls; 0 abandoned workers |
| 2 | `tests/test_sgt_sim_health.py::test_<state>_is_reported_within_20s` for blind, wedged, paused, stalled, scope-lost, old-build, disk, specs-silent | state entered within 20 s virtual; one HUD event; one phone alert queued; settings key written at the next minute |
| 2 | `tests/test_vsdc_health.py::test_record_holds_no_client_data` | regex scan of the JSON finds no PAN/GSTIN/ARN shapes |
| 3 | `tests/test_sgt_sim_recovery.py::test_missed_toast_is_recovered_from_filed_list` | a submission missed on purpose (OCR off, toast shorter than the read) appears when the list page is shown, marked recovered |
| 3 | `tests/test_sgt_replay.py::test_golden_session[itr_filed_returns_history]` (new golden) | five cards, five rows |
| 4 | `tests/test_sgt_digest.py::test_digest_lists_every_category` | fixtures with one item per category produce one line per category |

### 8.3 Numbers that must be measured on Windows before the thresholds are final

See section 12. The constants carry the recommended defaults; WP0 produces the measurements; the
owner confirms or adjusts them in decision D4/D5/D7/D8.

---

## 9. Work packages

Common rules for every WP (the agent reads these first):

- Branch and process as `docs/sgt-overhaul-runner.md` prescribes for a WP session (one WP, one
  session, hand-off note, commit). Never push to `main`. Do not touch `package_dist/` or
  `version.json`.
- Constraints 1 to 12 of the report hold. Nothing clicks, types, scrolls or focuses. No AI. No
  page text in logs. The corpus stays off the repo. Fictional test data only.
- Every WP passes `tests/test_sgt_replay.py` and the full suite with the live plugin
  (`CLAUDE.md`), and keeps every test named in `tests/test_sgt_*.py` and `tests/test_vsdc_diag.py`
  green unless the WP says which test changes and why.
- Plain language in comments and docs; match the surrounding code's comment density.
- Model tier: **Sonnet** unless marked **Haiku**. Where marked "Opus if available" the owner may
  prefer Opus; the spec is written so Sonnet can build it.
- Every WP ends at its STOP: report what was built, what was measured, and the next WP's id.

Dependency graph:

```
WP0 --> WP3 --> WP4 --> WP5 --> WP6 --> WP7, WP8, WP10
WP1 --> WP12
WP2 (independent)
WP5 --> WP9, WP11
WP6 --> WP13 (after WP7..WP12)
WP13 --> WP14
WP15: NOT BUILT (owner rejected D9 on 2026-10-14)
```

Owner answers to the decisions are in section 10, "Owner answers", and the review corrections
R1-R13 are in section 10a. Read both before starting any WP: they override the WP text.

### WP0. Probe pack: the measurements only a Windows PC can take

**Tier:** Sonnet to write the tool; the owner (or a local agent) runs it. **Depends on:** none.

**Build** `tools/sgt_probe_pack.py`, read-only, standalone like `tools/sgt_i_key_probe.py`, with
subcommands:

- `doc-url`: for every top-level browser window, list each Document element's cached `Value`
  (property 30045), `IsOffscreen`, framework id, and whether `vsdc_scope.is_in_scope_url` accepts
  it; print beside it what `VSDCRouter.extract_browser_url` returns. Run with Edge, Chrome and
  Firefox open on a portal page, in a normal window, in F11 full screen, and with a PDF open in the
  browser viewer (also print the first 20 lines of the PDF Document's cached subtree, redacted
  with `vsdc_alerts.redact_identifiers`).
- `read-timing --minutes N`: every 30 s, read the foreground portal page with
  `vsdc_uia_text.read_page_text` and `core/sgt_i/uia_nodes.read_page_nodes`; print ms, element
  count, line count, browser working set (from `psutil` if present, else `GetProcessMemoryInfo`),
  and whether the lines are identical. Run on a real GSTR-1 B2B page with hundreds of rows, fresh
  and after hours.
- `read-timing --mta`: the same with the worker initialised as MTA (`CoInitializeEx(COINIT_MULTITHREADED)`),
  to compare drift and hangs over the same hours.
- `gate --minutes N`: screenshot + `frame_signature` every 200 ms during invoice typing; print
  per minute: frames, ms per frame (screenshot, signature), cells changed histogram, how many
  frames would pass `GATE_MIN_CELLS = 3` and `FLASH_MIN_CELLS = 8`, and how often a 32x32 `hash()`
  (today's gate) would have fired.
- `toast`: run during a real filing (read-only): every 100 ms record tab, URL, line count and
  whether an ARN-shaped token appears in the UIA lines and in an OCR of the frame; print how long
  the ARN was visible by each channel. Also run `core/sgt_i/uia_events.FlashWatcher` beside it and
  print what it caught.
- `switches --minutes N`: count foreground window changes and tab-id changes per minute.

The tool writes `~/AmanAssociates_Sera/logs/probe_pack_<date>.txt` with numbers, counts and
redacted samples only.

**Tests:** `tests/test_sgt_probe_pack.py::test_report_lines_hold_no_identifiers` (feed fake lines
with a PAN, GSTIN and ARN; the report redacts them); `::test_gate_counts_from_fake_frames`.

**Done when** the tool runs on the dev PC and the report file holds every section.

**STOP (owner):** run all six probes; fill the table in section 12; answer D1 (Document URL),
D2 (MTA), D4, D5, D7 from the numbers. WP3 starts after that.

### WP1. Capture health: states, HUD event, phone alert, log

**Tier:** Sonnet. **Depends on:** none (built on today's loop; WP4 re-wires it).

**Files:** new `core/vsdc/vsdc_health.py` (section 7.1 as written); `core/vsdc/vsdc_alerts.py`
(`AlertSender.notify_capture_health(device, state, detail) -> bool`, same rate limit and redaction
as `notify_unattributed_submission`, title "Sera: capture problem on <device>"); `core/vsdc/vsdc_diag.py`
(`watch_worker` calls `monitor.stall(seconds)` and `Heartbeat.write` includes `health_state`);
`core/vsdc/vsdc_router.py` (`self.health = HealthMonitor(...)`; `evaluate_tick` calls
`health.window(hwnd, in_scope, portal)` after the scope gate, `health.read_result(...)` is fed from
`SgtShadow` through a new `on_read` callback `(usable: bool, ms: float, source: str)`, and
`health.tick()` at the end of each tick; HUD through `self.notify_activity("prompt", ...)`);
`core/sgt/sgt_shadow.py` (call `on_read` after each read; `take_diag` unchanged).

**Signatures and constants:** as section 7.1. `write` is a callable set by `main.py` in WP12; in
WP1 it is `None` and `write()` only returns the record and logs one `vsdc_diag.note("health ...")`
line per minute (no client data).

**Pseudocode of `tick()`:**

```
now = clock()
for each state in priority order: if its condition holds: new = state; break
if new != self.state:
    leaving ok -> raise(new); entering ok from a failure -> hud("Capture back to normal")
    self.state, self.since = new, now
elif new not ok and now - last_hud >= HUD_REPEAT_SEC: hud again; (phone: PHONE_REPEAT_SEC)
accumulate blind_sec_today / stalled_sec_today while in those states; reset at local midnight
```

"Blind" condition: `window()` was called with `scope_ok=True` within the last 5 s and the last
usable read (uia or ocr with >= MIN_UIA_LINES lines) for that window is older than
`BLIND_ALERT_SEC`, and at least one read was attempted in that time.

**Edge cases:** the HUD pill may be disabled (`read_hud_enabled`): still log and phone. Alerts
unconfigured: `alerts-unconfigured` state is informational (no phone possible) and shown in the
HUD once per run. Day rollover resets the today counters. The monitor never raises.

**Tests** (`tests/test_vsdc_health.py`): `test_blind_in_scope_raises_within_20s`,
`test_blind_clears_on_a_usable_read`, `test_a_persisting_failure_repeats_hud_every_5_min_only`,
`test_phone_alert_rate_limited`, `test_stall_from_the_watch_thread_is_a_state`,
`test_no_portal_is_never_alerted`, `test_record_holds_no_client_data`,
`test_priority_stalled_over_blind`, `test_counters_reset_at_midnight`.

**Done when** the tests pass and `vsdc.log` on the dev PC shows `health state=...` lines.

**STOP (owner):** confirm the HUD wording and D8 (20 s).

### WP2. Rows never lost: database retry and dead letter

**Tier:** Sonnet. **Depends on:** none.

**Files:** `main.py` (`_capture_worker` loop around line 829, `_process_extension_result`);
`database.py` (`_connect` for master and raw: add `PRAGMA busy_timeout = 5000;`); new
`core/capture_outbox.py`.

```python
# core/capture_outbox.py
DEAD_LETTER_NAME = "undelivered_rows.jsonl"      # ~/AmanAssociates_Sera/sgt_shadow/
RETRY_DELAYS_SEC = (0.5, 1, 2, 4, 8)             # then dead letter

class DeadLetter:
    def __init__(self, path: Path): ...
    def put(self, msg: dict, why: str) -> None     # append one JSON line {ts, why, msg}; never raises
    def take_all(self) -> List[dict]               # read and truncate (atomic rename to .replaying first)
    def count(self) -> int
```

**Behaviour:** `_capture_worker` catches `sqlite3.OperationalError` and `DatabaseError` from
`_process_extension_result`, retries with `RETRY_DELAYS_SEC`, then `DeadLetter.put`. At start-up
(after the DB is open) and every 10 minutes, `take_all()` re-queues the rows. Rows are idempotent
by `dataset_key`. `HealthMonitor.row("dead_letter")` is called on each put (state `disk` if the
put itself fails). `_flush_capture_queue_on_quit` writes anything still queued to the dead letter
instead of waiting past its timeout.

**Edge cases:** a row with `supersedes_dataset_key` must keep its order with the row it
supersedes (the dead letter preserves order). A row that fails for a non-transient reason
(`ValueError` from a malformed payload) goes to the dead letter with `why="malformed"` and is
not retried automatically; the digest lists the count.

**Tests** (`tests/test_capture_outbox.py`): `test_a_locked_database_is_retried_then_written`,
`test_after_retries_the_row_goes_to_the_dead_letter`, `test_dead_letter_is_replayed_at_startup`,
`test_order_of_superseding_rows_is_kept`, `test_quit_with_rows_queued_leaves_them_in_the_dead_letter`.

**Done when** a test holds a write lock on the raw DB for 3 s while a row is queued and the row
still lands.

**STOP (owner):** none needed; report the count of dead-letter rows after a day on the dev PC.

### WP3. Reader v2: MTA lanes, cached read, tickets, torn detection, Document URL

**Tier:** Sonnet (Opus if available). **Depends on:** WP0 (D1, D2 answered).

**Files:** new `core/vsdc/vsdc_reader.py` (section 4.2 and 4.3 in full); `core/vsdc/vsdc_uia_text.py`
(factor the helpers used by both readers into module-level functions that take `(uia, uia_client)`:
`selected_tab_id_here(uia, uia_client, root, cache)`, `address_bar_here(uia, uia_client, root)`
moved from `VSDCRouter.extract_browser_url` with its element cache, `documents_with_urls(uia, uia_client, root)`;
`read_page_text` and `selected_tab_id` keep their signatures and call the helpers on the old
pool); `core/sgt_i/uia_nodes.py` (`read_cached_subtree(uia, uia_client, element, raw_view=False)`
split out of `_read_docs`; `build_cache_request` gains a `properties` argument so the URL-only
request caches only `IsOffscreen` and `Value`); `core/vsdc/vsdc_diag.py` (`take_read_stats` reads
from `PageReader.stats()` when a reader is registered).

**Worker initialiser:** `comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)` if D2 says MTA
(default), else `CoInitialize()`. One line decides; both are tested by the fake backend.

**The backend seam** (so the reader is testable without Windows):

```python
class Backend(Protocol):
    def init_thread(self) -> None
    def root(self, hwnd: int) -> Any
    def tab_id(self, root) -> Optional[str]
    def documents(self, root) -> List[Tuple[Any, str, bool]]     # (element, url, offscreen)
    def address_bar(self, root) -> str
    def read_document(self, element) -> List[Dict[str, Any]]     # nodes
```

`UiaBackend` is the real one; `tests/sgt_sim/fake_backend.py` (WP6) implements it with latency
and wedges. WP3 ships a minimal fake inside its own tests.

**Torn detection** as section 4.6. **Scope gate inside the job:** only documents whose URL passes
`is_in_scope_url` are read; if none passes and the address bar is in scope, read every on-screen
document (a browser that exposes no Document URL, per D1). Record in the result which rule
applied (`scope_rule: "document" | "address_bar"`).

**Edge cases:** `ElementFromHandle` on a closed window; a Document whose `Value` is a
`chrome-extension://` or `about:` URL (out of scope: not read); Firefox exposes background tabs'
Documents (keep the `IsOffscreen` filter); a lane replaced while queued (`queued` is carried to
the new pool); `MAX_LANES` reached by background windows (foreground always gets a lane);
`close()` on shutdown leaves wedged threads daemon.

**Tests** (`tests/test_vsdc_reader.py`): `test_a_result_carries_its_context_back`,
`test_a_slow_read_never_blocks_another_windows_lane`, `test_a_queued_context_replaces_the_older_one`,
`test_a_wedged_lane_is_replaced_and_its_late_result_is_marked_abandoned`,
`test_five_wedged_workers_pause_then_resume`, `test_tab_change_during_read_is_torn_tab`,
`test_url_change_during_read_is_torn_url`, `test_only_in_scope_documents_are_read`,
`test_url_only_job_reads_no_content`, `test_address_bar_is_used_when_no_document_has_a_url`,
`test_lines_from_the_cached_read_equal_the_old_reader_on_the_fixture_tree` (reuse
`tools/sgt_lines_equivalence.py`'s FakeElement tree), `test_background_window_gets_no_lane_past_the_cap`.
Existing `tests/test_vsdc_diag.py` and `tests/test_sgt_tabs.py` reader tests stay green (the old
API is kept).

**Done when** the tests pass and `tools/sgt_lines_equivalence.py --corpus` reports identical
lines on the owner's corpus (the owner runs it; that is the 14.2 gate, now applied to the Core).

**STOP (owner):** run the equivalence tool on the real corpus and on three live pages; confirm
D1 and D2 as built.

### WP4. CaptureLoop: the SGT path out of the router, behind a Platform seam

**Tier:** Sonnet (Opus if available). **Depends on:** WP3.

**Files:** new `core/vsdc/vsdc_capture.py`; new `core/vsdc/vsdc_windows.py` (section 4.7, the
foreground-only version first: `browser_windows()` returns just the foreground window until WP10);
new `core/vsdc/vsdc_gate.py` (section 4.4); `core/vsdc/vsdc_router.py` (`evaluate_tick` calls
`self.capture.tick()` for the SGT path when `self._sgt_mode != SGT_OFF`; the crosshair and
VSDC247 code paths are left as they are, still foreground-only, still using the old helpers;
`_init_uia`, `extract_browser_url` and `_cached_address_elements` are deleted from the router
and the crosshair path takes the URL from the capture loop's last scope result);
`core/vsdc/vsdc_worker.py` (`check_interval_sec` default becomes `FRAME_TICK_SEC`; burst ticks
removed; the loop calls `router.evaluate_tick()` as before).

```python
# core/vsdc/vsdc_capture.py
FRAME_TICK_SEC = 0.2
class Platform(Protocol):
    def foreground(self) -> int
    def windows(self, foreground: int) -> List[WindowInfo]      # vsdc_windows.browser_windows
    def screenshot(self, hwnd: int) -> Any                       # VSDCOcrEngine.capture_window_image
    def is_window(self, hwnd: int) -> bool
    def now(self) -> float

@dataclass
class WindowState:
    hwnd: int; tab: Optional[str] = None; url: str = ""; url_source: str = ""; portal: str = ""
    scope_verified_at: float = 0.0; last_sig: Optional[bytes] = None
    last_read_finished: float = 0.0; last_read_ms: float = 0.0; burst_left: int = 0
    torn_retries: int = 0; blind_retries: int = 0; last_bg_tick: float = 0.0; last_seen: float = 0.0

class CaptureLoop:
    def __init__(self, platform: Platform, reader: PageReader, sgt: SgtShadow, ocr: VSDCOcrEngine,
                 health: HealthMonitor, scope=vsdc_scope, clock=time.monotonic): ...
    def tick(self) -> None                 # section 4.8
    def end_all(self, reason) -> None
    def diag(self) -> Dict[str, Any]       # for the heartbeat (replaces router.diag_tick for SGT)
```

**Scope gate per window** (`_scope(ws, w)`): the URL comes from the last `ReadResult` of that
window (`url_after`, source document or address bar) if it is younger than `URL_FRESH_SEC = 2.0`;
else a URL-only job is requested and this tick does not read; else the title grace as today
(`_SCOPE_GRACE_SEC = 8.0`, never refreshed by a title). Nothing else changes in the scope rule.

**Pseudocode** of the per-window part of `tick()` is section 4.8. Results are handed to
`sgt.absorb(r)` (WP5); until WP5 lands, WP4 adapts results to today's `observe()` through a thin
shim `_absorb_compat` that feeds `lines` to `observe(..., read_uia=lambda h: result)`; the shim is
deleted in WP5.

**Edge cases:** a window that disappears between `windows()` and `screenshot()`; the foreground is
not a browser (nothing is read, sessions age as today); the SGT mode is switched off mid-tick
(`end_all`); a lane says `paused` (health state; OCR triggers still evaluated).

**Tests** (`tests/test_vsdc_capture.py`, with a fake platform and the WP3 fake backend):
`test_no_content_is_read_before_the_url_is_known`, `test_title_grace_is_never_refreshed_by_a_title`,
`test_a_url_change_bursts_three_reads`, `test_a_still_page_costs_one_signature_per_tick`,
`test_typing_under_the_cell_threshold_does_not_read`, `test_a_toast_sized_change_reads`,
`test_reads_are_spaced_by_the_last_read_duration`, `test_periodic_reread_every_5s`,
`test_results_are_absorbed_on_the_next_tick`. The router tests in `tests/test_sgt_shadow.py`
(`test_shadow_observes_in_scope_ticks_and_never_changes_the_result`,
`test_out_of_scope_pages_are_never_observed`) are adapted to the Platform seam and kept.

**Done when** the tests pass and the dev PC runs a day with the new loop and the heartbeat shows
`reads`, `late=0` dropped reads.

**STOP (owner):** confirm D7 (frame tick) with WP0's gate numbers.

### WP5. SGT absorbs by provenance: late results, closed ring, torn and blind rules, source policy

**Tier:** Sonnet (Opus if available). **Depends on:** WP3, WP4.

**Files:** `core/sgt/sgt_shadow.py` (sections 6.1 to 6.3 and 5.2); `core/sgt/sgt_replay.py`
(replay builds `ReadResult`s from recorded pages and calls `absorb`, so replay exercises the new
path; `observe()` stays as a convenience that does `observe_window` + a synchronous `absorb`).

**Changes in detail:**

- Split `_observe` at the line `t0 = time.perf_counter()`: everything above is `observe_window`
  (returns the session), everything from `lines = ...` is `absorb`. `request_made(ctx, s)` stores
  `self._owners[ctx.seq] = (s.session_id, ctx.url, s.visit)`.
- `absorb(result)`: locate the session (open, shelf, closed ring by id); apply the drop rules;
  pick `policy = SOURCE_POLICY[result.source]`; apply the torn rules; then today's code with
  these conditionals: profile offers only if `policy["profile"] == "all"`; `_identity_conflict`
  only if `policy["identity_check"]`; `_update_draft` gets `policy["pieces"]`; `_merge_values`
  gets `follow = follow and policy["datasets"] == "merge_follow"`; a new slot from OCR gets
  `evidence_source = "ocr"`.
- `_Slot` gains `evidence_source: str = "uia"` and `first_seen_as: str = ""`; `_tracker_payload`
  puts both under `raw_payload["source"]`.
- `_finish` moves the session into `self._closed` instead of forgetting it; `_end_idle` and
  `end_session` unchanged otherwise. `_closed` is pruned on every `absorb` and `observe_window`.
- `NO_OCR_PORTALS` removed; `ocr_skips` kept at 0 for log compatibility and removed from the
  `session_end` stats line.
- `read_ms` and `last_read` from the result's times.
- Blind retries: `absorb` returns `"blind-retry"` when the lines are blind and the page read fine
  before (`s.good_lines_by_url[url] > MIN_UIA_LINES`) and `ws.blind_retries < BLIND_RETRIES`; the
  loop re-requests.

**Edge cases:** a result for a session that was shelved by a tab switch after the request (the
shelf is searched by id, the result is absorbed there, the session stays on the shelf); a result
for a session that ended by logout before the result came (closed ring: absorbed, rows queued,
`late_absorbed_after_end` event); a result whose `ctx.url` differs from the session's `last_url`
now (the result's `url_before` is the page the text is about; pieces are owned by it); two
results of the same page in a row (second is a no-op except misses); an OCR result on a page
where UIA already read fine 100 ms earlier (merge_add_only makes it harmless).

**Tests** (`tests/test_sgt_absorb.py`): `test_a_late_result_lands_in_the_session_that_asked`,
`test_a_result_after_logout_still_writes_the_row_under_that_client`,
`test_a_result_for_a_shelved_session_does_not_touch_the_one_in_view`,
`test_torn_tab_results_are_dropped`, `test_torn_url_results_keep_page_text_but_not_link_hits`,
`test_torn_results_never_clear_a_piece`, `test_results_older_than_60s_are_dropped`,
`test_ocr_never_sets_pan_gstin_or_name`, `test_ocr_fills_only_an_empty_piece_and_never_clears`,
`test_ocr_period_marker_cannot_clear_a_uia_period` (the real `Period •` case),
`test_an_ocr_only_arn_makes_a_row_marked_ocr`, `test_uia_confirmation_upgrades_the_ocr_mark`,
`test_ocr_never_asks_for_a_demotion`, `test_blind_after_a_good_read_retries_before_ocr`,
`test_gst_blind_page_now_goes_to_ocr`. In `tests/test_sgt_gst_fixes.py`,
`test_a_blind_gst_page_is_never_sent_to_ocr` is replaced by `test_a_blind_gst_page_is_sent_to_ocr_add_only`
(the owner decided constraint 7). `test_sgt_counts_a_blind_gst_page_as_skipped` in
`tests/test_vsdc_diag.py` becomes `..._as_ocr_read`.

**Done when** the tests pass, every golden session replays identically, and `tools/sgt_replay.py diff`
on the owner's corpus shows only additions (OCR-only rows are new rows, never changed values).

**STOP (owner):** look at the replay diff; approve the OCR merge policy (D3).

### WP6. The simulation harness and the six day profiles

**Tier:** Sonnet. **Depends on:** WP4, WP5.

**Files:** new package `tests/sgt_sim/`: `fake_browser.py`, `fake_backend.py` (implements WP3's
`Backend`), `fake_platform.py` (implements WP4's `Platform`), `profiles.py`, `harness.py`; tests
`tests/test_sgt_sim_profiles.py`, `tests/test_sgt_sim_scenarios.py`.

```python
# tests/sgt_sim/fake_browser.py
@dataclass
class Page:
    url: str; title: str; lines: List[str]; portal: str
    read_ms: float = 40.0            # how long a UIA read of it takes
    wedge: bool = False              # a read never returns
    blind: bool = False              # UIA sees nothing (canvas)
    partial_first_read: bool = False # G3: first read after idle returns 2 lines
    nodes: Optional[list] = None

@dataclass
class Toast:
    lines: List[str]; lifetime_sec: float; cells: int = 12      # cells it changes in the frame

class Tab:  id: str; page: Page; toasts: List[Tuple[float, Toast]]
class Window: hwnd: int; tabs: List[Tab]; selected: int; visible: bool; foreground: bool; monitor: int
class Browser:
    clock: VirtualClock
    windows: List[Window]
    def show(self, hwnd, tab_id, page) ...; def toast(self, hwnd, toast) ...; def switch_tab(...) ...
    def alt_tab(self, hwnd) ...; def type_chars(self, n) ...   # changes 1 cell per char
    def frame(self, hwnd) -> bytes      # a synthetic signature: base + toast cells + typed cells
    def lines(self, hwnd) -> (lines, tab, url)   # what a UIA read returns at this instant
```

`VirtualClock.advance(sec)` moves time; the fake backend completes a read when
`started + page.read_ms` has passed, returning the page as it was at `started + read_ms / 2`
(so a toast shorter than the read may or may not be in it, deterministically). The harness runs
`CaptureLoop.tick()` every `FRAME_TICK_SEC` of virtual time and collects every tracker row the
SGT outbox produces, with the virtual time it was produced.

`profiles.py` scripts profiles 1 to 6 of the report's section 5J with fictional clients, including
every submission moment (the ARN line, its lifetime, what happens next) and the exact scenario ids
each step covers. Each profile returns the list of expected submissions
`(client, form, period, arn, status, visible_from, visible_until)`.

**Assertions** (`harness.assert_profile`): every expected submission has a row with that ARN,
client PAN/GSTIN and status at least the expected level, produced within `ASSERT_WITHIN_SEC = 10`
of `visible_until`; no row carries a different client's identity; no row for an ARN that was never
on screen.

**Scenario tests** (one per id, named `test_<id>_<short>`) for every Gap and Unknown row of the
report's section 5 that the harness can express: A1, A2, A3, A5 (read_ms grows with virtual time),
A6, A7, A10, B1 to B6, C1, C2, C3, C7, C8 (after WP10), D1, D2, D3, D4, D6, D13 (address bar typed:
Document URL wins), D14, E1, E5 (a B2B page with 200 recipient GSTINs in the header layout), E6,
E7, E9, F1, F3, F4, G1, G3, G4, H2, H3.

**Done when** profiles 1 to 6 pass with zero missed and zero mis-attributed, with D7's frame tick,
D4's thresholds and D5's spacing as defaults; and the `--real` run of profiles 1 and 2 passes in
under 20 minutes.

**STOP (owner):** none; this WP's report is the first number for layer 1.

### WP7. Change gate v2 tuning and the flash trigger wiring

**Tier:** Sonnet. **Depends on:** WP6.

**Files:** `core/vsdc/vsdc_gate.py`, `core/vsdc/vsdc_capture.py` (OCR triggers of section 5.1:
`_ocr_wanted(ws, sig_delta, inflight, last_ms)`), new `core/vsdc/vsdc_ocr_lane.py`:

```python
class OcrLane:
    def __init__(self, engine: VSDCOcrEngine, clock=time.monotonic): ...
    def request(self, img, ctx: ReadContext) -> bool      # False if within OCR_MIN_GAP_SEC for this hwnd
    def results(self) -> List[ReadResult]                 # source="ocr", docs=[], doc_urls=[]
OCR_MIN_GAP_SEC = 1.0
FLASH_UIA_SLOW_MS = 700
```

Green trigger: `core/vsdc/vsdc247.py`'s green-region detection (`find_green_boxes`) is imported (function only, no
engine state); if its signature is not importable cleanly, copy the two functions into
`vsdc_gate.py` with a comment naming the origin.

**Tests** (`tests/test_vsdc_gate.py`, `tests/test_vsdc_ocr_lane.py`):
`test_caret_and_typing_change_under_three_cells`, `test_a_toast_changes_eight_or_more_cells`,
`test_flash_ocr_fires_when_uia_is_inflight`, `test_flash_ocr_fires_when_uia_is_slow`,
`test_ocr_is_rate_limited_per_window`, `test_green_box_triggers_ocr_once`,
`test_ocr_runs_while_uia_is_paused`; harness: `test_c1_toast_one_second_on_heavy_page_is_caught_by_ocr`.

**Done when** profile 1 passes with `read_ms = 2000` for the B2B page and a 1 s ARN toast.

**STOP (owner):** confirm D4 thresholds against WP0's gate histogram.

### WP8. Blind pages on every portal, and the corpus marks

**Tier:** Sonnet. **Depends on:** WP5, WP6.

Small WP that finishes constraint 7's switch: delete the last `NO_OCR_PORTALS` references,
documentation (`docs/sgt-gst-fixes-plan.md` gets a dated note that WP4 of that plan is superseded
by this blueprint's section 5.2), `sgt_health.py` (`record_read` counts `ocr` and a new
`ocr_only_rows` per day for the digest), the HUD line "page unreadable by UI Automation - OCR is
off here" removed. Tests: `tests/test_sgt_reliability.py::test_a_portal_turning_blind_to_ui_automation_is_noticed`
kept; new `test_ocr_only_rows_are_counted_per_day`.

**STOP (owner):** none.

### WP9. Identity persistence

**Tier:** Sonnet. **Depends on:** WP5.

**Files:** `core/sgt/sgt_shadow.py` (section 6.4: `window_gone`, `window_idle`, parked entries in
the snapshot, resume by client, monotonic clock for idle maths); `core/vsdc/vsdc_router.py` or
`vsdc_capture.py` (prune calls `window_idle` when `is_window(hwnd)` else `window_gone`).

```python
def window_idle(self, hwnd: int) -> None
def window_gone(self, hwnd: int) -> None
def _park(self, hwnd: int, s: _Session, now: float) -> None       # extracted from _end_idle
def _resume_by_client(self, s: _Session, key: str, value: str, now: float) -> bool
```

Snapshot format: `{"saved": ..., "sessions": [...], "parked": [{"hwnd": h, "parked_at": t, "snap": {...}}]}`;
`_recover` restores `parked` entries younger than `RESUME_WINDOW_SEC` into `self._parked` and does
not finish them.

**Edge cases:** an hwnd reused by a new window after a restart (parked entries are matched by
portal and tab as today, and by client in `_resume_by_client`; a wrong hwnd match without a
client match cannot borrow identity because the resumed session's profile is only used after the
page itself named the client, or when the same hwnd+tab resumes within the window as today);
a parked session whose client is named by a page in another PC's session (irrelevant: parking is
per PC).

**Tests** (add to `tests/test_sgt_shadow.py` idle section and `tests/test_sgt_boundaries.py`):
`test_the_router_prune_parks_instead_of_ending_when_the_window_still_exists`,
`test_a_closed_window_ends_without_parking`, `test_parked_sessions_survive_a_restart`,
`test_expired_parked_sessions_are_not_restored`, `test_a_new_window_naming_the_same_client_resumes_its_draft`,
`test_a_new_window_naming_another_client_never_resumes`, `test_idle_maths_survive_a_wall_clock_jump`.
`test_closing_the_window_ends_every_session_of_it` keeps its meaning (window gone).

**Owner answer D13 (section 10):** this WP also sets `IDLE_END_SEC = 4 * 3600` and
`RESUME_WINDOW_SEC = 12 * 3600` in `sgt_shadow.py`, and `_SESSION_IDLE_TIMEOUT_SEC = 4 * 3600` in
`vsdc_router.py`. Add `test_router_prune_never_precedes_sgt_idle_end` (the router timeout is at
least `IDLE_END_SEC`).

**Done when** the tests pass. **STOP (owner):** confirm that parked identity on disk for up to 12 h
is acceptable (it already is for open sessions).

### WP10. Visible windows that are not in front

**Tier:** Sonnet (Opus if available). **Depends on:** WP4, WP6; decision D6.

**Files:** `core/vsdc/vsdc_windows.py` (full `browser_windows` of section 4.7, `EnumWindows`
through ctypes, `GetWindowRect`, `MonitorFromWindow`); `core/vsdc/vsdc_capture.py` (background
cadence, `ctx.background`); `core/vsdc/vsdc_ocr.py` (no change; PrintWindow fallback exists).
No `vsdc_scope.py` change: the owner rejected D11 (2026-10-14).

**Rules:** foreground first; background windows every `BG_TICK_SEC`; `MAX_WATCHED_WINDOWS = 3`;
a background window's lane may be closed by the reader when the cap is hit; a background window
is never read before its own URL passed the scope gate (same URL-only job); the health monitor
treats a blind background window as `blind` only if its Document URL is in scope and the window
is not covered.

**Tests** (`tests/test_vsdc_windows.py`, `tests/test_sgt_sim_scenarios.py`):
`test_covered_windows_are_left_out`, `test_foreground_is_always_first`, `test_cap_keeps_foreground`,
`test_c8_success_on_second_monitor_is_captured`, `test_b6_two_clients_two_windows_alternating`,
`test_d10_signing_dialog_in_front_still_reads_the_browser`, `test_an_out_of_scope_background_window_is_never_read`.

**Done when** profile 3 (parallel filing) passes with the success shown in a window that is not in
front. **STOP (owner):** confirm D6 after a day with it on (CPU in Task Manager, heartbeat lanes).

### WP11. Recovery from list and status pages

**Tier:** Sonnet, plus one owner step. **Depends on:** WP5.

**Files:** `core/sgt/sgt_resolver.py` (`latest_period_only` keeps cards with an `arn`);
`core/sgt/sgt_shadow.py` (`first_seen_as` / `evidence_source="recovered"` for slots born from a
`start` record; `_merge` passes `recovered=True`); `core/sgt/sgt_fields.json` (two new records:
`gst_track_return_status` and `itr_filed_returns_history`, with examples and counter-examples
taken from fictional lines shaped like the corpus); `ui/windows/tracker_dump_window.py` (row
detail shows "first seen: list page (recovered)" from `raw_payload.source`); `tests/sgt_golden/itr_filed_returns_history.json`
and `gst_track_return_status.json` (fictional, five cards each, expected rows).

**Owner step inside the WP (STOP in the middle):** the agent writes
`docs/sgt-recovery-spec-notes.md` listing the exact labels it needs from the two pages; the owner
runs `python tools/sgt_replay.py show` and opens the corpus file of a day with those pages,
pastes the label lines (no values) into the notes; the agent finishes the specs. If the owner's
corpus has no such page, the agent ships the specs disabled (`"disabled": true`) with a note.

**Tests:** `tests/test_sgt_resolver.py::test_cards_with_their_own_arn_are_all_kept`,
`::test_cards_without_an_identifier_still_thin_to_the_latest_period`,
`tests/test_sgt_replay.py::test_golden_session[itr_filed_returns_history]`,
`[gst_track_return_status]`, `tests/test_sgt_absorb.py::test_a_row_born_on_a_list_page_is_marked_recovered`,
`::test_a_later_confirmation_keeps_the_first_seen_mark`, harness
`test_f2_arn_appearing_later_on_track_status_is_captured`,
`test_d2_an_old_success_page_reopened_does_not_duplicate`.

**Done when** the golden sessions replay and the tracker detail shows the mark.

### WP12. Fleet view and the mandatory update path

**Tier:** Sonnet. **Depends on:** WP1.

**Files:** `main.py` (a `health_record_signal` from the worker to the main thread; the handler
calls `self.db.set_setting(f"capture_health:{device_id}", json)` through the capture queue;
`_check_agent_update` passes `sessions_open`; one HUD event per staged version);
`core/update_agent.py` (`should_close_now(app_dir, captures_busy, sessions_open=False)`: mandatory
path); `build_tools/updater/sera_update_agent.ps1` (`mandatory` copied into `pending.json`);
`ui/dialogs/unified_settings_dialog.py` ("Capture health" panel, section 7.2); new
`core/sgt/sgt_digest.py` (section 7.3) and its HUD/log hook in `SgtShadow._daily_health_check`;
`sync_schema.py` unchanged (D10 default).

**Edge cases:** device ids differ from device names (use `sync_identity` device id for the key,
`device_name()` in the record); a PC that was renamed; settings keys of a PC removed from the
office (the panel lists them as "last seen"); a settings write while sync is applying a snapshot
(the capture queue retry from WP2 covers the lock).

**Tests** (`tests/test_fleet_health.py`, `tests/test_update_agent.py`):
`test_health_key_is_written_once_a_minute`, `test_panel_rows_colour_by_age_and_state`,
`test_panel_copy_report_holds_no_client_data`, `test_mandatory_update_closes_at_30s_idle`,
`test_never_closes_with_a_session_open_or_rows_queued`, `test_hud_update_ready_once_per_version`,
`test_digest_lists_every_category`, `test_digest_runs_once_a_day`.

**Done when** two PCs (or two app folders on one PC with sync in test mode, as
`tests/test_sync_*.py` do) show each other's health rows.

**STOP (owner):** confirm D10 (settings keys) and the panel wording; set `mandatory: true` on the
next release that carries WP1 to WP5.

### WP13. Live soak tool and pass/fail

**Tier:** Sonnet (Haiku can run it). **Depends on:** WP7 to WP12.

**Files:** new `tools/sgt_soak_report.py`: reads `~/AmanAssociates_Sera/logs/vsdc.log`
(heartbeats and health lines), `sgt_shadow/*.jsonl` (session_end stats, dataset events,
`late_absorbed_after_end`, `ocr_only`), `logs/memory.log`; takes `--start`/`--end`; prints the
layer 1 soak table of section 8.2 and PASS/FAIL per row; optional `--expected arns.txt` (a file
the owner writes with the ARNs of the test filings, kept off the repo) to count misses exactly.

Also extends `tools/sgt_i_uia_node_bench.py` with `--soak` pages: a growing table (a row added
every 60 s), a toast every 5 minutes with a fictional ARN, for a no-client soak on the dev PC.

**Tests:** `tests/test_sgt_soak_report.py::test_pass_fail_from_fixture_logs`,
`::test_report_holds_no_identifiers`.

**STOP (owner):** run a 5 h soak on one office PC (real work) and the dev PC (mock pages);
record the result in `docs/sgt-never-miss-soak.md` (numbers only).

### WP14. Scenario sweep and documentation

**Tier:** Haiku. **Depends on:** WP13.

Walk section 11's table; for each scenario id confirm the named test exists and passes, or mark
it; update `docs/sgt-blueprint.md` section 10a with a dated paragraph pointing here; add the
hand-off notes; update `docs/sgt-gst-fixes-plan.md` and `docs/sgt-idle-resume-plan.md` with
"superseded by" lines where this blueprint changed their decisions (OCR on GST; parking on disk).

**STOP (owner):** none.

### WP15. Downloads watching: NOT BUILT (owner rejected D9, 2026-10-14). Kept for the record only.

**Tier:** Sonnet. **Depends on:** WP5, D9.

**Files:** new `core/sgt/sgt_downloads.py`: a `watchdog`-free poller (every 5 s, `os.scandir` of
the browser's download folder, the folder from the Windows known-folder API, no registry of other
folders); files matching `DOWNLOAD_PATTERNS` (config in `sgt_fields.json` under `"downloads"`:
name regexes like `ITR-V`, `Acknowledgement`, `ARN`, `GSTR` plus `.pdf`), modified in the last
10 minutes, under 10 MB; text extracted with the PDF text layer only (no OCR of PDFs; the
`anthropic-skills:pdf` tooling is not used in the app; use `pypdf` if present, else skip); the
lines become a `ReadResult(source="file")` with `SOURCE_POLICY["file"] = SOURCE_POLICY["ocr"]`
(add-only, never identity) absorbed into the session of the window that was in front when the
file appeared (or unattributed). Privacy: file names and paths never logged; text never stored.

**Tests:** `tests/test_sgt_downloads.py::test_only_matching_recent_small_files_are_read`,
`::test_file_evidence_is_add_only`, `::test_nothing_outside_the_download_folder_is_touched`.

**STOP (owner):** D9 first.

---

## 10. Owner decisions (recommended defaults)

| Id | Decision | Recommended default | Why |
| :--- | :--- | :--- | :--- |
| D1 | Use the Document element's Value as the first URL source (after WP0 confirms it) | Yes, with the address bar as fallback and the title grace unchanged | atomic with the page, works in full screen and popups, filters sidebars per document; same privacy footprint as the address bar |
| D2 | Read workers in MTA (`CoInitializeEx(COINIT_MULTITHREADED)`) | Yes (unless WP0's soak shows more hangs than STA) | Microsoft's guidance for UIA clients; the app already runs two MTA UIA threads without trouble |
| D3 | OCR merge policy (section 5.2): profile none, pieces fill-empty-only, datasets add-only, no identity check | Accept as written | honours the `Period •` lesson and constraint 5; OCR can only add |
| D4 | Gate thresholds `GATE_MIN_CELLS = 3`, `FLASH_MIN_CELLS = 8`, `GATE_LEVEL_DELTA = 2` | Accept, adjust from WP0's histogram | typing and carets stay under 3 cells; toasts cover 8 or more |
| D5 | Read spacing `READ_GAP_FACTOR = 1.0`, `REREAD_AFTER_SEC = 5.0`, `BURST_READS = 3` | Accept | 50 % duty per lane bounds CPU on heavy pages; 5 s periodic read is cheap with the cached reader |
| D6 | Watch visible browser windows that are not in front (`MAX_WATCHED_WINDOWS = 3`, `BG_TICK_SEC = 1.0`) | Yes | closes C8, B6, D10; reading is passive; cost is one screenshot per second per extra window |
| D7 | Frame tick `FRAME_TICK_SEC = 0.2` | 0.2 s if WP0 measures a frame tick under 15 ms; else 0.35 s | bounds the invisible toast to 200 ms |
| D8 | `BLIND_ALERT_SEC = 20` and the HUD/phone repeat intervals (5 min / 30 min) | Accept | fast enough to matter, slow enough not to nag |
| D9 | Downloads watching (WP15) | Approve, scoped as WP15 says (browser download folder, portal-named PDFs, text layer only) | the only way to close C5; stays passive and local |
| D10 | Fleet health storage: `app_settings` keys vs a new replicated table | `app_settings` keys | no schema or sync migration; already LWW by key; one small write a minute |
| D11 | Browser list: add `vivaldi.exe`, allow extra names in `vsdc_scope.json` | Yes | G8 |
| D12 | `mandatory: true` on the release that ships WP1 to WP5 | Yes | gets the fix onto the all-day PCs at the first quiet minute |
| D13 | Keep `IDLE_END_SEC = 2 h`, `RESUME_WINDOW_SEC = 4 h` | Keep | nothing in the evidence argues for a change; parking now survives restarts |
| D14 | Hindi portal language (F9) | Out of scope; health reports `specs-silent` if it happens | staff work in English; a spec set for Hindi is config work for later |
| D15 | Tier upgrade to Opus for WP3, WP4, WP5, WP10 | If available | reader concurrency, provenance and the scope seam are where a subtle mistake costs the most |

### Owner answers (2026-10-14): these override the defaults above

Every WP agent follows this table. Where it differs from the text elsewhere in this blueprint,
**this table wins**.

| Id | Answer | What it changes |
| :--- | :--- | :--- |
| D1 | Yes | As recommended. |
| D2 | Yes | As recommended. |
| D3 | Accepted | As recommended. |
| D4 | Accepted | As recommended. |
| D5 | Accepted | As recommended. |
| D6 | Accepted | As recommended (WP10 is built). |
| D7 | Yes | As recommended. |
| D8 | Yes | As recommended. |
| D9 | **Rejected** | **WP15 is not built.** Scenario C5 (evidence only in a downloaded file) stays a layer-4 item: listed in the daily digest, never watched. No agent reads the Downloads folder or any file the browser saves. |
| D10 | Yes | As recommended. |
| D11 | **Rejected** | `BROWSER_EXE_NAMES` is **not** changed and `vsdc_scope.json` gets **no** `browsers` key. Ignore the D11 parts of section 4.7 and WP10. |
| D12 | Yes | As recommended. |
| D13 | **Changed: longer** | `IDLE_END_SEC = 4 * 3600` (was 2 h) and `RESUME_WINDOW_SEC = 12 * 3600` (was 4 h) in `core/sgt/sgt_shadow.py`, so one client survives a whole workday of pauses. The router's `_SESSION_IDLE_TIMEOUT_SEC` in `core/vsdc/vsdc_router.py` must be at least `IDLE_END_SEC` (set it to 4 h too), so the router never ends a session before SGT would. WP9 makes this change, and its tests use these values. WP9's STOP question becomes "parked identity on disk for up to 12 h". |
| D14 | Ignored | Out of scope, as recommended (the health check reports `specs-silent`). |
| D15 | Yes | Use Opus for WP3, WP4, WP5 and WP10. |

**How the WPs are run (owner, 2026-10-14):** by `tools/sgt_never_miss.py` (tracker and dispatcher,
files in `docs/sgt_never_miss/`), one fresh CLI session per WP, **on `main` in the APP folder, with
no separate worktree or branch**. Section 9's "branch and process as `docs/sgt-overhaul-runner.md`"
means `docs/sgt_never_miss/sgt-never-miss-runner.md` instead. Owner STOPs are recorded as checks and
the run carries on with the blueprint's defaults. The restore point from before the first WP is the
git tag `restore-point/pre-sgt-never-miss`. Hand-off notes go in section 14.

**Watch out for clashing ids:** the decision ids D1-D15 in these tables are not the navigation
scenario ids D1-D15 in section 11 and the report's section 5D. For example, "D13 (address bar
typed)" in WP6 is the scenario, not this decision.

### 10a. Review corrections (2026-10-14): these override the text of sections 4 to 9

This blueprint was reviewed against the code on Windows after it was written. The rules below
**override** anything in sections 4 to 9 that says otherwise. Every WP agent reads them before
starting. R1 to R7 change the design; R8 to R12 tune or correct it.

| Id | Where | Rule |
| :--- | :--- | :--- |
| R1 | 4.6, 6.1, 6.3, WP5 | **A read whose URL changed mid-read is dropped, exactly like a tab change.** `torn == "url"` is never absorbed: the loop re-requests (`reason="torn-retry"`, within `TORN_RETRIES`). Delete the "absorbed for its page text only" rule and the test `test_torn_url_results_keep_page_text_but_not_link_hits`; add `test_torn_url_results_are_dropped_and_re_requested`. **The closed ring never takes a result across a boundary:** a result is absorbed into an ended session only if that session ended by idle or window-gone, and never if it ended by logout, a login page, or an identity contradiction. Replace `test_a_result_after_logout_still_writes_the_row_under_that_client` with `test_a_result_after_logout_is_dropped` and add `test_logout_then_next_client_in_the_same_tab_never_mixes`. Why: a read started on client A's last page can return client B's page after a logout and re-login in the same tab. A wrong-client row is worse than a re-read. |
| R2 | 4.3, 4.8, WP3, WP4 | **The URL check never waits behind a page read.** Each lane has two workers: a **content worker** (page reads) and a **scope worker** (URL-only jobs: Document `Value`, `IsOffscreen`, tab id, address bar; no page content). Both are MTA and each has its own UIA objects. A URL-only job is never queued behind a content read. `URL_FRESH_SEC = 2.0` stays; while a heavy read runs, the scope worker keeps the URL fresh, so the scope gate stays open and the OCR flash trigger (5.1) can still fire. Add `test_scope_stays_fresh_while_a_5s_read_runs` and `test_flash_ocr_fires_during_a_heavy_read`. The wedge/abandon rules apply to each worker separately. |
| R3 | 4.3, WP3 | **Coalescing never loses a page read.** `lane.queued` holds at most one content context. A newer content context replaces an older one; a URL-only context never replaces a content context (it goes to the scope worker, R2). Add `test_a_url_only_request_never_replaces_a_queued_page_read`. |
| R4 | 4.1, 4.3, WP3, scenario A8 | **The SDIS recorder is switched off by the owner (2026-10-14).** It read every page a second time in raw view (`core/sdis/recorder.py`, `read_page_nodes_here`, no timeout), which doubled the browser's work on heavy pages; it shipped on by default in v2.12.7 (2026-10-09). No WP changes SDIS. If the owner switches it back on, it must first be changed so that it never reads while a lane's content worker is busy, never reads a page whose last Core read took over `SDIS_MAX_CORE_MS = 300`, and is guarded by a timeout like the lanes. Section 11's A8 becomes **Closed (SDIS off by owner; SGT-I gets nodes from the Core)**. |
| R5 | 6.5, WP11, resolver | **Recovery takes only the latest filing on a list page (owner, 2026-10-14).** `latest_period_only` is **not** changed: a list page still yields only the latest period's card(s). That card is still recovered, marked `evidence_source="recovered"` when it creates a slot, and still upgrades an existing row (a missing ARN, a higher status). Delete `test_cards_with_their_own_arn_are_all_kept`; keep `test_cards_without_an_identifier_still_thin_to_the_latest_period`; the golden sessions `itr_filed_returns_history` and `gst_track_return_status` expect **one** row (the latest filing), not five. Section 8.2's "five cards, five rows" becomes "five cards, one row (the latest)". |
| R6 | 5.2, WP5, D3 | **OCR may promote a status only together with an identifier.** Under `SOURCE_POLICY["ocr"]`, a slot may be created or its status raised only when the same OCR result also gives that dataset's ARN/ack, accepted by the existing identifier checks (label-anchored, checksum/shape, ITR ack carries its date). Wording or a tile alone never promotes, from OCR. This repeats no part of the 2026-10-06 bug (11 "Verified without ARN" rows from dashboard text). Add `test_ocr_status_wording_without_an_arn_never_promotes`. |
| R7 | WP4 | **The crosshair engines keep their own URL read.** WP4 does not delete `VSDCRouter._init_uia`, `extract_browser_url` or `_cached_address_elements`. The crosshair/VSDC247 path keeps using them, unchanged, whenever it runs; the SGT path uses the capture loop's scope worker (R2). They are removed only in a later WP that retires the crosshair engines. Add `test_crosshair_path_reads_the_url_with_sgt_off`. |
| R8 | 7.2, WP12, D10 | **How often a PC shares its health is an app setting (owner, 2026-10-14).** New setting `capture_health_sync_min` in Settings → Tracker, next to the "Capture health" panel: a spin box in minutes, default **15**, allowed 1 to 60. A record is also written at once on every state change. Replace `test_health_key_is_written_once_a_minute` with `test_health_key_is_written_on_change_and_every_n_minutes`. The local `vsdc.log` health line stays once a minute (it is not synced). |
| R9 | 4.3, WP3, WP0 | **At most one heavy read per browser process at a time.** Lanes stop Sera's windows from blocking each other, but every window of one browser is answered by that browser's single UI thread. A content read whose last read of that page took over `HEAVY_READ_MS = 700` waits (stays queued) while another lane of the same browser process (same PID) has a content read in flight. WP0's `read-timing` adds a run with two windows of one browser read at once vs one at a time, to confirm. Add `test_two_heavy_reads_of_one_browser_never_run_at_once`. |
| R10 | 5.3, D7 | **Do not count on 0.2 s yet.** The SGT blueprint measured screenshot + hash at about 50 ms (section 7 there), not the 2 ms assumed in 5.3. D7's fallback (0.35 s when a frame tick costs 15 ms or more) applies until WP0's `gate` probe measures it on an office PC. |
| R11 | WP2 | The database connection already waits up to 5 s on a lock (the default timeout of the `sqlite3`-style `connect`), so `PRAGMA busy_timeout = 5000` changes nothing; leave it out. The retry and dead letter are still needed: they cover locks longer than 5 s and every other write failure. |
| R12 | 11 | "The session waits (2 h idle)" reads 4 h (D13). |
| R13 | 1, report W11 | The report said all-day PCs never update; section 1 corrected that (the update installs after 3 min without input, or after 8 h staged plus 30 s without input). Staff entering invoices rarely stop for 3 minutes, so a release waits the full 8 hours on exactly those PCs. That is why D12 (mandatory path) matters. |

---

## 11. Every scenario, and what closes it

Status after this blueprint: **Closed** (a WP and a named test), **Partial** (closed in part,
with what remains), **Out** (by constraint, with the constraint), **Unobservable**.

| Id | After | By |
| :--- | :--- | :--- |
| A1 | Closed | WP3 cached read, WP5 late results, WP7 gate; `test_profile_1_zero_missed` |
| A2 | Closed | WP7; `test_a2_typing_does_not_saturate_the_reader` |
| A3 | Closed | WP3 torn detection, WP5; `test_a3_fast_switching_never_mixes_pages` |
| A4 | Partial until measured | WP0 `read-timing` on real large pages; same mechanisms as A1 |
| A5 | Partial until measured | WP0 `read-timing --minutes 240`, WP13 soak drift check; MTA (D2) |
| A6 | Closed | tab cache per lane; `test_a6_forty_tabs_tab_id_cache` |
| A7 | Closed | tickets: a late read is absorbed; OCR flash; `test_a7_slow_browser_late_read_absorbed` |
| A8 | Closed (R4) | SDIS recording is switched off by the owner; SGT-I gets the Core's nodes after WP3; `test_a8_sgt_i_never_uses_a_lane` |
| A9 | Partial | spacing and lanes bound CPU; WP13 soak on the slowest office PC decides |
| A10 | Closed | cell gate; `test_a10_spinner_never_passes_the_gate` |
| B1 | Closed | `test_tab_change_during_read_is_torn_tab`, `test_b1_tab_switch_mid_read` |
| B2 | Closed | Document URL in the same job; `test_url_change_during_read_is_torn_url` |
| B3 | Closed | `ReadContext.hwnd` fixed at request; `test_b3_foreground_change_mid_tick` |
| B4 | Partial | not detectable; ARN keys and the next read limit damage; `test_b4_modal_mid_read_invents_nothing` |
| B5 | Closed | provenance; `test_a_late_result_lands_in_the_session_that_asked` |
| B6 | Closed | lanes + WP10; `test_b6_two_clients_two_windows_alternating` |
| C1, C2, C3 | Closed | frame watch + flash OCR + late reads; `test_c1_toast_one_second_on_heavy_page_is_caught_by_ocr`, `test_c2_redirect_after_success`, `test_c3_user_closes_modal_at_once` |
| C4 | Partial | PDF viewer Document in scope if WP0 shows text through UIA; else the ack page before it is caught |
| C5 | Out (owner rejected D9): listed in the daily digest only | none |
| C6 | Unobservable | listed by the digest (unverified ITR rows older than 7 days) |
| C7 | Closed without events | the frame watch replaces reliance on UIA events; `FlashWatcher` stays SGT-I's |
| C8 | Closed (D6) | WP10; `test_c8_success_on_second_monitor_is_captured` |
| C9 | OK | unchanged |
| D1 | Closed | per-tab sessions + Document URL; `test_d1_same_url_two_clients_two_tabs` |
| D2 | Partial | ARN key prevents duplicates; a GST ARN carries no date so the time is the read time |
| D3 | Closed | Document URL for popups; `test_d3_popup_without_address_bar` |
| D4 | Closed | resume by client (WP9); `test_d4_tab_dragged_to_new_window_resumes_draft` |
| D5 | Partial | GST header GSTIN names the session; ITR deep link without the profile page stays unattributed + alert + digest |
| D6 | Closed | per-tab sessions; `test_d6_fifteen_restored_tabs` |
| D7, D8 | Partial as today | out-of-scope pages are never read; the session waits (4 h idle, D13) |
| D9 | Closed | short page read like any other; OCR net |
| D10 | Closed (D6) | `test_d10_signing_dialog_in_front_still_reads_the_browser` |
| D11 | Partial as today | focus leaves; session waits |
| D12 | Closed (D1) | `test_d12_full_screen_window_is_scoped_by_document_url` |
| D13 | Closed (D1) | `test_d13_typed_address_bar_does_not_change_scope` |
| D14 | Closed (D1) | `test_only_in_scope_documents_are_read` |
| D15 | Partial | no dataset; a redirect to a login URL ends the session, which is right |
| E1 | Closed | boundaries unchanged + provenance; `test_e1_forty_clients_back_to_back` |
| E2, E7 | Partial as today | login keyword ends; the new session names itself from the page |
| E3, E4 | OK | unchanged |
| E5 | Closed in the harness | `test_e5_b2b_page_with_200_recipient_gstins_never_splits`; live confirmation in WP13 |
| E6 | Closed | WP9 parking on disk, prune parks; `test_the_router_prune_parks_instead_of_ending_when_the_window_still_exists` |
| E8 | Partial by rule | unattributed row + alert + digest line |
| E9 | Closed | snapshot + parked on disk + dead letter; `test_parked_sessions_survive_a_restart` |
| E10 | Partial as today | keys + promote-only |
| E11 | Out (constraint 11) | contradiction rule protects attribution |
| F1 | Closed | late reads absorbed; health `slow`; `test_f1_slow_portal_half_loaded_pages` |
| F2 | Closed (WP11) | `test_f2_arn_appearing_later_on_track_status_is_captured` |
| F3 | Closed | `test_f3_already_filed_message_adds_no_row` |
| F4 | Closed | `test_f4_processing_error_never_promotes` |
| F5 | Closed for noticing | health `specs-silent` within 5 min + daily spec-quiet alert reach the office |
| F6 | Closed for noticing | tripwire becomes health `unlisted-portal` |
| F7 | Closed | OCR on every portal |
| F8 | OK | unchanged |
| F9 | Out (D14) | health `specs-silent` |
| G1 | Closed | covered windows left out; visible ones read |
| G2 | Closed for noticing | `specs-silent` + the digest; WP0's equivalence tool is the canary after a browser update |
| G3 | Closed | blind retries; `test_g3_partial_first_read_after_idle_is_retried` |
| G4 | Closed | a reload is a URL/frame change; `test_g4_sleeping_tab_reload` |
| G5 | OK | signature is of the client area |
| G6 | OK | sessions park |
| G7 | Partial as today | Gecko rules kept in the shared helper |
| G8 | Out (owner rejected D11: no extra browsers needed) | |
| G9 | Closed | OCR net + health `blind` |
| G10 | Closed | dead letter + snapshot + parked on disk |
| H1 | Closed for noticing | health `stalled` + phone; no COM on the capture thread after WP4 |
| H2 | Closed | OCR keeps running during a pause; health `uia-paused` |
| H3 | Closed | WP2 dead letter, snapshot re-queue |
| H4 | Closed | WP2 retry + `busy_timeout` |
| H5 | Partial by nature | the fleet view shows last capture per PC |
| H6 | Closed for noticing | health `disk` |
| H7 | Partial as today | mode change ends sessions with rows written |
| H8 | Closed | monotonic idle maths |
| H9 | Closed for noticing | memory in the health record; soak checks drift |
| I1 | Closed | mandatory path; HUD update event |
| I2 | Closed | fleet view |
| I3 | Closed for noticing | `alerts_configured`, `scope_extras`, `spec_override` in the record |
| I4 | Closed | a new PC appears in the panel |

---

## 12. What the owner must measure (WP0 fills this table)

| Question | Probe | Decides |
| :--- | :--- | :--- |
| Does the Document Value carry the page URL in Edge, Chrome, Firefox, in full screen, in a popup? | `doc-url` | D1, WP3 scope rule |
| Does the browser PDF viewer expose text through UIA? | `doc-url` | C4 status |
| Full read of a real B2B page with N rows: element-by-element vs cached, ms and element count | `read-timing` | A1, A4, READ_GAP_FACTOR |
| Does read time grow over hours on the same page? STA vs MTA | `read-timing --minutes 240`, `--mta` | D2, A5 |
| Frame tick cost (screenshot + signature) on an office PC | `gate` | D7 |
| Cells changed by typing, caret, spinner, toast, banner, modal | `gate` | D4 |
| How long an ARN is visible by UIA vs OCR at a real filing; what `FlashWatcher` catches | `toast` | C1 to C3 confidence, FLASH thresholds |
| Foreground and tab switches per minute in B2B work | `switches` | harness profile 1 realism |
| Which build each PC runs today | WP12 panel after it ships; until then `logs/vsdc.log` exists only on 2.12.10+ | I2 |

---

## 13. What this blueprint does not change

The spec file and its loader, the resolver's rules, the submit ladder, dataset rules, live keys,
the tracker merge rules, Sera Sync, the HUD pill's look, the SDIS and SGT-I contracts (they only
receive the Core's nodes instead of reading their own), the crosshair engines (off, untouched),
the scope allow-list, the privacy rules of the logs, and the process in constraint 12.

---

## 14. Hand-off notes

- **WB** (2026-10-14, claude-haiku-5-5): baseline only, no code changed. Full suite via `venv/Scripts/python.exe tools/sgt_never_miss_suite.py` (log `logs/sgt-never-miss/suite-20261014-140145.log`): **T=3078 F=2 S=1**. Pre-existing failures at baseline (not yet diagnosed; a later WP's failure is new only if it is absent here): `tests/test_class_diff_render.py::test_output_paths_never_recorder_or_tracked_folders` and `tests/test_sdis_tables.py::test_command_line_prints_no_values`. Replay baseline: `venv/Scripts/python.exe tools/sgt_replay.py baseline` writes `replay_baseline.json` to `~/AmanAssociates_Sera` (client data, never in the repo); then `diff` after a spec edit, `diff --specs <file>` to try a spec without installing it; `show` and `health` also exist. Use `--days N` and `--corpus DIR` to narrow or relocate. The suite ran for over 10 min on this PC, so run it in the background and watch the log for the `--- T=` line.
- **WP0** (2026-10-14, claude-sonnet-5-5): built `tools/sgt_probe_pack.py` (read-only, standalone, `sgt_i_key_probe` style) and `tests/test_sgt_probe_pack.py` (7 tests; the two required ones pass). `--help` works. Narrow run: all `tests/test_sgt_*.py` + `tests/test_vsdc_*.py` = 1089 passed, 1 skipped; full suite not run (not required for WP0), so the two WB failures are untouched.
  - Subcommands: `doc-url`, `read-timing --minutes N [--mta | --two-windows]`, `gate --minutes N`, `toast --minutes N [--no-ocr]`, `switches --minutes N`; each takes `--delay` and `--out`. Report: `~/AmanAssociates_Sera/logs/probe_pack_<date>.txt` (appended per day). Every line is run through `vsdc_alerts.redact_identifiers` on the way in; ARNs are only numbered (ARN-1, ARN-2) and never printed; non-portal URLs appear only as "[out of scope: url, N chars]".
  - Only a browser window in front on a portal page (`is_in_scope_url`) is ever read; `switches` reads no titles or text, `doc-url` lists every browser window's Document Value (portal addresses only) and, for an open PDF viewer, 20 redacted/clipped lines (`--no-pdf-sample` skips them). Nothing clicks, types, scrolls or focuses. No probe was run: the owner runs them.
  - Decisions: R10 - `gate` measures the real tick (screenshot + signature ms) and the report says what D7 needs; it does not assume 0.2 s. R9 - `--two-windows` reads two windows of one browser process one after the other, then at once on two MTA threads (`read_page_nodes_here`), and prints a SERIALISED / PARALLEL / PARTLY verdict from wall time vs sum (ratio >= 0.85 serialised, <= 0.65 parallel; pages reading under 150 ms are "too light to tell"). `--mta` swaps the app's own read worker (`vsdc_uia_text._POOL`) to MTA for that run only. Working set comes from ctypes `GetProcessMemoryInfo` (psutil is not installed) for the window's UI process only, not the whole browser tree.
  - `core/vsdc/vsdc_gate.py` still does not exist (WP7): the probe carries its own copies of `frame_signature` / `cells_changed` and the section 4.4 constants (32x32, 16 levels, delta 2, GATE_MIN_CELLS 3, FLASH_MIN_CELLS 8); WP7 should take its numbers from the owner's `gate` report and keep the two copies identical or have the probe import the new module.
  - Owner checks added (6, `check-add WP0`): doc-url (D1, C4), read-timing incl. R9 two-windows (D2), read-timing --mta (D2), gate (D4, D7), toast (D5), switches (D1, D4). Each names the exact command and the section 12 rows to fill. Until they come back, WPs that depend on D1/D2/D4/D5/D7 must use the blueprint's stated defaults, not the probe.
  - Not built (per D9/D11): no downloads watching; `BROWSER_EXE_NAMES` unchanged (no Vivaldi).
