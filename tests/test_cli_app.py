# -----------------------------------------------------------
# Structure of the command-line entry point.
#
# These tests pin what the package promises, not what the pipeline
# produces: that the historical invocation keeps working, that a single
# version is published in a single place, and that the multiprocessing
# workers stay reachable by qualified name from the renamed package.
#
# Run: venv/bin/python -m pytest tests/test_cli_app.py -q
# -----------------------------------------------------------
import importlib
import importlib.metadata
import os
import subprocess
import sys

import pytest

import teille_douce
from teille_douce.cli import app


def parse(argv):
    """The real entry path: normalise, then parse. Options live on the
    subcommand only, and `normalise` is what puts it first."""
    return app.parse_args(argv)


def test_bare_invocation_defaults_to_run():
    """`teille-douce` and `python3 main.py`, with no subcommand, must keep
    converting — that is the whole backwards-compatibility promise."""
    assert parse([]).command == "run"
    assert parse(["--skip-existing"]).command == "run"
    assert parse(["--skip-existing"]).skip_existing is True
    assert parse(["run", "--skip-existing"]).skip_existing is True
    assert parse(["run"]).skip_existing is False


def test_an_option_given_before_the_subcommand_survives_it():
    """argparse's subparser action copies its whole namespace over the
    parent's, defaults included, so `--skip-existing run` used to reset the
    flag to False and silently reconvert the corpus."""
    assert parse(["--skip-existing", "run"]).skip_existing is True


def test_bare_help_is_the_help_of_run():
    """`teille-douce -h` with no subcommand must document what a bare
    invocation actually does. The top-level parser alone lists a command
    and none of the options you can pass it."""
    assert app.normalise(["-h"]) == ["run", "-h"]
    assert app.normalise(["--help"]) == ["run", "--help"]


def test_a_short_cluster_ending_in_a_value_taking_letter_keeps_its_value():
    """`-qj 4` is `-q -j 4`: the value belongs to the LAST letter of the
    cluster. Testing the whole token left `4` looking like a positional,
    and the subcommand behind it was read as a document selector."""
    assert app.normalise(["-qj", "4", "run", "LIV1"]) == [
        "run", "-qj", "4", "LIV1"]
    args = app.parse_args(["-qj", "4", "run", "LIV1", "--no-config"])
    assert args.documents == ["LIV1"]
    assert app.settings_from(args, env={}).max_workers == 4


def test_an_attached_short_value_does_not_swallow_the_subcommand():
    """argparse gives the rest of a cluster to the FIRST letter that wants
    a value, attached: `-otei` is `-o tei` and consumes nothing further.
    Reading the LAST letter instead agreed with argparse only until an
    attached value happened to end in a short-option letter — and the `i`
    of `tei` then made `run` look like `-i`'s value, leaving a phantom
    document selector that stops the run as an unmatched typo."""
    assert app.normalise(["-otei", "run"]) == ["run", "-otei"]
    assert app.parse_args(["-otei", "run", "--no-config"]).documents == []


def test_the_cluster_model_agrees_with_argparse_on_every_cluster():
    """The property, not three examples of it: for every short cluster the
    parser accepts, normalise must consume the next token exactly when
    argparse would. Anything else moves a token across the boundary
    between an option's value and a positional."""
    import contextlib
    import io
    import itertools
    import warnings

    parser = app.build_parser()
    shorts = sorted({
        option[1]
        for sub in app._subparsers()
        for action in sub._actions
        for option in action.option_strings
        if len(option) == 2 and option.startswith("-")
        and option not in ("-h", "-V")
    })

    def argparse_consumes_the_next_token(cluster):
        sink = io.StringIO()
        try:
            with contextlib.redirect_stderr(sink), \
                 contextlib.redirect_stdout(sink), \
                 warnings.catch_warnings():
                warnings.simplefilter("ignore")
                args = parser.parse_args(["run", cluster, "MARK", "ZZZ"])
        except SystemExit:
            return None            # not a cluster this parser accepts
        return "MARK" not in (args.documents or [])

    checked, disagreements = 0, []
    for size in (2, 3):
        for letters in itertools.product(shorts, repeat=size):
            cluster = "-" + "".join(letters)
            truth = argparse_consumes_the_next_token(cluster)
            if truth is None:
                continue
            checked += 1
            rewritten = app.normalise([cluster, "MARK", "run"])
            if (rewritten == ["run", cluster, "MARK"]) != truth:
                disagreements.append((cluster, truth, rewritten))

    assert checked > 100, "the alphabet of short options went missing"
    assert not disagreements, disagreements[:5]


