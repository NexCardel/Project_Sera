"""
core/sdis/identity.py - who the client of one map is, and data fingerprinting
=============================================================================
Moved from the pre-dev compare.py, with group_clients and _by_time moved from link_map.py.
Part D: identity resolution by SGT session id + data fingerprinting (W1-3).
"""

import re
from datetime import date
from typing import Any, Dict, List, Optional, Set, Tuple

from core.sdis import labels
from core.sdis.align import align, pair_moved, shape

CLIENT_FIELDS = ("pan", "gstin")
_registry = None

MIN_VALUES = 3
SAME = 0.9
DIFF = 0.5


def client_ids(m: Any) -> set:
    """Who the client of one map is: the PAN / GSTIN that SGT-C's own specs (sgt_fields.json,
    checksums included) find in the text SGT sees there, as {"gstin:...", "pan:..."}. Empty when
    the page never shows them, or the link is no portal."""
    from core.sgt.sgt_resolver import resolve_page          # importing core.vsdc loads Qt: only when needed
    from core.sgt.sgt_specs import load_registry
    from core.vsdc.vsdc_scope import portal_for_url
    global _registry
    page = getattr(m, "link", "") or getattr(m, "page", "")     # the link, without " [browser] [screen n]"
    url = "https://" + page
    portal = portal_for_url(url)
    if not portal:
        return set()
    if _registry is None:
        _registry = load_registry()
    flat = m.to_flat() if hasattr(m, "to_flat") else m
    marked = any("sgt" in e.get("node", {}) for e in flat)      # key_probe marks it; the recorder's raw reads do not
    lines = [e["text"] for e in flat if e["text"] and (e.get("node", {}).get("sgt") or not marked)]
    res = resolve_page(_registry, lines, portal, url, date.today())
    return {f"{k}:{h.value}" for k, h in res.profile.items() if k in CLIENT_FIELDS}


def masked(ids: set) -> str:
    """Client ids for the console: first and last 2 characters only."""
    return ", ".join(sorted(f"{i.split(':', 1)[0]} {v[:2]}..{v[-2:]}" for i in ids for v in [i.split(":", 1)[1]])) or "unknown"


def _by_time(session: str) -> str:
    """A session name ends in its time ("capture <time>", "read <time>")."""
    return session.split()[-1]


def group_clients(session_ids: Dict[str, set]) -> Dict[str, str]:
    """{session: client name}. Sessions that share any client id (a PAN or GSTIN, on any page
    they visited) are the same client, chained: A shares a GSTIN with B, B a PAN with C -> one
    client, named "client <n>" in order of its first session. A session with NO id stays its own
    client, "unidentified <session>": it cannot be told apart from anyone (P18, Q10 open)."""
    parent = {s: s for s in session_ids}

    def root(s: str) -> str:
        while parent[s] != s:
            parent[s] = parent[parent[s]]
            s = parent[s]
        return s

    owner_of_id: Dict[str, str] = {}
    for s in sorted(session_ids, key=_by_time):
        for cid in session_ids[s]:
            if cid in owner_of_id:
                parent[root(s)] = root(owner_of_id[cid])
            else:
                owner_of_id[cid] = s
    names: Dict[str, str] = {}
    out: Dict[str, str] = {}
    for s in sorted(session_ids, key=_by_time):
        if not session_ids[s]:
            out[s] = f"unidentified {s}"
            continue
        names.setdefault(root(s), f"client {len(names) + 1}")
        out[s] = names[root(s)]
    return out


def _always(e: Dict[str, Any]) -> bool:
    return True


