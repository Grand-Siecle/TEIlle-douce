# -----------------------------------------------------------
# Book metadata loading module (csv_book)
# Loads document/book metadata from semicolon-delimited CSV files
# -----------------------------------------------------------
"""
Book metadata loading module.

This module provides functions to load and parse document/book metadata
from CSV files (metadata_livre.csv). The expected format is semicolon-delimited
with headers. It integrates with csv_person for author/contributor enrichment.
"""

import re
from pathlib import Path
from collections import defaultdict

import pandas as pd

from config import CSV_DELIMITER, BDD_PREFIX_PATTERN, METADATA_PERSON_CSV
from .csv_person import load_person_database, get_person_database


def load_metadata(csv_path):
    """
    Load metadata from a CSV file.

    Args:
        csv_path (Path or str): Path to the metadata CSV file.

    Returns:
        pd.DataFrame or None: Loaded DataFrame or None if loading fails.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        return None

    try:
        return pd.read_csv(csv_path, sep=CSV_DELIMITER)
    except Exception as e:
        print(f"[warn] Failed to read {csv_path}: {e}")
        return None


def find_metadata_row(df, doc_name):
    """
    Find the metadata row for a specific document.

    Searches the BDD column for rows starting with the document's prefix.
    The prefix is extracted from the document name (e.g., "ABC123_folder" -> "ABC123").

    Args:
        df (pd.DataFrame): Metadata DataFrame.
        doc_name (str): Document folder name.

    Returns:
        pd.Series or None: Matching row or None if not found.
    """
    if df is None or "BDD" not in df.columns:
        return None

    # Extract prefix from document name (letters followed by digits)
    prefix = _extract_bdd_prefix(doc_name)

    # Find matching rows
    matches = df[df["BDD"].astype(str).str.startswith(prefix)]
    return None if matches.empty else matches.iloc[0]


def _extract_bdd_prefix(doc_folder_name):
    """
    Extract the BDD prefix from a document folder name.

    Args:
        doc_folder_name (str): Document folder name.

    Returns:
        str: Extracted prefix or original name.
    """
    match = re.match(BDD_PREFIX_PATTERN, doc_folder_name)
    return match.group(1) if match else doc_folder_name


def _safe_value(row, key):
    """
    Safely get a value from a row, handling NaN and empty values.

    Args:
        row: DataFrame row or dict-like object.
        key (str): Column name to retrieve.

    Returns:
        str or None: The value or None if empty/NaN.
    """
    if row is None:
        return None
    value = row.get(key)
    if value is None:
        return None
    string = str(value).strip()
    return None if string == "" or string.lower() == "nan" else string


def _safe_value_list(row, key):
    """
    Safely get a list of values from a row, splitting on '|'.

    Args:
        row: DataFrame row or dict-like object.
        key (str): Column name to retrieve.

    Returns:
        list: List of stripped values, or empty list if none.
    """
    raw = _safe_value(row, key)
    if raw is None:
        return []
    return [v.strip() for v in raw.split("|") if v.strip()]


def build_metadata_dict(row):
    """
    Build a metadata dictionary from a CSV row.

    Converts a CSV row into the metadata format expected by the TEI header
    builder, with 'sru' and 'iiif' sub-dictionaries. Values containing '|'
    are split into lists.

    Args:
        row (pd.Series): A row from the metadata DataFrame.

    Returns:
        dict: Metadata dictionary with 'sru' and 'iiif' keys.
    """
    if row is None:
        return {
            "sru": defaultdict(lambda: None, {"found": False}),
            "iiif": defaultdict(lambda: None),
        }

    # Load person database if not already loaded
    person_db = get_person_database()
    if person_db is None:
        person_db = load_person_database(METADATA_PERSON_CSV)

    # Extract author information (split on |) - enriched with person data
    authors = []
    for aid in _safe_value_list(row, "ID_auteur"):
        if person_db and aid in person_db:
            author_data = person_db.enrich_author_data(aid, role="author")
            authors.append({
                "xmlid": author_data["xmlid"],
                "name": author_data["name"],
                "secondary_name": author_data["forename"],
                "namelink": author_data["namelink"],
                "primary_name": author_data["surname"],
                "isni": author_data["isni"],
                "ark": author_data["ark"],
                "birth_date": author_data["birth_date"],
                "death_date": author_data["death_date"],
                "role": "author",
            })
        else:
            authors.append({
                "xmlid": aid,
                "name": aid,
                "secondary_name": None,
                "namelink": None,
                "primary_name": None,
                "isni": None,
            })

    # Extract lists for fields that support multiple values
    publishers = _safe_value_list(row, "ID_Editeur") or _safe_value_list(row, "ID_imprimeurs")
    places = _safe_value_list(row, "Lieu_publication")
    languages = _safe_value_list(row, "langues")
    subjects = _safe_value_list(row, "Sujet") + _safe_value_list(row, "Matiere")
    repositories = _safe_value_list(row, "Localisation")
    idnos = _safe_value_list(row, "Cote")

    # Build SRU-like metadata (simulated from CSV)
    sru_data = {
        "found": row is not None,
        "ark": _safe_value(row, "ARK"),
        "title": _safe_value(row, "Titre_long") or _safe_value(row, "Titre_abrege"),
        "date": _safe_value(row, "Date_01") or _safe_value(row, "Date_02"),
        "publisher": publishers[0] if publishers else None,
        "publishers": publishers,
        "place": places[0] if places else None,
        "places": places,
        "language": languages[0] if languages else None,
        "languages": languages,
        "idno": idnos[0] if idnos else None,
        "idnos": idnos,
        "repository": repositories[0] if repositories else None,
        "repositories": repositories,
        "subject": subjects[0] if subjects else None,
        "subjects": subjects,
        "authors": authors,
    }

    # Build IIIF-like metadata
    iiif_data = {
        "manifest": _safe_value(row, "manifest_iiif"),
        "ark": _safe_value(row, "ARK"),
        "Creator": _safe_value(row, "ID_auteur"),
        "Title": _safe_value(row, "Titre_long") or _safe_value(row, "Titre_abrege"),
        "Date": _safe_value(row, "Date_01") or _safe_value(row, "Date_02"),
        "Publisher": publishers[0] if publishers else None,
        "Place": places[0] if places else None,
        "Extent": _safe_value(row, "Format"),
    }

    return {
        "sru": defaultdict(lambda: None, sru_data),
        "iiif": defaultdict(lambda: None, iiif_data),
    }


def override_teiheader_from_csv(root, row):
    """
    Inject CSV metadata into an existing TEI header.

    This function overrides placeholder values in the TEI header with
    actual metadata from the CSV file. Values containing '|' are split
    to create multiple TEI elements.

    Args:
        root (etree.Element): TEI root element.
        row (pd.Series): Metadata row from CSV.

    Returns:
        None: Modifies root in place.
    """
    if row is None:
        return

    from lxml import etree

    def set_text(xpath, value):
        """Set element text if value is valid."""
        if pd.isna(value) or value in (None, "", "nan"):
            return
        el = root.find(xpath)
        if el is not None:
            el.text = str(value)

    def set_text_multi(xpath, values, parent_xpath=None, tag_name=None):
        """
        Set element text for multiple values.

        First value goes in existing element, additional values create new siblings.
        """
        if not values:
            return
        # Set first value in existing element
        el = root.find(xpath)
        if el is not None:
            el.text = values[0]
            # Add siblings for additional values
            if len(values) > 1 and parent_xpath and tag_name:
                parent = root.find(parent_xpath)
                if parent is not None:
                    for val in values[1:]:
                        new_el = etree.SubElement(parent, tag_name)
                        new_el.text = val

    # Title
    titre = row.get("Titre_long") or row.get("Titre_abrege")
    set_text(".//teiHeader/fileDesc/titleStmt/title", titre)
    set_text(".//teiHeader/fileDesc/sourceDesc/bibl/title", titre)

    # Contributors - split on | for each role
    auteurs = _safe_value_list(row, "ID_auteur")
    imprimeurs = _safe_value_list(row, "ID_imprimeurs")
    libraires = _safe_value_list(row, "ID_libraires")
    editeurs = _safe_value_list(row, "ID_Editeur")
    traducteurs = _safe_value_list(row, "ID_Traducteurs")

    # Load person database for enrichment
    person_db = get_person_database()
    if person_db is None:
        person_db = load_person_database(METADATA_PERSON_CSV)

    def create_persname_element(parent, person_data):
        """Create a persName element with forename, surname, and identifiers."""
        persname = etree.SubElement(parent, "persName")
        if person_data.get("forename"):
            forename = etree.SubElement(persname, "forename")
            forename.text = person_data["forename"]
        if person_data.get("namelink"):
            namelink = etree.SubElement(persname, "nameLink")
            namelink.text = person_data["namelink"]
        if person_data.get("surname"):
            surname = etree.SubElement(persname, "surname")
            surname.text = person_data["surname"]
        # Add ISNI pointer
        if person_data.get("isni"):
            ptr = etree.SubElement(persname, "ptr", type="isni")
            ptr.attrib["target"] = f"https://isni.org/isni/{person_data['isni']}"
        # Add ARK pointer
        if person_data.get("ark"):
            ptr = etree.SubElement(persname, "ptr", type="ark")
            ptr.attrib["target"] = person_data["ark"]
        return persname

    def create_author_element(parent, person_id, role):
        """Create an author/editor element with full person data."""
        if person_db and person_id in person_db:
            person_data = person_db.enrich_author_data(person_id, role=role)
            author_el = etree.SubElement(parent, "author")
            author_el.attrib["role"] = role
            author_el.attrib["ref"] = f"#{person_id}"
            create_persname_element(author_el, person_data)
        else:
            author_el = etree.SubElement(parent, "author")
            author_el.attrib["role"] = role
            author_el.text = person_id
        return author_el

    # Add contributors to titleStmt
    titleStmt = root.find(".//teiHeader/fileDesc/titleStmt")
    if titleStmt is not None:
        # Remove existing empty author elements
        for old_author in titleStmt.findall("author"):
            if not old_author.text or old_author.text.strip() == "":
                titleStmt.remove(old_author)
        # Add one author element per contributor with role
        for aid in auteurs:
            create_author_element(titleStmt, aid, "author")
        for tid in traducteurs:
            create_author_element(titleStmt, tid, "translator")
        for iid in imprimeurs:
            create_author_element(titleStmt, iid, "printer")
        for lid in libraires:
            create_author_element(titleStmt, lid, "bookseller")
        for eid in editeurs:
            create_author_element(titleStmt, eid, "editor")

    # Publication places - support multiple
    lieux = _safe_value_list(row, "Lieu_publication")
    set_text_multi(
        ".//teiHeader/fileDesc/sourceDesc/bibl/pubPlace",
        lieux,
        ".//teiHeader/fileDesc/sourceDesc/bibl",
        "pubPlace"
    )

    # Publishers - combine editeurs and imprimeurs
    publishers = editeurs if editeurs else imprimeurs
    set_text_multi(
        ".//teiHeader/fileDesc/sourceDesc/bibl/publisher",
        publishers,
        ".//teiHeader/fileDesc/sourceDesc/bibl",
        "publisher"
    )

    # Date
    set_text(".//teiHeader/fileDesc/sourceDesc/bibl/date", row.get("Date_01") or row.get("Date_02"))

    # Repository info - support multiple
    localisations = _safe_value_list(row, "Localisation")
    set_text_multi(
        ".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier/repository",
        localisations,
        ".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier",
        "repository"
    )

    # Cote/idno - support multiple
    cotes = _safe_value_list(row, "Cote")
    set_text_multi(
        ".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier/idno",
        cotes,
        ".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier",
        "idno"
    )

    # Languages - support multiple
    langues = _safe_value_list(row, "langues")
    if langues:
        langUsage = root.find(".//teiHeader/profileDesc/langUsage")
        if langUsage is not None:
            # Set first language in existing element
            lang_el = langUsage.find("language")
            if lang_el is not None:
                lang_el.text = langues[0]
                lang_el.attrib["ident"] = langues[0][:3].lower() if langues[0] else ""
            # Add additional languages
            for lang in langues[1:]:
                new_lang = etree.SubElement(langUsage, "language")
                new_lang.text = lang
                new_lang.attrib["ident"] = lang[:3].lower() if lang else ""

    # Subjects - split both Sujet and Matiere on |
    sujets_raw = _safe_value_list(row, "Sujet")
    matieres_raw = _safe_value_list(row, "Matiere")
    all_sujets = sujets_raw + matieres_raw
    if all_sujets:
        prof = root.find(".//teiHeader/profileDesc")
        if prof is not None:
            keywords = etree.SubElement(prof, "keywords")
            for sujet in all_sujets:
                term = etree.SubElement(keywords, "term")
                term.text = sujet

    # ARK and IIIF manifest identifiers
    ark = row.get("ARK")
    manifest = row.get("manifest_iiif")
    if ark or manifest:
        idno_parent = root.find(".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier")
        if idno_parent is not None:
            if ark:
                id_ark = etree.SubElement(idno_parent, "idno", type="ark")
                id_ark.text = ark
            if manifest:
                id_manifest = etree.SubElement(idno_parent, "idno", type="iiif")
                id_manifest.text = manifest

    # Build listPerson in particDesc with all referenced persons
    all_person_ids = set(auteurs + traducteurs + imprimeurs + libraires + editeurs)
    if all_person_ids and person_db:
        profileDesc = root.find(".//teiHeader/profileDesc")
        if profileDesc is not None:
            particDesc = etree.SubElement(profileDesc, "particDesc")
            listPerson = etree.SubElement(particDesc, "listPerson")

            for pid in sorted(all_person_ids):
                if pid in person_db:
                    person_data = person_db.enrich_author_data(pid)
                    person_el = etree.SubElement(listPerson, "person")
                    person_el.attrib["{http://www.w3.org/XML/1998/namespace}id"] = pid

                    # persName
                    persname = etree.SubElement(person_el, "persName")
                    if person_data.get("forename"):
                        forename = etree.SubElement(persname, "forename")
                        forename.text = person_data["forename"]
                    if person_data.get("namelink"):
                        namelink = etree.SubElement(persname, "nameLink")
                        namelink.text = person_data["namelink"]
                    if person_data.get("surname"):
                        surname = etree.SubElement(persname, "surname")
                        surname.text = person_data["surname"]

                    # birth
                    if person_data.get("birth_date"):
                        birth = etree.SubElement(person_el, "birth")
                        birth.attrib["when"] = _normalize_date(person_data["birth_date"])
                        if person_data.get("birth_place"):
                            placename = etree.SubElement(birth, "placeName")
                            placename.text = person_data["birth_place"]

                    # death
                    if person_data.get("death_date"):
                        death = etree.SubElement(person_el, "death")
                        death.attrib["when"] = _normalize_date(person_data["death_date"])
                        if person_data.get("death_place"):
                            placename = etree.SubElement(death, "placeName")
                            placename.text = person_data["death_place"]

                    # idno - ISNI
                    if person_data.get("isni"):
                        idno_isni = etree.SubElement(person_el, "idno", type="isni")
                        idno_isni.text = person_data["isni"]

                    # idno - ARK
                    if person_data.get("ark"):
                        idno_ark = etree.SubElement(person_el, "idno", type="ark")
                        idno_ark.text = person_data["ark"]

                    # note
                    if person_data.get("note"):
                        note = etree.SubElement(person_el, "note")
                        note.text = person_data["note"]
                else:
                    # Person not in database - create minimal entry
                    person_el = etree.SubElement(listPerson, "person")
                    person_el.attrib["{http://www.w3.org/XML/1998/namespace}id"] = pid
                    persname = etree.SubElement(person_el, "persName")
                    persname.text = pid


def _normalize_date(date_str):
    """
    Normalize a date string to ISO format for TEI @when attribute.

    Handles formats like: 1590, 1590/05/22, 15, 159, etc.

    Args:
        date_str (str): Date string from CSV.

    Returns:
        str: Normalized date string.
    """
    if not date_str:
        return ""
    date_str = str(date_str).strip()
    # Replace / with - for ISO format
    date_str = date_str.replace("/", "-")
    return date_str