def test_the_published_version_is_the_package_version():
    """A version may live in exactly one place. `[project]` reads
    `teille_douce.__version__`, so the two can never disagree — unless the
    distribution metadata is stale, which is what this catches."""
    assert teille_douce.__version__

    try:
        installed = importlib.metadata.version("teille-douce")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("teille-douce is not installed in this environment")

    assert installed == teille_douce.__version__


@pytest.mark.parametrize(
    "qualified_name",
    [
        "teille_douce.sourcedoc.builder:_init_worker",
        "teille_douce.sourcedoc.builder:_build_surface_fragment",
    ],
)
def test_worker_entry_points_are_importable_by_name(qualified_name):
    """A `forkserver`/`spawn` child re-imports in a fresh interpreter and
    resolves the pool's callables by qualified name. Renaming the package
    breaks that at run time, inside a child process, never at import — so
    it gets its own cheap guard."""
    module_name, _, attribute = qualified_name.partition(":")
    module = importlib.import_module(module_name)

    assert callable(getattr(module, attribute))


def test_module_entry_point_runs(tmp_path):
    """`python -m teille_douce` is a documented way in, so it is exercised
    rather than merely declared. --version is the cheapest command that
    reaches the module entry point without touching a corpus."""
    try:
        importlib.metadata.version("teille-douce")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("teille-douce is not installed in this environment")

    from test_e2e_pipeline import _env_couverture_sous_processus

    env = {**os.environ, **_env_couverture_sous_processus()}
    res = subprocess.run(
        [sys.executable, "-m", "teille_douce", "--version"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )

    assert res.returncode == 0, res.stderr[-2000:]
    assert teille_douce.__version__ in res.stdout


# =============================================================================
# The console script
# =============================================================================

def test_the_console_script_imports_nothing_expensive_at_module_level():
    """setuptools' generated wrapper does `from <module> import main` at
    module level. `teille_douce.cli` there meant pandas and lxml — a
    quarter of a second — were imported outside anything that could catch
    a Ctrl-C, so an interrupt in that window came out as a traceback from
    inside pandas, over a run that had not started."""
    import ast
    import inspect as introspect

    from teille_douce import launcher

    tree = ast.parse(introspect.getsource(launcher))
    imported = [node for node in tree.body
                if isinstance(node, (ast.Import, ast.ImportFrom))]
    names = [alias.name for node in imported
             for alias in getattr(node, "names", [])]
    assert names == ["sys"], f"the wrapper's import is no longer free: {names}"


def test_the_entry_point_declared_in_the_metadata_is_the_guarded_one():
    """A `[project.scripts]` naming `teille_douce.cli:main` would put the
    heavy imports back outside the guard, and nothing else would change."""
    entry, = [point for point in
              importlib.metadata.distribution("teille-douce").entry_points
              if point.name == "teille-douce"]
    assert entry.value == "teille_douce.launcher:main"


def test_an_interrupt_before_the_run_starts_is_not_a_traceback(monkeypatch,
                                                               capsys):
    """130 is what a shell reports for SIGINT, and nothing has run yet:
    there is nothing to report but the interruption itself."""
    from teille_douce import cli, launcher

    def interrupt(_argv):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "main", interrupt)

    assert launcher.main([]) == 130
    assert "Interrupted" in capsys.readouterr().err
