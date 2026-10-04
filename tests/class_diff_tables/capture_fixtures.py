"""Reads the fictional table page in Edge, Chrome and Firefox and saves the raw-view nodes as fixtures.

    ../APP/venv/Scripts/python.exe tests/class_diff_tables/capture_fixtures.py [edge chrome firefox]

Writes tables.html (make_tables.page()), serves this folder on 127.0.0.1, opens each browser with a
THROWAWAY profile (a temp folder, deleted afterwards) on that page only, reads the window with the
same calls key_probe.py --once makes (raw view, each node marked whether the control view has it),
closes only the process it started, and writes tables_<browser>.json. Fictional data only; the
page link is stored as test.local so no local path or port lands in a fixture.
"""
import ctypes
import functools
import http.server
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "pre_dev" / "class_diff"))

import key_probe   # noqa: E402
import make_tables  # noqa: E402

BROWSERS = {
    "edge": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "firefox": r"C:\Program Files\Mozilla Firefox\firefox.exe",
}
# Firefox's first-run and terms screens would cover the page in a fresh profile.
FIREFOX_USER_JS = "\n".join(f'user_pref("{k}", {v});' for k, v in {
    "browser.aboutwelcome.enabled": "false", "datareporting.policy.dataSubmissionEnabled": "false",
    "browser.shell.checkDefaultBrowser": "false", "toolkit.telemetry.reportingpolicy.firstRun": "false",
    "browser.startup.homepage_override.mstone": '"ignore"', "trailhead.firstrun.didSeeAboutWelcome": "true",
    "termsofuse.bypassNotification": "true", "browser.preferences.moreFromMozilla": "false",
    "datareporting.policy.dataSubmissionPolicyBypassNotification": "true",
}.items()) + "\n"


def _serve():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(HERE))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _args(name, exe, profile, url):
    if name == "firefox":
        (Path(profile) / "user.js").write_text(FIREFOX_USER_JS, encoding="utf-8")
        return [exe, "-no-remote", "-profile", profile, "-width", "1400", "-height", "1000", url]
    return [exe, f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
            "--disable-sync", "--force-renderer-accessibility", "--new-window", "--window-size=1400,1000", url]


def capture(name: str, url: str) -> bool:
    exe = BROWSERS[name]
    if not Path(exe).exists():
        print(f"{name}: not installed, skipped")
        return False
    profile = tempfile.mkdtemp(prefix=f"sdis_tables_{name}_")
    proc = subprocess.Popen(_args(name, exe, profile, url))
    try:
        hwnd = 0
        for _ in range(40):
            time.sleep(0.5)
            hwnd, _title = key_probe.find_hwnd_by_title_substring(make_tables.TITLE)
            if hwnd:
                break
        if not hwnd:
            print(f"{name}: window not found")
            return False
        time.sleep(2.0)                         # let layout settle (boxes are what the extractor uses)
        for _ in range(key_probe.WARM_READS):
            key_probe.read_keys(hwnd, False)
        # Chrome builds its accessibility tree late in a fresh profile: read until the page shows.
        for _ in range(20):
            control = key_probe.read_keys(hwnd, False)
            raw = key_probe.read_keys(hwnd, True)
            if sum(len(d) for d in raw) > 50:
                break
            time.sleep(1.0)
        else:
            print(f"{name}: the page never showed up in UI Automation - fixture NOT overwritten")
            return False
        docs = key_probe._typed(raw, {key_probe._identity(n) for doc in control for n in doc})
        text = json.dumps(docs, ensure_ascii=False).replace(url.rsplit("/", 1)[0], "http://test.local")
        record = {"title": make_tables.TITLE, "page": "test.local/tables.html", "browser": name,
                  "read_at": "2026-10-05T00:00:00", "docs": json.loads(text)}
        (HERE / f"tables_{name}.json").write_text(json.dumps(record, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"{name}: {sum(len(d) for d in docs)} nodes saved")
        return True
    finally:
        # Only the process this script started (Chromium's children follow the parent's tree).
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        time.sleep(1.5)
        # A launcher that handed off to another process leaves the test window open: close THAT
        # window only (found by the fictional page's title), never any other.
        for _ in range(3):
            left, _t = key_probe.find_hwnd_by_title_substring(make_tables.TITLE)
            if not left:
                break
            ctypes.windll.user32.PostMessageW(left, 0x0010, 0, 0)        # WM_CLOSE
            time.sleep(1.5)
        shutil.rmtree(profile, ignore_errors=True)


def main(names):
    (HERE / "tables.html").write_text(make_tables.page(), encoding="utf-8")
    srv = _serve()
    url = f"http://127.0.0.1:{srv.server_address[1]}/tables.html"
    try:
        for name in names or list(BROWSERS):
            capture(name, url)
    finally:
        srv.shutdown()


if __name__ == "__main__":
    main(sys.argv[1:])
