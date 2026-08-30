# -----------------------------------------------------------
# ALTO2TEI - Main workflow script
# Converts ALTO XML files to TEI format with SegmOnto taxonomy
# -----------------------------------------------------------
"""
ALTO2TEI main workflow.

This script orchestrates the conversion of ALTO XML files to TEI format.
Configuration is imported from config.py.

Usage:
    python3 main.py
"""

import argparse
import logging
import re
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
from config import (
    OCR_DIR,
    OUTPUT_DIR,
    METADATA_CSV,
    METADATA_PERSON_CSV,
    APP_VERSIONS,
    IIIF_URI,
    RESPONSIBILITY,
    ENRICHMENT_ENABLED,
    MODERNIZE_ENABLED,
    NER_ENABLED,
    NER_ENTITY_TYPES,
    NER_MODELS,
    NER_CONFIDENCE_THRESHOLD,
    NER_OUTPUT_DIR,
    NER_CONTAINERS,
    NER_CERT_THRESHOLDS,
    DEBUG,
    LOG_FILE,
)

# Configure logging
_log_format = "%(asctime)s %(name)s [%(levelname)s] %(message)s"
_log_datefmt = "%Y-%m-%d %H:%M:%S"
_handlers = []

# File handler: always write DEBUG+ to log file
def _run_log_path(base_path, now):
    """
    Per-run, timestamped log file derived from LOG_FILE (audit 2.10).

    mode="w" on a fixed name destroyed the previous run's log: a failed
    nightly run relaunched in the morning became undiagnosable. Each run
    now writes its own file (e.g. pipeline_20260828_093000.log).
    """
    return base_path.with_name(f"{base_path.stem}_{now:%Y%m%d_%H%M%S}{base_path.suffix}")


# Actual log file of this run (None when file logging is disabled)
RUN_LOG_FILE = _run_log_path(Path(LOG_FILE), datetime.now()) if LOG_FILE else None

if RUN_LOG_FILE:
    # delay=True: the file is only created at the first record, so importing
    # this module (tests) does not litter the working directory with logs.
    _file_handler = logging.FileHandler(
        str(RUN_LOG_FILE), mode="w", encoding="utf-8", delay=True
    )
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(logging.Formatter(_log_format, datefmt=_log_datefmt))
    _handlers.append(_file_handler)

# Console handler: WARNING+ by default, DEBUG+ if DEBUG is enabled
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.DEBUG if DEBUG else logging.WARNING)
_console_handler.setFormatter(logging.Formatter("%(name)s [%(levelname)s] %(message)s"))
_handlers.append(_console_handler)

logging.basicConfig(level=logging.DEBUG, handlers=_handlers)

