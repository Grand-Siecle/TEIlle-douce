# -----------------------------------------------------------
# TEIlle-douce - Main workflow script
# Converts ALTO XML files to TEI format with SegmOnto taxonomy
# -----------------------------------------------------------
"""
TEIlle-douce main workflow.

This script orchestrates the conversion of ALTO XML files to TEI format.
Configuration is imported from config.py.

Usage:
    teille-douce run
"""

import logging
import os
import pathlib
import re
import warnings
from fnmatch import fnmatch
import shutil
import signal
import sys
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from time import perf_counter
from zipfile import BadZipFile, ZipFile

from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.console import Console
from rich.markup import escape

# Import configuration
from teille_douce.config import APP_VERSIONS, IIIF_URI, RESPONSIBILITY
from teille_douce.report.collector import Run as ReportRun
from teille_douce.sourcedoc.builder import warm_up
from teille_douce.report.counts import PhaseState
from teille_douce.report.dashboard import Dashboard
from teille_douce.report.logging_bridge import DigestHandler
from teille_douce.report.panel import Service
from teille_douce.report.gate import (EXIT_GATE_NOT_MET, gate_verdict,
                                      page_loss_failures)
from teille_douce.report.select import UI, choose_ui
from teille_douce.report.store import RunStore
from teille_douce.report.record import Code, Locator, Loss
from teille_douce.report.summary import render_summary
from teille_douce.settings import get_settings, use_settings

# Configure logging
#
# In a function, not in this module's body: the log file's name and the
# console level are settings, and settings are resolved after the arguments
# are parsed. Configuring at import time froze both before the command line
# had been read — and made `teille-douce --help` name a run log.
_log_format = "%(asctime)s %(name)s [%(levelname)s] %(message)s"
_log_datefmt = "%Y-%m-%d %H:%M:%S"

# Log file of the run in progress, or None. Read by the end-of-run summary.
RUN_LOG_FILE = None


def _run_log_path(base_path, now):
    """
    Per-run, timestamped log file derived from the log_file setting
    (audit 2.10).

    mode="w" on a fixed name destroyed the previous run's log: a failed
    nightly run relaunched in the morning became undiagnosable. Each run
    now writes its own file (e.g. pipeline_20260828_093000_4711.log).

    The process id is in the name because the timestamp alone is not
    unique: a launcher firing several volumes at once starts them within
    the same second, and mode="w" then reinstated exactly the failure this
    function exists to prevent, one run truncating another's log. Testing
    the name for existence first would not help — the handler is lazy, so
    neither file exists at the moment both runs choose their name.
    """
    stamp = f"{now:%Y%m%d_%H%M%S}_{os.getpid()}"
    return base_path.with_name(f"{base_path.stem}_{stamp}{base_path.suffix}")


def _log_path_is_usable(path):
    """Whether a log file could be written there, without writing anything.

    Walks up to the nearest existing ancestor: it has to be a directory we
    may write into. Everything below it will be created on the first
    record.
    """
    for ancestor in (path.parent, *path.parent.parents):
        if ancestor.exists():
            return ancestor.is_dir() and os.access(ancestor, os.W_OK)
    return False


class _LazyFileHandler(logging.FileHandler):
    """A file handler that creates its directory only when it writes.

    Installing the handler early is what lets the records emitted during
    setup — a corrupt archive, a metadata CSV that is not there — reach the
    run log the summary points at. Creating the directory early is what
    left one behind after a run refused to start. `delay=True` plus this
    override gives both.
    """

    def _open(self):
        pathlib.Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
        return super()._open()


def configure_logging(settings, now=None, quiet=False, write=True,
                      level_asked=False, log_path=None):
    """Install this run's handlers. Returns the run's log file, or None.

    `log_path` puts the log somewhere the settings do not know about —
    the run's own directory, beside the index of what it lost. Passed in
    rather than derived here, because only the caller knows whether the
    operator named a file of their own.
    """
    global RUN_LOG_FILE, _QUIET

    # -q asks for a quiet console; `debug` gates the diagnostics that also
    # go to the run log. They answer different questions, so a wrapper that
    # sets TDOUCE_DEBUG must not lose the -q it just typed.
    _QUIET = quiet

    handlers = []
    try:
        if log_path is not None:
            # Already a full name: it lives in a directory of its own, so
            # nothing has to be appended to keep two runs apart.
            RUN_LOG_FILE = Path(log_path)
        else:
            RUN_LOG_FILE = (
                _run_log_path(Path(settings.log_file), now or datetime.now())
                if settings.log_file else None
            )
    except ValueError as reason:
        # `--log-file .` and `--log-file /` have no name to derive from.
        warnings.warn(
            f"cannot use {settings.log_file} as a log file: {reason} — "
            "continuing without file logging",
            RuntimeWarning, stacklevel=2,
        )
        RUN_LOG_FILE = None
    if RUN_LOG_FILE and not _log_path_is_usable(RUN_LOG_FILE):
        # Checked without creating anything: the handler makes the
        # directory at its first record, and an OSError there would be
        # swallowed by logging as "--- Logging error ---" on every record.
        warnings.warn(
            f"cannot write the run log to {RUN_LOG_FILE} — continuing "
            "without file logging",
            RuntimeWarning, stacklevel=2,
        )
        RUN_LOG_FILE = None
    if RUN_LOG_FILE and not write:
        # --dry-run writes nothing, and a log directory is a write.
        RUN_LOG_FILE = None
    if RUN_LOG_FILE:
        # delay=True plus _LazyFileHandler: the file and its directory are
        # created at the first record, so a run that refuses to start
        # leaves neither behind.
        file_handler = _LazyFileHandler(
            str(RUN_LOG_FILE), mode="w", encoding="utf-8", delay=True
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_log_format, datefmt=_log_datefmt))
        handlers.append(file_handler)

    console_handler = logging.StreamHandler()
    # `debug` lowers the console to DEBUG only when nothing asked for a
    # level: -q, -qq and --log-level are typed just now, and installing a
    # DEBUG handler over them handed a fully verbose console to a run that
    # asked for quiet.
    level = getattr(logging, settings.log_level)
    if settings.debug and not level_asked:
        level = logging.DEBUG
    console_handler.setLevel(level)
    console_handler.setFormatter(
        logging.Formatter("%(name)s [%(levelname)s] %(message)s")
    )
    handlers.append(console_handler)

    logging.basicConfig(level=logging.DEBUG, handlers=handlers, force=True)

    # Third-party HTTP libs are extremely chatty at DEBUG and were filling
    # the run log with megabytes of connection traces.
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return RUN_LOG_FILE


# Import modules
from teille_douce import TEI
from teille_douce.body import link_notes_to_lines
from teille_douce.enrichment.ner_models import missing_ner_dependencies
from teille_douce.teiheader import build_header
from teille_douce.metadata import (load_metadata,
                          find_metadata_row,
                          build_metadata_dict,
                          override_teiheader_from_csv,
                          load_person_database,
                          select_manifest)
from teille_douce.utils import write_xml
from teille_douce.utils.files import (canonical_document_id,
                                      parse_document_id)


console = Console()

# Console verbosity, set by configure_logging(). `console.print` is not
# routed through logging, so a level alone silenced nothing: `-q` printed
# every per-document line and both progress bars, and was indistinguishable
# from a run without it.
_QUIET = False


def _set_quiet(quiet):
    """Silence the per-document chatter, or let it back.

    The panel uses this: everything `say` would print is already on it,
    and a print inside a Live region pushes a copy of the frame into the
    scrollback on every line.
    """
    global _QUIET
    _QUIET = bool(quiet)


def say(message):
    """Print an informational line, unless the run was asked to be quiet.

    Warnings, failures and the end-of-run summary are printed directly and
    are never suppressed: -q asks for less chatter, not for less truth.
    """
    if not _QUIET:
        console.print(message)


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def build_config():
    """
    Build the pipeline configuration dictionary.

    Returns:
        dict: Configuration dictionary for the pipeline.
    """
    # "data" and "offline" used to sit here too; nothing ever read them
    # (audit 4.6).
    return {
        "iiifURI": IIIF_URI,
        "responsibility": RESPONSIBILITY,
    }


def _extract_archive(zip_path, target):
    """
    Extract an archive into a temporary sibling directory, then rename it
    atomically to `target` (audit 2.2).

    Extracting straight into `target` meant an interrupted run left a
    partial directory that the `target.exists()` guard treated as fully
    extracted on the next run — a silently truncated document. A corrupt
    member makes extractall raise (BadZipFile/zlib.error), and the partial
    temporary directory is removed before the error propagates, so no
    second testzip() pass over the whole archive is needed.
    """
    tmp_target = target.with_name(target.name + ".extracting")
    if tmp_target.exists():
        # Leftover of a previously interrupted extraction
        shutil.rmtree(tmp_target)
    try:
        # mkdir up front: extractall on an empty archive creates nothing,
        # and the rename below must find a directory even in that case.
        tmp_target.mkdir(parents=True)
        with ZipFile(zip_path) as zf:
            zf.extractall(tmp_target)
        tmp_target.rename(target)
    except BaseException:
        shutil.rmtree(tmp_target, ignore_errors=True)
        raise


def _is_readable(directory):
    """Whether this process may actually list *directory*.

    `rglob` swallows a permission error and yields nothing, so an
    unreadable volume is indistinguishable from an empty one by its
    contents alone — and calling it empty sends the operator to repack a
    volume whose only problem is its mode.
    """
    try:
        for _ in directory.iterdir():
            break
    except OSError:
        return False
    return True


def entity_snapshot(entity_dir):
    """What a document's entity directory already holds, before NER writes.

    Answers (existing_files, created_by_this_run, failure_or_None).

    The third value is what makes this a function. An unreadable directory
    used to be announced -- "its entity files will be left alone if this
    document fails" -- and then treated as an empty one, which says the
    opposite: an empty before-set means this run wrote everything in
    there, and the cleanup after a failed document reads that as a licence
    to unlink the lot. A caller that gets a failure here has to leave the
    directory alone.
    """
    try:
        if not entity_dir.exists():
            return set(), True, None
        # iterdir() and not rglob(): rglob swallows the error and yields
        # nothing, so a directory full of a previous run's entity files
        # that this process cannot read came back as an EMPTY before-set —
        # which reads as "this run wrote all of it", the licence to delete.
        # Asking for one entry is enough to find out, and cheap.
        for _ in entity_dir.iterdir():
            break
        return set(entity_dir.rglob("*")), False, None
    except OSError as reason:
        return set(), False, reason


def may_remove_entity_files(stray, wrote_entities, snapshot_failed,
                           tei_exists):
    """Whether a failed document's entity directory may be touched at all.

    Four conditions, and the order is load-bearing. `stray.is_dir()` is
    last because it raises PermissionError on a directory whose parent is
    not traversable -- exactly the case `snapshot_failed` describes -- and
    that exception would escape the per-document handler and end a run
    that one broken volume is not allowed to end.

    A predicate rather than an inline `if` because each of these has
    already been the whole bug once: without `wrote_entities`, a --fast
    run deleted files it never wrote; without `snapshot_failed`, a
    directory this process could not even read counted as one this run
    had written whole.
    """
    return (wrote_entities
            and not snapshot_failed
            and not tei_exists
            and stray.is_dir())


