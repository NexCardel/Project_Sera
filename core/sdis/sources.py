"""
core/sdis/sources.py - SDIS's recorder output as link_map sources (Part K)
==========================================================================
The recorder (recorder.py) writes one JSON line per changed page to sdis_YYYY-MM-DD.jsonl. This
turns those lines into the (stamp, session, page link, read) tuples link_map.all_sources() gives
for the pre-dev captures, so every core function runs on both alike:

    stamp    the record's ISO time (sorts in time order; history.day_of reads its day)
    session  "sgt <SGT's session id> <started>" (Part D; ends in a time, as identity._by_time wants)
    link     keys.page_link(url), else "title: <title>" (as link_map.page_of)
    read     {"docs": the raw-view node tree, "browser": the window's browser (Part M)}

A line that is not a whole record (the file is being written) is skipped, not an error.
"""

import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

from core.sdis.recorder import FILE_PREFIX, FILE_SUFFIX

Source = Tuple[str, str, str, Dict[str, Any]]


def is_record_file(path: Path) -> bool:
    return path.name.startswith(FILE_PREFIX) and path.name.endswith(FILE_SUFFIX)


def record_files(directory: Path) -> List[Path]:
    return sorted(Path(directory).glob(f"{FILE_PREFIX}*{FILE_SUFFIX}"))


def file_records(path: Path) -> List[Source]:
    out: List[Source] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or not rec.get("ts") or not isinstance(rec.get("docs"), list):
                continue
            link = rec.get("link") or ("title: " + (rec.get("title") or ""))
            session = f"sgt {rec.get('session') or '-'} {rec.get('started') or rec['ts']}"
            out.append((str(rec["ts"]), session, link,
                        {"docs": rec["docs"], "browser": rec.get("browser") or ""}))
    return out


def read_records(directory: Path) -> Iterator[Source]:
    """Every record in the folder, oldest file first, in file order."""
    for path in record_files(directory):
        yield from file_records(path)
