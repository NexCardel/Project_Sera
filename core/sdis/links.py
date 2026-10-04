"""
core/sdis/links.py - smart page link resolution
================================================
Blueprint Part N (problem 17): page links that carry client values in the path
(/returns/2026-27/... or #/auth/ARN123). Every client becomes its own link and nothing is
compared unless the value position is masked to '{v}'.

resolve(links_by_client: Dict[str, Set[str]]) -> Dict[str, str]
    mapping every raw link to its resolved link.

A link is 'host/path#route' as keys.page_link() makes it.
Split it into host + segments (path segments, then the route's segments after '#').
Group links by (host, number of segments, whether a route exists).
Inside a group, look at every PAIR of links that differ in exactly ONE segment position i:
if the two links belong to DIFFERENT clients (and not to a common client) that is one vote 'value at i';
if the same client has both, that is one vote 'page at i'.
Position i is masked in that group when value votes >= 2 and page votes == 0: every link's segment i becomes '{v}'.
Never mask the host, never mask a segment of letters only that the same client also visited with another value.
Links not in any masked group map to themselves.
Do NOT use core/sgt_i atlas.url_hint.
"""

import itertools
from typing import Dict, List, NamedTuple, Optional, Set, Tuple


class ParsedLink(NamedTuple):
    raw: str
    host: str
    path_segments: List[str]
    route_segments: List[str]
    segments: List[str]
    has_route: bool
    route_prefix: str
    is_path_absolute: bool


def parse_link(link: str) -> ParsedLink:
    """Split a link into host + segments (path segments, then the route's segments after '#')."""
    if not link:
        return ParsedLink(raw="", host="", path_segments=[], route_segments=[], segments=[],
                          has_route=False, route_prefix="", is_path_absolute=False)

    if "#" in link:
        base_part, route_part = link.split("#", 1)
        has_route = True
    else:
        base_part = link
        route_part = ""
        has_route = False

    route_prefix = "/" if route_part.startswith("/") else ""
    route_segments = [s for s in route_part.split("/") if s]

    is_path_absolute = base_part.startswith("/")
    if is_path_absolute:
        host = ""
        path_segments = [s for s in base_part.split("/") if s]
    elif "/" in base_part:
        host, path_rest = base_part.split("/", 1)
        path_segments = [s for s in path_rest.split("/") if s]
    else:
        host = base_part
        path_segments = []

    segments = path_segments + route_segments
    return ParsedLink(
        raw=link,
        host=host,
        path_segments=path_segments,
        route_segments=route_segments,
        segments=segments,
        has_route=has_route,
        route_prefix=route_prefix,
        is_path_absolute=is_path_absolute,
    )


def reconstruct_link(parsed: ParsedLink, new_segments: List[str]) -> str:
    """Rebuild link string preserving host, path/route segment counts and route prefix."""
    n_path = len(parsed.path_segments)
    new_path = new_segments[:n_path]
    new_route = new_segments[n_path:]

    if n_path > 0:
        if parsed.host:
            base_out = f"{parsed.host}/" + "/".join(new_path)
        else:
            base_out = "/" + "/".join(new_path)
    else:
        base_out = parsed.host

    if parsed.has_route:
        route_out = "#" + parsed.route_prefix + "/".join(new_route)
        return base_out + route_out
    return base_out


def is_letters_only(s: str) -> bool:
    """Return True if s consists only of alphabetic letters."""
    return bool(s and s.isalpha())


def resolve(links_by_client: Dict[str, Set[str]]) -> Dict[str, str]:
    """Map every link to its resolved link by discovering value positions across clients.

    Inside a group (host, number of segments, whether a route exists):
    - pairs differing in exactly one segment position i cast:
      - 1 vote 'value at i' if links belong to DIFFERENT clients (and not to a common client)
      - 1 vote 'page at i' if the same client has both
    - position i is masked to '{v}' if value votes >= 2 and page votes == 0
    - never mask the host, never mask a segment of letters only that the same client
      also visited with another value.
    """
    all_links: Set[str] = set()
    for links in links_by_client.values():
        all_links.update(links)

    if not all_links:
        return {}

    parsed_map: Dict[str, ParsedLink] = {link: parse_link(link) for link in all_links}
    resolved: Dict[str, str] = {link: link for link in all_links}

    # Group links by (host, number of segments, whether a route exists)
    groups: Dict[Tuple[str, int, bool], List[str]] = {}
    for link, p in parsed_map.items():
        if len(p.segments) == 0:
            continue
        key = (p.host, len(p.segments), p.has_route)
        groups.setdefault(key, []).append(link)

    for group_key, group_links in groups.items():
        if len(group_links) < 2:
            continue

        group_set = set(group_links)
        n_segs = group_key[1]
        value_votes = [0] * n_segs
        page_votes = [0] * n_segs

        # Look at every PAIR of links that differ in exactly ONE segment position i
        for link_a, link_b in itertools.combinations(group_links, 2):
            segs_a = parsed_map[link_a].segments
            segs_b = parsed_map[link_b].segments
            diffs = [i for i in range(n_segs) if segs_a[i] != segs_b[i]]
            if len(diffs) != 1:
                continue
            i = diffs[0]
            cls_a = {c for c, clinks in links_by_client.items() if link_a in clinks}
            cls_b = {c for c, clinks in links_by_client.items() if link_b in clinks}
            if cls_a & cls_b:
                page_votes[i] += 1
            else:
                value_votes[i] += 1

        # Determine masked positions
        masked_positions: Set[int] = set()
        for i in range(n_segs):
            if value_votes[i] >= 2 and page_votes[i] == 0:
                # Never mask a segment of letters only that the same client also visited with another value
                letters_only_same_client = False
                for _c, clinks in links_by_client.items():
                    c_group_links = [l for l in clinks if l in group_set]
                    c_vals = {parsed_map[l].segments[i] for l in c_group_links}
                    if len(c_vals) > 1 and any(is_letters_only(v) for v in c_vals):
                        letters_only_same_client = True
                        break
                if not letters_only_same_client:
                    masked_positions.add(i)

        if not masked_positions:
            continue

        for link in group_links:
            p = parsed_map[link]
            new_segs = list(p.segments)
            for i in masked_positions:
                seg_val = p.segments[i]
                if is_letters_only(seg_val):
                    # Check if any client who visited link also visited another value at i
                    clients = {c for c, clinks in links_by_client.items() if link in clinks}
                    has_other = False
                    for c in clients:
                        other_vals = {parsed_map[l].segments[i] for l in links_by_client[c] if l in group_set}
                        if len(other_vals) > 1:
                            has_other = True
                            break
                    if has_other:
                        continue
                new_segs[i] = "{v}"
            resolved[link] = reconstruct_link(p, new_segs)

    return resolved
