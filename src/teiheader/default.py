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
    KEYWORDS_TAXONOMY,
    SEGMONTO,
    PLACEHOLDER_INFO_UNAVAILABLE,
    PLACEHOLDER_NO_METADATA,
    PLACEHOLDER_ORCID,
    EDITORIAL_DECLARATIONS,
    LANG_USAGE_DESCRIPTION,
)
from ..constants import XML_ID
from ..utils.files import canonical_document_id


def tei_version_number(version):
    """
    Keep the leading numeric part of a version string.

    TEI requires @version on <application> to be a version NUMBER
    (teidata.versionNumber), and it is mandatory — so the corpus
    convention of writing an imprecise patch level ("4.3.x") made every
    output file fail tei_all validation. The numeric prefix is emitted
    instead ("4.3"), which is the strongest true statement available;
    the full configured string stays visible in config.APP_VERSIONS.
    """
    match = re.match(r"\d+(?:\.\d+)*", str(version or ""))
    return match.group(0) if match else "0"


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

        # Four main children of <teiHeader> (revisionDesc must come last, P5)
        fileDesc = etree.SubElement(teiHeader, "fileDesc")
        profileDesc = etree.SubElement(teiHeader, "profileDesc")
        encodingDesc = etree.SubElement(teiHeader, "encodingDesc")

        # Build <fileDesc>
        self._build_file_desc(fileDesc, default_text, num_authors)

        # Build <profileDesc>
        self._build_profile_desc(profileDesc)

        # Build <encodingDesc>
        self._build_encoding_desc(encodingDesc)

        # Build <revisionDesc>
        self._build_revision_desc(teiHeader)

    def _build_revision_desc(self, teiHeader):
        """
        Build the <revisionDesc> section: one <change> per pipeline phase
        (audit 1.8).

        No @when attribute: no meaningful event date is known at build time,
        and a run date would make two runs on the same input differ (the
        golden-file comparison relies on deterministic output).
        """
        revisionDesc = etree.SubElement(teiHeader, "revisionDesc")
        phases = [
            ("conversion",
             "Conversion of the ALTO XML source (OCR/HTR output) into "
             "TEI P5 with sourceDoc and body by the alto2tei pipeline."),
            ("enrichment",
             "Linguistic enrichment: tokenization, part-of-speech tagging, "
             "lemmatization and sentence segmentation (PyHellen)."),
            ("modernization",
             "Modernization of early modern French forms, encoded as "
             "orig/reg pairs inside choice elements (VieuxParler)."),
            ("ner",
             "Named-entity recognition and resolution: persons, places, "
             "works, dates and techniques (CamemBERT, GLiNER)."),
        ]
        for n, (phase, description) in enumerate(phases, 1):
            change = etree.SubElement(revisionDesc, "change", n=str(n), type=phase)
            change.text = description

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
            resp_ptr = resp_person.get("ptr")
            if resp_ptr and PLACEHOLDER_ORCID not in (resp_ptr.get("target") or ""):
                etree.SubElement(persName, "ptr", resp_ptr)

        # <extent>. @quantity porte le nombre, @n ne le portait que sous
        # forme d'etiquette libre ; la volumetrie du texte est ajoutee en
        # fin de chaine par finalize_extent(), quand elle est connue.
        extent = etree.SubElement(fileDesc, "extent")
        images = etree.SubElement(
            extent, "measure", unit="images", n=self.count, quantity=self.count
        )
        images.text = f"{self.count} images"

        # <publicationStmt>
        publicationStmt = etree.SubElement(fileDesc, "publicationStmt")
        publisher = etree.SubElement(publicationStmt, "publisher")
        publisher.text = self.config["responsibility"]["publisher"]
        authority = etree.SubElement(publicationStmt, "authority")
        authority.text = self.config["responsibility"]["authority"]
        availability = etree.SubElement(
            publicationStmt, "availability", self.config["responsibility"]["availability"]
        )
        licence = etree.SubElement(
            availability, "licence", self.config["responsibility"]["licence"]
        )
        licence.text = self.config["responsibility"].get("licence_text")
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

        # altIdentifier with the canonical document id (e.g., "LIV0326_v2"),
        # matching the root xml:id built in src.tei.TEI.build_tree().
        altIdentifier = etree.SubElement(msIdentifier, "altIdentifier")
        alt_idno = etree.SubElement(altIdentifier, "idno", type="internal")
        alt_idno.text = canonical_document_id(self.document)

        physDesc = etree.SubElement(msDesc, "physDesc")
        objectDesc = etree.SubElement(physDesc, "objectDesc")
        self.children["p"] = etree.SubElement(objectDesc, "p")
        self.children["p"].text = default_text

    def _build_profile_desc(self, profileDesc):
        """Build the <profileDesc> section with language info."""
        langUsage = etree.SubElement(profileDesc, "langUsage")
        if LANG_USAGE_DESCRIPTION:
            p = etree.SubElement(langUsage, "p")
            p.text = LANG_USAGE_DESCRIPTION
        self.children["language"] = etree.SubElement(langUsage, "language")
        self.children["language"].attrib["ident"] = ""

    def _build_encoding_desc(self, encodingDesc):
        """Build the <encodingDesc> section with application info and taxonomy."""
        # <editorialDecl> from config declarations
        enabled = {k: v for k, v in EDITORIAL_DECLARATIONS.items() if v.get("enabled")}
        if enabled:
            editorialDecl = etree.SubElement(encodingDesc, "editorialDecl")
            for key, decl in enabled.items():
                child = etree.SubElement(editorialDecl, key, **(decl.get("attrs") or {}))
                p = etree.SubElement(child, "p")
                p.text = decl["text"]

        # <appInfo>
        appInfo = etree.SubElement(encodingDesc, "appInfo")

        # Build application entries from config
        for app_key, app_data in self.versions.items():
            app_el = etree.SubElement(appInfo, "application")
            app_el.attrib["ident"] = app_data["ident"]
            app_el.attrib["version"] = tei_version_number(app_data["version"])
            label = etree.SubElement(app_el, "label")
            label.text = app_data["label"]
            ptr = etree.SubElement(app_el, "ptr")
            ptr.attrib["target"] = app_data["url"]

        # <classDecl> for SegmOnto taxonomy
        classDecl = etree.SubElement(encodingDesc, "classDecl")
        taxonomy_id = {XML_ID: SEGMONTO["id"]}
        self.children["taxonomy"] = etree.SubElement(classDecl, "taxonomy", taxonomy_id)

        tax_bibl = etree.SubElement(self.children["taxonomy"], "bibl")
        tax_title = etree.SubElement(tax_bibl, "title")
        tax_title.text = SEGMONTO["id"]
        tax_ptr = etree.SubElement(tax_bibl, "ptr")
        tax_ptr.attrib["target"] = SEGMONTO["url"]

        # Second taxonomy: what <keywords scheme="..."> points at. The
        # subjects come from catalogue records; saying so is the
        # difference between a controlled descriptor and a loose tag.
        keywords_tax = etree.SubElement(
            classDecl, "taxonomy", {XML_ID: KEYWORDS_TAXONOMY["id"]}
        )
        kw_bibl = etree.SubElement(keywords_tax, "bibl")
        kw_bibl.text = KEYWORDS_TAXONOMY["label"]
