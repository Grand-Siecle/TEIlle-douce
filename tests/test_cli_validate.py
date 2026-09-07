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
    assert command._odd(parse([]).odd)[1] is True
    assert command._odd(parse(["--no-odd"]).odd)[1] is False


def test_a_demanded_schema_and_an_inherited_one_are_told_apart():
    """What a missing schema does depends on which of the two it was: a
    flag the user typed is a demand, the default is a preference."""
    assert command._odd(parse(["--odd"]).odd) == (True, True)
    assert command._odd(parse([]).odd) == (False, True)
    assert command._odd(parse(["--no-odd"]).odd) == (False, False)


def test_the_number_of_workers_is_not_one(monkeypatch):
    """It was 1, and every invocation in the documentation passes -j 8.
    A default everyone overrides is a false default.

    The machine is stubbed, both ways round. Asserting `_workers("64", 3)
    == 3` was true on this laptop and false on the two-core CI runner,
    where the answer is 2 — a test whose truth depends on the machine
    proves whichever of the two caps that machine happens to apply.
    """
    assert parse([]).jobs == "auto"

    monkeypatch.setattr(command, "cpu_count", lambda: 8)
    assert command._workers("auto", 40) == 8      # the machine caps it
    assert command._workers("auto", 1) == 1       # so does the file count
    assert command._workers("64", 3) == 3         # and so does the flag

    monkeypatch.setattr(command, "cpu_count", lambda: 2)
    assert command._workers("64", 3) == 2
    assert command._workers("auto", 40) == 2


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

    _print(verdicts, max_errors=10)
    ten = capsys.readouterr().out
    assert ten.count("ERROR") == 10
    assert "20 more" in ten

    _print(verdicts, max_errors=0)
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


def test_nothing_to_check_is_said_rather_than_reported_as_success(tmp_path,
                                                                  capsys):
    with pytest.raises(SystemExit) as raised:
        command.execute(parse([str(tmp_path)]))

    # 3: nothing ran. It exited 1, which in this CLI means "some files
    # failed validation" — a wrapper read an empty directory as a corpus
    # that does not conform.
    assert raised.value.code == 3
    assert "nothing to check" in capsys.readouterr().err


# =============================================================================
# What a typed value does, and what an inherited one does
# =============================================================================

def test_a_job_count_that_is_not_a_number_is_a_usage_error(capsys):
    """It lost its `type=int` in the move, so `-j eight` reached `int()`
    inside the worker count and came out as a traceback with exit 1 —
    which in this CLI means "some files failed validation", so a typo in a
    command line was read by a wrapper as a corpus problem. CLAUDE.md: a
    value typed as a flag is a usage error rather than a fallback."""
    with pytest.raises(SystemExit) as raised:
        parse(["-j", "eight"])
    assert raised.value.code == 2
    assert "not auto or a number" in capsys.readouterr().err

    with pytest.raises(SystemExit) as raised:
        parse(["-j", "0"])
    assert raised.value.code == 2


def test_the_workers_are_started_with_forkserver_not_fork(tmp_path, monkeypatch):
    """`available()` has just imported saxonche, so the parent carries
    SaxonC's native runtime and its threads; forking a multi-threaded
    process is a DeprecationWarning today and an error in 3.14.
    `sourcedoc/builder.py` says so and does the same."""
    import multiprocessing

    asked, real = [], multiprocessing.get_context

    def record(method=None):
        asked.append(method)
        return real(method)

    monkeypatch.setattr(multiprocessing, "get_context", record)
    # Two workers whatever the runner has: `_workers` caps on cpu_count,
    # and a single-core machine would take the sequential path and prove
    # nothing about the one being fixed here.
    monkeypatch.setattr(command, "cpu_count", lambda: 4)
    written = tmp_path / "out"
    written.mkdir()
    for name in ("a.xml", "b.xml"):
        (written / name).write_text(TEI, encoding="utf-8")

    command.execute(parse(["--no-odd", "-j", "2", str(written)]))

    assert asked and asked[0] in ("forkserver", "spawn")
    assert "fork" not in [method for method in asked if method != "forkserver"]


def test_a_schema_that_is_not_installed_narrows_the_check_and_stops_a_demand(
        tmp_path, monkeypatch, capsys):
    """`--odd` became the default, and a default that cannot be satisfied
    must not fail a run that asked for nothing: a wheel install carries no
    `schema/`, so every `teille-douce validate` exited on a missing file.
    A typed `--odd` is a demand and still fails."""
    from teille_douce.validation import schemas

    monkeypatch.setattr(schemas, "ODD_RNG", tmp_path / "absent.rng")
    written = tmp_path / "doc.xml"
    written.write_text(TEI, encoding="utf-8")

    assert command.execute(parse([str(written)])) == 0
    printed = capsys.readouterr().out
    assert "not applied" in printed and "[ok]" in printed

    with pytest.raises(SystemExit) as raised:
        command.execute(parse(["--odd", str(written)]))
    assert raised.value.code == 3


