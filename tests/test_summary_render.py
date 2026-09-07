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


def _exempt(line, headline="", room=10 ** 6):
    """The three kinds of line the module says it will not cut, and why.

    The headline and the verdict are the run's accounting — a cut number
    is a wrong number. A `next` command is meant to be copy-pasted, and
    `cat …/incidents.jsonl…` is not a shorter command but one that does
    not run. All three wrap in a terminal and lose nothing.
    """
    import re

    from teille_douce.report.text import cells

    bare = line.lstrip()
    # The headline is exempt whatever it says — `run.py` composes it, and
    # a bare `26/27 documents converted` is as much a headline as
    # `Done. …`. Matching its usual prefixes let the plainest form of it
    # through the sweep.
    if headline and bare == headline.strip():
        return True
    return (bare.startswith(("exit ", "Done.", "Completed", "Interrupted:"))
            or bare.startswith(("cat ", "teille-douce "))
            # A bare count on a line of its own: what is left when the
            # terminal is narrower than the figure it is being told. The
            # same rule as the two above — a number is printed whole or
            # not at all.
            #
            # Exempt only where the FIGURE itself does not fit. Exempting
            # the whole line let its indent grow past the margin unseen,
            # which is the one of the three margin call sites the test
            # written for them could not reach.
            or (bool(re.fullmatch(r"[\d\u202f ]+ of [\d\u202f ]+ \w+", bare))
                and cells(bare) > room))


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

    # The MARKER, not the label. `"repaired" in text` was satisfied by
    # `ALTO ids repaired`, the name of the code, so the marker that says
    # nothing was lost to it could be deleted with the test still green.
    assert "(repaired)" in text
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


def test_an_incident_points_at_the_command_that_reads_the_index():
    """It used to point at the file itself, because `teille-douce
    report` did not exist and a run whose last line exits 2 with
    "unrecognized arguments" has spent it making itself less
    trustworthy. It exists now — and it names the run rather than
    trusting "the last one", since a nightly may finish between this
    line being printed and somebody typing it."""
    record = RunRecord()
    record.add(Loss(Code.PHASE_LOST, "D1", "enrich", Locator.document("D1"),
                    count=0, total=1402, detail="PyHellen down"))

    shown = rendered(outcome(record=record,
                             report_path=Path("tei_output/.teille-douce/runs/r1")))

    assert "teille-douce report --run r1" in shown
    assert "incidents.jsonl" not in shown


def test_the_command_offered_carries_the_output_directory_it_needs():
    """`report` resolves the output directory through the same four
    layers, so a run written with `-o tei_test` must not end by offering
    a command that answers about `tei_output` — exit 3, and a last line
    that teaches distrust. The default is left off: it is the one value
    `report` will reach on its own."""
    record = RunRecord()
    record.add(Loss(Code.PHASE_LOST, "D1", "enrich", Locator.document("D1"),
                    count=0, total=1402, detail="PyHellen down"))

    elsewhere = rendered(outcome(
        record=record, output_dir=Path("tei_test"),
        report_path=Path("tei_test/.teille-douce/runs/r1")))
    at_home = rendered(outcome(
        record=record, output_dir=Path("tei_output"),
        report_path=Path("tei_output/.teille-douce/runs/r1")))

    assert "--run r1 -o tei_test" in elsewhere
    assert " -o " not in at_home.split("next")[1]


def test_no_index_is_offered_when_no_record_was_kept():
    """A read-only output directory keeps none, and pointing at a file
    that is not there is worse than saying nothing."""
    record = RunRecord()
    record.add(Loss(Code.PHASE_LOST, "D1", "enrich", Locator.document("D1"),
                    count=0, total=1402, detail="PyHellen down"))

    assert "incidents.jsonl" not in rendered(
        outcome(record=record, report_path=None))


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


# =============================================================================
# The right column of a summary line is the half that carries the news
# =============================================================================

