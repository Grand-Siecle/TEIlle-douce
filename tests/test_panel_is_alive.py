# -----------------------------------------------------------
# A live panel that does not move is worse than no panel.
#
# Five review rounds passed over this because every panel test built its
# `PanelState` by hand, with the phases already filled in and a clock
# already advanced. Nothing drove the collector from the real pipeline,
# and on a real run the panel stood perfectly still: `Run.phase()` was
# called from exactly one place — `_phase_lost` — so a phase that was
# going WELL never reached the screen, and `auto_refresh=False` over a
# snapshot renderable meant the clock, the eta and the spinner froze
# with it. An 850-page volume showed `0:02` for four minutes.
#
# Two independent facts, tested apart:
#   - the run tells the panel what each phase is doing, not only that it
#     died;
#   - the panel redraws on a clock, not only on an event.
#
# Run: venv/bin/python -m pytest tests/test_panel_is_alive.py -q
# -----------------------------------------------------------
import io
import time

import pytest

from teille_douce.cli import run as run_module
from teille_douce.report.collector import Run
from teille_douce.report.counts import PhaseState


class FakeTask:
    """Enough of a Rich `Progress` to see what the old bar was told."""

    def __init__(self):
        self.updates = []

    def update(self, task, **fields):
        self.updates.append((task, fields))


def a_run(**overrides):
    run = Run(**{"input_dir": "OCR", "output_dir": "out",
                 "volumes": 1, "pages": 10, **overrides})
    run.document_started("D1", pages=10)
    return run


def phase_of(run, name):
    return next((line for line in run.panel().current.phases
                 if line.name == name), None)


# =============================================================================
# A phase that is going well reaches the panel
# =============================================================================

def test_progress_reaches_the_reporter_and_not_only_the_old_bar():
    """The callback fed `progress.update` alone, so the panel's richest
    part — the per-phase line, its bar, its rate — was dead code on any
    run that did not lose a service."""
    run = a_run()
    bar = FakeTask()

    report = run_module._phase_progress(run, bar, "task", "D1", "enrich",
                                        "containers")
    report(318, 1402)

    assert bar.updates == [("task", {"completed": 318, "total": 1402})]
    line = phase_of(run, "enrich")
    assert line is not None, "the phase never reached the panel"
    assert (line.state, line.done, line.total) == (PhaseState.RUNNING, 318,
                                                   1402)
    assert line.unit == "containers"


def test_a_finished_phase_says_so_rather_than_staying_at_its_last_frame():
    """Left RUNNING, a finished phase keeps a spinner turning next to a
    count that will never change again — the panel claiming work that
    stopped."""
    run = a_run()

    run_module._phase_done(run, "D1", "enrich", 1387, 1402, "containers")

    line = phase_of(run, "enrich")
    assert line.state is PhaseState.DONE
    assert (line.done, line.total) == (1387, 1402)


def test_no_reporter_is_not_an_error():
    """`--plain` and `-q` runs pass None, and the reporting path must
    never be the thing that kills a four-hour conversion."""
    bar = FakeTask()

    run_module._phase_progress(None, bar, "task", "D1", "enrich", "x")(1, 2)
    run_module._phase_done(None, "D1", "enrich", 1, 2, "x")

    assert bar.updates == [("task", {"completed": 1, "total": 2})]


def test_a_total_the_phase_does_not_know_yet_is_not_rendered_as_zero():
    """Rich's `total=None` means indeterminate. Passed through as a
    denominator it would print `318/0 containers`, which is the sentence
    the whole report module exists to make impossible."""
    run = a_run()

    run_module._phase_progress(run, FakeTask(), "t", "D1", "ner", "pages")(3,
                                                                          None)

    assert phase_of(run, "ner").total is None


# =============================================================================
# The panel redraws on a clock, not only on an event
# =============================================================================

def a_console():
    from rich.console import Console

    return Console(file=io.StringIO(), force_terminal=True, width=100,
                   height=30, color_system=None)


def test_the_panel_redraws_while_nothing_happens():
    """Enrichment of one volume is minutes of work between two state
    changes. Drawing only on change froze the clock, the eta and the
    spinner for the whole of it, and an operator reads a frozen panel as
    a hung run."""
    from teille_douce.report.dashboard import Dashboard

    run = a_run()
    with Dashboard(a_console(), run) as dashboard:
        first = dashboard.frames
        time.sleep(0.8)
        assert dashboard.frames > first, (
            "no redraw in 800 ms with nothing to report")


def test_the_clock_a_frozen_panel_showed_is_the_one_that_moves():
    """`0:02` for four minutes was the visible symptom, so it is what is
    asserted: two frames, taken apart in time, with no state change
    between them, must not carry the same elapsed."""
    from teille_douce.report.dashboard import Dashboard
    from teille_douce.report.panel import as_text

    run = a_run(started_at=time.monotonic() - 3600)
    with Dashboard(a_console(), run) as dashboard:
        before = as_text(dashboard.snapshot())[0]
        time.sleep(1.1)
        after = as_text(dashboard.snapshot())[0]

    assert "elapsed 1:00:00" in before
    assert "elapsed 1:00:01" in after


