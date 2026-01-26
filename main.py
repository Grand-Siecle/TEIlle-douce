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
    APP_VERSIONS,
    IIIF_URI,
    RESPONSIBILITY,
)

# Import modules
from src import TEI
from src.teiheader import build_header
from src.metadata import (load_metadata, 
                          find_metadata_row, 
                          build_metadata_dict, 
                          override_teiheader_from_csv)
from src.utils import write_xml


console = Console()


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

            # Build body (with language detection)
            tree.build_body(detect_lang=True)

            # Override TEI header with CSV metadata
            override_teiheader_from_csv(tree.root, row)

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
