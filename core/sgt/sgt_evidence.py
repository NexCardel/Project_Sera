"""
core/sgt/sgt_evidence.py - how a tracker row came to sit on its client
======================================================================
SGT sometimes files a capture under the wrong client and nobody knows why (2026-11). Every SGT
tracker row therefore carries a small evidence block, ``raw_payload["sgt_ev"]``: which read named
the client, which reads set the form, period, ARN and status, how the session was born, what
happened to it, and how the tracker matched the row to a client record. The tracker dump is
replicated by Sera Sync, so the evidence reaches every PC with the row - no log file, no sync code.

The block (short keys; about 1 KB):

  v, pc, at, sid   app version, capturing PC, wall time, session id
  born, parent     how the session started (BORN_*) and the session it came from
  tabs             how many browser tabs the session owns
  id               identity field -> stamp of the read that named the client (+ spec, others)
  d                data field (DATA_FIELDS) -> stamp of the read that set it
  tr               the last TRAIL_CAP session events: [wall seconds, code, tab, detail]
  m                the tracker's client match: {"how": MATCH_*, "n": candidates, "by": value}
  f                flags (FLAG_*): what makes the row worth a look
  prev             up to PREV_KEPT older blocks of this row, from another session or PC

A stamp is {"t": wall seconds, "tab": short tab id, "pg": page path, "src": "uia"|"ocr"|"link"},
plus "late": 1 (absorbed into a session no longer in view) and "lag" (seconds from asking for the
read to absorbing it, when LAG_NOTED_SEC or more).

Privacy: PAN / GSTIN, page paths without host or query, short tab ids and times. Never page text.
The block is never identity evidence: the tracker's candidate scan skips it (sera_db/srpf.py).
No Qt import. Design: docs/sgt_v2/sgtv2-design.md, section 6.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

EV_KEY = "sgt_ev"
WRONG_KEY = "wrong_client"          # top-level payload key: the user marked the row as on the wrong client
TRAIL_CAP = 30
PREV_KEPT = 2
LAG_NOTED_SEC = 2.0
PAGE_CHARS = 48
DATA_FIELDS = ("form", "period", "arn", "status")
LINK_SPEC = "client_db:user_id"     # sgt_shadow.CLIENT_LINK_SPEC (not imported: that module imports this one)

BORN_NEW, BORN_PARKED, BORN_STRICT, BORN_SPLIT, BORN_TAB = "new", "parked", "strict", "split", "tab"

MATCH_ID = "id"         # a saved client holds the row's own PAN / GSTIN
MATCH_OTHER = "other"   # matched through another value found in the payload
MATCH_NEAR = "near"     # the client of a capture made shortly before (never for SGT rows)
MATCH_GIVEN = "given"   # the caller's client id
MATCH_NONE = "none"

FLAG_TAB_DIFF, FLAG_DATA_FIRST, FLAG_LINK_ID = "tab-diff", "data-first", "link-id"
FLAG_LATE, FLAG_RESUMED, FLAG_OTHER_PAN, FLAG_OCR = "late", "resumed", "other-pan", "ocr"
FLAG_STRICT, FLAG_NO_TAB, FLAG_NO_ID = "strict", "no-tab", "no-id"
FLAG_MATCH_OTHER, FLAG_MATCH_NONE = "match-other", "match-none"
REASON_MARKED, REASON_CLIENT_LACKS_ID = "marked", "client-lacks-id"

_HOST = re.compile(r"^[a-z][a-z0-9+.-]*://[^/]*", re.IGNORECASE)
_PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def tab_of(tab: Any) -> str:
    """A browser tab id, shortened: enough to tell a session's tabs apart."""
    return str(tab)[-6:] if tab else ""


def page_of(url: Any) -> str:
    """The page's path (and #fragment) without host or query, its last PAGE_CHARS characters."""
    return _HOST.sub("", str(url or "")).split("?")[0][-PAGE_CHARS:]


def stamp(t: float, tab: Any, url: Any, source: str, late: bool = False, lag: float = 0.0) -> Dict[str, Any]:
    # tenths of a second: two reads of one second must still be told apart (data before identity)
    out: Dict[str, Any] = {"t": round(float(t), 1), "tab": tab_of(tab), "pg": page_of(url), "src": source or ""}
    if late:
        out["late"] = 1
    if lag >= LAG_NOTED_SEC:
        out["lag"] = round(lag, 1)
    return out


