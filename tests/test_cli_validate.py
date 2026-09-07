# -----------------------------------------------------------
# `teille-douce validate`: the three defaults that were the wrong ones,
# and the compilation that happened twice.
#
# The checks themselves are exercised by tests/test_validate_tei.py, which
# is where they have always been. What is fixed here is the envelope:
# which schemas are applied without being asked, how many workers, what an
# argument may be, and where the schemas are built.
#
# Run: venv/bin/python -m pytest tests/test_cli_validate.py -q
# -----------------------------------------------------------
from pathlib import Path

import pytest

from teille_douce.cli import app
from teille_douce.cli import validate as command

TEI = ("<TEI xmlns='http://www.tei-c.org/ns/1.0'><teiHeader/>"
       "<text><body><ab>une ligne</ab></body></text></TEI>")


def parse(argv):
    return app.build_parser().parse_args(["validate", *argv])


# =============================================================================
# The defaults
# =============================================================================

def test_the_project_schema_is_applied_without_being_asked():
    """It was opt-in, and its absence forced the program to print a note
    saying five invariants had not been checked. A default that has to
    apologise is the wrong default."""
    assert parse([]).odd is True
    assert parse(["--no-odd"]).odd is False


def test_the_number_of_workers_is_not_one():
    """It was 1, and every invocation in the documentation passes -j 8.
    A default everyone overrides is a false default."""
    assert parse([]).jobs == "auto"
    assert command._workers("auto", 40) > 1
    # Never more than there are files, and never more than the machine.
    assert command._workers("auto", 1) == 1
    assert command._workers("64", 3) == 3


def test_an_argument_may_be_a_directory(tmp_path):
    """The question asked is almost always "check what I have just
    written", and the answer used to be a shell glob."""
    written = tmp_path / "out"
    written.mkdir()
    for name in ("b.xml", "a.xml"):
        (written / name).write_text(TEI, encoding="utf-8")
    (written / "notes.txt").write_text("x", encoding="utf-8")

    found = command.expand([str(written)], tmp_path / "unused")

    assert [path.name for path in found] == ["a.xml", "b.xml"]


def test_no_argument_means_the_output_directory(tmp_path):
    written = tmp_path / "tei_output"
    written.mkdir()
    (written / "one.xml").write_text(TEI, encoding="utf-8")

    assert command.expand([], written) == [written / "one.xml"]


# =============================================================================
# The truncations, and the compilation that happened twice
# =============================================================================

def test_the_caps_are_a_display_decision_and_can_be_lifted(tmp_path, capsys):
    """They were applied when the errors were COLLECTED — twenty RelaxNG
    failures, twenty Schematron violations — so a file with more was said
    to have exactly twenty, and no flag could lift it."""
    from teille_douce.cli.validate import _print

    verdicts = [("f.xml", [f"error {n}" for n in range(30)], [])]

    _print(verdicts, max_errors=10, with_schematron=True)
    ten = capsys.readouterr().out
    assert ten.count("ERROR") == 10
    assert "20 more" in ten

    _print(verdicts, max_errors=0, with_schematron=True)
    assert capsys.readouterr().out.count("ERROR") == 30


def test_the_schemas_are_compiled_in_one_place_only():
    """The parent built relaxng, odd_rng, the Schematron and the Saxon
    processor; the worker built them again into the context the check
    actually reads — in the parallel path and in the sequential one, where
    the worker runs in the same process. The parent's four objects were
    never read, and on a one-megabyte tei_all.rng that is not free."""
    import inspect

    source = inspect.getsource(command.execute)

    for compiled in ("RelaxNG(", "project_relaxng(", "project_schematron("):
        assert compiled not in source, (
            f"{compiled} is built in the parent as well as in the worker")
    assert "available(" in source, "the parent no longer asks what is possible"


def test_asking_what_is_possible_compiles_nothing(monkeypatch):
    """`available` exists so the parent can print its note without paying
    for a schema it will not read."""
    from teille_douce.validation import schemas

    def refuse(*_, **__):
        raise AssertionError("a schema was compiled to answer a question")

    monkeypatch.setattr(schemas, "project_relaxng", refuse)
    monkeypatch.setattr(schemas, "project_schematron", refuse)

    schemas.available(with_odd=True)
    schemas.available(with_odd=False)


# =============================================================================
# What it says when it cannot do everything
# =============================================================================

def test_a_missing_project_schema_names_the_command_that_builds_it(
        monkeypatch, tmp_path):
    from teille_douce.validation import Missing, schemas

    monkeypatch.setattr(schemas, "ODD_RNG", tmp_path / "absent.rng")

    with pytest.raises(Missing, match="odd build"):
        schemas.available(with_odd=True)


def test_nothing_to_check_is_said_rather_than_reported_as_success(tmp_path):
    with pytest.raises(SystemExit, match="nothing to check"):
        command.execute(parse([str(tmp_path)]))