def test_an_installed_distribution_is_not_told_to_run_odd_build(
        monkeypatch, tmp_path):
    """`teille-douce odd build` compiles the ODD into the schemas. An
    installed distribution has no ODD either, so naming that command as
    the remedy sends someone in a circle."""
    from teille_douce.validation import Missing, schemas

    monkeypatch.setattr(schemas, "CHECKOUT", None)
    monkeypatch.setattr(schemas, "ODD_RNG", tmp_path / "absent.rng")

    with pytest.raises(Missing) as raised:
        schemas.available(with_odd=True)

    assert "odd build" not in str(raised.value)
    assert "pip install -e" in str(raised.value)


def test_nothing_to_check_names_the_directory_that_was_asked_about(tmp_path,
                                                                   capsys):
    """It always named the configured output directory, so
    `validate /srv/exports/batch-12` answered about `tei_output` — a path
    the user never mentioned, and one that may well hold files."""
    elsewhere = tmp_path / "batch-12"
    elsewhere.mkdir()

    with pytest.raises(SystemExit) as raised:
        command.execute(parse([str(elsewhere)]))

    assert raised.value.code == 3
    assert "batch-12" in capsys.readouterr().err


def test_the_json_verdicts_say_which_schemas_were_applied(tmp_path, capsys,
                                                          monkeypatch):
    """The notes are prose and `--json` suppresses them, so a wrapper
    reading the verdicts could not tell a complete check from one narrowed
    by an absent Schematron — the same "a partial check looks complete"
    the notes exist to prevent."""
    import json

    from teille_douce.validation import schemas

    written = tmp_path / "doc.xml"
    written.write_text(TEI, encoding="utf-8")

    command.execute(parse(["--no-odd", "--json", str(written)]))
    narrowed = json.loads(capsys.readouterr().out)
    assert narrowed["applied"] == ["python invariants"]

    monkeypatch.setattr(schemas, "ODD_SVRL", tmp_path / "absent.xsl")
    command.execute(parse(["--json", str(written)]))
    without_schematron = json.loads(capsys.readouterr().out)
    assert "teille-douce.rng" in without_schematron["applied"]
    assert "teille-douce.svrl.xsl" not in without_schematron["applied"]


def test_a_schema_that_cannot_be_read_is_refused_before_any_worker(tmp_path,
                                                                   capsys):
    """A `Pool` whose initializer raises respawns its workers for ever.
    `validate --schema tei_all.rgn out/` — one transposed letter in the
    invocation the guide, schema.md and schema/README.md all teach —
    burned two cores with nothing on screen until it was killed, and the
    parallel path is the default now."""
    written = tmp_path / "out"
    written.mkdir()
    for name in ("a.xml", "b.xml"):
        (written / name).write_text(TEI, encoding="utf-8")

    with pytest.raises(SystemExit) as raised:
        command.execute(parse(["--no-odd", "--schema",
                               str(tmp_path / "absent.rng"), str(written)]))
    assert raised.value.code == 2
    assert "is not a file" in capsys.readouterr().err

    not_xml = tmp_path / "notes.md"
    not_xml.write_text("# not a schema\n", encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        command.execute(parse(["--no-odd", "--schema", str(not_xml),
                               str(written)]))
    assert raised.value.code == 2
    assert "cannot be parsed as XML" in capsys.readouterr().err

    not_rng = tmp_path / "doc.xml"
    not_rng.write_text(TEI, encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        command.execute(parse(["--no-odd", "--schema", str(not_rng),
                               str(written)]))
    assert raised.value.code == 2
    assert "not a RELAX NG grammar" in capsys.readouterr().err


def test_a_schema_that_will_not_compile_fails_the_run_rather_than_the_files(
        tmp_path, capsys, monkeypatch):
    """It parses as XML and is a grammar, so the parent lets it through
    and every worker fails identically on it. That is one broken
    argument, not a corpus that does not conform, so it is 3 and not the
    1 that means "some files failed validation"."""
    monkeypatch.setattr(command, "cpu_count", lambda: 1)
    broken = tmp_path / "broken.rng"
    broken.write_text('<grammar xmlns="http://relaxng.org/ns/structure/1.0">'
                      '<start><ref name="nope"/></start></grammar>',
                      encoding="utf-8")
    written = tmp_path / "doc.xml"
    written.write_text(TEI, encoding="utf-8")

    with pytest.raises(SystemExit) as raised:
        command.execute(parse(["--no-odd", "--schema", str(broken),
                               str(written)]))

    assert raised.value.code == 3
    assert "could not be compiled" in capsys.readouterr().err
