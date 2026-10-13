"""
core/sdis/distill.py - what the Distill dialog does, without Qt (SDIS Part L)
============================================================================
The dialog (ui/dialogs/sdis_dialog.py) only draws. Here: the user's decisions (the synced
`sdis_decisions` rows) read into maps, the datapoints and the "Please check" items built from the
saved mining state, the decisions pushed into that state so the NEXT mining run honours them, and
the staging folder that lets one mining run read the admin's own captures and every pushed device
folder.

A decision row is (signature, decision, label):
  label|<key>    decision "label"      label = what the user named the datapoint (R6)
  move|<key>     decision = a class    the user put it in another container kind (profile/dataset/info)
  dismiss|<key>  decision "dismissed"  never listed again (D8: a rejected kind is left out)
  reg|<key>      decision "registered" label = the sdis_mcl field name it was added as (D7)
  va|<json>      decision keep/reject  one variable_alignment text: keep = data, reject = template
<key> is the datapoint key as JSON; <json> is [link, shape, text]. Nothing here is logged.
"""

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

LABEL, MOVE, DISMISS, REG, VA = "label", "move", "dismiss", "reg", "va"
DISMISSED, REGISTERED, KEEP, REJECT = "dismissed", "registered", "keep", "reject"
CLASS_NAMES = {"profile": "Profile", "dataset": "Dataset", "info": "Others"}


def signature(kind: str, key: Iterable[Any]) -> str:
    return kind + "|" + json.dumps(list(key), ensure_ascii=False)


def _unsign(sig: str) -> Tuple[str, Optional[tuple]]:
    kind, _, rest = sig.partition("|")
    try:
        return kind, tuple(json.loads(rest))
    except (ValueError, TypeError):
        return kind, None


class Decided:
    """The decision rows, read once: labels / moves / registered by datapoint key, dismissed keys,
    and the variable_alignment answers by (link, shape, text)."""

    def __init__(self) -> None:
        self.labels: Dict[tuple, str] = {}
        self.moves: Dict[tuple, str] = {}
        self.dismissed: set = set()
        self.registered: Dict[tuple, str] = {}
        self.va: Dict[tuple, str] = {}

    def rejected_triples(self) -> set:
        return {t for t, d in self.va.items() if d == REJECT}


def read_decisions(rows: Iterable[Dict[str, Any]]) -> Decided:
    out = Decided()
    for r in rows:
        kind, key = _unsign(r.get("signature") or "")
        if key is None:
            continue
        dec, label = r.get("decision") or "", r.get("label") or ""
        if kind == LABEL and label:
            out.labels[key] = label
        elif kind == MOVE and dec:
            out.moves[key] = dec
        elif kind == DISMISS:
            out.dismissed.add(key)
        elif kind == REG and label:
            out.registered[key] = label
        elif kind == VA and dec in (KEEP, REJECT) and len(key) == 3:
            out.va[key] = dec
    return out


def datapoint_of(d: Any) -> Any:
    """A relevance.Datapoint from a saved dict (lists back to tuples)."""
    from core.sdis.relevance import Datapoint
    if isinstance(d, Datapoint):
        return d
    return Datapoint(key=tuple(d["key"]), label=d.get("label", ""), value_type=d.get("value_type", ""),
                     relevance_pct=int(d.get("relevance_pct", 0)), sure_pct=int(d.get("sure_pct", 0)),
                     pages=[tuple(p) for p in d.get("pages") or ()], nodes=[tuple(n) for n in d.get("nodes") or ()],
                     slot_type_pct=int(d.get("slot_type_pct", 0)), surprise=bool(d.get("surprise", False)),
                     suggested_class=d.get("suggested_class"), class_reason=d.get("class_reason", ""),
                     status=d.get("status", ""))


def datapoints_of(state: Dict[str, Any]) -> List[Any]:
    return [datapoint_of(d) for d in state.get("datapoints") or ()]


def shown_label(dp: Any, decided: Decided, field_labels: Optional[Dict[str, str]] = None) -> str:
    """The label the user sees: the library label of a registered field, else the user's edit, else
    what SDIS suggested (R6: an edit is never overwritten by a run)."""
    field = decided.registered.get(dp.key)
    if field and (field_labels or {}).get(field):
        return field_labels[field]
    return decided.labels.get(dp.key) or dp.label or ""


def example_of(dp: Any, memories: List[Any]) -> str:
    """One value of the datapoint, shown live to the admin only (never logged)."""
    from core.sdis.memory import voters
    for mi, nid in dp.nodes:
        try:
            nodes = memories[mi].nodes
            for c in voters(nodes[nid]).get("clients", {}).values():
                if c.get("texts"):
                    return str(c["texts"][-1])
        except (IndexError, AttributeError, KeyError, TypeError):
            continue
    return ""