def fingerprint(map_a: Any, map_b: Any,
                rarity: Optional[Dict[Tuple[str, Any, str], int]] = None) -> Tuple[float, float, int]:
    """Pair the two maps' to_flat() with align+pair_moved (as memory._match does);
    keep only pairs where both texts are non-empty and labels.element_type(e) is NOT in
    labels.FIXABLE_TYPES and is not 'control' (data-shaped values only);
    weight of a value = 1 / rarity[(link, shape, text)], rarity = number of sessions whose
    map of that link shows that (shape, text);
    an equal pair adds its weight to agree and total - counting the two compared sessions as ONE
    capture (rarity - 1): both show it because they are compared, so a value only they share
    weighs 1, like an unequal value only one of them shows (else agreement is always halved);
    an unequal pair adds the mean of both weights to total.
    Returns (agree_weight, total_weight, n_values)."""
    if rarity is None:
        rarity = {}
    flat_a = map_a.to_flat() if hasattr(map_a, "to_flat") else map_a
    flat_b = map_b.to_flat() if hasattr(map_b, "to_flat") else map_b

    pairs = pair_moved(flat_a, flat_b, align(flat_a, flat_b, _always), _always) if flat_a and flat_b else {}
    link = getattr(map_a, "link", getattr(map_a, "page", getattr(map_b, "link", getattr(map_b, "page", ""))))

    agree_weight = 0.0
    total_weight = 0.0
    n_values = 0

    def _is_data(e: Dict[str, Any]) -> bool:
        t = labels.element_type(e)
        return t not in labels.FIXABLE_TYPES and t != "control"

    for i, j in pairs.items():
        ea = flat_a[i]
        eb = flat_b[j]
        ta = ea.get("text") or ""
        tb = eb.get("text") or ""
        if not ta or not tb:
            continue
        if not (_is_data(ea) and _is_data(eb)):
            continue

        n_values += 1
        shape_a = shape(ea)
        shape_b = shape(eb)
        wa = 1.0 / max(1, rarity.get((link, shape_a, ta), 1))
        wb = 1.0 / max(1, rarity.get((link, shape_b, tb), 1))

        if ta == tb:
            wa = 1.0 / max(1, rarity.get((link, shape_a, ta), 2) - 1)
            agree_weight += wa
            total_weight += wa
        else:
            total_weight += (wa + wb) / 2.0

    return agree_weight, total_weight, n_values


def decide(sim: float, n: int) -> str:
    """n >= MIN_VALUES (3) and sim >= SAME (0.9) -> 'same';
    n >= 3 and sim <= DIFF (0.5) -> 'different';
    else 'undecided'."""
    if n >= MIN_VALUES and sim >= SAME:
        return "same"
    if n >= MIN_VALUES and sim <= DIFF:
        return "different"
    return "undecided"


