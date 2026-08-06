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

import logging
import sys
from time import perf_counter
from zipfile import ZipFile

from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.console import Console

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
if LOG_FILE:
    _file_handler = logging.FileHandler(str(LOG_FILE), mode="w", encoding="utf-8")
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(logging.Formatter(_log_format, datefmt=_log_datefmt))
    _handlers.append(_file_handler)

# Console handler: WARNING+ by default, DEBUG+ if DEBUG is enabled
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.DEBUG if DEBUG else logging.WARNING)
_console_handler.setFormatter(logging.Formatter("%(name)s [%(levelname)s] %(message)s"))
_handlers.append(_console_handler)

logging.basicConfig(level=logging.DEBUG, handlers=_handlers)

# Import modules
from src import TEI
from src.teiheader import build_header
from src.metadata import (load_metadata,
                          find_metadata_row,
                          build_metadata_dict,
                          override_teiheader_from_csv,
                          load_person_database)
from src.utils import write_xml


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


def expand_archives(ocr_dir):
    """
    Extract ZIP archives in the OCR directory.

    Automatically extracts any ZIP files found in the OCR directory
    into subdirectories with the same name as the archive.

    Args:
        ocr_dir (Path): Path to the OCR directory.

    Returns:
        list: List of directories ready for processing.
    """
    ready_dirs = set()

    # Extract ZIP files that haven't been extracted yet
    for zip_path in ocr_dir.glob("*.zip"):
        target = ocr_dir / zip_path.stem
        if not target.exists():
            console.print(f"[dim]Extracting: {zip_path.name} -> {target.name}/[/dim]")
            target.mkdir(parents=True, exist_ok=True)
            with ZipFile(zip_path) as zf:
                zf.extractall(target)
        ready_dirs.add(target)

    # Include existing directories with XML files
    for d in ocr_dir.iterdir():
        if d.is_dir():
            if any(d.rglob("*.xml")):
                ready_dirs.add(d)

    return sorted(ready_dirs)


def _extract_bdd_prefix(doc_folder_name):
    """
    Extract BDD prefix from document folder name.

    Args:
        doc_folder_name (str): Document folder name.

    Returns:
        str: Extracted prefix or original name.
    """
    import re
    from config import BDD_PREFIX_PATTERN
    match = re.match(BDD_PREFIX_PATTERN, doc_folder_name)
    return match.group(1) if match else doc_folder_name


# =============================================================================
# MAIN WORKFLOW
# =============================================================================

def main():
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
    # Verify OCR directory exists
    if not OCR_DIR.exists():
        console.print(f"[red]Directory not found: {OCR_DIR}[/red]")
        sys.exit(1)

    # Create output directory
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Extract ZIP archives
    ready_dirs = expand_archives(OCR_DIR)

    # Collect documents to process
    docs = []
    for d in ready_dirs:
        xmls = sorted(d.rglob("*.xml"))
        if xmls:
            docs.append((d.name, xmls, d))

    if not docs:
        console.print("[red]No ALTO documents found in OCR/.[/red]")
        sys.exit(1)

    # Load global metadata CSV
    df_meta = load_metadata(METADATA_CSV)

    # Load person metadata database
    person_db = load_person_database(METADATA_PERSON_CSV)
    if person_db and len(person_db) > 0:
        console.print(f"[dim]Loaded {len(person_db)} persons from {METADATA_PERSON_CSV}[/dim]")

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

        for doc_name, filepaths, doc_dir in docs:
            t0 = perf_counter()
            console.print(f"\n[bold cyan]-> {doc_name}[/bold cyan]")

            # Initialize TEI tree
            tree = TEI(doc_name, filepaths, doc_dir)
            tree.build_tree()

            # Progress bar for pages
            task_pages = progress.add_task(
                f"{doc_name}: pages", total=len(filepaths), visible=True
            )

            # Load metadata for this document
            row = find_metadata_row(df_meta, _extract_bdd_prefix(doc_name))
            tree.metadata = build_metadata_dict(row)

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

            # Step 1: Build body + language detection
            task_lang = progress.add_task(
                f"[cyan]{doc_name}: Detection des langues[/cyan]", total=None, visible=True
            )
            tree.build_body(detect_lang=True)
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
                    f"[cyan]{doc_name}: Annotation linguistique[/cyan]", total=None, visible=True
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

            # Step 4: Text modernization (applied after enrichment)
            if do_modernize:
                task_mod = progress.add_task(
                    f"[cyan]{doc_name}: Modernisation du texte[/cyan]", total=None, visible=True
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
                    f"[cyan]{doc_name}: Reconnaissance d'entites nommees[/cyan]",
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
                        person_db, NER_OUTPUT_DIR,
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
            out_path = OUTPUT_DIR / f"{doc_name}.tei.xml"
            write_xml(tree.root, out_path)

            dt = perf_counter() - t0
            console.print(f"[green]OK[/green] Written: {out_path} [dim]({dt:.2f}s)[/dim]")

            progress.update(task_pages, visible=False)
            progress.advance(task_docs)

    console.print("\n[bold green]Done.[/bold green]")


if __name__ == "__main__":
    main()
