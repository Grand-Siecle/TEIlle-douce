#!/usr/bin/env python3
"""Launcher: `python scripts/validate_tei.py` == `teille-douce validate`.

The logic lives in `teille_douce/validation/`. It moved there because
`scripts/` is not importable from an installed distribution, so the
subcommand could not have shared it.

Kept because CLAUDE.md, `docs/schema.md` and two years of habit invoke it
by name. Note that the defaults changed with the move: `--odd` is now on,
and `-j` is `auto`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if __name__ == "__main__":
    from teille_douce.cli import main

    raise SystemExit(main(["validate", *sys.argv[1:]]))
