"""`teille-douce odd` — compile the project schema, or check it."""

from pathlib import Path

from teille_douce.odd import Toolchain, compile_odd, verify
from teille_douce.odd.build import ODD, SCHEMA


def add_arguments(parser):
    """The surface of `teille-douce odd`."""
    parser.add_argument(
        "action", nargs="?", default="build", choices=("build", "check"),
        help="build: recompile the three artefacts beside the ODD. "
             "check: recompile in a temporary directory and compare, "
             "exiting 1 if the versioned schemas have drifted")
    parser.add_argument(
        "--refresh", action="store_true",
        help="download the pinned toolchain again before compiling")
    parser.add_argument(
        "--toolchain-dir", type=Path, default=None, metavar="DIR",
        help="where the pinned TEI Stylesheets, p5subset and SchXslt live "
             "[.odd-toolchain/]")
    return parser


def execute(args):
    if not ODD.exists():
        raise SystemExit(f"ODD not found: {ODD}")

    toolchain = (Toolchain(args.toolchain_dir) if args.toolchain_dir
                 else Toolchain())
    toolchain.prepare(refresh=args.refresh)
    if args.action == "check":
        return verify(toolchain=toolchain)
    compile_odd(SCHEMA, toolchain=toolchain)
    return 0
