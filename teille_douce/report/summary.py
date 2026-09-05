"""The end-of-run summary, as plain text.

Rendered from the record and nothing else, so the dashboard and the
journal print the same bytes. Two renderers that could disagree would be
two accounts of one run, and the reader would have no way to tell which
one to believe.

No markup, no colour: this same string goes to a terminal and to a log.
"""

from dataclasses import dataclass, field
from pathlib import Path

from .counts import _grouped, render_count
from .record import Block, Code, RunRecord
from .text import clip, pad

MAX_WIDTH = 100

# What each line of the three blocks is called, and what it is measured
# against. Written out rather than derived from the code name so the
# summary reads as English and the codes stay short.
_LABELS = {
    Code.PAGE_UNUSABLE: ("pages unusable", "pages"),
    Code.ALTO_IDS_REPAIRED: ("ALTO ids repaired", "ids"),
    Code.ARCHIVE_CORRUPT: ("archives corrupt", "archives"),
    Code.VOLUME_UNREADABLE: ("volumes unreadable", "volumes"),
    Code.READING_REJECTED: ("readings rejected", "readings"),
    Code.ENTITY_FILTERED: ("entities filtered", "entities"),
    Code.CONTAINER_UNANCHORED: ("containers unanchored", "containers"),
    Code.PHASE_LOST: ("whole phases", "phases"),
    Code.CONTAINER_FAILED: ("containers failed", "containers"),
    Code.DOCUMENT_FAILED: ("documents failed", "documents"),
    Code.BREAKER_SKIPPED: ("containers skipped", "containers"),
    Code.BATCH_FAILED: ("batches failed", "batches"),
}

