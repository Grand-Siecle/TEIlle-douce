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
import tomllib
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

    return parser


def _config_file(args, parser):
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


def settings_from(args, env=None, parser=None):
    """Turn parsed arguments into the settings of this run.

    A flag that was not given is absent from the mapping, so it falls
    through to the environment, the config file and finally the default —
    which is what makes precedence per setting rather than per layer.
    """
    parser = parser or build_parser()
    flags = {}
    for name in ("ocr_dir", "output_dir", "entities_dir", "metadata_csv",
                 "persons_csv", "pyhellen_url", "health_timeout",
                 "max_workers", "modernize_batch_size", "log_file"):
        value = getattr(args, name, None)
        if value is not None:
            flags[name] = value

    concurrency = getattr(args, "concurrency", None)
    if concurrency is not None:
        flags["modernize_concurrency"] = concurrency
        flags["pyhellen_concurrency"] = concurrency

    flags.update(options.resolve_phases(parser, getattr(args, "phase_ops", None)))

    if getattr(args, "force", False):
        flags["skip_existing"] = False
    elif getattr(args, "skip_existing", False):
        flags["skip_existing"] = True

    level = options.console_level(args)
    if level is not None:
        flags["log_level"] = level
        # Only a level that asks FOR debug turns the diagnostics on. `-q`
        # asks for a quiet console; the debug setting also gates what goes
        # into the run log, which console verbosity has no business
        # touching.
        if level == "DEBUG":
            flags["debug"] = True

    try:
        settings = Settings.load(flags=flags, env=env,
                                 config_file=_config_file(args, parser))
    except (ValueError, tomllib.TOMLDecodeError, OSError) as reason:
        # Exit 3, not a traceback and not 1: nothing ran, and 1 is
        # reserved for "some volumes failed".
        parser.exit(3, f"teille-douce: {reason}\n")

    # A value the environment offers is refused with a warning and the
    # default is kept -- that is the documented contract. A value the user
    # typed on the command line is a usage error: keeping a default they
    # explicitly overrode would be answering a different question.
    typed = [r for r in settings.rejected if r.layer == "flag"]
    if typed:
        parser.error("; ".join(
            f"{options.option_for(r.name)}={r.raw!r} {r.reason}" for r in typed
        ))

    # Two answers a flag gives that no layer below it can express.
    if getattr(args, "no_log_file", False):
        settings = replace(settings, log_file=None)
    modernize_url = getattr(args, "modernize_url", None)
    if modernize_url:
        settings = replace(
            settings,
            modernize_api={k: modernize_url for k in settings.modernize_api},
        )
    return settings


def normalise(argv, commands=("run",)):
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
    if argv and argv[0] in ("-h", "--help", "-V", "--version"):
        return argv

    takes_a_value = _value_taking_options()
    index = None
    skip = False
    for position, token in enumerate(argv):
        if skip:
            skip = False
            continue
        if token.startswith("-"):
            skip = token in takes_a_value
            continue
        if token in commands:
            index = position
        break

    if index is None:
        return ["run", *argv]
    return [argv[index], *argv[:index], *argv[index + 1:]]


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
    args = parse_args(argv)
    set_settings(settings_from(args))

    # Imported here, not at module scope: run.py configures logging and
    # names this run's log file when it is imported, and `--help` and
    # `--version` must do neither.
    from teille_douce.cli import run as run_command

    commands = {"run": run_command.execute}
    return commands[args.command](args)
