# Run: venv/bin/python tests/test_person_ids.py
#
# NOTE: none of the 4 OCR_test documents (LIV0039, LIV0043, LIV0044) carry a
# raw ark in their role columns (ID_auteur/ID_imprimeurs/ID_libraires/
# ID_Editeur/ID_Traducteurs) - see report. So the full-pipeline OCR_test run
# alone can't exercise the ark->xml:id sanitization path; the tests below
# drive override_teiheader_from_csv()/build_metadata_dict() directly with a
# synthetic row that does carry one (mirroring real data found in
# metadata_livre.csv for LIV0002, whose ID_imprimeurs/ID_libraires hold raw
# arks instead of PERS ids).
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from lxml import etree

import teille_douce.metadata.csv_person as csv_person_module
from teille_douce.metadata.csv_person import load_person_database
from teille_douce.metadata.csv_book import (
    safe_person_id,
    build_metadata_dict,
    override_teiheader_from_csv,
)

ARK = "ark:/12148/cb17834575c"

# Le listPerson de particDesc n'est construit que si un PersonDatabase
# charge avec succes est present (csv_book.py : `if all_person_ids and
# person_db:` ; la verite suit le chargement depuis le correctif __bool__).
# Le metadata_personne.csv de la racine est gitignore et absent d'un clone
# frais (CI) : on charge la fixture versionnee a la place.
FIXTURE_PERSON_CSV = Path(__file__).resolve().parent / "fixtures" / "metadata_personne.csv"


@pytest.fixture(autouse=True)
def reset_person_db_singleton():
    """teille_douce.metadata.csv_person._person_db est un singleton de module.
    Reset avant ET apres chaque test pour ne pas dependre du
    metadata_personne.csv reel (gitignore) ni polluer les autres modules
    de test - meme motif que tests/test_metadata_sources.py."""
    csv_person_module._person_db = None
    yield
    csv_person_module._person_db = None


def test_pers_id_unchanged():
    assert safe_person_id("PERS0023") == "PERS0023"


def test_ark_becomes_ncname():
    sid = safe_person_id(ARK)
    assert sid == "pers_cb17834575c", sid
    assert ":" not in sid and "/" not in sid


def _minimal_root():
    """Build a minimal TEI header skeleton, same construction pattern as
    teille_douce/tei.py + teille_douce/teiheader/default.py (unqualified tags + nsmap), so
    override_teiheader_from_csv's unqualified .find() xpaths resolve."""
    root = etree.Element("TEI", nsmap={None: "http://www.tei-c.org/ns/1.0"})
    teiHeader = etree.SubElement(root, "teiHeader")
    fileDesc = etree.SubElement(teiHeader, "fileDesc")
    titleStmt = etree.SubElement(fileDesc, "titleStmt")
    etree.SubElement(titleStmt, "title")
    sourceDesc = etree.SubElement(fileDesc, "sourceDesc")
    bibl = etree.SubElement(sourceDesc, "bibl")
    etree.SubElement(bibl, "title")
    etree.SubElement(teiHeader, "profileDesc")
    return root


def test_ark_role_id_sanitized_in_listperson_and_respstmt():
    """A raw ark in ID_imprimeurs must not leak into xml:id or ref."""
    row = {
        "ID_auteur": "",
        "ID_imprimeurs": ARK,
        "ID_libraires": "",
        "ID_Editeur": "",
        "ID_Traducteurs": "",
    }
    load_person_database(FIXTURE_PERSON_CSV)
    root = _minimal_root()
    override_teiheader_from_csv(root, row, "TESTDOC")
    xml = etree.tostring(root, encoding="unicode")

    assert 'xml:id="ark:' not in xml, xml
    assert 'ref="#ark:' not in xml, xml

    listPerson = root.find(".//profileDesc/particDesc/listPerson")
    assert listPerson is not None
    person = listPerson.find("person")
    assert person is not None
    assert person.attrib["{http://www.w3.org/XML/1998/namespace}id"] == "pers_cb17834575c"
    idno = person.find('idno[@type="ark"]')
    assert idno is not None and idno.text == ARK
    persname = person.find("persName")
    assert persname is not None and (persname.text is None or persname.text.strip() == "")

    # respStmt for the "Imprimeur" role (ID_imprimeurs = ARK, not in
    # person_db) must not show the raw ark as the persName/name text either
    # - it goes in a nested <idno type="ark"> instead (see
    # create_bibl_respstmt: respStmt's content model - resp+, (name|orgName|
    # persName)+ - doesn't allow idno as a direct respStmt child, so it's
    # nested inside persName rather than a sibling).
    bibl = root.find(".//teiHeader/fileDesc/sourceDesc/bibl")
    assert bibl is not None
    respstmts = bibl.findall("respStmt")
    assert respstmts, "expected a respStmt for the Imprimeur role"
    for rs in respstmts:
        rs_xml = etree.tostring(rs, encoding="unicode")
        assert not re.search(r"<(persName|name)[^>]*>[^<]*ark:/", rs_xml), rs_xml
        rs_persname = rs.find("persName")
        assert rs_persname is not None
        assert rs_persname.text is None or rs_persname.text.strip() == "", rs_xml
        rs_idno = rs_persname.find('idno[@type="ark"]')
        assert rs_idno is not None and rs_idno.text == ARK, rs_xml


def test_ark_titlestmt_author_and_translator_no_ark_text():
    """create_author_element / create_editor_element (titleStmt) must not
    show the raw ark as element text either when person_id isn't found in
    person_db; the ark goes into a nested <idno type="ark"> instead."""
    row = {
        "ID_auteur": ARK,
        "ID_imprimeurs": "",
        "ID_libraires": "",
        "ID_Editeur": "",
        "ID_Traducteurs": ARK,
    }
    root = _minimal_root()
    override_teiheader_from_csv(root, row, "TESTDOC")

    titleStmt = root.find(".//teiHeader/fileDesc/titleStmt")
    assert titleStmt is not None
    ts_xml = etree.tostring(titleStmt, encoding="unicode")
    assert not re.search(r"<(persName|name)[^>]*>[^<]*ark:/", ts_xml), ts_xml

    author_el = titleStmt.find("author")
    assert author_el is not None
    assert author_el.text is None or "ark:/" not in author_el.text
    author_idno = author_el.find('idno[@type="ark"]')
    assert author_idno is not None and author_idno.text == ARK, ts_xml

    editor_el = titleStmt.find('editor[@role="translator"]')
    assert editor_el is not None
    assert editor_el.text is None or "ark:/" not in editor_el.text
    editor_idno = editor_el.find('idno[@type="ark"]')
    assert editor_idno is not None and editor_idno.text == ARK, ts_xml


def test_build_metadata_dict_ark_author_xmlid_sanitized():
    """The build_metadata_dict() 'unknown author' branch feeds xml:id/ref
    directly in teiheader/full.py's FullTree._populate_authors (attrib[XML_ID]
    = author['xmlid'] / ref = f"#{author['xmlid']}"), so it must be sanitized
    at the source too."""
    row = {"ID_auteur": ARK}
    metadata = build_metadata_dict(row)
    authors = metadata["sru"]["authors"]
    assert len(authors) == 1
    assert authors[0]["xmlid"] == "pers_cb17834575c", authors[0]["xmlid"]


if __name__ == "__main__":
    test_pers_id_unchanged()
    test_ark_becomes_ncname()
    test_ark_role_id_sanitized_in_listperson_and_respstmt()
    test_ark_titlestmt_author_and_translator_no_ark_text()
    test_build_metadata_dict_ark_author_xmlid_sanitized()
    print("OK test_person_ids")
