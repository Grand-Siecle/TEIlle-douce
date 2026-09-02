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

from config import POS_TAGSETS, PYHELLEN_MODELS
from ..constants import XML_ID, tag_like
from ..utils.xml import local_tag
from ..volumetry import text_volume, token_count
from .default import DefaultTree
from .full import FullTree

# Units update_extent is responsible for: it rewrites them on every run
# and removes the ones it can no longer measure. <measure unit="images">
# is not one of them — it comes from the page count at build time.
OWNED_EXTENT_UNITS = ("words", "tokens")


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


def update_extent(root):
    """
    Record the text's volumetry in <extent>, once it is known.

    The header is built before a single line is read, so <extent> could
    only ever declare the number of images. The word count — asked of
    every catalogue and every corpus description — is measured on the
    sourceDoc, page apparatus excluded (see src/volumetry.py for what
    that means and why it is not counted on the body).

    The token count is reported apart, and only when enrichment has run:
    a token is what PyHellen segmented, which splits elisions ("l'art"
    into two) and counts punctuation on its own. Merging the two under
    one label would publish a number no one could reproduce.

    Idempotent: re-running rewrites both measures, and drops the one it
    can no longer measure — a document re-run without enrichment must
    not keep the token count of the run before.

    Args:
        root (etree.Element): TEI root element.

    Returns:
        dict: The measures written, keyed by unit.
    """
    header_extent = root.find(".//{*}extent")
    if header_extent is None:
        return {}

    measures = {}
    words = text_volume(root)
    if words:
        measures["words"] = words
    tokens = token_count(root)
    if tokens:
        measures["tokens"] = tokens

    for measure in list(header_extent):
        if (local_tag(measure.tag) == "measure"
                and measure.get("unit") in OWNED_EXTENT_UNITS):
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


def declare_pos_tagsets(root, idents):
    """
    Declare the morphosyntactic tagsets the annotation actually used.

    <w pos="NOMcom" msd="NOMB.=s|GENRE=m"> names values from a reference
    a reader cannot guess (audit 1.11). The declaration is written here,
    after the tagging, rather than in the header skeleton: built with the
    skeleton it would announce CATTEX, LASLA and Perseus in every file,
    including the runs made with enrichment disabled, which carry no
    annotation at all.

    Idempotent: a tagset already declared is left as it is.

    Args:
        root (etree.Element): TEI root element.
        idents (iterable): TEI language idents that were tagged.

    Returns:
        list: the xml:id of the tagsets declared.
    """
    tagsets = [
        POS_TAGSETS[PYHELLEN_MODELS[ident]]
        for ident in dict.fromkeys(idents)
        if ident in PYHELLEN_MODELS and PYHELLEN_MODELS[ident] in POS_TAGSETS
    ]
    if not tagsets:
        return []

    class_decl = root.find(".//{*}classDecl")
    if class_decl is None:
        encoding_desc = root.find(".//{*}encodingDesc")
        if encoding_desc is None:
            return []
        class_decl = etree.SubElement(
            encoding_desc, tag_like(encoding_desc, "classDecl")
        )

    declared = {
        el.get(XML_ID) for el in class_decl
        if local_tag(el.tag) == "taxonomy"
    }
    written = []
    for tagset in tagsets:
        if tagset["id"] in declared:
            continue
        taxonomy = etree.SubElement(
            class_decl, tag_like(class_decl, "taxonomy"), {XML_ID: tagset["id"]}
        )
        bibl = etree.SubElement(taxonomy, tag_like(class_decl, "bibl"))
        title = etree.SubElement(bibl, tag_like(class_decl, "title"))
        title.text = tagset["label"]
        etree.SubElement(
            bibl, tag_like(class_decl, "ptr"), target=tagset["url"]
        )
        written.append(tagset["id"])
    return written
