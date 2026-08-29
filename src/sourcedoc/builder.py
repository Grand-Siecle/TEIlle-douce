# -----------------------------------------------------------
# Code by: Kelly Christensen
# Orchestrates the construction of <sourceDoc> with parallel processing.
# -----------------------------------------------------------
"""
SourceDoc builder module.

This module handles the construction of the TEI <sourceDoc> element from
ALTO XML files. Uses multiprocessing for parallel page processing.
"""

import logging
import multiprocessing
from pathlib import Path
from multiprocessing import cpu_count

from lxml import etree
from rich.markup import escape as markup_escape

from config import MAX_WORKERS

logger = logging.getLogger(__name__)

# fork() in a multi-threaded parent (rich's progress refresh thread,
# coverage's tracing) deadlocks intermittently — observed locally and in
# CI, and warned about by Python itself. The forkserver context forks
# workers from a clean single-threaded server instead; preloading this
# module there keeps worker startup fork-fast (no per-worker re-import
# of lxml/pandas). spawn is the fallback where forkserver is missing.
if "forkserver" in multiprocessing.get_all_start_methods():
    _MP_CONTEXT = multiprocessing.get_context("forkserver")
    _MP_CONTEXT.set_forkserver_preload(["src.sourcedoc.builder"])
else:  # pragma: no cover - non-POSIX platforms
    _MP_CONTEXT = multiprocessing.get_context("spawn")
from ..constants import NS_ALTO
from ..utils.files import Files
from ..metadata.iiif import IIIFMapping
from .attributes import Attributes
from .elements import SurfaceTree


# XML parser configured for large ALTO files. Strict: recovery is a
# separate, explicit second chance in _parse_alto, always reported
# (audit 2.3 — no more silent repairs).
XML_PARSER = etree.XMLParser(huge_tree=True)


def extract_labels(source):
    """
    Extract SegmOnto labels from an ALTO file.

    Reads the OtherTag elements from an ALTO document and creates a
    mapping from element IDs to their labels. OtherTag entries missing
    ID or LABEL are skipped instead of raising KeyError (audit 2.3).

    Args:
        source: Path to the ALTO XML file, or an already-parsed ALTO
            root element (spares the second parse in the workers,
            audit 3.4).

    Returns:
        dict: Mapping of element IDs to their LABEL values.
    """
    if isinstance(source, etree._Element):
        root = source
    else:
        root = etree.parse(str(source), parser=XML_PARSER).getroot()
    elements = [t.attrib for t in root.findall(".//a:OtherTag", namespaces=NS_ALTO)]
    return {d["ID"]: d["LABEL"] for d in elements if "ID" in d and "LABEL" in d}


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
        tuple: (num, xml_bytes, error, warning) - page number, serialized
        surface XML (None on failure), an error message (None on success)
        and a warning (e.g. "recovered malformed XML", None when clean).
        Errors travel as plain strings: lxml exceptions are not picklable
        and used to come back as an opaque MaybeEncodingError that killed
        the whole pool, losing the already-processed pages (audit 5.5).
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

    try:
        num_out, xml_bytes, warning = _build_surface_fragment_inner(
            document_name, filepath, num, segmonto_zones, segmonto_lines,
            config, iiif_mapping_dict,
        )
        return num_out, xml_bytes, None, warning
    except Exception as e:  # audit 2.3: one bad page must not kill the run
        return num, None, f"{type(e).__name__}: {e}", None


def _parse_alto(filepath):
    """
    Parse an ALTO file: strict first, libxml2 recovery as a second chance.

    Audit 2.3 asked for one parser and no *silent* repairs. Recovery is
    still valuable for HTR exports with small quirks (unescaped ampersands,
    stray control characters): losing a whole page is worse than keeping a
    mostly-correct one — as long as it is reported. Returns (root, warning)
    where warning is None for a clean parse; raises when even recovery
    cannot produce a tree.
    """
    try:
        return etree.parse(str(filepath), parser=XML_PARSER).getroot(), None
    except etree.XMLSyntaxError as strict_error:
        recovery_parser = etree.XMLParser(huge_tree=True, recover=True)
        try:
            root = etree.parse(str(filepath), parser=recovery_parser).getroot()
        except etree.XMLSyntaxError:
            root = None
        if root is None:
            raise strict_error
        n_err = len(recovery_parser.error_log)
        first = recovery_parser.error_log[0] if n_err else strict_error
        return root, f"malformed XML recovered ({n_err} parse error(s); first: {first})"


def _build_surface_fragment_inner(document_name, filepath, num, segmonto_zones,
                                  segmonto_lines, config, iiif_mapping_dict):
    # Parse ALTO file (single parse, reused for the labels below)
    input_alto_root, warning = _parse_alto(filepath)
    file_stem = Path(filepath).stem

    # Extract zone/line type mappings from the already-parsed root (audit 3.4)
    tags = extract_labels(input_alto_root)

    # Create surface tree builder (with or without IIIF mapping)
    if iiif_mapping_dict:
        iiif_mapping = IIIFMapping()
        iiif_mapping.mapping = iiif_mapping_dict
        surface_tree = SurfaceTree(document_name, file_stem, input_alto_root, iiif_mapping)
    else:
        surface_tree = SurfaceTree(document_name, file_stem, input_alto_root)

    # Create <surface> element
    # `num` is the page number parsed from the filename stem by
    # Files.order_files() ("f1.xml" -> 1). Using it as the IIIF view number
    # (f<N>) ASSUMES the corpus convention that ALTO files are named after
    # their 1-based scan view; documents numbered from 0 yield view_number=0
    # for their first page, which Attributes treats as untrustworthy (no
    # @source emitted).
    attributes = Attributes(document_name, file_stem, input_alto_root, tags,
                            dict(config, view_number=num))
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

    # Duplicate ALTO ids were disambiguated by SurfaceTree; report them
    # through the return tuple — a logger call inside a forkserver worker
    # never reaches the parent's log file.
    dup_keys = [k for k, n in surface_tree._seen_keys.items() if n > 1]
    if dup_keys:
        dup_note = f"{len(dup_keys)} duplicate ALTO id(s) disambiguated"
        warning = f"{warning}; {dup_note}" if warning else dup_note

    return num, etree.tostring(surface, encoding="utf-8"), warning


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
        tuple: (root, skipped_pages)
            - root: the updated TEI root element
            - skipped_pages: sorted page numbers whose ALTO was unusable
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
    workers = min(cpu_count(), MAX_WORKERS)

    results = {}

    skipped_pages = []

    # Process pages in parallel
    with _MP_CONTEXT.Pool(workers) as pool:
        for idx, (num, xml_bytes, error, warning) in enumerate(
            pool.imap_unordered(_build_surface_fragment, jobs), start=1
        ):
            if warning:
                logger.warning("%s: page %s %s", document_name, num, warning)
            if error is not None:
                # Audit 2.3: report and skip the page, keep the document
                logger.error(
                    "%s: page %s skipped, ALTO unusable (%s)",
                    document_name, num, error,
                )
                skipped_pages.append(num)
            results[num] = xml_bytes

            # Update progress bar if provided
            if progress and parent_task_pages is not None:
                progress.update(
                    parent_task_pages,
                    description=f"[cyan]{markup_escape(document_name)}[/cyan] page {idx}/{total_pages}",
                    advance=1,
                )

    # Assemble surfaces in correct order (skipped pages were already
    # reported above, one ERROR line each)
    for f in sorted(ordered_files, key=lambda x: x.num):
        frag = results.get(f.num)
        if frag:
            sourceDoc.append(etree.fromstring(frag))

    return output_tei_root, sorted(skipped_pages)
