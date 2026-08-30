# Run: venv/bin/python -m pytest tests/test_csv_book.py -q
#
# Characterization tests for src/metadata/csv_book.py, written ahead of the
# planned rewrite of override_teiheader_from_csv (docs/rapport_audit.md
# §4.4: 470 lines, ~15 copy-pasted "if person.get(X): note..." blocks, 4
# nested functions, to be replaced by a field -> note-type mapping table).
#
# Assertions target the produced XML (presence / value / position / parent
# of elements) so the refactor is free to delete the private helpers and
# nested defs exercised only incidentally here. Never call a nested def
# directly, and never assert against a helper this module doesn't export.
#
# Tag comparisons use etree.QName(...).localname rather than raw .tag so
# these tests keep working regardless of whether a given code path is
# fixed to use namespaced tags (audit §4.2 - the project currently mixes
# bare and namespaced tags).
import pandas as pd
import pytest
from lxml import etree

from src.metadata import csv_person
from src.metadata.csv_person import load_person_database
from src.metadata.csv_book import (
    load_metadata,
    find_metadata_row,
    build_metadata_dict,
    override_teiheader_from_csv,
    _normalize_date,
    _volume_index,
    _safe_value,
    _safe_value_list,
)

NS_TEI = "http://www.tei-c.org/ns/1.0"
XML_ID_ATTR = "{http://www.w3.org/XML/1998/namespace}id"
PLACEHOLDER = "Information not available."

BOOK_COLUMNS = [
    "BDD", "ARK", "Titre_long", "Titre_abrege", "Date_01", "Date_02",
    "ID_auteur", "ID_imprimeurs", "ID_libraires", "ID_Editeur", "ID_Traducteurs",
    "Lieu_publication", "langues", "Sujet", "Matiere", "Localisation", "Cote",
    "manifest_iiif", "Format",
]

PERSON_COLUMNS = [
    "BDD", "ARK", "ISNI", "Label_categ", "Prenoms", "Nom", "Sexe", "RoleName",
    "GenName", "Surnoms", "Annee_naissance", "ID_Ville_naissance", "Ville_naissance",
    "Annee_mort", "ID_Ville_mort", "Ville_mort", "Confession", "Formation",
    "Professions", "Portraits", "Oeuvre", "Milieux_reseaux", "Contacts_artistes",
    "Fortune_critique", "Publications", "Citations", "Bibliographie", "Webographie",
    "Notes", "Commentaires",
]


def localname(el):
    return etree.QName(el).localname


def _write_book_csv(tmp_path, rows, name="metadata_livre.csv"):
    df = pd.DataFrame(rows)
    for col in BOOK_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    path = tmp_path / name
    df[BOOK_COLUMNS].to_csv(path, sep=";", index=False)
    return path


def _write_person_csv(tmp_path, rows, name="metadata_personne.csv"):
    df = pd.DataFrame(rows)
    for col in PERSON_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    path = tmp_path / name
    df[PERSON_COLUMNS].to_csv(path, sep=";", index=False)
    return path


def _build_default_root(document="TESTDOC0001"):
    """Minimal TEI header skeleton mirroring src/teiheader/default.py's
    DefaultTree output closely enough to exercise every branch of
    override_teiheader_from_csv (same element names, same nesting, same
    placeholder text, same msIdentifier child order: country, settlement,
    repository, idno, altIdentifier)."""
    root = etree.Element("TEI", nsmap={None: NS_TEI})
    teiHeader = etree.SubElement(root, "teiHeader")
    fileDesc = etree.SubElement(teiHeader, "fileDesc")
    profileDesc = etree.SubElement(teiHeader, "profileDesc")

    titleStmt = etree.SubElement(fileDesc, "titleStmt")
    ts_title = etree.SubElement(titleStmt, "title")
    ts_title.text = PLACEHOLDER
    etree.SubElement(titleStmt, "author")  # empty placeholder, should be dropped

    sourceDesc = etree.SubElement(fileDesc, "sourceDesc")
    bibl = etree.SubElement(sourceDesc, "bibl")
    bib_title = etree.SubElement(bibl, "title")
    bib_title.text = PLACEHOLDER
    pubPlace = etree.SubElement(bibl, "pubPlace")
    pubPlace.text = PLACEHOLDER
    publisher = etree.SubElement(bibl, "publisher")
    publisher.text = PLACEHOLDER
    date_el = etree.SubElement(bibl, "date")
    date_el.text = PLACEHOLDER

    msDesc = etree.SubElement(sourceDesc, "msDesc")
    msIdentifier = etree.SubElement(msDesc, "msIdentifier")
    etree.SubElement(msIdentifier, "country")
    settlement_el = etree.SubElement(msIdentifier, "settlement")
    settlement_el.text = PLACEHOLDER
    repository_el = etree.SubElement(msIdentifier, "repository")
    repository_el.text = PLACEHOLDER
    idno_el = etree.SubElement(msIdentifier, "idno")
    idno_el.text = PLACEHOLDER
    altIdentifier = etree.SubElement(msIdentifier, "altIdentifier")
    alt_idno = etree.SubElement(altIdentifier, "idno", type="internal")
    alt_idno.text = document

    langUsage = etree.SubElement(profileDesc, "langUsage")
    language_el = etree.SubElement(langUsage, "language")
    language_el.attrib["ident"] = ""

    return root


