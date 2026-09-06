"""Warnings go to the digest instead of to the screen.

Eighty WARNING lines a volume, printed one at a time, push the panel off
the top of the terminal — which is how a run that is repairing a source
defect comes to look like a run that is failing. Folding them absorbs the
flood without a single call site changing, and the DEBUG file handler
still receives every record untouched.
"""

import logging


class DigestHandler(logging.Handler):
    """Feeds a run's digest, and prints nothing."""

    def __init__(self, run, level=logging.WARNING):
        super().__init__(level=level)
        self._run = run

    def emit(self, record):
        # A reporter must not be the thing that ends a four-hour job, so a
        # record that will not format is folded by its raw template rather
        # than dropped: the shape is still the useful part.
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        try:
            self._run.warning(self._run.open_document or "", message)
        except Exception:
            # The guard covered formatting and stopped one line short of
            # the fold, which is where the work is: the digest sums the
            # integers a message carries, and `int()` refuses a digit run
            # past 4 300 characters. Raising here would come out of
            # `logger.warning(...)` in the middle of the pipeline, and the
            # document loop would book the volume as failed — a reporter
            # failing the run it reports on.
            #
            # `handleError` does not help: logging only calls it for what
            # `emit` lets through.
            pass

    def handleError(self, record):
        # logging's default prints a traceback to stderr, straight through
        # the live region it was installed to protect.
        pass
