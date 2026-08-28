# -----------------------------------------------------------
# Characterization tests for src/teiheader/{default,full,builder}.py
# Run: venv/bin/python -m pytest tests/test_teiheader.py -q
#
# These tests fix *behavior* (results), not implementation, so they
# survive the refactors planned in docs/rapport_audit.md:
#   - 3.2: _extract_labels will switch to etree.iterparse
#   - 4.9: _extract_labels / extract_labels will be merged
#   - 4.2: in-memory elements may become namespaced (currently bare)
# -----------------------------------------------------------
import pytest
from lxml import etree

from config import (
    SEGMONTO,
    PLACEHOLDER_INFO_UNAVAILABLE,
    PLACEHOLDER_NO_METADATA,
    RESPONSIBILITY,
    APP_VERSIONS,
)
from src.constants import NS_ALTO, NS_TEI, XML_ID
from src.teiheader.default import DefaultTree
from src.teiheader.full import FullTree, _extract_labels
from src.teiheader.builder import build_header
from src.utils.files import canonical_document_id


# -----------------------------------------------------------
# Helpers
# -----------------------------------------------------------

def ln(el):
    """Local name of an element, regardless of whether it is namespaced
    (audit 4.2: the project currently mixes bare and namespaced tags)."""
    return etree.QName(el).localname


def make_root(document="LIV0001_reconciled"):
    """Mirrors src.tei.TEI.build_tree() without importing TEI (out of scope)."""
    xml_id_att = {XML_ID: canonical_document_id(document)}
    return etree.Element("TEI", xml_id_att, nsmap={None: NS_TEI})


MINIMAL_CONFIG = {"responsibility": RESPONSIBILITY}


def make_default_tree(sru=None, iiif=None, count_pages=3,
                       document="LIV0001_reconciled", versions=None, config=None):
    root = make_root(document)
    metadata = {"sru": sru, "iiif": iiif}
    tree = DefaultTree(
        config or MINIMAL_CONFIG, document, root, metadata, count_pages,
        versions if versions is not None else APP_VERSIONS,
    )
    tree.build()
    return root, tree


ALTO_NS = NS_ALTO["a"]


def write_alto(tmp_path, name, labels):
    """Minimal ALTO file with <OtherTag ID=... LABEL=...> under <Tags>.

    labels: list of (id, label) tuples.
    """
    tags_xml = "\n".join(
        f'<OtherTag ID="{tag_id}" LABEL="{label}"/>' for tag_id, label in labels
    )
    content = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<alto xmlns="{ALTO_NS}">\n'
        f"  <Tags>\n{tags_xml}\n  </Tags>\n"
        f"  <Layout/>\n"
        f"</alto>\n"
    )
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# =============================================================================
# 1. DefaultTree.build()
# =============================================================================

def test_default_tree_author_placeholders_match_sru_count():
    sru = {"found": True, "authors": [{"xmlid": "au0"}, {"xmlid": "au1"}, {"xmlid": "au2"}]}
    root, tree = make_default_tree(sru=sru)
    authors = [el for el in tree.children["titleStmt"] if ln(el) == "author"]
    assert len(authors) == 3


def test_default_tree_author_placeholder_fallback_without_sru():
    root, tree = make_default_tree(sru=None)
    authors = [el for el in tree.children["titleStmt"] if ln(el) == "author"]
    assert len(authors) == 1
    assert authors[0].text is None  # placeholder only gets text when num_authors == 0


def test_default_tree_author_placeholder_text_when_sru_found_but_empty():
    sru = {"found": True, "authors": []}
    root, tree = make_default_tree(sru=sru)
    authors = [el for el in tree.children["titleStmt"] if ln(el) == "author"]
    assert len(authors) == 1
    assert authors[0].text == PLACEHOLDER_INFO_UNAVAILABLE


def test_default_tree_alt_identifier_is_canonical_document_id():
    document = "LIV0031_t2_reconciled"
    root, tree = make_default_tree(document=document)
    alt_idno = root.find(".//altIdentifier/idno")
    assert alt_idno is not None
    assert alt_idno.get("type") == "internal"
    assert alt_idno.text == canonical_document_id(document) == "LIV0031_t2"


