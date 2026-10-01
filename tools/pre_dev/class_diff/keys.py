"""
tools/pre_dev/class_diff/keys.py - PRE-DEV TEST: a reliable key for every node of a page read
==============================================================================================
Shared by compare.py (client vs client) and snapshot_diff.py (one client, before vs after).

A node's KEY is its address inside the page, built from structure only - never its text (the text
is what gets compared; the key says where it sits):

    D2 / Group.container[1] / Group.card[2] / Group.valueBox[1] / Text[2]

Each step is: element type, + "#id" when the element has a stable id, + ".classes", + [n] = the
n-th child OF THE SAME PARENT with that same step. Counting per parent (not across the page)
means a new element only renumbers its own siblings, never the rest of the page.

Class names that go into a key:
* tokens holding a digit are dropped (often generated: "c-3", "mat-input-7");
* STATE tokens are dropped - classes a page flips while you use it (ng-valid -> ng-invalid,
  mat-focused, active, expanded...). A token is state when it, or its last part after "-"/"_",
  is a STATE_WORDS word (so "ng-dirty", "cdk-keyboard-focused", "mdc-text-field--invalid" all go).
  The list is generic web vocabulary, not portal wording.
* LEARNED state tokens are dropped too: any other token snapshot_diff.py has SEEN flip on one
  element between two reads is saved to output/learned_state.json and left out of keys from then
  on (evidence, not a guess - "mdc-text-field--filled" is static on one portal, a state on another).

Ids: an id with no digit ("pan") is added to its step. It does NOT replace the path: portals reuse
ids (the ITR page used #itrVerticalStepper six times), so an id alone is not a safe key.

LOOSE key: the same address with element types and positions only (no ids, no classes). Two
reads whose strict keys differ but whose loose keys match are the same element whose classes
flipped - snapshot_diff.py uses it to tell "class flip" from "removed + added".
"""

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List

LEARNED_FILE = Path(__file__).resolve().parent / "output" / "learned_state.json"

STATE_WORDS = frozenset({
    "active", "inactive", "selected", "unselected", "current",
    "disabled", "enabled", "readonly",
    "focus", "focused", "hover", "hovered", "pressed",
    "dirty", "pristine", "touched", "untouched", "valid", "invalid", "pending", "submitted",
    "open", "opened", "closed", "shown", "showing", "hidden", "visible", "invisible",
    "expanded", "collapsed", "collapsing",
    "checked", "unchecked", "indeterminate",
    "loading", "loaded", "animating",
})
# Short words that are state only as a WHOLE token (Bootstrap "collapse in", "fade show") - as a
# last part they are ordinary words ("btn-sign-in", "slide-show", "btn-hide").
STATE_WHOLE_ONLY = frozenset({"in", "fade", "collapse", "show", "hide"})
# Two-part state endings ("mdc-floating-label--float-above": the label floats once a value is in).
STATE_ENDINGS = ("float-above", "should-float", "label-floating", "has-value")

_INPUTS = frozenset({50003, 50004, 50016})      # combo box, edit, spinner: the value is the data


def _load_learned() -> set:
    try:
        return set(json.loads(LEARNED_FILE.read_text(encoding="utf-8")).get("tokens") or [])
    except (OSError, ValueError):
        return set()


LEARNED = _load_learned()


def learn(tokens: Iterable[str]) -> List[str]:
    """Remember tokens seen flipping; returns the ones that were new."""
    new = sorted(set(tokens) - LEARNED)
    if new:
        LEARNED.update(new)
        LEARNED_FILE.parent.mkdir(parents=True, exist_ok=True)
        LEARNED_FILE.write_text(json.dumps({"tokens": sorted(LEARNED)}, indent=1), encoding="utf-8")
    return new


def is_state_token(token: str) -> bool:
    if token in LEARNED:
        return True
    t = token.lower()
    parts = [p for p in re.split(r"[-_]+", t) if p]
    return t in STATE_WORDS or t in STATE_WHOLE_ONLY or (bool(parts) and parts[-1] in STATE_WORDS) \
        or any(t.endswith(e) for e in STATE_ENDINGS)


def clean_classes(cls: str) -> str:
    """The class tokens that can key a node: sorted; digit-holding and state tokens dropped."""
    return " ".join(sorted(t for t in (cls or "").split()
                           if not any(c.isdigit() for c in t) and not is_state_token(t)))


def stable_id(aid: str) -> str:
    return aid if aid and not any(c.isdigit() for c in aid) else ""


def page_link(url: str) -> str:
    """A page's identity from its address: host + path + the single-page-app route after '#/'
    (or '#!/'), UNMASKED - "gstr1" and "gstr2b" stay apart (core/sgt_i/atlas.url_hint masks
    every segment holding a digit, which turns both into "AAAA9"). The query ("?id=42") is left
    out: it usually carries per-client or per-visit values, which would make the same page a
    different link for every client. A fragment that is not a route ("#top") is left out too."""
    from urllib.parse import urlparse
    try:
        u = urlparse(url or "")
    except ValueError:
        return ""
    if not u.scheme and not u.netloc and "/" in (url or "") and not (url or "").startswith(("/", "file:")):
        u = urlparse("https://" + url)          # the address bar often shows "host/path" without a scheme
    out = (u.netloc + u.path).rstrip("/") or u.path
    frag = (u.fragment or "").split("?")[0]
    if frag.startswith("!"):
        frag = frag[1:]
    if frag.startswith("/") and frag.strip("/"):
        out += "#" + frag.rstrip("/")
    return out


