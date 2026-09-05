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

from .counts import (PhaseState, _grouped, render_count,
                     render_phase_loss)
# The text arithmetic — cells, clipping, two-column padding — is shared
# with the summary, which measured in `len()` and kept no control
# characters out. Two implementations of the same contract is two ways of
# breaking it.
from .text import cells, clip, pad, shorten_path

MAX_WIDTH = 100
_MARGIN = 1

# Header, services, rule, bar, counts, blank. The rows that say what run
# this is; the last-resort trim keeps them and cuts inward from there.
_HEAD_ROWS = 6

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


def _plural(count, noun):
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _pad(left, right, room):
    """Left flush, right flush, one line, never wider than the room.

    `text.pad`, which keeps the RIGHT column and shortens the left. Every
    caller here wants it that way round: on the right sit `elapsed
    1:12:40`, `47%`, `9.1 pages/s`, `754 pages   3:21`, `2 volumes` —
    short, fixed, and the news. On the left sit the elastic things.
    Clipping the composed line, which is what this did, took the right
    column off the panel entirely as soon as the left grew: an absolute
    `-o` path — the ordinary case — cost the top line its elapsed time
    and its estimate, on every frame.
    """
    return pad(left, right, room)


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
        # A dead service first, and never skipped: `✗ NAME LOST hh:mm` is
        # the longest label by construction, so ordering alone still let
        # a first-fit pass drop exactly the one the banner exists to show
        # and keep a service nobody asked for. A dead one that will not
        # fit whole is shortened instead — a truncated alarm still reads
        # as an alarm, an absent one does not.
        ordered = sorted(labels, key=lambda pair: pair[1] != "vermilion")
        for text, style in ordered:
            width = cells(text) + (3 if kept else 0)
            if used + width > budget:
                room_left = budget - used - (3 if kept else 0)
                if style == "vermilion" and room_left >= 6:
                    text = clip(text, room_left)
                    width = cells(text) + (3 if kept else 0)
                else:
                    dropped += 1
                    continue
            if kept:
                kept.append(Span("   "))
                used += 3
            kept.append(Span(text, style))
            used += cells(text)
        return kept, used, dropped

    # The note has to be paid for before the services are chosen, or
    # appending it afterwards pushes the line past the edge. Its width
    # depends on how many were dropped, and the reserve is what changes
    # that — so the two go round until the reserve covers the note it
    # produces. Two fixed passes reserved " +9 more" and then wrote
    # " +10 more", one cell over the edge. Each turn strictly widens the
    # reserve, so it settles.
    kept, used, dropped = fill(0)
    reserve = 0
    while dropped and cells(f" +{dropped} more") != reserve:
        reserve = cells(f" +{dropped} more")
        kept, used, dropped = fill(reserve)
    if dropped:
        # Said, not silent. Truncating mid-name produced "NER localog",
        # and dropping a service outright removed it from the banner that
        # exists to show one dying.
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
    if cells(name) <= room:
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


