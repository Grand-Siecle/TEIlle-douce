#!/usr/bin/env python3
"""Launcher: `python scripts/build_odd.py` == `teille-douce odd`.

The logic lives in `teille_douce/odd/`. It moved there because `scripts/`
is not importable from an installed distribution, so the subcommand could
not have shared it.

Kept because CLAUDE.md, `schema/README.md`, CONTRIBUTING.md and the CI
invoke it by name.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if __name__ == "__main__":
    from teille_douce.cli import main

    # `--check` and `--refresh` keep their spelling; the action is
    # implied, as it was when this file was the whole command.
    asked = ["odd"]
    asked.append("check" if "--check" in sys.argv else "build")
    asked += [flag for flag in sys.argv[1:] if flag != "--check"]
    raise SystemExit(main(asked))
