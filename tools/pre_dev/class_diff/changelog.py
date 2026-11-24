"""
tools/pre_dev/class_diff/changelog.py - PRE-DEV: page reads stored as a per-client, per-page CHANGELOG
=====================================================================================================
Pre-development test. Keeping every read of every client page costs 300-400 MB a day; most of a read
repeats the one before it. A changelog keeps the first read of a page in full and, for every later
read, only what changed. Replaying it gives back EVERY read exactly (same fields, same order, same
values), so a compare algorithm reading the changelog knows everything it would have known from the
full reads. Nothing is judged here (label or value, which nodes match across clients): that is the
compare algorithm's work, so a better algorithm later never finds the stored data wrong.

FILE: one JSON line per read. A THREAD is one session (one client) on one page link.

    first read of a thread   {"t": thread, "i": 0, "meta": {every field but docs}, "base": [docs]}
    every later read         {"t": thread, "i": n, "meta": {the fields that changed}, "ch": [ops per doc]}

  A doc's ops walk the thread's previous read of that doc, in order:

    0                     the doc did not change at all
    [n]                   the next n nodes are unchanged
    ["~", [patch, ...]]   the next len(list) nodes are the same nodes with some fields changed;
                          patch = {field: new value}: "name"/"value" = its text, "rect" = it moved,
                          "parent" = its parent's index moved ({} never appears: unchanged = [n])
    ["-", n]              the next n nodes are gone
    ["+", [node, ...]]    these nodes are new here

  A read whose field list or doc count differs from the thread's previous read starts the thread
  again with a "base" line (i keeps counting).

NODE MATCHING: difflib over each node's content WITHOUT name, value, rect and parent. So a block that
appears above a value does not renumber the value (the problem of position keys): it is one "+" and
everything after it is kept; a text that changed is a "~" patch, not "gone + new".

CRASH / RESTART: one read = one line, so a cut-off last line is skipped and never breaks the reads
before it. ChangelogWriter.load() reads an existing file back, so a restarted writer carries on and
writes exactly what one that never stopped would have written.

Usage:
    python tools/pre_dev/class_diff/changelog.py check <reads.jsonl> <changelog.jsonl>
        replays the changelog and compares it with the full reads, line by line; prints the sizes
    python tools/pre_dev/class_diff/changelog.py show <changelog.jsonl> [--nodes]
        what changed in each read, in words (--nodes also lists the new / gone / changed texts)
    python tools/pre_dev/class_diff/changelog.py build <reads.jsonl> <changelog.jsonl>
        writes a changelog from a file of full reads (a key_probe reads_*.jsonl, a recorder sdis_*.jsonl)
    python tools/pre_dev/class_diff/changelog.py FILE [--out PAGE]
        the views window's "changelog" view: an HTML page of what changed in each read, with the replay
        check and the sizes. FILE = a capture_ / comp_ file (its capture's changelog, that page only), a
        changelog_ file, or any .jsonl of full reads (a changelog is built from it first). Writes
        output/changelog_view_<name>.html (client data: stays in the git-ignored output folder)

key_probe.py (capture mode) writes both files for every capture: reads_<start>.jsonl (every poll, in
full) and changelog_<start>.jsonl, in output/ (git-ignored: client data, keep it on this PC).
"""

import argparse
import difflib
import gzip
import html
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

J = dict(ensure_ascii=False, separators=(",", ":"))
NOT_MATCHED_ON = ("name", "value", "rect", "parent")
_MISSING = object()


def dumps(rec: Dict[str, Any]) -> str:
    """The one JSON form both files use (so a full read and its replay compare as text)."""
    return json.dumps(rec, **J)


def thread_of(rec: Dict[str, Any]) -> str:
    link = rec.get("link") or ("title: " + str(rec.get("title") or ""))
    return f"{rec.get('session') or ''} {link}"


def _sig(n: Dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in n.items() if k not in NOT_MATCHED_ON}, **J)


