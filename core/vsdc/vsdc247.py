"""
core/vsdc/vsdc247.py — VSDC 24x7: the crosshair-independent submission safety net
================================================================================
Crosshairs recognise a page by its URL. URLs change and pages get missed, so VSDC247
does not care what page it is on: on every page of the two tax portals it watches for the
one thing that always means the same - a submission confirmation carrying an ARN /
acknowledgement number.

It is VSDC (pixels + Windows OCR), not VSDC-X: it reads the screen the way a person does,
so it keeps working when the accessibility tree does not. Strictly passive - it never
clicks, scrolls, types or injects anything.

What makes it precise (each rule below exists because of a real mistake this project made):

  * The identifier must be LABEL-ANCHORED ("Acknowledgement Number", "ARN"...), never a bare
    number, and must pass validation. An ITR acknowledgement number ends in its own filing
    date as DDMMYY (verified against every ack in the live dumps), so a live submission's
    ack must carry today's date: a reference to an OLD return - the original-return ack a
    revised-return wizard prints - carries an old date and is rejected.
  * Several signals must agree: label + valid identifier + success wording + (form,
    period, green success box). No single signal triggers a capture.
  * Hard vetoes: "original/previous return" context, more than one candidate identifier,
    future/help wording ("will be", "once you submit"), failure wording, drafts, and the
    e-Verify wizard's stepper labels.
  * Two tiers: CERTAIN is saved; PROBABLE only raises a HUD prompt. Nothing uncertain is
    ever silently saved, and nothing is ever silently missed.

What makes it complete:
  * A cheap change / green-region check runs every tick; OCR runs on every changed frame
    (a success toast can vanish in seconds) and immediately when a green region appears.
  * A candidate seen once with a moderate score is re-read on the next ticks and promoted
    if it is seen again (temporal agreement), so a single OCR glitch neither triggers a
    capture nor causes a miss.

Privacy: only structured fields leave this module (identifier, status, form, period,
evidence labels). Page text is analysed in memory and never stored.
"""

import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from .vsdc_regex import (
    DIGIT_FIX_MAP,
    classify_verification_status,
    extract_assessment_year,
    extract_filing_type,
    extract_gst_fy,
    extract_gst_tax_period,
    format_gst_period_label,
    strip_everify_stepper,
)

# ── Modes ────────────────────────────────────────────────────────────────────────
MODE_LIVE = "live"      # save CERTAIN captures, prompt on PROBABLE ones
MODE_SHADOW = "shadow"  # observe and log only - nothing is saved or shown
MODE_OFF = "off"


def configured_mode() -> str:
    mode = (os.environ.get("VSDC247_MODE") or MODE_LIVE).strip().lower()
    return mode if mode in (MODE_LIVE, MODE_SHADOW, MODE_OFF) else MODE_LIVE


# ── Tunables ─────────────────────────────────────────────────────────────────────
CERTAIN_SCORE = 85          # saved
PROBABLE_SCORE = 55         # prompt only
SINGLE_FRAME_SCORE = 95     # certain AND strong enough to save without a second read
MIN_SCAN_INTERVAL_SEC = 0.30
PENDING_RESCAN_LIMIT = 3    # extra reads granted to a candidate awaiting agreement
WINDOW_LINES = 4            # lines either side of an identifier that count as "the message"

# ── Green success box detection ──────────────────────────────────────────────────
_DOWNSCALE = 4
_CELL = 6                   # cell edge, in downscaled pixels
_CELL_FILL = 0.35           # fraction of a cell that must be green to count
_MIN_CELLS = 6


@dataclass
class GreenBox:
    x: int
    y: int
    w: int
    h: int

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h


def _green_mask(rgb: np.ndarray) -> np.ndarray:
    r = rgb[..., 0].astype(np.int16)
    g = rgb[..., 1].astype(np.int16)
    b = rgb[..., 2].astype(np.int16)
    light = (g >= 180) & (g - r >= 12) & (g - b >= 12)        # pale success-banner fill
    strong = (g >= 110) & (g - r >= 40) & (g - b >= 40)       # saturated green fill / border
    return light | strong


