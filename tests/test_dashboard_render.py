# -----------------------------------------------------------
# The live panel, rendered as a pure function.
#
# No console, no clock, no terminal: (state, width, capabilities) in,
# lines out. That is what makes six moments of a four-hour run testable in
# milliseconds, and it is why the panel can be trusted — every number on
# it is a measurement the caller handed in, never something the renderer
# inferred.
#
# Run: venv/bin/python -m pytest tests/test_dashboard_render.py -q
# -----------------------------------------------------------
import pytest

from teille_douce.report.counts import PhaseState
from teille_douce.report.digest import WarningDigest
from teille_douce.report.panel import (DocumentLine, PanelState, PhaseLine,
                                       Service, as_markup, as_text,
                                       render_panel)


def state(**overrides):
    """A run in its cruising state: one volume open, three phases."""
    base = dict(
        input_dir="OCR", output_dir="tei_output",
        elapsed=2832, eta="~3h04 (median of 7)",
        services=(Service("PyHellen", up=True), Service("VieuxParler", up=True),
                  Service("NER local", up=True)),
        log_path="pipeline_20260903_183307.log",
        pages_written=6412, pages_in_flight=0, pages_total=16999,
        volumes_written=7, volumes_failed=1, volumes_to_go=19,
        pages_per_second=9.1,
        current=DocumentLine(
            name="LIV0038_t2_reconciled", pages=754, elapsed=291,
            phases=(
                PhaseLine("sourceDoc", PhaseState.DONE, done=754, total=754,
                          unit="pages", elapsed=52,
                          note="754 pages read · 3 unusable (41, 88, 89)"),
                PhaseLine("body+lang", PhaseState.DONE, done=754, total=754,
                          unit="pages", elapsed=37,
                          note="fra 691 · lat 48 · ita 12"),
                PhaseLine("enrich", PhaseState.RUNNING, done=318, total=430,
                          unit="containers", elapsed=198, rate=11.2),
                PhaseLine("modernize", PhaseState.PENDING),
                PhaseLine("NER", PhaseState.PENDING),
            )),
        source_lost=451, withheld=3218, incidents=0,
        incident_lines=(), digest=WarningDigest(),
    )
    base.update(overrides)
    return PanelState(**base)


def text(panel_state, width=92, **kwargs):
    return as_text(render_panel(panel_state, width=width, **kwargs))


# =============================================================================
# The run bar is three measurements, never a model
# =============================================================================

def test_the_bar_shows_written_in_flight_and_not_yet_read():
    """Three zones, each a measurement: pages of volumes already on disk,
    pages read in the volume still open — real work, at risk — and pages
    not read yet. A learned per-phase weighting would be the only number
    on the screen capable of being wrong by a factor of two."""
    lines = text(state(pages_written=6412, pages_in_flight=300))
    bar = next(line for line in lines if "6 412" in line)

    assert "█" in bar, "bitten through: pages already written"
    assert "▓" in bar, "half-bitten: pages read in the volume still open"
    assert "░" in bar, "bare plate: pages not read yet"


def test_the_empty_part_of_the_bar_is_a_glyph_and_not_a_space():
    """So the length of the bar can be judged without reading the
    percentage."""
    bar = next(line for line in text(state()) if "6 412" in line)
    drawn = bar[bar.index("█"):bar.rindex("░") + 1]

    assert " " not in drawn


def test_the_bar_never_claims_more_than_was_measured():
    """`pages_in_flight` is work at risk, not work done: it may not be
    counted as written, and the two together may not exceed the total."""
    lines = text(state(pages_written=16999, pages_in_flight=0))
    bar = next(line for line in lines if "16 999" in line)

    assert bar.count("░") == 0


def test_the_percentage_is_of_pages_and_not_of_volumes():
    """Volumes differ by two orders of magnitude in length; a volume
    percentage on a corpus like this one is a number that lies."""
    lines = text(state(pages_written=6412, pages_total=16999,
                       volumes_written=7, volumes_to_go=19))

    assert any("38%" in line for line in lines)


