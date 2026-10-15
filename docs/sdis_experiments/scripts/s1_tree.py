"""Shape-only outline of each comp page: type, depth, rect, text shape, #children. No values."""
import sys
from collections import Counter
from common import comp, children, rect, tshape, STAMPS

MAXDEPTH = int(sys.argv[1]) if len(sys.argv) > 1 else 6
ONLY = sys.argv[2] if len(sys.argv) > 2 else None   # node index to print the subtree of


def outline(fl, kids, i, depth=0, maxdepth=99):
    e = fl[i]
    r = rect(e)
    extra = ""
    if e.get("present") is False:
        extra += " LEFT"
    if len(e.get("values") or []) > 1:
        extra += f" values={len(e['values'])}"
    print(f"{'  ' * depth}[{i}] {e['type']} d{e['depth']} rect={r} text={tshape(e['text'])} kids={len(kids[i])}"
          f" role={e['node'].get('role') or ''}{extra}")
    if depth < maxdepth:
        for c in kids[i]:
            outline(fl, kids, c, depth + 1, maxdepth)


for stamp in STAMPS:
    rec = comp(stamp)
    fl = rec["nodes"]
    kids = children(fl)
    print(f"===== comp_{stamp}: {len(fl)} nodes, browser={rec.get('browser')}")
    print("depth histogram:", sorted(Counter(e["depth"] for e in fl).items()))
    tops = [i for i, e in enumerate(fl) if e["parent"] < 0]
    print("top-level nodes:", len(tops))
    if ONLY is not None:
        outline(fl, kids, int(ONLY), 0, MAXDEPTH)
    else:
        for t in tops:
            outline(fl, kids, t, 0, MAXDEPTH)
