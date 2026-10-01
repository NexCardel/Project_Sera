"""tools/sgt_i_id_census.py over fictional corpus records."""

from core.sgt_i import uia_nodes
from tools import sgt_i_id_census as census_tool

EDIT, TEXT, GROUP = 50004, 50020, 50026
URL = "https://portal.example/app/#/profile"


def _rec(session, nodes, v=3, url=URL):
    return {"v": v, "session": session, "portal": "Example", "url": url, "nodes": [nodes]}


def _page(session, n):
    """One read: a stable-id field, a generated-id field, a no-id field, a table of repeated
    labels, and Sera's own panel (which must not count)."""
    return _rec(session, [
        {"parent": -1, "depth": 0, "ctype": EDIT, "name": "PAN", "aid": "pan", "value": "AAAAA0000A"},
        {"parent": -1, "depth": 0, "ctype": EDIT, "name": "Mobile", "aid": f"mat-input-{n}"},
        {"parent": -1, "depth": 0, "ctype": EDIT, "name": "Email"},
        {"parent": -1, "depth": 0, "ctype": EDIT, "name": "Amount", "aid": "row-1"},
        {"parent": -1, "depth": 0, "ctype": EDIT, "name": "Amount", "aid": "row-2"},
        {"parent": -1, "depth": 0, "ctype": GROUP, "name": "", "own": True},
        {"parent": 5, "depth": 1, "ctype": EDIT, "name": "Sera note", "aid": "sera-x"},
        {"parent": -1, "depth": 0, "ctype": TEXT, "name": "Welcome"},
    ])


def test_fields_sorted_by_stability():
    pages = [_page(f"s{i}", i) for i in range(3)] + [_rec("old", [{"ctype": EDIT, "name": "PAN"}], v=2)]
    result = census_tool.census(pages, min_sessions=3)
    assert result["skipped_old"] == 1
    s = result["portals"]["Example"]
    assert (s["reads"], s["sessions"], s["pages"], s["pages_judged"]) == (3, 3, 1, 1)
    labels = {k: sorted(t[1] for t in v) for k, v in s["field_labels"].items()}
    assert labels == {"stable": ["PAN"], "shifting": ["Mobile"], "partial": [], "none": ["Email"],
                      "repeating": ["Amount"]}
    # Sera's own panel is left out: 5 portal fields per read, 4 with an id.
    assert (s["fields"], s["fields_id"], s["text"], s["text_id"]) == (15, 12, 18, 12)
    # pan, row-1, row-2 are on every session; the three mat-input ids on one session each.
    assert s["ids"] == {"judged": 6, "steady": 3, "one_type": 6, "unique": 6, "usable": 3, "one_session": 3}


def test_too_few_sessions_not_judged():
    s = census_tool.census([_page("s0", 0), _page("s1", 1)], min_sessions=3)["portals"]["Example"]
    assert s["pages_judged"] == 0 and s["ids"]["judged"] == 0
    assert all(not v for v in s["field_labels"].values()) and s["labels_too_few"] == 4


def test_report_masks_ids_with_digits_and_handles_empty():
    lines = []
    census_tool.report(census_tool.census([_page(f"s{i}", i) for i in range(3)]), 3, 5, out=lines.append)
    text = "\n".join(lines)
    assert "-> pan" in text and "AAA-AAAAA-9" in text and "mat-input" not in text
    empty = []
    census_tool.report(census_tool.census([]), 3, 5, out=empty.append)
    assert "No corpus v3" in empty[0]


class _El:
    def __init__(self, props):
        self.props = props

    def GetCachedPropertyValue(self, pid):
        return self.props.get(pid)


def test_reader_keeps_html_id():
    n = uia_nodes._node(_El({uia_nodes.PID_CONTROL_TYPE: EDIT, uia_nodes.PID_NAME: "PAN",
                             uia_nodes.PID_AUTOMATION_ID: " pan "}), -1, 0)
    assert n["aid"] == "pan" and "own" not in n
    n = uia_nodes._node(_El({uia_nodes.PID_CONTROL_TYPE: GROUP, uia_nodes.PID_AUTOMATION_ID: "sera-mecp-host"}), -1, 0)
    assert n["own"] is True
    n = uia_nodes._node(_El({uia_nodes.PID_CONTROL_TYPE: TEXT, uia_nodes.PID_NAME: "x"}), -1, 0)
    assert "aid" not in n


def test_key_probe_filters_chromium_defaults():
    from tools import sgt_i_key_probe as probe
    assert probe._aria("readonly=true;expanded=false;level=2;required=true;relevant=additions text", 50020) \
        == "level=2;required=true"
    assert probe._aria("readonly=true;expanded=false", EDIT) == "readonly=true"


def test_key_probe_report_marks_keys_sgt_cannot_see():
    from tools import sgt_i_key_probe as probe
    base = {"parent": -1, "depth": 0, "rect": None, "id": "", "cls": "", "role": "", "aria": "",
            "help": "", "desc": "", "required": False, "disabled": False, "value": ""}
    pan = dict(base, ctype=EDIT, name="PAN", id="pan", cls="form-control", aria="required=true", required=True)
    trader = dict(base, ctype=TEXT, name="Test Trader")
    control = [[pan, trader]]
    raw = [[pan, dict(base, ctype=GROUP, name="", id="legal-name", cls="val-text"), dict(trader, parent=1, depth=1),
            dict(base, ctype=GROUP, name="", id="row-1"), dict(base, ctype=GROUP, name="", id="row-1")]]
    text = "\n".join(probe.build_report(control, raw))
    assert "ids used more than once : 1  (row-1)" in text
    tree = text[text.index("PAGE TREE"):].splitlines()[2:]
    assert tree == [
        "  [Edit] #pan  .form-control  aria: required=true  [required] 'PAN'",
        "~ [Group] #legal-name  .val-text",
        "    [Text] 'Test Trader'",
        "~ [Group] #row-1",
        "~ [Group] #row-1",
    ]
