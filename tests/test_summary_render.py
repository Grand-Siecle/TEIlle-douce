# -----------------------------------------------------------
# The end-of-run summary: what a reader is left with.
#
# Printed after the live panel is torn down, so it survives in the
# scrollback — and rendered from the record, so it is identical byte for
# byte whether the run showed a dashboard or a journal. Two renderers that
# could disagree would be two accounts of the same run.
#
# Run: venv/bin/python -m pytest tests/test_summary_render.py -q
# -----------------------------------------------------------
from pathlib import Path

import pytest

from teille_douce.report.record import Block, Code, Locator, Loss, RunRecord
from teille_douce.report.summary import RunOutcome, render_summary


def outcome(**overrides):
    """A run that converted everything, with nothing lost."""
    base = dict(
        input_dir=Path("OCR"), output_dir=Path("tei_output"),
        volumes_total=27, volumes_written=27,
        pages_total=16999, pages_written=16999,
        failed_documents=(), failed_archives=(),
        record=RunRecord(), elapsed=3.0, exit_code=0, fail_on="never",
    )
    base.update(overrides)
    return RunOutcome(**base)


def rendered(outcome_):
    return "\n".join(render_summary(outcome_, width=92))


# =============================================================================
# The three blocks always have their lines
# =============================================================================

def test_each_block_prints_its_heading_even_on_a_clean_run():
    """A line that disappears at zero cannot be told from a phase that was
    never checked. The headings are the map of what this pipeline knows
    how to lose."""
    text = rendered(outcome())

    assert "the source was defective" in text
    assert "withheld on purpose" in text
    assert "lost to an incident" in text


def test_a_loss_is_written_against_its_denominator():
    record = RunRecord()
    record.add(Loss(Code.PAGE_UNUSABLE, "D1", "sourcedoc",
                    Locator.page("D1", "f41"), count=388, total=16999,
                    detail="no <surface> could be built"))

    text = rendered(outcome(record=record))

    assert "388 of 16 999 pages" in text
    assert "no <surface> could be built" in text


def test_a_repaired_defect_is_not_written_as_a_loss():
    """Duplicate ALTO ids are the loudest number in this corpus and
    nothing is lost to them. Filing them anywhere but block 1, or writing
    them without the word "repaired", makes the biggest figure in the
    summary a false alarm."""
    record = RunRecord()
    record.add(Loss(Code.ALTO_IDS_REPAIRED, "D1", "sourcedoc",
                    Locator.page("D1", "f88"), count=6174, total=41908,
                    detail="duplicates disambiguated"))

    text = rendered(outcome(record=record))

    assert "repaired" in text
    assert "6 174 of 41 908" in text


# =============================================================================
# A whole phase lost is never averaged into containers
# =============================================================================

def test_a_document_written_with_no_annotation_is_counted_apart():
    """Damage of a different kind. Three volumes carrying no enrichment
    at all, folded into a container percentage, is exactly the convenient
    lie this project forbids."""
    record = RunRecord()
    for index in range(3):
        document = f"LIV{index:04d}"
        record.add(Loss(Code.PHASE_LOST, document, "enrich",
                        Locator.document(document), count=0, total=1402,
                        detail="PyHellen down 19:41 → 20:14"))
    record.add(Loss(Code.CONTAINER_FAILED, "LIV0009", "enrich",
                    Locator.page("LIV0009", "f12"), count=294, total=12940,
                    detail="PyHellen HTTP 500"))

    text = rendered(outcome(record=record))

    assert "whole phases" in text
    assert "3 of 27" in text
    assert "294 of 12 940 containers" in text
    # and the two never became one number
    assert "297" not in text


# =============================================================================
# What can be acted on, stated even when the answer is "all of it"
# =============================================================================

def test_the_located_line_prints_even_when_nothing_is_unlocated():
    """A blind spot becomes a visible line rather than an absence."""
    assert "0 to a document only" in rendered(outcome())


def test_the_located_line_separates_precise_from_document_only():
    record = RunRecord()
    record.add(Loss(Code.PAGE_UNUSABLE, "D1", "sourcedoc",
                    Locator.page("D1", "f41"), count=28402, total=99999))
    record.add(Loss(Code.READING_REJECTED, "D2", "modernize",
                    Locator.document("D2"), count=3265, total=99999))

    text = rendered(outcome(record=record))

    assert "28 402 to a file or an xml:id" in text
    assert "3 265 to a document only" in text


# =============================================================================
# What to type next is part of reporting
# =============================================================================

def test_a_failed_document_offers_the_command_that_retries_it():
    """A failure state that does not say what to type next has not
    finished reporting."""
    text = rendered(outcome(
        volumes_written=25, exit_code=1,
        failed_documents=(("LIV0044_reconciled", "KeyError 'Date_edition'"),),
    ))

    assert "--retry-failed" in text
    assert "LIV0044_reconciled" in text


