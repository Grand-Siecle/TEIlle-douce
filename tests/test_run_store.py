# -----------------------------------------------------------
# What a run leaves behind for the reader who comes back on Thursday.
#
#   tei_output/.teille-douce/runs/20260903-180824/
#       run.json         settings, argv, status per document
#       incidents.jsonl  one incident per line, append-only, greppable
#       pipeline.log     this run's log, kept beside its own index
#
# The log is the transcript and the JSONL is its index. No fact is stored
# twice in two forms that could diverge: the record carries structured
# fields, the log carries the prose and the tracebacks.
#
# Written line by line, from the parent only. `execute` catches
# `Exception`; a KeyboardInterrupt is a BaseException and goes straight
# through it, so a Ctrl-C in the fourth hour used to lose everything the
# run knew.
#
# Run: venv/bin/python -m pytest tests/test_run_store.py -q
# -----------------------------------------------------------
import json
from datetime import datetime
from pathlib import Path

import pytest

from teille_douce.report.record import Code, Locator, Loss
from teille_douce.report.store import RunStore


def a_store(tmp_path, when=None):
    return RunStore(tmp_path / "tei_output",
                    now=when or datetime(2026, 9, 3, 18, 8, 24))


def a_loss(document="LIV0038", code=Code.PHASE_LOST):
    return Loss(code, document, "enrich", Locator.document(document),
                count=0, total=1402, detail="PyHellen down since 19:41")


# =============================================================================
# Where it lives
# =============================================================================

def test_a_run_gets_its_own_directory_named_for_when_it_started(tmp_path):
    store = a_store(tmp_path)

    assert store.path == (tmp_path / "tei_output" / ".teille-douce" / "runs"
                          / "20260903-180824")


def test_the_directory_is_not_created_until_something_is_written(tmp_path):
    """A run that refuses to start leaves nothing behind — the same rule
    the lazy log handler already follows."""
    store = a_store(tmp_path)

    assert not store.path.exists()


def test_writing_an_incident_creates_the_directory(tmp_path):
    store = a_store(tmp_path)

    store.incident(a_loss())

    assert (store.path / "incidents.jsonl").exists()


# =============================================================================
# The index
# =============================================================================

def test_an_incident_is_one_line_of_json(tmp_path):
    store = a_store(tmp_path)

    store.incident(a_loss())

    line, = (store.path / "incidents.jsonl").read_text(encoding="utf-8").splitlines()
    entry = json.loads(line)
    assert entry["code"] == "phase_lost"
    assert entry["document"] == "LIV0038"
    assert entry["locator"] == "LIV0038"
    assert entry["detail"] == "PyHellen down since 19:41"


def test_each_incident_is_flushed_as_it_happens(tmp_path):
    """The whole reason for JSONL. A Ctrl-C in the fourth hour must not
    take four hours of knowledge with it."""
    store = a_store(tmp_path)

    store.incident(a_loss("D1"))
    written = (store.path / "incidents.jsonl").read_text(encoding="utf-8")

    assert "D1" in written, "the line was still in a buffer"


def test_incidents_accumulate_rather_than_replace(tmp_path):
    store = a_store(tmp_path)

    store.incident(a_loss("D1"))
    store.incident(a_loss("D2"))

    assert len((store.path / "incidents.jsonl").read_text(
        encoding="utf-8").splitlines()) == 2


def test_only_incidents_are_indexed(tmp_path):
    """Blocks 1 and 2 are counted in the summary and belong in the log.
    An index of everything is an index of nothing."""
    store = a_store(tmp_path)

    store.incident(a_loss(code=Code.PAGE_UNUSABLE))

    assert not (store.path / "incidents.jsonl").exists()


# =============================================================================
# The manifest
# =============================================================================

def test_the_manifest_records_what_was_asked_and_what_happened(tmp_path):
    store = a_store(tmp_path)

    store.finish(argv=["teille-douce", "run", "--fast"],
                 settings={"paths.output": {"value": "tei_output",
                                            "origin": "flag"}},
                 documents={"D1": "ok", "D2": "failed"},
                 exit_code=1)

    manifest = json.loads((store.path / "run.json").read_text(encoding="utf-8"))
    assert manifest["argv"] == ["teille-douce", "run", "--fast"]
    assert manifest["documents"]["D2"] == "failed"
    assert manifest["exit_code"] == 1
    assert manifest["settings"]["paths.output"]["origin"] == "flag"


def test_the_manifest_is_written_even_when_the_run_was_interrupted(tmp_path):
    store = a_store(tmp_path)

    store.finish(argv=[], settings={}, documents={"D1": "ok"}, exit_code=130)

    manifest = json.loads((store.path / "run.json").read_text(encoding="utf-8"))
    assert manifest["exit_code"] == 130