# =============================================================================
# Blocked but alive
# =============================================================================

def test_a_phase_waiting_on_a_service_shows_the_wait_against_its_timeout():
    """The dominant state of a four-hour run is not "advancing" but
    "waiting on an API", and a frozen counter beside a spinning spinner
    does not tell slow from hung."""
    waiting = state(current=DocumentLine(
        name="D1", pages=754, elapsed=245,
        phases=(PhaseLine("enrich", PhaseState.RUNNING, done=318, total=430,
                          unit="containers", waiting=47, timeout=120),)))

    assert any("waiting 47s/120s" in line for line in text(waiting))


def test_a_phase_that_is_not_waiting_says_its_rate_instead():
    assert any("11.2/s" in line for line in text(state()))


# =============================================================================
# A service dies: it has to show up in more than one place
# =============================================================================

def test_a_lost_service_is_marked_in_the_banner():
    """The run lasts hours and the operator was looking elsewhere, so the
    failure surfaces in four independent places. This is the first."""
    dead = state(services=(Service("PyHellen", up=False, lost_at="19:41"),
                           Service("VieuxParler", up=True)))

    assert any("PyHellen" in line and "LOST 19:41" in line
               for line in text(dead))


def test_a_lost_phase_says_what_it_refused_and_why():
    """Second place. Every counter is at zero and the denominator is the
    point: 1 402 containers went unannotated, which is not a document
    that had none."""
    dead = state(current=DocumentLine(
        name="LIV0039a", pages=754, elapsed=22,
        phases=(PhaseLine("enrich", PhaseState.LOST, done=0, total=1402,
                          unit="containers",
                          note="PyHellen stopped answering 19:41 (was up at start)",
                          reason="PyHellen down since 19:41"),)))
    lines = text(dead)

    assert any("LOST · 0 of 1 402 containers" in line for line in lines)
    assert any("was up at start" in line for line in lines)


def test_an_incident_stays_on_the_panel_until_the_end():
    """Third place. It scrolled past while nobody was watching."""
    dead = state(incidents=3, incident_lines=(
        "I1 LIV0039a  enrich.service-lost   0 of 1 402 containers — PyHellen down since 19:41",
        "I2 LIV0039a  enrich.breaker-skipped  612 containers, from page f88 onward",
    ))
    lines = text(dead)

    assert any("I1 LIV0039a" in line for line in lines)
    assert any("I2 LIV0039a" in line for line in lines)


# =============================================================================
# The three blocks are totals here, and only totals
# =============================================================================

def test_the_three_blocks_show_as_totals_while_the_run_is_live():
    """3 218 withheld readings are noise while a run is going; two
    incidents are the reason to press Ctrl-C. The detail belongs in the
    summary."""
    lines = text(state())
    totals = next(line for line in lines if "withheld" in line)

    assert "source 451" in totals
    assert "withheld 3 218" in totals
    assert "incident 0" in totals


def test_the_totals_print_at_zero_too():
    lines = text(state(source_lost=0, withheld=0, incidents=0))

    assert any("incident 0" in line for line in lines)


# =============================================================================
# Repeated warnings
# =============================================================================

def test_repeated_warnings_are_folded_into_their_own_zone():
    digest = WarningDigest()
    for index in range(78):
        digest.add(f"LIV003{index % 4}",
                   f"page {index}: 53 duplicate ALTO id(s) disambiguated")

    lines = text(state(digest=digest))

    assert any("repeated warnings" in line for line in lines)
    assert any("78×" in line for line in lines)


def test_no_warning_zone_when_there_are_no_warnings():
    """A heading over nothing is worse than the silence it replaces."""
    assert not any("repeated warnings" in line for line in text(state()))


# =============================================================================
# The foot says how to stop, always
# =============================================================================

def test_the_foot_says_what_ctrl_c_will_and_will_not_cost():
    """The circuit breaker was dropped because the panel makes the
    decision the operator's; that only works if the panel says so."""
    lines = text(state(volumes_written=7))
    foot = lines[-1]

    assert "ctrl-c" in foot
    assert "7" in foot


