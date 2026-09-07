"""
Command-line entry point of TEIlle-douce.

One parser, one subcommand for now. `run` is the pipeline; the read-only
commands (`check`, `validate`, `info`, `report`) and the toolchain ones
(`odd`, `fixture`) join it later — declaring them empty would be a promise
this package does not keep yet.

Invoking with no subcommand runs the pipeline, so `python3 main.py`,
`python3 main.py --skip-existing`, `teille-douce` and `teille-douce run`
all do what the pipeline has always done.
"""

import argparse
import sys
import tomllib
import warnings
from dataclasses import replace
from pathlib import Path

import teille_douce
from teille_douce.cli import options
from teille_douce.settings import Settings, find_config_file, set_settings


def build_parser():
    """Build the argument parser.

    Returns:
        argparse.ArgumentParser: the parser, whose namespace always carries
        a ``command`` attribute — ``"run"`` when none was given.
    """
    parser = argparse.ArgumentParser(
        prog="teille-douce",
        description="Convert ALTO XML documents to TEI P5 with the SegmOnto "
                    "taxonomy.",
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version=f"teille-douce {teille_douce.__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    run_parser = subparsers.add_parser(
        "run",
        help="convert ALTO volumes to TEI (the default)",
        description="Convert ALTO XML documents to TEI P5 with the SegmOnto "
                    "taxonomy.",
    )
    options.add_run_arguments(run_parser)
    parser.set_defaults(command="run")

    # The commands that write nothing, and the two that rebuild what the
    # repository versions. Their arguments are declared by the modules
    # that implement them, so a flag cannot exist in one place and be
    # parsed in another.
    from teille_douce.cli import (check, completion, fixture, info, odd,
                                  report, validate)

    check.add_arguments(subparsers.add_parser(
        "check",
        help="is this installation usable, and what would a run cost",
        description="Diagnose the input, the output, the catalogues and the "
                    "services, without converting anything."))
    validate.add_arguments(subparsers.add_parser(
        "validate",
        help="check produced TEI against the project schema and the known "
             "failure modes",
        description="Validate TEI output: the project content model, the "
                    "ODD's Schematron constraints, and the failure modes "
                    "this pipeline is known to have."))
    info.add_arguments(subparsers.add_parser(
        "info",
        help="which value is in force for a setting, and which layer set it",
        description="Print the settings of this installation with the layer "
                    "each value came from: flag, TDOUCE_* variable, config "
                    "file, default."))
    report.add_arguments(subparsers.add_parser(
        "report",
        help="go back to a run that is over: what it lost, and where",
        description="Read the record a past run left under "
                    ".teille-douce/runs/. Touches no corpus."))
    odd.add_arguments(subparsers.add_parser(
        "odd",
        help="compile the project schema from its ODD, or check it",
        description="Compile schema/teille-douce.odd into the RelaxNG, "
                    "Schematron and SVRL artefacts versioned beside it."))
    completion.add_arguments(subparsers.add_parser(
        "completion",
        help="print a shell completion, generated from this parser",
        description="Generate a completion script for bash, zsh or fish. "
                    "Read off the parser, so it cannot offer a flag this "
                    "program does not have."))
    fixture.add_arguments(subparsers.add_parser(
        "fixture",
        help="rebuild the versioned test fixture (needs the private corpus)",
        description="Rebuild the minimal ALTO fixture, or the golden file "
                    "the strict end-to-end diff compares against."))

    return parser


def config_file(args, parser):
    """The config file this run reads, or None.

    A file named explicitly and missing is a misconfiguration (exit 3), not
    a reason to fall back silently on a different one.
    """
    if getattr(args, "no_config", False):
        return None
    named = getattr(args, "config_file", None)
    if named:
        path = Path(named)
        if not path.is_file():
            parser.exit(3, f"teille-douce: config file not found: {path}\n")
        return path
    return find_config_file()


def _parser_for(parser, command):
    """The subparser handling *command*, or the top-level parser."""
    for action in parser._actions:
        if getattr(action, "choices", None) and not action.option_strings:
            return action.choices.get(command, parser)
    return parser


