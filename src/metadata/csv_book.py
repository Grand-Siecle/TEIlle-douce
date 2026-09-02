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

import logging
import re
from pathlib import Path
from collections import defaultdict

import pandas as pd
from lxml import etree

from ..constants import KEYWORDS_TAXONOMY
from ..dates import date_attributes
from config import (
    CSV_DELIMITER,
    BDD_PREFIX_PATTERN,
    METADATA_PERSON_CSV,
    PLACEHOLDER_INFO_UNAVAILABLE,
)

logger = logging.getLogger(__name__)
from .csv_person import load_person_database, get_person_database
from ..utils.files import parse_document_id
from ..constants import XML_ID


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
        # Same protections as PersonDatabase.load (audit 5.3): no int64/
        # float64 inference eating leading zeros or appending ".0" to
        # dates and shelfmarks, no literal 'NA' cells turned into NaN.
        return pd.read_csv(
            csv_path, sep=CSV_DELIMITER, dtype=str, keep_default_na=False
        )
    except Exception as e:
        logger.warning("Failed to read %s: %s", csv_path, e)
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


def safe_person_id(pid):
    """Role columns sometimes hold raw arks instead of PERS ids; arks are
    not valid NCNames (':' '/') so derive a safe xml:id from the ark tail.

    Note: distinct from the NER pipeline's "pers-<uuid>" ids (hyphen) —
    "pers_<ark-tail>" (underscore) marks CSV-derived unresolved persons.
    """
    pid = str(pid).strip()
    if pid.startswith("ark:"):
        tail = pid.rstrip("/").rsplit("/", 1)[-1]
        if not tail or ":" in tail:
            # Malformed ark: sanitize the whole string instead of colliding
            # on an empty/invalid tail.
            tail = re.sub(r"[^A-Za-z0-9._\-]", "-", pid)
        return "pers_" + tail
    return pid


def _volume_index(volume):
    """'a'->0, 'b'->1, 't2'->1, 'v3'->2 ; None/unknown -> None."""
    if not volume:
        return None
    v = volume.lower()
    # Bare "t"/"v" are unnumbered tome/volume markers, not letter ranks.
    if len(v) == 1 and v.isalpha() and v not in ("t", "v"):
        return ord(v) - ord("a")
    m = re.match(r"[vtb](\d+)$", v)
    if m:
        return int(m.group(1)) - 1
    return None


def select_manifest(manifests, volume):
    """
    Pick the IIIF manifest matching a document volume.

    One manifest: always return it. Several: return the volume's manifest
    (order of the CSV '|' list = volume order). Unknown/missing volume
    with several manifests -> None, caller falls back to listing all.
    """
    if not manifests:
        return None
    if len(manifests) == 1:
        return manifests[0]
    idx = _volume_index(volume)
    if idx is None or idx >= len(manifests):
        return None
    return manifests[idx]


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
                "xmlid": safe_person_id(aid),
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
        "manifests": _safe_value_list(row, "manifest_iiif"),
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


# Language names as the catalogue writes them -> ISO 639-2 ident.
# Slicing the first three letters (the previous approach) works only by
# accident: "français"->fra and "latin"->lat are right, but
# "espagnol"->esp (spa), "anglais"->ang (eng — and "ang" IS Old
# English), "allemand"->all (deu), "grec"->gre (grc for the ancient
# language) are all wrong. This path is what survives on documents
# where detection found nothing, so its codes must be right.
_LANGUAGE_IDENTS = {
    "francais": "fra", "français": "fra", "french": "fra",
    "latin": "lat",
    "grec": "grc", "grec ancien": "grc", "ancient greek": "grc", "greek": "grc",
    "italien": "ita", "italian": "ita",
    "espagnol": "spa", "spanish": "spa",
    "anglais": "eng", "english": "eng",
    "allemand": "deu", "german": "deu",
    "neerlandais": "nld", "néerlandais": "nld", "dutch": "nld",
}


