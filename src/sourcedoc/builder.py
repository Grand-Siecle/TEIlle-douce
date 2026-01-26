# -----------------------------------------------------------
# Code by: Kelly Christensen
# Orchestrates the construction of <sourceDoc> with parallel processing.
# -----------------------------------------------------------
"""
SourceDoc builder module.

This module handles the construction of the TEI <sourceDoc> element from
ALTO XML files. Uses multiprocessing for parallel page processing.
"""

from pathlib import Path
from multiprocessing import Pool, cpu_count

from lxml import etree

from ..constants import NS_ALTO
from ..utils.files import Files
from ..metadata.iiif import IIIFMapping
from .attributes import Attributes
from .elements import SurfaceTree


# XML parser configured for large ALTO files
XML_PARSER = etree.XMLParser(huge_tree=True, recover=True)


def extract_labels(filepath):
    """
    Extract SegmOnto labels from an ALTO file.

    Reads the OtherTag elements from an ALTO file and creates a mapping
    from element IDs to their labels.

    Args:
        filepath: Path to the ALTO XML file.

    Returns:
        dict: Mapping of element IDs to their LABEL values.
    """
    root = etree.parse(str(filepath)).getroot()
    elements = [t.attrib for t in root.findall(".//a:OtherTag", namespaces=NS_ALTO)]
    return {d["ID"]: d["LABEL"] for d in elements}


def _build_surface_fragment(args):
    """
    Build a <surface> fragment for a single ALTO page.

    This function is designed to run in a multiprocessing pool.
    It processes one ALTO file and returns a serialized surface element.

    Args:
        args (tuple): Contains:
            - document_name (str): Name of the document
            - filepath (Path): Path to the ALTO file
            - num (int): Page number
            - segmonto_zones (list): List of valid zone types
            - segmonto_lines (list): List of valid line types
            - config (dict): IIIF configuration
            - iiif_mapping_dict (dict): IIIF URL mapping or None

    Returns:
        tuple: (num, xml_bytes) - page number and serialized surface XML
    """
    (
        document_name,
        filepath,
        num,
        segmonto_zones,
        segmonto_lines,
        config,
        iiif_mapping_dict,
    ) = args

    # Parse ALTO file
    input_alto_root = etree.parse(str(filepath), parser=XML_PARSER).getroot()
    file_stem = Path(filepath).stem

    # Extract zone/line type mappings
    tags = extract_labels(filepath)

    # Create surface tree builder (with or without IIIF mapping)
    if iiif_mapping_dict:
        iiif_mapping = IIIFMapping()
        iiif_mapping.mapping = iiif_mapping_dict
        surface_tree = SurfaceTree(document_name, file_stem, input_alto_root, iiif_mapping)
    else:
        surface_tree = SurfaceTree(document_name, file_stem, input_alto_root)

    # Create <surface> element
    attributes = Attributes(document_name, file_stem, input_alto_root, tags, config)
    surface = surface_tree.surface(attributes.surface())

    # Index ALTO elements by ID for fast lookup
    by_id = {el.get("ID"): el for el in input_alto_root.xpath("//*[@ID]")}

    # Process TextBlocks
    textblocks = attributes.zones("PrintSpace", "TextBlock", segmonto_zones)

    for tb in textblocks:
        if not tb.id:
            continue

        textblock = surface_tree.zone1(surface, tb.attributes, tb.id, num)
        textlines = attributes.zones(f'TextBlock[@ID="{tb.id}"]', "TextLine", segmonto_lines)
        line_count = 0

        for tl in textlines:
            if not tl.id:
                continue

            textline = surface_tree.zone2(textblock, tb.id, tl.attributes, tl.id, num)

            # Get the ALTO TextLine element
            line_node = by_id.get(tl.id)
            if line_node is None:
                continue

            # Extract text content from String and SP elements
            words_parts = []
            for child in line_node:
                local = etree.QName(child).localname
                if local == "String":
                    content = child.get("CONTENT")
                    if content:
                        words_parts.append(content)
                elif local == "SP":
                    words_parts.append(" ")

            line_count += 1
            surface_tree.line(textline, tb.id, tl.id, line_count, "".join(words_parts).strip())

    return num, etree.tostring(surface, encoding="utf-8")


def build_sourcedoc(
    document_name,
    output_tei_root,
    filepath_list,
    tags,
    segmonto_zones,
    segmonto_lines,
    config,
    progress=None,
    parent_task_pages=None,
    iiif_mapping=None,
):
    """
    Build the <sourceDoc> element with parallel page processing.

    Processes all ALTO files for a document in parallel using multiprocessing,
    then assembles the results in page order.

    Args:
        document_name (str): Name of the document.
        output_tei_root (etree.Element): TEI root element to append sourceDoc to.
        filepath_list (list): List of ALTO file paths.
        tags (dict): Tag mappings (unused, kept for compatibility).
        segmonto_zones (list): Valid SegmOnto zone types.
        segmonto_lines (list): Valid SegmOnto line types.
        config (dict): IIIF configuration dictionary.
        progress: Optional Rich progress bar instance.
        parent_task_pages: Optional task ID for progress updates.
        iiif_mapping (IIIFMapping): Optional IIIF URL mapping instance.

    Returns:
        etree.Element: The updated TEI root element.
    """
    # Order files by page number
    ordered_files = Files(document_name, filepath_list).order_files()
    sourceDoc = etree.SubElement(output_tei_root, "sourceDoc")
    total_pages = len(ordered_files)

    # Prepare IIIF mapping for multiprocessing (must be serializable)
    iiif_mapping_dict = None
    if iiif_mapping and iiif_mapping.has_mapping():
        iiif_mapping_dict = iiif_mapping.mapping.copy()

    # Prepare job arguments
    jobs = [
        (
            document_name,
            f.filepath,
            f.num,
            segmonto_zones,
            segmonto_lines,
            config,
            iiif_mapping_dict,
        )
        for f in ordered_files
    ]

    # Limit workers to avoid resource exhaustion
    workers = min(cpu_count(), 8)

    results = {}

    # Process pages in parallel
    with Pool(workers) as pool:
        for idx, (num, xml_bytes) in enumerate(
            pool.imap_unordered(_build_surface_fragment, jobs), start=1
        ):
            results[num] = xml_bytes

            # Update progress bar if provided
            if progress and parent_task_pages is not None:
                progress.update(
                    parent_task_pages,
                    description=f"[cyan]{document_name}[/cyan] page {idx}/{total_pages}",
                    advance=1,
                )

    # Assemble surfaces in correct order
    for f in sorted(ordered_files, key=lambda x: x.num):
        frag = results.get(f.num)
        if frag:
            sourceDoc.append(etree.fromstring(frag))

    return output_tei_root
