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


def test_the_folded_integers_are_never_shown_as_a_total():
    """Whether they were counts or identifiers is the one thing the fold
    cannot know, so it says neither."""
    digest = WarningDigest()
    digest.add("D1", "No metadata row for 'LIV9002_reconciled'")

    line = next(l for l in text(state(digest=digest)) if "1×" in l)

    assert "9 002" not in line


# =============================================================================
# What the panel does with text it did not write
# =============================================================================

def _cells(text):
    """Terminal columns, not characters: a wide glyph costs two."""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1
               for c in text)


@pytest.mark.parametrize("width", [56, 60, 72, 80, 92, 100])
def test_a_lost_phase_line_fits_at_every_width_the_panel_is_offered(width):
    """`choose_ui` hands out the dashboard from 56 columns, and the LOST
    line is the longest one the panel composes — with the reason the run
    actually passes, it overflowed from 56 to 79 and Rich cropped the
    cause, which is the one thing the design says must be loud."""
    dead = state(current=DocumentLine(
        name="LIV0038_t2_reconciled", pages=754, elapsed=22,
        phases=(PhaseLine("modernize", PhaseState.LOST, done=0, total=1402,
                          unit="containers",
                          reason="VieuxParler stopped answering"),)))

    for line in text(dead, width=width):
        assert _cells(line) <= min(width, 100), repr(line)


def test_a_newline_in_a_detail_does_not_add_a_line_the_panel_did_not_draw():
    """`httpx` error messages are two lines. Clipping by length keeps the
    control character, so the panel renders one line and the terminal
    shows two — the second unindented and unclipped."""
    two_lines = ("Client error '404 Not Found' for url 'http://x/a'\n"
                 "For more information check: https://developer.mozilla.org/")
    rendered = text(state(incidents=1, incident_lines=(f"I1 D1  {two_lines}",)))

    assert all("\n" not in line for line in rendered)
    assert not any("\r" in line or "\t" in line for line in rendered)


def test_a_tab_costs_what_it_looks_like_and_not_one_character():
    rendered = text(state(incidents=1, incident_lines=("I1 D1  a\tb",)))

    assert all("\t" not in line for line in rendered)


@pytest.mark.parametrize("width", [56, 80, 100])
def test_a_wide_glyph_is_measured_in_cells(width):
    """A CJK name is one `len()` per two columns, so a character count
    lets the line run off the edge."""
    wide = state(current=DocumentLine(name="漢字" * 40, pages=1, elapsed=1))

    for line in text(wide, width=width):
        assert _cells(line) <= min(width, 100), repr(line)


def test_the_banner_does_not_fuse_a_service_into_the_log_name():
    """"NER localog" is a word that does not exist, and at 56 columns the
    third service vanished — from the banner that exists to show one
    dying."""
    banner = text(state(), width=66)[1]

    assert "localog" not in banner
    assert "log " in banner


def test_a_service_dropped_for_want_of_room_is_not_dropped_silently():
    banner = text(state(), width=56)[1]

    assert "+1" in banner or "NER local" in banner


def test_the_incident_lines_are_capped_like_the_warnings_are():
    """Thirty volumes with both services dead is sixty incident lines and
    a sixty-nine-line panel, against a floor of twelve. Rich's Live
    ellipsises from the bottom, so the bar, the totals and the ctrl-c
    foot are what disappears."""
    many = state(incidents=60, incident_lines=tuple(
        f"I{n} LIV{n:04d}  enrich.phase_lost  PyHellen down" for n in range(60)))

    lines = text(many, height=24)

    assert len(lines) <= 24
    assert any("ctrl-c" in line for line in lines), "the foot was cut"
    assert any("more" in line for line in lines), "the elision was silent"


def test_a_panel_with_no_incidents_invents_no_line_about_them():
    """The trim fired on every frame of every clean run at the documented
    minimum window, adding " +0 more incidents" and making the panel one
    line TALLER than the height it was given."""
    lines = text(state(), height=12)

    assert not any("more incidents" in line for line in lines)


def test_the_trim_never_makes_the_panel_taller():
    for height in (5, 8, 12, 16, 24):
        for count in (0, 1, 5, 60):
            crowded = state(incidents=count, incident_lines=tuple(
                f"I{n} D{n}  enrich.phase_lost  down" for n in range(count)))
            without = len(text(crowded))
            with_height = len(text(crowded, height=height))
            assert with_height <= max(without, height), (height, count)


def test_the_foot_survives_a_crowded_panel():
    """Rich ellipsises from the bottom, so the ctrl-c line is what goes —
    and it is the line that tells the operator the decision is theirs."""
    crowded = state(incidents=60, incident_lines=tuple(
        f"I{n} D{n}  enrich.phase_lost  PyHellen down" for n in range(60)))

    lines = text(crowded, height=24)

    assert len(lines) <= 24
    assert "ctrl-c" in lines[-1]


