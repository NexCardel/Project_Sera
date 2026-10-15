"""Shared loader for the scratch scripts. Counts/shapes only are ever printed."""
import glob
import json
import sys
from pathlib import Path

import os
os.environ.setdefault("SDIS_DATA_DIR", "/tmp/sdis_cards_design/data")
REPO = Path("/home/user/Project_Sera")
CAP = REPO / "docs" / "sdis_experiments" / "captures"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools" / "pre_dev" / "class_diff"))

STAMPS = ("121513", "121834")


def comp(stamp):
    p = glob.glob(str(CAP / f"comp_20261012_{stamp}_1__*.json"))[0]
    return json.load(open(p, encoding="utf-8"))


def capture(stamp):
    p = glob.glob(str(CAP / f"capture_20261012_{stamp}_1__*.json"))[0]
    return json.load(open(p, encoding="utf-8"))


def children(fl):
    kids = [[] for _ in fl]
    for i, e in enumerate(fl):
        if e["parent"] >= 0:
            kids[e["parent"]].append(i)
    return kids


def rect(e):
    r = e["node"].get("rect")
    if not r or len(r) < 4 or r[2] <= 0 or r[3] <= 0:
        return None
    return tuple(int(v) for v in r[:4])


def tshape(t):
    """A text's shape, never its value: length and character classes."""
    if not t:
        return ""
    cls = ""
    if any(c.isdigit() for c in t):
        cls += "9"
    if any(c.isalpha() for c in t):
        cls += "A"
    if any(not c.isalnum() and not c.isspace() for c in t):
        cls += "."
    return f"len{len(t)}:{cls}"
