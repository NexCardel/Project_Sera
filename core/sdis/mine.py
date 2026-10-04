"""
core/sdis/mine.py - "Find datapoints": incremental mining (SDIS Part O, engine side)
=====================================================================================
mine() brings the saved state (store.py) up to date with a folder of captures, and takes only what is new:

  1. SOURCES   a capture file whose size/mtime is unchanged is not even opened; of a changed or new
               file only the snapshots (stamps) not taken yet are new.
  2. SESSIONS  a session with new snapshots gets its PAN / GSTIN ids and raw page links updated and its
               session maps rebuilt from its own files (a session is one capture, or the reads of one
               --client name). Links are resolved (Part N) over ALL raw links seen so far, owners (Part D)
               over ALL session maps so far.
  3. SAVE      the session-level facts are saved, with the clients whose maps still have to reach the
               memories in `todo` (a cancelled or killed run resumes from there).
  4. CLIENTS   each client in `todo` gets its maps rebuilt from all its sessions' files and added to the
               right PageMemory, ONE client map at a time (memory.place_map, as client_maps() does);
               progress(done, total, page_link) is called and the state saved after each. A client already
               in a memory is simply added again: votes are per client, so it counts once.
  5. RELEVANCE recomputed from the memories and stored.

A memory is rebuilt from scratch (its whole (link, browser) group is dropped and its clients re-added)
when the owner or the resolved link of an ALREADY-ADDED session changed, because votes already cast
cannot be taken back. Only counts are logged. Screen weights (Part G) are taken over the new batch plus
the stored session maps, so a screen split on an incremental run can differ from a rebuild in rare cases.
"""

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from core.sdis import link_map, store
from core.sdis.memory import PageMemory, place_map

log = logging.getLogger(__name__)


