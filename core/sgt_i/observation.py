"""
core/sgt_i/observation.py - the read-only copy of one page that SGT-I works on
==============================================================================
Blueprint 14.2 rule 1: SGT-I gets a COPY of what the Core read and captured, never the Core's
own objects. An Observation is built on the Core's thread, the moment the Core has resolved a
page, and is frozen all the way down (mappings become read-only views, lists become tuples),
so nothing SGT-I does with it can reach back into a session, slot or row.

It lives in memory only: the page text in it is never written anywhere by the host.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Tuple


def freeze(value: Any) -> Any:
    """A deep, read-only copy: dict -> read-only mapping, list / tuple / set -> tuple."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(freeze(v) for v in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


@dataclass(frozen=True)
class Observation:
    """What the Core read and captured for one page, as the Core saw it."""
    session_id: str
    portal: str
    url: str
    title: str
    source: str                      # "uia" or "ocr"; "uia_event" = a message that flashed (step 9)
    lines: Tuple[str, ...]
    result: Mapping[str, Any]        # PageResult.as_dict(): profile, datasets, current, is_list, conflicts
    profile: Mapping[str, str]       # the session's client profile after this page
    draft: Mapping[str, str]         # the dataset being worked on, after this page
    ts: float                        # the Core's clock when the page was read
    today: str                       # ISO date the Core used
    event: str = ""                  # for source "uia_event": live_region / notification / window_opened
    nodes: Tuple[Any, ...] = ()      # the page's UIA node tree (uia_nodes "docs"), only when the Core
                                     # had already read it (page recording on); else () - never an extra read


def make_observation(*, session_id: str, portal: str, url: str, title: str, source: str,
                     lines: Any, result: Any, profile: Any, draft: Any, ts: float,
                     today: Any, nodes: Any = None) -> Observation:
    as_dict = result.as_dict() if hasattr(result, "as_dict") else (result or {})
    return Observation(session_id=str(session_id), portal=portal or "", url=url or "", title=title or "",
                       source=source or "", lines=tuple(str(x) for x in (lines or ())),
                       result=freeze(as_dict), profile=freeze(profile or {}), draft=freeze(draft or {}),
                       ts=float(ts or 0.0), today=str(today or ""), nodes=freeze(nodes or ()))