def _language_ident(name):
    """ISO ident for a catalogue language name; "und" when unknown."""
    if not name:
        return "und"
    ident = _LANGUAGE_IDENTS.get(str(name).strip().lower())
    if ident:
        return ident
    logger.warning("Unknown catalogue language %r — ident set to 'und'", name)
    return "und"


# CSV field -> (TEI element, @type) for the person's identifiers and
# notes, in the order they are emitted inside <person>. Adding a CSV
# column is a line here, not another copy of the same if/SubElement
# block (audit 4.4).
_PERSON_EXTRA_FIELDS = (
    ("isni", "idno", "isni"),
    ("ark", "idno", "ark"),
    ("portraits", "note", "portrait"),
    ("oeuvre", "note", "works"),
    ("milieux_reseaux", "note", "networks"),
    ("contacts_artistes", "note", "contacts"),
    ("fortune_critique", "note", "reception"),
    ("publications", "note", "publications"),
    ("citations", "note", "citations"),
    ("bibliographie", "note", "bibliography"),
    ("webographie", "note", "webography"),
    ("note", "note", None),
    ("commentaires", "note", "comments"),
)


# =============================================================================
# TEI ELEMENT BUILDERS
#
# Module level, not nested inside override_teiheader_from_csv: four
# closures over `person_db` made the function untestable in pieces
# (audit 4.4). The database is an explicit parameter now.
# =============================================================================

def _add_life_event(parent, tag, date, place, place_id):
    """
    Add a <birth>/<death> to *parent*, one way for the whole file.

    titleStmt and listPerson used to encode the same CSV fields
    differently — raw "1590/05/22" against ISO "1590-05-22", a geonames
    <ptr> against a bare numeric @ref (a relative URI resolving to
    nothing) — audit 5.6. Here: normalized @when, and @ref carrying the
    full geonames URI.
    """
    if not date and not place:
        return None
    event = etree.SubElement(parent, tag)
    if date:
        attrs = date_attributes(date)
        for key, value in attrs.items():
            event.attrib[key] = value
        if not attrs:
            # Unparseable: keep what the catalogue says rather than drop
            # it or assert a date the source does not support.
            event.text = str(date).strip()
    if place:
        placename = etree.SubElement(event, "placeName")
        placename.text = str(place)
        if place_id:
            placename.attrib["ref"] = f"https://www.geonames.org/{place_id}/"
    return event


def create_persname_element(parent, person_data, with_pointers=True):
    """
    Create a <persName> with the name parts, and optionally the ISNI/ARK
    pointers (a <respStmt> in <bibl> wants the name alone).
    """
    persname = etree.SubElement(parent, "persName")
    if person_data.get("forename"):
        forename = etree.SubElement(persname, "forename")
        forename.text = person_data["forename"]
    if person_data.get("namelink"):
        # The CSV column behind "namelink" is GenName, which listPerson
        # already encodes as <genName>. <nameLink> means a linking
        # particle ("de", "van"), so the two paths disagreed on the same
        # cell and this one was semantically wrong too (audit 5.6).
        genname = etree.SubElement(persname, "genName")
        genname.text = person_data["namelink"]
    if person_data.get("surname"):
        surname = etree.SubElement(persname, "surname")
        surname.text = person_data["surname"]
    if not with_pointers:
        return persname
    # Add ISNI pointer
    if person_data.get("isni"):
        ptr = etree.SubElement(persname, "ptr", type="isni")
        ptr.attrib["target"] = f"https://isni.org/isni/{person_data['isni']}"
    # Add ARK pointer
    if person_data.get("ark"):
        ptr = etree.SubElement(persname, "ptr", type="ark")
        ptr.attrib["target"] = person_data["ark"]
    return persname

def _ark_fallback(element, person_id):
    """
    Encode an unresolved contributor id.

    A raw ark is not a name: it goes in an <idno>, valid directly inside
    <author>/<editor>/<person> (model.pPart.data). Anything else is a
    local id and can stand as the element's text. Written once — this
    shape used to be repeated at four sites (audit 4.4).
    """
    if str(person_id).startswith("ark:"):
        idno_el = etree.SubElement(element, "idno", type="ark")
        idno_el.text = person_id
    else:
        element.text = person_id


