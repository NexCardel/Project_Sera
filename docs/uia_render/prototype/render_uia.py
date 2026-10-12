"""Render one raw-view UIA snapshot (key_probe capture JSON) as an HTML page.
Uses only what the read holds: control type, screen box, name, class names. No portal knowledge.
usage: python render_uia.py <capture.json> <out.html>"""
import json
import sys
from pathlib import Path

src, out = Path(sys.argv[1]), Path(sys.argv[2])
rec = json.loads(src.read_text(encoding="utf-8"))
snap = rec["snapshots"][-1]
doc = max(snap["docs"], key=len)

nodes = []
for i, n in enumerate(doc):
    r = n.get("rect")
    nodes.append({"i": i, "p": n.get("parent", -1), "t": n.get("type_name") or str(n.get("ctype")),
                  "r": list(r) if r else None, "n": n.get("name") or "", "c": n.get("cls") or "",
                  "id": n.get("id") or "", "v": n.get("value") or ""})

# a node's text is drawn only when no descendant carries a name (the leaf-most text wins)
kids = {}
for n in nodes:
    kids.setdefault(n["p"], []).append(n["i"])


def named_below(i):
    for k in kids.get(i, []):
        if nodes[k]["n"].strip() or named_below(k):
            return True
    return False


for n in nodes:
    n["draw"] = bool(n["n"].strip()) and not named_below(n["i"])

real = [n for n in nodes if n["r"] and n["r"][2] > 0 and n["r"][3] > 0]
x0 = min(n["r"][0] for n in real)
y0 = min(n["r"][1] for n in real)
W = max(n["r"][0] + n["r"][2] for n in real) - x0
H = max(n["r"][1] + n["r"][3] for n in real) - y0
for n in real:
    n["r"] = [n["r"][0] - x0, n["r"][1] - y0, n["r"][2], n["r"][3]]

data = json.dumps({"nodes": real, "W": W, "H": H, "page": rec.get("page", ""),
                   "title": rec.get("title", ""), "browser": rec.get("browser", "")}, ensure_ascii=False)
html = Path(__file__).with_name("render_uia_template.html").read_text(encoding="utf-8")
out.write_text(html.replace("/*DATA*/null", data.replace("</", "<\\/")), encoding="utf-8")
print(f"{len(real)} boxes, {sum(1 for n in real if n['draw'])} texts, page {W}x{H} -> {out}")
