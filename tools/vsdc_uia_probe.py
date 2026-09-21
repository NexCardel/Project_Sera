"""
tools/vsdc_uia_probe.py — VSDC UI Automation Accessibility-Tree Probe
=======================================================================
Standalone diagnostic tool. NOT wired into the app or the VSDC pipeline.

Purpose: find out how much of a rendered GST/ITR portal page Chromium exposes
through its UI Automation accessibility tree (the same channel screen readers
use), and how fast it can be read — before deciding whether to build this
into VSDC as a replacement for OCR-based text capture on any given crosshair.

This never types, clicks, or submits anything — pure read-only observation,
same as the rest of VSDC.

Usage:
    1. Open Chrome/Edge, log into the GST or Income Tax portal, navigate to
       the screen you want to test (e.g. the GST welcome page, or a GSTR-1
       form-details page).
    2. Make sure that browser window is FOREGROUND (the active window).
    3. Run:  python tools/vsdc_uia_probe.py
    4. Watch the console — every step prints before AND after it runs, and
       every UIA call has a hard timeout, so if something hangs you'll see
       exactly where instead of staring at a blank terminal.
    5. Read the console summary, then open the saved .txt dump for full detail.

Run it once per screen you want to evaluate (welcome page, form-details page,
filing-success page, etc.) since each has a different DOM shape.
"""

import ctypes
from ctypes import wintypes
import os
import sys
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import comtypes.client

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

BROWSER_EXE_NAMES = ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe")

# UIA ControlType IDs (Windows SDK UIA_ControlTypeIds enum). Referenced as literals
# with comments rather than named constants, matching the existing convention in
# core/vsdc/vsdc_router.py — not every comtypes-generated wrapper exposes every
# named constant, but the numeric IDs are stable across Windows versions.
UIA_DOCUMENT_CONTROL_TYPE_ID = 50030
UIA_EDIT_CONTROL_TYPE_ID = 50004

CONTROL_TYPE_NAMES = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox",
    50004: "Edit", 50005: "Hyperlink", 50006: "Image", 50007: "ListItem",
    50008: "List", 50009: "Menu", 50010: "MenuBar", 50011: "MenuItem",
    50012: "ProgressBar", 50013: "RadioButton", 50014: "ScrollBar",
    50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab",
    50019: "TabItem", 50020: "Text", 50021: "ToolBar", 50022: "ToolTip",
    50023: "Tree", 50024: "TreeItem", 50025: "Custom", 50026: "Group",
    50027: "Thumb", 50028: "DataGrid", 50029: "DataItem", 50030: "Document",
    50031: "SplitButton", 50032: "Window", 50033: "Pane", 50034: "Header",
    50035: "HeaderItem", 50036: "Table", 50037: "TitleBar", 50038: "Separator",
    50039: "SemanticZoom", 50040: "AppBar",
}

def _init_com_on_worker_thread():
    """
    ThreadPoolExecutor spawns a plain OS thread with no COM apartment set up.
    comtypes (and UI Automation, which is COM underneath) requires each thread
    that touches COM objects to call CoInitialize first — the main thread gets
    this for free on first comtypes use, worker threads don't. max_workers=1
    means every task below reuses this same thread, so initializing once here
    (via the executor's `initializer` hook) covers every call.
    """
    import comtypes
    comtypes.CoInitialize()


_POOL = ThreadPoolExecutor(max_workers=1, initializer=_init_com_on_worker_thread)


def say(msg):
    """Print with a forced flush so output shows up immediately, even when the
    console would otherwise buffer it (e.g. when not attached to a real TTY)."""
    print(msg, flush=True)


def run_with_timeout(fn, timeout_sec, step_name):
    """
    Runs fn() with a hard wall-clock timeout. UIA calls made from a script with
    no Windows message loop can hang or genuinely deadlock on a complex page —
    this makes that visible ("TIMED OUT") instead of a silent frozen terminal.
    Note: on timeout the worker thread is abandoned (COM calls can't be safely
    force-killed), so the process may need a manual Ctrl+C / Task Manager kill
    after a timeout is reported.
    """
    say(f"  -> starting: {step_name} (timeout {timeout_sec}s)")
    t0 = time.perf_counter()
    future = _POOL.submit(fn)
    try:
        result = future.result(timeout=timeout_sec)
        say(f"  <- done: {step_name} ({(time.perf_counter() - t0):.1f}s)")
        return result, None
    except FutureTimeoutError:
        say(f"  !! TIMED OUT: {step_name} exceeded {timeout_sec}s — this call is hanging/deadlocked.")
        return None, "timeout"
    except Exception as e:
        say(f"  !! ERROR in {step_name}: {e}")
        return None, str(e)


