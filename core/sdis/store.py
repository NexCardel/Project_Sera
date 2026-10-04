"""
core/sdis/store.py - the mining state on disk (SDIS Part O)
============================================================
One gzip JSON file, data_dir() / memory.json.gz (admin PC only; it holds client page values, so it is as
private as the captures). The state is a plain dict:

  version        STATE_VERSION; a file of another version is not read (mine() rebuilds)
  last_run       ISO time of the last mine()
  files          {capture file name: [size, mtime]} - the files already taken in
  stamps         {capture file name: [snapshot stamps taken]}
  sessions       {session: {"files": [...], "links": [raw page links], "ids": [PAN / GSTIN ids]}}
  link_of        {raw page link: resolved link} as applied
  owners         {session: client name}
  session_maps   {session: {page key: LinkMap}} - for identity over all sessions
  todo           clients whose maps are not (all) in the memories yet (a cancelled run resumes here)
  memories       [PageMemory] - screens of one (link, browser) are next to each other
  rejected       datapoint keys the user rejected; picked: the ones the user picked
  rejected_triples  (link, shape, text) of rejected variable_alignment nodes, given to new memories
  datapoints     the last relevance result as plain dicts (relevance.Datapoint fields)

save() writes a .tmp beside the file and os.replace()s it, so a killed run leaves the old file whole.
"""

import gzip
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.sdis.link_map import LinkMap
from core.sdis.memory import PageMemory
from core.sdis.paths import data_dir

STATE_VERSION = 1
STATE_NAME = "memory.json.gz"
log = logging.getLogger(__name__)


def default_path() -> Path:
    return data_dir() / STATE_NAME


def new_state() -> Dict[str, Any]:
    return {"version": STATE_VERSION, "last_run": "", "files": {}, "stamps": {}, "sessions": {}, "link_of": {},
            "owners": {}, "session_maps": {}, "todo": [], "memories": [], "rejected": [], "picked": [],
            "rejected_triples": [], "datapoints": []}


def _map_to(lm: LinkMap) -> Dict[str, Any]:
    return {"client": lm.client, "page": lm.page, "screen": lm.screen, "link": lm.link, "browser": lm.browser,
            "entries": {k: dict(e, keys=sorted(e["keys"])) for k, e in lm.entries.items()},
            "children": [[pk, ks] for pk, ks in lm.children.items()],
            "reads": lm.reads, "read_info": lm.read_info, "sessions": lm.sessions, "events": lm.events}


def _map_from(d: Dict[str, Any]) -> LinkMap:
    lm = LinkMap(d["client"], d["page"], screen=d["screen"], link=d["link"], browser=d["browser"])
    lm.entries = {k: dict(e, keys=set(e["keys"])) for k, e in d["entries"].items()}
    lm.children = {pk: ks for pk, ks in d["children"]}
    lm.reads, lm.read_info, lm.sessions, lm.events = d["reads"], d["read_info"], d["sessions"], d["events"]
    return lm


def _memory_to(pm: PageMemory) -> Dict[str, Any]:
    return {"page": pm.page, "n": pm.n, "retire": pm.retire, "screen": pm.screen, "link": pm.link,
            "browser": pm.browser, "rejected": sorted(list(t) for t in pm.rejected), "clients": pm.clients,
            "read_info": pm.read_info, "nodes": pm.nodes, "order": pm.order, "pending": pm.pending, "log": pm.log,
            "views": {c: [flat, nid_of] for c, (flat, nid_of) in pm.views.items()}}


def _memory_from(d: Dict[str, Any]) -> PageMemory:
    pm = PageMemory(d["page"], d["n"], screen=d["screen"], link=d["link"], browser=d["browser"],
                    rejected=[tuple(t) for t in d["rejected"]], retire=d["retire"])
    pm.clients, pm.read_info, pm.nodes = d["clients"], d["read_info"], d["nodes"]
    pm.order, pm.pending, pm.log = d["order"], d["pending"], d["log"]
    pm.views = {c: (flat, nid_of) for c, (flat, nid_of) in d["views"].items()}
    return pm


def _key(k: Any) -> Any:
    return tuple(k) if isinstance(k, list) else k


def to_json(state: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in state.items() if k not in ("session_maps", "memories", "rejected", "picked",
                                                          "rejected_triples")}
    out["session_maps"] = {s: {p: _map_to(lm) for p, lm in maps.items()} for s, maps in state["session_maps"].items()}
    out["memories"] = [_memory_to(pm) for pm in state["memories"]]
    out["rejected"] = [list(k) if isinstance(k, tuple) else k for k in state["rejected"]]
    out["picked"] = [list(k) if isinstance(k, tuple) else k for k in state["picked"]]
    out["rejected_triples"] = [list(t) for t in state["rejected_triples"]]
    return out


def from_json(d: Dict[str, Any]) -> Dict[str, Any]:
    state = new_state()
    state.update({k: v for k, v in d.items() if k in state})
    state["session_maps"] = {s: {p: _map_from(m) for p, m in maps.items()} for s, maps in d["session_maps"].items()}
    state["memories"] = [_memory_from(m) for m in d["memories"]]
    state["rejected"] = [_key(k) for k in d["rejected"]]
    state["picked"] = [_key(k) for k in d["picked"]]
    state["rejected_triples"] = [tuple(t) for t in d["rejected_triples"]]
    return state


def save(state: Dict[str, Any], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=5) as f:
        json.dump(to_json(state), f, separators=(",", ":"))
    os.replace(tmp, path)


def load(path: Path) -> Optional[Dict[str, Any]]:
    """The saved state, or None when there is none, it cannot be read, or it is of another STATE_VERSION
    (then the caller rebuilds from the captures). Only counts are logged."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or d.get("version") != STATE_VERSION:
            log.warning("SDIS state has version %r, expected %d: rebuilding", d.get("version") if isinstance(d, dict) else None,
                        STATE_VERSION)
            return None
        return from_json(d)
    except (OSError, EOFError, ValueError, KeyError, TypeError, IndexError) as e:   # BadGzipFile is an OSError
        log.warning("SDIS state unreadable (%s): rebuilding", type(e).__name__)
        return None


def datapoint_dicts(dps: List[Any]) -> List[Dict[str, Any]]:
    from dataclasses import asdict
    return [asdict(dp) for dp in dps]
