"""
tools/pre_dev/class_diff/comp_map.py - PRE-DEV: the page map, one capture of one page link as a page a
non-technical person can read
=======================================================================================================
snapshot_diff.py merges every snapshot of a capture into one comp page. This tool draws that comp page
with the renderer's looks (core/uia_render/render.py: same boxes, same texts, same fit) and adds TIME:

  Everything     the complete map: every item the page ever showed, coloured by its story
                 (there from the start / appeared later / went away / came back / value changed).
                 Items that went away are drawn faded BEHIND the live page, so a popup that closed
                 no longer covers the page under it.
  At a moment    a slider (and Play) shows the page as it was at one snapshot, with the items that
                 appeared, changed or went away at that step lit up.
  What happened  the timeline in plain words: "+5 s: 3 new items appeared: July, August, September".
  Click an item  a plain card: what it is, when it appeared, whether it is still there, the values it showed.

Input: a comp_*.json (snapshot_diff.py --comp, or written by key_probe.py while capturing) or a
capture_*.json (the comp page is rebuilt from it). A comp page from before 2026-11-19 has no per-snapshot
record (seen / steps): its own capture_ file is used when it is still in output/, else first / last are
used and the items that came and went are marked "times approximate".

    python tools/pre_dev/class_diff/comp_map.py FILE [--out PATH] [--open]

Writes map_<input stem>.html beside the input (inside the repo only in output/). The page holds real
page values (client data): it stays on this PC, in the git-ignored output folder. The console prints
counts only. Same input, same bytes.
"""