def status_of(dp: Any, memories: List[Any]) -> str:
    """The datapoint's status (fixed / variable / semi-variable / variable_alignment etc.)."""
    from collections import Counter
    st = getattr(dp, "status", "")
    if st:
        return st
    counts: Counter = Counter()
    for mi, nid in getattr(dp, "nodes", ()):
        try:
            if mi < len(memories):
                s = memories[mi].status(nid)
                if s:
                    counts[s] += 1
        except Exception:
            continue
    return ", ".join(sorted(counts.keys())) if counts else ""


def portal_of(link: str) -> str:
    from core.vsdc import vsdc_scope
    return vsdc_scope.portal_for_url(link or "") or ""


def portal_of_datapoint(dp: Any) -> str:
    for page in dp.pages:
        p = portal_of(page[0])
        if p:
            return p
    return ""


def alignment_items(state: Dict[str, Any], decided: Decided) -> List[Dict[str, Any]]:
    """One item per (page link, shape, text) the memory holds as variable_alignment and the user has
    not answered: {triple, text, where, saw}. Counts only in `saw`."""
    from core.sdis.memory import _last_texts, voters
    items: List[Dict[str, Any]] = []
    seen = set()
    for pm in state.get("memories") or ():
        for nid, nd in enumerate(pm.nodes):
            if pm.status(nid) != "variable_alignment":
                continue
            text = next(iter(_last_texts(voters(nd))), "")
            triple = (pm.link, nd["shape"], text)
            if triple in seen or triple in decided.va:
                continue
            seen.add(triple)
            m = len(pm.clients) or 1
            pct = round(100 * len(voters(nd)["clients"]) / m)
            where = pm.link + (" · " + pm.browser if pm.browser else "")
            items.append({"triple": triple, "text": text, "where": where,
                          "saw": f"The same text for {pct}% of the clients, in a slot whose kind of text "
                                 f"differs between clients elsewhere on this page",
                          "page": pm.link, "browser": pm.browser, "pct": pct})
    return items


def apply_decisions(state: Dict[str, Any], decided: Decided) -> bool:
    """Pushes the decisions into the mining state so the next run honours them: dismissed keys ->
    `rejected`, registered keys -> `picked` (D8), rejected variable_alignment triples ->
    `rejected_triples` and every memory of that link (a kept one is taken back out). True when the
    state changed (the caller saves it - never while a mining child is running)."""
    changed = False
    for lst, keys in ((state["rejected"], decided.dismissed), (state["picked"], set(decided.registered))):
        for k in sorted(keys):
            if k not in lst:
                lst.append(k)
                changed = True
    rejected = decided.rejected_triples()
    kept = {t for t, d in decided.va.items() if d == KEEP}
    for t in sorted(rejected):
        if t not in state["rejected_triples"]:
            state["rejected_triples"].append(t)
            changed = True
    for t in sorted(kept):
        if t in state["rejected_triples"]:
            state["rejected_triples"].remove(t)
            changed = True
    for pm in state.get("memories") or ():
        for t in rejected:
            if t[0] in (pm.link, pm.page) and t not in pm.rejected:
                pm.rejected.add(t)
                changed = True
        for t in kept:
            if t in pm.rejected:
                pm.rejected.discard(t)
                changed = True
    return changed


def refresh_datapoints(state: Dict[str, Any], decided: Decided,
                       class_exceptions: Optional[Dict[str, str]] = None) -> List[Any]:
    """The datapoints to list. When the memories are there and a decision changed what counts
    (apply_decisions returned True) the caller passes recompute=True through recount(); here the
    saved list is only given the user's moves and the file's class exceptions."""
    from core.sdis.classes import suggest
    dps = datapoints_of(state)
    hit = [dp for dp in dps if dp.key in decided.moves
           or (decided.registered.get(dp.key) in (class_exceptions or {}))]
    if hit:
        got = suggest(hit, state.get("memories") or [], decided.moves, class_exceptions, decided.registered)
        for dp in hit:
            dp.suggested_class, dp.class_reason = got[dp.key]
    return dps


def recount(state: Dict[str, Any], decided: Decided, class_exceptions: Optional[Dict[str, str]] = None) -> List[Any]:
    """Relevance and class suggestion again over the loaded memories, with the user's decisions in
    (what a mining run does in its step 5); stored back as the state's datapoints."""
    from core.sdis import store
    from core.sdis.classes import annotate
    from core.sdis.relevance import datapoints
    mems = state.get("memories") or []
    dps = datapoints(mems, state["rejected"], state["picked"])
    annotate(dps, mems, decided.moves, class_exceptions, decided.registered)
    state["datapoints"] = store.datapoint_dicts(dps)
    return dps