@pytest.fixture(autouse=True)
def _reset_person_db():
    """csv_person._person_db is a module-level singleton; isolate tests
    from each other (and from the real, gitignored metadata_personne.csv
    that may happen to sit in the repo root during local dev - see the
    discovery documented in the final report)."""
    csv_person._person_db = None
    yield
    csv_person._person_db = None


# ---------------------------------------------------------------------------
# 1. _safe_value / _safe_value_list (UNIT)
# ---------------------------------------------------------------------------

def test_safe_value_none_row():
    assert _safe_value(None, "x") is None
    assert _safe_value_list(None, "x") == []


def test_safe_value_missing_key():
    assert _safe_value({}, "x") is None
    assert _safe_value_list({}, "x") == []


def test_safe_value_python_nan_float():
    assert _safe_value({"x": float("nan")}, "x") is None


def test_safe_value_pandas_nan_via_csv_roundtrip(tmp_path):
    """Real pandas NaN (as opposed to a python float('nan') built by hand),
    coming from an empty CSV cell."""
    path = tmp_path / "t.csv"
    path.write_text("BDD;Titre_long\nX1;Some Title\nX2;\n", encoding="utf-8")
    df = pd.read_csv(path, sep=";")
    assert _safe_value(df.iloc[0], "Titre_long") == "Some Title"
    assert _safe_value(df.iloc[1], "Titre_long") is None


def test_safe_value_string_nan_case_insensitive():
    assert _safe_value({"x": "nan"}, "x") is None
    assert _safe_value({"x": "NaN"}, "x") is None


def test_safe_value_empty_and_whitespace_only():
    assert _safe_value({"x": ""}, "x") is None
    assert _safe_value({"x": "   "}, "x") is None


def test_safe_value_valid_value_is_stripped():
    assert _safe_value({"x": "  Hello  "}, "x") == "Hello"


def test_safe_value_list_splits_and_strips():
    assert _safe_value_list({"x": "a| b |c "}, "x") == ["a", "b", "c"]


def test_safe_value_list_drops_empty_segments():
    assert _safe_value_list({"x": "a||b|  "}, "x") == ["a", "b"]


def test_safe_value_list_none_value():
    assert _safe_value_list({"x": None}, "x") == []


# ---------------------------------------------------------------------------
# 2. build_metadata_dict (UNIT)
# ---------------------------------------------------------------------------

def test_build_metadata_dict_row_none():
    metadata = build_metadata_dict(None)
    assert metadata["sru"]["found"] is False
    # defaultdict(lambda: None, {"found": False}) - no "authors" key was set
    # in this branch, so any other lookup falls through to None too.
    assert metadata["sru"]["authors"] is None
    assert metadata["iiif"]["Title"] is None