def expand_archives(ocr_dir, extract=True, wanted=None, keep=None):
    """
    Extract ZIP archives in the OCR directory.

    Automatically extracts any ZIP files found in the OCR directory
    into subdirectories with the same name as the archive. A corrupt
    archive is skipped and reported instead of killing the run
    (audit 2.2).

    Args:
        ocr_dir (Path): Path to the OCR directory.
        extract (bool): unpack, or merely report what would be unpacked.
        wanted (callable): name -> True when this archive is worth
            unpacking. Unpacking every archive to convert one named volume
            was the same waste the input and output guards were tightened
            against.
        keep (callable): name -> True when an already-extracted directory
            is in scope. Narrower than `wanted` on purpose: a volume the
            resume will skip still has to reach select_documents, which is
            the single place that counts a skip.

    Returns:
        tuple: (ready_dirs, failed_archives)
            - ready_dirs: sorted list of directories ready for processing
            - failed_archives: list of (archive_name, reason) tuples for
              archives that could not be extracted
    """
    ready_dirs = set()
    failed_archives = []

    # Extract ZIP files that haven't been extracted yet
    for zip_path in sorted(ocr_dir.glob("*.zip")):
        if wanted is not None and not wanted(zip_path.stem):
            continue
        target = ocr_dir / zip_path.stem
        if not target.exists() and not extract:
            # --dry-run: unpack nothing. The plan lists these itself, so
            # saying it here as well printed every archive twice.
            continue
        if not target.exists():
            say(f"[dim]Extracting: {escape(zip_path.name)} -> {escape(target.name)}/[/dim]")
            # Broad on purpose: zipfile surfaces corruption as BadZipFile,
            # zlib.error, RuntimeError or NotImplementedError depending on
            # where it hits; whatever the reason, the run must go on.
            try:
                _extract_archive(zip_path, target)
            except Exception as e:
                logging.getLogger(__name__).error(
                    "Cannot extract archive %s: %s", zip_path.name, e
                )
                console.print(
                    f"[bold red]Archive skipped:[/bold red] {escape(f'{zip_path.name} ({e})')}"
                )
                failed_archives.append((zip_path.name, str(e)))
                continue
        ready_dirs.add(target)

    # Include the volume directories already there (never the temporary
    # ".extracting" directories of an interrupted run, nor the noise a
    # zip tool leaves beside them).
    #
    # Directories holding no ALTO come through too, and the caller says
    # so out loud. Requiring an *.xml here dropped them before anything
    # could count them, and dropped them ONLY on this path: the archive
    # loop above adds its target whatever the archive turned out to hold.
    # The same empty volume was therefore reported when it arrived as a
    # zip and invisible once the operator deleted that zip — the one
    # difference being which of the two runs you happened to look at.
    for d in ocr_dir.iterdir():
        if not d.is_dir() or d.name.endswith(".extracting"):
            continue
        if d.name.startswith(".") or d.name == "__MACOSX":
            continue
        # Gated like the archives: listing every page of every volume
        # to throw them away afterwards is the same waste on a
        # hundred-volume rerun.
        if keep is not None and not keep(d.name):
            continue
        ready_dirs.add(d)

    return sorted(ready_dirs), failed_archives


def _document_iiif_config(manifest_url):
    """
    Per-document IIIF settings: config.IIIF_URI with the volume's image
    base filled in.

    The Gallica-derived base wins when one can be computed; otherwise
    whatever IIIF_URI configures survives. (Passing the derivation as a
    keyword overwrote a configured base with None for every non-Gallica
    manifest — the one key config.IIIF_URI still offers, audit 4.6.)
    """
    doc_iiif = dict(IIIF_URI)
    gallica_base = _gallica_image_base(manifest_url)
    if gallica_base:
        doc_iiif["image_base"] = gallica_base
    return doc_iiif


def _gallica_image_base(manifest_url):
    """
    IIIF image base for a Gallica manifest URL, else None.

    Other IIIF servers (e.g. digitale-sammlungen) expose a different image
    API that cannot be guessed from the manifest URL alone — @source is
    then omitted (or would need a per-page IIIF mapping CSV).

    Args:
        manifest_url (str): IIIF manifest URL, or None.

    Returns:
        str or None: Image base URL, or None if not a recognized Gallica manifest.
    """
    if not manifest_url:
        return None
    m = re.match(
        r"(https://gallica\.bnf\.fr/iiif/ark:/\d+/[a-z0-9]+)/manifest\.json",
        manifest_url.strip(),
    )
    return m.group(1) if m else None


def _out_path(doc_name, output_dir):
    """Final TEI output path of a document — single spelling for the writer,
    the --skip-existing filter and the failure cleanup."""
    return output_dir / f"{doc_name}.tei.xml"


# =============================================================================
# MAIN WORKFLOW
# =============================================================================

# Exit codes. 1 means some units failed; 3 means nothing ran because the
# run was misconfigured. They used to be the same number, so a wrapper
# could not tell "fix your path and rerun" from "some volumes broke".
EXIT_OK = 0
EXIT_SOME_FAILED = 1
# Two flags that contradict each other: argparse owns most of these, but
# the reporter choice is made after parsing.
EXIT_USAGE = 2
EXIT_MISCONFIGURED = 3
# Everything that ran failed. Separated from 1 so a wrapper can tell
# "retry these two" from "your input or your setup is wrong".
EXIT_ALL_FAILED = 4
# SIGINT. Distinct from every other code because it is not a verdict on
# the corpus: the run was stopped, and what it had written is kept.
EXIT_INTERRUPTED = 130


def select_documents(docs, selectors, exclusions, limit, skip=None):
    """Narrow the discovered documents to what was asked for.

    A selector matches a directory name, the internal id parsed from it,
    or a glob over either. Exclusions apply after selection, and the limit
    last, so `-x` can carve a hole out of a glob and `--limit` still counts
    what survives.

    Args:
        docs (list): (name, filepaths, directory) triples, in discovery order.
        selectors (list): names, internal ids or globs; empty means all.
        exclusions (list): patterns to drop afterwards.
        limit (int): keep at most this many, or None.
        skip (callable): name -> True when the volume is already
            converted. Applied BEFORE the limit, so `--skip-existing
            --limit N` advances through the corpus instead of taking the
            same N volumes and skipping them all on every run.

    Returns:
        tuple: (kept, skipped) — skipped counts what the resume predicate
        removed, and nothing else. Counting every kind of drop announced
        volumes as "already converted" that a selector or a limit had
        dropped. No pattern is judged here: `execute` validates them
        against the raw directory listing, which is the only place that
        sees the corpus before it is narrowed.
    """
    def matches(pattern, name):
        # The canonical id is what the pipeline writes into xml:id and what
        # a user reads back out of a TEI file, so it has to select: without
        # it `run LIV0002a` matched nothing while `LIV0002` silently took
        # every volume of the set.
        candidates = (name, parse_document_id(name)[0],
                      canonical_document_id(name))
        return any(candidate == pattern or fnmatch(candidate, pattern)
                   for candidate in candidates)

    # No pattern is judged here, selector or exclusion. By the time this
    # runs the list has been narrowed by those very patterns — an excluded
    # or non-selected archive was never unpacked — so a "no match" verdict
    # would be about this function's own filtering. `execute` validates
    # every pattern against the raw directory listing beforehand, which is
    # the only place that sees the whole corpus.
    kept = list(docs)
    if selectors:
        kept = [d for d in docs if any(matches(p, d[0]) for p in selectors)]
    for pattern in exclusions or []:
        # Exclusions are NOT reported unmatched here. By the time this runs
        # the list has already been narrowed — an excluded archive was
        # never unpacked, a non-selected directory was never scanned — so
        # this could only ever answer "no match" for patterns that matched
        # perfectly well. `execute` validates every selector and exclusion
        # against the raw directory listing before any of that.
        kept = [d for d in kept if not matches(pattern, d[0])]
    skipped = 0
    if skip is not None:
        before_skip = len(kept)
        kept = [d for d in kept if not skip(d[0])]
        skipped = before_skip - len(kept)
    if limit is not None:
        kept = kept[:limit]
    return kept, skipped


def _phase_lost(reporter, doc_name, phase, stats, unit, reason):
    """A whole phase that produced nothing because its service died.

    Never folded into a container count: a document written with none of
    a phase is damage of a different kind, and averaging the two is the
    convenient lie. This is also the only producer of a block-3 loss on a
    run where every volume converted — which is what `--fail-on incident`
    exists to catch.
    """
    if reporter is None:
        return
    # `containers_found`, which is the key the phases actually write.
    # `containers_total` was read here and written nowhere, so every lost
    # phase recorded "0 of 0 containers" — the exact sentence CLAUDE.md
    # forbids, produced by the code that exists to forbid it.
    reporter.phase(doc_name, phase, PhaseState.LOST,
                   done=0, total=stats.get("containers_found", 0) or 0,
                   unit=unit, reason=reason)
    # And on the banner. It answered the probe and has stopped answering,
    # which is the one state the service line was drawn for.
    if phase in _SERVICE_OF:
        reporter.service_lost(_SERVICE_OF[phase])


# One counter used to carry four causes. They are four lines of the
# report now, in two different blocks — a guard refusing to anchor
# annotations to the wrong characters is not the same fact as a service
# refusing to answer, and averaging them is the convenient lie.
# One counter used to carry four causes. They are four lines of the
# report now, in two different blocks — a guard refusing to anchor
# annotations to the wrong characters is not the same fact as a service
# refusing to answer, and averaging them is the convenient lie. Each
# keeps its own step, so the summary never sums two counts against one
# denominator.
_CONTAINER_CAUSES = (
    ("containers_broken", Code.CONTAINER_FAILED,
     "the pipeline raised on these"),
    ("containers_refused", Code.CONTAINER_FAILED,
     "the service refused these"),
    ("containers_unsent", Code.BREAKER_SKIPPED,
     "the circuit breaker stopped sending"),
    ("containers_unanchored", Code.CONTAINER_UNANCHORED,
     ">20% of a block could not be anchored"),
)


def _containers_failed(reporter, doc_name, phase, stats, reason):
    """Containers a live service did not annotate, by cause.

    Different from a dead service, and it stays a container count: the
    document has the phase, minus these.

    Only enrichment splits its causes; modernization counts one total, so
    the split keys are read when they are there and the total is used
    when they are not — reading only the split keys recorded nothing at
    all for modernization, while the console printed a warning about it.
    """
    total = stats.get("containers_found") or stats.get("containers_failed", 0)
    if not total:
        return
    split = sum(stats.get(key, 0) for key, _code, _why in _CONTAINER_CAUSES)
    if not split:
        failed = stats.get("containers_failed", 0)
        if failed:
            reporter.lost(Loss(Code.CONTAINER_FAILED, doc_name, phase,
                               Locator.document(doc_name), count=failed,
                               total=total, detail=reason))
        return
    for key, code, why in _CONTAINER_CAUSES:
        count = stats.get(key, 0)
        if count:
            # The cause is the step, so two causes sharing a code stay
            # two lines with their own diagnosis rather than one line
            # carrying the first one's.
            reporter.lost(Loss(code, doc_name, f"{phase}.{key.split('_')[-1]}",
                               Locator.document(doc_name), count=count,
                               total=total, detail=why))


def _refuse(code=None, settings=None):
    """Leave, having written nothing — the log included.

    Exit 3 says the run did not run. A record emitted during setup opens
    the lazy handler, so the refusal used to leave a `pipeline_*.log`
    behind: that is how twenty-five orphans came to sit at the root of
    the repository. The reason is already on the console, and `-v`
    reproduces it.
    """
    global RUN_LOG_FILE
    named = settings is not None and settings.origin("log_file") != "default"
    if RUN_LOG_FILE and not named:
        # A log the operator named is an instruction, and the summary
        # path already treats it as one. Two contradicting judgments in
        # one file is one too many.
        for handler in list(logging.getLogger().handlers):
            if getattr(handler, "baseFilename", None) == str(RUN_LOG_FILE):
                handler.close()
                logging.getLogger().removeHandler(handler)
        try:
            Path(RUN_LOG_FILE).unlink(missing_ok=True)
        except OSError:
            # A log we cannot remove is a smaller problem than a run that
            # ends on it.
            pass
        RUN_LOG_FILE = None
    sys.exit(EXIT_MISCONFIGURED if code is None else code)


