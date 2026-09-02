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

from lxml import etree

from ..constants import tag_like
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


def _transcribed_text(element, local_tag):
    """Text of *element*, counting each passage once.

    Two encodings would otherwise inflate a word count: a modernized
    line carries <choice><orig>…</orig><reg>…</reg></choice> — the same
    passage written twice — and enrichment turns punctuation into <pc>,
    which is not a word. Both are skipped, their tails kept.
    """
    parts = []

    def walk(node):
        if node.text:
            parts.append(node.text)
        for child in node:
            if isinstance(child.tag, str) and local_tag(child.tag) not in ("reg", "pc"):
                walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(element)
    return " ".join(parts)


def update_extent(root):
    """
    Record the text's volumetry in <extent>, once it is known.

    The header is built before a single line is read, so <extent> could
    only ever declare the number of images. The word count — asked of
    every catalogue and every corpus description — is known once the body
    exists, and the token count once enrichment has run.

    The two are separate units on purpose. A word here is a
    whitespace-delimited unit of the transcription; a token is what
    PyHellen segmented, which splits elisions ("l'art" -> two tokens) and
    counts punctuation apart. Merging them under one label would report
    a number no one could reproduce.

    Idempotent: re-running replaces the measures it owns.

    Args:
        root (etree.Element): TEI root element.

    Returns:
        dict: The measures written, keyed by unit.
    """
    from ..constants import TEXT_CONTAINERS
    from ..utils.xml import content_root, local_tag

    header_extent = None
    for el in root.iter():
        if local_tag(el.tag) == "extent":
            header_extent = el
            break
    if header_extent is None:
        return {}

    text = content_root(root)
    if text is None:
        return {}

    tokens = 0
    words = 0
    for container in text.iter(*TEXT_CONTAINERS):
        tokens += sum(1 for el in container.iter() if local_tag(el.tag) == "w")
        words += len(_transcribed_text(container, local_tag).split())

    measures = {}
    if words:
        measures["words"] = words
    if tokens:
        measures["tokens"] = tokens

    for measure in list(header_extent):
        if local_tag(measure.tag) == "measure" and measure.get("unit") in measures:
            header_extent.remove(measure)

    for unit, quantity in measures.items():
        el = etree.SubElement(
            header_extent,
            tag_like(header_extent, "measure"),
            unit=unit,
            quantity=str(quantity),
        )
        el.text = f"{quantity} {unit}"

    return measures