def test_default_tree_one_application_per_declared_version():
    versions = {
        "TOOL_A": {"version": "1.0", "ident": "ToolA", "label": "Tool A", "url": "https://example.org/a"},
        "TOOL_B": {"version": "2.0", "ident": "ToolB", "label": "Tool B", "url": "https://example.org/b"},
    }
    root, tree = make_default_tree(versions=versions)
    app_infos = [el for el in root.iter() if ln(el) == "appInfo"]
    assert len(app_infos) == 1
    applications = [el for el in app_infos[0] if ln(el) == "application"]
    assert len(applications) == 2
    idents = {a.get("ident") for a in applications}
    assert idents == {"ToolA", "ToolB"}
    a_tool = next(a for a in applications if a.get("ident") == "ToolA")
    assert a_tool.get("version") == "1.0"
    assert a_tool.find("./label").text == "Tool A"
    assert a_tool.find("./ptr").get("target") == "https://example.org/a"


def test_default_tree_taxonomy_created_with_segmonto_id():
    root, tree = make_default_tree()
    taxonomy = tree.children["taxonomy"]
    assert ln(taxonomy) == "taxonomy"
    assert taxonomy.get(XML_ID) == SEGMONTO["id"]
    assert taxonomy.find("./bibl/title").text == SEGMONTO["id"]
    assert taxonomy.find("./bibl/ptr").get("target") == SEGMONTO["url"]


def test_default_tree_editorial_decl_present_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "src.teiheader.default.EDITORIAL_DECLARATIONS",
        {"normalization": {"enabled": True, "attrs": {}, "text": "norm text"}},
    )
    root, tree = make_default_tree()
    editorial_decl = root.find(".//encodingDesc/editorialDecl")
    assert editorial_decl is not None
    assert [ln(c) for c in editorial_decl] == ["normalization"]
    assert editorial_decl.find("./normalization/p").text == "norm text"


def test_default_tree_editorial_decl_absent_when_all_disabled(monkeypatch):
    monkeypatch.setattr(
        "src.teiheader.default.EDITORIAL_DECLARATIONS",
        {"normalization": {"enabled": False, "attrs": {}, "text": "norm text"}},
    )
    root, tree = make_default_tree()
    editorial_decl = root.find(".//encodingDesc/editorialDecl")
    assert editorial_decl is None


# =============================================================================
# 2. FullTree.author_data()
# =============================================================================

def test_author_data_prefers_sru_over_iiif():
    sru = {
        "found": True,
        "authors": [{
            "xmlid": "au0",
            "primary_name": "Corneille",
            "secondary_name": "Pierre",
            "namelink": "de",
            "isni": "0000000121341163",
        }],
    }
    iiif = {"Creator": "Should not be used"}
    root, default = make_default_tree(sru=sru, iiif=iiif)
    full = FullTree(default.children, {"sru": sru, "iiif": iiif})
    full.author_data()

    ts_author = default.children["titleStmt"].find("./author")
    assert ts_author.get(XML_ID) == "au0"
    assert ts_author.find("./persName/surname").text == "Corneille"
    assert ts_author.find("./persName/forename").text == "Pierre"
    assert ts_author.find("./persName/nameLink").text == "de"
    isni_ptr = ts_author.find("./persName/ptr")
    assert isni_ptr.get("type") == "isni"
    assert isni_ptr.get("target") == "0000000121341163"
    assert ts_author.find("./name") is None  # SRU branch never adds a bare <name>

    bib_author = default.children["bibl"].find("./author")
    assert bib_author.get("ref") == "#au0"


