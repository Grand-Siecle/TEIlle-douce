# -----------------------------------------------------------
# Code by: Kelly Christensen
# Builds the default structure of a <teiHeader> with empty placeholders.
# -----------------------------------------------------------
"""
Default TEI Header structure builder.

This module creates the skeleton of a TEI header with all required elements
but with placeholder text. The structure is later populated with actual
metadata by the FullTree class.
"""

import re
from datetime import datetime
from collections import defaultdict
from lxml import etree

from config import (
    SEGMONTO,
    PLACEHOLDER_INFO_UNAVAILABLE,
    PLACEHOLDER_NO_METADATA,
)


def _parse_document_id(document_name):
    """
    Parse document name to extract internal ID and volume.

    Examples:
        "LIV0326_v2_altos_transcribed" -> ("LIV0326", "v2")
        "LIV0123_t1_something" -> ("LIV0123", "t1")
        "ABC999_b_folder" -> ("ABC999", "b")

    Args:
        document_name (str): The document folder/file name.

    Returns:
        tuple: (internal_id, volume) - volume may be None if not found.
    """
    # Pattern: capture ID (letters + digits) then optional volume marker
    # Volume patterns: v1, v2, t1, t2, b, etc.
    match = re.match(r"([A-Za-z]+\d+)(?:_([vVtTbB]\d*|[bB]))?", document_name)
    if match:
        internal_id = match.group(1)
        volume = match.group(2)
        return internal_id, volume
    return document_name, None


