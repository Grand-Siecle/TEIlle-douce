"""`teille-douce report` — going back to a run that is over.

A run writes a directory per run: the manifest, the incident index, and
the log. Until now the only way back into one was `less` and `jq`, which
is a poor answer to "what did Thursday's run lose, and where". This reads
what `report/store.py` wrote, touches no corpus, and is therefore
instant and cannot damage anything.

The selectors compose — a document, a block, a code, an incident — and
each of them narrows the same list, so `report LIV0044 --code phase_lost`
means what it looks like it means.

What it can answer is bounded by what the run indexed, and that bound is
deliberate: `store.incident` writes block 3 alone, because an index of
everything is an index of nothing. `--limits` says so rather than
letting an exhaustive-looking report be read as exhaustive — and says
the same about the two things this pipeline cannot measure at all.
"""

from dataclasses import dataclass

from teille_douce.report.text import cells, clip, pad, shorten_path

MAX_WIDTH = 100
_MARGIN = 2

# What cannot be counted, and which kind of "cannot" it is. The
# distinction is the point: **impossible** is a fact about the method,
# and **not yet** carries its price so that someone can decide to pay
# it. A limit with no price is an excuse.
LIMITS = (
    ("impossible", "entities below the NER confidence threshold",
     "`detect_entities` passes the threshold into GLiNER's own inference "
     "and drops CamemBERT spans under it before anything counts them, so "
     "what the threshold cost has no denominator to be a fraction of.",
     "run twice at two thresholds and diff the <standOff>."),
    ("impossible", "a modernized reading identical to its original",
     "`apply_modernization` writes no <choice> when the reading equals "
     "the original, so a line VieuxParler declined to modernize cannot "
     "be told from a line that was already modern.",
     "none: the two are the same observation."),
    ("impossible", "pages that never reached the ALTO",
     "every denominator is counted over the input. A page missing from "
     "the scan is not in it, and the book it was scanned from is not "
     "something this pipeline can see.",
     "none from here: it is a question for the catalogue."),
    ("not yet", "blocks 1 and 2, over a past run",
     "`incidents.jsonl` indexes block 3 alone — an index of everything "
     "is an index of nothing — so the defects of the source and what the "
     "guards withheld are in the end-of-run summary and in the log, and "
     "this command can only quote the log for them.",
     "index them too, and every run pays for the largest counter the "
     "corpus produces: repaired ALTO ids, thousands of lines a run."),
    ("not yet", "the instances behind a folded warning",
     "the digest groups repeated warnings by shape and keeps the shape; "
     "each occurrence is in the log and nowhere else.",
     "keep the instances, at the same growth as the line above."),
)


@dataclass(frozen=True, slots=True)
class Selection:
    """What the selectors leave, and what was asked for."""

    run: object
    incidents: tuple
    document: str | None = None
    code: str | None = None
    block: str | None = None
    why: object = None


def _matches_code(incident, asked):
    """A code, or a prefix of one. `alto` reaches `alto_ids_repaired`."""
    return incident.code == asked or incident.code.startswith(asked)


def select(run, document=None, code=None, block=None, why=None):
    """The incidents of *run* that survive every selector given."""
    incidents = run.incidents
    if document:
        incidents = tuple(item for item in incidents
                          if item.document == document)
    if code:
        incidents = tuple(item for item in incidents
                          if _matches_code(item, code))
    if block and block != "incident":
        # Blocks 1 and 2 are not in the index. Answering "nothing" would
        # be read as "the run lost nothing that way", which is the one
        # sentence this package exists to make impossible.
        incidents = ()
    found = None
    if why:
        found = next((item for item in incidents if item.index == why), None)
    return Selection(run=run, incidents=incidents, document=document,
                     code=code, block=block, why=found)


def _head(run, room):
    """Which run this is, and how it ended."""
    started = run.started
    when = started.strftime("%Y-%m-%d %H:%M") if started else run.name
    converted = sum(1 for status in run.documents.values() if status == "ok")
    said = (f"{converted} of {len(run.documents)} converted"
            if run.documents else "no document recorded")
    return pad(f"  run {run.name}   {when}",
               f"{said} · exit {run.exit_code}", room, keep="right")


