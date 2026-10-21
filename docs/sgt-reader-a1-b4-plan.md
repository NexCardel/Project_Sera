# SGT reader latency: A1 + B4 plan for the implementer

Status: ready to implement. Date: 2026-10-21.

Files: `core/vsdc/vsdc_reader.py`, `core/vsdc/vsdc_capture.py`, `tests/test_vsdc_reader.py`,
`tests/test_vsdc_capture.py`, `tests/sgt_sim/fake_backend.py`.

Do **not** commit, bump `version.json`, or touch `package_dist/`. Use Edit/Write for code.

## Goal

Two independent, low-risk changes:

- **A1**: remove the second full document locate from a clean content read.
- **B4**: after a slow read, start the next read quickly when the picture changes a lot.

Do **not** change `WEDGE_SEC`, `SLOW_GRACE_SEC`, `READ_GAP_FACTOR`, `REREAD_AFTER_SEC`,
`FRAME_TICK_SEC`, or any OCR threshold in this work. A2 (lean cache), B5 (urgent abandon), C6/C7,
and D8 are out of scope.

## Evidence

Measured on local HTML in Edge, benchmark windows minimised so the running app's scope worker
skipped them:

| Page | Reader v2 | Old reader | Where the time goes |
|---|---:|---:|---|
| Success dialog (13 nodes / 9 lines) | 55-115 ms | ~44 ms | 2 document locates ~50-105 ms; subtree 4 ms |
| GST submission (43 / 38) | 60-80 ms | ~46 ms | locate 27 + 27 ms; subtree 7 ms |
| Rendered UIA page (173 / 169) | 115-195 ms | ~102 ms | locate 43 + 41 ms; subtree 30 ms |
| Heavy table (2,927 / 2,713) | 850-900 ms | ~1,779 ms | locate 211 + 212 ms; subtree 538 ms |

`_content_job` currently calls `_locate()` twice: once before the read and once after it, only to
learn the URL after the read. The second call is a full `FindAllBuildCache(Descendants, Document)`
and is the largest avoidable cost on normal pages.

The capture loop then waits `READ_GAP_FACTOR * last_read_ms` (1.0) after a read finishes. After a
900 ms heavy read, a success page arriving immediately is not read for another ~900 ms even though
its own read would take ~60 ms. A large frame change is currently treated as a normal `"frame"`
read and pays that wait.

## A1 - one document locate per clean content read

### Design

After the content read, verify the page cheaply instead of scanning every Document again:

1. Re-read the tab id (already done today). If the tab changed, the result is torn (`"tab"`) and the
   second locate is unnecessary: set `url_after = url_before`.
2. Otherwise re-read the live `Value` URL of the primary Document that was read. Chromium and Gecko
   expose the page URL there.
3. If that URL is available and equals `url_before`, set `url_after = url_before` and skip `_locate`.
4. If it is unavailable (`None`), empty, or different from `url_before`, fall back to the existing
   full `_locate`. This preserves torn-URL detection and the address-bar rule.
5. If no Document was read (`to_read` is empty), keep the existing second `_locate` unchanged.

Only the first in-scope Document is checked, because `_locate` uses the first in-scope Document as
the page URL today. Do not change `doc_urls`, `scope_rule`, or `ReadResult.torn`.

### Exact edits

#### 1. Backend protocol and real backend

In `core/vsdc/vsdc_reader.py`, add to `Backend` (around line 134-143):

```python
def document_url(self, element: Any) -> Optional[str]: ...
```

`None` means "could not read it"; `""` means "the browser exposes no URL". Implement in
`UiaBackend` near `documents` / `read_document` (around line 195-204):

```python
def document_url(self, element: Any) -> Optional[str]:
    try:
        value = element.GetCurrentPropertyValue(uia_nodes.PID_VALUE)
    except Exception:
        return None
    return value.strip() if isinstance(value, str) else ""
```

`element` is a live Document reference returned by the URL-only `FindAllBuildCache`, so a current
property read is valid. `uia_nodes` is already imported.

#### 2. `_content_job`

In `core/vsdc/vsdc_reader.py` (around line 600-616), replace the tail after the per-document reads:

```python
r.tab_after = self._backend.tab_id(ctx.hwnd, root)
if r.tab_after != r.tab_before:
    r.url_after = r.url_before
elif to_read:
    primary = to_read[0][0]
    current = self._backend.document_url(primary)
    if current is not None and current == r.url_before:
        r.url_after = r.url_before
    else:
        r.url_after = self._locate(ctx.hwnd, root)[0]
else:
    r.url_after = self._locate(ctx.hwnd, root)[0]
```

Keep the existing job-level `except` and `finished_at` handling. Do not swallow a fallback
`_locate` exception inside the branch; the current behaviour (error result, no absorb) stands.

#### 3. Test fakes

`tests/test_vsdc_reader.py`, `FakeBackend` (around line 60-113):

```python
def document_url(self, doc):
    self.calls.append(("document_url", id(doc)))
    if getattr(doc, "current_url_error", False):
        return None
    return doc.url
```

`Doc.__init__` (around line 47): add `self.current_url_error = False`.

`tests/sgt_sim/fake_backend.py`, `FakeBackend` (around line 84-156): add the same method. Spend a
small virtual cost, e.g. add `"document_url": 0.001` to `COST`, then:

```python
def document_url(self, element: Any) -> Optional[str]:
    self._vc.spend(COST["document_url"])
    t = self._vc.rel()
    page = element.page_at(t)
    return page.url if page is not None else ""
```

### Tests for A1

Add to `tests/test_vsdc_reader.py`:

- `test_a_clean_read_asks_the_document_list_once`: one Document at `PORTAL`; after the result,
  `sum(1 for m, _ in backend.calls if m == "documents") == 1` and there is exactly one
  `"document_url"` call; `r.torn == ""`; lines unchanged.
- `test_tab_change_skips_the_second_document_scan`: extend the existing tab-change test to assert
  exactly one `"documents"` call while still reporting `torn == "tab"`.
- `test_url_change_still_scans_twice`: extend the existing URL-change test to assert two
  `"documents"` calls and `torn == "url"`.
- `test_an_unavailable_current_url_falls_back_to_a_full_locate`: set
  `doc.current_url_error = True`; one read; assert two `"documents"` calls, `r.torn == ""`, and the
  lines were delivered. This proves a failed cheap check does not drop a good read.
- Keep `test_only_in_scope_documents_are_read`, `test_address_bar_is_used_when_no_document_has_a_url`,
  and `test_a_result_carries_its_context_back` passing unchanged.

### Manual check for A1

The live property read was verified on local Edge 2026-10-21: a Document from
`documents_with_urls` returned its URL from `GetCurrentPropertyValue(PID_VALUE)`.

On a local Edge page (`tests/test_page_gst_submission.html` is enough):

- A clean read should show one `documents` call and one `document_url` call.
- Navigating the same window to another URL during a read should fall back and still report
  `torn == "url"`.
- Compare before/after: expect roughly 27-47 ms less on small/medium pages and ~210 ms less on a
  heavy page. If `document_url` always returns `None` on real Edge, stop and report: A1 has no
  benefit and needs a different cheap check.

## B4 - urgent read after a slow read and a large frame change

### Design

A read is "urgent" only when all of these hold:

- the reason is `"frame"` (not URL burst, torn retry, blind retry, periodic, or SGT wants-read);
- the frame change is large (`URGENT_FRAME_CELLS` or more of the 32x32 grid);
- the last page read was slow (`last_read_ms > vsdc_gate.FLASH_UIA_SLOW_MS`, currently 700 ms), or
  a large change is already waiting behind a slow in-flight read;
- no urgent read was asked in the last `URGENT_COOLDOWN_SEC`.

An urgent read uses `BURST_GAP_SEC` (0.3 s) instead of
`max(MIN_READ_GAP_SEC, READ_GAP_FACTOR * last_read_ms)`. It does **not** preempt an in-flight read;
it only removes the extra post-read wait once that read finishes. Keep the reason string `"frame"`
so SGT, logs, and existing tests see the same values.