def test_the_foot_and_the_totals_are_clipped_like_everything_else():
    """"Everything composed goes through clip" was not true of the two
    lines composed without `_pad`: a six-digit total or a three-digit
    volume count ran past the edge and Rich cropped the foot."""
    huge = state(volumes_written=1234, source_lost=1234567,
                 withheld=987654, incidents=456789)

    for line in text(huge, width=56):
        assert len(line) <= 56, repr(line)


def test_the_banner_keeps_the_dead_service_when_it_has_to_choose():
    """`✗ NAME LOST hh:mm` is the longest label by construction, so a
    first-fit loop dropped it and kept the one nobody asked for — from
    the banner that exists to show one dying."""
    crowded = state(services=(
        Service("PyHellen", up=True),
        Service("VieuxParler", up=False, lost_at="12:04"),
        Service("NER local", up=None)))

    banner = text(crowded, width=60)[1]

    assert "VieuxParler" in banner and "LOST" in banner


def test_the_digest_line_prints_only_what_the_fold_actually_knows():
    """It summed the integers of the folded messages and printed the
    last one. For "No metadata row for 'LIV9002_reconciled'" across two
    volumes that is 9002 + 9004 = 18 006 — two identifiers read as a
    count, beside a volume count, with no unit to tell them apart.

    The fold knows how many times a shape occurred and in how many
    volumes. It cannot know what the numbers inside meant, and the one
    case where they were worth printing — duplicate ALTO ids — is
    already a line of the summary, measured against its own denominator."""
    digest = WarningDigest()
    digest.add("A", "No metadata row for 'LIV9002_reconciled'")
    digest.add("B", "No metadata row for 'LIV9004_reconciled'")

    line = next(l for l in text(state(digest=digest)) if "2×" in l)

    assert "18 006" not in line
    assert "2 volumes" in line


def test_a_dead_service_appears_even_when_its_label_does_not_fit():
    """Sorting it first was not enough: the fill is first-fit, so a label
    too long to fit was skipped and a shorter LIVE one kept its place —
    the same outcome, at the width the module calls its floor."""
    crowded = state(
        log_path="tei_output/.teille-douce/runs/20260903-180824-0282822/pipeline.log",
        services=(Service("PyHellen", up=True),
                  Service("VieuxParler", up=False, lost_at="17:04"),
                  Service("NER local", up=None)))

    for width in (56, 57, 58, 59, 60, 72, 92):
        banner = text(crowded, width=width)[1]
        assert "VieuxParler" in banner, (width, banner)


def test_every_dead_service_is_on_the_banner_or_in_its_count():
    """What the banner actually promises, and all it promises: the FIRST
    dead service is there at every width, whole or clipped, and the ones
    that did not fit are counted rather than silently gone.

    An earlier attempt asserted `count("✗") >= 1 or "+2" in banner`,
    which the pre-fix first-fit behaviour also satisfied."""
    crowded = state(services=tuple(
        Service(name, up=False, lost_at="17:04")
        for name in ("PyHellen", "VieuxParler", "NER local")))

    for width in range(56, 121):
        banner = text(crowded, width=width)[1]
        shown = banner.count("✗")
        assert shown >= 1, (width, banner)
        missing = 3 - shown
        assert missing == 0 or f"+{missing} more" in banner, (width, banner)


def test_the_banner_never_runs_past_the_edge_however_many_services_die():
    """Two fixed passes reserved " +9 more" and then wrote " +10 more",
    one cell over."""
    from teille_douce.report.text import cells

    for count in range(1, 20):
        crowded = state(services=tuple(
            Service(f"service-{i:02d}", up=False, lost_at="17:04")
            for i in range(count)))
        for width in range(56, 121):
            banner = text(crowded, width=width)[1]
            assert cells(banner) <= min(width, 100), (count, width, banner)


# =============================================================================
# The two columns of a line, and which one survives
# =============================================================================

def test_the_top_line_keeps_the_elapsed_and_the_estimate():
    """It clipped the composed line, so the right column went first: an
    absolute `-o` path — the ordinary case, not the edge — took the
    elapsed time and the eta off the panel on every single frame.

    The WHOLE right column, not its first field: asserting `elapsed
    47:12` alone passed under a clip, because `elapsed` is what comes
    first and the estimate is what a clip eats."""
    absolute = state(
        output_dir="/home/rayondemiel/univ_geneve/tei_output_grand_siecle")

    for width in (56, 72, 92, 100):
        header = text(absolute, width=width)[0]
        assert header.endswith("elapsed 47:12   eta ~3h04 (median of 7)"), (
            width, header)


