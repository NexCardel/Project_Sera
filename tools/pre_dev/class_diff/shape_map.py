"""
tools/pre_dev/class_diff/shape_map.py - PRE-DEV: a page's container shapes as a map, and the patterns they imply
==============================================================================================================
Builds on render.py --outline: every container (a node with children and a box in the view) has a
shape (its key without the [n] counters: types, ids, classes) and a loose shape (types only). This tool
groups the containers of one page by shape and reads the groups the way a person reads the outline view:

  record set   a shape with 2+ instances that do not contain each other, each hold 2+ texts and are alike
               (fewest texts >= LIKE x most), and that does not sit inside another record set (top level).
  layout       column (same left edge), row (same top), grid (both repeat), scattered; the gaps between
               neighbours and the spread of the sizes.
  lift         when the instances' ancestors one level or more up are still one per instance, disjoint and
               of one loose shape, the record is probably that ancestor (the tile around the part found).
  parts        repeated shapes inside the members: 1 per member = a part of the record (a block, a
               stepper), k per member = a nested list (3 steps per card), varying = optional or a list.
  loose merge  instances of 2+ strict shapes that share one loose shape, lie disjoint and line up: the
               same record with a class that differs (an active tile, a highlighted row).

Nothing here knows a portal, a class name or a wording. Deterministic: the same input gives the same
bytes. The console gets counts, sizes and the last two steps of each shape (template class names) - no
page text. The HTML map draws text boxes as grey bars, never their text.

Usage:
    python tools/pre_dev/class_diff/shape_map.py FILE [--snapshot N] [--doc N] [--open]
FILE: a comp page, a key_probe capture or read (.json), or a record file (.jsonl, with --record N).
Writes output/shape_map_<file>.html.
"""

import argparse
import json
import sys
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import render  # noqa: E402

TOL = 4                 # pixels: two edges this close are the same line (render boxes are whole pixels)
MIN_TEXTS = 2           # a record holds at least this many drawn texts
LIKE = 0.5              # PROVISIONAL, not measured: members are alike when fewest texts >= LIKE x most texts.
                        # Keeps a generic layout wrapper that repeats with unlike contents (a page "row" of 3
                        # texts and one of 23) from being a record set that swallows the real one inside it.


def page_containers(raw: List[Dict[str, Any]], comp_view: str = "present") -> Dict[str, Any]:
    """The outline view's data for one doc: containers (render._outline_els), drawn texts, and for each
    container its nearest container ancestor."""
    nodes = render._norm(raw)
    render._decide(nodes, comp_view)
    view = [n for n in nodes if n["in_view"] and render._pos(n["rect"])]
    ox = min((n["rect"][0] for n in view), default=0)
    oy = min((n["rect"][1] for n in view), default=0)
    W = max((n["rect"][0] + n["rect"][2] for n in view), default=0) - ox
    H = max((n["rect"][1] + n["rect"][3] for n in view), default=0) - oy
    ols, shapes, loose = render._outline_els(raw, nodes, ox, oy)
    by_k = {e["k"]: e for e in ols}
    for e in ols:
        p = nodes[e["k"]]["p"]
        while p >= 0 and p not in by_k:
            p = nodes[p]["p"]
        e["up"] = p                                  # nearest outlined ancestor, -1 = none
    texts = []
    for n in nodes:
        if n["text_drawn"]:
            x, y, w, h = n["rect"]
            p = n["p"]
            while p >= 0 and p not in by_k:
                p = nodes[p]["p"]
            texts.append({"k": n["i"], "x": x - ox, "y": y - oy, "w": w, "h": h, "up": p})
    return {"W": W, "H": H, "ols": ols, "by_k": by_k, "shapes": shapes, "loose": loose, "texts": texts}


def _ancestors(by_k: Dict[int, Dict[str, Any]], k: int) -> List[int]:
    out = []
    p = by_k[k]["up"]
    while p >= 0:
        out.append(p)
        p = by_k[p]["up"]
    return out


def _tail(shape: str, steps: int = 2) -> str:
    return " / ".join(shape.split(" / ")[-steps:])


def _spread(vals: List[int]) -> int:
    return max(vals) - min(vals) if vals else 0