def _contributor_element(parent, person_id, person_db, tag, role=None):
    """
    Create an <author> or <editor> for titleStmt.

    Name and pointers only: <birth>/<death> are not allowed inside these
    elements (verified against tei_all.rng) and the person's life events
    live in the <listPerson> entry @ref points at — the authority record
    (audit 5.6). The two builders were copy-paste twins differing by tag
    and @role alone (audit 4.4).
    """
    element = etree.SubElement(parent, tag)
    if role:
        element.attrib["role"] = role
    # listPerson gets an entry for this id whether or not the database
    # resolves it — @ref must point at it, or that record is orphaned.
    element.attrib["ref"] = f"#{safe_person_id(person_id)}"

    if person_db and person_id in person_db:
        person_data = person_db.enrich_author_data(person_id, role=role or "author")
        create_persname_element(element, person_data)
    else:
        _ark_fallback(element, person_id)
    return element


def create_author_element(parent, person_id, person_db):
    """Create an <author> for titleStmt (see _contributor_element)."""
    return _contributor_element(parent, person_id, person_db, "author")


def create_editor_element(parent, person_id, role, person_db):
    """Create an <editor role="..."> for titleStmt (see _contributor_element)."""
    return _contributor_element(parent, person_id, person_db, "editor", role=role)


def create_bibl_respstmt(parent, person_id, resp_label, person_db):
    """
    Create a <respStmt> in <bibl> with a French role label.

    respStmt's content model is strict — resp+, (name|orgName|persName)+
    — so an unresolved ark cannot be a sibling of persName here (unlike
    <author>/<editor>); it is nested inside the otherwise-empty persName,
    where <idno> is valid.
    """
    respstmt = etree.SubElement(parent, "respStmt")
    resp_el = etree.SubElement(respstmt, "resp")
    resp_el.text = resp_label

    if person_db and person_id in person_db:
        person_data = person_db.enrich_author_data(person_id)
        # Same name parts as everywhere else, minus the identifier
        # pointers, which belong to the listPerson authority record.
        persname = create_persname_element(respstmt, person_data, with_pointers=False)
        persname.attrib["ref"] = f"#{safe_person_id(person_id)}"
    else:
        persname = etree.SubElement(respstmt, "persName")
        _ark_fallback(persname, person_id)
    return respstmt


def _set_creation(root, date_raw, attrs):
    """Record in <profileDesc> when the text was produced.

    <bibl><date> dates the edition described in the sourceDesc; TEI's
    <creation> dates the work itself. Without it nothing in the file
    answers "when is this text from" without walking into the source
    description — and a corpus is queried by period before anything else.
    """
    profile = root.find(".//teiHeader/profileDesc")
    if profile is None or not date_raw or not attrs:
        return None

    creation = profile.find("creation")
    if creation is None:
        # TEI: <creation> comes first in <profileDesc>.
        creation = etree.Element("creation")
        profile.insert(0, creation)
    for child in list(creation):
        creation.remove(child)
    date_el = etree.SubElement(creation, "date", attrs)
    date_el.text = str(date_raw)
    return creation


