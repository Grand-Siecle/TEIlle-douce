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
        return pd.read_csv(csv_path, sep=";")
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
    match = re.match(r"([A-Za-z]+?\d+)", doc_folder_name)
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


def build_metadata_dict(row):
    """
    Build a metadata dictionary from a CSV row.

    Converts a CSV row into the metadata format expected by the TEI header
    builder, with 'sru' and 'iiif' sub-dictionaries.

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

    # Extract author information
    authors_field = _safe_value(row, "ID_auteur")
    authors = []
    if authors_field:
        for aid in str(authors_field).split("|"):
            aid = aid.strip()
            if aid:
                authors.append({
                    "xmlid": aid,
                    "name": aid,
                    "secondary_name": None,
                    "namelink": None,
                    "primary_name": None,
                    "isni": None,
                })

    # Build SRU-like metadata (simulated from CSV)
    sru_data = {
        "found": row is not None,
        "ark": _safe_value(row, "ARK"),
        "title": _safe_value(row, "Titre_long") or _safe_value(row, "Titre_abrege"),
        "date": _safe_value(row, "Date_01") or _safe_value(row, "Date_02"),
        "publisher": _safe_value(row, "ID_Editeur") or _safe_value(row, "ID_imprimeurs"),
        "place": _safe_value(row, "Lieu_publication"),
        "language": _safe_value(row, "langues"),
        "idno": _safe_value(row, "Cote"),
        "repository": _safe_value(row, "Localisation"),
        "subject": _safe_value(row, "Sujet") or _safe_value(row, "Matiere"),
        "authors": authors,
    }

    # Build IIIF-like metadata
    iiif_data = {
        "manifest": _safe_value(row, "manifest_iiif"),
        "ark": _safe_value(row, "ARK"),
        "Creator": _safe_value(row, "ID_auteur"),
        "Title": _safe_value(row, "Titre_long") or _safe_value(row, "Titre_abrege"),
        "Date": _safe_value(row, "Date_01") or _safe_value(row, "Date_02"),
        "Publisher": _safe_value(row, "ID_Editeur") or _safe_value(row, "ID_imprimeurs"),
        "Place": _safe_value(row, "Lieu_publication"),
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
    actual metadata from the CSV file.

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

    # Title
    titre = row.get("Titre_long") or row.get("Titre_abrege")
    set_text(".//teiHeader/fileDesc/titleStmt/title", titre)
    set_text(".//teiHeader/fileDesc/sourceDesc/bibl/title", titre)

    # Contributors
    auteur = row.get("ID_auteur")
    imprimeur = row.get("ID_imprimeurs")
    editeur = row.get("ID_Editeur")
    traducteur = row.get("ID_Traducteurs")

    contrib = ", ".join(
        str(x) for x in [auteur, traducteur, imprimeur, editeur] if x and not pd.isna(x)
    )
    if contrib:
        el = root.find(".//teiHeader/fileDesc/titleStmt/author")
        if el is None:
            parent = root.find(".//teiHeader/fileDesc/titleStmt")
            if parent is not None:
                el = etree.SubElement(parent, "author")
        if el is not None:
            el.text = contrib

    # Publication info
    set_text(".//teiHeader/fileDesc/sourceDesc/bibl/pubPlace", row.get("Lieu_publication"))
    set_text(".//teiHeader/fileDesc/sourceDesc/bibl/publisher", editeur or imprimeur)
    set_text(".//teiHeader/fileDesc/sourceDesc/bibl/date", row.get("Date_01") or row.get("Date_02"))

    # Repository info
    set_text(".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier/repository", row.get("Localisation"))
    set_text(".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier/idno", row.get("Cote"))

    # Language
    set_text(".//teiHeader/profileDesc/langUsage/language", row.get("langues"))

    # Subjects
    sujets = [row.get("Sujet"), row.get("Matiere")]
    sujets = [s for s in sujets if s and not pd.isna(s)]
    if sujets:
        prof = root.find(".//teiHeader/profileDesc")
        if prof is not None:
            text = "; ".join(sujets)
            keywords = etree.SubElement(prof, "keywords")
            term = etree.SubElement(keywords, "term")
            term.text = text

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