def test_build_metadata_dict_known_author_and_multivalued_fields(tmp_path):
    person_csv = _write_person_csv(tmp_path, [{
        "BDD": "PERS0001", "ARK": "ark:/12148/pers0001",
        "ISNI": "000000012153501X",
        "Label_categ": "AUT", "Prenoms": "Jean", "Nom": "Dupont",
        "GenName": "le Jeune",
        "Annee_naissance": "1590/05/22", "Annee_mort": "1650/01/01",
    }])
    load_person_database(person_csv)

    row = {
        "ID_auteur": "PERS0001",
        "Titre_long": "Le Grand Titre",
        "Lieu_publication": "Paris|Lyon",
        "langues": "français|latin",
        "Sujet": "Peinture",
        "Matiere": "Sculpture",
        "Localisation": "Munich, BSB|Autre lieu",
        "Cote": "Cote1|Cote2",
        "ID_Editeur": "",
        "ID_imprimeurs": "EdID1|EdID2",
    }
    metadata = build_metadata_dict(row)
    sru = metadata["sru"]

    authors = sru["authors"]
    assert len(authors) == 1
    author = authors[0]
    assert author["xmlid"] == "PERS0001"
    assert author["name"] == "Jean Dupont"
    assert author["secondary_name"] == "Jean"
    assert author["primary_name"] == "Dupont"
    assert author["namelink"] == "le Jeune"
    assert author["isni"] == "000000012153501X"
    assert author["birth_date"] == "1590/05/22"
    assert author["death_date"] == "1650/01/01"
    assert author["role"] == "author"

    assert sru["places"] == ["Paris", "Lyon"]
    assert sru["place"] == "Paris"
    assert sru["languages"] == ["français", "latin"]
    assert sru["subjects"] == ["Peinture", "Sculpture"]
    assert sru["repositories"] == ["Munich, BSB", "Autre lieu"]
    assert sru["idnos"] == ["Cote1", "Cote2"]
    # publisher fallback: ID_Editeur is present-but-empty (-> [] is falsy),
    # so "or" moves on to ID_imprimeurs.
    assert sru["publishers"] == ["EdID1", "EdID2"]
    assert sru["publisher"] == "EdID1"

    iiif = metadata["iiif"]
    assert iiif["Title"] == "Le Grand Titre"
    assert iiif["Place"] == "Paris"


def test_build_metadata_dict_isni_leading_zeros_preserved(tmp_path):
    """Audit 5.3 : PersonDatabase.load() lit desormais avec dtype=str, un
    ISNI entierement numerique garde ses zeros de tete (les ISNI reels font
    16 chiffres, frequemment zero-pades)."""
    person_csv = _write_person_csv(tmp_path, [{
        "BDD": "PERS0009", "Prenoms": "Ada", "Nom": "Lovelace",
        "ISNI": "0000000123456789",
    }])
    load_person_database(person_csv)
    metadata = build_metadata_dict({"ID_auteur": "PERS0009"})
    assert metadata["sru"]["authors"][0]["isni"] == "0000000123456789"


# ---------------------------------------------------------------------------
# 3. override_teiheader_from_csv (INTEG - nominal path)
# ---------------------------------------------------------------------------

@pytest.fixture
def rich_person_csv(tmp_path):
    return _write_person_csv(tmp_path, [
        {
            "BDD": "PERS0001", "ARK": "ark:/12148/pers0001",
            "ISNI": "000000012153501X",
            "Label_categ": "AUT", "Prenoms": "Jean", "Nom": "Dupont", "Sexe": "m",
            "RoleName": "peintre", "GenName": "le Jeune", "Surnoms": "JD",
            "Annee_naissance": "1590/05/22", "ID_Ville_naissance": "2988507",
            "Ville_naissance": "Paris",
            "Annee_mort": "1650/01/01", "ID_Ville_mort": "2867714",
            "Ville_mort": "Munich",
            "Confession": "catholique", "Formation": "atelier", "Professions": "peintre",
            "Portraits": "portrait X", "Oeuvre": "oeuvre Y", "Milieux_reseaux": "reseau Z",
            "Contacts_artistes": "contact A", "Fortune_critique": "critique B",
            "Publications": "pub C", "Citations": "citation D", "Bibliographie": "biblio E",
            "Webographie": "web F", "Notes": "note G", "Commentaires": "commentaire H",
        },
        {
            "BDD": "PERS0002", "Prenoms": "Marie", "Nom": "Martin", "Label_categ": "TRAD",
        },
    ])


