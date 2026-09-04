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
from teille_douce.settings import get_settings

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


def configure_logging(settings, now=None):
    """Install this run's handlers. Returns the run's log file, or None."""
    global RUN_LOG_FILE, _QUIET

    _QUIET = (not settings.debug
              and getattr(logging, settings.log_level) >= logging.ERROR)

    handlers = []
    RUN_LOG_FILE = (
        _run_log_path(Path(settings.log_file), now or datetime.now())
        if settings.log_file else None
    )
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
    console_handler.setLevel(
        logging.DEBUG if settings.debug else getattr(logging, settings.log_level)
    )
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
from teille_douce.teiheader import build_header
from teille_douce.metadata import (load_metadata,
                          find_metadata_row,
                          build_metadata_dict,
                          override_teiheader_from_csv,
                          load_person_database,
                          select_manifest)
from teille_douce.utils import write_xml
from teille_douce.utils.files import parse_document_id


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


def expand_archives(ocr_dir, extract=True):
    """
    Extract ZIP archives in the OCR directory.

    Automatically extracts any ZIP files found in the OCR directory
    into subdirectories with the same name as the archive. A corrupt
    archive is skipped and reported instead of killing the run
    (audit 2.2).

    Args:
        ocr_dir (Path): Path to the OCR directory.

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
        target = ocr_dir / zip_path.stem
        if not target.exists() and not extract:
            # --dry-run: say what would happen, unpack nothing.
            say(f"[dim]Would extract: {escape(zip_path.name)}[/dim]")
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
        tuple: (kept, unmatched) — unmatched holds the selectors that named
        nothing, because a typo must not look like an empty corpus.
    """
    def matches(pattern, name):
        return (name == pattern
                or parse_document_id(name)[0] == pattern
                or fnmatch(name, pattern)
                or fnmatch(parse_document_id(name)[0], pattern))

    kept, unmatched = list(docs), []
    if selectors:
        kept = [d for d in docs if any(matches(p, d[0]) for p in selectors)]
        unmatched = [p for p in selectors
                     if not any(matches(p, d[0]) for d in docs)]
    for pattern in exclusions or []:
        kept = [d for d in kept if not matches(pattern, d[0])]
    if skip is not None:
        kept = [d for d in kept if not skip(d[0])]
    if limit is not None:
        kept = kept[:limit]
    return kept, unmatched


