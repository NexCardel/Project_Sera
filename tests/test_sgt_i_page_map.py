"""
Tests for core/sgt_i/page_map.py (blueprint 14.4 step 1). Synthetic node fixtures reproduce the
14.1 bug table cases; every name, PAN and value here is fictional.
"""

from core.sgt_i import page_map as pm

TEXT, EDIT, RADIO, GROUP, TABITEM, TABLE, CELL, HEADER_ITEM, COMBO, LIST, LISTITEM = (
    50020, 50004, 50013, 50026, 50019, 50036, 50029, 50035, 50003, 50008, 50007)


class Doc:
    """Builds one uia_nodes-style document: pre-order dicts with parent indices."""

    def __init__(self):
        self.nodes = []

    def add(self, name, rect=None, ctype=TEXT, parent=-1, **extra):
        depth = self.nodes[parent]["depth"] + 1 if parent >= 0 else 0
        node = {"parent": parent, "depth": depth, "ctype": ctype, "name": name,
                "rect": rect}
        node.update(extra)
        self.nodes.append(node)
        return len(self.nodes) - 1

    def map(self, **kw):
        return pm.build_page_map(pm.nodes_from_uia([self.nodes]), **kw)


def _values(page):
    return [p.value for p in page.pairs]


def _main(doc):
    return doc.add("", (0, 150, 1200, 900), GROUP, landmark=80002)


def test_option_list_never_beats_the_selected_option():
    d = Doc()
    main = _main(d)
    d.add("Filing details", (20, 160, 300, 30), parent=main, heading=2)
    d.add("Filed u/s:", (20, 200, 90, 20), parent=main)
    grp = d.add("", (130, 200, 400, 20), GROUP, parent=main)
    for i, (name, sel) in enumerate((("Original", True), ("Belated", False), ("Revised", False))):
        d.add(name, (130 + i * 130, 202, 16, 16), RADIO, parent=grp, selected=sel)
        d.add(name, (150 + i * 130, 200, 90, 20), parent=grp)
    combo = d.add("Status", (20, 260, 200, 24), COMBO, parent=main, value="Filed")
    lst = d.add("", (20, 284, 200, 60), LIST, parent=combo)
    d.add("Draft", (20, 284, 200, 20), LISTITEM, parent=lst)
    d.add("Pending", (20, 304, 200, 20), LISTITEM, parent=lst)
    page = d.map()
    by_label = {p.label: p for p in page.pairs}
    assert by_label["Filed u/s"].value == "Original"
    assert by_label["Filed u/s"].method == "choice"
    assert by_label["Status"].value == "Filed"
    for wrong in ("Belated", "Revised", "Draft", "Pending"):
        assert wrong not in _values(page)
    assert page.section_path(by_label["Filed u/s"].section) == ("Filing details",)


def test_a_pan_under_landlord_details_is_the_landlords():
    d = Doc()
    main = _main(d)
    d.add("Income from house property", (20, 160, 500, 34), parent=main, heading=1)
    d.add("Personal details", (20, 200, 300, 30), parent=main, heading=2)
    d.add("PAN", (20, 240, 60, 20), parent=main)
    d.add("ABCDE1234F", (200, 240, 120, 20), parent=main)
    d.add("Landlord details", (20, 290, 300, 30), parent=main, heading=2)
    d.add("PAN", (20, 330, 60, 20), parent=main)
    d.add("PQRSX6789K", (200, 330, 120, 20), parent=main)
    page = d.map()
    landlord = page.pairs_in("Landlord details")
    assert [(p.label, p.value) for p in landlord] == [("PAN", "PQRSX6789K")]
    assert page.section_path(landlord[0].section) == ("Income from house property",
                                                      "Landlord details")
    assert [p.value for p in page.pairs_in("Personal details")] == ["ABCDE1234F"]
    # a label never pairs across a section boundary, even when the layout would allow it
    assert all(p.section == page.nodes[p.label_node].section for p in page.pairs)


def test_search_box_is_a_control_not_content():
    d = Doc()
    search = d.add("", (600, 10, 400, 40), GROUP, landmark=80004)
    d.add("Search Box Input Field", (610, 15, 300, 30), EDIT, parent=search, value="")
    main = _main(d)
    d.add("Client name", (20, 200, 120, 20), parent=main)
    d.add("", (200, 198, 250, 24), EDIT, parent=main, value="Asha Verma")
    page = d.map()
    box = next(n for n in page.nodes if n.text == "Search Box Input Field")
    assert box.zone == pm.NAVIGATION
    assert not box.is_content
    assert box not in page.content()
    assert "Search Box Input Field" not in _values(page)
    assert "Search Box Input Field" not in [p.label for p in page.pairs]
    [pair] = page.pairs
    assert (pair.label, pair.value, pair.method) == ("Client name", "Asha Verma", "control")


def test_truncated_header_name_is_trusted_less_than_the_profile():
    d = Doc()
    banner = d.add("", (0, 0, 1200, 60), GROUP, role="banner")
    d.add("Welcome, ASHA VERMA SHAR...", (900, 20, 250, 20), parent=banner)
    main = _main(d)
    d.add("Name", (20, 200, 80, 20), parent=main)
    d.add("Asha Verma Sharma", (200, 200, 200, 20), parent=main)
    page = d.map()
    head = next(n for n in page.nodes if n.text.startswith("Welcome"))
    prof = next(n for n in page.nodes if n.text == "Asha Verma Sharma")
    assert head.zone == pm.HEADER and head.truncated
    assert prof.zone == pm.MAIN and not prof.truncated
    assert prof.trust > head.trust
    assert [(p.label, p.value) for p in page.pairs] == [("Name", "Asha Verma Sharma")]