def test_override_teiheader_nominal_path(rich_person_csv):
    load_person_database(rich_person_csv)
    root = _build_default_root(document="TESTDOC0001_t2")
    row = {
        "Titre_long": "Le Grand Titre", "Titre_abrege": "",
        "Date_01": "1600", "Date_02": "",
        "ID_auteur": "PERS0001",
        "ID_Traducteurs": "PERS0002",
        "ID_imprimeurs": "PERS0002",
        "ID_libraires": "PERS0002",
        "ID_Editeur": "PERS0002",
        "Lieu_publication": "Paris|Lyon",
        "langues": "français|latin",
        "Sujet": "Peinture",
        "Matiere": "Sculpture",
        "Localisation": "Munich, Bayerische Staatsbibliothek|Bibliothèque nationale",
        "Cote": "Shelfmark1|Shelfmark2",
        "ARK": "ark:/12148/testdoc",
        "manifest_iiif": "https://x/manifest1.json|https://x/manifest2.json",
    }
    override_teiheader_from_csv(root, row, "TESTDOC0001_t2")

    # --- Title ---
    assert root.find(".//teiHeader/fileDesc/titleStmt/title").text == "Le Grand Titre"
    assert root.find(".//teiHeader/fileDesc/sourceDesc/bibl/title").text == "Le Grand Titre"

    # --- titleStmt: author (enriched) + translator-as-editor ---
    titleStmt = root.find(".//teiHeader/fileDesc/titleStmt")
    authors = [a for a in titleStmt if localname(a) == "author"]
    # the empty placeholder <author/> from the default tree must be gone
    assert len(authors) == 1
    author_el = authors[0]
    assert author_el.get("ref") == "#PERS0001"
    persname = author_el.find("persName")
    assert persname.find("forename").text == "Jean"
    assert persname.find("surname").text == "Dupont"
    assert persname.find("nameLink").text == "le Jeune"
    assert persname.find('ptr[@type="isni"]').get("target") == \
        "https://isni.org/isni/000000012153501X"
    assert persname.find('ptr[@type="ark"]').get("target") == "ark:/12148/pers0001"

    # Audit 5.6 : plus de <birth>/<death> dans <author> — tei_all.rng les
    # y refuse, et les evenements de vie vivent dans le <listPerson> que
    # le @ref ci-dessus designe (verifie plus bas).
    assert author_el.find("birth") is None
    assert author_el.find("death") is None

    editor_el = titleStmt.find('editor[@role="translator"]')
    assert editor_el is not None
    assert editor_el.get("ref") == "#PERS0002"
    assert editor_el.find("persName/forename").text == "Marie"
    assert editor_el.find("persName/surname").text == "Martin"
    assert editor_el.find("birth") is None

    # --- sourceDesc/bibl: pubPlace (multi), publisher removed, respStmt, date ---
    bibl = root.find(".//teiHeader/fileDesc/sourceDesc/bibl")
    pubplaces = [p.text for p in bibl.findall("pubPlace")]
    assert pubplaces == ["Paris", "Lyon"]
    assert bibl.findall("publisher") == []

    respstmts = bibl.findall("respStmt")
    resp_labels = {rs.find("resp").text for rs in respstmts}
    assert resp_labels == {"Imprimeur", "Libraire", "Éditeur"}
    for rs in respstmts:
        pn = rs.find("persName")
        assert pn.get("ref") == "#PERS0002"
        assert pn.find("forename").text == "Marie"

    assert bibl.find("date").text == "1600"

    # --- msIdentifier: settlement/repository split, country dropped, cotes,
    #     ark/iiif idno ---
    msIdentifier = root.find(".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier")
    assert msIdentifier.find("settlement").text == "Munich"
    repos = [r.text for r in msIdentifier.findall("repository")]
    assert repos == ["Bayerische Staatsbibliothek", "Bibliothèque nationale"]
    assert msIdentifier.find("country") is None

    untyped_idnos = [i.text for i in msIdentifier.findall("idno") if i.get("type") is None]
    assert untyped_idnos == ["Shelfmark1", "Shelfmark2"]

    ark_idno = msIdentifier.find('idno[@type="ark"]')
    assert ark_idno.text == "ark:/12148/testdoc"
    iiif_idno = msIdentifier.find('idno[@type="iiif"]')
    # volume "t2" -> index 1 -> second manifest, selected unambiguously (no
    # "n" numbering, that's only for the "unknown volume" fallback branch).
    assert iiif_idno.text == "https://x/manifest2.json"
    assert iiif_idno.get("n") is None

    # --- profileDesc: languages ---
    langUsage = root.find(".//teiHeader/profileDesc/langUsage")
    langs = langUsage.findall("language")
    assert [l.text for l in langs] == ["français", "latin"]
    assert langs[0].get("ident") == "fra"
    assert langs[1].get("ident") == "lat"

    # --- subjects: presence only here (their exact parent is the audit
    #     §1.2 violation, pinned separately below as xfail) ---
    terms = [t.text for t in root.findall(".//teiHeader/profileDesc//term")]
    assert terms == ["Peinture", "Sculpture"]

    # --- listPerson ---
    listPerson = root.find(".//teiHeader/profileDesc/particDesc/listPerson")
    persons = listPerson.findall("person")
    ids = sorted(p.get(XML_ID_ATTR) for p in persons)
    assert ids == ["PERS0001", "PERS0002"]

    p1 = next(p for p in persons if p.get(XML_ID_ATTR) == "PERS0001")
    assert p1.get("sex") == "m"
    pn1 = p1.find("persName")
    assert pn1.find("roleName").text == "peintre"
    assert pn1.find("forename").text == "Jean"
    assert pn1.find("genName").text == "le Jeune"
    assert pn1.find("surname").text == "Dupont"
    assert pn1.find('addName[@type="nickname"]').text == "JD"

    birth1 = p1.find("birth")
    # Audit 5.6 : un seul encodage pour ces champs — @when normalise en
    # ISO, et @ref portant l'URI geonames complete (un id nu est une URI
    # relative qui ne resout rien).
    assert birth1.get("when") == "1590-05-22"
    bp1 = birth1.find("placeName")
    assert bp1.text == "Paris"
    assert bp1.get("ref") == "https://www.geonames.org/2988507/"

    death1 = p1.find("death")
    assert death1.get("when") == "1650-01-01"

    assert p1.find("faith").text == "catholique"
    assert p1.find("education").text == "atelier"
    assert p1.find("occupation").text == "peintre"
    assert p1.find('idno[@type="isni"]').text == "000000012153501X"
    assert p1.find('idno[@type="ark"]').text == "ark:/12148/pers0001"
    assert p1.find('note[@type="portrait"]').text == "portrait X"
    assert p1.find('note[@type="works"]').text == "oeuvre Y"
    assert p1.find('note[@type="networks"]').text == "reseau Z"
    assert p1.find('note[@type="contacts"]').text == "contact A"
    assert p1.find('note[@type="reception"]').text == "critique B"
    assert p1.find('note[@type="publications"]').text == "pub C"
    assert p1.find('note[@type="citations"]').text == "citation D"
    assert p1.find('note[@type="bibliography"]').text == "biblio E"
    assert p1.find('note[@type="webography"]').text == "web F"
    assert p1.find('note[@type="comments"]').text == "commentaire H"
    plain_notes = [n for n in p1.findall("note") if n.get("type") is None]
    assert len(plain_notes) == 1
    assert plain_notes[0].text == "note G"


