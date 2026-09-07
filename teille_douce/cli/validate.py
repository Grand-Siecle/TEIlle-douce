"""`teille-douce validate` — check produced TEI against what it must be.

Three defaults changed on the way in, each because it was the wrong one:

`--odd` is on. It was off, and its absence forced the program to print a
note explaining that five invariants had not been checked. A default that
has to apologise is the wrong default; `--no-odd` cancels it.

`-j` is `auto`. It was 1, and every invocation in the documentation
passes `-j 8`. A default everyone overrides is a false default.

An argument may be a directory, and no argument means the output
directory. The question asked is almost always "check what I have just
written", and the answer used to be a shell glob.
"""

import argparse
import json
import multiprocessing
import sys
from multiprocessing import cpu_count
from pathlib import Path

from lxml import etree

from teille_douce.settings import get_settings
from teille_douce.validation import Missing, available, checks

# Each worker process compiles the schemas once and for all: compiling
# costs a tenth of a second, and doing it per file would be absurd.
_CONTEXT = {}


def _prepare_worker(schema, with_odd):
    """The ONLY place the schemas are compiled.

    The parent used to build `relaxng`, `odd_rng`, `schematron` and the
    Saxon processor as well — and this is what the check reads, in the
    parallel path and in the sequential one, where this runs in the same
    process. The parent's four objects were never read. On a one-megabyte
    tei_all.rng and a Schematron sheet through Saxon, that was not free.
    """
    _CONTEXT["relaxng"] = (etree.RelaxNG(etree.parse(str(schema)))
                           if schema else None)
    _CONTEXT["odd_rng"] = None
    _CONTEXT["schematron"] = None
    if with_odd:
        from teille_douce.validation.schemas import (project_relaxng,
                                                     project_schematron)
        _CONTEXT["odd_rng"] = project_relaxng()
        try:
            _CONTEXT["processor"], _CONTEXT["schematron"] = project_schematron()
        except Missing:
            # The parent has already said so once; a worker saying it
            # again would say it once per process.
            pass


def _check(path):
    """One file's verdict: (path, errors, warnings).

    An unreadable file is a failure of THAT file, not of the batch. Saxon
    raises its own exceptions on documents lxml accepts — an unreachable
    DOCTYPE, too deep a nesting — and letting those through stopped
    everything at the first offending file.
    """
    try:
        errors, warnings = checks.validate(
            path,
            relaxng=_CONTEXT.get("relaxng"),
            schematron=_CONTEXT.get("schematron"),
            odd_rng=_CONTEXT.get("odd_rng"))
        return path, errors, warnings
    except Exception as failure:
        return path, [f"invalid file: {type(failure).__name__}: {failure}"], []


def expand(given, output_dir):
    """The files to check, from what was asked for.

    A directory becomes the `.xml` files directly inside it — output
    directories are flat — and nothing at all becomes the configured
    output directory, which is what the end-of-run report already offers
    as the next thing to type.
    """
    if not given:
        given = [output_dir]
    files = []
    for name in given:
        path = Path(name)
        if path.is_dir():
            files.extend(sorted(path.glob("*.xml")))
        else:
            files.append(path)
    return files


def _jobs(raw):
    """`auto`, or a positive integer, refused by argparse if neither.

    It lost its `type=int` in the move, so `-j eight` reached `int()`
    inside the worker count and came out as a traceback with exit 1 —
    which in this CLI's own contract means "some files failed
    validation", so a wrapper read a typo in its command line as a
    corpus problem. CLAUDE.md: a value typed as a flag is a usage error.
    """
    if raw == "auto":
        return raw
    try:
        asked = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{raw!r} is not auto or a number")
    if asked < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return asked


def _odd(flag):
    """(the user demanded the project schema, the run applies it).

    An explicit `--odd` is a demand, and a schema that is not there
    fails the run; the inherited default is a preference, and a schema
    that is not there narrows the check instead. CLAUDE.md holds that a
    value typed as a flag is a usage error rather than a fallback — and
    this is its converse: a value nobody typed must not stop a
    pipx-installed `teille-douce validate` from running every other
    check it has. `store_true` with `default=True` cannot tell the two
    apart, which is why the default is `None`.
    """
    return flag is True, True if flag is None else flag


def _workers(asked, count):
    """`auto` is as many as there are files, capped by the machine."""
    if asked == "auto":
        return max(1, min(count, cpu_count()))
    return max(1, min(int(asked), count, cpu_count()))


