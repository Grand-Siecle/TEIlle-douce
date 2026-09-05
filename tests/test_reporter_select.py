# -----------------------------------------------------------
# Which reporter a run gets, and why.
#
# A live panel redrawing four times a second is right in a terminal and
# catastrophic in a CI log, where it becomes forty thousand lines of
# half-drawn frames. The choice has to be automatic and overridable, and
# every rule below exists because getting it wrong is silent: nobody
# notices a dashboard that never appeared, and nobody reads the CI log
# that swallowed one.
#
# Run: venv/bin/python -m pytest tests/test_reporter_select.py -q
# -----------------------------------------------------------
import pytest

from teille_douce.report.select import UI, choose_ui


def choose(env=None, tty=True, width=92, height=30, **flags):
    return choose_ui(env=env or {}, is_tty=tty, size=(width, height), **flags)


# =============================================================================
# A terminal gets the panel; anything else does not
# =============================================================================

def test_an_interactive_terminal_gets_the_dashboard():
    assert choose() is UI.DASHBOARD


def test_a_pipe_gets_the_journal():
    """Redirected to a file, a live panel is forty thousand lines of
    half-drawn frames."""
    assert choose(tty=False) is UI.PLAIN


def test_a_dumb_terminal_gets_the_journal():
    """TERM=dumb is a terminal saying it cannot move the cursor."""
    assert choose(env={"TERM": "dumb"}) is UI.PLAIN


def test_a_continuous_integration_run_gets_the_journal():
    """CI is a tty often enough to fool the check, and its log is read
    long after the run."""
    assert choose(env={"CI": "true"}) is UI.PLAIN
    assert choose(env={"GITHUB_ACTIONS": "true"}) is UI.PLAIN


def test_a_terminal_too_small_for_the_panel_gets_the_journal():
    """Below the floor the panel stops being scannable and starts hiding
    the very lines it exists to show."""
    assert choose(width=40) is UI.PLAIN
    assert choose(height=8) is UI.PLAIN


def test_the_floor_is_low_enough_for_a_normal_split_pane():
    assert choose(width=60, height=14) is UI.DASHBOARD


# =============================================================================
# What the operator asked for wins
# =============================================================================

def test_the_dashboard_flag_beats_every_automatic_refusal():
    """A reader who pipes into `less -R` on purpose knows what they are
    doing, and the automatic rules exist to guess, not to forbid."""
    assert choose(tty=False, dashboard=True) is UI.DASHBOARD
    assert choose(env={"CI": "true"}, dashboard=True) is UI.DASHBOARD


def test_the_plain_flag_beats_a_perfectly_good_terminal():
    assert choose(plain=True) is UI.PLAIN


def test_asking_for_both_is_a_usage_error():
    """No reading of `--plain --dashboard` says what was wanted."""
    with pytest.raises(ValueError, match="contradict"):
        choose(plain=True, dashboard=True)


# =============================================================================
# The environment sits between the automatic rules and the flags
# =============================================================================

def test_the_environment_variable_overrides_the_automatic_rules():
    assert choose(env={"TDOUCE_UI": "plain"}) is UI.PLAIN
    assert choose(env={"TDOUCE_UI": "dashboard"}, tty=False) is UI.DASHBOARD


def test_a_flag_overrides_the_environment_variable():
    assert choose(env={"TDOUCE_UI": "dashboard"}, plain=True) is UI.PLAIN


def test_auto_means_decide_automatically():
    assert choose(env={"TDOUCE_UI": "auto"}, tty=False) is UI.PLAIN
    assert choose(env={"TDOUCE_UI": "auto"}) is UI.DASHBOARD


def test_an_unknown_value_is_refused_rather_than_ignored():
    """Silently ignoring it is how someone spends an afternoon wondering
    why their setting does nothing."""
    with pytest.raises(ValueError, match="TDOUCE_UI"):
        choose(env={"TDOUCE_UI": "fancy"})


# =============================================================================
# Quiet and structured output are not compatible with a live panel
# =============================================================================

def test_quiet_means_no_panel():
    """-q asks for less; a panel redrawing four times a second is more."""
    assert choose(quiet=1) is UI.PLAIN


def test_a_dry_run_still_gets_a_panel():
    """It resolves everything and prints a plan; there is nothing about
    it that needs the panel suppressed."""
    assert choose(dry_run=True) is UI.DASHBOARD
