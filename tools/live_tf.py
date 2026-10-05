"""Pytest plugin: print one T / F / S per test the moment it finishes,
then the list of failed tests at the end.

Run:  python -m pytest -p live_tf -p no:terminal  (with tools/ on PYTHONPATH)
Output goes to a handle this plugin owns, so tests that close sys.stdout
cannot break it.
"""
import os

_out = os.fdopen(os.dup(1), "w", encoding="utf-8", errors="replace", buffering=1)
_counts = {"T": 0, "F": 0, "S": 0}
_failed = []


_total = 0


def pytest_collection_modifyitems(items):
    global _total
    _total = len(items)


def _emit(letter, nodeid):
    _counts[letter] += 1
    if letter == "F":
        _failed.append(nodeid)
    done = sum(_counts.values())
    _out.write(f"\r{letter}  {done} of {_total} done   (T={_counts['T']} F={_counts['F']} S={_counts['S']})   ")
    _out.flush()


def pytest_runtest_logreport(report):
    if report.when == "call":
        if report.passed:
            _emit("T", report.nodeid)
        elif report.failed:
            _emit("F", report.nodeid)
        else:
            _emit("S", report.nodeid)
    elif report.when == "setup":
        if report.failed:
            _emit("F", report.nodeid)
        elif report.skipped:
            _emit("S", report.nodeid)
    elif report.when == "teardown" and report.failed:
        _emit("F", report.nodeid + " (teardown)")


def pytest_collectreport(report):
    if report.failed:
        _emit("F", f"{report.nodeid} (collection error)")


def pytest_sessionfinish(session, exitstatus):
    _out.write(f"\n--- T={_counts['T']} F={_counts['F']} S={_counts['S']} (exit {int(exitstatus)})\n")
    if _failed:
        _out.write("Failed:\n")
        for nodeid in _failed:
            _out.write(f"  {nodeid}\n")
    _out.flush()