def _phase_span(phase, room, unicode_, tick=0, reserved=0):
    """One phase's line, and what it is allowed to claim.

    `reserved` is what the caller will put after it — the phase clock —
    so the line is composed against the room it will actually have.
    Composed against the full width and clipped afterwards, the clock
    itself was cut: `1:12:…` at eighty-three columns.
    """
    room -= reserved
    ok, bad, idle, warn = _MARK[unicode_]
    # In cells, not characters, and computed once: `{phase.name:<12}`
    # pads by `len`, which is the fault `_entry` was just fixed for one
    # module away. No production phase name is wide today; the next one
    # to be added must not have to know that.
    named = phase.name + " " * max(1, 13 - cells(phase.name))
    # Measured, not assumed. `room - 15` was right for a twelve-cell name
    # and the column is thirteen now, so every PENDING and LOST line ran
    # one cell past the edge — and where a clock follows, that one cell
    # was the room `reserved` had just set aside for it, which turned
    # `1:12:40` into `1:12:…` at every width. A budget written as a
    # literal is a budget that goes wrong the first time the thing it
    # counts changes size.
    # The head is clipped too. It was the one span on the panel that was
    # not, so a phase name wider than the terminal ran straight past the
    # edge, trailed its own column pad, and ate the room the clock had
    # just been promised — `1:12:…` again, moved from the narrow case to
    # the long-name one. `panel.py` says a name that wide must not have
    # to know it is unusual; this is what that costs.
    head = clip(f"   {named}", max(4, room - 4))
    body_room = room - cells(head)
    if phase.state is PhaseState.PENDING:
        return [Span(head, ""),
                Span(clip(f"{idle} pending", body_room), "graphite")]

    if phase.state is PhaseState.LOST:
        body = render_phase_loss(PhaseState.LOST, phase.done, phase.total,
                                 phase.unit, phase.reason or "service lost")
        # Clipped like everything else: this is the longest line the
        # panel composes, and it is the one whose cause must be loud.
        return [Span(head),
                Span(clip(f"{bad} {body}", body_room), "vermilion")]

    if phase.state is PhaseState.RUNNING:
        # A phase that does not know its denominator yet says so by
        # leaving it out. `total or 0` wrote `318/0 containers`, which is
        # the one sentence this whole package exists to make impossible —
        # and Rich's own indeterminate total is exactly `None`, so the
        # case arrives from a perfectly ordinary progress callback.
        if phase.total is None:
            # A phase with nothing to count yet says what it is doing.
            # "0 " is not a measurement, and a bare spinner beside a name
            # is the panel admitting it has nothing to report.
            measured = (f"{_grouped(phase.done)} {phase.unit}" if phase.done
                        else (phase.note or "working"))
        else:
            measured = f"{_grouped(phase.done)}/{_grouped(phase.total)} {phase.unit}"
        if phase.waiting is not None:
            # Slow and hung look identical without this: the wait of the
            # request in flight, against the timeout it will give up at.
            trailing = f"waiting {phase.waiting}s/{phase.timeout}s"
        elif phase.rate is not None:
            trailing = f"{phase.rate}/s"
        else:
            trailing = ""
        head = f"   {named}{_pulse(tick, unicode_)} "
        # No bar without a denominator: it would draw a proportion of an
        # unknown.
        bar = (_phase_bar(phase.done, phase.total, 20, unicode_) + " "
               if phase.total else "")
        aside = f" · {trailing}" if trailing else ""

        # A stated order of who gives way, tried longest first. The
        # drawing goes before the figures — at sixty columns
        # `318/430 · waiting 47s/120s` is what the operator needs and the
        # bar is what goes — and the figures never give way at all.
        # Composed and clipped instead, the bar pushed the line over and
        # the clip took `waiting 47s/120s` down to `waiting 47s/12…`: a
        # twelve-second timeout on a hundred-and-twenty-second one, on
        # the line whose entire job is telling slow from hung.
        for candidate in (head + bar + measured + aside,
                          head + measured + aside,
                          head + bar + measured,
                          head + measured):
            if cells(candidate) <= room:
                return [Span(candidate, "bitten")]
        return [Span(clip(head + measured, room), "bitten")]

    # The caller says whether a finished phase is worth a warning mark.
    # This used to test the note against a literal taken from one sample
    # document, so every other volume got the wrong mark and that one got
    # a warning for no reason.
    mark = warn if phase.reason else ok
    if phase.note:
        left = f"   {named}{mark} {phase.note}"
    elif phase.total is not None:
        # The numbers, when the caller measured them. A bare "done" beside
        # a phase that read eight hundred and fifty pages throws away the
        # only thing the line had to say.
        left = (f"   {named}{ok} "
                f"{render_count(min(phase.done, phase.total), phase.total, phase.unit)}")
    else:
        left = f"   {named}{ok} done"
    return [Span(clip(left, room))]