def diff_doc(old: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> Any:
    """The ops that turn `old` into `new` (see the module doc)."""
    if old == new:
        return 0
    ops: List[Any] = []

    def keep(n: int) -> None:
        if ops and isinstance(ops[-1][0], int):
            ops[-1][0] += n
        else:
            ops.append([n])

    def patch(p: Dict[str, Any]) -> None:
        if ops and ops[-1][0] == "~":
            ops[-1][1].append(p)
        else:
            ops.append(["~", [p]])

    def gone(n: int) -> None:
        if ops and ops[-1][0] == "-":
            ops[-1][1] += n
        else:
            ops.append(["-", n])

    def new_nodes(nodes: List[Dict[str, Any]]) -> None:
        if ops and ops[-1][0] == "+":
            ops[-1][1].extend(nodes)
        else:
            ops.append(["+", list(nodes)])

    a, b = [_sig(n) for n in old], [_sig(n) for n in new]
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                o, n = old[i], new[j]
                if o == n:
                    keep(1)
                elif list(o) != list(n):
                    # a field came or went (only possible for the unmatched fields): the node is
                    # replaced whole, so its field order replays exactly
                    gone(1)
                    new_nodes([n])
                else:
                    patch({k: v for k, v in n.items() if o.get(k, _MISSING) != v})
        else:
            if i2 > i1:
                gone(i2 - i1)
            if j2 > j1:
                new_nodes(new[j1:j2])
    return ops


def apply_doc(old: List[Dict[str, Any]], ops: Any) -> List[Dict[str, Any]]:
    if ops == 0:
        return old
    out: List[Dict[str, Any]] = []
    i = 0
    for op in ops:
        if isinstance(op[0], int):
            out.extend(old[i:i + op[0]])
            i += op[0]
        elif op[0] == "-":
            i += op[1]
        elif op[0] == "+":
            out.extend(op[1])
        elif op[0] == "~":
            for p in op[1]:
                out.append({k: p.get(k, v) for k, v in old[i].items()})
                i += 1
        else:
            raise ValueError(f"unknown op {op[0]!r}")
    return out


class ChangelogWriter:
    """Turns full reads into changelog lines. One writer per changelog file."""

    def __init__(self) -> None:
        self.last: Dict[str, Dict[str, Any]] = {}       # thread -> its last read, in full
        self.count: Dict[str, int] = {}

    def load(self, lines: Iterable[str]) -> None:
        """Carry on after an existing changelog (a restart)."""
        for rec, thread, i in replay(lines):
            self.last[thread] = rec
            self.count[thread] = i + 1

    def line(self, rec: Dict[str, Any]) -> str:
        """The changelog line for one full read (a dict with "docs": [[node, ...], ...]), with "\\n"."""
        thread = thread_of(rec)
        prev = self.last.get(thread)
        i = self.count.get(thread, 0)
        docs = rec["docs"]
        if prev is None or list(prev) != list(rec) or len(prev["docs"]) != len(docs):
            out = {"t": thread, "i": i, "meta": {k: v for k, v in rec.items() if k != "docs"}, "base": docs}
        else:
            out = {"t": thread, "i": i, "meta": {k: v for k, v in rec.items() if k != "docs" and prev[k] != v},
                   "ch": [diff_doc(o, n) for o, n in zip(prev["docs"], docs)]}
        self.last[thread] = rec
        self.count[thread] = i + 1
        return dumps(out) + "\n"


def replay(lines: Iterable[str]) -> Iterator[Tuple[Dict[str, Any], str, int]]:
    """(full read, thread, i) for every whole line, in file order."""
    last: Dict[str, Dict[str, Any]] = {}
    for line in lines:
        try:
            o = json.loads(line)
        except ValueError:
            continue                                   # a cut-off last line
        if not isinstance(o, dict) or "t" not in o:
            continue
        t = o["t"]
        if "base" in o:
            rec = dict(o["meta"], docs=o["base"])
        else:
            prev = last[t]
            rec = {k: ([apply_doc(d, ops) for d, ops in zip(v, o["ch"])] if k == "docs" else o["meta"].get(k, v))
                   for k, v in prev.items()}
        last[t] = rec
        yield rec, t, o["i"]


def read_changelog(lines: Iterable[str]) -> Iterator[Dict[str, Any]]:
    """Every read of a changelog, in full, exactly as it was captured."""
    for rec, _t, _i in replay(lines):
        yield rec


def full_reads(lines: Iterable[str]) -> List[Dict[str, Any]]:
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and isinstance(rec.get("docs"), list):
            out.append(rec)
    return out


# ── command line ─────────────────────────────────────────────────────────────
def _lines(path: str) -> List[str]:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return f.read().splitlines()


def _size(path: str) -> int:
    import os
    return os.path.getsize(path)


def compare(read_lines: List[str], log_lines: List[str]) -> Dict[str, Any]:
    """Replays the changelog against the full reads: {"same", "reads", "replayed", "first_bad"}."""
    reads = [dumps(r) for r in full_reads(read_lines)]
    back = [dumps(r) for r in read_changelog(log_lines)]
    bad = next((n for n, (a, b) in enumerate(zip(reads, back)) if a != b), None)
    if bad is None and len(reads) != len(back):
        bad = min(len(reads), len(back))
    return {"same": bad is None, "reads": len(reads), "replayed": len(back), "first_bad": bad}


def _bytes(lines: List[str]) -> int:
    return sum(len(x.encode("utf-8")) + 1 for x in lines)


def _gz(lines: List[str]) -> int:
    return len(gzip.compress("\n".join(lines).encode("utf-8"), 9))


def check(reads_path: str, log_path: str) -> int:
    rl, ll = _lines(reads_path), _lines(log_path)
    res = compare(rl, ll)
    raw_b, log_b, gz_b = _size(reads_path), _size(log_path), _gz(ll)
    print(f"full reads : {res['reads']:5d} reads  {raw_b / 1e6:8.2f} MB")
    print(f"changelog  : {res['replayed']:5d} reads  {log_b / 1e6:8.2f} MB   gzipped {gz_b / 1e6:.2f} MB"
          f"   ({raw_b / max(gz_b, 1):.0f}x smaller gzipped)")
    if not res["same"]:
        print(f"NOT THE SAME: first difference at read {res['first_bad']}")
        return 1
    print("SAME: every read replays exactly from the changelog")
    return 0


def _text(n: Dict[str, Any]) -> str:
    return str(n.get("name") or n.get("value") or "")


def describe(lines: Iterable[str]) -> Iterator[Dict[str, Any]]:
    """What each changelog line says, in words: {thread, i, ts, bytes, base (the nodes of a full page, else
    None), meta (changed field names but ts), counts {kept, text, moved, other, gone, new}, items: [(kind,
    text, was)]} with kind "+" new, "-" gone, "~" text changed (was = the old text), "f" other fields."""
    prev: Dict[str, Dict[str, Any]] = {}
    for line in lines:
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if not isinstance(o, dict) or "t" not in o:
            continue
        t = o["t"]
        out = {"thread": t, "i": o["i"], "ts": str(o["meta"].get("ts") or ""), "bytes": len(line.encode("utf-8")),
               "base": None, "meta": sorted(k for k in o["meta"] if k != "ts"), "items": [],
               "counts": {"kept": 0, "text": 0, "moved": 0, "other": 0, "gone": 0, "new": 0}}
        if "base" in o:
            out["base"] = [n for d in o["base"] for n in d]
            out["meta"] = []
            prev[t] = dict(o["meta"], docs=o["base"])
            yield out
            continue
        c, items = out["counts"], out["items"]
        for d, ops in zip(prev[t]["docs"], o["ch"]):
            if ops == 0:
                c["kept"] += len(d)
                continue
            i = 0
            for op in ops:
                if isinstance(op[0], int):
                    c["kept"] += op[0]
                    i += op[0]
                elif op[0] == "-":
                    c["gone"] += op[1]
                    items += [("-", _text(n), "") for n in d[i:i + op[1]] if _text(n)]
                    i += op[1]
                elif op[0] == "+":
                    c["new"] += len(op[1])
                    items += [("+", _text(n), "") for n in op[1] if _text(n)]
                else:
                    for p in op[1]:
                        if "name" in p or "value" in p:
                            c["text"] += 1
                            items.append(("~", _text(dict(d[i], **p)), _text(d[i])))
                        elif set(p) <= {"rect", "parent"}:
                            c["moved"] += 1
                        else:
                            c["other"] += 1
                            items.append(("f", ", ".join(sorted(p)), ""))
                        i += 1
        p = prev[t]
        prev[t] = {k: ([apply_doc(d, ops) for d, ops in zip(v, o["ch"])] if k == "docs" else o["meta"].get(k, v))
                   for k, v in p.items()}
        yield out


def show(log_path: str, nodes: bool = False) -> int:
    for r in describe(_lines(log_path)):
        head = f"[{r['i']:3d}] {r['ts'][11:23]:12s} {r['bytes'] / 1e3:6.1f} KB  {r['thread'][-60:]}"
        if r["base"] is not None:
            print(f"{head}\n      FULL PAGE: {len(r['base'])} nodes")
            continue
        print(head + ("" if not r["meta"] else f"   meta changed: {r['meta']}"))
        print("      " + "  ".join(f"{k} {v}" for k, v in r["counts"].items() if v))
        if nodes:
            for kind, text, was in r["items"]:
                if kind == "~":
                    print(f"      ~ {was[:33]!r} -> {text[:33]!r}")
                elif kind == "f":
                    print(f"      ~ fields {text}")
                else:
                    print(f"      {kind} {text[:70]}")
    return 0


def build(reads_path: str, log_path: str) -> int:
    w = ChangelogWriter()
    reads = full_reads(_lines(reads_path))
    with open(log_path, "w", encoding="utf-8") as f:
        for rec in reads:
            f.write(w.line(rec))
    print(f"{len(reads)} reads -> {log_path}")
    return check(reads_path, log_path)


# ── the views window's page ──────────────────────────────────────────────────
OUT_DIR = Path(__file__).resolve().parent / "output"
_STAMP = re.compile(r"^(?:capture|comp)_(\d{8}_\d{6})_\d+__")
ITEMS_MAX = 300          # change lines listed per read on the page (the rest is counted)


def sources_for(path: Path) -> Dict[str, Any]:
    """What a picked file means for the changelog view: {"log": changelog lines, "reads": full-read lines or
    None, "page": the page link to show (None = every page), "built": True when the changelog was made here
    from the full reads, "names": the files used}.
      capture_ / comp_ file  -> the changelog_<start>.jsonl (+ reads_<start>.jsonl) key_probe wrote for that
                                capture, limited to the file's page link
      changelog_ file        -> itself (+ its reads_ file)
      other .jsonl of reads  -> (reads_, a recorder sdis_ day) a changelog built from it here
    Raises FileNotFoundError with a plain reason."""
    path = Path(path)
    name = path.name
    m = _STAMP.match(name)
    if m:
        log, reads = path.with_name(f"changelog_{m.group(1)}.jsonl"), path.with_name(f"reads_{m.group(1)}.jsonl")
        if not log.exists():
            raise FileNotFoundError(f"no {log.name} beside this capture (made before the changelog existed, "
                                    "or with --no-changelog): make a New capture")
        try:
            with open(path, encoding="utf-8") as f:
                page = json.load(f).get("page") or None
        except (OSError, ValueError):
            page = None
        return {"log": _lines(str(log)), "reads": _lines(str(reads)) if reads.exists() else None, "page": page,
                "built": False, "names": [log.name] + ([reads.name] if reads.exists() else [])}
    if name.startswith("changelog_") and name.endswith((".jsonl", ".jsonl.gz")):
        reads = path.with_name("reads_" + name[len("changelog_"):])
        return {"log": _lines(str(path)), "reads": _lines(str(reads)) if reads.exists() else None, "page": None,
                "built": False, "names": [name] + ([reads.name] if reads.exists() else [])}
    if name.endswith((".jsonl", ".jsonl.gz")):
        rl = _lines(str(path))
        w = ChangelogWriter()
        return {"log": [w.line(r).rstrip("\n") for r in full_reads(rl)], "reads": rl, "page": None, "built": True,
                "names": [name]}
    raise FileNotFoundError(f"{name}: pick a capture_ / comp_ file, a changelog_ file or a .jsonl of reads")


def _e(s: Any) -> str:
    return html.escape(str(s), quote=True)


CSS = """body{font:14px/1.45 Segoe UI,Arial,sans-serif;margin:24px;color:#1f2328;background:#fafafa}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px;word-break:break-all}
.sub{color:#59636e;margin-bottom:16px}
.box{background:#fff;border:1px solid #d0d7de;border-radius:8px;padding:12px 16px;margin:12px 0}
.ok{border-left:6px solid #1a7f37}.bad{border-left:6px solid #cf222e}.na{border-left:6px solid #9a6700}
table{border-collapse:collapse;width:100%;background:#fff}
td,th{border-bottom:1px solid #eaeef2;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#f6f8fa;font-weight:600}td.n{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.chip{display:inline-block;border-radius:10px;padding:0 8px;margin:1px 4px 1px 0;font-size:12px;background:#eaeef2}
.c-text{background:#ddf4ff}.c-new{background:#dafbe1}.c-gone{background:#ffebe9}.c-other{background:#fff8c5}
.c-base{background:#d0d7de}details summary{cursor:pointer;color:#0969da}ul{margin:4px 0 0;padding-left:18px}
li{font-family:Consolas,monospace;font-size:12px;word-break:break-word}
.p{color:#1a7f37}.m{color:#cf222e}.t{color:#0969da}.f{color:#9a6700}.quiet{color:#8c959f}"""


def _rows(src: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [r for r in describe(src["log"]) if src["page"] is None or r["thread"].endswith(" " + src["page"])]


def _list(lis: List[str], total: int) -> str:
    more = total - len(lis)
    return "<ul>" + "".join(lis) + (f"<li class='quiet'>&hellip; and {more} more</li>" if more > 0 else "") + "</ul>"


def view_html(src: Dict[str, Any], title: str) -> str:
    rows = _rows(src)
    gz = _gz(src["log"])
    if src["reads"] is not None:
        res = compare(src["reads"], src["log"])
        raw_b = _bytes(src["reads"])
        verdict = (("ok", f"SAME: all {res['reads']} reads replay exactly from the changelog") if res["same"] else
                   ("bad", f"NOT THE SAME: first difference at read {res['first_bad']} "
                           f"({res['reads']} full reads, {res['replayed']} replayed)"))
        sizes = (f"full reads {raw_b / 1e6:.2f} MB &rarr; changelog {_bytes(src['log']) / 1e6:.2f} MB, "
                 f"gzipped {gz / 1e6:.2f} MB ({raw_b / max(gz, 1):.0f}&times; smaller)")
    else:
        verdict = ("na", "no full reads file beside the changelog: the replay could not be checked")
        sizes = f"changelog {_bytes(src['log']) / 1e6:.2f} MB, gzipped {gz / 1e6:.2f} MB"
    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>{_e(title)}</title>"
           f"<style>{CSS}</style></head><body>",
           f"<h1>Changelog: {_e(title)}</h1>",
           f"<div class='sub'>{_e(', '.join(src['names']))}"
           + (" (changelog built here from the full reads)" if src["built"] else "")
           + (f" &middot; page {_e(src['page'])}" if src["page"] else "") + "</div>",
           f"<div class='box {verdict[0]}'><b>{_e(verdict[1])}</b><br>{sizes}"
           + (f"<br>this page: {len(rows)} reads, {sum(r['bytes'] for r in rows) / 1e3:.0f} KB of changelog"
              if src["page"] else "") + "</div>",
           "<div class='box'>Each read is stored as only what changed since the read before it of the same page "
           "and client. <span class='chip c-text'>text</span> a node's text changed &middot; "
           "<span class='chip c-new'>new</span> nodes appeared &middot; <span class='chip c-gone'>gone</span> "
           "nodes disappeared &middot; <span class='chip'>moved</span> only the position changed (scroll, resize) "
           "&middot; <span class='chip'>kept</span> unchanged. Open a row's changes for the texts.</div>"]
    threads: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        threads.setdefault(r["thread"], []).append(r)
    if not threads:
        out.append("<div class='box na'>no reads of this page in the changelog</div>")
    for t, rs in threads.items():
        session, _, link = t.partition(" ")
        out.append(f"<h2>{_e(link)} <span class='quiet'>&middot; session {_e(session or '-')} &middot; "
                   f"{len(rs)} reads &middot; {sum(r['bytes'] for r in rs) / 1e3:.0f} KB</span></h2>")
        out.append("<table><tr><th>read</th><th>time</th><th>stored</th><th>what changed</th></tr>")
        for r in rs:
            if r["base"] is not None:
                texts = [_text(n) for n in r["base"] if _text(n)]
                what = (f"<span class='chip c-base'>full page {len(r['base'])} nodes</span>"
                        f"<details><summary>{len(texts)} texts</summary>"
                        + _list([f"<li>{_e(x[:160])}</li>" for x in texts[:ITEMS_MAX]], len(texts)) + "</details>")
            else:
                what = "".join(f"<span class='chip c-{k}'>{k} {v}</span>" for k, v in r["counts"].items() if v) \
                    or "<span class='quiet'>nothing changed</span>"
                if r["meta"]:
                    what += f" <span class='chip c-other'>also changed: {_e(', '.join(r['meta']))}</span>"
                if r["items"]:
                    lis = []
                    for kind, text, was in r["items"][:ITEMS_MAX]:
                        if kind == "~":
                            lis.append(f"<li class='t'>~ {_e(was[:120])} &rarr; {_e(text[:120])}</li>")
                        elif kind == "f":
                            lis.append(f"<li class='f'>~ fields: {_e(text)}</li>")
                        else:
                            lis.append(f"<li class='{'p' if kind == '+' else 'm'}'>{kind} {_e(text[:160])}</li>")
                    what += f"<details><summary>{len(r['items'])} changes</summary>" + _list(lis, len(r["items"])) \
                        + "</details>"
            out.append(f"<tr><td class='n'>{r['i']}</td><td class='n'>{_e(r['ts'][11:23])}</td>"
                       f"<td class='n'>{r['bytes'] / 1e3:.1f} KB</td><td>{what}</td></tr>")
        out.append("</table>")
    out.append("</body></html>")
    return "\n".join(out) + "\n"


def view(path: str, out: Optional[str] = None) -> int:
    p = Path(path)
    try:
        src = sources_for(p)
    except FileNotFoundError as e:
        print(f"changelog: {e}")
        return 1
    stem = p.name.split(".json")[0]
    if stem.startswith("changelog_"):
        stem = stem[len("changelog_"):]
    dest = Path(out) if out else OUT_DIR / f"changelog_view_{stem}.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(view_html(src, p.name), encoding="utf-8")
    rows = _rows(src)
    print(f"{len(rows)} reads, {sum(1 for r in rows if r['base'] is not None)} full pages, "
          f"{sum(r['counts']['text'] for r in rows)} text changes, {sum(r['counts']['new'] for r in rows)} new nodes, "
          f"{sum(r['counts']['gone'] for r in rows)} gone")
    if src["reads"] is not None:
        res = compare(src["reads"], src["log"])
        print("SAME: every read replays exactly" if res["same"] else f"NOT THE SAME at read {res['first_bad']}")
    print(f"-> {dest}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in ("check", "show", "build", "-h", "--help"):
        ap = argparse.ArgumentParser(description="The changelog of a capture as an HTML page (the views window).")
        ap.add_argument("file", help="a capture_ / comp_ file, a changelog_ file or a .jsonl of full reads")
        ap.add_argument("--out", default=None, help="the page to write (default output/changelog_view_<name>.html)")
        a = ap.parse_args(argv)
        return view(a.file, a.out)
    ap = argparse.ArgumentParser(description="Page reads as a changelog (pre-dev).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("reads"); c.add_argument("changelog")
    s = sub.add_parser("show"); s.add_argument("changelog"); s.add_argument("--nodes", action="store_true")
    b = sub.add_parser("build"); b.add_argument("reads"); b.add_argument("changelog")
    args = ap.parse_args(argv)
    if args.cmd == "check":
        return check(args.reads, args.changelog)
    if args.cmd == "show":
        return show(args.changelog, args.nodes)
    return build(args.reads, args.changelog)


if __name__ == "__main__":
    raise SystemExit(main())