# =============================================================================
# Shape, and the rule that colour may never be the only carrier of meaning
# =============================================================================

@pytest.mark.parametrize("width", [60, 80, 92, 100, 120])
def test_no_line_exceeds_the_width(width):
    for line in text(state(), width=width):
        assert len(line) <= min(width, 100), repr(line)


@pytest.mark.parametrize("width", [60, 80, 92, 100])
def test_the_ascii_rendering_has_the_same_lines(width):
    """A terminal that cannot draw box characters still gets a panel, and
    the same one."""
    unicode_lines = text(state(), width=width)
    ascii_lines = text(state(), width=width, unicode=False)

    assert len(unicode_lines) == len(ascii_lines)
    for line in ascii_lines:
        assert line.isascii(), repr(line)


def test_no_color_parity():
    """Character for character, the coloured and uncoloured renderings
    carry the same content. A cheap assertion that structurally forbids
    colour from becoming the only thing that says a phase died."""
    coloured = render_panel(state(), width=92)
    plain = render_panel(state(), width=92, color=False)

    assert as_text(coloured) == as_text(plain)
    assert as_markup(plain) == as_text(plain)


def test_a_lost_phase_is_legible_with_no_colour_at_all():
    dead = state(current=DocumentLine(
        name="D", pages=1, elapsed=1,
        phases=(PhaseLine("enrich", PhaseState.LOST, done=0, total=1402,
                          unit="containers", reason="PyHellen down"),)))

    assert any("LOST" in line for line in text(dead))


# =============================================================================
# Details that decide whether the panel is readable
# =============================================================================

def test_the_log_name_is_shortened_at_a_boundary_not_mid_number():
    """`log …0260903_183307.log` is a truncation that costs the reader
    more than it saves. The tail has to start somewhere meaningful."""
    from teille_douce.report.panel import _short_log

    # The property, not one spelling of it: whatever is elided, what is
    # left starts at a separator, so no number is shown half-cut.
    for name in ("pipeline_20260903_183307.log",
                 "a-very-long-prefix_20260903_183307.log",
                 "short.log"):
        shortened = _short_log(name)
        assert name.endswith(shortened.lstrip("…"))
        if shortened.startswith("…"):
            assert shortened[1] == "_", shortened

    banner = next(line for line in text(state()) if "log " in line)
    assert "_183307.log" in banner


def test_a_running_phase_carries_its_own_small_bar():
    """The phase line is where "blocked but alive" is read, and a bar is
    what makes a stalled 318/430 visible at a glance."""
    lines = text(state())
    enrich = next(line for line in lines if "318/430" in line)

    assert "█" in enrich and "░" in enrich


def test_the_phase_bar_disappears_when_there_is_no_room_for_it():
    """At sixty columns the numbers matter more than the drawing."""
    narrow = text(state(), width=60)
    enrich = next(line for line in narrow if "318/430" in line)

    assert len(enrich) <= 60


# =============================================================================
# The visual language comes from the subject, not from the terminal
# =============================================================================

def test_the_bar_is_a_hatch_that_fills_rather_than_a_line_that_grows():
    """An engraver renders a value by the density of the hatching, and
    this pipeline is named after copperplate engraving. Three tones —
    bitten through, half-bitten, bare plate — say the same thing as a
    progress bar in the vocabulary of the thing being made."""
    from teille_douce.report.panel import TONES

    assert TONES[True] == ("█", "▓", "░")
    assert len(set(TONES[False])) == 3, "the ASCII tones stay distinguishable"


def test_every_colour_on_the_panel_comes_from_the_named_palette():
    """One place to change how the panel looks, and a guarantee that no
    line reached for a raw colour on its own."""
    from teille_douce.report.panel import PALETTE, render_panel

    used = {span.style for line in render_panel(state(), width=92)
            for span in line if span.style}

    assert used, "the panel is not entirely unstyled"
    assert used <= set(PALETTE), f"outside the palette: {used - set(PALETTE)}"


