"""
The options of `teille-douce run`, and what they resolve to.

Declared here rather than in `run.py` so that building the parser does not
import the pipeline: `--help` and `--version` must not configure logging or
name a run log.
"""

import argparse

PHASES = ("enrich", "modernize", "ner")


class _PhaseAction(argparse.Action):
    """Record phase operations in the order they were typed.

    `--phases` replaces the set; each `--X` / `--no-X` applies as a delta
    on top of it. Order matters — `--fast --enrich` means "no service work
    except enrichment" — and argparse does not preserve it on its own, so
    the operations are appended to one list and resolved afterwards.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        operations = list(getattr(namespace, "phase_ops", None) or [])
        if self.dest == "phases_set":
            operations.append(("set", self.const if self.const is not None else values))
        else:
            # nargs=0 gives `values == []` for both spellings, so it is the
            # option string that says whether the phase is added or removed.
            removed = (option_string or "").startswith("--no-")
            operations.append(("remove" if removed else "add", self.dest))
        namespace.phase_ops = operations


def _phase_set(parser, raw):
    """Parse the argument of --phases into a set of phase names."""
    if raw in ("all", ""):
        return set(PHASES)
    if raw == "none":
        return set()
    names = {n.strip() for n in raw.split(",") if n.strip()}
    unknown = names - set(PHASES)
    if unknown:
        parser.error(
            f"unknown phase(s) {', '.join(sorted(unknown))}; "
            f"choose from {', '.join(PHASES)}, or 'all' / 'none'"
        )
    return names


def resolve_phases(parser, operations):
    """Fold the recorded operations into the set of phases to run.

    Returns None when nothing was asked, so that the lower layers — the
    environment, the config file — stay in charge.
    """
    if not operations:
        return None

    enabled = None
    named_in_set = set()
    for kind, value in operations:
        if kind == "set":
            enabled = _phase_set(parser, value)
            named_in_set = set(enabled)
            continue
        if enabled is None:
            enabled = set(PHASES)
        if kind == "add":
            enabled.add(value)
        else:
            if value in named_in_set:
                parser.error(
                    f"--phases names '{value}' and --no-{value} removes it; "
                    "there is no reading of that which says what you meant"
                )
            enabled.discard(value)
    return enabled


def add_run_arguments(parser, *, suppress_defaults=False):
    """Declare `run`'s options on *parser*.

    `suppress_defaults` is what makes the same options safe to declare on
    both the bare parser and the `run` subparser. argparse's subparser
    action parses into a namespace of its own and then copies **all** of it
    over the parent's — defaults included — so an option given before the
    subcommand was silently reset: `teille-douce --skip-existing run`
    dropped the flag and reconverted the whole corpus. With SUPPRESS the
    subparser sets an attribute only when the option is actually given.
    Every option declared on both parsers must use it.
    """
    none = argparse.SUPPRESS if suppress_defaults else None

    selection = parser.add_argument_group("document selection")
    if suppress_defaults:
        selection.add_argument(
            "documents", nargs="*", metavar="DOC", default=[],
                help="volumes to convert: directory name (LIV0044_reconciled), "
                 "internal id (LIV0044), or a glob (LIV003*). Repeatable. "
                 "Default: every volume found in the input directory. A "
                 "selector matching nothing stops the run — a typo must "
                 "not look like an empty corpus.",
        )
    else:
        parser.set_defaults(documents=[])
    selection.add_argument(
        "-x", "--exclude", action="append", metavar="PATTERN", default=none,
        help="drop volumes matching PATTERN, after selection (repeatable)",
    )
    selection.add_argument(
        "--limit", type=int, metavar="N", default=none,
        help="convert at most N of the selected volumes, in order",
    )
    resume = selection.add_mutually_exclusive_group()
    resume.add_argument(
        "--skip-existing", action="store_true",
        default=argparse.SUPPRESS if suppress_defaults else False,
        help="skip volumes whose TEI output already exists "
             "(minimal resume after an interrupted run)",
    )
    resume.add_argument(
        "--force", action="store_true",
        default=argparse.SUPPRESS if suppress_defaults else False,
        help="convert even those (cancels skip_existing from a config file "
             "or the environment)",
    )

    paths = parser.add_argument_group("paths")
    paths.add_argument("-i", "--input", dest="ocr_dir", metavar="DIR",
                       default=none, help="directory of volumes / ZIP archives  [OCR]")
    paths.add_argument("-o", "--output", dest="output_dir", metavar="DIR",
                       default=none, help="directory for the TEI files  [tei_output]")
    paths.add_argument("--entities", dest="entities_dir", metavar="DIR",
                       default=none, help="directory for the NER entity CSVs  [entities]")
    paths.add_argument("--metadata", dest="metadata_csv", metavar="CSV",
                       default=none, help="catalogue of volumes  [metadata_livre.csv]")
    paths.add_argument("--persons", dest="persons_csv", metavar="CSV",
                       default=none, help="catalogue of persons  [metadata_personne.csv]")

    phases = parser.add_argument_group(
        "phases",
        "Parsing, language detection, header and writing always run. The "
        "three annotation phases below are optional and enabled by default.",
    )
    phases.add_argument(
        "--phases", dest="phases_set", action=_PhaseAction, metavar="LIST",
        default=argparse.SUPPRESS,
        help="set the enabled phases absolutely, comma-separated: "
             "enrich, modernize, ner — or 'all' / 'none'",
    )
    phases.add_argument(
        "--fast", dest="phases_set", action=_PhaseAction, nargs=0,
        const="none", default=argparse.SUPPRESS,
        help="alias for --phases none: no service, no model",
    )
    for phase in PHASES:
        phases.add_argument(
            f"--{phase}", f"--no-{phase}", dest=phase, action=_PhaseAction,
            nargs=0, default=argparse.SUPPRESS,
            help=argparse.SUPPRESS if phase != "enrich" else
                 "add or remove one phase from the current set "
                 "(--enrich / --no-enrich, and likewise for modernize, ner)",
        )

    services = parser.add_argument_group("services")
    services.add_argument("--pyhellen", dest="pyhellen_url", metavar="URL",
                          default=none,
                          help="linguistic enrichment base URL  [http://localhost:8000]")
    services.add_argument("--vieuxparler", dest="modernize_url", metavar="URL",
                          default=none,
                          help="modernization base URL  [http://localhost:8011]")
    services.add_argument("--health-timeout", dest="health_timeout", metavar="S",
                          default=none,
                          help="seconds allowed to the pre-run probe  [30]")
    services.add_argument("--no-probe", action="store_true",
                          default=argparse.SUPPRESS if suppress_defaults else False,
                          help="do not probe; assume the services answer")
    services.add_argument("--require-services", action="store_true",
                          default=argparse.SUPPRESS if suppress_defaults else False,
                          help="a phase whose service is down is a fatal error "
                               "before anything is written, instead of a warning "
                               "and a run without that annotation")

    limits = parser.add_argument_group("resources")
    limits.add_argument("-j", "--jobs", dest="max_workers", metavar="N",
                        default=none, help="page-parsing workers  [min(cpu_count, 8)]")
    limits.add_argument("--batch-size", dest="modernize_batch_size", metavar="N",
                        default=none, help="lines per modernization request  [64]")
    limits.add_argument("--concurrency", dest="concurrency", metavar="N",
                        default=none, help="in-flight requests per service  [8]")

    failure = parser.add_argument_group("failure handling")
    failure.add_argument("-n", "--dry-run", action="store_true",
                         default=argparse.SUPPRESS if suppress_defaults else False,
                         help="resolve everything, list the plan, write nothing")
    failure.add_argument("--fail-fast", action="store_true",
                         default=argparse.SUPPRESS if suppress_defaults else False,
                         help="stop at the first volume that fails")
    failure.add_argument("--max-failures", type=int, metavar="N", default=none,
                         help="stop after N failed volumes  [no limit]")

    output = parser.add_argument_group("output")
    verbosity = output.add_mutually_exclusive_group()
    verbosity.add_argument("-v", "--verbose", action="count",
                           default=argparse.SUPPRESS if suppress_defaults else 0,
                           help="-v: per-phase detail; -vv: debug")
    verbosity.add_argument("-q", "--quiet", action="count",
                           default=argparse.SUPPRESS if suppress_defaults else 0,
                           help="-q: summary and errors only")
    output.add_argument("--log-level", dest="log_level", metavar="LEVEL",
                        default=none,
                        help="set the console level outright (DEBUG…CRITICAL)")
    log = output.add_mutually_exclusive_group()
    log.add_argument("--log-file", dest="log_file", metavar="PATH", default=none,
                     help="run log, timestamped per run  [pipeline.log]")
    log.add_argument("--no-log-file", action="store_true",
                     default=argparse.SUPPRESS if suppress_defaults else False,
                     help="disable file logging")

    return parser


def console_level(args):
    """The console log level asked for, or None when nothing was asked."""
    if getattr(args, "log_level", None):
        return args.log_level
    verbose = getattr(args, "verbose", 0) or 0
    quiet = getattr(args, "quiet", 0) or 0
    if verbose:
        return "INFO" if verbose == 1 else "DEBUG"
    if quiet:
        return "ERROR" if quiet == 1 else "CRITICAL"
    return None