def test_header_without_landmarks_is_the_top_band():
    d = Doc()
    d.add("Welcome, ASHA VE...", (900, 20, 200, 20))
    d.add("Name", (20, 300, 80, 20))
    d.add("Asha Verma", (200, 300, 200, 20))
    page = d.map()
    assert [n.zone for n in page.nodes] == [pm.HEADER, pm.MAIN, pm.MAIN]


def test_unvisited_stepper_step_is_upcoming_and_never_paired():
    d = Doc()
    main = _main(d)
    steps = ("Personal Info", "Income", "Tax Paid", "Return Successfully Verified")
    for i, name in enumerate(steps):
        d.add(str(i + 1), (40 + i * 250, 200, 20, 20), parent=main)
        d.add(name, (70 + i * 250, 200, 170, 20), TABITEM, parent=main, selected=(i == 0))
    d.add("Name", (20, 300, 80, 20), parent=main)
    d.add("Asha Verma", (200, 300, 200, 20), parent=main)
    page = d.map()
    state = {n.text: n.step for n in page.nodes if n.zone == pm.STEPPER and n.step}
    assert state == {"Personal Info": "current", "Income": "upcoming", "Tax Paid": "upcoming",
                     "Return Successfully Verified": "upcoming"}
    verified = next(n for n in page.nodes if n.text == "Return Successfully Verified")
    assert verified.trust < 0.5
    assert all(page.nodes[p.value_node].zone != pm.STEPPER for p in page.pairs)
    assert [(p.label, p.value) for p in page.pairs] == [("Name", "Asha Verma")]


def test_a_row_of_labels_is_not_a_stepper_and_pairs_below():
    d = Doc()
    main = _main(d)
    d.add("Name", (20, 200, 80, 20), parent=main)
    d.add("PAN", (300, 200, 80, 20), parent=main)
    d.add("Date of birth", (600, 200, 120, 20), parent=main)
    d.add("Asha Verma", (20, 225, 150, 20), parent=main)
    d.add("ABCDE1234F", (300, 225, 120, 20), parent=main)
    d.add("01/01/1990", (600, 225, 120, 20), parent=main)
    page = d.map()
    assert not any(n.zone == pm.STEPPER for n in page.nodes)
    assert {(p.label, p.value, p.method) for p in page.pairs} == {
        ("Name", "Asha Verma", "below"), ("PAN", "ABCDE1234F", "below"),
        ("Date of birth", "01/01/1990", "below")}


def test_table_cells_pair_with_their_column_header():
    d = Doc()
    main = _main(d)
    t = d.add("", (20, 200, 800, 100), TABLE, parent=main)
    d.add("Assessment Year", (20, 200, 200, 20), HEADER_ITEM, parent=t, role="columnheader",
          grid=(0, 0))
    d.add("Status", (300, 200, 200, 20), HEADER_ITEM, parent=t, role="columnheader", grid=(0, 1))
    for r, (ay, st) in enumerate((("2025-26", "Processed"), ("2024-25", "Filed")), start=1):
        c0 = d.add("", (20, 200 + r * 25, 200, 20), CELL, parent=t, grid=(r, 0))
        d.add(ay, (22, 200 + r * 25, 100, 20), parent=c0)
        d.add(st, (300, 200 + r * 25, 200, 20), CELL, parent=t, grid=(r, 1))
    page = d.map()
    assert [(p.label, p.value, p.row, p.method) for p in page.pairs] == [
        ("Assessment Year", "2025-26", 1, "column"), ("Status", "Processed", 1, "column"),
        ("Assessment Year", "2024-25", 2, "column"), ("Status", "Filed", 2, "column")]


def test_help_and_dialog_zones():
    d = Doc()
    main = _main(d)
    d.add("Frequently Asked Questions", (20, 600, 400, 30), parent=main, heading=2)
    d.add("Name", (20, 640, 80, 20), parent=main)
    d.add("Example Person", (200, 640, 200, 20), parent=main)
    dlg = d.add("", (300, 200, 500, 200), role="dialog")
    d.add("Success", (320, 210, 200, 30), parent=dlg, heading=2)
    d.add("Acknowledgement No.", (320, 260, 180, 20), parent=dlg)
    d.add("123456789012345", (520, 260, 180, 20), parent=dlg)
    page = d.map()
    zones = {p.value: p.zone for p in page.pairs}
    assert zones == {"Example Person": pm.HELP, "123456789012345": pm.DIALOG}
    ack = next(p for p in page.pairs if p.zone == pm.DIALOG)
    assert page.section_path(ack.section) == ("Success",)
    assert pm.ZONE_TRUST[pm.HELP] == 0.0


def test_ocr_lines_give_the_same_map():
    words = []
    lines = []
    for text, x, y, h in (("Personal details", 20, 100, 30), ("PAN", 20, 150, 18),
                          ("ABCDE1234F", 200, 150, 18), ("Landlord details", 20, 200, 30),
                          ("PAN", 20, 250, 18), ("PQRSX6789K", 200, 250, 18)):
        lines.append(text)
        for i, w in enumerate(text.split()):
            words.append({"text": w, "x": x + i * 90, "y": y, "width": 80, "height": h})
    boxes = pm.ocr_line_boxes({"lines": lines, "words": words})
    assert boxes[0] == {"text": "Personal details", "x": 20, "y": 100, "width": 170, "height": 30}
    page = pm.build_page_map(pm.nodes_from_ocr(boxes), header_band_px=0)
    assert [s.heading for s in page.sections] == ["Personal details", "Landlord details"]
    assert [p.value for p in page.pairs_in("Landlord details")] == ["PQRSX6789K"]
    assert [p.value for p in page.pairs_in("Personal details")] == ["ABCDE1234F"]
