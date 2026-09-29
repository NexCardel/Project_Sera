"""
core/scc/which.py - SCC-U step 5: which password worked
========================================================
Autofill-tweaks blueprint G.2 step 5. OutcomeReader (outcome.py) reports "worked" once per attempt;
this decides which row gets the credit, from the attempt's ledger of copies (labels only):

  exactly one distinct row copied since the last wrong-password message -> that row (SccCard.credit)
  several                                                               -> the card asks which
  none                                                                  -> the card asks, with
                                                                           "None - I typed my own"

A row the portal refused (x) in this attempt is never credited. When the card asks, "None - I typed
my own" is offered too (it saves nothing). A row staff already credited with "This one worked"
before the header was read is left as it is. No password is seen here: the ledger holds labels.
"""

from typing import Any, Callable, List, Optional

from .attempts import Attempt
from .scc_rules import RuleSet, get_rules


def pick_rows(copied: List[str], failed: List[str]) -> List[str]:
    """The distinct rows that may have logged in, in the order first copied (refused rows left out)."""
    out: List[str] = []
    for label in copied:
        if label not in failed and label not in out:
            out.append(label)
    return out


class WhichOne:
    def __init__(self, card: Any, copied_since_refusal: Callable[[Attempt], List[str]],
                 rules: Optional[Callable[[], RuleSet]] = None,
                 then: Optional[Callable[[Attempt, str, str], Any]] = None) -> None:
        self._card = card                            # SccCard: credit / ask_which / failed
        self._copied = copied_since_refusal          # OutcomeReader.copied_since_refusal
        self._rules = rules or get_rules
        self._then = then                            # the next on_outcome listener (step 7's counts)

    def on_outcome(self, att: Attempt, kind: str, detail: str) -> Optional[str]:
        """OutcomeReader's on_outcome. Returns, for tests: the credited label / "asked" / None."""
        got = None
        if kind == "worked":
            got = self._attribute(att)
        if self._then:
            self._then(att, kind, detail)
        return got

    def _attribute(self, att: Attempt) -> Optional[str]:
        if any(e.startswith("worked: ") or e == "typed own" for e in list(att.ledger)):
            return None                              # staff already said which
        rows = pick_rows(self._copied(att), self._card.failed(att.attempt_id))
        if len(rows) == 1 and self._card.credit(att, rows[0]):
            return rows[0]
        return "asked" if self._card.ask_which(att, rows, self._rules().message("ask_which")) else None
