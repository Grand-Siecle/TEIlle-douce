# -----------------------------------------------------------
# Code by: Kelly Christensen
# Central TEI data structure for the ALTO2TEI pipeline.
# -----------------------------------------------------------
"""
TEI document class.

This module provides the TEI class which is the central data structure
for carrying document state through the conversion pipeline.
"""

from pathlib import Path

from lxml import etree

from .constants import NS_TEI, XML_ID
from .teiheader import build_header
from .sourcedoc import build_sourcedoc
from .body import build_body, Text
from .metadata import IIIFMapping
from .lang import update_langusage


class TEI:
    """
    Central data structure for TEI document construction.

    Carries document state through the pipeline and provides methods
    for building each section of the TEI document.

    Attributes:
        d (str): Document name.
        fp (list): List of ALTO file paths.
        doc_dir (Path): Directory containing the document files.
        metadata (dict): Metadata dictionary with 'sru' and 'iiif' keys.
        tags (dict): Tag mappings (unused, kept for compatibility).
        root (etree.Element): TEI XML root element.
        segmonto_zones (list): SegmOnto zone labels found in the document.
        segmonto_lines (list): SegmOnto line labels found in the document.
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
        self._iiif_mapping = None

    def build_tree(self):
        """
        Create the root TEI element with namespaces.

        Initializes the XML tree with the TEI root element and proper
        namespace declarations.
        """
        xml_id_att = {XML_ID: f"ark_12148_{self.d}"}
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
        Build the <body> element from sourceDoc text.

        Extracts text lines from the sourceDoc and assembles them into
        the TEI body structure. Optionally detects language for each
        element and adds xml:lang attributes.

        Note: Call update_lang_header() after this to update the TEI
        header's <langUsage> element with the detected languages.

        Args:
            detect_lang (bool): If True, detect language with FastText
                               and add xml:lang attributes to elements.

        Returns:
            dict or None: Language statistics if detect_lang=True.
        """
        text = Text(self.root)
        self.lang_stats = build_body(self.root, text.data, detect_lang=detect_lang)
        return self.lang_stats

    def update_lang_header(self):
        """
        Update the <langUsage> element in the TEI header.

        Should be called after build_body() and after any CSV overrides
        to ensure the detected languages are properly recorded.

        Returns:
            dict or None: Language statistics that were used.
        """
        if hasattr(self, "lang_stats") and self.lang_stats:
            update_langusage(self.root, self.lang_stats)
        return getattr(self, "lang_stats", None)
