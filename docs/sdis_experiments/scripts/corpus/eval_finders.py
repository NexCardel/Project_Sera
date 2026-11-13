"""Run the three record finders over the corpus sample. Counts only. Read-only on the repo.
Run sample.py first. Options: --aid (feed the corpus aid as the id), env LIKE=x (override shape_map.LIKE)."""
import json, re, sys, os, collections, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SP = REPO / "tools" / "pre_dev" / "class_diff" / "output"   # git-ignored: the sample holds client data
SP.mkdir(parents=True, exist_ok=True)
PD = REPO / "tools" / "pre_dev" / "class_diff"
os.environ["SDIS_DATA_DIR"] = str(SP)
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(PD))
import cards, arrange, shape_map, render  # noqa

CODE = re.compile(r"^GSTR-[0-9A-Z]+$")
DASH = "return.gst.gov.in/returns/auth/dashboard"
AID = "--aid" in sys.argv
shape_map.LIKE = float(os.environ.get("LIKE", shape_map.LIKE))          # variant: feed the corpus 'aid' as the id the shape uses


def desc_sets(fl):
    kids = collections.defaultdict(list)
    for i, e in enumerate(fl):
        if e["parent"] >= 0:
            kids[e["parent"]].append(i)

    def under(r):
        out, st = set(), [r]
        while st:
            j = st.pop(); out.add(j); st.extend(kids[j])
        return out
    return under


def score(members, truth):
    """members: list of node-index sets. -> (truth nodes alone in a member, members with no truth, members with 2+)."""
    alone = extra = multi = 0
    for m in members:
        k = len(m & truth)
        if k == 1: alone += 1
        elif k == 0: extra += 1
        else: multi += 1
    return alone, extra, multi


def best(cands, truth):
    """The candidate set that isolates most truth nodes (ties: fewest extras)."""
    if not cands:
        return (0, 0, 0)
    return max((score(c, truth) for c in cands), key=lambda s: (s[0], -s[1], -s[2]))


stats = collections.defaultdict(lambda: collections.defaultdict(list))
ratios = []            # (min/max texts, n instances) for every repeated disjoint shape
dash_rows = []
tile_ratio = []
t_cost = collections.Counter()
n = 0
for line in open(SP / "corpus_sample.jsonl", encoding="utf-8"):
    r = json.loads(line)
    link = r.get("link")
    big = max(r["docs"], key=len)
    for nd in big:
        nd["type_name"] = render._type_of(nd.get("ctype"))
        if AID and nd.get("aid"):
            nd["id"] = nd["aid"]
    rec = {"docs": [big]}
    n += 1
    try:
        fl, docs = cards.load_flat(rec)
        under = desc_sets(fl)
        t0 = time.perf_counter()
        pc = shape_map.page_containers(big)
        inf = shape_map.infer(pc)
        t1 = time.perf_counter()
        tg = [g for g in cards.find_groups(fl, docs) if g["source"] == "siblings" and g["parent_group"] is None]
        t2 = time.perf_counter()
        ar = arrange.arrange(fl)
        t3 = time.perf_counter()
    except Exception as ex:  # noqa
        stats[link]["err"].append(type(ex).__name__)
        continue
    t_cost["shape"] += t1 - t0; t_cost["tree"] += t2 - t1; t_cost["arr"] += t3 - t2
    ag = [g for g in ar.groups if g.parent is None]
    stats[link]["shape"].append(len(inf["sets"]))
    stats[link]["tree"].append(len(tg))
    stats[link]["arr"].append(len(ag))
    stats[link]["shape_sizes"].append(tuple(sorted(s["count"] for s in inf["sets"])))
    # LIKE ratios
    groups = collections.defaultdict(list)
    anc = {e["k"]: set(shape_map._ancestors(pc["by_k"], e["k"])) for e in pc["ols"]}
    for e in pc["ols"]:
        groups[e["sh"]].append(e)
    for sh, v in groups.items():
        ks = {e["k"] for e in v}
        if len(v) >= 2 and all(not (anc[e["k"]] & ks) for e in v) and min(e["nt"] for e in v) >= 2:
            ratios.append((min(e["nt"] for e in v) / max(e["nt"] for e in v), len(v), link))
    if link == DASH:
        truth = {i for i, e in enumerate(fl) if CODE.match((e["node"].get("name") or "").strip())
                 and str(e["node"].get("ctype")) == "50020" and (e["node"].get("rect") or [0, 0, 0, 0])[2] > 0}
        if not truth:
            continue
        sh_c = [[under(k) for k in s["members"]] for s in inf["sets"]]
        li_c = [[under(k) for k in s["lift"]["members"]] for s in inf["sets"] if s["lift"]]
        tr_c = [[under(m["root"]) for m in g["members"]] for g in tg]
        ar_c = [[{ar.leaves[i].node for i in range(a, b)} for a, b in g.members] for g in ag]
        for s_ in inf["sets"]:
            if score([under(k) for k in s_["members"]], truth)[0] == len(truth):
                tile_ratio.append(round(min(s_["texts"]) / max(s_["texts"]), 3))
        dash_rows.append({"client": ",".join(r["_ids"]) or "?", "T": len(truth),
                          "shape": best(sh_c, truth), "lift": best(sh_c + li_c, truth),
                          "tree": best(tr_c, truth), "arr": best(ar_c, truth)})