def _keep_the_record(store, settings, failed, exit_code):
    """The manifest and the log, on a path that exits before the run loop.

    Without it a corpus where nothing could be opened leaves no record,
    and the next `--retry-failed` is told nothing failed. The log comes
    too: an index that outlives its transcript is exactly what pruning
    them together exists to prevent.
    """
    global RUN_LOG_FILE
    if store is None:
        return
    if RUN_LOG_FILE and settings.origin("log_file") == "default":
        RUN_LOG_FILE = store.adopt_log(RUN_LOG_FILE)
    store.finish(argv=["teille-douce", *sys.argv[1:]],
                 settings=settings.as_manifest(),
                 documents={Path(name).stem: "failed" for name, _ in failed},
                 exit_code=exit_code)
    RunStore.prune(settings.output_dir, spare=store.path)


# "no handler was installed", told apart from a handler that IS None.
_UNSET_SIGNAL = object()


class HeldInterrupts:
    """SIGINT recorded instead of delivered, until `release`.

    A second interrupt threw away the very thing the first one exists to
    preserve: the run directory, the manifest and the report of what had
    been written. Two of them a hundred and fifty milliseconds apart left
    one TEI file on disk, no manifest, no summary and a `--retry-failed`
    with nothing to read — over a message that had just promised to
    finish the report.

    Held from inside the interrupt handler and not merely around the
    report, because the second one lands during the panel's teardown:
    wrapping the writing alone still lost four sweeps in six. Held and
    not ignored, over a teardown, a JSON write and some printing — and
    honoured on the way out.
    """

    def __init__(self):
        self._held = _UNSET_SIGNAL
        self._arrived = []
        # Whether the note reached the operator through the print rather
        # than through the log. Recorded because the choice is made by a
        # predicate over the root logger's handlers, and no test can see
        # the difference from the outside.
        self.said = []

    def hold(self):
        if self._held is not _UNSET_SIGNAL:
            return
        try:
            self._held = signal.signal(
                signal.SIGINT, lambda *_: self._arrived.append(True))
        except ValueError:      # pragma: no cover - not the main thread
            pass

    def release(self):
        """Give the signal back, and say whether one was held.

        It does NOT raise. Raising from the `finally` that releases it
        replaced whatever was already on its way out — including the
        `sys.exit(exit_code)` that is the last statement of the run — so
        a run whose summary said `exit 5` and whose manifest said 5 gave
        the shell 130, the one code documented as "not a verdict on the
        corpus". It swallowed real exceptions the same way, leaving the
        failure in `__context__` under a bare "Interrupted".

        And the verdict is right: by the time this releases, everything
        is written and printed. An interrupt that arrives after the run
        has finished its work has stopped nothing.
        """
        try:
            if self._held is not _UNSET_SIGNAL:
                try:
                    signal.signal(signal.SIGINT, self._held)
                except TypeError:
                    # `getsignal` answers None for a handler installed
                    # outside Python — the premise `_UNSET_SIGNAL` exists
                    # for — and `signal(SIGINT, None)` is a TypeError.
                    # The default is the honest fallback: a Ctrl-C works
                    # again, which is all the caller needed.
                    #
                    # `ValueError` is NOT caught here, and that is the
                    # point: `signal.signal` raises it for exactly one
                    # reason, not being on the main thread — so the
                    # recovery is the identical call and raises the
                    # identical error. Catching it here could only ever
                    # re-raise what it caught. It is caught below, with
                    # everything else, because nothing this method does
                    # is worth the run's exit code.
                    signal.signal(signal.SIGINT, signal.default_int_handler)
        except BaseException:
            # BaseException, not Exception: a SIGINT delivered between
            # the restore and the return is a `KeyboardInterrupt`, and
            # it escaped this `finally` over the run's own verdict —
            # which is the defect four rounds ago was written to end,
            # surviving in the guard meant to close it.
            #
            # And the default installed here, not merely swallowed: the
            # line below forgets what was held, so a restore that failed
            # would otherwise leave the deferring lambda in place with
            # no record of the real handler — the next `hold()` saving a
            # lambda, and the process deaf for good.
            try:
                signal.signal(signal.SIGINT, signal.default_int_handler)
            except BaseException:
                pass
        # Cleared whatever happened. Left set by an exception on the way
        # through, the object still believes it is holding, and the next
        # `hold()` is a no-op.
        self._held = _UNSET_SIGNAL
        held, self._arrived = bool(self._arrived), []
        return held


@contextmanager
def finishing(interrupts=None):
    """The stretch where the run must not be interrupted again."""
    interrupts = interrupts if interrupts is not None else HeldInterrupts()
    interrupts.hold()
    try:
        yield
    finally:
        arrived = interrupts.release()
        if arrived:
            # Said, not silent, and not acted on: the report and the
            # manifest are already on disk, so there is nothing left for
            # the signal to stop.
            #
            # To the LOG and to stderr, not into the report: the verdict
            # is the summary's last line and the one wrappers grep, and
            # this note printed after it. And to the log because stdout
            # is transient — an interrupt in this stretch left no trace
            # a reader coming back on Thursday could find.
            # To the log FILE only. The same message through the console
            # handler and again through the print below said it twice on
            # stderr, and under `-qq` — the level whose help says the
            # reader accepts losing warnings — only the unsuppressable
            # copy survived, which is the wrong way round.
            note = ("a Ctrl-C arrived while the report was being written; "
                    "the report and the manifest are complete")
            # Where the note went, for the one test that can tell the
            # print's branch from the log's — the two are chosen by a
            # predicate over the root logger's handlers, and a test
            # process has handlers a terminal does not.
            printed = interrupts.said
            # Only when the log line would NOT be seen. At the default
            # level INFO reaches the file alone, so this print is the one
            # visible copy; under `-vv` the console shows INFO too and
            # the note was said twice.
            # A handler ON STDOUT OR STDERR, not any `StreamHandler`
            # without a filename. pytest's own capture handler is one of
            # those, at level 0 — so under the suite this was always
            # true, the print below was never executed by any test, and
            # the two tests written to reach it stopped here. The
            # operator's only copy of this note at the default level was
            # covered by nothing.
            spoken = any(
                isinstance(handler, logging.StreamHandler)
                and getattr(handler, "stream", None) in (sys.stdout,
                                                         sys.stderr)
                and handler.level <= logging.INFO
                for handler in logging.getLogger().handlers)
            try:
                # Inside the guard, not above it: `logging`'s own
                # `handleError` catches `OSError` alone, so a console
                # handler on a closed stream raised `ValueError` out of
                # this `finally` — verbatim the failure the rest of this
                # comment says the guard exists to prevent, one
                # statement above it.
                logging.getLogger(__name__).info(note)
                # `sys.stderr` is None under `2>&-`, and `print(file=None)`
                # falls back to STDOUT — which puts the note back into
                # the report, after the verdict, which is the placement
                # it was moved away from. And a CLOSED stderr raises
                # ValueError, not OSError, so the guard let it escape
                # this `finally` and replace the run's own exit code:
                # the failure mode of two rounds ago, inside the guard
                # written to prevent it.
                if sys.stderr is not None and not spoken:
                    print("  (a Ctrl-C arrived while the report was being "
                          "written; it is complete)", file=sys.stderr)
                    printed.append(True)
            except Exception:
                pass


@contextmanager
def panel_installed(reporter, active):
    """Put the live panel up, and take it down whatever happens.

    Straight-line setup was not enough. A signal arrives at an arbitrary
    instruction, not conveniently inside the one `try` the loop had, and
    it took the whole teardown with it: the digest handler stayed on the
    root logger swallowing every later warning, the console handler was
    gone for good, `_QUIET` stayed true, and Rich's Live kept the cursor
    — on top of losing the summary and the manifest.

    Torn down before the summary prints, so the summary lands in the
    scrollback rather than inside a frame about to be erased.
    """
    if not active:
        yield None
        return

    root = logging.getLogger()
    # Eighty WARNING lines a volume printed one at a time push the panel
    # off the top of the terminal, which is how a run that is repairing a
    # source defect comes to look like one that is failing. They are
    # folded instead; the file handler still receives every record.
    digest = DigestHandler(reporter)
    replaced = [h for h in root.handlers
                if isinstance(h, logging.StreamHandler)
                and not hasattr(h, "baseFilename")]
    was_quiet = _QUIET
    panel = Dashboard(console, reporter)
    try:
        for handler in replaced:
            root.removeHandler(handler)
        root.addHandler(digest)
        # Silences `say`: everything it would print is on the panel, and
        # a print inside a Live region pushes a copy of the frame into
        # the scrollback on every line.
        _set_quiet(True)
        panel.__enter__()
        yield panel
    finally:
        # Each step in its own guard. A second Ctrl-C arriving inside
        # `Live.stop()` skipped the rest of this block, and the process
        # kept the digest handler, lost the console one and stayed quiet
        # for good — the state this context manager exists to prevent,
        # one frame inward.
        teardown = None
        try:
            panel.__exit__(None, None, None)
        except BaseException as reason:
            # Swallowed so the rest of the teardown still happens, but
            # not silently: a Live that failed to stop leaves the cursor
            # somewhere, and the operator is owed the reason.
            teardown = reason
        _set_quiet(was_quiet)
        root.removeHandler(digest)
        for handler in replaced:
            root.addHandler(handler)
        if teardown is not None:
            # AFTER the console handler is back. Logged where it was
            # raised, the only non-file handler on root was the digest —
            # which prints nothing by design and is read through a panel
            # that had just come down — so the one message explaining a
            # terminal left with no cursor went to a screen nobody would
            # ever draw again.
            logging.getLogger(__name__).warning(
                "the live panel did not shut down cleanly (%s)", teardown)


def _pages_per_second(reporter, started):
    """Measured, like everything else on the panel.

    Off the collector's own counter and not off a whole `PanelState`:
    this is recomputed on every state change, and a volume's worth of
    per-container progress is fourteen hundred of them.
    """
    elapsed = perf_counter() - started
    return reporter.pages_written / elapsed if elapsed > 0 else 0.0


# Which service answers for which phase. The banner shows one dying, so
# something has to tell it which one died — the phase knows, and parsing
# the reason string for a name would be a second place to get it wrong.
_SERVICE_OF = {"enrich": "PyHellen", "modernize": "VieuxParler",
               "ner": "NER local"}


def _phase_progress(reporter, progress, task, doc_name, phase, unit):
    """A progress callback that tells the panel, not only the old bar.

    Rich's `Progress` was the only thing these callbacks fed, so
    `Run.phase()` had exactly one caller in the whole codebase —
    `_phase_lost` — and only ever for a phase that DIED. Every line the
    panel draws for a phase that is going well, its bar and its rate, was
    unreachable code: a real run showed three zeroed loss counters, a
    blank phase block and a clock that did not move.
    """
    def report(current, total):
        progress.update(task, completed=current, total=total)
        if reporter is not None:
            # `total` stays None when the phase does not know it yet —
            # Rich's own indeterminate. The panel renders a count with no
            # denominator rather than inventing a zero.
            reporter.phase(doc_name, phase, PhaseState.RUNNING,
                           done=current, total=total, unit=unit)
    return report


