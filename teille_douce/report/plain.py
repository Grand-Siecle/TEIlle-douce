"""The journal reporter: one line per thing that happened.

What a pipe, a log file and a CI runner get. It reads the same collector
the panel does, so the two cannot drift, and it carries no markup because
its output is as likely to be grepped as read.
"""

from .counts import PhaseState, render_phase_loss

_PREFIX = {
    "document": "->",
    "done": "OK",
    "failed": "FAILED",
    "archive-failed": "FAILED",
    "phase-lost": "LOST",
}


def journal_lines(events):
    """One plain line per event, in the order they happened."""
    lines = []
    for event in events:
        mark = _PREFIX.get(event.kind, "  ")
        text = f"{mark} {event.document}"
        if event.text:
            text += f": {event.text}"
        lines.append(text)
    return lines


def phase_line(name, state, done=0, total=None, unit="", reason=""):
    """A phase's outcome, in the journal's own shape.

    Through `render_phase_loss` and not a format string of its own: a
    second way of writing a loss is a second way of writing it wrongly.
    """
    body = render_phase_loss(state, count=done, total=total, unit=unit,
                             reason=reason)
    return f"   {name}: {body}"
