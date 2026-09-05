"""How a loss is allowed to be written.

CLAUDE.md says every phase reports what it lost, and that a counter left
at zero because a server died must not look like a document that had
nothing to process. A bare integer cannot tell those apart, so this module
refuses to produce one: a count carries its denominator or it raises.
"""

from enum import Enum


class PhaseState(Enum):
    """Where a phase is, which is what decides how its zero reads."""

    OFF = "off"                  # never asked for
    UNAVAILABLE = "unavailable"  # asked for, service was not there at start
    PENDING = "pending"          # asked for, not reached yet
    RUNNING = "running"
    DONE = "done"
    LOST = "lost"                # was running, the ground gave way


def _grouped(number):
    """Thin spaces every three digits: 16 999 reads faster than 16999."""
    return f"{number:,}".replace(",", " ")


def _singular(unit):
    """"containers" -> "container". Crude on purpose: the units are a
    closed set this module ships with, not user input."""
    return unit[:-3] + "y" if unit.endswith("ies") else unit.rstrip("s")


def render_count(count, denominator, unit):
    """`n of N unit`, or nothing at all.

    The denominator is not decoration. It is what gives a zero a meaning:
    "0 of 1 402 containers" is a phase that refused everything, "0 of 0"
    is a document that had nothing. Without it the two are one string.
    """
    if denominator is None:
        raise ValueError(
            f"a count of {unit} needs a denominator: a bare number cannot "
            f"tell a phase that lost everything from one that had nothing"
        )
    if count < 0 or denominator < 0:
        raise ValueError(f"negative count: {count} of {denominator} {unit}")
    if count > denominator:
        raise ValueError(
            f"count exceeds its denominator: {count} of {denominator} {unit}"
        )
    return f"{_grouped(count)} of {_grouped(denominator)} {unit}"


def render_phase_loss(state, count=None, total=None, unit=None, reason=None,
                      verb="enrich"):
    """One phase's line, chosen on its state rather than on a number.

    Choosing on the number is how "0" came to mean four different things
    in one column. Choosing on the state is what keeps LOST loud, and what
    lets a zero be read: off, refused, lost, or genuinely empty.
    """
    if state is PhaseState.PENDING:
        return "pending"
    if state is PhaseState.OFF:
        return f"off ({reason})" if reason else "off"
    if state is PhaseState.UNAVAILABLE:
        return f"off · {reason}" if reason else "off · unavailable"
    if state is PhaseState.LOST:
        if not reason:
            raise ValueError("a lost phase must give a reason")
        return f"LOST · {render_count(count or 0, total, unit)} — {reason}"

    count = count or 0
    measured = render_count(count, total, unit)
    if total == 0:
        # The honest empty, in words: a reader who sees only "0 of 0"
        # cannot tell it from a phase that was never reached.
        return f"no {_singular(unit)} to {verb} (0 found)"
    if not count:
        return f"nothing lost ({_grouped(0)} of {_grouped(total)})"
    return measured
