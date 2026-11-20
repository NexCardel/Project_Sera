"""UCW WP2: the capture loop's whole suite (tests/test_vsdc_capture.py) again, over RemoteReader.

Every test there runs here with its rig's reader built as RemoteReader - the translation layer - over a
capture worker on a thread (core/capture/remote.ThreadChild: real OS pipes, the same frames and pickling
as the worker process), reading the same fake backend. The loop and SGT's engine are not changed for
it: the suite passing here is the plan's "SGT keeps receiving exactly what it receives today".
"""

import pytest

import tests.test_vsdc_capture as base
from tests.test_vsdc_capture import *  # noqa: F401,F403 - the suite itself


@pytest.fixture(autouse=True)
def _remote_reader(monkeypatch):
    monkeypatch.setattr(base, "READER", "remote")