The cell count is relative to `ws.last_sig`, the frame at which the last read was asked, exactly as
the existing `"frame"` gate works. Do not compare with `ws.prev_sig` here: that would miss a change
that arrived while a read was in flight and would reset on every tick.

### Exact edits

#### 1. Constants and state

In `core/vsdc/vsdc_capture.py`, next to the frame constants (around line 42-50):

```python
URGENT_FRAME_CELLS = 64       # 32x32 grid; ~6% of the picture (tune on a local success page)
URGENT_COOLDOWN_SEC = 2.0     # at most one urgent read per window in this window
```

In `WindowState` (around line 94-125), add:

```python
frame_cells: int = 0          # cells changed at the last frame check (0..1024)
wanted_urgent: bool = False   # the waiting reason is an urgent frame read
last_urgent_at: float = 0.0   # when the last urgent read was asked
```

#### 2. `_read_reason`

In `_read_reason` (around line 491-504), compute the changed-cell count once and keep `"frame"`:

```python
changed = None
if sig is not None and ws.last_sig is not None:
    changed = vsdc_gate.cells_changed(ws.last_sig, sig)
ws.frame_cells = changed or 0
if sig is None or ws.last_sig is None or ws.frame_cells >= vsdc_gate.GATE_MIN_CELLS:
    return "frame"
```

Leave the `retry`, burst, `wanted`, periodic, and `wants_read` branches as they are. Do not add a
new reason string.

#### 3. Urgency test

Add a small helper near `_spaced`:

```python
@staticmethod
def _urgent(ws: WindowState, reason: str, now: float) -> bool:
    if reason != "frame":
        return False
    if ws.wanted == reason and ws.wanted_urgent:
        return True
    if now - ws.last_urgent_at < URGENT_COOLDOWN_SEC:
        return False
    return ws.last_read_ms > vsdc_gate.FLASH_UIA_SLOW_MS and ws.frame_cells >= URGENT_FRAME_CELLS
```

#### 4. Tick and `_spaced`

In `tick` (around line 424-439):

```python
reason = self._read_reason(w, ws, sig, now)
if not reason:
    return
urgent = self._urgent(ws, reason, now)
if ws.pending_seq or not self._spaced(ws, reason, now, urgent):
    ws.wanted, ws.wanted_urgent = reason, urgent
    return
status = self._request(w, ws, now, reason, sig, img, portal, owner)
if status in ("started", "queued"):
    ws.wanted, ws.wanted_urgent = "", False
    ws.last_sig, ws.last_request_at = sig, now
    if urgent:
        ws.last_urgent_at = now
    if reason in ("url", "burst"):
        ws.burst_left -= 1
    elif reason in ("torn-retry", REASON_BLIND_RETRY):
        ws.retry = ""
else:
    ws.wanted, ws.wanted_urgent = reason, urgent
```

In `_spaced` (around line 506-512):

```python
@staticmethod
def _spaced(ws: WindowState, reason: str, now: float, urgent: bool = False) -> bool:
    if not ws.last_read_finished:
        return True
    quick = reason in ("url", "burst", "torn-retry", REASON_BLIND_RETRY)
    if urgent:
        gap = BURST_GAP_SEC
    else:
        gap = max(BURST_GAP_SEC if quick else MIN_READ_GAP_SEC,
                  READ_GAP_FACTOR * ws.last_read_ms / 1000.0)
    return now - ws.last_read_finished >= gap
```

Important: this preserves today's behaviour for `"url"`, `"burst"`, `"torn-retry"`, and
`REASON_BLIND_RETRY` (they still include `READ_GAP_FACTOR`). Only the new urgent path bypasses it.

#### 5. Reset

When `_learn_url` sees a URL change (around line 337-342), clear `ws.wanted_urgent = False`. Also
clear it wherever `ws.wanted` is cleared. `_absorb` does not need to change.

### Tests for B4

In `tests/test_vsdc_capture.py`:

- Update `test_reads_are_spaced_by_the_last_read_duration` (around line 216-228): it currently paints
  the whole 320x320 screen, which would now be urgent. Paint a block below the urgent threshold,
  e.g. `r.platform.paint((0, 0, 49, 49), shade)` (5x5 = 25 cells), and keep the >=2.4 s spacing
  assertion. This test is about normal spacing, not urgency.
