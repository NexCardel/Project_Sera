"""
core/sdis/register.py - Part Q: a datapoint the user picked becomes an SGT field spec on every PC
=================================================================================================
draft_spec() drafts the spec the way the SGT-I miner drafts a mined one (core/sgt_i/miner.py
draft_field): SDIS supplies the label, the value type and the values' shapes. The row goes into
the synced `sdis_fields` table (sera_db/sdis.py); every PC then writes the active rows with
write_fields_file() to <Sera data>/sdis_fields.json, which core/sgt/sgt_specs.SpecStore loads
after the built-in file and the local override.

A Profile builder field is a `profile` spec (it latches, like SGT's own profile). A dataset or
Others field must not latch: it is a `current_dataset` spec, and SGT hands its hits to the
containers (core/sgt/sgt_containers.py, by the "sdis." name), never to its built-in dataset
(W4-7). The user's label goes into the
spec's `note`, never its `labels` (the page labels it is found by), so renaming changes nothing
that is captured. No values are written anywhere: the shapes are letters -> A, digits -> 9.

Part U: register_field() makes every registered datapoint a datapoint of an sdis_mcl field: the
same field picked on two pages is two specs (sdis.<field>, sdis.<field>.2) of one field.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

PROFILE = "profile"
CURRENT = "current_dataset"                    # the sgt_fields.json section of dataset / Others fields
# container kind (or sdis_mcl class) -> (spec section, sdis_mcl class)
SECTIONS = {"profile": (PROFILE, "profile"), "dataset": (CURRENT, "dataset"),
            "others": (CURRENT, "info"), "info": (CURRENT, "info")}
NAME_PREFIX = "sdis."                          # spec names never land on a built-in spec's name

# SDIS value type -> the SGT-I miner's type. label / sentence / control have no drafted pattern:
# such a datapoint is not registered (never a guessed pattern). A period gets the shape pattern
# of the values seen (a container's period field must be capturable).
MINER_TYPES = {"text": "text", "number": "number", "amount": "amount", "date": "date", "code": "code",
               "alphanumeric": "code", "email": "email", "phone": "phone", "percentage": "percentage",
               "yes/no": "yes/no", "period": "code"}


MIXED_SHAPES = "mixed shapes"                  # the miner's Drop reason that "register anyway" overrides


class NotRegistrable(ValueError):
    """The datapoint cannot become a spec; the reason holds no page text or value."""


def _get(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def shape_of(text: str) -> str:
    from core.sgt_i.pairs import mask_shape
    return mask_shape(" ".join(str(text).split()))


def value_shapes(datapoint: Any, memories: Sequence[Any]) -> Dict[str, int]:
    """{shape: count} over the datapoint's nodes: each client's distinct values once, sure
    pairings only (an unsure look-alike has no vote, Part E)."""
    from core.sdis.memory import voters
    seen = set()
    shapes: Dict[str, int] = {}
    for mi, nid in _get(datapoint, "nodes") or ():
        nodes = _get(memories[mi], "nodes") or {}
        nd = nodes.get(nid) if isinstance(nodes, dict) else nodes[nid] if 0 <= nid < len(nodes) else None
        if not nd:
            continue
        for client, c in voters(nd).get("clients", {}).items():
            for t in c.get("texts") or ():
                if t and (client, t) not in seen:
                    seen.add((client, t))
                    s = shape_of(t)
                    shapes[s] = shapes.get(s, 0) + 1
    return shapes


def clean_label(label: str) -> str:
    return " ".join(str(label or "").split()).rstrip(":").strip()


def _taken_fields(base_paths: Sequence[Path]) -> set:
    from core.sgt import sgt_specs
    reg = sgt_specs.load_registry(list(base_paths))
    return {s.field for s in reg.profile} | {s.field for s in reg.current} | {s.name for s in reg.records}


def field_name(label: str, taken: Iterable[str]) -> str:
    """A lower_case field name from the label that never lands on an existing field (it would
    merge into that field's captures)."""
    from core.sgt_i.miner import _slug
    taken = set(taken)
    base = _slug(label)[:36]
    name, n = base, 2
    while name in taken:
        name, n = "%s_%d" % (base, n), n + 1
    return name


def draft_spec(datapoint: Any, memories: Sequence[Any], portal: str, container: str = PROFILE,
               field: Optional[str] = None, base_paths: Optional[Sequence[Path]] = None,
               loose: bool = False) -> Dict[str, Any]:
    """The `sdis_fields` row for a picked datapoint: {name, field, portal, section, spec, label}.
    Raises NotRegistrable (a counts-only reason) when it cannot be drafted or the SGT loader
    refuses the drafted spec beside the existing ones. `loose` is the user's override for values
    with no common shape ("mixed shapes"): the spec then takes any one-line value beside the label."""
    from core.sgt import sgt_specs
    from core.sgt_i import miner

    if container not in SECTIONS:
        raise NotRegistrable("unknown container kind")
    section = SECTIONS[container][0]
    if not portal:
        raise NotRegistrable("no portal")
    label = clean_label(_get(datapoint, "label") or "")
    if not label:
        raise NotRegistrable("the datapoint has no label")
    typ = MINER_TYPES.get(_get(datapoint, "value_type") or "")
    if typ is None:
        raise NotRegistrable("value type has no drafted pattern")
    shapes = value_shapes(datapoint, memories)
    base = list(base_paths) if base_paths is not None else sgt_specs.default_paths()
    field = field or field_name(label, _taken_fields(base))
    try:
        spec = miner.draft_field(label, typ, shapes, field, section, portal)
    except miner.Drop as e:
        if not (loose and str(e) == MIXED_SHAPES):
            raise NotRegistrable(str(e))
        try:
            spec = miner.draft_field(label, "any", shapes, field, section, portal)
        except miner.Drop as e2:
            raise NotRegistrable(str(e2))
    spec["name"] = NAME_PREFIX + field
    spec["note"] = label
    err = miner.check_spec(section, spec, base)
    if err:
        raise NotRegistrable("refused by the SGT loader")
    return {"name": spec["name"], "field": field, "portal": portal, "section": section,
            "spec": spec, "label": label}


def fields_document(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """The active rows as an sgt_fields.json document (Profile builder fields under `profile`,
    dataset / Others fields under `current_dataset.fields`), the user's label in `note`."""
    profile: List[Dict[str, Any]] = []
    current: List[Dict[str, Any]] = []
    for r in rows:
        if r.get("status", "active") != "active" or r.get("section") not in (PROFILE, CURRENT):
            continue
        spec = r.get("spec")
        if spec is None:
            try:
                spec = json.loads(r.get("spec_json") or "{}")
            except ValueError:
                continue
        if not isinstance(spec, dict) or not spec:
            continue
        spec = dict(spec, name=r.get("name") or spec.get("name"))
        if r.get("label"):
            spec["note"] = r["label"]
        (profile if r.get("section") == PROFILE else current).append(spec)
    doc: Dict[str, Any] = {}
    if profile:
        doc["profile"] = profile
    if current:
        doc["current_dataset"] = {"fields": current}
    return doc


def fields_path() -> Path:
    from core.sgt.sgt_specs import sdis_fields_path
    return sdis_fields_path()


def write_fields_file(rows: Iterable[Dict[str, Any]], path: Optional[Path] = None) -> Optional[Path]:
    """Writes the active rows atomically; with none, removes the file (SGT then loads exactly
    what it loaded before Part Q). Returns the path written, or None."""
    path = Path(path) if path is not None else fields_path()
    doc = fields_document(rows)
    if not doc:
        path.unlink(missing_ok=True)
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return path


def spec_name_for(field: str, taken_names: Iterable[str]) -> str:
    """sdis.<field> for the field's first spec, sdis.<field>.2, .3 ... for more pages of it."""
    taken = set(taken_names)
    name, n = NAME_PREFIX + field, 2
    while name in taken:
        name, n = "%s%s.%d" % (NAME_PREFIX, field, n), n + 1
    return name


def register_field(db: Any, datapoint: Any, memories: Sequence[Any], portal: str,
                   field: Optional[str] = None, cls: str = PROFILE, created_by: str = "",
                   base_paths: Optional[Sequence[Path]] = None, loose: bool = False) -> Dict[str, Any]:
    """Part U: a picked datapoint becomes a datapoint of a field. `field` names an existing
    sdis_mcl field (PAN picked again on another page); None makes a new field from the label.
    The spec goes into sdis_fields linked to the field (mcl_gid), named sdis.<field>[.n]. Which
    container the field is in is a containers-file edit (core/sdis/config.py), never a spec
    change. Returns {mcl_gid, field_gid, field, name}; raises NotRegistrable."""
    from core.sgt import sgt_specs

    library = {r["name"]: r for r in db.list_sdis_mcl()}
    specs = db.list_sdis_fields()
    base = list(base_paths) if base_paths is not None else sgt_specs.default_paths()
    if field is not None:
        if field not in library:
            raise NotRegistrable("no such field in the library")
        name_for = field
    else:
        label = clean_label(_get(datapoint, "label") or "")
        if not label:
            raise NotRegistrable("the datapoint has no label")
        name_for = field_name(label, _taken_fields(base) | set(library))
    # the same page again re-registers its spec; another page of the field gets a new spec
    row = draft_spec(datapoint, memories, portal, cls, field=name_for, base_paths=base, loose=loose)
    mcl = library.get(name_for)
    same = [s for s in specs if mcl and s.get("mcl_gid") == mcl["gid"] and s.get("portal") == portal
            and (s.get("spec") or {}).get("labels") == row["spec"].get("labels")]
    name = same[0]["name"] if same else spec_name_for(name_for, (s["name"] for s in specs))
    if name != row["name"]:
        row["spec"]["name"] = name
        if miner_check(row["spec"], base, row["section"]):
            raise NotRegistrable("refused by the SGT loader")
    mcl_class = SECTIONS[cls][1]
    mcl_gid = db.add_sdis_mcl(name_for, row["label"], _get(datapoint, "value_type") or "", mcl_class, portal, created_by)
    label = (library.get(name_for) or {}).get("label") or row["label"]
    field_gid = db.add_sdis_field(name, portal, row["section"], row["spec"], label, created_by, mcl_gid)
    return {"mcl_gid": mcl_gid, "field_gid": field_gid, "field": name_for, "name": name}


def miner_check(spec: Dict[str, Any], base: Sequence[Path], section: str = PROFILE) -> Optional[str]:
    from core.sgt_i import miner
    return miner.check_spec(section, spec, base)


def refresh(db: Any) -> Optional[Path]:
    """Start-up and after sync applied remote rows: the table -> the file."""
    return write_fields_file(db.list_sdis_fields(active_only=True))
