"""
The SGT simulation harness (never-miss blueprint section 8): the real PageReader, CaptureLoop and
SgtShadow wired to the scripted browser, on a virtual clock, with nothing mocked in between.

    h = Harness(browser, tmp_path)
    h.run(600)                     # ten virtual minutes
    assert_profile(h, expected)    # zero missed, zero mis-attributed

Ticks come at the capture loop's own pace (FRAME_TICK_SEC) around anything that changes and once a
second otherwise, so a long quiet stretch costs little. Tracker rows are drained every tick and stamped
with the virtual time they came out. Fictional data only; failures name clients by index, never by value.
"""
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from unittest.mock import patch

import pytest

from core.sgt.sgt_shadow import SgtShadow
from core.sgt.sgt_specs import BUILTIN_FIELDS_PATH, SpecStore
from core.sgt.sgt_toolbox import SUBMIT_LEVELS
from core.vsdc import vsdc_reader
from core.vsdc.vsdc_capture import FRAME_TICK_SEC, CaptureLoop
from core.vsdc.vsdc_reader import PageReader

from .fake_backend import T0, FakeBackend, SimThread, VirtualClock
from .fake_browser import Browser
from .fake_platform import FakeOcr, FakePlatform

ASSERT_WITHIN_SEC = 10.0          # a submission must be a row this long after it left the screen
IDLE_STEP_SEC = 1.0
TODAY = date(2026, 10, 14)


class _Health:
    def window(self, *a, **kw) -> None: ...
    def answered(self) -> None: ...
    def requested(self, ctx) -> None: ...


class Harness:
    def __init__(self, browser: Browser, tmp_path, *, keep_state: bool = False, today: date = TODAY,
                 ocr: bool = True, tick_sec: float = FRAME_TICK_SEC, idle_step: float = IDLE_STEP_SEC,
                 real: bool = False) -> None:
        self.real = real                          # sleep each step for real (the owner's --real runs)
        self.browser, self.tmp, self.today = browser, Path(tmp_path), today
        self.keep_state, self.use_ocr, self.tick_sec, self.idle_step = keep_state, ocr, tick_sec, idle_step
        self.vc = VirtualClock()
        self.rows: List[Tuple[float, Dict[str, Any]]] = []
        self.ticks = 0
        self.restarts = 0
        self.counts: Counter = Counter()          # SGT and capture-loop diag counts, summed over the run
        self._patch = patch.object(vsdc_reader, "_OneThread", lambda name: SimThread(self.vc))
        self._patch.start()
        self._build()

    def _build(self) -> None:
        self.backend = FakeBackend(self.browser, self.vc)
        self.reader = PageReader(self.backend, clock=self.vc.now, diag=lambda m: None)
        self.platform = FakePlatform(self.browser, self.vc)
        self.ocr = FakeOcr() if self.use_ocr else None
        self.sgt = SgtShadow(store=SpecStore([BUILTIN_FIELDS_PATH], log=lambda m: None),
                             read_uia=lambda hwnd: {"lines": []}, dispatched_ids=lambda: [],
                             log_dir=self.tmp / "shadow", clock=self.vc.now, today=lambda: self.today,
                             echo=lambda m: None,
                             state_path=(self.tmp / "state.json") if self.keep_state else None)
        self.loop = CaptureLoop(self.platform, self.reader, self.sgt, self.ocr, _Health(), clock=self.vc.now)

    def close(self) -> None:
        self._patch.stop()

    # ---- time ------------------------------------------------------------------------------------------

    def rel(self) -> float:
        return self.vc.t - T0

    def run(self, until: float) -> "Harness":
        """Virtual time `until` seconds from the start of the run."""
        while self.rel() < until - 1e-9:
            step = self.tick_sec if self.browser.active_at(self.rel()) else self.idle_step
            step = min(step, until - self.rel())
            self.vc.advance_to(self.vc.t + step)
            self.tick()
            if self.real:
                time.sleep(step)
        return self

    def tick(self) -> None:
        self.ticks += 1
        self.loop.tick()
        self.collect()
        for k, v in self.sgt.take_diag().items():
            if isinstance(v, int) and not isinstance(v, bool):
                self.counts[k] += v
        for k, v in self.loop.diag().items():
            if isinstance(v, int) and not isinstance(v, bool):
                self.counts[k] += v

    def window_state(self, hwnd: int) -> Any:
        return self.loop._states.get(hwnd)

    def collect(self) -> None:
        for row in self.sgt.drain():
            self.rows.append((self.rel(), row))

    # ---- the app going away and coming back ------------------------------------------------------------

    def restart_app(self) -> None:
        """The Sera process dies and starts again: reader, loop and SGT are new; SGT's crash snapshot
        (keep_state) is all it has of the day. Rows not yet drained are lost with the process."""
        self.restarts += 1
        self.reader.close()
        self._build()
        self.collect()

    def end_day(self) -> None:
        self.loop.end_all("test end")
        self.collect()

    # ---- what the run produced ---------------------------------------------------------------------------

    def rows_for_arn(self, arn: str) -> List[Dict[str, Any]]:
        return [r for _t, r in self.rows if r.get("arn") == arn]