@dataclass
class SessionEv:
    """One session's evidence: how it was born, what happened to it, what named its client."""
    born: str = BORN_NEW
    parent: str = ""
    trail: List[List[Any]] = field(default_factory=list)
    ident: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def note(self, t: float, code: str, tab: Any = None, detail: str = "") -> None:
        """Adds a trail entry; the same event again right after itself is not repeated."""
        entry = [int(t), code, tab_of(tab), str(detail or "")[:24]]
        while entry and entry[-1] == "":
            entry.pop()
        if self.trail and self.trail[-1][1:] == entry[1:]:
            return
        self.trail.append(entry)
        del self.trail[:-TRAIL_CAP]

    def to_json(self) -> Dict[str, Any]:
        return {"born": self.born, "parent": self.parent, "trail": [list(e) for e in self.trail],
                "ident": {k: dict(v) for k, v in self.ident.items()}}

    @classmethod
    def from_json(cls, raw: Any) -> "SessionEv":
        raw = raw if isinstance(raw, dict) else {}
        return cls(born=str(raw.get("born") or BORN_NEW), parent=str(raw.get("parent") or ""),
                   trail=[list(e) for e in raw.get("trail") or [] if isinstance(e, list)][-TRAIL_CAP:],
                   ident={k: dict(v) for k, v in (raw.get("ident") or {}).items() if isinstance(v, dict)})


def flags_of(block: Dict[str, Any], client_known: bool) -> List[str]:
    """What makes this row worth a look. A flag is a reason to check, not a verdict."""
    ids = [v for v in (block.get("id") or {}).values() if isinstance(v, dict)]
    data = [v for v in (block.get("d") or {}).values() if isinstance(v, dict)]
    codes = {e[1] for e in block.get("tr") or [] if len(e) > 1}
    out = []
    if not client_known:
        out.append(FLAG_NO_ID)
    id_tabs = {i.get("tab") for i in ids if i.get("tab")}
    data_tabs = {d.get("tab") for d in data if d.get("tab")}
    if id_tabs and data_tabs and not data_tabs <= id_tabs:
        out.append(FLAG_TAB_DIFF)
    if ids and data and min(d.get("t", 0) for d in data) < min(i.get("t", 0) for i in ids):
        out.append(FLAG_DATA_FIRST)
    if any(i.get("spec") == LINK_SPEC for i in ids):
        out.append(FLAG_LINK_ID)
    if "late" in codes or any(x.get("late") for x in ids + data):
        out.append(FLAG_LATE)
    if block.get("born") == BORN_PARKED or any(c.startswith("resumed") for c in codes):
        out.append(FLAG_RESUMED)
    if any(i.get("others") for i in ids):
        out.append(FLAG_OTHER_PAN)
    if any(x.get("src") == "ocr" for x in ids + data):
        out.append(FLAG_OCR)
    if block.get("born") == BORN_STRICT:
        out.append(FLAG_STRICT)
    if any(not i.get("tab") and i.get("spec") != LINK_SPEC for i in ids):
        out.append(FLAG_NO_TAB)
    return out


def build(ev: SessionEv, slot_ev: Optional[Dict[str, Dict[str, Any]]], *, sid: str, tabs: int,
          client_known: bool, version: str, pc: str, at: str) -> Dict[str, Any]:
    """The evidence block of one tracker row."""
    block: Dict[str, Any] = {"v": version, "pc": pc, "at": at, "sid": sid, "born": ev.born, "tabs": tabs,
                             "id": {k: dict(v) for k, v in ev.ident.items()},
                             "d": {k: dict(v) for k, v in (slot_ev or {}).items()},
                             "tr": [list(e) for e in ev.trail]}
    if ev.parent:
        block["parent"] = ev.parent
    block["f"] = flags_of(block, client_known)
    return block


# ── The tracker's side (sera_db/tracker_dump.py) ─────────────────────────────────

