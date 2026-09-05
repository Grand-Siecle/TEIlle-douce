"""The live panel, as a pure function of (state, width, capabilities).

No console, no clock, no I/O. Six moments of a four-hour run are testable
in milliseconds because of it, and — more importantly — every number on
the panel is a measurement the caller handed in. Nothing here infers.

Lines are sequences of `Span`, and the plain text is the concatenation of
their `text`. Colour therefore cannot carry meaning on its own: strip
every style and the panel says exactly the same thing. That is
structural, not a convention someone has to remember.
"""

from dataclasses import dataclass, field
from typing import Optional

import re
import unicodedata

from .counts import PhaseState, _grouped, render_phase_loss

# Newlines, tabs, carriage returns and the rest: a clip by length keeps
# them, and one of them turns one rendered line into two on screen.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

MAX_WIDTH = 100
_MARGIN = 1

# This pipeline is named after taille-douce, copperplate engraving, and an
# engraver renders a value by the density of the hatching. So the bar is a
# hatch that fills rather than a line that grows: bitten through, half
# bitten, bare plate. It says what a progress bar says, in the vocabulary
# of the thing being made — and the bare plate is a glyph, not a space, so
# the length can be judged without reading the percentage.
TONES = {
    True: ("█", "▓", "░"),
    False: ("#", "+", "."),
}

# Colours taken from what a copperplate is made of, not from what a
# terminal happens to offer. No background is ever painted: a panel that
# paints its own ground fights the theme the reader chose.
PALETTE = {
    "plate": "#B4785A",      # burnished copper — what is done
    "bitten": "#8A6552",     # bister ink — work in flight
    "verdigris": "#4A7C74",  # oxidised copper — a service that answers
    "vermilion": "#C1440E",  # the one alarm
    "graphite": "#6B7280",   # rules, structure, secondary text
    "bold": "bold",
    "dim": "dim",
}

# Not a braille spinner, which is the default of the genre — and not the
# tone family either, which is the bar's: one glyph must not carry two
# meanings on the same line. A turning quadrant, for the press wheel.
_PULSE = {True: "◜◝◞◟", False: "/-\\|"}
_MARK = {True: ("✓", "✗", "·", "!"), False: ("v", "x", ".", "!")}

# One ASCII character per glyph, never two: the padding is computed on the
# composed line, so a substitution that changed its length would push
# every right-flush column out by one.
_ASCII = str.maketrans({
    "→": ">", "·": ".", "—": "-", "×": "x", "─": "-", "═": "=",
    "━": "=", "╌": "-", "┄": ".", "╾": ">", "✓": "v", "✗": "x", "◓": "*",
    "…": "~", "◒": "*", "█": "#", "▓": "+", "░": ".", "▒": "-",
    "◜": "/", "◝": "-", "◞": "\\", "◟": "|",
})


@dataclass(frozen=True, slots=True)
class Span:
    text: str
    style: str = ""


@dataclass(frozen=True, slots=True)
class Service:
    name: str
    up: Optional[bool] = None
    lost_at: Optional[str] = None


@dataclass(frozen=True, slots=True)
class PhaseLine:
    """One phase of the volume currently open."""

    name: str
    state: PhaseState
    done: int = 0
    total: Optional[int] = None
    unit: str = ""
    elapsed: Optional[int] = None
    rate: Optional[float] = None
    waiting: Optional[int] = None
    timeout: Optional[int] = None
    note: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class DocumentLine:
    name: str
    pages: int
    elapsed: int
    phases: tuple = ()


@dataclass(frozen=True, slots=True)
class PanelState:
    """Everything the panel is allowed to know, all of it measured."""

    input_dir: str
    output_dir: str
    elapsed: int
    eta: str
    services: tuple
    log_path: str
    pages_written: int
    pages_in_flight: int
    pages_total: int
    volumes_written: int
    volumes_failed: int
    volumes_to_go: int
    pages_per_second: float
    current: Optional[DocumentLine]
    source_lost: int
    withheld: int
    incidents: int
    incident_lines: tuple = ()
    digest: object = None


def as_text(lines):
    """The panel with every style removed — which is all of its meaning."""
    return ["".join(span.text for span in line) for line in lines]


def as_markup(lines):
    """The panel as Rich markup, for the only consumer that has a screen."""
    rendered = []
    for line in lines:
        rendered.append("".join(
            f"[{span.style}]{span.text}[/{span.style}]" if span.style
            else span.text
            for span in line))
    return rendered