def test_two_losses_that_differ_only_in_their_cause_do_not_render_alike():
    """`_columns` dropped the right half with no marker when the line did
    not fit, and the right half is the only thing that tells two losses
    sharing a code apart — which is the entire reason the grouping key is
    (code, detail). At eighty columns, the width a piped run gets, two
    different causes rendered as the same line."""
    record = RunRecord()
    for detail in ("PyHellen refused these containers",
                   "VieuxParler refused these containers"):
        record.add(Loss(Code.CONTAINER_FAILED, "LIV0038_reconciled", "enrich",
                        Locator.document("LIV0038_reconciled"), count=1402,
                        total=4000, detail=detail))

    shown = render_summary(outcome(record=record), width=80)

    assert sum("containers failed" in line for line in shown) == 2
    # Both causes are readable, whether they share their entry's line or
    # sit under it: dropping one was what made the two entries identical.
    assert any("PyHellen refused" in line for line in shown), shown
    assert any("VieuxParler refused" in line for line in shown), shown


def test_a_two_line_failure_reason_stays_one_line():
    """An httpx reason is two lines. Sliced by characters with the
    control characters left in, one FAILED record became two screen
    lines, the second a bare fragment — and FAILED is the token every
    wrapper this project has greps for."""
    shown = render_summary(outcome(
        volumes_written=26, exit_code=1,
        failed_documents=(("LIV0038_reconciled",
                           "Server disconnected\nwithout a response"),)),
        width=92)

    assert not any("\n" in line for line in shown)
    failed, = [line for line in shown if "FAILED" in line]
    assert "without a response" not in failed or " " in failed


def test_a_wide_glyph_is_measured_in_columns_and_not_in_characters():
    """`len()` over CJK let the line run off the edge of the very
    terminals the width contract was written for."""
    from teille_douce.report.text import cells

    record = RunRecord()
    record.add(Loss(Code.PAGE_UNUSABLE, "漢字巻" * 12, "sourcedoc",
                    Locator.document("漢字巻" * 12), count=1, total=2,
                    detail="出力ディレクトリが読めない"))

    for width in (56, 72, 80, 92, 100, 140):
        shown = outcome(record=record)
        for line in render_summary(shown, width=width):
            if _exempt(line, shown.headline):
                continue
            assert cells(line) <= min(width, 100), (width, cells(line), line)


def test_the_widest_summary_this_pipeline_can_produce_still_fits():
    """The fixture of the test above never reaches a phase loss, a failed
    volume, a quality gate or a next step — which is most of the lines
    the module can compose, and where the two that escaped the cap
    were."""
    from teille_douce.report.text import cells

    record = RunRecord()
    for index in range(3):
        document = f"LIV{index:04d}_reconciled"
        for phase in ("enrich", "modernize", "ner"):
            record.add(Loss(Code.PHASE_LOST, document, phase,
                            Locator.document(document), count=1402,
                            total=1402,
                            detail="the service stopped answering at 19:41"))
    record.add(Loss(Code.PAGE_UNUSABLE, "LIV0044_reconciled", "sourcedoc",
                    Locator.page("LIV0044_reconciled", "f388"), count=388,
                    total=400, detail="no <surface> could be built"))

    full = outcome(
        record=record, exit_code=5, fail_on="loss", volumes_written=3,
        page_loss_failures=(("LIV0044_reconciled", 97.0),),
        max_page_loss=30.0,
        failed_documents=(("LIV0051_reconciled", "KeyError 'Date_edition'"),),
        report_path=Path("tei_output/.teille-douce/runs/20260903-180824-0031415"))

    for width in (40, 56, 72, 80, 92, 100, 140):
        for line in render_summary(full, width=width):
            if _exempt(line, full.headline):
                continue
            assert cells(line) <= min(width, 100), (width, cells(line), line)


