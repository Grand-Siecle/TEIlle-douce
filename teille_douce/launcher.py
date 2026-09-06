"""The console script, and the only thing it does before the imports.

`teille-douce` is `[project.scripts]`, and setuptools' generated wrapper
does `from <entry point module> import main` at module level. Naming
`teille_douce.cli` there meant pandas and lxml — a quarter of a second —
were imported outside anything that could catch a Ctrl-C, so an
interrupt in that window came out as a traceback from somewhere inside
pandas over a run that had not started. `main.py`, the compatibility
launcher, guards its own import; the installed command, which is the
form the documentation uses, did not.

This module imports nothing expensive, so the wrapper's import of it is
free, and the guard is in place before anything heavy is read.
"""

import sys


def main(argv=None):
    try:
        from teille_douce.cli import main as run

        return run(argv)
    except KeyboardInterrupt:
        # Nothing has run yet, so there is nothing to report — but a
        # traceback says otherwise. 130 is what a shell reports for
        # SIGINT.
        print("\nInterrupted.", file=sys.stderr)
        return 130