def settings_from(args, env=None, parser=None):
    """Turn parsed arguments into the settings of this run.

    A flag that was not given is absent from the mapping, so it falls
    through to the environment, the config file and finally the default —
    which is what makes precedence per setting rather than per layer.
    """
    parser = parser or build_parser()
    # Resolved once: computing it twice made every rejected TDOUCE_* value
    # warn twice, and the second resolution below used to omit it entirely,
    # so a level set in the config file was invisible to the -q floor.
    config_path = config_file(args, parser)
    flags = {}
    for name in options.PASSED_THROUGH:
        value = getattr(args, name, None)
        if value is not None:
            flags[name] = value

    concurrency = getattr(args, "concurrency", None)
    if concurrency is not None:
        flags["modernize_concurrency"] = concurrency
        flags["pyhellen_concurrency"] = concurrency

    flags.update(options.resolve_phases(parser, getattr(args, "phase_ops", None)))

    if getattr(args, "strict", False) and getattr(args, "command", "run") == "run":
        # An alias, resolved here so that only one name reaches the
        # settings: two spellings of one level is two things to keep in
        # step. `run`'s alone: `check --strict` and `validate` mean
        # something else by the word, and folding theirs in here set a
        # quality gate for a command that writes nothing.
        flags.setdefault("fail_on", "incident")

    if getattr(args, "force", False):
        flags["skip_existing"] = False
    elif getattr(args, "skip_existing", False):
        flags["skip_existing"] = True

    try:
        return _resolve_settings(args, env, parser, config_path, flags)
    except (ValueError, tomllib.TOMLDecodeError, OSError) as reason:
        # Exit 3, not a traceback and not 1: nothing ran, and 1 is
        # reserved for "some volumes failed".
        parser.exit(3, f"teille-douce: {reason}\n")


def _resolve_settings(args, env, parser, config_file, flags):
    """Fold the verbosity flags in, then resolve every layer."""
    level = options.console_level(args)
    if level is not None:
        if getattr(args, "quiet", 0) or getattr(args, "verbose", 0):
            # -q asks for less and -v for more, neither for the opposite:
            # an operator whose wrapper set ERROR and who adds -q must not
            # get every WARNING back, and one who set DEBUG and adds -v
            # must not end up at INFO.
            order = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
            with warnings.catch_warnings():
                # The layers are about to be resolved for real; warning
                # here would say everything twice.
                warnings.simplefilter("ignore", RuntimeWarning)
                without_flags = Settings.load(flags={}, env=env,
                                              config_file=config_file)
                # The floor is the console level this run would have had
                # with no verbosity flag, so it has to be derived by the
                # rule configure_logging actually applies: `debug` lowers
                # the console to DEBUG only when nothing else asked for a
                # level. Reading `debug` alone claimed a DEBUG floor for a
                # run whose console was really at the level the
                # environment set, and `-q` then let ERROR back up to
                # WARNING — louder for asking for less — while `-v` did
                # nothing at all.
                floor = ("DEBUG"
                         if (without_flags.debug
                             and without_flags.origin("log_level") == "default")
                         else without_flags.log_level)
            quieter = bool(getattr(args, "quiet", 0))
            asked, current = order.index(level), order.index(floor)
            if (asked > current) if quieter else (asked < current):
                flags["log_level"] = level
        else:
            flags["log_level"] = level

        # Only a level that asks FOR debug turns the diagnostics on. `-q`
        # asks for a quiet console; the debug setting also gates what goes
        # into the run log, which console verbosity has no business
        # touching.
        if level.strip().upper() == "DEBUG":
            flags["debug"] = True

    settings = Settings.load(flags=flags, env=env, config_file=config_file)

    # A value the environment offers is refused with a warning and the
    # default is kept -- that is the documented contract. A value the user
    # typed on the command line is a usage error: keeping a default they
    # explicitly overrode would be answering a different question.
    typed = [r for r in settings.rejected if r.layer == "flag"]
    if typed:
        # One option can feed two settings (--concurrency does), and the
        # reader does not need to be told twice.
        seen, messages = set(), []
        for rejection in typed:
            message = (f"{options.option_for(rejection.name)}="
                       f"{rejection.raw!r} {rejection.reason}")
            if message not in seen:
                seen.add(message)
                messages.append(message)
        parser.error("; ".join(messages))

    # Two answers a flag gives that no layer below it can express.
    if getattr(args, "no_log_file", False):
        settings = replace(settings, log_file=None)
    return settings


