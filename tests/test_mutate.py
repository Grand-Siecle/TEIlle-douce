# -----------------------------------------------------------
# `scripts/mutate.py`: the tool that edits your source.
#
# It answers the question CLAUDE.md asks in capitals — does this test
# fail against the code it condemns — by reverting a line and running
# the tests. Which means it writes to files under version control, and a
# tool that does that has to be right about putting them back: the first
# time it ran on a real branch it was killed by a timeout mid-mutation
# and left `report/text.py` unparsed and stripped of every comment.
#
# Run: venv/bin/python -m pytest tests/test_mutate.py -q
# -----------------------------------------------------------
import ast
import subprocess
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "scripts"))

import mutate  # noqa: E402

TWO_LINE = '''"""A module."""


def widest(items, room):
    column = min(max(len(item) for item in items) + 2,
                 max(20, room // 3))
    if column > room:
        return room
    return column
'''


@pytest.fixture
def a_module(tmp_path, monkeypatch):
    """A file under a ROOT of its own, so nothing real is touched."""
    monkeypatch.setattr(mutate, "ROOT", tmp_path)
    written = tmp_path / "sample.py"
    written.write_text(TWO_LINE, encoding="utf-8")
    return written


# =============================================================================
# A line is not a unit of Python
# =============================================================================

def test_a_statement_spanning_two_lines_is_found_from_either(a_module):
    """`column = min(max(…),\\n  …)` is one statement over two lines, and
    replacing the first with `pass` leaves the second dangling — which
    the first version reported as "would not parse", a tool failure
    dressed up as a result."""
    tree = ast.parse(a_module.read_text(encoding="utf-8"))

    first = mutate._statement(tree, 5)
    second = mutate._statement(tree, 6)

    assert isinstance(first, ast.Assign) and first is not None
    assert second.lineno == first.lineno


def test_mutating_a_two_line_statement_still_parses(a_module):
    source = a_module.read_text(encoding="utf-8")

    mutated = mutate._mutated(source, 6, None)

    assert mutated is not None
    ast.parse(mutated)                      # the point of using the tree
    assert "min(max(" not in mutated


def test_a_condition_is_mutated_both_ways(a_module):
    source = a_module.read_text(encoding="utf-8")
    node = mutate._statement(ast.parse(source), 7)

    labels = [label for label, _ in mutate._mutations(node)]

    assert labels == ["condition always false", "condition always true"]
    for label, transform in mutate._mutations(node):
        fresh = ast.parse(source)
        transform(mutate._statement(fresh, 7))
        assert "if " in ast.unparse(fresh)


def test_a_docstring_is_not_worth_mutating(a_module):
    tree = ast.parse(a_module.read_text(encoding="utf-8"))

    assert mutate._is_prose(mutate._statement(tree, 1))
    assert not mutate._is_prose(mutate._statement(tree, 5))


# =============================================================================
# Putting the file back
# =============================================================================

def test_the_file_is_restored_after_a_run(a_module, monkeypatch):
    monkeypatch.setattr(mutate, "_runs_clean", lambda tests: False)
    before = a_module.read_text(encoding="utf-8")

    mutate.check("sample.py:7", [], say=lambda *_: None)

    assert a_module.read_text(encoding="utf-8") == before
    assert not list(a_module.parent.glob("*" + mutate.BACKUP))


def test_a_killed_run_is_undone_by_the_next_one(a_module):
    """A `finally` does not run when the process is KILLED, and the
    first thing this tool did on a real branch was get terminated by a
    timeout mid-mutation. The recovery is on the next invocation,
    because that is the only place it can be."""
    before = a_module.read_text(encoding="utf-8")
    kept = mutate._keep(a_module)
    a_module.write_text("# what a killed run left\n", encoding="utf-8")

    restored = mutate.restore_any(say=lambda *_: None)

    assert [str(path) for path in restored] == ["sample.py"]
    assert a_module.read_text(encoding="utf-8") == before
    assert not kept.exists()


def test_a_backup_is_never_committed():
    """An unparsed, comment-less copy of a source file, added by a
    `git add -A` after a killed run."""
    ignored = (RACINE / ".gitignore").read_text(encoding="utf-8")

    assert "*" + mutate.BACKUP in ignored


# =============================================================================
# What it reports
# =============================================================================

def test_a_surviving_mutation_is_named_and_counted(a_module, monkeypatch):
    """The whole output of this tool is the survivors: a mutation
    nothing notices is a test that does not hold."""
    monkeypatch.setattr(mutate, "_runs_clean", lambda tests: True)
    said = []

    survivors = mutate.check("sample.py:7", [], say=said.append)

    assert survivors == ["condition always false", "condition always true"]
    assert all("SURVIVED" in line for line in said)


