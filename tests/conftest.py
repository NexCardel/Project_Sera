"""
Shared test setup.

The VSDC router sends a phone alert (through ntfy) when it captures a submission with no
client attached, if the developer has configured a topic in vsdc_alert.json or the
environment. A test run must never do that: it would push real notifications for fake
filings. So every test starts with alerts pointed at a config that does not exist and the
alert environment variables removed. Tests that exercise alerts build their own
AlertSender with an explicit config and a fake transport.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def _no_real_phone_alerts(monkeypatch, tmp_path):
    monkeypatch.setenv("VSDC_ALERT_CONFIG", str(tmp_path / "no_such_vsdc_alert.json"))
    for var in ("VSDC_ALERT_TOPIC", "VSDC_ALERT_SERVER", "VSDC_ALERT_MACHINE"):
        monkeypatch.delenv(var, raising=False)
