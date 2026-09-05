"""Which reporter a run gets.

A live panel redrawing four times a second is right in a terminal and
catastrophic in a CI log, where it becomes forty thousand lines of
half-drawn frames. Every rule here exists because getting it wrong is
silent: nobody notices a dashboard that never appeared, and nobody reads
the log that swallowed one.

Precedence, highest first: the flag typed just now, `TDOUCE_UI`, then what
the terminal can actually do.
"""

from enum import Enum

# Below these the panel stops being scannable and starts hiding the lines
# it exists to show. Low enough for a split pane.
MIN_WIDTH = 56
# Measured, not chosen. A volume open with its five phases is already
# fifteen rows before a single incident or folded warning: six of header,
# the document line, five phases, the totals row, the closing rule and
# the ctrl-c foot. Twelve was a guess, and under it the panel cut away
# the part it exists for while still calling itself the better view.
MIN_HEIGHT = 16

# Set by every CI system worth naming. A CI runner is a tty often enough
# to fool the check, and its log is read long after the run.
_CI_MARKERS = ("CI", "CONTINUOUS_INTEGRATION", "GITHUB_ACTIONS",
               "GITLAB_CI", "BUILDKITE", "JENKINS_URL", "TEAMCITY_VERSION")


class UI(Enum):
    DASHBOARD = "dashboard"
    PLAIN = "plain"


def choose_ui(env, is_tty, size, plain=False, dashboard=False, quiet=0,
              dry_run=False, asked=None):
    """The reporter this run should use.

    `dry_run` is accepted and deliberately changes nothing: it resolves
    everything and prints a plan, and there is nothing about that which
    needs the panel suppressed. Named here rather than left out so the
    caller can pass the whole invocation without the reader wondering
    whether it was forgotten.
    """
    if plain and dashboard:
        raise ValueError("--plain and --dashboard contradict each other")
    if plain:
        return UI.PLAIN
    if dashboard:
        # The automatic rules exist to guess, not to forbid: someone
        # piping into `less -R` on purpose knows what they are doing.
        return UI.DASHBOARD

    # Already validated by the settings layer when the caller passes it;
    # `env` remains for callers that have no Settings, which is only the
    # tests of this matrix.
    if asked is None:
        asked = env.get("TDOUCE_UI")
        if asked is not None:
            asked = asked.strip().lower()
            if asked not in ("auto", "plain", "dashboard"):
                raise ValueError(
                    f"TDOUCE_UI={asked!r} is not one of auto, plain, dashboard")
    if asked == "plain":
        return UI.PLAIN
    if asked == "dashboard":
        return UI.DASHBOARD

    if quiet:
        # -q asks for less; a panel redrawing four times a second is more.
        return UI.PLAIN
    if not is_tty:
        return UI.PLAIN
    if env.get("TERM", "").strip().lower() == "dumb":
        # Only when the terminal says so. An absent TERM is common in
        # perfectly capable environments, and treating it as dumb would
        # refuse the panel to most of them.
        return UI.PLAIN
    if any(env.get(marker) for marker in _CI_MARKERS):
        return UI.PLAIN
    width, height = size
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        return UI.PLAIN
    return UI.DASHBOARD