# =============================================================================
# Coming back three days later
# =============================================================================

def test_the_latest_run_is_findable(tmp_path):
    output = tmp_path / "tei_output"
    for stamp in ("20260901-100000", "20260903-180824", "20260902-090000"):
        (output / ".teille-douce" / "runs" / stamp).mkdir(parents=True)

    assert RunStore.latest(output).name == "20260903-180824"


def test_there_may_be_no_previous_run(tmp_path):
    assert RunStore.latest(tmp_path / "tei_output") is None


def test_the_documents_the_last_run_failed_can_be_asked_for(tmp_path):
    store = a_store(tmp_path)
    store.finish(argv=[], settings={},
                 documents={"D1": "ok", "D2": "failed", "D3": "failed"},
                 exit_code=1)

    assert RunStore.failed_last_time(tmp_path / "tei_output") == ("D2", "D3")


def test_no_previous_run_means_nothing_to_retry(tmp_path):
    assert RunStore.failed_last_time(tmp_path / "tei_output") == ()


# =============================================================================
# Retention
# =============================================================================

def test_only_the_last_ten_runs_are_kept(tmp_path):
    output = tmp_path / "tei_output"
    for day in range(1, 15):
        (output / ".teille-douce" / "runs" / f"202609{day:02d}-100000").mkdir(
            parents=True)

    RunStore.prune(output, keep=10)

    kept = sorted(p.name for p in
                  (output / ".teille-douce" / "runs").iterdir())
    assert len(kept) == 10
    assert kept[0] == "20260905-100000"


def test_pruning_removes_the_whole_directory_not_just_the_index(tmp_path):
    """An index and its transcript must not be able to diverge, so they
    are deleted together or not at all."""
    output = tmp_path / "tei_output"
    old = output / ".teille-douce" / "runs" / "20260901-100000"
    old.mkdir(parents=True)
    (old / "pipeline.log").write_text("a transcript", encoding="utf-8")
    (output / ".teille-douce" / "runs" / "20260902-100000").mkdir()

    RunStore.prune(output, keep=1)

    assert not old.exists()


def test_pruning_a_directory_that_is_not_there_is_not_an_error(tmp_path):
    RunStore.prune(tmp_path / "tei_output", keep=10)


# =============================================================================
# The store may not be the thing that ends a four-hour job
# =============================================================================

def test_a_directory_it_cannot_write_to_does_not_kill_the_run(tmp_path):
    """The same rule the lazy log handler follows: a reporter that cannot
    record has to say so, not raise. Here the output directory is a file,
    which is the shape a mistyped `-o` takes."""
    (tmp_path / "tei_output").write_text("not a directory", encoding="utf-8")
    store = a_store(tmp_path)

    store.incident(a_loss())
    store.finish(argv=[], settings={}, documents={}, exit_code=0)

    assert store.unwritable, "the failure was swallowed instead of recorded"


def test_it_says_so_only_once(tmp_path):
    """One warning about the store, not one per incident of a four-hour
    run."""
    (tmp_path / "tei_output").write_text("not a directory", encoding="utf-8")
    store = a_store(tmp_path)

    store.incident(a_loss())
    store.incident(a_loss())

    assert len(store.problems) == 1


def test_pruning_cannot_raise_either(tmp_path):
    (tmp_path / "tei_output").write_text("not a directory", encoding="utf-8")

    RunStore.prune(tmp_path / "tei_output", keep=1)


# =============================================================================
# The log lives beside its own index
# =============================================================================

def test_the_log_is_moved_in_beside_its_own_index(tmp_path):
    """Twenty-one orphan logs accumulated at the root of the repository
    because `log_file` was the one setting with no home. Moved at the end
    rather than written there from the start: the directory sits under
    the output directory, and creating it while the run may still refuse
    would break the promise that exit 3 leaves nothing behind."""
    store = a_store(tmp_path)
    log = tmp_path / "pipeline_20260903.log"
    log.write_text("the transcript", encoding="utf-8")

    moved = store.adopt_log(log)

    assert moved == store.path / "pipeline.log"
    assert moved.read_text(encoding="utf-8") == "the transcript"
    assert not log.exists(), "the orphan stayed at the root"


def test_a_run_with_no_log_has_nothing_to_move(tmp_path):
    assert a_store(tmp_path).adopt_log(None) is None


def test_a_log_that_cannot_be_moved_is_left_where_it_is(tmp_path):
    """A log where it was is still a log; losing the run over a rename
    would not be."""
    (tmp_path / "tei_output").write_text("not a directory", encoding="utf-8")
    store = a_store(tmp_path)
    log = tmp_path / "pipeline.log"
    log.write_text("x", encoding="utf-8")

    assert store.adopt_log(log) == log
    assert log.exists()
