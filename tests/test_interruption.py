# -----------------------------------------------------------
# Ctrl-C in the fourth hour.
#
# `execute` catches `Exception`. A KeyboardInterrupt is a BaseException
# and goes straight through it, so the summary — which lives outside the
# progress block — never ran: no report, no manifest, nothing to resume
# from, after four hours of work.
#
# Driven in-process with a real KeyboardInterrupt rather than a signal or
# an environment hook: no test-only branch belongs in the run, and a
# timed SIGINT to a subprocess would be a flake waiting to happen.
#
# Run: venv/bin/python -m pytest tests/test_interruption.py -q
# -----------------------------------------------------------
import json
import shutil
from pathlib import Path

import pytest

from teille_douce.cli import app, run as run_module
from teille_douce.settings import use_settings

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ALTO_MIN = FIXTURES / "alto_min"
DOCUMENT = "LIV9001_reconciled"


def a_corpus(tmp_path, volumes=2):
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    for index in range(2, volumes + 1):
        shutil.copytree(ocr / DOCUMENT, ocr / f"LIV900{index}_reconciled")
    for csv in ("metadata_livre.csv", "metadata_personne.csv"):
        shutil.copy(FIXTURES / csv, tmp_path / csv)
    return ocr


def interrupt_after(monkeypatch, documents):
    """Let `documents` volumes through, then press Ctrl-C."""
    real = run_module._process_document
    seen = []

    def maybe_stop(doc_name, *args, **kwargs):
        if len(seen) >= documents:
            raise KeyboardInterrupt
        seen.append(doc_name)
        return real(doc_name, *args, **kwargs)

    monkeypatch.setattr(run_module, "_process_document", maybe_stop)
    return seen


def execute(tmp_path, ocr, extra=()):
    args = app.parse_args(["run", "--fast", "--no-config", "--no-log-file",
                           "-i", str(ocr), "-o", str(tmp_path / "out"),
                           *extra])
    settings = app.settings_from(args, env={})
    with use_settings(settings):
        with pytest.raises(SystemExit) as exit:
            run_module.execute(args)
    return exit.value.code


def test_an_interrupted_run_exits_with_the_code_for_interrupted(tmp_path,
                                                                monkeypatch):
    ocr = a_corpus(tmp_path)
    interrupt_after(monkeypatch, 1)

    assert execute(tmp_path, ocr) == 130


def test_what_was_written_before_the_interruption_is_kept(tmp_path,
                                                          monkeypatch):
    """The foot of the panel promises exactly this: abandon this volume,
    keep the ones already written."""
    ocr = a_corpus(tmp_path)
    interrupt_after(monkeypatch, 1)

    execute(tmp_path, ocr)

    written = sorted(p.name for p in (tmp_path / "out").glob("*.tei.xml"))
    assert written == [f"{DOCUMENT}.tei.xml"]


def test_an_interrupted_run_still_prints_its_summary(tmp_path, monkeypatch,
                                                     capsys):
    """Four hours of knowledge must not leave with the signal."""
    ocr = a_corpus(tmp_path)
    interrupt_after(monkeypatch, 1)

    execute(tmp_path, ocr)

    out = capsys.readouterr().out
    assert "interrupted" in out.lower()
    assert "1/2 documents converted" in out
    assert "exit 130" in out


def test_an_interrupted_run_still_writes_its_manifest(tmp_path, monkeypatch):
    """Which is what the next `--retry-failed` reads."""
    ocr = a_corpus(tmp_path)
    interrupt_after(monkeypatch, 1)

    execute(tmp_path, ocr)

    runs = sorted((tmp_path / "out" / ".teille-douce" / "runs").iterdir())
    manifest = json.loads((runs[-1] / "run.json").read_text(encoding="utf-8"))
    assert manifest["exit_code"] == 130
    assert manifest["documents"][DOCUMENT] == "ok"


def test_the_volume_that_was_open_is_not_reported_as_converted(tmp_path,
                                                               monkeypatch):
    """It was abandoned mid-flight; counting it would make the summary
    claim a file that is not there."""
    ocr = a_corpus(tmp_path)
    interrupt_after(monkeypatch, 1)

    execute(tmp_path, ocr)

    runs = sorted((tmp_path / "out" / ".teille-douce" / "runs").iterdir())
    manifest = json.loads((runs[-1] / "run.json").read_text(encoding="utf-8"))
    assert "LIV9002_reconciled" not in manifest["documents"]


# =============================================================================
# The interrupt does not arrive where it is convenient
# =============================================================================

def with_the_panel(tmp_path, ocr, extra=()):
    """Force the dashboard on: without a terminal the run picks the
    journal, and the handler swap under test never happens at all."""
    return execute(tmp_path, ocr, extra=("--dashboard", *extra))