def render(selection, width=92, context=None):
    """The whole answer, as lines. A pure function of the selection."""
    room = min(width, MAX_WIDTH) - _MARGIN
    run = selection.run
    lines = [_head(run, room), ""]

    for name, reason in run.unreadable:
        # Said first and said plainly: a report of a run that went wrong
        # is exactly where a half-written record turns up.
        lines.append(clip(f"  unreadable  {name}: {reason}", room))
    if run.unreadable:
        lines.append("")

    if selection.why is not None:
        return lines + _why(selection.why, run, room, context)

    if selection.block and selection.block != "incident":
        lines.append(clip(f"  the {selection.block} block is not indexed — "
                          f"teille-douce report --limits", room))
        # `shorten_path`, and the room measured off the sentence rather
        # than guessed: clipping from the right drops the `pipeline.log`
        # that says what the line is pointing at, and a budget short by
        # five cells lets the outer `clip` do it anyway.
        said = "  it is in the end-of-run summary and in "
        lines.append(said + shorten_path(_log_name(run),
                                         room - cells(said)))
        return lines

    if not selection.incidents:
        lines.append(clip("  no incident" + _narrowed(selection), room))
        return lines

    lines.append(clip(f"  {len(selection.incidents)} incident"
                      f"{'s' if len(selection.incidents) != 1 else ''}"
                      + _narrowed(selection), room))
    lines.append("")
    # Measured off the widest of each column, as everywhere else here:
    # `LIV0326_v1_reconciled` is twenty-one and `container_unanchored`
    # twenty, and a literal width loses whichever of them is longer.
    marker = max(cells(item.index) for item in selection.incidents) + 1
    document = min(max(cells(item.document) for item in selection.incidents),
                   max(12, room // 4))
    for item in selection.incidents:
        left = (f"  {item.index:<{marker}} "
                f"{clip(item.document, document):<{document}}  {item.code}")
        lines.append(pad(left, _counted(item), room, keep="left"))
        if item.locator and item.locator != item.document:
            lines.append(clip(f"  {' ' * (marker + document + 2)}"
                              f"{item.kind} {item.locator}", room))
    lines.append("")
    lines.append(clip("  teille-douce report --why "
                      f"{selection.incidents[0].index} for one of them, "
                      "--limits for what is not here", room))
    return lines


def _log_name(run):
    return str(run.log) if run.log else "the run log"


def _narrowed(selection):
    """What the selectors asked for, said back."""
    asked = [what for what in (
        f"in {selection.document}" if selection.document else None,
        f"with code {selection.code}" if selection.code else None) if what]
    return (" " + " ".join(asked)) if asked else ""


def _counted(item):
    """`3 of 148 pages`, or `3`, never a bare integer with a false total."""
    if item.total in (None, 0):
        return str(item.count)
    return f"{item.count} of {item.total}"


def _why(item, run, room, context):
    """One incident, and everything the record holds about it."""
    lines = [clip(f"  {item.index}  {item.code}", room), ""]
    # The locator only when it says more than the document does: a
    # `doc`-kind locator IS the document, and printing it twice under
    # two labels reads as two facts.
    located = item.locator if item.locator != item.document else ""
    for label, value in (("document", item.document),
                         ("step", item.step),
                         (item.kind or "locator", located),
                         ("count", _counted(item)),
                         ("detail", item.detail)):
        if value in (None, ""):
            continue
        lines.append(clip(f"    {label:<10}{value}", room))
    if context:
        lines.append("")
        lines.append(clip(f"    from {shorten_path(_log_name(run), room - 10)}",
                          room))
        for line in context:
            lines.append(clip(f"    | {line}", room))
    elif run.log:
        lines.append("")
        lines.append(clip("    --context for the log around it", room))
    return lines


def log_context(run, item, lines=6):
    """The log around one incident: the lines naming its document.

    The transcript is what the index is an index OF, so this is where a
    reader goes next. It greps rather than seeking to an offset: nothing
    in the record ties a JSONL line to a byte of the log, and inventing
    one would be a fact stored twice in two forms that could diverge.
    """
    if run.log is None:
        return ()
    try:
        transcript = run.log.read_text(encoding="utf-8",
                                       errors="replace").splitlines()
    except OSError:
        return ()
    found = [line.rstrip() for line in transcript
             if item.document and item.document in line]
    # The step narrows it when it can. A document's log is everything
    # that happened to it, and the last six lines of that are whatever
    # ran last, not what this incident is about.
    narrowed = [line for line in found if item.step and item.step in line]
    return tuple((narrowed or found)[-lines:])


def render_runs(runs, width=92):
    """`--runs`: what is still on disk, newest first."""
    room = min(width, MAX_WIDTH) - _MARGIN
    if not runs:
        return [clip("  no run has been recorded here yet", room)]
    lines = [clip(f"  {len(runs)} run{'s' if len(runs) != 1 else ''} kept, "
                  f"newest first", room), ""]
    for run in runs:
        started = run.started
        when = started.strftime("%Y-%m-%d %H:%M") if started else ""
        converted = sum(1 for status in run.documents.values()
                        if status == "ok")
        said = f"{converted}/{len(run.documents)} converted"
        if run.incidents:
            said += f" · {len(run.incidents)} incident"
            said += "s" if len(run.incidents) != 1 else ""
        lines.append(pad(f"  {run.name}  {when}",
                         f"{said} · exit {run.exit_code}", room, keep="left"))
    return lines


def render_limits(width=92):
    """`--limits`: what could not be measured, and which kind of cannot."""
    room = min(width, MAX_WIDTH) - _MARGIN
    lines = [clip("  what this pipeline does not measure, and why", room), ""]
    for kind, subject, why, price in LIMITS:
        lines.append(pad(f"  {subject}", kind, room, keep="left"))
        lines.extend(_wrapped(why, room, "      "))
        lines.extend(_wrapped(f"price: {price}", room, "      "))
        lines.append("")
    lines.append(clip("  impossible is a fact about the method; not yet "
                      "carries its price, so that", room))
    lines.append(clip("  someone can decide to pay it.", room))
    return lines


def _wrapped(text, room, indent):
    """Prose at the width given. `textwrap` measures in characters and
    this file measures in cells, which differ on the ¬ and the em dash
    the corpus is full of."""
    words, line, out = text.split(), indent, []
    for word in words:
        candidate = f"{line} {word}" if line.strip() else f"{indent}{word}"
        if cells(candidate) > room and line.strip():
            out.append(line)
            line = f"{indent}{word}"
        else:
            line = candidate
    if line.strip():
        out.append(line)
    return out


def add_arguments(parser):
    """The surface of `teille-douce report`."""
    parser.add_argument(
        "document", nargs="?", metavar="DOC",
        help="one volume, by the name the run recorded [all of them]")
    parser.add_argument("-o", "--output", dest="output_dir", metavar="DIR",
                        default=None,
                        help="where the runs were written  [tei_output]")
    parser.add_argument("--runs", action="store_true",
                        help="list the runs kept here, newest first")
    parser.add_argument("--run", metavar="STAMP",
                        help="a precise run, by name or by any prefix of it "
                             "[the last one]")
    parser.add_argument("--block", choices=("source", "withheld", "incident"),
                        help="one block of the three")
    parser.add_argument("--code", metavar="CODE",
                        help="one loss code, or any prefix of one")
    parser.add_argument("--why", metavar="In",
                        help="one incident by its number, in full")
    parser.add_argument("--context", action="store_true",
                        help="with --why: the log around it")
    parser.add_argument("--limits", action="store_true",
                        help="what could not be measured, and why")
    parser.add_argument("--json", action="store_true",
                        help="the same lines, as one JSON object")
    return parser


def _chosen(output_dir, stamp):
    """The run directory asked for, or the last one. None if there is none."""
    from teille_douce.report.store import RunStore

    kept = RunStore.kept(output_dir)
    if not kept:
        return None
    if not stamp:
        return kept[-1]
    matching = [path for path in kept if path.name.startswith(stamp)]
    if not matching:
        return None
    # The newest, when a prefix names several: a stamp without its pid
    # matches every run started in that second, and the last is the one
    # whose record the others were written beside.
    return matching[-1]


def _as_json(selection, context=None):
    run = selection.run
    return {
        "run": run.name,
        "path": str(run.path),
        "exit_code": run.exit_code,
        # The prose says "not indexed" in a sentence; JSON has no
        # sentence to say it in, so it says it as a field. Without this
        # an empty `incidents` under `--block source` is read as "the run
        # lost nothing that way" — the same misreading the prose branch
        # exists to prevent, in the output a wrapper actually parses.
        "block": selection.block,
        "indexed": selection.block in (None, "incident"),
        "documents": dict(run.documents),
        "unreadable": [{"file": name, "reason": reason}
                       for name, reason in run.unreadable],
        "incidents": [{"index": item.index, "code": item.code,
                       "document": item.document, "step": item.step,
                       "locator": item.locator, "kind": item.kind,
                       "count": item.count, "total": item.total,
                       "detail": item.detail}
                      for item in selection.incidents],
        "context": list(context or ()),
    }


def execute(args):
    import json
    import sys

    from teille_douce.cli.run import console
    from teille_douce.report.store import read_run
    from teille_douce.settings import get_settings

    width = console.width
    if args.limits:
        for line in render_limits(width):
            console.print(line, highlight=False)
        return 0

    output_dir = get_settings().output_dir
    if args.runs:
        from teille_douce.report.store import RunStore

        runs = [read_run(path) for path in reversed(RunStore.kept(output_dir))]
        for line in render_runs(runs, width):
            console.print(line, highlight=False)
        # Nothing recorded is 3, as it is below: the question was about a
        # run, and there is none to answer about.
        return 0 if runs else 3

    path = _chosen(output_dir, args.run)
    if path is None:
        asked = f" matching {args.run!r}" if args.run else ""
        raise SystemExit(
            f"no run recorded in {output_dir}{asked} — a run writes its "
            f"record under .teille-douce/runs/ as it finishes")

    run = read_run(path)
    selection = select(run, document=args.document, code=args.code,
                       block=args.block, why=args.why)
    if args.why and selection.why is None:
        raise SystemExit(f"no incident {args.why!r} in {run.name} — "
                         f"teille-douce report lists them by number")

    context = (log_context(run, selection.why)
               if args.context and selection.why is not None else None)
    if args.json:
        json.dump(_as_json(selection, context), sys.stdout,
                  ensure_ascii=False, indent=2)
        print()
    else:
        for line in render(selection, width, context=context):
            console.print(line, highlight=False)

    # 1 when block 3 is not empty, whatever the selectors left: the
    # question a wrapper asks this command is "did that run need a
    # human", and narrowing the view must not change the answer.
    return 1 if run.incidents else 0