def test_override_localisation_without_comma_drops_settlement_placeholder():
    """No comma in the (single) Localisation value -> no city can be
    derived, and the still-placeholder <settlement> is removed rather than
    kept misleading."""
    root = _build_default_root()
    row = {"Localisation": "Institution Sans Ville"}
    override_teiheader_from_csv(root, row, "TESTDOC0001")

    msIdentifier = root.find(".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier")
    assert msIdentifier.find("settlement") is None
    assert msIdentifier.find("repository").text == "Institution Sans Ville"


def test_override_manifest_fallback_lists_all_when_volume_unknown():
    """document_name carries no volume marker -> select_manifest can't pick
    one -> every manifest is listed, numbered via @n."""
    root = _build_default_root(document="TESTDOC0002")
    row = {"manifest_iiif": "https://x/m1.json|https://x/m2.json"}
    override_teiheader_from_csv(root, row, "TESTDOC0002")

    msIdentifier = root.find(".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier")
    iiif_idnos = msIdentifier.findall('idno[@type="iiif"]')
    assert [(i.get("n"), i.text) for i in iiif_idnos] == [
        ("1", "https://x/m1.json"),
        ("2", "https://x/m2.json"),
    ]


def test_override_unknown_non_ark_person_gets_bare_persname_text(tmp_path):
    """A person id absent from a (present, non-empty) person database gets
    a minimal <person> entry: xml:id only, and - since it isn't an ark -
    the id itself as the persName text (no nested <idno>, unlike the ark
    case already covered by tests/test_person_ids.py)."""
    # Une entree quelconque suffit a rendre la base chargee (et donc vraie
    # depuis le correctif __bool__ base sur le succes du chargement).
    person_csv = _write_person_csv(tmp_path, [{"BDD": "PERS9999", "Nom": "Unrelated"}])
    load_person_database(person_csv)

    root = _build_default_root()
    row = {"ID_auteur": "PERS0042_UNKNOWN"}
    override_teiheader_from_csv(root, row, "TESTDOC0001")

    listPerson = root.find(".//teiHeader/profileDesc/particDesc/listPerson")
    assert listPerson is not None
    person_el = listPerson.find("person")
    assert person_el.get(XML_ID_ATTR) == "PERS0042_UNKNOWN"
    persname = person_el.find("persName")
    assert persname.text == "PERS0042_UNKNOWN"
    assert person_el.find("idno") is None