def _process_document(doc_name, filepaths, doc_dir, df_meta, config,
                      person_db, do_enrich, do_modernize, progress):
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
    if get_settings().ner:
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
                console.print(f"  [dim]NER: {summary}[/dim]")

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
    say(f"[green]OK[/green] Written: {out_path} [dim]({dt:.2f}s)[/dim]")

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
    configure_logging(settings)

    # Verify OCR directory exists
    # is_dir(), not exists(): -i now makes it easy to point the input at a
    # file, and iterdir() would then raise NotADirectoryError instead of
    # the exit 3 the contract promises.
    if not settings.ocr_dir.is_dir():
        console.print(f"[red]Directory not found: {escape(str(settings.ocr_dir))}[/red]")
        sys.exit(EXIT_MISCONFIGURED)

    # Create output directory
    dry_run = getattr(args, "dry_run", False)

    # Extract ZIP archives (corrupt ones are skipped and reported)
    ready_dirs, failed_archives = expand_archives(settings.ocr_dir,
                                                 extract=not dry_run)

    # An archive nobody selected is none of this run's business: reporting
    # it made `run LIV0044` say "1/2 documents converted" and exit 1.
    selectors = getattr(args, "documents", []) or []
    if selectors:
        failed_archives = [
            (name, reason) for name, reason in failed_archives
            if select_documents([(Path(name).stem, [], None)], selectors, [], None)[0]
        ]

    # Collect documents to process
    docs = []
    for d in ready_dirs:
        xmls = sorted(d.rglob("*.xml"))
        if xmls:
            docs.append((d.name, xmls, d))

    if not docs:
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

    # Narrow to what was asked for. A selector that names nothing stops the
    # run: a typo must not look like an empty corpus.
    # Both filters live in one place, in the order that makes the pair
    # usable: skip what is already converted, then count.
    already_converted = (
        (lambda name: _out_path(name, settings.output_dir).exists())
        if settings.skip_existing else None
    )
    before = len(docs)
    docs, unmatched = select_documents(
        docs, getattr(args, "documents", []) or [],
        getattr(args, "exclude", None) or [], getattr(args, "limit", None),
        skip=already_converted,
    )
    if unmatched:
        console.print(
            "[red]No volume matches:[/red] "
            + escape(", ".join(unmatched))
        )
        sys.exit(EXIT_MISCONFIGURED)
    if not docs:
        console.print("[red]Every volume was excluded.[/red]")
        sys.exit(EXIT_MISCONFIGURED)

    # Audit 2.5: minimal resume after a crash — skip already-converted docs
    skipped_existing = 0
    skipped_existing = before - len(docs) if settings.skip_existing else 0
    if skipped_existing:
        say(
            f"[dim]--skip-existing: {skipped_existing} document(s) "
            f"already converted, skipped[/dim]"
        )
    if settings.skip_existing and not docs and not failed_archives:
        console.print(
            "[bold green]Nothing to do:[/bold green] every document already "
            "has a TEI output."
        )
        return

    if dry_run:
        console.print(
            f"\n[bold]Plan[/bold] — {len(docs)} volume(s), "
            f"{sum(len(f) for _, f, _ in docs)} pages"
        )
        for name, filepaths, _ in docs:
            console.print(f"  {escape(name)}  [dim]{len(filepaths)} pages[/dim]")
        phases = [n for n, on in (("enrich", settings.enrich),
                                  ("modernize", settings.modernize),
                                  ("ner", settings.ner)) if on] or ["none"]
        console.print(
            f"[dim]  input {settings.ocr_dir} → output {settings.output_dir}"
            f" · phases {', '.join(phases)} · {settings.max_workers} workers[/dim]"
        )
        console.print("[dim]  nothing written (--dry-run)[/dim]")
        return

    # Load global metadata CSV
    df_meta = load_metadata(settings.metadata_csv)

    # Load person metadata database
    person_db = load_person_database(settings.persons_csv)
    if person_db:
        say(f"[dim]Loaded {len(person_db)} persons from {settings.persons_csv}[/dim]")
    else:
        console.print(
            f"[yellow]Warning: person metadata not loaded ({settings.persons_csv}) "
            f"— headers will keep placeholder person entries.[/yellow]"
        )

    no_probe = getattr(args, "no_probe", False)
    require_services = getattr(args, "require_services", False)
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

    # Created here and not earlier: every exit above this line means
    # nothing will be written, and leaving an empty directory behind after
    # refusing to run is a write like any other.
    settings.output_dir.mkdir(parents=True, exist_ok=True)

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
        # Corrupt archives belong in the summary and in the exit code, but
        # not in the failure budget: seeding the list with them made
        # --fail-fast stop after the first SUCCESSFUL document.
        failed_docs = []
        for doc_name, filepaths, doc_dir in docs:
            try:
                _process_document(
                    doc_name, filepaths, doc_dir, df_meta, config,
                    person_db, do_enrich, do_modernize, progress,
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
                break
            max_failures = getattr(args, "max_failures", None)
            if max_failures is not None and len(failed_docs) >= max_failures:
                console.print(
                    f"[yellow]--max-failures {max_failures}: reached, stopping.[/yellow]"
                )
                break

    # Audit 2.10: end-of-run summary + non-zero exit code on failures
    failed_docs = failed_docs + list(failed_archives)
    total = len(docs) + len(failed_archives)
    converted = f"{len(ok_docs)}/{total} documents converted"
    if skipped_existing:
        converted += f" ({skipped_existing} more skipped, already converted)"
    if failed_docs:
        console.print(f"\n[bold yellow]Completed with errors:[/bold yellow] {converted}")
        for name, reason in failed_docs:
            console.print(f"  [red]FAILED[/red] {escape(f'{name}: {reason}')}")
        if RUN_LOG_FILE:
            console.print(f"[dim]Tracebacks in {RUN_LOG_FILE}[/dim]")
        sys.exit(EXIT_SOME_FAILED)

    console.print(f"\n[bold green]Done.[/bold green] {converted}")

