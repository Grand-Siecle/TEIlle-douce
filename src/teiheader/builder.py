# -----------------------------------------------------------
# Code by: Kelly Christensen
# Orchestrates the assembly of the <teiHeader> element.
# -----------------------------------------------------------
"""
TEI Header builder module.

This module orchestrates the creation of the <teiHeader> element by:
1. Building the default structure with empty placeholders
2. Populating it with metadata from CSV/IIIF
3. Generating the SegmOnto taxonomy from ALTO files
"""

from .default import DefaultTree
from .full import FullTree


def build_header(metadata, document, root, count_pages, config, app_versions, filepaths):
    """
    Create all elements of the <teiHeader>.

    This function builds the TEI header in two stages:
    1. Generate a default structure with empty placeholders
    2. Populate the structure with actual metadata

    Args:
        metadata (dict): Dictionary containing 'sru' and 'iiif' metadata dictionaries.
        document (str): Name of the document directory.
        root (etree.Element): XML-TEI tree root element.
        count_pages (int): Number of pages in the document.
        config (dict): Pipeline configuration dictionary.
        app_versions (dict): Dictionary of application versions (KRAKEN, YOLO, YALTAI).
        filepaths (list): List of ALTO file paths for taxonomy extraction.

    Returns:
        tuple: (root, segmonto_zones, segmonto_lines)
            - root: Updated XML-TEI tree with teiHeader
            - segmonto_zones: List of zone labels found in the document
            - segmonto_lines: List of line labels found in the document
    """
    # Step 1: Generate default <teiHeader> structure
    elements = DefaultTree(config, document, root, metadata, count_pages, app_versions)
    elements.build()

    # Step 2: Populate with available metadata
    htree = FullTree(elements.children, metadata)
    htree.author_data()
    htree.bib_data()

    # Step 3: Build SegmOnto taxonomy from ALTO files
    segmonto_zones, segmonto_lines = htree.segmonto_taxonomy(filepaths)

    return root, segmonto_zones, segmonto_lines
