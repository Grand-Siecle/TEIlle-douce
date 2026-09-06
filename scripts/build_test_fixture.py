#!/usr/bin/env python3
"""Launcher: `python scripts/build_test_fixture.py` == `teille-douce fixture`.

The logic lives in `teille_douce/cli/fixture.py`. It moved there because
`scripts/` is not importable from an installed distribution, so the
subcommand could not have shared it — and because this file used to test
`if "--golden" in sys.argv` and let `--help`, along with every typo, fall
through to a function whose first act was `rmtree` of the versioned
fixture.

Kept because CONTRIBUTING.md, the CI and two years of wrapper scripts
invoke it by name.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if __name__ == "__main__":
    from teille_douce.cli import main

    raise SystemExit(main(["fixture", *sys.argv[1:]]))
