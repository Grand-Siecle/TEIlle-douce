# -----------------------------------------------------------
# Code by: Kelly Christensen
# Central TEI data structure for the ALTO2TEI pipeline.
# -----------------------------------------------------------
"""
TEI document class.

This module provides the TEI class which is the central data structure
for carrying document state through the conversion pipeline.
"""

import logging
from pathlib import Path

from lxml import etree

from .constants import NS_TEI, XML_ID
from .utils.files import canonical_document_id

logger = logging.getLogger(__name__)
from .teiheader import build_header
from .sourcedoc import build_sourcedoc
from .body import build_body, apply_modernization, apply_modernization_enriched, Text
from .metadata import IIIFMapping
from .lang import build_langusage
from .enrichment import enrich_body as _enrich_body


class TEI:
    """
    Central data structure for TEI document construction.

    Carries document state through the pipeline and provides methods
    for building each section of the TEI document.

    Attributes:
        d (str): Document name/identifier.
        fp (list): List of ALTO file paths.
        doc_dir (Path): Directory containing the document files.
        metadata (dict): Metadata dictionary with 'sru' and 'iiif' keys.
        root (etree.Element): TEI XML root element.
        segmonto_zones (list): SegmOnto zone labels found in the document.
        segmonto_lines (list): SegmOnto line labels found in the document.
        lang_stats (dict): Language detection statistics after build_body().

    Example:
        >>> tree = TEI("document_name", filepaths, doc_dir)
        >>> tree.build_tree()
        >>> tree.build_body(detect_lang=True)
        >>> tree.finalize_langusage()
    """

    def __init__(self, document, filepaths, doc_dir=None):
        """
        Initialize a TEI document builder.

        Args:
            document (str): Name of the document.
            filepaths (list): List of ALTO file paths.
            doc_dir (Path): Directory containing the document files.
                           Used for auto-detecting IIIF mapping CSV.
        """
        self.d = document
        self.fp = filepaths
        self.doc_dir = doc_dir
        self.metadata = {"sru": None, "iiif": None}
        self.tags = {}
        self.root = None
        self.segmonto_zones = None
        self.segmonto_lines = None
        self.lang_stats = None
        self._iiif_mapping = None

    def build_tree(self):
        """
        Create the root TEI element with namespaces.

        Initializes the XML tree with the TEI root element and proper
        namespace declarations.
        """
        xml_id_att = {XML_ID: canonical_document_id(self.d)}
        nsmap = {None: NS_TEI}
        self.root = etree.Element("TEI", xml_id_att, nsmap=nsmap)

    @property
    def iiif_mapping(self):
        """
        Lazy-load IIIF mapping from auto-detected CSV.

        Returns:
            IIIFMapping or None: Loaded mapping or None if not found.
        """
        if self._iiif_mapping is None and self.doc_dir:
            mapping_csv = IIIFMapping.detect_csv(Path(self.doc_dir), self.fp)
            if mapping_csv:
                self._iiif_mapping = IIIFMapping()
                self._iiif_mapping.load_from_csv(mapping_csv)

        return self._iiif_mapping

    def build_sourcedoc(self, config, progress=None, parent_task_pages=None):
        """
        Build the <sourceDoc> element from ALTO files.

        Uses parallel processing for performance on large documents.

        Args:
            config (dict): Pipeline configuration with IIIF settings.
            progress: Optional Rich progress bar instance.
            parent_task_pages: Optional task ID for progress updates.
        """
        build_sourcedoc(
            self.d,
            self.root,
            self.fp,
            self.tags,
            self.segmonto_zones,
            self.segmonto_lines,
            config["iiifURI"],
            progress=progress,
            parent_task_pages=parent_task_pages,
            iiif_mapping=self.iiif_mapping,
        )

    def build_body(self, detect_lang=True):
        """
        Build the `<body>` element from sourceDoc text.

        Extracts text lines from the sourceDoc and assembles them into
        the TEI body structure with appropriate elements (`<ab>`, `<note>`,
        `<fw>`, `<lb/>`, etc.).

        When `detect_lang=True`, uses Lingua to detect the language of
        each text container and adds `xml:lang` attributes. Language
        statistics are stored in `self.lang_stats` for later use.

        Note:
            Call `finalize_langusage()` after any CSV metadata overrides
            to update the `<langUsage>` element in the TEI header.

        Args:
            detect_lang (bool): If True, detect languages with Lingua
                               and add `xml:lang` attributes to containers.
                               Defaults to True.

        Returns:
            dict or None: Language statistics if detect_lang=True.
                         Format: {"fra": 1716, "lat": 38, "grc": 7}
        """
        text = Text(self.root)
        self.lang_stats = build_body(self.root, text.data, detect_lang=detect_lang)
        return self.lang_stats

    def extract_line_data(self):
        """
        Extract line texts, corresp values, and zone types from <lb> elements.

        Must be called after build_body() and before enrich_body(),
        because enrichment replaces <lb> tails with <w>/<pc> elements.

        Returns:
            list[tuple[str|None, str, str]]: List of (corresp, text, zone_type)
                tuples, one per <lb> element in document order.
        """
        body = self.root.find(".//body")
        if body is None:
            return []
        result = []
        for lb in body.iter("lb"):
            corresp = lb.get("corresp")
            text = lb.tail or ""
            # Walk up to the container (ab, note, fw) to get zone type
            parent = lb.getparent()
            while parent is not None and parent.tag not in ("ab", "note", "fw"):
                parent = parent.getparent()
            zone_type = parent.get("type", "") if parent is not None else ""
            result.append((corresp, text, zone_type))
        return result

    def modernize_body(self, line_data=None, enriched=False, progress_callback=None):
        """
        Modernize text in the body via the VieuxParler API.

        When enriched=False (default), wraps <lb> tails in <choice>.
        When enriched=True, wraps <w>/<pc> groups per line in <choice><orig>/<reg>.

        Args:
            line_data: Pre-extracted [(corresp, text)] from extract_line_data().
                       Required when enriched=True (lb tails no longer exist).
                       If None and enriched=False, extracts from current DOM.
            enriched: Whether enrich_body() has already been called.
            progress_callback: Optional callable(completed, total).

        Returns:
            int: Number of lines modernized, or 0 on failure.
        """
        from .modernize import modernize_texts, dehyphenate_lines

        # Get line texts
        if line_data is None:
            line_data = self.extract_line_data()

        original_texts = [text for _, text, *_ in line_data]
        zone_types = [zt for _, _, zt, *_ in line_data] if line_data and len(line_data[0]) > 2 else None

        # Dehyphenate before modernization: join words split by ¬/-
        # across lines so the API sees complete words.
        # Only joins within the same zone type to avoid cross-container merges.
        joined_texts = dehyphenate_lines(original_texts, zone_types=zone_types)

        try:
            modernized = modernize_texts(
                joined_texts, lang="fra", progress_callback=progress_callback
            )
        except Exception as e:
            logger.error("Modernization failed: %s: %r", type(e).__name__, e)
            return 0

        if modernized is None:
            return 0

        if enriched:
            # Build corresp -> modernized mapping for lines that changed
            corresp_to_mod = {}
            for ld, mod in zip(line_data, modernized):
                corresp, orig = ld[0], ld[1]
                if mod and mod != orig and corresp:
                    corresp_to_mod[corresp] = mod
            return apply_modernization_enriched(self.root, corresp_to_mod)
        else:
            return apply_modernization(self.root, modernized)

    def enrich_body(self, progress_callback=None):
        """
        Apply linguistic enrichment to body containers.

        Tokenizes text, adds POS tags, lemmas, and sentence boundaries
        using the PyHellen NLP API. Transforms container structure from
        <lb/>+text to <s>/<w>/<pc>/<lb/>.

        Args:
            progress_callback: Optional callable(current, total) for progress updates.

        Returns:
            dict: Enrichment statistics.
        """
        return _enrich_body(self.root, progress_callback=progress_callback)

    def finalize_langusage(self):
        """
        Update the `<langUsage>` element in the TEI header.

        Builds or updates the `<langUsage>` element with language statistics
        collected during `build_body()`. Each detected language is recorded
        with its usage count.

        This method should be called:
            1. After `build_body(detect_lang=True)`
            2. After any CSV metadata overrides (which may modify the header)

        The resulting `<langUsage>` will contain `<language>` elements
        sorted by usage count in descending order.

        Returns:
            dict or None: The language statistics that were applied,
                         or None if no statistics were available.

        Example:
            >>> tree.build_body(detect_lang=True)
            >>> override_teiheader_from_csv(tree.root, row)  # May modify header
            >>> tree.finalize_langusage()  # Ensures langUsage is correct
            {'fra': 1716, 'lat': 38, 'grc': 7}

        TEI Output:
            <langUsage>
                <language ident="fra" usage="1716">French</language>
                <language ident="lat" usage="38">Latin</language>
                <language ident="grc" usage="7">Ancient Greek</language>
            </langUsage>
        """
        if self.lang_stats:
            build_langusage(self.root, self.lang_stats)
        return self.lang_stats
