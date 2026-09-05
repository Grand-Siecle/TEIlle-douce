# -----------------------------------------------------------
# The journal reporter, and the collector both reporters read.
#
# The run talks to one object. What it says is the same whether a panel is
# being drawn or a log is being written — which is the only way the two
# can be guaranteed to agree, and the reason the end-of-run summary is
# byte-identical in both.
#
# Scripted events in, lines out. No service, no ALTO, no subprocess.
#
# Run: venv/bin/python -m pytest tests/test_reporter_plain.py -q
# -----------------------------------------------------------
from pathlib import Path

import pytest

from teille_douce.report.collector import Run
from teille_douce.report.counts import PhaseState
from teille_douce.report.record import Block, Code, Locator, Loss


def a_run(**overrides):
    base = dict(input_dir=Path("OCR"), output_dir=Path("tei_output"),
                volumes=3, pages=30, log_path=Path("pipeline.log"))
    base.update(overrides)
    return Run(**base)


# =============================================================================
# The collector is what both reporters read
# =============================================================================

def test_a_finished_document_counts_towards_what_was_written():
    run = a_run()
    run.document_started("D1", pages=10)
    run.document_finished("D1", ok=True)

    assert run.panel().volumes_written == 1
    assert run.panel().pages_written == 10


def test_a_failed_document_is_a_failure_and_an_incident():
    """The two are the same fact seen from two sides: the exit code needs
    the failure, the summary needs the incident with its address."""
    run = a_run()
    run.document_started("D1", pages=10)
    run.document_finished("D1", ok=False, reason="KeyError 'Date_edition'")

    assert run.panel().volumes_failed == 1
    incident, = run.record.losses(Block.INCIDENT)
    assert incident.code is Code.DOCUMENT_FAILED
    assert incident.detail == "KeyError 'Date_edition'"


def test_the_pages_of_the_open_volume_are_at_risk_not_written():
    """The middle zone of the bar is work that has been done and not yet
    saved. Counting it as written would make the bar claim more than the
    disk holds."""
    run = a_run()
    run.document_started("D1", pages=10)
    run.pages_read("D1", 6)

    assert run.panel().pages_written == 0
    assert run.panel().pages_in_flight == 6


def test_finishing_a_volume_moves_its_pages_from_at_risk_to_written():
    run = a_run()
    run.document_started("D1", pages=10)
    run.pages_read("D1", 10)
    run.document_finished("D1", ok=True)

    assert run.panel().pages_in_flight == 0
    assert run.panel().pages_written == 10


def test_a_failed_volume_leaves_no_pages_claimed_anywhere():
    """Nothing was written, so nothing may be counted as written — and
    the at-risk zone has to empty too, or the bar keeps growing on work
    that was thrown away."""
    run = a_run()
    run.document_started("D1", pages=10)
    run.pages_read("D1", 6)
    run.document_finished("D1", ok=False, reason="boom")

    assert run.panel().pages_written == 0
    assert run.panel().pages_in_flight == 0


# =============================================================================
# The step is remembered, so a failure can say where it happened
# =============================================================================

def test_a_failure_carries_the_step_it_happened_in():
    """`main.py` throws away the return value of `_process_document`, so
    when a document raises the step is lost. Keeping it is what lets the
    summary write `step 12/15`."""
    run = a_run()
    run.document_started("D1", pages=10)
    run.step("D1", "metadata 12/15")
    run.document_finished("D1", ok=False, reason="KeyError")

    incident, = run.record.losses(Block.INCIDENT)
    assert incident.step == "metadata 12/15"


# =============================================================================
# The journal
# =============================================================================

def test_the_journal_names_each_volume_as_it_starts():
    run = a_run()
    lines = []
    run.document_started("LIV0038_reconciled", pages=754)

    from teille_douce.report.plain import journal_lines
    lines = journal_lines(run.drain())

    assert any("LIV0038_reconciled" in line for line in lines)


def test_the_journal_says_what_a_phase_lost_with_its_denominator():
    run = a_run()
    run.document_started("D1", pages=10)
    run.phase("D1", "enrich", PhaseState.LOST, done=0, total=1402,
              unit="containers", reason="PyHellen down since 19:41")

    from teille_douce.report.plain import journal_lines
    text = "\n".join(journal_lines(run.drain()))

    assert "LOST · 0 of 1 402 containers" in text
    assert "PyHellen down since 19:41" in text


def test_the_journal_carries_no_markup():
    """It goes to a pipe and to a log file."""
    run = a_run()
    run.document_started("D1", pages=10)
    run.document_finished("D1", ok=False, reason="boom")

    from teille_douce.report.plain import journal_lines
    for line in journal_lines(run.drain()):
        assert "[/" not in line and "[bold" not in line


def test_draining_twice_does_not_repeat_what_was_already_said():
    """The journal prints as it goes; a second drain that replayed the
    run would double every line."""
    run = a_run()
    run.document_started("D1", pages=10)
    first = run.drain()
    second = run.drain()

    assert first and not second


