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

import teille_douce

# The pipeline body configures logging at import time, so it is imported
# lazily by dispatch() rather than here: `teille-douce --version` and
# `--help` must not create a run log.
_COMMANDS = ("run",)


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
    _add_run_arguments(run_parser)

    # The same options on the bare parser, so that the historical
    # invocation keeps working without naming a subcommand.
    _add_run_arguments(parser)
    parser.set_defaults(command="run")

    return parser


def _add_run_arguments(parser):
    """Declare `run`'s options, on both the subparser and the bare parser.

    Declared here rather than in `run.py` so that building the parser does
    not import the pipeline: `--help` and `--version` must not configure
    logging or open a run log.
    """
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="skip volumes whose TEI output already exists "
             "(minimal resume after an interrupted run)",
    )
    return parser


def main(argv=None):
    """Parse *argv* and run the requested command.

    Returns:
        int or None: the process exit status; ``None`` means success. The
        pipeline raises SystemExit itself on the paths that already did.
    """
    args = build_parser().parse_args(argv)

    from teille_douce.cli import run as run_command
    return run_command.execute(args)
