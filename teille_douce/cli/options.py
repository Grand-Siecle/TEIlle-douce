"""
The options of `teille-douce run`, and what they resolve to.

Declared here rather than in `run.py` so that building the parser does not
import the pipeline: `--help` and `--version` must not configure logging or
name a run log.
"""

import argparse

import teille_douce

PHASES = ("enrich", "modernize", "ner")

# A usage error must name the option the user typed, not the setting it
# feeds: reporting `--max-workers` for `-j` sends the reader looking for a
# flag that does not exist.
_OPTION_FOR_SETTING = {
    "ocr_dir": "-i/--input",
    "output_dir": "-o/--output",
    "entities_dir": "--entities",
    "metadata_csv": "--metadata",
    "persons_csv": "--persons",
    "pyhellen_url": "--pyhellen",
    "modernize_url": "--vieuxparler",
    "health_timeout": "--health-timeout",
    "max_workers": "-j/--jobs",
    "modernize_batch_size": "--batch-size",
    "modernize_concurrency": "--concurrency",
    "pyhellen_concurrency": "--concurrency",
    "ner_device": "--device",
    "log_file": "--log-file",
    "log_level": "--log-level",
}

# The settings whose flag carries their value straight through, with no
# arithmetic on the way: argparse's dest IS the setting name, so the caller
# copies them across by name. Derived from the map above rather than
# written out again — a second list is how `--device` was declared,
# documented, shown in --help, and read by nothing.
#   - concurrency feeds two settings from one flag
#   - log_level is decided by the -v/-q floor, not copied
PASSED_THROUGH = tuple(
    name for name in _OPTION_FOR_SETTING
    if name not in ("modernize_concurrency", "pyhellen_concurrency",
                    "log_level")
)


def _at_least_one(raw):
    """A count starts at one.

    `--max-failures 0` stopped the run after the first document having
    failed nothing, and `--limit -1` — a plausible "no limit" idiom —
    silently dropped the last volume and exited 0.
    """
    try:
        value = int(raw)
    except ValueError:
        # Without this, argparse falls back to the callable's __name__ and
        # says "invalid _at_least_one value".
        raise argparse.ArgumentTypeError("must be a whole number, at least 1")
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def option_for(setting):
    """The option that feeds *setting*, for an error message."""
    return _OPTION_FOR_SETTING.get(setting, "--" + setting.replace("_", "-"))


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
    """Parse the argument of --phases into (set, names typed literally).

    The literal names matter for the contradiction check below: `all` and
    `none` are aliases, and naming no phase they cannot contradict a later
    `--no-X`. `--phases all --no-ner` is the same idiom as `--fast --enrich`.
    """
    raw = raw.strip()
    if raw == "all":
        return set(PHASES), set()
    if not raw:
        # Set but empty. For an environment variable the rule is "keep the
        # default and warn"; for something typed on the command line it is
        # a usage error, like any other unusable flag value. Silently
        # meaning "all" turned every phase back on for a wrapper doing
        # `--phases "$PHASES"` with PHASES unset.
        parser.error("--phases was given an empty list")
    if raw == "none":
        return set(), set()
    names = {n.strip() for n in raw.split(",") if n.strip()}
    if not names:
        # `--phases "$A,$B"` with both variables unset: a comma-only value
        # meant "none" in silence and disabled every phase for a corpus.
        parser.error(f"--phases was given no phase name ({raw!r})")
    unknown = names - set(PHASES)
    if unknown:
        parser.error(
            f"unknown phase(s) {', '.join(sorted(unknown))}; "
            f"choose from {', '.join(PHASES)}, or 'all' / 'none'"
        )
    return names, set(names)


def resolve_phases(parser, operations):
    """Fold the recorded operations into {phase: enabled}.

    Only phases the command line actually decided appear in the result:
    a lone `--no-modernize` says nothing about enrichment or NER, so it
    must leave them to the environment, the config file and the default.
    Seeding from all three phases switched them back on — precedence is
    per setting, not per layer.

    Returns an empty mapping when nothing was asked.
    """
    decided = {}
    typed_in_set = set()
    for kind, value in operations or []:
        if kind == "set":
            enabled, typed_in_set = _phase_set(parser, value)
            decided = {phase: phase in enabled for phase in PHASES}
            continue
        if kind == "remove" and value in typed_in_set:
            parser.error(
                f"--phases names '{value}' and --no-{value} removes it; "
                "there is no reading of that which says what you meant"
            )
        decided[value] = (kind == "add")
    return decided


