# -----------------------------------------------------------
# Configuration for ALTO2TEI pipeline
# Modify these values to customize the pipeline
# -----------------------------------------------------------

import math
import os
import warnings
from pathlib import Path


def _env(name, default, convert=None):
    """
    Read a setting from the environment, or keep *default*.

    Single conversion path for every setting type below, so they cannot
    disagree on what a doubtful value means:

    - unset keeps the default;
    - SET BUT EMPTY also keeps the default, and says so. A wrapper doing
      `export ALTO2TEI_MODERNIZE_URL="${MODERNIZE_URL}"` with the outer
      variable unset would otherwise hand out an empty base URL, which
      disables the service with no message a reader can act on;
    - a value *convert* rejects keeps the default AND says so: silently
      ignoring a typo would leave the operator believing a setting took
      effect. The converter states the reason ("is not a number").
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    raw = raw.strip()
    if not raw:
        warnings.warn(
            f"{name} is set but empty — keeping the default {default!r}",
            RuntimeWarning, stacklevel=3,
        )
        return default
    if convert is None:
        return raw
    try:
        return convert(raw)
    except ValueError as reason:
        warnings.warn(
            f"{name}={raw!r} {reason} — keeping the default {default!r}",
            RuntimeWarning, stacklevel=3,
        )
        return default


def _number(minimum=None, maximum=None):
    """Converter for a finite float within [minimum, maximum] (inclusive).

    Parsing alone is not enough: `float("nan")` and `float("inf")` both
    succeed, and a NaN threshold quietly turns every comparison against
    it into False — the guard reading it would stop rejecting anything.
    Out-of-range values are the other readable-but-wrong case (a
    similarity given as 95 rather than 0.95, a negative timeout).
    """
    def convert(raw):
        try:
            value = float(raw)
        except ValueError:
            raise ValueError("is not a number") from None
        if not math.isfinite(value):
            raise ValueError("is not a finite number")
        if (minimum is not None and value < minimum) or (
            maximum is not None and value > maximum
        ):
            low = "-inf" if minimum is None else minimum
            high = "+inf" if maximum is None else maximum
            raise ValueError(f"is outside [{low}, {high}]")
        return value
    return convert


def _boolean(raw):
    v = raw.lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    raise ValueError("is not a boolean")


def _env_path(name, default):
    """Path overridable via environment variable (for fast test runs)."""
    return Path(_env(name, default))


def _env_str(name, default):
    """String setting overridable via environment variable — service URLs
    move between machines (a laptop, a lab server, CI) and used to be
    editable only by patching this file (audit 2.13)."""
    return _env(name, default)


def _env_float(name, default, minimum=None, maximum=None):
    """Numeric setting overridable via environment variable."""
    return _env(name, default, _number(minimum, maximum))


def _env_bool(name, default):
    """Boolean overridable via environment variable.

    True: "1", "true", "yes", "on"; False: "0", "false", "no", "off"
    (case-insensitive). Anything else keeps the default and warns.
    """
    return _env(name, default, _boolean)


# =============================================================================
# DEBUG & LOGGING
# =============================================================================

# Enable verbose logging in console for modernization, enrichment, etc.
DEBUG = False

# Log file path (all DEBUG+ messages are always written here, regardless of DEBUG flag)
# Set to None to disable file logging
LOG_FILE = Path("pipeline.log")

# =============================================================================
# DIRECTORIES
# =============================================================================

# Input directory containing ALTO XML files or ZIP archives
OCR_DIR = _env_path("ALTO2TEI_OCR_DIR", "OCR")

# Output directory for generated TEI XML files
OUTPUT_DIR = _env_path("ALTO2TEI_OUTPUT_DIR", "tei_output")

# =============================================================================
# METADATA SOURCE
# =============================================================================

# CSV file containing document metadata (semicolon-delimited)
# Expected columns: BDD, Titre_long, Titre_abrege, ID_auteur, ID_imprimeurs,
#                   ID_Editeur, ID_Traducteurs, Lieu_publication, Date_01,
#                   Date_02, Localisation, Cote, langues, Sujet, Matiere,
#                   ARK, manifest_iiif, Format
METADATA_CSV = Path("metadata_livre.csv")

# CSV file containing person metadata (semicolon-delimited)
# Expected columns: BDD (PERSXXXX), ARK, ISNI, Label_categ, Prenoms, Nom,
#                   Sexe, RoleName, GenName, Surnoms, Annee_naissance,
#                   ID_Ville_naissance, Ville_naissance, Annee_mort,
#                   ID_Ville_mort, Ville_mort, Confession, Formation,
#                   Professions, Notes
METADATA_PERSON_CSV = Path("metadata_personne.csv")

# =============================================================================
# PROCESSING PARAMETERS
# =============================================================================

# Maximum number of parallel workers for multiprocessing
# Set to a lower value if you experience memory issues
MAX_WORKERS = 8

# =============================================================================
# APPLICATION VERSIONS
# =============================================================================

# OCR/HTR applications used to generate ALTO files
# Each entry contains: version, ident (xml attribute), label, url
APP_VERSIONS = {
    "KRAKEN": {
        "version": "4.3.x",
        "ident": "Kraken",
        "label": "Kraken",
        "url": "https://github.com/mittagessen/kraken",
    },
    "YOLO": {
        "version": "8.0.x",
        "ident": "YOLO",
        "label": "YOLO - Ultralytics",
        "url": "https://github.com/ultralytics/ultralytics",
    },
    "YALTAI": {
        "version": "1.0.x",
        "ident": "YALTAi",
        "label": "YALTAi - You Actually Look Twice At it",
        "url": "https://github.com/PonteIneptique/YALTAi",
    },
}

# =============================================================================
# SEGMONTO TAXONOMY CONFIGURATION
# =============================================================================

# SegmOnto taxonomy identifier and URL
SEGMONTO = {
    "id": "SegmOnto",
    "url": "https://github.com/segmonto",
    "zones_category_id": "SegmOntoZones",
    "lines_category_id": "SegmOntoLines",
}

# =============================================================================
# CSV CONFIGURATION
# =============================================================================

# Delimiter for metadata CSV files
CSV_DELIMITER = ";"

# Patterns to auto-detect IIIF mapping CSV files in document directories
IIIF_CSV_PATTERNS = ["*iiif*.csv", "*mapping*.csv", "*manifest*.csv"]

# Maximum CSV file size to process (in bytes) - skip larger files
IIIF_CSV_MAX_SIZE = 10_000_000

# Number of CSV rows to sample for validation
IIIF_CSV_SAMPLE_ROWS = 100

# Minimum match rate for CSV validation (30% = 0.3)
IIIF_CSV_MIN_MATCH_RATE = 0.3

# Regex pattern to extract BDD prefix from document folder names
BDD_PREFIX_PATTERN = r"([A-Za-z]+?\d+)"

# =============================================================================
# PLACEHOLDER TEXTS
# =============================================================================

# Default placeholder text when metadata exists but field is empty
PLACEHOLDER_INFO_UNAVAILABLE = "Information not available."

# Default placeholder text when no metadata found at all
PLACEHOLDER_NO_METADATA = "Metadata not found in catalogue."

# Placeholder ORCID: respStmt entries carrying this value get no <ptr>
PLACEHOLDER_ORCID = "0000-0000-0000-0000"

# =============================================================================
# IIIF CONFIGURATION
# =============================================================================

# IIIF server configuration for image references
# IIIF image settings.
#
# "image_base" is the IIIF Image API base of the volume's scans; @source
# on each page is built from it. main.py derives it per document from a
# Gallica manifest URL, and falls back to the value set here — which is
# how a non-Gallica server (whose image API cannot be guessed from its
# manifest URL) gets its pages linked.
#
# The five URL-building keys this dict used to carry (scheme, server,
# manifest_prefix, manifest_suffix, image_prefix) were read nowhere:
# editing them changed nothing in the output (audit 4.6).
IIIF_URI = {
    # "image_base": "https://iiif.example.org/iiif/2/my-volume",
}

# =============================================================================
# LANGUAGE DETECTION CONFIGURATION
# =============================================================================

# Languages to detect (add/uncomment as needed)
# Uses lingua-language-detector restricted to this subset
SUPPORTED_LANGUAGES = {
    "fr": {"name": "French", "ident": "fra"},
    "el": {"name": "Ancient Greek", "ident": "grc"},
    "la": {"name": "Latin", "ident": "lat"},
    #"de": {"name": "German", "ident": "deu"},
    #"nl": {"name": "Dutch", "ident": "nld"},
    #"it": {"name": "Italian", "ident": "ita"},
    #"en": {"name": "English", "ident": "eng"},
}

# Minimum confidence for direct acceptance (0.0-1.0)
# Below this, heuristic fallback is tried for short texts
LANG_CONFIDENCE_THRESHOLD = 0.35

LANG_MIN_TEXT_LENGTH = 10
LANG_DEFAULT = "fra"  # fallback to French for ambiguous texts

# =============================================================================
# LINGUISTIC ENRICHMENT
# =============================================================================

# Enable/disable linguistic enrichment (tokenization, POS, lemmatization)
ENRICHMENT_ENABLED = _env_bool("ALTO2TEI_ENRICHMENT", True)

# PyHellen API server URL
PYHELLEN_URL = _env_str("ALTO2TEI_PYHELLEN_URL", "http://localhost:8000")

# Request timeout in seconds (higher for first request / model loading)
PYHELLEN_TIMEOUT = _env_float("ALTO2TEI_PYHELLEN_TIMEOUT", 120, minimum=0.1)

# Timeout of the reachability probe both services answer before a run
# (PyHellen /api/languages, VieuxParler /health). Shared, and generous:
# a remote server still loading its model takes seconds to answer, and a
# probe that gives up first disables the phase for the whole run with a
# single warning line to explain the missing <w> or <choice>.
HEALTH_TIMEOUT = _env_float("ALTO2TEI_HEALTH_TIMEOUT", 30, minimum=0.1)

# Maximum concurrent PyHellen requests (audit 3.3 — same model as
# MODERNIZE_MAX_CONCURRENT).
PYHELLEN_MAX_CONCURRENT = 8

# Circuit breaker (audit 2.7): stop calling PyHellen after this many
# consecutive failures — a frozen server must not turn into hours of
# sequential timeouts.
PYHELLEN_MAX_CONSECUTIVE_FAILURES = 10

# Fail a container when more than this share of its tokens could not be
# anchored in the text (audit 2.12): past this, annotations would attach
# to the wrong characters.
ENRICHMENT_MAX_MISALIGNED_RATIO = 0.2

# Mapping from TEI language ident to PyHellen model name
PYHELLEN_MODELS = {"fra": "freem", "lat": "lasla", "grc": "grc"}

# TEI container elements to enrich
# Elements whose text goes through PyHellen. <head> and <titlePart>
# joined the list when headings and title pages got their own container:
# the title page is where the author, printer, place and date of the
# volume are written out.
ENRICHMENT_CONTAINERS = {
    "ab",
    "note",
    "head",
    "titlePart",
    # "fw",  # running titles and page numbers: no sentence to annotate
}

# Minimum text length (chars) to attempt enrichment
ENRICHMENT_MIN_TEXT_LENGTH = 5

# =============================================================================
# TEXT MODERNIZATION (API)
# =============================================================================

# Enable/disable text modernization (old French -> modern French)
MODERNIZE_ENABLED = _env_bool("ALTO2TEI_MODERNIZE", True)

# Mapping from TEI language ident to modernization API base URL.
# Add entries below as APIs become available: each one is overridable by
# ALTO2TEI_MODERNIZE_URL_<IDENT> (e.g. ALTO2TEI_MODERNIZE_URL_FRA), and
# the bare ALTO2TEI_MODERNIZE_URL moves every language that has no
# specific override — so a second entry stays configurable without a
# patch to this file, which is the whole point.
_MODERNIZE_API_DEFAULTS = {"fra": "http://localhost:8011"}
_MODERNIZE_URL = _env_str("ALTO2TEI_MODERNIZE_URL", None)
MODERNIZE_API = {
    ident: _env_str(
        f"ALTO2TEI_MODERNIZE_URL_{ident.upper()}", _MODERNIZE_URL or default
    )
    for ident, default in _MODERNIZE_API_DEFAULTS.items()
}

# Number of lines per batch request to the modernization API
MODERNIZE_BATCH_SIZE = 64

# Timeout in seconds for modernization API calls
MODERNIZE_TIMEOUT = _env_float("ALTO2TEI_MODERNIZE_TIMEOUT", 300, minimum=0.1)

# Max concurrent requests to the modernization API (avoid PoolTimeout)
MODERNIZE_MAX_CONCURRENT = 8

# Similarity (original vs modernized) -> @cert on the reading. Keys ARE
# the emitted values, so they must belong to TEI's closed vocabulary
# (teidata.certainty). Boundaries measured on real VieuxParler output
# for this corpus: the similarity of a modernized line runs 0.81-1.00
# (median 0.96), anything below MODERNIZE_SIMILARITY_MIN being rejected
# outright as a hallucination — so >= 0.95 is a light spelling
# normalization, and < 0.90 a heavy rewrite worth flagging to a reader.
# Raising the floor below narrows these buckets from underneath: at 0.95
# nothing can be graded "low" or "medium" any more.
MODERNIZE_CERT_THRESHOLDS = {"low": 0.0, "medium": 0.90, "high": 0.95}

# Below this character-level similarity (after normalizing long-s,
# accents and case) a modernized line is rejected as a hallucination and
# left unmodified. 0.8 sits under the legitimate changes — cognoiſtre →
# connaître normalizes to ~0.73 similarity on the word alone, but a full
# line of ordinary modernization stays above 0.81 — and over the Greek
# OCR artifacts the API answers with invented French.
# Single home: the value used to live in src/modernize.py AND be
# restated in the editorialDecl prose below, free to diverge (audit
# 2.13) — the prose now reads it.
MODERNIZE_SIMILARITY_MIN = _env_float(
    "ALTO2TEI_MODERNIZE_SIMILARITY_MIN", 0.8, minimum=0.0, maximum=1.0
)

# =============================================================================
# NAMED ENTITY RECOGNITION (NER)
# =============================================================================

# Enable/disable automatic NER pipeline (runs after modernization)
NER_ENABLED = _env_bool("ALTO2TEI_NER", True)

# Entity types to detect — add/remove entries to customize
# Each key maps to a TEI annotation strategy
NER_ENTITY_TYPES = {
    "person": {
        "tei_element": "persName",
        "tei_list": "listPerson",
        "tei_item": "person",
        "tei_parent": "particDesc",
        "gliner_label": "person name",
        "camembert_label": "PER",
        "csv_file": "entities_persons.csv",
    },
    "place": {
        "tei_element": "placeName",
        "tei_list": "listPlace",
        "tei_item": "place",
        "tei_parent": "settingDesc",
        "gliner_label": "place name",
        "camembert_label": "LOC",
        "csv_file": "entities_places.csv",
    },
    "organization": {
        "tei_element": "orgName",
        "tei_list": "listOrg",
        "tei_item": "org",
        "tei_parent": "particDesc",
        "gliner_label": "organization",
        "camembert_label": "ORG",
        "csv_file": "entities_orgs.csv",
    },
    "date": {
        "tei_element": "date",
        "tei_list": None,
        "tei_item": None,
        "tei_parent": None,
        "gliner_label": "date",
        "camembert_label": "DATE",
        "csv_file": None,
    },
    "artwork": {
        "tei_element": "objectName",
        "tei_list": "listObject",
        "tei_item": "object",
        # TEI requires the name to sit inside an <objectIdentifier>;
        # emitting <objectName> directly under <object> made every file
        # carrying an artwork entity fail tei_all validation.
        "tei_item_wrapper": "objectIdentifier",
        "tei_parent": "standOff",
        "gliner_label": "artwork",
        "camembert_label": None,
        "csv_file": "entities_artworks.csv",
    },
    "literary_work": {
        "tei_element": "title",
        "tei_list": "listBibl",
        "tei_item": "bibl",
        "tei_parent": "standOff",
        "gliner_label": "literary work",
        "camembert_label": None,
        "csv_file": "entities_works.csv",
    },
    "material": {
        "tei_element": "material",
        "tei_list": None,
        "tei_item": None,
        "tei_parent": None,
        "gliner_label": "material",
        "camembert_label": None,
        "csv_file": "entities_materials.csv",
    },
    "technique": {
        "tei_element": "rs",
        "tei_element_attrs": {"type": "technique"},
        "tei_list": None,
        "tei_item": None,
        "tei_parent": None,
        "gliner_label": "artistic technique",
        "camembert_label": None,
        "csv_file": "entities_techniques.csv",
    },
    "event": {
        "tei_element": "rs",
        "tei_element_attrs": {"type": "event"},
        "tei_list": "listEvent",
        "tei_item": "event",
        "tei_parent": "standOff",
        "gliner_label": "historical event",
        "camembert_label": None,
        "csv_file": "entities_events.csv",
    },
}

# NER models configuration
NER_MODELS = {
    "camembert": {
        "model_id": "pjox/camembert-classical-fr-ner/final-model.pt",
        "batch_size": 64,
        "languages": ["fra"],
        "source_text": "orig",
    },
    "gliner": {
        "model_id": "urchade/gliner_multi-v2.1",
        "batch_size": 24,
        "languages": None,  # all languages
        "source_text": "reg",  # <reg> for fra, raw text for others
        # GLiNER truncates inputs > ~384 subword tokens. Long blocks are sliced
        # into sliding word windows: max_words per window with overlap_words
        # of overlap so entities at chunk boundaries are still captured.
        "max_words": 240,
        "overlap_words": 30,
    },
}

# Minimum confidence score to keep a NER prediction
NER_CONFIDENCE_THRESHOLD = 0.6

# Output directory for entity CSV files
NER_OUTPUT_DIR = Path("entities")

# TEI containers to scan for NER (same as enrichment by default)
# <fw> excluded: running titles and page numbers rarely contain entities
# and produce only noise in practice.
NER_CONTAINERS = {"ab", "note", "head", "titlePart"}

# Confidence → @cert mapping thresholds
# Confidence -> @cert. The keys ARE the emitted values, so they must
# belong to TEI's closed vocabulary (teidata.certainty:
# high|medium|low|unknown) — "mid" made every NER output fail
# tei_all validation.
NER_CERT_THRESHOLDS = {"low": 0.0, "medium": 0.6, "high": 0.85}

# =============================================================================
# EDITORIAL DECLARATIONS (encodingDesc/editorialDecl)
# =============================================================================

# Each entry produces a child element of <editorialDecl> in the TEI header.
# Only entries whose "enabled" key is True (or whose matching pipeline flag
# is True) are injected.  Set to None or remove an entry to skip it.
EDITORIAL_DECLARATIONS = {
    "normalization": {
        "enabled": MODERNIZE_ENABLED,
        "attrs": {"method": "markup"},
        "text": (
            "Original historical spelling is preserved in orig elements. "
            "Modernized spelling is provided in reg elements, generated "
            "automatically via a translation API (LSTM Fairseq/FreEM model). "
            "Lines whose modernized form diverges too far from the original "
            "(word-count ratio or character-level similarity after "
            f"normalization below {MODERNIZE_SIMILARITY_MIN}) are left unmodified."
        ),
    },
    "segmentation": {
        "enabled": ENRICHMENT_ENABLED,
        "attrs": {},
        "text": (
            "Linguistic annotation (tokenization, POS tagging, "
            "lemmatization, sentence segmentation) was produced "
            "automatically by the PyHellen NLP API. Tokens are "
            "encoded as w elements with @lemma, @pos and @msd "
            "attributes; punctuation as pc elements; sentence "
            "boundaries as s elements."
        ),
    },
    "interpretation": {
        "enabled": NER_ENABLED,
        "attrs": {},
        "text": (
            "Named entities were automatically detected using a hybrid "
            "NER pipeline. French text was processed with "
            "CamemBERT-classical-fr-ner on original orthography and "
            "GLiNER-multi-v2.1 on modernized text. Non-French text "
            "was processed with GLiNER only. Annotations carry "
            '@resp="#ner-auto" and @cert (low < 0.6, mid 0.6\u20130.85, '
            "high >= 0.85). Identifiers were resolved against local "
            "authority files."
        ),
    },
}

# Description of the language-detection methodology, rendered as a <p>
# inside <profileDesc>/<langUsage>. Kept separate from EDITORIAL_DECLARATIONS
# because <langUsage> is not a valid child of <editorialDecl> in TEI P5.
LANG_USAGE_DESCRIPTION = (
    "Language detection uses Lingua (statistical n-gram model) at "
    "the container level (ab, note, fw). Mixed-language containers "
    "are segmented by Lingua's detect_multiple_languages_of, which "
    "identifies contiguous blocks via sliding-window character "
    "n-gram comparison. Segments classified as foreign are validated "
    "by rule-based heuristics (character sets, keywords, patterns): "
    "a segment is rejected if the primary-language heuristic score "
    "reaches 2, or if the target-language heuristic score is 0. "
    "Greek is detected reliably through Unicode character ranges; "
    "Latin relies on distinctive vocabulary not shared with French."
)

# =============================================================================
# RESPONSIBILITY STATEMENT
# =============================================================================

RESPONSIBILITY = {
    "text": "TEI SegmOnto encoding from ALTO (custom pipeline).",
    "resp": [
        {
            "forename": "Maxime",
            "surname": "Humeau",
            "ptr": {
                "type": "orcid",
                "target": "https://orcid.org/0000-0001-6860-0916",
            },
        },
        {
            "forename": "Jan",
            "surname": "Blanc",
            "ptr": {
                "type": "orcid",
                "target": "https://orcid.org/0000-0002-2091-2520",
            },
        },
        {
            "forename": "Antoine",
            "surname": "Gallay",
            "ptr": {
                "type": "orcid",
                "target": "https://orcid.org/0009-0003-2463-9589",
            },
        },
        {
            "forename": "Gabriel",
            "surname": "Batalla-Lagleyre",
            "ptr": {
                "type": "orcid",
                "target": "https://orcid.org/0000-0003-4600-0295",
            },
        },
        {
            "forename": "Pauline",
            "surname": "Randonneix",
            # TODO: real ORCID needed (placeholder is skipped at generation time)
            "ptr": {
                "type": "orcid",
                "target": "https://orcid.org/0000-0000-0000-0000",
            },
        },
        {
            "forename": "Léonie",
            "surname": "Marquaille",
            # TODO: real ORCID needed (placeholder is skipped at generation time)
            "ptr": {
                "type": "orcid",
                "target": "https://orcid.org/0000-0000-0000-0000",
            },
        },
    ],
    "publisher": "Projet Grand Siècle",
    "authority": "Université de Lausanne - UNIL, Université de Genève - UNIGE",
    "availability": {"status": "restricted"},
    "licence": {"target": "https://creativecommons.org/licenses/by/4.0/"},
}