def add_arguments(parser):
    """The surface of `teille-douce validate`."""
    parser.add_argument(
        "files", nargs="*", metavar="FILE|DIR",
        help="TEI files, or directories to take the .xml of "
             "[the output directory]")
    parser.add_argument(
        "--jobs", "-j", default="auto", metavar="auto|N", type=_jobs,
        help="check N files at once; they are independent [auto]")
    # `default=None`, not `True`: what the program does when the
    # schemas are not installed depends on whether the user asked for
    # them or inherited them from the default, and `store_true` with
    # `default=True` cannot tell those apart.
    parser.add_argument(
        "--odd", action="store_true", default=None,
        help="validate against the project schema [on]")
    parser.add_argument(
        "--no-odd", dest="odd", action="store_false",
        help="do not: five invariants live only in the ODD and will go "
             "unchecked")
    parser.add_argument(
        "--schema", metavar="RNG",
        help="a tei_all.rng: adds full TEI RelaxNG validation")
    parser.add_argument(
        "--max-errors", type=int, default=10, metavar="N",
        help="how many errors to print per file; 0 for all [10]")
    parser.add_argument(
        "--json", action="store_true",
        help="the same verdicts, one JSON object")
    return parser


def execute(args):
    asked = args.files or [get_settings().output_dir]
    files = expand(asked, get_settings().output_dir)
    if not files:
        # The directory the user actually named. It always said the
        # configured output directory, so `validate /srv/exports/batch-12`
        # answered about `tei_output` — a path they never mentioned and
        # that may well hold files.
        raise SystemExit("nothing to check: no .xml file in "
                         + ", ".join(str(path) for path in asked))

    demanded, with_odd = _odd(args.odd)
    try:
        schematron, note = available(with_odd)
    except Missing as absent:
        if demanded:
            raise SystemExit(str(absent))
        schematron, with_odd = False, False
        note = f"note: the project schema was not applied — {absent}"
    if note and not args.json:
        print(note)
    if not with_odd and not args.json:
        # Five local invariants live only in the ODD; saying nothing here
        # would let a partial check look like a complete one.
        print("note: prose in <langUsage>, an undivided IIIF idno, a residual "
              "¬ in a <reg>,\n      a GraphicZone with no xml:id and a "
              "template ORCID are stated by the\n      ODD, and were not "
              "checked.")

    workers = _workers(args.jobs, len(files))
    paths = [str(path) for path in files]
    if workers > 1:
        # forkserver, like `sourcedoc/builder.py`, and for the reason it
        # gives: `available()` has just imported saxonche, so the parent
        # carries SaxonC's native runtime and its threads, and forking a
        # multi-threaded process is a deprecation warning today and an
        # error in 3.14. It never came up before because `--odd` was off
        # and `-j` was 1; both are now the default.
        context = multiprocessing.get_context(
            "forkserver" if "forkserver" in multiprocessing.get_all_start_methods()
            else "spawn")
        with context.Pool(workers, initializer=_prepare_worker,
                          initargs=(args.schema, with_odd)) as pool:
            verdicts = pool.map(_check, paths)
    else:
        _prepare_worker(args.schema, with_odd)
        verdicts = [_check(path) for path in paths]

    if args.json:
        # What was applied, named. The notes above are suppressed under
        # `--json` — they are prose — so without this a CI reading the
        # verdicts could not tell a complete check from one narrowed by
        # an absent Schematron, which is the same "a partial check looks
        # complete" the notes exist to prevent.
        json.dump({"applied": _applied(with_odd, schematron, args.schema),
                   "files": [{"path": path, "errors": errors,
                              "warnings": warnings}
                             for path, errors, warnings in verdicts],
                   "errors": sum(len(e) for _, e, _ in verdicts)},
                  sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        _print(verdicts, args.max_errors)

    return 1 if any(errors for _, errors, _ in verdicts) else 0


def _applied(with_odd, schematron, schema):
    """The schemas this invocation actually used, in order of cost."""
    return [name for name, used in (
        ("python invariants", True),
        ("teille-douce.rng", with_odd),
        ("teille-douce.svrl.xsl", with_odd and schematron),
        (schema, bool(schema))) if used]


def _print(verdicts, max_errors):
    for path, errors, warnings in verdicts:
        status = "FAIL" if errors else "ok"
        print(f"[{status}] {path}: {len(errors)} errors, "
              f"{len(warnings)} warnings")
        # `--max-errors 0` means all of them. The caps used to be applied
        # when the errors were COLLECTED, so a file with more than twenty
        # was said to have exactly twenty and no flag could lift it.
        shown = errors if max_errors == 0 else errors[:max_errors]
        for error in shown:
            print(f"   ERROR {error}")
        if len(errors) > len(shown):
            print(f"   … {len(errors) - len(shown)} more "
                  f"(--max-errors 0 for all)")
        shown = warnings if max_errors == 0 else warnings[:max_errors]
        for warning in shown:
            print(f"   warn  {warning}")
        if len(warnings) > len(shown):
            print(f"   … {len(warnings) - len(shown)} more")