def test_author_data_sru_found_but_empty_authors_leaves_placeholder_untouched():
    # num_authors == 0 -> DefaultTree already stamped the placeholder <author>
    # with default_text; author_data() must recognize it as "already filled"
    # and return without touching it.
    sru = {"found": True, "authors": []}
    root, default = make_default_tree(sru=sru)
    full = FullTree(default.children, {"sru": sru, "iiif": None})
    full.author_data()

    ts_author = default.children["titleStmt"].find("./author")
    assert ts_author.text == PLACEHOLDER_INFO_UNAVAILABLE
    assert ts_author.get(XML_ID) is None
    assert len(ts_author) == 0


def test_author_data_ignores_extra_placeholder_authors_beyond_sru_list():
    # Defensive branch: normally DefaultTree keeps the placeholder <author>
    # count in sync with len(sru authors), but _populate_authors must not
    # crash or misbehave if a parent carries more <author> placeholders
    # than the SRU authors list provides.
    sru = {"found": True, "authors": [{"xmlid": "au0", "primary_name": "Corneille"}]}
    root, default = make_default_tree(sru=sru)
    extra_author = etree.SubElement(default.children["titleStmt"], "author")

    full = FullTree(default.children, {"sru": sru, "iiif": None})
    full.author_data()

    authors = default.children["titleStmt"].findall("./author")
    assert len(authors) == 2
    assert authors[0].get(XML_ID) == "au0"
    assert extra_author.get(XML_ID) is None  # beyond the list -> left untouched
    assert len(extra_author) == 0


def test_author_data_falls_back_to_iiif_when_sru_not_found():
    iiif = {"Creator": "Doe, Jane"}
    root, default = make_default_tree(sru=None, iiif=iiif)
    full = FullTree(default.children, {"sru": None, "iiif": iiif})
    full.author_data()

    ts_author = default.children["titleStmt"].find("./author")
    assert ts_author.get(XML_ID) == "Do"  # creator[:2]
    assert ts_author.find("./name").text == "Doe, Jane"
    assert ts_author.find("./persName") is None  # IIIF branch never adds persName

    bib_author = default.children["bibl"].find("./author")
    assert bib_author.get("ref") == "#Do"
    assert bib_author.find("./name").text == "Doe, Jane"


def test_author_data_no_creator_leaves_placeholder_author_untouched():
    root, default = make_default_tree(sru=None, iiif={})
    full = FullTree(default.children, {"sru": None, "iiif": {}})
    full.author_data()

    ts_author = default.children["titleStmt"].find("./author")
    assert ts_author.get(XML_ID) is None
    assert len(ts_author) == 0  # no <name> child added


# =============================================================================
# 3. FullTree.bib_data()
# =============================================================================

def test_bib_data_populates_preferring_sru_over_iiif():
    sru = {
        "found": True,
        "title": "Le Cid",
        "ptr": "https://example.org/cid",
        "place": "Paris",
        "publisher": "Compagnie",
        "date": "1637",
        "repository": "BnF",
        "idno": "RES-YF-123",
        "language": "fre",
    }
    iiif = {
        "Title": "Should not win",
        "Date": "Should not win",
        "Repository": "Should not win",
        "Shelfmark": "Should not win",
        "Language": "Only source for language text",
    }
    root, default = make_default_tree(sru=sru, iiif=iiif)
    full = FullTree(default.children, {"sru": sru, "iiif": iiif})
    full.bib_data()

    assert default.children["ts_title"].text == "Le Cid"
    assert default.children["bib_title"].text == "Le Cid"
    assert default.children["ptr"].get("target") == "https://example.org/cid"
    assert default.children["pubPlace"].text == "Paris"
    assert default.children["publisher"].text == "Compagnie"
    assert default.children["date"].text == "1637"
    assert default.children["repository"].text == "BnF"
    assert default.children["idno"].text == "RES-YF-123"
    # <language> text has no SRU mapping -> comes purely from IIIF
    assert default.children["language"].text == "Only source for language text"
    # <language>/@ident has no IIIF mapping -> comes purely from SRU
    assert default.children["language"].get("ident") == "fre"


