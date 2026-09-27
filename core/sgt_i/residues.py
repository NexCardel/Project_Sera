"""
core/sgt_i/residues.py - tracking unclaimed containers/typed values (step 10)
==============================================================================
Blueprint 14.4 step 10. Every page leaves a residue: containers and typed values
that no spec claimed. Only counts and shapes are kept (privacy rule 1, 14.5).

A ResiduesComponent identifies:
1. Containers with specific domain types (PAN, ARN, GSTIN, etc.) - known to be important
2. Which ones the Core's specs claimed (profile, datasets, current)
3. Counts the unclaimed ones by masked shape

The health report then says, for example: "GST confirmation pages: ARN-shaped value
seen 4×, claimed 0×" (or more generally, shape-to-count mapping per page kind).

Pure functions over page text and PageResult.
"""

import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .pairs import mask_shape

# Patterns for specific domain types that are important to track
# Format: (name, regex_pattern) - these are the types we monitor for unclaimed residues
_DOMAIN_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    # PAN: 10-char alphanumeric (ABCPD1234E style)
    ("PAN", re.compile(r"(?<![0-9A-Za-z])([A-Z]{5}[0-9]{4}[A-Z]{1})(?![0-9A-Za-z])", re.IGNORECASE)),
    # ARN: 15-digit (may have OCR confusion with O/0, I/1, S/5, B/8)
    ("ARN", re.compile(r"(?<![0-9A-Za-z])([0-9OoIlSB]{15})(?![0-9A-Za-z])")),
    # GSTIN: 15-char code (2 digits, 10-digit PAN-like, 3 entity code, 1 char, 1 digit)
    ("GSTIN", re.compile(r"(?<![0-9A-Za-z])([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[0-9]{1}[Z]{1}[0-9]{1})(?![0-9A-Za-z])", re.IGNORECASE)),
    # Aadhaar: 12 digits, often formatted with spaces
    ("Aadhaar", re.compile(r"(?<![0-9])(\d{4}\s?\d{4}\s?\d{4})(?![0-9])")),
)


@dataclass(frozen=True)
class ShapedValue:
    """A found value with its type and masked shape, never the value itself."""
    value_type: str      # PAN, ARN, GSTIN, Aadhaar, etc.
    shape: str           # masked: letters -> A, digits -> 9


def find_shaped_values(text: str) -> Dict[str, int]:
    """Find all domain-typed values in text, return {shape: count}."""
    shaped: Dict[str, int] = {}
    for type_name, pattern in _DOMAIN_PATTERNS:
        for match in pattern.finditer(text or ""):
            value = match.group(1)
            shape = mask_shape(value)
            key = f"{type_name}:{shape}"
            shaped[key] = shaped.get(key, 0) + 1
    return shaped


def extract_claimed_shapes(result: Dict) -> Dict[str, int]:
    """Extract shapes of values claimed by Core specs from PageResult.as_dict()."""
    claimed: Dict[str, int] = {}
    if not isinstance(result, dict):
        return claimed

    # Check profile hits
    for field, hit in (result.get("profile") or {}).items():
        if isinstance(hit, dict) and "value" in hit:
            value = str(hit.get("value", ""))
            for type_name, pattern in _DOMAIN_PATTERNS:
                if pattern.search(value):
                    shape = mask_shape(value)
                    key = f"{type_name}:{shape}"
                    claimed[key] = claimed.get(key, 0) + 1
                    break

    # Check current hits
    for field, hit in (result.get("current") or {}).items():
        if isinstance(hit, dict) and "value" in hit:
            value = str(hit.get("value", ""))
            for type_name, pattern in _DOMAIN_PATTERNS:
                if pattern.search(value):
                    shape = mask_shape(value)
                    key = f"{type_name}:{shape}"
                    claimed[key] = claimed.get(key, 0) + 1
                    break

    # Check datasets
    for dataset in (result.get("datasets") or []):
        if isinstance(dataset, dict):
            for field, hit in (dataset.get("values") or {}).items():
                if hit:
                    value = str(hit)
                    for type_name, pattern in _DOMAIN_PATTERNS:
                        if pattern.search(value):
                            shape = mask_shape(value)
                            key = f"{type_name}:{shape}"
                            claimed[key] = claimed.get(key, 0) + 1
                            break

    return claimed


def detect_page_kind(url: str, title: str, lines: Tuple[str, ...]) -> Optional[str]:
    """Guess the page kind from URL, title, or content. Returns a short label or None."""
    text = f"{url} {title} {' '.join(lines[:10])}".lower()

    # Simple heuristics for common page kinds
    if "gst" in text and ("confirmation" in text or "ack" in text or "acknowledgement" in text):
        return "gst_confirmation"
    if "gst" in text and ("return" in text or "filing" in text):
        return "gst_return"
    if "gst" in text and "dashboard" in text:
        return "gst_dashboard"

    if "itr" in text and ("filing" in text or "ack" in text or "acknowledgement" in text):
        return "itr_ack"
    if "itr" in text and ("return" in text or "form" in text):
        return "itr_return"
    if "itr" in text and ("profile" in text or "dashboard" in text):
        return "itr_profile"

    # Generic fallback based on content type
    if "form" in text or "input" in text:
        return "form_page"
    if "list" in text or "table" in text or "record" in text:
        return "list_page"
    if "confirmation" in text or "success" in text or "submitted" in text:
        return "confirmation_page"

    return "other"


class ResiduesComponent:
    """Step 10: track unclaimed domain-typed values per portal and page kind."""

    name = "residues"

    def observe(self, obs, ctx) -> None:
        """Find unclaimed values and report their counts and shapes."""
        if obs.source == "uia_event":
            return  # skip event observations

        # Find all typed values in the page
        text = " ".join(obs.lines)
        found = find_shaped_values(text)
        if not found:
            return

        # Find which ones were claimed by Core specs
        claimed = extract_claimed_shapes(obs.result)

        # Calculate residues: found but not claimed
        residues: Dict[str, int] = {}
        for shape_key, count in found.items():
            claimed_count = claimed.get(shape_key, 0)
            if claimed_count < count:
                residues[shape_key] = count - claimed_count

        if not residues:
            return

        # Determine page kind
        page_kind = detect_page_kind(obs.url, obs.title, obs.lines)
        if not page_kind:
            return

        # Enrich with counts and shapes of unclaimed values
        # Format: {page_kind: {shape: count}}
        ctx.enrich({
            "residues": {
                page_kind: residues
            }
        })