def normalise(argv, commands=None):
    """Put the subcommand first, inserting the default one if absent.

    `teille-douce`, `teille-douce --skip-existing` and `teille-douce
    LIV0044` all mean `run`, and `teille-douce --fast run --enrich` means
    what it looks like. Options are declared on the subcommand only —
    declaring them on both parsers instead was a trap: argparse's
    subparser action copies its whole namespace over the parent's, so an
    option given on both sides replaced rather than merged, and `--fast
    run --enrich` silently lost the --fast.

    A token is the command only if it is not the value of an option that
    takes one, so an output directory called "run" stays a directory.
    """
    argv = list(argv)
    # Derived, not hardcoded: the day `validate` is added, a hardcoded list
    # would rewrite `teille-douce validate out.xml` into a `run` whose
    # first document selector is "validate".
    commands = tuple(commands or _subcommands())
    if argv and argv[0] in ("-V", "--version"):
        return argv
    if argv and argv[0] in ("-h", "--help"):
        # The bare invocation IS `run`, so its help is `run`'s: the bare
        # parser alone would list a command and nothing you can pass it.
        return ["run", *argv]

    takes_a_value = _value_taking_options()
    index = None
    skip = False
    for position, token in enumerate(argv):
        if skip:
            skip = False
            continue
        if token == "--":
            # End of options: what follows is a positional, so no token
            # past it can be the subcommand.
            break
        if token.startswith("-"):
            # argparse accepts unambiguous prefixes, so `--inp OCR run`
            # must not read OCR as the first positional and `run` as a
            # document selector.
            name = token.split("=", 1)[0]
            if "=" in token:
                continue
            if name in takes_a_value:
                skip = True
                continue
            if name.startswith("--") and len(name) > 2:
                # argparse accepts unambiguous prefixes.
                matches = [o for o in _long_options() if o.startswith(name)]
                skip = len(matches) == 1 and matches[0] in takes_a_value
                continue
            # A short cluster. argparse hands the rest of the token to the
            # FIRST letter that wants a value, as an ATTACHED one: `-otei`
            # is `-o tei` and consumes nothing further, while `-qj 4` is
            # `-q -j 4` and does. Reading the last letter instead agreed
            # with argparse only until an attached value happened to end
            # in a short-option letter, and `-otei run` then swallowed
            # `run` as `-i`'s value and left a phantom selector behind.
            if len(name) > 2 and not name.startswith("--"):
                letters = name[1:]
                skip = False
                for offset, letter in enumerate(letters):
                    if f"-{letter}" in takes_a_value:
                        skip = offset == len(letters) - 1
                        break
            continue
        if token in commands:
            index = position
        break

    if index is None:
        return ["run", *argv]
    return [argv[index], *argv[:index], *argv[index + 1:]]


def _subcommands():
    """The subcommand names the parser accepts."""
    for action in build_parser()._actions:
        if getattr(action, "choices", None) and not action.option_strings:
            return tuple(action.choices)
    return ()


def _long_options():
    """Every long option string the parser knows, for prefix matching."""
    names = set()
    for parser in (build_parser(), *_subparsers()):
        for action in parser._actions:
            names.update(o for o in action.option_strings if o.startswith("--"))
    return names


def _subparsers():
    for action in build_parser()._actions:
        if getattr(action, "choices", None) and not action.option_strings:
            return tuple(action.choices.values())
    return ()


def _value_taking_options():
    """Option strings that consume the token after them."""
    parser = build_parser()
    names = set()
    for action in parser._actions:
        if action.nargs != 0 and action.option_strings:
            names.update(action.option_strings)
    for subparsers in [a for a in parser._actions if hasattr(a, "choices")]:
        for sub in (subparsers.choices or {}).values():
            for action in sub._actions:
                if action.nargs != 0 and action.option_strings:
                    names.update(action.option_strings)
    return names


def parse_args(argv=None):
    """Parse *argv* the way the command line does."""
    import sys
    return build_parser().parse_args(
        normalise(sys.argv[1:] if argv is None else argv)
    )


def main(argv=None):
    """Parse *argv* and run the requested command.

    Returns:
        int or None: the process exit status; ``None`` means success. The
        pipeline raises SystemExit itself on the paths that already did.
    """
    parser = build_parser()
    args = parser.parse_args(normalise(
        (sys.argv[1:] if argv is None else argv)
    ))
    # The subcommand's parser, so a usage error shows the usage line that
    # actually contains the option the message names.
    set_settings(settings_from(args, parser=_parser_for(parser, args.command)))

    # Imported here, not at module scope: run.py configures logging and
    # names this run's log file when it is imported, and `--help` and
    # `--version` must do neither.
    from teille_douce.cli import run as run_command

    from teille_douce.cli import (check, completion, fixture, info, odd,
                                  report, validate)

    commands = {"run": run_command.execute, "check": check.execute,
                "info": info.execute, "report": report.execute,
                "completion": completion.execute,
                "fixture": fixture.execute, "odd": odd.execute,
                "validate": validate.execute}
    try:
        return commands[args.command](args)
    except KeyboardInterrupt:
        # The document loop has its own handler, which stops cleanly and
        # keeps what it wrote. This one covers everything before and
        # after it — loading the CSVs, probing the services, unpacking
        # archives (minutes on the real corpus), writing the manifest —
        # where a Ctrl-C used to come out as a raw traceback. 130 is what
        # a shell reports for SIGINT, and it is not a verdict on the
        # corpus.
        print("\nInterrupted.", file=sys.stderr)
        return 130
