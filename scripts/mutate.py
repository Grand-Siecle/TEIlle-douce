#!/usr/bin/env python3
"""Does this test fail against the code it condemns?

`CLAUDE.md` requires a regression test with every fix and says, in
capitals, *check that it does*. The check is mechanical — revert the
line, run the test, watch it fail — and doing it by hand is why it gets
skipped: six review rounds on the CLI instalment found guards that
passed against the very code they were written for, and one of them had
been written in the round before.

    scripts/mutate.py teille_douce/report/text.py:120 tests/test_text.py
    scripts/mutate.py --changed                      # every line this branch touched

Not `mutmut`. That mutates a whole package — nine thousand statements
here, hours — while the question this project actually asks is narrow:
*the line I just changed, against the test I just wrote*. So a mutation
is a line NUMBER and a test, and the mutations tried on it are the four
that turn a guard into no guard:

    a condition to `False`     the branch never runs
    a condition to `True`      the branch always runs
    a `return X` to `return`   the value stops being computed
    a body to `pass`           the statement does nothing

A test that survives all four applicable ones is a test that does not
hold, and this prints it as such. Timeouts are part of the answer: a
mutation that HANGS is a mutation the test caught, since the guard it
removed was there to stop a block.
"""

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Long enough for a real end-to-end test, short enough that a hang is
# noticed rather than waited out.
TIMEOUT = 180
# Where the original waits while its file is mutated. A suffix, so it
# sits beside the file and `restore_any` finds it by walking the tree.
BACKUP = ".mutate-backup"


def _statement(tree, line):
    """The smallest statement containing *line*.

    A line is not a unit of Python: `column = min(max(...),\n  ...)` is
    one statement over two lines, and replacing its first line with
    `pass` leaves the second dangling — reported as "would not parse",
    which is a tool failure dressed up as a result. The tree knows where
    the statement begins and ends.
    """
    found = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt):
            continue
        if node.lineno <= line <= (node.end_lineno or node.lineno):
            if found is None or node.lineno > found.lineno:
                found = node
    return found


def _mutations(node):
    """The mutations applicable to one statement, as (label, transform).

    The four that turn a guard into no guard. Each is a change to the
    TREE, so what is written back is always valid Python — a mutation
    that does not parse would otherwise be counted as one the tests
    caught, when it is only a broken file.
    """
    if isinstance(node, (ast.If, ast.While)):
        return [("condition always false",
                 lambda n: setattr(n, "test", ast.Constant(False))),
                ("condition always true",
                 lambda n: setattr(n, "test", ast.Constant(True)))]
    if isinstance(node, ast.Return) and node.value is not None:
        return [("value not computed",
                 lambda n: setattr(n, "value", ast.Constant(None)))]
    if isinstance(node, (ast.Try, ast.For, ast.With, ast.FunctionDef)):
        # Emptying a whole block says nothing useful — of course the
        # tests notice. The interesting mutation is inside it.
        return []
    return [("statement removed", None)]


def _mutated(source, line, transform):
    """*source* with the statement at *line* mutated, or None.

    `ast.unparse` rewrites the whole module, so comments and formatting
    go. Nothing here reads them: the file is restored afterwards and the
    tests judge behaviour.
    """
    tree = ast.parse(source)
    node = _statement(tree, line)
    if node is None:
        return None
    if transform is None:
        replaced = _replace_with_pass(tree, node)
        if not replaced:
            return None
    else:
        transform(node)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def _replace_with_pass(tree, target):
    """Swap one statement for `pass`, wherever it sits in the tree."""
    for parent in ast.walk(tree):
        for _, value in ast.iter_fields(parent):
            if not isinstance(value, list):
                continue
            for index, item in enumerate(value):
                if item is target:
                    value[index] = ast.Pass()
                    return True
    return False


