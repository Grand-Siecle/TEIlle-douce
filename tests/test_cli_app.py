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
