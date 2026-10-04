"""
core/sdis/screens.py - weighted matching to tell apart different screens on the same page link
=============================================================================================
Part G (problem 5): one page link can show truly different pages (a form, then a popup, then
a success message, or replaced content).

- Weight of a node: how rare its (shape, text) is across links:
      weight = log((1 + links) / links_showing_it)
  Site furniture (header, menu, footer) is on every link and weighs about 0; text found only on
  this link weighs the most. Nodes without text weigh 0. A key not in the dict (new text) gets
  log(1 + links).

- Covers:
      cover_new = matched weight / total weight on new side
      cover_old = matched weight / total weight on old side
  If a side's total weight < 1e-9 its cover is 1.0 (a loading shell or empty map never forces
  a new screen).

- Same screen:
      same_screen(cover_new, cover_old) = max(...) >= SCREEN_MIN (0.5)
"""

import math
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from core.sdis.align import shape
from core.sdis.keys import flatten

SCREEN_MIN: float = 0.5


class WeightDict(dict):
    """Mapping of (shape, text) -> weight with default weight for unseen keys."""

    def __init__(self, mapping: Optional[Dict[Tuple[str, str], float]] = None,
                 default: float = 0.0, links: int = 0) -> None:
        super().__init__(mapping or {})
        self.default = default
        self.links = links

    def __missing__(self, key: Tuple[str, str]) -> float:
        return self.default

    def get(self, key: Any, default: Any = None) -> Any:
        if key in self:
            return self[key]
        return self.default if default is None else default


def link_weights(sources: Optional[List[Tuple[str, str, str, Dict[str, Any]]]] = None) -> WeightDict:
    """{(shape, text): weight} where sources are link_map.all_sources() tuples;
    links = number of distinct page links in sources;
    weight = math.log((1 + links) / links_showing_it);
    a key not in the dict (new text) gets math.log(1 + links);
    nodes without text weigh 0.
    """
    if sources is None:
        from core.sdis import link_map
        sources = link_map.all_sources()

    distinct_links = {page for _, _, page, _ in sources}
    links = len(distinct_links)
    default_weight = math.log(1 + links) if links > 0 else 0.0

    showing: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    for _stamp, _session, page, rec in sources:
        flat = rec if isinstance(rec, list) else flatten(rec)
        for e in flat:
            t = e.get("text")
            if not t:
                continue
            sh = e.get("shape") if "shape" in e else shape(e)
            showing[(sh, t)].add(page)

    weights = WeightDict(default=default_weight, links=links)
    for pair, page_set in showing.items():
        links_showing_it = len(page_set)
        weights[pair] = math.log((1 + links) / links_showing_it)

    return weights


def _node_shape(e: Dict[str, Any]) -> str:
    if "shape" in e:
        return str(e["shape"])
    if "key" in e:
        return shape(e)
    return str(e.get("type", ""))


def _node_weight(e: Dict[str, Any], weights: Any, default_weight: Optional[float] = None) -> float:
    t = e.get("text")
    if not t:
        return 0.0
    sh = _node_shape(e)
    k = (sh, t)
    if isinstance(weights, dict):
        if k in weights:
            return float(weights[k])
        if default_weight is not None:
            return float(default_weight)
        if hasattr(weights, "default"):
            return float(weights.default)
        return 0.0
    if default_weight is not None:
        return float(default_weight)
    return 0.0


def covers(flat_new: List[Dict[str, Any]],
           flat_old: List[Dict[str, Any]],
           pairs: Dict[int, int],
           weights: Any,
           default_weight: Optional[float] = None) -> Tuple[float, float]:
    """where pairs is {index in new: index in old};
    cover = matched weight / total weight on that side;
    if a side's total weight < 1e-9 its cover is 1.0 (a loading shell or an empty map never forces a new screen).
    """
    total_new = sum(_node_weight(e, weights, default_weight) for e in flat_new)
    matched_new = sum(_node_weight(flat_new[i], weights, default_weight)
                      for i in pairs if 0 <= i < len(flat_new))
    cover_new = 1.0 if total_new < 1e-9 else matched_new / total_new

    total_old = sum(_node_weight(e, weights, default_weight) for e in flat_old)
    matched_old_indices = {j for j in pairs.values() if 0 <= j < len(flat_old)}
    matched_old = sum(_node_weight(flat_old[j], weights, default_weight)
                      for j in matched_old_indices)
    cover_old = 1.0 if total_old < 1e-9 else matched_old / total_old

    return (cover_new, cover_old)


def same_screen(cover_new: Any, cover_old: Optional[float] = None) -> bool:
    """max(...) >= SCREEN_MIN (0.5, a module constant)."""
    if cover_old is None and isinstance(cover_new, (tuple, list)):
        c_new, c_old = cover_new
    else:
        c_new, c_old = cover_new, cover_old
    return max(c_new, c_old) >= SCREEN_MIN