class _ProgressToPanel:
    """A Rich `Progress` that also tells the panel.

    `build_sourcedoc` speaks to a `Progress` and to nothing else, and it
    runs the multiprocessing pool — threading a second callback down
    through the worker fan-out for the reporter's sole benefit would put
    a picklability constraint on the panel. Intercepting the one call it
    makes costs nothing and keeps the builder unaware there is a panel.
    """

    def __init__(self, progress, task, reporter, doc_name, phase, unit,
                 total):
        self._progress = progress
        self._task = task
        self._reporter = reporter
        self._doc = doc_name
        self._phase = phase
        self._unit = unit
        self._total = total
        self._done = 0

    def __getattr__(self, name):
        return getattr(self._progress, name)

    def update(self, task, **fields):
        self._progress.update(task, **fields)
        if task != self._task or self._reporter is None:
            return
        self._done += fields.get("advance", 0)
        if "completed" in fields:
            self._done = fields["completed"]
        self._reporter.phase(self._doc, self._phase, PhaseState.RUNNING,
                             done=self._done, total=self._total,
                             unit=self._unit)


def _phase_done(reporter, doc_name, phase, done, total, unit, note=""):
    """The phase stopped, and stopped well.

    Left RUNNING it keeps a spinner turning beside a count that will
    never change again — the panel claiming work that has finished.
    """
    if reporter is None:
        return
    reporter.phase(doc_name, phase, PhaseState.DONE, done=done, total=total,
                   unit=unit, note=note)


def _process_document(doc_name, filepaths, doc_dir, df_meta, config,
                      person_db, do_enrich, do_modernize, do_ner, progress,
                      reporter=None):
    """
    Run the full conversion pipeline on one document.

    Raises on failure: error isolation lives in main()'s loop (audit 2.1),
    so one broken document cannot kill a multi-hour run.
    """
    settings = get_settings()
    t0 = perf_counter()
    say(f"\n[bold cyan]-> {escape(doc_name)}[/bold cyan]")

    # Initialize TEI tree
    tree = TEI(doc_name, filepaths, doc_dir)
    tree.build_tree()

    # Progress bar for pages
    task_pages = progress.add_task(
        f"{escape(doc_name)}: pages", total=len(filepaths), visible=True
    )

    if reporter is not None:
        # The stretches between the phases raise too — a missing CSV
        # column, an unwritable output path — and the index has a field
        # for which one.
        reporter.step(doc_name, "metadata")

    # Load metadata for this document
    # find_metadata_row extracts the BDD prefix itself (audit 4.11:
    # main.py used to keep a copy and apply it a second time).
    row = find_metadata_row(df_meta, doc_name)
    tree.metadata = build_metadata_dict(row)

    # Resolve this document's IIIF image base from its manifest (Gallica
    # only — other servers' image APIs aren't derivable from the manifest
    # URL, so @source is omitted for those pages).
    manifests = tree.metadata["iiif"].get("manifests") or []
    volume = parse_document_id(doc_name)[1]
    doc_manifest = select_manifest(manifests, volume)
    config["iiifURI"] = _document_iiif_config(doc_manifest)

    # Build TEI header
    # The header states what ran, not what was asked for. A service that
    # failed its probe disables its phase for the whole run, and every file
    # still declared <normalization> asserting that reg readings had been
    # generated — an editorial claim about a file that carries none.
    with use_settings(enrich=do_enrich, modernize=do_modernize, ner=do_ner):
        tree.root, tree.segmonto_zones, tree.segmonto_lines = build_header(
            tree.metadata,
            tree.d,
            tree.root,
            len(tree.fp),
            config,
            APP_VERSIONS,
            tree.fp,
        )

    # Build sourceDoc (parallel processing)
    tree.build_sourcedoc(
        config,
        progress=_ProgressToPanel(progress, task_pages, reporter, doc_name,
                                  "sourceDoc", "pages", len(filepaths)),
        parent_task_pages=task_pages,
    )

    if reporter is not None:
        read = len(filepaths) - len(tree.skipped_pages)
        reporter.pages_read(doc_name, read)
        # The pages it could not use, named. They are the reason this
        # phase has a line of its own, and three page numbers are what
        # sends the operator to the right files.
        lost = sorted(tree.skipped_pages)[:3]
        note = ""
        if tree.skipped_pages:
            more = "" if len(tree.skipped_pages) <= 3 else ", …"
            note = (f"{read} of {len(filepaths)} pages read · "
                    f"{len(tree.skipped_pages)} unusable "
                    f"({', '.join(str(page) for page in lost)}{more})")
        _phase_done(reporter, doc_name, "sourceDoc", read, len(filepaths),
                    "pages", note=note)
        if tree.repaired_ids:
            # A defect of the source that was REPAIRED: nothing is
            # missing from the output because of it, and filing it
            # anywhere but block 1 would make the largest number in the
            # summary read as an alarm.
            reporter.lost(Loss(
                Code.ALTO_IDS_REPAIRED, doc_name, "sourcedoc",
                Locator.document(doc_name), count=tree.repaired_ids,
                total=tree.minted_ids or tree.repaired_ids,
                detail="duplicates disambiguated"))
        for page in tree.skipped_pages:
            reporter.lost(Loss(
                Code.PAGE_UNUSABLE, doc_name, "sourcedoc",
                Locator.page(doc_name, page), count=1,
                total=len(filepaths),
                detail="no <surface> could be built"))

    # Unusable pages must be loud: a document with no page at all is a
    # failure, a partial one is flagged here, on the document it belongs
    # to, and written to the log. The end-of-run summary carries them too,
    # from the record: a nightly wrapper reading that line alone cannot
    # tell a corpus converted whole from one that lost pages in a dozen
    # volumes.
    if tree.skipped_pages:
        if len(tree.skipped_pages) == len(filepaths):
            raise RuntimeError(
                f"all {len(filepaths)} pages unusable (see log for details)"
            )
        console.print(
            f"  [yellow]Warning: {len(tree.skipped_pages)}/{len(filepaths)} "
            f"pages skipped (unusable ALTO) — see log[/yellow]"
        )

    # Step 1: Build body + language detection
    task_lang = progress.add_task(
        f"[cyan]{escape(doc_name)}: Detection des langues[/cyan]", total=None, visible=True
    )
    if reporter is not None:
        reporter.phase(doc_name, "body+lang", PhaseState.RUNNING,
                       note="reading the zones")
    tree.build_body(detect_lang=True)
    link_notes_to_lines(tree.root)
    progress.update(task_lang, visible=False)
    # The step between sourceDoc and enrichment showed nothing at all:
    # the panel drew a finished sourceDoc over an empty gap while the
    # zones were being read, and its own design — down to the fixture the
    # render tests use — carries a `body+lang` line.
    if reporter is not None:
        spoken = " · ".join(
            f"{code} {count}" for code, count
            in sorted(tree.lang_stats.items(), key=lambda pair: -pair[1])[:4])
        _phase_done(reporter, doc_name, "body+lang", 1, 1, "documents",
                    note=spoken or "no language detected")

    if tree.lang_stats:
        langs = [f"{k}:{v}" for k, v in sorted(tree.lang_stats.items(), key=lambda x: -x[1])[:4]]
        say(f"  [dim]Languages: {', '.join(langs)}[/dim]")

    # Step 2: Extract line data for modernization (before enrichment modifies DOM)
    line_data = None
    if do_modernize:
        line_data = tree.extract_line_data()

    # Step 3: Linguistic enrichment (must run before modernization is applied)
    if do_enrich:
        task_enrich = progress.add_task(
            f"[cyan]{escape(doc_name)}: Annotation linguistique[/cyan]", total=None, visible=True
        )

        # "requests", not "containers": the callback fires once per text
        # sent to PyHellen and a container can be several, so the bar was
        # measured in one unit and the line it settles on in another. The
        # DONE line below says containers, which is what the phase's
        # losses are counted in; both are printed with their unit, and
        # neither is the other's denominator.
        _enrich_progress = _phase_progress(reporter, progress, task_enrich,
                                           doc_name, "enrich", "requests")

        enrich_stats = tree.enrich_body(progress_callback=_enrich_progress)
        progress.update(task_enrich, visible=False)

        if enrich_stats and enrich_stats.get("containers_enriched", 0) > 0:
            say(
                f"  [dim]Annotation: {enrich_stats['containers_enriched']} containers, "
                f"{enrich_stats['tokens_total']} tokens, "
                f"{enrich_stats['sentences_total']} sentences[/dim]"
            )
        # containers_failed was never displayed: a document with hundreds
        # of unannotated containers looked like a success in the console.
        if enrich_stats and enrich_stats.get("containers_failed", 0) > 0:
            console.print(
                f"  [yellow]Warning: {enrich_stats['containers_failed']} "
                f"container(s) failed enrichment — see log[/yellow]"
            )
        # A server that died after the startup probe leaves every counter
        # at zero: without this the document would look simply "not
        # enriched" instead of "enrichment lost".
        if enrich_stats and enrich_stats.get("server_unavailable"):
            console.print(
                "  [yellow]Warning: PyHellen unreachable for this document "
                "— no linguistic annotation[/yellow]"
            )
            _phase_lost(reporter, doc_name, "enrich", enrich_stats,
                        "containers", "PyHellen stopped answering")
        elif reporter is not None:
            # Not `and enrich_stats`: an empty dict left the phase
            # RUNNING for the rest of the volume, spinning beside a count
            # that would never change again. The same shape as the
            # modernize branch below, one guard away.
            enrich_stats = enrich_stats or {}
            _containers_failed(reporter, doc_name, "enrich", enrich_stats,
                               "PyHellen refused these containers")
            _phase_done(reporter, doc_name, "enrich",
                        enrich_stats.get("containers_enriched", 0),
                        enrich_stats.get("containers_found", 0), "containers")

    # Step 4: Text modernization (applied after enrichment)
    if do_modernize:
        task_mod = progress.add_task(
            f"[cyan]{escape(doc_name)}: Modernisation du texte[/cyan]", total=None, visible=True
        )

        # "batches": `_modernize_all` reports one unit per HTTP batch.
        # Labelled containers, the bar filled to "10 of 10 containers" on
        # a document with twenty-three and then jumped to 23 of 23 — the
        # denominator changing meaning mid-phase, on the phase whose bar
        # an operator watches longest.
        _mod_progress = _phase_progress(reporter, progress, task_mod,
                                        doc_name, "modernize", "batches")

        mod_stats = tree.modernize_body(
            line_data=line_data,
            enriched=do_enrich,
            progress_callback=_mod_progress,
        )
        progress.update(task_mod, visible=False)

        if mod_stats.get("lines_modernized", 0) > 0:
            say(
                f"  [dim]Modernisation: {mod_stats['lines_modernized']} lines[/dim]"
            )
        # Same hole as enrichment had (audit 2.7): a document whose
        # containers were left untouched looked like a success.
        if mod_stats.get("containers_failed", 0) > 0:
            console.print(
                f"  [yellow]Warning: {mod_stats['containers_failed']} "
                f"container(s) left unmodernized — see log[/yellow]"
            )
        # A service that died mid-run leaves every counter at zero, and
        # nothing under it was printed: the document was written without
        # a single <choice> and reported as converted.
        if mod_stats.get("server_unavailable"):
            console.print(
                "  [yellow]Warning: VieuxParler returned nothing for this "
                "document — no modernization[/yellow]"
            )
            _phase_lost(reporter, doc_name, "modernize", mod_stats,
                        "containers", "VieuxParler stopped answering")
        elif reporter is not None:
            _containers_failed(reporter, doc_name, "modernize", mod_stats,
                               "VieuxParler refused these containers")
            if mod_stats.get("batches_failed"):
                reporter.lost(Loss(
                    Code.BATCH_FAILED, doc_name, "modernize",
                    Locator.document(doc_name),
                    count=mod_stats["batches_failed"],
                    # Against every batch, not against itself. `count ==
                    # total` made this line say a hundred per cent for
                    # one refused batch in four, which is the only thing
                    # it could ever say.
                    total=mod_stats.get("batches_total",
                                        mod_stats["batches_failed"]),
                    detail=f"{mod_stats.get('lines_lost', 0)} lines never "
                           f"reached VieuxParler"))
            if mod_stats.get("retries_unreachable"):
                # Block 3, beside the failed batches: a retry the service
                # never answered is the service dying, not a guard doing
                # its job, and filing it in block 2 hid it from every
                # `--fail-on` level there is.
                reporter.lost(Loss(
                    Code.RETRY_UNANSWERED, doc_name, "modernize.retry",
                    Locator.document(doc_name),
                    count=mod_stats["retries_unreachable"],
                    # Against the lines that were RETRIED. Measured
                    # against every line of the first pass, a retry phase
                    # that lost all six of its lines read `6 of 40`.
                    total=mod_stats.get("lines_retried",
                                        mod_stats["retries_unreachable"]),
                    detail="VieuxParler did not answer the retry"))
            if mod_stats.get("readings_rejected"):
                # Block 2. The service answered and the guard refused
                # what it answered — a divergent reading kept as its
                # original is the pipeline working, not failing, and it
                # was counted into a local and dropped. CLAUDE.md names
                # this one by name: "rejected modernizations … counted
                # and printed".
                reporter.lost(Loss(
                    Code.READING_REJECTED, doc_name, "modernize",
                    Locator.document(doc_name),
                    count=mod_stats["readings_rejected"],
                    total=mod_stats.get("lines_offered",
                                        mod_stats["readings_rejected"]),
                    detail="the reading diverged from the original and was "
                           "not used",
                    unit="readings"))
            # Marked done, which it never was: the spinner turned beside
            # a full bar for the whole of NER, the header override and
            # the write — minutes, on a volume with entity recognition —
            # claiming work that had finished. Verbatim what
            # `_phase_done` exists to prevent, on the one phase that
            # never called it.
            found = mod_stats.get("containers_found", 0)
            _phase_done(reporter, doc_name, "modernize",
                        max(0, found - mod_stats.get("containers_failed", 0)),
                        found, "containers")

    # Step 5: Named Entity Recognition (after enrichment + modernization)
    if do_ner:
        task_ner = progress.add_task(
            f"[cyan]{escape(doc_name)}: Reconnaissance d'entites nommees[/cyan]",
            total=None,
            visible=True,
        )

        if reporter is not None:
            reporter.phase(doc_name, "ner", PhaseState.RUNNING,
                           note="reading the document")
        try:
            from teille_douce.enrichment.ner_pipeline import run_ner, summarize

            ner_losses = {}
            resolved = run_ner(tree.root, person_db, doc_name,
                               losses=ner_losses)
            summary = summarize(resolved)
            if summary:
                say(f"  [dim]NER: {summary}[/dim]")
            if reporter is not None and ner_losses.get("entities_filtered"):
                # Block 2: an entity that reached resolution and was then
                # pruned is something the pipeline could have emitted and
                # chose not to. It went to a `logger.info` alone, so the
                # block printed "nothing" over runs that had withheld
                # hundreds — the second of the two cases CLAUDE.md names.
                reporter.lost(Loss(
                    Code.ENTITY_FILTERED, doc_name, "ner",
                    Locator.document(doc_name),
                    count=ner_losses["entities_filtered"],
                    total=ner_losses.get("entities_offered",
                                         ner_losses["entities_filtered"]),
                    detail="one mention, below the confidence floor",
                    unit="entities"))
            _phase_done(reporter, doc_name, "ner", 1, 1, "documents",
                        note=summary or "no entity found")

        # The third phase reported nothing at all: a run whose NER died on
        # every volume exited 0 under `--fail-on incident`, drew a green
        # tick on the banner and printed "lost to an incident … nothing"
        # over a corpus with no <standOff> in it. Every phase reports what
        # it lost — this one was the exception that proved nobody had
        # tried it.
        #
        # Per document and against a denominator of one: NER runs over the
        # whole tree in one step, so the honest measure of what was lost
        # is the document itself.
        except ImportError as e:
            console.print(
                f"[yellow]Warning: NER dependencies not installed "
                f"({escape(str(e))}) — skipping NER.[/yellow]"
            )
            if reporter is not None:
                reporter.phase(doc_name, "ner", PhaseState.LOST, done=0,
                               total=1, unit="documents",
                               reason=f"NER dependencies not installed ({e})")
                reporter.service_lost(_SERVICE_OF["ner"])
        except Exception as e:
            logging.getLogger(__name__).error("NER pipeline failed: %s", e, exc_info=True)
            console.print(f"[yellow]Warning: NER failed ({escape(str(e))}) — continuing.[/yellow]")
            if reporter is not None:
                reporter.phase(doc_name, "ner", PhaseState.LOST, done=0,
                               total=1, unit="documents",
                               reason=f"NER failed ({e})")
                reporter.service_lost(_SERVICE_OF["ner"])

        progress.update(task_ner, visible=False)

    # Override TEI header with CSV metadata
    override_teiheader_from_csv(tree.root, row, doc_name)

    # Finalize langUsage with detected languages (after CSV override)
    tree.finalize_langusage()

    # Volumetry, once every phase that could add tokens has run
    tree.finalize_extent()

    # Write output file
    if reporter is not None:
        reporter.step(doc_name, "write")
    out_path = _out_path(doc_name, settings.output_dir)
    write_xml(tree.root, out_path)

    dt = perf_counter() - t0
    say(f"[green]OK[/green] Written: {escape(str(out_path))} [dim]({dt:.2f}s)[/dim]")

    progress.update(task_pages, visible=False)


