"""Corpus inventory: counts only, ids hashed. Read-only."""
import json, re, sys, hashlib, collections
from pathlib import Path

CORPUS = Path.home() / "AmanAssociates_Sera" / "sdis" / "corpus"
GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]\b")
PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")

def h(s):
    return hashlib.md5(s.encode()).hexdigest()[:6]

links = collections.defaultdict(lambda: {"reads": 0, "sessions": set(), "clients": set(), "browsers": collections.Counter(),
                                         "days": set(), "devices": set(), "nodes": [], "noid": 0})
total = 0
for f in sorted(CORPUS.glob("*/*.jsonl")):
    dev = f.parent.name[:4]
    with open(f, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            total += 1
            L = links[(r.get("portal") or "?", r.get("link") or "?")]
            L["reads"] += 1
            L["sessions"].add(r.get("session"))
            L["browsers"][r.get("browser") or "?"] += 1
            L["days"].add((r.get("ts") or "")[:10])
            L["devices"].add(dev)
            n = 0
            ids = set()
            for d in r.get("docs") or []:
                n += len(d)
                for nd in d:
                    nm = nd.get("name") or ""
                    if len(nm) < 200:
                        for m in GSTIN.findall(nm):
                            ids.add("G" + h(m))
                        for m in PAN.findall(nm):
                            ids.add("P" + h(m))
            L["nodes"].append(n)
            if ids:
                L["clients"].add(frozenset(ids))
            else:
                L["noid"] += 1

print("total reads", total, "links", len(links))
rows = sorted(links.items(), key=lambda kv: -kv[1]["reads"])
print(f"{'portal':10s} {'reads':>6s} {'sess':>4s} {'idsets':>6s} {'ids':>4s} {'noid':>5s} {'days':>4s} {'dev':>3s} {'med nodes':>9s}  browsers  link")
for (portal, link), L in rows:
    allids = set().union(*L["clients"]) if L["clients"] else set()
    ns = sorted(L["nodes"])
    print(f"{portal[:10]:10s} {L['reads']:6d} {len(L['sessions']):4d} {len(L['clients']):6d} {len(allids):4d} {L['noid']:5d} "
          f"{len(L['days']):4d} {len(L['devices']):3d} {ns[len(ns)//2]:9d}  {dict(L['browsers'])}  {link[:90]}")
