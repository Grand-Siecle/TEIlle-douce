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
        self.data, self.graphics, self.front_pages = self._extract()

    def _extract(self):
        """
        Walk the sourceDoc once, collecting everything the body needs.

        Returns:
            tuple: (lines, graphics, front_pages)

        Lines and graphics come out of the SAME traversal on purpose: a
        graphic is placed by the number of lines preceding it, so two
        walks counting lines by different rules would misplace — and then
        silently drop — every figure past the first discrepancy.

        A GraphicZone often holds no TextLine at all: walking lines alone,
        an illustration leaves no trace in the body even though the
        sourceDoc has its coordinates and its IIIF crop URL.
        """
        lines = []
        graphics = []
        page_zone_types = {}  # surface xml:id -> set of zone types

        for el in self.root.iter():
            tag = local_tag(el.tag)

            if tag == "line":
                lines.append(self._line_of(el))
                continue
            if tag != "zone":
                continue

            zone_type = el.get("type")
            surface = self._surface_of(el)
            page_id = surface.get(XML_ID) if surface is not None else None
            if page_id is not None:
                page_zone_types.setdefault(page_id, set()).add(zone_type)

            if zone_type in GRAPHIC_ZONES:
                graphics.append(
                    Graphic(
                        zone_id=el.get(XML_ID),
                        zone_type=zone_type,
                        source=el.get("source"),
                        page_id=page_id,
                        page_n=surface.get("n") if surface is not None else None,
                        after_lines=len(lines),
                    )
                )

        # Front matter is a page given over to a title page. A page that
        # also carries running text is NOT one: the corpus has an inner
        # title page (LIV0039b f848) sitting among MainZone and margin
        # notes at page 848 — hoisting it, and its running text with it,
        # to the head of the volume would be a plain falsification.
        front_pages = {
            page_id
            for page_id, types in page_zone_types.items()
            if types & FRONT_ZONES
            and not any((t or "").startswith("Main") for t in types)
        }

        return lines, graphics, front_pages

    def _line_of(self, ln):
        """Build the Line namedtuple for one <line> element."""
        # line -> zone (TextLine) -> zone (TextBlock) -> surface
        line_zone = ln.getparent()
        text_block = line_zone.getparent() if line_zone is not None else None
        surface = text_block.getparent() if text_block is not None else None

        return Line(
            id=line_zone.get(XML_ID) if line_zone is not None else None,
            n=ln.get("n"),
            text=ln.text or "",
            line_type=line_zone.get("type") if line_zone is not None else None,
            zone_type=text_block.get("type") if text_block is not None else None,
            zone_id=text_block.get(XML_ID) if text_block is not None else None,
            page_id=surface.get(XML_ID) if surface is not None else None,
            page_n=surface.get("n") if surface is not None else None,
        )

    @staticmethod
    def _surface_of(el):
        """Walk up to the enclosing <surface>, or None."""
        parent = el.getparent()
        while parent is not None and local_tag(parent.tag) != "surface":
            parent = parent.getparent()
        return parent