# ---------------------------------------------------------------------------
# 4. load_metadata + find_metadata_row (UNIT)
# ---------------------------------------------------------------------------

def test_load_metadata_missing_file(tmp_path):
    assert load_metadata(tmp_path / "absent.csv") is None


def test_load_metadata_valid_csv(tmp_path):
    path = _write_book_csv(tmp_path, [{"BDD": "LIV0002", "Titre_long": "Titre A"}])
    df = load_metadata(path)
    assert df is not None
    assert list(df["BDD"]) == ["LIV0002"]


def test_find_metadata_row_prefix_found(tmp_path):
    path = _write_book_csv(tmp_path, [
        {"BDD": "LIV0002", "Titre_long": "Titre A"},
        {"BDD": "LIV0009", "Titre_long": "Titre B"},
    ])
    df = load_metadata(path)
    row = find_metadata_row(df, "LIV0002a_reconciled")
    assert row is not None
    assert row["Titre_long"] == "Titre A"


def test_find_metadata_row_prefix_not_found(tmp_path):
    path = _write_book_csv(tmp_path, [{"BDD": "LIV0002", "Titre_long": "Titre A"}])
    df = load_metadata(path)
    assert find_metadata_row(df, "XYZ9999_reconciled") is None


def test_find_metadata_row_no_bdd_column():
    df = pd.DataFrame([{"Other": "value"}])
    assert find_metadata_row(df, "LIV0002") is None


def test_find_metadata_row_none_df():
    assert find_metadata_row(None, "LIV0002") is None


# ---------------------------------------------------------------------------
# 5. _normalize_date + _volume_index (NONREG)
# ---------------------------------------------------------------------------

def test_normalize_date_slash_to_dash():
    assert _normalize_date("1590/05/22") == "1590-05-22"


def test_normalize_date_empty_or_none():
    assert _normalize_date("") == ""
    assert _normalize_date(None) == ""


def test_normalize_date_no_slash_unchanged():
    assert _normalize_date("1590") == "1590"


def test_volume_index_none_or_empty():
    assert _volume_index(None) is None
    assert _volume_index("") is None


def test_volume_index_bare_markers_are_none():
    """Bare "t"/"v" are unnumbered tome/volume markers, not letter ranks -
    they must NOT be treated as volume "t" or "v" (rank 19/21)."""
    assert _volume_index("t") is None
    assert _volume_index("v") is None


def test_volume_index_unrecognized_multi_letter_is_none():
    assert _volume_index("aa") is None


def test_volume_index_letter_rank():
    assert _volume_index("a") == 0
    assert _volume_index("c") == 2


def test_volume_index_numbered_markers():
    assert _volume_index("t2") == 1
    assert _volume_index("v3") == 2
    assert _volume_index("b1") == 0


# ---------------------------------------------------------------------------
# 6/7. Known TEI P5 violations (audit §1.2) - xfail until the correctif lands
# ---------------------------------------------------------------------------

