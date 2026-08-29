# -----------------------------------------------------------
# Code by: Kelly Christensen
# Populates the default <teiHeader> structure with actual metadata.
# -----------------------------------------------------------
"""
TEI Header metadata population module.

This module takes the default header structure created by DefaultTree
and populates it with actual metadata from CSV or IIIF sources.
"""

import logging
import re
from collections import namedtuple
from lxml import etree

from config import SEGMONTO
from ..constants import NS_ALTO, XML_ID, SEGMONTO_ZONES, SEGMONTO_LINES

logger = logging.getLogger(__name__)


_LABEL_PARSER = etree.XMLParser(huge_tree=True)


def _extract_labels(filepath):
    """
    Extract SegmOnto labels from an ALTO file.

    A file that does not parse contributes no labels instead of killing
    the whole document: its fate is decided page by page in the sourcedoc
    workers, not here. OtherTag entries without ID or LABEL are skipped.

    Args:
        filepath: Path to the ALTO XML file.

    Returns:
        dict: Mapping of element IDs to their labels.
    """
    try:
        root = etree.parse(str(filepath), parser=_LABEL_PARSER).getroot()
    except etree.XMLSyntaxError as e:
        logger.warning("No labels read from %s (unparseable: %s)", filepath, e)
        return {}
    elements = [t.attrib for t in root.findall(".//a:OtherTag", namespaces=NS_ALTO)]
    return {d["ID"]: d["LABEL"] for d in elements if "ID" in d and "LABEL" in d}