def test_bib_data_leaves_default_placeholder_when_field_absent_from_metadata():
    sru = {"found": True, "title": "Le Cid"}  # no place/publisher/date/...
    root, default = make_default_tree(sru=sru, iiif=None)
    full = FullTree(default.children, {"sru": sru, "iiif": None})
    full.bib_data()

    assert default.children["ts_title"].text == "Le Cid"
    # Fields absent from both sources are left exactly as DefaultTree set them.
    assert default.children["pubPlace"].text == PLACEHOLDER_INFO_UNAVAILABLE
    assert default.children["publisher"].text == PLACEHOLDER_INFO_UNAVAILABLE
    assert default.children["date"].text == PLACEHOLDER_INFO_UNAVAILABLE


def test_bib_data_leaves_no_metadata_placeholder_when_sru_not_found():
    root, default = make_default_tree(sru=None, iiif=None)
    full = FullTree(default.children, {"sru": None, "iiif": None})
    full.bib_data()
    assert default.children["ts_title"].text == PLACEHOLDER_NO_METADATA


def test_bib_data_ptr_falls_back_to_bnf_ark_when_no_explicit_ptr():
    sru = {"found": True, "ark": "ark:/12148/bpt6k123456"}
    root, default = make_default_tree(sru=sru)
    full = FullTree(default.children, {"sru": sru, "iiif": None})
    full.bib_data()
    assert default.children["ptr"].get("target") == "https://catalogue.bnf.fr/ark:/12148/bpt6k123456"


def test_bib_data_ptr_element_removed_when_no_ptr_and_no_bnf_ark():
    sru = {"found": True}
    root, default = make_default_tree(sru=sru)
    full = FullTree(default.children, {"sru": sru, "iiif": None})
    full.bib_data()

    bibl = default.children["bibl"]
    assert bibl.find("./ptr") is None
    assert default.children["ptr"].getparent() is None


def test_bib_data_ptr_not_removed_for_non_bnf_ark():
    # ark present but not a BnF ark ("ark:/12148/...") -> no fallback, ptr dropped too
    sru = {"found": True, "ark": "ark:/99999/other"}
    root, default = make_default_tree(sru=sru)
    full = FullTree(default.children, {"sru": sru, "iiif": None})
    full.bib_data()
    assert default.children["bibl"].find("./ptr") is None


def test_bib_data_with_incomplete_children_dict_skips_missing_keys():
    # Defensive branch: bib_data() must not crash when the children dict
    # (normally produced by DefaultTree.build()) is missing expected keys.
    full = FullTree({}, {"sru": {"found": True, "title": "X"}, "iiif": None})
    full.bib_data()  # no exception


# =============================================================================
# 4. FullTree.segmonto_taxonomy()
# =============================================================================

def test_extract_labels_returns_id_to_label_dict(tmp_path):
    alto_path = write_alto(tmp_path, "page1.xml", [("BT1", "MainZone"), ("BT2", "DefaultLine")])
    labels = _extract_labels(alto_path)
    assert labels == {"BT1": "MainZone", "BT2": "DefaultLine"}


def test_segmonto_taxonomy_extracts_labels_and_always_adds_defaultline(tmp_path):
    alto_path = write_alto(tmp_path, "page1.xml", [
        ("BT1", "MainZone"),
        ("BT2", "NumberingZone"),
        ("BT3", "HeadingLine"),
        ("BT4", "FantasyZone"),  # zone-like label, not in SEGMONTO_ZONES
    ])
    root, default = make_default_tree()
    full = FullTree(default.children, {"sru": None, "iiif": None})
    zones, lines = full.segmonto_taxonomy([alto_path])

    # The raw label lists include *every* "Zone"/"Line"-shaped label found,
    # even labels unknown to the SegmOnto taxonomy dicts.
    assert set(zones) == {"MainZone", "NumberingZone", "FantasyZone"}
    assert set(lines) == {"HeadingLine"}

    taxonomy = default.children["taxonomy"]
    categories = [c for c in taxonomy if ln(c) == "category"]
    assert len(categories) == 2
    zone_cat, line_cat = categories

    zone_ids = {c.get(XML_ID) for c in zone_cat if ln(c) == "catDesc"}
    # Only labels recognized by SEGMONTO_ZONES get a taxonomy entry:
    # "FantasyZone" is silently excluded even though it was returned above.
    assert zone_ids == {"MainZone", "NumberingZone"}

    line_ids = {c.get(XML_ID) for c in line_cat if ln(c) == "catDesc"}
    # DefaultLine is always present in the taxonomy, even though it never
    # appeared in the ALTO file.
    assert line_ids == {"HeadingLine", "DefaultLine"}