SLUG_MAX = 90


def page_slug(page: str) -> str:
    """A page link as a file-name part: "return.gst.gov.in/returns/auth#/gstr1/file" ->
    "return.gst.gov.in_returns_auth_gstr1_file". Same link, same name - in every session. A link
    too long for a file name keeps its END (the route, which tells pages apart)."""
    s = re.sub(r"[^A-Za-z0-9.-]+", "_", page or "").strip("_.") or "page"
    return s if len(s) <= SLUG_MAX else s[-SLUG_MAX:].lstrip("_.")


def breadcrumb(flat: List[Dict[str, Any]]) -> str:
    """The page's breadcrumb trail ("Home > Returns > View Filed Returns"), when it shows one: the
    texts under the first element whose class, id or name says "breadcrumb" (standard web wording,
    not portal wording), in page order. "" when there is none."""
    for i, e in enumerate(flat):
        n = e["node"]
        if not any("breadcrumb" in (n.get(f) or "").lower() for f in ("cls", "id", "name")):
            continue
        trail: List[str] = []
        for d in flat[i + 1:]:
            if d["depth"] <= e["depth"]:
                break
            t = d["text"]
            if t and t not in trail and t.lower() != "breadcrumb":
                trail.append(t)
        if trail:
            return " > ".join(trail)
    return ""


# Elements where the user makes a CHOICE - their value is data (compare.py types it "control").
CT_COMBOBOX, CT_RADIO, CT_CHECKBOX = 50003, 50013, 50002
CHOICE_CTYPES = frozenset({CT_COMBOBOX, CT_RADIO, CT_CHECKBOX})
# Buttons: actions, not data and not labels - compare.py leaves them out.
BUTTON_CTYPES = frozenset({50000, 50031})                    # Button, SplitButton


def is_chosen(n: Dict[str, Any]) -> bool:
    """A radio / checkbox is selected: uia_nodes' "selected", or the browser's aria state
    (key_probe keeps aria properties; "checked=true" / "selected=true")."""
    if n.get("selected") is True:
        return True
    return any(p.strip() in ("checked=true", "selected=true", "checked=mixed") for p in (n.get("aria") or "").split(";"))


def text_of(n: Dict[str, Any]) -> str:
    """What an element SAYS - the thing compared between sessions:
    * an input box / dropdown: its value only (an empty box has no text - its name is just its
      label, which the page shows as its own text anyway); a dropdown's value is the chosen option;
    * a radio / checkbox: its caption and whether it is chosen ("Individual = selected"), so a
      different choice is a different value;
    * anything else: its name (a link's name, not its address)."""
    ct = n.get("ctype")
    if ct in _INPUTS:
        return (n.get("value") or "").strip()
    name = (n.get("name") or "").strip()
    if ct in (CT_RADIO, CT_CHECKBOX) and name:
        return f"{name} = {'selected' if is_chosen(n) else 'not selected'}"
    return name


def flatten(rec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every raw-view node of a key_probe read, in page order, each with:
    key, loose (see module doc), parent (index into this list, -1 = none), depth, cls (the
    cleaned classes), type, text (a text that only repeats its parent's is blanked: Chromium lists
    a heading's text twice), node (the raw node)."""
    out: List[Dict[str, Any]] = []
    for d, doc in enumerate(rec.get("docs") or []):
        base = len(out)
        strict_seen: Dict[int, Counter] = {}
        loose_seen: Dict[int, Counter] = {}
        for n in doc:
            p = n.get("parent", -1)
            parent = base + p if p >= 0 else -1
            typ = str(n.get("type_name") or n.get("ctype"))
            cls = clean_classes(n.get("cls", ""))
            aid = stable_id(n.get("id", ""))
            step = typ + (f"#{aid}" if aid else "") + (f".{cls}" if cls else "")
            sc = strict_seen.setdefault(parent, Counter())
            lc = loose_seen.setdefault(parent, Counter())
            sc[step] += 1
            lc[typ] += 1
            head, lhead = (out[parent]["key"], out[parent]["loose"]) if parent >= 0 else (f"D{d + 1}", f"D{d + 1}")
            text = text_of(n)
            if parent >= 0 and text == out[parent]["text"]:
                text = ""
            out.append({"node": n, "key": f"{head} / {step}[{sc[step]}]", "loose": f"{lhead} / {typ}[{lc[typ]}]",
                        "parent": parent, "depth": out[parent]["depth"] + 1 if parent >= 0 else 0,
                        "cls": cls, "type": typ, "text": text})
    return out