def render_panel(state, width=92, height=None, unicode=True, color=True,
                 tick=0):
    """The whole panel, as a list of lines of spans."""
    room = min(width, MAX_WIDTH) - _MARGIN
    lines = []

    # Shortened keeping the leaf: a path clipped from the right loses the
    # only part that says which corpus this is, and `-o` is absolute far
    # more often than not. Half the elastic room each, so one very long
    # path cannot squeeze the other out.
    header_right = f"elapsed {_clock(state.elapsed)}"
    if state.eta:
        header_right += f"   eta {state.eta}"
    prefix = " TEIlle-douce   "
    for_paths = max(8, room - cells(prefix) - len("/ → /") - cells(header_right)
                    - 1)
    # The input is what the operator typed and is nearly always short;
    # the output is the one that runs long. So the input gets a floor and
    # the output gets everything it leaves.
    shown_in = shorten_path(state.input_dir, max(6, for_paths // 3))
    shown_out = shorten_path(state.output_dir,
                             max(6, for_paths - cells(shown_in)))
    header_left = f"{prefix}{shown_in}/ → {shown_out}/"
    # No estimate until a volume has finished: "eta " with nothing after
    # it reads as a broken template, and a made-up figure would be worse.
    lines.append([Span(_pad(header_left, header_right, room))])
    lines.append(_services(state, room, unicode))
    lines.append([Span("─" * room if unicode else "-" * room, "graphite")])

    pages = (f"{_grouped(state.pages_written)}/{_grouped(state.pages_total)} "
             f"pages")
    percent = f"{round(100 * state.pages_written / max(state.pages_total, 1))}%"
    # `bar_cells`, not `cells`: the module function of that name measures
    # a string in columns, and shadowing it here made every line that
    # clips raise TypeError.
    bar_cells = max(4, room - cells(pages) - cells(percent) - 6)
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
            clock = "" if phase.elapsed is None else _clock(phase.elapsed)
            spans = _phase_span(phase, room, unicode, tick,
                                reserved=cells(clock) + 1 if clock else 0)
            if clock:
                text_so_far = "".join(span.text for span in spans)
                spans.append(Span(_pad("", clock,
                                       room - cells(text_so_far))))
            lines.append(spans)
            if phase.state is PhaseState.LOST and phase.note:
                # The cause on its own line, under the phase it belongs
                # to. "(was up at start)" is the difference between a
                # misconfiguration and a service that died mid-run.
                # Indented to the column the mark above it starts at,
                # measured. Fifteen literal spaces was right when the
                # name column was twelve cells; it has been thirteen
                # since the round that widened it, and the cause sat one
                # short of the thing it explains. The third literal that
                # change invalidated, in the caller of the two that were
                # fixed.
                under = 3 + cells(phase.name + " " * max(1, 13 - cells(phase.name)))
                lines.append([Span(clip(f"{' ' * under}{phase.note}", room),
                                   "vermilion")])

    # Composed span by span to keep the incident count its own colour.
    # Clipped span by span, the row lost `incident` entirely below fifty
    # columns and cut it mid-number up to sixty-two: `incident 9 99…`,
    # on the row whose whole purpose is that a counter is not mistaken
    # for a document that had nothing to process. The separators give
    # way instead, and then the words — the numbers never.
    counts = ((f"source {_grouped(state.source_lost)}", ""),
              (f"withheld {_grouped(state.withheld)}", ""),
              (f"incident {_grouped(state.incidents)}",
               "vermilion" if state.incidents else ""))
    bare = ((f"src {_grouped(state.source_lost)}", ""),
            (f"wth {_grouped(state.withheld)}", ""),
            (f"inc {_grouped(state.incidents)}",
             "vermilion" if state.incidents else ""))
    for shown, gap in ((counts, "    "), (counts, " "), (bare, " ")):
        width_needed = sum(cells(t) for t, _ in shown) + len(gap) * 2 + 1
        if width_needed <= room:
            totals = [Span(" ")]
            for index, (text_, style) in enumerate(shown):
                if index:
                    totals.append(Span(gap))
                totals.append(Span(text_, style))
            break
    else:
        # Nothing fits whole. The incident count is the one that must
        # survive, so it is the one kept.
        totals = [Span(" "), Span(clip(bare[2][0], room - 1), bare[2][1])]
    lines.append(totals)

    # What is left for the two elastic blocks, once the closing rule and
    # the ctrl-c foot are paid for.
    #
    # Budgeted BEFORE they are composed, not trimmed after. The trim was
    # only ever willing to cut incident lines, so a run with no incidents
    # and the corpus' ordinary warning flood — eighty a volume in five
    # shapes, per CLAUDE.md — returned fifteen lines for a height of
    # twelve, and Rich's Live crops from the BOTTOM: the `+2 more
    # shapes`, the closing rule and the ctrl-c foot were what vanished.
    # Which is the outcome the trim exists to prevent, in the case it
    # never fired for.
    spare = (height - len(lines) - 2) if height else None

    # Incidents first: they are the block that needs a human. The note
    # costs a line of its own, so it is paid for out of the same budget.
    shown = state.incident_lines
    hidden = 0
    if spare is not None and len(shown) > max(0, spare):
        keep = max(0, spare - 1)
        shown, hidden = shown[:keep], len(shown) - keep
    for incident in shown:
        lines.append([Span(clip(" " + incident, room), "vermilion")])
    if hidden:
        lines.append(
            [Span(clip(f" +{hidden} more incidents, all of them in the summary",
                       room), "vermilion")])

    # Whatever the incidents left. A digest block is a label plus its
    # lines plus, when the fold hid shapes, a note — so it needs two
    # rows before it is worth starting at all.
    left = None if spare is None else spare - len(shown) - (1 if hidden else 0)
    limit = 3 if left is None else max(0, min(3, left - 2))
    digest_lines = state.digest.lines(limit=limit) if state.digest and limit else ()
    if digest_lines:
        label = " repeated warnings "
        rule = ("─" if unicode else "-") * max(0, room - len(label))
        lines.append([Span(label), Span(rule, "dim")])
        for entry in digest_lines:
            # How many times, and in how many volumes. Not the sum of
            # the integers inside: the fold cannot know whether they
            # were counts or identifiers, and "No metadata row for
            # 'LIV9002_reconciled'" across two volumes rendered 18 006 —
            # two identifiers added together and printed beside a volume
            # count with no unit to tell them apart. The one case where
            # the sum was worth having, duplicate ALTO ids, is a line of
            # the summary already, measured against its own denominator.
            right = _plural(entry.documents, "volume")
            # The counts are why the line exists, so a shape too long to
            # fit gives up its own prose rather than its numbers. The
            # verbatim message is in the log either way.
            head = f"    {entry.occurrences}×  "
            shape = clip(entry.shape,
                         max(0, room - cells(head) - cells(right) - 2))
            lines.append([Span(_pad(head + shape, right, room), "graphite")])
        elided = state.digest.elided(limit=limit)
        if elided:
            lines.append([Span(f"    +{elided} more shapes", "graphite")])

    lines.append([Span("─" * room if unicode else "-" * room, "graphite")])
    # What ctrl-c would actually do, which is not the same thing between
    # two volumes as it is inside one. "abandon this volume" over a
    # finished corpus offers to throw away something that is not there.
    kept = _grouped(state.volumes_written)
    offer = (f" ctrl-c  abandon this volume, keep the {kept} already written"
             if state.current is not None else
             f" ctrl-c  stop the run, keep the {kept} already written")
    lines.append([Span(clip(offer, room), "graphite")])

    # A contract, not a courtesy: the panel never returns more lines than
    # the height it was given. `select.py`'s floor makes this unreachable
    # on its own, but `--dashboard` in a ten-row window is a supported
    # way to be wrong, and Rich's Live crops from the BOTTOM — so without
    # this the closing rule and the ctrl-c foot are what disappears.
    if height and len(lines) > height:
        foot = min(2, height)
        head = min(_HEAD_ROWS, max(0, height - foot))
        middle = lines[head:len(lines) - foot][:max(0, height - head - foot)]
        lines = lines[:head] + middle + lines[len(lines) - foot:]

    if not unicode:
        # One pass at the end rather than a glyph table threaded through
        # every composition: it is the only way the ASCII panel is
        # guaranteed to have the same lines as the Unicode one.
        lines = [[Span(span.text.translate(_ASCII), span.style)
                  for span in line] for line in lines]
    if not color:
        lines = [[Span(span.text) for span in line] for line in lines]
    return lines
