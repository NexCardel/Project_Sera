"""core/drs/strip.py - the All Clients "Filing status" strip: a one-row DRS per client.

Each client gets a list of self-labelled chips such as [GSTR-1 · Jun 26], coloured by the
tracker's status ladder. For every form the client files:

  * the current period is the latest period captured for that form by ANY client (snapped to the
    client's own cadence: monthly, quarterly or yearly, read from their latest capture);
  * the chips walk back from there, one per period, up to MAX_SLOTS;
  * a period with no capture is a "Not captured" chip, so a gap shows instead of vanishing
    (never older than the client's first capture of that form; a client with no capture at all
    only gets the current-period chip);
  * a GST form filed under several GSTINs shows the worst status of them, with the count.

Chips are ordered current period first across all forms, then the previous period, and so on,
so the most important ones are on the left when the cell is too narrow for all of them.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from core.ltt.feed import LEVELS, NOT_APPLICABLE, entry_form, ladder, local_time, parse_period, _pan_of
from core.ltt.monthly import ANNUAL_FORMS, is_gst_form, period_key, _capture_time, _gstin_of
from core.ltt.rules import canon

from .engine import (
    _extract_client_gstins,
    _history_of,
    _mcl_column_ids,
    _resolve_pan,
    _tracker_history_entries,
    detect_forms_and_services,
)

MISSING = "Not captured"
MAX_SLOTS = 6

# worst first: a strip chip for several GSTINs shows the lowest of these
_RANK = {MISSING: 0, LEVELS[0]: 1, LEVELS[1]: 2, LEVELS[2]: 3, LEVELS[3]: 4, NOT_APPLICABLE: 5}

# same colours as the LTT workbook (core/ltt/monthly.py STATUS_FILL / STATUS_TEXT)
LEVEL_COLOURS = {
    LEVELS[0]: ("#F4A99B", "#7A1F12"),
    LEVELS[1]: ("#FFDB70", "#6B4A00"),
    LEVELS[2]: ("#C9E06F", "#4A5A0A"),
    LEVELS[3]: ("#86D4A0", "#0F4F2A"),
    NOT_APPLICABLE: ("#DADADA", "#555555"),
    MISSING: (None, "#B4432F"),           # hollow chip, outlined in the text colour
}

_CANON_FIX = {"GSTRIA": "GSTR1A", "GSTR38": "GSTR3B"}     # OCR slips detect_forms_and_services also fixes


def _canon_form(entry: dict) -> str:
    key = canon(entry_form(entry))
    return _CANON_FIX.get(key, key)


@dataclass(frozen=True)
class StripChip:
    form: str               # display spelling, "GSTR-3B"
    period: str             # short period, "Jun 26" / "Apr–Jun 26" / "AY 26-27"
    level: str              # one of LEVELS, NOT_APPLICABLE or MISSING
    slot: int               # 0 = current period, 1 = the one before, ...
    registrations: int = 1  # GSTINs folded into this chip
    detail: str = ""        # tooltip body

    @property
    def text(self) -> str:
        return f"{self.form} · {self.period}"

    @property
    def colours(self) -> tuple:
        return LEVEL_COLOURS[self.level]


def _shift(d: date, months: int) -> date:
    n = d.year * 12 + d.month - 1 + months
    return date(n // 12, n % 12 + 1, 1)


def _span(entry: dict, canon_f: str) -> int:
    if canon_f.startswith(ANNUAL_FORMS):
        return 12
    months, _fy = parse_period(entry.get("period_label"), canon_f.startswith("ITR"))
    return 3 if len(months) == 3 else 1


def _snap(end: date, span: int) -> date:
    """The last period end of this cadence at or before `end` (quarters end Jun / Sep / Dec / Mar)."""
    if span == 3:
        return _shift(end, -(end.month % 3))
    return end


def period_text(end: date, span: int, canon_f: str) -> str:
    abbr = calendar.month_abbr
    if span == 12:
        if canon_f.startswith("ITR"):
            return f"AY {end.year % 100:02d}-{(end.year + 1) % 100:02d}"
        return f"FY {(end.year - 1) % 100:02d}-{end.year % 100:02d}"
    if span == 3:
        start = _shift(end, -2)
        return f"{abbr[start.month]}–{abbr[end.month]} {end.year % 100:02d}"
    return f"{abbr[end.month]} {end.year % 100:02d}"


def _best(entries: list[dict]) -> tuple[str, dict | None]:
    """The best ladder level among one period's captures, newest capture breaking ties (as DRS does)."""
    pick = None
    for h in entries:
        lvl = ladder(h.get("status"), h.get("arn"))
        key = (_RANK.get(lvl, 0), _capture_time(str(h.get("created_at") or "")))
        if pick is None or key > pick[0]:
            pick = (key, lvl, h)
    return (pick[1], pick[2]) if pick else (MISSING, None)


def _detail_line(gstin: str, level: str, h: dict | None) -> str:
    head = f"{gstin}: {level}" if gstin else level
    if not h:
        return head
    bits = []
    arn = str(h.get("arn") or "").strip()
    if arn and arn.upper() != "N/A":
        bits.append(f"ARN {arn}")
    at = str(h.get("created_at") or "")
    if at:
        bits.append(f"captured {local_time(at)}")
    by = str(h.get("captured_by") or "").strip()
    if by:
        bits.append(f"by {by}")
    return head + (" · " + " · ".join(bits) if bits else "")


