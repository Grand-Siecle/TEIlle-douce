# -----------------------------------------------------------
# When a run that converted everything is still not good enough.
#
# 27 of 27 volumes written, three of them with no annotation at all
# because PyHellen died at 19:41. Exiting 0 on that is defensible — it is
# the default — but the operator has to be able to demand better, or the
# only recourse is auditing the files one by one.
#
# "Important" is not a threshold to invent: the three blocks already
# define it. Block 3 is titled "this needs a human"; that IS the
# definition. Block 2 never counts — a guard that rejects a hallucination
# did its job, and making that a failure punishes the chain for being
# honest.
#
# Run: venv/bin/python -m pytest tests/test_quality_gate.py -q
# -----------------------------------------------------------
import pytest

from teille_douce.cli import app
from teille_douce.report.gate import EXIT_GATE_NOT_MET, gate_verdict
from teille_douce.report.record import Code, Locator, Loss, RunRecord


def record_with(*codes):
    record = RunRecord()
    for index, code in enumerate(codes):
        record.add(Loss(code, f"D{index}", "step", Locator.document(f"D{index}"),
                        count=1, total=10, detail="something"))
    return record


# =============================================================================
# What each level counts
# =============================================================================

def test_the_default_lets_a_lossy_run_succeed():
    """A degraded conversion is still a conversion, and this is a corpus
    of seventeenth-century OCR: block 1 is never empty."""
    verdict = gate_verdict("never", record_with(Code.PHASE_LOST,
                                                Code.PAGE_UNUSABLE))

    assert verdict.met is True


def test_incident_counts_only_the_block_that_needs_a_human():
    assert gate_verdict("incident", record_with(Code.PHASE_LOST)).met is False
    assert gate_verdict("incident", record_with(Code.PAGE_UNUSABLE)).met is True


def test_a_guard_doing_its_job_never_trips_any_level():
    """Punishing the chain for rejecting a hallucination would punish it
    for being honest."""
    withheld = record_with(Code.READING_REJECTED, Code.ENTITY_FILTERED,
                           Code.CONTAINER_UNANCHORED)

    assert gate_verdict("incident", withheld).met is True
    assert gate_verdict("loss", withheld).met is True


def test_loss_also_counts_what_the_source_got_wrong():
    """For a CI freezing an already-clean corpus, not for a daily
    production run — on this corpus there will always be unusable pages."""
    assert gate_verdict("loss", record_with(Code.PAGE_UNUSABLE)).met is False


def test_a_repaired_defect_does_not_trip_even_the_strictest_level():
    """Nothing was lost to it. A level that failed on repairs would fail
    on every run this corpus has ever produced."""
    assert gate_verdict("loss", record_with(Code.ALTO_IDS_REPAIRED)).met is True


def test_the_verdict_names_what_tripped_it():
    """Otherwise it turns a diagnosis into a guessing game."""
    verdict = gate_verdict("incident", record_with(Code.PHASE_LOST))

    assert Code.PHASE_LOST in verdict.tripped_by


# =============================================================================
# A volume that lost most of its pages is not a degraded success
# =============================================================================

def test_a_volume_losing_more_than_the_allowed_share_of_pages_is_a_failure():
    """The chain already fails a document whose pages are ALL unusable;
    this lowers that implicit 100 %. 388 of 400 pages illegible is not a
    degraded conversion, it is a file nobody wants to publish."""
    from teille_douce.report.gate import page_loss_failures

    failures = page_loss_failures({"D1": (388, 400), "D2": (2, 400)},
                                  max_page_loss=50)

    assert failures == (("D1", 97.0),)


def test_the_default_share_allows_everything_but_a_total_loss():
    from teille_douce.report.gate import page_loss_failures

    assert page_loss_failures({"D1": (399, 400)}, max_page_loss=100) == ()


def test_a_volume_with_no_pages_at_all_is_not_a_division_by_zero():
    from teille_douce.report.gate import page_loss_failures

    assert page_loss_failures({"D1": (0, 0)}, max_page_loss=50) == ()


# =============================================================================
# The exit code
# =============================================================================

def test_the_gate_code_is_only_reached_when_nothing_else_failed():
    """A failed volume gives 1, which is the more concrete fact and wins.
    The two differ in remedy: 1 is rerun with --retry-failed, 5 is look at
    what was lost and decide whether you accept it."""
    assert EXIT_GATE_NOT_MET == 5


def test_without_the_flag_the_gate_cannot_produce_anything():
    verdict = gate_verdict("never", record_with(Code.DOCUMENT_FAILED))

    assert verdict.met and verdict.tripped_by == ()


# =============================================================================
# The command line
# =============================================================================

def test_the_flag_reaches_the_settings():
    args = app.parse_args(["run", "--fail-on", "incident", "--no-config"])

    assert app.settings_from(args, env={}).fail_on == "incident"


def test_strict_is_the_incident_level_under_another_name():
    args = app.parse_args(["run", "--strict", "--no-config"])

    assert app.settings_from(args, env={}).fail_on == "incident"