def layout(insts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """column / row / grid / scattered, with the gaps between neighbours along the line."""
    xs = sorted({e["x"] for e in insts})
    ys = sorted({e["y"] for e in insts})

    def lines(vals):
        out = []
        for v in vals:
            if not out or v - out[-1] > TOL:
                out.append(v)
        return out
    cols, rows = lines(xs), lines(ys)
    n = len(insts)
    if len(cols) == 1:
        kind, order = "column", sorted(insts, key=lambda e: e["y"])
        gaps = [b["y"] - (a["y"] + a["h"]) for a, b in zip(order, order[1:])]
    elif len(rows) == 1:
        kind, order = "row", sorted(insts, key=lambda e: e["x"])
        gaps = [b["x"] - (a["x"] + a["w"]) for a, b in zip(order, order[1:])]
    elif len(cols) * len(rows) >= n and len(cols) * len(rows) <= 2 * n and len(cols) > 1 and len(rows) > 1:
        kind, gaps = f"grid {len(rows)}x{len(cols)}", []
    else:
        kind, gaps = "scattered", []
    return {"kind": kind, "gap_min": min(gaps) if gaps else None, "gap_max": max(gaps) if gaps else None,
            "w_spread": _spread([e["w"] for e in insts]), "h_spread": _spread([e["h"] for e in insts])}


def infer(pc: Dict[str, Any]) -> Dict[str, Any]:
    """Record sets, their parts and nested lists, lifts and loose merges (module doc)."""
    ols, by_k = pc["ols"], pc["by_k"]
    anc = {e["k"]: set(_ancestors(by_k, e["k"])) for e in ols}
    groups: Dict[int, List[Dict[str, Any]]] = {}
    for e in ols:
        groups.setdefault(e["sh"], []).append(e)

    def disjoint(insts):
        ks = {e["k"] for e in insts}
        return all(not (anc[e["k"]] & ks) for e in insts)

    repeated = {sh: sorted(v, key=lambda e: (e["y"], e["x"], e["k"])) for sh, v in groups.items()
                if len(v) >= 2 and disjoint(v) and min(e["nt"] for e in v) >= MIN_TEXTS}
    alike = {sh: v for sh, v in repeated.items()
             if min(e["nt"] for e in v) >= LIKE * max(e["nt"] for e in v)}
    rec_of = {e["k"]: sh for sh, v in alike.items() for e in v}

    # top level: an alike repeated shape with no ancestor that is a member of another alike one
    top = [sh for sh, v in alike.items() if not any(a in rec_of for e in v for a in anc[e["k"]])]
    sets = []
    for sh in sorted(top, key=lambda s: (-sum(e["nt"] for e in repeated[s]), s)):
        insts = repeated[sh]
        members = {e["k"] for e in insts}
        # parts and nested lists: repeated shapes inside the members, counted per member
        inner = []
        for sh2, v2 in sorted(repeated.items()):
            if sh2 == sh:
                continue
            per = {m: 0 for m in members}
            outside = 0
            for e in v2:
                hit = members & anc[e["k"]]
                if hit:
                    per[next(iter(hit))] += 1
                else:
                    outside += 1
            if outside or not any(per.values()):
                continue
            counts = sorted(per.values())
            role = ("part" if counts[0] == counts[-1] == 1 else
                    f"list of {counts[0]}" if counts[0] == counts[-1] else "varies")
            inner.append({"shape": sh2, "tail": _tail(pc["shapes"][sh2]), "role": role,
                          "per_member": [per[e["k"]] for e in insts], "texts": v2[0]["nt"],
                          "members": [e["k"] for e in v2]})
        # nest the inner shapes: each under the inner shape whose instances hold it (the nearest one)
        inner_ks = {i["shape"]: {e["k"] for e in repeated[i["shape"]]} for i in inner}
        depth_of = {i["shape"]: repeated[i["shape"]][0]["dep"] for i in inner}
        for i in inner:
            holders = [o["shape"] for o in inner if o["shape"] != i["shape"]
                       and inner_ks[o["shape"]] & anc[repeated[i["shape"]][0]["k"]]]
            i["level"] = len(holders)
        inner.sort(key=lambda i: (min(e["y"] for e in repeated[i["shape"]]), depth_of[i["shape"]], i["shape"]))
        inner = _tree_order(inner, inner_ks, repeated, anc)
        lift = _lift(pc, insts, anc)
        sets.append({"shape": sh, "tail": _tail(pc["shapes"][sh]), "type": insts[0]["t"], "count": len(insts),
                     "texts": [e["nt"] for e in insts], "members": [e["k"] for e in insts],
                     "layout": layout(insts), "lift": lift, "inner": inner})
    return {"sets": sets, "merges": _loose_merges(pc, groups, anc), "repeated": len(repeated)}


def _tree_order(inner, inner_ks, repeated, anc):
    """Depth-first: each inner shape right after the inner shape that holds it."""
    def holder(i):
        cands = [o for o in inner if o["shape"] != i["shape"]
                 and inner_ks[o["shape"]] & anc[repeated[i["shape"]][0]["k"]]]
        return max(cands, key=lambda o: o["level"])["shape"] if cands else None
    kids: Dict[Optional[int], List[Dict[str, Any]]] = {}
    for i in inner:
        kids.setdefault(holder(i), []).append(i)
    out: List[Dict[str, Any]] = []

    def walk(sh):
        for i in kids.get(sh, []):
            out.append(i)
            walk(i["shape"])
    walk(None)
    return out


def _lift(pc, insts, anc) -> Optional[Dict[str, Any]]:
    """Walk the instances' ancestors up together while they stay one per instance, disjoint, of one loose
    shape and hold no other instance's subtree. The last such level is the probable record."""
    by_k = pc["by_k"]
    cur = [e["k"] for e in insts]
    levels = 0
    best = None
    while True:
        up = [by_k[k]["up"] for k in cur]
        if any(u < 0 for u in up) or len(set(up)) != len(up):
            break
        if len({by_k[u]["lo"] for u in up}) != 1:
            break
        if any(set(up) & anc[u] for u in up):
            break
        cur, levels = up, levels + 1
        best = cur
    if not best:
        return None
    strict = len({by_k[k]["sh"] for k in best})
    e0 = by_k[best[0]]
    return {"levels": levels, "type": e0["t"], "members": best, "strict_shapes": strict,
            "loose_tail": _tail(pc["loose"][e0["lo"]]), "texts": [by_k[k]["nt"] for k in best]}


def _loose_merges(pc, groups, anc) -> List[Dict[str, Any]]:
    """One loose shape over 2+ strict shapes whose instances are disjoint, hold 2+ texts and line up."""
    by_lo: Dict[int, List[Dict[str, Any]]] = {}
    for e in pc["ols"]:
        if e["nt"] >= MIN_TEXTS:
            by_lo.setdefault(e["lo"], []).append(e)
    out = []
    for lo, v in sorted(by_lo.items()):
        strict = sorted({e["sh"] for e in v})
        if len(strict) < 2 or len(v) < 2:
            continue
        ks = {e["k"] for e in v}
        if any(anc[e["k"]] & ks for e in v):
            continue
        lay = layout(v)
        if lay["kind"] == "scattered":
            continue
        out.append({"loose": lo, "tail": _tail(pc["loose"][lo]), "count": len(v), "strict_shapes": len(strict),
                    "members": sorted(e["k"] for e in v), "layout": lay})
    return out


# ── output ────────────────────────────────────────────────────────────────────

def report(inf: Dict[str, Any]) -> List[str]:
    """Counts, sizes and shape tails only."""
    lines = [f"repeated shapes {inf['repeated']}, top-level record sets {len(inf['sets'])}, loose merges {len(inf['merges'])}"]
    for i, s in enumerate(inf["sets"]):
        lay = s["layout"]
        gaps = "" if lay["gap_min"] is None else f", gaps {lay['gap_min']}..{lay['gap_max']} px"
        lines.append(f"set {i}: {s['count']} x {s['type']}  texts {sorted(set(s['texts']))}  {lay['kind']}{gaps}, "
                     f"size spread {lay['w_spread']}x{lay['h_spread']} px   ...{s['tail'][-70:]}")
        if s["lift"]:
            L = s["lift"]
            lines.append(f"    lift: {L['levels']} level(s) up to {L['type']} (texts {sorted(set(L['texts']))}, "
                         f"{L['strict_shapes']} strict shape(s))  ...{L['loose_tail'][-60:]}")
        for p in s["inner"]:
            pad = "  " * p["level"]
            lines.append(f"    {pad}{p['role']:12s} {p['texts']:3d} texts  per member {p['per_member']}  ...{p['tail'][-60:]}")
    for m in inf["merges"]:
        lines.append(f"loose merge: {m['count']} containers of {m['strict_shapes']} strict shapes, {m['layout']['kind']}  ...{m['tail'][-60:]}")
    return lines


def build_html(pc: Dict[str, Any], inf: Dict[str, Any], meta: Dict[str, Any]) -> str:
    data = {"W": pc["W"], "H": pc["H"], "page": meta.get("page", ""), "source": meta.get("source", ""),
            "texts": [[t["x"], t["y"], t["w"], t["h"]] for t in pc["texts"]],
            "boxes": {str(e["k"]): [e["x"], e["y"], e["w"], e["h"]] for e in pc["ols"]},
            "sets": inf["sets"], "merges": inf["merges"], "report": report(inf)}
    blob = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE.replace("/*DATA*/null", blob)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="A page's container shapes as a map, and the record sets they imply.")
    ap.add_argument("src")
    ap.add_argument("--snapshot", type=int)
    ap.add_argument("--record", type=int)
    ap.add_argument("--doc", default="auto")
    ap.add_argument("--comp-view", default="present", choices=["present", "all"])
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args(argv)
    src = Path(a.src)
    docs, meta = render.load(src, snapshot=a.snapshot, record=a.record)
    k = render._pick_docs(docs, a.doc if a.doc in ("auto", "all") else int(a.doc))[0]
    pc = page_containers(docs[k], a.comp_view)
    inf = infer(pc)
    for line in report(inf):
        print(line)
    out = render.PRE_DEV_OUT / f"shape_map_{src.stem}.html"
    render.check_out(out)
    render.write_html(out, build_html(pc, inf, meta))
    print(f"-> {out.relative_to(render.REPO)}")
    if a.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shape map</title>