class _Reader:
    """Capture files, each opened at most once per run; None for one that cannot be read (half-written)."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.cache: Dict[str, Optional[List[Any]]] = {}

    def sources(self, name: str) -> Optional[List[Any]]:
        if name not in self.cache:
            try:
                self.cache[name] = link_map.file_sources(self.directory / name)
            except (OSError, ValueError, KeyError, TypeError):
                self.cache[name] = None
        return self.cache[name]


def _undecided(owner: str) -> bool:
    return owner.startswith("undecided")


def _weights(state: Dict[str, Any], fresh: List[Any], leave_out: Set[str], link_of: Dict[str, str]) -> Any:
    """Screen weights over `fresh` sources plus the stored session maps of the other, decided sessions."""
    from core.sdis.screens import link_weights
    pseudo = [("", s, lm.link, lm.to_flat()) for s, maps in state["session_maps"].items()
              if s not in leave_out and not _undecided(state["owners"].get(s, ""))
              for lm in maps.values()]
    return link_weights(pseudo + [(a, b, link_of.get(c, c), d) for a, b, c, d in fresh])


def _session_sources(state: Dict[str, Any], reader: _Reader, sessions: Set[str]) -> List[Any]:
    out = []
    for s in sessions:
        for name in state["sessions"][s]["files"]:
            out.extend(x for x in (reader.sources(name) or []) if x[1] == s)
    return sorted(out, key=lambda x: x[0])


def counts(state: Dict[str, Any]) -> Dict[str, int]:
    """{'<status> <verdict>': text nodes} over the memories with 2+ clients."""
    from collections import Counter
    total: Counter = Counter()
    for pm in state["memories"]:
        if len(pm.clients) >= 2:
            total.update({f"{st} {v}": k for (st, v), k in pm.summary().items()})
    return dict(total)


def mine(captures_dir: Any, state_path: Any = None, progress: Optional[Callable[[int, int, str], None]] = None,
         cancel: Any = None, rebuild: bool = False) -> Dict[str, Any]:
    """Bring the state at state_path (default data_dir()/memory.json.gz) up to date with captures_dir.
    Returns {processed (new snapshots taken), files, clients (client maps added), pages (memories),
    rebuilt (pages rebuilt from scratch), rebuild_all, cancelled, skipped, verdicts, datapoints, seconds}."""
    from core.sdis.identity import group_clients, resolve_owners, client_ids
    from core.sdis.links import resolve

    t0 = time.time()
    captures_dir = Path(captures_dir)
    state_path = Path(state_path) if state_path else store.default_path()
    old = store.load(state_path)
    rebuild_all = rebuild or old is None
    state = store.new_state() if rebuild_all else old
    if rebuild_all and old is not None:                       # what the user decided outlives a rebuild
        for k in ("rejected", "picked", "rejected_triples"):
            state[k] = old[k]
    summary: Dict[str, Any] = {"processed": 0, "files": 0, "clients": 0, "pages": 0, "rebuilt": [],
                               "rebuild_all": rebuild_all, "cancelled": False, "skipped": 0, "verdicts": {},
                               "datapoints": 0, "seconds": 0.0}

    # 1. sources not taken yet
    reader = _Reader(captures_dir)
    fresh: List[Any] = []
    meta: Dict[str, Tuple[List[float], List[str]]] = {}
    files_of: Dict[str, Set[str]] = {}
    for path in sorted([*captures_dir.glob("key_probe_*.json"), *captures_dir.glob("capture_*.json")],
                       key=lambda p: p.name):
        try:
            st = path.stat()
        except OSError:
            summary["skipped"] += 1
            continue
        sig = [st.st_size, st.st_mtime]
        if state["files"].get(path.name) == sig:
            continue
        srcs = reader.sources(path.name)
        if srcs is None:
            summary["skipped"] += 1
            continue
        have = set(state["stamps"].get(path.name, ()))
        news = [s for s in srcs if s[0] not in have]
        fresh.extend(news)
        for s in news:
            files_of.setdefault(s[1], set()).add(path.name)
        meta[path.name] = (sig, [s[0] for s in srcs])
        summary["files"] += bool(news)
    summary["processed"] = len(fresh)

    def finish() -> Dict[str, Any]:
        summary["pages"] = len(state["memories"])
        summary["verdicts"] = counts(state)
        summary["datapoints"] = len(state["datapoints"])
        summary["seconds"] = round(time.time() - t0, 2)
        return summary

    def take_in_files() -> None:
        for name, (sig, stamps) in meta.items():
            state["files"][name] = sig
            state["stamps"][name] = stamps

    if not fresh and not state["todo"]:
        if meta:                                               # files touched, nothing new in them
            take_in_files()
            store.save(state, state_path)
        return finish()

    # 2. sessions: ids, raw links, session maps, links, owners
    processed_before = set(state["sessions"])
    touched = {s[1] for s in fresh}
    if fresh:
        ids: Dict[str, Set[str]] = {}
        links: Dict[str, Set[str]] = {}
        for (session, _page), lm in link_map.build_maps(sources=fresh).items():
            ids.setdefault(session, set()).update(client_ids(lm))
            links.setdefault(session, set()).add(lm.link)
        for s in touched:
            info = state["sessions"].setdefault(s, {"files": [], "links": [], "ids": []})
            info["files"] = sorted(set(info["files"]) | files_of.get(s, set()))
            info["links"] = sorted(set(info["links"]) | links.get(s, set()))
            info["ids"] = sorted(set(info["ids"]) | ids.get(s, set()))
    sessions = state["sessions"]
    base = group_clients({s: set(i["ids"]) for s, i in sessions.items()})
    by_client: Dict[str, Set[str]] = {}
    for s, i in sessions.items():
        by_client.setdefault(base.get(s, s), set()).update(i["links"])
    resolved = resolve(by_client)
    link_of = {l: resolved.get(l, l) for i in sessions.values() for l in i["links"]}
    old_link_of = state["link_of"]
    relinked = {s for s in processed_before - touched
                if any(link_of.get(l, l) != old_link_of.get(l, l) for l in sessions[s]["links"])}

    rebuilt_sessions = touched | relinked
    if rebuilt_sessions:
        sources = _session_sources(state, reader, rebuilt_sessions)
        got = {x[1] for x in sources}
        weights = _weights(state, sources, rebuilt_sessions, link_of)
        built: Dict[str, Dict[str, Any]] = {}
        for (session, page), lm in link_map.build_maps(sources=sources, link_of=link_of, weights=weights).items():
            built.setdefault(session, {})[page] = lm
        for s in rebuilt_sessions & got:                      # a file that vanished keeps the old maps
            state["session_maps"][s] = built.get(s, {})
    ids_all = {s: set(i["ids"]) for s, i in sessions.items()}
    maps_all = {(s, p): lm for s, maps in state["session_maps"].items() for p, lm in maps.items()}
    owners = resolve_owners(maps_all, ids_all)
    old_owners = state["owners"]
    owner_changed = {s for s in processed_before - touched if owners.get(s) != old_owners.get(s)}

    # memories that cannot take their votes back are dropped, whole (link, browser) groups
    changed = relinked | owner_changed
    bad_links = {old_link_of.get(l, l) for s in relinked for l in sessions[s]["links"]}
    bad_clients = {old_owners[s] for s in changed if s in old_owners}
    dropped = {(pm.link, pm.browser) for pm in state["memories"]
               if pm.link in bad_links or bad_clients & set(pm.clients)}
    live_owners = {o for o in owners.values() if not _undecided(o)}
    dirty = set(state["todo"]) | {owners[s] for s in rebuilt_sessions | owner_changed if not _undecided(owners[s])}
    if dropped:
        gone = [pm for pm in state["memories"] if (pm.link, pm.browser) in dropped]
        summary["rebuilt"] = [pm.page for pm in gone]
        log.info("SDIS mine: rebuilding %d memories (owner or link of added sessions changed)", len(gone))
        state["memories"] = [pm for pm in state["memories"] if (pm.link, pm.browser) not in dropped]
        dirty |= {c for pm in gone for c in pm.clients} & live_owners
    dirty = {c for c in dirty if c in live_owners}

    # 3. the session-level facts are saved first; `todo` says what the memories still lack
    take_in_files()
    state["link_of"], state["owners"], state["todo"] = link_of, owners, sorted(dirty)
    store.save(state, state_path)

    # 4. the dirty clients' maps, one at a time into the memories
    mems: List[PageMemory] = state["memories"]
    dirty_sessions = {s for s, o in owners.items() if o in dirty}
    sources = _session_sources(state, reader, dirty_sessions)
    weights = _weights(state, sources, dirty_sessions, link_of)
    cmaps = link_map.build_maps(client_of_session=owners, sources=sources, link_of=link_of, weights=weights)
    units = sorted(cmaps.values(), key=lambda m: m.reads[0] if m.reads else "")
    left: Dict[str, int] = {}
    for lm in units:
        left[lm.client] = left.get(lm.client, 0) + 1
    state["todo"] = [c for c in state["todo"] if c in left]
    used: Dict[str, Set[int]] = {}
    for done, lm in enumerate(units, 1):
        if cancel is not None and cancel.is_set():
            summary["cancelled"] = True
            break
        group = [pm for pm in mems if pm.link == lm.link and pm.browser == lm.browser]
        taken = {i for i, pm in enumerate(group) if id(pm) in used.get(lm.client, ())}
        n_before = len(group)
        pm, _idx = place_map(group, lm, lm.link, lm.browser, weights, taken, state["rejected_triples"])
        if len(group) > n_before:
            mems.append(pm)
        used.setdefault(lm.client, set()).add(id(pm))
        left[lm.client] -= 1
        if not left[lm.client]:
            state["todo"].remove(lm.client)
        summary["clients"] += 1
        store.save(state, state_path)
        if progress is not None:
            progress(done, len(units), pm.page)

    # 5. relevance
    from core.sdis.classes import annotate
    from core.sdis.relevance import datapoints
    dps = datapoints(mems, state["rejected"], state["picked"])     # node ids index into state["memories"]
    annotate(dps, mems)
    state["datapoints"] = store.datapoint_dicts(dps)
    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    store.save(state, state_path)
    return finish()
