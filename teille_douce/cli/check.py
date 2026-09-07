"""`teille-douce check` — is this installation usable, and what will it cost?

The command that was missing. The only way to know whether the input was
well formed was to convert the whole corpus; the user guide says so of
`--dry-run`, which resolves everything and lists a plan without asking
whether the catalogue matches anything.

It shares the run's analyses rather than repeating them, because a
preflight that disagreed with the run it precedes would be worse than
none. And it writes nothing at all — the acceptance criterion of the six
commands this instalment adds.

Rendering is a pure function of the answer, so every family of attention
is testable without a terminal and without a corpus.
"""

from pathlib import Path

from teille_douce.preflight import inspect
from teille_douce.report.text import cells, clip, pad

MAX_WIDTH = 100
_MARGIN = 2


def _grouped(number):
    return f"{number:,}".replace(",", " ")


def _row(label, value, aside, room):
    """`label  value  …aside`, the aside flush right.

    The aside is what a reader scans for — `writable`, `1 unmatched`,
    `refused` — so it is the half that survives, as it is on the panel.
    """
    left = f"  {label:<11}{value}"
    return pad(left, aside, room, keep="right")


def render(preflight, width=92, strict=False):
    """The whole answer, as a list of lines."""
    room = min(width, MAX_WIDTH) - _MARGIN
    lines = []

    volumes = len(preflight.volumes)
    counted = (f"{_grouped(volumes)} volume{'s' if volumes != 1 else ''} · "
               f"{_grouped(preflight.pages)} pages")
    if preflight.archives:
        counted += (f" · {preflight.archives} "
                    f"archive{'s' if preflight.archives != 1 else ''}")
    lines.append(_row("input", str(preflight.input_dir), counted, room))

    written = "writable" if preflight.output_writable else "NOT WRITABLE"
    if preflight.already_converted:
        written += (f" · {preflight.already_converted} already converted")
    lines.append(_row("output", str(preflight.output_dir), written, room))

    catalogue = preflight.catalogue
    if catalogue.get("rows") is None:
        said = "did not load"
    else:
        said = (f"{_grouped(catalogue['rows'])} rows · "
                f"{catalogue['matched']} matched")
        if catalogue["unmatched"]:
            said += f" · {len(catalogue['unmatched'])} unmatched"
    lines.append(_row("catalogue", _name(catalogue["path"]), said, room))

    persons = preflight.persons
    lines.append(_row("", _name(persons["path"]),
                      f"{_grouped(persons['count'])} persons"
                      if persons["loaded"] else "did not load", room))

    for index, service in enumerate(preflight.services):
        lines.append(_row("services" if index == 0 else "",
                          f"{service.name:<12}{service.endpoint}",
                          service.state
                          + (f" ({service.detail})" if service.detail else ""),
                          room))

    for setting, path, reason, where in preflight.unusable:
        # `where` is the layer that supplied the value, named as the
        # operator set it — `TDOUCE_OCR_DIR`, or `paths.input in
        # ./teille-douce.toml`, and only `-i` when it really was a flag.
        lines.append(_row("unusable", f"{where} {path}", reason, room))

    if preflight.attention:
        lines.append("")
        # Clipped like everything else. It was the one literal in this
        # renderer that skipped it, so the block heading was the only
        # line that overran a very narrow terminal.
        lines.append(clip("  needs attention", room))
        # One column, measured off the widest subject rather than a
        # literal. `{subject:<14}` pads to fourteen and stops, so
        # `LIV0326_v1_reconciled` ran straight into its own detail — the
        # defect `summary._entry` was fixed for, reproduced in a new
        # file the week after. The consequence lines up under the detail,
        # which is why it is measured too.
        column = max(cells(item.subject) for item in preflight.attention)
        column = min(column + 2, max(16, room // 3))
        for item in preflight.attention:
            gap = " " * max(2, column - cells(item.subject))
            # The subject, then what is wrong, then what it costs. A line
            # that says something is wrong without saying what it costs
            # is a line an operator learns to skip.
            lines.append(clip(f"    {item.subject}{gap}{item.detail}", room))
            lines.append(clip(f"    {' ' * column}— {item.consequence}", room))

    lines.append("")
    lines.append(_verdict(preflight, strict, room))
    # Only where `--strict` would change something: on an unusable
    # installation it changes nothing, and a remedy offered for a
    # problem that did not happen teaches the reader to skip the block.
    offered = f"  teille-douce check{_addressed(preflight)} --strict"
    if (preflight.verdict == 1 and not strict
            and offered.strip() not in lines[-1]):
        lines.append(offered)
        lines.append(clip("      fails on these", room))
    return lines


def _name(path):
    return Path(path).name if path else "(none)"


def _addressed(preflight):
    """The `-i`/`-o` that make a `check` mean this one, quoted.

    The footer said `teille-douce check --strict fails on these` with
    nothing else, so pasted from another working directory it answered
    "unusable — nothing to convert" about a corpus it had never been
    shown. `report/summary.py` carries the same rule for the commands it
    offers, and for the same sentence: a last line that exits 3 is a
    last line that teaches distrust.
    """
    import shlex

    from teille_douce import config

    said = ""
    if Path(preflight.input_dir) != Path(config.DEFAULT_OCR_DIR):
        said += f" -i {shlex.quote(str(preflight.input_dir))}"
    if Path(preflight.output_dir) != Path(config.DEFAULT_OUTPUT_DIR):
        said += f" -o {shlex.quote(str(preflight.output_dir))}"
    return said


def _verdict(preflight, strict, room):
    verdict = preflight.verdict
    if verdict == 3:
        # The unreadable input first: it is the more specific cause, and
        # it is also why there are no volumes, so answering "nothing to
        # convert" sent the reader to look at a corpus rather than at a
        # mode.
        why = ("an input this run must read is not readable"
               if preflight.unusable
               else "the output cannot be written"
               if not preflight.output_writable
               else "nothing to convert")
        return clip(f"  unusable — {why}", room)
    if not preflight.attention:
        return clip("  nothing to report", room)
    count = len(preflight.attention)
    said = f"  usable, with {count} thing{'s' if count != 1 else ''} to look at"
    if strict:
        return pad(said, "--strict: these are failures", room, keep="right")
    # Two lines when the command does not fit beside the verdict: it is
    # printed whole or not at all, which is the rule `summary` states
    # for the commands it offers.
    offered = f"teille-douce check{_addressed(preflight)} --strict"
    if cells(said) + cells(offered) + 2 <= room:
        return pad(said, offered, room, keep="right")
    # The verdict is a sentence and IS clipped; the command below it is
    # not, and is emitted separately by `render`.
    return clip(said, room)


def add_arguments(parser):
    """The surface of `teille-douce check`."""
    # The four paths this command reads, named the way `run` names them.
    # They were left to the environment and the config file, on the
    # ground that check is asked about the installation rather than about
    # one invocation — but `teille-douce run -i OCR_test` is the shape
    # the guide teaches, and `teille-douce check -i OCR_test` answering
    # `unrecognized arguments` teaches that the preflight cannot be asked
    # about what the run is about to do. `settings_from` folds any dest
    # in `PASSED_THROUGH` into the flag layer, so declaring them is all
    # it takes.
    parser.add_argument("-i", "--input", dest="ocr_dir", metavar="DIR",
                        default=None,
                        help="directory of volumes / ZIP archives  [OCR]")
    parser.add_argument("-o", "--output", dest="output_dir", metavar="DIR",
                        default=None,
                        help="directory for the TEI files  [tei_output]")
    parser.add_argument("--metadata", dest="metadata_csv", metavar="CSV",
                        default=None,
                        help="catalogue of volumes  [metadata_livre.csv]")
    parser.add_argument("--persons", dest="persons_csv", metavar="CSV",
                        default=None,
                        help="catalogue of persons  [metadata_personne.csv]")
    parser.add_argument(
        "--strict", action="store_true",
        help="treat anything worth looking at as a failure, for CI")
    parser.add_argument(
        "--no-probe", dest="probe", action="store_false", default=True,
        help="do not ask the services anything; for a machine that is offline")
    return parser


def execute(args):
    from teille_douce.settings import get_settings

    from teille_douce.cli.run import console

    answer = inspect(get_settings(), probe=args.probe)
    for line in render(answer, width=console.width, strict=args.strict):
        console.print(line, highlight=False)

    if answer.verdict == 3:
        return 3
    # An imperfect corpus is NOT a failure. The design note settles it in
    # one line — "on seventeenth-century OCR, imperfection is the normal
    # state" — and a preflight that exited non-zero on the normal state
    # would make every nightly red for ever, which is how a check comes
    # to be run with `|| true` and stops being read.
    #
    # `--strict` is what a wrapper asks for when it wants none of them,
    # and 1 rather than 2: 2 is the usage error, and a wrapper reading
    # exit codes would have been told its command line was wrong.
    return 1 if (args.strict and answer.attention) else 0
