# -----------------------------------------------------------
# The only part of the panel that needs a screen.
#
# Kept to the smallest thing that can work, because it is the one piece
# this suite cannot exercise honestly: Rich's Live owns a refresh thread
# and a cursor. What IS tested here is that nothing decided anywhere else
# gets re-decided on the way to the screen.
#
# Run: venv/bin/python -m pytest tests/test_dashboard_live.py -q
# -----------------------------------------------------------
from pathlib import Path

from rich.console import Console

from teille_douce.report.collector import Run
from teille_douce.report.dashboard import Dashboard, to_renderable
from teille_douce.report.panel import Span


def test_a_span_becomes_text_with_a_style_and_never_markup():
    """A volume name carrying a bracket would be parsed as a tag and
    either vanish or crash the render. Styles are applied, not written."""
    group = to_renderable([[Span("LIV[0038]_reconciled", "plate")]])

    line, = group.renderables
    assert line.plain == "LIV[0038]_reconciled"


def test_the_rendering_carries_the_same_characters_as_the_pure_panel():
    """Rich is handed the panel; it is not asked to lay one out."""
    lines = [[Span(" a"), Span("b", "vermilion")], [Span("c")]]

    group = to_renderable(lines)

    assert [line.plain for line in group.renderables] == [" ab", "c"]


def test_drawing_before_the_panel_is_open_does_nothing():
    """The run calls `draw` from several places; none of them should have
    to know whether the panel is up."""
    run = Run(input_dir=Path("OCR"), output_dir=Path("out"), volumes=1,
              pages=1, log_path=Path("x.log"))
    dashboard = Dashboard(Console(), run)

    dashboard.draw()          # no Live yet, and no exception


def test_the_panel_is_torn_down_before_anything_else_prints():
    """The summary has to land in the scrollback, not inside a frame that
    is about to be erased."""
    run = Run(input_dir=Path("OCR"), output_dir=Path("out"), volumes=1,
              pages=1, log_path=Path("x.log"))
    console = Console(file=open("/dev/null", "w"), force_terminal=True,
                      width=92)
    dashboard = Dashboard(console, run)

    with dashboard:
        assert dashboard._live is not None
    assert dashboard._live is None
