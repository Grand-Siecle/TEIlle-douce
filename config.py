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
# APPLICATION VERSIONS
# =============================================================================

# Versions of OCR/HTR applications used to generate ALTO files
APP_VERSIONS = {
    "KRAKEN_VERSION": "4.3.x",
    "YOLO_VERSION": "8.0.x",
    "YALTAI_VERSION": "1.0.x",
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
