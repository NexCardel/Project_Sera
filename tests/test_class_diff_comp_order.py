"""
SDIS pre-dev: the comp page keeps PAGE order (tools/pre_dev/class_diff/snapshot_diff.py, AnchorPage).
A node that appears after the anchor page is fixed sits where its snapshot showed it, inside its own
parent; it is not appended at the end. Seen 2026-10-16: a sixth ITR card that loaded late went to the end
of the comp page, 25 leaves away from the other five, so the arrangement found 5 cards of 6.
Fictional fixtures only (the card shape of test_class_diff_arrange.py).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "pre_dev" / "class_diff"))
sys.path.insert(0, str(ROOT / "tests"))

import arrange as A  # noqa: E402
from snapshot_diff import AnchorPage  # noqa: E402
from test_class_diff_arrange import Page, _itr_card  # noqa: E402


def _page(n_cards, footer=True, extra_header=False):
    p = Page()
    root = p.add(-1, "Group", rect=[0, 0, 1200, 3000])
    p.add(root, "Text", name="Filed returns", rect=[0, 0, 300, 20])
    if extra_header:
        p.add(root, "Text", name="Notice", rect=[400, 0, 300, 20])
    lst = p.add(root, "Group", rect=[0, 40, 1100, 2900])
    for k in range(n_cards):
        _itr_card(p, lst, 0, 40 + 400 * k)
    if footer:
        p.add(root, "Text", name="Footer", rect=[0, 2950, 300, 20])
    return p.read()


def _contiguous_subtrees(fl):
    kids = [[] for _ in fl]
    for i, e in enumerate(fl):
        if e["parent"] >= 0:
            kids[e["parent"]].append(i)
    for r in range(len(fl)):
        st, sub = [r], []
        while st:
            j = st.pop()
            sub.append(j)
            st += kids[j]
        if max(sub) - min(sub) + 1 != len(sub):
            return False
    return True


def _comp(*reads, anchor="settled"):
    ap = AnchorPage("fictional.example/order", "s", "msedge", anchor)
    for t, rec in enumerate(reads):
        ap.add(rec, f"+{t}.0s", quiet_before=t > 0)
    return ap, ap.comp_flat()


def test_a_card_that_loads_after_the_anchor_is_fixed_sits_beside_the_other_cards():
    ap, fl = _comp(_page(3), _page(3), _page(4))
    assert ap.settled_at == "+0.0s"
    assert sum(1 for e in fl if e["origin"] == "attached" and e["text"]) > 0
    assert _contiguous_subtrees(fl)
    footer = [i for i, e in enumerate(fl) if e["text"] == "Footer"]
    attached = [i for i, e in enumerate(fl) if e["origin"] == "attached"]
    assert footer and max(attached) < footer[0]            # the new card comes before the footer, not after
    cards = [g for g in A.arrange(fl).groups if g.parent is None and g.period >= 27]
    assert len(cards) == 1 and len(cards[0].members) == 4


def test_an_attached_node_keeps_its_real_parent():
    _, fl = _comp(_page(2), _page(2), _page(3))
    for i, e in enumerate(fl):
        if e["origin"] == "attached" and e["parent"] >= 0:
            assert e["parent"] < i                          # the parent comes first, as in a page read
            assert fl[e["parent"]]["depth"] == e["depth"] - 1


def test_a_node_added_before_the_first_node_of_its_parent_goes_in_front():
    _, fl = _comp(_page(2), _page(2), _page(2, extra_header=True))
    texts = [e["text"] for e in fl if e["text"]]
    assert texts.index("Notice") == texts.index("Filed returns") + 1
    assert _contiguous_subtrees(fl)


def test_a_returned_node_keeps_its_place():
    _, fl = _comp(_page(2), _page(2), _page(2, footer=False), _page(2))
    footer = [e for e in fl if e["text"] == "Footer"]
    assert len(footer) == 1 and footer[0]["present"] is True and footer[0]["came_back"] == 1
    assert fl.index(footer[0]) == len(fl) - 1               # it was last on the page, and still is
    assert _contiguous_subtrees(fl)


def test_anchor_first_mode_also_keeps_page_order():
    _, fl = _comp(_page(2), _page(3), anchor="first")
    assert _contiguous_subtrees(fl)
    cards = [g for g in A.arrange(fl).groups if g.parent is None and g.period >= 27]
    assert len(cards) == 1 and len(cards[0].members) == 3


def test_a_subtree_is_never_split_even_when_an_inner_node_was_paired_far_away():
    # Real case (ITR, 2026-10-16): one button of a late card was paired with an older button elsewhere,
    # and the card's following nodes were placed after that button. Simulate it: move part of the new
    # card's nodes to the end of the insertion order; the comp page still keeps the card in one piece.
    ap, _ = _comp(_page(3), _page(3), _page(4))
    new = [k for k in ap.order if ap.entries[k]["origin"] == "attached"]
    tail = new[len(new) // 2:]
    ap.order = [k for k in ap.order if k not in tail] + tail
    fl = ap.comp_flat()
    assert _contiguous_subtrees(fl)
    cards = [g for g in A.arrange(fl).groups if g.parent is None and g.period >= 27]
    assert len(cards) == 1 and len(cards[0].members) == 4


def test_same_snapshots_same_comp_page():
    a = _comp(_page(3), _page(3), _page(4))[1]
    b = _comp(_page(3), _page(3), _page(4))[1]
    assert [(e["type"], e["text"], e["parent"]) for e in a] == [(e["type"], e["text"], e["parent"]) for e in b]
