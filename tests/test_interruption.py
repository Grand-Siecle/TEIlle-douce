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
