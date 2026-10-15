"""Reproduce bugs 10, 11, 15, 17 on the two ITR comp pages. Counts and shapes only."""
from collections import Counter, defaultdict
from common import comp, children, rect, tshape, STAMPS, REPO
import cards
import compare

A, B = (comp(s) for s in STAMPS)
flA, flB = A["nodes"], B["nodes"]
link = A["page"]


def card_of(fl, groups, node):
    """Index of the outer card (member of the biggest group) that holds `node`, or None."""
    big = max(groups, key=lambda g: max(len(m["items"]) for m in g["members"]))
    roots = {m["root"]: k for k, m in enumerate(big["members"])}
    a = node
    while a >= 0:
        if a in roots:
            return roots[a]
        a = fl[a]["parent"]
    return None


print("=== bug 10: inner groups matched independently of their card ===")
gA = cards.find_groups(flA, cards.docs_for(A, flA))
gB = cards.find_groups(flB, cards.docs_for(B, flB))
print(f"groups: A={len(gA)} B={len(gB)}")
used = set()
cross = same = 0
for gi, g in enumerate(gA):
    j = cards.match_group(g, flA, gB, flB, used)
    if j is None:
        print(f"  A group {gi} ({len(g['members'])} members, parent_group={g['parent_group']}): no partner")
        continue
    used.add(j)
    h = gB[j]
    if g["parent_group"] is None:
        continue
    ca = card_of(flA, gA, g["members"][0]["root"])
    cb = card_of(flB, gB, h["members"][0]["root"])
    # card identity = text of the card's first slot (the year), compared as equal/unequal only
    big_a = max(gA, key=lambda x: max(len(m["items"]) for m in x["members"]))
    big_b = max(gB, key=lambda x: max(len(m["items"]) for m in x["members"]))
    name_a = big_a["members"][ca]["items"][0]["text"]
    name_b = big_b["members"][cb]["items"][0]["text"]
    ok = name_a == name_b
    same += ok
    cross += not ok
    print(f"  A group {gi} (card #{ca}) <-> B group {j} (card #{cb}): same card name? {ok}")
print(f"inner group pairs: same card={same}, DIFFERENT card={cross}")

print("\n=== bug 10b: each value reported twice (inner group row + card slot row) ===")
rows, counts = compare.cards_rows(A, B, link)
print("counts:", counts)
by_pairing = Counter(r["pairing"] for r in rows)
print("rows by pairing:", dict(by_pairing))
# a text node reported in more than one row: count by (example_value, key) duplicates for group rows
seen = Counter()
for r in rows:
    if r.get("group") != "" and r.get("slot"):
        seen[(r["example_value"], r["previous_example"])] += 1
dups = sum(1 for k, v in seen.items() if v > 1 and k[0])
print(f"value pairs appearing in 2+ group rows: {dups} of {len(seen)}")
print("with rho=0.75:")
cards.RHO = 0.75
rows2, counts2 = compare.cards_rows(A, B, link, "clientA", "clientB")
print("counts:", counts2)
print("rows by pairing:", dict(Counter(r["pairing"] for r in rows2)))
print("rows by status:", dict(Counter(r["status"] for r in rows2)))
kinds = Counter((r["group_kind"]) for r in rows2 if r.get("group") != "")
print("group kinds in rows:", dict(kinds))
cards.RHO = None

print("\n=== bug 11: variable_alignment in the plain --comp compare ===")
plain = compare.compare_flat(flA, flB)
st = Counter(r["status"] for r in plain)
print("plain compare statuses:", dict(st))
va = [r for r in plain if r["status"] == "variable_alignment"]
print("variable_alignment rows:", len(va), "shapes:", Counter(tshape(r["example_value"]) for r in va).most_common(8))
amb = sum(1 for r in plain if r["check"].startswith("ambiguous"))
mis = sum(1 for r in plain if r["check"].startswith("types differ"))
print("ambiguous pairing flags:", amb, " types-differ flags:", mis)
# label guessing: how many variable rows have a label, and is the label the text to the LEFT on the same line?
lab = Counter(bool(r["label"]) for r in plain if r["status"] == "variable")
print("variable rows with/without a guessed label:", dict(lab))

print("\n=== bug 15 / present flag ===")
for s, fl in (("A", flA), ("B", flB)):
    left = [(i, e["type"], rect(e)) for i, e in enumerate(fl) if e.get("present") is False]
    print(f"{s}: nodes present=False: {left}")
    for i, t, r in left:
        chain, a = [], fl[i]["parent"]
        while a >= 0:
            chain.append(f"{fl[a]['type']}[{a}]")
            a = fl[a]["parent"]
        print(f"   node {i}: first={fl[i].get('first')} last={fl[i].get('last')} text={tshape(fl[i]['text'])} parents:", chain[:5])

print("\n=== bug 17: ticking text (comp nodes with several values) ===")
for s, fl in (("A", flA), ("B", flB)):
    multi = [(i, e["type"], len(e["values"]), tshape(e["text"])) for i, e in enumerate(fl) if len(e.get("values") or []) > 1]
    print(f"{s}: nodes with 2+ values: {len(multi)} -> {multi}")

print("\n=== zero / missing boxes ===")
for s, fl in (("A", flA), ("B", flB)):
    z = [(i, e["type"], e["depth"], bool(e["text"]), e["node"].get("role") or "") for i, e in enumerate(fl) if rect(e) is None]
    print(f"{s}: {len(z)} nodes without a box: types={Counter(t for _, t, _, _, _ in z)}, with text={sum(1 for x in z if x[3])}")
    print("    ", z)
