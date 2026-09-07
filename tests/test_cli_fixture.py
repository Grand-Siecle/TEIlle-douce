# -----------------------------------------------------------
# Asking for the help must not destroy the fixture.
#
# `scripts/build_test_fixture.py` tested `if "--golden" in sys.argv` and
# let everything else fall through to `main()`, whose first act is
# `rmtree` of the versioned fixture. `--help` included. Every typo
# included. The file the strict golden diff compares against was one
# mistyped flag away from being rebuilt from a corpus most machines do
# not have — and on a machine that does, silently rebuilt with today's
# thinning rules rather than the ones the golden was written under.
#
# It has an ArgumentParser now, which is all it ever needed.
#
# Run: venv/bin/python -m pytest tests/test_cli_fixture.py -q
# -----------------------------------------------------------
import subprocess
import sys
from pathlib import Path

import pytest

from teille_douce.cli import app, fixture

RACINE = Path(__file__).resolve().parent.parent
VERSIONED = RACINE / "tests" / "fixtures" / "alto_min" / "LIV9001_reconciled"
# The OTHER versioned file this command can rewrite, and the one the
# strict end-to-end diff compares against. The first version of this test
# fingerprinted only the fixture, so `--gold` — which argparse
# prefix-matched to `--golden` — regenerated the golden on every run of
# the suite while every assertion passed.
GOLDEN = RACINE / "tests" / "fixtures" / "golden"


def _fingerprint(directory):
    """Every file under *directory*, with its size. Enough to see a
    rebuild, cheap enough to take twice in a test."""
    return sorted((str(path.relative_to(directory)), path.stat().st_size)
                  for path in directory.rglob("*") if path.is_file())


# =============================================================================
# The command that writes nothing unless it is told to
# =============================================================================

@pytest.mark.parametrize("asked", [
    ["main.py", "fixture", "--help"],
    ["main.py", "fixture", "-h"],
    ["main.py", "fixture", "--goldne"],         # the typo that rebuilt it
    # An ABBREVIATION of the destructive flag. argparse prefix-matches by
    # default, so this used to mean `--golden` and rewrite the golden.
    ["main.py", "fixture", "--gold"],
    ["main.py", "fixture", "build", "--gold"],
    # Two ways of asking for different things: a usage error, not a
    # precedence rule that silently discards one of them.
    ["main.py", "fixture", "build", "--golden"],
    # And by the name two years of wrapper scripts use, which is where
    # the bug lived: this file had no parser at all.
    ["scripts/build_test_fixture.py", "--help"],
    ["scripts/build_test_fixture.py", "--goldne"],
])
def test_the_help_and_a_typo_leave_both_versioned_files_alone(asked):
    before = _fingerprint(VERSIONED), _fingerprint(GOLDEN)

    finished = subprocess.run(
        [sys.executable, str(RACINE / asked[0]), *asked[1:]],
        capture_output=True)

    assert (_fingerprint(VERSIONED), _fingerprint(GOLDEN)) == before, (
        f"`{' '.join(asked)}` rewrote a versioned file")
    # And it said something rather than doing something.
    assert finished.returncode in (0, 1, 2), finished.stderr.decode()[-800:]


def test_the_script_is_a_launcher_and_carries_no_logic_of_its_own():
    """It stays because CONTRIBUTING.md, the CI and two years of wrapper
    scripts invoke it by name. It carries nothing, so it cannot drift
    from the command it launches."""
    import ast

    launcher = (RACINE / "scripts" / "build_test_fixture.py").read_text(
        encoding="utf-8")
    # The code, not the prose: the docstring explains what used to be
    # here, and grepping the file would find its own explanation.
    tree = ast.parse(launcher)
    code = ast.unparse(ast.Module(
        body=[node for node in tree.body
              if not (isinstance(node, ast.Expr)
                      and isinstance(node.value, ast.Constant))],
        type_ignores=[]))

    assert "rmtree" not in code and "shutil" not in code
    assert "fixture" in code, "it does not launch the command it stands for"
    assert len(code.splitlines()) < 15, "a launcher, not a second copy"


# =============================================================================
# What it does when it IS told to
# =============================================================================

def test_build_writes_where_it_is_told_and_nowhere_else(tmp_path):
    """`--target` exists so a test can watch it work without touching
    the versioned copy."""
    source = tmp_path / "corpus"
    for relative, _, _ in fixture.PAGES:
        page = source / relative
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(
            "<alto xmlns='http://www.loc.gov/standards/alto/ns-v4#'>"
            "<Description><sourceImageInformation><fileName>x.jpg</fileName>"
            "</sourceImageInformation></Description>"
            "<Layout><Page PHYSICAL_IMG_NR='0'><PrintSpace><TextBlock>"
            "<TextLine><String CONTENT='une ligne'/></TextLine>"
            "</TextBlock></PrintSpace></Page></Layout></alto>",
            encoding="utf-8")
    before = _fingerprint(VERSIONED)

    fixture.build(source=source, target=tmp_path / "out", say=lambda *_: None)

    assert (tmp_path / "out" / "content" / "data" / "doc_1" / "f1.xml").exists()
    assert _fingerprint(VERSIONED) == before


def test_a_missing_corpus_is_said_before_anything_is_removed(tmp_path):
    """The refusal has to come first: the old order was `rmtree` and then
    the check, on the one path where the check was the point."""
    target = tmp_path / "out"
    target.mkdir()
    (target / "keep.xml").write_text("<alto/>", encoding="utf-8")

    with pytest.raises(SystemExit, match="source corpus not found"):
        fixture.build(source=tmp_path / "absent", target=target,
                      say=lambda *_: None)

    assert (target / "keep.xml").exists()


def test_the_subcommand_is_reachable_and_defaults_to_build():
    args = app.build_parser().parse_args(["fixture"])

    assert args.command == "fixture"
    # `None`, not `"build"`: the default has to be distinguishable from
    # the action typed out, or `fixture build --golden` cannot be told
    # from `fixture --golden` and one of the two is silently discarded.
    assert args.action is None
    assert args.golden is False


def test_the_fixture_is_not_removed_when_a_source_page_has_moved(tmp_path):
    """The whole-corpus guard was first and the per-page one was inside
    the loop, so one renamed page in the private corpus rmtree'd the
    versioned fixture, wrote the pages before it, and then raised — the
    file the strict golden diff compares against, deleted and half
    rebuilt, with no IIIF mapping."""
    source = tmp_path / "corpus"
    for relative, _, _ in fixture.PAGES[:-1]:        # one page short
        page = source / relative
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("<alto/>", encoding="utf-8")
    target = tmp_path / "out"
    target.mkdir()
    (target / "keep.xml").write_text("<alto/>", encoding="utf-8")

    with pytest.raises(SystemExit, match="nothing was removed"):
        fixture.build(source=source, target=target, say=lambda *_: None)

    assert (target / "keep.xml").exists()


def test_asking_for_the_fixture_and_the_golden_at_once_is_refused():
    from teille_douce.cli import app

    args = app.build_parser().parse_args(["fixture", "build", "--golden"])

    with pytest.raises(SystemExit, match="different things"):
        fixture.execute(args)
