"""
tools/sgt_atlas.py - inspect a portal's atlas: pages, coverage, and change alarm
=================================================================================
Blueprint 14.4 step 4 (`core/sgt_i/atlas.py`). Read-only: this tool never merges a page, only
reads the two files SGT-I already wrote for a portal.

    python tools/sgt_atlas.py list                          # portals with an atlas on this PC
    python tools/sgt_atlas.py show gst.gov.in                # every page: visits, slots, fingerprint
    python tools/sgt_atlas.py show gst.gov.in --page p-3f9a  # one page in full: slots, regions, retired
    python tools/sgt_atlas.py coverage gst.gov.in             # pages seen, slots claimed/unclaimed
    python tools/sgt_atlas.py diff old.json new.json         # what appeared / disappeared - the alarm

Options: --dir DIR (defaults to sgt_i_dir(), the real per-PC atlas store - never the repo).
`diff` also takes a directory in place of a file, with --portal to pick its atlas file inside it -
so the same command compares two live installs, or two dated copies of one.

`coverage`'s "claimed" reads whatever a slot's `claimed_by` already holds (14.4 step 4's JSON
example); no later WP has to change this tool, it just starts showing more as that field fills in.
Everything printed is portal structure and counts (14.5) - never a client's value.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.sgt_i.atlas import ATLAS_DIR, PortalAtlas    # noqa: E402
from core.sgt_i.stats import sgt_i_dir                  # noqa: E402


def list_portals(base: Path) -> List[str]:
    """Portal ids (file stems) that have an atlas file under `base`."""
    d = base / ATLAS_DIR
    return sorted(p.stem for p in d.glob("*.json")) if d.is_dir() else []


def load_portal(portal: str, base: Path) -> Dict[str, Any]:
    """The public atlas for one portal - empty when it has none yet (never written to disk)."""
    return PortalAtlas(portal, directory=base).to_json()


def _slots(page: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(page.get("slots", {}).values())


def coverage(atlas: Dict[str, Any]) -> Dict[str, Any]:
    """Pages seen, slots claimed/unclaimed - per page and for the whole portal."""
    by_page = []
    total = claimed = 0
    for p in atlas.get("pages", []):
        slots = _slots(p)
        c = sum(1 for s in slots if s.get("claimed_by"))
        total += len(slots)
        claimed += c
        by_page.append({"id": p["id"], "visits": p["visits"], "clients": p["clients"],
                        "slots": len(slots), "claimed": c, "unclaimed": len(slots) - c})
    return {"portal": atlas.get("portal", ""), "pages": len(by_page), "slots": total,
            "claimed": claimed, "unclaimed": total - claimed, "by_page": by_page}


def diff_atlases(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """What a redesign changes: pages that appeared or disappeared, and - for a page seen in both -
    fingerprint tokens and slot containers gained or lost (14.4 step 4: "Date of Birth" gone, "DOB"
    appeared). Matched by page id: stable across saves of one atlas, and, since an id carries no
    salt, across two PCs that synced it (14.5 rule 7)."""
    old_pages = {p["id"]: p for p in old.get("pages", [])}
    new_pages = {p["id"]: p for p in new.get("pages", [])}
    added = sorted(new_pages.keys() - old_pages.keys())
    removed = sorted(old_pages.keys() - new_pages.keys())
    changed = []
    for pid in sorted(old_pages.keys() & new_pages.keys()):
        op, np = old_pages[pid], new_pages[pid]
        fp_added = sorted(set(np.get("fingerprint", ())) - set(op.get("fingerprint", ())))
        fp_removed = sorted(set(op.get("fingerprint", ())) - set(np.get("fingerprint", ())))
        old_containers = {s["container"] for s in _slots(op)}
        new_containers = {s["container"] for s in _slots(np)}
        sl_added = sorted(new_containers - old_containers)
        sl_removed = sorted(old_containers - new_containers)
        if fp_added or fp_removed or sl_added or sl_removed:
            changed.append({"id": pid, "fingerprint_added": fp_added, "fingerprint_removed": fp_removed,
                            "slots_added": sl_added, "slots_removed": sl_removed})
    return {"pages_added": [{"id": i, "fingerprint": new_pages[i].get("fingerprint", [])} for i in added],
            "pages_removed": [{"id": i, "fingerprint": old_pages[i].get("fingerprint", [])} for i in removed],
            "pages_changed": changed}


def format_diff(d: Dict[str, Any]) -> str:
    lines = []
    for p in d["pages_added"]:
        lines.append("+ page %s appeared: %s" %
                      (p["id"], ", ".join(p["fingerprint"][:4]) or "(no fingerprint yet)"))
    for p in d["pages_removed"]:
        lines.append("- page %s disappeared: %s" %
                      (p["id"], ", ".join(p["fingerprint"][:4]) or "(no fingerprint yet)"))
    for c in d["pages_changed"]:
        lines.append("~ page %s changed:" % c["id"])
        lines += ["    - %s" % t for t in c["fingerprint_removed"]]
        lines += ["    + %s" % t for t in c["fingerprint_added"]]
        lines += ["    - slot %s" % s for s in c["slots_removed"]]
        lines += ["    + slot %s" % s for s in c["slots_added"]]
    return "\n".join(lines) if lines else "No difference."


def page_line(page: Dict[str, Any]) -> str:
    slots = _slots(page)
    claimed = sum(1 for s in slots if s.get("claimed_by"))
    fading = " fading" if page.get("fading") else ""
    return ("%-8s visits=%-4d clients=%-3d slots=%d (%d claimed) elements=%d  %s..%s%s" %
            (page["id"], page["visits"], page["clients"], len(slots), claimed,
             len(page.get("elements", {})), page["first_seen"], page["last_seen"], fading))


def page_detail(page: Dict[str, Any]) -> str:
    lines = [page_line(page), "  fingerprint:"]
    lines += ["    " + t for t in page.get("fingerprint", [])] or ["    (none yet)"]
    lines.append("  slots:")
    for s in _slots(page):
        shapes = ", ".join(s.get("shapes", {}))
        lines.append("    %-40s type=%-12s %-10s seen=%-3d %s" %
                      (s["container"], s.get("type", ""), s.get("claimed_by") or "unclaimed",
                       s.get("seen", 0), shapes))
    if page.get("regions"):
        lines.append("  regions:")
        for r in page["regions"]:
            lines.append("    %s: %s (seen %d%s)" %
                          (r["role"], r["label"], r["seen"], ", optional" if r.get("optional") else ""))
    if page.get("retired"):
        lines.append("  retired:")
        for r in page["retired"]:
            lines.append("    was %s %r (%s..%s)" %
                          (r["was"], r.get("text") or r.get("container", ""), r["first_seen"], r["last_seen"]))
    return "\n".join(lines)


def _diff_source(arg: str, portal: Optional[str]) -> Dict[str, Any]:
    path = Path(arg)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    if not portal:
        raise SystemExit("%s is a directory - pass --portal to pick its atlas file" % arg)
    return load_portal(portal, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="portals with an atlas on this PC")
    p_list.add_argument("--dir", type=Path, default=None)

    p_show = sub.add_parser("show", help="a portal's pages")
    p_show.add_argument("portal")
    p_show.add_argument("--dir", type=Path, default=None)
    p_show.add_argument("--page", default=None, help="show one page in full")

    p_cov = sub.add_parser("coverage", help="pages seen, slots claimed/unclaimed")
    p_cov.add_argument("portal")
    p_cov.add_argument("--dir", type=Path, default=None)
    p_cov.add_argument("--json", action="store_true")

    p_diff = sub.add_parser("diff", help="what appeared / disappeared between two atlas files")
    p_diff.add_argument("old")
    p_diff.add_argument("new")
    p_diff.add_argument("--portal", default=None, help="when old/new names a directory, not a file")
    p_diff.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)
    base = getattr(args, "dir", None) or sgt_i_dir()

    if args.command == "list":
        portals = list_portals(base)
        print("\n".join(portals) if portals else "No atlas files under %s" % (base / ATLAS_DIR))
        return 0

    if args.command == "show":
        atlas = load_portal(args.portal, base)
        pages = atlas.get("pages", [])
        if not pages:
            print("No atlas yet for %r under %s" % (args.portal, base))
            return 1
        if args.page:
            page = next((p for p in pages if p["id"] == args.page), None)
            if page is None:
                print("No page %r on %r" % (args.page, args.portal))
                return 1
            print(page_detail(page))
        else:
            print("%s: %d page(s), %d transition(s)" %
                  (args.portal, len(pages), len(atlas.get("transitions", []))))
            for p in pages:
                print(page_line(p))
        return 0

    if args.command == "coverage":
        cov = coverage(load_portal(args.portal, base))
        if args.json:
            print(json.dumps(cov, indent=1))
        else:
            print("%s: %d page(s), %d slot(s): %d claimed, %d unclaimed" %
                  (cov["portal"] or args.portal, cov["pages"], cov["slots"], cov["claimed"], cov["unclaimed"]))
            for p in cov["by_page"]:
                print("  %-8s slots=%-3d claimed=%-3d unclaimed=%-3d (visits=%d clients=%d)" %
                      (p["id"], p["slots"], p["claimed"], p["unclaimed"], p["visits"], p["clients"]))
        return 0

    if args.command == "diff":
        d = diff_atlases(_diff_source(args.old, args.portal), _diff_source(args.new, args.portal))
        print(json.dumps(d, indent=1) if args.json else format_diff(d))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
