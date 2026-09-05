"""The live panel on a real screen.

The only module here that touches Rich, and the only one that cannot be
tested without a terminal — so it is kept to the smallest thing that can
work: turn spans into `Text`, hand them to `Live`, and stop.

Everything that decides what the panel says lives in `panel.py`, which is
pure. This file decides nothing.
"""

from rich.console import Group
from rich.live import Live
from rich.text import Text

from .panel import PALETTE, render_panel

# Four a second: fast enough that a spinner reads as alive, slow enough
# that a four-hour run does not spend a core on redrawing.
REFRESH_PER_SECOND = 4


def to_renderable(lines):
    """Spans to Rich `Text`, one line each.

    `Text.append` with an explicit style, never markup: a document name
    with a bracket in it would otherwise be parsed as a tag and either
    vanish or crash the render.
    """
    rendered = []
    for line in lines:
        text = Text(no_wrap=True, overflow="crop")
        for span in line:
            text.append(span.text, style=PALETTE.get(span.style) or None)
        rendered.append(text)
    return Group(*rendered)


class Dashboard:
    """Draws the panel while a run goes, and gets out of the way after."""

    def __init__(self, console, run):
        self._console = console
        self._run = run
        self._live = None
        self._tick = 0

    def __enter__(self):
        self._live = Live(console=self._console, auto_refresh=False,
                          transient=False,
                          refresh_per_second=REFRESH_PER_SECOND)
        self._live.__enter__()
        return self

    def __exit__(self, *exception):
        # Torn down before the summary prints, so the summary lands in the
        # scrollback rather than inside a frame that will be erased.
        live, self._live = self._live, None
        if live is not None:
            live.__exit__(*exception)
        return False

    def draw(self, eta="", pages_per_second=0.0):
        if self._live is None:
            return
        self._tick += 1
        # The run computes it from what actually happened; the caller
        # only overrides it in tests.
        state = self._run.panel(eta=eta or self._run.eta(),
                                pages_per_second=pages_per_second)
        width = min(self._console.width, 100)
        self._live.update(
            to_renderable(render_panel(state, width=width,
                                       # The terminal's real height: the
                                       # panel trims its incident list to
                                       # fit rather than letting Rich
                                       # ellipsise the foot away.
                                       height=self._console.height,
                                       tick=self._tick)),
            refresh=True)
