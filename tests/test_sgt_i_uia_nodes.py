"""The cached node reader's pure parts: the tree walk and the lines rebuilt from nodes (W2-1).
Fake cached elements stand in for UIA - no browser, no COM."""

from core.sgt_i import uia_nodes as un


class _Children:
    def __init__(self, items):
        self.items = items
        self.Length = len(items)

    def GetElement(self, i):
        return self.items[i]


class _El:
    def __init__(self, props, children=()):
        self.props = props
        self.children = list(children)

    def GetCachedPropertyValue(self, pid):
        return self.props.get(pid)

    def GetCachedChildren(self):
        return _Children(self.children)


def _el(name, ctype=50020, children=(), extra=None):
    props = {un.PID_NAME: name, un.PID_CONTROL_TYPE: ctype, un.PID_BOUNDING_RECT: (1.0, 2.0, 3.0, 4.0)}
    props.update(extra or {})
    return _El(props, children)


def _doc():
    return _El({}, [
        _el("Personal details", 50020, extra={un.PID_HEADING_LEVEL: 80052}),
        _el("Group", 50026, children=[
            _el("First Name", un.CT_EDIT, extra={un.PID_VALUE: " Test "}),
            _el("Original", un.CT_RADIOBUTTON, extra={un.PID_SELECTION_IS_SELECTED: True}),
            _el("Revised", un.CT_RADIOBUTTON, extra={un.PID_SELECTION_IS_SELECTED: False}),
        ]),
        _el("", 50026),
    ])


def test_walk_is_preorder_with_parent_depth_and_properties():
    nodes = []
    un._walk(_doc(), -1, 0, nodes)
    assert [n["name"] for n in nodes] == ["Personal details", "Group", "First Name", "Original", "Revised", ""]
    assert [n["parent"] for n in nodes] == [-1, -1, 1, 1, 1, -1]
    assert [n["depth"] for n in nodes] == [0, 0, 1, 1, 1, 0]
    assert nodes[0]["heading"] == 2 and nodes[0]["rect"] == (1, 2, 3, 4)
    assert nodes[2]["value"] == "Test"
    assert nodes[3]["selected"] is True and nodes[4]["selected"] is False


def test_lines_from_nodes_match_the_line_reader_shape():
    nodes = []
    un._walk(_doc(), -1, 0, nodes)
    assert un.lines_from_nodes([nodes]) == ["Personal details", "Group", "First Name", "Test", "Original", "Revised"]
    assert un.lines_from_nodes([nodes], include_selection=True)[-2:] == ["Selected: Original", "Revised"]
    assert un.lines_from_nodes([nodes], max_lines=2) == ["Personal details", "Group"]
