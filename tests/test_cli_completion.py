# -----------------------------------------------------------
# `teille-douce completion`: generated from the parser, never written.
#
# A list of flags copied into a shell script diverges at the first PR that
# adds one, and a completion offering a flag the program does not have is
# worse than none: it is a promise the parser then refuses. So what is
# fixed here is that the generator READS, and that what it emits is
# syntactically valid for the shell it names.
#
# Run: venv/bin/python -m pytest tests/test_cli_completion.py -q
# -----------------------------------------------------------
import argparse
import shutil
import subprocess

import pytest

from teille_douce.cli import app
from teille_douce.cli import completion as command


def a_parser():
    """A parser of the same shape, with a flag this file has never seen."""
    parser = argparse.ArgumentParser(prog="teille-douce")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    made_up = subparsers.add_parser("levitate", help="rise gently")
    made_up.add_argument("--altitude", metavar="M", help="how high")
    made_up.add_argument("--slowly", action="store_true", help="take time")
    made_up.add_argument("--mood", choices=("calm", "brisk"), help="how")
    return parser


# =============================================================================
# It reads
# =============================================================================

def test_a_flag_this_file_has_never_heard_of_is_completed():
    """The anti-drift property, and the only one that matters: nothing in
    `completion.py` knows what `--altitude` is."""
    known = command.surface(a_parser())

    levitate, = known.commands
    flags = [flag for option in levitate.options for flag in option.flags]
    assert flags == ["--altitude", "--slowly", "--mood"]
    for shell in command.SHELLS:
        # `fish` writes `-l altitude`, so the bare name is what all three
        # have in common.
        assert "altitude" in command.WRITERS[shell](known)


def test_a_closed_set_of_values_is_offered_because_argparse_declares_it():
    """`--block <TAB>` offers the three blocks because `record.py` has
    three, not because this file lists them."""
    known = command.surface(a_parser())
    mood, = [option for option in known.commands[0].options
             if option.flags == ("--mood",)]

    assert mood.choices == ("calm", "brisk")
    assert "calm brisk" in command.bash(known)
    assert "(calm brisk)" in command.zsh(known)
    assert "-a 'calm brisk'" in command.fish(known)


def test_a_flag_that_takes_no_value_is_not_asked_for_one():
    """zsh completing a value after `--slowly` puts the cursor where
    nothing can be typed."""
    known = command.surface(a_parser())
    by_flag = {option.flags[0]: option for option in known.commands[0].options}

    assert by_flag["--altitude"].takes_a_value is True
    assert by_flag["--slowly"].takes_a_value is False
    assert "'--slowly[take time]'" in command.zsh(known)


def test_every_subcommand_of_the_real_parser_is_offered():
    known = command.surface()
    names = {item.name for item in known.commands}

    assert {"run", "check", "validate", "info", "report", "odd", "fixture",
            "completion"} <= names
    for shell in command.SHELLS:
        script = command.WRITERS[shell]()
        for name in names:
            assert name in script, f"{name} missing from the {shell} script"


def test_every_flag_of_run_reaches_the_scripts():
    """`run` is the command with fifty flags; if any generator drops
    them, it drops them there."""
    known = command.surface()
    run, = [item for item in known.commands if item.name == "run"]
    flags = [flag for option in run.options for flag in option.flags]

    assert "--fail-on" in flags and "--max-page-loss" in flags
    for shell in command.SHELLS:
        script = command.WRITERS[shell]()
        for flag in flags:
            bare = flag.lstrip("-")
            assert bare in script, f"{flag} missing from the {shell} script"


# =============================================================================
# What it emits is valid
# =============================================================================

def test_the_bash_script_is_valid_bash(tmp_path):
    written = tmp_path / "teille-douce.bash"
    written.write_text(command.bash(), encoding="utf-8")

    finished = subprocess.run(["bash", "-n", str(written)],
                              capture_output=True, text=True)

    assert finished.returncode == 0, finished.stderr


def test_the_bash_script_installs_a_completion_for_the_command():
    script = command.bash()

    assert script.rstrip().endswith("complete -F _teille_douce teille-douce")


@pytest.mark.parametrize("shell,check", [
    ("zsh", ["zsh", "-n"]),
    ("fish", ["fish", "--no-execute"]),
])
def test_the_other_shells_accept_what_they_are_given(tmp_path, shell, check):
    if shutil.which(check[0]) is None:
        pytest.skip(f"{check[0]} is not installed")
    written = tmp_path / f"completion.{shell}"
    written.write_text(command.WRITERS[shell](), encoding="utf-8")

    finished = subprocess.run(check + [str(written)],
                              capture_output=True, text=True)

    assert finished.returncode == 0, finished.stderr


def test_the_help_reaches_the_script_as_the_help_reads():
    """Two escapes leak otherwise. argparse writes a literal per cent as
    `%%`, so `--max-page-loss` described itself as `PCT%%` in a file
    `--help` spells `PCT%`; and zsh's `_arguments` ends an option
    description at the first unescaped `]`, which nearly every flag here
    has, because it documents its default as `[OCR]`."""
    known = command.surface()
    run, = [item for item in known.commands if item.name == "run"]
    by_flag = {option.flags[0]: option for option in run.options}

    assert "%%" not in by_flag["--max-page-loss"].help
    assert "PCT%" in by_flag["--max-page-loss"].help
    assert "(OCR)" in by_flag["-i"].help and "[" not in by_flag["-i"].help

    for shell in command.SHELLS:
        script = command.WRITERS[shell](known)
        assert "%%" not in script
    # zsh delimits with `]`; the only ones left are the ones it writes.
    zsh = command.zsh(known)
    for line in zsh.splitlines():
        if line.strip().startswith("'-") and "[" in line:
            assert line.count("[") == line.count("]") == 1, line


def test_a_quote_in_a_help_string_cannot_break_the_script(tmp_path):
    """Help text is prose written by whoever added the flag. One
    apostrophe would close a shell string and leave the rest of the file
    as code — in a file people are told to source from their shell."""
    parser = argparse.ArgumentParser(prog="teille-douce")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    made_up = subparsers.add_parser("levitate", help="don't `fall`")
    made_up.add_argument("--altitude", help="the pilot's \"choice\"")
    known = command.surface(parser)

    assert known.commands[0].help == "dont fall"
    assert known.commands[0].options[0].help == "the pilots choice"

    written = tmp_path / "quoted.bash"
    written.write_text(command.bash(known), encoding="utf-8")
    finished = subprocess.run(["bash", "-n", str(written)],
                              capture_output=True, text=True)
    assert finished.returncode == 0, finished.stderr


# =============================================================================
# The surface
# =============================================================================

def test_the_shell_is_named_and_only_the_three_are_accepted(capsys):
    assert app.build_parser().parse_args(
        ["completion", "bash"]).shell == "bash"

    with pytest.raises(SystemExit) as raised:
        app.build_parser().parse_args(["completion", "elvish"])

    assert raised.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


@pytest.mark.parametrize("shell", command.SHELLS)
def test_the_command_prints_the_script_it_was_asked_for(capsys, shell):
    assert command.execute(app.build_parser().parse_args(
        ["completion", shell])) == 0

    printed = capsys.readouterr().out
    assert "teille-douce" in printed
    assert printed.endswith("\n") and printed.count("\n") > 10
