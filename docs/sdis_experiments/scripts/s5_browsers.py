"""The same fictional page read in Chrome, Firefox and Edge (tests/class_diff_align): how the trees differ,
what the tree method finds, and whether the leaf token strings agree. Fixture data is fictional."""
import json
from collections import Counter
from difflib import SequenceMatcher
from common import REPO, children, rect
from arr import leaves, tokens, tandem_runs, runs_report, step
import cards
from core.sdis.keys import flatten

FIX = REPO / "tests" / "class_diff_align"
TFIX = REPO / "tests" / "class_diff_tables"

strings = {}
for client in ("A", "B"):
    for br in ("msedge", "chrome", "firefox"):
        rec = json.loads((FIX / f"client_{client}_{br}.json").read_text(encoding="utf-8"))
        fl = flatten(rec)
        kids = children(fl)
        lv = leaves(fl, present_only=False)
        S = tokens(fl, lv)
        strings[(client, br)] = (fl, lv, S)
        print(f"== client {client} {br}: nodes={len(fl)} depth max={max(e['depth'] for e in fl)} leaves={len(lv)} "
              f"kinds={Counter(fl[i]['type'] for i in lv).most_common()}")
        print(f"   node types: {Counter(e['type'] for e in fl).most_common()}")
        gs = cards.find_groups(fl, rec.get("docs"))
        print(f"   tree method groups: {[(g['kind'], g['source'], len(g['members']), [len(m['items']) for m in g['members']]) for g in gs]}")
        runs = tandem_runs(S)
        print("   tandem runs:")
        runs_report(runs, top=5)
        # the card rows ('Period' .. 'ARN'): leaf positions of the texts that repeat on the page
        cnt = Counter(fl[i]["text"] for i in lv if fl[i]["text"])
        rep = [k for k, i in enumerate(lv) if cnt[fl[i]["text"]] >= 3 and fl[i]["type"] in ("Text",)]
        print(f"   leaves whose text repeats 3+ times on the page: {len(rep)} at positions {rep}")
        # direction tokens of the card region
        if rep:
            a, b = rep[0], rep[-1] + 2
            print(f"   tokens in the repeated region: {S[a:b]}")
print()
print("== cross-browser agreement of the token strings (SequenceMatcher ratio) ==")
for client in ("A", "B"):
    for b1, b2 in (("msedge", "chrome"), ("msedge", "firefox"), ("chrome", "firefox")):
        s1, s2 = strings[(client, b1)][2], strings[(client, b2)][2]
        sm = SequenceMatcher(None, s1, s2, autojunk=False)
        ops = [(op, i1, i2, j1, j2) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]
        print(f"  {client} {b1} vs {b2}: lengths {len(s1)}/{len(s2)} ratio={sm.ratio():.3f} edits={len(ops)}")
        for op, i1, i2, j1, j2 in ops[:6]:
            print(f"      {op}: {s1[i1:i2]} -> {s2[j1:j2]}")
print()
print("== the leaf boxes across browsers: same leaf (by document order), pixel differences ==")
for client in ("A",):
    fe, le, _ = strings[(client, "msedge")]
    for br in ("chrome", "firefox"):
        fb, lb, _ = strings[(client, br)]
        if len(le) != len(lb):
            print(f"  {br}: different leaf count ({len(le)} vs {len(lb)}): boxes not compared one to one")
            continue
        dx = [rect(fb[j])[0] - rect(fe[i])[0] for i, j in zip(le, lb)]
        dy = [rect(fb[j])[1] - rect(fe[i])[1] for i, j in zip(le, lb)]
        dw = [rect(fb[j])[2] - rect(fe[i])[2] for i, j in zip(le, lb)]
        print(f"  edge vs {br}: dx range {min(dx)}..{max(dx)}, dy range {min(dy)}..{max(dy)}, width diff range {min(dw)}..{max(dw)}")

print()
print("== tables fixtures (Table pages in three browsers): node counts and leaf counts ==")
for br in ("edge", "chrome", "firefox"):
    rec = json.loads((TFIX / f"tables_{br}.json").read_text(encoding="utf-8"))
    fl = flatten(rec)
    lv = leaves(fl, present_only=False)
    print(f"  {br}: nodes={len(fl)} leaves={len(lv)} types={Counter(e['type'] for e in fl).most_common(6)} leaf kinds={Counter(fl[i]['type'] for i in lv).most_common()}")
