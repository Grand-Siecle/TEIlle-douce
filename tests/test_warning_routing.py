# -----------------------------------------------------------
# A warning reaches the digest, and the log, and not the panel's face.
#
# Eighty WARNING lines a volume printed one at a time push the panel off
# the screen — which is how a run that is repairing a source defect comes
# to look like a run that is failing. The fold absorbs them without any
# call site changing, and the DEBUG file handler still receives every one.
#
# Run: venv/bin/python -m pytest tests/test_warning_routing.py -q
# -----------------------------------------------------------
import logging

from teille_douce.report.collector import Run
from teille_douce.report.logging_bridge import DigestHandler


def a_run():
    return Run(input_dir="OCR", output_dir="out", volumes=1, pages=1)


def wired(run, name):
    """Through a real logger: the level is enforced by logging itself, so
    a test that called `handle` directly would prove nothing about which
    records actually arrive."""
    logger = logging.getLogger(name)
    logger.handlers = [DigestHandler(run)]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


def test_a_warning_record_lands_in_the_digest():
    run = a_run()

    wired(run, "test.builder").warning(
        "%s: page %s 7 duplicate ALTO id(s) disambiguated", "LIV0038", 41)

    line, = run.digest.lines()
    assert line.occurrences == 1
    assert "duplicate ALTO id(s)" in line.shape


def test_the_document_the_warning_belongs_to_is_taken_from_the_run():
    """The fold counts volumes, and a record does not name one."""
    run = a_run()
    run.document_started("LIV0038", pages=1)

    wired(run, "test.owner").warning("something")

    line, = run.digest.lines()
    assert line.first_document == "LIV0038"


def test_anything_below_a_warning_is_not_folded():
    """INFO and DEBUG are the log's business, not the panel's."""
    run = a_run()

    wired(run, "test.info").info("chatty")

    assert run.digest.lines() == ()


def test_an_error_is_folded_too():
    run = a_run()

    wired(run, "test.error").error("broken")

    assert run.digest.lines()[0].occurrences == 1


def test_a_record_that_cannot_be_formatted_does_not_kill_the_run():
    """A reporter must not be the thing that ends a four-hour job."""
    run = a_run()

    wired(run, "test.bad").warning("%d wrong args", "not a number")

    assert run.digest.lines(), "the record was dropped instead of folded"


def test_a_number_too_long_to_be_a_number_does_not_end_the_run():
    """The guard in `emit` covered formatting and stopped one line short
    of the fold, which is where the work is: the digest sums the integers
    a message carries, and `int()` refuses a digit run past 4 300
    characters. Raising there came out of `logger.warning(...)` in the
    middle of the pipeline, and the document loop booked the volume as
    failed — a reporter failing the run it reports on."""
    import logging

    from teille_douce.report.collector import Run
    from teille_douce.report.logging_bridge import DigestHandler

    run = Run(input_dir="OCR", output_dir="out", volumes=1, pages=1)
    handler = DigestHandler(run)
    record = logging.LogRecord("t", logging.WARNING, __file__, 1,
                               "offset %s in the stream", ("9" * 5000,), None)

    handler.emit(record)

    assert run.digest.lines()