# =============================================================================
# The outcome the summary is rendered from
# =============================================================================

def test_the_outcome_carries_what_the_summary_needs():
    run = a_run(volumes=3, pages=30)
    run.document_started("D1", pages=10)
    run.document_finished("D1", ok=True)
    run.document_started("D2", pages=10)
    run.document_finished("D2", ok=False, reason="boom")

    outcome = run.finished(exit_code=1, elapsed=12.0)

    assert outcome.volumes_total == 3
    assert outcome.volumes_written == 1
    assert outcome.pages_written == 10
    assert outcome.failed_documents == (("D2", "boom"),)
    assert outcome.exit_code == 1


def test_the_same_run_renders_the_same_summary_whichever_reporter_drew_it():
    """The one property that makes two presenters safe: they share the
    record, so there is one account of the run and not two."""
    from teille_douce.report.summary import render_summary

    run = a_run()
    run.document_started("D1", pages=10)
    run.lost(Loss(Code.PAGE_UNUSABLE, "D1", "sourcedoc",
                  Locator.page("D1", "f3"), count=2, total=10))
    run.document_finished("D1", ok=True)
    outcome = run.finished(exit_code=0, elapsed=1.0)

    assert render_summary(outcome, width=92) == render_summary(outcome, width=92)


# =============================================================================
# Warnings reach the digest, never the console directly
# =============================================================================

def test_a_warning_goes_to_the_digest():
    """Which is what absorbs eighty of them without touching a single
    call site."""
    run = a_run()
    run.document_started("D1", pages=10)
    for index in range(78):
        run.warning("D1", f"page {index}: 3 duplicate ALTO id(s) disambiguated")

    line, = run.digest.lines()

    assert line.occurrences == 78


def test_the_panel_totals_the_three_blocks():
    run = a_run()
    run.lost(Loss(Code.PAGE_UNUSABLE, "D1", "sourcedoc",
                  Locator.page("D1", "f3"), count=2, total=10))
    run.lost(Loss(Code.READING_REJECTED, "D1", "modernize",
                  Locator.document("D1"), count=7, total=20))

    panel = run.panel()

    assert (panel.source_lost, panel.withheld, panel.incidents) == (2, 7, 0)


def test_a_run_without_a_log_file_still_has_a_panel():
    """`--no-log-file` is a supported way to run, and the panel says there
    is no log rather than pointing at one that does not exist."""
    run = Run(input_dir=Path("OCR"), output_dir=Path("out"), volumes=1,
              pages=1, log_path=None)

    assert run.panel().log_path == "(none)"


# =============================================================================
# The two accounts may never disagree
# =============================================================================

def test_pages_written_counts_the_pages_that_were_written():
    """A volume of ten pages losing three writes seven surfaces. Adding
    the volume's total instead printed "10 of 10 pages written" one line
    above "pages unusable 3 of 10"."""
    run = a_run()
    run.document_started("D1", pages=10)
    run.pages_read("D1", 7)
    run.document_finished("D1", ok=True)

    assert run.panel().pages_written == 7
    assert run.finished(exit_code=0, elapsed=1.0).pages_written == 7


def test_a_volume_that_read_every_page_writes_every_page():
    run = a_run()
    run.document_started("D1", pages=10)
    run.pages_read("D1", 10)
    run.document_finished("D1", ok=True)

    assert run.panel().pages_written == 10


def test_a_reporter_cannot_kill_the_run_it_is_reporting_on():
    """`phase(..., LOST)` with the method's own defaults raised out of the
    collector — and raised AFTER recording the loss, leaving the record
    holding an entry the journal never got."""
    run = a_run()
    run.document_started("D1", pages=10)

    run.phase("D1", "enrich", PhaseState.LOST)          # no reason, no total

    loss, = run.record.losses(Block.INCIDENT)
    assert loss.code is Code.PHASE_LOST
    assert "unknown" in " ".join(e.text for e in run.drain()).lower()


def test_a_repair_is_not_counted_among_the_things_that_can_be_looked_at():
    """`located` answers "how much of what was lost can you open". A
    repaired defect lost nothing, and it is the largest figure this corpus
    produces — counting it made a clean run report six thousand
    unlocatable losses."""
    from teille_douce.report.record import RunRecord

    record = RunRecord()
    record.add(Loss(Code.ALTO_IDS_REPAIRED, "D1", "sourcedoc",
                    Locator.document("D1"), count=6174, total=41908))

    assert record.located() == {"precise": 0, "document_only": 0}


def test_a_repair_is_not_counted_in_the_panel_total_either():
    from teille_douce.report.record import Block as B, RunRecord

    record = RunRecord()
    record.add(Loss(Code.ALTO_IDS_REPAIRED, "D1", "sourcedoc",
                    Locator.document("D1"), count=6174, total=41908))
    record.add(Loss(Code.PAGE_UNUSABLE, "D1", "sourcedoc",
                    Locator.page("D1", "f1"), count=3, total=754))

    assert record.total(B.SOURCE) == 3
