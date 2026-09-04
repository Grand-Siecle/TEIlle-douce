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
from dataclasses import replace

import teille_douce
from teille_douce.cli import options
from teille_douce.settings import Settings, set_settings


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
    _add_run_arguments(run_parser, suppress_defaults=True)

    # The same options on the bare parser, so that the historical
    # invocation keeps working without naming a subcommand.
    _add_run_arguments(parser)
    parser.set_defaults(command="run")

    return parser


def _add_run_arguments(parser, *, suppress_defaults=False):
    """Declare `run`'s options. See teille_douce/cli/options.py."""
    return options.add_run_arguments(parser, suppress_defaults=suppress_defaults)


def settings_from(args, env=None):
    """Turn parsed arguments into the settings of this run.

    A flag that was not given is absent from the mapping, so it falls
    through to the environment, the config file and finally the default —
    which is what makes precedence per setting rather than per layer.
    """
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

    enabled = options.resolve_phases(
        build_parser(), getattr(args, "phase_ops", None)
    )
    if enabled is not None:
        for phase in options.PHASES:
            flags[phase] = phase in enabled

    level = options.console_level(args)
    if level is not None:
        flags["log_level"] = level

    settings = Settings.load(flags=flags, env=env)

    # A value the environment offers is refused with a warning and the
    # default is kept -- that is the documented contract. A value the user
    # typed on the command line is a usage error: keeping a default they
    # explicitly overrode would be answering a different question.
    typed = [r for r in settings.rejected if r.layer == "flag"]
    if typed:
        build_parser().error(
            "; ".join(f"--{r.name.replace('_', '-')}={r.raw!r} {r.reason}"
                      for r in typed)
        )

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
    """Insert the default subcommand when none was named.

    `teille-douce`, `teille-douce --skip-existing` and `teille-douce
    LIV0044` all mean `run`. The command is inserted only when no token is
    one — so `teille-douce --skip-existing run` is left for argparse, and
    an output directory that happens to be called "run" is not mistaken
    for the subcommand.
    """
    argv = list(argv)
    if any(token in commands for token in argv):
        return argv
    if argv and argv[0] in ("-h", "--help", "-V", "--version"):
        return argv
    return ["run", *argv]


def main(argv=None):
    """Parse *argv* and run the requested command.

    Returns:
        int or None: the process exit status; ``None`` means success. The
        pipeline raises SystemExit itself on the paths that already did.
    """
    import sys

    args = build_parser().parse_args(
        normalise(sys.argv[1:] if argv is None else argv)
    )
    set_settings(settings_from(args))

    # Imported here, not at module scope: run.py configures logging and
    # names this run's log file when it is imported, and `--help` and
    # `--version` must do neither.
    from teille_douce.cli import run as run_command

    commands = {"run": run_command.execute}
    return commands[args.command](args)