def test_one_volume_is_not_written_as_one_volumes():
    """A negative assertion satisfied by absence is not an assertion:
    the fixture left `fail_on="never"`, so `_verdict` short-circuited
    and the sentence under test was never composed. It asserts the
    sentence now, and its agreement with its own noun."""
    record = RunRecord()
    record.add(Loss(Code.PHASE_LOST, "D1", "enrich", Locator.document("D1"),
                    count=1402, total=1402, detail="PyHellen down"))

    shown = render_summary(outcome(record=record, exit_code=5,
                                   fail_on="incident"), width=92)
    verdict = next(line for line in shown if line.lstrip().startswith("exit "))

    assert "1 volume carries no enrich at all" in verdict, verdict

    for index in range(2, 4):
        many = RunRecord()
        for number in range(index):
            document = f"D{number}"
            many.add(Loss(Code.PHASE_LOST, document, "enrich",
                          Locator.document(document), count=1402, total=1402,
                          detail="PyHellen down"))
        spoken = "\n".join(render_summary(
            outcome(record=many, exit_code=5, fail_on="incident"), width=92))
        assert f"{index} volumes carry no enrich at all" in spoken, spoken


def test_the_count_is_the_last_thing_a_narrow_line_gives_up():
    """`_entry`'s two-line form spent the second line on the cause and
    clipped the count instead: `1 402 of 4 …` at forty columns — the
    `11 of 16 pages → 11 of 1…` case this module exists to make
    impossible, reproduced inside its own remedy. The order is fixed:
    the alignment column goes first, then the cause moves to a line of
    its own, then the label is shortened. The count never gives way."""
    from teille_douce.report.text import cells

    record = RunRecord()
    record.add(Loss(Code.CONTAINER_FAILED, "D1", "enrich",
                    Locator.document("D1"), count=1402, total=4000,
                    detail="the pipeline raised on these"))

    for width in range(40, 101):
        shown = render_summary(outcome(record=record), width=width)
        assert any("1 402 of 4 000 containers" in line for line in shown), (
            width, [l for l in shown if "containers" in l])
        for line in shown:
            if not _exempt(line):
                assert cells(line) <= min(width, 100), (width, line)


def test_a_verdict_never_names_a_cause_the_level_does_not_count():
    """`--fail-on never --max-page-loss 30` is a supported pair. A run
    that also lost a phase named it on the last line — the one wrappers
    grep — over a gate block that had correctly named the one bar it
    failed."""
    record = RunRecord()
    record.add(Loss(Code.PHASE_LOST, "LIV0001", "enrich",
                    Locator.document("LIV0001"), count=1402, total=1402,
                    detail="PyHellen down"))

    shown = rendered(outcome(
        record=record, exit_code=5, fail_on="never",
        page_loss_failures=(("LIV0044", 40.0),), max_page_loss=30.0))

    assert "lost too many pages" in shown
    assert "carries no enrich" not in shown, shown


def test_the_page_accounting_survives_an_absolute_output_path():
    """`→ <out>` and `N of M pages written` went through the block-entry
    rule, where the LEFT is the load-bearing half — so an absolute `-o`,
    which the panel rightly calls the ordinary case, dropped the run's
    page count off the summary at every width from fifty-six to
    ninety-five, the default included. Every test used a short relative
    path, so the suite could not see it."""
    absolute = Path("/home/rayondemiel/univ_geneve/test_tei_ouput/tei_output")

    for width in range(48, 121):
        shown = render_summary(outcome(output_dir=absolute), width=width)
        # Below about fifty columns the count takes a line of its own,
        # and drops the word: the arrow line above already says these
        # are what was written, and the eight figures are what matter.
        # Asserted as a pair rather than as the weaker substring the
        # rewrite left behind — `pages` alone also matches `0/16 999
        # pages` in the bar.
        assert any("16 999 of 16 999 pages" in line for line in shown), (
            width, shown[:8])
        assert any(line.rstrip().endswith("written") or
                   line.rstrip().endswith("16 999 pages") for line in shown), (
            width, shown[:8])


