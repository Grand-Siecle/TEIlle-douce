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


# Named tuple for line data. page_n defaults to None so hand-built Line
# objects (tests, external callers) stay valid without it.
Line = namedtuple(
    "Line",
    ["id", "n", "text", "line_type", "zone_type", "zone_id", "page_id", "page_n"],
    defaults=(None,),
)


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