def test_keywords_must_be_wrapped_in_textclass():
    """Audit 1.2 : TEI P5's profileDesc content model requires <keywords>
    to live inside a <textClass>."""
    root = _build_default_root()
    row = {"Sujet": "Peinture|Sculpture"}
    override_teiheader_from_csv(root, row, "TESTDOC0001")

    profileDesc = root.find(".//teiHeader/profileDesc")
    textClass = profileDesc.find("textClass")
    assert textClass is not None
    keywords = textClass.find("keywords")
    assert keywords is not None
    assert [t.text for t in keywords.findall("term")] == ["Peinture", "Sculpture"]


def test_ark_and_iiif_idno_must_precede_altidentifier():
    """Audit 1.2 : TEI P5's msIdentifier content model requires idno
    elements before altIdentifier."""
    root = _build_default_root()
    row = {
        "ARK": "ark:/12148/xyz",
        "manifest_iiif": "https://example/manifest.json",
    }
    override_teiheader_from_csv(root, row, "TESTDOC0001")

    msIdentifier = root.find(".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier")
    children = list(msIdentifier)
    alt_index = next(i for i, c in enumerate(children) if localname(c) == "altIdentifier")
    ark_index = next(
        i for i, c in enumerate(children)
        if localname(c) == "idno" and c.get("type") == "ark"
    )
    assert ark_index < alt_index


def test_extra_repository_and_cote_siblings_precede_altidentifier():
    """Audit 1.2, meme famille : les valeurs multiples de Localisation et de
    Cote deviennent des freres inseres a la suite de l'element existant, pas
    des appends en fin de msIdentifier (donc apres altIdentifier)."""
    root = _build_default_root()
    row = {
        "Localisation": "Munich, Bayerische Staatsbibliothek|Bibliothèque nationale",
        "Cote": "Shelfmark1|Shelfmark2",
    }
    override_teiheader_from_csv(root, row, "TESTDOC0001")

    msIdentifier = root.find(".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier")
    children = list(msIdentifier)
    alt_index = next(i for i, c in enumerate(children) if localname(c) == "altIdentifier")
    for tag, texte in [
        ("repository", "Bibliothèque nationale"),
        ("idno", "Shelfmark2"),
    ]:
        idx = next(
            i for i, c in enumerate(children)
            if localname(c) == tag and c.text == texte
        )
        assert idx < alt_index, f"<{tag}> '{texte}' doit preceder altIdentifier"


# ---------------------------------------------------------------------------
# Encodage unifie des evenements de vie (audit 5.6) et version d'application
# ---------------------------------------------------------------------------

def test_add_life_event_normalizes_date_and_builds_a_geonames_uri():
    """Audit 5.6 : un seul encodage — @when ISO, @ref en URI complete."""
    from src.metadata.csv_book import _add_life_event

    parent = etree.Element("person")
    birth = _add_life_event(parent, "birth", "1590/05/22", "Paris", "2988507")
    assert birth.get("when") == "1590-05-22"
    place = birth.find("placeName")
    assert place.text == "Paris"
    assert place.get("ref") == "https://www.geonames.org/2988507/"


def test_add_life_event_partial_and_empty_inputs():
    from src.metadata.csv_book import _add_life_event

    parent = etree.Element("person")
    # sans identifiant de lieu : pas de @ref invente
    death = _add_life_event(parent, "death", "1650", "Lyon", None)
    assert death.get("when") == "1650"
    assert death.find("placeName").get("ref") is None

    # date seule : pas de <placeName> vide
    only_date = _add_life_event(parent, "birth", "1600", None, None)
    assert only_date.find("placeName") is None

    # rien a encoder : aucun element cree
    before = len(parent)
    assert _add_life_event(parent, "birth", None, None, "2988507") is None
    assert len(parent) == before


def test_tei_version_number_keeps_the_numeric_prefix():
    """TEI exige un numero de version sur <application> ; la convention du
    corpus ("4.3.x") le rendait invalide."""
    from src.teiheader.default import tei_version_number

    assert tei_version_number("4.3.x") == "4.3"
    assert tei_version_number("8.0.x") == "8.0"
    assert tei_version_number("1.0.0") == "1.0.0"
    assert tei_version_number("2") == "2"
    assert tei_version_number("") == "0"
    assert tei_version_number(None) == "0"
    assert tei_version_number("vNext") == "0"