def get_foreground_info():
    """Returns (hwnd, title, process_name) of the current foreground window."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0, "", ""
    length = user32.GetWindowTextLengthW(hwnd)
    buff = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buff, length + 1)
    title = buff.value.strip()

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    hproc = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    proc_name = ""
    if hproc:
        name_buff = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(512)
        if kernel32.QueryFullProcessImageNameW(hproc, 0, name_buff, ctypes.byref(size)):
            proc_name = name_buff.value.split("\\")[-1].lower()
        kernel32.CloseHandle(hproc)

    return hwnd, title, proc_name


def try_read_address_bar(uia, uia_client, root_element):
    """Best-effort read of the browser's own address bar, for context only."""
    try:
        condition = uia.CreatePropertyCondition(uia_client.UIA_ControlTypePropertyId, UIA_EDIT_CONTROL_TYPE_ID)
        edits = root_element.FindAll(uia_client.TreeScope_Descendants, condition)
        for i in range(edits.Length):
            edit = edits.GetElement(i)
            name = (edit.CurrentName or "").lower()
            auto_id = (edit.CurrentAutomationId or "").lower()
            if any(k in name for k in ("address", "search", "url")) or any(k in auto_id for k in ("address", "url", "view_")):
                val_pattern = edit.GetCurrentPattern(uia_client.UIA_ValuePatternId)
                if val_pattern:
                    val_obj = val_pattern.QueryInterface(uia_client.IUIAutomationValuePattern)
                    return val_obj.CurrentValue
    except Exception:
        pass
    return None


def find_document_elements(uia, uia_client, root_element):
    """Finds all Document-typed elements under root (each tab's actual page content)."""
    condition = uia.CreatePropertyCondition(uia_client.UIA_ControlTypePropertyId, UIA_DOCUMENT_CONTROL_TYPE_ID)
    found = root_element.FindAll(uia_client.TreeScope_Descendants, condition)
    return [found.GetElement(i) for i in range(found.Length)]


# Dropdowns / text fields keep what is SELECTED or TYPED in ValuePattern, not in Name; radio
# buttons and checkboxes keep whether they are ticked in SelectionItem / Toggle. Without these
# a dump shows a dropdown's label but never its chosen value - what VSDC-X and SGT really read.
_VALUE_TYPES = (50003, 50004, 50016)          # ComboBox, Edit, Spinner
_RADIO, _CHECKBOX = 50013, 50002


def _value_of(uia_client, el):
    try:
        p = el.GetCurrentPattern(uia_client.UIA_ValuePatternId)
        return (p.QueryInterface(uia_client.IUIAutomationValuePattern).CurrentValue or "").strip() if p else ""
    except Exception:
        return ""


def _ticked(uia_client, el, ctype):
    try:
        if ctype == _RADIO:
            p = el.GetCurrentPattern(uia_client.UIA_SelectionItemPatternId)
            return bool(p and p.QueryInterface(uia_client.IUIAutomationSelectionItemPattern).CurrentIsSelected)
        if ctype == _CHECKBOX:
            p = el.GetCurrentPattern(uia_client.UIA_TogglePatternId)
            return bool(p and p.QueryInterface(uia_client.IUIAutomationTogglePattern).CurrentToggleState == 1)
    except Exception:
        pass
    return False


def collect_descendant_text(uia, uia_client, root_element):
    """
    Pulls every descendant element under root_element and returns a list of
    (control_type_name, text, class_name) for elements with a Name or a value. The text
    is the Name, plus "  => <value>" for a dropdown / field that holds one, plus
    "  [selected]" for a ticked radio button / checkbox.
    Meant to be called through run_with_timeout — no printing in here since
    this runs on a worker thread.
    """
    results = []
    true_cond = uia.CreateTrueCondition()
    elements = root_element.FindAll(uia_client.TreeScope_Descendants, true_cond)
    count = elements.Length if elements else 0
    for i in range(count):
        try:
            el = elements.GetElement(i)
            name = (el.CurrentName or "").strip()
            ctype = el.CurrentControlType
            value = _value_of(uia_client, el) if ctype in _VALUE_TYPES else ""
            if not name and not value:
                continue
            text = name
            if value and value != name:
                text = f"{name}  => {value}".strip()
            if ctype in (_RADIO, _CHECKBOX) and _ticked(uia_client, el, ctype):
                text += "  [selected]"
            ctype_name = CONTROL_TYPE_NAMES.get(ctype, str(ctype))
            cls = el.CurrentClassName or ""
            results.append((ctype_name, text, cls))
        except Exception:
            continue
    return results, count


def write_dump(out_path, title, proc_name, url, source, results):
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"Window title : {title}\n")
        f.write(f"Process      : {proc_name}\n")
        f.write(f"Address bar  : {url}\n")
        f.write(f"Source       : {source}\n")
        f.write(f"Node count   : {len(results)}\n")
        f.write("=" * 70 + "\n\n")
        for ctype_name, name, cls in results:
            f.write(f"[{ctype_name:12s}] ({cls}) {name}\n")


