"""Where the files that are not code live, and whether they are here.

`Path(__file__).parent.parent.parent` is the repository root when the
package is imported from a checkout, and the directory holding
`site-packages/` when it is not: the wheel carries `teille_douce/` and
nothing else — no `schema/`, no `tests/`, no `OCR/`. Three modules used
that expression as though it were always the first, so an installed
distribution looked for `site-packages/schema/teille-douce.rng`, did not
find it, and said "missing: teille-douce odd build" — a command that
cannot help, because building it needs an ODD that is not there either.

This answers which of the two it is, once, so that each caller can say
something true. CLAUDE.md's `pip install -e .` is the supported install
and keeps `CHECKOUT` a real path; the console script makes the other one
reachable, and the schemas are not the only thing it lacks.
"""

from pathlib import Path

# What only a checkout has: the ODD every derivative is compiled from.
# The derivatives themselves are the wrong marker — asking for them is
# what the callers do, and a missing one is the case being distinguished.
MARKER = Path("schema") / "teille-douce.odd"


def find_checkout():
    """The source checkout this package belongs to, or None.

    Looked for above the package first, then above the working
    directory: `pip install -e .` leaves the package inside the
    checkout, a plain `pip install` does not, and the maintainer's
    commands are still typed from one.
    """
    here = Path(__file__).resolve()
    for start in (here, Path.cwd().resolve()):
        for ancestor in (start, *start.parents):
            if (ancestor / MARKER).exists():
                return ancestor
    return None


CHECKOUT = find_checkout()

# Where the files beside the package are looked for. A real path either
# way, so that a message can name it; when there is no checkout it
# simply does not hold them, and `CHECKOUT is None` is how a caller
# knows that "run the build command" is not the advice to give.
ROOT = CHECKOUT or Path(__file__).resolve().parent.parent