def _runs_clean(tests):
    """True when the tests pass — which, under a mutation, is bad news."""
    try:
        finished = subprocess.run(
            # `-x`: the answer is "did ANY test notice", so the first
            # one that does is the whole answer. A caught mutation costs
            # seconds instead of the eighty a full suite takes, which is
            # what makes `--changed` usable rather than only correct —
            # and most mutations are caught, so most of the run is that
            # saving.
            [sys.executable, "-m", "pytest", *tests, "-x", "-q",
             "--no-header", "-p", "no:cacheprovider"],
            cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        # A hang is a caught mutation: the guard that was removed was
        # there to stop a block, and a suite that hangs is a suite that
        # noticed.
        return False
    return finished.returncode == 0


def _keep(path):
    """Put the file back, whatever happens to this process.

    Not a `try/finally`: a `finally` does not run when the process is
    KILLED, and the first thing this tool did on a real branch was get
    terminated mid-mutation by a timeout, leaving `report/text.py`
    unparsed and stripped of every comment in the working tree. A tool
    that edits your source has to survive its own death.

    So the original goes to a sibling file first. `restore_any()` puts
    back whatever a previous run left behind, and runs before anything
    else — the recovery is on the next invocation, which is the only
    place it can be.
    """
    kept = path.with_suffix(path.suffix + BACKUP)
    kept.write_bytes(path.read_bytes())
    return kept


def restore_any(say=print):
    """Undo what a killed run left behind. Returns what it restored."""
    restored = []
    for kept in ROOT.rglob("*" + BACKUP):
        original = kept.with_suffix("")
        original.write_bytes(kept.read_bytes())
        kept.unlink()
        restored.append(original.relative_to(ROOT))
        say(f"restored {original.relative_to(ROOT)} from a killed run")
    return restored


def check(target, tests, say=print):
    """Mutate one line and report which mutations the tests survive."""
    path_text, _, line_text = target.rpartition(":")
    path = ROOT / path_text
    line = int(line_text)
    source = path.read_text(encoding="utf-8")
    try:
        ast.parse(source)
    except SyntaxError as reason:
        raise SystemExit(f"{path} does not parse: {reason}")

    node = _statement(ast.parse(source), line)
    if node is None:
        say("  no statement on that line")
        return []

    survivors = []
    for label, transform in _mutations(node):
        mutated = _mutated(source, line, transform)
        if mutated is None:
            say(f"  {label:22} skipped (nothing to mutate)")
            continue
        kept = _keep(path)
        path.write_text(mutated, encoding="utf-8")
        try:
            if _runs_clean(tests):
                survivors.append(label)
                say(f"  {label:22} SURVIVED")
            else:
                say(f"  {label:22} caught")
        finally:
            path.write_text(source, encoding="utf-8")
            kept.unlink(missing_ok=True)
    return survivors


# Not measured, so not mutated: `tests/` is the thing being judged, and
# `scripts/` is one-line launchers plus this file. `pyproject.toml`'s
# coverage `omit` says the same in the same words.
SKIP = ("tests/", "scripts/")


def changed_lines(base="main"):
    """Every STATEMENT this branch touched, as `path:line`, once each.

    The question is never "is this package well tested" but "does what I
    just wrote hold", so the target is the diff. Resolved to statements
    and deduplicated: a diff is lines, and a two-line statement counted
    twice runs the whole suite twice for one answer — 275 targets for
    this branch became 30.

    Comments, blank lines and docstrings resolve to no statement worth
    mutating and drop out here rather than costing a suite run each.
    """
    finished = subprocess.run(
        ["git", "diff", "-U0", f"{base}...HEAD", "--", "*.py"],
        cwd=ROOT, capture_output=True, text=True)
    touched, path = {}, None
    for line in finished.stdout.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("@@") and path and not path.startswith(SKIP):
            span = re.search(r"\+(\d+)(?:,(\d+))?", line)
            start = int(span.group(1))
            touched.setdefault(path, set()).update(
                range(start, start + int(span.group(2) or 1)))

    targets = []
    for path, lines in touched.items():
        try:
            tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        starts = set()
        for line in sorted(lines):
            node = _statement(tree, line)
            if node is None or _is_prose(node):
                continue
            starts.add(node.lineno)
        targets.extend(f"{path}:{start}" for start in sorted(starts))
    return targets


def _is_prose(node):
    """A docstring: text, not behaviour, and mutating it proves nothing."""
    return (isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str))


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="scripts/mutate.py",
        description="Mutate a line and check the tests notice.")
    parser.add_argument("target", nargs="?", metavar="FILE:LINE",
                        help="the line to mutate")
    parser.add_argument("tests", nargs="*", metavar="TEST",
                        help="what to run against it [the whole suite]")
    parser.add_argument("--changed", action="store_true",
                        help="every line this branch added, against the "
                             "whole suite; slow, and the one to run before "
                             "asking for a review")
    parser.add_argument("--base", default="main", metavar="REF",
                        help="what --changed compares against [main]")
    args = parser.parse_args(argv)

    if args.changed:
        targets, tests = changed_lines(args.base), []
    elif args.target:
        targets, tests = [args.target], args.tests
    else:
        parser.error("a FILE:LINE, or --changed")

    restore_any()
    survived = 0
    for target in targets:
        mutations = _mutations(_statement(
            ast.parse((ROOT / target.rpartition(":")[0]).read_text(
                encoding="utf-8")), int(target.rpartition(":")[2])) or ast.Pass())
        if not mutations:
            # A `def`, a `class`, a whole `try`: emptying one says
            # nothing useful, and printing a bare header for it makes
            # the report look like it found nothing when it did not look.
            continue
        print(target)
        try:
            survived += len(check(target, tests))
        except (OSError, ValueError, IndexError) as reason:
            print(f"  skipped ({reason})")
    print(f"\n{survived} mutation{'s' if survived != 1 else ''} survived")
    # 1, not 0: a surviving mutation is a test that does not hold, and
    # this is meant to be usable from a hook.
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
