# -----------------------------------------------------------
# How a loss is allowed to be written.
#
# "Every phase reports what it lost. A counter left at zero because a
# server died must not look like a document that had nothing to process."
# That sentence of CLAUDE.md was a wish. Here it is a rule the renderer
# enforces: a count with no denominator raises rather than printing a bare
# integer, because a bare integer is exactly what makes those two zeros
# indistinguishable.
#
# Run: venv/bin/python -m pytest tests/test_loss_rendering.py -q
# -----------------------------------------------------------
import pytest

from teille_douce.report.counts import PhaseState, render_count, render_phase_loss


# =============================================================================
# A count is never a bare integer
# =============================================================================

def test_a_count_without_a_denominator_raises():
    """The whole point. `388 pages unusable` cannot be judged; `388 of
    16 999` can. Making it a ValueError rather than a convention is what
    stops the honest form from being optional."""
    with pytest.raises(ValueError, match="denominator"):
        render_count(388, None, "pages")


def test_a_count_with_a_denominator_reads_as_a_proportion():
    assert render_count(388, 16999, "pages") == "388 of 16 999 pages"


def test_a_zero_against_a_denominator_is_still_written_out():
    """A line that disappears at zero is indistinguishable from a phase
    that was never checked."""
    assert render_count(0, 1402, "containers") == "0 of 1 402 containers"


def test_the_denominator_may_be_zero_when_there_was_genuinely_nothing():
    assert render_count(0, 0, "containers") == "0 of 0 containers"


def test_a_count_larger_than_its_denominator_is_a_programming_error():
    """Two counters that disagree is a bug, and printing `19 of 12` hides
    it behind a plausible-looking line."""
    with pytest.raises(ValueError, match="exceeds"):
        render_count(19, 12, "pages")


def test_a_negative_count_is_refused():
    with pytest.raises(ValueError, match="negative"):
        render_count(-1, 12, "pages")


# =============================================================================
# The four meanings of a zero
# =============================================================================

def test_a_phase_switched_off_says_so_and_shows_no_number():
    """It never ran; there is no denominator, and inventing one would
    claim it had looked."""
    assert render_phase_loss(PhaseState.OFF, reason="TDOUCE_ENRICHMENT=0") \
        == "off (TDOUCE_ENRICHMENT=0)"


def test_a_phase_disabled_by_an_unreachable_service_names_the_service():
    """Different from "off": the operator asked for it and did not get
    it. Same absence of numbers, different sentence."""
    assert render_phase_loss(PhaseState.UNAVAILABLE,
                             reason="PyHellen unreachable") \
        == "off · PyHellen unreachable"


def test_a_phase_that_died_mid_run_shows_what_it_refused():
    """The pathological case CLAUDE.md names. Every counter is at zero,
    and the denominator is the whole point: 1 402 containers went
    unannotated, which is not the same as a document with none."""
    assert render_phase_loss(PhaseState.LOST, count=0, total=1402,
                             unit="containers",
                             reason="PyHellen down since 19:41") \
        == "LOST · 0 of 1 402 containers — PyHellen down since 19:41"


def test_a_phase_that_ran_and_found_nothing_says_the_empty_out_loud():
    """The honest empty, written in words so it cannot be read as a
    failure."""
    assert render_phase_loss(PhaseState.DONE, count=0, total=0,
                             unit="containers") \
        == "no container to enrich (0 found)"


def test_the_honest_empty_names_the_phase_that_found_nothing(): 
    """"no container to enrich" is right for enrichment and wrong for
    every other phase. The verb belongs to the caller."""
    assert render_phase_loss(PhaseState.DONE, count=0, total=0,
                             unit="pages", verb="convert") \
        == "no page to convert (0 found)"
    assert render_phase_loss(PhaseState.DONE, count=0, total=0,
                             unit="entities", verb="resolve") \
        == "no entity to resolve (0 found)"


def test_a_phase_that_ran_and_lost_nothing_says_so_without_a_number():
    assert render_phase_loss(PhaseState.DONE, count=0, total=430,
                             unit="containers") == "nothing lost (0 of 430)"


def test_a_phase_that_ran_and_lost_something_gives_the_proportion():
    assert render_phase_loss(PhaseState.DONE, count=12, total=430,
                             unit="containers") == "12 of 430 containers"


# =============================================================================
# LOST is reserved
# =============================================================================

def test_only_a_lost_phase_may_render_as_lost():
    """`LOST` in red is the loudest thing on the panel. If a merely
    degraded phase could reach it, it would stop meaning anything."""
    for state in (PhaseState.OFF, PhaseState.UNAVAILABLE, PhaseState.PENDING,
                  PhaseState.RUNNING, PhaseState.DONE):
        rendered = render_phase_loss(state, count=0, total=10, unit="pages",
                                     reason="whatever")
        assert "LOST" not in rendered, state


def test_a_lost_phase_must_say_why_and_how_much():
    """A red LOST with no cause and no scope is an alarm with no address."""
    with pytest.raises(ValueError, match="reason"):
        render_phase_loss(PhaseState.LOST, count=0, total=10, unit="pages")

    with pytest.raises(ValueError, match="denominator"):
        render_phase_loss(PhaseState.LOST, count=0, total=None,
                          unit="pages", reason="PyHellen down")


def test_a_pending_phase_has_nothing_to_report_yet():
    assert render_phase_loss(PhaseState.PENDING) == "pending"
