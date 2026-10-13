"""
core/sdis/mine_process.py - "Find datapoints" in its own process (SDIS Part O, child side)
==========================================================================================
main.py hands `<exe> --sdis-mine --captures <folder> [--state <file>] [--rebuild]` to run() before Qt or the
database load. run() calls mine() and writes one JSON line per finished client map to stdout, flushed:

  {"done": n, "total": t, "page": link}
  {"result": "ok", ...mine() summary}          last line, exit code 0
  {"result": "error", "message": ...}          last line, exit code 1

No CPU cap (owner, 2026-10-04: mining runs only on the admin PC). Cancel is the parent ending the process:
mine() saves the state after every client map, atomically, so the next run resumes. Pass --state: without
it the default path loads core.vsdc (Qt) to find the Sera data folder.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _emit(obj: Dict[str, Any]) -> None:
    out = sys.stdout
    if out is None:                                          # a windowed exe started without a pipe
        return
    out.write(json.dumps(obj, default=str) + "\n")          # ASCII: a pipe's code page cannot fail
    out.flush()


def run(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv if argv is None else argv)
    args = [a for a in args[1:] if a != "--sdis-mine"]       # argv[0] is the exe, or main.py from source
    parser = argparse.ArgumentParser(prog="--sdis-mine", add_help=False)
    parser.add_argument("--captures", required=True)
    parser.add_argument("--state", default="")
    parser.add_argument("--rebuild", action="store_true")
    try:
        opts, _rest = parser.parse_known_args(args)
    except SystemExit:
        _emit({"result": "error", "message": "usage: --sdis-mine --captures <folder> [--state <file>] [--rebuild]"})
        return 1
    captures = Path(opts.captures)
    if not captures.is_dir():
        _emit({"result": "error", "message": "the captures folder does not exist"})
        return 1
    try:
        from core.sdis.mine import mine
        summary = mine(captures, opts.state or None, rebuild=opts.rebuild,
                       progress=lambda done, total, page: _emit({"done": done, "total": total, "page": page}))
    except Exception as exc:                                 # the type only: a message could quote page text
        _emit({"result": "error", "message": f"mining failed ({type(exc).__name__})"})
        return 1
    _emit({"result": "ok", **summary})
    return 0