def _load_histories(db):
    """(entries by PAN, entries by client id, every entry) from SRPF containers plus raw tracker rows."""
    containers = []
    if hasattr(db, "get_srpf_containers"):
        try:
            containers = db.get_srpf_containers(limit=1_000_000, slim=True) or []
        except Exception:
            containers = []
    by_pan: dict[str, list] = {}
    by_cid: dict[int, list] = {}
    every: list = []
    for c in containers:
        hist = _history_of(c)
        every.extend(hist)
        p = _pan_of(c)
        if p:
            by_pan.setdefault(p, []).extend(hist)
        if c.get("client_id"):
            by_cid.setdefault(c["client_id"], []).extend(hist)
    tracker = _tracker_history_entries(db)
    for cid, ident, entry in tracker:
        every.append(entry)
        if cid:
            by_cid.setdefault(cid, []).append(entry)
        p = ident if len(ident) == 10 else (ident[2:12] if len(ident) == 15 else "")
        if p:
            by_pan.setdefault(p, []).append(entry)
    return containers, by_pan, by_cid, every, [e for _c, _i, e in tracker]


def build_filing_strips(db, clients: list[dict]) -> dict[int, list[StripChip]]:
    """client id -> its strip chips (see the module docstring). Clients with nothing to show get []."""
    containers, by_pan, by_cid, every, tracker_entries = _load_histories(db)
    detection = list(containers)
    if tracker_entries:
        detection.append({"history": tracker_entries})
    if not every:
        return {}
    form_defs = detect_forms_and_services(db, detection)
    order = {f["canon_form"]: i for i, f in enumerate(form_defs)}
    names = {f["canon_form"]: f["form"] for f in form_defs}

    # the current period of every form: the latest any client has captured
    latest: dict[str, date] = {}
    form_counts: dict[str, int] = {}
    for h in every:
        f = _canon_form(h)
        if f not in order:
            continue
        form_counts[f] = form_counts.get(f, 0) + 1
        end = period_key(str(h.get("period_label") or ""), f)
        if end and (f not in latest or end > latest[f]):
            latest[f] = end

    # forms a service implies for a client with no capture on that service's portal yet
    implied: dict[int, list[str]] = {}
    itr_forms = sorted((f for f in latest if f.startswith("ITR")), key=lambda f: -form_counts.get(f, 0))
    for fdef in form_defs:
        svc = fdef.get("service")
        f = fdef["canon_form"]
        if not svc or f not in latest:
            continue
        if f in ("GSTR1", "GSTR3B") or (itr_forms and f == itr_forms[0]):
            implied.setdefault(svc["id"], []).append(f)

    mcl_cols = db.get_mcl_columns() if hasattr(db, "get_mcl_columns") else []
    pan_ids, gstin_ids, gst_ids = _mcl_column_ids(mcl_cols)
    pan_containers = {p: {"history": h} for p, h in by_pan.items()}

    out: dict[int, list[StripChip]] = {}
    for client in clients:
        cid = client.get("id")
        pan = _resolve_pan(client, mcl_cols, pan_ids, gst_ids)
        seen, grouped = set(), {}
        for h in (by_pan.get(pan, []) if pan else []) + by_cid.get(cid, []):
            f = _canon_form(h)
            if f not in latest:
                continue
            arn = str(h.get("arn") or "").strip().upper()
            k = (f, str(h.get("period_label") or ""), "" if arn == "N/A" else arn, str(h.get("created_at") or ""))
            if k in seen:
                continue
            seen.add(k)
            grouped.setdefault(f, []).append(h)

        forms = set(grouped)
        for sid in client.get("service_ids") or []:
            wanted = implied.get(sid, [])
            gst_family = any(is_gst_form(f) for f in wanted)
            covered = any(is_gst_form(f) == gst_family for f in grouped)
            if not covered:
                forms.update(wanted)

        gstins = None
        chips: list[StripChip] = []
        for f in forms:
            entries = grouped.get(f, [])
            placed = [(period_key(str(h.get("period_label") or ""), f), h) for h in entries]
            placed = [(e, h) for e, h in placed if e]
            if placed:
                newest = max(placed, key=lambda p: (p[0], _capture_time(str(p[1].get("created_at") or ""))))[1]
                span = _span(newest, f)
                first = min(e for e, _h in placed)
            else:
                span = 12 if f.startswith(ANNUAL_FORMS) else 1
                first = None
            current = _snap(latest[f], span)

            regs = [""]
            if is_gst_form(f):
                if gstins is None:
                    gstins = _extract_client_gstins(client, mcl_cols, pan_containers, pan, gstin_ids)
                regs = gstins or [""]

            for slot in range(MAX_SLOTS):
                end = _shift(current, -slot * span)
                if slot and (first is None or end < first):
                    break
                in_period = [h for e, h in placed if e == end]
                lines, worst = [], None
                for g in regs:
                    mine = [h for h in in_period if not g or not _gstin_of(h, "") or _gstin_of(h, "") == g]
                    lvl, h = _best(mine)
                    lines.append(_detail_line(g if len(regs) > 1 else "", lvl, h))
                    if worst is None or _RANK[lvl] < _RANK[worst]:
                        worst = lvl
                label = period_text(end, span, f)
                name = names.get(f, f)
                chips.append(StripChip(
                    form=name, period=label, level=worst, slot=slot, registrations=len(regs),
                    detail=f"{name} · {label}\n" + "\n".join(lines)))
        chips.sort(key=lambda c: (c.slot, order.get(canon(c.form), 99)))
        out[cid] = chips
    return out


def strip_text(chips: list[StripChip]) -> str:
    """Plain text of a strip, for copying cells and for the grid's sort/search text."""
    return "; ".join(f"{c.text}: {c.level}" for c in chips)