def test_the_palette_is_taken_from_the_materials_of_the_subject():
    """Copper, bister ink, verdigris, vermilion, graphite — chosen for
    what a copperplate is made of rather than for what a terminal
    happens to offer."""
    from teille_douce.report.panel import PALETTE

    assert set(PALETTE) >= {"plate", "bitten", "verdigris", "vermilion",
                            "graphite"}
    for name, colour in PALETTE.items():
        assert colour.startswith("#") or colour in ("bold", "dim"), name


def test_the_background_is_never_painted():
    """A panel that paints its own background fights the theme the reader
    chose, and looks broken in half of them."""
    from teille_douce.report.panel import PALETTE

    assert not any("on " in colour for colour in PALETTE.values())


def test_the_panel_does_not_string_its_own_facts_with_middle_dots():
    """`A · B · C` is the commonest tell of an interface nobody laid out:
    a list pretending not to be a table. The rule is about the chrome the
    renderer composes — a language distribution handed in as a note
    genuinely is a list, and gets to look like one."""
    notes = {phase.note for phase in state().current.phases if phase.note}

    for line in text(state()):
        if any(note in line for note in notes):
            continue
        assert line.count("·") <= 1, repr(line)


# =============================================================================
# What the header and the digest do when they run out of room
# =============================================================================

def test_an_unknown_eta_is_not_written_as_an_empty_label():
    """`eta ` with nothing after it reads as a broken template. A run that
    has not converted a volume yet cannot estimate anything, and saying so
    is better than a blank."""
    header = text(state(eta=""))[0]

    assert "eta" not in header
    assert "elapsed" in header


def test_a_known_eta_is_still_shown():
    assert "eta ~3h04" in text(state(eta="~3h04"))[0]


def test_one_volume_is_not_written_as_one_volumes():
    digest = WarningDigest()
    digest.add("D1", "something happened")

    line = next(l for l in text(state(digest=digest)) if "1×" in l)

    assert "1 volume" in line and "1 volumes" not in line


def test_a_long_warning_shape_gives_up_its_own_text_not_its_counts():
    """The counts are the reason the line exists. Truncating from the
    right dropped them and kept the prose."""
    digest = WarningDigest()
    digest.add("D1", "a warning message so long that it cannot possibly fit "
                     "inside the panel next to its own counters, not even "
                     "at a hundred columns of terminal width")

    line = next(l for l in text(state(digest=digest)) if "1×" in l)

    assert len(line) <= 92
    assert "1 volume" in line


def test_a_service_nobody_asked_for_is_off_and_not_down():
    """The four meanings of a zero, applied to the banner. `--fast` asks
    for no annotation at all, and painting three services red says the
    machine is broken when the operator simply did not want them."""
    off = state(services=(Service("PyHellen", up=None),))
    banner = text(off)[1]

    assert "off" in banner
    assert "DOWN" not in banner and "✗" not in banner


def test_a_service_that_was_asked_for_and_did_not_answer_is_down():
    banner = text(state(services=(Service("PyHellen", up=False),)))[1]

    assert "DOWN" in banner


def test_the_folded_integers_are_shown_only_when_there_is_a_sum_to_show():
    """One occurrence has nothing summed: the number is already in the
    shape, and printing it again as a total invites reading an identifier
    as a count."""
    digest = WarningDigest()
    digest.add("D1", "No metadata row for 'LIV9002_reconciled'")

    line = next(l for l in text(state(digest=digest)) if "1×" in l)

    assert "9 002" not in line


def test_a_folded_count_is_still_shown_when_it_is_really_a_sum():
    digest = WarningDigest()
    digest.add("D1", "page 1: 19 duplicate ALTO id(s) disambiguated")
    digest.add("D2", "page 2: 23 duplicate ALTO id(s) disambiguated")

    line = next(l for l in text(state(digest=digest)) if "2×" in l)

    assert "42" in line
