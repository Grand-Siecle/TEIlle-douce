#!/usr/bin/env python3
"""Compatibility launcher: `python3 main.py` still runs the pipeline.

The repository is now an installable package exposing a `teille-douce`
command. This file is kept because the documentation, the end-to-end test
harness and any wrapper script written over the past two years invoke it
by name.
"""

from teille_douce.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