def test_the_default_is_never():
    assert app.settings_from(app.parse_args(["run", "--no-config"]),
                             env={}).fail_on == "never"


def test_an_unknown_level_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exit:
        app.parse_args(["run", "--fail-on", "sometimes", "--no-config"])

    assert exit.value.code == 2


def test_the_page_loss_share_reaches_the_settings():
    args = app.parse_args(["run", "--max-page-loss", "20", "--no-config"])

    assert app.settings_from(args, env={}).max_page_loss == 20.0


def test_a_share_outside_zero_to_a_hundred_is_refused(capsys):
    with pytest.raises(SystemExit) as exit:
        app.settings_from(
            app.parse_args(["run", "--max-page-loss", "120", "--no-config"]),
            env={})

    assert exit.value.code == 2


# =============================================================================
# The level has to have something that can trip it
# =============================================================================

def test_every_incident_code_has_a_producer_somewhere():
    """`--fail-on incident` was advertised in --help and in the guide
    while nothing in the pipeline could emit a block-3 loss other than a
    failed document — which already exits 1 by itself. A gate that cannot
    fire is a gate that stops guarding without anyone noticing."""
    import pathlib

    from teille_douce.report.record import Block, Code

    # Two files are excluded and no more: the one that declares the codes
    # and the one that labels them for printing. A label is not a
    # producer, and counting it made this assertion pass over two codes
    # nothing in the pipeline could emit.
    not_producers = {"record.py", "summary.py"}
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in pathlib.Path("teille_douce").rglob("*.py")
        if path.name not in not_producers)

    unproduced = [code.name for code in Code
                  if code.block is Block.INCIDENT
                  and f"Code.{code.name}" not in sources]

    assert not unproduced, (
        f"declared and never emitted: {', '.join(unproduced)}")


def test_a_service_dying_is_what_the_incident_level_is_for():
    """Every volume converted, three of them carrying no annotation at
    all. Exit 0 on that is the default and defensible; demanding better
    has to be possible."""
    from teille_douce.report.gate import gate_verdict
    from teille_douce.report.record import Code as C

    assert gate_verdict("incident", record_with(C.PHASE_LOST)).met is False
    assert gate_verdict("incident", record_with(C.CONTAINER_FAILED)).met is False
    assert gate_verdict("incident", record_with(C.VOLUME_UNREADABLE)).met is False


# =============================================================================
# The block under NOT MET says what tripped the gate, and only that
# =============================================================================

def test_the_gate_block_does_not_blame_a_repair():
    """It re-derived its lines from the blocks the LEVEL names and
    printed every one of them — including the repaired defects
    `gate_verdict` deliberately skips, and a literal "nothing" under NOT
    MET. A gate that names a repair as its cause is worse than one that
    names nothing."""
    from pathlib import Path

    from teille_douce.report.record import Locator, Loss, RunRecord
    from teille_douce.report.summary import RunOutcome, render_summary

    record = RunRecord()
    record.add(Loss(Code.PAGE_UNUSABLE, "D1", "sourcedoc",
                    Locator.page("D1", "f4"), count=3, total=8,
                    detail="no <surface> could be built"))
    record.add(Loss(Code.ALTO_IDS_REPAIRED, "D1", "sourcedoc",
                    Locator.page("D1", "f2"), count=14, total=128,
                    detail="duplicates disambiguated"))

    shown = render_summary(RunOutcome(
        input_dir=Path("OCR"), output_dir=Path("out"), volumes_total=1,
        volumes_written=1, pages_total=8, pages_written=5, record=record,
        elapsed=3.0, exit_code=5, fail_on="loss"), width=92)
    after = shown[shown.index(next(l for l in shown if "quality gate" in l)):]

    assert any("pages unusable" in line for line in after), after
    assert not any("repaired" in line for line in after), after
    assert not any(line.strip() == "nothing" for line in after), after


def test_a_share_is_never_printed_as_equal_to_the_bar_it_cleared():
    """The comparison is strict and the display was rounded to one place,
    so 300 of 999 pages — 30.03 % — printed as "30% of its pages
    unusable" against a rule documented as "more than 30": the run
    contradicting its own threshold on the line that names it."""
    from pathlib import Path

    from teille_douce.report.gate import page_loss_failures
    from teille_douce.report.record import RunRecord
    from teille_douce.report.summary import RunOutcome, render_summary

    failures = page_loss_failures({"LIV0044": (300, 999)}, 30.0)

    shown = render_summary(RunOutcome(
        input_dir=Path("OCR"), output_dir=Path("out"), volumes_total=1,
        volumes_written=1, pages_total=999, pages_written=699,
        record=RunRecord(), elapsed=3.0, exit_code=5, fail_on="never",
        page_loss_failures=failures, max_page_loss=30.0), width=92)

    assert any("30.03% of its pages unusable" in line for line in shown), shown
