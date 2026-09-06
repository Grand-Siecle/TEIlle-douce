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
import sys
from pathlib import Path

import pytest
from lxml import etree

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


def test_a_pool_that_will_not_start_leaves_ctrl_c_working(monkeypatch,
                                                          tmp_path):
    """SIGINT is deferred across the pool's birth so a Ctrl-C cannot
    catch a worker half-unpickled. Restored on the happy path only, that
    deferral leaked the moment `Pool()` raised — too many open files, a
    forkserver that will not start — and `execute` catches Exception
    around each document, so the run carried on over every remaining
    volume with Ctrl-C DEAD. Which is the outcome deferring instead of
    ignoring exists to avoid.
    """
    import signal

    from teille_douce.sourcedoc import builder

    class Refuses:
        def Pool(self, *args, **kwargs):
            raise OSError(24, "too many open files")

        def get_start_method(self):
            return "forkserver"

    monkeypatch.setattr(builder, "_MP_CONTEXT", Refuses())
    before = signal.getsignal(signal.SIGINT)

    page = tmp_path / "f1.xml"
    page.write_text("<alto/>", encoding="utf-8")
    with pytest.raises(OSError):
        builder.build_sourcedoc("D1", etree.Element("TEI"), [page], (), (),
                                {"iiifURI": None})

    assert signal.getsignal(signal.SIGINT) is before


def test_an_interrupt_during_the_pools_birth_is_raised_not_swallowed(
        monkeypatch, tmp_path):
    """A Ctrl-C arriving while the worker pool is being born catches a
    worker half-unpickled and prints its traceback across the report —
    half of all interrupt timings did, once straight through the final
    verdict line. Ignoring the signal for that window fixed the
    tracebacks and swallowed three interrupts in fourteen, which is
    worse: a Ctrl-C that does nothing.

    So it is DEFERRED, and raised again as soon as there is a pool to
    shut down. Sent for real here rather than asserted about the source:
    a test that greps for `raise KeyboardInterrupt` would have passed
    over the leak the sibling test above covers.
    """
    import os
    import signal

    from teille_douce.sourcedoc import builder

    stopped = []

    class Pool:
        def imap_unordered(self, function, jobs):
            return iter(())

        def close(self):
            stopped.append("close")

        def join(self):
            pass

        def terminate(self):
            stopped.append("terminate")

    class SignalsMidBirth:
        def Pool(self, *args, **kwargs):
            os.kill(os.getpid(), signal.SIGINT)
            return Pool()

        def get_start_method(self):
            return "forkserver"

    monkeypatch.setattr(builder, "_MP_CONTEXT", SignalsMidBirth())
    before = signal.getsignal(signal.SIGINT)

    page = tmp_path / "f1.xml"
    page.write_text("<alto/>", encoding="utf-8")
    with pytest.raises(KeyboardInterrupt):
        builder.build_sourcedoc("D1", etree.Element("TEI"), [page], (), (),
                                {"iiifURI": None})

    assert "terminate" in stopped, "the pool was left running"
    assert signal.getsignal(signal.SIGINT) is before


def test_the_forkserver_is_started_before_there_is_anything_to_interrupt():
    """It is started lazily by the first pool otherwise, inheriting a
    live SIGINT handler, and a Ctrl-C landing while it preloads the
    builder killed it mid-import — printing a
    `multiprocessing/forkserver.py` traceback over the report."""
    import signal

    from teille_douce.sourcedoc import builder

    builder.warm_up()

    # And it gives the handler back: the window is one instant at
    # startup, not the rest of the run.
    assert signal.getsignal(signal.SIGINT) is not signal.SIG_IGN


