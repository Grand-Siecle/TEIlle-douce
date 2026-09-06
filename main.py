#!/usr/bin/env python3
"""Compatibility launcher: `python3 main.py` still runs the pipeline.

The repository is now an installable package exposing a `teille-douce`
command. This file is kept because the documentation, the end-to-end test
harness and any wrapper script written over the past two years invoke it
by name.
"""

if __name__ == "__main__":
    try:
        # Imported inside the guard: pulling in pandas and lxml is a
        # quarter of a second, and a Ctrl-C in it used to come out as a
        # traceback from somewhere inside pandas. Nothing has run yet, so
        # there is nothing to report — but a traceback says otherwise.
        from teille_douce.cli import main

        raise SystemExit(main())
    except KeyboardInterrupt:
        import sys

        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
