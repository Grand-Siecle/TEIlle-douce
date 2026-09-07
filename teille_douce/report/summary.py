"""The end-of-run summary, as plain text.

Rendered from the record and nothing else, so the dashboard and the
journal print the same bytes. Two renderers that could disagree would be
two accounts of one run, and the reader would have no way to tell which
one to believe.

No markup, no colour: this same string goes to a terminal and to a log.
"""

import shlex
from dataclasses import dataclass, field, replace
from pathlib import Path

from teille_douce import config

from .counts import _grouped, render_count
from .gate import gate_verdict
from .record import Block, Code, RunRecord
from .text import cells, clip, pad, shorten_path

MAX_WIDTH = 100

# Every line fits the width, with four exceptions and one reason: a
# number and a command are each printed whole or not at all. The
# headline and the verdict are the run's accounting; a bare count on a
# line of its own is what is left when the terminal is narrower than the
# figure it is being told; and a `next` command is meant to be pasted
# into a shell. A terminal wraps all four and nothing is lost. Clipped,
# the first three would state a number that is not true and the fourth
# would not run.
#
# There were three in this comment and four in the code, which is how
# `Interrupted: …` — a headline like the other two — came to be outside
# the list the tests check.

# What each line of the three blocks is called, and what it is measured
# against. Written out rather than derived from the code name so the
# summary reads as English and the codes stay short.
_LABELS = {
    Code.PAGE_UNUSABLE: ("pages unusable", "pages"),
    Code.ALTO_IDS_REPAIRED: ("ALTO ids repaired", "ids"),
    Code.ARCHIVE_CORRUPT: ("archives corrupt", "archives"),
    Code.VOLUME_UNREADABLE: ("volumes unreadable", "volumes"),
    Code.RETRY_UNANSWERED: ("retries unanswered", "lines"),
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

    The label survives; the aside is shortened. Never dropped, which is
    what it used to be — silently, and the aside is the only thing that
    tells two losses sharing a code apart, so at eighty columns two
    different causes rendered as the same line.

    And never at the label's expense either: the left half of a block
    entry carries the COUNT, and clipping it turned `11 of 16 pages` into
    `11 of 1…` at sixty-four columns — a wrong number, not a short one.
    """
    return pad(left, right, min(width, MAX_WIDTH) - _MARGIN, keep="left")


def _entry(label, measured, right, width):
    """A block entry, on one line or on two.

    Three parts and a strict order of who gives way: the count never
    does, the cause moves to a line of its own before it is cut, and the
    alignment column goes first of all. Aligning the label to
    twenty-two columns is worth two of the count's characters at ninety
    and none at forty — where padding it left `1 402 of 4 …`, which is
    the `11 of 16 pages → 11 of 1…` case this module exists to make
    impossible, reproduced inside its own remedy.
    """
    room = min(width, MAX_WIDTH) - _MARGIN
    # Padded in cells and never below two spaces. `{label:<22}` counts
    # characters, so a CJK name misaligned — and a name of exactly
    # twenty-two ran straight into its own number: `..._tome_II30.03%`.
    # This corpus's longest volume name is twenty-one characters.
    gap = max(2, 23 - cells(label))
    wide = f"    {label}{' ' * gap}{measured}"
    narrow = f"    {label}  {measured}"
    if cells(wide) <= room:
        left = wide
    elif cells(narrow) <= room:
        left = narrow
    else:
        # The LABEL gives way, never the count. A volume named
        # `BDD_1685_Felibien_Entretiens_sur_les_vies_tome_II` is what
        # this corpus is full of, and clipping the composed line dropped
        # the share it was measured by.
        for_label = room - cells(measured) - 6
        if for_label < 1:
            # Not even the label's first character fits beside it. The
            # count takes a line of its own rather than being clipped —
            # `3 of 1 402 volumes · 1 699 998 containers lo…` is the
            # sentence this module exists to make impossible, and the
            # clamp below used to produce it at every width under
            # fifty-five.
            # The indent is the last thing spent before the number is.
            pad_by = " " * max(0, min(6, room - cells(measured)))
            below = [clip(f"    {label}", room), f"{pad_by}{measured}"]
            if right:
                below.append(clip(f"      {right}", room))
            return below
        left = f"    {clip(label, for_label)}  {measured}"
    if not right:
        # No aside, no padding: the same string goes into a log file, and
        # forty-seven trailing spaces there are noise. `_MARGIN`'s own
        # comment says as much.
        return [left]
    if cells(left) + cells(right) + 2 <= room:
        return [pad(left, right, room, keep="left")]
    return [left, clip("      " + right, room)]


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
            #
            # Both figures, because both are printed elsewhere and a
            # reader has to be able to join them: the panel's incident
            # counter sums what was LOST — containers — and this line
            # counted documents, so one screen said 69 and the other 3
            # with nothing to connect them.
            measured = render_count(len({e.document for e in matching}),
                                    outcome.volumes_total, "volumes")
            amount = sum(e.count for e in matching)
            counted = next((e.unit for e in matching if e.unit), "")
            if amount:
                joined = f" · {_grouped(amount)} {counted} lost".replace(
                    "  ", " ")
                # Dropped whole rather than clipped when the width cannot
                # hold it. It is the supplement that lets a reader join
                # this line to the panel's incident counter; the count in
                # front of it is the block's own measure, and cutting
                # either into `1 699 998 container…` states a number that
                # is not true. Below about fifty columns the supplement
                # is a luxury and the measure is not.
                if cells(measured) + cells(joined) + 8 <= min(width,
                                                              MAX_WIDTH):
                    measured += joined
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
            # The loss's own unit wins where it has one. The label table
            # says `batch_failed` counts batches, and a retry the service
            # never answered is counted in LINES — so the summary
            # relabelled it, which is the "measured in one unit and
            # labelled another" defect the panel had, moved here.
            counted = next((e.unit for e in matching if e.unit), unit)
            measured = render_count(sum(e.count for e in matching),
                                    sum(per_phase.values()), counted)
        shown = detail
        if code.repaired:
            shown = (shown + " (repaired)").strip()
        lines.extend(_entry(label, measured, shown, width))
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
    # Cells, not characters: a six-figure count is wider than `len` says
    # only in the CJK case, but this is the one line that CHOOSES its
    # wording by width, and choosing on the wrong measure is how it came
    # to overflow the very contract it was written to respect.
    if cells(spelt) <= room:
        return spelt
    short = f"  located   {precise} precise · {vague} doc-only"
    if cells(short) <= room:
        return short
    # Both figures print at zero because a blind spot has to be a visible
    # line — and a figure cut in half is not one. `clip` took the second
    # apart mid-number below forty-two columns, so they go on two lines
    # rather than one and a bit.
    # The indent is spent before the figure is: twelve spaces in front of
    # a seven-figure count runs off a thirty-column terminal, and the
    # count is the only part of the line that cannot be shortened.
    # BOTH lines. The adaptive indent landed on the second alone, and
    # the first — which carries the other figure — was left unclipped:
    # the fix on one line of a two-line pair, which is this branch's own
    # recurring shape.
    first, second = f"{precise} precise", f"{vague} doc-only"
    pair = (f"  located   {first}", f"{' ' * 12}{second}")
    if all(cells(line) <= room for line in pair):
        return "\n".join(pair)
    # And a third fold under that, for a terminal narrower than the
    # label and a seven-figure count together. The word goes on its own
    # line before either figure is shortened, because neither can be.
    return "\n".join((
        "  located",
        f"{' ' * max(0, min(4, room - cells(first)))}{first}",
        f"{' ' * max(0, min(4, room - cells(second)))}{second}"))


def _share(value, bar):
    """A percentage written with enough places to show it cleared the bar.

    One decimal turned 30.03 into "30" beside a `--max-page-loss 30` the
    rule says means "more than 30" — the run contradicting its own
    threshold on the line that names it.
    """
    for places in (1, 2, 3, 4):
        shown = round(value, places)
        if shown > bar:
            return f"{shown:g}"
    return f"{value:g}"


def _tripping_lines(outcome, width):
    """The entries the quality gate actually failed on."""
    tripped = set(gate_verdict(outcome.fail_on, outcome.record).tripped_by)
    if not tripped:
        return []
    shown = RunRecord()
    for entry in outcome.record.losses():
        if entry.code in tripped:
            shown.add(entry)
    only_these = replace(outcome, record=shown)
    lines = []
    for block in _gate_blocks(outcome.fail_on):
        if shown.losses(block):
            lines.extend(_block_lines(only_these, block, width)[1:])
    return lines


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
        # `teille-douce report` now exists, so this stopped being a
        # `cat` of the JSONL. The run is named rather than left to
        # "the last one": by the time anyone types this, a nightly may
        # have finished after it. `-o` is carried whenever the output
        # directory is not the default one, because `report` resolves
        # it through the same four layers and would otherwise answer
        # about a directory nobody in this run mentioned — a last line
        # that exits 3 is a last line that teaches distrust.
        # Quoted: `-o tei out` pasted into a shell reads `out` as the
        # DOC positional and exits 3 saying "no run recorded in tei" —
        # the last line that teaches distrust, which is what the `-o` is
        # here to prevent.
        # `Path(...)` on both sides: the default is declared as a string
        # in config.py and arrives here as a Path, and `Path("tei_output")
        # == "tei_output"` is False — which put `-o tei_output` on every
        # default run, offering a flag nobody needs to type.
        where = ("" if Path(outcome.output_dir) == Path(config.DEFAULT_OUTPUT_DIR)
                 else f" -o {shlex.quote(str(outcome.output_dir))}")
        steps.append((f"teille-douce report --run {outcome.report_path.name}"
                      f"{where}",
                      "every incident above, and the log around any of them"))
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
        # Every cause, not the first one found. Both bars can be crossed
        # by the same run, and naming one of them left the other
        # invisible under a block that was printing its lines.
        causes = []
        if outcome.page_loss_failures:
            causes.append(
                f"{_plural(len(outcome.page_loss_failures), 'volume')} "
                f"lost too many pages to publish")
        # Only a cause the LEVEL counts. `--fail-on never --max-page-loss
        # 30` is a supported pair, and a run that also lost a phase named
        # it on the last line — the one wrappers grep — over a gate block
        # that had correctly named the one bar it failed. The same defect
        # as the block blaming a repair, one function later.
        phases = (outcome.record.whole_phases_lost()
                  if gate_verdict(outcome.fail_on, outcome.record).tripped_by
                  else 0)
        if phases:
            # Named, not assumed: with VieuxParler down and PyHellen up,
            # "no enrichment at all" is false and sends the reader to
            # restart the wrong service.
            lost = sorted({entry.step for entry in outcome.record.losses()
                           if entry.code is Code.PHASE_LOST})
            causes.append(f"{_plural(phases, 'volume')} "
                          f"{'carries' if phases == 1 else 'carry'} no "
                          f"{', '.join(lost[:-1]) + ' or ' if len(lost) > 1 else ''}"
                          f"{lost[-1]} at all")
        if not causes:
            causes.append("the quality gate was not met")
        return "exit 5 — everything converted, but " + ", and ".join(causes)
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
    # `keep="right"`, and the path shortened rather than the count: this
    # is the run's page accounting, and it went through the block-entry
    # rule — where the left is the load-bearing half — so an absolute
    # `-o`, which `panel.py` rightly calls the ordinary case, dropped
    # `N of M pages written` off the summary entirely at every width from
    # fifty-six to ninety-five, the default included. Every test used a
    # short relative path, so the suite could not see it.
    written = (f"{render_count(outcome.pages_written, outcome.pages_total, 'pages')}"
               f" written")
    # `- _MARGIN` like every other composed line: this one passed the
    # bare room, so it alone ran to a hundred cells where its neighbours
    # stop at ninety-eight.
    for_path = room - _MARGIN - cells(written) - 9
    beside = shorten_path(outcome.output_dir, for_path)
    # One line only while the path still names its leaf. At forty-seven
    # columns eight cells was "enough", and `…_output` is not the
    # directory anybody typed.
    if for_path >= 8 and Path(outcome.output_dir).name in beside:
        lines.append(pad(f"      → {beside}", written, room - _MARGIN,
                         keep="right"))
    else:
        # Neither `pad` direction helps once the two together will not
        # fit: `keep="right"` clips the COMPOSITE, so below thirty-eight
        # columns it cut the count, and above that it left a path that
        # was one ellipsis. Same remedy as a block entry — the count
        # takes a line of its own.
        lines.append(clip(
            f"      → {shorten_path(outcome.output_dir, room - _MARGIN - 8)}",
            room - _MARGIN))
        # The count alone, and without the word: the arrow line above
        # already says these are what was written, and at thirty columns
        # `1 699 998 of 2 779 999 pages written` does not fit however it
        # is indented — while the eight figures do.
        bare = render_count(outcome.pages_written, outcome.pages_total,
                            "pages")
        lines.append(
            f"{' ' * max(0, min(8, room - _MARGIN - cells(bare)))}{bare}")
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
            lines.append(clip(f"    FAILED {name}: {reason}".rstrip(),
                              room - _MARGIN))
        lines.append("")

    lines.extend(_located_line(outcome.record.located(),
                               room - _MARGIN).split("\n"))
    lines.append("")

    if outcome.exit_code == 5:
        # Both, when both were crossed: naming one made the other
        # invisible while its own lines printed underneath.
        bars = []
        if outcome.page_loss_failures:
            bars.append(f"--max-page-loss {outcome.max_page_loss:g}")
        if gate_verdict(outcome.fail_on, outcome.record).tripped_by:
            bars.append(f"--fail-on {outcome.fail_on}")
        asked = " ".join(bars) or f"--fail-on {outcome.fail_on}"
        # `keep="right"` here, alone in this module: NOT MET is the
        # verdict of the block and the flag list beside it is the
        # elastic half. Clipped to `--max-page-loss 30 NOT…` the line
        # says nothing at all.
        lines.append(pad(f"  quality gate {asked}", "NOT MET",
                         min(room, MAX_WIDTH) - _MARGIN, keep="right"))
        for document, share in outcome.page_loss_failures:
            # `_entry`, not `_columns`: this is the line the share was
            # written for, and a volume name long enough — which is what
            # this corpus has — pushed the share off it entirely, or
            # left `30.…`. The remedy landed on the block entries and
            # not on the block it was written for.
            lines.extend(_entry(
                document, f"{_share(share, outcome.max_page_loss)}%",
                "of its pages unusable", room))
        # The lines that actually tripped it, taken from the gate's own
        # verdict. Re-deriving them from the blocks the level names
        # printed every line of those blocks — including the repaired
        # defects the gate deliberately skips, and a literal "nothing"
        # under NOT MET. A gate that names a repair as its cause is worse
        # than one that names nothing.
        for line in _tripping_lines(outcome, room):
            lines.append(line)
        lines.append("")

    steps = _next_steps(outcome)
    if steps:
        lines.append("  next")
        for command, why in steps:
            # The command is never clipped, on the same reasoning as the
            # headline and the verdict: `teille-douce report --run
            # 20260903-1808…` is not a shorter command, it is one that
            # does not run. A terminal wraps it and it is still
            # copy-pasteable. The reason beside it may be shortened, or
            # move under it.
            if cells(f"    {command}") + cells(why) + 2 <= room - _MARGIN:
                lines.append(_columns(f"    {command}", why, room))
            else:
                lines.append(f"    {command}")
                lines.append(clip(f"      {why}", room - _MARGIN))
        lines.append("")

    # Exempt from the width cap for the same reason as the headline
    # above, and stated here because a reader of the cap will look for
    # the exception: this line is the run's verdict and its accounting,
    # and a cut verdict is a wrong one. A terminal wraps it; nothing is
    # lost. Everything between them is clipped.
    lines.append(f"  {_verdict(outcome)}")
    return lines
