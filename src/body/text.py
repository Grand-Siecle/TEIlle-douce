# -----------------------------------------------------------
# Code by: Kelly Christensen
# Extracts and stores text line data from the <sourceDoc>.
# -----------------------------------------------------------
"""
Text extraction module.

This module provides the Text class which extracts text line data from
the built sourceDoc for use in body construction.
"""

from collections import namedtuple

from ..constants import XML_ID
from ..utils.xml import local_tag


# Named tuple for line data. page_n defaults to None so hand-built Line
# objects (tests, external callers) stay valid without it.
Line = namedtuple(
    "Line",
    ["id", "n", "text", "line_type", "zone_type", "zone_id", "page_id", "page_n"],
    defaults=(None,),
)

# A zone that carries an image rather than (only) text. `after_lines` is
# the number of lines extracted before it, i.e. where it belongs in
# reading order — a GraphicZone holding no TextLine has no line of its
# own to hang from, and used to leave no trace at all in the body.
# Shares page_id/page_n with Line so both can feed <pb> construction.
Graphic = namedtuple(
    "Graphic",
    ["zone_id", "zone_type", "source", "page_id", "page_n", "after_lines"],
)

# SegmOnto zones whose content belongs to the front matter rather than
# the running text: a page carrying one is routed to <text><front>.
FRONT_ZONES = frozenset({"TitlePageZone"})

# SegmOnto zones rendered as <figure> in the body.
GRAPHIC_ZONES = frozenset({"GraphicZone"})


class Text:
    """
    Extracts text line data from a TEI sourceDoc.

    Parses all <line> elements from the sourceDoc and extracts their
    content and contextual information for use in body construction.

    Attributes:
        root (etree.Element): TEI root element containing sourceDoc.
        data (list): List of Line namedtuples with extracted data.
    """

    def __init__(self, root):
        """
        Initialize and extract text data from the TEI tree.

        Args:
            root (etree.Element): TEI root element containing sourceDoc.
        """
        self.root = root
        self.data = self._extract_lines()
        self.graphics, self.front_pages = self._extract_zones()

    def _extract_zones(self):
        """
        Collect the zones the body needs but no line can announce.

        Returns:
            tuple: (graphics, front_pages)
                graphics: list of Graphic, in reading order.
                front_pages: set of surface xml:id carrying front matter.

        A GraphicZone often holds no TextLine at all: walking lines alone,
        an illustration leaves no trace in the body even though the
        sourceDoc has its coordinates and its IIIF crop URL.
        """
        graphics = []
        front_pages = set()
        seen_lines = 0

        for el in self.root.iter():
            tag = local_tag(el.tag)
            if tag == "line":
                seen_lines += 1
                continue
            if tag != "zone":
                continue
            zone_type = el.get("type")
            if zone_type in FRONT_ZONES:
                surface = self._surface_of(el)
                if surface is not None:
                    front_pages.add(surface.get(XML_ID))
            elif zone_type in GRAPHIC_ZONES:
                surface = self._surface_of(el)
                graphics.append(
                    Graphic(
                        zone_id=el.get(XML_ID),
                        zone_type=zone_type,
                        source=el.get("source"),
                        page_id=surface.get(XML_ID) if surface is not None else None,
                        page_n=surface.get("n") if surface is not None else None,
                        after_lines=seen_lines,
                    )
                )

        return graphics, front_pages

    @staticmethod
    def _surface_of(el):
        """Walk up to the enclosing <surface>, or None."""
        parent = el.getparent()
        while parent is not None and local_tag(parent.tag) != "surface":
            parent = parent.getparent()
        return parent

    def _extract_lines(self):
        """
        Extract contextual and attribute data for each text line.

        Parses all <line> elements in the sourceDoc and creates Line
        namedtuples containing:
        - id: The line zone's xml:id
        - n: Line number
        - text: Text content
        - line_type: Type of the line (DefaultLine, HeadingLine, etc.)
        - zone_type: Type of the parent zone (MainZone, NumberingZone, etc.)
        - zone_id: The parent zone's xml:id
        - page_id: The page surface's xml:id
        - page_n: The page surface's number (@n), for <pb n="..."> (audit 1.6)

        Returns:
            list: List of Line namedtuples for each text line.
        """
        lines = []

        for ln in self.root.findall(".//line"):
            # Navigate up the tree to get parent element info
            # line -> zone (TextLine) -> zone (TextBlock) -> surface
            line_zone = ln.getparent()
            text_block = line_zone.getparent() if line_zone is not None else None
            surface = text_block.getparent() if text_block is not None else None

            lines.append(
                Line(
                    id=line_zone.get(XML_ID) if line_zone is not None else None,
                    n=ln.get("n"),
                    text=ln.text or "",
                    line_type=line_zone.get("type") if line_zone is not None else None,
                    zone_type=text_block.get("type") if text_block is not None else None,
                    zone_id=text_block.get(XML_ID) if text_block is not None else None,
                    page_id=surface.get(XML_ID) if surface is not None else None,
                    page_n=surface.get("n") if surface is not None else None,
                )
            )

        return lines