def last_run_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    mems = state.get("memories") or []
    owners = {o for o in (state.get("owners") or {}).values() if not str(o).startswith("undecided")}
    return {"when": state.get("last_run") or "",
            "captures": sum(len(v) for v in (state.get("stamps") or {}).values()),
            "clients": len(owners),
            "browsers": len({pm.browser for pm in mems if pm.browser})}


def counts(dps: List[Any], decided: Decided, items: List[Any], new_keys: set,
           captured: set) -> Dict[str, int]:
    live = [dp for dp in dps if dp.key not in decided.dismissed]
    return {"found": len(live), "new": len([dp for dp in live if dp.key in new_keys]),
            "check": len(items), "captured": len([dp for dp in live if dp.key in captured]),
            "little": len([dp for dp in live if dp.class_reason == "not enough evidence"])}


# ── containers: where a datapoint can go, and what is in each place ──

Where = Any            # a dataset container's name, or ("profile" | "others", portal)


def where_label(where: Where) -> str:
    if isinstance(where, tuple):
        return "Profile builder" if where[0] == "profile" else "Others"
    return where


def where_class(where: Where) -> str:
    """The sdis_mcl class a field added there gets."""
    if isinstance(where, tuple):
        return "profile" if where[0] == "profile" else "others"
    return "dataset"


def container_fields(doc: Dict[str, Any], where: Where) -> List[str]:
    if isinstance(where, tuple):
        return list(((doc.get(where[0]) or {}).get(where[1])) or ())
    for c in doc.get("containers") or ():
        if c.get("name") == where:
            return list(c.get("fields") or ())
    return []


def portal_containers(doc: Dict[str, Any], portal: str) -> List[Where]:
    """Every place on the portal, in the order the side panel lists them."""
    return [("profile", portal)] + [c["name"] for c in doc.get("containers") or () if c.get("portal") == portal] \
        + [("others", portal)]


def suggest_container(doc: Dict[str, Any], portal: str, cls: Optional[str]) -> Optional[Where]:
    """The place a datapoint of this suggested class belongs on the portal: its Profile builder or
    Others, or the portal's only dataset container (several: the user picks)."""
    if cls == "profile":
        return ("profile", portal)
    if cls == "info":
        return ("others", portal)
    if cls == "dataset":
        names = [c["name"] for c in doc.get("containers") or () if c.get("portal") == portal]
        return names[0] if len(names) == 1 else None
    return None


# ── "new since the last run": a small file beside the state (admin PC only, as private as it) ──

def roll_seen(path: Path, last_run: str, keys: Iterable[tuple]) -> set:
    """Keys that were not in the run before this one. The file holds {"run", "before", "now"}; when
    the state's run changed, `now` becomes `before`. The first run has nothing to compare with."""
    now = sorted(json.dumps(list(k), ensure_ascii=False) for k in keys)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if data.get("run") != last_run:
        data = {"run": last_run, "before": data.get("now"), "now": now}
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass
    if data.get("before") is None:
        return set()
    old = set(data["before"])
    return {tuple(json.loads(s)) for s in now if s not in old}


# ── one mining input folder out of the admin's own folder and every pushed device folder ──

def stage_inputs(dest: Path, sources: Iterable[Tuple[str, Path]]) -> Path:
    """mine() reads ONE folder and keys files by bare name, while the same day's file exists on every
    device. So each recorder file is linked (copied when linking is not possible) into `dest` as
    sdis_<tag>__<rest of its name>; names stay the same from run to run, a changed source is
    refreshed. `sources` = [(tag, folder)]."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for tag, folder in sources:
        folder = Path(folder)
        if not folder.is_dir():
            continue
        for src in sorted(folder.glob("sdis_*.jsonl")):
            name = "sdis_%s__%s" % (tag, src.name[len("sdis_"):])
            target = dest / name
            try:
                if target.exists():
                    if os.path.samefile(src, target) or (src.stat().st_size, src.stat().st_mtime) == (
                            target.stat().st_size, target.stat().st_mtime):
                        continue
                    target.unlink()
                try:
                    os.link(src, target)
                except OSError:
                    shutil.copy2(src, target)
            except OSError:
                continue
    return dest


def mining_sources() -> List[Tuple[str, Path]]:
    """The admin's own recorder folder, then corpus/<device id>/ of every device that pushed."""
    from core.sdis import recorder
    from core.sdis.paths import data_dir
    out: List[Tuple[str, Path]] = [("local", recorder.default_dir())]
    corpus = data_dir() / "corpus"
    if corpus.is_dir():
        out += [(p.name, p) for p in sorted(corpus.iterdir()) if p.is_dir()]
    return out


def stage_default() -> Path:
    from core.sdis.paths import data_dir
    return stage_inputs(data_dir() / "mine_input", mining_sources())