def test_a_shortened_path_keeps_the_directory_it_names():
    """Cut from the right, a path loses the only part that says which
    corpus this is.

    Long enough that it MUST be shortened at every width tested — an
    earlier version of this test used a path that still fitted at
    ninety-two columns, so it passed on the unfixed panel too, for the
    opposite reason: that one clipped the composed line, dropped the
    right column, and left the path whole."""
    long = ("/home/rayondemiel/univ_geneve/corpus/grand-siecle/"
            "reconciled/2026/tei_output_final")

    for width in (72, 92, 100):
        header = text(state(output_dir=long), width=width)[0]
        shown = header.split("→ ", 1)[1].split("/ ")[0] + "/"
        assert "…" in shown, (width, header)
        # Whatever it kept, it kept the END. Clipped from the right this
        # read `/home/rayondemiel/univ…`, which names the operator rather
        # than the corpus and is the same for every run they ever make.
        kept = shown.rstrip("/").rsplit("…", 1)[-1]
        assert long.endswith(kept), (width, header)

    # And wherever there is room for it, the leaf whole.
    for width in (92, 100):
        assert "tei_output_final" in text(state(output_dir=long),
                                          width=width)[0], width


def test_a_dead_service_survives_at_every_width_it_can_be_read_at():
    """Guards the clipping the round before last introduced: a dead
    service whose `✗ NAME LOST hh:mm` will not fit is shortened, not
    skipped. Stated as a sweep because the failure was width-dependent —
    it showed only at the module's own floor."""
    crowded = state(
        log_path="tei_output/.teille-douce/runs/20260903-180824-0282822/pipeline.log",
        services=(Service("PyHellen", up=True),
                  Service("VieuxParler", up=False, lost_at="17:04"),
                  Service("NER local", up=None)))

    for width in range(56, 121):
        banner = text(crowded, width=width)[1]
        assert "VieuxParler" in banner, (width, banner)


def test_the_panel_is_never_taller_than_the_height_it_was_given():
    """The trim only ever cut incident lines, so a run with none and the
    corpus' ordinary warning flood returned fifteen lines for twelve —
    and Rich's Live crops from the BOTTOM, taking the closing rule and
    the ctrl-c foot, which is what the trim exists to prevent."""
    from teille_douce.report.digest import WarningDigest

    flood = WarningDigest()
    for volume in ("LIV0038", "LIV0039"):
        for page in range(16):
            for shape in ("no metadata row for '%s'", "page %s: unknown zone",
                          "line %s rejected", "hyphen %s unresolved",
                          "id %s duplicated"):
                flood.add(volume, f"{volume}: " + shape % page)

    for height in range(8, 32):
        lines = text(state(digest=flood, incident_lines=()), width=92,
                     height=height)
        assert len(lines) <= height, (height, len(lines))
        assert "ctrl-c" in lines[-1], (height, lines[-1])


def test_a_finished_run_is_not_offered_a_volume_to_abandon():
    assert "stop the run" in text(state(current=None))[-1]


# =============================================================================
# A phase line gives way in a stated order, and never in its numbers
# =============================================================================

def test_a_phase_line_drops_its_bar_then_its_aside_and_never_its_numbers():
    """Composed and clipped, the bar pushed the line over and the clip
    took `waiting 47s/120s` down to `waiting 47s/12…` — a twelve-second
    timeout on a hundred-and-twenty-second one, on the line whose entire
    job is telling slow from hung, at the panel's own minimum width."""
    from teille_douce.report.text import cells

    phase = PhaseLine("modernize", PhaseState.RUNNING, done=318, total=1402,
                      unit="containers", waiting=47, timeout=120,
                      elapsed=4360)
    running = state(current=DocumentLine(name="D", pages=754, elapsed=291,
                                         phases=(phase,)))

    for width in range(56, 141):
        line = next(l for l in text(running, width=width) if "modernize" in l)
        assert cells(line) <= min(width, 100), (width, cells(line), line)
        assert "318/1 402 containers" in line, (width, line)
        assert "1:12:40" in line, (width, line)
        if "waiting" in line:
            assert "waiting 47s/120s" in line, (width, line)


def test_the_phase_column_is_measured_in_cells_like_everything_else():
    """`{phase.name:<12}` padded by `len`, and the two branches that clip
    against it used a literal `room - 15` — so widening the column by one
    put every PENDING and LOST line one cell past the edge, and where a
    clock followed it took the room the clock had been promised."""
    from teille_douce.report.text import cells

    for name in ("ner", "modernize.retry", "漢字巻物", "a" * 30):
        for phase_state in (PhaseState.PENDING, PhaseState.LOST,
                            PhaseState.DONE):
            phase = PhaseLine(name, phase_state, done=318, total=430,
                              unit="containers", elapsed=4360,
                              reason="PyHellen stopped answering at 19:41")
            one = state(current=DocumentLine(name="D", pages=7, elapsed=3,
                                             phases=(phase,)))
            for width in range(56, 141):
                line = next(l for l in text(one, width=width)
                            if name[:6] in l)
                assert cells(line) <= min(width, 100), (
                    name, phase_state, width, cells(line), line)
                assert "1:12:40" in line, (name, phase_state, width, line)