def _load(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return {}
    return payload if isinstance(payload, dict) else {}


def block_of(payload: Any) -> Optional[Dict[str, Any]]:
    """The evidence block of a tracker payload (a JSON string or a dict), or None."""
    raw = _load(payload).get("raw_payload")
    block = raw.get(EV_KEY) if isinstance(raw, dict) else None
    return block if isinstance(block, dict) else None


def id_forms(values: Iterable[Any]) -> Set[str]:
    """The PANs and GSTINs among `values`, upper-cased, plus the PAN inside each GSTIN."""
    out: Set[str] = set()
    for v in values:
        v = str(v or "").strip().upper()
        if _PAN.match(v):
            out.add(v)
        elif _GSTIN.match(v):
            out.update((v, v[2:12]))
    return out


def own_ids(payload: Any) -> Set[str]:
    """The row's own client: the PAN / GSTIN the capture engine put on it."""
    p = _load(payload)
    raw = p.get("raw_payload") if isinstance(p.get("raw_payload"), dict) else {}
    return id_forms([p.get("pan"), p.get("gstin"), raw.get("pan"), raw.get("gstin")])


def match_how(own: Set[str], matched: Optional[str], near: bool, given: bool) -> str:
    if matched:
        return MATCH_NEAR if near else MATCH_ID if matched in own else MATCH_OTHER
    return MATCH_GIVEN if given else MATCH_NONE


def finish_row(payload: Dict[str, Any], candidates: List[str], matched: Optional[str], near: bool,
               given: bool, existing: Any) -> bool:
    """Called by insert_tracker_dump before the row is written. Records how the client record was
    chosen, keeps the blocks of the row it replaces (`existing`: that row's payload) when they are
    from another session or PC, and keeps the user's wrong-client mark. True when `payload` changed."""
    changed = False
    old = _load(existing)
    if old.get(WRONG_KEY) and not payload.get(WRONG_KEY):
        payload[WRONG_KEY] = old[WRONG_KEY]
        changed = True
    block = block_of(payload)
    if block is None:
        return changed
    own = own_ids(payload)
    how = match_how(own, matched, near, given)
    block["m"] = {"how": how, "n": len(candidates)}
    if how == MATCH_OTHER:
        block["m"]["by"] = matched
    flags = [f for f in block.get("f") or [] if not str(f).startswith("match-")]
    if how == MATCH_OTHER:
        flags.append(FLAG_MATCH_OTHER)
    elif how == MATCH_NONE and own:
        flags.append(FLAG_MATCH_NONE)
    block["f"] = flags
    old_block = block_of(old)
    if old_block is not None:
        prev = list(old_block.get("prev") or [])
        if (old_block.get("sid"), old_block.get("pc")) != (block.get("sid"), block.get("pc")):
            prev.insert(0, {k: v for k, v in old_block.items() if k != "prev"})
        if prev:
            block["prev"] = prev[:PREV_KEPT]
    return True


def set_wrong_mark(payload: Any, wrong: bool, pc: str, at: str) -> Optional[str]:
    """The payload JSON with the wrong-client mark set or removed; None when the payload is not a
    JSON object (it is then left alone)."""
    p = _load(payload)
    if not p and str(payload or "").strip() not in ("", "{}"):
        return None
    if wrong:
        p[WRONG_KEY] = {"pc": pc, "at": at}
    else:
        p.pop(WRONG_KEY, None)
    return json.dumps(p, ensure_ascii=False)


def is_marked_wrong(payload: Any) -> bool:
    if isinstance(payload, str) and WRONG_KEY not in payload:
        return False
    return bool(_load(payload).get(WRONG_KEY))


def export_entry(row: Dict[str, Any], client_ids: Optional[Set[str]]) -> Optional[Dict[str, Any]]:
    """One tracker row for the evidence export, or None when nothing about it needs a look.
    row: id, client_id, unassigned_identity, portal, period_label, arn_number, capture_method,
    status, raw_payload_json, captured_by, created_at, dataset_key. client_ids: the PANs / GSTINs
    of the client record the row sits on (None = it sits on no client)."""
    payload = _load(row.get("raw_payload_json"))
    block = block_of(payload)
    own = own_ids(payload)
    reasons = []
    if payload.get(WRONG_KEY):
        reasons.append(REASON_MARKED)
    if client_ids is not None and own and not own & client_ids:
        reasons.append(REASON_CLIENT_LACKS_ID)      # works on rows older than the evidence block too
    reasons += [f for f in (block or {}).get("f") or []]
    if not reasons:
        return None
    out = {k: row.get(k) for k in ("id", "created_at", "portal", "period_label", "arn_number", "status",
                                   "capture_method", "captured_by", "client_id", "unassigned_identity",
                                   "dataset_key")}
    out.update({"reasons": reasons, "row_ids": sorted(own),
                "client_ids": sorted(client_ids) if client_ids is not None else None,
                WRONG_KEY: payload.get(WRONG_KEY) or None, EV_KEY: block})
    return out


def write_export(entries: List[Dict[str, Any]], path: str, pc: str, version: str, made: str) -> int:
    """Writes the export file; returns the number of rows in it."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"made": made, "pc": pc, "v": version, "rows": entries}, f, ensure_ascii=False, indent=1)
    return len(entries)
