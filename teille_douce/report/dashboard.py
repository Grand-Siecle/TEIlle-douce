"""The live panel on a real screen.

The only module here that touches Rich, and the only one that cannot be
tested without a terminal — so it is kept to the smallest thing that can
work: turn spans into `Text`, hand them to `Live`, and stop.

Everything that decides what the panel *says* lives in `panel.py`, which
is pure. This file decides one thing only, and it is the one thing a pure
module cannot know: WHEN to draw.

It draws on a clock. Drawing only when the collector changed sounds
frugal and is the bug that made the panel useless: enrichment of one
volume is minutes of work between two events, and across those minutes
the clock, the estimate and the spinner all stood still. A measured run
redrew 16 times in 44 seconds — 0.36 a second, with a longest gap of 9.2
— and an operator reads a frozen panel as a hung run.
"""

import threading
import time

from rich.console import Group
from rich.live import Live
from rich.text import Text

from .panel import PALETTE, render_panel

# Four a second: fast enough that a spinner reads as alive, slow enough
# that a four-hour run does not spend a core on redrawing. It is also the
# ceiling on event-driven draws, which matters more: the per-container
# callback fires fourteen hundred times a volume.
REFRESH_PER_SECOND = 4
_INTERVAL = 1 / REFRESH_PER_SECOND


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
        # The collector's lock, not one of this class's own. The drawing
        # side walks `_phases`, `record` and `digest` while the pipeline
        # writes them — `tuple(dict.values())` over a dict being resized
        # raises — and a second lock would be taken in one order by the
        # heartbeat and the other by an event-driven draw, which is a
        # four-hour run hanging on a half-drawn frame.
        self._lock = run.lock
        self._beat = None
        self._stop = threading.Event()
        self._drawn_at = 0.0
        self._eta = ""
        self._rate = 0.0
        # Frames actually put on the screen. Read by the tests, and the
        # only honest way to ask "is this thing alive".
        self.frames = 0
        # A render that raised is kept, not lost and not fatal: the panel
        # is the one component that must never end the run, and a
        # heartbeat thread dying quietly would freeze it exactly as
        # before.
        self.failures = []

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self):
        self._live = Live(console=self._console, auto_refresh=False,
                          transient=False,
                          refresh_per_second=REFRESH_PER_SECOND)
        self._live.__enter__()
        self._beat = threading.Thread(target=self._heartbeat,
                                      name="teille-douce-panel", daemon=True)
        self._beat.start()
        return self

    def __exit__(self, *exception):
        # Torn down before the summary prints, so the summary lands in the
        # scrollback rather than inside a frame that will be erased.
        self._stop.set()
        beat, self._beat = self._beat, None
        # Joined outside the lock, and joined at all: a thread still
        # drawing writes frames over the summary that replaced it. It
        # cannot take longer than one interval, and a bounded wait is
        # still better than a hang if it somehow does.
        if beat is not None:
            beat.join(timeout=1.0)
        # One last frame, past the rate limit. The final state change of
        # a run — the last volume finishing — lands within a quarter of a
        # second of the one before it, so the throttle swallowed it and
        # the panel froze one frame short of the truth: "1 written · 0
        # failed · 1 to go" left on the screen of a finished run.
        self._render(force=True)
        with self._lock:
            live, self._live = self._live, None
        if live is not None:
            live.__exit__(*exception)
        return False

    def _heartbeat(self):
        while not self._stop.wait(_INTERVAL):
            self._render()

    # -- drawing -----------------------------------------------------------

    def draw(self, eta="", pages_per_second=0.0):
        """What the run calls when something changed.

        The two figures only the run can compute are recorded on every
        call; the frame itself is rate-limited, because the caller is a
        per-container callback and the clock will draw it within a
        quarter of a second anyway.
        """
        self._eta = eta
        self._rate = pages_per_second
        self._render()

    def snapshot(self):
        """The lines the next frame would carry. For tests, and for a
        caller that wants the panel without a terminal."""
        with self._lock:
            return self._compose()

    def _compose(self):
        self._tick += 1
        state = self._run.panel(eta=self._eta or self._run.eta(),
                                pages_per_second=self._rate)
        return render_panel(state, width=min(self._console.width, 100),
                            # The terminal's real height: the panel trims
                            # its own foot to fit rather than letting Rich
                            # crop it away from the bottom.
                            height=self._console.height, tick=self._tick)

    def _render(self, force=False):
        with self._lock:
            if self._live is None:
                return
            now = time.monotonic()
            if not force and now - self._drawn_at < _INTERVAL:
                return
            self._drawn_at = now
            try:
                self._live.update(to_renderable(self._compose()), refresh=True)
            except Exception as reason:      # pragma: no cover - defensive
                self.failures.append(reason)
                return
            self.frames += 1
