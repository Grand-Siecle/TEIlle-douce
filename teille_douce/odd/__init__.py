"""The project's ODD: compiling it, and checking it has not drifted.

It was `scripts/build_odd.py` and `scripts/rng_simplify.py`. The logic
moved here for the same reason the fixture's did — `scripts/` is not
importable from an installed distribution, so `teille-douce odd` could
not have shared it — and, being inside the package now, it is in English
like the rest of it.
"""

from .build import Toolchain, compile_odd, verify, without_the_date
from .simplify import simplify

__all__ = ["Toolchain", "compile_odd", "verify", "without_the_date", "simplify"]
