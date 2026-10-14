"""The change gate (core/vsdc/vsdc_gate): the frame signature, the D4 thresholds and the OCR flash rule.

SGT never-miss blueprint 4.4 / 5.1 / WP7. Pictures are drawn here; no screenshot, no page, no text of a portal.
"""

import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.vsdc import vsdc_gate

SIZE = (1280, 720)
PAPER = (245, 245, 245)


def page():
    img = Image.new("RGB", SIZE, PAPER)
    d = ImageDraw.Draw(img)
    for row in range(8):                                      # a few lines of fictional text-like bars
        d.rectangle((100, 120 + row * 60, 700, 130 + row * 60), fill=(90, 90, 90))
    return img


def sig(img):
    return vsdc_gate.frame_signature(img)


def test_the_thresholds_are_the_owners_d4_answer():
    assert vsdc_gate.GATE_MIN_CELLS == 3
    assert vsdc_gate.FLASH_MIN_CELLS == 8
    assert vsdc_gate.GATE_LEVEL_DELTA == 2
    assert vsdc_gate.FLASH_UIA_SLOW_MS == 700.0


def test_the_signature_is_1024_cells_and_none_without_a_picture():
    assert len(sig(page())) == 1024
    assert sig(None) is None
    assert vsdc_gate.cells_changed(None, sig(page())) == 1024


def test_caret_and_typing_change_under_three_cells():
    before = page()
    caret = before.copy()
    ImageDraw.Draw(caret).rectangle((410, 400, 411, 418), fill=(0, 0, 0))            # a caret
    assert vsdc_gate.cells_changed(sig(before), sig(caret)) < vsdc_gate.GATE_MIN_CELLS
    typing = before.copy()
    d = ImageDraw.Draw(typing)
    for i in range(6):                                                                 # six typed characters
        d.text((410 + i * 9, 400), "x", fill=(0, 0, 0))
    assert vsdc_gate.cells_changed(sig(before), sig(typing)) < vsdc_gate.GATE_MIN_CELLS


def test_a_toast_changes_eight_or_more_cells():
    before = page()
    toast = before.copy()
    ImageDraw.Draw(toast).rectangle((780, 40, 1230, 120), fill=(40, 120, 70))         # a banner top right
    cells = vsdc_gate.cells_changed(sig(before), sig(toast))
    assert cells >= vsdc_gate.FLASH_MIN_CELLS
    assert vsdc_gate.flash_wanted(cells, True, 0.0, False)


def test_a_small_banner_passes_the_read_gate_but_not_the_flash_gate():
    before = page()
    small = before.copy()
    ImageDraw.Draw(small).rectangle((780, 40, 900, 70), fill=(40, 120, 70))
    cells = vsdc_gate.cells_changed(sig(before), sig(small))
    assert vsdc_gate.GATE_MIN_CELLS <= cells < vsdc_gate.FLASH_MIN_CELLS
    assert not vsdc_gate.flash_wanted(cells, True, 5000.0, True)


def test_flash_wants_uia_to_be_busy_slow_or_paused():
    n = vsdc_gate.FLASH_MIN_CELLS
    assert not vsdc_gate.flash_wanted(n, False, 100.0, False)          # UIA is quick and free: it will see it
    assert vsdc_gate.flash_wanted(n, True, 100.0, False)               # a read of the window is running
    assert vsdc_gate.flash_wanted(n, False, vsdc_gate.FLASH_UIA_SLOW_MS + 1, False)
    assert not vsdc_gate.flash_wanted(n, False, vsdc_gate.FLASH_UIA_SLOW_MS, False)
    assert vsdc_gate.flash_wanted(n, False, 0.0, True)                 # UIA paused
    assert not vsdc_gate.flash_wanted(n - 1, True, 9000.0, True)


def banner(img, box, colour=(30, 150, 70)):
    out = img.copy()
    ImageDraw.Draw(out).rectangle(box, fill=colour)
    return out


def test_green_boxes_finds_a_success_banner_and_nothing_on_a_plain_page():
    assert vsdc_gate.green_boxes(None) == []
    assert vsdc_gate.green_boxes(page()) == []
    boxes = vsdc_gate.green_boxes(banner(page(), (300, 60, 900, 130)))
    assert len(boxes) == 1


def test_a_green_box_is_new_once():
    a = (300, 60, 600, 70)
    assert vsdc_gate.new_green_box((), [a])
    assert not vsdc_gate.new_green_box([a], [a])                       # it stays put
    assert not vsdc_gate.new_green_box([a], [(304, 62, 596, 68)])      # a few pixels of jitter
    assert vsdc_gate.new_green_box([a], [(300, 400, 600, 70)])         # another box elsewhere
    assert not vsdc_gate.new_green_box([a], [])
