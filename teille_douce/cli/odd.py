"""`teille-douce odd` — compile the project schema, or check it."""

from pathlib import Path

from teille_douce.cli.exits import MISCONFIGURED, refuse
from teille_douce.odd import Toolchain, compile_odd, verify
from teille_douce.odd.build import ODD, SCHEMA
from teille_douce.paths import CHECKOUT


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
        if CHECKOUT is None:
            # The wheel carries `teille_douce/` and nothing else. Naming
            # a path under site-packages and stopping there would send
            # someone looking for a file that was never installed.
            # 3, not 1: nothing was compiled. `odd check` returns 1 for
            # "the versioned schemas have drifted", and a CI must be able
            # to tell that apart from a toolchain that is not there.
            refuse("teille-douce odd needs the source checkout: the ODD it "
                   "compiles is versioned beside the sources, in schema/, "
                   "and an installed distribution does not carry it. This "
                   "is a maintainer's command — clone the repository, or "
                   "run it from one.", MISCONFIGURED, "teille-douce odd")
        refuse(f"ODD not found: {ODD}", MISCONFIGURED, "teille-douce odd")

    toolchain = (Toolchain(args.toolchain_dir) if args.toolchain_dir
                 else Toolchain())
    toolchain.prepare(refresh=args.refresh)
    if args.action == "check":
        return verify(toolchain=toolchain)
    compile_odd(SCHEMA, toolchain=toolchain)
    return 0