def override_teiheader_from_csv(root, row, document_name=None):
    """
    Inject CSV metadata into an existing TEI header.

    This function overrides placeholder values in the TEI header with
    actual metadata from the CSV file. Values containing '|' are split
    to create multiple TEI elements.

    Args:
        root (etree.Element): TEI root element.
        row (pd.Series): Metadata row from CSV.
        document_name (str, optional): Document folder name, used to
            determine the volume marker for selecting the matching
            IIIF manifest when several are listed in the CSV.

    Returns:
        None: Modifies root in place.
    """
    if row is None:
        return


    def set_text(xpath, value):
        """Set element text if value is valid."""
        if pd.isna(value) or value in (None, "", "nan"):
            return
        el = root.find(xpath)
        if el is not None:
            el.text = str(value)

    def set_text_multi(xpath, values):
        """
        Set element text for multiple values.

        First value goes in the existing element; additional values become
        siblings of the same tag inserted right after it, so the P5 element
        order of the parent (e.g. idno before altIdentifier in msIdentifier,
        audit 1.2) is preserved.
        """
        if not values:
            return
        el = root.find(xpath)
        if el is not None:
            el.text = values[0]
            anchor = el
            for val in values[1:]:
                new_el = etree.Element(el.tag)
                new_el.text = val
                anchor.addnext(new_el)
                anchor = new_el

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

    # titleStmt: only authors and translators (intellectual contributors)
    titleStmt = root.find(".//teiHeader/fileDesc/titleStmt")
    if titleStmt is not None:
        # Remove existing empty author elements
        for old_author in titleStmt.findall("author"):
            if not old_author.text or old_author.text.strip() == "":
                titleStmt.remove(old_author)
        # Authors
        for aid in auteurs:
            create_author_element(titleStmt, aid, person_db)
        # Translators as <editor role="translator">
        for tid in traducteurs:
            create_editor_element(titleStmt, tid, "translator", person_db)

    # Publication places - support multiple
    lieux = _safe_value_list(row, "Lieu_publication")
    set_text_multi(".//teiHeader/fileDesc/sourceDesc/bibl/pubPlace", lieux)

    # sourceDesc/bibl: imprimeurs, libraires, éditeurs as <respStmt>
    bibl = root.find(".//teiHeader/fileDesc/sourceDesc/bibl")
    if bibl is not None:
        # Remove placeholder publisher elements
        for old_pub in bibl.findall("publisher"):
            bibl.remove(old_pub)
        # Add respStmt for each production/distribution role
        for iid in imprimeurs:
            create_bibl_respstmt(bibl, iid, "Imprimeur", person_db)
        for lid in libraires:
            create_bibl_respstmt(bibl, lid, "Libraire", person_db)
        for eid in editeurs:
            create_bibl_respstmt(bibl, eid, "Éditeur", person_db)

    # Date. The <bibl> one describes the edition; <creation> in
    # profileDesc says when the text it carries was produced, which is
    # what a diachronic query reads (audit 1.10) — and it is the same
    # cell, so both are normalized by the same parser.
    date_raw = row.get("Date_01") or row.get("Date_02")
    date_attrs = date_attributes(date_raw)  # parsed once: an unusable
    # value must not log its warning twice per document
    set_text(".//teiHeader/fileDesc/sourceDesc/bibl/date", date_raw)
    date_el = root.find(".//teiHeader/fileDesc/sourceDesc/bibl/date")
    if date_el is not None:
        # Clear what a previous pass asserted: a run over the same tree
        # with a differently-shaped date would otherwise leave @when
        # beside a @notBefore/@notAfter span that excludes it.
        for key in ("when", "notBefore", "notAfter", "cert"):
            date_el.attrib.pop(key, None)
        for key, value in date_attrs.items():
            date_el.set(key, value)
    _set_creation(root, date_raw, date_attrs)

    # Repository info - support multiple.
    # Localisation is stored as "Ville, Institution" (e.g. "Munich, Bayerische
    # Staatsbibliothek"): the city goes to <settlement>, the institution to
    # <repository>. Only the first value drives <settlement> (TEI allows a
    # single settlement); additional pipe-separated values become extra
    # <repository> siblings, kept as-is if they have no comma.
    localisations = _safe_value_list(row, "Localisation")
    if localisations:
        first_loc = localisations[0]
        if "," in first_loc:
            settlement_txt, repo_txt = (p.strip() for p in first_loc.split(",", 1))
        else:
            settlement_txt, repo_txt = None, first_loc.strip()
        repo_texts = [repo_txt] + [loc.strip() for loc in localisations[1:]]
        set_text_multi(
            ".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier/repository",
            repo_texts,
        )
        msid = root.find(".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier")
        if msid is not None:
            settlement_el = msid.find("settlement")
            if settlement_el is not None:
                if settlement_txt:
                    settlement_el.text = settlement_txt
                elif settlement_el.text == PLACEHOLDER_INFO_UNAVAILABLE:
                    # No city could be derived: drop the placeholder rather
                    # than keep a misleading default.
                    msid.remove(settlement_el)

    # <country> has no CSV source; drop it if it's still the empty placeholder
    # left by DefaultTree.
    country_el = root.find(".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier/country")
    if country_el is not None and (country_el.text is None or not country_el.text.strip()):
        country_parent = country_el.getparent()
        if country_parent is not None:
            country_parent.remove(country_el)

    # Cote/idno - support multiple
    cotes = _safe_value_list(row, "Cote")
    set_text_multi(".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier/idno", cotes)

    # Languages — the catalogue's declared languages.
    #
    # Audit 4.12 calls this block dead because finalize_langusage()
    # rewrites <langUsage> afterwards. Measured: it does so ONLY when
    # language detection produced statistics (build_langusage returns
    # early, without clearing, on empty stats). So in a normal run these
    # values are indeed replaced by the detected ones — but when nothing
    # was detected (an image-only document, or detect_lang=False) they
    # are what survives, and a catalogue declaration beats an empty
    # langUsage. Kept deliberately; pinned by
    # tests/test_csv_book.py::test_csv_languages_survive_when_nothing_is_detected.
    langues = _safe_value_list(row, "langues")
    if langues:
        langUsage = root.find(".//teiHeader/profileDesc/langUsage")
        if langUsage is not None:
            # Set first language in existing element
            lang_el = langUsage.find("language")
            if lang_el is not None:
                lang_el.text = langues[0]
                lang_el.attrib["ident"] = _language_ident(langues[0])
            # Add additional languages
            for lang in langues[1:]:
                new_lang = etree.SubElement(langUsage, "language")
                new_lang.text = lang
                new_lang.attrib["ident"] = _language_ident(lang)

    # Subjects - split both Sujet and Matiere on |
    sujets_raw = _safe_value_list(row, "Sujet")
    matieres_raw = _safe_value_list(row, "Matiere")
    all_sujets = sujets_raw + matieres_raw
    if all_sujets:
        prof = root.find(".//teiHeader/profileDesc")
        if prof is not None:
            # P5: <keywords> is only valid inside <textClass> (audit 1.2)
            text_class = prof.find("textClass")
            if text_class is None:
                text_class = etree.SubElement(prof, "textClass")
            keywords = etree.SubElement(
                text_class, "keywords",
                scheme=f"#{KEYWORDS_TAXONOMY['id']}",
            )
            for sujet in all_sujets:
                term = etree.SubElement(keywords, "term")
                term.text = sujet

    # ARK and IIIF manifest identifiers
    ark = _safe_value(row, "ARK")
    manifests = _safe_value_list(row, "manifest_iiif")
    if ark or manifests:
        idno_parent = root.find(".//teiHeader/fileDesc/sourceDesc/msDesc/msIdentifier")
        if idno_parent is not None:
            # P5: idno must precede altIdentifier in msIdentifier (audit 1.2)
            alt_identifier = idno_parent.find("altIdentifier")

            def add_idno(text, **atts):
                idno = etree.Element("idno", **atts)
                idno.text = text
                if alt_identifier is not None:
                    alt_identifier.addprevious(idno)
                else:
                    idno_parent.append(idno)

            if ark:
                add_idno(ark, type="ark")
            volume = parse_document_id(document_name)[1] if document_name else None
            selected = select_manifest(manifests, volume)
            if selected:
                add_idno(selected, type="iiif")
            else:
                # Unknown volume: list every manifest, numbered
                for i, m in enumerate(manifests, 1):
                    add_idno(m, type="iiif", n=str(i))

    # Build listPerson in particDesc with all referenced persons
    all_person_ids = set(auteurs + traducteurs + imprimeurs + libraires + editeurs)
    if all_person_ids and person_db:
        profileDesc = root.find(".//teiHeader/profileDesc")
        if profileDesc is not None:
            # Find-or-create: the NER phase runs first and may already
            # have created the particDesc — appending a second one left
            # the header with two containers for one concept (audit 4.2).
            #
            # Inside it, the curated CSV list and the automatic NER list
            # (source="#ner-auto") stay SEPARATE on purpose: audit 1.3
            # defers merging them to the reconciliation repo. A consumer
            # that wants both must iterate listPerson, not find() one.
            particDesc = profileDesc.find("particDesc")
            if particDesc is None:
                particDesc = etree.SubElement(profileDesc, "particDesc")

            # Replace-not-append, like inject_header_entities does for its
            # own list (audit 6.13): a second pass over the same tree must
            # not duplicate the persons — and their xml:ids with them.
            for old_list in list(particDesc):
                if (etree.QName(old_list).localname == "listPerson"
                        and old_list.get("source") != "#ner-auto"):
                    particDesc.remove(old_list)
            listPerson = etree.SubElement(particDesc, "listPerson")

            for pid in sorted(all_person_ids):
                if pid in person_db:
                    person = person_db.get(pid)
                    person_el = etree.SubElement(listPerson, "person")
                    person_el.attrib[XML_ID] = safe_person_id(pid)

                    # sex attribute
                    if person.get("sex"):
                        person_el.attrib["sex"] = person["sex"]

                    # persName with all sub-elements
                    persname = etree.SubElement(person_el, "persName")
                    if person.get("role_name"):
                        rolename_el = etree.SubElement(persname, "roleName")
                        rolename_el.text = person["role_name"]
                    if person.get("forename"):
                        forename = etree.SubElement(persname, "forename")
                        forename.text = person["forename"]
                    if person.get("gen_name"):
                        genname_el = etree.SubElement(persname, "genName")
                        genname_el.text = person["gen_name"]
                    if person.get("surname"):
                        surname = etree.SubElement(persname, "surname")
                        surname.text = person["surname"]
                    if person.get("nicknames"):
                        addname_el = etree.SubElement(persname, "addName", type="nickname")
                        addname_el.text = person["nicknames"]

                    _add_life_event(
                        person_el, "birth", person.get("birth_date"),
                        person.get("birth_place"), person.get("birth_place_id"),
                    )
                    _add_life_event(
                        person_el, "death", person.get("death_date"),
                        person.get("death_place"), person.get("death_place_id"),
                    )

                    # faith (Confession)
                    if person.get("confession"):
                        faith_el = etree.SubElement(person_el, "faith")
                        faith_el.text = person["confession"]

                    # education (Formation)
                    if person.get("formation"):
                        education_el = etree.SubElement(person_el, "education")
                        education_el.text = person["formation"]

                    # occupation (Professions)
                    if person.get("professions"):
                        occupation_el = etree.SubElement(person_el, "occupation")
                        occupation_el.text = person["professions"]

                    # Identifiers and notes: one table, one loop. The
                    # same "if person.get(X): <note type=Y>" shape was
                    # written out thirteen times (audit 4.4) — every new
                    # CSV column meant another copy-paste of it.
                    for field, tag, note_type in _PERSON_EXTRA_FIELDS:
                        value = person.get(field)
                        if not value:
                            continue
                        attrs = {"type": note_type} if note_type else {}
                        el = etree.SubElement(person_el, tag, attrs)
                        el.text = value

                else:
                    # Person not in database - create minimal entry
                    person_el = etree.SubElement(listPerson, "person")
                    person_el.attrib[XML_ID] = safe_person_id(pid)
                    persname = etree.SubElement(person_el, "persName")
                    if pid.startswith("ark:"):
                        # Unknown name: the ark goes in an idno, not in persName.
                        idno_el = etree.SubElement(person_el, "idno", type="ark")
                        idno_el.text = pid
                    else:
                        persname.text = pid


