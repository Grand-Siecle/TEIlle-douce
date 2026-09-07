# -----------------------------------------------------------
# `teille-douce odd`, and the launcher that still answers to the old name.
#
# The compilation chain itself cannot run here — it downloads a hundred
# megabytes of TEI Stylesheets and needs Saxon — so what is fixed here is
# the surface: which action is chosen, where the toolchain is looked for,
# and that the launcher and the subcommand cannot drift apart, since the
# launcher is what CLAUDE.md, schema/README.md and the CI still type.
#
# Run: venv/bin/python -m pytest tests/test_cli_odd.py -q
# -----------------------------------------------------------
import ast
from pathlib import Path

import pytest

from teille_douce.cli import app, odd
from teille_douce.odd.build import DEFAULT_TOOLCHAIN, Toolchain

RACINE = Path(__file__).resolve().parent.parent


def test_the_default_action_is_the_one_the_old_script_had():
    """`scripts/build_odd.py` with no argument compiled. The subcommand
    with no argument has to do the same, or two years of muscle memory
    silently starts checking instead of building."""
    args = app.build_parser().parse_args(["odd"])

    assert args.command == "odd"
    assert args.action == "build"
    assert args.refresh is False


def test_check_is_asked_for_by_name():
    assert app.build_parser().parse_args(["odd", "check"]).action == "check"


def test_the_launcher_translates_the_old_flag_into_the_new_action():
    """`--check` was a flag and is an action. The launcher is what the CI
    runs, so it has to accept the flag and mean the action — and it has to
    keep meaning `build` without it."""
    launcher = (RACINE / "scripts" / "build_odd.py").read_text(encoding="utf-8")
    code = ast.unparse(ast.Module(
        body=[node for node in ast.parse(launcher).body
              if not (isinstance(node, ast.Expr)
                      and isinstance(node.value, ast.Constant))],
        type_ignores=[]))

    assert "'--check'" in code or '"--check"' in code
    assert "'odd'" in code or '"odd"' in code
    assert "simplify" not in code and "saxonche" not in code
    assert len(code.splitlines()) < 20, "a launcher, not a second copy"


def test_the_toolchain_directory_can_be_moved(tmp_path):
    """`--toolchain-dir` exists so a machine with the hundred megabytes
    already unpacked somewhere does not fetch them twice."""
    elsewhere = Toolchain(tmp_path / "chain")

    assert elsewhere.p5 == tmp_path / "chain" / "p5subset.xml"
    assert Toolchain().p5.parent == DEFAULT_TOOLCHAIN
    assert all(path.is_relative_to(tmp_path) for path in elsewhere.required)


def test_a_missing_odd_is_named_rather_than_traced(monkeypatch, tmp_path):
    monkeypatch.setattr(odd, "ODD", tmp_path / "absent.odd")

    with pytest.raises(SystemExit, match="ODD not found"):
        odd.execute(app.build_parser().parse_args(["odd", "check"]))