def test_a_hang_counts_as_caught(a_module, monkeypatch):
    """The guard that was removed was there to stop a block, and a suite
    that hangs is a suite that noticed."""
    def never_returns(*_, **__):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=1)

    monkeypatch.setattr(mutate.subprocess, "run", never_returns)

    assert mutate._runs_clean([]) is False


def test_a_surviving_mutation_is_a_non_zero_exit(a_module, monkeypatch):
    """Meant to be usable from a hook."""
    monkeypatch.setattr(mutate, "_runs_clean", lambda tests: True)
    monkeypatch.setattr(mutate, "restore_any", lambda *_, **__: [])

    assert mutate.main(["sample.py:7"]) == 1

    monkeypatch.setattr(mutate, "_runs_clean", lambda tests: False)
    assert mutate.main(["sample.py:7"]) == 0


# =============================================================================
# What `--changed` asks about
# =============================================================================

def test_changed_lines_asks_once_per_statement(tmp_path, monkeypatch):
    """A diff is lines, and a two-line statement counted twice runs the
    whole suite twice for one answer — 275 targets for this branch
    became thirty."""
    monkeypatch.setattr(mutate, "ROOT", tmp_path)
    (tmp_path / "sample.py").write_text(TWO_LINE, encoding="utf-8")

    def diff(*_, **__):
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout=(
                "+++ b/sample.py\n"
                "@@ -1,0 +5,2 @@\n"          # both lines of one statement
                "@@ -1,0 +7,1 @@\n"), stderr="")

    monkeypatch.setattr(mutate.subprocess, "run", diff)

    assert mutate.changed_lines() == ["sample.py:5", "sample.py:7"]


def test_the_tests_and_the_tools_are_not_mutated(tmp_path, monkeypatch):
    """`tests/` is the thing being judged, and `scripts/` is one-line
    launchers plus this tool. `pyproject.toml`'s coverage `omit` says
    the same in the same words."""
    monkeypatch.setattr(mutate, "ROOT", tmp_path)

    def diff(*_, **__):
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout=(
                "+++ b/tests/test_thing.py\n@@ -1,0 +2,1 @@\n"
                "+++ b/scripts/mutate.py\n@@ -1,0 +2,1 @@\n"), stderr="")

    monkeypatch.setattr(mutate.subprocess, "run", diff)

    assert mutate.changed_lines() == []


def test_a_return_loses_its_value_and_a_block_is_left_alone(a_module):
    """Two more of the four. `return X` → `return None` asks whether
    anything reads the value; a whole `def` or `try` emptied is a
    mutation nothing could fail to notice, so it is not offered — a
    report full of trivially-caught mutations hides the one that
    matters."""
    tree = ast.parse(a_module.read_text(encoding="utf-8"))

    returning = mutate._statement(tree, 8)
    labels = [label for label, _ in mutate._mutations(returning)]
    assert labels == ["value not computed"]

    whole_function = mutate._statement(tree, 4)
    assert isinstance(whole_function, ast.FunctionDef)
    assert mutate._mutations(whole_function) == []


def test_a_file_that_does_not_parse_is_said_rather_than_traced(tmp_path,
                                                               monkeypatch):
    monkeypatch.setattr(mutate, "ROOT", tmp_path)
    (tmp_path / "broken.py").write_text("def (:", encoding="utf-8")

    with pytest.raises(SystemExit, match="does not parse"):
        mutate.check("broken.py:1", [], say=lambda *_: None)


def test_a_line_with_no_statement_on_it_is_said(a_module):
    said = []

    assert mutate.check("sample.py:2", [], say=said.append) == []
    assert "no statement" in said[0]


def test_the_target_or_changed_is_required(capsys):
    with pytest.raises(SystemExit) as raised:
        mutate.main([])

    assert raised.value.code == 2
    assert "FILE:LINE" in capsys.readouterr().err


def test_the_tests_are_run_against_the_mutated_tree(a_module, monkeypatch):
    """The one thing this tool must not get wrong: what pytest sees is
    the MUTATED file, and what is left afterwards is not."""
    seen = []

    def look(tests):
        seen.append(a_module.read_text(encoding="utf-8"))
        return False

    monkeypatch.setattr(mutate, "_runs_clean", look)

    mutate.check("sample.py:7", [], say=lambda *_: None)

    # Both mutations of the condition, each seen by the tests in turn.
    assert any("if False:" in source for source in seen)
    assert any("if True:" in source for source in seen)
    left = a_module.read_text(encoding="utf-8")
    assert "if False:" not in left and "if True:" not in left
