"""
core/sgt/sgt_containers.py - SDIS dataset containers inside SGT (blueprint S.2, S.3, S.4)
=========================================================================================
Pure: no UIA, no database. The containers document comes from core.sdis.config (a refused file
keeps the last good one; no file -> no containers). SGT hands this module the values its SDIS
field specs (spec names "sdis.<field>") found on a page; it gets back the container instances
that changed.

  instance  one per (client, form, period) (D17): the form is the container's name unless the
            container marks a form_field; the period is its period_field. One SGT session is one
            client, so a session keeps (form, period) -> instance. A value shown once on a page
            belongs to the instance being worked on; a new period starts (or returns to) another
            instance; values seen before any period is known wait in the session (D22: one
            instance per page and key - list pages never feed it).
  level     core.sdis.config.level_for over the captured fields - the names, thresholds and the
            ladder mapping are the document's (levels, level_map), never code's. It only promotes,
            and the status written to the tracker only promotes too. No level -> 'k of n' and
            SGT's minimum for a keyed dataset (Draft).
  others    the portal's Others fields: the latest value wins; history is kept by the tracker's
            SRPF container (S.4, sera_db/srpf.py).
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .sgt_toolbox import SUBMIT_LEVELS, is_submit_level, submit_level

SPEC_PREFIX = "sdis."                   # core/sdis/register.py names every SDIS spec so
KEYED_MINIMUM = SUBMIT_LEVELS[1]        # a keyed dataset is at least a Draft (S.2)


def load_doc() -> Dict[str, Any]:
    """The containers document in force; never raises (no document = no containers)."""
    try:
        from core.sdis import config
        return config.current()
    except Exception:
        return {}


def _portal_entry(mapping: Dict[str, Any], portal: str) -> Any:
    if portal in mapping:
        return mapping[portal]
    low = (portal or "").strip().lower()
    return next((v for k, v in mapping.items() if str(k).strip().lower() == low), None)


def containers_for(doc: Dict[str, Any], portal: str) -> List[Dict[str, Any]]:
    low = (portal or "").strip().lower()
    return [c for c in doc.get("containers") or () if str(c.get("portal") or "").strip().lower() == low]


def container_named(doc: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    return next((c for c in doc.get("containers") or () if c.get("name") == name), None)


def others_fields(doc: Dict[str, Any], portal: str) -> List[str]:
    return list(_portal_entry(doc.get("others") or {}, portal) or ())


def profile_fields(doc: Dict[str, Any], portal: str) -> List[str]:
    return list(_portal_entry(doc.get("profile") or {}, portal) or ())


def form_of(container: Dict[str, Any], values: Dict[str, str]) -> str:
    ff = container.get("form_field")
    return (values.get(ff) or "") if ff else str(container.get("name") or "")


@dataclass
class Instance:
    container: str
    form: str = ""
    period: str = ""
    values: Dict[str, str] = field(default_factory=dict)
    evidence: Dict[str, str] = field(default_factory=dict)     # field -> the page link it came from
    level: Optional[str] = None
    status: str = ""                    # the ladder status last given to the tracker (only promotes)
    k: int = 0
    n: int = 0
    proves: List[str] = field(default_factory=list)            # proves fields captured
    sent_key: Optional[str] = None      # the tracker dataset_key this instance was last written under
    needs_period: bool = True

    @property
    def captured(self) -> List[str]:
        return [f for f, v in self.values.items() if v]

    @property
    def keyed(self) -> bool:
        """Written to the tracker only once its form (and period, if it has one) is known."""
        return bool(self.form) and (bool(self.period) or not self.needs_period)

    def to_json(self) -> Dict[str, Any]:
        return {"container": self.container, "form": self.form, "period": self.period,
                "values": dict(self.values), "evidence": dict(self.evidence), "level": self.level,
                "status": self.status, "k": self.k, "n": self.n, "proves": list(self.proves),
                "sent_key": self.sent_key, "needs_period": self.needs_period}

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "Instance":
        return cls(container=str(raw.get("container") or ""), form=str(raw.get("form") or ""),
                   period=str(raw.get("period") or ""), values=dict(raw.get("values") or {}),
                   evidence=dict(raw.get("evidence") or {}), level=raw.get("level"),
                   status=str(raw.get("status") or ""), k=int(raw.get("k") or 0), n=int(raw.get("n") or 0),
                   proves=list(raw.get("proves") or ()), sent_key=raw.get("sent_key"),
                   needs_period=bool(raw.get("needs_period", True)))


@dataclass
class Carrier:
    """The one tracker row of a session that has Others / Profile builder values but no dataset row."""
    sent_key: Optional[str] = None


def evaluate(instance: Instance, container: Dict[str, Any], doc: Dict[str, Any]) -> bool:
    """Re-evaluates the level, 'k of n' and status from the document; nothing moves down.
    True when the level or status changed."""
    from core.sdis import config
    captured = instance.captured
    instance.k, instance.n = config.k_of_n(container, captured)
    proves = (container.get("exceptions") or {}).get("proves") or {}
    instance.proves = sorted(f for f in proves if f in captured)
    names = [lv.get("name") for lv in config.levels_of(container, doc)]
    new = config.level_for(container, captured, doc)
    level = instance.level
    if new is not None and not (level in names and names.index(level) >= names.index(new)):
        level = new
    mapped = config.level_map_of(container, doc).get(level) if level else None
    status = mapped if is_submit_level(mapped) else KEYED_MINIMUM
    if submit_level(instance.status) > submit_level(status):
        status = instance.status
    changed = (level, status) != (instance.level, instance.status)
    instance.level, instance.status = level, status
    return changed


def apply(instance: Instance, fld: str, value: str, container: Dict[str, Any],
          doc: Dict[str, Any], page: str = "") -> bool:
    """One captured value into its instance (the latest value wins). True when anything changed."""
    changed = instance.values.get(fld) != value
    if changed:
        instance.values[fld] = value
        if page:
            instance.evidence[fld] = page
    instance.form = form_of(container, instance.values) or instance.form
    return evaluate(instance, container, doc) or changed


def evidence_of(instance: Instance) -> Dict[str, Any]:
    return {"k_of_n": f"{instance.k} of {instance.n}", "level": instance.level,
            "proves": list(instance.proves)}


class ContainerSession:
    """One SGT session's container instances, values waiting for a period, and Others values."""

    def __init__(self) -> None:
        self.instances: List[Instance] = []
        self.current: Dict[str, Instance] = {}          # container name -> the instance being worked on
        self.pending: Dict[str, Dict[str, str]] = {}    # container name -> values seen before the period
        self.others: Dict[str, str] = {}
        self.at: str = ""                               # when an Others value last changed
        self.carried: str = ""                          # the 'sdis' info a dispatched row last carried
        self.carrier: Optional[Carrier] = None

    @property
    def has_content(self) -> bool:
        return bool(self.instances or self.pending or self.others)

    def feed(self, doc: Dict[str, Any], portal: str, values: Dict[str, str], page: str = "") -> List[Instance]:
        """The SDIS values one page showed. Returns the instances that changed."""
        out: List[Instance] = []
        for c in containers_for(doc, portal):
            name = c.get("name")
            seen = {f: v for f, v in values.items() if f in (c.get("fields") or ()) and v}
            if not name or not seen:
                continue
            pf = c.get("period_field")
            cur = self.current.get(name)
            period = seen.get(pf) if pf else ""
            if (pf and period and (cur is None or cur.period != period)) or (not pf and cur is None):
                cur = next((i for i in self.instances if i.container == name and i.period == period), None)
                if cur is None:
                    cur = Instance(container=name, period=period, needs_period=bool(pf))
                    self.instances.append(cur)
                self.current[name] = cur
                seen = {**self.pending.pop(name, {}), **seen}
            if cur is None:
                self.pending.setdefault(name, {}).update(seen)
                continue
            changed = False
            for f, v in seen.items():
                changed = apply(cur, f, v, c, doc, page) or changed
            if changed and cur not in out:
                out.append(cur)
        return out

    def feed_others(self, doc: Dict[str, Any], portal: str, values: Dict[str, str], at: str) -> List[str]:
        """The portal's Others fields among the values; returns the fields whose value changed."""
        changed = [f for f in others_fields(doc, portal) if values.get(f) and self.others.get(f) != values[f]]
        for f in changed:
            self.others[f] = values[f]
        if changed:
            self.at = at
        return changed

    def recompute(self, doc: Dict[str, Any]) -> List[Instance]:
        """The document changed: every instance is re-evaluated; none moves down. An instance whose
        container is gone keeps what it had."""
        out = []
        for inst in self.instances:
            c = container_named(doc, inst.container)
            if c is None:
                continue
            inst.form = form_of(c, inst.values) or inst.form
            if evaluate(inst, c, doc):
                out.append(inst)
        return out

    def to_json(self) -> Dict[str, Any]:
        cur = {name: self.instances.index(i) for name, i in self.current.items() if i in self.instances}
        return {"instances": [i.to_json() for i in self.instances], "current": cur,
                "pending": {k: dict(v) for k, v in self.pending.items()}, "others": dict(self.others),
                "at": self.at, "carried": self.carried,
                "carrier": self.carrier.sent_key if self.carrier else None,
                "has_carrier": self.carrier is not None}

    @classmethod
    def from_json(cls, raw: Optional[Dict[str, Any]]) -> "ContainerSession":
        cs = cls()
        raw = raw or {}
        cs.instances = [Instance.from_json(i) for i in raw.get("instances") or ()]
        for name, idx in (raw.get("current") or {}).items():
            if isinstance(idx, int) and 0 <= idx < len(cs.instances):
                cs.current[name] = cs.instances[idx]
        cs.pending = {k: dict(v) for k, v in (raw.get("pending") or {}).items()}
        cs.others = dict(raw.get("others") or {})
        cs.at, cs.carried = str(raw.get("at") or ""), str(raw.get("carried") or "")
        if raw.get("has_carrier"):
            cs.carrier = Carrier(raw.get("carrier"))
        return cs


def split_hits(current: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(SGT's own current-dataset hits, SDIS field hits) - by spec name, so an SDIS field never
    becomes a piece of SGT's built-in dataset."""
    own, sdis = {}, {}
    for f, h in current.items():
        (sdis if str(getattr(h, "spec", "") or "").startswith(SPEC_PREFIX) else own)[f] = h
    return own, sdis


def sdis_info(cs: ContainerSession, doc: Dict[str, Any], portal: str,
              profile: Dict[str, str], now: str) -> Optional[Dict[str, Any]]:
    """The payload's 'sdis' key (S.4), or None when the session has no such values."""
    pp = {f: profile[f] for f in profile_fields(doc, portal) if profile.get(f)}
    if not (pp or cs.others):
        return None
    return {"portal": portal, "portal_profile": pp, "others": dict(cs.others), "at": cs.at or now}


def info_signature(info: Optional[Dict[str, Any]]) -> str:
    if not info:
        return ""
    import json
    return json.dumps([info.get("portal_profile"), info.get("others")], sort_keys=True, ensure_ascii=False)