def test_an_interrupt_outside_the_guarded_call_still_reports(tmp_path,
                                                             monkeypatch):
    """The loop's `try` covers `_process_document` and nothing else. A
    signal arrives at an arbitrary instruction, so it lands in the rest of
    the body just as easily — and the panel's setup was straight-line
    code, so the report, the manifest and the console handlers all went
    with it."""
    ocr = a_corpus(tmp_path)
    real = run_module._pages_per_second
    calls = []

    def stop_on_the_second(*args, **kwargs):
        calls.append(1)
        if len(calls) >= 2:
            raise KeyboardInterrupt
        return real(*args, **kwargs)

    monkeypatch.setattr(run_module, "_pages_per_second", stop_on_the_second)

    assert with_the_panel(tmp_path, ocr) == 130
    runs = sorted((tmp_path / "out" / ".teille-douce" / "runs").iterdir())
    assert (runs[-1] / "run.json").exists(), "the manifest went with the signal"


def test_the_console_handlers_come_back_however_the_run_ends(tmp_path,
                                                             monkeypatch):
    """The panel swaps them for the digest. Leaked, every later warning in
    the process is swallowed and the console has no handler at all."""
    import logging

    ocr = a_corpus(tmp_path)

    def explode(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(run_module, "_pages_per_second", explode)
    with_the_panel(tmp_path, ocr)

    # Not a before/after count: `configure_logging` legitimately replaces
    # the root handlers with `basicConfig(force=True)`. What must hold is
    # that the swap the panel made is undone.
    after = logging.getLogger().handlers
    assert not any(type(h).__name__ == "DigestHandler" for h in after), \
        "the digest handler leaked and swallows every later warning"
    assert any(isinstance(h, logging.StreamHandler)
               and not hasattr(h, "baseFilename") for h in after), \
        "the console was left with no handler at all"


def test_the_live_region_is_closed_however_the_run_ends(tmp_path, monkeypatch):
    """A Live left open owns the cursor and the alternate screen for the
    rest of the process."""
    from teille_douce.cli.run import console

    ocr = a_corpus(tmp_path)

    def explode(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(run_module, "_pages_per_second", explode)
    with_the_panel(tmp_path, ocr)

    assert getattr(console, "_live", None) is None


def test_the_chatter_is_not_silenced_for_ever(tmp_path, monkeypatch):
    """`_QUIET` is global. Left True, every later run in the process is
    silent for a reason nobody can find."""
    ocr = a_corpus(tmp_path)

    def explode(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(run_module, "_pages_per_second", explode)
    with_the_panel(tmp_path, ocr)

    assert run_module._QUIET is False


def test_a_second_interrupt_during_the_teardown_still_restores_everything(
        monkeypatch):
    """A Ctrl-C arriving inside `Live.stop()` skipped the rest of the
    `finally` — leaving the digest handler on the root logger, the
    console with none, and `_QUIET` stuck true for the life of the
    process. The state the context manager exists to prevent, moved one
    frame inward."""
    import logging

    from teille_douce.report.collector import Run

    run = Run(input_dir="OCR", output_dir="out", volumes=1, pages=1)

    def explode(self, *exception):
        raise KeyboardInterrupt

    monkeypatch.setattr(run_module.Dashboard, "__exit__", explode)
    before = list(logging.getLogger().handlers)

    try:
        with run_module.panel_installed(run, active=True):
            pass
    except KeyboardInterrupt:
        pass

    assert logging.getLogger().handlers == before
    assert run_module._QUIET is False


def test_a_panel_that_would_not_shut_down_says_so_where_it_can_be_read(
        monkeypatch):
    """The message went out while the digest handler was the only
    non-file handler on the root logger — a handler that prints nothing
    by design, read through a panel that had just come down. So the one
    explanation for a terminal left with no cursor was written to a
    screen nobody would ever draw again."""
    import io
    import logging

    from teille_douce.report.collector import Run

    run = Run(input_dir="OCR", output_dir="out", volumes=1, pages=1)

    def explode(self, *exception):
        raise RuntimeError("Live.stop blew up")

    monkeypatch.setattr(run_module.Dashboard, "__exit__", explode)

    seen = io.StringIO()
    watcher = logging.StreamHandler(seen)
    root = logging.getLogger()
    root.addHandler(watcher)
    try:
        with run_module.panel_installed(run, active=True):
            pass
    finally:
        root.removeHandler(watcher)

    assert "did not shut down cleanly" in seen.getvalue()
    assert "Live.stop blew up" in seen.getvalue()
