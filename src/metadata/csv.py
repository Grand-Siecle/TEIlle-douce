# -----------------------------------------------------------
# CSV metadata loading module
# Loads document metadata from semicolon-delimited CSV files
# -----------------------------------------------------------
"""
CSV metadata loading module.

This module provides functions to load and parse document metadata from
CSV files. The expected format is semicolon-delimited with headers.
"""

import re
from pathlib import Path
from collections import defaultdict

import pandas as pd

from config import CSV_DELIMITER, BDD_PREFIX_PATTERN


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

    # Extract author information (split on |)
    authors = []
    for aid in _safe_value_list(row, "ID_auteur"):
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
    editeurs = _safe_value_list(row, "ID_Editeur")
    traducteurs = _safe_value_list(row, "ID_Traducteurs")

    # Combine all contributors for titleStmt/author
    all_contribs = auteurs + traducteurs + imprimeurs + editeurs
    if all_contribs:
        titleStmt = root.find(".//teiHeader/fileDesc/titleStmt")
        if titleStmt is not None:
            # Remove existing empty author elements
            for old_author in titleStmt.findall("author"):
                if not old_author.text or old_author.text.strip() == "":
                    titleStmt.remove(old_author)
            # Add one author element per contributor
            for contrib in all_contribs:
                author_el = etree.SubElement(titleStmt, "author")
                author_el.text = contrib

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
