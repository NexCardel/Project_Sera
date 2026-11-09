"""core/vsdc/vsdc_windows: which windows the capture loop watches (never-miss blueprint 4.7, WP10).
pick_windows is the whole choice; the Win32 listing feeds it. Fictional windows only."""
from core.vsdc import vsdc_windows
from core.vsdc.vsdc_windows import MAX_WATCHED_WINDOWS, TopWindow, is_covered, pick_windows

FULL = (0, 0, 1920, 1040)
LEFT, RIGHT = (0, 0, 960, 1040), (960, 0, 1920, 1040)
SECOND = (1920, 0, 3840, 1040)


def chrome(hwnd, rect=FULL, title="Some page - Google Chrome", **kw):
    return TopWindow(hwnd=hwnd, rect=rect, title=title, proc_name="chrome.exe", **kw)


def other(hwnd, rect=FULL, **kw):
    return TopWindow(hwnd=hwnd, rect=rect, title="Book1 - Excel", proc_name="excel.exe", **kw)


def hwnds(infos):
    return [(i.hwnd, i.background) for i in infos]


def test_covered_windows_are_left_out():
    tops = [chrome(1), chrome(2), chrome(3, SECOND)]
    assert hwnds(pick_windows(1, tops)) == [(1, False), (3, True)]


def test_a_window_covered_by_two_windows_together_is_left_out():
    tops = [other(5, LEFT), chrome(1, RIGHT), chrome(2, FULL)]
    assert hwnds(pick_windows(1, tops)) == [(1, False)]


def test_a_window_with_a_strip_showing_is_kept():
    tops = [chrome(1, (0, 0, 1900, 1040)), chrome(2, FULL)]
    assert hwnds(pick_windows(1, tops)) == [(1, False), (2, True)]


def test_a_click_through_overlay_hides_nothing():
    tops = [other(5, FULL, covers=False), chrome(1, LEFT), chrome(2, RIGHT)]
    assert hwnds(pick_windows(1, tops)) == [(1, False), (2, True)]


def test_foreground_is_always_first():
    # A topmost browser window above the one in front is still listed after it.
    tops = [chrome(2, SECOND), chrome(1, LEFT), chrome(3, RIGHT)]
    out = pick_windows(1, tops)
    assert out[0].hwnd == 1 and not out[0].background
    assert all(i.background for i in out[1:])


def test_cap_keeps_foreground():
    tops = [chrome(h, (400 * h, 0, 400 * h + 390, 500)) for h in range(1, 7)]
    out = pick_windows(6, tops)
    assert len(out) == MAX_WATCHED_WINDOWS
    assert out[0].hwnd == 6 and not out[0].background
    assert [i.hwnd for i in out[1:]] == [1, 2]       # then by z-order


def test_portal_titled_windows_come_before_other_browser_windows():
    tops = [chrome(1, LEFT), chrome(2, (960, 0, 1440, 1040)), chrome(3, (1440, 0, 1920, 1040)),
            chrome(4, SECOND, title="Income Tax Department - Google Chrome")]
    assert [i.hwnd for i in pick_windows(1, tops)] == [1, 4, 2]


def test_signing_dialog_in_front_leaves_the_browser_behind_it_watched():
    tops = [other(9, (760, 380, 1160, 660)), chrome(1, FULL)]
    assert hwnds(pick_windows(9, tops)) == [(1, True)]


def test_a_program_in_front_maximised_hides_the_browser():
    tops = [other(9, FULL), chrome(1, FULL), chrome(2, SECOND)]
    assert hwnds(pick_windows(9, tops)) == [(2, True)]


def test_non_browser_and_untitled_windows_are_never_watched_behind():
    tops = [chrome(1, LEFT), other(5, RIGHT), chrome(2, SECOND, title=""),
            chrome(3, (0, 1040, 960, 2080), watchable=False)]
    assert hwnds(pick_windows(1, tops)) == [(1, False)]


def test_the_window_in_front_is_kept_even_when_not_watchable_behind():
    # The foreground is watched whatever its rectangle (a hung or off-monitor flag only applies behind).
    tops = [chrome(1, (0, 0, 0, 0), watchable=False, covers=False)]
    assert hwnds(pick_windows(1, tops)) == [(1, False)]


def test_is_covered_slack():
    assert is_covered((0, 0, 100, 100), [(0, 0, 99, 100)])        # a 1 px strip does not count
    assert not is_covered((0, 0, 100, 100), [(0, 0, 90, 100)])
    assert not is_covered((0, 0, 100, 100), [])


def test_browser_windows_falls_back_to_the_window_in_front(monkeypatch):
    def broken(_fg):
        raise OSError("no EnumWindows")
    monkeypatch.setattr(vsdc_windows, "top_windows", broken)
    monkeypatch.setattr(vsdc_windows, "window_info",
                        lambda h: vsdc_windows.WindowInfo(hwnd=h, title="Page - Google Chrome", proc_name="chrome.exe"))
    assert hwnds(vsdc_windows.browser_windows(7)) == [(7, False)]
    assert vsdc_windows.browser_windows(0) == []
