"""
core/sgt_i/shapes.py - shape grammar induction with rule-of-three bounds
=========================================================================
Blueprint 14.4 step 3, shape grammar part (14.9: "shape - a value with letters as A and digits as
9"). Given many masked shapes from the *same container* (pairs.py's `ValuePair.shape`, collected
over many pages/sessions - the atlas's job, step 4), this module finds the one pattern most of
them share and proves, with a number, how safe it is to trust that pattern on a value nobody has
shown SGT-I yet.

* class string - pairs.py's `mask_shape()`: every letter -> "A", every digit -> "9", everything
                 else (spaces, punctuation) kept as-is. Re-exported here, not redefined.
* generalisation - a class string is first collapsed into runs of the same class ("AAAAA9999A"
                 -> five A's, four 9's, one A). Many shapes from one container generalise into a
                 single regex when they share the same *run structure* (the same classes in the
                 same order - punctuation must match exactly, since a class run's "class" for a
                 punctuation character is the character itself); a run's length becomes a fixed
                 count when every matching shape agrees, or a {min,max} range when it doesn't. A
                 shape with a different run structure is an *exception* - not folded into the
                 regex, just counted.
* confidence bound - the rule of three: seeing zero exceptions in N values bounds the true
                 exception rate below -ln(1-confidence)/N, about 3/N at the usual 95% confidence
                 (-ln(0.05) = 2.9957). One exception already disproves "every value fits this
                 shape", so the bound is only defined for zero.

Where this stops (14.4 step 3, "where maths stops"): a regex this module drafts is *structure*
proven by counting, never *meaning* - it does not know or claim the container is a PAN or a GSTIN.
Pure functions over shape strings: no UIA, no I/O, no state, and (privacy rule 1, 14.5) no value
ever passes through here - only mask_shape()'s output does.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from .pairs import mask_shape

__all__ = ["mask_shape", "ShapeGrammar", "induce_shape_grammar", "shape_grammar_confidence"]

_Run = Tuple[str, int]     # (class, run length): class is "A", "9", or a literal punctuation char


def _runs(shape: str) -> Tuple[_Run, ...]:
    """Collapse a masked shape into (class, length) runs, e.g. "AAAAA9999A" -> (("A",5),("9",4),
    ("A",1)). A punctuation/space character's "class" is the character itself, so two shapes only
    share a run's class when they share that exact character."""
    runs = []
    for ch in shape:
        cls = "A" if ch.isalpha() else "9" if ch.isdigit() else ch
        if runs and runs[-1][0] == cls:
            runs[-1][1] += 1
        else:
            runs.append([cls, 1])
    return tuple((cls, n) for cls, n in runs)


def _atom(cls: str) -> str:
    if cls == "A":
        return "[A-Za-z]"
    if cls == "9":
        return r"\d"
    return re.escape(cls)


def _quantified(cls: str, lo: int, hi: int) -> str:
    atom = _atom(cls)
    if lo == hi == 1:
        return atom
    if lo == hi:
        return f"{atom}{{{lo}}}"
    return f"{atom}{{{lo},{hi}}}"


@dataclass(frozen=True)
class ShapeGrammar:
    """The pattern most shapes from one container share, and how safe it is to trust it.

    pattern     - an anchored regex (^...$) matching every shape that shares the majority run
                  structure; fixed-length runs get an exact count, varying ones a {min,max} range.
    n           - how many shapes this was induced from.
    exceptions  - how many of those n did NOT share the majority run structure (not matched by
                  `pattern` even though they came from the same container).
    confidence  - the rule-of-three upper bound on the true exception rate, or None when
                  `exceptions` is not zero (a pattern already contradicted by a real value is not
                  "safe within a bound" - it is disproven).
    """
    pattern: str
    n: int
    exceptions: int
    confidence: Optional[float]


def shape_grammar_confidence(n: int, exceptions: int, confidence: float = 0.95) -> Optional[float]:
    """Rule of three: having observed `n` values with zero exceptions, the true exception rate is
    below -ln(1-confidence)/n at the given confidence (about 3/n at the usual 95%: -ln(0.05) =
    2.9957). Any observed exception already disproves "this pattern always holds", so the bound is
    only defined when `exceptions` is 0 - returns None otherwise, never a wrong number."""
    if exceptions:
        return None
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1 exclusive")
    return -math.log(1.0 - confidence) / n


def induce_shape_grammar(shapes: Sequence[str], confidence: float = 0.95) -> Optional[ShapeGrammar]:
    """Generalise many masked shapes from one container into a single regex (blueprint 14.4 step
    3). Picks the run structure the most shapes share, builds a pattern for it, and counts every
    shape with a different structure as an exception. Returns None for an empty `shapes`."""
    if not shapes:
        return None
    run_lists = [_runs(s) for s in shapes]
    skeletons = [tuple(cls for cls, _ in runs) for runs in run_lists]
    majority = Counter(skeletons).most_common(1)[0][0]
    matching = [runs for runs, sk in zip(run_lists, skeletons) if sk == majority]
    exceptions = len(shapes) - len(matching)

    parts = []
    for i, cls in enumerate(majority):
        lengths = [runs[i][1] for runs in matching]
        parts.append(_quantified(cls, min(lengths), max(lengths)))
    pattern = "^" + "".join(parts) + "$"

    return ShapeGrammar(
        pattern=pattern,
        n=len(shapes),
        exceptions=exceptions,
        confidence=shape_grammar_confidence(len(shapes), exceptions, confidence),
    )
