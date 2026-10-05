"""client_ids on a recorder read (no "sgt" marks) must still find the PAN; a marked read keeps its filter."""
from types import SimpleNamespace

from core.sdis import identity

PAN = "ABCPE1234F"
LINK = "eportal.incometax.gov.in/iec/foservices#/dashboard/myProfile/profileDetail"


def _map(marks):
    lines = ["PAN", PAN, "Name", "A Person"]
    flat = [{"text": t, "node": ({"sgt": marks} if marks is not None else {})} for t in lines]
    return SimpleNamespace(link=LINK, to_flat=lambda: flat)


def test_unmarked_read_uses_every_line():
    assert identity.client_ids(_map(None)) == {f"pan:{PAN}"}


def test_marked_read_keeps_only_marked_lines():
    assert identity.client_ids(_map(False)) == set()
    assert identity.client_ids(_map(True)) == {f"pan:{PAN}"}


def test_slide_position_text_is_noise():
    from core.sdis.noise import is_slide_position
    for t in ("Media Reports 7 of 8", "NSDL 2 of 3", "Site Map 3 of 4", "Income Tax India 1 of 3"):
        assert is_slide_position(t)
    for t in ("3 of 4", "Pending 3", "Page", "Order of 2 items", ""):
        assert not is_slide_position(t)
