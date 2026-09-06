# -----------------------------------------------------------
# What a run leaves behind for the reader who comes back on Thursday.
#
#   tei_output/.teille-douce/runs/20260903-180824-0031415/
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

    import os

    # The pid too: two runs started inside the same second would
    # otherwise share a directory and destroy each other's record.
    assert store.path == (tmp_path / "tei_output" / ".teille-douce" / "runs"
                          / f"20260903-180824-{os.getpid():07d}")


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
    run — deduplicated rather than latched, so the other artefacts are
    still attempted."""
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


# =============================================================================
# Two runs at once
# =============================================================================

def test_two_runs_in_the_same_second_do_not_share_a_directory(monkeypatch):
    """`_run_log_path` puts a pid in the log name for exactly this
    reason. Without one here, the second run's `Path.replace` destroys
    the first's log and its `run.json` overwrites the first's manifest —
    and the first exits 0 having printed a path to a record that is not
    its own."""
    import os
    from datetime import datetime

    instant = datetime(2026, 9, 3, 18, 8, 24)
    monkeypatch.setattr(os, "getpid", lambda: 4711)
    one = RunStore(Path("out"), now=instant).path
    monkeypatch.setattr(os, "getpid", lambda: 4712)
    other = RunStore(Path("out"), now=instant).path

    assert one != other


def test_the_directory_still_sorts_by_when_it_started(monkeypatch):
    """`latest()` sorts on the name, so the pid must come after the
    timestamp or the newest run stops being the last."""
    import os
    from datetime import datetime

    monkeypatch.setattr(os, "getpid", lambda: 999999)
    early = RunStore(Path("out"), now=datetime(2026, 9, 3, 10, 0, 0)).path
    monkeypatch.setattr(os, "getpid", lambda: 1)
    late = RunStore(Path("out"), now=datetime(2026, 9, 3, 11, 0, 0)).path

    assert early.name < late.name

    # And within one second the pid orders numerically: unpadded, "1234"
    # sorts before "987", so the newer of two concurrent runs was missed
    # by --retry-failed and deleted first by the retention.
    instant = datetime(2026, 9, 3, 12, 0, 0)
    monkeypatch.setattr(os, "getpid", lambda: 987)
    first = RunStore(Path("out"), now=instant).path
    monkeypatch.setattr(os, "getpid", lambda: 1234)
    second = RunStore(Path("out"), now=instant).path

    assert first.name < second.name


# =============================================================================
# One failure must not cost the whole record
# =============================================================================

def test_a_failed_incident_write_does_not_abandon_the_manifest(tmp_path):
    """A single blip at minute three of a four-hour run took the manifest
    with it — and with the manifest, `--retry-failed`."""
    store = a_store(tmp_path)
    store._ready()
    (store.path / "incidents.jsonl").mkdir()

    store.incident(a_loss())
    store.finish(argv=[], settings={}, documents={"D1": "ok"}, exit_code=0)

    assert (store.path / "run.json").exists()
    assert store.problems, "the failure was swallowed"


def test_a_log_that_cannot_be_moved_does_not_abandon_the_manifest(tmp_path):
    """cwd and `-o` on different filesystems is an ordinary layout, and
    `Path.replace` across them raises EXDEV."""
    store = a_store(tmp_path)
    log = tmp_path / "pipeline.log"
    log.write_text("x", encoding="utf-8")

    def refuse(self, target):
        raise OSError(18, "Invalid cross-device link")

    import pathlib as _pathlib
    original = _pathlib.Path.replace
    _pathlib.Path.replace = refuse
    try:
        store.adopt_log(log)
        store.finish(argv=[], settings={}, documents={}, exit_code=0)
    finally:
        _pathlib.Path.replace = original

    assert (store.path / "run.json").exists()


# =============================================================================
# A manifest that cannot be read is not a run with no failures
# =============================================================================

def test_a_corrupt_manifest_is_not_reported_as_nothing_to_retry(tmp_path):
    store = a_store(tmp_path)
    store.finish(argv=[], settings={}, documents={"D1": "failed"}, exit_code=1)
    (store.path / "run.json").write_text('{"documents": {"D1"', encoding="utf-8")

    with pytest.raises(ValueError, match="could not be read"):
        RunStore.failed_last_time(tmp_path / "tei_output")


def test_no_previous_run_is_still_simply_nothing_to_retry(tmp_path):
    assert RunStore.failed_last_time(tmp_path / "tei_output") == ()


# =============================================================================
# The panel's three counts have to add up to the corpus
# =============================================================================

def test_a_volume_that_could_not_be_opened_is_failed_and_not_still_to_come():
    """A corrupt archive counted in the denominator but not among the
    failures, so a finished run ended on "3 written · 0 failed · 1 to
    go" — with nothing left to do."""
    from teille_douce.report.collector import Run

    run = Run(input_dir="OCR", output_dir="out", volumes=2, pages=10)
    run.archive_failed("LIV9005_reconciled.zip", "File is not a zip file")
    run.document_started("D1", pages=10)
    run.pages_read("D1", 10)
    run.document_finished("D1", ok=True)

    panel = run.panel()

    assert (panel.volumes_written, panel.volumes_failed,
            panel.volumes_to_go) == (1, 1, 0)