def test_a_clean_run_is_not_told_to_retry_anything():
    """The block is chosen by what broke. Offering a remedy for a problem
    that did not happen teaches the reader to skip the block."""
    assert "--retry-failed" not in rendered(outcome())


def test_an_incident_offers_the_report_that_shows_it_in_full():
    record = RunRecord()
    record.add(Loss(Code.PHASE_LOST, "D1", "enrich", Locator.document("D1"),
                    count=0, total=1402, detail="PyHellen down"))

    assert "report --block incident" in rendered(outcome(record=record))


# =============================================================================
# The last line says what happened and why it is or is not enough
# =============================================================================

def test_a_clean_run_ends_by_saying_so():
    assert rendered(outcome()).rstrip().endswith(
        "exit 0 — 27 of 27 volumes converted")


def test_a_partial_failure_ends_on_the_count_that_matters():
    text = rendered(outcome(volumes_written=25, exit_code=1,
                            failed_documents=(("A", "x"), ("B", "y"))))

    assert text.rstrip().endswith("exit 1 — 2 of 27 volumes not converted")


def test_a_quality_gate_says_both_what_worked_and_why_it_is_not_enough():
    """"Everything converted" and "exit 5" only contradict each other if
    you believe a written file is an acceptable file — which is the
    distinction this flag exists to make."""
    record = RunRecord()
    for index in range(3):
        record.add(Loss(Code.PHASE_LOST, f"D{index}", "enrich",
                        Locator.document(f"D{index}"), count=0, total=1402,
                        detail="PyHellen down 19:41 → 20:14"))

    text = rendered(outcome(record=record, exit_code=5, fail_on="incident"))

    assert "quality gate --fail-on incident" in text
    assert "NOT MET" in text
    assert text.rstrip().endswith(
        "exit 5 — everything converted, but 3 volumes carry no enrichment at all")


def test_the_gate_names_the_lines_that_tripped_it():
    """Otherwise it turns a diagnosis into a guessing game."""
    record = RunRecord()
    record.add(Loss(Code.PHASE_LOST, "D1", "enrich", Locator.document("D1"),
                    count=0, total=1402,
                    detail="PyHellen down 19:41 → 20:14"))

    text = rendered(outcome(record=record, exit_code=5, fail_on="incident"))

    assert "PyHellen down 19:41 → 20:14" in text


# =============================================================================
# Shape
# =============================================================================

@pytest.mark.parametrize("width", [60, 80, 92, 100, 200])
def test_no_line_is_wider_than_the_panel(width):
    """Measured in cells, and the panel is capped at 100: past about a
    hundred characters a block stops being scannable."""
    record = RunRecord()
    record.add(Loss(Code.PAGE_UNUSABLE, "D1", "sourcedoc",
                    Locator.page("D1", "f41"), count=388, total=16999,
                    detail="no <surface> could be built"))

    for line in render_summary(outcome(record=record), width=width):
        assert len(line) <= min(width, 100), repr(line)


def test_the_summary_is_plain_text_carrying_no_markup():
    """It is rendered once and printed by both reporters; a Rich tag left
    in it would show up literally in a log file."""
    text = rendered(outcome())

    assert "[/" not in text and "[bold" not in text


# =============================================================================
# Details that decide whether it reads
# =============================================================================

def test_the_elapsed_time_is_written_the_way_a_human_says_it():
    """`17491s` is a number to divide; `4h 51m 31s` is a duration."""
    assert "4h 51m 31s" in rendered(outcome(elapsed=17491))
    assert "51m 31s" in rendered(outcome(elapsed=3091))
    assert "31s" in rendered(outcome(elapsed=31))


def test_one_archive_is_not_written_as_archives():
    text = rendered(outcome(volumes_written=26, exit_code=1,
                            failed_archives=(("x.zip", "not a zip"),)))

    assert "1 archive" in text and "1 archives" not in text


def test_no_line_carries_trailing_whitespace():
    """The same string goes to a terminal and to a log file. The right
    margin is made by reserving the room, not by padding into it."""
    record = RunRecord()
    record.add(Loss(Code.PAGE_UNUSABLE, "D1", "sourcedoc",
                    Locator.page("D1", "f41"), count=388, total=16999,
                    detail="no <surface> could be built"))

    for line in render_summary(outcome(record=record), width=92):
        assert line == line.rstrip(), repr(line)


def test_the_gate_shows_the_lines_that_tripped_it_not_every_record():
    """Three volumes lost the same phase for the same reason: that is one
    line saying "3 of 27", not the same sentence printed three times."""
    record = RunRecord()
    for index in range(3):
        record.add(Loss(Code.PHASE_LOST, f"D{index}", "enrich",
                        Locator.document(f"D{index}"), count=0, total=1402,
                        detail="PyHellen down 19:41 → 20:14"))

    text = rendered(outcome(record=record, exit_code=5, fail_on="incident"))
    gate = text.split("quality gate")[1].split("next")[0]

    assert gate.count("PyHellen down 19:41 → 20:14") == 1
    assert "whole phases" in gate
    assert "3 of 27" in gate