class FullTree:
    """
    Populates the default TEI header structure with metadata.

    This class fills in the placeholder elements created by DefaultTree
    with actual data from CSV or IIIF metadata sources.
    """

    def __init__(self, children, metadata):
        """
        Initialize the FullTree populator.

        Args:
            children (dict): Dictionary of elements from DefaultTree.
            metadata (dict): Metadata dictionary with 'sru' and 'iiif' keys.
        """
        self.children = children
        self.sru = metadata.get("sru") or {}
        self.iiif = metadata.get("iiif") or {}

    def author_data(self):
        """
        Populate author information in <titleStmt> and <bibl>.

        Uses SRU metadata if available, otherwise falls back to IIIF data.
        """
        if self.sru.get("found"):
            self._populate_authors(self.children["titleStmt"], is_first_id=True)
            self._populate_authors(self.children["bibl"], is_first_id=False)
        else:
            self._populate_authors(self.children["titleStmt"], is_first_id=True, use_iiif=True)
            self._populate_authors(self.children["bibl"], is_first_id=False, use_iiif=True)

    def _populate_authors(self, parent, is_first_id, use_iiif=False):
        """
        Create author elements in the given parent element.

        Args:
            parent (etree.Element): Parent element (<titleStmt> or <bibl>).
            is_first_id (bool): If True, use xml:id; if False, use ref attribute.
            use_iiif (bool): If True, use IIIF data instead of SRU data.
        """
        author_el = parent.find("./author")
        if author_el is None or author_el.text:
            return  # No author element or already has default text

        if use_iiif:
            creator = self.iiif.get("Creator")
            if creator:
                if is_first_id:
                    author_el.attrib[XML_ID] = f"{creator[:2]}"
                else:
                    author_el.attrib["ref"] = f"#{creator[:2]}"
                name = etree.SubElement(author_el, "name")
                name.text = creator
        else:
            authors = self.sru.get("authors", [])
            for count, author_root in enumerate(parent.findall("./author")):
                if count >= len(authors):
                    break
                author = authors[count]

                if is_first_id:
                    author_root.attrib[XML_ID] = author.get("xmlid", f"au{count}")
                else:
                    ref = author.get("xmlid", f"au{count}")
                    author_root.attrib["ref"] = f"#{ref}"

                persname = etree.SubElement(author_root, "persName")

                if author.get("secondary_name"):
                    forename = etree.SubElement(persname, "forename")
                    forename.text = author["secondary_name"]

                if author.get("namelink"):
                    namelink = etree.SubElement(persname, "nameLink")
                    namelink.text = author["namelink"]

                if author.get("primary_name"):
                    surname = etree.SubElement(persname, "surname")
                    surname.text = author["primary_name"]

                if author.get("isni"):
                    ptr = etree.SubElement(persname, "ptr")
                    ptr.attrib["type"] = "isni"
                    ptr.attrib["target"] = author["isni"]

    def bib_data(self):
        """
        Populate bibliographic data in the header.

        Maps metadata fields to their corresponding TEI elements.
        Prefers SRU data over IIIF data when both are available.
        """
        Entry = namedtuple("Entry", ["tei_element", "attribute", "iiif_data", "sru_data"])

        entries = [
            # Title in <titleStmt>
            Entry("ts_title", None, "Title", "title"),
            # Pointer in <bibl>
            Entry("ptr", "target", None, "ptr"),
            # Title in <bibl>
            Entry("bib_title", None, "Title", "title"),
            # Publication place
            Entry("pubPlace", None, None, "place"),
            # Publisher
            Entry("publisher", None, None, "publisher"),
            # Date
            Entry("date", None, "Date", "date"),
            # Repository
            Entry("repository", None, "Repository", "repository"),
            # Identifier
            Entry("idno", None, "Shelfmark", "idno"),
            # Language
            Entry("language", None, "Language", None),
            Entry("language", "ident", None, "language"),
        ]

        for entry in entries:
            element = self.children.get(entry.tei_element)
            if element is None:
                continue

            # Try SRU data first, then IIIF
            value = None
            if entry.sru_data and self.sru:
                value = self.sru.get(entry.sru_data)
            if not value and entry.iiif_data and self.iiif:
                value = self.iiif.get(entry.iiif_data)

            if value:
                self._set_entry(value, element, entry.attribute)

        # Fallback: BnF catalogue URL from the ark; drop the ptr if still empty
        ptr_el = self.children.get("ptr")
        if ptr_el is not None and not ptr_el.get("target"):
            ark = self.sru.get("ark")
            if ark and str(ark).startswith("ark:/12148/"):
                ptr_el.set("target", f"https://catalogue.bnf.fr/{ark}")
            else:
                parent = ptr_el.getparent()
                if parent is not None:
                    parent.remove(ptr_el)

    def _set_entry(self, data, tei_element, attribute):
        """
        Set the value of a TEI element.

        Args:
            data: The value to set.
            tei_element (etree.Element): The target element.
            attribute (str): If provided, set this attribute; otherwise set text.
        """
        if attribute:
            tei_element.attrib[attribute] = str(data)
        else:
            tei_element.text = str(data)

    def segmonto_taxonomy(self, filepaths):
        """
        Build the SegmOnto taxonomy from ALTO files.

        Extracts all zone and line labels from the ALTO files and adds
        corresponding entries to the TEI taxonomy.

        Args:
            filepaths (list): List of ALTO file paths.

        Returns:
            tuple: (document_zones, document_lines) - lists of labels found.
        """
        # Extract labels from all ALTO files
        all_tag_dicts = [_extract_labels(f) for f in filepaths]

        # Get unique main labels (before colon, if present)
        unique_labels = set()
        for dic in all_tag_dicts:
            for value in dic.values():
                match = re.match(r"(\w+):?(\w+)?#?(\d?)?", value)
                if match:
                    unique_labels.add(match.group(1))

        # Separate zones and lines. sorted(): unique_labels is a set whose
        # iteration order depends on PYTHONHASHSEED — without a stable
        # order, two runs produce their <catDesc> differently (audit 6.9).
        document_zones = sorted(label for label in unique_labels if "Zone" in label)
        document_lines = sorted(label for label in unique_labels if "Line" in label)

        # Add zone categories to taxonomy
        cat_id = {XML_ID: SEGMONTO["zones_category_id"]}
        category = etree.SubElement(self.children["taxonomy"], "category", cat_id)
        for zone in sorted(set(SEGMONTO_ZONES).intersection(document_zones)):
            self._add_taxonomy_category(category, zone, SEGMONTO_ZONES[zone])

        # Add line categories to taxonomy
        cat_id = {XML_ID: SEGMONTO["lines_category_id"]}
        category = etree.SubElement(self.children["taxonomy"], "category", cat_id)
        common_lines = sorted(set(SEGMONTO_LINES).intersection(document_lines))
        for line in common_lines:
            self._add_taxonomy_category(category, line, SEGMONTO_LINES[line])

        # Always include DefaultLine
        if "DefaultLine" not in common_lines:
            self._add_taxonomy_category(category, "DefaultLine", SEGMONTO_LINES["DefaultLine"])

        return document_zones, document_lines

    def _add_taxonomy_category(self, category, tag, url):
        """
        Add a category entry to the SegmOnto taxonomy.

        Args:
            category (etree.Element): Parent <category> element.
            tag (str): SegmOnto tag name.
            url (str): URL pointing to the tag documentation.
        """
        catDesc_id = {XML_ID: tag}
        catDesc = etree.SubElement(category, "catDesc", catDesc_id)

        title = etree.SubElement(catDesc, "title")
        title.text = tag

        ptr = etree.SubElement(catDesc, "ptr")
        ptr.attrib["target"] = url