def test_an_unreadable_volume_counts_the_same_way():
    from teille_douce.report.collector import Run

    run = Run(input_dir="OCR", output_dir="out", volumes=1, pages=0)
    run.volume_unreadable("LIV9002_reconciled", "Permission denied")

    assert run.panel().volumes_failed == 1
    assert run.panel().volumes_to_go == 0


# =============================================================================
# A record that is not a record
# =============================================================================

@pytest.mark.parametrize("written", ["null", "[]", '"a string"',
                                     '{"documents": ["a"]}'])
def test_a_manifest_of_the_wrong_shape_is_refused_the_documented_way(
        tmp_path, written):
    """`manifest.get(...)` assumed the JSON decoded to an object. A
    truncated write flushed as `null` is valid JSON and not a dict, so it
    raised AttributeError — which is not the ValueError the caller
    catches, so it left the CLI as a raw traceback with exit 1, and a
    wrapper read "some volumes failed"."""
    store = a_store(tmp_path)
    store.finish(argv=["teille-douce"], settings={}, documents={},
                 exit_code=0)
    (store.path / "run.json").write_text(written, encoding="utf-8")

    with pytest.raises(ValueError):
        RunStore.failed_last_time(tmp_path / "tei_output")


# =============================================================================
# Pruning does not eat the run that is doing it
# =============================================================================

def test_the_running_run_is_never_pruned_from_under_itself(tmp_path):
    """Every run prunes, so a long one outranked by ten later short ones
    was rmtree'd while it was still writing: `_ready` quietly recreated
    the directory, the manifest went back into it and the incident index
    did not — an index outlived by its transcript — and the next
    --retry-failed reported nothing to retry."""
    live = a_store(tmp_path, when=datetime(2026, 1, 1, 0, 0, 0))
    live.incident(Loss(Code.VOLUME_UNREADABLE, "LIV0001", "expand",
                       Locator.document("LIV0001"), count=1, total=1,
                       detail="permission denied"))

    for later in range(12):
        newer = a_store(tmp_path, when=datetime(2026, 1, 2, 0, 0, later))
        newer.finish(argv=["teille-douce"], settings={}, documents={},
                     exit_code=0)
        RunStore.prune(tmp_path / "tei_output", spare=live.path)

    assert live.path.is_dir()
    assert (live.path / "incidents.jsonl").exists()


def test_a_run_directory_that_is_not_a_directory_is_not_a_clean_run(tmp_path):
    """`_runs` swallows the OSError and answers "no runs", which the
    caller reads as "the last run had no failures" — so a `.teille-douce`
    that is a file, or one this process may not read, sent the operator
    away believing nothing had failed, having converted nothing."""
    output = tmp_path / "tei_output"
    (output / ".teille-douce").parent.mkdir(parents=True)
    (output / ".teille-douce").write_text("not a directory", encoding="utf-8")

    with pytest.raises(ValueError, match="readable run directory|could not be read"):
        RunStore.failed_last_time(output)


def test_an_output_directory_with_no_record_yet_has_nothing_to_retry(tmp_path):
    """The other half: a first run leaves no record to read, and that is
    not an error."""
    assert RunStore.failed_last_time(tmp_path / "tei_output") == ()
