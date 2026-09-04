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
import re
import warnings
from fnmatch import fnmatch
import shutil
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter
from zipfile import BadZipFile, ZipFile

from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.console import Console
from rich.markup import escape

# Import configuration
from teille_douce.config import APP_VERSIONS, IIIF_URI, RESPONSIBILITY
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
    now writes its own file (e.g. pipeline_20260828_093000.log).
    """
    return base_path.with_name(f"{base_path.stem}_{now:%Y%m%d_%H%M%S}{base_path.suffix}")


def configure_logging(settings, now=None, quiet=False, write=True,
                      level_asked=False):
    """Install this run's handlers. Returns the run's log file, or None."""
    global RUN_LOG_FILE, _QUIET

    # -q asks for a quiet console; `debug` gates the diagnostics that also
    # go to the run log. They answer different questions, so a wrapper that
    # sets TDOUCE_DEBUG must not lose the -q it just typed.
    _QUIET = quiet

    handlers = []
    try:
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
    if RUN_LOG_FILE and not write:
        # --dry-run writes nothing, and a log directory is a write.
        RUN_LOG_FILE = None
    if RUN_LOG_FILE:
        # --log-file is a user-facing setting now, so its directory may not
        # exist: without this every record raised FileNotFoundError inside
        # logging and buried the console in tracebacks, while the summary
        # still pointed at a file nothing had created.
        try:
            RUN_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        except OSError as reason:
            # A log is a diagnostic, not the job: an unusable path costs the
            # log, not the conversion. Said on stderr, since the console is
            # not configured yet.
            warnings.warn(
                f"cannot write the run log to {RUN_LOG_FILE}: {reason} — "
                "continuing without file logging",
                RuntimeWarning, stacklevel=2,
            )
            RUN_LOG_FILE = None
    if RUN_LOG_FILE:
        # delay=True: the file is only created at the first record, so a run
        # that dies before logging anything leaves no empty file behind.
        file_handler = logging.FileHandler(
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


def expand_archives(ocr_dir, extract=True, wanted=None):
    """
    Extract ZIP archives in the OCR directory.

    Automatically extracts any ZIP files found in the OCR directory
    into subdirectories with the same name as the archive. A corrupt
    archive is skipped and reported instead of killing the run
    (audit 2.2).

    Args:
        ocr_dir (Path): Path to the OCR directory.
        extract (bool): unpack, or merely report what would be unpacked.
        wanted (callable): name -> True when this archive is in scope.
            Unpacking every archive to convert one named volume was the
            same waste the input and output guards were tightened against.

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

    # Include existing directories with XML files (never the temporary
    # ".extracting" directories of an interrupted run)
    for d in ocr_dir.iterdir():
        if d.is_dir() and not d.name.endswith(".extracting"):
            if any(d.rglob("*.xml")):
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
EXIT_MISCONFIGURED = 3


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
        tuple: (kept, unmatched, skipped) — unmatched holds the selectors
        that named nothing, because a typo must not look like an empty
        corpus; skipped counts what the resume predicate removed, and
        nothing else. Counting every kind of drop announced volumes as
        "already converted" that a selector or a limit had dropped.
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

    kept, unmatched = list(docs), []
    if selectors:
        kept = [d for d in docs if any(matches(p, d[0]) for p in selectors)]
        unmatched = [p for p in selectors
                     if not any(matches(p, d[0]) for d in docs)]
    for pattern in exclusions or []:
        if not any(matches(pattern, d[0]) for d in docs):
            # Same rule as a selector: `-x LIV0038_reconcilied` converted
            # at full cost the volume it was meant to hold back.
            unmatched.append(pattern)
        kept = [d for d in kept if not matches(pattern, d[0])]
    skipped = 0
    if skip is not None:
        before_skip = len(kept)
        kept = [d for d in kept if not skip(d[0])]
        skipped = before_skip - len(kept)
    if limit is not None:
        kept = kept[:limit]
    return kept, unmatched, skipped