def add_run_arguments(parser):
    """Declare `run`'s options on *parser*.

    They live on the `run` subparser alone. Declaring them on the bare
    parser as well — so that the historical invocation kept working — was a
    trap: argparse's subparser action copies its whole namespace over the
    parent's, so an option given on both sides replaced rather than merged.
    `teille-douce.cli.app.normalise` puts the subcommand first instead.
    """
    none = None

    # Also on the subcommand: the options moved here, so `teille-douce
    # --skip-existing --version` would otherwise be an unrecognised
    # argument, where any ordering used to work.
    parser.add_argument(
        "-V", "--version", action="version",
        version=f"teille-douce {teille_douce.__version__}",
    )

    selection = parser.add_argument_group("document selection")
    selection.add_argument(
        "documents", nargs="*", metavar="DOC", default=[],
        help="volumes to convert: directory name (LIV0044_reconciled), "
             "internal id (LIV0044), or a glob (LIV003*). Repeatable. "
             "Default: every volume found in the input directory. A "
             "selector matching nothing stops the run — a typo must not "
             "look like an empty corpus.",
    )
    selection.add_argument(
        "-x", "--exclude", action="append", metavar="PATTERN", default=none,
        help="drop volumes matching PATTERN, after selection (repeatable)",
    )
    selection.add_argument(
        "--limit", type=_at_least_one, metavar="N", default=none,
        help="convert at most N of the selected volumes, in order",
    )
    resume = selection.add_mutually_exclusive_group()
    resume.add_argument(
        "--skip-existing", action="store_true",
        default=False,
        help="skip volumes whose TEI output already exists "
             "(minimal resume after an interrupted run)",
    )
    resume.add_argument(
        "--force", action="store_true",
        default=False,
        help="convert even those (cancels skip_existing from a config file "
             "or the environment)",
    )

    configuration = parser.add_argument_group("configuration")
    where = configuration.add_mutually_exclusive_group()
    where.add_argument("--config", dest="config_file", metavar="PATH",
                       default=none,
                       help="use this config file instead of discovering one")
    where.add_argument("--no-config", action="store_true",
                       default=False,
                       help="skip config-file discovery entirely")

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
                          default=False,
                          help="do not probe; assume the services answer")
    services.add_argument("--require-services", action="store_true",
                          default=False,
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
    limits.add_argument("--device", dest="ner_device", metavar="DEV",
                        default=none,
                        help="where the NER models run: auto, cpu, cuda, "
                             "cuda:1, mps  [auto]")

    failure = parser.add_argument_group("failure handling")
    failure.add_argument("-n", "--dry-run", action="store_true",
                         default=False,
                         help="resolve everything, list the plan, write nothing")
    failure.add_argument("--fail-fast", action="store_true",
                         default=False,
                         help="stop at the first volume that fails")
    failure.add_argument("--max-failures", type=_at_least_one, metavar="N",
                         default=none,
                         help="stop after N failed volumes  [no limit]")

    output = parser.add_argument_group("output")
    verbosity = output.add_mutually_exclusive_group()
    verbosity.add_argument("-v", "--verbose", action="count",
                           default=0,
                           help="-v: per-phase detail; -vv: debug")
    verbosity.add_argument("-q", "--quiet", action="count",
                           default=0,
                           help="-q: summary and errors only")
    verbosity.add_argument("--log-level", dest="log_level", metavar="LEVEL",
                           default=none,
                           help="set the console level outright (DEBUG…CRITICAL)")
    log = output.add_mutually_exclusive_group()
    log.add_argument("--log-file", dest="log_file", metavar="PATH", default=none,
                     help="run log, timestamped per run  [pipeline.log]")
    log.add_argument("--no-log-file", action="store_true",
                     default=False,
                     help="disable file logging")

    return parser


def asks_to_be_quieter(args):
    """Whether the command line asked for LESS console output.

    `debug` puts the console at DEBUG; only a request for less contradicts
    it. `-v` asks for more, so a debug setting that already gives more is
    not overridden by it — that would make -v less verbose than no flag.
    """
    if getattr(args, "quiet", 0):
        return True
    level = getattr(args, "log_level", None)
    return bool(level) and level.strip().upper() != "DEBUG"


def console_level(args):
    """The console log level asked for, or None when nothing was asked."""
    level = getattr(args, "log_level", None)
    if level is not None:
        # Not `if level:` — an empty string must reach the converter and be
        # refused, like every other flag. `--log-level "$LEVEL"` with LEVEL
        # unset is the same wrapper idiom `--phases` already guards against.
        return level
    verbose = getattr(args, "verbose", 0) or 0
    quiet = getattr(args, "quiet", 0) or 0
    if verbose:
        return "INFO" if verbose == 1 else "DEBUG"
    if quiet:
        # -q reduces chatter; the console handler stays at WARNING so a
        # logger-emitted warning still reaches the operator. -qq is the
        # deliberate spelling for "I accept losing those too".
        return "WARNING" if quiet == 1 else "ERROR"
    return None