def test_a_burst_of_updates_does_not_redraw_a_thousand_times():
    """A container-by-container callback fires fourteen hundred times per
    volume. Redrawing on each one spends a core on the report and makes
    the run slower than the plain one it replaced."""
    from teille_douce.report.dashboard import Dashboard

    holder = {}
    run = a_run(on_change=lambda: (holder["panel"].draw()
                                   if holder.get("panel") else None))
    with Dashboard(a_console(), run) as dashboard:
        holder["panel"] = dashboard
        before = dashboard.frames
        for done in range(1400):
            run.phase("D1", "enrich", PhaseState.RUNNING, done=done,
                      total=1400, unit="containers")
        drawn = dashboard.frames - before

    assert drawn <= 20, f"{drawn} frames for 1400 updates"


def test_the_heartbeat_stops_with_the_panel():
    """A thread still drawing into a console the run has moved on from
    writes frames over the summary."""
    from teille_douce.report.dashboard import Dashboard

    run = a_run()
    with Dashboard(a_console(), run) as dashboard:
        pass
    settled = dashboard.frames
    time.sleep(0.6)

    assert dashboard.frames == settled


def test_a_reporter_state_change_during_a_redraw_does_not_crash_the_run():
    """The heartbeat reads the collector from another thread while the
    pipeline writes it. `tuple(self._phases.values())` over a dict being
    resized raises, and it would raise inside the panel — the one place
    allowed to break nothing."""
    from teille_douce.report.dashboard import Dashboard

    run = a_run()
    with Dashboard(a_console(), run) as dashboard:
        deadline = time.monotonic() + 1.0
        turn = 0
        while time.monotonic() < deadline:
            turn += 1
            run.phase("D1", f"phase{turn % 40}", PhaseState.RUNNING,
                      done=turn, total=99, unit="containers")
        assert dashboard.failures == [], dashboard.failures


# =============================================================================
# End to end: the phase lines are on a real run's screen
# =============================================================================

@pytest.mark.e2e
def test_a_real_run_shows_the_phase_it_is_in(tmp_path):
    """The regression as an operator met it: every frame of a healthy run
    carried three zeroed loss counters, a blank phase block and a clock
    that did not move.

    Through a pty, because that is the only place a live panel exists:
    Rich draws a `Live` once, at the end, when its output is a pipe — so
    a test reading a pipe sees exactly the one frame in which no volume
    is open any more, which is the frame that proved nothing.
    """
    import os
    import pty
    import shutil
    import subprocess
    import sys

    from test_e2e_pipeline import (ALTO_MIN, DOCUMENT, FIXTURES, MODE_COURT,
                                   RACINE, _env_couverture_sous_processus)

    for csv in ("metadata_livre.csv", "metadata_personne.csv"):
        shutil.copy(FIXTURES / csv, tmp_path / csv)
    # Four volumes of a hundred and sixty pages, not the eight-page
    # fixture. Two reasons, and both are about time: the fixture volume is
    # converted well inside one redraw interval, so the only frame that
    # ever reaches the screen is the one drawn at teardown — by which
    # time no volume is open and the phase block is empty either way; and
    # the phase this asserts on has to be on screen long enough for a
    # redraw to land in it, which means sourceDoc has to be the bulk of
    # the volume rather than a tenth of it.
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    pages = sorted((ocr / DOCUMENT).rglob("*.xml"))
    for copy in range(1, 20):
        for page in pages:
            shutil.copy(page, page.with_name(f"{page.stem}_{copy}.xml"))
    for copy in range(3):
        shutil.copytree(ocr / DOCUMENT, ocr / f"LIV9{copy + 2:03d}_reconciled")
    env = {**os.environ, "TDOUCE_OCR_DIR": str(ocr),
           "TDOUCE_OUTPUT_DIR": str(tmp_path / "out"),
           # NO_COLOR, but NOT `FORCE_COLOR=""` the way the piped e2e
           # harness sets it: Rich reads the mere presence of that
           # variable as "this is not a terminal", so a `Live` under it
           # draws once at teardown however alive it is. Which is why no
           # existing test could have seen a frozen panel.
           "NO_COLOR": "1",
           "COLUMNS": "100", "LINES": "40", "TERM": "xterm",
           **MODE_COURT, **_env_couverture_sous_processus()}

    parent, child = pty.openpty()
    try:
        process = subprocess.Popen(
            [sys.executable, str(RACINE / "main.py"), "run", "--dashboard"],
            cwd=tmp_path, env=env, stdout=child, stderr=child, stdin=child)
        os.close(child)
        seen = bytearray()
        while True:
            try:
                chunk = os.read(parent, 65536)
            except OSError:
                break
            if not chunk:
                break
            seen += chunk
        code = process.wait(timeout=300)
    finally:
        os.close(parent)

    screen = seen.decode("utf-8", "replace")
    assert code == 0, screen[-3000:]
    # More than the one frame Rich draws at teardown. A panel that
    # redraws once is the frozen one, and that is what a real run showed:
    # sixteen frames in forty-four seconds, a longest gap of nine.
    frames = screen.count("TEIlle-douce   ")
    assert frames > 4, f"{frames} frame(s) for a four-volume run"
    # And the phase block, on a run where nothing went wrong. It is the
    # whole of what `Run.phase` was written for and it had no caller.
    assert "sourceDoc" in screen, screen[-4000:]