@pytest.fixture()
def sim(tmp_path):
    made: List[Harness] = []

    def make(browser: Browser, **kw) -> Harness:
        h = Harness(browser, tmp_path / f"run{len(made)}", **kw)
        made.append(h)
        return h

    yield make
    for h in made:
        h.close()


# ---- the check -----------------------------------------------------------------------------------------------

def _rank(status: str) -> int:
    return SUBMIT_LEVELS.index(status) if status in SUBMIT_LEVELS else -1


def _identity_of(row: Dict[str, Any]) -> Tuple[str, str]:
    gstin = (row.get("gstin") or "").upper()
    pan = (row.get("pan") or "").upper() or (gstin[2:12] if len(gstin) == 15 else "")
    return pan, gstin


def _is_clients(row: Dict[str, Any], client: Any) -> bool:
    pan, gstin = _identity_of(row)
    if not pan and not gstin:
        return False
    return (not pan or pan == client.pan) and (not gstin or gstin == client.gstin)


def problems(h: Harness, expected: Sequence[Any], within: float = ASSERT_WITHIN_SEC, *, known: Sequence[Any] = (),
             allow_missed: bool = False) -> List[str]:
    """Every way the run broke the rule, by client and submission index only. `known`: clients who appear on
    screen without a submission to check (their draft rows are fine, an ARN under them is not).
    allow_missed: only the never-mix rules are checked (a scenario about mixing, not about speed)."""
    out: List[str] = []
    owner = {s.arn: s.client for s in expected}
    people = [s.client for s in expected] + list(known)
    ids = {c.pan: c for c in people}
    ids.update({c.gstin: c for c in people if c.gstin})
    for i, s in enumerate([] if allow_missed else expected):
        late = s.visible_until + within
        got = [r for t, r in h.rows if r.get("arn") == s.arn and t <= late and _is_clients(r, s.client)]
        if not got:
            seen = [r for t, r in h.rows if r.get("arn") == s.arn]
            out.append(f"missed: submission {i} (client {s.client.index}, form {s.form}): "
                       + ("a row carries its ARN but not its client" if seen else "no row with its ARN by the deadline"))
        elif max(_rank(r.get("status", "")) for r in got) < _rank(s.status):
            out.append(f"under-stated: submission {i} (client {s.client.index}) never reached its status")
    for t, r in h.rows:
        arn = r.get("arn") or "N/A"
        pan, gstin = _identity_of(r)
        who = ids.get(gstin) or ids.get(pan)
        if arn != "N/A":
            if arn not in owner:
                out.append(f"invented: a row at {t:.0f}s carries an ARN that was never on screen")
            elif (pan or gstin) and not _is_clients(r, owner[arn]):
                out.append(f"mis-attributed: a row at {t:.0f}s has the ARN of client {owner[arn].index} "
                           f"under {'client ' + str(who.index) if who else 'another identity'}")
        elif (pan or gstin) and who is None:
            out.append(f"mis-attributed: a row at {t:.0f}s carries an identity no client has")
        if pan and gstin and gstin[2:12] != pan:
            out.append(f"mis-attributed: a row at {t:.0f}s mixes a PAN with another GSTIN")
    return out


def assert_profile(h: Harness, expected: Sequence[Any], within: float = ASSERT_WITHIN_SEC, **kw) -> None:
    found = problems(h, expected, within, **kw)
    assert not found, f"{len(found)} problem(s):\n" + "\n".join(found[:20])