# Third-party HTTP libs are extremely chatty at DEBUG and were filling
# pipeline.log with megabytes of connection traces.
for _noisy in ("httpx", "httpcore", "urllib3", "asyncio"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# Import modules
from src import TEI
from src.body import link_notes_to_lines
from src.teiheader import build_header
from src.metadata import (load_metadata,
                          find_metadata_row,
                          build_metadata_dict,
                          override_teiheader_from_csv,
                          load_person_database,
                          select_manifest)
from src.utils import write_xml
from src.utils.files import parse_document_id


console = Console()

# Lazy-loaded NER models (shared across documents when NER is enabled)
_ner_models = None


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def build_config():
    """
    Build the pipeline configuration dictionary.

    Returns:
        dict: Configuration dictionary for the pipeline.
    """
    return {
        "data": {"path": str(OCR_DIR)},
        "iiifURI": IIIF_URI,
        "responsibility": RESPONSIBILITY,
        "offline": True,
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


def expand_archives(ocr_dir):
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
        if not target.exists():
            console.print(f"[dim]Extracting: {escape(zip_path.name)} -> {escape(target.name)}/[/dim]")
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


def _extract_bdd_prefix(doc_folder_name):
    """
    Extract BDD prefix from document folder name.

    Args:
        doc_folder_name (str): Document folder name.

    Returns:
        str: Extracted prefix or original name.
    """
    from config import BDD_PREFIX_PATTERN
    match = re.match(BDD_PREFIX_PATTERN, doc_folder_name)
    return match.group(1) if match else doc_folder_name


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


def _out_path(doc_name):
    """Final TEI output path of a document — single spelling for the writer,
    the --skip-existing filter and the failure cleanup."""
    return OUTPUT_DIR / f"{doc_name}.tei.xml"


# =============================================================================
# MAIN WORKFLOW
# =============================================================================

def _process_document(doc_name, filepaths, doc_dir, df_meta, config,
                      person_db, do_enrich, do_modernize, progress):
    """
    Run the full conversion pipeline on one document.

    Raises on failure: error isolation lives in main()'s loop (audit 2.1),
    so one broken document cannot kill a multi-hour run.
    """
    t0 = perf_counter()
    console.print(f"\n[bold cyan]-> {escape(doc_name)}[/bold cyan]")

    # Initialize TEI tree
    tree = TEI(doc_name, filepaths, doc_dir)
    tree.build_tree()

    # Progress bar for pages
    task_pages = progress.add_task(
        f"{escape(doc_name)}: pages", total=len(filepaths), visible=True
    )

    # Load metadata for this document
    row = find_metadata_row(df_meta, _extract_bdd_prefix(doc_name))
    tree.metadata = build_metadata_dict(row)

    # Resolve this document's IIIF image base from its manifest (Gallica
    # only — other servers' image APIs aren't derivable from the manifest
    # URL, so @source is omitted for those pages).
    manifests = tree.metadata["iiif"].get("manifests") or []
    volume = parse_document_id(doc_name)[1]
    doc_manifest = select_manifest(manifests, volume)
    config["iiifURI"] = dict(IIIF_URI, image_base=_gallica_image_base(doc_manifest))

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
        console.print(f"  [dim]Languages: {', '.join(langs)}[/dim]")

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
            console.print(
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

        mod_count = tree.modernize_body(
            line_data=line_data,
            enriched=do_enrich,
            progress_callback=_mod_progress,
        )
        progress.update(task_mod, visible=False)

        if mod_count > 0:
            console.print(f"  [dim]Modernisation: {mod_count} lines[/dim]")

    # Step 5: Named Entity Recognition (after enrichment + modernization)
    if NER_ENABLED:
        task_ner = progress.add_task(
            f"[cyan]{escape(doc_name)}: Reconnaissance d'entites nommees[/cyan]",
            total=None,
            visible=True,
        )

        try:
            from src.enrichment.ner_detect import extract_ner_blocks, detect_entities
            from src.enrichment.ner_align import align_and_inject
            from src.enrichment.ner_resolve import resolve_entities
            from src.enrichment.ner_models import NERModels

            # Lazy-load models (shared across documents)
            global _ner_models
            if _ner_models is None:
                _ner_models = NERModels(NER_MODELS)

            ner_models = _ner_models

            # Phase 7: Extract blocks + inference
            ner_blocks = extract_ner_blocks(tree.root, NER_CONTAINERS)
            ner_spans = detect_entities(
                ner_blocks, ner_models, NER_ENTITY_TYPES,
                NER_MODELS, NER_CONFIDENCE_THRESHOLD,
                root=tree.root,
            )

            # Phase 8: Align + merge + inject
            aligned = align_and_inject(
                ner_blocks, ner_spans,
                NER_ENTITY_TYPES, NER_CERT_THRESHOLDS,
            )

            # Phase 9: Resolve + CSV + header + @ref
            resolved = resolve_entities(
                tree.root, aligned, NER_ENTITY_TYPES,
                person_db, NER_OUTPUT_DIR, doc_name,
            )

            if resolved:
                by_type = {}
                for ent in resolved:
                    by_type[ent.entity_type] = by_type.get(ent.entity_type, 0) + 1
                total_mentions = sum(len(e.mentions) for e in resolved)
                type_str = ", ".join(
                    f"{c} {t}" for t, c in sorted(by_type.items(), key=lambda x: -x[1])
                )
                console.print(
                    f"  [dim]NER: {len(resolved)} entities ({type_str}), "
                    f"{total_mentions} mentions[/dim]"
                )

        except ImportError as e:
            console.print(
                f"[yellow]Warning: NER dependencies not installed ({e}) "
                f"— skipping NER.[/yellow]"
            )
        except Exception as e:
            logging.getLogger(__name__).error("NER pipeline failed: %s", e, exc_info=True)
            console.print(f"[yellow]Warning: NER failed ({e}) — continuing.[/yellow]")

        progress.update(task_ner, visible=False)

    # Override TEI header with CSV metadata
    override_teiheader_from_csv(tree.root, row, doc_name)

    # Finalize langUsage with detected languages (after CSV override)
    tree.finalize_langusage()

    # Write output file
    out_path = _out_path(doc_name)
    write_xml(tree.root, out_path)

    dt = perf_counter() - t0
    console.print(f"[green]OK[/green] Written: {out_path} [dim]({dt:.2f}s)[/dim]")

    progress.update(task_pages, visible=False)


def _parse_args(argv=None):
    """Command-line interface of the pipeline."""
    parser = argparse.ArgumentParser(
        description="Convert ALTO XML documents to TEI with SegmOnto taxonomy."
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="skip documents whose TEI output already exists "
             "(minimal resume after an interrupted run, audit 2.5)",
    )
    return parser.parse_args(argv)


def main(argv=None):
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
    args = _parse_args(argv)

    # Verify OCR directory exists
    if not OCR_DIR.exists():
        console.print(f"[red]Directory not found: {OCR_DIR}[/red]")
        sys.exit(1)

    # Create output directory
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Extract ZIP archives (corrupt ones are skipped and reported)
    ready_dirs, failed_archives = expand_archives(OCR_DIR)

    # Collect documents to process
    docs = []
    for d in ready_dirs:
        xmls = sorted(d.rglob("*.xml"))
        if xmls:
            docs.append((d.name, xmls, d))

    if not docs:
        console.print("[red]No ALTO documents found in OCR/.[/red]")
        sys.exit(1)

    # Audit 2.5: minimal resume after a crash — skip already-converted docs
    skipped_existing = 0
    if args.skip_existing:
        pending = [d for d in docs if not _out_path(d[0]).exists()]
        skipped_existing = len(docs) - len(pending)
        if skipped_existing:
            console.print(
                f"[dim]--skip-existing: {skipped_existing} document(s) "
                f"already converted, skipped[/dim]"
            )
        docs = pending
        if not docs and not failed_archives:
            console.print("[bold green]Nothing to do:[/bold green] every document already has a TEI output.")
            return

    # Load global metadata CSV
    df_meta = load_metadata(METADATA_CSV)

    # Load person metadata database
    person_db = load_person_database(METADATA_PERSON_CSV)
    if person_db:
        console.print(f"[dim]Loaded {len(person_db)} persons from {METADATA_PERSON_CSV}[/dim]")
    else:
        console.print(
            f"[yellow]Warning: person metadata not loaded ({METADATA_PERSON_CSV}) "
            f"— headers will keep placeholder person entries.[/yellow]"
        )

    # Check modernization API availability
    do_modernize = False
    if MODERNIZE_ENABLED:
        from src.modernize import check_api as check_modernize_api
        if check_modernize_api():
            do_modernize = True
            console.print("[green]Modernization API (VieuxParler) available.[/green]")
        else:
            console.print("[yellow]Warning: Modernization API unreachable — continuing without modernization.[/yellow]")

    # Check linguistic enrichment API availability
    do_enrich = False
    if ENRICHMENT_ENABLED:
        from src.enrichment.client import check_server as check_enrichment_api
        if check_enrichment_api():
            do_enrich = True
            console.print("[green]Enrichment API (PyHellen) available.[/green]")
        else:
            console.print("[yellow]Warning: Enrichment API unreachable — continuing without linguistic annotation.[/yellow]")

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
    ) as progress:

        task_docs = progress.add_task("Processing documents", total=len(docs))

        ok_docs = []
        failed_docs = list(failed_archives)  # corrupt archives count as failures
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
                if not _out_path(doc_name).exists():
                    shutil.rmtree(NER_OUTPUT_DIR / doc_name, ignore_errors=True)
                # And no zombie progress rows left spinning forever
                for tid in progress.task_ids:
                    if tid != task_docs:
                        progress.update(tid, visible=False)
            else:
                ok_docs.append(doc_name)
            progress.advance(task_docs)

    # Audit 2.10: end-of-run summary + non-zero exit code on failures
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
        sys.exit(1)

    console.print(f"\n[bold green]Done.[/bold green] {converted}")


if __name__ == "__main__":
    main()
