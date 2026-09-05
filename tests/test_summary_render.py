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
    # The phase is named rather than assumed: three volumes lost their
    # enrichment here, and a run that lost modernization instead must not
    # be told to restart PyHellen.
    assert text.rstrip().endswith(
        "exit 5 — everything converted, but 3 volumes carry no enrich at all")


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

    long_headline = ("Completed with errors: 1/2 documents converted "
                     "(1 more skipped, already converted) "
                     "(1 more held no ALTO)")
    rendered_lines = render_summary(
        outcome(record=record, headline=long_headline), width=width)

    for line in rendered_lines:
        if long_headline in line:
            # The one exception, and it is data rather than chrome: the
            # cap keeps composed blocks scannable, and cutting the run's
            # accounting to fit would turn a number into a wrong number.
            continue
        assert len(line) <= min(width, 100), repr(line)

    assert any(long_headline in line for line in rendered_lines)


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


def test_a_failed_archive_is_named_in_the_list_and_counted_in_the_remedy():
    """It is a volume that did not convert, whatever shape it arrived in,
    so --retry-failed has to know about it."""
    text = rendered(outcome(volumes_written=26, exit_code=1,
                            failed_archives=(("x.zip", "not a zip"),)))

    assert "x.zip" in text and "not a zip" in text
    assert "convert the 1 volume that failed" in text
    assert "1 volumes" not in text


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


def test_a_denominator_is_summed_over_documents_and_not_taken_from_one():
    """Each document measures its own losses against its own total. Two
    volumes losing 3 of 754 pages each is 6 of 1 508, not 6 of 754 — and
    `render_count` refuses the second, which is how this was found."""
    record = RunRecord()
    for document in ("D1", "D2"):
        for page in ("f1", "f2", "f3"):
            record.add(Loss(Code.PAGE_UNUSABLE, document, "sourcedoc",
                            Locator.page(document, page), count=1, total=754))

    assert "6 of 1 508 pages" in rendered(outcome(record=record))


def test_one_document_is_still_measured_against_its_own_total():
    record = RunRecord()
    record.add(Loss(Code.PAGE_UNUSABLE, "D1", "sourcedoc",
                    Locator.page("D1", "f1"), count=3, total=754))

    assert "3 of 754 pages" in rendered(outcome(record=record))


def test_an_interruption_is_not_reported_as_a_failure():
    """Nothing was judged: the run was stopped. Saying "2 of 5 volumes not
    converted" would read as a verdict on a corpus nobody finished
    looking at."""
    text = rendered(outcome(volumes_written=2, exit_code=130))

    assert text.rstrip().endswith(
        "exit 130 — interrupted, 2 of 27 volumes written and kept")


def test_one_document_losing_the_same_way_twice_keeps_both_denominators():
    """Enrichment and modernization both record CONTAINER_FAILED for one
    volume, against their own totals. Keeping only the last one summed 53
    against a denominator of 4 — and `render_count` refuses that, which
    would end a four-hour run at its very last step."""
    record = RunRecord()
    record.add(Loss(Code.CONTAINER_FAILED, "D1", "enrich.refused",
                    Locator.document("D1"), count=50, total=1402))
    record.add(Loss(Code.CONTAINER_FAILED, "D1", "modernize",
                    Locator.document("D1"), count=3, total=4))

    assert "53 of 1 406 containers" in rendered(outcome(record=record))


def test_two_losses_of_one_step_still_share_one_denominator():
    """Several pages of one volume are measured against that volume's
    pages once, not once per page."""
    record = RunRecord()
    for page in ("f1", "f2"):
        record.add(Loss(Code.PAGE_UNUSABLE, "D1", "sourcedoc",
                        Locator.page("D1", page), count=1, total=754))

    assert "2 of 754 pages" in rendered(outcome(record=record))


def test_the_verdict_names_the_phase_that_was_actually_lost():
    """With VieuxParler down and PyHellen up, "3 volumes carry no
    enrichment at all" is false: enrichment is complete and it is
    modernization that is missing."""
    record = RunRecord()
    for index in range(3):
        record.add(Loss(Code.PHASE_LOST, f"D{index}", "modernize",
                        Locator.document(f"D{index}"), count=0, total=900,
                        detail="VieuxParler down"))

    text = rendered(outcome(record=record, exit_code=5, fail_on="incident"))

    assert "no modernize at all" in text
    assert "enrichment" not in text


def test_two_phases_lost_are_both_named():
    record = RunRecord()
    record.add(Loss(Code.PHASE_LOST, "D1", "enrich", Locator.document("D1"),
                    count=0, total=900, detail="PyHellen down"))
    record.add(Loss(Code.PHASE_LOST, "D2", "modernize",
                    Locator.document("D2"), count=0, total=900,
                    detail="VieuxParler down"))

    text = rendered(outcome(record=record, exit_code=5, fail_on="incident"))

    assert "enrich" in text and "modernize" in text


def test_two_causes_of_one_phase_share_that_phase_s_denominator():
    """Giving each cause its own step, so that two diagnoses survive, made
    the (document, step) denominator sum the SAME hundred containers
    twice: `92 of 200` for a volume that has a hundred."""
    record = RunRecord()
    record.add(Loss(Code.CONTAINER_FAILED, "D1", "enrich.broken",
                    Locator.document("D1"), count=2, total=100,
                    detail="the pipeline raised on these"))
    record.add(Loss(Code.CONTAINER_FAILED, "D1", "enrich.refused",
                    Locator.document("D1"), count=90, total=100,
                    detail="the service refused these"))

    text = rendered(outcome(record=record))

    assert "of 200" not in text
    assert "2 of 100 containers" in text
    assert "90 of 100 containers" in text


def test_each_cause_keeps_its_own_diagnosis():
    """`2 broken + 90 refused` printed "92 containers — the pipeline
    raised on these": the right count under the wrong cause."""
    record = RunRecord()
    record.add(Loss(Code.CONTAINER_FAILED, "D1", "enrich.broken",
                    Locator.document("D1"), count=2, total=100,
                    detail="the pipeline raised on these"))
    record.add(Loss(Code.CONTAINER_FAILED, "D1", "enrich.refused",
                    Locator.document("D1"), count=90, total=100,
                    detail="the service refused these"))

    text = rendered(outcome(record=record))

    assert "raised on these" in text and "refused these" in text


def test_two_phases_of_one_volume_still_add_their_own_totals():
    record = RunRecord()
    record.add(Loss(Code.CONTAINER_FAILED, "D1", "enrich.refused",
                    Locator.document("D1"), count=50, total=1402,
                    detail="the service refused these"))
    record.add(Loss(Code.CONTAINER_FAILED, "D1", "modernize",
                    Locator.document("D1"), count=3, total=4,
                    detail="the service refused these"))

    # One diagnosis, two phases: 53 of 1 406.
    assert "53 of 1 406 containers" in rendered(outcome(record=record))
