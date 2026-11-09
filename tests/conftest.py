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


@pytest.fixture(autouse=True)
def _diag_log_in_tmp(monkeypatch, tmp_path):
    """The capture engine's diagnostics log (core/vsdc/vsdc_diag) goes to the test's own folder,
    never to the real ~/AmanAssociates_Sera/logs/vsdc.log."""
    monkeypatch.setenv("VSDC_DIAG_LOG", str(tmp_path / "vsdc.log"))


@pytest.fixture(autouse=True)
def _sca_log_in_tmp(monkeypatch, tmp_path):
    """SCA's step log (core/sca/sca_log) goes to the test's own folder, never to the real
    ~/AmanAssociates_Sera/logs/sca.log (test runs once filled it with fake portal lines)."""
    monkeypatch.setenv("SERA_SCA_LOG", str(tmp_path / "sca.log"))


@pytest.fixture(autouse=True)
def _assist_engine_from_tmp(monkeypatch, tmp_path):
    """automation.assist_engine() reads the test's own empty settings.ini (so: 'extension'), never
    the real ~/AmanAssociates_Sera/settings.ini - with the owner's 'assist_engine = os' there, SCA
    arms went to the OS engine and the SCA UID tests failed on this PC only. Tests that need the
    OS engine patch assist_engine or _settings_ini_path themselves."""
    monkeypatch.setattr("automation._settings_ini_path", lambda: tmp_path / "settings.ini")


@pytest.fixture(autouse=True)
def _reconciliation_log_in_tmp(monkeypatch, tmp_path):
    """The accountant reconciliation log (core/vsdc/vsdc247_reconciler) goes to the test's own
    folder, never to the real ~/AmanAssociates_Sera/logs/gst_reconciliation.txt."""
    monkeypatch.setenv("VSDC_ACCOUNTANT_LOG", str(tmp_path / "gst_reconciliation.txt"))
    monkeypatch.delenv("GST_RECONCILIATION_LOG", raising=False)

