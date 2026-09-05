"""Seventy-eight identical warnings are one fact, not seventy-eight.

A real `pipeline_*.log` for this corpus carries eighty WARNING lines in
five raw shapes. Printed one at a time they push everything else off the
screen, which is how a run that is repairing a source defect comes to look
like a run that is failing.

The fold is by *shape*: the document prefix comes off (it is already on the
volume's own line) and digit runs become `N`. Nothing is discarded — every
occurrence is counted, the integers inside the message are kept and summed,
and the DEBUG file handler still receives every record untouched. Only the
number of LINES is bounded, and the digest says how many shapes that hid.
"""

import re
from dataclasses import dataclass, field

# A document prefix, as every call site writes it: "LIV0038_reconciled: ".
_PREFIX = re.compile(r"^[A-Za-z][\w.-]*:\s+")
_DIGITS = re.compile(r"\d+")


def shape_of(message):
    """The message with its variable parts removed, as a fold key."""
    return _DIGITS.sub("N", _PREFIX.sub("", message.strip()))


@dataclass(slots=True)
class DigestLine:
    """One shape, and everything the fold had to keep about it."""

    shape: str
    occurrences: int = 0
    example: str = ""
    first_document: str = ""
    _documents: set = field(default_factory=set)
    totals: list = field(default_factory=list)

    @property
    def documents(self):
        return len(self._documents)


class WarningDigest:
    """Every WARNING+ record of a run, folded by shape.

    Records arrive here instead of going straight to the console. That is
    what absorbs the flood without touching a single call site.
    """

    def __init__(self):
        self._lines = {}

    def add(self, document, message):
        shape = shape_of(message)
        # The prefix is stripped before the numbers are read, or the
        # digits of the document name join the sum: two volumes whose
        # names carry a different count of digit runs fold to one shape
        # and shift every position of the total.
        # `.strip()` first, exactly as `shape_of` does: a message with
        # leading whitespace kept its document prefix, and its digits
        # rejoined the sum — the same bug one indentation away.
        body = _PREFIX.sub("", message.strip())
        line = self._lines.get(shape)
        if line is None:
            line = DigestLine(shape=shape, example=_PREFIX.sub("", message),
                              first_document=document)
            self._lines[shape] = line
        line.occurrences += 1
        line._documents.add(document)

        # The integers the message carried, summed position by position:
        # "78× page N: N duplicate ALTO id(s)" is only usable next to
        # "4 129 ids", and the fold is where that number would be lost.
        numbers = [int(found) for found in _DIGITS.findall(body)]
        while len(line.totals) < len(numbers):
            line.totals.append(0)
        for index, number in enumerate(numbers):
            line.totals[index] += number

    def _ordered(self):
        return sorted(self._lines.values(),
                      key=lambda line: (-line.occurrences, line.shape))

    def lines(self, limit=None):
        ordered = self._ordered()
        return tuple(ordered if limit is None else ordered[:limit])

    def elided(self, limit):
        """How many shapes the cap hid. Printed, never silent: the failure
        mode here is a warning whose variable part is alphabetic, which
        fragments into single-occurrence shapes and would otherwise just
        vanish."""
        return max(0, len(self._lines) - limit)