def find_green_boxes(img: Image.Image) -> List[GreenBox]:
    """
    Regions of the screen that look like a green success banner: large, roughly
    rectangular, predominantly green. Small green things - check icons, status dots,
    the portal's green logo strip - are too small to qualify.
    """
    if img is None:
        return []
    w, h = img.size
    if w < 64 or h < 64:
        return []
    small = img.convert("RGB").resize((max(1, w // _DOWNSCALE), max(1, h // _DOWNSCALE)))
    mask = _green_mask(np.asarray(small))
    gh, gw = mask.shape[0] // _CELL, mask.shape[1] // _CELL
    if gh == 0 or gw == 0:
        return []
    cells = mask[: gh * _CELL, : gw * _CELL].reshape(gh, _CELL, gw, _CELL).mean(axis=(1, 3)) >= _CELL_FILL
    if not cells.any():
        return []

    seen = np.zeros_like(cells, dtype=bool)
    boxes: List[GreenBox] = []
    scale = _DOWNSCALE * _CELL
    for cy in range(gh):
        for cx in range(gw):
            if not cells[cy, cx] or seen[cy, cx]:
                continue
            stack = [(cy, cx)]
            seen[cy, cx] = True
            members = []
            while stack:
                y, x = stack.pop()
                members.append((y, x))
                for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if 0 <= ny < gh and 0 <= nx < gw and cells[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            if len(members) < _MIN_CELLS:
                continue
            ys = [m[0] for m in members]
            xs = [m[1] for m in members]
            bx, by = min(xs) * scale, min(ys) * scale
            bw, bh = (max(xs) - min(xs) + 1) * scale, (max(ys) - min(ys) + 1) * scale
            bbox_cells = (max(xs) - min(xs) + 1) * (max(ys) - min(ys) + 1)
            # A banner fills most of its bounding box, is wide enough to hold a sentence and
            # tall enough to hold a line of text.
            if bw >= 0.12 * w and bh >= 28 and len(members) / bbox_cells >= 0.5:
                boxes.append(GreenBox(bx, by, bw, bh))
    return boxes


# ── Identifier extraction & validation ───────────────────────────────────────────
# Characters OCR commonly substitutes for digits. Only ever applied inside a token that
# already has the right length and shape, and the result must then validate.
_DIGITISH = r"0-9OoIl|!SsBZzDQgq"
_ITR_TOKEN = re.compile(rf"(?<![0-9A-Za-z])([{_DIGITISH}]{{15}})(?![0-9A-Za-z])")
_GST_TOKEN = re.compile(rf"(?<![0-9A-Za-z])([A-Z]{{2}}[{_DIGITISH}]{{11,14}}[A-Z0-9]?)(?![0-9A-Za-z])")
_LABEL = re.compile(
    r"(acknowledg\w*|\back\b|\barn\b|application\s+reference|reference\s+(?:no|number|id)|receipt\s+(?:no|number))",
    re.IGNORECASE,
)
# Wording that turns an identifier into a reference to ANOTHER filing.
_OTHER_FILING = re.compile(
    r"\b(original|previous|previously|earlier|prior|old|revised\s+return\s+of|against|corresponding\s+to)\b",
    re.IGNORECASE,
)

_SUCCESS = re.compile(
    r"(successfully\s+(?:submitted|filed|e-?\s*verified|verified|completed)"
    r"|(?:has|have|had)\s+been\s+(?:successfully\s+)?(?:submitted|filed|e-?\s*verified|received)"
    r"|(?:submitted|filed)\s+successfully"
    r"|(?:return|application|form|request)\s+(?:is\s+|was\s+)?(?:submitted|filed)"
    r"|thank\s+you\s+for\s+(?:filing|submitting)"
    r"|\barn\s+(?:has\s+been\s+)?generated"
    r"|acknowledg\w*\s+(?:number\s+)?(?:has\s+been\s+)?generated)",
    re.IGNORECASE,
)
# Help copy, future tense and instructions - not an event that has happened.
_FUTURE = re.compile(
    r"\b(will\s+be|would\s+be|shall\s+be|will\s+receive|once\s+(?:you|the|your)|after\s+(?:you\s+)?(?:submit|fil)\w*|"
    r"when\s+you\s+(?:submit|file)|to\s+(?:file|submit)|should|may\s+be|can\s+be|if\s+you|in\s+case)\b",
    re.IGNORECASE,
)
_FAILURE = re.compile(
    r"\b(fail\w*|error|unsuccessful|not\s+(?:been\s+)?(?:submitted|filed)|invalid|rejected|unable|try\s+again|"
    r"could\s+not|cannot|can't|declined|expired)\b",
    re.IGNORECASE,
)
_DRAFT = re.compile(r"\b(draft|saved\s+as|save\s+as|save\s+draft|saved\s+successfully)\b", re.IGNORECASE)

_GST_FORM = re.compile(r"\b(GSTR[-\s]?[0-9A-Z]{1,3}|CMP[-\s]?08|ITC[-\s]?04|REG[-\s]?[0-9]{2}[A-Z]?|RFD[-\s]?[0-9]{2}[A-Z]?|"
                       r"PMT[-\s]?[0-9]{2}|TRAN[-\s]?[0-9]|ASP[-\s]?[0-9]{2}|IFF)\b", re.IGNORECASE)


# A number sitting right after an ARN label whose shape is NOT the usual two letters + digits.
# "Starts with two letters" is an observed pattern, not a documented rule, so a labelled number
# that breaks it is not thrown away - but it is never saved on that evidence alone (see
# analyze_frame): at most it raises a "possible submission" prompt.
_GST_LOOSE = re.compile(
    r"(?:\barn\b|application\s+reference\s+(?:no|number))\s*[:\-–#.]*\s*"
    r"([A-Za-z0-9]{12,17})(?![0-9A-Za-z])",
    re.IGNORECASE,
)


@dataclass
class Candidate:
    identifier: str
    kind: str               # "itr_ack" | "gst_arn"
    line: int
    labeled: bool
    other_filing: bool = False
    date_state: str = "n/a"  # ITR only: "today" | "stale" | "invalid"
    ack_date: Optional[date] = None
    usual_shape: bool = True  # GST only: two letters + digits, as every ARN seen so far


def _repair_digits(token: str) -> str:
    return "".join(DIGIT_FIX_MAP.get(c, c) for c in token)


def _gst_normalise(tok: str) -> Optional[str]:
    """
    AA + 11-14 digits + optional check character. OCR digit repair is applied to the
    numeric body only; the last character is kept as read when the body is already
    complete without it, so a genuine check letter is never "repaired" into a digit.
    """
    if re.fullmatch(r"[A-Z]{2}[0-9]{11,14}[A-Z0-9]?", tok):
        return tok                       # already valid as read: nothing to repair
    body = tok[2:]
    all_digits = _repair_digits(body)
    if all_digits.isdigit() and 11 <= len(all_digits) <= 14:
        return tok[:2] + all_digits
    if len(body) >= 12 and body[-1].isalnum():
        lead = _repair_digits(body[:-1])
        if lead.isdigit() and 11 <= len(lead) <= 14:
            return tok[:2] + lead + body[-1]
    return None


def itr_ack_date(ack: str) -> Optional[date]:
    """
    The filing date an ITR acknowledgement number carries in its last six digits
    (DDMMYY) - e.g. 901036690280826 -> 28-Aug-2026. Verified against every ack in the
    live View Filed Returns dumps. None if those digits are not a real date.
    """
    if not re.fullmatch(r"\d{15}", ack or ""):
        return None
    try:
        return date(2000 + int(ack[13:15]), int(ack[11:13]), int(ack[9:11]))
    except ValueError:
        return None


def _labelled(lines: Sequence[str], idx: int, start: int) -> bool:
    """True if a label sits before the token on its own line or ends the previous line."""
    before = lines[idx][:start]
    if _LABEL.search(before):
        return True
    return idx > 0 and bool(_LABEL.search(lines[idx - 1][-60:]))


def find_candidates(lines: Sequence[str], portal: str, today: date) -> List[Candidate]:
    out: List[Candidate] = []
    for i, line in enumerate(lines):
        if portal == "Income Tax":
            for m in _ITR_TOKEN.finditer(line):
                tok = m.group(1)
                if sum(c.isdigit() for c in tok) < 12:
                    continue
                ack = _repair_digits(tok)
                if not ack.isdigit():
                    continue
                cand = Candidate(ack, "itr_ack", i, _labelled(lines, i, m.start()))
                cand.ack_date = itr_ack_date(ack)
                if cand.ack_date is None:
                    cand.date_state = "invalid"
                elif abs((cand.ack_date - today).days) <= 1:
                    cand.date_state = "today"
                else:
                    cand.date_state = "stale"
                near = " ".join(lines[max(0, i - 1): i + 1])
                cand.other_filing = bool(_OTHER_FILING.search(near))
                out.append(cand)
        elif portal == "GST Portal":
            for m in _GST_TOKEN.finditer(line):
                arn = _gst_normalise(m.group(1))
                if not arn:
                    continue
                cand = Candidate(arn, "gst_arn", i, _labelled(lines, i, m.start()))
                near = " ".join(lines[max(0, i - 1): i + 1])
                cand.other_filing = bool(_OTHER_FILING.search(near))
                out.append(cand)
    if portal == "GST Portal" and not out:
        # Nothing has the usual shape. Look for a label-anchored number that breaks it, so a
        # portal that changes the format is noticed instead of silently missed. Only tried
        # when there is no usual-shaped candidate, so it can never turn one ARN into a "list".
        for i, line in enumerate(lines):
            for m in _GST_LOOSE.finditer(line):
                tok = m.group(1).upper()
                if sum(c.isdigit() for c in tok) < 9:
                    continue
                cand = Candidate(tok, "gst_arn", i, True, usual_shape=False)
                near = " ".join(lines[max(0, i - 1): i + 1])
                cand.other_filing = bool(_OTHER_FILING.search(near))
                out.append(cand)
    return out


# ── Detection ────────────────────────────────────────────────────────────────────
@dataclass
class Detection:
    portal: str
    kind: str
    identifier: str
    status: str
    score: int
    tier: str                      # "certain" | "probable" | "vetoed"
    form: Optional[str] = None
    period: Optional[str] = None
    filing_date: Optional[str] = None      # ITR: taken from the ack itself
    reasons: List[str] = field(default_factory=list)
    vetoes: List[str] = field(default_factory=list)
    in_green_box: bool = False
    has_success_wording: bool = False
    usual_shape: bool = True        # False: an ARN-labelled number that breaks the usual ARN shape
    page_url: str = ""              # where it was seen (query string stripped); set by the router

    @property
    def evidence(self) -> Dict[str, Any]:
        """Structured evidence only - never page text."""
        return {"score": self.score, "tier": self.tier, "reasons": list(self.reasons),
                "vetoes": list(self.vetoes), "green_box": self.in_green_box}


def _word_boxes_for(words: Sequence[Dict[str, Any]], needle: str) -> List[Dict[str, Any]]:
    return [w for w in words if needle and needle in re.sub(r"\s+", "", str(w.get("text", "")))]


def analyze_frame(
    text: str,
    lines: Sequence[str],
    words: Sequence[Dict[str, Any]],
    green_boxes: Sequence[GreenBox],
    portal: str,
    today: Optional[date] = None,
    known_form: Optional[str] = None,
    known_period: Optional[str] = None,
) -> Optional[Detection]:
    """
    Reads one OCR'd frame and decides whether it shows a submission confirmation.
    Returns None when nothing worth reporting is on screen, otherwise a Detection whose
    tier says what to do with it.
    """
    today = today or date.today()
    if portal not in ("Income Tax", "GST Portal") or not lines:
        return None

    # The e-Verify wizard draws "Return Successfully Verified" as a step label on every step.
    lines = [ln for ln in (strip_everify_stepper(l).strip() for l in lines) if ln]
    candidates = find_candidates(lines, portal, today)
    if not candidates:
        return None

    vetoes: List[str] = []

    # References to some OTHER filing (a revised return prints the original's ack).
    referenced = [c for c in candidates if c.other_filing]
    candidates = [c for c in candidates if not c.other_filing]
    if referenced and not candidates:
        return _vetoed(portal, referenced[0], ["identifier refers to another filing (original/previous)"])

    # More than one identifier on screen - labelled or not, fresh or stale - is a LIST
    # (View Filed Returns, filing history, the e-Verify picker), never one submission.
    # This must be decided before any date filtering: otherwise a history page whose top
    # card was filed today would look like a single fresh submission.
    distinct = {c.identifier for c in candidates}
    if len(distinct) > 1:
        return _vetoed(portal, candidates[0], [f"{len(distinct)} different identifiers on screen - a list, not one submission"])

    cand = candidates[0]
    if not cand.labeled:
        return _vetoed(portal, cand, ["identifier is not next to an ARN / acknowledgement label"])

    if portal == "Income Tax":
        if cand.date_state == "stale":
            return _vetoed(portal, cand, ["ack is dated " + cand.ack_date.isoformat()
                                          + ", not today - an old return, not a live submission"])
        if cand.date_state == "invalid":
            return _vetoed(portal, cand, ["ack does not carry a valid filing date - misread or not an ack"])

    lo, hi = max(0, cand.line - WINDOW_LINES), cand.line + WINDOW_LINES + 1
    window_lines = list(lines[lo:hi])
    window = "\n".join(window_lines)

    if _FAILURE.search(window):
        vetoes.append("failure wording next to the identifier")
    if _DRAFT.search(window):
        vetoes.append("draft / saved wording next to the identifier")
    success_hit = _SUCCESS.search(window)
    if success_hit and _FUTURE.search(window[max(0, success_hit.start() - 60): success_hit.end() + 60]):
        vetoes.append("wording describes what WILL happen, not what has")
        success_hit = None
    if vetoes:
        return _vetoed(portal, cand, vetoes)

    score = 40
    reasons = [f"{'ack' if cand.kind == 'itr_ack' else 'ARN'} next to its label"]
    if cand.kind == "itr_ack":
        score += 25
        reasons.append(f"ack carries today's date ({cand.ack_date.isoformat()})")
    if success_hit:
        score += 30
        reasons.append("success wording: " + re.sub(r"\s+", " ", success_hit.group(0).lower()))

    form = extract_filing_type(window) or (_GST_FORM.search(window).group(1).upper().replace(" ", "-") if _GST_FORM.search(window) else None)
    if form:
        score += 10
        reasons.append("form on screen: " + form)
    period = None
    if portal == "Income Tax":
        period = extract_assessment_year(window)
    else:
        tp, fy = extract_gst_tax_period(window), extract_gst_fy(window)
        period = format_gst_period_label(tp, fy) if (tp or fy) else None
    if period:
        score += 5
        reasons.append("period on screen: " + period)

    in_green = False
    if green_boxes:
        targets = _word_boxes_for(words, cand.identifier)
        if success_hit:
            first = re.sub(r"[^a-z]", "", success_hit.group(0).lower().split()[0])
            targets += [w for w in words if re.sub(r"[^a-z]", "", str(w.get("text", "")).lower()) == first]
        for w in targets:
            cx = float(w.get("x", 0)) + float(w.get("width", 0)) / 2
            cy = float(w.get("y", 0)) + float(w.get("height", 0)) / 2
            if any(b.contains(cx, cy) for b in green_boxes):
                in_green = True
                break
    if in_green:
        score += 15
        reasons.append("inside a green success box")

    status = classify_verification_status(window)
    if portal == "GST Portal":
        from .vsdc_assembler import get_status_rank
        if get_status_rank(status) < 2:
            status = "Filed"
    else:
        from .vsdc_assembler import get_status_rank
        if get_status_rank(status) < 2:
            status = "Filing Submitted"

    # A submission needs the words as well as the number - a labelled ack on a list page
    # with a valid date is at most a PROBABLE.
    if not success_hit:
        tier = "probable" if score >= PROBABLE_SCORE else "vetoed"
        if tier == "vetoed":
            return _vetoed(portal, cand, ["no success wording"])
    else:
        tier = "certain" if score >= CERTAIN_SCORE else ("probable" if score >= PROBABLE_SCORE else "vetoed")
        if tier == "vetoed":
            return _vetoed(portal, cand, ["evidence too weak"])
    if not cand.usual_shape and tier == "certain":
        tier = "probable"
        reasons.append("ARN does not have the usual two-letter + digits shape - shown for a human check, not saved")

    return Detection(
        portal=portal, kind=cand.kind, identifier=cand.identifier, status=status, score=score, tier=tier,
        form=form or known_form, period=period or known_period,
        filing_date=cand.ack_date.isoformat() if cand.ack_date else None,
        reasons=reasons, in_green_box=in_green, has_success_wording=bool(success_hit),
        usual_shape=cand.usual_shape,
    )


def _vetoed(portal: str, cand: Candidate, vetoes: List[str]) -> Detection:
    return Detection(portal=portal, kind=cand.kind, identifier=cand.identifier, status="", score=0,
                     tier="vetoed", vetoes=vetoes)


# ── Per-window scanner: when to look, and when to believe ────────────────────────
@dataclass
class ScanOutcome:
    scanned: bool
    reason: str = ""
    detection: Optional[Detection] = None
    promoted_by_agreement: bool = False


class Vsdc247Scanner:
    """
    One per browser window. Decides cheaply whether the frame is worth an OCR pass,
    runs it, and applies the temporal-agreement rule before a moderate-confidence
    candidate is believed.
    """

    def __init__(self) -> None:
        self._last_hash: Optional[int] = None
        self._last_scan_ts: float = 0.0
        self._last_green: int = 0
        self._seen_first_frame = False
        self._pending: Dict[str, int] = {}     # identifier -> consecutive sightings
        self._rescans_left: int = 0
        self.recent: Deque[Tuple[float, str]] = deque(maxlen=8)

    @staticmethod
    def _frame_hash(img: Image.Image) -> int:
        return hash(img.convert("RGB").resize((32, 32)).tobytes())

    def observe(self, img: Image.Image) -> None:
        """
        Notes a frame as SEEN without reading it - used when the crosshair pipeline has
        just handled this very screen. Without it the next tick would treat that old
        change as new and spend an OCR pass re-reading a page already dealt with.
        """
        if img is None:
            return
        self._seen_first_frame = True
        self._last_hash = self._frame_hash(img)
        self._last_green = len(find_green_boxes(img))

    def scan(self, img: Image.Image, ocr: Any, portal: str, now: float,
             today: Optional[date] = None, known_form: Optional[str] = None,
             known_period: Optional[str] = None) -> ScanOutcome:
        if img is None:
            return ScanOutcome(False, "no frame")

        h = self._frame_hash(img)
        boxes = find_green_boxes(img)
        green_appeared = len(boxes) > self._last_green
        changed = h != self._last_hash
        first_frame = not self._seen_first_frame
        self._seen_first_frame = True
        self._last_hash = h
        self._last_green = len(boxes)

        interval_ok = (now - self._last_scan_ts) >= MIN_SCAN_INTERVAL_SEC
        if green_appeared:
            reason = "green success box appeared"
        elif first_frame:
            # The first frame this window is ever seen gets ONE read. Skipping it as a mere
            # baseline would miss a confirmation that is already on screen when VSDC first
            # looks (app started on it, or the window was opened straight onto it) - a static
            # page never changes again, so nothing would ever trigger a read. One OCR pass per
            # window lifetime; the ack-date rule keeps an old confirmation from being believed.
            reason = "first sight of this window"
        elif changed and interval_ok:
            reason = "screen changed"
        elif self._rescans_left > 0 and interval_ok:
            reason = "re-reading a candidate for agreement"
            self._rescans_left -= 1
        else:
            return ScanOutcome(False, "static")

        self._last_scan_ts = now
        res = ocr.scan_image(img, region_type="full")
        text = res.get("text", "") or ""
        lines = res.get("lines", []) or []
        words = res.get("words", []) or []
        det = analyze_frame(text, lines, words, boxes, portal, today=today,
                            known_form=known_form, known_period=known_period)
        if det is None or det.tier == "vetoed":
            self._pending.clear()
            return ScanOutcome(True, reason, det)

        self.recent.append((now, det.identifier))
        seen = self._pending.get(det.identifier, 0) + 1
        self._pending = {det.identifier: seen}
        promoted = False
        if det.tier == "probable":
            if seen >= 2 and det.has_success_wording and det.usual_shape:
                det.tier = "certain"
                det.reasons.append("read again on a later frame (agreement)")
                promoted = True
            else:
                # No success wording: agreement alone never makes a bare number believable.
                self._rescans_left = PENDING_RESCAN_LIMIT if (det.has_success_wording and det.usual_shape) else 0
        elif det.tier == "certain" and det.score < SINGLE_FRAME_SCORE and seen < 2:
            det.tier = "probable"
            self._rescans_left = PENDING_RESCAN_LIMIT
        return ScanOutcome(True, reason, det, promoted)