def test_an_interrupt_before_the_loop_is_not_a_traceback(monkeypatch,
                                                         capsys):
    """The document loop has its own handler; everything before it —
    loading the CSVs, probing the services, unpacking archives, which is
    minutes on the real corpus — had none, so a Ctrl-C there came out as
    a raw KeyboardInterrupt traceback with no report and no manifest."""
    from teille_douce.cli import app

    def interrupted(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(run_module, "execute", interrupted)

    assert app.main(["run", "--no-config"]) == 130
    assert "Interrupted" in capsys.readouterr().err


def test_a_second_interrupt_does_not_throw_away_the_first_ones_report():
    """The first Ctrl-C prints "finishing the report for what was already
    written". A second, a hundred and fifty milliseconds later, threw
    that away: one TEI file on disk, no run directory, no manifest, no
    summary, and a `--retry-failed` afterwards with nothing to read.

    Held from inside the interrupt handler and not merely around the
    writing, because the second one lands during the panel's teardown —
    wrapping the report alone still lost four sweeps in six.
    """
    import signal

    held = run_module.HeldInterrupts()
    before = signal.getsignal(signal.SIGINT)

    wrote = []
    with run_module.finishing(held):
        signal.raise_signal(signal.SIGINT)
        wrote.append("the manifest")
        signal.raise_signal(signal.SIGINT)
        wrote.append("the summary")

    assert wrote == ["the manifest", "the summary"]
    assert signal.getsignal(signal.SIGINT) is before


def test_an_interrupt_after_the_work_is_done_does_not_replace_the_verdict():
    """`release` used to raise from the `finally`, which REPLACED
    whatever was on its way out — including the `sys.exit(exit_code)`
    that is the run's last statement. A run whose summary said `exit 5`
    and whose manifest said 5 gave the shell 130, the one code
    documented as "not a verdict on the corpus"; a wrapper reads that and
    retries instead of looking at what was lost. It swallowed real
    exceptions the same way."""
    import signal

    held = run_module.HeldInterrupts()

    with pytest.raises(SystemExit) as verdict:
        with run_module.finishing(held):
            signal.raise_signal(signal.SIGINT)
            raise SystemExit(5)

    assert verdict.value.code == 5


def test_a_failure_inside_the_held_stretch_is_not_masked_by_the_signal():
    import signal

    held = run_module.HeldInterrupts()

    with pytest.raises(RuntimeError, match="the manifest could not"):
        with run_module.finishing(held):
            signal.raise_signal(signal.SIGINT)
            raise RuntimeError("the manifest could not be written")


def test_holding_is_idempotent_so_the_two_call_sites_cannot_fight():
    """`hold()` is called from the interrupt handler and again by
    `finishing`; the second must not overwrite the saved handler with
    the deferring one and restore that."""
    import signal

    held = run_module.HeldInterrupts()
    before = signal.getsignal(signal.SIGINT)

    held.hold()
    held.hold()
    held.release()

    assert signal.getsignal(signal.SIGINT) is before


def test_the_forkserver_warm_up_gives_the_handler_back_even_when_it_was_none(
        monkeypatch):
    """`warm_up` carried the very `previous is not None` bug the `_UNSET`
    sentinel was introduced to fix one function below it — and it
    installs SIG_IGN, so its leak is Ctrl-C dead for the WHOLE run rather
    than for one document. A handler set outside Python reads back as
    None, which is the premise the sentinel exists for."""
    import signal

    from teille_douce.sourcedoc import builder

    installed = []

    def remembers(number, handler):
        installed.append(handler)
        return None            # a handler set outside Python

    monkeypatch.setattr(builder.signal, "signal", remembers)
    builder.warm_up()

    assert installed[0] is signal.SIG_IGN
    assert installed[-1] is None, "the handler was never given back"


def test_the_launcher_answers_a_ctrl_c_in_its_own_imports(tmp_path):
    """`from teille_douce.cli import main` pulls in pandas and lxml — a
    quarter of a second — and a Ctrl-C there came out as a traceback from
    somewhere inside pandas, over a run that had not started.

    The interrupt is raised from inside that import rather than timed at
    it: a real signal sent early enough to land there is also early
    enough to land before the interpreter has a Python handler at all,
    and the test would be measuring the race instead of the guard.
    """
    import os
    import subprocess
    import sys

    from test_e2e_pipeline import RACINE

    (tmp_path / "sitecustomize.py").write_text(
        "import importlib.abc, importlib.machinery, sys\n"
        "class Interrupts(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'teille_douce.cli':\n"
        "            raise KeyboardInterrupt\n"
        "        return None\n"
        "sys.meta_path.insert(0, Interrupts())\n", encoding="utf-8")

    finished = subprocess.run(
        [sys.executable, str(RACINE / "main.py"), "run", "--help"],
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(tmp_path)})

    assert finished.returncode == 130, finished.stderr.decode()[-2000:]
    assert b"Traceback" not in finished.stderr, finished.stderr.decode()
    assert b"Interrupted" in finished.stderr


