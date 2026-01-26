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
# RESPONSIBILITY STATEMENT
# =============================================================================

# Information about who created the TEI encoding (appears in teiHeader)
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
