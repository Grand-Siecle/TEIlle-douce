# -----------------------------------------------------------
# Code by: Kelly Christensen
# Parses ALTO element attributes and maps them to TEI attributes.
# -----------------------------------------------------------
"""
ALTO attribute extraction and TEI mapping module.

This module provides the Attributes class which extracts coordinate and
type information from ALTO elements and maps them to TEI attribute format.
"""

import re
from collections import namedtuple

from lxml import etree

from ..constants import NS_ALTO, XML_ID
from ..utils.xml import xml_id_safe


# Named tuple for zone data
ZoneData = namedtuple("ZoneData", ["attributes", "id"])


def format_alto_points(raw):
    """Convert an ALTO points string ("x y x y" or "x,y x,y", int or float)
    to the TEI "x,y x,y" integer format.

    Args:
        raw (str): Raw ALTO points string.

    Returns:
        str: Formatted points in TEI format ("x,y x,y ...").
    """
    if not raw:
        return ""
    nums = re.findall(r"-?\d+(?:\.\d+)?", raw)
    return " ".join(
        f"{int(float(nums[k]))},{int(float(nums[k + 1]))}"
        for k in range(0, len(nums) - 1, 2)
    )


class Attributes:
    """
    Parses ALTO element attributes and maps them to TEI format.

    This class handles the extraction of coordinates, types, and other
    attributes from ALTO elements and converts them to TEI attribute format.

    Attributes:
        doc (str): Document name.
        folio (str): Page/folio identifier.
        root (etree.Element): ALTO XML root element.
        tags (dict): Mapping of ALTO tag IDs to labels.
        image_base (str): Full IIIF image base URL derived from the volume's
            manifest (e.g. Gallica "https://gallica.bnf.fr/iiif/ark:/12148/bpt6k...").
            None when the image API cannot be derived from the manifest URL
            (e.g. non-Gallica servers) — in that case @source is omitted.
        view_number (int): 1-based ordinal of this page within the document,
            used as the IIIF view number (e.g. "f<N>") in the image URL.
    """

    def __init__(self, doc, folio, alto_root, tags, config):
        """
        Initialize the Attributes parser.

        Args:
            doc (str): Document name.
            folio (str): Page/folio identifier.
            alto_root (etree.Element): Parsed ALTO XML root element.
            tags (dict): Mapping of ALTO tag IDs to labels.
            config (dict): IIIF configuration with image_base and view_number.
        """
        self.doc = doc
        self.folio = folio
        self.root = alto_root
        self.tags = tags
        self.image_base = config.get("image_base")  # e.g. https://gallica.bnf.fr/iiif/ark:/12148/bpt6k106230g
        self.view_number = config.get("view_number")  # 1-based ordinal of the page in the document

    def surface(self):
        """
        Create attributes for the TEI <surface> element from ALTO <Page>.

        Extracts page dimensions from the ALTO Page element and converts
        them to TEI surface coordinate attributes.

        Returns:
            dict: TEI surface attributes (xml:id, n, ulx, uly, lrx, lry).
        """
        # Get ALTO Page element attributes
        page_el = self.root.find(".//a:Page", namespaces=NS_ALTO)
        folio_id = xml_id_safe(self.folio)
        if page_el is None:
            return {XML_ID: folio_id, "n": "0", "ulx": "0", "uly": "0", "lrx": "0", "lry": "0"}

        att_list = page_el.attrib

        # Extract page number from folio name (handles "f12" or bare "12")
        match = re.match(r"f?(\d+)", str(self.folio))
        page_n = match.group(1) if match else "0"

        # Map ALTO attributes to TEI format
        return {
            XML_ID: folio_id,
            "n": page_n,
            "ulx": "0",
            "uly": "0",
            "lrx": att_list.get("WIDTH", "0"),
            "lry": att_list.get("HEIGHT", "0"),
        }

    def zones(self, parent, target, segmonto_labels, elements=None):
        """
        Extract zone data from ALTO TextBlock or TextLine elements.

        Args:
            parent (str): Parent element XPath (e.g., 'PrintSpace' or 'TextBlock[@ID="..."]').
            target (str): Target element name ('TextBlock' or 'TextLine').
            segmonto_labels (list): Valid SegmOnto labels for corresp linking.
            elements (list): Pre-collected target elements — skips the
                full-tree XPath scan (audit 3.5: one scan per TextBlock
                dominated worker time).

        Returns:
            list: List of ZoneData namedtuples with attributes and IDs.
        """
        output = []

        if elements is None:
            # Find all target elements under the parent
            xpath = f".//a:{parent}/a:{target}"
            elements = self.root.findall(xpath, namespaces=NS_ALTO)

        for element in elements:
            # Skip elements without ID
            if "ID" not in element.attrib:
                continue

            element_id = element.attrib["ID"]
            attributes = {}

            # Extract SegmOnto type information from TAGREFS
            if "TAGREFS" in element.attrib and element.attrib["TAGREFS"] in self.tags:
                tag = str(self.tags[element.attrib["TAGREFS"]])

                # Parse tag syntax: MainZone:column#1 -> (MainZone, column, 1)
                tag_parts = re.match(r"(\w+):?(\w+)?#?(\d?)?", tag)
                if tag_parts:
                    main_type = tag_parts.group(1) or "none"
                    attributes["type"] = main_type

                    # Add corresp link if type is in valid SegmOnto labels
                    if segmonto_labels and main_type in segmonto_labels:
                        attributes["corresp"] = f"#{main_type}"

                    attributes["subtype"] = tag_parts.group(2) or "none"
                    attributes["n"] = tag_parts.group(3) or "none"
            else:
                # Default type from element name
                main_type = etree.QName(element).localname
                if main_type == "SP":
                    main_type = "Space"
                attributes["type"] = main_type

            # Extract coordinate data
            if "HPOS" in element.attrib:
                x = element.attrib["HPOS"]
                y = element.attrib["VPOS"]
                w = element.attrib["WIDTH"]
                h = element.attrib["HEIGHT"]

                attributes["ulx"] = x
                attributes["uly"] = y

                # Calculate lower-right coordinates
                try:
                    attributes["lrx"] = str(int(w) + int(x))
                except ValueError:
                    attributes["lrx"] = str(int(float(w)) + int(float(x)))

                try:
                    attributes["lry"] = str(int(h) + int(y))
                except ValueError:
                    attributes["lry"] = str(int(float(h)) + int(float(y)))

            # Extract polygon points
            polygon = element.find(".//a:Polygon", namespaces=NS_ALTO)
            if polygon is not None and polygon.get("POINTS"):
                points = polygon.get("POINTS")
                attributes["points"] = format_alto_points(points)

            # Add IIIF image source URL — only when both a real image base
            # and a trustworthy 1-based view number are known. A view of 0
            # means the document's file numbering does not match IIIF view
            # ordinals, so no resolvable URL can be built.
            if "HPOS" in element.attrib and self.image_base and self.view_number:
                attributes["source"] = (
                    f"{self.image_base}/f{self.view_number}/"
                    f"{x},{y},{w},{h}/full/0/native.jpg"
                )

            output.append(ZoneData(attributes, element_id))

        return output