_BLOCK_ASIDE = {
    Block.SOURCE: "nothing the pipeline could do",
    Block.WITHHELD: "the guards did their job",
    Block.INCIDENT: "this needs a human",
}


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Everything the summary is allowed to know."""

    input_dir: Path
    output_dir: Path
    volumes_total: int
    volumes_written: int
    pages_total: int
    pages_written: int
    record: RunRecord
    elapsed: float
    exit_code: int
    fail_on: str = "never"
    failed_documents: tuple = ()
    failed_archives: tuple = ()
    report_path: Path = None
    # The accounting sentence, composed by the run itself. Taken rather
    # than recomputed: two places deriving the same fraction is two
    # accounts of one run, and the reader has no way to tell which to
    # believe. It is also what a nightly wrapper greps.
    headline: str = ""
    page_loss_failures: tuple = ()
    max_page_loss: float = 100.0


def _duration(seconds):
    """`4h 51m 31s`, not `17491s`. One is a duration, the other a number
    the reader has to divide."""
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _plural(count, noun):
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _rule(width, char="─"):
    return char * min(width, MAX_WIDTH)


# Two columns of blank kept at the right edge: past about a hundred
# characters a block stops being scannable, and the margin is what lets
# the eye find the end of a line. Reserved rather than padded, because the
# same string goes into a log file and trailing spaces there are noise.
_MARGIN = 2


def _columns(left, right, width):
    """A label on the left, an aside on the right, in one line.

    The aside survives; the label is shortened to make room for it. It
    used to be the other way about — the right half was dropped with no
    marker at all — and the right half is the only thing that tells two
    losses sharing a code apart, which is the entire reason the grouping
    key is `(code, detail)`. At eighty columns, the default when stdout
    is not a terminal, two different causes rendered as the same line.
    """
    return pad(left, right, min(width, MAX_WIDTH) - _MARGIN)


def _block_lines(outcome, block, width):
    """One block: its heading, and one line per code that has a number.

    The heading prints on a clean run too. A missing line cannot be told
    from a phase that was never checked, which is the whole reason the
    blocks are named rather than implied.
    """
    lines = [_columns(f"  {block.value}", _BLOCK_ASIDE[block], width)]
    losses = outcome.record.losses(block)
    if not losses:
        lines.append("    nothing")
        return lines

    # Grouped by (code, cause): one line per kind of loss, not one per
    # event — and not one per code either, because two causes sharing a
    # code have two different diagnoses. `2 broken + 90 refused` printed
    # "92 containers — the pipeline raised on these", which is the right
    # count under the wrong cause.
    seen = []
    for code in Code:
        if code.block is not block:
            continue
        for entry in losses:
            if entry.code is code and (code, entry.detail) not in seen:
                seen.append((code, entry.detail))

    for code, detail in seen:
        matching = [entry for entry in losses
                    if entry.code is code and entry.detail == detail]
        label, unit = _LABELS[code]
        if code is Code.PHASE_LOST:
            # Never folded into a container count: a document carrying
            # none of a phase is damage of another kind.
            measured = render_count(len({e.document for e in matching}),
                                    outcome.volumes_total, "")
        else:
            # Per (document, PHASE), not per step: giving each cause its
            # own step is what lets two diagnoses survive, and keying the
            # denominator on it summed one volume's hundred containers
            # once per cause — "92 of 200" for a volume that has a
            # hundred.
            per_phase = {}
            for entry in matching:
                per_phase[(entry.document, entry.step.split(".", 1)[0])] = \
                    entry.total
            measured = render_count(sum(e.count for e in matching),
                                    sum(per_phase.values()), unit)
        shown = detail
        if code.repaired:
            shown = (shown + " (repaired)").strip()
        lines.append(_columns(f"    {label:<22}{measured}", shown, width))
    return lines


def _located_line(located, room):
    """How much of what was lost can be opened.

    Two phrasings, because nothing on this panel is allowed to wrap and
    the long one does not fit at sixty columns. Both print at zero: a
    blind spot has to be a visible line rather than an absence.
    """
    precise, vague = _grouped(located["precise"]), _grouped(located["document_only"])
    spelt = (f"  located   {precise} to a file or an xml:id · "
             f"{vague} to a document only")
    return spelt if len(spelt) <= room else \
        f"  located   {precise} precise · {vague} doc-only"


def _next_steps(outcome):
    """What to type next, chosen by what actually broke.

    A fixed table, not a suggestion engine: a failure state that does not
    say what to type has not finished reporting, and a remedy offered for
    a problem that did not happen teaches the reader to skip the block.
    """
    steps = []
    failed = len(outcome.failed_documents) + len(outcome.failed_archives)
    if failed:
        steps.append(("teille-douce run --retry-failed",
                      f"convert the {_plural(failed, 'volume')} that failed"))
        if outcome.failed_documents:
            first = outcome.failed_documents[0][0]
            steps.append((f"teille-douce run {first} -vv",
                          "reproduce with full logging"))
    if outcome.record.losses(Block.INCIDENT) and outcome.report_path:
        # The file, not a subcommand. `teille-douce report` does not
        # exist yet, and a run that ends by telling the reader to type
        # something that exits 2 with "unrecognized arguments" has spent
        # its last line making itself less trustworthy.
        steps.append((f"cat {outcome.report_path / 'incidents.jsonl'}",
                      "every incident above, one JSON line each"))
    return steps


def _gate_blocks(fail_on):
    """Which blocks a gate level counts.

    Block 2 is never in it: a guard that rejects a hallucination did its
    job, and making that a failure would punish the chain for being
    honest.
    """
    if fail_on == "incident":
        return (Block.INCIDENT,)
    if fail_on == "loss":
        return (Block.SOURCE, Block.INCIDENT)
    return ()


def _verdict(outcome):
    """The last line: what happened, and why it is or is not enough."""
    not_converted = outcome.volumes_total - outcome.volumes_written
    if outcome.exit_code == 130:
        # Not a verdict on the corpus: the run was stopped, and what it
        # had written is on disk.
        return (f"exit 130 — interrupted, {outcome.volumes_written} of "
                f"{outcome.volumes_total} volumes written and kept")
    if outcome.exit_code == 5:
        if outcome.page_loss_failures:
            return (f"exit 5 — everything converted, but "
                    f"{_plural(len(outcome.page_loss_failures), 'volume')} "
                    f"lost too many pages to publish")
        phases = outcome.record.whole_phases_lost()
        if phases:
            # Named, not assumed: with VieuxParler down and PyHellen up,
            # "no enrichment at all" is false and sends the reader to
            # restart the wrong service.
            lost = sorted({entry.step for entry in outcome.record.losses()
                           if entry.code is Code.PHASE_LOST})
            return (f"exit 5 — everything converted, but "
                    f"{_plural(phases, 'volume')} "
                    f"carry no {' and no '.join(lost)} at all")
        return ("exit 5 — everything converted, but the quality gate was "
                "not met")
    if not_converted:
        return (f"exit {outcome.exit_code} — {not_converted} of "
                f"{outcome.volumes_total} volumes not converted")
    return (f"exit {outcome.exit_code} — {outcome.volumes_written} of "
            f"{outcome.volumes_total} volumes converted")


def render_summary(outcome, width=92):
    """The whole summary, as a list of plain lines."""
    room = min(width, MAX_WIDTH)
    lines = [_rule(room, "═")]
    lines.append(_columns("  TEIlle-douce finished",
                          f"{_duration(outcome.elapsed)} · exit {outcome.exit_code}",
                          room))
    lines.append(_rule(room, "═"))
    lines.append("")
    headline = outcome.headline or (
        f"{outcome.volumes_written}/{outcome.volumes_total} documents converted")
    # Never truncated, unlike everything else here. The cap exists so
    # that composed blocks stay scannable; this line is the run's
    # accounting, and a cut number is a wrong number where a long line is
    # only long.
    lines.append(f"  {headline}")
    lines.append(_columns(
        f"      → {outcome.output_dir}",
        f"{render_count(outcome.pages_written, outcome.pages_total, 'pages')} written",
        room))
    lines.append("")

    for block in Block:
        lines.extend(_block_lines(outcome, block, room))
        lines.append("")

    if outcome.failed_documents or outcome.failed_archives:
        lines.append("  failed")
        for name, reason in (*outcome.failed_documents, *outcome.failed_archives):
            # The FAILED token stays: it is what every wrapper this
            # project has greps for, and aligning the name into a column
            # instead would break on the first long one anyway.
            # Clipped in cells and with the control characters out: an
            # httpx reason is two lines, and slicing by characters left
            # the second one loose in the middle of the report — one
            # record for a wrapper reading FAILED lines became two, the
            # second a bare fragment.
            lines.append(clip(f"    FAILED {name}: {reason}", room))
        lines.append("")

    lines.append(_located_line(outcome.record.located(), room))
    lines.append("")

    if outcome.exit_code == 5:
        asked = (f"--max-page-loss {outcome.max_page_loss:g}"
                 if outcome.page_loss_failures
                 else f"--fail-on {outcome.fail_on}")
        lines.append(_columns(f"  quality gate {asked}", "NOT MET", room))
        for document, share in outcome.page_loss_failures:
            lines.append(_columns(f"    {document}",
                                  f"{share:g}% of its pages unusable", room))
        # The block's own lines, not one per record: three volumes losing
        # the same phase for the same reason is one line saying "3 of 27",
        # and repeating the sentence three times turns a diagnosis into
        # noise. Without naming WHICH line tripped, the gate would turn a
        # diagnosis into a guessing game.
        for block in _gate_blocks(outcome.fail_on):
            lines.extend(_block_lines(outcome, block, room)[1:])
        lines.append("")

    steps = _next_steps(outcome)
    if steps:
        lines.append("  next")
        for command, why in steps:
            lines.append(_columns(f"    {command}", why, room))
        lines.append("")

    lines.append(f"  {_verdict(outcome)}")
    return lines