<style>
  :root { --bg:#f4f4f6; --ink:#1d1d1f; --muted:#6e6e73; --line:#c9c9ce; --card:#fff; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#1c1c1e; --ink:#e5e5ea; --muted:#98989d; --line:#48484a; --card:#2c2c2e; } }
  :root[data-theme="dark"] { --bg:#1c1c1e; --ink:#e5e5ea; --muted:#98989d; --line:#48484a; --card:#2c2c2e; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font:13px/1.45 system-ui, "Segoe UI", sans-serif; }
  header { padding:10px 16px; border-bottom:1px solid var(--line); }
  header .meta { color:var(--muted); overflow-wrap:anywhere; }
  .wrap { display:flex; gap:16px; padding:12px 16px; align-items:flex-start; flex-wrap:wrap; }
  .map { flex:0 0 auto; background:#fff; box-shadow:0 1px 4px rgba(0,0,0,.15); }
  .side { flex:1 1 360px; min-width:280px; }
  .set { background:var(--card); border:1px solid var(--line); border-left:6px solid; border-radius:6px; padding:8px 10px; margin-bottom:8px; cursor:pointer; }
  .set.on { box-shadow:0 0 0 2px #e8590c; }
  .set .h { font-weight:600; }
  .set .s { color:var(--muted); font:12px/1.4 ui-monospace, Consolas, monospace; overflow-wrap:anywhere; }
  .part { margin:4px 0 0 10px; font:12px/1.4 ui-monospace, Consolas, monospace; }
  pre { background:var(--card); border:1px solid var(--line); padding:8px; overflow:auto; font-size:12px; }
  label { display:inline-flex; gap:6px; align-items:center; margin-right:12px; cursor:pointer; }
</style>
</head>
<body>
<header><b>Shape map</b> <span class="meta" id="meta"></span><br>
  <label><input type="checkbox" id="showparts" checked> parts and nested lists</label>
  <label><input type="checkbox" id="showlift" checked> lifted records</label>
  <span class="meta">click a set to show only it · click again to show all</span>
</header>
<div class="wrap">
  <svg class="map" id="map"></svg>
  <div class="side"><div id="sets"></div><pre id="rep"></pre></div>
</div>
<script>
const D = /*DATA*/null;
const NS = 'http://www.w3.org/2000/svg';
const PAL = ['#1971c2','#e8590c','#2f9e44','#ae3ec9','#f08c00','#0c8599','#c2255c','#5c940d','#6741d9','#d9480f'];
const scale = Math.min(1, 640 / Math.max(1, D.W));
const svg = document.getElementById('map');
svg.setAttribute('width', Math.round(D.W * scale)); svg.setAttribute('height', Math.round(D.H * scale));
svg.setAttribute('viewBox', '0 0 ' + D.W + ' ' + D.H);
function rect(b, attrs) {
  const r = document.createElementNS(NS, 'rect');
  r.setAttribute('x', b[0]); r.setAttribute('y', b[1]); r.setAttribute('width', Math.max(1, b[2])); r.setAttribute('height', Math.max(1, b[3]));
  for (const [k, v] of Object.entries(attrs)) r.setAttribute(k, v);
  svg.appendChild(r); return r;
}
for (const t of D.texts) rect(t, {fill:'#c9c9ce'});
let sel = null;
const layers = [];
D.sets.forEach((s, i) => {
  const col = PAL[i % PAL.length], g = [];
  for (const k of s.members) g.push({r: rect(D.boxes[k], {fill:col, 'fill-opacity':.10, stroke:col, 'stroke-width':4 / scale}), kind:'set'});
  if (s.lift) for (const k of s.lift.members) g.push({r: rect(D.boxes[k], {fill:'none', stroke:col, 'stroke-width':2 / scale, 'stroke-dasharray':(10 / scale) + ' ' + (6 / scale)}), kind:'lift'});
  for (const q of s.inner) for (const k of q.members)
    g.push({r: rect(D.boxes[k], {fill:'none', stroke:col, 'stroke-opacity':.7, 'stroke-width':1 / scale}), kind:'part'});
  layers.push({i, g, col});
});
function apply() {
  const showLift = document.getElementById('showlift').checked;
  const showParts = document.getElementById('showparts').checked;
  for (const L of layers) for (const x of L.g) {
    const vis = (sel === null || sel === L.i) && (x.kind !== 'lift' || showLift) && (x.kind !== 'part' || showParts);
    x.r.style.display = vis ? '' : 'none';
  }
  document.querySelectorAll('.set').forEach((el, i) => el.classList.toggle('on', sel === i));
  document.querySelectorAll('.part').forEach(el => el.style.display = document.getElementById('showparts').checked ? '' : 'none');
}
const box = document.getElementById('sets');
D.sets.forEach((s, i) => {
  const el = document.createElement('div');
  el.className = 'set'; el.style.borderLeftColor = PAL[i % PAL.length];
  const lay = s.layout;
  const gaps = lay.gap_min === null ? '' : ', gaps ' + lay.gap_min + '..' + lay.gap_max + ' px';
  el.innerHTML = '<div class="h"></div><div class="s"></div>';
  el.querySelector('.h').textContent = 'set ' + i + ': ' + s.count + ' × ' + s.type + ' · ' + lay.kind + gaps;
  el.querySelector('.s').textContent = 'texts ' + [...new Set(s.texts)].sort((a, b) => a - b).join(', ') + ' · size spread ' + lay.w_spread + '×' + lay.h_spread + ' px\n…' + s.tail;
  if (s.lift) {
    const p = document.createElement('div'); p.className = 'part';
    p.textContent = 'lift ' + s.lift.levels + ' up → ' + s.lift.type + ' (texts ' + [...new Set(s.lift.texts)].join(', ') + ', ' + s.lift.strict_shapes + ' strict shape(s))';
    el.appendChild(p);
  }
  for (const q of s.inner) {
    const p = document.createElement('div'); p.className = 'part';
    p.style.marginLeft = (10 + 14 * q.level) + 'px';
    p.textContent = q.role + ' · ' + q.texts + ' texts · per member [' + q.per_member.join(', ') + '] · …' + q.tail;
    el.appendChild(p);
  }
  el.addEventListener('click', () => { sel = sel === i ? null : i; apply(); });
  box.appendChild(el);
});
document.getElementById('showparts').addEventListener('change', apply);
document.getElementById('showlift').addEventListener('change', apply);
document.getElementById('rep').textContent = D.report.join('\n');
document.getElementById('meta').textContent = [D.page, D.source].filter(Boolean).join(' · ');
apply();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    raise SystemExit(main())