def test_a_label_of_exactly_the_column_width_keeps_its_separator():
    """`{label:<22}` pads to twenty-two and stops, so a label of exactly
    that ran into its own number: `..._tome_II30.03%`. This corpus's
    longest volume name is twenty-one characters."""
    from teille_douce.report.summary import _entry

    for length in range(18, 30):
        line, = _entry("L" * length, "30.03%", "", 120)
        assert "L30.03%" not in line, (length, line)
        assert "  30.03%" in line, (length, line)


def test_a_seven_figure_count_is_not_clipped_at_a_narrow_width():
    """`max(1, room - cells(measured) - 6)` clamped, the composed line
    then exceeded the room, and the clip below cut the count —
    `3 of 1 402 volumes · 1 699 998 containers lo…` at every width under
    fifty-five."""
    record = RunRecord()
    record.add(Loss(Code.CONTAINER_FAILED, "D1", "enrich",
                    Locator.document("D1"), count=1699998, total=1699998,
                    detail="the pipeline raised on these"))

    for width in range(36, 101):
        shown = render_summary(outcome(record=record), width=width)
        assert any("1 699 998 of 1 699 998 containers" in line
                   for line in shown), (width, [l for l in shown if "699" in l])


def test_a_loss_measured_in_lines_is_not_relabelled_batches():
    """The label table says `batch_failed` counts batches; a retry the
    service never answered is counted in lines, and the loss's own unit
    was honoured for `PHASE_LOST` alone — so the summary printed
    `57 of 1 402 batches` over two numbers that are lines."""
    record = RunRecord()
    record.add(Loss(Code.RETRY_UNANSWERED, "D1", "modernize.retry",
                    Locator.document("D1"), count=57, total=1402,
                    detail="VieuxParler did not answer the retry"))

    shown = rendered(outcome(record=record))

    assert "57 of 1 402 lines" in shown
    assert "batches" not in shown


# =============================================================================
# The three lines that carry a number, at every width
# =============================================================================

def test_the_page_accounting_keeps_both_its_count_and_its_path():
    """Rewritten once already for this: `pad(keep="right")` clips the
    COMPOSITE, so below thirty-eight columns it cut the count and above
    that it left a path that was one ellipsis and no arrow."""
    absolute = Path("/home/rayondemiel/univ_geneve/test_tei_ouput/tei_output")

    for width in range(28, 121):
        shown = "\n".join(render_summary(outcome(output_dir=absolute),
                                         width=width))
        assert "16 999 of 16 999 pages" in shown, (width, shown[:400])
        assert "→" in shown, (width, shown[:400])
        assert "tei_output" in shown, (width, shown[:400])


def test_the_located_line_prints_both_figures_whole():
    """`clip` took the second apart mid-number below forty-two columns.
    Its own docstring says both print even at zero, because a blind spot
    has to be a visible line — and half a figure is not one."""
    record = RunRecord()
    record.add(Loss(Code.PAGE_UNUSABLE, "D1", "sourcedoc",
                    Locator.page("D1", "f1"), count=1699998, total=2779999))
    record.add(Loss(Code.READING_REJECTED, "D2", "modernize",
                    Locator.document("D2"), count=2779000, total=2779999))

    for width in range(28, 121):
        shown = "\n".join(render_summary(outcome(record=record), width=width))
        assert "1 699 998" in shown, (width, shown)
        assert "2 779 000" in shown, (width, shown)


def test_every_line_of_a_summary_fits_its_terminal():
    """The three exemptions are the module's own, and they are one rule:
    a number is printed whole or not at all."""
    from teille_douce.report.text import cells

    record = RunRecord()
    record.add(Loss(Code.CONTAINER_FAILED, "D1", "enrich",
                    Locator.document("D1"), count=1699998, total=1699998,
                    detail="the pipeline raised on these"))

    shown = outcome(record=record,
                    output_dir=Path("/home/rayondemiel/tei_output"),
                    headline="26/27 documents converted")
    for width in range(24, 141):
        for line in render_summary(shown, width=width):
            if _exempt(line, shown.headline, min(width, 100)):
                continue
            assert cells(line) <= min(width, 100), (width, cells(line), line)


