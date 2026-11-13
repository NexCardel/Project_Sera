"""Build a de-duplicated sample of the corpus: per (link, session), the distinct page structures.
Writes corpus_sample.jsonl in tools/pre_dev/class_diff/output/ (git-ignored, stays on this PC). Prints counts only."""
import json, re, hashlib, collections, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SP = REPO / "tools" / "pre_dev" / "class_diff" / "output"   # git-ignored: the sample holds client data
SP.mkdir(parents=True, exist_ok=True)
CORPUS = Path.home() / "AmanAssociates_Sera" / "sdis" / "corpus"
GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]\b")
PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
CAP = 12     # distinct structures kept per (link, session)

def h(s):
    return hashlib.md5(s.encode()).hexdigest()[:8]

seen = collections.defaultdict(dict)   # (link, session) -> {struct hash: count}
kept = 0
with open(SP / "corpus_sample.jsonl", "w", encoding="utf-8") as out:
    for f in sorted(CORPUS.glob("*/*.jsonl")):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                docs = r.get("docs") or []
                big = max(docs, key=len, default=[])
                if len(big) < 30:
                    continue
                st = h("|".join(f"{n.get('ctype')}:{n.get('parent')}" for n in big))
                key = (r.get("link"), r.get("session"))
                d = seen[key]
                if st in d:
                    d[st] += 1
                    continue
                if len(d) >= CAP:
                    continue
                d[st] = 1
                ids = set()
                for dd in docs:
                    for nd in dd:
                        nm = nd.get("name") or ""
                        if len(nm) < 200:
                            ids.update("G" + h(m) for m in GSTIN.findall(nm))
                            ids.update("P" + h(m) for m in PAN.findall(nm))
                r["_ids"] = sorted(ids)
                r["_dev"] = f.parent.name[:4]
                out.write(json.dumps(r, ensure_ascii=False) + "\n")
                kept += 1
per_link = collections.Counter()
for (link, s), d in seen.items():
    per_link[link] += min(len(d), CAP)
print("kept", kept)
for link, n in per_link.most_common():
    print(f"{n:5d}  {link}")