def test_the_foot_groups_its_number_like_every_other_on_the_panel():
    assert "1 699 998 already written" in text(
        state(volumes_written=1699998, current=None))[-1]


# =============================================================================
# The panel, as a property, over names and widths nobody types today
# =============================================================================

def _every_phase_shape():
    """Names and states the panel must survive, not the ones it meets."""
    for name in ("ner", "sourceDoc", "reconstruction-align", "a" * 40,
                 "漢" * 20, "x"):
        for phase_state in PhaseState:
            for note in ("(was up at start)", ""):
                yield PhaseLine(name, phase_state, done=318, total=430,
                                unit="containers", elapsed=4360, note=note,
                                waiting=47, timeout=120,
                                reason="PyHellen stopped answering at 19:41")


def test_the_panel_holds_its_contract_over_names_it_will_never_meet():
    """Four fixes shipped without one of these: the clipped head, the
    measured cause indent, the totals row's degradation and `_short_log`
    in cells. Reverting the module left the suite green.

    Every one of them is about a value wider than the column it was
    budgeted for, so the property is stated over names and figures wider
    than production has — `panel.py` says a name that wide must not have
    to know it is unusual."""
    from teille_douce.report.text import cells

    for phase in _every_phase_shape():
        wide = state(
            source_lost=1699998, withheld=999999, incidents=999999,
            # A name the CHARACTER cut overflows on: nine wide glyphs
            # and no separator to fall back to. The one it had before
            # was long enough that `name[-room:]` happened to fit, so
            # the mutation that measures in cells and cuts in characters
            # went unnoticed.
            log_path="漢字巻物のログ",
            current=DocumentLine(name="D", pages=7, elapsed=3,
                                 phases=(phase,)))
        for width in range(24, 141):
            for unicode_ in (True, False):
                for line in text(wide, width=width, unicode=unicode_):
                    assert cells(line) <= min(width, 100), (
                        phase.name[:8], phase.state, width, cells(line), line)
                    assert line == line.rstrip(), (
                        phase.name[:8], phase.state, width, repr(line))
                    assert not (line.strip() and set(line.strip()) <= {"…"}), (
                        phase.name[:8], width, repr(line))


def test_a_lost_phases_cause_sits_under_the_mark_it_explains():
    """The indent was measured off the UNCLIPPED name, so as soon as the
    head itself was shortened it landed past the mark — and at
    four-and-twenty columns it swallowed the cause whole. Two fixes of
    one round disagreeing with each other."""
    # A CJK name too: the indent is measured in cells, and the mutation
    # that measures it in characters is the one the docstring cites.
    for name in ("reconstruction-align", "漢字巻物のログ"):
        _the_cause_sits_under_the_mark(name)


def _the_cause_sits_under_the_mark(name):
    from teille_douce.report.text import cells

    phase = PhaseLine(name, PhaseState.LOST, done=0,
                      total=430, unit="containers", note="(was up at start)",
                      reason="PyHellen stopped answering")
    lost = state(current=DocumentLine(name="D", pages=7, elapsed=3,
                                      phases=(phase,)))

    # From twenty-four, not from forty. The old indent and the new one
    # agree at every width where the head fits whole — which is every
    # width above twenty-nine, so a sweep that started at forty passed
    # against the code it was written to condemn.
    for width in range(24, 141):
        lines = text(lost, width=width)
        at = next(i for i, l in enumerate(lines) if name[:4] in l)
        head, cause = lines[at], lines[at + 1]
        if not cause.strip():
            continue        # no room for any of it; the head said so
        # In cells, like the panel itself: `str.index` is a character
        # offset, and with a wide-glyph name the two differ — which is
        # exactly the mutation this is here to catch.
        assert cells(cause[:len(cause) - len(cause.lstrip())]) == \
            cells(head[:head.index("✗")]), (width, head, cause)


def test_the_totals_row_keeps_its_three_counts_or_the_one_that_matters():
    """Clipped span by span, the row lost `incident` entirely below fifty
    columns and cut it mid-number up to sixty-two — on the row that
    exists so a counter is not mistaken for a document that had nothing
    to process."""
    big = state(source_lost=1699998, withheld=999999, incidents=999999)

    for width in range(24, 141):
        row = next(l for l in text(big, width=width)
                   if "inc" in l and "incident" not in l or "incident" in l)
        assert "999 999" in row, (width, row)