def test_a_teardown_that_raises_gives_the_signal_back(monkeypatch, tmp_path):
    """The hold is taken in the loop's `finally`, which is INSIDE the
    `with panel_installed(...)`, and the only release is after it. A
    teardown that raised therefore left the process deaf to Ctrl-C —
    and now that the hold is taken on every way out of the loop, not
    only after a first interrupt, that is every teardown failure rather
    than a coincidence of two.

    In process, because the assertion is about the handler this process
    is left with: a subprocess that dies of the teardown has no "after".
    """
    import contextlib
    import os
    import signal

    from teille_douce.cli import app
    from teille_douce.cli.app import settings_from
    from teille_douce.settings import set_settings

    from test_e2e_pipeline import ALTO_MIN, FIXTURES

    for csv in ("metadata_livre.csv", "metadata_personne.csv"):
        shutil.copy(FIXTURES / csv, tmp_path / csv)
    monkeypatch.chdir(tmp_path)

    @contextlib.contextmanager
    def explodes_on_the_way_out(reporter, active):
        yield None
        raise RuntimeError("teardown exploded")

    monkeypatch.setattr(run_module, "panel_installed", explodes_on_the_way_out)

    monkeypatch.setenv("TDOUCE_NER", "0")
    monkeypatch.setenv("TDOUCE_ENRICHMENT", "0")
    monkeypatch.setenv("TDOUCE_MODERNIZE", "0")
    parser = app.build_parser()
    args = parser.parse_args(["run", "--no-config", "-i", str(ALTO_MIN),
                              "-o", str(tmp_path / "out")])
    set_settings(settings_from(args, parser=parser))

    before = signal.getsignal(signal.SIGINT)
    with pytest.raises(RuntimeError, match="teardown exploded"):
        run_module.execute(args)

    assert signal.getsignal(signal.SIGINT) is before, (
        "the process is left deaf to Ctrl-C")
    # And it really is deaf otherwise: the default handler raises.
    with pytest.raises(KeyboardInterrupt):
        os.kill(os.getpid(), signal.SIGINT)


def test_the_note_never_lands_in_the_report_or_replaces_its_verdict(
        monkeypatch, capsys):
    """It went through `console.print`, so it printed AFTER the verdict —
    the summary's last line and the one wrappers grep. Moved to stderr,
    its guard caught `OSError` alone: under `2>&-` `sys.stderr` is None
    and `print(file=None)` falls back to stdout, putting it back in the
    report; a CLOSED stderr raises `ValueError`, which escaped the
    `finally` and replaced the run's own `SystemExit`."""
    import io
    import signal

    # A closed stderr: the case that used to escape.
    closed = io.StringIO()
    closed.close()
    monkeypatch.setattr(sys, "stderr", closed)

    with pytest.raises(SystemExit) as verdict:
        with run_module.finishing(run_module.HeldInterrupts()):
            signal.raise_signal(signal.SIGINT)
            raise SystemExit(5)

    assert verdict.value.code == 5

    # And `2>&-`, where `print(file=None)` would fall back to stdout.
    monkeypatch.setattr(sys, "stderr", None)
    capsys.readouterr()
    with run_module.finishing(run_module.HeldInterrupts()):
        signal.raise_signal(signal.SIGINT)

    assert capsys.readouterr().out == ""


def test_the_progress_bars_own_teardown_cannot_leave_ctrl_c_held(
        monkeypatch, tmp_path):
    """The guard was one frame too far in: `Progress.__exit__` is Rich
    stopping a Live and writing to the console — the `BrokenPipeError`
    risk under `| head` — and it ran between the hold and any release."""
    import os
    import signal

    from teille_douce.cli import app
    from teille_douce.cli.app import settings_from
    from teille_douce.settings import set_settings

    from test_e2e_pipeline import ALTO_MIN, FIXTURES

    for csv in ("metadata_livre.csv", "metadata_personne.csv"):
        shutil.copy(FIXTURES / csv, tmp_path / csv)
    monkeypatch.chdir(tmp_path)

    real = run_module.Progress

    class BreaksOnTheWayOut(real):
        def __exit__(self, *exception):
            super().__exit__(*exception)
            raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(run_module, "Progress", BreaksOnTheWayOut)
    for name in ("TDOUCE_NER", "TDOUCE_ENRICHMENT", "TDOUCE_MODERNIZE"):
        monkeypatch.setenv(name, "0")
    parser = app.build_parser()
    args = parser.parse_args(["run", "--no-config", "-i", str(ALTO_MIN),
                              "-o", str(tmp_path / "out")])
    set_settings(settings_from(args, parser=parser))

    before = signal.getsignal(signal.SIGINT)
    with pytest.raises(BrokenPipeError):
        run_module.execute(args)

    assert signal.getsignal(signal.SIGINT) is before
    with pytest.raises(KeyboardInterrupt):
        os.kill(os.getpid(), signal.SIGINT)