def test_no_line_of_a_summary_ends_in_whitespace():
    """The same strings go into a log file, where trailing runs of spaces
    are noise — which is what `_MARGIN`'s own comment says."""
    record = RunRecord()
    record.add(Loss(Code.DOCUMENT_FAILED, "D1", "run", Locator.document("D1"),
                    count=1, total=1, detail=""))

    for width in range(28, 121):
        for line in render_summary(
                outcome(record=record, exit_code=4, volumes_written=0,
                        failed_documents=(("D1", ""),)), width=width):
            assert line == line.rstrip(), (width, repr(line))


def test_every_composed_line_leaves_the_margin_the_module_reserves():
    """Two columns of blank at the right edge, so the eye can find the
    end of a line. The page-accounting line alone passed the bare room,
    and ran to a hundred cells where its neighbours stop at ninety-eight
    — invisible to every width assertion, which all compare against the
    terminal rather than against the margin."""
    from teille_douce.report.summary import MAX_WIDTH, _MARGIN
    from teille_douce.report.text import cells

    record = RunRecord()
    record.add(Loss(Code.PAGE_UNUSABLE, "D1", "sourcedoc",
                    Locator.page("D1", "f1"), count=388, total=400,
                    detail="no <surface> could be built"))

    for width in range(24, 141):
        room = min(width, MAX_WIDTH) - _MARGIN
        # With a failed volume and a `next` block: those are two of the
        # three call sites the margin fix touched, and a fixture without
        # them renders neither — the "fixture that never reached a
        # failed volume or a next step" shape, inside the test written
        # to replace it.
        shown = outcome(record=record,
                        output_dir=Path("/home/rayondemiel/univ/tei_output"),
                        headline="26/27 documents converted",
                        volumes_written=26, exit_code=1,
                        failed_documents=(("BDD_1685_Felibien_tome_II",
                                           "KeyError 'Date_edition'"),),
                        report_path=Path(
                            "tei_output/.teille-douce/runs/20260903-180824-0031415"))
        for line in render_summary(shown, width=width):
            # A rule spans the width on purpose; the margin is for the
            # lines that carry words.
            if (_exempt(line, shown.headline, room) or not line.strip()
                    or set(line) <= {"═", "─"}):
                continue
            assert cells(line) <= room, (width, cells(line), room, line)


def test_a_block_with_nothing_in_it_says_nothing_rather_than_showing_none():
    """A heading with an empty space under it cannot be told from a
    phase that was never checked, which is the whole reason the blocks
    are named rather than implied. The test above asserts the headings;
    this asserts the word under them."""
    shown = render_summary(outcome(), width=92)
    headings = [i for i, line in enumerate(shown)
                if line.startswith(("  the source was defective",
                                    "  withheld on purpose",
                                    "  lost to an incident"))]

    assert len(headings) == 3
    for at in headings:
        assert shown[at + 1].strip() == "nothing", (at, shown[at + 1])


def test_the_summary_prints_the_headline_it_was_given_and_not_its_own():
    """One account of the run: the sentence every wrapper greps and every
    accounting test balances is composed once, by the run, and the report
    takes it. A second fraction derived here would be a second account
    the reader has no way to choose between."""
    spoken = rendered(outcome(headline="Completed with errors: 25/27 "
                                       "documents converted"))

    assert "Completed with errors: 25/27 documents converted" in spoken


def test_the_command_offered_survives_a_space_in_the_path():
    """`-o tei out` pasted into a shell reads `out` as the DOC
    positional: exit 3, "no run recorded in tei" — the last line that
    teaches distrust, which is the thing the `-o` is there to prevent."""
    record = RunRecord()
    record.add(Loss(Code.PHASE_LOST, "D1", "enrich", Locator.document("D1"),
                    count=0, total=1402, detail="PyHellen down"))

    shown = rendered(outcome(
        record=record, output_dir=Path("/srv/tei out"),
        report_path=Path("/srv/tei out/.teille-douce/runs/r1")))

    assert "-o '/srv/tei out'" in shown