import argparse
import json
import re
import sys
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
for _p in (str(REPO), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import render  # noqa: E402  (core/uia_render/render.py)
import snapshot_diff as sd  # noqa: E402

EXAMPLES = 3            # example texts per timeline step
EXAMPLE_LEN = 30        # characters per example text
_STAMP = re.compile(r"^\+?(-?\d+(?:\.\d+)?)s$")


def secs(stamp) -> float:
    """'+4.0s' -> 4.0; anything else -> 0.0."""
    m = _STAMP.match(str(stamp or "").strip())
    return float(m.group(1)) if m else 0.0


# ---------------------------------------------------------------- loading

def _approx(rec) -> int:
    """A comp page without seen / steps: one run from first to last. Exact for a node that never left and
    never came back; the others are marked approximate. Returns how many are approximate."""
    at = {row.get("snapshot"): i for i, row in enumerate(rec.get("log") or [])}
    n_snap = max(1, len(rec.get("log") or []))
    count = 0
    for n in rec.get("nodes") or []:
        a = at.get(n.get("first"), 0)
        b = at.get(n.get("last"), n_snap - 1)
        n["seen"] = [[a, max(a, b)]]
        n["steps"] = [[a, n.get("text") or ""]]
        if n.get("left") or n.get("came_back"):
            n["approx"] = True
            count += 1
    return count


def load(path):
    """(comp record, note). The record always has seen / steps on every node."""
    path = Path(path)
    rec = json.loads(path.read_text(encoding="utf-8"))
    if rec.get("capture") or ("snapshots" in rec and not rec.get("comp_page")):
        ap, _ = sd.comp_from_capture(path)
        return sd.comp_record(ap), "rebuilt from the capture"
    if not rec.get("comp_page"):
        raise ValueError(f"{path.name} is not a comp page or a capture")
    if all("seen" in n for n in rec.get("nodes") or []):
        return rec, ""
    cap = path.with_name("capture_" + path.name[len("comp_"):]) if path.name.startswith("comp_") else None
    if cap is not None and cap.exists():
        ap, _ = sd.comp_from_capture(cap, rec.get("anchor") or "settled")
        return sd.comp_record(ap), "rebuilt from its capture (the comp file predates the snapshot record)"
    n = _approx(rec)
    return rec, f"older comp file: {n} items that came and went have approximate times"


# ---------------------------------------------------------------- the story of each item

def _at(runs, si) -> bool:
    return any(a <= si <= b for a, b in runs)


def story(n, n_snap):
    """Flags of one comp node for the map: late (appeared after the page was loaded), gone (not there at
    the end), back (left and came back), changes (how many times its text changed)."""
    runs = n.get("seen") or [[0, n_snap - 1]]
    texts = [t for _, t in n.get("steps") or []]
    return {"late": n.get("origin") == sd.ATTACHED, "gone": not n.get("present", True),
            "back": len(runs) > 1, "changes": max(0, len(texts) - 1)}


def _short(t):
    t = " ".join(str(t).split())
    return t if len(t) <= EXAMPLE_LEN else t[:EXAMPLE_LEN - 1] + "…"


def _plural(k, one, many):
    return f"{k} {one if k == 1 else many}"


def timeline(rec, items):
    """One row per snapshot: what changed on the page, counted over the items the map draws (items:
    {node index: text}). kind: open / loading / change / quiet."""
    nodes = rec.get("nodes") or []
    log = rec.get("log") or []
    settled = rec.get("settled_at") or ""
    rows = []
    for si, row in enumerate(log):
        new, back, gone, chg = [], [], [], []
        for k, text in items.items():
            n = nodes[k]
            runs = n["seen"]
            starts = [a for a, _ in runs]
            if si in starts:
                (new if starts.index(si) == 0 else back).append(k)
            if any(b == si - 1 for _, b in runs) and not _at(runs, si):
                gone.append(k)
            if si > runs[0][0] and any(s == si for s, _ in n["steps"]) and _at(runs, si) and si not in starts:
                chg.append(k)

        def ex(ks):
            return [_short(items[k]) for k in ks if items[k] and not render._is_icon(items[k].strip())][:EXAMPLES]
        on = sum(1 for k in items if _at(nodes[k]["seen"], si))
        if si == 0:
            kind, say = "open", f"The page opened with {_plural(on, 'item', 'items')}."
        else:
            parts = []
            if new:
                parts.append(_plural(len(new), "new item appeared", "new items appeared"))
            if back:
                parts.append(_plural(len(back), "item came back", "items came back"))
            if gone:
                parts.append(_plural(len(gone), "item went away", "items went away"))
            if chg:
                parts.append(_plural(len(chg), "value changed", "values changed"))
            loading = row.get("phase") == "anchor"
            kind = "loading" if loading else ("change" if parts else "quiet")
            say = ("Still loading: " if loading else "") + ("; ".join(parts) + "." if parts else
                                                            "Nothing you can see changed.")
        rows.append({"i": si, "t": secs(row.get("snapshot")), "stamp": str(row.get("snapshot") or ""),
                     "kind": kind, "say": say, "on": on, "settled": bool(settled) and row.get("snapshot") == settled,
                     "new": len(new), "back": len(back), "gone": len(gone), "changed": len(chg),
                     "ex_new": ex(new), "ex_back": ex(back), "ex_gone": ex(gone), "ex_changed": ex(chg)})
    return rows


# ---------------------------------------------------------------- build

def build(rec, source="", note=""):
    """(html, summary). summary holds counts only."""
    docs = render._comp_docs(rec)
    raw = docs[0]
    nodes = render._norm(raw)
    render._decide(nodes, "all")
    view = [n for n in nodes if n["in_view"] and render._pos(n["rect"])]
    ox = min((n["rect"][0] for n in view), default=0)
    oy = min((n["rect"][1] for n in view), default=0)
    W = max((n["rect"][0] + n["rect"][2] for n in view), default=0) - ox
    H = max((n["rect"][1] + n["rect"][3] for n in view), default=0) - oy
    lh = render._line_h(nodes)
    els = render._elements(nodes, lh, ox, oy)
    comp = rec.get("nodes") or []
    n_snap = max(1, len(rec.get("log") or []))
    drawn = sorted({e["k"] for e in els})
    items = {k: nodes[k]["text"] for k in drawn}
    info = {}
    for k in drawn:
        c, n = comp[k], nodes[k]
        if (c.get("text") or "") != n["text"]:
            # snapshot_diff's text (keys.flatten) is not the text the render draws (a Button's name, say):
            # its steps say nothing about the drawn text, so the drawn text stands for the whole capture
            c = dict(c, steps=[[c["seen"][0][0], n["text"]]])
        info[str(k)] = {"t": n["type"], "text": n["text"], "seen": c["seen"], "steps": c["steps"],
                        "values": c.get("values") or [], "first": c.get("first"), "last": c.get("last"),
                        "approx": bool(c.get("approx")), **story(c, n_snap),
                        "tech": {"key": c.get("key"), "id": n["id"], "cls": n["cls"], "box": n["rect"],
                                 "origin": c.get("origin"), "index": k}}
    for e in els:
        e.pop("muted", None)          # the map draws its own story classes
        e.pop("att", None)
    steps = timeline(rec, items)
    flags = [info[str(k)] for k in drawn]
    summary = {"items": len(drawn), "snapshots": n_snap,
               "late": sum(1 for f in flags if f["late"]), "gone": sum(1 for f in flags if f["gone"]),
               "back": sum(1 for f in flags if f["back"]), "changed": sum(1 for f in flags if f["changes"]),
               "approx": sum(1 for f in flags if f["approx"]),
               "steps_with_change": sum(1 for s in steps if s["kind"] == "change")}
    log = rec.get("log") or []
    data = {"page": str(rec.get("page") or ""), "browser": str(rec.get("browser") or ""),
            "session": str(rec.get("session") or ""), "source": source, "note": note,
            "settled": str(rec.get("settled_at") or ""), "watched": secs(log[-1].get("snapshot")) if log else 0.0,
            "W": W, "H": H, "lineH": lh, "els": els, "items": info, "steps": steps, "summary": summary}
    blob = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE.replace("/*DATA*/null", blob), summary


def default_out(src: Path) -> Path:
    return src.parent / f"map_{src.stem}.html"


def main(argv=None):
    ap = argparse.ArgumentParser(description="The page map: a comp page over time, for a non-technical reader.")
    ap.add_argument("src", help="a comp_*.json or capture_*.json")
    ap.add_argument("--out", help="output path (default: map_<input stem>.html beside the input)")
    ap.add_argument("--open", action="store_true", help="open the page in the default browser")
    a = ap.parse_args(argv)
    src = Path(a.src)
    if not src.exists():
        print(f"No such file: {src}")
        return 1
    try:
        rec, note = load(src)
        out = Path(a.out) if a.out else default_out(src)
        render.check_out(out)
        html, s = build(rec, src.name, note)
    except ValueError as e:
        raise SystemExit(str(e))
    render.write_html(out, html)
    print(f"page map: {s['items']} items over {s['snapshots']} snapshots; {s['late']} appeared later, "
          f"{s['gone']} went away, {s['back']} came back, {s['changed']} changed value; "
          f"{s['steps_with_change']} steps with a visible change")
    if note:
        print(f"  ({note})")
    print(f"-> {out}")
    if a.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Page map</title>
<style>
  :root { --bg:#f4f4f6; --panel:#ffffff; --ink:#1d1d1f; --muted:#6e6e73; --line:#d2d2d7; --accent:#1a73e8;
          --late:#2f9e44; --gone:#868e96; --back:#7048e8; --chg:#e67700; --now:#e03131; --hl:#ffb000; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
          --bg:#161618; --panel:#1f1f22; --ink:#e5e5ea; --muted:#98989d; --line:#3a3a3c; } }
  :root[data-theme="dark"] { --bg:#161618; --panel:#1f1f22; --ink:#e5e5ea; --muted:#98989d; --line:#3a3a3c; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 system-ui, "Segoe UI", sans-serif; }
  header { position:sticky; top:0; z-index:30; background:var(--panel); border-bottom:1px solid var(--line); padding:10px 16px; }
  .top { display:flex; gap:12px; align-items:baseline; flex-wrap:wrap; }
  h1 { font-size:18px; margin:0; font-weight:650; }
  .meta { color:var(--muted); font-size:13px; overflow-wrap:anywhere; }
  .chips { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }
  .chip { border:1px solid var(--line); border-radius:999px; padding:2px 10px; font-size:13px; background:var(--bg); }
  .chip b { font-weight:650; }
  .chip i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:6px; vertical-align:-1px; font-style:normal; }
  .ctrl { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:10px; }
  .seg { display:inline-flex; border:1px solid var(--line); border-radius:6px; overflow:hidden; }
  .seg button { font:inherit; border:none; background:transparent; color:var(--ink); padding:5px 12px; cursor:pointer; }
  .seg button.on { background:var(--accent); color:#fff; }
  .play { font:inherit; border:1px solid var(--line); background:var(--bg); color:var(--ink); border-radius:6px; padding:5px 12px; cursor:pointer; min-width:78px; }
  #slider { flex:1 1 260px; min-width:160px; accent-color:var(--accent); }
  #when { font-variant-numeric:tabular-nums; min-width:210px; }
  .layout { display:grid; grid-template-columns:minmax(0,1fr) 360px; gap:16px; padding:16px; align-items:start; }
  @media (max-width: 900px) { .layout { grid-template-columns:minmax(0,1fr); } aside { position:static !important; max-height:none !important; } }
  .pagewrap { overflow:auto; background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px; max-height:calc(100vh - 190px); }
  .page { position:relative; background:#fff; color:#1d1d1f; box-shadow:0 1px 4px rgba(0,0,0,.12); }
  aside { position:sticky; top:150px; max-height:calc(100vh - 170px); overflow:auto; display:flex; flex-direction:column; gap:12px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px; }
  .card h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin:0 0 8px; font-weight:600; }
  .note { font-size:12px; color:var(--muted); margin-top:6px; }
  ol.steps { list-style:none; margin:0; padding:0; }
  ol.steps li { padding:6px 8px; border-radius:6px; cursor:pointer; display:grid; grid-template-columns:52px 1fr; gap:6px; }
  ol.steps li:hover { background:rgba(26,115,232,.08); }
  ol.steps li.cur { background:rgba(26,115,232,.16); }
  ol.steps li.quiet { color:var(--muted); }
  ol.steps .ts { font-variant-numeric:tabular-nums; color:var(--muted); }
  ol.steps .ex { display:block; font-size:12px; color:var(--muted); overflow-wrap:anywhere; }
  ol.steps .ok { display:inline-block; font-size:11px; border:1px solid var(--late); color:var(--late); border-radius:4px; padding:0 4px; margin-left:4px; }
  .legend div { display:flex; gap:8px; align-items:center; margin:4px 0; font-size:13px; }
  .sw { width:22px; height:14px; border-radius:3px; flex:none; background:#fff; border:1px solid #9aa0a8; }
  .sw.late { border:2px solid var(--late); } .sw.gone { border:1px dashed var(--gone); opacity:.6; }
  .sw.back { border:2px solid var(--back); } .sw.chg { position:relative; } .sw.chg::after { content:"2"; position:absolute; right:-6px; top:-6px; background:var(--chg); color:#fff; font:10px/14px system-ui; border-radius:7px; min-width:14px; text-align:center; }
  .sw.now-new { border:2px solid var(--late); box-shadow:0 0 0 3px rgba(47,158,68,.3); }
  .sw.now-gone { border:2px dashed var(--now); } .sw.now-chg { border:2px solid var(--chg); box-shadow:0 0 0 3px rgba(230,119,0,.3); }
  .legend .mode-all, .legend .mode-at { display:none; }
  body.all .legend .mode-all, body.at .legend .mode-at { display:flex; }
  .legend label { display:flex; gap:6px; align-items:center; margin-top:6px; font-size:13px; cursor:pointer; }
  #item .empty { color:var(--muted); }
  #item dl { margin:0; display:grid; grid-template-columns:110px 1fr; gap:4px 8px; font-size:13px; }
  #item dt { color:var(--muted); } #item dd { margin:0; overflow-wrap:anywhere; }
  #item .vals { margin:4px 0 0; padding-left:18px; } #item .vals li { overflow-wrap:anywhere; }
  #item details { margin-top:8px; font-size:12px; color:var(--muted); } #item pre { white-space:pre-wrap; overflow-wrap:anywhere; font:11px ui-monospace,Consolas,monospace; }
  .warn { font-size:12px; color:var(--muted); border-top:1px solid var(--line); padding-top:8px; }

  /* the drawn page: the renderer's looks */
  .n { position:absolute; z-index:2; }
  .text { white-space:nowrap; display:flex; align-items:center; line-height:1; cursor:pointer; }
  .text .t { display:inline-block; white-space:nowrap; }
  .text.Button { justify-content:center; }
  .text.link { color:#1a4fd6; text-decoration:underline; }
  .text.icon { border:1px dashed #8a8a8e; border-radius:3px; color:#8a8a8e; font-size:8px !important; justify-content:center; }
  .box { cursor:pointer; }
  .box.Button { border:1px solid #9aa0a8; border-radius:4px; background:#eef0f3; }
  .box.Edit, .box.ComboBox { border:1px solid #777; background:#fff; }
  .box.Table { border:1px solid #777; }
  .box.DataItem, .box.HeaderItem, .box.Header { border:1px solid #c9c9ce; }
  .box.Image { border:1px dashed #8a8a8e; background:repeating-linear-gradient(45deg,#f2f2f2 0 6px,#fafafa 6px 12px); }
  .box.CheckBox::before, .box.RadioButton::before { content:""; position:absolute; left:3px; top:50%; width:11px; height:11px;
           transform:translateY(-50%); border:1px solid #555; background:#fff; }
  .box.RadioButton::before { border-radius:50%; }
  .box.combo::after { content:"\25BE"; position:absolute; right:4px; top:50%; transform:translateY(-50%); color:#555; font-size:10px; }
  .box.tab { border:1px solid #c9c9ce; border-bottom:none; border-radius:4px 4px 0 0; }
  .req { position:absolute; right:-8px; top:0; color:#c0392b; }
  .dis { opacity:.5; }
  .badge { position:absolute; right:-7px; top:-7px; background:var(--chg); color:#fff; font:10px/14px system-ui; min-width:14px; padding:0 3px; border-radius:7px; text-align:center; z-index:3; pointer-events:none; }

  /* Everything: each item's story */
  body.all .n.late { outline:2px solid var(--late); outline-offset:1px; }
  body.all .n.back { outline:2px solid var(--back); outline-offset:1px; }
  body.all .n.gone { opacity:.38; z-index:1; outline:1px dashed var(--gone); outline-offset:0; }
  body.all.hide-gone .n.gone { display:none; }
  body.all.plain .n { outline:none !important; } body.all.plain .badge { display:none; }
  body.at .badge { display:none; }
  /* At a moment */
  body.at .n.off { display:none; }
  body.at .n.now-new { outline:2px solid var(--late); box-shadow:0 0 0 3px rgba(47,158,68,.3); }
  body.at .n.now-chg { outline:2px solid var(--chg); box-shadow:0 0 0 3px rgba(230,119,0,.3); }
  body.at .n.now-gone { display:flex; opacity:.45; outline:2px dashed var(--now); z-index:1; }
  body.at .box.now-gone { display:block; }
  .n.hot { outline:3px solid var(--hl) !important; z-index:6; opacity:1 !important; }
  #tip { position:fixed; z-index:40; pointer-events:none; background:#222; color:#fff; font:12px/1.4 system-ui, sans-serif;
         padding:5px 8px; border-radius:4px; max-width:360px; display:none; }
</style>
</head>
<body class="all">
<header>
  <div class="top"><h1>Page map</h1><span class="meta" id="meta"></span></div>
  <div class="chips" id="chips"></div>
  <div class="ctrl">
    <span class="seg"><button id="m-all" class="on" title="Every item the page ever showed (E)">Everything</button><button id="m-at" title="The page as it was at one moment">At a moment</button></span>
    <button class="play" id="play" title="Play the capture (Space)">&#9654; Play</button>
    <input type="range" id="slider" min="0" max="0" value="0" step="1" aria-label="moment">
    <span id="when" class="meta"></span>
  </div>
</header>
<div class="layout">
  <div class="pagewrap"><div class="page" id="page"></div></div>
  <aside>
    <section class="card" id="item"><h2>Selected item</h2><div class="empty">Click any item on the page to see its story.</div></section>
    <section class="card"><h2>What happened</h2><ol class="steps" id="steps"></ol>
      <div class="note">Click a step to see the page at that moment. &larr; &rarr; step through, Space plays.</div></section>
    <section class="card legend"><h2>How to read the map</h2>
      <div class="mode-all"><span class="sw"></span>There the whole time</div>
      <div class="mode-all"><span class="sw late"></span>Appeared after the page had loaded</div>
      <div class="mode-all"><span class="sw back"></span>Went away and came back</div>
      <div class="mode-all"><span class="sw gone"></span>Gone by the end (drawn faded, behind)</div>
      <div class="mode-all"><span class="sw chg"></span>Its value changed (how many times)</div>
      <label class="mode-all"><input type="checkbox" id="showgone" checked> show items that went away</label>
      <label class="mode-all"><input type="checkbox" id="colour" checked> colour by story</label>
      <div class="mode-at"><span class="sw now-new"></span>Appeared at this step</div>
      <div class="mode-at"><span class="sw now-chg"></span>Value changed at this step</div>
      <div class="mode-at"><span class="sw now-gone"></span>Went away at this step</div>
      <div class="warn">This page shows real client values. Keep it on this PC.</div>
    </section>
  </aside>
</div>
<div id="tip"></div>
<script>
const D = /*DATA*/null;
const FIT_MIN = 7;
const body = document.body, page = document.getElementById('page'), tip = document.getElementById('tip');
const KIND = {Text:'text', Hyperlink:'link', Button:'button', Edit:'input box', ComboBox:'drop-down', CheckBox:'tick box',
  RadioButton:'option', Image:'picture', Table:'table', DataItem:'table cell', HeaderItem:'column heading', Header:'heading row',
  ListItem:'list item', MenuItem:'menu item', TabItem:'tab', Group:'area', Document:'page', Custom:'item'};
const fmt = t => (Math.round(t * 10) / 10) + ' s';
const N = D.steps.length;
function mk(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
const at = (runs, si) => runs.some(([a, b]) => a <= si && si <= b);
function textAt(it, si) { let t = it.text; for (const [s, x] of it.steps) { if (s <= si) t = x; else break; } return t; }
function when(si) { const s = D.steps[si]; return s ? '+' + fmt(s.t) : ''; }

// header
document.getElementById('meta').textContent = [D.page, D.browser, 'watched for ' + fmt(D.watched), D.source].filter(Boolean).join(' · ');
const S = D.summary, chips = document.getElementById('chips');
function chip(n, label, col) { const c = mk('span', 'chip'); if (col) { const i = mk('i'); i.style.background = col; c.appendChild(i); }
  c.appendChild(mk('b', null, String(n))); c.appendChild(document.createTextNode(' ' + label)); chips.appendChild(c); }
chip(S.items, 'items on the page');
chip(S.steps_with_change, S.steps_with_change === 1 ? 'change you can see' : 'changes you can see');
chip(S.late, 'appeared later', 'var(--late)');
chip(S.gone, 'went away', 'var(--gone)');
chip(S.back, 'came back', 'var(--back)');
chip(S.changed, 'changed value', 'var(--chg)');
if (D.note) chips.appendChild(mk('span', 'chip meta', D.note));

// the page, drawn once
page.style.width = D.W + 'px'; page.style.height = D.H + 'px';
const DIVS = [];   // {e, it, div, sp}
for (const e of D.els) {
  const it = D.items[e.k];
  const div = mk('div', 'n ' + e.kind + ' ' + e.t);
  div.style.left = e.x + 'px'; div.style.top = e.y + 'px'; div.style.width = e.w + 'px'; div.style.height = e.h + 'px';
  div.dataset.k = e.k;
  let sp = null;
  if (e.kind === 'box') {
    if (e.tab) div.classList.add('tab');
    if (e.combo) div.classList.add('combo');
    if (e.req) div.appendChild(mk('span', 'req', '*'));
  } else {
    if (e.icon) div.classList.add('icon');
    if (e.link) div.classList.add('link');
    sp = mk('span', 't', e.txt);
    div.appendChild(sp);
    div.dataset.fs = e.fs;
    div.style.fontSize = e.fs + 'px';
  }
  if (e.dis) div.classList.add('dis');
  if (it.late) div.classList.add('late');
  if (it.gone) div.classList.add('gone');
  if (it.back && !it.gone) div.classList.add('back');
  if (it.changes && e.kind === 'text') div.appendChild(mk('span', 'badge', String(it.changes)));
  page.appendChild(div);
  DIVS.push({e, it, div, sp});
}
function fit(d) {
  if (!d.sp || d.e.icon) return;
  const fs0 = parseFloat(d.div.dataset.fs), w = d.e.w;
  let fs = fs0; d.div.style.fontSize = fs + 'px';
  while (d.sp.offsetWidth > w + 1 && fs > FIT_MIN) { fs -= 0.5; d.div.style.fontSize = fs + 'px'; }
}
for (const d of DIVS) fit(d);

// the timeline list
const stepsEl = document.getElementById('steps');
const LIS = D.steps.map(s => {
  const li = mk('li', s.kind);
  li.appendChild(mk('span', 'ts', s.i === 0 ? 'start' : '+' + fmt(s.t)));
  const say = mk('span', null, s.say);
  if (s.settled) say.appendChild(mk('span', 'ok', 'page loaded'));
  for (const [k, lab] of [['ex_new', 'new'], ['ex_back', 'back'], ['ex_gone', 'gone'], ['ex_changed', 'changed']])
    if (s[k].length) say.appendChild(mk('span', 'ex', lab + ': ' + s[k].join(' · ')));
  li.appendChild(say);
  li.addEventListener('click', () => { stop(); show(s.i); });
  stepsEl.appendChild(li);
  return li;
});
// only steps where something changed (and the first) are listed by default: quiet ones are hidden when there are many
if (N > 12) LIS.forEach((li, i) => { if (D.steps[i].kind === 'quiet') li.style.display = 'none'; });

// modes
const slider = document.getElementById('slider');
slider.max = Math.max(0, N - 1);
let mode = 'all', cur = N - 1, timer = null;
function setMode(m) {
  mode = m;
  body.classList.toggle('all', m === 'all'); body.classList.toggle('at', m === 'at');
  document.getElementById('m-all').classList.toggle('on', m === 'all');
  document.getElementById('m-at').classList.toggle('on', m === 'at');
  paint();
}
function show(si) { cur = Math.max(0, Math.min(N - 1, si)); slider.value = cur; if (mode !== 'at') setMode('at'); else paint(); }
function paint() {
  for (const li of LIS) li.classList.remove('cur');
  if (mode === 'all') {
    for (const d of DIVS) {
      d.div.classList.remove('off', 'now-new', 'now-chg', 'now-gone');
      if (d.sp && !d.e.icon && d.sp.textContent !== d.e.txt) { d.sp.textContent = d.e.txt; fit(d); }
    }
    document.getElementById('when').textContent = 'Every item the page showed, from start to ' + when(N - 1);
    return;
  }
  LIS[cur] && LIS[cur].classList.add('cur');
  const s = D.steps[cur];
  for (const d of DIVS) {
    const runs = d.it.seen, on = at(runs, cur);
    const starts = runs.map(r => r[0]);
    const goneNow = !on && runs.some(r => r[1] === cur - 1);
    d.div.classList.toggle('off', !on && !goneNow);
    d.div.classList.toggle('now-new', on && cur > 0 && starts.includes(cur));
    d.div.classList.toggle('now-gone', goneNow);
    d.div.classList.toggle('now-chg', on && cur > 0 && !starts.includes(cur) && d.it.steps.some(([x]) => x === cur));
    if (d.sp && !d.e.icon) {
      const t = textAt(d.it, on ? cur : Math.max(0, cur - 1));
      if (d.sp.textContent !== t) { d.sp.textContent = t; fit(d); }
    }
  }
  document.getElementById('when').textContent = (cur === 0 ? 'Start' : when(cur)) + ' · step ' + (cur + 1) + ' of ' + N + ' · ' + s.on + ' items on screen';
}
document.getElementById('m-all').addEventListener('click', () => { stop(); setMode('all'); });
document.getElementById('m-at').addEventListener('click', () => { stop(); setMode('at'); });
slider.addEventListener('input', () => { stop(); show(Number(slider.value)); });
const playBtn = document.getElementById('play');
function stop() { if (timer) { clearInterval(timer); timer = null; playBtn.innerHTML = '&#9654; Play'; } }
function play() {
  if (timer) return stop();
  if (mode !== 'at' || cur >= N - 1) show(0);
  playBtn.innerHTML = '&#10073;&#10073; Pause';
  timer = setInterval(() => { if (cur >= N - 1) return stop(); show(cur + 1); }, 900);
}
playBtn.addEventListener('click', play);
document.addEventListener('keydown', ev => {
  if (ev.target.tagName === 'INPUT' && ev.target.type !== 'range') return;
  if (ev.key === 'ArrowRight') { stop(); show(mode === 'at' ? cur + 1 : 0); ev.preventDefault(); }
  else if (ev.key === 'ArrowLeft') { stop(); show(mode === 'at' ? cur - 1 : N - 1); ev.preventDefault(); }
  else if (ev.key === ' ') { play(); ev.preventDefault(); }
  else if (ev.key === 'e' || ev.key === 'E') { stop(); setMode('all'); }
});
document.getElementById('showgone').addEventListener('change', e => body.classList.toggle('hide-gone', !e.target.checked));
document.getElementById('colour').addEventListener('change', e => body.classList.toggle('plain', !e.target.checked));

// the selected item, in plain words
const card = document.getElementById('item');
function storyLine(it) {
  const runs = it.seen, out = [];
  out.push(runs[0][0] === 0 ? 'There from the start' : (it.late ? 'Appeared after the page had loaded, at ' : 'Appeared while the page was loading, at ') + when(runs[0][0]));
  runs.slice(1).forEach(r => out.push('came back at ' + when(r[0])));
  if (it.gone) out.push('went away at ' + when(runs[runs.length - 1][1] + 1));
  else out.push('still there at the end');
  return out.join(' · ') + (it.approx ? ' (times approximate)' : '');
}
function select(k) {
  for (const h of page.querySelectorAll('.hot')) h.classList.remove('hot');
  for (const d of DIVS) if (String(d.e.k) === String(k)) d.div.classList.add('hot');
  const it = D.items[k];
  card.textContent = '';
  card.appendChild(mk('h2', null, 'Selected item'));
  const dl = mk('dl');
  const row = (a, b) => { dl.appendChild(mk('dt', null, a)); dl.appendChild(mk('dd', null, b)); };
  row('What it is', 'A ' + (KIND[it.t] || it.t.toLowerCase()));
  row('Shows', it.text ? '“' + (mode === 'at' ? textAt(it, cur) : it.text) + '”' : '(no text)');
  row('Its story', storyLine(it));
  row('Value', it.changes ? 'changed ' + it.changes + (it.changes === 1 ? ' time' : ' times') : 'did not change');
  card.appendChild(dl);
  if (it.steps.length > 1) {
    card.appendChild(mk('div', 'note', 'Values it showed, in order:'));
    const ul = mk('ol', 'vals');
    for (const [s, t] of it.steps) ul.appendChild(mk('li', null, (s === 0 ? 'start' : when(s)) + ': ' + (t || '(empty)')));
    card.appendChild(ul);
  }
  const det = mk('details');
  det.appendChild(mk('summary', null, 'Technical details'));
  det.appendChild(mk('pre', null, JSON.stringify(it.tech, null, 1)));
  card.appendChild(det);
}
page.addEventListener('click', ev => { const t = ev.target.closest('.n'); if (t) select(t.dataset.k); });
page.addEventListener('mousemove', ev => {
  const t = ev.target.closest('.n');
  if (!t) { tip.style.display = 'none'; return; }
  const it = D.items[t.dataset.k];
  tip.textContent = (KIND[it.t] || it.t) + (it.text ? ': ' + (mode === 'at' ? textAt(it, cur) : it.text) : '') + '  — click for its story';
  tip.style.display = 'block';
  tip.style.left = Math.min(ev.clientX + 14, innerWidth - 370) + 'px';
  tip.style.top = (ev.clientY + 16) + 'px';
});
page.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
setMode('all');
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