def _process_document(doc_name, filepaths, doc_dir, df_meta, config,
                      person_db, do_enrich, do_modernize, do_ner, progress):
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
        progress=progress,
        parent_task_pages=task_pages,
    )

    # Unusable pages must be loud: a document with no page at all is a
    # failure, a partial one is flagged in the console and the summary.
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
    tree.build_body(detect_lang=True)
    link_notes_to_lines(tree.root)
    progress.update(task_lang, visible=False)

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

        def _enrich_progress(current, total):
            progress.update(task_enrich, completed=current, total=total)

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

    # Step 4: Text modernization (applied after enrichment)
    if do_modernize:
        task_mod = progress.add_task(
            f"[cyan]{escape(doc_name)}: Modernisation du texte[/cyan]", total=None, visible=True
        )

        def _mod_progress(current, total):
            progress.update(task_mod, completed=current, total=total)

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

    # Step 5: Named Entity Recognition (after enrichment + modernization)
    if do_ner:
        task_ner = progress.add_task(
            f"[cyan]{escape(doc_name)}: Reconnaissance d'entites nommees[/cyan]",
            total=None,
            visible=True,
        )

        try:
            from teille_douce.enrichment.ner_pipeline import run_ner, summarize

            resolved = run_ner(tree.root, person_db, doc_name)
            summary = summarize(resolved)
            if summary:
                say(f"  [dim]NER: {summary}[/dim]")

        except ImportError as e:
            console.print(
                f"[yellow]Warning: NER dependencies not installed "
                f"({escape(str(e))}) — skipping NER.[/yellow]"
            )
        except Exception as e:
            logging.getLogger(__name__).error("NER pipeline failed: %s", e, exc_info=True)
            console.print(f"[yellow]Warning: NER failed ({escape(str(e))}) — continuing.[/yellow]")

        progress.update(task_ner, visible=False)

    # Override TEI header with CSV metadata
    override_teiheader_from_csv(tree.root, row, doc_name)

    # Finalize langUsage with detected languages (after CSV override)
    tree.finalize_langusage()

    # Volumetry, once every phase that could add tokens has run
    tree.finalize_extent()

    # Write output file
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
    if not settings.ocr_dir.is_dir():
        console.print(f"[red]Directory not found: {escape(str(settings.ocr_dir))}[/red]")
        sys.exit(EXIT_MISCONFIGURED)

    # Checked here rather than at mkdir time: -o naming an existing file
    # used to be discovered after expand_archives had unpacked the whole
    # corpus and both catalogues had been read.
    if settings.output_dir.exists() and not settings.output_dir.is_dir():
        console.print(
            f"[red]Not a directory:[/red] "
            f"{escape(str(settings.output_dir))} (--output)"
        )
        sys.exit(EXIT_MISCONFIGURED)

    # Create output directory
    dry_run = getattr(args, "dry_run", False)

    no_probe = getattr(args, "no_probe", False)
    require_services = getattr(args, "require_services", False)
    if no_probe and require_services:
        # One says "assume they answer", the other "prove they do". Checked
        # here, before expand_archives unpacks anything: --require-services
        # promises to fail before a single write.
        console.print(
            "[red]--no-probe and --require-services contradict each other.[/red]"
        )
        sys.exit(EXIT_MISCONFIGURED)

    # Cheap, and before the probes: a typo used to pay up to two health
    # timeouts of blocking HTTP before being told it was a typo. Names are
    # taken from the directory listing, so nothing has to be extracted to
    # know a selector matches something.
    selectors = getattr(args, "documents", []) or []
    exclusions_asked = getattr(args, "exclude", None) or []

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
            sys.exit(EXIT_MISCONFIGURED)

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

    if require_services and unavailable:
        # Asked for explicitly: a phase whose service is down is fatal
        # BEFORE anything is written, rather than a whole corpus quietly
        # converted without its annotations.
        console.print(
            "[bold red]--require-services:[/bold red] "
            + escape(", ".join(unavailable))
            + " unreachable; nothing written."
        )
        sys.exit(EXIT_MISCONFIGURED)

    # Extract ZIP archives (corrupt ones are skipped and reported)
    ready_dirs, failed_archives = expand_archives(
        settings.ocr_dir, extract=not dry_run,
        wanted=_selected if (selectors or exclusions_asked) else None,
    )

    # Archives --dry-run did not unpack are prospective work, not absent
    # documents: they are a documented input layout, and the plan has to
    # be right precisely before the first run.
    pending_archives, skipped_archives = [], 0
    if dry_run:
        for zip_path in sorted(settings.ocr_dir.glob("*.zip")):
            if (settings.ocr_dir / zip_path.stem).exists():
                continue
            # The plan applies the filters the run would — but a skipped
            # archive is counted rather than dropped, or the plan says
            # nothing at all about a volume that is already converted.
            if (settings.skip_existing
                    and _out_path(zip_path.stem, settings.output_dir).exists()):
                skipped_archives += 1
                continue
            pending_archives.append(zip_path.name)

    # Selection has to see the archives too. It only ever saw extracted
    # directories, so naming a volume whose archive failed to extract was
    # reported as a typo — exit 3, "no volume matches" — and the failure
    # itself never reached the summary or the exit code.
    selectors = getattr(args, "documents", []) or []

    if selectors or exclusions_asked:
        failed_archives = [
            (name, reason) for name, reason in failed_archives
            if _selected(Path(name).stem)
        ]
        pending_archives = [n for n in pending_archives if _selected(Path(n).stem)]

    # Names the selectors could legitimately match, whether or not they
    # produced a directory to walk.
    # Collect documents to process
    docs = []
    for d in ready_dirs:
        xmls = sorted(d.rglob("*.xml"))
        if xmls:
            docs.append((d.name, xmls, d))

    if not docs and not pending_archives:
        # The directory actually configured, not the literal "OCR/": the
        # message used to name a path the run was not reading.
        if failed_archives:
            # Not a misconfiguration: the input was there and unreadable.
            console.print(
                f"[bold red]No document could be read:[/bold red] "
                f"{len(failed_archives)} archive(s) failed to extract."
            )
            for name, reason in failed_archives:
                console.print(f"  [red]FAILED[/red] {escape(f'{name}: {reason}')}")
            sys.exit(EXIT_SOME_FAILED)
        console.print(
            f"[red]No ALTO documents found in {escape(str(settings.ocr_dir))}.[/red]"
        )
        sys.exit(EXIT_MISCONFIGURED)

    # Only directories that actually hold ALTO: an extracted archive with
    # no *.xml is not a volume a selector can match, and treating it as one
    # answered "every volume was excluded" when nothing had been.
    known = ({name for name, _, _ in docs}
             | {Path(n).stem for n, _ in failed_archives}
             | {Path(n).stem for n in pending_archives})

    # Narrow to what was asked for. A selector that names nothing stops the
    # run: a typo must not look like an empty corpus.
    # Both filters live in one place, in the order that makes the pair
    # usable: skip what is already converted, then count.
    already_converted = (
        (lambda name: _out_path(name, settings.output_dir).exists())
        if settings.skip_existing else None
    )
    docs, unmatched, skipped_existing = select_documents(
        docs, getattr(args, "documents", []) or [],
        getattr(args, "exclude", None) or [], getattr(args, "limit", None),
        skip=already_converted,
    )
    # A selector that named an archive matched something real, even if that
    # something failed or is still zipped.
    unmatched = [
        selector for selector in unmatched
        if not any(select_documents([(name, [], None)], [selector], [], None)[0]
                   for name in known)
    ]
    if unmatched:
        console.print(
            "[red]No volume matches:[/red] "
            + escape(", ".join(unmatched))
        )
        sys.exit(EXIT_MISCONFIGURED)
    skipped_existing += skipped_archives
    if not docs and not failed_archives and not pending_archives:
        if skipped_existing:
            # Resuming a corpus that is already complete is a success, not
            # a misconfiguration — it is the idiom the user guide
            # recommends for a nightly wrapper.
            console.print(
                "[bold green]Nothing to do:[/bold green] every document "
                "already has a TEI output."
            )
            return
        console.print("[red]Every volume was excluded.[/red]")
        sys.exit(EXIT_MISCONFIGURED)

    if not docs and pending_archives:
        pass
    elif not docs:
        # Nothing left to convert, but an archive failed: that is a
        # failure to report, not a success to return. Exiting early here
        # let a nightly wrapper announce success forever while one archive
        # never converted.
        early = f"0/{len(failed_archives)} documents converted"
        if skipped_existing:
            early += f" ({skipped_existing} more skipped, already converted)"
        console.print(f"[bold yellow]Completed with errors:[/bold yellow] {early}")
        for name, reason in failed_archives:
            console.print(f"  [red]FAILED[/red] {escape(f'{name}: {reason}')}")
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

    # Configured here and not at the top: everything above this line can
    # still refuse to run, and a log directory left behind after refusing
    # is a write like any other.
    from teille_douce.cli import options as _options
    configure_logging(
        settings,
        quiet=bool(getattr(args, "quiet", 0)),
        write=not dry_run,
        # A level set by the environment or the config file was asked for
        # too: `debug` used to install a DEBUG console over an explicit
        # TDOUCE_LOG_LEVEL=ERROR with nothing said.
        level_asked=(_options.asks_to_be_quieter(args)
                     or settings.origin("log_level") != "default"),
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
        sys.exit(EXIT_MISCONFIGURED)

    # Build pipeline configuration
    config = build_config()

    # Process documents with progress bar
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[green]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
        # A progress bar is chatter, and -q asks for none.
        disable=_QUIET,
    ) as progress:

        task_docs = progress.add_task("Processing documents", total=len(docs))

        ok_docs = []
        stopped_early = False
        # Corrupt archives belong in the summary and in the exit code, but
        # not in the failure budget: seeding the list with them made
        # --fail-fast stop after the first SUCCESSFUL document.
        failed_docs = []
        for doc_name, filepaths, doc_dir in docs:
            try:
                _process_document(
                    doc_name, filepaths, doc_dir, df_meta, config,
                    person_db, do_enrich, do_modernize, do_ner, progress,
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
                # No orphan side effects: entity CSVs written before the
                # failure would reference a TEI that was never produced
                if not _out_path(doc_name, settings.output_dir).exists():
                    shutil.rmtree(settings.entities_dir / doc_name, ignore_errors=True)
                # And no zombie progress rows left spinning forever
                for tid in progress.task_ids:
                    if tid != task_docs:
                        progress.update(tid, visible=False)
            else:
                ok_docs.append(doc_name)
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

    # Audit 2.10: end-of-run summary + non-zero exit code on failures
    failed_docs = failed_docs + list(failed_archives)
    total = len(docs) + len(failed_archives)
    converted = f"{len(ok_docs)}/{total} documents converted"
    if skipped_existing:
        converted += f" ({skipped_existing} more skipped, already converted)"
    never_tried = len(docs) - len(ok_docs) - (len(failed_docs) - len(failed_archives))
    if stopped_early and never_tried > 0:
        # Every phase reports what it lost: a run stopped after one failure
        # out of forty must not read as thirty-nine silent successes.
        converted += f" ({never_tried} never attempted, the run stopped early)"
    if failed_docs:
        console.print(f"\n[bold yellow]Completed with errors:[/bold yellow] {converted}")
        for name, reason in failed_docs:
            console.print(f"  [red]FAILED[/red] {escape(f'{name}: {reason}')}")
        if RUN_LOG_FILE:
            console.print(f"[dim]Tracebacks in {escape(str(RUN_LOG_FILE))}[/dim]")
        sys.exit(EXIT_SOME_FAILED)

    console.print(f"\n[bold green]Done.[/bold green] {converted}")