def _pulse(tick, unicode_):
    """One frame of the plate being inked."""
    frames = _PULSE[unicode_]
    return frames[tick % len(frames)]


def _clock(seconds):
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def cells(text):
    """Terminal columns, not characters.

    A CJK glyph occupies two columns for one `len()`, so a width contract
    measured in characters lets the line run off the edge of the very
    terminals it was written for.
    """
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1
               for c in text)


def clip(text, room):
    """`text`, safe to put on one line of `room` columns.

    Control characters go first: clipping by length keeps a newline, so
    the panel renders one line and the terminal shows two — the second
    unindented and unclipped. httpx error messages are two lines, so this
    is not hypothetical.
    """
    text = _CONTROL.sub(" ", text)
    if room <= 0:
        return ""
    if cells(text) <= room:
        return text
    kept, used = [], 0
    for char in text:
        width = cells(char)
        if used + width > room - 1:
            break
        kept.append(char)
        used += width
    return "".join(kept) + "…"


def _plural(count, noun):
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _pad(left, right, room):
    """Left flush, right flush, one line, never wider than the room.

    Measured in cells throughout, and clipped rather than sliced: a
    negative room used to make `[:room]` count from the END, so the
    function returned three characters for a budget of minus two.
    """
    left, right = _CONTROL.sub(" ", left), _CONTROL.sub(" ", right)
    gap = room - cells(left) - cells(right)
    if gap >= 1:
        return left + " " * gap + right
    return clip(left + " " + right, room)


def _bar(state, width, unicode_):
    """Three measured zones, in proportion, filling exactly `width`."""
    done_glyph, flight_glyph, void_glyph = TONES[unicode_]
    total = max(state.pages_total, 1)
    written = min(state.pages_written, total)
    in_flight = min(state.pages_in_flight, total - written)

    done_width = round(width * written / total)
    flight_width = round(width * in_flight / total)
    if written and not done_width:
        done_width = 1
    if in_flight and not flight_width:
        flight_width = 1
    done_width = min(done_width, width)
    flight_width = min(flight_width, width - done_width)
    void_width = width - done_width - flight_width
    return [Span(done_glyph * done_width, "plate"),
            Span(flight_glyph * flight_width, "bitten"),
            Span(void_glyph * void_width, "graphite")]


def _services(state, room, unicode_):
    """The service banner, and the log it points at.

    Each service keeps its own style, so they are separate spans; the
    separator is a span of its own rather than trailing spaces, or the
    padding is computed on a string shorter than the one emitted.
    """
    ok, bad, idle, _ = _MARK[unicode_]
    labels = []
    for service in state.services:
        if service.up:
            labels.append((f"{ok} {service.name}", "verdigris"))
        elif service.up is None:
            # Nobody asked for it. Painting it red says the machine is
            # broken when the operator simply did not want the phase —
            # the same four meanings of a zero, on the banner.
            labels.append((f"{idle} {service.name} off", "graphite"))
        else:
            lost = f" LOST {service.lost_at}" if service.lost_at else " DOWN"
            labels.append((f"{bad} {service.name}{lost}", "vermilion"))

    # The log name shrinks with the panel: at fifty-six columns a
    # twenty-two-character tail is a third of the line.
    right = f"log {_short_log(state.log_path, room=max(8, room // 3))}"

    def fill(reserve):
        budget = room - cells(right) - 2 - reserve
        kept, used, dropped = [], 0, 0
        # A dead service first: `✗ NAME LOST hh:mm` is the longest label
        # by construction, so a first-fit pass dropped exactly the one
        # the banner exists to show and kept a service nobody asked for.
        ordered = sorted(labels, key=lambda pair: pair[1] != "vermilion")
        for text, style in ordered:
            width = cells(text) + (3 if kept else 0)
            if used + width > budget:
                dropped += 1
                continue
            if kept:
                kept.append(Span("   "))
                used += 3
            kept.append(Span(text, style))
            used += cells(text)
        return kept, used, dropped

    # Two passes: the note has to be paid for before the services are
    # chosen, or appending it afterwards pushes the line past the edge.
    kept, used, dropped = fill(0)
    if dropped:
        note = f" +{dropped} more"
        kept, used, dropped = fill(cells(note))
        if dropped:
            # Said, not silent. Truncating mid-name produced "NER
            # localog", and dropping a service outright removed it from
            # the banner that exists to show one dying.
            note = f" +{dropped} more"
            kept.append(Span(note, "graphite"))
            used += cells(note)

    gap = max(1, room - 1 - used - cells(right))
    return [Span(" "), *kept, Span(" " * gap), Span(right, "graphite")]