def test_segmonto_taxonomy_defaultline_not_duplicated_when_already_found(tmp_path):
    alto_path = write_alto(tmp_path, "page1.xml", [("BT1", "DefaultLine")])
    root, default = make_default_tree()
    full = FullTree(default.children, {"sru": None, "iiif": None})
    zones, lines = full.segmonto_taxonomy([alto_path])

    assert lines == ["DefaultLine"]
    taxonomy = default.children["taxonomy"]
    line_cat = [c for c in taxonomy if ln(c) == "category"][1]
    line_catdescs = [c for c in line_cat if ln(c) == "catDesc"]
    assert len(line_catdescs) == 1  # not duplicated


# =============================================================================
# 5. build_header() — integration
# =============================================================================

def test_build_header_assembles_default_full_and_taxonomy(tmp_path):
    alto_path = write_alto(tmp_path, "page1.xml", [
        ("BT1", "MainZone"),
        ("BT2", "DefaultLine"),
    ])
    document = "LIV0099_reconciled"
    root = make_root(document)
    sru = {
        "found": True,
        "authors": [{"xmlid": "au0", "primary_name": "Corneille"}],
        "title": "Le Cid",
        "ark": "ark:/12148/bpt6k999999",
    }
    metadata = {"sru": sru, "iiif": None}
    config = {"responsibility": RESPONSIBILITY}

    new_root, zones, lines = build_header(
        metadata, document, root, 1, config, APP_VERSIONS, [alto_path]
    )

    assert new_root is root
    tei_header = root.find("./teiHeader")
    assert tei_header is not None

    # Full metadata population happened
    assert tei_header.find(".//titleStmt/title").text == "Le Cid"
    author = tei_header.find(".//titleStmt/author")
    assert author.get(XML_ID) == "au0"
    ptr = tei_header.find(".//sourceDesc/bibl/ptr")
    assert ptr.get("target") == "https://catalogue.bnf.fr/ark:/12148/bpt6k999999"

    # altIdentifier still set from the default skeleton
    alt_idno = tei_header.find(".//altIdentifier/idno")
    assert alt_idno.text == canonical_document_id(document)

    # Taxonomy built from the ALTO file
    assert zones == ["MainZone"]
    assert lines == ["DefaultLine"]
    taxonomy = tei_header.find(".//classDecl/taxonomy")
    zone_ids = {c.get(XML_ID) for c in taxonomy if ln(c) == "category"}
    assert len(zone_ids) == 2  # SegmOntoZones + SegmOntoLines categories present


# =============================================================================
# 6. <revisionDesc> — audit §1.8, not implemented yet
# =============================================================================

def test_revision_desc_has_one_change_per_pipeline_phase(tmp_path):
    """Audit 1.8 : un <change> par phase du pipeline, en dernier enfant du header."""
    alto_path = write_alto(tmp_path, "page1.xml", [("BT1", "MainZone")])
    document = "LIV0100_reconciled"
    root = make_root(document)
    metadata = {"sru": None, "iiif": None}
    config = {"responsibility": RESPONSIBILITY}

    build_header(metadata, document, root, 1, config, APP_VERSIONS, [alto_path])

    tei_header = root.find("./teiHeader")
    revision_desc = tei_header.find("./revisionDesc")
    assert revision_desc is not None, "revisionDesc absent (audit 1.8)"

    changes = [el for el in revision_desc if ln(el) == "change"]
    # One <change> per pipeline phase (audit: conversion, enrichissement,
    # modernisation, NER).
    assert len(changes) == 4
    assert all(c.text for c in changes)