def resolve_owners(session_maps: Dict[Tuple[str, str], Any],
                   session_ids: Dict[str, set]) -> Dict[str, str]:
    """Resolve owner per session:
    start from group_clients(session_ids);
    for every session with NO ids, compare it with every other session sharing a page link
    (sum agree/total/n over the shared links):
      any 'same' -> take that session's owner (chain like group_clients, union-find);
      'different' from every session it shares a link with -> its own client 'client <n>' (next free number);
      otherwise owner 'undecided <session>'.
    Sessions with ids keep their group_clients owner."""
    all_sessions = sorted(set(session_ids.keys()) | {s for s, _ in session_maps.keys()}, key=_by_time)
    full_session_ids = {s: session_ids.get(s, set()) for s in all_sessions}
    base_owners = group_clients(full_session_ids)

    # Compute rarity: number of sessions whose map of that link shows that (shape, text)
    session_seen: Dict[Tuple[str, Any, str], Set[str]] = {}
    for (session, page_key), lm in session_maps.items():
        link = getattr(lm, "link", page_key)
        flat = lm.to_flat() if hasattr(lm, "to_flat") else lm
        for e in flat:
            txt = e.get("text")
            if txt:
                session_seen.setdefault((link, shape(e), txt), set()).add(session)
    rarity = {k: len(sessions) for k, sessions in session_seen.items()}

    # A page is (link, browser): screens of one link are numbered per session, so "screen 2" of
    # one session need not be "screen 2" of another.
    pages_by_session: Dict[str, Dict[Tuple[str, str], List[Any]]] = {}
    for (session, page_key), lm in session_maps.items():
        page = (getattr(lm, "link", page_key), getattr(lm, "browser", ""))
        pages_by_session.setdefault(session, {}).setdefault(page, []).append(lm)

    def compare_pair(sa: str, sb: str) -> Tuple[str, float, int]:
        shared = sorted(set(pages_by_session.get(sa, {}).keys()) & set(pages_by_session.get(sb, {}).keys()))
        if not shared:
            return "undecided", 0.0, 0
        sum_agr = sum_tot = 0.0
        sum_n = 0
        for pk in shared:
            ma, mb = pages_by_session[sa][pk], pages_by_session[sb][pk]
            if len(ma) != 1 or len(mb) != 1:
                continue                    # which screen pairs with which is not known: no evidence
            agr, tot, n = fingerprint(ma[0], mb[0], rarity)
            sum_agr += agr
            sum_tot += tot
            sum_n += n
        sim = sum_agr / sum_tot if sum_tot > 0 else 0.0
        return decide(sim, sum_n), sim, sum_n

    parent: Dict[str, str] = {s: s for s in all_sessions}

    def root(s: str) -> str:
        while parent[s] != s:
            parent[s] = parent[parent[s]]
            s = parent[s]
        return s

    def union(a: str, b: str) -> None:
        ra, rb = root(a), root(b)
        if ra == rb:
            return
        a_has_id = any(full_session_ids[s] for s in all_sessions if root(s) == ra)
        b_has_id = any(full_session_ids[s] for s in all_sessions if root(s) == rb)
        if a_has_id and not b_has_id:
            parent[rb] = ra
        elif b_has_id and not a_has_id:
            parent[ra] = rb
        else:
            if _by_time(ra) <= _by_time(rb):
                parent[rb] = ra
            else:
                parent[ra] = rb

    # First, chain sessions with IDs via group_clients union-find
    owner_of_id: Dict[str, str] = {}
    for s in sorted(all_sessions, key=_by_time):
        for cid in full_session_ids[s]:
            if cid in owner_of_id:
                union(s, owner_of_id[cid])
            else:
                owner_of_id[cid] = s

    # Compare sessions with NO ids against other sessions sharing a link
    has_same: Dict[str, bool] = {s: False for s in all_sessions}
    all_different: Dict[str, bool] = {s: False for s in all_sessions}

    for s in all_sessions:
        if full_session_ids[s]:
            continue
        shared_others = [other for other in all_sessions
                         if other != s and bool(set(pages_by_session.get(s, {}).keys()) &
                                                set(pages_by_session.get(other, {}).keys()))]
        if not shared_others:
            continue
        verdicts = [compare_pair(s, other)[0] for other in shared_others]
        if any(v == "same" for v in verdicts):
            has_same[s] = True
            for other, v in zip(shared_others, verdicts):
                if v == "same":
                    union(s, other)
        elif all(v == "different" for v in verdicts):
            all_different[s] = True

    # Assign names
    used_client_nums = {
        int(m.group(1))
        for v in base_owners.values()
        for m in [re.match(r"^client (\d+)$", v)]
        if m
    }
    next_client_num = max(used_client_nums, default=0) + 1

    names: Dict[str, str] = {}
    for s in sorted(all_sessions, key=_by_time):
        if full_session_ids[s]:
            names.setdefault(root(s), base_owners[s])

    out: Dict[str, str] = {}
    for s in sorted(all_sessions, key=_by_time):
        if full_session_ids[s]:
            out[s] = base_owners[s]
        elif has_same[s] or any(has_same[other] for other in all_sessions if root(other) == root(s)):
            r = root(s)
            if r not in names:
                names[r] = f"client {next_client_num}"
                next_client_num += 1
            out[s] = names[r]
        elif all_different[s]:
            names[s] = f"client {next_client_num}"
            next_client_num += 1
            out[s] = names[s]
        else:
            out[s] = f"undecided {s}"

    return out