class DefaultTree:
    """
    Builds the default <teiHeader> structure with empty placeholders.

    The header includes:
    - fileDesc: bibliographic description
    - profileDesc: language usage
    - encodingDesc: application info and taxonomy

    Attributes:
        children (dict): Dictionary of created elements for later population.
    """

    def __init__(self, config, document, root, metadata, count_pages, versions):
        """
        Initialize the DefaultTree builder.

        Args:
            config (dict): Pipeline configuration dictionary.
            document (str): Document name/identifier.
            root (etree.Element): XML-TEI root element.
            metadata (dict): Metadata dictionary with 'sru' and 'iiif' keys.
            count_pages (int): Number of pages in the document.
            versions (dict): Application version strings.
        """
        self.config = config
        self.document = document
        self.root = root
        self.sru = metadata["sru"]
        self.iiif = metadata["iiif"]
        self.count = str(count_pages)
        self.versions = versions
        # Instance-level children dict (not class-level to avoid shared state)
        self.children = defaultdict(list)

    def build(self):
        """
        Build the complete default <teiHeader> structure.

        Creates all required TEI header elements with placeholder text.
        Elements that need to be populated later are stored in self.children.
        """
        # Determine default text based on metadata availability
        if self.sru and self.sru.get("found"):
            default_text = PLACEHOLDER_INFO_UNAVAILABLE
            num_authors = len(self.sru.get("authors", []))
        else:
            default_text = PLACEHOLDER_NO_METADATA
            num_authors = 1

        # Build main header structure
        teiHeader = etree.SubElement(self.root, "teiHeader")

        # Three main children of <teiHeader>
        fileDesc = etree.SubElement(teiHeader, "fileDesc")
        profileDesc = etree.SubElement(teiHeader, "profileDesc")
        encodingDesc = etree.SubElement(teiHeader, "encodingDesc")

        # Build <fileDesc>
        self._build_file_desc(fileDesc, default_text, num_authors)

        # Build <profileDesc>
        self._build_profile_desc(profileDesc)

        # Build <encodingDesc>
        self._build_encoding_desc(encodingDesc)

    def _build_file_desc(self, fileDesc, default_text, num_authors):
        """Build the <fileDesc> section with title, publication, and source info."""
        # <titleStmt>
        titleStmt = etree.SubElement(fileDesc, "titleStmt")
        self.children["titleStmt"] = titleStmt

        self.children["ts_title"] = etree.SubElement(titleStmt, "title")
        self.children["ts_title"].text = default_text

        # Author elements
        for _ in range(num_authors):
            etree.SubElement(titleStmt, "author")
        if num_authors == 0:
            ts_author = etree.SubElement(titleStmt, "author")
            ts_author.text = default_text

        # Responsibility statement
        respStmt = etree.SubElement(titleStmt, "respStmt")
        resp = etree.SubElement(respStmt, "resp")
        resp.text = self.config["responsibility"]["text"]

        for resp_person in self.config["responsibility"]["resp"]:
            persName = etree.SubElement(respStmt, "persName")
            forename = etree.SubElement(persName, "forename")
            forename.text = resp_person["forename"]
            surname = etree.SubElement(persName, "surname")
            surname.text = resp_person["surname"]
            etree.SubElement(persName, "ptr", resp_person["ptr"])

        # <extent>
        extent = etree.SubElement(fileDesc, "extent")
        etree.SubElement(extent, "measure", unit="images", n=self.count)

        # <publicationStmt>
        publicationStmt = etree.SubElement(fileDesc, "publicationStmt")
        publisher = etree.SubElement(publicationStmt, "publisher")
        publisher.text = self.config["responsibility"]["publisher"]
        authority = etree.SubElement(publicationStmt, "authority")
        authority.text = self.config["responsibility"]["authority"]
        availability = etree.SubElement(
            publicationStmt, "availability", self.config["responsibility"]["availability"]
        )
        etree.SubElement(availability, "licence", self.config["responsibility"]["licence"])
        today = datetime.today().strftime("%Y-%m-%d")
        etree.SubElement(publicationStmt, "date", when=today)

        # <sourceDesc>
        self._build_source_desc(fileDesc, default_text, num_authors)

    def _build_source_desc(self, fileDesc, default_text, num_authors):
        """Build the <sourceDesc> section with bibliographic and manuscript info."""
        sourceDesc = etree.SubElement(fileDesc, "sourceDesc")

        # <bibl>
        bibl = etree.SubElement(sourceDesc, "bibl")
        self.children["bibl"] = bibl
        self.children["ptr"] = etree.SubElement(bibl, "ptr")

        for _ in range(num_authors):
            etree.SubElement(bibl, "author")
        if num_authors == 0:
            bib_author = etree.SubElement(bibl, "author")
            bib_author.text = default_text

        self.children["bib_title"] = etree.SubElement(bibl, "title")
        self.children["bib_title"].text = default_text

        self.children["pubPlace"] = etree.SubElement(bibl, "pubPlace")
        self.children["pubPlace"].text = default_text

        self.children["publisher"] = etree.SubElement(bibl, "publisher")
        self.children["publisher"].text = default_text

        self.children["date"] = etree.SubElement(bibl, "date")
        self.children["date"].text = default_text

        # <msDesc>
        msDesc = etree.SubElement(sourceDesc, "msDesc")
        msIdentifier = etree.SubElement(msDesc, "msIdentifier")

        self.children["country"] = etree.SubElement(msIdentifier, "country")
        self.children["settlement"] = etree.SubElement(msIdentifier, "settlement")
        self.children["settlement"].text = default_text

        self.children["repository"] = etree.SubElement(msIdentifier, "repository")
        self.children["repository"].text = default_text

        self.children["idno"] = etree.SubElement(msIdentifier, "idno")
        self.children["idno"].text = default_text

        # Parse document name for internal ID and volume
        internal_id, volume = _parse_document_id(self.document)

        # altIdentifier with combined internal ID (e.g., "LIV0326_v2")
        altIdentifier = etree.SubElement(msIdentifier, "altIdentifier")
        alt_idno = etree.SubElement(altIdentifier, "idno", type="internal")
        if volume:
            alt_idno.text = f"{internal_id}_{volume}"
        else:
            alt_idno.text = internal_id

        physDesc = etree.SubElement(msDesc, "physDesc")
        objectDesc = etree.SubElement(physDesc, "objectDesc")
        self.children["p"] = etree.SubElement(objectDesc, "p")
        self.children["p"].text = default_text

    def _build_profile_desc(self, profileDesc):
        """Build the <profileDesc> section with language info."""
        langUsage = etree.SubElement(profileDesc, "langUsage")
        self.children["language"] = etree.SubElement(langUsage, "language")
        self.children["language"].attrib["ident"] = ""

    def _build_encoding_desc(self, encodingDesc):
        """Build the <encodingDesc> section with application info and taxonomy."""
        # <appInfo>
        appInfo = etree.SubElement(encodingDesc, "appInfo")

        # Build application entries from config
        for app_key, app_data in self.versions.items():
            app_el = etree.SubElement(appInfo, "application")
            app_el.attrib["ident"] = app_data["ident"]
            app_el.attrib["version"] = app_data["version"]
            label = etree.SubElement(app_el, "label")
            label.text = app_data["label"]
            ptr = etree.SubElement(app_el, "ptr")
            ptr.attrib["target"] = app_data["url"]

        # <classDecl> for SegmOnto taxonomy
        classDecl = etree.SubElement(encodingDesc, "classDecl")
        taxonomy_id = {"{http://www.w3.org/XML/1998/namespace}id": SEGMONTO["id"]}
        self.children["taxonomy"] = etree.SubElement(classDecl, "taxonomy", taxonomy_id)

        tax_bibl = etree.SubElement(self.children["taxonomy"], "bibl")
        tax_title = etree.SubElement(tax_bibl, "title")
        tax_title.text = SEGMONTO["id"]
        tax_ptr = etree.SubElement(tax_bibl, "ptr")
        tax_ptr.attrib["target"] = SEGMONTO["url"]
