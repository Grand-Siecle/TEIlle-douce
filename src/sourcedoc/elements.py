# -----------------------------------------------------------
# Code by: Kelly Christensen
# Creates TEI elements inside <sourceDoc> and maps ALTO data to them.
# -----------------------------------------------------------
"""
SourceDoc element creation module.

This module provides the SurfaceTree class which creates TEI <surface>,
<zone>, <path>, and <line> elements from ALTO data.
"""

import uuid

from lxml import etree

from ..constants import NS_ALTO, UUID_NAMESPACE, XML_ID
from ..utils.xml import xml_id_safe
from .attributes import format_alto_points


class SurfaceTree:
    """
    Creates TEI <surface> and child elements for one ALTO page.

    This class handles the conversion of ALTO page structure to TEI format,
    including zones (TextBlocks), line zones (TextLines), and text lines.

    Attributes:
        doc (str): Document name.
        folio (str): Page/folio identifier.
        root (etree.Element): ALTO XML root element.
        iiif_mapping (IIIFMapping): Optional IIIF URL mapping.
        ids (dict): Mapping of element keys to generated UUIDs.
    """

    def __init__(self, doc, folio, alto_root, iiif_mapping=None):
        """
        Initialize the SurfaceTree builder.

        Args:
            doc (str): Document name.
            folio (str): Page/folio identifier (usually the file stem).
            alto_root (etree.Element): Parsed ALTO XML root element.
            iiif_mapping (IIIFMapping): Optional IIIF URL mapping instance.
        """
        self.doc = doc
        self.folio = folio
        self.root = alto_root
        self.iiif_mapping = iiif_mapping
        self.ids = {}
        self._seen_keys = {}
        # First-wins ID index (audit 3.5): find() scanned the whole ALTO
        # tree once per line/string/glyph — 26 % of worker time. find()
        # returns the first match, so the index keeps the first too.
        self._by_id = {}
        for el in alto_root.iter():
            el_id = el.get("ID")
            if el_id is not None:
                self._by_id.setdefault(el_id, el)

    def _uuid(self, prefix, *parts):
        """
        Deterministic identifier for one sourceDoc element.

        uuid5 over (document, folio, prefix, ALTO ids...): the same input
        yields the same TEI ids on every run (audit 2.8), which is what
        makes golden-file diffs and stable external references possible.
        The joined key mirrors what self.ids uses as registry key.

        Args:
            prefix (str): Type prefix (e.g., "zone_", "line_").
            *parts: ALTO identifiers (and role tokens) that make this
                element unique within the page.

        Returns:
            str: Prefixed, deterministic identifier.
        """
        key = (prefix, parts)
        occurrence = self._seen_keys.get(key, 0)
        self._seen_keys[key] = occurrence + 1
        if occurrence:
            # Real ALTO exports do duplicate element IDs (whole duplicated
            # blocks — hundreds of hits on some corpus pages). uuid4
            # silently papered over it; the deterministic id disambiguates
            # by document-order occurrence, so it stays reproducible. No
            # per-occurrence log here: the worker reports one aggregated
            # warning per page (a logger call in a forkserver worker
            # would not reach the parent's log anyway).
            parts = (*parts, f"dup{occurrence}")
        name = "\x1f".join((self.doc, self.folio, prefix, *map(str, parts)))
        return prefix + uuid.uuid5(UUID_NAMESPACE, name).hex

    def surface(self, page_attributes):
        """
        Create a <surface> element for this page.

        Args:
            page_attributes (dict): Attributes for the surface element.

        Returns:
            etree.Element: The created <surface> element.
        """
        folio_id = xml_id_safe(self.folio)
        surface = etree.Element(
            "surface",
            {XML_ID: folio_id, **page_attributes},
        )
        xml_id = surface.get(XML_ID, folio_id)

        # Add IIIF graphic element if mapping is available
        if self.iiif_mapping and self.iiif_mapping.has_mapping():
            graphic_url = self.iiif_mapping.get_url(xml_id)
            if graphic_url:
                etree.SubElement(surface, "graphic", url=graphic_url)

        return surface

    def zone1(self, surface, attributes, block_id, blocks_on_page):
        """
        Create a <zone> element for a TextBlock.

        Args:
            surface (etree.Element): Parent <surface> element.
            attributes (dict): Zone attributes (type, coords, etc.).
            block_id (str): ALTO TextBlock ID.
            blocks_on_page: Page context (unused, kept for compatibility).

        Returns:
            etree.Element: The created <zone> element.
        """
        zone_uuid = self._uuid("zone_", block_id)
        self.ids[("block", self.folio, block_id)] = zone_uuid

        zone = etree.SubElement(surface, "zone", {XML_ID: zone_uuid})

        for key, value in attributes.items():
            zone.attrib[key] = value

        return zone

    def zone2(self, textblock, block_parent, attributes, line_id, lines_on_page):
        """
        Create a <zone> element for a TextLine with baseline path.

        Args:
            textblock (etree.Element): Parent TextBlock <zone> element.
            block_parent (str): Parent block ID.
            attributes (dict): Zone attributes (type, coords, etc.).
            line_id (str): ALTO TextLine ID.
            lines_on_page: Page context (unused, kept for compatibility).

        Returns:
            etree.Element: The created <zone> element.
        """
        zone_uuid = self._uuid("zoneLine_", block_parent, line_id)
        self.ids[("linezone", self.folio, block_parent, line_id)] = zone_uuid

        zone = etree.SubElement(textblock, "zone", {XML_ID: zone_uuid})

        for key, value in attributes.items():
            # Normalize "default" type to "DefaultLine"
            if key == "type" and value == "default":
                value = "DefaultLine"
            zone.attrib[key] = value

        # Add baseline <path> element
        path_uuid = self._uuid("path_", block_parent, line_id)
        baseline = etree.SubElement(zone, "path", {XML_ID: path_uuid})

        # Get baseline points from ALTO (indexed lookup, audit 3.5)
        textline = self._by_id.get(line_id)
        if textline is not None and etree.QName(textline).localname != "TextLine":
            textline = None
        if textline is not None:
            baseline_str = textline.get("BASELINE", "")
            baseline.attrib["points"] = format_alto_points(baseline_str)

        return zone

    def line(self, textline, block_parent, line_parent, lines_on_page, extracted_words):
        """
        Create a <line> element with text content.

        Args:
            textline (etree.Element): Parent TextLine <zone> element.
            block_parent (str): Parent block ID.
            line_parent (str): Parent line ID.
            lines_on_page (int): Line number on the page.
            extracted_words (str): Pre-extracted text content.

        Returns:
            etree.Element: The created <line> element.
        """
        line_uuid = self._uuid("line_", block_parent, line_parent)
        self.ids[("line", self.folio, block_parent, line_parent)] = line_uuid

        line_el = etree.SubElement(textline, "line", {XML_ID: line_uuid})
        line_el.attrib["n"] = str(lines_on_page)

        # Use pre-extracted text or fall back to ALTO String content
        if extracted_words:
            line_el.text = extracted_words
        else:
            parent_line = self._by_id.get(line_parent)
            string_el = (
                parent_line.find("a:String", namespaces=NS_ALTO)
                if parent_line is not None else None
            )
            if string_el is not None:
                line_el.text = string_el.get("CONTENT", "")

        return line_el

    def zone3(self, textline, block_parent, line_parent, attributes, seg_id, strings_on_page):
        """
        Create a <zone> element for a String/word with confidence data.

        Args:
            textline (etree.Element): Parent TextLine <zone> element.
            block_parent (str): Parent block ID.
            line_parent (str): Parent line ID.
            attributes (dict): Zone attributes.
            seg_id (str): ALTO String ID.
            strings_on_page: Count context (unused).

        Returns:
            etree.Element: The created <zone> element.
        """
        string_uuid = self._uuid("string_", block_parent, line_parent, seg_id)
        self.ids[("string", self.folio, block_parent, line_parent, seg_id)] = string_uuid

        zone = etree.SubElement(textline, "zone", {XML_ID: string_uuid})
        for key, value in attributes.items():
            zone.attrib[key] = value

        # Add confidence data if available (indexed lookup, audit 3.5)
        alto_string = self._by_id.get(seg_id)
        if alto_string is not None and etree.QName(alto_string).localname != "String":
            alto_string = None
        if alto_string is not None:
            wc = alto_string.get("WC")
            if wc:
                cert_uuid = self._uuid("cert_", block_parent, line_parent, seg_id)
                etree.SubElement(
                    zone,
                    "certainty",
                    {
                        XML_ID: cert_uuid,
                        "locus": "value",
                        "degree": wc,
                        "target": f"#{string_uuid}",
                    },
                )

        return zone

    def zone4(
        self, string, block_parent, line_parent, seg_parent, attributes, glyph_id, glyphs_on_page
    ):
        """
        Create a <zone> element for a Glyph.

        Args:
            string (etree.Element): Parent String <zone> element.
            block_parent (str): Parent block ID.
            line_parent (str): Parent line ID.
            seg_parent (str): Parent string ID.
            attributes (dict): Zone attributes.
            glyph_id (str): ALTO Glyph ID.
            glyphs_on_page: Count context (unused).

        Returns:
            etree.Element: The created <zone> element.
        """
        glyph_uuid = self._uuid("glyph_", block_parent, line_parent, seg_parent, glyph_id)
        self.ids[("glyphzone", self.folio, block_parent, line_parent, seg_parent, glyph_id)] = (
            glyph_uuid
        )

        zone = etree.SubElement(string, "zone", {XML_ID: glyph_uuid})
        for key, value in attributes.items():
            zone.attrib[key] = value

        # Add confidence data if available
        alto_glyph = self._by_id.get(glyph_id)
        if alto_glyph is not None and etree.QName(alto_glyph).localname != "Glyph":
            alto_glyph = None
        if alto_glyph is not None:
            gc = alto_glyph.get("GC")
            if gc:
                cert_uuid = self._uuid(
                    "cert_", block_parent, line_parent, seg_parent, glyph_id, "zone"
                )
                etree.SubElement(
                    zone,
                    "certainty",
                    {
                        XML_ID: cert_uuid,
                        "locus": "value",
                        "degree": gc,
                        "target": f"#{glyph_uuid}",
                    },
                )

        return zone

    def car(self, zone, glyph, block_parent, line_parent, seg_parent, glyph_id, glyphs_on_page):
        """
        Create a <c> (character) element for a Glyph.

        Args:
            zone (etree.Element): Parent Glyph <zone> element.
            glyph: ALTO Glyph element.
            block_parent (str): Parent block ID.
            line_parent (str): Parent line ID.
            seg_parent (str): Parent string ID.
            glyph_id (str): ALTO Glyph ID.
            glyphs_on_page: Count context (unused).

        Returns:
            etree.Element: The created <c> element.
        """
        car_uuid = self._uuid("car_", block_parent, line_parent, seg_parent, glyph_id)
        self.ids[("car", self.folio, block_parent, line_parent, seg_parent, glyph_id)] = car_uuid

        car_el = etree.SubElement(zone, "c", {XML_ID: car_uuid})

        # Add confidence data if available. GC (glyph confidence), the same
        # source as the glyph's <zone>: reading WC here gave the same glyph
        # two different certainty degrees (audit 5.4). Read straight off
        # the `glyph` element already passed in (also used for CONTENT
        # below) — a second full-tree lookup by ID was one more way for
        # the two readings to diverge.
        if glyph is not None:
            gc = glyph.get("GC")
            if gc:
                cert_uuid = self._uuid(
                    "cert_", block_parent, line_parent, seg_parent, glyph_id, "c"
                )
                etree.SubElement(
                    car_el,
                    "certainty",
                    {
                        XML_ID: cert_uuid,
                        "locus": "value",
                        "degree": gc,
                        "target": f"#{car_uuid}",
                    },
                )

        car_el.text = glyph.attrib.get("CONTENT", "")
        return car_el
