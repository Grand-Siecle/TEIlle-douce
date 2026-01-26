# -----------------------------------------------------------
# Code by: Kelly Christensen
# Creates TEI elements inside <sourceDoc> and maps ALTO data to them.
# -----------------------------------------------------------
"""
SourceDoc element creation module.

This module provides the SurfaceTree class which creates TEI <surface>,
<zone>, <path>, and <line> elements from ALTO data.
"""

import re
import uuid

from lxml import etree

from ..constants import NS_ALTO, XML_ID


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

    def _uuid(self, prefix):
        """
        Generate a UUID with a type-specific prefix.

        Args:
            prefix (str): Prefix for the UUID (e.g., "zone_", "line_").

        Returns:
            str: Prefixed UUID string.
        """
        return prefix + uuid.uuid4().hex

    def surface(self, page_attributes):
        """
        Create a <surface> element for this page.

        Args:
            page_attributes (dict): Attributes for the surface element.

        Returns:
            etree.Element: The created <surface> element.
        """
        surface = etree.Element(
            "surface",
            {XML_ID: self.folio, **page_attributes},
        )
        xml_id = surface.get(XML_ID, self.folio)

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
        zone_uuid = self._uuid("zone_")
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
        zone_uuid = self._uuid("zoneLine_")
        self.ids[("linezone", self.folio, block_parent, line_id)] = zone_uuid

        zone = etree.SubElement(textblock, "zone", {XML_ID: zone_uuid})

        for key, value in attributes.items():
            # Normalize "default" type to "DefaultLine"
            if key == "type" and value == "default":
                value = "DefaultLine"
            zone.attrib[key] = value

        # Add baseline <path> element
        path_uuid = self._uuid("path_")
        baseline = etree.SubElement(zone, "path", {XML_ID: path_uuid})

        # Get baseline points from ALTO
        textline = self.root.find(f'.//a:TextLine[@ID="{line_id}"]', namespaces=NS_ALTO)
        if textline is not None:
            baseline_str = textline.get("BASELINE", "")
            # Convert "x y x y" format to "x,y x,y" format
            points = " ".join(
                [re.sub(r"\s", ",", x) for x in re.findall(r"(\d+ \d+)", baseline_str)]
            )
            baseline.attrib["points"] = points

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
        line_uuid = self._uuid("line_")
        self.ids[("line", self.folio, block_parent, line_parent)] = line_uuid

        line_el = etree.SubElement(textline, "line", {XML_ID: line_uuid})
        line_el.attrib["n"] = str(lines_on_page)

        # Use pre-extracted text or fall back to ALTO String content
        if extracted_words:
            line_el.text = extracted_words
        else:
            string_el = self.root.find(
                f'.//a:TextLine[@ID="{line_parent}"]/a:String', namespaces=NS_ALTO
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
        string_uuid = self._uuid("string_")
        self.ids[("string", self.folio, block_parent, line_parent, seg_id)] = string_uuid

        zone = etree.SubElement(textline, "zone", {XML_ID: string_uuid})
        for key, value in attributes.items():
            zone.attrib[key] = value

        # Add confidence data if available
        alto_string = self.root.find(f'.//a:String[@ID="{seg_id}"]', namespaces=NS_ALTO)
        if alto_string is not None:
            wc = alto_string.get("WC")
            if wc:
                cert_uuid = self._uuid("cert_")
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
        glyph_uuid = self._uuid("glyph_")
        self.ids[("glyphzone", self.folio, block_parent, line_parent, seg_parent, glyph_id)] = (
            glyph_uuid
        )

        zone = etree.SubElement(string, "zone", {XML_ID: glyph_uuid})
        for key, value in attributes.items():
            zone.attrib[key] = value

        # Add confidence data if available
        alto_glyph = self.root.find(f'.//a:Glyph[@ID="{glyph_id}"]', namespaces=NS_ALTO)
        if alto_glyph is not None:
            gc = alto_glyph.get("GC")
            if gc:
                cert_uuid = self._uuid("cert_")
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
        car_uuid = self._uuid("car_")
        self.ids[("car", self.folio, block_parent, line_parent, seg_parent, glyph_id)] = car_uuid

        car_el = etree.SubElement(zone, "c", {XML_ID: car_uuid})

        # Add confidence data if available
        alto_glyph = self.root.find(f'.//a:Glyph[@ID="{glyph_id}"]', namespaces=NS_ALTO)
        if alto_glyph is not None:
            wc = alto_glyph.get("WC")
            if wc:
                cert_uuid = self._uuid("cert_")
                etree.SubElement(
                    car_el,
                    "certainty",
                    {
                        XML_ID: cert_uuid,
                        "locus": "value",
                        "degree": wc,
                        "target": f"#{car_uuid}",
                    },
                )

        car_el.text = glyph.attrib.get("CONTENT", "")
        return car_el