def execute(args):
    """
    Main workflow for ALTO to TEI conversion.

    Processes all documents in the OCR directory:
    1. Extracts ZIP archives if present
    2. Loads metadata from CSV
    3. For each document:
       - Builds TEI tree
       - Builds TEI header with metadata
       - Builds sourceDoc from ALTO files (parallel processing)
       - Builds body from extracted text
       - Writes output TEI XML file
    """
    # Reassigned when the store adopts the log at the end.
    global RUN_LOG_FILE
    settings = get_settings()
    # -q silences chatter, never a warning: a mistyped --metadata is
    # reported by a logger, and hiding it converted a whole corpus with
    # placeholder headers and exited 0 with nothing said.
    # `level_asked` and not the recorded origin: -q resolving to the same
    # level as the default leaves the origin at "default", and `debug`
    # would then reinstall a DEBUG console over the quiet just typed.
    # Verify OCR directory exists
    # is_dir(), not exists(): -i now makes it easy to point the input at a
    # file, and iterdir() would then raise NotADirectoryError instead of
    # the exit 3 the contract promises.
    # Every path this run will READ, before it reads any of them. The
    # input directory was checked here and the two catalogues were not,
    # so a mistyped `TDOUCE_METADATA_CSV` warned once, converted
    # twenty-seven volumes with placeholder headers and exited 0 —
    # forty minutes to produce files nobody wants, reported as a
    # success. `teille-douce check` asks the same question in two
    # seconds; this is what happens when nobody asked.
    unreadable = settings.unreadable_inputs()
    if unreadable:
        for name, path, reason, where in unreadable:
            # `where` is now the layer that supplied the value, not the
            # first of the three names a setting answers to.
            console.print(f"[red]{escape(where)}: "
                          f"{escape(str(path))} {reason}.[/red]")
        _refuse(settings=settings)

    # Checked here rather than at mkdir time: -o naming an existing file
    # used to be discovered after expand_archives had unpacked the whole
    # corpus and both catalogues had been read.
    entities = settings.entities_dir.resolve()
    corpus = settings.ocr_dir.resolve()
    entities_asked = settings.origin("entities_dir") != "default"
    if entities_asked and (entities == corpus or corpus in entities.parents
                           or entities in corpus.parents):
        # A per-document failure removes that document's entity directory.
        # Pointing --entities at the corpus made that removal delete the
        # ALTO it had just read.
        console.print(
            f"[red]The entity directory must not overlap the input "
            f"directory[/red] — {escape(str(settings.entities_dir))} "
            f"({escape(settings.origin('entities_dir'))}) against "
            f"{escape(str(settings.ocr_dir))}"
        )
        _refuse(settings=settings)

    if settings.output_dir.exists() and not settings.output_dir.is_dir():
        console.print(
            f"[red]Not a directory:[/red] "
            f"{escape(str(settings.output_dir))} (--output)"
        )
        _refuse(settings=settings)

    # Create output directory
    dry_run = getattr(args, "dry_run", False)

    # Installed before anything logs. The handler creates neither its file
    # nor its directory until a record arrives, so a run that refuses to
    # start still leaves nothing behind — while the records emitted during
    # setup (a corrupt archive, a metadata CSV that is not there) reach the
    # log the summary points at, which they did not when this ran last.
    from teille_douce.cli import options as _options
    # Created here but written to lazily, so a run that refuses to start
    # still leaves nothing behind.
    store = None if dry_run else RunStore(settings.output_dir)
    configure_logging(
        settings,
        quiet=bool(getattr(args, "quiet", 0)),
        write=not dry_run,
        level_asked=(_options.asks_to_be_quieter(args)
                     or settings.origin("log_level") != "default"),
    )

    no_probe = getattr(args, "no_probe", False)
    require_services = getattr(args, "require_services", False)
    if no_probe and require_services:
        # One says "assume they answer", the other "prove they do". Checked
        # here, before expand_archives unpacks anything: --require-services
        # promises to fail before a single write.
        console.print(
            "[red]--no-probe and --require-services contradict each other.[/red]"
        )
        # 2, like every other pair of contradictory flags. `--force
        # --skip-existing` and `--plain --dashboard` both exit 2, and
        # this one exited 3 — so a wrapper keying on 2 for "the command
        # line is wrong" got both answers for the same kind of mistake.
        _refuse(code=EXIT_USAGE, settings=settings)

    # Cheap, and before the probes: a typo used to pay up to two health
    # timeouts of blocking HTTP before being told it was a typo. Names are
    # taken from the directory listing, so nothing has to be extracted to
    # know a selector matches something.
    selectors = getattr(args, "documents", []) or []
    exclusions_asked = getattr(args, "exclude", None) or []

    if getattr(args, "retry_failed", False):
        # The remedy the summary offers. Read from the last run's
        # manifest rather than retyped off the screen.
        try:
            selectors = list(RunStore.failed_last_time(settings.output_dir))
        except ValueError as reason:
            # Not "nothing failed": that would send the operator away
            # believing the last run was clean.
            console.print(f"[red]--retry-failed: {escape(str(reason))}[/red]")
            _refuse(settings=settings)
        if not selectors:
            # Not "a selector matched nothing": there is no selector, and
            # the previous run simply had nothing to retry.
            console.print("[green]--retry-failed: nothing failed last "
                          "time.[/green]")
            _refuse(settings=settings)
        say(f"[dim]--retry-failed: {len(selectors)} volume(s) from the "
            f"last run[/dim]")

    def _selected(name):
        return bool(select_documents([(name, [], None)],
                                     selectors, exclusions_asked, None)[0])

    if selectors or exclusions_asked:
        candidates = [(entry.name if entry.is_dir() else entry.stem, [], None)
                      for entry in settings.ocr_dir.iterdir()
                      if entry.is_dir() or entry.suffix == ".zip"]
        stray = [
            pattern for pattern in (*selectors, *exclusions_asked)
            if not select_documents(candidates, [pattern], [], None)[0]
        ]
        if stray:
            console.print("[red]No volume matches:[/red] " + escape(", ".join(stray)))
            _refuse(settings=settings)

    config_origin = settings.origin("__config__")
    if config_origin != "default":
        # Named in every run, not only in the plan: a teille-douce.toml
        # found by walking up can redirect paths.output, and an operator
        # who has forgotten it has nothing to read.
        # console.print, not say(): a file found by walking up can
        # redirect paths.output, and -q silences chatter, never a fact the
        # operator needs to explain where the output went.
        console.print(f"[dim]Reading {escape(config_origin)}[/dim]")

    unavailable = []

    # Check modernization API availability
    do_modernize = False
    if settings.modernize:
        from teille_douce.modernize import check_api as check_modernize_api
        if no_probe or check_modernize_api():
            do_modernize = True
            say("[green]Modernization API (VieuxParler) available.[/green]")
        else:
            unavailable.append("VieuxParler (modernization)")
            console.print("[yellow]Warning: Modernization API unreachable — continuing without modernization.[/yellow]")

    # Check linguistic enrichment API availability
    do_enrich = False
    if settings.enrich:
        from teille_douce.enrichment.client import check_server as check_enrichment_api
        if no_probe or check_enrichment_api():
            do_enrich = True
            say("[green]Enrichment API (PyHellen) available.[/green]")
        else:
            unavailable.append("PyHellen (enrichment)")
            console.print("[yellow]Warning: Enrichment API unreachable — continuing without linguistic annotation.[/yellow]")

    # NER has no service to probe, but it has dependencies that are often
    # absent — and without this the header declared entity recognition on
    # every file of a run that produced not one <persName>.
    #
    # The packages are named rather than imported: every heavy import in
    # ner_models.py is deferred into a method body, so importing the
    # pipeline module succeeds with none of them installed and a probe
    # written that way could never fire.
    do_ner = settings.ner
    if do_ner:
        missing = missing_ner_dependencies()
        if missing:
            do_ner = False
            unavailable.append("NER models")
            console.print(
                f"[yellow]Warning: NER dependencies not installed "
                f"({escape(', '.join(missing))}) — no entity recognition."
                f"[/yellow]"
            )
        else:
            # Where they will run, before several gigabytes of model load:
            # "auto" on a shared GPU silently means "whatever is left", and
            # a run that fell back to CPU is a run that will take hours
            # rather than minutes. Worth one line.
            from teille_douce.enrichment.ner_models import resolve_device

            device = resolve_device()
            asked = settings.origin("ner_device")
            say(f"[green]Entity recognition on {escape(device)}[/green]"
                + ("" if asked == "default"
                   else f" [dim]({escape(asked)})[/dim]"))

    if require_services and unavailable:
        # Asked for explicitly: a phase whose service is down is fatal
        # BEFORE anything is written, rather than a whole corpus quietly
        # converted without its annotations.
        console.print(
            "[bold red]--require-services:[/bold red] "
            + escape(", ".join(unavailable))
            + " unreachable; nothing written."
        )
        _refuse(settings=settings)

    # Extract ZIP archives (corrupt ones are skipped and reported)
    # --limit deliberately does NOT bound extraction. Ordering it by name
    # would let a corrupt archive, sorted first, eat the whole budget and
    # convert nothing; and whether an archive yields a volume is not
    # knowable without unpacking it. The limit caps conversions, not
    # discovery. Selectors do bound it, because they are name-based.
    unpacked_skips = []

    def _only_selected(name):
        return bool(select_documents([(name, [], None)], selectors, [], None)[0])

    def _worth_unpacking(name):
        # Unlike --limit, the resume predicate is name-based and knowable
        # without unpacking: a resumed corpus used to extract every
        # archive and only then skip it, minutes and gigabytes of pure
        # waste on a multi-volume rerun. What is skipped here is counted,
        # or a finished archive corpus would look empty.
        if (selectors or exclusions_asked) and not _selected(name):
            return False
        if (settings.skip_existing
                and _out_path(name, settings.output_dir).exists()):
            # Counted here ONLY when this archive is the sole trace of the
            # volume. With an extracted directory beside it the directory
            # pass keeps it and select_documents counts the skip; in a dry
            # run `skipped_archives` does. Counting in both places
            # announced "2 documents already converted" for one volume.
            extracted = settings.ocr_dir / name
            if not dry_run and not extracted.is_dir():
                # Counted here only when this archive is the volume's ONLY
                # trace. The directory pass now hands on every directory,
                # ALTO or not, and the caller counts each one exactly
                # once — as a document, as a skip, or as a volume that
                # held no ALTO. Testing for *.xml as well, as this did
                # while the directory pass still filtered on it, made an
                # empty directory with its archive still beside it arrive
                # in two of those three at the same time: one volume,
                # counted twice in one summary line.
                unpacked_skips.append(name)
            return False
        return True

    ready_dirs, failed_archives = expand_archives(
        settings.ocr_dir, extract=not dry_run, wanted=_worth_unpacking,
        # Selectors only. An exclusion has to reach select_documents,
        # which is what tells a matched `-x` from a mistyped one: dropping
        # the volume here made every working `-x` report "No volume
        # matches" and exit 3, the documented example included.
        keep=_only_selected if selectors else None,
    )

    # Archives --dry-run did not unpack are prospective work, not absent
    # documents: they are a documented input layout, and the plan has to
    # be right precisely before the first run.
    pending_archives, skipped_archives = [], 0
    if dry_run:
        for zip_path in sorted(settings.ocr_dir.glob("*.zip")):
            if (settings.ocr_dir / zip_path.stem).exists():
                continue
            # Selection first: counting before it reported a volume the
            # user never selected as "already converted".
            if (selectors or exclusions_asked) and not _selected(zip_path.stem):
                continue
            # A skipped archive is counted rather than dropped, or the plan
            # says nothing at all about a volume already converted — and a
            # finished, still-archived corpus read as a misconfiguration.
            if (settings.skip_existing
                    and _out_path(zip_path.stem, settings.output_dir).exists()):
                skipped_archives += 1
                continue
            pending_archives.append(zip_path.name)


    if selectors or exclusions_asked:
        failed_archives = [
            (name, reason) for name, reason in failed_archives
            if _selected(Path(name).stem)
        ]
        pending_archives = [n for n in pending_archives if _selected(Path(n).stem)]

    # Names the selectors could legitimately match, whether or not they
    # produced a directory to walk.
    # Collect documents to process
    docs, without_alto, unreadable, unreadable_skips = [], [], [], []
    for d in ready_dirs:
        # Regular files only. A FIFO named `f1.xml` in a volume blocked
        # `etree.parse` in the main process, so the run never returned
        # and nothing reached the screen — the worst way for a command
        # to fail, and the same shape as a mapping CSV that is a FIFO.
        # `is_file` follows the symlink and answers no to a FIFO, a
        # directory and a broken link alike.
        xmls = sorted(page for page in d.rglob("*.xml") if page.is_file())
        if xmls:
            docs.append((d.name, xmls, d))
        elif not _selected(d.name):
            continue
        elif not _is_readable(d):
            # rglob answers "no ALTO" for a directory it is not allowed to
            # open, which is a different thing and sends the operator to
            # repack a volume whose only problem is its permissions.
            if (settings.skip_existing
                    and _out_path(d.name, settings.output_dir).exists()):
                # Already converted, so its mode does not matter: this is
                # what --skip-existing means, and it is what an archive in
                # the same position gets. Failing here would turn a
                # nightly wrapper permanently red over a volume there is
                # nothing left to do to.
                unreadable_skips.append(d.name)
            else:
                unreadable.append(d.name)
        else:
            # A directory that is there and holds no ALTO is a defect of
            # the source, not an absence — and it used to fall out of
            # `docs` with no counter, no log line and no summary mention.
            # A volume whose ALTO subfolder was left out of its archive
            # disappeared from a two-hundred-volume run without a word,
            # and the run still exited 0 claiming every document it had
            # kept was every document there was.
            without_alto.append(d.name)

    # Built once and read everywhere a volume is accounted for. Adding
    # `unreadable` to two of the four such places, and not the other two,
    # is how a volume came to vanish from a resumed run and how the
    # never-attempted count went wrong: four places is three too many for
    # a list that has to agree with itself.
    broken = list(failed_archives) + [
        (name, "directory could not be read") for name in sorted(unreadable)
    ]

    if unreadable:
        console.print(
            f"[yellow]Warning: {len(unreadable)} "
            f"director{'y' if len(unreadable) == 1 else 'ies'} could not be "
            f"read, so nothing was converted from "
            f"{'it' if len(unreadable) == 1 else 'them'}:[/yellow] "
            f"{escape(', '.join(sorted(unreadable)))}"
        )
        for name in sorted(unreadable):
            logging.getLogger(__name__).warning(
                "%s: cannot list this directory; whatever ALTO it holds was "
                "not read", name,
            )

    if without_alto:
        console.print(
            f"[yellow]Warning: {len(without_alto)} "
            f"director{'y' if len(without_alto) == 1 else 'ies'} with no ALTO "
            f"file, nothing to convert:[/yellow] "
            f"{escape(', '.join(sorted(without_alto)))}"
        )
        for name in sorted(without_alto):
            logging.getLogger(__name__).warning(
                "%s: no *.xml under this directory; nothing was converted "
                "from it", name,
            )

    everything_excluded = bool(selectors or exclusions_asked) and not any(
        _selected(entry.name if entry.is_dir() else entry.stem)
        for entry in settings.ocr_dir.iterdir()
        if entry.is_dir() or entry.suffix == ".zip"
    )
    # Every bucket that means "there IS something here", in one place.
    # Naming them one by one in a boolean chain is what let the newest of
    # them be forgotten twice running: a resumed corpus whose only volume
    # was skipped unread answered "No ALTO documents found" and exited 3,
    # the code that tells a wrapper to go and fix its own configuration.
    resumed = skipped_archives + len(unpacked_skips) + len(unreadable_skips)
    if not docs and not pending_archives and not resumed \
            and not everything_excluded:
        # The directory actually configured, not the literal "OCR/": the
        # message used to name a path the run was not reading.
        if broken:
            # Not a misconfiguration: the input was there and unreadable.
            # A directory this process may not open is that same case, so
            # it answers with the same exit code — 3 would have told a
            # wrapper to go and fix its own configuration.
            note = ("" if not without_alto else
                    f" ({len(without_alto)} more held no ALTO)")
            console.print(
                f"[bold red]No document could be read:[/bold red] "
                f"{len(broken)} volume(s) could not be opened.{note}"
            )
            for name, reason in broken:
                console.print(f"  [red]FAILED[/red] {escape(f'{name}: {reason}')}")
            # Indexed even here. The document loop never starts on this
            # path, so no reporter was ever built and `incidents.jsonl`
            # was simply absent: the run that failed hardest left the
            # least behind, and `--retry-failed` afterwards had a
            # manifest but no account of why.
            if store is not None:
                # Only the unreadable directories. A corrupt archive is
                # `Code.ARCHIVE_CORRUPT`, which is block 1 — the source
                # being defective — and `RunStore.incident` indexes block
                # 3 alone, by design: an index of everything is an index
                # of nothing. Calling it for an archive looked like
                # thoroughness and wrote nothing at all.
                #
                # The two diagnoses stay distinct for the same reason the
                # document loop keeps them apart: a corrupt archive is a
                # file to repack, a directory this process may not open
                # is a mode to change.
                packed = {name for name, _ in failed_archives}
                for name, reason in broken:
                    if name in packed:
                        continue
                    store.incident(Loss(
                        Code.VOLUME_UNREADABLE, name, "expand",
                        Locator.document(name), count=1, total=1,
                        detail=reason))
            # 4, not 1. Nothing was converted and everything that could
            # fail did — which is the distinction 4 exists to draw, and
            # the reason it exists: 1 tells a wrapper to retry volume by
            # volume, and there is no volume here worth retrying.
            _keep_the_record(store, settings, broken, EXIT_ALL_FAILED)
            sys.exit(EXIT_ALL_FAILED)
        # Whichever wording follows, say how many volumes were there and
        # empty. Exit 3 reads as "your input directory is wrong", which is
        # usually right — but not when the directory is the right one and
        # its volumes are the problem, and the count is what tells the two
        # apart at a glance.
        found = ("" if not without_alto else
                 f" {len(without_alto)} director"
                 f"{'y' if len(without_alto) == 1 else 'ies'} there held "
                 f"none.")
        if selectors:
            # The corpus may be perfectly healthy: it is the selected
            # volume that holds no ALTO, and naming the whole directory
            # sent the operator to inspect the wrong thing.
            console.print(
                "[red]No ALTO found in:[/red] "
                + escape(", ".join(selectors)) + f"[red].{found}[/red]"
            )
        else:
            console.print(
                f"[red]No ALTO documents found in "
                f"{escape(str(settings.ocr_dir))}.{found}[/red]"
            )
        _refuse(settings=settings)

    # Only directories that actually hold ALTO: an extracted archive with
    # no *.xml is not a volume a selector can match, and treating it as one
    # answered "every volume was excluded" when nothing had been.
    # Narrow to what was asked for. A selector that names nothing stops the
    # run: a typo must not look like an empty corpus.
    # Both filters live in one place, in the order that makes the pair
    # usable: skip what is already converted, then count.
    already_converted = (
        (lambda name: _out_path(name, settings.output_dir).exists())
        if settings.skip_existing else None
    )
    docs, skipped_existing = select_documents(
        docs, getattr(args, "documents", []) or [],
        getattr(args, "exclude", None) or [], getattr(args, "limit", None),
        skip=already_converted,
    )
    skipped_existing += (skipped_archives + len(unpacked_skips)
                         + len(unreadable_skips))
    if not docs and not broken and not pending_archives:
        if skipped_existing:
            # Resuming a corpus that is already complete is a success, not
            # a misconfiguration — it is the idiom the user guide
            # recommends for a nightly wrapper.
            # "every document" has to mean every document. A volume that
            # held no ALTO has no TEI output and never will, and saying
            # otherwise on the line right under the warning that named it
            # contradicts the warning.
            note = ("" if not without_alto else
                    f" ({len(without_alto)} more held no ALTO)")
            console.print(
                f"[bold green]Nothing to do:[/bold green] "
                f"{skipped_existing} document(s) already converted"
                f"{note}."
            )
            return
        console.print("[red]Every volume was excluded.[/red]")
        _refuse(settings=settings)

    if not docs and pending_archives:
        pass
    elif not docs:
        # Nothing left to convert, but an archive failed: that is a
        # failure to report, not a success to return. Exiting early here
        # let a nightly wrapper announce success forever while one archive
        # never converted.
        early = f"0/{len(broken)} documents converted"
        if skipped_existing:
            early += f" ({skipped_existing} more skipped, already converted)"
        if without_alto:
            early += f" ({len(without_alto)} more held no ALTO)"
        console.print(f"[bold yellow]Completed with errors:[/bold yellow] {early}")
        for name, reason in broken:
            console.print(f"  [red]FAILED[/red] {escape(f'{name}: {reason}')}")
        # The second path that leaves before the run loop. The record was
        # added to the first only, so this one still told the next
        # --retry-failed that nothing had failed.
        _keep_the_record(store, settings, broken, EXIT_SOME_FAILED)
        sys.exit(EXIT_SOME_FAILED)

    # Audit 2.5: minimal resume after a crash — skip already-converted docs
    if skipped_existing:
        say(
            f"[dim]--skip-existing: {skipped_existing} document(s) "
            f"already converted, skipped[/dim]"
        )
    if dry_run:
        # A real run extracts first, so an archive enters `ready_dirs`
        # sorted and competes for the limit in name order. Predicting it
        # means merging the two lists and sorting before counting, not
        # appending the archives at the end.
        planned = sorted(
            [(name, len(filepaths)) for name, filepaths, _ in docs]
            + [(Path(name).stem, None) for name in pending_archives]
        )
        limit = getattr(args, "limit", None)
        if limit is not None:
            planned = planned[:limit]
        console.print(
            f"\n[bold]Plan[/bold] — {len(planned)} volume(s), "
            f"{sum(pages or 0 for _, pages in planned)} pages"
            + (" (archived volumes not counted)"
               if any(pages is None for _, pages in planned) else "")
        )
        for name, pages in planned:
            detail = (f"{pages} pages" if pages is not None
                      else "still archived, pages unknown until it is unpacked")
            console.print(f"  {escape(name)}  [dim]{detail}[/dim]")
        # The probes ran above, so the plan states what would actually
        # happen rather than what was asked — the one command whose whole
        # job is to say what would happen must not contradict what it just
        # learned.
        phases = [n for n, on in (("enrich", do_enrich),
                                  ("modernize", do_modernize),
                                  ("ner", do_ner)) if on] or ["none"]
        console.print(
            f"[dim]  input {escape(str(settings.ocr_dir))} → output "
            f"{escape(str(settings.output_dir))} · phases "
            f"{', '.join(phases)} · {settings.max_workers} workers[/dim]"
        )
        origin = settings.origin("__config__")
        console.print(f"[dim]  config {escape(origin)}[/dim]" if origin != "default"
                      else "[dim]  no config file[/dim]")
        console.print("[dim]  nothing written (--dry-run)[/dim]")
        return

    # Load global metadata CSV
    df_meta = load_metadata(settings.metadata_csv)

    # Load person metadata database
    person_db = load_person_database(settings.persons_csv)
    if person_db:
        say(f"[dim]Loaded {len(person_db)} persons from {escape(str(settings.persons_csv))}[/dim]")
    else:
        console.print(
            f"[yellow]Warning: person metadata not loaded ({escape(str(settings.persons_csv))}) "
            f"— headers will keep placeholder person entries.[/yellow]"
        )

    # Created here and not earlier: every exit above this line means
    # nothing will be written, and leaving an empty directory behind after
    # refusing to run is a write like any other.
    try:
        settings.output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as reason:
        # -o makes it as easy to name an existing file as -i does, and the
        # input guard above already refuses that. Same answer here: exit 3,
        # not a traceback after the metadata have been loaded.
        console.print(
            f"[red]Cannot use {escape(str(settings.output_dir))} as an "
            f"output directory:[/red] {escape(str(reason))}"
        )
        _refuse(settings=settings)

    # Build pipeline configuration
    config = build_config()
    run_started = perf_counter()

    # Which reporter this run gets. Refused rather than guessed when the
    # two flags contradict: no reading of `--plain --dashboard` says what
    # was wanted.
    try:
        ui = choose_ui(
            asked=settings.ui,
            env=os.environ, is_tty=console.is_terminal,
            size=(console.width, console.height),
            plain=getattr(args, "plain", False),
            dashboard=getattr(args, "dashboard", False),
            quiet=getattr(args, "quiet", 0),
            dry_run=dry_run)
    except ValueError as reason:
        console.print(f"[red]{escape(str(reason))}[/red]")
        sys.exit(EXIT_USAGE)

    # Before the loop, so the forkserver is born at an instant when a
    # Ctrl-C has nothing to interrupt. Started lazily inside the loop it
    # inherited a live SIGINT handler and died mid-preload, printing a
    # multiprocessing traceback across the report.
    warm_up()

    # Process documents with progress bar
    # The release has to sit OUTSIDE the progress bar, not inside it.
    # `Progress.__exit__` is Rich stopping a Live and writing to the
    # console — the very thing that raises `BrokenPipeError` under
    # `| head` — and it runs between the hold taken in the loop's
    # `finally` and any release. Guarded one frame too far in, it
    # left the process deaf to Ctrl-C with no report written, which
    # is the state the guard was added to make unreachable.
    # Bound BEFORE the `try`, not inside it. Moving the guard out one
    # frame last round left this binding one frame in, so anything that
    # raised before it — `Progress()` starting a Live, the first
    # `store.incident` write hitting a full disk — reached the handler
    # whose first statement reads it and came out as an
    # `UnboundLocalError` with the real cause buried in `__context__`.
    # Which is the failure this stretch was guarded against in the first
    # place, reproduced one frame outside it.
    # Shared with the finishing section below, so a second Ctrl-C cannot
    # land between the two.
    interrupts = HeldInterrupts()
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("[green]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
            # A progress bar is chatter, and -q asks for none. The panel is
            # the same information, drawn better: two of them at once would
            # fight over the same lines.
            disable=_QUIET or ui is UI.DASHBOARD,
        ) as progress:

            task_docs = progress.add_task("Processing documents", total=len(docs))

            # One object both the panel and the journal read. Created here
            # because this is the first point at which the run knows what it
            # is about to attempt, which is what every denominator on the
            # summary is measured against.
            panel_holder = {}
            reporter_run = ReportRun(
                input_dir=settings.ocr_dir, output_dir=settings.output_dir,
                # For the `next` block alone: a command it offers must
                # name the catalogues this run was given, or it reports
                # "no catalogue row" for every volume.
                metadata_csv=settings.metadata_csv,
                persons_csv=settings.persons_csv,
                # `broken` and not `failed_archives`: an unreadable directory
                # is counted in the denominator everywhere else, and freezing
                # a different total here let the headline say "1/2 converted"
                # over a verdict saying "1 of 1" — the one disagreement the
                # shared record exists to prevent.
                volumes=len(docs) + len(broken),
                pages=sum(len(paths) for _, paths, _ in docs),
                log_path=RUN_LOG_FILE or settings.log_file,
                # The probe already answered for each of them. A panel that
                # does not say which services are up cannot show one dying,
                # and that is the case the whole design is built around.
                # None where the phase was never asked for, False where it
                # was and the probe said no: red for a service nobody wanted
                # would say the machine is broken.
                services=tuple(
                    Service(name, up=(up if asked else None))
                    for name, up, asked in (
                        ("PyHellen", do_enrich, settings.enrich),
                        ("VieuxParler", do_modernize, settings.modernize),
                        ("NER local", do_ner, settings.ner))),
                # Every change redraws, so a phase that has been waiting on a
                # service for forty seconds looks different from one that has
                # stopped — which is the state a four-hour run spends most of
                # its time in.
                on_change=lambda: (panel_holder["panel"].draw(
                    pages_per_second=_pages_per_second(reporter_run, run_started))
                    if panel_holder.get("panel") else None),
            )
            if store is not None:
                # Indexed as it happens. `execute` catches Exception, and a
                # KeyboardInterrupt is a BaseException that goes straight
                # through it — a Ctrl-C in the fourth hour used to take four
                # hours of knowledge with it.
                reporter_run.on_incident = store.incident
            for name, reason in failed_archives:
                reporter_run.archive_failed(name, reason)
            for name, reason in broken:
                if any(name == known for known, _ in reporter_run.failed_archives):
                    continue
                # Before the loop, like the archives: recorded after the panel
                # came down, an unreadable volume never reached the counts the
                # panel shows, so a finished run still read "1 to go" beside
                # "incident 0" over a summary reporting one.
                #
                # And filing it as a corrupt archive gave the wrong diagnosis
                # and an address that is a directory: the problem is
                # permissions, and repacking would not fix it.
                reporter_run.volume_unreadable(name, reason)

            # Per volume, because a share only means something against one
            # volume's own pages: 388 lost out of 400 is a file nobody wants
            # to publish, and out of 16 999 it is a normal Tuesday.
            pages_lost_per_document = {}

            ok_docs = []
            stopped_early = False
            interrupted = False
            # Entity directories this run brought into existence. A failure
            # cleans up only these, never a directory that was already there.
            entity_dirs_created, entity_files_before = set(), {}
            entity_snapshot_failed = set()
            # Corrupt archives belong in the summary and in the exit code, but
            # not in the failure budget: seeding the list with them made
            # --fail-fast stop after the first SUCCESSFUL document.
            failed_docs = []
            # Under a context manager, so the teardown happens whatever ends
            # the loop: a signal arrives at an arbitrary instruction, not
            # conveniently inside the one `try` this loop used to have.
            with panel_installed(reporter_run, ui is UI.DASHBOARD) as panel:
                panel_holder["panel"] = panel
                try:
                    for doc_name, filepaths, doc_dir in docs:
                        # Under the same guard as the conversion: a permissions change
                        # on an entity directory must not end a multi-hour run. And
                        # only when NER can write there — a --fast run walked a
                        # pre-existing directory once per volume for nothing.
                        entity_dir = settings.entities_dir / doc_name
                        entity_files_before[doc_name] = set()
                        if do_ner:
                            existing, created, reason = entity_snapshot(entity_dir)
                            entity_files_before[doc_name] = existing
                            if created:
                                entity_dirs_created.add(doc_name)
                            if reason is not None:
                                entity_snapshot_failed.add(doc_name)
                                logging.getLogger(__name__).warning(
                                    "%s: cannot inspect %s (%s); its entity files will "
                                    "be left alone if this document fails",
                                    doc_name, entity_dir, reason,
                                )
                        try:
                            reporter_run.document_started(doc_name, len(filepaths))
                            _process_document(
                                doc_name, filepaths, doc_dir, df_meta, config,
                                person_db, do_enrich, do_modernize, do_ner, progress,
                                reporter=reporter_run,
                            )
                        except Exception as e:
                            # Audit 2.1: one broken document must not kill the run.
                            # escape(): a doc name or error text containing [tag]-like
                            # substrings would corrupt or crash the Rich rendering.
                            logging.getLogger(__name__).error(
                                "Document %s failed: %s", doc_name, e, exc_info=True
                            )
                            console.print(f"[bold red]FAILED[/bold red] {escape(f'{doc_name}: {e}')}")
                            failed_docs.append((doc_name, str(e)))
                            reporter_run.document_finished(doc_name, ok=False,
                                                           reason=str(e))
                            # No orphan side effects: entity CSVs written before the
                            # failure would reference a TEI that was never produced
                            # Only what this run created. `entities_dir` is a setting
                            # now, so an unbounded rmtree here destroyed whatever the
                            # named directory happened to hold: `--entities OCR` plus
                            # any per-document failure deleted the source volume.
                            # Only when NER ran: with the phase off nothing was
                            # written there, so there is nothing of this run's to
                            # remove — and removing anything would be removing someone
                            # else's files.
                            stray = settings.entities_dir / doc_name
                            if may_remove_entity_files(
                                    stray,
                                    wrote_entities=do_ner,
                                    snapshot_failed=doc_name in entity_snapshot_failed,
                                    tei_exists=_out_path(
                                        doc_name, settings.output_dir).exists()):
                                if doc_name in entity_dirs_created:
                                    shutil.rmtree(stray, ignore_errors=True)
                                else:
                                    # A directory that was already there keeps what it
                                    # held; only the files this run wrote are removed,
                                    # or they would reference a TEI that does not
                                    # exist (audit 2.4).
                                    #
                                    # Guarded on its own: this runs inside the handler
                                    # that exists so one broken document cannot kill a
                                    # multi-hour run, and an OSError here — a
                                    # concurrent writer, a permission change, a
                                    # directory that gained a file between the walk
                                    # and the rmdir — would have escaped it.
                                    try:
                                        for path in sorted(stray.rglob("*"), reverse=True):
                                            if path in entity_files_before[doc_name]:
                                                continue
                                            if path.is_file():
                                                path.unlink(missing_ok=True)
                                            elif path.is_dir():
                                                path.rmdir()
                                    except OSError as cleanup_error:
                                        logging.getLogger(__name__).warning(
                                            "%s: could not remove the entity files of "
                                            "this failed document (%s)",
                                            doc_name, cleanup_error,
                                        )
                            # And no zombie progress rows left spinning forever
                            for tid in progress.task_ids:
                                if tid != task_docs:
                                    progress.update(tid, visible=False)
                        else:
                            ok_docs.append(doc_name)
                            reporter_run.document_finished(doc_name, ok=True)
                            if panel is not None:
                                panel.draw(pages_per_second=_pages_per_second(
                                    reporter_run, run_started))
                            pages_lost_per_document[doc_name] = (
                                reporter_run.pages_lost(doc_name), len(filepaths))
                        progress.advance(task_docs)

                        # Both asked for explicitly; the default is still to convert
                        # every volume, because per-document isolation is the contract.
                        if failed_docs and getattr(args, "fail_fast", False):
                            console.print("[yellow]--fail-fast: stopping at the first failure.[/yellow]")
                            stopped_early = True
                            break
                        max_failures = getattr(args, "max_failures", None)
                        if max_failures is not None and len(failed_docs) >= max_failures:
                            console.print(
                                f"[yellow]--max-failures {max_failures}: reached, stopping.[/yellow]"
                            )
                            stopped_early = True
                            break

                except KeyboardInterrupt:
                    # Caught where the report can still be written. It went
                    # straight through `except Exception` to the interpreter,
                    # so the summary — which lives outside this block — never
                    # ran, and four hours of knowledge left with the signal.
                    # First statement, before even the message: a second
                    # Ctrl-C arriving during that `console.print` propagated
                    # straight out of `execute`, past the `finally` below
                    # that would have held it. Idempotent, so the `finally`
                    # can hold again for the paths that never come here.
                    interrupts.hold()
                    interrupted = True
                    console.print(
                        "\n[yellow]Interrupted — finishing the report for "
                        "what was already written.[/yellow]")
                finally:
                    panel_holder["panel"] = None
                    # Held here and not in the handler above: the handler is
                    # only reached when a FIRST interrupt arrived, so a run
                    # that finished cleanly went through the panel's
                    # teardown, the accounting and the gate unprotected — and
                    # a single Ctrl-C in that stretch lost the run directory,
                    # the manifest and the summary alike. This `finally` runs
                    # on every way out of the loop, and it is above the
                    # teardown, which happens as the `with` below it exits.
                    interrupts.hold()
    except BaseException:
        interrupts.release()
        raise
    # Ctrl-C held from the loop's own `finally` above to the end of
    # the run, and released here. Everything in between — the
    # panel's teardown, the accounting, the gate, the manifest, the
    # summary — is the stretch a second interrupt used to throw
    # away, and the stretch a FIRST one threw away on a run that
    # had finished cleanly.
    with finishing(interrupts):
        # Audit 2.10: end-of-run summary + non-zero exit code on failures
        failed_docs = failed_docs + broken
        total = len(docs) + len(broken)
        converted = f"{len(ok_docs)}/{total} documents converted"
        if skipped_existing:
            converted += f" ({skipped_existing} more skipped, already converted)"
        if without_alto:
            # In the summary and not only in the warning above it: the summary
            # line is what a nightly wrapper reads, and a volume that yielded
            # no ALTO was absent from both sides of the fraction.
            converted += (f" ({len(without_alto)} more held no ALTO)")
        # Against `broken` and not `failed_archives`: everything in it failed
        # WITHOUT being one of `docs`, so counting only the archives left the
        # unreadable volumes subtracted from a set they were never in, and the
        # volumes a --fail-fast never reached went unreported.
        never_tried = len(docs) - len(ok_docs) - (len(failed_docs) - len(broken))
        if stopped_early and never_tried > 0:
            # Every phase reports what it lost: a run stopped after one failure
            # out of forty must not read as thirty-nine silent successes.
            converted += f" ({never_tried} never attempted, the run stopped early)"
        # 1 covered "one volume broke" and "all forty broke" alike. A wrapper
        # can now tell a partial failure, worth retrying volume by volume,
        # from a total one, which usually means the input or the setup is
        # wrong.
        # "Everything" has to mean everything: a --fail-fast that stopped
        # after the first volume did not prove the corpus is unconvertible,
        # and reporting a total failure would send the operator to check a
        # configuration that is fine.
        if interrupted:
            # 130 is what a shell reports for SIGINT, and it says something
            # no other code says: this is not a verdict on the corpus.
            exit_code = EXIT_INTERRUPTED
        elif failed_docs and not ok_docs and not never_tried:
            exit_code = EXIT_ALL_FAILED
        elif failed_docs:
            exit_code = EXIT_SOME_FAILED
        else:
            exit_code = EXIT_OK

        # Only when nothing else failed: a failed volume is the more concrete
        # fact and takes the code. The two are worth separating because the
        # remedy differs — 1 is rerun with --retry-failed, 5 is look at what
        # was lost and decide whether you accept it.
        # An interrupted run is not a judgement on quality: it did not finish
        # looking.
        verdict = gate_verdict("never" if interrupted else settings.fail_on,
                               reporter_run.record)
        too_lossy = page_loss_failures(pages_lost_per_document,
                                       settings.max_page_loss)
        if exit_code == EXIT_OK and (not verdict.met or too_lossy):
            exit_code = EXIT_GATE_NOT_MET

        headline = ("Interrupted: " + converted if interrupted
                    else f"Completed with errors: {converted}" if failed_docs
                    else f"Done. {converted}")

        if store is not None:
            # Moved now rather than written there from the start: the run
            # directory lives under the output directory, and creating it
            # while the run might still refuse would break the promise that
            # exit 3 leaves nothing behind. Only a log nobody named: a
            # --log-file is an instruction.
            if RUN_LOG_FILE and settings.origin("log_file") == "default":
                RUN_LOG_FILE = store.adopt_log(RUN_LOG_FILE)
            store.finish(argv=["teille-douce", *sys.argv[1:]],
                         settings=settings.as_manifest(),
                         # The stem, because every selector matches a stem:
                         # writing `LIV9003_reconciled.zip` made the
                         # --retry-failed the summary offers answer "No
                         # volume matches" and exit 3.
                         documents={**{Path(name).stem: "ok" for name in ok_docs},
                                    **{Path(name).stem: "failed"
                                       for name, _ in failed_docs}},
                         exit_code=exit_code)
            RunStore.prune(settings.output_dir, spare=store.path)
            if store.unwritable:
                # Said once, and said: a report nobody can find later is a
                # report that was not written, and silence about it is worse
                # than the failure.
                console.print(f"[yellow]No run record kept: "
                              f"{escape(store.problems[0])}[/yellow]")

        outcome = reporter_run.finished(exit_code=exit_code, headline=headline,
                                        elapsed=perf_counter() - run_started,
                                        fail_on=settings.fail_on,
                                        page_loss_failures=too_lossy,
                                        max_page_loss=settings.max_page_loss,
                                        report_path=(store.path if store is not None
                                                     and not store.unwritable
                                                     else None))
        console.print("")
        for line in render_summary(outcome, width=console.width):
            console.print(escape(line), highlight=False)
        if failed_docs and RUN_LOG_FILE:
            console.print(f"[dim]Tracebacks in {escape(str(RUN_LOG_FILE))}[/dim]")
        if exit_code:
            sys.exit(exit_code)