print("reads", n, "| seconds per read:", {k: round(v / n, 4) for k, v in t_cost.items()})
print("\n== top-level groups per read, by link (median [min..max]) ==")
def mm(v):
    v = sorted(v); return f"{v[len(v)//2]} [{v[0]}..{v[-1]}]"
for link, s in sorted(stats.items(), key=lambda kv: -len(kv[1]["shape"])):
    if not s["shape"]:
        print("ERR", link, collections.Counter(s["err"])); continue
    print(f"{len(s['shape']):4d} reads  shape {mm(s['shape']):12s} tree {mm(s['tree']):12s} arr {mm(s['arr']):12s} {link}"
          + (f"  ERR {len(s['err'])}" if s["err"] else ""))

print("\n== GST returns dashboard: tiles isolated (truth = on-screen form-code texts) ==")
print("reads with tiles:", len(dash_rows), " tiles per read:", collections.Counter(d["T"] for d in dash_rows))
for m in ("shape", "lift", "tree", "arr"):
    exact = sum(1 for d in dash_rows if d[m][0] == d["T"] and d[m][1] == 0 and d[m][2] == 0)
    allf = sum(1 for d in dash_rows if d[m][0] == d["T"])
    none = sum(1 for d in dash_rows if d[m][0] == 0)
    tiles = sum(d[m][0] for d in dash_rows); tot = sum(d["T"] for d in dash_rows)
    print(f"  {m:6s} exact {exact:3d}  all tiles (with extras) {allf:3d}  none {none:3d}  of {len(dash_rows)} reads; tiles isolated {tiles}/{tot}")
byc = collections.defaultdict(list)
for d in dash_rows:
    byc[d["client"]].append(d)
print("  per client (reads: exact shape / lift / tree / arr):")
for c, ds in sorted(byc.items()):
    f = lambda m: sum(1 for d in ds if d[m][0] == d["T"] and d[m][1] == 0 and d[m][2] == 0)
    print(f"    {c[:24]:24s} {len(ds):3d}: {f('shape')} / {f('lift')} / {f('tree')} / {f('arr')}")

print("\n== LIKE: min/max text ratio of every repeated disjoint shape ==")
vals = sorted(set(round(x[0], 4) for x in ratios))
hist = collections.Counter(min(int(x[0] * 10), 9) for x in ratios)
print("  shapes:", len(ratios), " histogram by tenth:", [hist[i] for i in range(10)])
gaps = sorted(((b - a, a, b) for a, b in zip(vals, vals[1:])), reverse=True)[:4]
print("  widest empty gaps:", [(round(g, 3), a, b) for g, a, b in gaps])

print("  true tile sets, min/max text ratio:", collections.Counter(tile_ratio))