def _short_log(name, room=22):
    """Enough of the log name to find it, cut where it means something.

    Cutting on a character count lands mid-number — `…0260903_183307.log`
    costs the reader more than it saves — so the tail starts at the
    nearest separator.
    """
    if len(name) <= room:
        return name
    tail = name[-room:]
    boundary = tail.find("_")
    return "…" + (tail[boundary:] if boundary != -1 else tail)


def _phase_bar(done, total, cells, unicode_):
    """The small bar on a running phase's line, where "blocked but alive"
    is actually read."""
    done_glyph, _, void_glyph = TONES[unicode_]
    filled = min(cells, round(cells * done / max(total, 1)))
    return done_glyph * filled + void_glyph * (cells - filled)


def _phase_span(phase, room, unicode_, tick=0):
    """One phase's line, and what it is allowed to claim."""
    ok, bad, idle, warn = _MARK[unicode_]
    if phase.state is PhaseState.PENDING:
        return [Span(f"   {phase.name:<12}", ""),
                Span(clip(f"{idle} pending", room - 15), "graphite")]

    if phase.state is PhaseState.LOST:
        body = render_phase_loss(PhaseState.LOST, phase.done, phase.total,
                                 phase.unit, phase.reason or "service lost")
        # Clipped like everything else: this is the longest line the
        # panel composes, and it is the one whose cause must be loud.
        return [Span(f"   {phase.name:<12}"),
                Span(clip(f"{bad} {body}", room - 15), "vermilion")]

    if phase.state is PhaseState.RUNNING:
        measured = f"{_grouped(phase.done)}/{_grouped(phase.total or 0)} {phase.unit}"
        if phase.waiting is not None:
            # Slow and hung look identical without this: the wait of the
            # request in flight, against the timeout it will give up at.
            trailing = f"waiting {phase.waiting}s/{phase.timeout}s"
        elif phase.rate is not None:
            trailing = f"{phase.rate}/s"
        else:
            trailing = ""
        left = f"   {phase.name:<12}{_pulse(tick, unicode_)} "
        if trailing:
            measured += f" · {trailing}"
        # The bar only when there is room for the numbers first: at sixty
        # columns "318/430 · waiting 47s/120s" is what the operator needs
        # and the drawing is what goes.
        spare = room - len(left) - len(measured) - 8
        if spare >= 12:
            left += _phase_bar(phase.done, phase.total or 0, 20, unicode_) + " "
        return [Span(clip(left + measured, room), "bitten")]

    # The caller says whether a finished phase is worth a warning mark.
    # This used to test the note against a literal taken from one sample
    # document, so every other volume got the wrong mark and that one got
    # a warning for no reason.
    mark = warn if phase.reason else ok
    left = f"   {phase.name:<12}{mark} {phase.note}" if phase.note else \
        f"   {phase.name:<12}{ok} done"
    return [Span(clip(left, room))]