def test_a_failure_before_the_loop_names_itself(monkeypatch, tmp_path):
    """The guard moved out one frame and the holder it reads stayed one
    frame in, so anything raising before that binding — `Progress()`
    starting a Live, the first incident write hitting a full disk —
    reached a handler whose first statement is `interrupts.release()`
    and came out as an `UnboundLocalError` with the real cause buried in
    `__context__`. Which is the failure this stretch is guarded against,
    reproduced one frame outside it."""
    import signal

    from teille_douce.cli import app
    from teille_douce.cli.app import settings_from
    from teille_douce.settings import set_settings

    from test_e2e_pipeline import ALTO_MIN, FIXTURES

    for csv in ("metadata_livre.csv", "metadata_personne.csv"):
        shutil.copy(FIXTURES / csv, tmp_path / csv)
    monkeypatch.chdir(tmp_path)

    class RefusesToStart(run_module.Progress):
        def __enter__(self):
            raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(run_module, "Progress", RefusesToStart)
    for name in ("TDOUCE_NER", "TDOUCE_ENRICHMENT", "TDOUCE_MODERNIZE"):
        monkeypatch.setenv(name, "0")
    parser = app.build_parser()
    args = parser.parse_args(["run", "--no-config", "-i", str(ALTO_MIN),
                              "-o", str(tmp_path / "out")])
    set_settings(settings_from(args, parser=parser))

    before = signal.getsignal(signal.SIGINT)
    with pytest.raises(BrokenPipeError):
        run_module.execute(args)

    assert signal.getsignal(signal.SIGINT) is before


def test_a_handler_set_outside_python_does_not_make_release_raise():
    """`getsignal` answers None for one, which is the premise `_UNSET`
    exists for — and `signal(SIGINT, None)` is a TypeError, out of the
    `finally` that calls `release`, replacing the run's own exit code.
    The one thing `release`'s docstring promises it will never do."""
    import signal

    held = run_module.HeldInterrupts()
    held._held = None          # what `getsignal` gives for a C handler

    with pytest.raises(SystemExit) as verdict:
        with run_module.finishing(held):
            raise SystemExit(5)

    assert verdict.value.code == 5
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


def test_release_off_the_main_thread_does_not_replace_the_verdict():
    """The recovery clause was the call that failed. `signal.signal`
    raises `ValueError` for exactly one reason — not the main thread —
    and the `except (TypeError, ValueError)` answered it by making the
    identical call, which raised the identical error out of the
    `finally` and took the run's exit code with it. It could only ever
    re-raise what it caught."""
    import signal
    import threading

    escaped = []

    def off_the_main_thread():
        held = run_module.HeldInterrupts()
        held._held = signal.default_int_handler   # as if it had held
        try:
            with run_module.finishing(held):
                raise SystemExit(5)
        except BaseException as reason:
            escaped.append(reason)

    worker = threading.Thread(target=off_the_main_thread)
    worker.start()
    worker.join(timeout=10)

    assert escaped and isinstance(escaped[0], SystemExit), escaped
    assert escaped[0].code == 5


def test_a_release_that_could_not_restore_still_forgets_it_was_holding():
    """Left set by an exception on the way through, the object believes
    it is still holding and the next `hold()` is a no-op — so the stretch
    after it runs unprotected while thinking it is not."""
    import signal

    held = run_module.HeldInterrupts()
    held._held = object()          # nothing `signal.signal` will accept

    assert held.release() is False
    held.hold()
    assert held._held is not run_module._UNSET_SIGNAL, (
        "hold() did nothing, so release() had not forgotten")
    held.release()
    assert signal.getsignal(signal.SIGINT) is not None


def test_the_note_survives_a_console_handler_on_a_closed_stream():
    """`logging`'s own `handleError` catches `OSError` alone, so a
    console handler at INFO over a closed stream raised `ValueError` out
    of the `finally` — verbatim the failure the guard one statement
    below it was written to prevent."""
    import io
    import logging
    import signal

    closed = io.StringIO()
    closed.close()
    watcher = logging.StreamHandler(closed)
    watcher.setLevel(logging.INFO)
    root = logging.getLogger()
    root.addHandler(watcher)
    try:
        with pytest.raises(SystemExit) as verdict:
            with run_module.finishing(run_module.HeldInterrupts()):
                signal.raise_signal(signal.SIGINT)
                raise SystemExit(5)
    finally:
        root.removeHandler(watcher)

    assert verdict.value.code == 5
