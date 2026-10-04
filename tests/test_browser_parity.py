"""tools/browser_parity.py: the table/diff logic on hand-made lines, and that closing only ever
targets the processes it started. No browser is opened here."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import browser_parity as bp  # noqa: E402


# ---- diff / table ----------------------------------------------------------------------------

def test_line_diff_counts_and_order():
    lines = {"chrome": ["Period", "May", "Selected: Quarterly"],
             "firefox": ["Period", "May", "April", "May", "Selected: Quarterly"]}
    assert bp.line_diff(lines) == [("May", {"chrome": 1, "firefox": 2}),
                                   ("April", {"chrome": 0, "firefox": 1})]
    assert bp.line_diff({"chrome": ["a", "b"], "msedge": ["a", "b"]}) == []


def test_same_order():
    assert bp.same_order({"chrome": ["a", "b", "c"], "firefox": ["a", "x", "b", "c", "c"]})
    assert not bp.same_order({"chrome": ["a", "b"], "firefox": ["b", "a"]})
    assert bp.same_order({})


def test_selected_and_foreign_lines():
    lines = ["Selected: Nil return", "GSTR-1", "BETA FOODS", "Selected: Return period = May"]
    assert bp.selected_lines(lines) == ["Selected: Nil return", "Selected: Return period = May"]
    # whole lines only: B's "-" never matches inside the shared "GSTR-1"
    assert bp.foreign_lines(lines, {"-", "BETA FOODS", ""}) == ["BETA FOODS"]


def test_client_values_flattens_nested():
    clients = {"A": {"gstin": "X1", "notice": None, "returns": [("Jun", "GSTR-1")], "cash": ("1", "2")}}
    assert bp.client_values(clients, "A") == {"X1", "Jun", "GSTR-1", "1", "2"}


def _read(lines, **kw):
    r = {"window": True, "url": "http://test.local/x", "lines": lines, "password_seen": False,
         "nodes_control": 3, "docs_control": 1, "nodes_raw": 5,
         "ms": {"url": 1, "lines": 2, "control": 3, "raw": 4}}
    r.update(kw)
    return r


def test_parity_table_rows_and_differences():
    results = {"chrome": {"form": _read(["Tax", "Selected: Monthly"])},
               "firefox": {"form": _read(["Tax", "Selected: Monthly", "June"], url="", password_seen=True)},
               "msedge": {"form": {"window": False}}}
    text = "\n".join(bp.parity_table(results))
    assert "| chrome | yes | yes | 2 | 1 | no |" in text
    assert "| firefox | yes | NO | 3 | 1 | YES |" in text
    assert "| msedge | NO |" in text
    assert "Lines that differ between browsers: 1;" in text
    assert "| `June` | 0 | 1 |" in text
    assert "- chrome Selected: lines: `Selected: Monthly`" in text


def test_parity_table_no_difference():
    results = {"chrome": {"client_A": _read(["a"], foreign=[])}, "firefox": {"client_A": _read(["a"], foreign=[])}}
    text = "\n".join(bp.parity_table(results))
    assert "Lines that differ between browsers: none; common lines in the same order: yes" in text
    assert "| 0 | 3 (1) | 5 |" in text            # other client's lines: 0


# ---- launch / fixture ------------------------------------------------------------------------

def test_launch_args_throwaway_profile():
    chrome = bp.launch_args("chrome", "chrome.exe", r"C:\t\p", ["u1", "u2"])
    assert chrome[0] == "chrome.exe" and r"--user-data-dir=C:\t\p" in chrome and chrome[-2:] == ["u1", "u2"]
    assert "--no-first-run" in chrome
    assert bp.launch_args("firefox", "ff.exe", "P", ["u1", "u2"]) == ["ff.exe", "-no-remote", "-profile", "P", "u1", "u2"]


def test_firefox_prefs(tmp_path):
    bp.write_firefox_prefs(str(tmp_path))
    text = (tmp_path / "user.js").read_text(encoding="utf-8")
    assert 'user_pref("browser.aboutwelcome.enabled", false);' in text
    assert 'user_pref("termsofuse.acceptedVersion", 999);' in text
    assert 'user_pref("startup.homepage_welcome_url", "");' in text


def test_fixture_record_hides_local_address(tmp_path, monkeypatch):
    monkeypatch.setenv("SDIS_DATA_DIR", str(tmp_path))
    docs = [[{"name": "Help desk", "value": "http://127.0.0.1:59064/client_A.html#"}]]
    rec = bp.fixture_record("SDIS align A", "http://127.0.0.1:59064/client_A.html", "firefox", docs)
    assert set(rec) == {"title", "page", "read_at", "docs", "browser"}
    assert "127.0.0.1" not in json.dumps(rec)
    assert rec["page"].startswith("test.local") and rec["docs"][0][0]["value"] == "http://test.local/client_A.html#"


# ---- processes: only the ones started here ---------------------------------------------------

def _table():
    return {100: (4, "firefox.exe"),          # started here
            101: (100, "firefox.exe"), 102: (100, "firefox.exe"), 103: (101, "firefox.exe"),
            104: (100, "crashreporter.exe"),  # not the browser's own exe: left alone
            200: (4, "firefox.exe"), 201: (200, "firefox.exe"),   # the user's own Firefox
            300: (4, "chrome.exe")}


def test_tree_pids_only_descendants_with_same_exe():
    assert sorted(bp.tree_pids(100, _table(), "firefox.exe")) == [100, 101, 102, 103]


def test_kill_tree_targets_only_started_tree(monkeypatch):
    monkeypatch.setattr(bp.time, "sleep", lambda s: None)
    tables = [_table(), {**_table(), 105: (102, "firefox.exe")}]   # a child appears late
    calls = []
    killed = bp.kill_tree(100, "firefox.exe", table_fn=lambda: tables.pop(0), terminate=calls.append)
    assert sorted(calls) == [100, 101, 102, 103, 105] == sorted(killed)


def test_kill_tree_skips_a_gone_or_reused_root(monkeypatch):
    monkeypatch.setattr(bp.time, "sleep", lambda s: None)
    reused = {100: (4, "notepad.exe"), 200: (4, "firefox.exe")}
    calls = []
    bp.kill_tree(100, "firefox.exe", table_fn=lambda: reused, terminate=calls.append)
    assert calls == []


def test_run_browser_closes_only_its_own_pids(monkeypatch, tmp_path):
    started = []

    class FakeProc:
        def __init__(self, args):
            self.pid = 1000 + 10 * len(started)
            started.append((self.pid, args))

        def wait(self, timeout=None):
            return 0

    def table():
        t = {200: (4, "firefox.exe"), 201: (200, "firefox.exe")}     # the user's own browser
        for pid, _ in started:
            t[pid] = (4, "firefox.exe")
            t[pid + 1] = (pid, "firefox.exe")
        return t

    terminated = []
    monkeypatch.setattr(bp.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(bp, "_process_table", table)
    monkeypatch.setattr(bp, "_terminate", terminated.append)
    monkeypatch.setattr(bp, "find_window", lambda pids, title: 0)     # no window: nothing is read
    monkeypatch.setattr(bp, "WINDOW_TIMEOUT", 0.0)
    monkeypatch.setattr(bp.time, "sleep", lambda s: None)
    def mkdtemp(prefix=""):
        path = tmp_path / f"{prefix}{len(started)}"
        path.mkdir()
        return str(path)
    monkeypatch.setattr(bp.tempfile, "mkdtemp", mkdtemp)
    results = bp.run_browser("firefox", r"C:\FF\firefox.exe", 1, {}, fixtures=False)
    assert len(started) == len(bp.PAGES)
    ours = {p for pid, _ in started for p in (pid, pid + 1)}
    assert set(terminated) == ours and not {200, 201} & set(terminated)
    assert all(not r["window"] and not r["profile_left"] for r in results.values())
    assert all("-profile" in args and "-no-remote" in args for _, args in started)
    assert all(os.path.isfile(os.path.join(args[args.index("-profile") + 1], "user.js")) is False
               for _, args in started)                               # profiles deleted afterwards


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
