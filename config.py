# -----------------------------------------------------------
# Configuration for ALTO2TEI pipeline
# Modify these values to customize the pipeline
# -----------------------------------------------------------

from pathlib import Path

# =============================================================================
# DIRECTORIES
# =============================================================================

# Input directory containing ALTO XML files or ZIP archives
OCR_DIR = Path("OCR")

# Output directory for generated TEI XML files
OUTPUT_DIR = Path("tei_output")

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

# Model information for OCR/HTR
MODELS_VERSIONS = {
    "KRAKEN_MODEL": {
        "name": "",
        "source": "",
    },
    "YOLO_MODEL": {
        "name": "CapricciosaX.pt",
        "source": "https://doi.org/10.5281/zenodo.10602196",
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

# =============================================================================
# IIIF CONFIGURATION
# =============================================================================

# IIIF server configuration for image references
IIIF_URI = {
    "scheme": "https",
    "server": "gallica.bnf.fr",
    "manifest_prefix": "/iiif/ark:/12148/",
    "manifest_suffix": "/manifest.json",
    "image_prefix": "/iiif/ark:/12148",
}

# =============================================================================
# LANGUAGE DETECTION CONFIGURATION
# =============================================================================

# Supported languages for FastText detection
# Format: {"ISO 639-1 code": {"name": "Full name", "ident": "TEI ident code"}}
# See: https://en.wikipedia.org/wiki/List_of_ISO_639-1_codes
SUPPORTED_LANGUAGES = {
    "fr": {"name": "French", "ident": "fra"},
    "el": {"name": "Ancient Greek", "ident": "grc"},  # FastText uses "el" for Greek
    "la": {"name": "Latin", "ident": "lat"},
    #"de": {"name": "German", "ident": "deu"},
    #"nl": {"name": "Dutch", "ident": "nld"},
    #"it": {"name": "Italian", "ident": "ita"},
    #"en": {"name": "English", "ident": "eng"},
}

# Minimum confidence threshold for language detection (0.0-1.0)
# Note: For historical OCR text, lower values (0.3-0.5) work better
LANG_CONFIDENCE_THRESHOLD = 0.35

# Minimum text length to attempt language detection
LANG_MIN_TEXT_LENGTH = 10

# Default language when detection fails or text is too short
LANG_DEFAULT = "und"  # "und" = undetermined (ISO 639-2)

# Fallback language when detected language is not in SUPPORTED_LANGUAGES
# Set to None to use LANG_DEFAULT, or a TEI ident like "fra" for French
LANG_FALLBACK = "fra"

# FastText model path (lid.176.bin for language identification)
# If None, will auto-download to ~/.fasttext/
FASTTEXT_MODEL_PATH = None

# =============================================================================
# RESPONSIBILITY STATEMENT
# =============================================================================

# Information about who created the TEI encoding (appears in teiHeader)
# =============================================================================
# LINGUISTIC ENRICHMENT
# =============================================================================

# Enable/disable linguistic enrichment (tokenization, POS, lemmatization)
ENRICHMENT_ENABLED = True

# PyHellen API server URL
PYHELLEN_URL = "http://localhost:8000"

# Request timeout in seconds (higher for first request / model loading)
PYHELLEN_TIMEOUT = 120

# Mapping from TEI language ident to PyHellen model name
PYHELLEN_MODELS = {"fra": "freem", "lat": "lasla", "grc": "grc"}

# TEI container elements to enrich
ENRICHMENT_CONTAINERS = {"ab", "note", "fw"}

# Minimum text length (chars) to attempt enrichment
ENRICHMENT_MIN_TEXT_LENGTH = 5

# =============================================================================
# TEXT MODERNIZATION (API)
# =============================================================================

# Enable/disable text modernization (old French -> modern French)
MODERNIZE_ENABLED = True

# Mapping from TEI language ident to modernization API base URL
# Add entries for other languages as APIs become available
MODERNIZE_API = {
    "fra": "http://localhost:8010",
}

# Number of lines per batch request to the modernization API
MODERNIZE_BATCH_SIZE = 64

# Timeout in seconds for modernization API calls
MODERNIZE_TIMEOUT = 300

# =============================================================================
# RESPONSIBILITY STATEMENT
# =============================================================================

RESPONSIBILITY = {
    "text": "TEI SegmOnto encoding from ALTO (custom pipeline).",
    "resp": [
        {
            "forename": "Firstname",
            "surname": "Lastname",
            "ptr": {
                "type": "orcid",
                "target": "https://orcid.org/0000-0000-0000-0000",
            },
        }
    ],
    "publisher": "Your lab / project",
    "authority": "Your institution",
    "availability": {"status": "restricted"},
    "licence": {"target": "https://creativecommons.org/licenses/by/4.0/"},
}