def test_every_command_offered_carries_the_paths_this_run_used():
    """`--retry-failed` without `-o` resolves the output directory
    through the four layers and reads a different run's manifest: pasted
    after a run written to `exports/`, it answers "nothing failed last
    time" and exits 3 over a volume that had just failed — the last line
    that teaches distrust, which is what the `-o` on the report line was
    added to prevent. It was added to one line of three.

    Each command gets the flags IT takes, and all of them: `-i` on
    `report`, which has none, made that line an `unrecognized
    arguments` usage error, and leaving the two catalogues off made a
    pasted `run` report "no catalogue row" for every volume. Both were
    this same rule, half applied.
    """
    record = RunRecord()
    record.add(Loss(Code.PHASE_LOST, "D1", "enrich", Locator.document("D1"),
                    count=0, total=1402, detail="PyHellen down"))

    shown = rendered(outcome(
        record=record, exit_code=1, volumes_written=26,
        input_dir=Path("/srv/ocr in"), output_dir=Path("/srv/tei out"),
        metadata_csv=Path("/srv/cat alogue.csv"),
        persons_csv=Path("/srv/per sons.csv"),
        failed_documents=(("LIV 0326", "KeyError"),),
        report_path=Path("/srv/tei out/.teille-douce/runs/r1")))

    offered = [said for said in shown.splitlines()
               if said.strip().startswith("teille-douce")]
    assert len(offered) == 3
    for line in offered:
        assert "-o '/srv/tei out'" in line, line
        if " report " in line:
            # `report` takes `-o` and nothing else of the four.
            assert " -i " not in line and "--metadata" not in line, line
        else:
            assert "-i '/srv/ocr in'" in line, line
            assert "--metadata '/srv/cat alogue.csv'" in line, line
            assert "--persons '/srv/per sons.csv'" in line, line
    # And the volume name is quoted too, or it becomes two selectors.
    assert "run 'LIV 0326' -vv" in shown


def test_every_command_offered_actually_parses():
    """Put through the real parser rather than eyeballed: `-i` on
    `report`, which has none, was an `unrecognized arguments` usage
    error in the last line of a run. Asserting the flags by name cannot
    catch it — `-vv` is two `-v` and is in no `option_strings` — and
    asserting from memory is how it got there. argparse is the
    authority, so argparse is asked.
    """
    import shlex

    from teille_douce.cli.app import build_parser, normalise

    record = RunRecord()
    record.add(Loss(Code.PHASE_LOST, "D1", "enrich", Locator.document("D1"),
                    count=0, total=1402, detail="PyHellen down"))
    shown = rendered(outcome(
        record=record, exit_code=1,
        input_dir=Path("/srv/ocr in"), output_dir=Path("/srv/tei out"),
        metadata_csv=Path("/srv/cat alogue.csv"),
        persons_csv=Path("/srv/per sons.csv"),
        failed_documents=(("LIV 0326", "KeyError"),),
        report_path=Path("/srv/tei out/.teille-douce/runs/r1")))

    offered = [shlex.split(line) for line in shown.splitlines()
               if line.strip().startswith("teille-douce ")]
    assert len(offered) == 3, offered
    for words in offered:
        assert words[0] == "teille-douce"
        # `shlex.split` undoes the quoting, so a path with a space
        # arrives as one word — which is the whole point of quoting it.
        parsed = build_parser().parse_args(normalise(words[1:]))
        assert parsed.command in ("run", "report")


def test_the_default_directories_are_not_repeated_back():
    """They are the one thing every command resolves on its own."""
    record = RunRecord()
    record.add(Loss(Code.PHASE_LOST, "D1", "enrich", Locator.document("D1"),
                    count=0, total=1402, detail="PyHellen down"))

    shown = rendered(outcome(
        record=record, exit_code=1,
        failed_documents=(("LIV0326", "KeyError"),),
        report_path=Path("tei_output/.teille-douce/runs/r1")))

    assert " -o " not in shown.split("next")[1]
    assert " -i " not in shown.split("next")[1]
