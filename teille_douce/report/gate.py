"""When a run that converted everything is still not good enough.

Twenty-seven volumes of twenty-seven written, three of them carrying no
annotation at all because a service died at 19:41. Exiting 0 on that is
defensible — it is the default — but an operator has to be able to demand
better, or the only recourse is auditing the files one by one.

"Important" is not a threshold to invent. The three blocks already define
it: block 3 is titled *this needs a human*, and that is the definition.
Block 2 never counts, at any level — a guard that rejects a hallucination
did its job, and making that a failure would punish the chain for being
honest.
"""

from dataclasses import dataclass

from .record import Block

# Only when nothing else failed. A failed volume gives 1, which is the
# more concrete fact and wins; the two are worth separating because the
# remedy differs — 1 is rerun with --retry-failed, 5 is look at what was
# lost and decide whether you accept it.
EXIT_GATE_NOT_MET = 5

LEVELS = ("never", "incident", "loss")

_COUNTED = {
    "never": (),
    "incident": (Block.INCIDENT,),
    "loss": (Block.SOURCE, Block.INCIDENT),
}


@dataclass(frozen=True, slots=True)
class Verdict:
    met: bool
    level: str
    tripped_by: tuple = ()


def gate_verdict(level, record):
    """Whether this run clears the bar the operator set."""
    blocks = _COUNTED[level]
    tripped = []
    for loss in record.losses():
        if loss.block not in blocks:
            continue
        # A defect that was repaired cost nothing. A level that failed on
        # repairs would fail on every run this corpus has ever produced.
        if loss.code.repaired:
            continue
        if loss.code not in tripped:
            tripped.append(loss.code)
    return Verdict(met=not tripped, level=level, tripped_by=tuple(tripped))


def page_loss_failures(pages_per_document, max_page_loss):
    """Volumes that lost more of their pages than the operator allows.

    The one place where "important" really is a ratio and not a category.
    The chain already fails a document whose pages are all unusable; this
    lowers that implicit hundred per cent. A volume with 388 of 400 pages
    illegible is not a degraded conversion, it is a file nobody wants to
    publish.
    """
    failures = []
    for document, (lost, total) in sorted(pages_per_document.items()):
        if not total:
            continue
        share = 100.0 * lost / total
        if share > max_page_loss:
            # The exact share, not a rounded one. The comparison is
            # strict and the display rounded, so 300 of 999 pages —
            # 30.03 % — was printed as "30% of its pages unusable"
            # against a rule documented as "more than 30".
            failures.append((document, share))
    return tuple(failures)
