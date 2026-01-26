# -----------------------------------------------------------
# ALTO2TEI - ALTO to TEI XML conversion pipeline
# -----------------------------------------------------------
"""
ALTO2TEI package.

Converts ALTO (Analyzed Layout and Text Object) XML files from OCR/HTR systems
into TEI (Text Encoding Initiative) XML format with SegmOnto taxonomy.

Main modules:
    - tei: Central TEI document class
    - teiheader: TEI header construction
    - sourcedoc: SourceDoc construction from ALTO
    - body: Body construction from extracted text
    - metadata: Metadata loading (CSV, IIIF)
    - utils: Shared utilities

Usage:
    from src import TEI
    from src.teiheader import build_header
    from src.sourcedoc import build_sourcedoc
    from src.body import build_body
    from src.metadata import load_metadata, IIIFMapping
"""

from .tei import TEI
from .constants import NS_ALTO, NS_TEI, NS_XML, XML_ID

__all__ = [
    "TEI",
    "NS_ALTO",
    "NS_TEI",
    "NS_XML",
    "XML_ID",
]

__version__ = "2.0.0"
