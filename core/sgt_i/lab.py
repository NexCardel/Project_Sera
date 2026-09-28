"""
core/sgt_i/lab.py - the SGT lab: accept / reject logic for step 11's proposals
===============================================================================
Blueprint 14.4 step 11 / 14.10 W12-2. `core/sgt_i/miner.py` drafts proposals and writes
them to `proposals.json`; this module is what a **decision** does to that file and to the
local override `sgt_fields.json` (`core/sgt/sgt_specs.override_path()`). No UI here - the
SGT lab dialog (`ui/dialogs/sgt_lab_dialog.py`) calls these functions so the decision logic
has its own tests, independent of Qt.

**Accept** merges the proposal's drafted spec into the override file, under the section the
miner chose (`profile`, `current_dataset` or `records`), replacing any earlier mined spec of
the same name; a developer later promotes it into the shipped file (14.4 step 11). The user
may rename the field first (the miner's own name never collides with a Core field, but a
mined name reads worse than a chosen one). **Reject** never touches the override file; it
only remembers the decision, so the same proposal is not shown again next time the miner
runs (`miner.write_proposals` already carries any non-pending status forward).

Decision 2026-09-28 (this WP): mined datapoints stay in the lab and the override file only -
no tracker column is added for a newly accepted field. A developer wires a new column by hand
once a datapoint has proven itself, same as any other spec.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..sgt import sgt_specs
from . import miner

__all__ = ["load_proposals", "set_status", "accept", "reject", "merge_into_override", "run_miner"]


# ── proposals.json: load and remember a decision ─────────────────────────────────
def load_proposals(path: Optional[Path] = None) -> Dict[str, Any]:
    """The miner's proposals.json, or an empty shell when it does not exist yet / is unreadable."""
    path = path or miner.proposals_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"format": miner.FORMAT, "generated": "", "proposals": [], "dropped": {}}
    if not isinstance(data, dict) or not isinstance(data.get("proposals"), list):
        return {"format": miner.FORMAT, "generated": "", "proposals": [], "dropped": {}}
    return data


def _write_json_atomic(data: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def set_status(proposal_id: str, status: str, path: Optional[Path] = None) -> bool:
    """Marks a proposal accepted/rejected/pending in proposals.json. False when the id is not
    there (a stale card - the miner has since re-run without it)."""
    path = path or miner.proposals_path()
    data = load_proposals(path)
    found = False
    for p in data["proposals"]:
        if p.get("id") == proposal_id:
            p["status"] = status
            found = True
            break
    if found:
        _write_json_atomic(data, path)
    return found


# ── Accept: the drafted spec into the local override file ───────────────────────
def merge_into_override(section: str, spec: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Writes `spec` into the override file's `section`, replacing any earlier spec of the
    same name there; every other section and top-level key is left exactly as it was. An override
    file that exists but cannot be read raises ValueError: it may hold hand-made Core specs, and
    rewriting it from nothing would silently remove them."""
    path = path or sgt_specs.override_path()
    data: Any = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            raise ValueError(f"{path.name} could not be read ({e}) - fix it first, nothing was written")
        if not isinstance(data, dict):
            raise ValueError(f"{path.name} is not a JSON object - fix it first, nothing was written")
    name = spec.get("name") or spec.get("field")
    if section == miner.CURRENT:
        current = data.get("current_dataset")
        if not isinstance(current, dict):
            current = {}
        fields = [f for f in (current.get("fields") or []) if not isinstance(f, dict) or f.get("name") != name]
        fields.append(spec)
        current["fields"] = fields
        data["current_dataset"] = current
    else:
        items = [s for s in (data.get(section) or []) if not isinstance(s, dict) or s.get("name") != name]
        items.append(spec)
        data[section] = items
    _write_json_atomic(data, path)
    return path


def _renamed(spec: Dict[str, Any], section: str, rename: str) -> Dict[str, Any]:
    """A copy of `spec` with its target field renamed. For a record (several fields), `rename`
    renames the record's first field only - the common case (a single new column); renaming a
    record with several new fields is left to the developer who promotes it."""
    spec = json.loads(json.dumps(spec))
    if section == miner.RECORDS:
        fields = spec.get("fields") or []
        if fields:
            old = fields[0].get("field")
            fields[0]["field"] = rename
            spec["require"] = [rename if f == old else f for f in (spec.get("require") or [])]
    else:
        spec["field"] = rename
    return spec


def accept(proposal: Dict[str, Any], level: Optional[str] = None, rename: Optional[str] = None,
           override_path: Optional[Path] = None, proposals_file: Optional[Path] = None) -> Dict[str, Any]:
    """Accept: draft the final spec (`miner.spec_for_accept`, filling a status-wording proposal's
    ladder level), optionally rename its field, merge it into the override file, and remember the
    decision. Raises ValueError when the proposal carries no spec (a graduation candidate) or a
    status-wording proposal is accepted with no level."""
    section = proposal.get("section")
    if section is None or proposal.get("spec") is None:
        raise ValueError("this proposal has no spec to accept (needs: %s)" % proposal.get("needs"))
    spec = miner.spec_for_accept(proposal, level)
    if rename:
        spec = _renamed(spec, section, rename)
    merge_into_override(section, spec, override_path)
    set_status(proposal["id"], "accepted", proposals_file)
    return spec


MINE_CORPUS_DAYS = 14      # the corpus pages the miner reads and replays (the Core's own 30-day files)


def run_miner(atlas_dir: Optional[Path] = None, corpus_dir: Optional[Path] = None,
              proposals_file: Optional[Path] = None, days: int = MINE_CORPUS_DAYS) -> Dict[str, Any]:
    """The lab's "Look for new datapoints" (decision 2026-09-28, W12-R): one offline miner run over
    the atlas and the recent corpus, written to proposals.json (keeping earlier decisions). Slow -
    it replays the corpus per proposal - so the dialog runs it off the UI thread. Returns
    {"proposals": n pending, "dropped": {...}}."""
    from ..sgt.sgt_corpus import load_pages
    from .atlas import Atlas

    result = miner.mine(atlas=Atlas(atlas_dir), pages=load_pages(corpus_dir, days=days))
    miner.write_proposals(result, proposals_file)
    pending = [p for p in load_proposals(proposals_file)["proposals"]
               if (p.get("status") or "pending") == "pending"]
    return {"proposals": len(pending), "dropped": result.get("dropped") or {}}


def reject(proposal_id: str, proposals_file: Optional[Path] = None) -> bool:
    """Reject: remembers the decision only. The override file is never touched, so a rejected
    proposal leaves no trace outside the lab."""
    return set_status(proposal_id, "rejected", proposals_file)