def print_type_breakdown(results):
    by_type = {}
    for ctype_name, _name, _cls in results:
        by_type[ctype_name] = by_type.get(ctype_name, 0) + 1
    for ctype_name, cnt in sorted(by_type.items(), key=lambda x: -x[1]):
        say(f"    {ctype_name:15s} : {cnt}")


def main():
    say("=" * 70)
    say("VSDC UI Automation Accessibility-Tree Probe (diagnostic only)")
    say("=" * 70)

    countdown = 10
    say(f"\nSwitch to the browser tab now — capturing foreground window in {countdown}s...")
    for remaining in range(countdown, 0, -1):
        say(f"  {remaining}...")
        time.sleep(1)

    say("\n[1/6] Reading foreground window info...")
    hwnd, title, proc_name = get_foreground_info()
    if not hwnd:
        say("No foreground window detected. Aborting.")
        return
    say(f"  Foreground window : {title!r}")
    say(f"  Process           : {proc_name}")

    if not any(b in proc_name for b in BROWSER_EXE_NAMES):
        say("\n[!] Foreground window doesn't look like a browser. Make sure the")
        say("    portal tab is the active, focused window, then re-run.")
        return

    say("\n[2/6] Initializing UI Automation (first run can take up to ~30s while")
    say("       comtypes generates COM bindings for UIAutomationCore.dll)...")

    def _init_uia():
        uia_client = comtypes.client.GetModule("UIAutomationCore.dll")
        uia = comtypes.client.CreateObject(uia_client.CUIAutomation, interface=uia_client.IUIAutomation)
        return uia, uia_client

    init_result, err = run_with_timeout(_init_uia, 45, "UI Automation init")
    if err:
        say("\nUI Automation initialization failed or timed out. Stopping here.")
        return
    uia, uia_client = init_result

    say("\n[3/6] Getting root UIA element for the window...")
    root_result, err = run_with_timeout(lambda: uia.ElementFromHandle(hwnd), 15, "ElementFromHandle")
    if err or not root_result:
        say("Could not get root UIA element for the foreground window. Stopping here.")
        return
    root_element = root_result

    say("\n[4/6] Reading address bar (context only)...")
    url, _ = run_with_timeout(lambda: try_read_address_bar(uia, uia_client, root_element), 10, "address bar read")
    say(f"  Address bar : {url!r}")

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vsdc_uia_probe_output")
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"uia_probe_{ts}.txt")

    say("\n[5/6] Finding Document element(s) (the actual page content, this is the")
    say("       important pass — runs BEFORE the expensive whole-window walk)...")
    doc_elements_result, err = run_with_timeout(
        lambda: find_document_elements(uia, uia_client, root_element), 30, "find Document elements"
    )
    doc_elements = doc_elements_result or []
    say(f"  Found {len(doc_elements)} Document-typed element(s).")

    doc_results = []
    for idx, doc_el in enumerate(doc_elements):
        try:
            doc_name = (doc_el.CurrentName or "").strip()
        except Exception:
            doc_name = "(unnamed)"
        r, err = run_with_timeout(
            lambda de=doc_el: collect_descendant_text(uia, uia_client, de),
            60, f"walk Document[{idx}] {doc_name!r}"
        )
        if r:
            results, node_count = r
            say(f"    {node_count} descendant nodes, {len(results)} with text")
            doc_results.extend(results)

    if doc_results:
        say(f"\n  Document-scoped text found ({len(doc_results)} elements) — saving now")
        write_dump(out_path, title, proc_name, url, "Document-scoped", doc_results)
        say(f"  Saved (partial, in case Pass 6 hangs): {out_path}")
        say("\n  Breakdown by control type:")
        print_type_breakdown(doc_results)
    else:
        say("  No Document-scoped text found (or the walk timed out/errored above).")

    say("\n[6/6] Optional: whole-window walk (includes browser chrome — tabs, toolbar).")
    say("       This is the riskiest call (largest, most likely to hang). Ctrl+C now")
    say("       to skip it if Pass 5 above already gave you what you need.")
    whole_result, err = run_with_timeout(
        lambda: collect_descendant_text(uia, uia_client, root_element), 60, "whole-window walk"
    )
    if whole_result:
        whole_results, node_count = whole_result
        say(f"  {node_count} descendant nodes, {len(whole_results)} with text")
        final_results = doc_results if doc_results else whole_results
        source = "Document-scoped" if doc_results else "whole-window (no Document element found)"
        write_dump(out_path, title, proc_name, url, source, final_results)
        say(f"\n  Final dump saved to: {out_path}")

    say("\nDone. Run this again on other portal screens (welcome page, form-details,")
    say("filing-success) to compare. Share the saved .txt file(s) to review together.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("\nInterrupted by user.")
    finally:
        _POOL.shutdown(wait=False, cancel_futures=True)
