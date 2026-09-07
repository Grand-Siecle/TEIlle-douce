"""The exit codes, and the one way to leave with one.

`raise SystemExit("a message")` prints the message and exits **1**. In
this CLI 1 means "some volumes failed", so every refusal written that
way told a wrapper that the corpus was at fault — a missing run record,
a directory with no XML in it, two flags that contradict each other.
`info._named` got it right and its comment names the trap; six other
sites had fallen into it, which is what a shared helper is for.

    0    everything asked for succeeded
    1    partial failure: some volumes failed, others did not
    2    usage error, on the command line
    3    misconfigured, and nothing ran
    4    total failure: everything that ran failed
    5    the quality gate was not met
    130  interrupted

The message goes to stderr, as argparse's own does, so that `--json` on
stdout stays parseable when the command refuses.
"""

import sys

USAGE = 2
MISCONFIGURED = 3


def refuse(message, code=MISCONFIGURED, program="teille-douce"):
    """Say why, on stderr, and leave with *code*. Never returns."""
    print(f"{program}: {message}", file=sys.stderr)
    raise SystemExit(code)