- Add `test_a_large_change_after_a_slow_read_is_asked_on_the_next_tick`:
  - `r.win.docs[0].cost_sec = 1.2`;
  - `r.warm()`;
  - paint the whole screen and `r.step(4)`;
  - assert `r.loop._states[HWND].last_read_ms >= 1200`;
  - record `before = ws.last_read_finished`;
  - paint the whole screen again and `r.step()`;
  - assert the new `page_reads()[-1].asked_at - before <= TICK + 1e-6` (asked at the next tick,
    not after another 1.2 s).
- Add `test_a_large_change_after_a_fast_read_keeps_the_normal_gap`: repeat with
  `cost_sec = 0.05`; the next read should still respect `MIN_READ_GAP_SEC` (0.35 s), i.e. not the
  urgent 0.3 s path.
- Add `test_a_small_change_after_a_slow_read_is_not_urgent`: use
  `_urgent` directly with `frame_cells = 20`, `last_read_ms = 1200`, and assert `False`.
- Add `test_urgent_has_a_cooldown`: `_urgent` with `frame_cells = 500`, `last_read_ms = 1200`,
  `last_urgent_at = now`, and assert `False` at `now + 1.0`, `True` at `now + 3.0`.
- Keep `test_a_toast_sized_change_reads`, `test_a_url_change_bursts_three_reads`,
  `test_torn_read_is_dropped_and_asked_again`, and all OCR flash/green tests passing unchanged.

`_urgent` and `_spaced` are static methods; tests may call them with a `WindowState` directly. If
calling a static method on `CaptureLoop` feels awkward, expose them as module-level helpers, but
keep the edit small.

### Manual check for B4

Use a local page that replaces a heavy form with a large success panel after a timer. With the app
or a one-off reader harness:

- while the heavy form read is running, the success panel must not be absorbed late;
- once the heavy read finishes, the large change must be asked at the next tick (~0.35 s), not after
  another `last_read_ms`;
- a small toast on the same slow page must keep normal spacing;
- no window should produce more than one urgent read every 2 s.

## Order of work

1. A1 + its tests. Run `tests/test_vsdc_reader.py`; verify the clean-read call count.
2. B4 + its tests. Run `tests/test_vsdc_capture.py`.
3. Run the SGT simulation tests that use the fake backend:
   `tests/test_sgt_sim_scenarios.py` and `tests/test_sgt_sim_profiles.py`.
4. Full suite with the live plugin, per CLAUDE.md:

```powershell
$env:PYTHONPATH="tools"; python -m pytest -p live_tf -p no:terminal -p no:cacheprovider tests
```

Report pre-existing failures separately; do not fix unrelated tests.

## Acceptance criteria

A1:

- A clean content read calls `Backend.documents` exactly once.
- A tab change still yields `torn == "tab"` and skips the second scan.
- A URL change or an unavailable current URL still falls back to a full locate and yields the same
  `url_after`, `torn`, `doc_urls`, `scope_rule`, and lines as before.
- Local timing drops by roughly 27-47 ms on small/medium pages and ~210 ms on the heavy page.
- The second `_locate` is not removed for the no-document / address-bar path.

B4:

- A >=64-cell frame change after a >700 ms read is asked within ~0.35 s of that read finishing.
- A >=64-cell change after a fast read keeps the normal 0.35 s gap.
- A <64-cell change after a slow read is not urgent.
- Urgent reads are limited to one per 2 s per window.
- Existing reasons and SGT-visible values are unchanged; `"frame"` remains `"frame"`.
- No change to `READ_GAP_FACTOR`, `MIN_READ_GAP_SEC`, `BURST_GAP_SEC`, `WEDGE_SEC`, or OCR
  thresholds.

## Rollback

Revert only `core/vsdc/vsdc_reader.py` and `core/vsdc/vsdc_capture.py` plus the test/fake changes.
There is no data migration, schema change, or log-format change. A1 can be reverted without B4 and
vice versa; they are independent.
