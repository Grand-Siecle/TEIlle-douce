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

from .counts import PhaseState, _grouped, render_phase_loss

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


def _pad(left, right, room):
    """Left flush, right flush, one line, never wider than the room."""
    gap = room - len(left) - len(right)
    return left + " " * gap + right if gap >= 1 else (left + " " + right)[:room]


def _bar(state, cells, unicode_):
    """Three measured zones, in proportion, filling exactly `cells`."""
    done_glyph, flight_glyph, void_glyph = TONES[unicode_]
    total = max(state.pages_total, 1)
    written = min(state.pages_written, total)
    in_flight = min(state.pages_in_flight, total - written)

    done_cells = round(cells * written / total)
    flight_cells = round(cells * in_flight / total)
    if written and not done_cells:
        done_cells = 1
    if in_flight and not flight_cells:
        flight_cells = 1
    done_cells = min(done_cells, cells)
    flight_cells = min(flight_cells, cells - done_cells)
    void_cells = cells - done_cells - flight_cells
    return [Span(done_glyph * done_cells, "plate"),
            Span(flight_glyph * flight_cells, "bitten"),
            Span(void_glyph * void_cells, "graphite")]


def _services(state, room, unicode_):
    ok, bad, _, _ = _MARK[unicode_]
    spans = [Span(" ")]
    for service in state.services:
        if service.up:
            spans.append(Span(f"{ok} {service.name}   ", "verdigris"))
        else:
            lost = f" LOST {service.lost_at}" if service.lost_at else " DOWN"
            spans.append(Span(f"{bad} {service.name}{lost}   ", "vermilion"))
    left = "".join(span.text for span in spans).rstrip()
    right = f"log {_short_log(state.log_path)}"
    spans = [Span(_pad(left, "", room - len(right))), Span(right, "graphite")]
    return spans


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
        return [Span(f"   {phase.name:<12}", ""), Span(f"{idle} pending", "graphite")]

    if phase.state is PhaseState.LOST:
        body = render_phase_loss(PhaseState.LOST, phase.done, phase.total,
                                 phase.unit, phase.reason or "service lost")
        return [Span(f"   {phase.name:<12}"), Span(f"{bad} {body}", "vermilion")]

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
        return [Span(left + measured, "bitten")]

    mark = warn if phase.note.startswith(("!", "754 pages read")) else ok
    left = f"   {phase.name:<12}{mark} {phase.note}" if phase.note else \
        f"   {phase.name:<12}{ok} done"
    return [Span(left)]


def render_panel(state, width=92, height=None, unicode=True, color=True,
                 tick=0):
    """The whole panel, as a list of lines of spans."""
    room = min(width, MAX_WIDTH) - _MARGIN
    lines = []

    header_left = f" TEIlle-douce   {state.input_dir}/ → {state.output_dir}/"
    header_right = f"elapsed {_clock(state.elapsed)}   eta {state.eta}"
    lines.append([Span(_pad(header_left, header_right, room))])
    lines.append(_services(state, room, unicode))
    lines.append([Span("─" * room if unicode else "-" * room, "graphite")])

    pages = (f"{_grouped(state.pages_written)}/{_grouped(state.pages_total)} "
             f"pages")
    percent = f"{round(100 * state.pages_written / max(state.pages_total, 1))}%"
    cells = max(4, room - len(pages) - len(percent) - 6)
    lines.append([Span(" "), *_bar(state, cells, unicode),
                  Span("  " + _pad(pages, percent, room - cells - 3))])

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
                lines.append([Span(f"               {phase.note}"[:room],
                                   "vermilion")])

    lines.append([
        Span(f" source {_grouped(state.source_lost)}    "),
        Span(f"withheld {_grouped(state.withheld)}    "),
        Span(f"incident {_grouped(state.incidents)}",
             "vermilion" if state.incidents else ""),
    ])

    for incident in state.incident_lines:
        lines.append([Span(" " + incident[:room - 1], "vermilion")])

    digest_lines = state.digest.lines(limit=3) if state.digest else ()
    if digest_lines:
        label = " repeated warnings "
        rule = ("─" if unicode else "-") * max(0, room - len(label))
        lines.append([Span(label), Span(rule, "dim")])
        for entry in digest_lines:
            left = f"    {entry.occurrences}×  {entry.shape}"
            right = (f"{entry.documents} volumes"
                     + (f" · {_grouped(entry.totals[-1])} ids"
                        if entry.totals else ""))
            lines.append([Span(_pad(left, right, room), "graphite")])
        hidden = state.digest.elided(limit=3)
        if hidden:
            lines.append([Span(f"    +{hidden} more shapes", "graphite")])

    lines.append([Span("─" * room if unicode else "-" * room, "graphite")])
    lines.append([Span(
        f" ctrl-c  abandon this volume, keep the "
        f"{state.volumes_written} already written", "graphite")])

    if not unicode:
        # One pass at the end rather than a glyph table threaded through
        # every composition: it is the only way the ASCII panel is
        # guaranteed to have the same lines as the Unicode one.
        lines = [[Span(span.text.translate(_ASCII), span.style)
                  for span in line] for line in lines]
    if not color:
        lines = [[Span(span.text) for span in line] for line in lines]
    return lines