def render_panel(state, width=92, height=None, unicode=True, color=True,
                 tick=0):
    """The whole panel, as a list of lines of spans."""
    room = min(width, MAX_WIDTH) - _MARGIN
    lines = []

    header_left = f" TEIlle-douce   {state.input_dir}/ → {state.output_dir}/"
    # No estimate until a volume has finished: "eta " with nothing after
    # it reads as a broken template, and a made-up figure would be worse.
    header_right = f"elapsed {_clock(state.elapsed)}"
    if state.eta:
        header_right += f"   eta {state.eta}"
    lines.append([Span(_pad(header_left, header_right, room))])
    lines.append(_services(state, room, unicode))
    lines.append([Span("─" * room if unicode else "-" * room, "graphite")])

    pages = (f"{_grouped(state.pages_written)}/{_grouped(state.pages_total)} "
             f"pages")
    percent = f"{round(100 * state.pages_written / max(state.pages_total, 1))}%"
    # `bar_cells`, not `cells`: the module function of that name measures
    # a string in columns, and shadowing it here made every line that
    # clips raise TypeError.
    bar_cells = max(4, room - len(pages) - len(percent) - 6)
    lines.append([Span(" "), *_bar(state, bar_cells, unicode),
                  Span("  " + _pad(pages, percent, room - bar_cells - 3))])

    # Three facts are three columns, not a string joined with middle
    # dots: a list pretending not to be a table is the commonest tell of
    # an interface nobody laid out.
    counts = (f" {state.volumes_written:>4} written"
              f"{state.volumes_failed:>8} failed"
              f"{state.volumes_to_go:>8} to go")
    lines.append([Span(_pad(counts, f"{state.pages_per_second} pages/s", room))])
    lines.append([Span("")])

    if state.current is not None:
        head = f" {_pulse(tick, unicode)} {state.current.name}"
        tail = f"{_grouped(state.current.pages)} pages   {_clock(state.current.elapsed)}"
        lines.append([Span(_pad(head, tail, room), "bold")])
        for phase in state.current.phases:
            spans = _phase_span(phase, room, unicode, tick)
            if phase.elapsed is not None:
                text_so_far = "".join(span.text for span in spans)
                spans.append(Span(_pad("", _clock(phase.elapsed),
                                       room - len(text_so_far))))
            lines.append(spans)
            if phase.state is PhaseState.LOST and phase.note:
                # The cause on its own line, under the phase it belongs
                # to. "(was up at start)" is the difference between a
                # misconfiguration and a service that died mid-run.
                lines.append([Span(clip(f"               {phase.note}", room),
                                   "vermilion")])

    # Composed span by span to keep the incident count its own colour,
    # so clipped span by span too: six-digit totals ran past the edge.
    totals, used = [], 0
    for text_, style in ((f" source {_grouped(state.source_lost)}    ", ""),
                         (f"withheld {_grouped(state.withheld)}    ", ""),
                         (f"incident {_grouped(state.incidents)}",
                          "vermilion" if state.incidents else "")):
        piece = clip(text_, room - used)
        if not piece:
            break
        totals.append(Span(piece, style))
        used += cells(piece)
    lines.append(totals)

    incidents_at = len(lines)
    for incident in state.incident_lines:
        lines.append([Span(clip(" " + incident, room), "vermilion")])
    incidents_end = len(lines)

    digest_lines = state.digest.lines(limit=3) if state.digest else ()
    if digest_lines:
        label = " repeated warnings "
        rule = ("─" if unicode else "-") * max(0, room - len(label))
        lines.append([Span(label), Span(rule, "dim")])
        for entry in digest_lines:
            # No unit on the integers: the fold cannot know what they
            # counted, and "8 ids" over a warning about worker counts is
            # a confident wrong answer.
            # Only when something was actually summed: on a single
            # occurrence the number is already in the shape, and showing
            # it again as a total invites reading an identifier as a
            # count.
            summed = entry.totals and entry.occurrences > 1
            right = (_plural(entry.documents, "volume")
                     + (f"  {_grouped(entry.totals[-1])}" if summed else ""))
            # The counts are why the line exists, so a shape too long to
            # fit gives up its own prose rather than its numbers. The
            # verbatim message is in the log either way.
            head = f"    {entry.occurrences}×  "
            shape = entry.shape[:max(0, room - len(head) - len(right) - 2)]
            lines.append([Span(_pad(head + shape, right, room), "graphite")])
        hidden = state.digest.elided(limit=3)
        if hidden:
            lines.append([Span(f"    +{hidden} more shapes", "graphite")])

    lines.append([Span("─" * room if unicode else "-" * room, "graphite")])
    lines.append([Span(clip(
        f" ctrl-c  abandon this volume, keep the "
        f"{state.volumes_written} already written", room), "graphite")])

    # Trimmed last, against the height the terminal actually has: thirty
    # volumes with both services dead is sixty incident lines, and Rich's
    # Live ellipsises from the BOTTOM — so the bar, the totals and the
    # ctrl-c foot would be what disappears. The incidents are the part
    # that can be cut, because the summary carries all of them.
    over = len(lines) - height if height else 0
    if over > 0 and state.incident_lines:
        # Only the incidents can be cut, because the summary carries all
        # of them — and only when there are some: the trim used to add a
        # " +0 more" line to a clean panel, which made it one line TALLER
        # than the height it was given, on every frame of every run at
        # the documented minimum window.
        #
        # The note costs a line of its own, so it is paid for here.
        shown = max(0, len(state.incident_lines) - over - 1)
        hidden = len(state.incident_lines) - shown
        replacement = lines[incidents_at:incidents_at + shown]
        if hidden:
            replacement.append(
                [Span(f" +{hidden} more incidents, all of them in the summary",
                      "vermilion")])
        lines[incidents_at:incidents_end] = replacement

    if not unicode:
        # One pass at the end rather than a glyph table threaded through
        # every composition: it is the only way the ASCII panel is
        # guaranteed to have the same lines as the Unicode one.
        lines = [[Span(span.text.translate(_ASCII), span.style)
                  for span in line] for line in lines]
    if not color:
        lines = [[Span(span.text) for span in line] for line in lines]
    return lines
